"""教程专用动画：只绘制示例，不持有编辑器或设备操作接口。"""
from dataclasses import dataclass
import tkinter as tk
from tkinter import font as tkfont


@dataclass(frozen=True)
class Scene:
    caption: str
    keys: tuple = ()
    text: str = '请把苹果拿过来。'
    selected: tuple = ()
    cursor: int = -1
    view: str = 'editor'
    status: str = '预输入框'
    turn: bool = False


# 六页、每页三条；演示状态与正文逐条对应。样例不来自用户草稿。
SCENES = (
    (
        (Scene('点左上，启动一次 AI 改写。', (0,)),
         Scene('出现前后对照，再点左上确认。', (0,), view='compare', status='确认改写'),
         Scene('长按左上 5 秒，退出设备模式。', (0,), view='window', status='设备模式已退出')),
        (Scene('点左下，开始说话。', (2,), text='', status='正在录音'),
         Scene('说的话出现在预输入框。', text='明天下午', status='正在录音'),
         Scene('再点左下，结束这次录音。', (2,), text='明天下午三点开会。', cursor=9, status='录入完成')),
        (Scene('空闲时，右上删除光标前的一个字。', (1,), text='苹果', cursor=2),
         Scene('有选区时，右上删除整段选区。', (1,), text='请拿苹果', selected=(2, 4)),
         Scene('录音中右上取消；右下用于组合操作。', (1,), text='请拿', status='已取消录音')),
    ),
    (
        (Scene('没有选区：在光标处接着写。', text='明天开会。', cursor=2),
         Scene('说“下午三点”，插入到光标位置。', (2,), text='明天下午三点开会。', cursor=6),
         Scene('选中“苹果”后说“梨”，只替换选区。', (2,), selected=(2, 4)),
         Scene('选区以外的文字保留。', text='请把梨拿过来。', cursor=3)),
        (Scene('点左下，正在准备麦克风。', (2,), text='', status='准备录音'),
         Scene('准备期间再点左下，也会结束本轮。', (2,), text='', status='录音结束'),
         Scene('不想保留这一轮时，点右上取消。', (1,), status='恢复录音前正文')),
        (Scene('窗口贴边收起时，仍可点左下录入。', (2,), view='window', status='贴边收起'),
         Scene('开始录入，TalkMan 自动展开。', view='window', status='展开编辑'),
         Scene('录完保持展开，方便继续检查。', text='明天下午三点开会。', status='录入完成')),
    ),
    (
        (Scene('光标先停在“苹”字前。', cursor=2),
         Scene('顺向旋转，向后移动一个完整字符。', (4,), cursor=3, turn=True),
         Scene('反向旋转，光标移回前一个位置。', (4,), cursor=2, turn=True)),
        (Scene('点一下旋钮，固定选区起点。', (4,), cursor=2),
         Scene('向后旋转，选中“苹果”。', (4,), selected=(2, 4), turn=True),
         Scene('反向旋转，缩小当前选区。', (4,), selected=(2, 3), turn=True)),
        (Scene('选好范围后，再点一次旋钮。', (4,), selected=(2, 4)),
         Scene('结束选框，已选文字仍保留。', selected=(2, 4)),
         Scene('用鼠标点另一位置，重新定位光标。', cursor=7)),
    ),
    (
        (Scene('先选中要修改的“苹果”。', selected=(2, 4)),
         Scene('按住右下，再按左下说“把苹果改成梨”。', (2, 3), selected=(2, 4), status='录入修改指令'),
         Scene('同一组合结束录音，检查改写对照。', (2, 3), view='compare', status='改写对照')),
        (Scene('点左上，AI 整理一次。', (0,)),
         Scene('出现对照，先检查改写内容。', view='compare', status='改写对照'),
         Scene('此时转旋钮，上下阅读对照内容。', (4,), view='compare', status='向下阅读', turn=True)),
        (Scene('对照中：左上确认，右上放弃。', (0, 1), view='compare', status='检查后选择'),
         Scene('回到空闲后，右下＋右上清空全文。', (1, 3), text='', status='正文已清空'),
         Scene('点桌面“上一步”，可以恢复全文。', status='已恢复正文')),
    ),
    (
        (Scene('按住右下，进入目标预选。', (3,), view='target', status='豆包'),
         Scene('继续按住并旋转，候选变为 Codex。', (3, 4), view='target', status='Codex', turn=True),
         Scene('继续转到下一页，可以选择更多目标。', (3, 4), view='target', status='千问', turn=True)),
        (Scene('按住右下时，只预览候选目标。', (3,), view='target', status='豆包'),
         Scene('松开右下，才选定最后高亮的窗口。', view='target', status='已选定豆包'),
         Scene('Windows 在目标窗口周围显示绿色外框。', view='target', status='目标已高亮')),
        (Scene('先按住右下，再按左上。', (0, 3), view='window', status='展开编辑'),
         Scene('TalkMan 收起，露出目标窗口。', view='window', status='贴边收起'),
         Scene('再按同一组合，呼出 TalkMan。', (0, 3), view='window', status='展开编辑')),
    ),
    (
        (Scene('长按旋钮至少 0.8 秒。', (4,), view='target', status='长按进度'),
         Scene('松开旋钮：只填入，尚未发送。', view='target', status='已填入 · 5 秒'),
         Scene('检查好后，短按左上才发送。', (0,), view='target', status='已发送')),
        (Scene('填入成功后，进入 5 秒确认期。', view='target', status='已填入 · 5 秒'),
         Scene('点左下可立即开始新录入，不必等倒计时。', (2,), text='', status='新一轮录入'),
         Scene('右下＋左下开始新指令，上次发送确认取消。', (2, 3), status='新一轮指令'),
         Scene('不再操作则到期，原文字留在目标框。', view='target', status='已超时 · 未发送')),
        (Scene('平时先按住右下，再短按旋钮。', (3, 4), view='target', status='目标已有文字'),
         Scene('按住旋钮时转动，会取消本次发送。', (3, 4), view='target', status='发送已取消', turn=True),
         Scene('重新按组合并松开旋钮，才执行发送。', (3,), view='target', status='已发送'),
         Scene('发送成功，回到原来的贴边收起状态。', view='window', status='贴边收起')),
    ),
)


class GuidePreview(tk.Frame):
    """嵌在左侧示意图内的预览层，不创建窗口、不抢键盘焦点。"""
    def __init__(self, parent, palette, family, scenes, number, dismiss):
        super().__init__(parent, bg=palette['input'], highlightthickness=1,
                         highlightbackground=palette['signal'])
        self.palette, self.family, self.scenes = palette, family, scenes
        self.index, self.job, self.paused, self.closed = 0, None, False, False
        self.place(x=0, y=0, relwidth=1, relheight=1)
        header = tk.Frame(self, bg=palette['input'])
        header.pack(fill='x', padx=12, pady=(8, 0))
        tk.Label(header, text=f'{number:02d} / 操作演示', bg=palette['input'], fg=palette['signal'],
                 font=(family, 10, 'bold')).pack(side='left')
        self.counter = tk.Label(header, bg=palette['input'], fg=palette['muted'], font=(family, 10))
        self.counter.pack(side='right')
        controls = tk.Frame(self, bg=palette['input'])
        controls.pack(side='bottom', fill='x', padx=8, pady=(0, 6))
        self.pause_button = self._button(controls, '暂停', self.toggle)
        self.pause_button.pack(side='left')
        self._button(controls, '重播', self.replay).pack(side='left', padx=4)
        self._button(controls, '收起', dismiss).pack(side='right')
        self.caption = tk.Label(self, bg=palette['input'], fg=palette['text'], justify='left',
                                anchor='w', font=(family, 10))
        self.caption.pack(side='bottom', fill='x', padx=12, pady=(4, 8))
        self.canvas = tk.Canvas(self, bg=palette['input'], highlightthickness=0, width=280, height=150)
        self.canvas.pack(fill='both', expand=True, padx=8, pady=(4, 0))
        self.canvas.bind('<Configure>', lambda _e: self.draw())
        self.bind('<Configure>', lambda e: self.caption.configure(wraplength=max(120, e.width-24))
                  if e.widget == self else None)
        self.bind('<Destroy>', lambda e: self.stop() if e.widget == self else None)
        self.render()

    def _button(self, parent, text, command):
        p = self.palette
        return tk.Button(parent, text=text, command=command, font=(self.family, 10),
                         bg=p['input'], fg=p['text'], activebackground=p['panel'],
                         activeforeground=p['signal'], relief='flat', borderwidth=0,
                         highlightthickness=1, highlightbackground=p['editor_border'],
                         highlightcolor=p['signal'], padx=8, pady=2, cursor='hand2')

    def render(self):
        self.caption.configure(text=self.scenes[self.index].caption)
        self.counter.configure(text=f'{self.index+1} / {len(self.scenes)}')
        self.pause_button.configure(text='继续' if self.paused else '暂停',
                                    state='disabled' if self.index == len(self.scenes)-1 else 'normal')
        self.draw()
        if not self.paused and self.index < len(self.scenes)-1:
            self.job = self.after(1600, self.advance)

    def advance(self):
        self.job = None
        if not self.closed and not self.paused and self.index < len(self.scenes)-1:
            self.index += 1
            self.render()

    def _cancel(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None

    def toggle(self):
        self._cancel()
        self.paused = not self.paused
        self.render()

    def replay(self):
        self._cancel()
        self.index, self.paused = 0, False
        self.render()

    def stop(self):
        self.closed = True
        self._cancel()

    def destroy(self):
        self.stop()
        super().destroy()

    def draw(self):
        c, p, s = self.canvas, self.palette, self.scenes[self.index]
        c.delete('all')
        w, h = max(180, c.winfo_width()), max(80, c.winfo_height())
        # 左侧为真实四键方位，右侧为桌面局部，读者同时看到动作与结果。
        cx, cy, r = 48, h/2, 26
        for i, (x, y) in enumerate(((8, cy-56), (60, cy-56), (8, cy+34), (60, cy+34))):
            color = p['signal'] if i in s.keys else p['muted']
            c.create_rectangle(x, y, x+28, y+22, outline=color, width=2 if i in s.keys else 1)
            c.create_text(x+14, y+11, text=('AI', '退', '录', '组')[i], fill=color, font=(self.family, 9))
        c.create_oval(cx-r, cy-r, cx+r, cy+r, outline=p['signal'] if 4 in s.keys else p['editor_border'], width=2)
        c.create_text(cx, cy, text='T.M.', fill=p['signal'], font=(self.family, 10, 'bold'))
        if s.turn or s.status == '长按进度':
            c.create_arc(cx-r-5, cy-r-5, cx+r+5, cy+r+5, start=40+self.index*50,
                         extent=135 if s.turn else 270, style='arc', width=3, outline=p['signal'])
        x, y, right, bottom = 108, max(8, (h-128)/2), w-4, min(h-4, (h+128)/2)
        c.create_rectangle(x, y, right, bottom, outline=p['signal'] if s.status == '目标已高亮' else p['editor_border'],
                           width=3 if s.status == '目标已高亮' else 1)
        width = max(60, right-x-16)
        font = tkfont.Font(self, family=self.family, size=10)
        c.create_text(x+8, y+12, anchor='w', text=s.status if s.view != 'target' else '目标输入框',
                      fill=p['muted'], font=font, width=width)
        if s.view == 'window':
            c.create_rectangle(x+12, y+32, right-12, bottom-12, outline=p['muted'])
            c.create_text((x+right)/2, y+48, text='Agent', fill=p['muted'], font=font)
            if s.status == '贴边收起':
                c.create_rectangle(x+12, y+36, x+20, bottom-16, fill=p['signal'], outline='')
            elif s.status != '设备模式已退出':
                c.create_rectangle(x+22, y+56, right-20, bottom-8, fill=p['input'], outline=p['signal'])
                c.create_text((x+right)/2, y+76, text='TalkMan 编辑区', fill=p['text'], font=font)
        elif s.view == 'compare':
            c.create_text(x+8, y+38, anchor='nw', text='原文  请把苹果拿过来。', fill=p['muted'], font=font, width=width)
            c.create_text(x+8, y+72, anchor='nw', text='改写  请把梨拿过来。', fill=p['signal'], font=font, width=width)
            c.create_line(right-6, y+36, right-6, bottom-12, fill=p['editor_border'], width=2)
            if s.turn:
                c.create_line(right-6, bottom-40, right-6, bottom-12, fill=p['signal'], width=2)
        elif s.view == 'target':
            # 预选场景显示四角 Agent；填入场景显示目标中的文字与明确状态。
            if s.status in ('豆包', 'Codex', '千问'):
                names = ('豆包', 'Codex', 'Claude', '千问') if s.status != '千问' else ('千问', '浏览器', '', '')
                for name, (xx, yy) in zip(names, ((x+8,y+38),(right-8,y+38),(x+8,bottom-20),(right-8,bottom-20))):
                    c.create_text(xx, yy, anchor='w' if xx == x+8 else 'e', text=name,
                                  fill=p['signal'] if name == s.status else p['muted'], font=font)
                c.create_text((x+right)/2, y+67, text='预选 · 未切窗', fill=p['text'], font=font)
            else:
                c.create_text(x+8, y+36, anchor='nw', text='明天下午三点开会。', fill=p['text'], font=font, width=width)
                c.create_text(x+8, bottom-24, anchor='nw', text=s.status, fill=p['signal'], font=font, width=width)
        else:
            # 按实际字宽折行、绘制选区与插入线，中文位置可直接观察。
            px, py, line = x+8, y+38, font.metrics('linespace')+4
            for index, char in enumerate(s.text):
                advance = font.measure(char)
                if px+advance > right-8:
                    px, py = x+8, py+line
                if s.cursor == index:
                    c.create_line(px, py-1, px, py+line-3, fill=p['signal'], width=2)
                selected = bool(s.selected and s.selected[0] <= index < s.selected[1])
                if selected:
                    c.create_rectangle(px, py-1, px+advance, py+line-3, fill=p['signal'], outline='')
                c.create_text(px, py, anchor='nw', text=char, font=font, fill=p['input'] if selected else p['text'])
                px += advance
            if s.cursor == len(s.text):
                c.create_line(px, py-1, px, py+line-3, fill=p['signal'], width=2)
