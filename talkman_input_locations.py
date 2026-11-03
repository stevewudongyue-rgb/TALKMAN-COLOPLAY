"""一次目标选择事务：窗口和具体控件一起确认，原生控件只留在目标线程。"""
from dataclasses import dataclass
import uuid


@dataclass(frozen=True)
class InputRegion:
    number: int
    rect: tuple[int, int, int, int]
    enter_send: bool = False


def valid_rect(rect):
    return (isinstance(rect, (tuple, list)) and len(rect) == 4
            and all(isinstance(n, (int, float)) and abs(n) < 100000 for n in rect)
            and rect[2] >= 4 and rect[3] >= 4)


class InputLocationControllerMixin:
    @property
    def input_selecting(self):
        with self._lock:
            return getattr(self, '_input_selecting', False)

    @property
    def selected_input(self):
        from talkman_target import TargetError
        with self._lock:
            chosen = getattr(self, '_chosen_input', None)
            if not chosen and self.selection.metadata.get('input_required'):
                raise TargetError('请重新确认输入位置；本次不会使用默认主输入框。')
            return chosen[1] if chosen else None

    def begin_inputs(self, target, callback):
        with self._lock:
            self._input_generation = getattr(self, '_input_generation', 0) + 1
            generation = self._input_generation
            self._input_selecting = True
            self._input_draft = None
            self._pending_send = None
            self._sync_epoch += 1
        self._queue.put(('input_begin', callback, (generation, target)))

    def cancel_inputs(self):
        with self._lock:
            self._input_generation = getattr(self, '_input_generation', 0) + 1
            self._input_selecting = False
            self._input_draft = None

    def inspect_input(self, ticket, index, callback, *, confirm=False, enter_send=None):
        self._queue.put(('input_confirm' if confirm else 'input_inspect', callback, (ticket, index, enter_send)))

    def _input_send_default(self, target, handle):
        method = getattr(self.backend, 'input_supports_enter', None)
        return bool(callable(method) and method(target, handle) is True)

    def _bind_chosen_input(self, target):
        from talkman_target import TargetError
        with self._lock:
            chosen = getattr(self, '_chosen_input', None)
        bind = getattr(self.backend, 'bind_input', None)
        if chosen and chosen[0].session_key() != target.session_key():
            raise TargetError('已选输入位置所属窗口已变化，请重新选择位置。')
        if chosen and not callable(bind):
            raise TargetError('当前系统不支持指定输入位置，请重新选择。')
        if callable(bind):
            bind(target, chosen[2] if chosen else None)

    def _input_command(self, command, callback, value):
        from talkman_target import TargetError
        try:
            if command == 'input_begin':
                generation, target = value
                with self._lock:
                    if generation != self._input_generation or not self._input_selecting:
                        return
                backend = self.backend
                if not all(callable(getattr(backend, name, None)) for name in
                           ('list_inputs', 'inspect_input', 'bind_input', 'activate_window')):
                    raise TargetError('当前系统尚不支持可访问输入位置选择。')
                backend.bind_input(target, None)
                backend.activate_window(target)
                entries = backend.list_inputs(target)
                if not entries:
                    raise TargetError('未识别到可访问输入区域，请展开目标页面后重新选择。')
                if len(entries) > 32:
                    raise TargetError('可编辑区域过多，请先缩小目标页面范围后重新选择。')
                if any(not valid_rect(rect) for _, rect in entries):
                    raise TargetError('输入区域坐标无法验证，请重新选择。')
                regions = [InputRegion(i + 1, tuple(rect), self._input_send_default(target, handle))
                           for i, (handle, rect) in enumerate(entries)]
                ticket = uuid.uuid4().hex
                with self._lock:
                    if generation != self._input_generation or not self._input_selecting:
                        return
                    self._input_draft = (ticket, target, entries, generation)
                self._emit(callback, 'ok', dict(ticket=ticket, target=target, regions=regions))
                return
            ticket, index, enter_send = value
            with self._lock:
                draft = getattr(self, '_input_draft', None)
                if not draft or draft[0] != ticket or not self._input_selecting:
                    raise TargetError('本轮位置选择已失效，请重新选择。')
                _, target, entries, generation = draft
            if type(index) is not int or not 0 <= index < len(entries):
                raise TargetError('该编号不存在，请重新选择位置。')
            handle = entries[index][0]
            rect = self.backend.inspect_input(target, handle)
            if not valid_rect(rect):
                raise TargetError('输入区域已隐藏或移出可见范围，请重新选择。')
            enabled = self._input_send_default(target, handle) if enter_send is None else enter_send is True
            region = InputRegion(index + 1, tuple(rect), enabled)
            with self._lock:
                if generation != self._input_generation or self._input_draft is not draft:
                    return
                if command == 'input_confirm':
                    # Keep both choices atomic, including the focus-memory reset.
                    self._selected = target
                    self._chosen_input = (target, region, handle)
                    self._focus_token = None
                    self._pending_send = None
                    self._sync_epoch += 1
                    self.selection.set(target)
                    self.selection.metadata['input_required'] = '1'
                    self._input_draft = None
                    self._input_selecting = False
            warning = ''
            if command == 'input_confirm':
                # Native handles/ordinals are session-local, never persisted.
                try:
                    self.save()
                except Exception:
                    warning = ' 本次选择已生效，但目标设置未能保存。'
            self._emit(callback, 'ok', dict(target=target, region=region, warning=warning))
        except TargetError as error:
            self._emit(callback, 'error', str(error))
        except Exception:
            self._emit(callback, 'error', '输入区域无法确认，请重新选择；未填入任何文字。')


def navigation_instruction(text):
    """只接受完整、明确的导航口令，其他语句继续原来的 AI 指令流程。"""
    import re
    text = text.strip().rstrip('。.!！')
    direct = {'下一个输入框': ('step', 1), '上一个输入框': ('step', -1),
              '选择下一个输入框': ('step', 1), '选择上一个输入框': ('step', -1),
              '确认这个位置': ('confirm', 0), '确认输入位置': ('confirm', 0),
              '取消位置选择': ('cancel', 0)}
    if text in direct:
        return direct[text]
    match = re.fullmatch(r'选择第([一二三四五六七八九十]|[1-9][0-9]?)个输入框', text)
    if match:
        number = match[1]
        return 'index', (int(number) if number.isdigit() else '一二三四五六七八九十'.index(number) + 1) - 1
    return None
