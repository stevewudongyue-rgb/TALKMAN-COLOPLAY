"""小屏幕设置视口：超出工作区才滚动，页签及控件仍使用原生 Tk。"""
import sys
import tkinter as tk
from tkinter import ttk


class SettingsViewport(ttk.Frame):
    def __init__(self, parent, window, palette):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                bg=palette['bg'], width=600, height=540)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.vertical = ttk.Scrollbar(self, command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=lambda a,b: self.scrollbar(self.vertical, a,b,1,0,'ns'),
                              xscrollcommand=lambda a,b: self.scrollbar(self.horizontal, a,b,0,1,'ew'))
        self.notebook = ttk.Notebook(self.canvas, style='Settings.TNotebook')
        self.item = self.canvas.create_window(0, 0, window=self.notebook, anchor='nw')
        self._extent = None
        self.canvas.bind('<Configure>', self.layout)
        self.notebook.bind('<Configure>', self.layout, add=True)
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed, add=True)
        window.bind('<MouseWheel>', self.wheel, add=True)
        window.bind('<Button-4>', self.wheel, add=True)
        window.bind('<Button-5>', self.wheel, add=True)
        window.bind('<FocusIn>', self.see_focus, add=True)

    def scrollbar(self, bar, first, last, column, row, sticky):
        bar.set(first, last)
        needed = not (float(first) <= 0 and float(last) >= 1)
        shown = bar.winfo_manager() == 'grid'
        if not needed and shown:
            bar.grid_remove()
        elif needed and not shown:
            bar.grid(row=row, column=column, sticky=sticky)

    def layout(self, _event=None):
        selected = self.notebook.select()
        if selected:
            page = self.notebook.nametowidget(selected)
            # Notebook 默认按最高的所有页定高，会给短页面留下大块空白，
            # 并把关闭/保存推到不必要的滚动区。只使用当前页的内容高度。
            needed = page.winfo_reqheight()
            if int(self.notebook.cget('height') or 0) != needed:
                self.notebook.configure(height=needed)
        width = max(self.canvas.winfo_width(), self.notebook.winfo_reqwidth())
        height = max(self.canvas.winfo_height(), self.notebook.winfo_reqheight())
        # Mac 对重复几何配置也会派发事件；只在范围变化时刷新，避免回调循环。
        if self._extent != (width, height):
            self._extent = (width, height)
            self.canvas.itemconfigure(self.item, width=width, height=height)
            self.canvas.configure(scrollregion=(0, 0, width, height))

    def tab_changed(self, _event=None):
        self.canvas.yview_moveto(0)
        self.layout()

    def wheel(self, event):
        if event.widget.winfo_class() in ('Text', 'Treeview', 'TCombobox'):
            return
        if self.canvas.yview() == (0.0, 1.0):
            return
        number = getattr(event, 'num', None)
        raw = 120 if number == 4 else -120 if number == 5 else int(getattr(event, 'delta', 0))
        if raw:
            step = max(-3, min(3, -raw)) if sys.platform == 'darwin' else -max(1, abs(raw)//120)*(1 if raw>0 else -1)
            self.canvas.yview_scroll(step, 'units')
            return 'break'

    def see_focus(self, event):
        widget = event.widget
        if not str(widget).startswith(str(self.notebook)+'.'):
            return
        # 聚焦可能早于页签切换事件；先按当前页重算，不能沿用上一短页的滚动范围。
        self.layout()
        self.canvas.update_idletasks()
        for axis, start, size, dimension in (('y', widget.winfo_rooty()-self.canvas.winfo_rooty(), widget.winfo_height(), self.canvas.winfo_height()),
                                             ('x', widget.winfo_rootx()-self.canvas.winfo_rootx(), widget.winfo_width(), self.canvas.winfo_width())):
            if start < 0 or start+size > dimension:
                total = self.notebook.winfo_height() if axis == 'y' else self.notebook.winfo_width()
                offset = self.canvas.canvasy(0) if axis == 'y' else self.canvas.canvasx(0)
                getattr(self.canvas, axis+'view_moveto')(max(0, offset+start-16)/max(1,total))
