"""桌面、旋钮和语音共用的位置选择；UI 仅接收编号与矩形。"""
import queue
import time
import tkinter as tk
from tkinter import ttk

from talkman_input_outline import InputOutline


class InputPicker:
    def __init__(self, app):
        self.app = app
        self.active = False
        self.generation = 0
        self.index = 0
        self.regions = []
        self.ticket = None
        self.target = None
        self.error = ''
        self.send_choices = {}
        self.loading = False
        self.confirming = False
        self.checking = False
        self.panel = None
        self.job = None
        self.outline = InputOutline()
        self.events = queue.Queue()
        self.last_check = 0
        app.bind('<Configure>', self.reposition, add=True)

    def stamp(self):
        return self.generation, self.active, self.index, bool(self.error), self.confirming

    def begin(self, target=None, *, source='desktop'):
        a = self.app
        controller = a.target_controller
        target = target or (controller.selected if controller else None)
        if not controller or target is None or a.target_filling:
            a.status_var.set('请先选择可用的 Agent 窗口。')
            return
        self.cancel(quiet=True)
        self.active, self.loading, self.source = True, True, source
        self.target, self.ticket, self.regions, self.index = target, None, [], 0
        self.error = ''
        self.send_choices = {}
        a.disarm_delivery()
        a.sync_mode_var.set(False)
        a.live_sync.configure(False)
        a.target_picker.close()
        a.window_controller.reveal(focus=False)
        self.build()
        self.render()
        generation = self.generation
        controller.begin_inputs(target, lambda state, value:
            self.events.put((generation, 'begin', 0, state, value)))
        self.tick()

    def build(self):
        a = self.app
        self.panel = ttk.Frame(a, padding=8, style='Panel.TFrame', borderwidth=1, relief='solid')
        self.title = tk.StringVar(a)
        self.note = tk.StringVar(a)
        ttk.Label(self.panel, textvariable=self.title, style='Panel.TLabel').pack(anchor='w')
        self.note_label = ttk.Label(self.panel, textvariable=self.note, style='PanelMuted.TLabel', wraplength=370)
        self.note_label.pack(fill='x', pady=(2, 4))
        self.enter_send = tk.BooleanVar(a, False)
        self.send_toggle = ttk.Checkbutton(self.panel, text='此位置用回车发送 · 填入后再次确认',
            variable=self.enter_send, command=lambda: self.send_choices.update({self.index: self.enter_send.get()}))
        self.send_toggle.pack(anchor='w', pady=(0, 4))
        row = ttk.Frame(self.panel, style='Panel.TFrame')
        row.pack(fill='x')
        self.previous = a.button(row, '← 上一个', lambda: self.step(-1), style='PanelGhost.TButton')
        self.previous.pack(side='left')
        self.next = a.button(row, '下一个 →', lambda: self.step(1), style='PanelGhost.TButton')
        self.next.pack(side='left', padx=4)
        self.confirm_button = a.button(row, '确认位置', self.confirm, style='PanelGhost.TButton')
        self.confirm_button.pack(side='right')
        row2 = ttk.Frame(self.panel, style='Panel.TFrame')
        row2.pack(fill='x', pady=(2, 0))
        self.retry = a.button(row2, '重新查找', lambda: self.begin(self.target, source=self.source), style='PanelGhost.TButton')
        self.retry.pack(side='left')
        self.cancel_button = a.button(row2, '取消 · 保留原目标', self.cancel, style='PanelGhost.TButton')
        self.cancel_button.pack(side='right')
        for widget in (self.previous, self.next, self.confirm_button, self.retry, self.cancel_button):
            widget.configure(padding=(8, 3))
            widget.bind('<Escape>', lambda _e: self.cancel_key())
        self.reposition()
        self.panel.lift()

    def reposition(self, event=None):
        if not self.panel or (event is not None and event.widget != self.app):
            return
        width = min(440, max(280, self.app.winfo_width()-24))
        bottom = self.app.winfo_height()-64
        for name in ('replace_button', 'compact_record', 'target_button', 'compact_fill'):
            button = getattr(self.app, name, None)
            if button is not None and button.winfo_ismapped():
                bottom = min(bottom, button.winfo_rooty()-self.app.winfo_rooty()-12)
                break
        bottom = max(self.panel.winfo_reqheight()+12, bottom)
        self.panel.place(relx=1, x=-12, y=bottom, anchor='se', width=width)
        self.note_label.configure(wraplength=width-32)

    def render(self):
        if not self.panel:
            return
        count = len(self.regions)
        self.title.set(f'{self.target.app} · 选择输入位置' + (f' {self.index+1}/{count}' if count else ''))
        self.note.set(self.error or ('正在查找可编辑区域…' if self.loading else
            '正在确认位置…' if self.confirming else '旋转切换 · 点按旋钮确认 · 右上取消'))
        enabled = bool(count and not self.loading and not self.confirming and not self.error)
        if count:
            self.enter_send.set(self.send_choices.setdefault(self.index, self.regions[self.index].enter_send))
        self.send_toggle.state(['!disabled'] if enabled else ['disabled'])
        for button in (self.previous, self.next, self.confirm_button):
            button.state(['!disabled'] if enabled else ['disabled'])
        self.retry.state(['disabled'] if self.loading or self.confirming else ['!disabled'])
        self.cancel_button.state(['disabled'] if self.confirming else ['!disabled'])
        self.app.status_var.set(self.error or self.title.get())

    def step(self, delta):
        if not self.active or not self.regions or self.error or self.confirming:
            return
        self.index = (self.index + delta) % len(self.regions)
        self.outline.hide()
        self.last_check = 0
        self.render()

    def confirm(self):
        if (not self.active or not self.ticket or self.error or self.loading or self.confirming
                or (self.app.busy and getattr(self.app, 'voice_input_navigation', False))):
            return
        if not self.outline.show(self.target, self.regions[self.index]):
            self.outline.hide()
            self.error = '无法显示输入位置高亮，请重新选择；本次未确认。'
            self.render()
            return
        self.confirming = True
        generation, index = self.generation, self.index
        self.render()
        self.app.target_controller.inspect_input(self.ticket, index,
            lambda state, value: self.events.put((generation, 'confirm', index, state, value)),
            confirm=True, enter_send=self.enter_send.get())

    def navigate(self, command):
        if not self.active:
            self.app.status_var.set('请先选择 Agent，再选择输入位置。')
            return
        action, value = command
        if action == 'cancel':
            self.cancel()
        elif action == 'confirm':
            self.confirm()
        elif action == 'step':
            self.step(value)
        elif action == 'index':
            if 0 <= value < len(self.regions):
                self.step(value-self.index)
            else:
                self.app.status_var.set('本轮没有这个输入框编号，请按屏幕编号选择。')

    def tick(self):
        self.job = None
        if not self.active or self.app.closing:
            return
        while not self.events.empty():
            generation, kind, index, state, value = self.events.get_nowait()
            if generation != self.generation:
                continue
            if kind == 'inspect':
                self.checking = False
                if index != self.index or self.confirming:
                    continue
            if state != 'ok':
                self.loading = self.confirming = False
                self.error = str(value)
                self.outline.hide()
            elif kind == 'begin':
                self.loading = False
                self.ticket, self.regions = value['ticket'], value['regions']
            elif kind == 'inspect':
                self.regions[index] = value['region']
                if not self.outline.show(self.target, value['region']):
                    self.outline.hide()
                    self.error = '无法显示输入位置高亮，请重新选择；本次未确认。'
            elif kind == 'confirm':
                label = self.app.target_controller.label
                warning = value.get('warning', '')
                self.cancel(quiet=True)
                self.app.target_label_var.set(label)
                mode = ('填入核验后可再次确认发送。' if value['region'].enter_send else
                        '此位置仅填入，提交请在目标内完成。')
                self.app.status_var.set(f'已确认：{label}；{mode}' + warning)
                return
            self.render()
        if self.ticket and not self.error and not self.confirming and not self.checking and time.monotonic()-self.last_check >= .3:
            self.checking = True
            self.last_check = time.monotonic()
            generation, index = self.generation, self.index
            self.app.target_controller.inspect_input(self.ticket, index,
                lambda state, value: self.events.put((generation, 'inspect', index, state, value)))
        self.job = self.app.after(60, self.tick)

    def cancel_key(self):
        self.cancel()
        return 'break'

    def cancel(self, *, quiet=False):
        if self.confirming and not quiet:
            return
        was_active = self.active
        self.active = self.loading = self.confirming = self.checking = False
        self.generation += 1
        if self.job:
            self.app.after_cancel(self.job)
            self.job = None
        if was_active and self.app.target_controller:
            self.app.target_controller.cancel_inputs()
        self.outline.hide()
        if self.panel:
            self.panel.destroy()
            self.panel = None
        if was_active and not quiet:
            if self.app.busy and getattr(self.app, 'voice_input_navigation', False):
                self.app.cancel_operation()
            self.app.status_var.set('已取消位置选择，原目标保持不变。')

    def close(self):
        self.cancel(quiet=True)
        self.outline.close()
