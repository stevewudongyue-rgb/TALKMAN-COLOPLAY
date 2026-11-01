"""编辑焦点与实体旋钮选区；不生成系统按键、不改写正文。"""
from bisect import bisect_left, bisect_right
import regex


class EditorKnobFocus:
    def __init__(self, controller):
        self.controller, self.app = controller, controller.app
        self.active = False
        self.selecting = False
        self.generation = 0
        self.anchor = self.head = None
        self.last = None
        self.document = None
        self.boundaries = [0]
        self.count_cache = ('', 0)
        self.tag = 'TalkManEditorFocus:' + str(id(self))
        # 在按钮和 Canvas 的 break 绑定之前观察点击，不抢走原有动作。
        self.app.bind_class(self.tag, '<ButtonPress-1>', self.pointer)
        self.attach(self.app)
        self.app.text.bind('<KeyRelease-Tab>', lambda _e: self.enter(), add=True)

    def attach(self, widget):
        if widget.winfo_toplevel() != self.app:
            return
        widget.bindtags((self.tag,) + widget.bindtags())
        for child in widget.winfo_children():
            self.attach(child)

    def pointer(self, event):
        text = self.app.text
        if event.widget == text or event.widget == self.app.empty_hint or event.widget.master == text:
            self.enter(reset=True)
        else:
            self.leave()

    def enter(self, reset=False):
        if not self.app.editor_navigation_allowed():
            return
        if not self.active or reset:
            self.active = True
            self.selecting = False
            self.invalidate()
            self.refresh()

    def leave(self):
        if self.active:
            self.active = False
            self.selecting = False
            self.invalidate()
            self.refresh()

    def invalidate(self, *, cancel_navigation=True):
        self.generation += 1
        self.anchor = self.head = self.last = None
        c = self.controller
        # 结束／取消录音绑定的是录音会话，正文的实时转写不应吞掉它。
        pending = c.intent.pending
        preserve = (pending and (pending[3] in ('stop', 'send') or (pending[3] == 'cancel' and self.app.busy))
                    and pending[2] == c.action_stamp(pending[3]))
        if not preserve:
            c.intent.cancel()
        if cancel_navigation:
            c.cancel_navigation()
            c.highlight.hide()
        if c.local_session:
            lease = c.local_session.lease
            if not (lease.pending and lease.pending[3] == 'send'):
                lease.cancel()
            c.local_session.turns = 0

    def signature(self):
        a = self.app
        return (a.revision, tuple(str(x) for x in a.text.tag_ranges('sel')), a.text.index('insert'))

    def sync(self):
        if self.active:
            a = self.app
            focus = a.focus_displayof()
            # 模拟器是实体控件的替身，点击它不会把编辑模式切回目标选择。
            sim = self.controller.simulator
            simulated = bool(sim and not sim.closed and focus and focus.winfo_toplevel() == sim.win)
            editor = focus == a.text or bool(focus and focus.master == a.text)
            if not a.winfo_viewable() or self.controller.paused() or not (editor or simulated):
                self.leave()
        signature = self.signature()
        if self.last is not None and signature != self.last:
            self.selecting = False
            # 正文变动只废弃旧编辑预约；录音转写时仍可预选和切换目标窗口。
            self.invalidate(cancel_navigation=False)
        self.last = signature
        return (self.active, self.generation, self.selecting)

    def toggle_selection(self):
        self.sync()
        if not self.app.editor_navigation_allowed(): return
        selecting = not self.selecting
        if selecting:
            self.controller.hidden = False
            self.app.window_controller.begin_device_edit()
            self.app.window_controller.reveal(focus=True)
            self.app.text.focus_set()
            self.active = True
        self.invalidate()
        self.selecting = selecting
        self.last = self.signature()
        self.refresh()

    def move(self, direction):
        self.sync()
        if self.selecting: return self.select(direction)
        a,text=self.app,self.app.text
        if not a.editor_navigation_allowed() or a._editor_selecting: return False
        document=a.current_text()
        if self.document != document:
            self.document = document
            self.boundaries = [0]+[m.end() for m in regex.finditer(r'\X',document)]
        bounds = self.boundaries
        caret=len(text.get('1.0','insert'))
        selected=a.get_selection()
        if selected:
            caret=len(text.get('1.0',selected[1] if direction>0 else selected[0]))
        else:
            pos=(bisect_right(bounds,caret)+direction-1 if direction>0
                 else bisect_left(bounds,caret)+direction)
            caret=bounds[max(0,min(len(bounds)-1,pos))]
        a.cancel_auto_timer();a.disarm_delivery();a.magnifier.hide()
        text.tag_remove('sel','1.0','end');text.tag_remove('operation','1.0','end')
        text.mark_set('insert',f'1.0 + {caret} chars');text.see('insert')
        self.anchor=self.head=None;self.last=self.signature();a.update_meta()
        return True

    def refresh(self):
        a = self.app
        if a.closing:
            return
        color = a.palette['signal'] if self.active else a.palette['editor_border']
        a.text.configure(highlightbackground=color, highlightcolor=color)
        a.selection_label.configure(foreground=a.palette['signal'] if self.active else a.palette['muted'])
        a.update_meta()

    def selection_count(self):
        selected = self.app.get_selection()
        value = selected[2] if selected else ''
        if self.count_cache[0] != value:
            self.count_cache = (value, len(regex.findall(r'\X',value)))
        return self.count_cache[1]

    def select(self, direction):
        self.sync()
        a, text = self.app, self.app.text
        if not self.active or not a.editor_navigation_allowed() or a._editor_selecting:
            return False
        document = a.current_text()
        if self.document != document:
            self.document = document
            self.boundaries = [0] + [match.end() for match in regex.finditer(r'\X', document)]
        bounds = self.boundaries
        offset = lambda index: len(text.get('1.0', index))
        if self.anchor is None:
            caret = offset('insert')
            selected = a.get_selection()
            if selected:
                start, end = offset(selected[0]), offset(selected[1])
                if caret <= start:
                    self.anchor, self.head = bounds[bisect_left(bounds, end)], bounds[max(0,bisect_right(bounds,start)-1)]
                else:
                    self.anchor, self.head = bounds[max(0,bisect_right(bounds,start)-1)], bounds[bisect_left(bounds,end)]
            else:
                index = max(0,bisect_right(bounds,caret)-1) if direction > 0 else bisect_left(bounds,caret)
                self.anchor = self.head = bounds[min(index,len(bounds)-1)]
        position = bisect_left(bounds,self.head)
        self.head = bounds[max(0,min(len(bounds)-1,position+direction))]
        start, end = sorted((self.anchor,self.head))
        a.cancel_auto_timer()
        a.disarm_delivery()
        a.magnifier.hide()
        text.tag_remove('sel','1.0','end')
        text.tag_remove('operation','1.0','end')
        text.mark_set('insert',f'1.0 + {self.head} chars')
        if start != end:
            text.tag_add('sel',f'1.0 + {start} chars',f'1.0 + {end} chars')
        text.see('insert')
        # 由旋钮产生的选区变化不增加代次，允许 USB 下一帧之前连续旋转。
        self.last = self.signature()
        a.update_meta()
        return True

    def close(self):
        self.app.unbind_class(self.tag, '<ButtonPress-1>')
