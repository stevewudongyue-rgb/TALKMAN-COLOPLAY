"""欢迎与连接弹窗；其余步骤直接指向主界面的真实控件。"""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
import webbrowser

from talkman_credentials import secure_storage_supported
from talkman_qwen import (DEFAULT_SERVICE_ORIGIN, QwenError, _validate_api_key,
                          qwen_key_remembered, save_settings)

GUIDE_VERSION = 2
STEPS = (
    ('连接语音和智能修改', '', ('settings_button',)),
    ('说话变文字', '点这里开始说话，再点一次结束；也可以按住说、松手停。没选字就接着写，选中一段就替换那一段。', ('replace_button',)),
    ('告诉它怎么改', '先用鼠标选一段，再点这里说“把苹果改成梨”。它会按你的意思修改；改错了点“上一步”。', ('rewrite_button',)),
    ('检查后，放进别的应用', '选好目标，点“填入 Agent”放入对方输入框；核对后再点“下发”发送。也可在对方窗口手动发送。', ('target_selector', 'target_button')),
)


def should_offer_guide(preferences, has_key):
    return (not preferences.get('guide_seen', False)
            or (preferences.get('guide_version', 0) < GUIDE_VERSION and not has_key))


class QuickGuide:
    def __init__(self, app):
        self.app, self.page = app, 0
        self.opened, self.dialog, self.panel, self.job = True, None, None, None
        self.waiting_settings = False
        self.markers, self.targets = [], ()
        app.hover_help.hide()
        app.target_picker.close()
        self.configure_binding = app.bind('<Configure>', self.resize, add=True)
        self.welcome()

    @property
    def modal(self):
        return self.dialog is not None or self.waiting_settings

    def _clear(self):
        self.app.hover_help.hide()
        if self.job:
            self.app.after_cancel(self.job)
            self.job = None
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
        if self.panel is not None:
            self.panel.destroy()
            self.panel = None
        for marker in self.markers:
            marker.destroy()
        self.markers, self.targets = [], ()

    def _dialog(self, title):
        self._clear()
        win = self.dialog = tk.Toplevel(self.app)
        win.withdraw()
        win.title(title + ' · TALKMAN COLOPLAY')
        win.configure(bg=self.app.palette['bg'])
        win.transient(self.app)
        win.resizable(False, False)
        win.protocol('WM_DELETE_WINDOW', self.close)
        win.bind('<Escape>', lambda _e: self.close())
        shell = ttk.Frame(win, padding=24)
        shell.pack(fill='both', expand=True)
        return shell

    def _present(self):
        win, app = self.dialog, self.app
        win.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width()-win.winfo_reqwidth())//2
        y = app.winfo_rooty() + max(8, (app.winfo_height()-win.winfo_reqheight())//2)
        win.geometry(f'+{x}+{y}')
        win.deiconify()
        win.grab_set()
        win.lift()

    def _label(self, parent, text, title=False):
        label = ttk.Label(parent, text=text, font=(self.app.fonts.ui, 16 if title else 11),
                          wraplength=420, justify='left')
        label.pack(anchor='w', pady=(0, 16))
        return label

    def welcome(self):
        shell = self._dialog('欢迎使用')
        self._label(shell, '把话说出来，改好再发。', title=True)
        self._label(shell, 'TALKMAN COLOPLAY 是放在桌面上的文字草稿框。\n你可以说话输入，选一段让 AI 改写，检查好后再放进聊天或工作软件。')
        self._label(shell, '安装后自动接入免费语音和星辰改写，无需填写 Key。\n接下来只需：说话 → 修改 → 填入。')
        self._label(shell, '免费服务适合先体验；推荐使用自己的千问 API Key，在“设置 → 模型连接”配置，用量按千问账户计费。')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(8, 0))
        self.app.button(row, '稍后再看', self.close, style='Ghost.TButton').pack(side='left')
        self.welcome_next = self.app.button(row, '开始使用', lambda: self.show(0), style='Accent.TButton')
        self.welcome_next.pack(side='right')
        self._present()
        self.welcome_next.focus_set()

    def show(self, page):
        if not self.opened:
            return
        self.page = max(0, min(3, page))
        self.waiting_settings = False
        self._clear()
        if self.page == 0 and self.app.free_mode_var.get() and self.app.free_service.configured:
            shell = self._dialog('免费在线内测')
            self._label(shell, '免费服务，安装即用', title=True)
            self._label(shell, '自动接入免费语音与星辰文字服务，Windows 和 Mac 均无需单独开通。说话结束后返回文字，也可以用指令改写和整理。')
            self._label(shell, '推荐使用自己的千问 API Key。可在“设置 → 模型连接”配置后关闭“使用免费服务”，切回自己的模型；用量按千问账户计费。')
            from talkman_free_service import NOTICE
            self._label(shell, NOTICE)
            self.app.button(shell, '开始使用', lambda: self.show(1), style='Accent.TButton').pack(anchor='e')
            self._present()
            return
        if self.page == 0:
            self._key_step()
            return
        app = self.app
        title, body, names = STEPS[self.page]
        if self.page == 3 and app.sync_mode_var.get():
            body = '在这里选目标。当前已开同步，文字会跟着出现；点“下发”会在目标输入框按回车发送。'
        self.targets = tuple(getattr(app, name) for name in names)
        self.panel = ttk.Frame(app, padding=16, style='Panel.TFrame', relief='solid', borderwidth=1)
        self.panel.columnconfigure(0, weight=1)
        ttk.Label(self.panel, text=f'{self.page+1} / 4　{title}', style='Panel.TLabel',
                  font=(app.fonts.ui, 12)).grid(row=0, column=0, sticky='w', pady=(0, 8))
        self.body_label = ttk.Label(self.panel, text=body, style='Panel.TLabel',
                                    font=(app.fonts.ui, 11), justify='left')
        self.body_label.grid(row=1, column=0, sticky='ew')
        row = ttk.Frame(self.panel, style='Panel.TFrame')
        row.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        app.button(row, '上一步', lambda: self.show(self.page-1), style='PanelGhost.TButton').pack(side='left')
        app.button(row, '结束引导', self.close, style='PanelGhost.TButton').pack(side='left', padx=4)
        self.next = app.button(row, '完成' if self.page == 3 else '下一步', self.advance, style='Accent.TButton')
        self.next.pack(side='right')
        self.markers = [tk.Frame(app, bg=app.palette['text']) for _ in range(4)]
        self.reposition()

    def _key_step(self):
        app = self.app
        shell = self._dialog('第 1 步：连接服务')
        self._label(shell, '1 / 4　先填 API Key', title=True)
        self._label(shell, '语音和智能修改需要连接模型服务。API Key 就是连接凭证，请从自己的千问账户复制；用量由服务商计费。')
        self.key = tk.StringVar(value=app.settings.api_key)
        origin = app.settings.service_origin
        if not app.settings.api_key and origin == DEFAULT_SERVICE_ORIGIN:
            origin = 'https://maas.qianwenaiapi.com'
        self.origin = tk.StringVar(value=origin)
        self.key_feedback = tk.StringVar(value='已填写，可保留原设置继续。' if app.settings.api_key else '未填写前，语音和智能修改暂时不能用。')
        ttk.Label(shell, text='千问通用 API Key').pack(anchor='w', pady=(0, 6))
        self.key_entry = ttk.Entry(shell, textvariable=self.key, show='•', width=48, font=(app.fonts.ui, 11))
        self.key_entry.pack(fill='x')
        app.hover_help.attach(self.key_entry, '在千问控制台创建并复制通用 API Key，粘贴在这里。请不要把它发给别人。')
        supported = secure_storage_supported()
        self.remember = tk.BooleanVar(value=supported and (not app.settings.api_key or qwen_key_remembered(app.data_dir)))
        check = ttk.Checkbutton(shell, text='在这台电脑加密记住 Key', variable=self.remember)
        check.pack(anchor='w', pady=(8, 12))
        if not supported:
            check.state(['disabled'])
        app.hover_help.attach(check, '勾选后由系统加密保存。未勾选时只在这次运行中使用，退出后需要重新填写。')
        ttk.Label(shell, text='服务地址（与 Key 所在工作空间一致）').pack(anchor='w', pady=(0, 6))
        ttk.Combobox(shell, textvariable=self.origin, font=(app.fonts.ui, 11),
                     values=('https://maas.qianwenaiapi.com', app.settings.service_origin)).pack(fill='x')
        ttk.Label(shell, textvariable=self.key_feedback, wraplength=420, justify='left',
                  style='Muted.TLabel').pack(anchor='w', pady=(12, 8))
        links = ttk.Frame(shell)
        links.pack(fill='x')
        app.button(links, '去千问获取 Key', lambda: webbrowser.open('https://platform.qianwenai.com/home/api-keys'),
                   style='Ghost.TButton').pack(side='left')
        app.button(links, '其他服务／更多设置', self.more_settings, style='Ghost.TButton').pack(side='right')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(16, 0))
        app.button(row, '先打字试用', lambda: self.show(1), style='Ghost.TButton').pack(side='left')
        self.key_next = app.button(row, '保存并继续', self.save_key, style='Accent.TButton')
        self.key_next.pack(side='right')
        self._present()
        (self.key_next if app.settings.api_key else self.key_entry).focus_set()

    def save_key(self):
        app = self.app
        if app.busy or app.auto_running:
            self.key_feedback.set('请先结束当前录音或文字处理，再保存连接设置。')
            return
        try:
            key = _validate_api_key(self.key.get())
            new = replace(app.settings, api_key=key, service_origin=self.origin.get().strip().rstrip('/'))
            unchanged = (new == app.settings and self.remember.get() == qwen_key_remembered(app.data_dir))
            if not unchanged:
                save_settings(app.data_dir, new, self.remember.get())
            app.settings = new
            app.qwen_credentials_error = ''
            app.status_var.set('连接设置已保存；是否可用将在首次调用时确认。')
        except (QwenError, ValueError) as ex:
            self.key_feedback.set(str(ex))
            return
        except Exception:
            self.key_feedback.set('保存失败，请检查系统凭证存储或数据目录权限。')
            return
        self.show(1)

    def more_settings(self):
        self._clear()
        self.waiting_settings = True
        self.app.open_settings('model')

    def settings_closed(self):
        if self.opened and self.waiting_settings and not self.app.closing:
            self.show(1 if self.app.text_model_ready() else 0)

    def advance(self):
        self.close() if self.page == 3 else self.show(self.page+1)

    def resize(self, event):
        if event.widget == self.app and self.panel is not None and self.job is None:
            self.job = self.app.after_idle(self.reposition)

    def reposition(self):
        self.job = None
        if not self.opened or self.panel is None:
            return
        app = self.app
        width = min(390, max(280, app.winfo_width()-32))
        self.body_label.configure(wraplength=width-34)
        # 未映射时完成几何计算，避免新卡片短暂出现在 (0, 0)。
        self.panel.configure(width=width)
        self.panel.update_idletasks()
        left = min(w.winfo_rootx() for w in self.targets)-app.winfo_rootx()
        top = min(w.winfo_rooty() for w in self.targets)-app.winfo_rooty()
        right = max(w.winfo_rootx()+w.winfo_width() for w in self.targets)-app.winfo_rootx()
        bottom = max(w.winfo_rooty()+w.winfo_height() for w in self.targets)-app.winfo_rooty()
        height = self.panel.winfo_reqheight()
        x = max(12, min(left, app.winfo_width()-width-12))
        y = top-height-12 if top-height-12 >= 8 else bottom+12
        y = max(8, min(y, app.winfo_height()-height-8))
        self.panel.place(x=x, y=y, width=width)
        self.panel.lift()
        for marker, pos in zip(self.markers, (
                (left-3, top-3, right-left+6, 2), (left-3, bottom+1, right-left+6, 2),
                (left-3, top-3, 2, bottom-top+6), (right+1, top-3, 2, bottom-top+6))):
            marker.place(x=pos[0], y=pos[1], width=pos[2], height=pos[3])
            marker.lift()

    def refresh_theme(self):
        for marker in self.markers:
            marker.configure(bg=self.app.palette['text'])
        if self.dialog:
            self.dialog.configure(bg=self.app.palette['bg'])

    def close(self, remember=True):
        if not self.opened:
            return
        self.opened = False
        self.app.unbind('<Configure>', self.configure_binding)
        self._clear()
        if remember:
            try:
                self.app.save_ui_preferences(guide_seen=True, guide_version=GUIDE_VERSION)
            except OSError:
                self.app.status_var.set('引导已关闭，下次可能再次显示。')
        if not self.app.closing:
            self.app.window_controller.sync_hotkeys()
            if not self.app.text_model_ready():
                self.app.status_var.set('可先打字；语音和智能修改需要在“设置 → 模型连接”填写 API Key。')
            self.app.text.focus_set()
