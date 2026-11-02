#!/usr/bin/env python3
"""Talk Man 原生桌面编辑器：多家语音识别与选区语音修改。"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

if sys.platform == "win32" and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "TalkMan-libs"))

from talkman_qwen import QwenClient, QwenError, QwenSettings, TextEdit, load_settings
from talkman_voice import (VoiceSession, FileVoiceSession, VoicePermissionError,
                           prepare_microphone_permission)
from talkman_asr import ASRSettings, PROVIDERS, load_asr_settings, transcriber, validate
from talkman_assets import load_fonts, load_icons
from talkman_shortcuts import (DEFAULT_SHORTCUTS, ShortcutError, load_shortcuts, display_shortcut,
                              matches_event, released_event)
from talkman_magnifier import TextMagnifier
from talkman_target import TargetController, TargetError, TargetWindow, FillReceipt, UNSUPPORTED_MESSAGE, is_agent_window
from talkman_records import DeliveryHistory, delivery_destination, validate_layout_only
from talkman_settings_ui import SettingsPanelMixin
from talkman_ui_widgets import ColoPlayWordmark, MatrixStatus, PillButton, ToggleSwitch
from talkman_editor import RuledText
from talkman_window import DesktopWindowController
from talkman_global_hotkeys import voice_bindings
from talkman_target_picker import TargetPicker
from talkman_live_sync import LiveSync
from talkman_onboarding import QuickGuide, should_offer_guide
from talkman_mac_guide import MacSetupGuide, mac_guide_enabled, should_offer_mac_guide
from talkman_hover_help import HoverHelp, BUTTON_HELP
from talkman_ai_edit import AIEditMixin
from talkman_conventions import Conventions, is_remember_instruction
from talkman_conventions_ui import ConventionsUIMixin
from talkman_delivery_ui import DeliveryUIMixin
from talkman_free_service import FreeService, FreeClient, default_free_mode
from talkman_system_style import SystemStyle, set_app_identity
from talkman_translation_ui import TranslationUIMixin

THEMES = {
    "matrix": dict(bg="#000000", panel="#000000", input="#07130c", line="#254e35",
                 text="#9fe8b6", muted="#6b9f7b", accent="#6fffa0", select="#173b25",
                 button="#0a1b10", hover="#112c1c", pressed="#214b31", inverse="#031007",
                 signal="#6fffa0", signal_dim="#28573a", caret="#90ffb3", selection="#154b2a",
                 editor_border="#386e49", rule="#0e2819", review="#91ffc0",
                 recording="#74ffab", recording_text="#031007", recording_hover="#9dffbf",
                 recording_pressed="#4cd783", icon="green", accent_icon="black", recording_icon="black"),
    "dark": dict(bg="#000000", panel="#000000", input="#111111", line="#333333",
                 text="#e8e8e8", muted="#999999", accent="#ffffff", select="#333333",
                 button="#111111", hover="#222222", pressed="#333333", inverse="#000000",
                 signal="#63e994", signal_dim="#28573a", caret="#d71921", selection="#4c171c",
                 editor_border="#555555", rule="#202020", review="#e5484d",
                 recording="#d71921", recording_text="#ffffff", recording_hover="#c6161e",
                 recording_pressed="#b31219", icon="white", accent_icon="black", recording_icon="white"),
    "light": dict(bg="#f5f5f5", panel="#f5f5f5", input="#ffffff", line="#cccccc",
                  text="#1a1a1a", muted="#666666", accent="#000000", select="#dddddd",
                  button="#eeeeee", hover="#dddddd", pressed="#cccccc", inverse="#ffffff",
                  signal="#17653a", signal_dim="#578269", caret="#d71921", selection="#f7dadd",
                  editor_border="#aaaaaa", rule="#e0e0e0", review="#e5484d",
                  recording="#d71921", recording_text="#ffffff", recording_hover="#c6161e",
                  recording_pressed="#b31219", icon="black", accent_icon="white", recording_icon="white"),
}
THEME_LABELS = {'matrix':'骇客绿屏', 'dark':'Nothing 黑白 · 深色', 'light':'Nothing 黑白 · 浅色'}
COLORS = dict(THEMES["matrix"])


class AutoHideScrollbar(ttk.Scrollbar):
    """只在内容超出可视范围时显示窄滚动条。"""
    def set(self, first, last):
        if float(first) <= 0 and float(last) >= 1:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


def app_data_dir() -> Path:
    custom = os.environ.get("TALKMAN_DATA_DIR")
    if custom:
        path = Path(custom)
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TalkMan-COLOPLAY"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "TalkMan-COLOPLAY"
    else:
        path = Path.home() / ".local" / "share" / "TalkMan-COLOPLAY"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TextHistory:
    """每次语音/模型操作只记一步，临时转写不会污染回溯记录。"""
    def __init__(self, text=""):
        self.items = [text]
        self.index = 0

    def commit(self, text):
        if text != self.items[self.index]:
            self.items = self.items[:self.index + 1] + [text]
            self.items = self.items[-100:]
            self.index = len(self.items) - 1

    def undo(self):
        self.index = max(0, self.index - 1)
        return self.items[self.index]

    def redo(self):
        self.index = min(len(self.items) - 1, self.index + 1)
        return self.items[self.index]


class TalkManApp(TranslationUIMixin, DeliveryUIMixin, ConventionsUIMixin, AIEditMixin, SettingsPanelMixin, tk.Tk):
    def __init__(self, data_dir=None, session_factory=VoiceSession, client_factory=QwenClient, file_session_factory=FileVoiceSession, desktop_services=None):
        if session_factory is VoiceSession and desktop_services is not False:
            set_app_identity()
        super().__init__()
        COLORS.update(THEMES["matrix"])
        self.palette = COLORS
        self.theme_key = "matrix"
        self.fonts = load_fonts(self)
        self.icon_sets = {tone: load_icons(self, tone) for tone in ("white", "black", "red", "green")}
        self.icon_buttons = []
        self.title("TALKMAN COLOPLAY")
        self.geometry("1180x780")
        self.minsize(760, 520)
        self.configure(bg=COLORS["bg"])
        self.data_dir = Path(data_dir) if data_dir else app_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.target_available = sys.platform in ("win32", "darwin")
        self.target_platform_note = (
            "首次使用请在「系统设置 → 隐私与安全性 → 辅助功能」允许 TalkMan，再刷新窗口。"
            if sys.platform == "darwin" else
            "打开需要使用的 Agent 窗口，再点击刷新。" if self.target_available else UNSUPPORTED_MESSAGE)
        self.target_windows = {}
        self.target_filling = False
        try:
            self.target_controller = TargetController(self.data_dir)
            self.target_label_var = tk.StringVar(value=self.target_controller.label)
        except Exception as ex:
            self.target_controller = None
            self.target_available = False
            self.target_platform_note = str(ex) or "目标输入功能暂不可用，请复制全部后粘贴。"
            self.target_label_var = tk.StringVar(value="未选择目标输入框")
        self.history_path = self.data_dir / "history.json"
        self.ui_path = self.data_dir / "interface.json"
        self.settings_error = ""
        try:
            self.shortcuts = load_shortcuts(self.data_dir)
        except ShortcutError as ex:
            self.shortcuts = DEFAULT_SHORTCUTS.copy()
            self.settings_error = str(ex)
        try:
            preferences = json.loads(self.ui_path.read_text(encoding="utf-8"))
            if not isinstance(preferences, dict):
                preferences = {}
            magnifier_enabled = preferences.get("magnifier", False) is True
        except (OSError, ValueError, AttributeError):
            preferences = {}
            magnifier_enabled = False
        self.ui_preferences = preferences
        self.theme_key = preferences.get('appearance_theme', 'matrix')
        if not isinstance(self.theme_key, str) or self.theme_key not in THEMES:
            self.theme_key = 'matrix'
        COLORS.update(THEMES[self.theme_key])
        self.configure(bg=COLORS['bg'])
        self.appearance_choices = THEME_LABELS
        self.appearance_var = tk.StringVar(value=THEME_LABELS[self.theme_key])
        self.magnifier_var = tk.BooleanVar(value=magnifier_enabled)
        self.wheel_caret_var = tk.BooleanVar(value=preferences.get("wheel_caret", True) is not False)
        self.edge_hide_var = tk.BooleanVar(value=preferences.get("edge_hide", True) is not False)
        self.topmost_var = tk.BooleanVar(value=preferences.get("topmost", False) is True)
        self.global_voice_var = tk.BooleanVar(value=preferences.get("global_voice", True) is not False)
        self.only_agents_var = tk.BooleanVar(value=preferences.get("only_agents", True) is not False)
        self.sync_mode_var = tk.BooleanVar(value=preferences.get('sync_mode', False) is True)
        self.clear_after_fill_var = tk.BooleanVar(value=preferences.get('clear_after_fill', False) is True)
        delay = preferences.get('auto_delay', 5)
        self.auto_delay_var = tk.IntVar(value=delay if type(delay) is int and 1 <= delay <= 60 else 5)
        self.conventions = Conventions(self.data_dir)
        self.init_convention_learning()
        self.convention_panel = None
        self.term_layout_binding = None
        self._editor_selecting = False
        self._delivery_snapshot = None
        self.pending_delivery = None
        self.free_service = FreeService(self.data_dir)
        self.free_mode_var = tk.BooleanVar(value=default_free_mode(preferences))
        self.init_translation(preferences)
        self.sync_status_var = tk.StringVar()
        self.guide = None
        self.guide_job = None
        self.hover_help = HoverHelp(self)
        self.voice_gesture_var = tk.StringVar(value=preferences.get("voice_gesture", "both")
            if preferences.get("voice_gesture", "both") in ("both", "toggle", "hold") else "both")
        self._wheel_remainder = 0
        self.mic_key_down = None
        self.mic_key_pending = False
        self.qwen_credentials_error = ""
        self.asr_credentials_error = ""
        try:
            self.settings = load_settings(self.data_dir)
        except QwenError as ex:
            self.qwen_credentials_error = str(ex)
            try:
                self.settings = load_settings(self.data_dir, read_credentials=False)
            except QwenError:
                self.settings = QwenSettings()
            self.settings_error = str(ex)
        except Exception:
            self.settings = QwenSettings()
            self.settings_error = "设置未能读取，请重新填写千问 Key。"
        try:
            self.asr_settings = load_asr_settings(self.data_dir)
        except QwenError as ex:
            self.asr_credentials_error = str(ex)
            try:
                self.asr_settings = load_asr_settings(self.data_dir, read_credentials=False)
            except QwenError:
                self.asr_settings = ASRSettings()
            self.settings_error = "；".join(filter(None, (self.settings_error, str(ex))))
        self.free_mode_var.set(default_free_mode(preferences, own_model=bool(
            self.settings.api_key or self.asr_settings.credentials()
            or self.qwen_credentials_error or self.asr_credentials_error)))
        self.session_factory, self.client_factory = session_factory, client_factory
        self.file_session_factory = file_session_factory
        self.events = queue.Queue()
        self._mic_permission_pending = False
        self._mic_permission_request = 0
        self.operation_id = 0
        self.busy = False
        self.voice = None
        self.voice_from_device = False
        self.voice_mode = None
        self.voice_base = ""
        self.voice_start = self.voice_end = 0
        self.voice_selection = None
        self._had_selection = False
        self.voice_text = ""
        self.voice_stopping = False
        self.auto_job = self.capture_job = self.hold_job = None
        self.hold_key = None
        self.active_hold = None
        self.hold_pending_space = False
        self.voice_button_press = None
        self.clear_key_down = None
        self.closing = False
        self.revision = 0
        self.auto_request = 0
        self.auto_running = False
        self.auto_block_revision = -1
        self.ai_review = None
        # 一次去语气词先只标记候选，用户再次点击才提交到正文。
        self.filler_pending = None
        self.filler_hover_ranges = []
        self.timeline = TextHistory()
        self.saved_history = self.load_history()
        self.draft_id = None
        self.feedback_jobs = {}
        self.button_animation_jobs = set()
        self.record_mode = preferences.get('record_tab', 'notebook') if preferences.get('record_tab') in ('notebook', 'delivery') else 'notebook'
        self.record_views = {}
        self.delivery_error = ""
        try:
            self.deliveries = DeliveryHistory(self.data_dir)
        except OSError:
            self.deliveries = None
            self.delivery_error = "投递历史读取失败，请检查数据目录权限"
        self.preview_text = None
        self.pending_delete = None
        self.history_open = False
        initial_status = self.settings_error or self.conventions.error or "就绪 · 在设置中连接语音服务"
        self.status_var = tk.StringVar(value=initial_status)
        self.count_var = tk.StringVar(value="0 字")
        self.selection_var = tk.StringVar(value="鼠标框选后可语音修改")
        self.filler_var = tk.BooleanVar(value=preferences.get("auto_filler", True) is not False)
        self.punct_var = tk.BooleanVar(value=preferences.get("auto_punct", True) is not False)
        self.layout_var = tk.BooleanVar(value=preferences.get("auto_layout", True) is not False)
        self.rewrite_var = tk.BooleanVar(value=preferences.get("auto_rewrite", True) is not False)
        self.preview_ai_var = tk.BooleanVar(value=preferences.get("preview_ai", False) is True)
        self.edit_controls = []
        self.build_ui()
        self.build_convention_hint()
        self.magnifier = TextMagnifier(self.text, enabled=self.magnifier_var.get,
            allowed=lambda: not self.busy and not self.closing and not self.history_open and self.convention_panel is None,
            font_family=lambda: self.fonts.ui, colors=lambda: COLORS)
        self.build_history_panel()
        self.bind_shortcuts()
        self.refresh_shortcut_labels()
        self.refresh_history()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.poll_job = self.after(40, self.poll_events)
        self.text.focus_set()
        self.update_meta()
        self.window_controller = DesktopWindowController(self, native_enabled=(
            session_factory is VoiceSession if desktop_services is None else desktop_services))
        self.live_sync = LiveSync(self)
        self.system_style = SystemStyle(self, native=self.window_controller.native_enabled)
        self.live_sync.refresh()
        from talkman_coloplay import ColoPlayController
        self.coloplay = ColoPlayController(self)
        self.coloplay_status.configure(textvariable=self.coloplay.badge)
        self.title('TALKMAN COLOPLAY')
        offer_guide = (should_offer_mac_guide(preferences) if mac_guide_enabled()
                       else should_offer_guide(preferences, self.text_model_ready()))
        if self.window_controller.native_enabled and offer_guide:
            self.guide_job = self.after(500, self.show_quick_guide)
        if self.window_controller.native_enabled and self.free_mode_var.get():
            threading.Thread(target=self.free_service.warmup, daemon=True).start()

    def show_quick_guide(self):
        self.guide_job = None
        if self.busy or self.target_filling or self.closing or self.auto_running or self.ai_review is not None:
            return
        if self.guide and self.guide.opened:
            return
        settings = getattr(self, 'settings_window', None)
        if settings and settings.winfo_exists():
            self._close_settings(settings)
        self.window_controller.reveal(focus=False)
        self.guide = MacSetupGuide(self) if mac_guide_enabled() else QuickGuide(self)
        self.window_controller.sync_hotkeys()

    def set_sync_mode(self, enabled):
        if self.target_filling:
            self.status_var.set('请等待本次下发完成。')
            return False
        try:
            self.save_ui_preferences(sync_mode=bool(enabled))
        except OSError:
            self.status_var.set('同步设置保存失败。')
            return False
        self.sync_mode_var.set(bool(enabled))
        self.live_sync.configure(enabled)
        self.status_var.set('已打开同步；文字会出现在目标输入框，点击「下发」才按回车发送。' if enabled else '已关闭同步；「填入」只粘贴，不发送。')
        return True

    def save_ui_preferences(self, **changes):
        preferences = {**self.ui_preferences, **changes}
        temp = self.ui_path.with_suffix('.tmp')
        temp.write_text(json.dumps(preferences, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.ui_path)
        self.ui_preferences = preferences

    def text_model_ready(self):
        return self.free_service.configured if self.free_mode_var.get() else bool(self.settings.api_key)

    def make_text_client(self, settings=None):
        return FreeClient(self.free_service) if self.free_mode_var.get() else self.client_factory(settings or replace(self.settings))

    def save_window_preferences(self):
        self.save_ui_preferences(edge_hide=self.edge_hide_var.get(), topmost=self.topmost_var.get(),
                                 global_voice=self.global_voice_var.get(), voice_gesture=self.voice_gesture_var.get())
        self.window_controller.apply_preferences()

    def toggle_topmost(self):
        previous = self.topmost_var.get()
        self.topmost_var.set(not previous)
        try:
            self.save_window_preferences()
            self.status_var.set('已置顶 · 可在其他窗口旁继续输入' if not previous else '已取消置顶')
        except OSError:
            self.topmost_var.set(previous)
            self.status_var.set('置顶设置保存失败，请检查数据目录')

    def show_window_menu(self):
        self.capture_edit()
        old = getattr(self, 'window_menu', None)
        if old:
            old.destroy()
        menu = self.window_menu = tk.Menu(self, tearoff=False, bg=COLORS['input'], fg=COLORS['text'],
                                         activebackground=COLORS['select'], activeforeground=COLORS['text'],
                                         font=(self.fonts.ui, 11), borderwidth=1, relief='solid')
        # 菜单变量只显示勾选状态；实际偏好由现有保存流程更新。
        self.window_menu_pin = tk.BooleanVar(value=self.topmost_var.get())
        def run(command):
            menu.unpost()
            menu.grab_release()
            self.after_idle(command)
        menu.add_checkbutton(label='始终置顶', variable=self.window_menu_pin,
                             command=lambda: run(self.toggle_topmost))
        menu.add_separator()
        controller = self.window_controller
        self.window_menu_edge = tk.StringVar(value=controller.edge or '')
        for edge, label in (('top', '顶部'), ('left', '左侧'), ('right', '右侧'), ('bottom', '底部')):
            menu.add_radiobutton(label=f'收起到{label}', variable=self.window_menu_edge, value=edge,
                                 command=lambda edge=edge: run(lambda: controller.dock(edge)))
        menu.add_separator()
        menu.add_command(label='取消收起，恢复普通窗口', state='normal' if controller.edge else 'disabled',
                         command=lambda: run(controller.undock))
        try:
            menu.tk_popup(self.pin_button.winfo_rootx(),
                          self.pin_button.winfo_rooty()+self.pin_button.winfo_height()+4)
        finally:
            menu.grab_release()

    def icon_tone(self, style=''):
        return COLORS['recording_icon'] if style=='Recording.TButton' else COLORS['accent_icon'] if style=='Accent.TButton' else COLORS['icon']

    def button(self, parent, label, command=None, icon=None, tooltip=None, **kw):
        if icon:
            tone = self.icon_tone(kw.get('style'))
            kw.update(image=self.icon_sets[tone][icon], compound="left")
        button = ttk.Button(parent, text=label, takefocus=True, **kw)
        def invoke():
            button.state(['pressed'])
            def release():
                self.button_animation_jobs.discard(job)
                if button.winfo_exists():
                    button.state(['!pressed'])
            job = self.after(180, release)
            self.button_animation_jobs.add(job)
            return command()
        if command:
            button.configure(command=invoke)
        if icon:
            self.icon_buttons.append((button, icon))
            button.icon_only = not label
            button.base_icon = icon
        tooltip = tooltip or BUTTON_HELP.get(label)
        if tooltip:
            self.hover_help.attach(button, tooltip)
        return button

    def button_feedback(self, button, label):
        """只在操作完成后调用；短暂显示结果，不另开提示窗口。"""
        if not button.winfo_exists():
            return
        previous = self.feedback_jobs.pop(button, None)
        if previous:
            self.after_cancel(previous[0])
        original = previous[1] if previous else button.cget('text')
        if getattr(button, 'icon_only', False):
            button.feedback_icon = button.base_icon if any(word in label for word in ('失败', '未完成')) else 'check'
            tone = self.icon_tone(button.cget('style'))
            button.configure(image=self.icon_sets[tone][button.feedback_icon])
        else:
            button.configure(text=label)
        def restore():
            entry = self.feedback_jobs.pop(button, None)
            if entry and button.winfo_exists():
                button.configure(text=entry[1])
                if getattr(button, 'icon_only', False):
                    button.feedback_icon = None
                    tone = self.icon_tone(button.cget('style'))
                    button.configure(image=self.icon_sets[tone][button.base_icon])
        self.feedback_jobs[button] = (self.after(1100, restore), original)

    def set_button_label(self, button, label):
        """刷新常态文案时保留尚未结束的操作反馈。"""
        entry = self.feedback_jobs.get(button)
        if entry:
            self.feedback_jobs[button] = (entry[0], label)
        else:
            button.configure(text=label)

    def configure_style(self):
        style = ttk.Style(self)
        if style.theme_use() != "clam":
            style.theme_use("clam")
        style.configure(".", font=(self.fonts.ui, 10), background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["input"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", foreground=COLORS["muted"])
        style.configure('SettingsTitle.TLabel', font=(self.fonts.ui, 12, 'bold'), foreground=COLORS['text'])
        style.configure('SettingsField.TLabel', font=(self.fonts.ui, 10, 'bold'), foreground=COLORS['text'])
        style.configure('SettingsHelp.TLabel', font=(self.fonts.ui, 10), foreground=COLORS['muted'])
        style.configure('SettingsGroup.TFrame', borderwidth=1, relief='solid',
                        bordercolor=COLORS['line'], lightcolor=COLORS['line'], darkcolor=COLORS['line'])
        style.configure('Settings.TSeparator', background=COLORS['line'])
        style.configure("Panel.TLabel", background=COLORS["input"])
        style.configure("PanelMuted.TLabel", background=COLORS["input"], foreground=COLORS["muted"])
        style.configure("TButton", padding=(14, 9), background=COLORS["button"],
                        bordercolor=COLORS["line"], lightcolor=COLORS["button"],
                        darkcolor=COLORS["button"], borderwidth=1, relief="flat")
        style.map("TButton", background=[("pressed", COLORS["pressed"]), ("active", COLORS["hover"])],
                  foreground=[("disabled", COLORS["muted"])])
        style.configure("Ghost.TButton", background=COLORS["bg"], borderwidth=0, padding=(10, 8))
        style.configure('Device.Ghost.TButton', padding=(0, 2), foreground=COLORS['muted'])
        style.configure('Connected.Device.Ghost.TButton', foreground=COLORS['signal'])
        style.configure("Tool.TButton", background=COLORS["bg"], borderwidth=0, padding=(10, 8), width=0)
        style.configure("Outline.TButton", background=COLORS["bg"], borderwidth=1,
                        bordercolor=COLORS["line"], lightcolor=COLORS["bg"],
                        darkcolor=COLORS["bg"], padding=(10, 8))
        style.map("Outline.TButton", background=[("pressed", COLORS["bg"]), ("active", COLORS["bg"])],
                  bordercolor=[("active", COLORS["muted"])])
        style.configure("PanelGhost.TButton", background=COLORS["input"], borderwidth=0, padding=(10, 8))
        style.configure("Accent.TButton", background=COLORS["accent"], foreground=COLORS["inverse"], borderwidth=0)
        style.map("Accent.TButton", background=[("pressed", COLORS["text"]), ("active", COLORS["text"])],
                  foreground=[("disabled", COLORS["muted"])])
        style.configure("Recording.TButton", background=COLORS['recording'], foreground=COLORS['recording_text'])
        style.map("Recording.TButton", background=[("pressed", COLORS['recording_pressed']), ("active", COLORS['recording_hover'])])
        style.configure("TCheckbutton", background=COLORS["bg"])
        for choice_style in ('TCheckbutton', 'TRadiobutton'):
            style.configure(choice_style, indicatorbackground=COLORS['input'], indicatorforeground=COLORS['accent'],
                            upperbordercolor=COLORS['line'], lowerbordercolor=COLORS['line'])
            style.map(choice_style, indicatorbackground=[('active', COLORS['hover'])],
                      indicatorforeground=[('disabled', COLORS['muted'])])
        style.configure('RecordTab.TRadiobutton', padding=(12, 8), background=COLORS['input'])
        style.map('RecordTab.TRadiobutton', foreground=[('selected', COLORS['inverse']), ('!selected', COLORS['muted'])],
                  background=[('selected', COLORS['accent']), ('!selected', COLORS['input'])])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])
        style.configure("Treeview", rowheight=58, background=COLORS["input"],
                        fieldbackground=COLORS["input"], foreground=COLORS["text"],
                        borderwidth=0, relief="flat")
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map("Treeview", background=[("selected", COLORS["select"])],
                  foreground=[("selected", COLORS["text"])])
        style.configure("TEntry", fieldbackground=COLORS["input"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"], padding=6, bordercolor=COLORS["line"],
                        lightcolor=COLORS["input"], darkcolor=COLORS["input"])
        style.map("TEntry", bordercolor=[("focus",COLORS["muted"])])
        style.configure("TCombobox", fieldbackground=COLORS["input"], background=COLORS["button"],
                        foreground=COLORS["text"], arrowcolor=COLORS["text"], padding=6,
                        bordercolor=COLORS["line"], lightcolor=COLORS["input"], darkcolor=COLORS["input"])
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["input"])],
                  foreground=[("readonly", COLORS["text"])], selectbackground=[("readonly", COLORS["select"])],
                  selectforeground=[("readonly", COLORS["text"])])
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0,
                        bordercolor=COLORS["bg"], lightcolor=COLORS["bg"], darkcolor=COLORS["bg"])
        style.configure("TNotebook.Tab", background=COLORS["bg"], padding=(20, 12),
                        bordercolor=COLORS["bg"], lightcolor=COLORS["bg"], darkcolor=COLORS["bg"])
        style.map("TNotebook.Tab", background=[("selected", COLORS["accent"])],
                  foreground=[("selected", COLORS["inverse"])], padding=[("selected",(20,12))],
                  lightcolor=[("selected",COLORS["accent"]),("!selected",COLORS["bg"])],
                  darkcolor=[("selected",COLORS["accent"]),("!selected",COLORS["bg"])],
                  bordercolor=[("selected",COLORS["accent"]),("!selected",COLORS["bg"])])
        style.layout("Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": True, "sticky": "nswe"})]})])
        style.configure("Vertical.TScrollbar", width=6, arrowsize=0, borderwidth=0,
                        troughcolor=COLORS["bg"], background=COLORS["line"],
                        bordercolor=COLORS["bg"], lightcolor=COLORS["line"], darkcolor=COLORS["line"])
        style.map("Vertical.TScrollbar", background=[("active", COLORS["muted"]), ("pressed", COLORS["muted"])])
        for option, color in (("background", "input"), ("foreground", "text"),
                              ("selectBackground", "select"), ("selectForeground", "text")):
            self.option_add("*TCombobox*Listbox." + option, COLORS[color])

    def toggle_theme(self):
        themes = tuple(THEMES)
        self.set_theme(themes[(themes.index(self.theme_key)+1)%len(themes)])

    def set_theme(self, key):
        if key not in THEMES:
            return False
        try:
            self.save_ui_preferences(appearance_theme=key)
        except OSError:
            self.appearance_var.set(THEME_LABELS[self.theme_key])
            self.status_var.set('主题未保存，请检查数据目录；当前样式保留。')
            return False
        self.hover_help.hide()
        self.theme_key = key
        self.appearance_var.set(THEME_LABELS[key])
        COLORS.update(THEMES[self.theme_key])
        self.system_style.set_theme(self.theme_key)
        self.configure_style()
        self.configure(bg=COLORS["bg"])
        self.magnifier.hide()
        win = getattr(self, "settings_window", None)
        if win and win.winfo_exists():
            win.configure(bg=COLORS["bg"])
            self.settings_viewport.canvas.configure(bg=COLORS['bg'])
        self.history_panel.configure(bg=COLORS["input"], highlightbackground=COLORS["line"])
        if self.ai_review is not None:
            self.ai_review.configure(bg=COLORS['line'])
            self.ai_review_text.configure(bg=COLORS['bg'], fg=COLORS['text'],
                selectbackground=COLORS['select'], selectforeground=COLORS['text'])
            self.ai_review_text.tag_configure('review_added', foreground=COLORS['review'])
        self.text.tag_configure('review_removed', foreground=COLORS['review'])
        if self.convention_panel is not None:
            self.convention_panel.configure(bg=COLORS['line'])
        for widget, background in ((self.text, "bg"), (self.preview_widget, "input")):
            widget.configure(bg=COLORS[background], fg=COLORS["text"], insertbackground=COLORS["accent"],
                             selectbackground=COLORS["select"], selectforeground=COLORS["text"])
        for line in self.surface_lines:
            line.configure(bg=COLORS["line"])
        self.empty_hint.configure(bg=COLORS["bg"], fg=COLORS["muted"])
        self.text.tag_configure("live", background=COLORS["select"], foreground=COLORS["text"])
        self.configure_editor_palette()
        self.text.tag_configure("filler_candidate", foreground=COLORS['review'], underline=True)
        self.text.tag_configure('filler_pending', foreground=COLORS['review'], underlinefg=COLORS['review'])
        for button, icon in self.icon_buttons:
            if button.winfo_exists():
                tone = self.icon_tone(str(button.cget('style')))
                if button == self.mic_button:
                    icon = "square" if self.busy else "mic"
                icon = getattr(button, 'feedback_icon', None) or icon
                button.configure(image=self.icon_sets[tone][icon])
        for widget in (self.wordmark, self.matrix_status, self.mic_button, self.replace_button, self.rewrite_button):
            widget.refresh()
        if self.guide and self.guide.opened:
            self.guide.refresh_theme()
        if hasattr(self, 'coloplay'):
            self.coloplay.editor_focus.refresh()
        version = getattr(self, 'version_text', None)
        if version is not None and version.winfo_exists():
            version.configure(bg=COLORS['bg'], fg=COLORS['text'])
        return True

    def build_ui(self):
        self.configure_style()
        self._device_controls_hidden = False
        self._compact_layout = False
        self.surface_lines = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(24, 16, 24, 8))
        self.full_header = header
        header.grid(row=0, column=0, sticky="ew")
        brand = ttk.Frame(header)
        brand.pack(side="left")
        self.wordmark = ColoPlayWordmark(brand, palette=lambda: COLORS, width=310, height=30)
        self.wordmark.pack(anchor='w')
        edition = ttk.Frame(brand)
        edition.pack(anchor='w', pady=(4, 0))
        self.coloplay_status = self.button(edition, '○ 未连接',
            lambda: self.coloplay.settings(), style='Device.Ghost.TButton',
            tooltip='COLOPLAY 连接状态；点击打开设备设置。')
        self.coloplay_status.pack(side='left')
        self.matrix_status = MatrixStatus(edition, palette=lambda: COLORS,
            font=self.fonts.ui, mono=self.fonts.mono)
        self.matrix_status.pack(side='left', padx=(12, 0))
        self.hover_help.attach(self.matrix_status, '字符雨表示麦克风已就绪、正在录音，不代表音量或识别进度。')
        self.theme_button = self.button(header, "", self.toggle_theme, icon="sun",
                                       tooltip="切换绿屏／黑白深浅色；设置 → COLOPLAY 可选择主题。", style="Tool.TButton")
        self.theme_button.pack(side="right")
        self.settings_button = self.button(header, "", self.open_settings,
                                          icon="settings", style="Tool.TButton", tooltip=BUTTON_HELP['设置'])
        self.settings_button.pack(side="right", padx=(8, 8))
        self.pin_button = self.button(header, "置顶 ▾", self.show_window_menu, style="Ghost.TButton",
                                      tooltip="置顶／收起到顶部或左右侧／取消收起")
        self.pin_button.pack(side="right", padx=(8, 0))
        self.records_button = self.button(header, "记录", self.toggle_records, style="Ghost.TButton",
            tooltip="草稿簿存放还没发出去的文字；历史记录填入内容、时间和目标。")
        self.records_button.pack(side="right")
        self.provider_var = tk.StringVar(value=('代码口述 · ' if self.conventions.language != 'off' else '') + self._provider_label())

        body = ttk.Frame(self, padding=(24, 0, 24, 0))
        self.editor_body = body
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        tools = ttk.Frame(body, padding=(0, 8, 0, 8))
        self.full_editor_tools = tools
        tools.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.editor_title = ttk.Label(tools, text="预输入", font=(self.fonts.ui, 18))
        self.editor_title.pack(side="left")
        self.count_label = ttk.Label(tools, textvariable=self.count_var, style="Muted.TLabel")
        self.count_label.pack(side="left", padx=(16, 0))
        for label, fn, icon in reversed([("上一步",self.undo,"undo-2"), ("下一步",self.redo,"redo-2"),
                                        ("保存",self.save_current,"save"), ("复制全部",self.copy_all,"copy")]):
            b = self.button(tools, "", fn, icon=icon,
                            tooltip=BUTTON_HELP[label], style="Tool.TButton")
            b.pack(side="right", padx=(4, 0))
            self.edit_controls.append(b)
            if icon == 'save':
                self.save_button = b
            elif icon == 'copy':
                self.copy_button = b
        self.clear_button = self.button(tools, "", self.clear_text, icon="trash-2",
                                        tooltip=lambda: "清空编辑区 · " + display_shortcut(self.shortcuts.get("clear", ""))
                                        + "（可用上一步恢复）",
                                        style="Tool.TButton")
        self.clear_button.pack(side="right", padx=(4, 0))
        self.edit_controls.append(self.clear_button)
        self.backspace_button = self.button(tools, "", self.backspace_text, icon='delete',
            tooltip="删除选中文字；没有选区时删除光标前一个字，可用上一步恢复",
            style="Tool.TButton")
        self.backspace_button.pack(side="right", padx=(4, 0))
        self.edit_controls.append(self.backspace_button)
        self.remember_button = self.button(tools, '', self.remember_selection, icon='bookmark-plus',
            style='Tool.TButton', tooltip='记住这个词：先选中一个词，再保存正确写法和常见误写。')
        self.remember_button.pack(side='right', padx=(16, 12))
        self.remember_button.state(['disabled'])
        line = tk.Frame(body, bg=COLORS["line"], height=1)
        line.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.surface_lines.append(line)
        self.editor_hint_line = line

        self.editor_surface = ttk.Frame(body)
        self.editor_surface.grid(row=2, column=0, sticky="nsew")
        self.editor_surface.rowconfigure(0, weight=1)
        self.editor_surface.columnconfigure(0, weight=1)
        self.editor_surface.columnconfigure(1, minsize=8)
        self.text = RuledText(self.editor_surface, width=20, height=8, wrap="word", undo=False, auto_fit=True,
            font=(self.fonts.ui, 16), bg=COLORS["bg"], fg=COLORS["text"],
            insertbackground=COLORS["accent"], selectbackground=COLORS["select"],
            selectforeground=COLORS["text"], exportselection=False, padx=16, pady=18,
            spacing1=4, spacing3=6, relief="flat", borderwidth=0, highlightthickness=0)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = AutoHideScrollbar(self.editor_surface, command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=24)
        self.text.configure(yscrollcommand=sb.set)
        self.text.tag_configure("live", background=COLORS["select"], foreground=COLORS["text"])
        self.configure_editor_palette()
        self.text.tag_configure("filler_pending", foreground=COLORS['review'],
                                underline=True, underlinefg=COLORS['review'])
        self.text.bind("<<Modified>>", self.on_text_modified)
        self.text.bind("<ButtonRelease-1>", self.editor_selection_finished, add=True)
        self.bind("<ButtonRelease-1>", self.editor_selection_finished, add=True)
        self.text.bind("<<Selection>>", lambda e:self.update_meta(), add=True)
        self.text.bind("<KeyRelease>", self.key_release)
        self.text.bind("<KeyPress>", self.key_press)
        self.text.bind("<ButtonPress-1>", self.editor_clicked, add=True)
        self.text.bind("<B1-Motion>", lambda e: "break" if self.busy else None, add=True)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.text.bind(sequence, self.editor_wheel)
        self.empty_hint = tk.Label(body, text="把想说的，先放在这里。",
            font=(self.fonts.ui, 10), bg=COLORS["bg"], fg=COLORS["muted"], anchor="w", padx=6)
        self.empty_hint.bind("<ButtonPress-1>", self.editor_clicked)
        self.empty_hint.place(in_=self.editor_hint_line, x=16, y=0, anchor="w")
        self.editor_surface.bind("<Configure>", self.layout_history, add=True)
        self.bind("<Configure>", self.layout_history, add=True)
        meta = ttk.Frame(body, padding=(0, 8, 0, 12))
        self.full_editor_meta = meta
        meta.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.selection_label = ttk.Label(meta, textvariable=self.selection_var, style="Muted.TLabel")
        self.selection_label.pack(side="left")
        self.provider_label = ttk.Label(meta, textvariable=self.provider_var, style="Muted.TLabel")
        self.provider_label.pack(side="right")

        dock = ttk.Frame(self, padding=(24, 0, 24, 0))
        self.full_voice_dock = dock
        dock.grid(row=2, column=0, sticky="ew")
        dock.columnconfigure((0,1), weight=1, uniform="voice")
        visible_column = 0
        self.voice_hint_widgets = []
        for column, (name, label, mode) in enumerate((
                ("mic_button","开始输入","insert"),
                ("replace_button","录音和覆盖","replace"),
                ("rewrite_button","指令修改","rewrite"))):
            cell = ttk.Frame(dock)
            if mode != "insert":
                cell.grid(row=0,column=visible_column,sticky="ew",padx=(0,12) if visible_column == 0 else 0)
                visible_column += 1
            button = PillButton(cell,text=label,command=self.toggle_mic if mode=="insert" else None,
                                palette=lambda:COLORS,font=(self.fonts.ui,11),
                                style="Accent.TButton" if mode in ("insert", "replace") else "TButton",
                                image=self.icon_sets[self.icon_tone('Accent.TButton')]["mic"] if mode=="insert" else None)
            setattr(self,name,button)
            button.pack(fill="x")
            if mode=="insert":
                self.icon_buttons.append((button,"mic"))
                cell.grid_remove()
            else:
                self.bind_voice_button(button,mode)
            caption = tk.StringVar()
            setattr(self,name+"_hint",caption)
            hint = ttk.Label(cell,textvariable=caption,style="Muted.TLabel",anchor="center",wraplength=300)
            hint.pack(fill="x",pady=(6,0))
            self.voice_hint_widgets.append(hint)

        bottom = ttk.Frame(self,padding=(24,12,24,12))
        self.full_bottom = bottom
        bottom.grid(row=3,column=0,sticky="ew")
        switches = ttk.Frame(bottom)
        switches.pack(fill="x")
        self.one_filler_button = self.button(switches, "AI 整理一次", self.organize_once,
                                              icon="brain", style="Outline.TButton")
        self.one_filler_button.pack(side="left", padx=(0, 8))
        self.build_translation(switches)
        self.hover_help.attach(self.one_filler_button, "让 AI 整理全文的口误、标点和分行，尽量保持你的说话方式。可在设置中开启改动对照。")
        delivery_row = ttk.Frame(bottom)
        delivery_row.pack(in_=switches, side='right')
        self.target_picker = TargetPicker(self)
        self.target_button = self.button(delivery_row, "填入 Agent", self.fill_to_target,
                                         icon="clipboard-paste", style="Ghost.TButton")
        self.target_button.pack(side="right")
        self.target_selector = self.target_picker.button(delivery_row)
        self.target_selector.pack(side="right", padx=(0, 8))
        footer_feedback = ttk.Frame(bottom)
        footer_feedback.pack(fill='x', pady=(6, 0))
        self.cancel_button = self.button(footer_feedback,"取消本次 · Esc",self.cancel_operation,style="Ghost.TButton")
        self.status_label = ttk.Label(footer_feedback,textvariable=self.status_var,style="Muted.TLabel",wraplength=600)
        self.status_label.pack(side='left', fill='x', expand=True)
        footer_feedback.bind("<Configure>",lambda e:self.status_label.configure(wraplength=max(200,e.width-170)))

        # 紧凑模式复用同一个正文组件，选区、光标、事务和撤销不做镜像复制。
        self.compact_header = ttk.Frame(self, padding=(14, 8, 14, 6))
        ttk.Label(self.compact_header, text='快速输入', font=(self.fonts.ui, 11)).pack(side='left')
        self.button(self.compact_header, '收起', self.collapse_compact,
                    style='Ghost.TButton').pack(side='right')
        self.button(self.compact_header, '完整窗口 ↗', lambda: self.window_controller.reveal(focus=True),
                    style='Ghost.TButton').pack(side='right', padx=(6, 4))
        self.compact_bottom = ttk.Frame(self, padding=(14, 6, 14, 10))
        row = ttk.Frame(self.compact_bottom)
        row.pack(fill='x')
        self.compact_record = self.button(row, '录音', lambda: self.window_controller.trigger('global_record'),
                                          style='Accent.TButton')
        self.compact_record.pack(side='left')
        self.compact_rewrite = self.button(row, '指令', lambda: self.window_controller.trigger('global_rewrite'),
                                           style='Ghost.TButton')
        self.compact_rewrite.pack(side='left', padx=4)
        self.compact_cancel = self.button(row, '取消', self.cancel_operation, style='Ghost.TButton')
        self.compact_cancel.pack(side='left')
        delivery = ttk.Frame(self.compact_bottom)
        delivery.pack(fill='x', pady=(6, 0))
        self.compact_fill = self.button(delivery, '填入', self.fill_to_target, icon='clipboard-paste',
                                        tooltip=lambda: '向已核对的目标输入框按回车发送' if self.sync_mode_var.get() or self.pending_delivery else '先填入目标，再点一次「下发」才发送', style='Ghost.TButton')
        self.compact_fill.pack(side='right')
        self.compact_target_selector = self.target_picker.button(delivery, compact=True)
        self.compact_target_selector.pack(side='right', padx=(0, 8))
        self.compact_status = ttk.Label(self.compact_bottom, textvariable=self.status_var,
                                        style='Muted.TLabel', wraplength=440)
        self.compact_status.pack(fill='x', pady=(6, 0))
        ttk.Label(delivery, textvariable=self.sync_status_var, style='Muted.TLabel').pack(side='left')
        self.compact_bottom.bind('<Configure>', lambda e: self.compact_status.configure(wraplength=max(200, e.width-28)))
        for widget, help_text in (
            (self.wordmark, 'TALKMAN COLOPLAY：先说出来，检查修改，再把文字放进别的应用。'),
            (self.editor_title, '先把想说的放在这里，确认后再送到别的应用。这里就是你的文字草稿框。'),
            (self.count_label, '这里显示当前文字的字数。'),
            (self.selection_label, '选中文字后可以覆盖或指令修改；没有选字时，就从光标位置开始。'),
            (self.provider_label, '这是当前把语音转成文字的服务，可以在设置中更换。'),
            (self.replace_button, '没选字就从光标接着写；选中一段就用你说的新话替换它。点一次开始、再点结束，也可以按住说话。'),
            (self.rewrite_button, '选一段，说“把苹果改成梨”或“移到最后一句前面”；没有选字时，也可以修改光标前后明确的文字。'),
            (self.target_button, lambda: '核对同步的文字，再向目标按回车发送。' if self.sync_mode_var.get()
                else '核对刚填入的目标正文后按回车；目标有变化时停止。' if self.pending_delivery
                else '先把文字填入目标，成功后按钮变成「下发」；再点一次才按回车发送。'),
        ):
            self.hover_help.attach(widget, help_text)

    def set_device_controls_hidden(self, hidden):
        """只改变布局；保留按钮事件、录音事务、正文和撤销记录。"""
        hidden = bool(hidden)
        if self._device_controls_hidden == hidden:
            return
        self._device_controls_hidden = hidden
        if hidden:
            self.full_voice_dock.grid_remove()
            self.one_filler_button.pack_forget()
            self.compact_record.pack_forget()
            self.compact_rewrite.pack_forget()
        else:
            if not self._compact_layout:
                self.full_voice_dock.grid()
            self.one_filler_button.pack(side='left', padx=(0, 8), before=self.translation_tools)
            self.compact_record.pack(side='left', before=self.compact_cancel)
            self.compact_rewrite.pack(side='left', padx=4, before=self.compact_cancel)

    def set_compact_layout(self, enabled):
        self._compact_layout = bool(enabled)
        self.target_picker.close()
        widgets = (self.full_header, self.full_editor_tools, self.full_editor_meta,
                   self.full_voice_dock, self.full_bottom)
        for widget in widgets:
            widget.grid_remove() if enabled else widget.grid()
        self.editor_body.configure(padding=(14, 8, 14, 0) if enabled else (32, 0, 32, 0))
        if enabled:
            self.compact_header.grid(row=0, column=0, sticky='ew')
            self.compact_bottom.grid(row=2, column=0, sticky='ew')
            self.text.see('insert')
        else:
            self.compact_header.grid_remove()
            self.compact_bottom.grid_remove()
        if self._device_controls_hidden:
            self.full_voice_dock.grid_remove()
        self.refresh_compact_controls()

    def refresh_compact_controls(self):
        if not hasattr(self, 'compact_record'):
            return
        for button, mode, label in ((self.compact_record, 'record', '录音'),
                                     (self.compact_rewrite, 'rewrite', '指令')):
            active = self.voice is not None and not self.voice_stopping and (
                (self.voice_mode == 'rewrite') == (mode == 'rewrite'))
            button.configure(text='结束录音' if active else label,
                             style='Recording.TButton' if active else 'Accent.TButton' if mode == 'record' else 'Ghost.TButton')
            button.state(['!disabled'] if active or (not self.busy and not self._mic_permission_pending) else ['disabled'])
        self.compact_cancel.state(['!disabled'] if self.busy or self.auto_running else ['disabled'])
        self.compact_fill.state(['disabled'] if self.busy or self.target_filling else ['!disabled'])
        self.target_picker.update_buttons()
        if hasattr(self, 'live_sync'):
            self.live_sync.refresh()

    def collapse_compact(self):
        if not self.window_controller.hide():
            self.status_var.set('请先结束或取消本轮录音／处理，再收起')

    def configure_editor_palette(self):
        selection = COLORS['selection']
        self.text.set_editor_palette(COLORS["bg"], COLORS["text"],
            COLORS['editor_border'], COLORS['rule'], COLORS['caret'])
        self.text.configure(selectbackground=selection, inactiveselectbackground=selection,
                            selectforeground=COLORS["text"], insertbackground=COLORS['caret'])
        for tag in ("live", "operation"):
            self.text.tag_configure(tag, background=selection, foreground=COLORS["text"],
                                    underline=True, underlinefg=COLORS['caret'])

    def emphasize_voice_range(self, start, end):
        self.text.tag_remove("operation", "1.0", "end")
        self.text.tag_remove("sel", "1.0", "end")
        if end > start:
            first, last = f"1.0 + {start} chars", f"1.0 + {end} chars"
            self.text.tag_add("sel", first, last)
            self.text.tag_add("operation", first, last)
            self.text.tag_raise("operation")
            self.text.set_operation_caret(None)
        else:
            self.text.set_operation_caret(f"1.0 + {start} chars")

    def editor_clicked(self, _event=None):
        self.window_controller.keep_expanded()
        if self.busy or self.ai_review is not None or self.convention_panel is not None:
            return "break"
        self._editor_selecting = _event is not None and _event.widget == self.text
        self.cancel_auto_timer()
        if self.history_open:
            self.close_history()
        self.empty_hint.place_forget()
        self.text.focus_set()
        if not self._editor_selecting:
            self.text.resume_fit_after_interaction()

    def editor_selection_finished(self, _event=None):
        if not self._editor_selecting:
            return
        self._editor_selecting = False
        self.text.resume_fit_after_interaction()
        # Tk 的默认鼠标绑定随后才完成选区更新。
        self.after_idle(self._selection_settled)

    def _selection_settled(self):
        if not self.closing:
            self.update_meta()
            self.schedule_auto()

    def editor_navigation_allowed(self):
        return not (self.busy or self.closing or self.history_open or self._mic_permission_pending
                    or self.convention_panel is not None or self.ai_review is not None)

    def move_editor_cursor(self, delta):
        if not self.editor_navigation_allowed() or not delta:
            return False
        self.cancel_hold()
        self.magnifier.hide()
        selection = self.get_selection()
        remaining = abs(delta)
        direction = 1 if delta > 0 else -1
        if selection:
            index = selection[1] if direction > 0 else selection[0]
            remaining -= 1
        else:
            index = self.text.index("insert")
        index = self.text.index(f"{index} + {direction * remaining} chars")
        if self.text.compare(index, ">", "end-1c"):
            index = self.text.index("end-1c")
        self.text.tk.call("tk::TextSetCursor", self.text._w, index)
        self.text.focus_set()
        self.update_meta()
        return True

    def focus_at_pointer(self):
        if not self.editor_navigation_allowed():
            return "break"
        self.cancel_hold()
        self.magnifier.hide()
        px, py = self.text.winfo_pointerxy()
        x, y = px - self.text.winfo_rootx(), py - self.text.winfo_rooty()
        if 0 <= x < self.text.winfo_width() and 0 <= y < self.text.winfo_height():
            hit = self.winfo_containing(px, py)
            if hit == self.text or (hit is not None and hit.master == self.text):
                # 与原生鼠标点击一致，取最近的字间空隙，避免总落在字符左侧。
                index = self.text.tk.call("tk::TextClosestGap", self.text._w, x, y)
                self.text.tk.call("tk::TextSetCursor", self.text._w, index)
        self.text.focus_set()
        if hasattr(self, 'coloplay'):
            self.coloplay.editor_focus.enter(reset=True)
        self.text.resume_fit_after_interaction()
        self.update_meta()
        return "break"

    def focus_shortcut(self, event):
        if (event.widget.winfo_toplevel() == self
                and matches_event(self.shortcuts.get("focus", ""), event)):
            return self.focus_at_pointer()

    def editor_wheel(self, event):
        if self.busy or self.closing or self._mic_permission_pending:
            self._wheel_remainder = 0
            return "break"
        if self.history_open or self.focus_get() != self.text:
            self._wheel_remainder = 0
            return None
        state = int(getattr(event, "state", 0))
        modifier_mask = (4 | 8 | 16) if sys.platform == "darwin" else (4 | 8 | 0x20000)
        if state & modifier_mask:
            self._wheel_remainder = 0
            return None
        number = getattr(event, "num", None)
        raw = 120 if number == 4 else -120 if number == 5 else int(getattr(event, "delta", 0))
        if not raw:
            return "break"
        if state & 1:
            self._wheel_remainder = 0
            units = (1 if raw > 0 else -1) * max(1, abs(raw) // 120)
            self.text.yview_scroll(-units * 3, "units")
            return "break"
        if not self.wheel_caret_var.get():
            self._wheel_remainder = 0
            return None
        if sys.platform == "darwin" and number not in (4, 5):
            steps = raw
        else:
            self._wheel_remainder += raw
            steps = int(self._wheel_remainder / 120)
            self._wheel_remainder -= steps * 120
        if steps:
            self.move_editor_cursor(-steps)
        return "break"

    def build_history_panel(self):
        self.history_panel = tk.Frame(self,bg=COLORS["input"],
                                      highlightthickness=1,highlightbackground=COLORS["line"])
        box = ttk.Frame(self.history_panel,style="Panel.TFrame",padding=16)
        box.pack(fill="both",expand=True)
        box.columnconfigure(0,weight=1)
        box.rowconfigure(2,weight=1)
        heading = ttk.Frame(box,style="Panel.TFrame")
        heading.grid(row=0,column=0,sticky="ew",pady=(0,8))
        self.history_heading = ttk.Label(heading,text="草稿簿",style="Panel.TLabel",font=(self.fonts.ui,18))
        self.history_heading.pack(side="left")
        self.record_tab_var = tk.StringVar(value=self.record_mode)
        tabs = ttk.Frame(heading, style='Panel.TFrame')
        tabs.pack(side='left', padx=(16, 0))
        style = ttk.Style(self)
        style.configure('RecordTab.TRadiobutton', padding=(12, 8), background=COLORS['input'])
        style.layout('RecordTab.TRadiobutton', [('Radiobutton.border', {'sticky': 'nswe', 'children': [
            ('Radiobutton.padding', {'sticky': 'nswe', 'children': [('Radiobutton.label', {'sticky': 'nswe'})]})]})])
        self.history_button = ttk.Radiobutton(tabs, text='草稿簿', variable=self.record_tab_var, value='notebook',
            command=lambda: self.open_history('notebook'), style='RecordTab.TRadiobutton')
        self.delivery_button = ttk.Radiobutton(tabs, text='历史', variable=self.record_tab_var, value='delivery',
            command=lambda: self.open_history('delivery'), style='RecordTab.TRadiobutton')
        self.history_button.pack(side='left')
        self.delivery_button.pack(side='left')
        self.hover_help.attach(self.history_button, '草稿簿存放还没发出去的文字，选中后可预览和恢复。')
        self.hover_help.attach(self.delivery_button, '历史记录已填入的文字、时间和目标；已填入不代表已发送。')
        self.history_count_var = tk.StringVar()
        ttk.Label(heading,textvariable=self.history_count_var,style="PanelMuted.TLabel").pack(side="left",padx=12)
        self.button(heading,"收起  ×",self.close_history,style="PanelGhost.TButton").pack(side="right")
        self.history_status = tk.StringVar()
        self.history_status_label = ttk.Label(box,textvariable=self.history_status,
                                             style="PanelMuted.TLabel",wraplength=600)
        self.history_status_label.grid(row=1,column=0,sticky="ew",pady=(0,8))
        content = ttk.Frame(box,style="Panel.TFrame")
        content.grid(row=2,column=0,sticky="nsew")
        content.rowconfigure(0,weight=1)
        content.columnconfigure(0,weight=0,minsize=210)
        content.columnconfigure(1,weight=1)
        listing = ttk.Frame(content,style="Panel.TFrame")
        listing.grid(row=0,column=0,sticky="nsew",padx=(0,16))
        listing.rowconfigure(0,weight=1)
        listing.columnconfigure(0,weight=1)
        self.history_list = ttk.Treeview(listing,show="tree",height=5,selectmode="browse",takefocus=True)
        self.history_list.column("#0",width=190,minwidth=170,stretch=True)
        self.history_list.grid(row=0,column=0,sticky="nsew")
        hs = AutoHideScrollbar(listing,command=self.history_list.yview)
        hs.grid(row=0,column=1,sticky="ns")
        self.history_list.configure(yscrollcommand=hs.set)
        self.history_list.bind("<<TreeviewSelect>>",self.preview_history)
        self.preview_panel = ttk.Frame(content,style="Panel.TFrame")
        self.preview_panel.grid(row=0,column=1,sticky="nsew")
        self.preview_panel.rowconfigure(0,weight=1)
        self.preview_panel.columnconfigure(0,weight=1)
        self.preview_widget = tk.Text(self.preview_panel,width=15,height=4,wrap="word",state="disabled",
            bg=COLORS["input"],fg=COLORS["text"],selectbackground=COLORS["select"],
            selectforeground=COLORS["text"],relief="flat",highlightthickness=0,borderwidth=0,
            padx=8,pady=0,font=(self.fonts.ui,12),spacing3=6)
        self.preview_widget.grid(row=0,column=0,sticky="nsew")
        ps = AutoHideScrollbar(self.preview_panel,command=self.preview_widget.yview)
        ps.grid(row=0,column=1,sticky="ns")
        self.preview_widget.configure(yscrollcommand=ps.set)
        self.history_empty = ttk.Label(self.preview_panel,text="选择一条记录，查看全文",
                                      style="PanelMuted.TLabel",wraplength=230)
        self.history_empty.place(x=8,y=8)
        self.history_actions = ttk.Frame(box,style="Panel.TFrame")
        self.history_actions.grid(row=3,column=0,sticky="ew",pady=(8,0))
        self.history_restore_button = self.button(self.history_actions,"恢复到预输入",self.confirm_history,style="Accent.TButton")
        self.history_restore_button.pack(side="right")
        self.history_external_button = self.button(self.history_actions,"外部编辑",self.edit_history_external,style="PanelGhost.TButton")
        self.history_external_button.pack(side="right",padx=(0,8))
        self.history_export_button = self.button(self.history_actions,"导出 TXT",self.export_history,style="PanelGhost.TButton")
        self.history_export_button.pack(side="right",padx=(0,8))
        self.history_delete_button = self.button(self.history_actions,"删除",self.delete_history,style="PanelGhost.TButton")
        self.history_delete_button.pack(side="left",padx=(0,8))
        self.history_delete_cancel = self.button(self.history_actions,"保留",self.cancel_delete_history,style="PanelGhost.TButton")
        self.history_restore_button.state(["disabled"])
        self.history_external_button.state(["disabled"])
        self.history_export_button.state(["disabled"])
        self.history_delete_button.state(["disabled"])

    def layout_history(self, _event=None):
        if not self.history_open:
            return
        if _event is not None and _event.widget not in (self, self.editor_surface):
            return
        width = min(self.editor_surface.winfo_width()-20, max(590,min(840,int(self.editor_surface.winfo_width()*0.76))))
        # 在同一窗口内覆盖底部工具栏，避免列表被正文编辑区的高度限制。
        top = self.editor_surface.master.winfo_y()
        right = self.editor_surface.winfo_rootx() - self.winfo_rootx() + self.editor_surface.winfo_width()
        self.history_panel.place(x=right,y=top,anchor="ne",width=width,
                                 height=max(1,self.winfo_height()-top-20))
        self.history_status_label.configure(wraplength=max(400,width-44))
        self.history_panel.lift()

    def toggle_history(self):
        if self.history_open and self.record_mode == "notebook":
            self.close_history()
        else:
            self.open_history("notebook")

    def toggle_records(self):
        if self.history_open:
            self.close_history()
        else:
            self.open_history(self.record_mode)

    def remember_record_view(self):
        selected = self.history_list.selection()
        self.record_views[self.record_mode] = (selected[0] if selected else None, self.history_list.yview()[0], self.preview_widget.yview()[0])

    def toggle_deliveries(self):
        if self.history_open and self.record_mode == "delivery":
            self.close_history()
        else:
            self.open_history("delivery")

    @property
    def record_name(self):
        return "历史" if self.record_mode == "delivery" else "草稿簿"

    def record_items(self):
        return (self.deliveries.items if self.deliveries else []) if self.record_mode == "delivery" else self.saved_history

    def open_history(self, mode="notebook"):
        if self.busy or self.ai_review is not None or self.convention_panel is not None: return
        self.cancel_hold()
        self.cancel_auto_timer()
        self.magnifier.hide()
        if self.history_open:
            self.remember_record_view()
        if mode != self.record_mode:
            self.cancel_history()
        self.record_mode = mode
        self.record_tab_var.set(mode)
        self.history_open = True
        self.refresh_history()
        identifier, scroll, preview_scroll = self.record_views.get(mode, (None, 0, 0))
        if identifier and self.history_list.exists(identifier):
            self.history_list.selection_set(identifier)
            self.preview_history()
        self.history_list.yview_moveto(scroll)
        self.preview_widget.yview_moveto(preview_scroll)
        self.layout_history()
        self.history_list.focus_set()
        try:
            self.save_ui_preferences(record_tab=mode)
        except OSError:
            self.history_status.set('当前页已切换，但未能记住页签。')

    def close_history(self):
        if self.history_open:
            self.remember_record_view()
        self.cancel_history()
        self.history_open = False
        self.history_panel.place_forget()
        self.refresh_history()
        self.text.focus_set()

    def current_text(self):
        return self.text.get("1.0","end-1c")

    def update_meta(self):
        self.check_delivery_confirmation()
        self.refresh_translation_controls()
        if hasattr(self, 'live_sync'):
            self.live_sync.changed()
        self.count_var.set(f"{len(self.current_text())} 字")
        if hasattr(self,"empty_hint"):
            if self.current_text() or self.busy:
                self.empty_hint.place_forget()
            else:
                self.empty_hint.place(in_=self.editor_hint_line, x=16, y=0, anchor="w")
        sel = self.get_selection()
        if not self.busy and self.learning_pending is None and self.current_text() == self.learning_baseline:
            self.learning_selected = (len(self.text.get('1.0', sel[0])), len(self.text.get('1.0', sel[1]))) if sel else None
        if sel:
            self.cancel_auto_timer()
        if hasattr(self, 'remember_button'):
            self.remember_button.state(['!disabled'] if sel and 0 < len(sel[2]) <= 80 and '\n' not in sel[2] and not self.busy and self.ai_review is None else ['disabled'])
        self.clear_button.state(["!disabled"] if self.current_text() and not self.busy else ["disabled"])
        selection_cleared = self._had_selection and not sel
        self._had_selection = bool(sel)
        self.selection_var.set(
            f"已选 {len(sel[2])} 字 · 可覆盖或按指令修改"
            if sel else "光标处录入；指令修改可处理光标前后文字"
        )
        device = getattr(self, 'coloplay', None)
        if device and device.editor_focus.active:
            self.selection_var.set((('● 选区中 · 旋钮选字 · 点按结束' if device.editor_focus.selecting else '● 聚焦 · 旋钮移光标 · 点按选字') if self.editor_navigation_allowed() else '● 聚焦 · 编辑暂停')
                                   + (f' · 已选 {device.editor_focus.selection_count()} 字' if sel else ''))
        if hasattr(self,"replace_button") and not self.busy:
            self.replace_button.state(["!disabled"])
            self.rewrite_button.state(["!disabled"])
        if selection_cleared and not self.busy and not self.hold_key:
            self.schedule_auto()

    def get_selection(self):
        r = self.text.tag_ranges("sel")
        return (str(r[0]),str(r[1]),self.text.get(r[0],r[1])) if r else None

    def render_text(self, value, cursor=None, live=None, selection=None):
        self.reset_manual_learning(value)
        self.text.configure(state="normal")
        self.text.delete("1.0","end")
        self.text.insert("1.0",value)
        def index(offset):
            # Text 的 chars 运算按完整字符移动；显式列号才按 UTF-16。
            return f"1.0 + {offset} chars"
        if cursor is not None:
            self.text.mark_set("insert",index(cursor))
            self.text.see("insert")
        if live:
            self.text.tag_add("live",index(live[0]),index(live[1]))
        if selection and selection[1] > selection[0]:
            self.text.tag_add("sel", index(selection[0]), index(selection[1]))
        if self.busy and live is not None:
            self.emphasize_voice_range(*live)
        self.text.edit_modified(False)
        self.text.configure(state="disabled" if self.busy else "normal")
        self.update_meta()

    def on_text_modified(self,_event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self.busy:
            return
        if hasattr(self, 'window_controller'):
            self.window_controller.keep_expanded()
        self.track_manual_learning()
        self.revision += 1
        self.update_meta()
        if self.capture_job:
            self.after_cancel(self.capture_job)
        self.capture_job = self.after(500,self.capture_edit)
        self.schedule_auto()

    def capture_edit(self):
        if self.capture_job:
            self.after_cancel(self.capture_job)
        self.capture_job = None
        if not self.busy:
            self.timeline.commit(self.current_text())

    def set_text(self,value):
        if self.busy:
            return
        self.flush_manual_learning()
        if self.filler_pending is not None:
            self.filler_pending = None
            if hasattr(self, "one_filler_button"):
                self.one_filler_button.configure(text="AI 整理一次")
            try:
                self.text.tag_remove("filler_pending", "1.0", "end")
            except tk.TclError:
                pass
        self.capture_edit()
        self.timeline.commit(value)
        self.revision += 1
        self.render_text(value,len(value))

    def clear_text(self):
        if self.busy or self.ai_review is not None:
            return
        if not self.current_text():
            return
        self._prepare_delete()
        self.set_text("")
        self.auto_block_revision = self.revision
        self.text.focus_set()
        self.status_var.set("已清空编辑区，可用「上一步」恢复")

    def backspace_text(self):
        if self.busy or self.ai_review is not None:
            return
        selection = self.get_selection()
        value = self.current_text()
        if selection:
            start, end, removed = selection
            offset = len(self.text.get("1.0", start))
            end_offset = len(self.text.get("1.0", end))
        else:
            end_offset = len(self.text.get("1.0", "insert"))
            offset = max(0, end_offset - 1)
        if offset == end_offset:
            return
        self._prepare_delete()
        self.set_text(value[:offset] + value[end_offset:])
        self.auto_block_revision = self.revision
        self.text.mark_set("insert", f"1.0 + {offset} chars")
        self.text.see("insert")
        self.text.focus_set()
        self.status_var.set("已删除，可用「上一步」恢复")

    def _prepare_delete(self):
        self.cancel_hold()
        self.cancel_auto_timer()
        self.auto_request += 1
        self.auto_running = False
        self.one_filler_button.state(["!disabled"])
        self.one_filler_button.configure(text="AI 整理一次")
        self.close_history()
        self.refresh_cancel_button()

    def undo(self):
        if self.active_hold:
            self.stop_voice()
        self.cancel_hold()
        if self.busy:
            return "break"
        self.reset_manual_learning(self.current_text())
        self.filler_pending = None
        if hasattr(self, "one_filler_button"):
            self.one_filler_button.configure(text="AI 整理一次")
        self.capture_edit()
        self.revision += 1
        self.auto_block_revision = self.revision
        self.cancel_auto_timer()
        self.render_text(self.timeline.undo())
        self.status_var.set("已返回上一步；自动处理暂停至下次输入")
        return "break"

    def redo(self):
        if self.active_hold:
            self.stop_voice()
        self.cancel_hold()
        if self.busy:
            return "break"
        self.reset_manual_learning(self.current_text())
        self.filler_pending = None
        if hasattr(self, "one_filler_button"):
            self.one_filler_button.configure(text="AI 整理一次")
        self.capture_edit()
        self.revision += 1
        self.auto_block_revision = self.revision
        self.cancel_auto_timer()
        self.render_text(self.timeline.redo())
        return "break"

    def bind_shortcuts(self):
        self.text.bind("<Control-z>",lambda e:self.undo())
        self.text.bind("<Control-y>",lambda e:self.redo())
        self.text.bind("<Control-Shift-Z>",lambda e:self.redo())
        if sys.platform == "darwin":
            self.text.bind("<Command-z>",lambda e:self.undo())
            self.text.bind("<Command-Shift-Z>",lambda e:self.redo())
        self.bind("<Escape>",lambda e:self.cancel_operation())
        # 放在控件的默认键盘行为之前：焦点落在工具栏或空白处也能录音。
        # 设置页、其他输入框和历史面板仍使用各自的键盘操作。
        self._window_shortcut_tag = f"TalkManShortcuts:{self._w}"
        self._global_key_down = None
        self.bind_class(self._window_shortcut_tag, "<KeyPress>", self.window_shortcut)
        def attach(widget):
            if widget != self.text and widget.winfo_toplevel() == self:
                widget.bindtags((self._window_shortcut_tag,) + widget.bindtags())
            for child in widget.winfo_children():
                if child.winfo_toplevel() == self:
                    attach(child)
        attach(self)
        self.bind("<KeyRelease>",self.key_release,add=True)
        self.bind("<FocusOut>",lambda e:self.after_idle(self.check_focus))

    def window_shortcut(self, event):
        if (event.widget == self.text or event.widget.winfo_toplevel() != self
                or self.history_open or self.closing
                or event.widget.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")):
            return None
        controller = getattr(self, "window_controller", None)
        if controller and controller.settings_open():
            return None
        action = next((name for name, binding in self.shortcuts.items()
                       if matches_event(binding, event)), None)
        if action is None:
            return None
        if not action.startswith("global_"):
            self.text.focus_set()
        return self.key_press(event)

    def check_focus(self):
        if self.closing:
            return
        if hasattr(self, 'coloplay'):
            self.coloplay.editor_focus.sync()
        focus = self.focus_displayof()
        if focus is None or (self.active_hold and focus != self.text):
            self._global_key_down = None
            self.mic_key_down = None
            self.clear_key_down = None
            self.mic_key_pending = False
            self.cancel_hold()
            if self.voice and not self.voice_stopping and not self.voice_from_device:
                self.stop_voice()

    def key_press(self,event):
        if self.convention_panel is not None:
            return 'break' if event.widget == self.text else None
        if self.guide and self.guide.opened and self.guide.modal:
            return
        self.magnifier.hide()
        self.empty_hint.place_forget()
        if event.keysym == "Escape":
            return self.cancel_operation()
        action = next((name for name,binding in self.shortcuts.items() if matches_event(binding,event)), None)
        controller = getattr(self, "window_controller", None)
        global_binding = controller and action in (controller.bindings or {})
        if action in ("global_record", "global_rewrite", "global_show") or global_binding:
            self.cancel_hold()
            if (action == 'global_show' or self.global_voice_var.get()) and self._global_key_down != self.shortcuts[action]:
                self._global_key_down = self.shortcuts[action]
                if controller and not controller.native_enabled:
                    controller.hotkey_press(action)
            return "break"
        if action == "focus":
            return self.focus_at_pointer()
        if action in ("cursor_prev", "cursor_next"):
            self.move_editor_cursor(-1 if action == "cursor_prev" else 1)
            return "break"
        if action == "clear":
            if not self.clear_key_down:
                self.clear_key_down = self.shortcuts["clear"]
                self.clear_text()
            return "break"
        if self._mic_permission_pending and action in ("mic", "replace", "rewrite"):
            self.cancel_hold()
            return "break"
        if action != "mic":
            self.mic_key_pending = False
        if self.hold_key and (not action or self.shortcuts[action] != self.hold_key):
            if self.active_hold:
                self.stop_voice()
            self.cancel_hold()
        if action == "mic":
            if not self.mic_key_down:
                self.mic_key_down = self.shortcuts["mic"]
                self.mic_key_pending = self.mic_key_down in ("Control","Alt","Shift","Command")
                if not self.mic_key_pending:
                    self.toggle_mic()
            return "break"
        if action not in ("replace", "rewrite"):
            return "break" if self.busy else None
        if self.busy:
            return "break"
        if not self.hold_job and not self.hold_key:
            binding = self.shortcuts[action]
            self.hold_key=binding
            self.hold_pending_space = binding == "space" and getattr(event, "widget", self.text) == self.text
            def begin():
                self.hold_job=None
                mode = action
                if action == "replace" and not self.get_selection():
                    mode = "insert"
                if self.hold_key == binding and self.start_voice(mode):
                    self.active_hold=binding
            self.hold_job=self.after(250,begin)
        return "break"

    def key_release(self,event):
        if self._global_key_down and released_event(self._global_key_down, event):
            controller = getattr(self, 'window_controller', None)
            action = next((name for name, binding in self.shortcuts.items() if binding == self._global_key_down), None)
            if controller and not controller.native_enabled:
                controller.hotkey_release(action)
            self._global_key_down = None
            return "break"
        if self.voice_button_press and self.voice_button_press[3] == event.keysym:
            return self.release_voice_button(self.voice_button_press[0], event)
        if self.clear_key_down and released_event(self.clear_key_down, event):
            self.clear_key_down = None
            return "break"
        if self.mic_key_down and released_event(self.mic_key_down,event):
            if self.mic_key_pending:
                self.toggle_mic()
            self.mic_key_down = None
            self.mic_key_pending = False
        if self.hold_key and released_event(self.hold_key,event):
            binding = self.hold_key
            pending_space = self.hold_pending_space
            active = bool(self.active_hold)
            if self.active_hold:
                self.stop_voice()
            self.cancel_hold()
            # 空格既是默认录音快捷键，也是正文中的常用字符；短按保留编辑器行为。
            if pending_space and not active and not self.busy and not self.history_open:
                self.text.focus_set()
                selection = self.get_selection()
                if selection:
                    self.text.mark_set("insert", selection[0])
                    self.text.delete(selection[0], selection[1])
                    self.text.tag_remove("sel", "1.0", "end")
                self.text.insert("insert", " ")
                self.update_meta()
            return "break"
        self.update_meta()

    def cancel_hold(self):
        self.mic_key_pending = False
        if self.hold_job:
            self.after_cancel(self.hold_job)
        self.hold_job=self.hold_key=self.active_hold=None
        self.hold_pending_space = False

    def bind_voice_button(self, button, mode):
        def down(event):
            if button.instate(["disabled"]):
                return "break"
            if self.voice_button_press is not None:
                return "break"
            if self.voice and not self.voice_stopping:
                self.stop_voice()
                return "break"
            actual_mode = mode
            if mode == "replace" and not self.get_selection():
                actual_mode = "insert"
            self.text.focus_set()
            if self.start_voice(actual_mode):
                key = getattr(event, "keysym", None)
                self.voice_button_press = (button, time.monotonic(), self.operation_id,
                                           key if key in ("space", "Return") else None,
                                           getattr(event, 'time', 0))
                button.grab_set()
                button.state(["pressed"])
            return "break"
        def up(event):
            return self.release_voice_button(button, event)
        button.bind("<ButtonPress-1>",down)
        button.bind("<ButtonRelease-1>",up)
        button.bind("<KeyPress-space>",down)
        button.bind("<KeyRelease-space>",up)
        button.bind("<KeyPress-Return>",down)
        button.bind("<KeyRelease-Return>",up)

    def release_voice_button(self, button, event):
        gesture = self.voice_button_press
        if gesture is None or gesture[0] != button:
            return "break"
        self.voice_button_press = None
        if button.grab_current() == button:
            button.grab_release()
        button.state(["!pressed"])
        inside = (gesture[3] is not None or not hasattr(event, "x") or
                  0 <= event.x < button.winfo_width() and 0 <= event.y < button.winfo_height())
        elapsed = time.monotonic() - gesture[1]
        pressed_at = gesture[4] if len(gesture) > 4 else 0
        released_at = getattr(event, 'time', 0)
        if isinstance(pressed_at, int) and pressed_at > 0 and isinstance(released_at, int) and released_at > 0:
            # 用系统输入时间区分点按与按住，界面绘制耗时不计入按住时间。
            elapsed = ((released_at - pressed_at) & 0xffffffff) / 1000
        # 短点保持录音，下一次点按结束；按住或移出按钮松手则结束。
        if (gesture[2] == self.operation_id and self.voice
                and (elapsed >= 0.25 or not inside)):
            self.stop_voice()
        return "break"

    def set_busy(self,busy):
        self.busy=busy
        if busy:
            self.target_picker.close()
            self.magnifier.hide()
        self.text.configure(state="disabled" if busy else "normal")
        for b in self.edit_controls+[self.settings_button,self.records_button,self.history_button,self.delivery_button,self.target_button,
                                     self.one_filler_button,self.replace_button,self.rewrite_button]:
            b.state(["disabled"] if busy else ["!disabled"])
        if self.target_filling:
            self.target_button.state(["disabled"])
        self.clear_button.state(["!disabled"] if not busy and self.current_text() else ["disabled"])
        self.refresh_cancel_button()
        self.mic_button.configure(
                                  style="Recording.TButton" if busy else "Accent.TButton",
                                  image=self.icon_sets[self.icon_tone('Recording.TButton' if busy else 'Accent.TButton')]["square" if busy else "mic"])
        self.refresh_shortcut_labels()
        if not busy:
            self.voice_button_press = None
            self.text.tag_remove("operation", "1.0", "end")
            self.text.set_operation_caret(None)
            for button in (self.replace_button,self.rewrite_button):
                if button.grab_current() == button:
                    button.grab_release()
                button.state(["!pressed"])

    def refresh_shortcut_labels(self):
        for action,button,label in (("mic",self.mic_button,"停止输入" if self.busy else "麦克风"),
                                    ("replace",self.replace_button,"录音和覆盖"),
                                    ("rewrite",self.rewrite_button,"指令修改")):
            shortcut = self.shortcuts[action]
            button.configure(text=label)
            hint = getattr(self,{"mic":"mic_button_hint","replace":"replace_button_hint","rewrite":"rewrite_button_hint"}[action])
            if action == "mic":
                hint.set("点按" + (" · "+display_shortcut(shortcut) if shortcut else "按钮"))
            else:
                active = self.voice_mode in (("insert", "replace") if action == "replace" else ("rewrite",))
                recording = self.voice is not None and not self.voice_stopping and active
                button.configure(text="结束录音" if recording else label,
                    style="Recording.TButton" if recording else "Accent.TButton" if action == "replace" else "TButton")
                button.state(["!disabled"] if not self.busy or recording else ["disabled"])
                global_keys = voice_bindings(self.shortcuts) if self.global_voice_var.get() else {}
                fallback = "global_record" if action == "replace" else "global_rewrite"
                background = global_keys.get(action) or global_keys.get(fallback)
                if recording:
                    hint.set("点按结束录音" + (" · " + display_shortcut(background) if background else ""))
                elif background and background == shortcut:
                    hint.set("任意窗口：" + display_shortcut(background) + " · 点按开始／结束")
                else:
                    local = "本窗口：" + display_shortcut(shortcut) if shortcut else "按钮点按／按住"
                    hint.set(local)
        self.refresh_compact_controls()

    def refresh_cancel_button(self):
        self.refresh_translation_controls()
        if self.busy or self.auto_running:
            self.cancel_button.pack(side="right")
            self.cancel_button.state(["!disabled"])
        else:
            self.cancel_button.pack_forget()

    def toggle_mic(self):
        if self.voice:
            self.stop_voice()
        elif not self.busy:
            self.start_voice("insert")

    def start_voice(self,mode, *, from_device=False):
        if self.busy or self.closing or self.ai_review is not None or self.convention_panel is not None:
            return False
        if hasattr(self, 'coloplay') and (self.coloplay.active or self.window_controller.edge):
            self.window_controller.begin_device_edit()
        if self._mic_permission_pending:
            self.cancel_hold()
            self.status_var.set("请先完成系统麦克风授权，随后重新按住录音。")
            return False
        provider = 'free' if self.free_mode_var.get() else self.asr_settings.provider
        spec = PROVIDERS[provider]
        try:
            if provider == 'free' and not self.free_service.configured:
                raise QwenError('这台电脑尚未开通免费内测，请在设置中检查连接。')
            if provider != 'free':
                validate(self.asr_settings)
            if provider != 'free' and (provider == "qwen" or mode == "rewrite") and not self.settings.api_key:
                raise QwenError("请在「设置 → 模型连接」填写千问 API Key；指令修改需要文字模型。")
            if provider != 'free' and (provider == "qwen" or mode == "rewrite") and self.settings.api_key.startswith("sk-sp-"):
                raise QwenError("请填写千问通用 API Key，当前不支持套餐专属 Key。")
            recognize = (FreeClient(self.free_service).transcribe if provider == 'free'
                         else None if provider == "qwen" else transcriber(self.asr_settings))
        except QwenError as ex:
            self.status_var.set(str(ex))
            return False
        sel=self.get_selection()
        if mode == "replace" and not sel:
            self.status_var.set("请先框选需要修改的文字")
            return False
        if mode == "rewrite" and not sel and not from_device and self.focus_displayof() != self.text:
            self.status_var.set("请先在预输入框中点击要修改的位置，或框选需要修改的文字")
            return False
        self._mic_permission_request += 1
        request = self._mic_permission_request
        self._mic_permission_pending = True
        try:
            allowed = prepare_microphone_permission(
                lambda granted, message: self.events.put(("mic_permission", request, granted, message)))
        except VoicePermissionError as ex:
            self._mic_permission_pending = False
            self.cancel_hold()
            self.status_var.set(str(ex))
            return False
        if not allowed:
            self.cancel_hold()
            self.status_var.set("请在系统提示中允许麦克风，随后重新按住录音。")
            return False
        self._mic_permission_pending = False
        self.disarm_delivery()
        self.flush_manual_learning()
        self.capture_edit()
        self.cancel_auto_timer()
        self.auto_request += 1
        self.auto_running = False
        self.close_history()
        self.voice_base=self.current_text()
        self.voice_conventions = self.conventions.snapshot()
        if sel:
            start=len(self.text.get("1.0",sel[0]))
            end=len(self.text.get("1.0",sel[1]))
        else:
            start=end=len(self.text.get("1.0","insert"))
        self.voice_selection = (start, end) if sel else None
        self.voice_start=end if mode=="insert" else start
        self.voice_end=end
        self.voice_mode=mode
        self.voice_text=""
        self.voice_stopping=False
        self.voice_from_device=from_device
        self.operation_id += 1
        self.voice_ready_token = None
        token=self.operation_id
        self.set_busy(True)
        self.emphasize_voice_range(self.voice_start, self.voice_end)
        self.update_meta()
        self.voice_limit = spec.seconds
        self.status_var.set("正在打开麦克风…" if provider != "qwen" or self.settings.voice_mode == "file" else "正在连接千问，准备录音…")
        try:
            callback=lambda kind,payload:self.events.put(("voice",token,kind,payload))
            if provider != "qwen" or self.settings.voice_mode == "file":
                recognize = recognize or self.client_factory(replace(self.settings)).transcribe
                self.voice=self.file_session_factory(self.settings.api_key if provider == "qwen" else provider,
                    self.settings.file_model if provider == "qwen" else spec.label, callback,
                    transcribe=recognize, max_seconds=spec.seconds, request_timeout=spec.timeout)
            else:
                self.voice=self.session_factory(self.settings.api_key,self.settings.asr_model,callback,endpoint=self.settings.asr_ws_endpoint)
            self.voice.start()
            self.refresh_shortcut_labels()
        except VoicePermissionError as ex:
            self.restore_voice(str(ex))
            return False
        except Exception:
            self.restore_voice("录音启动失败，请检查麦克风或重新打开程序")
            return False
        return True

    def stop_voice(self):
        if self.voice and not self.voice_stopping:
            self.voice_stopping=True
            if self.voice_button_press:
                button = self.voice_button_press[0]
                self.voice_button_press = None
                if button.grab_current() == button:
                    button.grab_release()
                button.state(["!pressed"])
            self.refresh_shortcut_labels()
            self.status_var.set("已停止录音，等待最后的识别结果…")
            self.voice.stop()

    def show_voice_text(self,value):
        self.voice_text=value
        self.window_controller.voice_progress(value)
        if self.voice_mode == "rewrite":
            self.status_var.set("听到指令："+value[-100:])
        else:
            s,e=self.voice_start,self.voice_end
            self.render_text(self.voice_base[:s]+value+self.voice_base[e:],s+len(value),(s,s+len(value)))

    def finish_voice(self,value):
        self.window_controller.voice_progress(value)
        limit_reached = getattr(self.voice,"stop_reason",None) == "limit"
        self.cancel_hold()
        self.voice=None
        self.refresh_shortcut_labels()
        self.voice_text=value
        if not value.strip():
            self.restore_voice("没有识别到有效语音，原文已保留")
        elif self.voice_mode == "rewrite":
            token=self.operation_id
            selected=self.voice_base[self.voice_start:self.voice_end]
            if is_remember_instruction(value):
                self.restore_voice('请确认约定词的标准写法')
                if selected:
                    self.remember_selection(selected)
                else:
                    self.status_var.set('请先选中要记住的词，再说“把选中的词记为约定”。')
                return
            value = self.voice_conventions.apply(value, code=False)
            value = self.voice_conventions.instruction(value, self.voice_base + '\n' + value)
            client=self.make_text_client(replace(self.settings))
            if self.voice_start == self.voice_end:
                self.status_var.set("千问正在按指令处理光标前后的文字…")
                before=self.voice_base[:self.voice_start]
                after=self.voice_base[self.voice_end:]
                self.run_worker("rewrite",token,lambda:client.edit_by_instruction(before,after,value))
            else:
                self.status_var.set("千问正在按语音指令修改选区…")
                document, start, end = self.voice_base, self.voice_start, self.voice_end
                self.run_worker("rewrite",token,lambda:client.edit_selection(document,start,end,value))
        else:
            self.commit_voice(self.voice_conventions.apply(value))
            if limit_reached:
                self.status_var.set(f"本轮已达 {self.voice_limit} 秒上限，录音已停止并写入；可继续下一轮")

    def commit_voice(self,value):
        if isinstance(value, TextEdit):
            if not (type(value.start) is int and type(value.end) is int
                    and 0 <= value.start <= value.end <= len(self.voice_base)
                    and isinstance(value.text, str)):
                self.restore_voice("修改范围无效，原文已保留")
                return
            self.voice_start, self.voice_end, value = value.start, value.end, value.text
        new=self.voice_base[:self.voice_start]+value+self.voice_base[self.voice_end:]
        if self.voice_start < self.voice_end:
            self.learn_correction(self.voice_base, new, (self.voice_start, self.voice_end))
        else:
            # 新一轮录入可再次提供独立纠正证据；撤销／重做不更换此标识。
            import uuid
            self.learning_epoch = uuid.uuid4().hex
        cursor=self.voice_start+len(value)
        keep_selection = self.voice_mode == "rewrite"
        self.voice_mode=None
        self.set_busy(False)
        self.timeline.commit(new)
        self.revision+=1
        self.render_text(new,cursor,selection=(self.voice_start,cursor) if keep_selection else None)
        self.status_var.set("已按指令修改，可用「上一步」撤销" if keep_selection else
                            "已写入，光标停在新文字末尾；可用「上一步」撤销")
        self.schedule_auto()

        self.window_controller.voice_complete()

    def restore_voice(self,message):
        self.window_controller.voice_failed()
        if self.voice:
            self.voice.cancel()
        self.voice=None
        self.voice_mode=None
        self.operation_id+=1
        self.set_busy(False)
        self.render_text(self.voice_base,self.voice_start,selection=self.voice_selection)
        self.status_var.set(message)

    def cancel_operation(self):
        if self.pending_delivery:
            self.disarm_delivery()
            self.status_var.set('已取消下发确认，目标中已填入的文字保留。')
            return 'break'
        if self.convention_panel is not None:
            self.close_convention()
            return 'break'
        if self.ai_review is not None:
            self.ai_review_finish()
            return "break"
        self.mic_key_pending = False
        self.cancel_hold()
        if self.busy:
            self.restore_voice("已取消，原文未改动")
        elif self.auto_running:
            self.window_controller.voice_failed()
            self.status_var.set("已取消全文整理，当前文字保留；下次输入后继续")
            if hasattr(self, "one_filler_button"):
                self.one_filler_button.state(["!disabled"])
                self.one_filler_button.configure(text="AI 整理一次")
        elif self.history_open:
            self.close_history()
        elif self.get_selection():
            self.text.tag_remove("sel", "1.0", "end")
            self.update_meta()
            self.status_var.set("已取消选区")
            return "break"
        self.auto_running=False
        self.auto_request+=1
        self.auto_block_revision=self.revision
        self.cancel_auto_timer()
        self.refresh_cancel_button()
        return "break"

    def run_worker(self,kind,token,work):
        def run():
            try:
                result=work()
                self.events.put((kind,token,"ok",result))
            except QwenError as ex:
                self.events.put((kind,token,"error",str(ex)))
            except Exception:
                self.events.put((kind,token,"error","服务请求失败，请检查网络或模型设置"))
        threading.Thread(target=run,daemon=True).start()

    def poll_events(self):
        if self.closing:
            return
        for _ in range(120):
            try:
                kind,token,state,value=self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "mic_permission":
                if token == self._mic_permission_request and self._mic_permission_pending:
                    self._mic_permission_pending = False
                    self.cancel_hold()
                    self.mic_key_down = None
                    self.status_var.set(value)
            elif kind == "voice" and token==self.operation_id and self.busy:
                if state=="ready" and not self.voice_stopping:
                    self.voice_ready_token = token
                    self.status_var.set(f"正在录音 · {PROVIDERS['free' if self.free_mode_var.get() else self.asr_settings.provider].label} · 单轮 {self.voice_limit} 秒")
                elif state in ("partial","final"):
                    self.show_voice_text(value)
                elif state=="done":
                    self.finish_voice(value)
                elif state=="error":
                    self.restore_voice(value+"；原文已保留")
            elif kind=="rewrite" and token==self.operation_id and self.busy:
                if state=="ok": self.review_voice_result(value)
                else: self.restore_voice(value+"；原文已保留")
            elif kind == "ai_once":
                self._finish_ai(token, state, value, once=True)
            elif kind == 'translation':
                self.finish_translation(token, state, value)
            elif kind=="auto":
                self._finish_ai(token, state, value)
            elif kind=="check":
                callback=token
                callback(state,value)
            elif kind == "target_refresh":
                callback = token
                if state == "ok":
                    windows = list(value or [])
                    self.target_windows = {getattr(item, "id", None): item for item in windows
                                           if getattr(item, "id", None) is not None}
                    callback("ok", self.target_window_rows())
                else:
                    self.target_windows = {}
                    callback("error", value)
            elif kind in ("target_fill", "target_fill_device"):
                self.target_filling = False
                self.target_button.state(["disabled"] if self.busy else ["!disabled"])
                self.refresh_compact_controls()
                self.finish_normal_fill(state, value)
                if kind == 'target_fill_device': self.coloplay.finish_device_fill(token)
            elif kind in ('target_send', 'target_send_device'):
                self.target_filling = False
                self.finish_target_fill(state, value)
                self.live_sync.refresh()
                self.coloplay.finish_send(token, state)
            elif kind == 'live_sync':
                self.live_sync.finish(token, state, value)
        self.refresh_convention_hint()
        self.check_delivery_confirmation()
        self.poll_job=self.after(40,self.poll_events)

    def cancel_auto_timer(self):
        if self.auto_job:
            self.after_cancel(self.auto_job)
        self.auto_job=None

    def auto_changed(self):
        self.auto_request+=1
        self.auto_running=False
        self.one_filler_button.state(["!disabled"])
        self.set_button_label(self.one_filler_button, "AI 整理一次")
        self.refresh_cancel_button()
        self.auto_block_revision=-1
        if self.ai_enabled() and not self.text_model_ready():
            self.status_var.set("全文自动整理需要在「设置 → 模型连接」填写千问 Key")
        self.schedule_auto()
        try:
            self.save_ui_preferences(auto_filler=self.filler_var.get(), auto_punct=self.punct_var.get(),
                                     auto_layout=self.layout_var.get(), auto_rewrite=self.rewrite_var.get())
        except OSError:
            self.status_var.set("整理开关已在本次生效，但未能记住；请检查数据目录权限")










    def load_history(self):
        try:
            values=json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(values,list): return []
            return [x for x in values if isinstance(x,dict) and all(isinstance(x.get(k),str) for k in ("id","time","text"))][:100]
        except (OSError,ValueError):
            return []

    def refresh_history(self):
        items = self.record_items()
        selected = self.history_list.selection()
        for item in self.history_list.get_children(): self.history_list.delete(item)
        seen=set()
        for x in items:
            if x["id"] in seen: continue
            seen.add(x["id"])
            excerpt = x["text"].replace("\n"," ")[:12]
            label = x["time"][5:16].replace("T", " ")+"\n"+excerpt
            if self.record_mode == "delivery":
                label += "\n" + delivery_destination(x)[:24]
            self.history_list.insert("","end",iid=x["id"],text=label)
        if selected and self.history_list.exists(selected[0]):
            self.history_list.selection_set(selected[0])
        self.history_button.configure(text=f"草稿簿 · {len(self.saved_history)}")
        count = len(self.deliveries.items) if self.deliveries else 0
        self.delivery_button.configure(text=f"历史 · {count}")
        self.records_button.configure(text='收起记录' if self.history_open else '记录')
        self.history_heading.configure(text='记录')
        self.history_count_var.set(f"{len(items):02d} 条")
        if self.record_mode == "delivery":
            self.history_status.set(self.delivery_error or "记录成功填入的文字、时间与目标窗口；已填入不代表已发送。")
        else:
            self.history_status.set("选中可预览；恢复前自动保存当前正文。" if items else "暂无记事；点击「保存」留存文本。")

    def write_history(self, items):
        try:
            temp=self.history_path.with_suffix(".tmp")
            temp.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding="utf-8")
            temp.replace(self.history_path)
        except OSError:
            message="草稿簿保存失败，请检查数据目录权限；原有记录和当前文本保留"
            self.status_var.set(message)
            self.history_status.set(message)
            return False
        self.saved_history=items
        self.refresh_history()
        return True

    def save_current(self):
        if self.busy: return False
        value=self.current_text()
        if not value.strip():
            self.status_var.set('正文为空，无需保存')
            return True
        previous = next((x for x in self.saved_history if x['id'] == self.draft_id), None)
        if previous and previous['text'] == value:
            self.status_var.set('已保存，内容没有变化')
            self.button_feedback(self.save_button, '已保存')
            return True
        # 比较最终文本：原字符顺序完整保留，才属于仅增加文字。
        remaining = iter(value)
        additions = previous is not None and all(c in remaining for c in previous['text'])
        if additions:
            item = dict(previous, time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), text=value)
        else:
            item = dict(id=str(time.time_ns()), time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), text=value)
            if previous:
                item['parent_id'] = previous['id']
        if self.translation_memory.latest is not None:
            item['translation_protected'] = self.translation_memory.protection(value)
        else:
            item.pop('translation_protected', None)
        items=([item]+[x for x in self.saved_history if x['id'] != item['id']])[:100]
        if not self.write_history(items):
            self.button_feedback(self.save_button, '保存失败')
            return False
        self.draft_id = item['id']
        self.status_var.set('已更新原草稿，只增加了文字' if additions else '改写已另存副本，原草稿保留' if previous else '已保存到草稿簿')
        self.button_feedback(self.save_button, '已更新' if additions else '已保存')
        return True

    def preview_history(self,_event=None):
        if self.busy: return
        selected=self.history_list.selection()
        if not selected:
            self.cancel_history()
            return
        item=next((x for x in self.record_items() if x["id"]==selected[0]),None)
        if not item: return
        preview_key = (self.record_mode, item['id'], item['text'])
        if getattr(self, '_record_preview_key', None) == preview_key:
            return
        self._record_preview_key = preview_key
        self.cancel_delete_history()
        self.preview_text=item["text"]
        self.history_empty.place_forget()
        self.preview_widget.configure(state="normal")
        self.preview_widget.delete("1.0","end")
        self.preview_widget.insert("1.0",self.preview_text)
        self.preview_widget.configure(state="disabled")
        detail = ("已填入 · " + delivery_destination(item)) if self.record_mode == "delivery" else "恢复前自动保存当前正文"
        self.history_status.set(f"{item['time']} · {len(item['text'])} 字 · {detail}")
        self.history_restore_button.state(["!disabled"])
        self.history_external_button.state(["!disabled"])
        self.history_export_button.state(["!disabled"])
        self.history_delete_button.state(["!disabled"])

    def _selected_history_item(self):
        """返回当前历史项；导出和外部编辑只读取该快照。"""
        selected = self.history_list.selection()
        if not selected:
            return None
        return next((x for x in self.record_items() if x["id"] == selected[0]), None)

    def export_history(self):
        item = self._selected_history_item()
        if not item:
            self.history_status.set(f"请先选择一条{self.record_name}记录，再导出 TXT。")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title=f"导出{self.record_name} TXT", defaultextension=".txt",
            initialfile=f"TalkMan-{item['time'].replace(':', '-')}.txt",
            filetypes=(("文本文件", "*.txt"), ("所有文件", "*.*")))
        if not path:
            return
        try:
            metadata = (f"时间：{item['time']}\n目标：{delivery_destination(item)}\n状态：{item.get('status', '已填入')}（不代表已发送）\n\n"
                        if self.record_mode == "delivery" else "")
            Path(path).write_text(metadata + item["text"], encoding="utf-8")
        except OSError:
            self.history_status.set("导出失败，请检查目标路径权限。")
            return
        self.history_status.set(f"已导出 TXT：{Path(path).name}")
        self.status_var.set(f"已导出选中的{self.record_name}文本；当前正文未改变")

    def edit_history_external(self):
        item = self._selected_history_item()
        if not item:
            self.history_status.set(f"请先选择一条{self.record_name}记录，再打开外部编辑。")
            return
        try:
            folder = self.data_dir / "外部编辑"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"TalkMan-{self.record_name}-{item['id']}.txt"
            path.write_text(item["text"], encoding="utf-8")
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except (OSError, FileNotFoundError):
            self.history_status.set("无法打开外部编辑器，请先导出 TXT 后手动打开。")
            return
        self.history_status.set("已在系统默认编辑器打开副本；编辑不会自动改回原记录。")
        self.status_var.set(f"已打开{self.record_name}文本副本；当前正文未改变")

    def confirm_history(self):
        if self.busy or self.preview_text is None: return
        value=self.preview_text
        source = self._selected_history_item()
        if self.save_current():
            self.translation_memory.restore_draft(value, source.get('translation_protected') if source else None)
            self.set_text(value)
            self.draft_id = source['id'] if source and self.record_mode == 'notebook' else None
            self.close_history()
            self.status_var.set("已恢复所选文本；替换前的文本已存入草稿簿")

    def cancel_history(self):
        self._record_preview_key = None
        self.preview_text=None
        self.cancel_delete_history()
        self.preview_widget.configure(state="normal")
        self.preview_widget.delete("1.0","end")
        self.preview_widget.configure(state="disabled")
        self.history_empty.place(x=8,y=8)
        selected=self.history_list.selection()
        if selected: self.history_list.selection_remove(*selected)
        self.history_restore_button.state(["disabled"])
        self.history_external_button.state(["disabled"])
        self.history_export_button.state(["disabled"])
        self.history_delete_button.state(["disabled"])

    def delete_history(self):
        if self.busy: return
        selected = self.history_list.selection()
        if not selected: return
        if self.pending_delete == selected[0]:
            return self.confirm_delete_history()
        self.pending_delete = selected[0]
        self.history_delete_button.configure(text="确认删除")
        self.history_delete_cancel.pack(side="left",padx=(0,8))
        self.history_status.set(f"删除这条{self.record_name}记录？不会影响当前正文；再按「确认删除」完成。")

    def cancel_delete_history(self):
        self.pending_delete = None
        self.history_delete_button.configure(text="删除")
        self.history_delete_cancel.pack_forget()

    def confirm_delete_history(self):
        if self.busy or not self.pending_delete: return False
        selected = self.history_list.selection()
        if not selected or selected[0] != self.pending_delete: return False
        items = [x for x in self.record_items() if x["id"] != self.pending_delete]
        if self.record_mode == "delivery":
            try:
                self.deliveries.delete(self.pending_delete)
            except OSError:
                self.history_status.set("历史删除失败，原记录保留")
                return False
            self.refresh_history()
        elif not self.write_history(items):
            return False
        self.cancel_history()
        self.history_status.set(f"已删除 · 剩余 {len(items)} 条{self.record_name}记录")
        self.status_var.set(f"已删除选中的{self.record_name}记录，当前正文未改变")
        return True

    def copy_all(self):
        value=self.current_text()
        if not value.strip():
            self.status_var.set("预输入框为空")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status_var.set("已复制全部文本，请在目标 Agent 输入框粘贴；不会自动发送")
        self.button_feedback(self.copy_button, '已复制')

    def refresh_target_windows(self, callback):
        """主界面与设置页共用；扫描在目标 worker 中进行，结果回到 Tk 队列。"""
        if not self.target_controller:
            callback("error", self.target_platform_note)
            return

        def done(state, value):
            self.events.put(("target_refresh", callback, state, value))

        self.target_controller.refresh(done)

    def target_window_rows(self):
        return [{"id": item.id, "app": item.app, "title": item.title,
                 "label": " · ".join(filter(None, (item.app, item.title)))}
                for item in self.target_windows.values()
                if not self.only_agents_var.get() or is_agent_window(item)]

    def choose_target(self, window_id):
        if not self.target_controller:
            raise TargetError(self.target_platform_note)
        target = self.target_windows.get(window_id)
        if target is None:
            raise TargetError("目标窗口已失效，请重新刷新后选择。")
        self.target_controller.select(target)
        self.live_sync.configure(self.sync_mode_var.get())
        self.target_label_var.set(target.display_name())
        self.status_var.set(f"已选择目标：{target.display_name()}；" + ('正在准备同步，点击下发才发送' if self.sync_mode_var.get() else '填入时不会自动发送'))
        return "目标已保存，可回到编辑器填入文字。"

    def clear_target(self):
        if self.target_controller:
            self.target_controller.clear()
        self.target_windows = {}
        self.live_sync.configure(False)
        self.target_label_var.set("未选择目标输入框")
        self.status_var.set("已清除目标输入框")

    def fill_to_target(self, *, paste_only=False, device_confirm=False):
        if self.convention_panel is not None:
            self.status_var.set('请先保存或取消约定词。')
            return
        if self.ai_review is not None:
            self.status_var.set("请先确认或取消改写预览，再填入目标")
            return
        if self.sync_mode_var.get() and not paste_only:
            self.live_sync.flush(send=True)
            return
        if self.target_filling:
            return
        if self.busy:
            self.status_var.set("请先结束当前录音，再填入目标")
            return
        if paste_only:
            # 设备长按永远只填入；清除桌面按钮留下的发送确认。
            self.disarm_delivery()
            if self.target_controller:
                self.target_controller.stop_sync()
        elif self.pending_delivery:
            self.send_pending_delivery()
            return
        value = self.current_text()
        if not value.strip():
            self.status_var.set("预输入框为空")
            return
        if not self.target_controller:
            self.status_var.set(self.target_platform_note)
            return
        self.target_button.state(["disabled"])
        self.cancel_auto_timer()
        # 填入的是点击时的正文。未完成的整理不能在回执前偷偷覆盖它。
        self.auto_request += 1
        self.auto_running = False
        self.one_filler_button.state(['!disabled'])
        self.set_button_label(self.one_filler_button, 'AI 整理一次')
        self.refresh_cancel_button()
        self._delivery_snapshot = (self.revision, value)
        self.text.defer_fit_until_interaction()
        self.target_filling = True
        self.target_picker.close()
        self.refresh_compact_controls()
        self.status_var.set("正在聚焦目标输入框并填入文字；不会自动发送…")
        device_epoch = self.coloplay.delivery_epoch if device_confirm else None
        callback = lambda state, message: self.events.put(("target_fill_device" if device_confirm else "target_fill", device_epoch, state, message))
        if paste_only:
            target = self.target_controller.selected
            self.target_controller.fill_with_receipt(value, callback,
                expected_target=target.session_key() if target else None,
                allow_send_confirmation=device_confirm)
        else:
            self.target_controller.fill_with_receipt(value, callback)

    def finish_target_fill(self, state, value):
        snapshot = self._delivery_snapshot
        self._delivery_snapshot = None
        if state != "ok" or not isinstance(value, FillReceipt):
            self.status_var.set(str(value))
            self.button_feedback(self.target_button, '未完成')
            return
        try:
            if self.deliveries is None:
                self.deliveries = DeliveryHistory(self.data_dir)
            self.deliveries.add(value.text, value.app, value.title, value.time, value.status)
            self.delivery_error = ""
        except OSError:
            self.delivery_error = "文字已填入，但历史记录保存失败；请检查数据目录权限"
            self.status_var.set(self.delivery_error + "，不要重复填入")
            return
        self.refresh_history()
        self.button_feedback(self.target_button, '已下发' if value.status == '已触发下发' else '已填入')
        self.status_var.set(value.message + (' · 已记入历史；发送结果以目标为准' if value.status == '已触发下发' else ' · 已记入历史；不会自动发送'))
        if self.clear_after_fill_var.get():
            if (snapshot == (self.revision, value.text) and self.current_text() == value.text
                    and not self.busy and self.ai_review is None and self.convention_panel is None):
                # 成功回执及历史落盘后才清空；先停同步，不能把空文本同步回目标。
                if self.sync_mode_var.get():
                    self.live_sync.paused = True
                    self.live_sync.cancel_timer()
                self.cancel_auto_timer()
                self.auto_request += 1
                self.auto_running = False
                self.one_filler_button.state(['!disabled'])
                self.set_button_label(self.one_filler_button, 'AI 整理一次')
                self.refresh_cancel_button()
                self.text.defer_fit_until_interaction()
                self.set_text('')
                self.draft_id = None
                self.auto_block_revision = self.revision
                self.status_var.set(value.message + ' · 已存入历史并清空，可用「上一步」恢复')
                return True
            self.status_var.set(self.status_var.get() + ' · 正文已变化，保留当前内容')
        return False

    def close_app(self):
        if self.closing: return
        if self.target_filling:
            self.status_var.set("正在填入并记录历史，请稍候再退出")
            return
        if self.busy: self.cancel_operation()
        self.close_convention()
        if self.ai_review is not None:
            self.ai_review_finish()
        if not self.save_current():
            return
        self.flush_manual_learning()
        self.closing=True
        if hasattr(self, 'coloplay'):
            self.coloplay.close()
        self.system_style.close()
        self.free_service.close()
        for job in self.button_animation_jobs:
            self.after_cancel(job)
        self.button_animation_jobs.clear()
        for job, _ in self.feedback_jobs.values():
            self.after_cancel(job)
        self.feedback_jobs.clear()
        if self.guide_job:
            self.after_cancel(self.guide_job)
        if self.guide:
            self.guide.close(remember=False)
        self.hover_help.close()
        if hasattr(self, 'live_sync'):
            self.live_sync.close()
        if hasattr(self, 'window_controller'):
            self.window_controller.close()
        self.cancel_hold()
        self.cancel_auto_timer()
        self.magnifier.destroy()
        if self.target_controller:
            self.target_controller.close()
        if self.poll_job: self.after_cancel(self.poll_job)
        if self.capture_job: self.after_cancel(self.capture_job)
        self.destroy()


if __name__ == "__main__":
    if '--free-service-self-check' in sys.argv:
        from talkman_free_service import self_check
        index = sys.argv.index('--free-service-self-check')
        sys.exit(self_check(sys.argv[index+1]))
    if '--coloplay-self-check' in sys.argv:
        from talkman_coloplay import smoke_check
        index=sys.argv.index('--coloplay-self-check')
        sys.exit(smoke_check(sys.argv[index+1]))
    app=TalkManApp()
    if '--coloplay-simulator' in sys.argv:
        app.after(300,app.coloplay.open_simulator)
    app.mainloop()
