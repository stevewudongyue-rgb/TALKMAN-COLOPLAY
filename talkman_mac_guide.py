"""Mac 首次使用的窗内引导。只在明确点击时申请权限或保存连接。"""
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import ttk

from talkman_macos_setup import check_permissions, open_permission_settings
from talkman_voice import prepare_microphone_permission, VoicePermissionError
from talkman_coloplay_discovery import local_addresses
from talkman_coloplay_protocol import validate_config, save_config

GUIDE_VERSION = 1
STEPS = ('安装位置', '麦克风', '语音服务', '设备连接', 'Agent 授权', '开始使用')
TITLES = ('先把应用放好', '允许使用麦克风', '先用免费服务体验',
          '把 COLOPLAY 连起来', '允许填入 Agent', '说出来，改好再发')


def mac_guide_enabled():
    return sys.platform == 'darwin'


def should_offer_mac_guide(preferences):
    return preferences.get('mac_setup_guide_version', 0) != GUIDE_VERSION


def installed_location(path):
    if path is None:
        return False
    return any(path.is_relative_to(root) for root in (Path('/Applications'), Path.home() / 'Applications'))


class MacSetupGuide:
    modal = True

    def __init__(self, app):
        self.app, self.opened, self.waiting_settings = app, True, False
        self.job = self.aux_window = None
        self.callbacks = queue.Queue()
        self.permission_pending = False
        self.feedback = tk.StringVar(app, '')
        self.status = tk.StringVar(app, '')
        self.labels = [tk.StringVar(app, '') for _ in STEPS]
        self.host = tk.StringVar(app, app.coloplay.config['host'])
        self.device_host = tk.StringVar(app, app.coloplay.config.get('device_host', ''))
        self.discovery = tk.BooleanVar(app, app.coloplay.config.get('discovery', True))
        self.addresses = local_addresses()
        if self.host.get() not in self.addresses and len(self.addresses) == 1:
            self.host.set(self.addresses[0])
        app.hover_help.hide(); app.target_picker.close()
        self.panel = tk.Frame(app, bg=app.palette['bg'], highlightthickness=1,
                              highlightbackground=app.palette['editor_border'])
        self.panel.bind('<Escape>', lambda _e: self.close())
        page = app.ui_preferences.get('mac_setup_step', 0)
        self.show(page if type(page) is int and 0 <= page < len(STEPS) else 0)
        self.poll()

    def remember(self, **values):
        try:
            self.app.save_ui_preferences(**values)
        except OSError:
            self.feedback.set('进度未能保存，可继续使用；下次可能重新显示。')

    def label(self, text, *, muted=False):
        widget = ttk.Label(self.body, text=text, style='Muted.TLabel' if muted else 'TLabel',
                           wraplength=460, justify='left', font=(self.app.fonts.ui, self.app.font_size(11)))
        widget.pack(fill='x', pady=(0, 8))
        self.wrapping.append(widget)
        return widget

    def buttons(self, items):
        row = ttk.Frame(self.body); row.pack(fill='x', pady=(0, 8))
        for index, (text, action) in enumerate(items):
            button = self.app.button(row, text, action, style='Ghost.TButton' if index else 'TButton')
            button.pack(side='left', padx=(0, 8))
        return row

    def show(self, page):
        if not self.opened:
            return
        self.page = page
        self.waiting_settings = False
        self.feedback.set('')
        self.remember(mac_setup_step=page)
        for child in self.panel.winfo_children():
            child.destroy()
        self.wrapping = []
        self.mic_button = None
        a, p = self.app, self.app.palette
        header = ttk.Frame(self.panel, padding=(24, 18, 24, 12)); header.pack(fill='x')
        ttk.Label(header, text='TALKMAN / MAC', font=(a.fonts.mono, a.font_size(11)), foreground=p['signal']).pack(side='left')
        a.button(header, '稍后继续', self.close, style='Ghost.TButton').pack(side='right')
        footer = ttk.Frame(self.panel, padding=(24, 12, 24, 18)); footer.pack(side='bottom', fill='x')
        self.back = a.button(footer, '上一步', lambda: self.show(page-1), style='Ghost.TButton')
        self.back.pack(side='left'); self.back.state(['disabled'] if page == 0 else ['!disabled'])
        ttk.Label(footer, text=f'{page+1:02d} / 06', font=(a.fonts.mono, a.font_size(11)), style='Muted.TLabel').pack(side='left', padx=16)
        self.next = a.button(footer, '进入编辑' if page == 5 else '下一步',
                             self.close if page == 5 else lambda: self.show(page+1), style='Accent.TButton')
        self.next.pack(side='right')
        layout = ttk.Frame(self.panel, padding=(24, 0, 24, 0)); layout.pack(fill='both', expand=True)
        rail = ttk.Frame(layout, width=152); rail.pack(side='left', fill='y', padx=(0, 24)); rail.pack_propagate(False)
        self.rail_buttons = []
        for i, title in enumerate(STEPS):
            button = a.button(rail, f'{i+1:02d}  {title}', lambda i=i: self.show(i),
                              style='Accent.TButton' if i == page else 'Ghost.TButton')
            button.pack(fill='x', pady=(0, 2)); self.rail_buttons.append(button)
            ttk.Label(rail, textvariable=self.labels[i], style='Muted.TLabel', font=(a.fonts.ui, a.font_size(9))).pack(anchor='w', padx=12, pady=(0, 10))
        self.body = ttk.Frame(layout); self.body.pack(side='left', fill='both', expand=True)
        ttk.Label(self.body, text=TITLES[page], font=(a.fonts.ui, a.font_size(22), 'bold')).pack(anchor='w', pady=(0, 8))
        ttk.Label(self.body, textvariable=self.status, foreground=p['signal'], font=(a.fonts.ui, a.font_size(11))).pack(anchor='w', pady=(0, 10))
        getattr(self, f'page_{page}')()
        feedback = ttk.Label(self.body, textvariable=self.feedback, style='Muted.TLabel', wraplength=460, justify='left')
        feedback.pack(fill='x', pady=(4, 0)); self.wrapping.append(feedback)
        self.body.bind('<Configure>', lambda e: [w.configure(wraplength=max(160, e.width-4)) for w in self.wrapping])
        self.panel.place(x=8, y=8, relwidth=1, relheight=1, width=-16, height=-16)
        self.panel.lift(); self.panel.grab_set(); self.panel.focus_set()
        self.refresh_status()

    def page_0(self):
        self.label('在安装镜像中，把 TALKMAN COLOPLAY.app 拖到“应用程序”，再从那里打开。以后覆盖升级会保留已保存的 Key、历史和配对。')
        self.label('若系统阻止测试版：先尝试打开，再到“系统设置 → 隐私与安全性 → 仍要打开”。', muted=True)
        self.label('后面每一步都能跳过。回到系统设置授权后，返回这里会自动检查；在“设置 → 新手引导”继续。', muted=True)

    def page_1(self):
        self.label('点击授权，在 macOS 提示中允许。这里只申请权限，不会开始录音。')
        row = self.buttons((('授权麦克风', self.allow_microphone), ('打开麦克风设置', lambda: self.open_permission('microphone'))))
        self.mic_button = row.winfo_children()[0]
        self.label('之前点过“不允许”？在“隐私与安全性 → 麦克风”开启 TALKMAN COLOPLAY。若被公司策略限制，请联系管理员。', muted=True)
        self.label('开始说话后，录制的音频会交给所选在线服务转换成文字。', muted=True)

    def page_2(self):
        self.label('免费语音识别 + 星辰文字改写，无需 API Key。网络可用性和识别效果在实际使用时确认。')
        self.buttons((('使用免费服务', self.use_free_service), ('配置自己的千问 Key', lambda: self.open_settings('model'))))
        self.label('推荐长期使用自己的千问通用 API Key。模型设置中可加密记住 Key；费用按你的千问账户计算。', muted=True)
        self.label('在线服务会接收你主动录制的音频和待修改文字。免费服务受额度及服务策略限制。', muted=True)

    def page_3(self):
        self.label('保留 USB 数据线，接入同一局域网。\n设备启动与这台 Mac 配对的新版 TalkMan 卡带。')
        row = ttk.Frame(self.body); row.pack(fill='x', pady=(0, 8))
        ttk.Label(row, text='本机 IP · Mac', width=17).pack(side='left')
        self.host_choice = ttk.Combobox(row, textvariable=self.host, values=self.addresses, width=18)
        self.host_choice.pack(side='left', fill='x', expand=True)
        self.app.button(row, '刷新', self.refresh_addresses, style='Ghost.TButton').pack(side='left', padx=(6, 0))
        row = ttk.Frame(self.body); row.pack(fill='x', pady=(0, 8))
        ttk.Label(row, text='设备 IP · 可留空', width=17).pack(side='left')
        ttk.Entry(row, textvariable=self.device_host, width=18).pack(side='left', fill='x', expand=True)
        ttk.Checkbutton(self.body, text='自动寻找已配对电脑', variable=self.discovery).pack(anchor='w', pady=(0, 10))
        self.buttons((('保存并连接', self.save_connection), ('本地网络权限', lambda: self.open_permission('local_network')),
                      ('配对与卡带', self.open_device_settings)))
        self.label('广播受限时填设备 IP；换网后刷新地址并保存。\n首次配对需导出源码、编译 ROM 后安装；无设备可跳过。', muted=True)

    def page_4(self):
        self.label('要把文字填入其他应用，需要允许“辅助功能”。只在 TALKMAN COLOPLAY 内编辑或复制文字，可以跳过。')
        self.buttons((('打开辅助功能设置', lambda: self.open_permission('accessibility')),))
        self.label('开启 TALKMAN COLOPLAY；列表里没有时，点“＋”从“应用程序”添加。返回这里会自动检查。', muted=True)
        self.label('打开豆包等应用，再刷新 TalkMan 的目标列表。首次可先点击对方输入框，再填入；填入不会自动发送。', muted=True)

    def page_5(self):
        self.label('01  录入：左下开始，再按左下结束。\n02  修改：选中文字，按住右下再按左下，说修改要求。\n03  填入：右下＋旋转选 Agent，松手确认；长按旋钮填入。')
        self.label('填入后 5 秒内，左上发送、右上取消；左下可以直接开始新录入。桌面上的按钮也能完成录入和修改。', muted=True)
        self.buttons((('查看四键与旋钮图解', self.open_device_guide),))
        self.label('进入编辑后，点“录音和覆盖”或设备左下试说一句话。引导不会自动录音、修改正文或向 Agent 发送。未完成项仍可在左侧补齐。', muted=True)

    def refresh_addresses(self):
        self.addresses = local_addresses(); self.host_choice.configure(values=self.addresses)
        if len(self.addresses) == 1:
            self.host.set(self.addresses[0])
        self.feedback.set('已刷新 Mac 地址。多块网卡时选与 COLOPLAY 同网段的地址。')

    def save_connection(self):
        d = self.app.coloplay
        try:
            cfg = validate_config({**d.config, 'enabled': True, 'host': self.host.get().strip(),
                                   'device_host': self.device_host.get().strip(), 'discovery': self.discovery.get()})
            if set(cfg['bindings'].values()) & set(self.app.shortcuts.values()):
                raise ValueError('设备快捷键有冲突，请到高级连接调整。')
            d.configure(cfg)
            if not d.server:
                raise ValueError('服务未启动，请刷新 Mac 地址并检查端口。')
            save_config(d.path, d.config)
            self.feedback.set(d.discovery_error or ('已保存，等待设备连接。若出现“本地网络”提示请选择允许。'
                              if cfg['discovery'] else '已保存固定地址模式；卡带地址需与 Mac 一致。'))
        except (ValueError, OSError) as exc:
            self.feedback.set('连接未完成：' + str(exc))
        self.refresh_status()

    def use_free_service(self):
        a = self.app
        if a.busy or a.auto_running or a.ai_review is not None:
            self.feedback.set('请先结束当前录音或改写。'); return
        if not a.free_service.configured:
            self.feedback.set('免费服务组件缺失，请安装最新版本。'); return
        try:
            a.save_ui_preferences(free_mode=True)
        except OSError:
            self.feedback.set('未能保存服务选择，请稍后重试。'); return
        a.free_mode_var.set(True); a.cancel_auto_timer(); a.auto_request += 1
        a.provider_var.set(('代码口述 · ' if a.conventions.language != 'off' else '') + a._provider_label())
        self.feedback.set('已选择免费服务，原来的 Key 已保留。联网可用性将在使用时确认。')
        self.refresh_status()

    def allow_microphone(self):
        a = self.app
        if self.permission_pending or a._mic_permission_pending or a.busy:
            return
        a._mic_permission_request += 1
        request = a._mic_permission_request
        a._mic_permission_pending = self.permission_pending = True
        def completed(allowed, message):
            a.events.put(('mic_permission', request, allowed, message))
            self.callbacks.put((allowed, message))
        try:
            if prepare_microphone_permission(completed):
                a._mic_permission_pending = self.permission_pending = False
                self.feedback.set('麦克风已允许，进入编辑后再开始录音。')
            else:
                self.feedback.set('请在系统提示中允许；这一步不会录音。')
        except VoicePermissionError as exc:
            a._mic_permission_pending = self.permission_pending = False
            self.feedback.set(str(exc))
        self.refresh_status()

    def open_permission(self, kind):
        self.feedback.set(open_permission_settings(kind))

    def suspend(self):
        self.waiting_settings = True
        if self.panel.grab_current() == self.panel:
            self.panel.grab_release()
        self.panel.place_forget()

    def open_settings(self, page):
        self.suspend(); self.app.open_settings(page)
        self.aux_window = self.app.settings_window

    def open_device_settings(self):
        self.suspend(); self.app.coloplay.connection_settings()
        self.aux_window = self.app.coloplay_window

    def settings_closed(self):
        if self.opened and self.waiting_settings and not self.app.closing:
            self.aux_window = None
            self.host.set(self.app.coloplay.config['host'])
            self.device_host.set(self.app.coloplay.config.get('device_host', ''))
            self.discovery.set(self.app.coloplay.config.get('discovery', True))
            self.show(self.page)

    def open_device_guide(self):
        self.close(); self.app.coloplay.show_guide()

    def refresh_status(self):
        s, a, d = check_permissions(), self.app, self.app.coloplay
        network = ('仅模拟，未连实机' if d.simulator else 'USB 已连接' if d.local_session and d.local_session.enabled
                   else 'Wi-Fi 已连通' if d.peer else '等待设备' if d.server else '尚未连接')
        service = ('免费 · 待使用验证' if a.free_mode_var.get() and a.free_service.configured else
                   '自备模型 · 待验证' if not a.free_mode_var.get() and a.text_model_ready() else '待配置')
        values = ['已在应用程序' if installed_location(s.app) else '请确认安装位置',
                  s.microphone_label, service, network, s.accessibility_label + ' · 可选', '图解与试用']
        for label, value in zip(self.labels, values):
            label.set(value)
        current = values[self.page]
        if self.page == 3 and not d.peer and not d.local_session:
            current += ' · 本地网络权限需到系统查看'
        self.status.set(current)
        if self.mic_button is not None:
            self.mic_button.state(['disabled'] if a._mic_permission_pending else ['!disabled'])

    def poll(self):
        self.job = None
        if not self.opened or self.app.closing:
            return
        while not self.callbacks.empty():
            allowed, message = self.callbacks.get_nowait()
            self.permission_pending = False
            self.feedback.set('麦克风已允许，进入编辑后再开始录音。' if allowed else message)
        if self.waiting_settings and self.aux_window is not None and not self.aux_window.winfo_exists():
            self.settings_closed()
        self.refresh_status()
        self.job = self.app.after(1000, self.poll)

    def refresh_theme(self):
        self.panel.configure(bg=self.app.palette['bg'], highlightbackground=self.app.palette['editor_border'])
        if not self.waiting_settings:
            self.show(self.page)

    def close(self, remember=True):
        if not self.opened:
            return
        self.opened = False
        if self.job:
            self.app.after_cancel(self.job); self.job = None
        if self.panel.grab_current() == self.panel:
            self.panel.grab_release()
        self.panel.destroy()
        if remember:
            self.remember(mac_setup_guide_version=GUIDE_VERSION, mac_setup_step=self.page,
                          guide_seen=True, guide_version=2)
        if not self.app.closing:
            self.app.window_controller.sync_hotkeys(); self.app.text.focus_set()
