"""目标输入框选择与投递核心。

本模块不读取目标窗口的正文，也不在 Tk 线程中调用平台 UI 自动化 API。
平台后端只需要实现 ``list_windows``、``capture_focus`` 和 ``fill``，具体的
窗口枚举与输入由一个专用 worker 串行完成。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Iterable
from talkman_input_locations import InputLocationControllerMixin


TARGET_FILE = "target.json"
UNSUPPORTED_MESSAGE = "当前平台尚未支持自动填入，请复制全部后粘贴。"


@dataclass(frozen=True)
class FillReceipt:
    message: str
    text: str
    app: str
    title: str
    time: str
    status: str = '已填入'
    send_token: str = ''


class TargetError(RuntimeError):
    """目标选择、重连或投递失败。消息面向中文界面。"""


class InputLocationError(TargetError):
    """填入前固定控件失效；界面可重新开启位置选择，但不得自动重贴。"""


class DeliveryReadError(TargetError):
    """固定阶段码供诊断使用；不把系统异常或输入框内容带入报告。"""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SendSnapshot:
    handle: Any
    before: str
    # UIA can expose a hint as Value/Text. Keep the observed value intact;
    # only exact post-paste text may establish the alternative empty-editor case.
    empty_hint: bool = False
    paragraph_end: str = ''


def is_agent_window(window) -> bool:
    """仅用窗口身份作可关闭的列表筛选，不读取窗口正文或影响填入校验。"""
    get = window.get if isinstance(window, dict) else lambda key, default='': getattr(window, key, default)
    app = str(get('app', '')).casefold()
    identity = str(get('identity', '')).replace('\\', '/').rsplit('/', 1)[-1].casefold()
    title = str(get('title', '')).casefold()
    pattern = r'(?<![a-z0-9])(?:chatgpt|claude|codex|zcode|doubao|qwen|tongyi|kimi|deepseek|yuanbao|perplexity|gemini|cursor|trae|cherry[ _-]?studio|chatbox|lm[ _-]?studio|open[ _-]?webui)(?![a-z0-9])|豆包|千问|通义|元宝|文小言|文心一言'
    if re.search(pattern, app + ' ' + identity):
        return True
    # 浏览器和通用编辑器只有活动窗口标题明确标识 AI 工具时才纳入。
    generic = r'chrome|msedge|edge|firefox|safari|brave|arc|browser|code|vscodium|浏览器'
    administrative = (r'api[ _-]?keys?|api密钥|密钥管理|控制台|开发者|开发平台|开放平台|开发文档|接入文档|'
                      r'\bdocumentation\b|\bdocs\b|\bconsole\b|\bdashboard\b|\bdeveloper\b|'
                      r'\bpricing\b|\bsettings\b|账户设置|账号设置|模型设置|计费|教程')
    if re.search(generic, app + ' ' + identity) and re.search(administrative, title):
        return False
    return bool(re.search(generic, app + ' ' + identity) and re.search(pattern, title))


@dataclass(frozen=True)
class TargetWindow:
    """可展示的窗口身份。

    ``hwnd``、``pid`` 只代表本次运行的会话句柄，不能写入配置并跨重启复用。
    ``identity`` 通常为进程可执行路径，是跨会话重连的主要身份。
    """

    hwnd: Any = None
    pid: int | None = None
    app: str = ""
    title: str = ""
    identity: str = ""
    process_started: str | float | int | None = None

    @classmethod
    def from_value(cls, value: Any) -> "TargetWindow":
        if isinstance(value, cls):
            return value
        # 平台后端可保留自己的 dataclass；这里只取
        # 两边约定的身份字段，避免 core 依赖平台模块形成循环导入。
        if isinstance(value, dict):
            get = value.get
            hwnd = get("hwnd", get("id"))
            pid = get("pid")
            app = get("app", "")
            title = get("title", "")
            identity = get("identity", get("process_path", get("process", "")))
            process_started = get("process_started")
        else:
            if not all(hasattr(value, name) for name in ("pid", "app", "title")):
                raise TargetError("目标窗口信息格式无效。")
            hwnd = getattr(value, "hwnd", getattr(value, "id", None))
            pid = getattr(value, "pid", None)
            app = getattr(value, "app", "")
            title = getattr(value, "title", "")
            identity = getattr(value, "identity", "") or getattr(value, "process_path", "")
            process_started = getattr(value, "process_started", None)
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        return cls(
            hwnd=hwnd,
            pid=pid,
            app=str(app or ""),
            title=str(title or ""),
            identity=str(identity or ""),
            process_started=process_started,
        )

    @property
    def id(self) -> Any:
        """与平台后端的 ``id`` 命名兼容。"""
        return self.hwnd

    @property
    def process_path(self) -> str:
        """Windows 后端使用的进程身份别名。"""
        return self.identity

    def session_key(self) -> tuple[Any, int | None]:
        return self.hwnd, self.pid

    def metadata(self) -> dict[str, str]:
        """返回可跨进程保存的字段，不包含 hwnd、pid 或进程启动时间。"""
        return {"app": self.app, "title": self.title, "identity": self.identity}

    def display_name(self) -> str:
        return self.title or self.app or self.identity or "未命名窗口"


class TargetSelection:
    """目标选择的持久化容器，只保存身份元数据。"""

    def __init__(self, data_dir: Path, metadata: dict[str, Any] | None = None):
        self.data_dir = Path(data_dir)
        self.metadata = self._clean(metadata or {})

    @staticmethod
    def _clean(value: dict[str, Any]) -> dict[str, str]:
        allowed = ("app", "title", "identity", "input_required")
        return {key: str(value.get(key, "") or "") for key in allowed if value.get(key)}

    @property
    def path(self) -> Path:
        return self.data_dir / TARGET_FILE

    @property
    def selected(self) -> bool:
        return bool(self.metadata.get("identity") or self.metadata.get("title") or self.metadata.get("app"))

    @classmethod
    def load(cls, data_dir: Path) -> "TargetSelection":
        selection = cls(data_dir)
        path = selection.path
        if not path.exists():
            return selection
        try:
            if path.stat().st_size > 32768:
                raise TargetError("目标设置文件过大，请重新选择目标输入框。")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TargetError("目标设置未能读取，请重新选择目标输入框。")
            payload = raw.get("target", raw)
            if not isinstance(payload, dict):
                raise TargetError("目标设置未能读取，请重新选择目标输入框。")
            selection.metadata = cls._clean(payload)
            return selection
        except TargetError:
            raise
        except (OSError, ValueError, UnicodeError, TypeError):
            raise TargetError("目标设置未能读取，请重新选择目标输入框。") from None

    def set(self, target: TargetWindow | dict[str, Any]) -> dict[str, str]:
        self.metadata = self._clean(TargetWindow.from_value(target).metadata())
        return dict(self.metadata)

    def clear(self) -> None:
        self.metadata = {}

    def save(self) -> None:
        directory = self.data_dir
        temporary: Path | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "target": self.metadata}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise TargetError("目标设置保存失败，请检查数据目录。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _as_windows(values: Iterable[Any]) -> list[TargetWindow]:
    result: list[TargetWindow] = []
    for value in values or ():
        try:
            target = TargetWindow.from_value(value)
        except TargetError:
            continue
        if target.hwnd is not None or target.identity or target.title:
            result.append(target)
    return result


def _match_refreshed_window(windows: list[TargetWindow], reference: TargetWindow) -> TargetWindow | None:
    """窗口临时句柄变化后，按同一进程的身份重新匹配。"""
    exact = [window for window in windows if window.session_key() == reference.session_key()]
    if len(exact) == 1:
        candidate = exact[0]
        if reference.identity and candidate.identity != reference.identity:
            return None
        if reference.process_started is not None and candidate.process_started != reference.process_started:
            return None
        return candidate

    identity = reference.identity.replace("\\", "/").strip().casefold()
    if reference.pid is None or not identity or reference.process_started is None:
        return None
    same_process = [window for window in windows if (
        window.pid == reference.pid
        and window.identity.replace("\\", "/").strip().casefold() == identity
        and window.process_started == reference.process_started
    )]
    if len(same_process) == 1:
        return same_process[0]
    if len(same_process) > 1:
        title = reference.title.strip().casefold()
        same_title = [window for window in same_process if window.title.strip().casefold() == title]
        if len(same_title) == 1:
            return same_title[0]
    return None


class _UnsupportedBackend:
    def list_windows(self) -> list[TargetWindow]:
        raise TargetError(UNSUPPORTED_MESSAGE)


Callback = Callable[[str, Any], None]


class TargetController(InputLocationControllerMixin):
    """目标窗口 worker。

    所有后端调用都在同一线程发生，以满足 Windows UIA/COM apartment 要求。
    回调也在 worker 线程触发；Tk 应用应只把事件放回自己的队列。
    """

    def __init__(self, data_dir: Path, backend: Any | None = None, platform: str | None = None):
        self.data_dir = Path(data_dir)
        self.selection = TargetSelection.load(self.data_dir)
        self.backend = backend if backend is not None else self._default_backend(platform)
        self._queue: queue.Queue[tuple[str, Any, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._windows: list[TargetWindow] = []
        self._selected: TargetWindow | None = None
        self._focus_token: Any = None
        self._last_focus_check = 0.0
        self._refresh_note = ""
        self._sync_epoch = 0
        self._sync_lease = None
        self._pending_send = None
        self._refresh_report = "尚未刷新目标窗口。"
        self._last_fill_report = "尚未尝试填入。"
        self._last_send_report = "尚未核验填入后的发送确认。"
        self._chosen_input = None
        self._input_selecting = False
        self._input_draft = None
        self._input_generation = 0
        self._thread = threading.Thread(target=self._run, name="TalkManTarget", daemon=True)
        self._thread.start()

    @staticmethod
    def _default_backend(platform: str | None) -> Any:
        platform = platform or sys.platform
        if platform == "win32":
            try:
                from talkman_target_windows import WindowsTargetBackend
                return WindowsTargetBackend()
            except Exception:
                return _UnsupportedBackend()
        if platform == "darwin":
            from talkman_target_macos import MacTargetBackend
            return MacTargetBackend()
        return _UnsupportedBackend()

    @property
    def selected(self) -> TargetWindow | None:
        with self._lock:
            return self._selected

    @property
    def label(self) -> str:
        with self._lock:
            if self._selected:
                if self._chosen_input:
                    return f'{self._selected.app} · 位置 {self._chosen_input[1].number}'
                return self._selected.display_name()
            metadata = self.selection.metadata
            return str(metadata.get("title") or metadata.get("app") or "未选择目标输入框")

    @property
    def windows(self) -> list[TargetWindow]:
        with self._lock:
            return list(self._windows)

    @property
    def refresh_note(self) -> str:
        with self._lock:
            return self._refresh_note

    @property
    def refresh_report(self) -> str:
        with self._lock:
            return self._refresh_report

    @property
    def last_fill_report(self) -> str:
        with self._lock:
            return self._last_fill_report

    @property
    def last_send_report(self) -> str:
        with self._lock:
            return self._last_send_report

    def _record_send_check(self, stage, reason, *, snapshot=None, text=None, after=None, attempts=0):
        details = []
        for label, value in (("填入前", snapshot.before if snapshot else None), ("本次填入", text), ("读回", after)):
            if isinstance(value, str):
                details.append(f"{label}：{len(value)} 字符")
        if attempts:
            details.append(f"读回次数：{attempts}")
        with self._lock:
            self._last_send_report = f"发送核验：{stage} · {reason}" + ("；" + "，".join(details) if details else "")

    @staticmethod
    def _send_read_reason(error):
        reasons = {
            'window_changed': '目标窗口或会话变化', 'input_changed': '主输入框控件变化',
            'no_input': '未找到唯一主输入框', 'readonly': '输入框可编辑性无法确认',
            'unreadable': '输入框正文不可读取', 'placeholder': '读到空框提示，尚不能确认正文',
        }
        return reasons.get(error.reason, '正文读取失败') if isinstance(error, DeliveryReadError) else '正文读取失败'

    def _record_fill(self, stage: str, result: str, target: TargetWindow | None = None) -> None:
        name = target.app if target is not None else "未选目标"
        with self._lock:
            self._last_fill_report = f"上次填入：{name} · {stage} · {result}"

    def _fill_stage(self, stage: str) -> str:
        if stage != "聚焦并填入":
            return stage
        backend_stage = getattr(self.backend, "last_fill_stage", None)
        known = {"检查目标窗口", "激活目标窗口", "查找主输入框", "聚焦主输入框",
                 "读取目标光标", "定位目标光标", "最终校验光标", "写入剪贴板", "粘贴到目标",
                 "多个聊天输入框同级", "未暴露可编辑输入框", "输入框缺少聊天标识",
                 "未找到唯一聊天输入框", "输入框字符数不可读", "等待输入框加载"}
        if not isinstance(backend_stage, str) or backend_stage not in known:
            return stage
        focus_role = getattr(self.backend, "last_fill_focus_role", None)
        roles = {"窗口", "网页区域", "容器", "文字", "多行文字框", "单行文字框", "其他控件"}
        if backend_stage in {"未暴露可编辑输入框", "输入框缺少聊天标识", "未找到唯一聊天输入框"} and focus_role in roles:
            backend_stage = f"{backend_stage}（焦点：{focus_role}）"
        tree = getattr(self.backend, "last_fill_tree", None)
        if isinstance(tree, dict):
            details = []
            for key, label in (("nodes", "扫描节点"), ("web_areas", "网页区域"), ("text_controls", "文字控件"),
                               ("editable", "可编辑"), ("child_errors", "子树读取失败")):
                value = tree.get(key)
                if type(value) is int and 0 <= value <= 3000:
                    details.append(f"{label}：{value}")
            accessibility = getattr(self.backend, "last_fill_accessibility", None)
            if accessibility in ("未尝试", "不支持，未尝试", "已启用，已读回确认", "请求已接受，等待启用"):
                details.append(f"辅助树：{accessibility}")
            elif isinstance(accessibility, str) and accessibility.startswith("尝试失败"):
                details.append("辅助树：启用失败")
            if details:
                backend_stage += "；" + "，".join(details)
        return backend_stage

    def _emit(self, callback: Callback | None, state: str, value: Any) -> None:
        if callback:
            try:
                callback(state, value)
            except Exception:
                # UI 回调异常不能终止窗口 worker。
                pass

    def refresh(self, callback: Callback | None = None) -> None:
        self._queue.put(("refresh", callback, None))

    def cycle(self, direction, callback=None, expected_target=None, cancelled=None, *, commit_preview=False):
        self._queue.put(('cycle', callback, (1 if direction > 0 else -1, expected_target, cancelled, commit_preview)))

    def _cycle(self, callback, request):
        direction, expected_target, cancelled, *options = request
        commit_preview = bool(options and options[0])
        def check_current():
            if cancelled is not None and cancelled.is_set():
                raise TargetError('目标预选已变化，本次切换已取消。')
        try:
            check_current()
            with self._lock:
                previous_windows = list(self._windows)
                current = self._selected
            if commit_preview:
                target = (_match_refreshed_window(previous_windows, expected_target)
                          if isinstance(expected_target, TargetWindow) else next(
                              (w for w in previous_windows if w.session_key() == expected_target), None))
                if target is None or not is_agent_window(target):
                    raise TargetError('预览的目标窗口已变化，请刷新后重新旋转选择。')
                check_current()
                # 确认预选不依赖后台应用取得焦点；真正填入前仍严格验证窗口。
                self.select(target)
                self._emit(callback, 'ok', target)
                return
            windows = [w for w in _as_windows(self.backend.list_windows()) if is_agent_window(w)]
            windows.sort(key=lambda w: (w.app.casefold(), str(w.id), str(w.process_started)))
            with self._lock:
                self._windows = windows
            if not windows:
                raise TargetError('未找到已打开的 Agent 窗口。')
            current_window = _match_refreshed_window(windows, current) if current else None
            index = next((i for i,w in enumerate(windows) if current_window and w.session_key() == current_window.session_key()),
                         -1 if direction > 0 else 0)
            if expected_target is not None:
                reference = expected_target if isinstance(expected_target, TargetWindow) else next(
                    (w for w in previous_windows if w.session_key() == expected_target), None)
                if reference is None and current and current.session_key() == expected_target:
                    reference = current
                target = (_match_refreshed_window(windows, reference) if reference else next(
                    (w for w in windows if w.session_key() == expected_target), None))
                if target is None:
                    raise TargetError('预览的目标窗口已关闭或改变，请重新旋转选择。')
            else:
                target = windows[(index+direction) % len(windows)]
            check_current()
            self.backend.activate_window(target)
            check_current()
            self.select(target)
            with self._lock:
                self._windows = windows
            self._emit(callback, 'ok', target)
        except Exception as error:
            self._emit(callback, 'error', str(error))

    def select(self, window: TargetWindow | dict[str, Any]) -> TargetWindow:
        target = TargetWindow.from_value(window)
        if target.hwnd is None and not target.identity and not target.title:
            raise TargetError("请选择一个有效的目标窗口。")
        with self._lock:
            self.cancel_inputs()
            self._chosen_input = None
            self._selected = target
            self._sync_epoch += 1
            self._focus_token = None
            self.selection.set(target)
        self.save()
        return target

    def clear(self) -> None:
        with self._lock:
            self.cancel_inputs()
            self._chosen_input = None
            self._selected = None
            self._sync_epoch += 1
            self._focus_token = None
            self.selection.clear()
        self.save()

    def save(self) -> None:
        # 磁盘／杀毒扫描可能拖慢 fsync；不可拿着界面读取所需的锁写文件。
        # 写入串行化后再取最新元数据，避免并发选择把旧目标写回文件。
        with self._save_lock:
            with self._lock:
                snapshot = TargetSelection(self.data_dir, dict(self.selection.metadata))
            snapshot.save()

    def fill(self, text: str, callback: Callback | None = None) -> None:
        if not isinstance(text, str) or not text:
            self._emit(callback, "error", "没有可填入的文字。")
            return
        self._queue.put(("fill", callback, text))

    def fill_with_receipt(self, text: str, callback: Callback | None = None, *, expected_target=None,
                          allow_send_confirmation=True) -> None:
        if not isinstance(text, str) or not text:
            self._emit(callback, "error", "没有可填入的文字。")
            return
        with self._lock:
            self._queue.put(("fill_receipt", callback,
                (text, self._sync_epoch, expected_target, allow_send_confirmation)))

    def close(self) -> None:
        self.stop_sync()
        self._stop.set()
        self._queue.put(("close", None, None))
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def stop_sync(self):
        with self._lock:
            self._sync_epoch += 1
            self._pending_send = None

    def send_filled(self, token, callback=None):
        """只发送已核实的填入快照，绝不重新粘贴或自动重试。"""
        self._queue.put(('send_filled', callback, token))

    def send_current(self, expected_target, callback=None, cancelled=None):
        """显式组合操作：读回唯一可编辑输入框，再发送它已有的非空内容。"""
        with self._lock:
            request = (self._sync_epoch, expected_target, time.monotonic()+3, cancelled)
        self._queue.put(('send_current', callback, request))

    def _send_current(self, callback, request):
        epoch, expected_target, expires, cancelled = request
        try:
            if self.input_selecting or self.selected_input is not None or self.selection.metadata.get('input_required'):
                raise TargetError('所选输入位置的提交方式尚未验证，请在目标内确认；本次未发送。')
            with self._lock:
                target = self._selected
                self._pending_send = None
            def current():
                return (not self._stop.is_set() and epoch == self._sync_epoch
                        and time.monotonic() < expires and target is not None
                        and self._selected == target and target.session_key() == expected_target
                        and not (cancelled and cancelled()))
            if not current():
                raise TargetError('目标已变化，本次未发送。')
            open_target, read, send = self._delivery_methods()
            if not all(callable(m) for m in (open_target, read, send)):
                raise TargetError('目标不支持核对内容，请在目标内手动发送。')
            handle = open_target(target)
            text = read(target, handle)
            if not isinstance(text, str) or not text.strip():
                raise TargetError('目标输入框为空或无法读取，本次未发送。')
            if not current() or read(target, handle) != text:
                raise TargetError('目标文字已变化，本次未发送。')
            send(target, handle, text, current)
            self._emit(callback, 'ok', FillReceipt('已向目标按下回车，请在目标确认发送结果。',
                text, target.app, target.title, datetime.now().astimezone().isoformat(timespec='seconds'),
                '已触发下发'))
        except TargetError as exc:
            self._emit(callback, 'error', str(exc))
        except Exception:
            self._emit(callback, 'error', '无法确认发送结果，不会自动重试；请检查目标输入框。')

    def _send_filled(self, callback, token):
        with self._lock:
            pending, self._pending_send = self._pending_send, None
        stage = '确认凭据'
        try:
            if pending is None or not token or pending[0] != token:
                self._record_send_check('二次发送', '确认凭据已失效或已使用')
                raise TargetError('这次下发确认已失效，请检查目标输入框。')
            _, epoch, target, handle, expected, expires = pending
            def current():
                return (not self._stop.is_set() and epoch == self._sync_epoch
                        and time.monotonic() < expires and self._selected == target)
            _open, read, send = self._delivery_methods()
            if not current():
                self._record_send_check('二次发送', '确认时间已过' if time.monotonic() >= expires else '目标或操作已变化')
                raise TargetError('目标、文字或确认时间已变化，本次未下发；请检查目标输入框。')
            stage = '重新读取正文'
            actual = read(target, handle)
            if actual != expected:
                self._record_send_check('二次发送', '目标正文已变化', text=expected, after=actual)
                stage = '正文不符'
                raise TargetError('目标、文字或确认时间已变化，本次未下发；请检查目标输入框。')
            stage = '聚焦并触发回车'
            send(target, handle, expected, current)
            self._record_send_check('二次发送', '已触发回车，需在目标确认发送结果')
            result = FillReceipt('已向目标按下回车，请在目标确认发送结果。', expected, target.app, target.title,
                datetime.now().astimezone().isoformat(timespec='seconds'), '已触发下发')
            self._emit(callback, 'ok', result)
        except TargetError as exc:
            if stage in ('重新读取正文', '聚焦并触发回车'):
                self._record_send_check('二次发送 · ' + stage, self._send_read_reason(exc) if stage == '重新读取正文' else '未完成，不自动重试')
            self._emit(callback, 'error', str(exc))
        except Exception:
            self._record_send_check('二次发送 · ' + stage, '结果无法确认，不自动重试')
            self._emit(callback, 'error', '无法确认下发结果，不会自动重试；请检查目标输入框。')

    def _delivery_methods(self):
        names = ('open_delivery', 'read_delivery', 'send_delivery')
        if not all(callable(getattr(self.backend, name, None)) for name in names):
            names = ('open_sync', 'read_sync', 'send_sync')
        return tuple(getattr(self.backend, name, None) for name in names)

    def _prepare_send_snapshot(self, target):
        open_target, read, _send = self._delivery_methods()
        if not all(callable(method) for method in self._delivery_methods()):
            self._record_send_check('填入前', '目标不支持正文核验')
            return None
        try:
            capture = getattr(self.backend, 'capture_delivery_snapshot', None)
            if callable(capture):
                snapshot = capture(target)
            else:
                handle = open_target(target)
                snapshot = SendSnapshot(handle, read(target, handle))
            if not isinstance(snapshot, SendSnapshot) or not isinstance(snapshot.before, str):
                self._record_send_check('填入前', '正文返回类型无效')
                return None
            self._record_send_check('填入前', '检测到空框提示，等待填入后逐字核验' if snapshot.empty_hint else '已读取正文', snapshot=snapshot)
            return snapshot
        except Exception as error:
            self._record_send_check('填入前', self._send_read_reason(error))
            return None

    def _arm_filled_send(self, target, text, snapshot, epoch, *, allow_send=True):
        if snapshot is None:
            return ''
        handle, before = snapshot.handle, snapshot.before
        _open, read, _send = self._delivery_methods()
        text = text.replace('\r\n', '\n')
        after = None
        attempt = 0
        try:
            for attempt in range(5):
                with self._lock:
                    if self._stop.is_set() or epoch != self._sync_epoch or self._selected != target:
                        self._record_send_check('填入后', '目标或操作已变化', snapshot=snapshot, text=text, attempts=attempt)
                        return ''
                after = read(target, handle)
                verified = False
                # The hint is not assumed to be an empty body. Only this exact
                # readback, on the original verified control, accepts that case.
                empty_match = (snapshot.empty_hint and bool(text) and after != before
                               and snapshot.paragraph_end in ('', '\n')
                               and after in (text, text + snapshot.paragraph_end))
                if empty_match:
                    verified = True
                if isinstance(after, str) and len(after) == len(before) + len(text):
                    # 允许填在光标处；移除刚插入的完整文本后必须严格等于旧正文。
                    prefix = 0
                    while prefix < len(before) and before[prefix] == after[prefix]:
                        prefix += 1
                    suffix = 0
                    while suffix < len(before) and before[-suffix-1] == after[-suffix-1]:
                        suffix += 1
                    # 重复词可能使最长公共前缀越过插入点；在前后文均匹配的范围内找完整插入。
                    lower = len(before) - suffix
                    start = after.find(text, lower, prefix+len(text)) if lower <= prefix else -1
                    if start >= 0:
                        verified = True
                if verified:
                    token = uuid.uuid4().hex
                    with self._lock:
                        if self._stop.is_set() or epoch != self._sync_epoch or self._selected != target:
                            self._record_send_check('填入后', '目标或操作已变化', snapshot=snapshot, text=text, after=after, attempts=attempt+1)
                            return ''
                        if allow_send:
                            self._pending_send = (token, epoch, target, handle, after, time.monotonic()+60)
                    reason = '空框提示已消失，完整正文与本次填入一致' if empty_match else '已核对精确插入，保留原有正文'
                    self._record_send_check('通过', reason, snapshot=snapshot, text=text, after=after, attempts=attempt+1)
                    return token if allow_send else ''
                if attempt < 4:
                    time.sleep(.08)
            reason = ('正文返回类型无效' if not isinstance(after, str) else
                      '填入后仍读到旧值，未确认文字更新' if after == before else
                      '读回内容与预期插入不符')
            self._record_send_check('填入后', reason, snapshot=snapshot, text=text, after=after, attempts=attempt+1)
        except Exception as error:
            self._record_send_check('填入后', self._send_read_reason(error), snapshot=snapshot, text=text, after=after, attempts=attempt+1)
        return ''

    def sync(self, text, callback, send=False):
        with self._lock:
            request = (self._sync_epoch, self._selected, str(text), send)
        self._queue.put(('sync', callback, request))

    def _sync(self, callback, request):
        from talkman_sync import SyncLease
        epoch, target, text, send = request
        def current():
            return not self._stop.is_set() and epoch == self._sync_epoch
        try:
            if self.input_selecting or self.selected_input is not None or self.selection.metadata.get('input_required'):
                raise TargetError('指定输入位置当前仅支持单次填入，请关闭实时同步。')
            if not current():
                raise TargetError('同步已停止。')
            if target is None:
                raise TargetError('请先刷新并选择目标，再打开同步。')
            if not all(callable(getattr(self.backend, name, None)) for name in
                       ('open_sync', 'read_sync', 'write_sync', 'send_sync')):
                raise TargetError('当前目标不支持实时同步，可关闭同步后使用「填入」。')
            if self._sync_lease is None or self._sync_lease[0] != epoch:
                self._sync_lease = (epoch, SyncLease(self.backend, target, text))
            lease = self._sync_lease[1]
            if send:
                message = lease.send(text, current)
                result = FillReceipt(message, lease.expected, target.app, target.title,
                                     datetime.now().astimezone().isoformat(timespec='seconds'), '已触发下发')
            else:
                lease.update(text, current)
                result = text
            self._emit(callback, 'ok', result)
        except TargetError as error:
            self._emit(callback, 'error', str(error))
        except Exception:
            self._emit(callback, 'error', '此输入框暂不支持可靠同步；可关闭同步后使用「填入」。')

    def _resolve(self, windows: list[TargetWindow]) -> TargetWindow | None:
        with self._lock:
            current = self._selected
            metadata = self.selection.metadata
        # 本次运行中优先使用 hwnd+pid，避免同标题窗口误投。
        if current and current.hwnd is not None and current.pid is not None:
            for window in windows:
                if window.hwnd == current.hwnd and window.pid == current.pid:
                    return window
        identity = metadata.get("identity", "")
        title = metadata.get("title", "")
        app = metadata.get("app", "")
        # 优先精确匹配进程路径和标题；部分 Windows 进程没有权限读取路径，
        # 因此保存的目标可能只有 app/title，仍应允许唯一窗口自动重连。
        predicates = []
        if identity and title:
            predicates.append(lambda w: w.identity.casefold() == identity.casefold() and w.title == title)
        if identity:
            predicates.append(lambda w: w.identity.casefold() == identity.casefold())
        if title and app:
            predicates.append(lambda w: w.title == title and w.app.casefold() == app.casefold())
        if title:
            predicates.append(lambda w: w.title == title)
        for predicate in predicates:
            matches = [w for w in windows if predicate(w)]
            if len(matches) == 1:
                return matches[0]
        return None

    def _record_refresh(self, *, count: int = 0, error: str = "") -> None:
        note = str(getattr(self.backend, "last_scan_note", "") or "")
        report = str(getattr(self.backend, "last_scan_report", "") or "")
        if error:
            note = error
            report = (report + "\n" if report else "") + f"刷新结果：{error}"
        elif not report:
            report = f"刷新结果：找到 {count} 个目标窗口。"
        with self._lock:
            self._refresh_note = note
            self._refresh_report = report

    def _refresh(self, callback: Callback | None) -> None:
        try:
            windows = _as_windows(self.backend.list_windows())
            selected = self._resolve(windows)
            with self._lock:
                self._windows = windows
                if not self._chosen_input and not self._input_selecting:
                    self._selected = selected
                if selected is None:
                    self._focus_token = None
            self._record_refresh(count=len(windows))
            self._emit(callback, "ok", windows)
        except TargetError as exc:
            self._record_refresh(error=str(exc))
            self._emit(callback, "error", str(exc))
        except Exception:
            message = "目标窗口枚举失败，请稍后重试。"
            self._record_refresh(error=message)
            self._emit(callback, "error", message)

    def _capture_focus(self, target: TargetWindow) -> Any:
        self._bind_chosen_input(target)
        method = getattr(self.backend, "capture_focus", None)
        if method is None:
            raise TargetError(UNSUPPORTED_MESSAGE)
        try:
            return method(target)
        except TypeError:
            # 兼容早期后端只接受 id/句柄的接口。
            return method(target.hwnd)

    def _monitor(self) -> None:
        if self.input_selecting:
            return
        now = time.monotonic()
        if now - self._last_focus_check < 0.3:
            return
        self._last_focus_check = now
        with self._lock:
            target = self._selected
        if target is None:
            return
        try:
            focus = self._capture_focus(target)
            with self._lock:
                if focus is False:
                    self._focus_token = None
                elif focus is not None:
                    self._focus_token = focus
        except Exception:
            # 监测失败不打断编辑；下一次 fill 会再严格验证。
            return

    def _fill(self, callback: Callback | None, text: str, receipt=False, expected_target=None,
              requested_epoch=None, allow_send_confirmation=True) -> None:
        stage = "检查目标选择"
        self._record_send_check('尚未开始' if receipt and allow_send_confirmation else '未启用', '本次尚无发送确认')
        with self._lock:
            if self.input_selecting:
                self._emit(callback, 'error', '请先确认或取消输入位置选择，本次未填入。')
                return
            if not self._chosen_input and self.selection.metadata.get('input_required'):
                self._emit(callback, 'error', '请重新确认输入位置；上次的临时位置不跨重启恢复。')
                return
            if requested_epoch is not None and requested_epoch != self._sync_epoch:
                self._record_fill(stage, "未完成")
                self._emit(callback, 'error', '目标已切换，本次未填入。')
                return
            self._pending_send = None
            epoch = self._sync_epoch
            target = self._selected
            token = self._focus_token
            persisted = self.selection.selected
        if expected_target is not None and (target is None or target.session_key() != expected_target):
            self._record_fill(stage, "未完成", target)
            self._emit(callback, 'error', '目标已变化，本次未填入，请重新长按旋钮。')
            return
        # 应用重启后 UI 只加载了持久化标签，第一次点“填入 Agent”时不应
        # 强迫用户再打开设置页刷新。后台直接枚举并恢复唯一匹配窗口。
        if target is None and persisted:
            try:
                stage = "恢复已选目标窗口"
                windows = _as_windows(self.backend.list_windows())
                target = self._resolve(windows)
                with self._lock:
                    self._windows = windows
                    self._selected = target
                    if target is None:
                        self._focus_token = None
            except TargetError as exc:
                self._record_fill(stage, "未完成", target)
                self._emit(callback, "error", str(exc) or "恢复目标窗口未完成，请在设置中复制诊断。")
                return
            except Exception:
                target = None
        if target is None:
            self._record_fill(stage, "未完成")
            self._emit(callback, "error", "目标输入框不可用，请点「填入」旁的目标下拉，刷新并重新选择。")
            return
        try:
            if receipt:
                stage = "核验目标窗口"
                validate = getattr(self.backend, "validate_window", None)
                if validate is not None:
                    current = validate(target)
                    if current is None:
                        raise TargetError("目标窗口已失效，请重新刷新并选择。")
                    target = TargetWindow.from_value(current)
                    with self._lock:
                        if epoch != self._sync_epoch:
                            raise TargetError('目标已切换，本次未填入。')
                        # 验证后的标题可能比列表旧标题新；保存本次真实窗口快照。
                        self._selected = target
            stage = "检查目标焦点"
            focus = self._capture_focus(target)
            if focus is False:
                raise TargetError("目标输入框已失去焦点，请重新点击目标输入框后再试。")
            if focus is not None:
                token = focus
            # 点击 Talk Man 的填入按钮后，目标窗口必然暂时失去前台焦点。
            # token 为空时交给平台后端重新寻找并聚焦主输入框；只有窗口会话
            # 已失效（focus is False）才应直接返回不可用提示。
            snapshot = self._prepare_send_snapshot(target) if receipt and allow_send_confirmation else None
            stage = "聚焦并填入"
            with self._lock:
                if epoch != self._sync_epoch or self.input_selecting:
                    raise TargetError('目标选择已变化，本次未填入。')
            result = self.backend.fill(target, text, token)
            message = result if result is not None else "已填入目标输入框。"
            if receipt:
                stage = "核验发送确认"
                specific_input = self.selected_input
                can_send = specific_input is None or specific_input.enter_send
                send_token = self._arm_filled_send(target, text, snapshot, epoch, allow_send=can_send) if allow_send_confirmation else ''
                if not can_send:
                    message = str(message) + f' 位置 {specific_input.number} 的提交方式尚未验证，请在目标内确认。'
                elif allow_send_confirmation and not send_token:
                    message = str(message) + ' 无法核对目标正文，请在目标内手动发送。'
                message = FillReceipt(str(message), text, target.app, target.title,
                                      datetime.now().astimezone().isoformat(timespec="seconds"), send_token=send_token)
            result_note = "已填入"
            if receipt and allow_send_confirmation:
                result_note += "；发送确认可用" if send_token else "；未取得发送确认"
            self._record_fill("目标输入框", result_note, target)
            self._emit(callback, "ok", message)
        except TargetError as exc:
            stage = self._fill_stage(stage)
            self._record_fill(stage, "未完成", target)
            self._emit(callback, "error", exc if isinstance(exc, InputLocationError) else
                       str(exc) or f"{stage}未完成，请在设置中复制诊断。")
        except Exception:
            stage = self._fill_stage(stage)
            self._record_fill(stage, "未完成（程序异常）", target)
            self._emit(callback, "error", "目标输入框不可用，请重新选择后再试。")

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    command, callback, value = self._queue.get(timeout=0.1)
                except queue.Empty:
                    self._monitor()
                    continue
                if command == "close":
                    break
                if command.startswith('input_'):
                    self._input_command(command, callback, value)
                    continue
                if command == "refresh":
                    self._refresh(callback)
                elif command == 'cycle':
                    self._cycle(callback, value)
                elif command == "fill":
                    self._fill(callback, value)
                elif command == "fill_receipt":
                    self._fill(callback, value[0], receipt=True, requested_epoch=value[1],
                               expected_target=value[2], allow_send_confirmation=value[3])
                elif command == 'sync':
                    self._sync(callback, value)
                elif command == 'send_filled':
                    self._send_filled(callback, value)
                elif command == 'send_current':
                    self._send_current(callback, value)
        finally:
            close_backend = getattr(self.backend, "close", None)
            if close_backend is not None:
                try:
                    close_backend()
                except Exception:
                    pass


__all__ = [
    "TARGET_FILE", "UNSUPPORTED_MESSAGE", "TargetError", "TargetWindow",
    "TargetSelection", "TargetController", "FillReceipt",
]
