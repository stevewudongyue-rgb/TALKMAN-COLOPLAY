"""CXUS 状态、配对按键与可选音频；单工作线程拥有 vendor USB 端点。"""
from __future__ import annotations

import json
import queue
import secrets
import struct
import threading
import time

from talkman_coloplay_protocol import config_id
from talkman_coloplay_preview import LocalDeviceSession
from talkman_coloplay_cartridge import valid_build
from talkman_airdata import BUTTONS, STATE, HELLO, Frames, PING, PONG, packet as data_packet
from talkman_usb_audio import AudioManager

VID, PID = 0x303A, 0x4005
DISPLAY_FIELDS = (
    'ready', 'phase', 'title', 'device_build', 'input_selecting', 'input_index',
    'input_count', 'input_error', 'input_loading', 'input_target', 'selecting',
    'send_seconds', 'editor_focused', 'selection_count', 'target', 'cycling',
    'target_view', 'target_waiting', 'target_scanning', 'target_progress',
    'target_choice', 'target_index', 'target_count', 'target_error',
    'target_page_names', 'target_page_start', 'target_page', 'target_pages',
    'can_fill', 'pending', 'armed', 'update_phase', 'update_message',
)


def state_packet(snapshot, frame_id, config):
    state = {key: snapshot[key] for key in DISPLAY_FIELDS if key in snapshot}
    state = {key: value[:120] if isinstance(value, str) else value
             for key, value in state.items()}
    if 'target_page_names' in state:
        state['target_page_names'] = [str(name)[:40] for name in state['target_page_names'][:4]]
    state['ready'] = bool(state.get('ready'))
    message = dict(v=1, token=config['token'], config=config_id(config),
                   frame=frame_id & 65535, state=state)
    payload = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > 4096:
        raise ValueError('圆屏状态超过设备消息上限')
    return data_packet(STATE, payload, seq=frame_id)


def hello_packet(config):
    # 不包含正文或操作；卡带只对本机配对的探测回复零键包。
    message = dict(protocol='TM_USB_HELLO_1', v=1, token=config['token'], config=config_id(config))
    return data_packet(HELLO, json.dumps(message, separators=(',', ':')).encode('utf-8'))


def button_message(frame):
    if (frame.kind != BUTTONS or frame.priority != 1 or frame.tag or frame.sub
            or len(frame.data) > 512):return None
    try:message=json.loads(frame.data.decode('utf-8'))
    except (UnicodeError,ValueError):return None
    return message if isinstance(message,dict) else None


def select_device(devices):
    if len(devices)!=1:raise ValueError('请只连接一台 COLOPLAY USB 设备')
    return devices[0]


def recording_navigation_matches(displayed,current):
    """同轮录音可继续选目标；正文/光标更新不改变目标身份及确认阶段。"""
    return bool(len(displayed or ())>=14 and len(current or ())>=14 and
                displayed[4] in ('opening','recording') and current[4] in ('opening','recording') and
                displayed[2:4]+displayed[5:6]+displayed[9:11]+displayed[12:] ==
                current[2:4]+current[5:6]+current[9:11]+current[12:])


def display_matches(displayed,current,mask):
    if displayed==current:return True
    # 位置选择中只允许连续转动使用上一帧；确认必须对应当前编号。
    if (mask in (0,32,64,96) and len(displayed or ())>=14 and len(current or ())>=14
            and current[4]=='input_select' and displayed[:13]==current[:13]
            and displayed[13][:2]+displayed[13][3:]==current[13][:2]+current[13][3:]):return True
    # 填入导致目标激活／编辑失焦，不应吞掉发送确认或明确的新一轮录入。
    # 仅放宽焦点与光标；令牌、正文、目标、会话和阶段仍须完全一致。
    if (mask in (0,1,2,4,8,12) and len(displayed or ())>=13 and len(current or ())>=13
            and current[12] and displayed[12] == current[12]
            and displayed[:6]+displayed[8:11] == current[:6]+current[8:11]):return True
    # 外圈连续预选仅改变“待切换”位，不能因前一帧尚未刷新而吞掉后续转动／松手。
    # 任何正文、选区、目标或工作阶段变化仍走原校验；其它按钮不放宽。
    if (mask in (0,8,40,72,104) and len(displayed or ())>=11 and len(current or ())>=11
            and displayed[:8]+displayed[9:]==current[:8]+current[9:]):return True
    if mask in (0,8,40,72,104) and recording_navigation_matches(displayed,current):return True
    # 同一聚焦代次内的旋钮选字，允许上一帧的选区／光标落后。
    # 外部点击、键盘编辑、失焦均更换代次；其它按钮仍须逐字核对。
    if (mask in (0,32,64,96) and len(displayed or ())>=12 and len(current or ())>=12
            and displayed[11]==current[11] and current[4]=='idle'
            and displayed[:6]+displayed[8:]==current[:6]+current[8:]):return True
    if not displayed or displayed[2:4]!=current[2:4]:return False
    # 同一轮实时转写会持续改变正文；结束/取消不要求状态追上每个字。
    # 工作阶段改变时仍拒绝，尤其不能把“取消”解释成空闲 Delete。
    phase=current[4]
    # 三键更新手势会等松开再执行短按，零键/仅右下也是结束录音的一部分。
    if phase in ('opening','recording') and displayed[4] in ('opening','recording') and mask in (0,4,8,12):return True
    if displayed[4]!=phase:return False
    return bool((phase=='recording' and mask&4) or
                (phase in ('recording','opening','processing','permission') and mask&2))


def target_start_matches(displayed, current):
    """开始预选只绑定会话与目标，正文/光标的小屏同步延迟不能吞掉组合键。"""
    return bool(len(displayed or ()) >= 14 and len(current or ()) >= 14
                and current[4] in ('idle', 'opening', 'recording')
                and not current[12]
                and displayed[2:6]+displayed[9:11]+displayed[12:] ==
                    current[2:6]+current[9:11]+current[12:])


class UsbWire:
    def __init__(self,config):
        self.config=config
        self.events=queue.Queue(maxsize=128)
        self.stop=threading.Event()
        self.lock=threading.Lock();self.state=None
        self.frames={};self.frame_stamps={};self.last_seq=None
        self.audio=AudioManager(config)
        self.paired=False;self.v2=False;self.last_pair=0
        self.wait_notice='';self.pair_rejected=False
        self.thread=threading.Thread(target=self.run,name='TalkMan-USB',daemon=True)
        self.thread.start()

    def publish(self,state,stamp=None):
        with self.lock:self.state=(state,stamp) if state is not None else None

    def put(self,kind,value):
        try:self.events.put_nowait((kind,value,time.monotonic()))
        except queue.Full:
            self.stop.set() # 忙碌过久时关闭连接，不积攒可执行操作。

    def validate(self,m,now):
        if (m.get('v')!=1 or m.get('token')!=self.config['token'] or
                m.get('config')!=config_id(self.config)):
            self.pair_rejected=True
            self.waiting('卡带未配对：请安装这台电脑的配对卡带。' if not m.get('token') else
                         '卡带与这台电脑的配对不一致，请安装本机配对卡带。')
            return None
        if any(type(m.get(k)) is not int for k in ('seq','mask','frame')):return None
        if not 0<=m['mask']<=255 or m['seq']<0:return None
        self.paired=True
        self.pair_rejected=False
        self.last_pair=now
        build = valid_build(m.get('device_build'))
        if build != getattr(self, 'device_build', None):
            self.device_build = build
            self.put('device_build', build)
        if m.get('exit') is True:return 'exit'
        if m['frame'] not in self.frames or now-self.frames[m['frame']]>1.5:
            if m['mask']==0:self.last_seq=None
            return 'wake' if m['mask']==0 else None
        if self.last_seq is not None and m['seq']<=self.last_seq:return None
        gap=self.last_seq is None or m['seq']!=self.last_seq+1
        self.last_seq=m['seq']
        return ('reset' if gap else 'buttons',m['mask'])

    def receive_buttons(self,frame,now):
        message=button_message(frame)
        if message is None:return
        event=self.validate(message,now)
        if event in ('wake','exit'):self.put(event,None)
        elif event:self.put(event[0],dict(mask=event[1],stamp=self.frame_stamps.get(message['frame'])))

    def waiting(self,message):
        if message != getattr(self,'wait_notice',''):
            self.wait_notice=message
            self.put('status',message)

    def run(self):
        dev=None;claimed=False
        try:
            import usb.core
            import usb.util
            import libusb_package
            backend=libusb_package.get_libusb1_backend()
            if backend is None:raise RuntimeError('USB 运行库不可用')
            devices=list(usb.core.find(find_all=True,idVendor=VID,idProduct=PID,backend=backend))
            if not devices:raise RuntimeError('未找到 COLOPLAY，请连接 USB 数据线')
            dev=select_device(devices)
            cfg=dev.get_active_configuration()
            interface=next((i for i in cfg if i.bInterfaceNumber==3 and i.bInterfaceClass==255),None)
            if interface is None:raise RuntimeError('此固件未提供 USB 数据接口，请更新固件或使用 Wi-Fi 卡带')
            # 状态协议不需要图流分辨率或设备语言表。Windows 复合设备可能
            # 无法经 WinUSB 读取 LANGID；用 VID/PID、接口和端点识别，再核验 v2。
            endpoints={e.bEndpointAddress:e for e in interface}
            if 3 not in endpoints or endpoints[3].bmAttributes&3!=2:raise RuntimeError('USB 数据端点不匹配')
            if 0x83 not in endpoints or endpoints[0x83].bmAttributes&3!=2:
                raise RuntimeError('此固件未提供 USB 双向数据通道，请更新固件或使用 Wi-Fi 卡带')
            read_size = int(endpoints[0x83].wMaxPacketSize)
            if read_size not in (8, 16, 32, 64, 512, 1024):
                raise RuntimeError('USB 数据端点的单包大小无效')
            usb.util.claim_interface(dev,3);claimed=True
            # 只声明 vendor 数据接口，不打开设备的 CDC 串口或 HID 接口。
            self.put('connected','USB 数据接口已打开，等待 USB 卡带')
            incoming=Frames();fid=secrets.randbelow(65536);sent=0
            self.audio.transport_stats = incoming.stats
            self.audio.transport_crc_frames = incoming.crc_frames
            self.audio.read_packet_bytes = read_size
            ping_at=0;pong_at=0;hello_at=-float('inf');ping_id=secrets.randbelow(65536)
            opened_at=time.monotonic()
            while not self.stop.is_set():
                now=time.monotonic()
                self.audio.available=self.v2 and self.paired and now-self.last_pair<1.5
                self.audio.tick(now)
                for _ in range(16):
                    try:command=self.audio.commands.get_nowait()
                    except queue.Empty:break
                    if dev.write(3,command,timeout=300)!=len(command):raise RuntimeError('USB 控制消息未完整写入')
                if now-ping_at>=1:
                    ping_id=(ping_id+1)&65535
                    ping=data_packet(PING,seq=ping_id)
                    if dev.write(3,ping,timeout=300)!=len(ping):raise RuntimeError('USB 状态检测发送失败')
                    ping_at=now
                if self.v2 and now-pong_at>3:raise RuntimeError('USB 设备未响应')
                # Full-speed COLOPLAY uses 64-byte bulk packets. Request one
                # endpoint packet so a full packet completes the native read;
                # do not wait for 512 bytes/a short packet and then cancel a
                # partially filled multi-packet read on the 10ms timeout.
                # CXUS frames still span multiple reads and require CRC checks.
                try:chunk=bytes(dev.read(0x83,read_size,timeout=10))
                except usb.core.USBTimeoutError:chunk=b''
                for frame in incoming.feed(chunk):
                    self.audio.observe_frame(frame)
                    if frame.kind==PONG and len(frame.data)==38:
                        version,echo=struct.unpack_from('<HH',frame.data)
                        if version==2 and echo==ping_id:self.v2=True;pong_at=time.monotonic()
                    elif frame.kind==BUTTONS and self.v2:self.receive_buttons(frame,time.monotonic())
                    else:self.audio.receive(frame)
                paired_live=self.paired and now-self.last_pair<1.5
                if self.v2 and not paired_live and now-hello_at>=1:
                    hello=hello_packet(self.config)
                    if dev.write(3,hello,timeout=300)!=len(hello):raise RuntimeError('USB 配对探测未完整写入')
                    hello_at=now
                if now-opened_at>5 and not self.paired and not self.pair_rejected:
                    self.waiting('USB 已响应，但未收到卡带配对；请打开本机配对卡带。' if self.v2 else
                                 'USB 接口已打开，但双向数据未响应；请检查设备固件。')
                with self.lock:published=self.state
                if self.v2 and paired_live and published is not None and now-sent>=(.35 if self.audio.stream else .25):
                    state,stamp=published
                    packet=state_packet(state,fid,self.config)
                    if dev.write(3,packet,timeout=500)!=len(packet):raise RuntimeError('USB 状态未完整写入')
                    self.frames[fid]=time.monotonic()
                    self.frame_stamps[fid]=stamp
                    self.frames={k:v for k,v in self.frames.items() if now-v<2}
                    self.frame_stamps={k:v for k,v in self.frame_stamps.items() if k in self.frames}
                    fid=(fid+1)&65535;sent=now
        except Exception as error:self.put('error','USB 未就绪：'+str(error))
        finally:
            self.audio.disconnect()
            if dev is not None:
                import usb.util
                if claimed:
                    try:usb.util.release_interface(dev,3)
                    except Exception:pass
                usb.util.dispose_resources(dev)
            self.put('closed',None)

    def close(self):
        self.audio.disconnect()
        self.stop.set();self.thread.join(timeout=1.5)


class UsbBridge:
    def __init__(self,controller, *, quiet=False):
        self.controller=controller
        self.quiet=quiet
        self.session=LocalDeviceSession(controller,source='usb')
        self.session.enabled=False
        self.last_input=0;self.streaming=False;self.disconnected=False
        self.closed=False;self.exited=False
        self.target_stamps=[];self.target_after=None
        self.wire=UsbWire(controller.config)
        # Wait for a paired cartridge wake message before sending any state.
        self.wire.publish(None)
        if not quiet:controller.local_notice('正在检查 USB 接口…')

    def poll(self):
        if self.closed:return
        now=time.monotonic()
        for _ in range(128):
            try:kind,value,arrived=self.wire.events.get_nowait()
            except queue.Empty:break
            if kind in ('error','connected','status'):
                self.controller.usb_status.set(value)
                if not self.quiet or self.session.enabled:self.controller.local_notice(value)
            if kind=='closed':
                self.disconnected=True
                self.session.device_build=None
                self.session.enabled=False
                if self.controller.local_session is self.session:
                    self.session.reset();self.controller.local_session=None
                self.streaming=False
            elif kind=='device_build':
                self.session.device_build=valid_build(value)
            elif kind=='exit':
                self.wire.audio.disconnect()
                self.exited=True;self.session.enabled=False
                if self.controller.local_session is self.session:
                    self.session.reset();self.controller.local_session=None
                self.streaming=False
            elif kind=='wake':
                self.controller.usb_status.set('USB 已配对，正在同步状态和按键。')
                # 零按键报文仅启动状态同步，不执行任何业务动作。
                self.streaming=True;self.exited=False
                if self.controller.local_session is not self.session:
                    self.controller.intent.cancel()
                self.controller.local_session=self.session
                self.session.enabled=True;self.last_input=now
                self.session.feed(0)
            elif kind in ('buttons','reset'):
                if now-arrived>.35 or self.exited:
                    if self.controller.local_session is self.session:self.session.reset()
                    self.target_stamps=[];self.target_after=None
                    continue
                mask=value['mask'];displayed=value['stamp'];current=self.controller.stamp()
                # 预选会主动离开编辑焦点。仅允许同一右下按住手势继续使用此前
                # 已验证的屏幕帧；外部编辑、换目标、换阶段或断包仍使它失效。
                preview_current=(current==self.target_after or
                                 recording_navigation_matches(self.target_after,current))
                target_changed=(self.target_after is not None and self.session.runtime.gesture.target_turned and
                                not preview_current)
                own_preview=(kind=='buttons' and self.session.enabled and
                             self.session.runtime.mask&8 and mask in (0,8,40,72,104) and
                             preview_current and displayed in self.target_stamps)
                neutral=(mask==0 and self.session.runtime.mask==0)
                start_preview=(mask in (8,40,72,104) and self.target_after is None and
                               not self.session.runtime.gesture.target_turned and
                               self.session.runtime.gesture.knob is None and
                               target_start_matches(displayed,current))
                if target_changed or not (neutral or start_preview or own_preview or display_matches(displayed,current,mask)):
                    if self.controller.local_session is self.session:
                        self.session.reset()
                        # 同步被拒绝的物理状态，不执行动作。零键帧立即重新就绪，
                        # 不让下一次正常点按被误当作“启动时仍按住”。
                        self.session.runtime.step(mask,now*1000)
                        self.last_input=now
                    self.target_stamps=[];self.target_after=None
                    continue
                if kind=='reset' or not self.session.enabled:
                    self.session.reset()
                    self.target_stamps=[];self.target_after=None
                if self.controller.local_session is not self.session:
                    self.controller.intent.cancel()
                self.controller.local_session=self.session
                self.session.enabled=True;self.last_input=now
                self.session.feed(mask)
                if (mask&8 and self.session.runtime.gesture.target_turned and
                        (self.controller.target_due or self.controller.target_scanning)):
                    if not preview_current:self.target_stamps=[]
                    for stamp in (displayed,current):
                        if stamp not in self.target_stamps:self.target_stamps.append(stamp)
                    self.target_stamps=self.target_stamps[-16:]
                    self.target_after=self.controller.stamp()
                else:
                    self.target_stamps=[];self.target_after=None
                if not self.session.enabled:
                    self.exited=True;self.streaming=False
        if self.session.enabled and now-self.last_input>1:
            self.wire.audio.disconnect()
            self.session.enabled=False;self.session.reset()
            self.streaming=False
            if self.controller.local_session is self.session:self.controller.local_session=None
            self.controller.local_notice('USB 按键连接已中断')
        self.session.now=now*1000
        self.session.drain_turns()
        if not self.streaming:
            self.wire.publish(None)
        else:
            self.wire.publish(self.session.display_state(),self.controller.stamp())

    def close(self):
        if self.closed:return
        owned=self.controller.local_session is self.session
        self.closed=True;self.session.enabled=False
        if owned:self.session.reset()
        self.wire.close()
        if self.controller.local_session is self.session:self.controller.local_session=None
        if not self.controller.connected():self.controller.set_active(False)
