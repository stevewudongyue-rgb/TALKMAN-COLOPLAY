"""窗内设备教程。示意图只用于说明，不连接麦克风或目标窗口。"""
import tkinter as tk
from tkinter import ttk
from talkman_coloplay_guide_preview import GuidePreview, SCENES

GUIDE_VERSION = 1
PAGES = (
    ('先认识四键与旋钮', '把操作留在手边，把文字留在桌面。',
     ('左上：AI 改写／确认；长按 5 秒退出设备模式。',
      '左下：点按开始录音，再按一次结束。',
      '右上：退格／取消。右下：按住配合其他操作。'),
     (0, 1, 2, 3), '旋转移光标\n点按开始／结束选框', 'AI 改写', '退格', '录音', '组合'),
    ('说出来，再检查', '左下点按开始，再按结束。',
     ('没有选区：从光标处插入；有选区：覆盖选中内容。',
      '准备麦克风时也可以再次点按结束。右上取消本轮。',
      '贴边收起时会主动展开；录完后保持可见，方便修改。'),
     (2,), '正文在电脑上\n小屏提示录音状态', 'AI 改写', '取消', '开始／结束', '组合'),
    ('旋转找字，点按选字', '不需要一直按住旋钮。',
     ('直接旋转：前后移动光标，一格一个完整字符。',
      '点按旋钮后旋转：从当前位置扩展或缩小选区。',
      '再点一次结束选框，已有选区保留；鼠标也能重新定位。'),
     (4,), '旋转移动\n点按切换选框', 'AI 改写', '退格', '录音／覆盖', '组合'),
    ('指令修改与 AI 对照', '先选文字，再说修改要求。',
     ('按住右下，再按左下：录入修改指令；再按结束。',
      '左上：AI 改写一次。出现对照后，旋转上下阅读。',
      '对照中左上确认，右上放弃；右下＋右上清空正文，可撤销。'),
     (0, 2, 3), '对照时旋转\n上下阅读', '确认改写', '放弃改写', '指令修改', '先按住'),
    ('先选 Agent，再选位置', '最后点按旋钮，才确认本轮目标。',
     ('按住右下并旋转：在四角预览 Agent 名称，多目标自动翻页。',
      '松开右下：展开该窗口。旋转选择编号区域，点按旋钮确认。',
      '右上取消整轮，保留原目标。右下＋左下可说“选择第二个输入框”。'),
     (1, 3, 4), '松开后选位置\n点按旋钮确认', '暂不可用', '取消选择', '位置指令', '按住选目标'),
    ('填入到确认的位置', '新选位置的提交方式尚未验证，请在目标内发送。',
     ('确认位置后，长按旋钮 0.8 秒再松开：填入该位置。',
      '按住旋钮转动只取消填入。位置消失时要求重选，不换到主框。',
      '旧目标原有发送确认仍保留；新选位置暂不支持直接发送或实时同步。'),
     (3, 4), '长按：填入\n按住转动：取消', 'AI 改写', '退格', '新录入', '组合'),
)


class DeviceGuide:
    modal = True

    def __init__(self, app):
        self.app, self.page, self.opened = app, 0, True
        self.preview = None
        self.preview_index = None
        self.hover_job = self.leave_job = None
        self.point_rows = []
        self.panel = tk.Frame(app, bg=app.palette['bg'], highlightthickness=1,
                              highlightbackground=app.palette['editor_border'])
        self.panel.place(x=8, y=8, relwidth=1, relheight=1, width=-16, height=-16)
        self.panel.lift()
        self.panel.grab_set()
        self.panel.bind('<Escape>', self.escape)
        self.panel.bind('<Left>', lambda _e: self.show(max(0, self.page-1)))
        self.panel.bind('<Right>', lambda _e: self.advance())
        self.show(0)

    def show(self, page):
        self.hide_preview()
        self.page = page
        a, p, panel = self.app, self.app.palette, self.panel
        for child in panel.winfo_children(): child.destroy()
        title, subtitle, points, active, center, *labels = PAGES[page]
        if page == 0:
            current = '当前使用免费语音和星辰改写，安装即用。' if a.free_mode_var.get() and a.free_service.configured else '设置中可启用免费语音和星辰改写，无需单独开通。'
            subtitle = current + '推荐使用自己的千问 API Key。\n在「设置 → 模型连接」配置，用量按千问账户计费。'
        header = ttk.Frame(panel, padding=(24, 20, 24, 12)); header.pack(fill='x')
        ttk.Label(header, text='COLOPLAY / 按键指南', foreground=p['signal'],
                  font=(a.fonts.ui, 10, 'bold')).pack(side='left')
        a.button(header, '跳过' if page < 5 else '关闭', self.close, style='Ghost.TButton').pack(side='right')
        titlebox=ttk.Frame(panel,padding=(24, 0, 24, 8));titlebox.pack(fill='x')
        ttk.Label(titlebox, text=title, font=(a.fonts.ui, 22, 'bold')).pack(anchor='w')
        ttk.Label(titlebox, text=subtitle, style='Muted.TLabel').pack(anchor='w',pady=(6,0))
        footer=ttk.Frame(panel,padding=(24,12,24,20));footer.pack(side='bottom',fill='x')
        self.back=a.button(footer,'上一步',lambda:self.show(page-1),style='Ghost.TButton')
        self.back.pack(side='left');self.back.state(['disabled'] if page==0 else ['!disabled'])
        ttk.Label(footer,text=f'{page+1:02d} / {len(PAGES):02d}',foreground=p['muted']).pack(side='left',padx=20)
        a.button(footer,'完成，开始使用' if page==5 else '下一步',self.advance,style='Accent.TButton').pack(side='right')
        body=ttk.Frame(panel,padding=(24,8,24,0));body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1,uniform='guide');body.columnconfigure(1,weight=1,uniform='guide');body.rowconfigure(0,weight=1)
        self.canvas=tk.Canvas(body,bg=p['input'],highlightthickness=1,highlightbackground=p['editor_border'],width=320,height=270)
        self.canvas.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        self.canvas.bind('<Configure>',lambda _e:self.draw(active,center,labels))
        copy=ttk.Frame(body);copy.grid(row=0,column=1,sticky='nsew')
        self.point_labels=[]
        self.point_rows=[]
        for number,point in enumerate(points,1):
            row = tk.Frame(copy, bg=p['bg'], takefocus=True, cursor='hand2',
                           highlightthickness=1, highlightbackground=p['bg'], highlightcolor=p['signal'])
            row.pack(fill='x', pady=(4, 4))
            badge = tk.Label(row, text=f'{number:02d}  ▷', bg=p['bg'], fg=p['signal'],
                             font=(a.fonts.ui,10,'bold'), anchor='w', cursor='hand2')
            badge.pack(fill='x', padx=6, pady=(4, 2))
            label=tk.Label(row,text=point,wraplength=280,justify='left',anchor='w',bg=p['bg'],fg=p['text'],
                           font=(a.fonts.ui,11),cursor='hand2')
            label.pack(anchor='w',fill='x',padx=6,pady=(0,4));self.point_labels.append(label)
            self.point_rows.append(row)
            for widget in (row, badge, label):
                widget.bind('<Enter>', lambda _e, i=number-1: self.queue_preview(i))
                widget.bind('<Leave>', self.queue_leave)
                widget.bind('<Button-1>', lambda _e, i=number-1: self.open_preview(i))
                widget.bind('<Escape>', self.escape)
            row.bind('<FocusIn>', lambda _e, i=number-1: self.open_preview(i))
            row.bind('<FocusOut>', self.queue_leave)
            row.bind('<Return>', lambda _e, i=number-1: self.open_preview(i))
            row.bind('<space>', lambda _e, i=number-1: self.open_preview(i))
        ttk.Label(copy, text='悬停数字或说明，查看操作演示', foreground=p['muted'],
                  font=(a.fonts.ui,9)).pack(anchor='w',padx=7,pady=(4,0))
        copy.bind('<Configure>',lambda e:[label.configure(wraplength=max(120,e.width-16)) for label in self.point_labels])
        self.panel.focus_set()

    def _cancel_job(self, name):
        job = getattr(self, name)
        if job:
            self.app.after_cancel(job)
            setattr(self, name, None)

    def queue_preview(self, index):
        self._cancel_job('leave_job')
        self._cancel_job('hover_job')
        if self.preview_index != index:
            self.hover_job = self.app.after(260, lambda: self.open_preview(index))

    def queue_leave(self, _event=None):
        self._cancel_job('leave_job')
        self.leave_job = self.app.after(160, self.leave_preview)

    @staticmethod
    def _within(widget, parent):
        while widget is not None:
            if widget is parent:
                return True
            widget = getattr(widget, 'master', None)
        return False

    def leave_preview(self):
        self.leave_job = None
        if not self.opened:
            return
        x, y = self.panel.winfo_pointerxy()
        under = self.panel.winfo_containing(x, y)
        if self.preview and self._within(under, self.preview):
            return
        for row in self.point_rows:
            if self._within(under, row) or self._within(self.panel.focus_get(), row):
                return
        self.hide_preview()

    def open_preview(self, index):
        self._cancel_job('hover_job')
        self._cancel_job('leave_job')
        if not self.opened or self.preview_index == index:
            return 'break'
        self.hide_preview()
        self.preview_index = index
        self.point_rows[index].configure(highlightbackground=self.app.palette['signal'])
        self.preview = GuidePreview(self.canvas, self.app.palette, self.app.fonts.ui,
                                    SCENES[self.page][index], index+1, self.hide_preview)
        pending = [self.preview]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            widget.bind('<Enter>', lambda _e: self._cancel_job('leave_job'), add=True)
            widget.bind('<Leave>', self.queue_leave, add=True)
            widget.bind('<Escape>', self.escape)
        return 'break'

    def hide_preview(self):
        self._cancel_job('hover_job')
        self._cancel_job('leave_job')
        if self.preview:
            self.preview.destroy()
            self.preview = None
        self.preview_index = None
        for row in self.point_rows:
            if row.winfo_exists():
                row.configure(highlightbackground=self.app.palette['bg'])

    def escape(self, _event=None):
        if self.preview or self.hover_job:
            self.hide_preview()
        else:
            self.close()
        return 'break'

    def draw(self, active, center, labels):
        c,p=self.canvas,self.app.palette;c.delete('all')
        w,h=c.winfo_width(),c.winfo_height();cx,cy=w/2,h/2
        r=min(w*.29,h*.31);b=min(72,w*.22);bh=42
        c.create_oval(cx-r-8,cy-r-8,cx+r+8,cy+r+8,outline=p['signal'] if 4 in active else p['editor_border'],width=3 if 4 in active else 1)
        c.create_oval(cx-r,cy-r,cx+r,cy+r,outline=p['editor_border'])
        c.create_text(cx,cy-26,text='T. M.',font=(self.app.fonts.ui,19,'bold'),fill=p['signal'])
        c.create_text(cx,cy+20,text=center,font=(self.app.fonts.ui,10),fill=p['text'],justify='center')
        for index,(x,y) in enumerate(((12,16),(w-b-12,16),(12,h-bh-16),(w-b-12,h-bh-16))):
            color=p['signal'] if index in active else p['muted']
            c.create_rectangle(x,y,x+b,y+bh,outline=color,width=2 if index in active else 1)
            c.create_text(x+b/2,y+bh/2,text=labels[index],fill=color,font=(self.app.fonts.ui,10,'bold'))

    def advance(self):
        if self.page == len(PAGES)-1:self.close()
        else:self.show(self.page+1)

    def refresh_theme(self):
        self.panel.configure(bg=self.app.palette['bg']);self.show(self.page)

    def settings_closed(self): pass

    def close(self, remember=True):
        if not self.opened:return
        self.hide_preview()
        self.opened=False
        if self.panel.grab_current() == self.panel:self.panel.grab_release()
        self.panel.destroy()
        if remember:
            try:self.app.save_ui_preferences(coloplay_guide_version=GUIDE_VERSION)
            except OSError:self.app.status_var.set('教程已关闭，下次可能再次显示。')
        if not self.app.closing:
            self.app.window_controller.sync_hotkeys();self.app.text.focus_set()
