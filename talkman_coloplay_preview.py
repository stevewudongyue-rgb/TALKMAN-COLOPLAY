"""可操作的设备预览；直接复用卡带的 Lua 手势和圆屏绘图。"""
from __future__ import annotations

import math
from pathlib import Path
import sys
import time
import tkinter as tk

from lupa.lua54 import LuaRuntime
from PIL import Image, ImageDraw, ImageFont, ImageTk

from talkman_coloplay_protocol import Intent
from talkman_coloplay_scroll import SCROLL_ACTIONS
from talkman_coloplay_cartridge import CARTRIDGE_BUILD, FILL_HOLD_MS

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'TalkMan-设备卡带'


class DeviceRuntime:
    def __init__(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT/'TalkMan-手势.lua').read_text('utf-8'))
        self.lua.execute((ROOT/'TalkMan-更新组合.lua').read_text('utf-8'))
        self.config = self.lua.table_from(dict(center=4, left=5, right=6, fill_ms=FILL_HOLD_MS, exit_ms=5000))
        self.reset()
        self.image = Image.new('RGB', (240, 240))
        self.draw = ImageDraw.Draw(self.image)
        fonts = ['C:/Windows/Fonts/msyh.ttc', '/System/Library/Fonts/PingFang.ttc',
                 '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
        self.font = next((ImageFont.truetype(p, 16) for p in fonts if Path(p).exists()), ImageFont.load_default())
        self.font_path=next((p for p in fonts if Path(p).exists()),None)
        bold_path='C:/Windows/Fonts/msyhbd.ttc'
        self.bold_path=bold_path if Path(bold_path).exists() else self.font_path
        self.fonts={}
        self.text_layout=[]
        g = self.lua.globals()
        g.getcolor = lambda r, green, b: int(r)*65536+int(green)*256+int(b)
        color = lambda c: ((int(c)>>16)&255, (int(c)>>8)&255, int(c)&255)
        def line(x1,y1,x2,y2,c,width=1):
            self.draw.line((x1,y1,x2,y2), fill=color(c), width=max(1,round(width)))
        def rect(x1,y1,x2,y2,c,fill=False):
            self.draw.rectangle((x1,y1,x2,y2), **{'fill' if fill else 'outline':color(c)})
        def circle(x,y,r,c,fill=False):
            self.draw.ellipse((x-r,y-r,x+r,y+r), **{'fill' if fill else 'outline':color(c)})
        g.cls = lambda c: self.draw.rectangle((0,0,240,240), fill=color(c))
        g.line = line
        g.wline = lambda w,a,b,c,d,col: line(a,b,c,d,col,w)
        g.rect = rect
        g.rectfill = lambda *a: rect(*a,fill=True)
        g.circ = circle
        g.circfill = lambda *a: circle(*a,fill=True)
        def font(size,bold=False):
            key=(int(size),bool(bold))
            if key not in self.fonts:
                path=self.bold_path if bold else self.font_path
                self.fonts[key]=ImageFont.truetype(path,int(size)) if path else ImageFont.load_default(size=int(size))
            return self.fonts[key]
        def centered(value,cx,y,maxw,c,size,bold):
            value=''.join(ch for ch in str(value) if ch.isprintable())
            face=font(size,bold)
            # 宽字符使用完整字形，不再按英文 8 像素逐字堆叠。
            while self.draw.textlength(value,font=face)>maxw and size>12:
                size-=1;face=font(size,bold)
            if self.draw.textlength(value,font=face)>maxw:
                while value and self.draw.textlength(value+'…',font=face)>maxw:value=value[:-1]
                value+='…'
            width=self.draw.textlength(value,font=face)
            x=round(cx-width/2)
            self.draw.text((x,y),value,font=face,fill=color(c),anchor='lt')
            self.text_layout.append(dict(text=value,x=x,y=y,width=width,size=size))
        g.TMText=centered
        g.print = lambda s,x,y,c,opt=0,size=16,*_: self.draw.text((x,y),str(s),font=font(size),fill=color(c),anchor='lt')
        self.lua.execute((ROOT/'TalkMan-屏幕.lua').read_text('utf-8'))

    def reset(self):
        self.gesture = self.lua.globals().TMUpdateControls(self.config)
        self.mask = 0

    def step(self, mask, now, context=None):
        self.mask = mask
        buttons = self.lua.table_from({i:bool(mask & (1<<i)) for i in range(8)})
        return list(self.gesture.step(self.gesture, buttons, now, context).values())

    def render(self, state, now, pending=False, armed=False):
        self.text_layout=[]
        g = self.gesture
        m = dict(state, now=now, configured=True, buttons={i:bool(self.mask & (1<<i)) for i in range(8)},
                 update_hold=g.started,
                 modifier=bool(self.mask & 8), ul=g.ul, exit_ms=5000, pending=pending,
                 armed=armed, hold_short=g.knob is not None and now-g.knob<self.config.fill_ms,
                 fill_progress=min(1,max(0,(now-g.knob)/self.config.fill_ms)) if g.knob is not None and pending else None,
                 fill_cancelled=g.knob is not None and g.cancelled,
                 send_current=g.knob is not None and g.send_combo and not g.cancelled,
                 scroll_axis=('横向' if self.mask & 16 else '纵向') if self.mask & 8 else '', inactive=bool(g.exited))
        self.lua.globals().TMDraw(self.lua.table_from(m,recursive=True))
        return self.image.copy()


class LocalDeviceSession:
    """本机模拟器或经过验证的 USB 会话。网络/HID 仍使用原预约入口。"""
    def __init__(self, controller, source='simulator', clock=time.monotonic):
        self.controller, self.source, self.clock = controller, source, clock
        self.device_build = CARTRIDGE_BUILD if source == 'simulator' else None
        self.runtime = DeviceRuntime()
        self.lease = Intent(clock)
        self.enabled = True
        self.turns = 0
        self.turn_context = None
        self.now = self.clock()*1000
        self.runtime.step(0,self.now)

    def reset(self, *, cancel_navigation=True, cancel_send=False):
        self.lease.cancel(); self.turns=0; self.runtime.reset()
        if cancel_send:self.controller.cancel_device_send()
        self.controller.scroll.cancel()
        if cancel_navigation:self.controller.cancel_navigation()

    def feed(self, mask, now=None):
        self.now = self.clock()*1000 if now is None else now
        c = self.controller
        if not self.enabled or c.paused():
            self.reset()
            return
        previous = self.runtime.mask
        if mask & 8 and not previous & 8:
            self.lease.cancel(); self.turns=0; c.cancel_navigation(); c.scroll.cancel()
        for action in self.runtime.step(mask,self.now,c.gesture_context()):
            if action == 'update_hold':
                self.lease.cancel();self.turns=0;c.cancel_navigation();c.cancel_device_send()
            elif action == 'cartridge_update':
                self.update_feedback=('模拟器不安装卡带' if self.source=='simulator' else 'USB请用官方程序导入卡带')
                self.update_until=self.now+5000
                c.local_notice(self.update_feedback)
            elif action == 'exit':
                self.enabled=False; self.lease.cancel(); self.turns=0
                c.set_active(False)
            elif action in ('arm_knob', 'arm_ai', 'arm_send_current'):
                self.turns=0
                if action != 'arm_ai' and (c.target_due or c.target_scanning):
                    c.cancel_navigation();self.lease.cancel()
                    c.local_notice('预选已取消，请松开右下选定目标后再操作')
                    continue
                key=action.removeprefix('arm_')
                semantic=c.semantic(key)
                self.lease.cancel()
                if semantic: self.lease.arm('local',key,c.action_stamp(semantic),semantic)
            elif action == 'cancel_arm':
                self.lease.cancel(); self.turns=0
            elif action in ('fill','knob','ai','send_current'):
                reserved=self.lease.pending[3] if self.lease.pending else None
                semantic=self.lease.consume('knob' if action=='fill' else action,c.action_stamp(reserved))
                if action=='fill': semantic='fill' if semantic=='select' and c.semantic('fill') else None
                if semantic=='send' and not c.device_send_pending():semantic=None
                if semantic: c.execute(semantic)
                else: c.local_notice('操作已取消或不可用')
            elif action in ('previous','next'):
                if c.device_send_pending():continue
                context=c.editor_focus.sync()
                if self.turn_context!=context:self.turns=0
                self.turn_context=context
                self.turns=max(-8,min(8,self.turns+(1 if action=='next' else -1)))
            else:
                if action in SCROLL_ACTIONS: self.lease.cancel(); self.turns=0
                if action in ('cancel_delete','clear'): self.lease.cancel(); self.turns=0
                self.perform(action)
        self.drain_turns()

    def perform(self, action):
        if not self.enabled or self.controller.paused(): return
        semantic=self.controller.semantic(action)
        if semantic: self.controller.execute(semantic)
        else: self.controller.local_notice('此时不能执行')

    def drain_turns(self):
        if self.turn_context != self.controller.editor_focus.sync():
            self.turns=0
        if self.enabled and self.turns and not self.controller.paused():
            direction=1 if self.turns>0 else -1
            steps=abs(self.turns);self.turns=0
            semantic=self.controller.semantic('next' if direction>0 else 'previous')
            if semantic:self.controller.execute(semantic,steps=steps)

    def display_state(self):
        state=self.controller.snapshot()
        state['ready']=self.enabled
        if self.now < getattr(self,'update_until',0):
            state.update(update_phase='error',update_message=self.update_feedback)
        valid=bool(self.lease.id()) and self.lease.pending[2]==self.controller.action_stamp(self.lease.pending[3])
        if not valid: self.lease.cancel()
        state['pending']=bool(valid and self.lease.pending[3]=='select')
        state['armed']=bool(valid)
        return state

    def render(self):
        state=self.display_state()
        return self.runtime.render(state,self.now,state['pending'],state['armed'])


class ColoPlaySimulator:
    def __init__(self, controller):
        self.controller=controller
        self.session=LocalDeviceSession(controller)
        controller.local_session=self.session
        self.win=tk.Toplevel(controller.app,takefocus=False)
        self.win.title('TALKMAN COLOPLAY · 设备模拟器')
        self.win.configure(bg='#080d0b')
        self.win.resizable(False,False)
        self.win.attributes('-topmost',True)
        self.canvas=tk.Canvas(self.win,width=530,height=630,bg='#080d0b',highlightthickness=0,takefocus=False)
        self.canvas.pack()
        self.mask=0;self.hit=None;self.angle=None;self.accum=0;self.shift=False
        self.closed=False;self.job=None;self.photo=None;self.rotation=0
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.drag)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-4>',lambda e:self.rotate(-1))
        self.canvas.bind('<Button-5>',lambda e:self.rotate(1))
        self.win.bind('<FocusOut>',lambda e:self.win.after_idle(self.check_focus))
        self.win.bind('<KeyRelease-Shift_L>', self.release_shift)
        self.win.bind('<KeyRelease-Shift_R>', self.release_shift)
        self.win.bind('<Escape>',lambda e:self.abort())
        self.win.bind('<KeyPress-u>',lambda e:self.update_chord(True))
        self.win.bind('<KeyRelease-u>',lambda e:self.update_chord(False))
        for key, direction, horizontal in [('Up',-1,False), ('Down',1,False), ('Left',-1,True), ('Right',1,True)]:
            self.win.bind('<Shift-'+key+'>', lambda e,d=direction,h=horizontal:self.scroll_key(d,h))
        self.win.protocol('WM_DELETE_WINDOW',self.close)
        a=controller.app
        x=max(0,min(a.winfo_rootx()+a.winfo_width()+20,self.win.winfo_screenwidth()-550))
        y=max(0,min(70,self.win.winfo_screenheight()-680))
        self.win.geometry(f'+{x}+{y}')
        self.tick()

    def check_focus(self):
        # 转目标会主动激活外部窗口；外圈拖拽可继续，含长按的其它操作失焦即撤销。
        if not self.closed and self.hit!='rim' and self.win.focus_displayof() is None:
            # 自己发起的前台切换也会触发 FocusOut。只撤销按键／填入预约，
            # 不能把正在执行的窗口切换作废，否则成功回执和外框一起丢失。
            self.abort(cancel_navigation=not self.controller.cycling)

    def update_chord(self,held):
        self.mask=14 if held else 0
        self.feed()
        return 'break'

    def feed(self): self.session.feed(self.mask)

    def abort(self, *, cancel_navigation=True):
        self.session.reset(cancel_navigation=cancel_navigation);self.mask=0;self.hit=None;self.angle=None
        self.shift=False
        try:self.canvas.grab_release()
        except tk.TclError:pass

    def press(self,e):
        if not self.session.enabled:
            if 200<e.x<330 and 550<e.y<595:
                self.session.enabled=True;self.session.reset();self.feed()
            return 'break'
        x,y=e.x,e.y
        self.hit=None
        for index,(bx,by) in enumerate([(36,119),(394,119),(36,407),(394,407)]):
            if bx<=x<=bx+100 and by<=y<=by+70: self.hit=index
        radius=math.hypot(x-265,y-290)
        if self.hit is None and radius<126: self.hit=4
        elif self.hit is None and 126<=radius<=166: self.hit='rim'
        if self.hit is None:
            self.controller.editor_focus.leave()
            return 'break'
        self.canvas.grab_set()
        self.angle=math.atan2(y-290,x-265);self.accum=0
        if e.state & 1 and self.hit in (0,1,2,4,'rim') and not self.mask & 8:
            self.mask |= 8;self.feed();self.shift=True
        if isinstance(self.hit,int):
            self.mask |= 1<<self.hit
        self.feed()
        return 'break'

    def drag(self,e):
        if self.hit not in (4,'rim'):return 'break'
        if math.hypot(e.x-265,e.y-290)<45:return 'break'
        angle=math.atan2(e.y-290,e.x-265)
        if self.angle is not None:
            self.accum+=(angle-self.angle+math.pi)%(2*math.pi)-math.pi
        self.angle=angle
        while abs(self.accum)>=math.pi/9:
            direction=1 if self.accum>0 else -1
            self.accum-=direction*math.pi/9
            self.rotate(direction)
        return 'break'

    def release(self,e=None):
        hit=self.hit
        self.hit=None;self.angle=None
        if isinstance(hit,int):
            self.mask &= ~(1<<hit)
            self.feed()
        if self.shift and (e is None or not e.state & 1):
            self.mask &= ~8;self.feed();self.shift=False
        try:self.canvas.grab_release()
        except tk.TclError:pass
        return 'break'

    def rotate(self,direction):
        if not self.session.enabled:return 'break'
        self.rotation+=direction*math.pi/9
        bit=1<<(6 if direction>0 else 5)
        self.session.feed(self.mask|bit)
        self.feed()
        return 'break'

    def wheel(self,e):
        # 一次滚轮刻度与设备一次旋转一致，不产生系统键盘输入。
        temporary=bool(e.state & 1 and not self.mask & 8)
        if temporary:self.mask |=8;self.feed();self.shift=True
        for _ in range(min(8,max(1,abs(e.delta)//120))):self.rotate(-1 if e.delta>0 else 1)
        return 'break'

    def release_shift(self, _event=None):
        if self.shift:
            self.mask &= ~8;self.feed();self.shift=False
        return 'break'

    def scroll_key(self, direction, horizontal=False):
        if self.mask & ~8: return 'break'
        self.mask=8; self.shift=True; self.feed()
        self.rotate(direction)
        return 'break'

    def paint(self):
        c=self.canvas;c.delete('all')
        green='#5cff8b';muted='#7aa48a';white='#c8eed4'
        def text(x,y,s,size=11,color=white,anchor='center'):
            c.create_text(x,y,text=s,font=('Microsoft YaHei UI',size),fill=color,anchor=anchor)
        text(28,27,'TALKMAN COLOPLAY',16,white,'w')
        text(28,60,'设备模拟器 · 操作连接当前正文',10,muted,'w')
        c.create_line(28,83,502,83,fill='#233c2b')
        c.create_oval(95,120,435,460,fill='#101c15',outline='#2c5138',width=2)
        for i in range(60):
            a=i*math.pi/30+self.rotation
            c.create_line(265+math.cos(a)*151,290+math.sin(a)*151,
                          265+math.cos(a)*161,290+math.sin(a)*161,fill='#41654b' if i%5==0 else '#233c2b')
        # 使用与实机同源的 240×240 圆屏，四角之外可拖拽外圈。
        circle=self.session.render().convert('RGBA')
        mask=Image.new('L',(240,240));ImageDraw.Draw(mask).ellipse((0,0,239,239),fill=255)
        circle.putalpha(mask)
        self.photo=ImageTk.PhotoImage(circle,master=self.win)
        c.create_image(265,290,image=self.photo)
        mx=265+math.sin(self.rotation)*157;my=290-math.cos(self.rotation)*157
        c.create_oval(mx-3,my-3,mx+3,my+3,fill=green,outline='')
        labels=self.controller.snapshot()['labels']
        for index,(x,y,label) in enumerate([(36,119,'显隐' if self.mask&8 else labels['ai']),(394,119,labels['clear'] if self.mask&8 else labels['cancel_delete']),
                    (36,407,'指令' if self.mask&8 else labels['record']),(394,407,'组合中' if self.mask&8 else labels['modifier'])]):
            active=bool(self.mask & (1<<index))
            c.create_rectangle(x,y,x+100,y+70,fill='#1c3827' if active else '#0c1710',outline=green if active else '#39513f',width=2 if active else 1)
            text(x+50,y+27,label,12,green if active else white)
            text(x+50,y+51,['右下组合显隐' if self.mask&8 else '长按 5 秒退出','清空全文' if self.mask&8 else '取消 / 清空','点按开始／结束','按住＋旋转选目标'][index],8,muted)
        phase=self.controller.phase()[0]
        text(265,493,'旋转滚动改写预览' if phase=='review' else '旋转选字 · 点按结束' if self.controller.editor_focus.selecting else '旋转移动光标 · 点按开始选字',11)
        text(265,520,'长按松开：填入 · 5 秒内短按左上发送',10,muted)
        text(265,545,'Shift＋左上：显隐 · 左下：指令 · 右上：清空',10,muted)
        if not self.session.enabled:
            c.create_rectangle(200,553,330,595,fill=green,outline=green)
            text(265,574,'重新进入',11,'#001b08')
        else:
            text(265,581,'Shift＋旋转预选 · 松开 Shift 选定目标',10,green)
        text(265,610,'Shift＋短按旋钮：发送已有内容 · 按住 U：三键更新演示',9,muted)

    def tick(self):
        if self.closed:return
        self.feed();self.paint()
        self.controller.set_active(self.session.enabled)
        self.job=self.win.after(40,self.tick)

    def close(self):
        if self.closed:return
        self.closed=True
        if self.job:self.win.after_cancel(self.job)
        self.abort();self.session.enabled=False
        if self.controller.local_session is self.session:self.controller.local_session=None
        self.controller.simulator=None
        self.controller.set_active(False)
        self.win.destroy()
