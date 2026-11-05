"""官方 CDC 配对按键 + CXUS 图流与录音；单工作线程拥有 USB 端点。"""
from __future__ import annotations

import binascii
import io
import json
import queue
import re
import secrets
import struct
import threading
import time

from talkman_coloplay_protocol import config_id
from talkman_coloplay_preview import LocalDeviceSession
from talkman_coloplay_cartridge import valid_build
from talkman_airdata import Frames, PING, PONG, packet as data_packet
from talkman_usb_audio import AudioManager

VID, PID = 0x303A, 0x4005
HEADER = struct.Struct('<IHHIHHHH')


def jpeg_packet(image, frame_id, limit):
    buf=io.BytesIO();image.convert('RGB').save(buf,'JPEG',quality=85)
    payload=buf.getvalue()
    if len(payload)>limit:raise ValueError('圆屏图像超过设备单帧上限')
    return HEADER.pack(0x53555843,1,1,len(payload),frame_id&65535,
                       image.width,image.height,binascii.crc_hqx(payload,0xffff))+payload


class SerialFrames:
    """CDC 可拆包、粘包及混入固件日志；只接受有界的本协议 JSON 行。"""
    def __init__(self):self.buffer=b'';self.discard=False

    def feed(self,data):
        rows=[]
        for part in data.splitlines(keepends=True):
            if not self.discard:self.buffer+=part
            if len(self.buffer)>2048:self.buffer=b'';self.discard=True
            if part.endswith(b'\n'):
                if not self.discard:
                    start=self.buffer.find(b'TMCP1 ')
                    if start>=0:
                        try:
                            obj=json.loads(self.buffer[start+6:])
                            if isinstance(obj,dict):rows.append(obj)
                        except (ValueError,UnicodeError):pass
                self.buffer=b'';self.discard=False
        return rows


def select_pair(devices,ports):
    # 不把两个不同实体设备的串口、画面端点拼到一起。
    ports=[p for p in ports if (p.vid,p.pid)==(VID,PID)]
    pairs=[]
    for dev in devices:
        serial=dev.serial_number
        for port in ports:
            if serial and port.serial_number and serial==port.serial_number:pairs.append((dev,port))
    if len(pairs)==1:return pairs[0]
    if not pairs and len(devices)==1 and len(ports)==1:
        a,b=devices[0].serial_number,ports[0].serial_number
        if not a or not b:return devices[0],ports[0]
    raise ValueError('请只连接一台 COLOPLAY；未找到唯一匹配的 USB 图流接口与串口')


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
    # 同一聚焦代次内的旋钮选字，允许上一帧的选区／光标落后。
    # 外部点击、键盘编辑、失焦均更换代次；其它按钮仍须逐字核对。
    if (mask in (0,32,64,96) and len(displayed or ())>=12 and len(current or ())>=12
            and displayed[11]==current[11] and current[4]=='idle'
            and displayed[:6]+displayed[8:]==current[:6]+current[8:]):return True
    if not displayed or displayed[2:4]!=current[2:4]:return False
    # 同一轮实时转写会持续改变正文；结束/取消不要求图像追上每个字。
    # 工作阶段改变时仍拒绝，尤其不能把“取消”解释成空闲 Delete。
    phase=current[4]
    if phase in ('opening','recording') and displayed[4] in ('opening','recording') and mask in (4,12):return True
    if displayed[4]!=phase:return False
    return bool((phase=='recording' and mask&4) or
                (phase in ('recording','opening','processing','permission') and mask&2))


class UsbWire:
    def __init__(self,config):
        self.config=config
        self.events=queue.Queue(maxsize=128)
        self.stop=threading.Event()
        self.lock=threading.Lock();self.image=None
        self.frames={};self.frame_stamps={};self.last_seq=None
        self.audio=AudioManager(config)
        self.paired=False;self.v2=False;self.last_pair=0
        self.thread=threading.Thread(target=self.run,name='TalkMan-USB',daemon=True)
        self.thread.start()

    def publish(self,image,stamp=None):
        with self.lock:self.image=(image,stamp) if image is not None else None

    def put(self,kind,value):
        try:self.events.put_nowait((kind,value,time.monotonic()))
        except queue.Full:
            self.stop.set() # 忙碌过久时关闭连接，不积攒可执行操作。

    def validate(self,m,now):
        if (m.get('v')!=1 or m.get('token')!=self.config['token'] or
                m.get('config')!=config_id(self.config)):return None
        if any(type(m.get(k)) is not int for k in ('seq','mask','frame')):return None
        if not 0<=m['mask']<=255 or m['seq']<0:return None
        self.paired=True
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

    def run(self):
        dev=ser=None;claimed=False
        try:
            import usb.core
            import usb.util
            import libusb_package
            import serial
            from serial.tools import list_ports
            backend=libusb_package.get_libusb1_backend()
            if backend is None:raise RuntimeError('USB 运行库不可用')
            devices=list(usb.core.find(find_all=True,idVendor=VID,idProduct=PID,backend=backend))
            if not devices:raise RuntimeError('未找到 COLOPLAY，请连接 USB 数据线')
            dev,port=select_pair(devices,list(list_ports.comports()))
            cfg=dev.get_active_configuration()
            interface=next((i for i in cfg if i.bInterfaceNumber==3 and i.bInterfaceClass==255),None)
            if interface is None:raise RuntimeError('此固件未提供 USB 图流接口，请更新固件或使用 Wi-Fi 卡带')
            description=usb.util.get_string(dev,interface.iInterface) or ''
            match=re.fullmatch(r'cxair_R(\d+)x(\d+)_M(\d+)',description)
            if not match:raise RuntimeError('USB 接口未报告图流能力')
            width,height,limit=map(int,match.groups())
            if (width,height)!=(240,240):raise RuntimeError('本版仅支持初代 240×240 圆屏；当前设备规格不匹配')
            endpoints={e.bEndpointAddress:e for e in interface}
            if 3 not in endpoints or endpoints[3].bmAttributes&3!=2:raise RuntimeError('USB 图流端点不匹配')
            usb.util.claim_interface(dev,3);claimed=True
            # 不重置整台复合设备、不解绑 HID/CDC 的系统驱动。
            ser=serial.Serial(port.device,115200,timeout=.015,write_timeout=.2)
            ser.reset_input_buffer()
            self.put('connected','USB 已打开，等待 USB 卡带')
            parser=SerialFrames();incoming=Frames();fid=secrets.randbelow(65536);sent=0
            ping_at=0;pong_at=0;ping_id=secrets.randbelow(65536)
            duplex=0x83 in endpoints
            while not self.stop.is_set():
                now=time.monotonic()
                self.audio.available=self.v2 and self.paired and now-self.last_pair<1.5
                self.audio.tick(now)
                for _ in range(16):
                    try:command=self.audio.commands.get_nowait()
                    except queue.Empty:break
                    if dev.write(3,command,timeout=300)!=len(command):raise RuntimeError('USB 控制消息未完整写入')
                if duplex and now-ping_at>=1:
                    ping_id=(ping_id+1)&65535
                    ping=data_packet(PING,seq=ping_id)
                    if dev.write(3,ping,timeout=300)!=len(ping):raise RuntimeError('USB 状态检测发送失败')
                    ping_at=now
                if self.v2 and now-pong_at>3:raise RuntimeError('USB 设备未响应')
                if duplex:
                    try:chunk=bytes(dev.read(0x83,512,timeout=10))
                    except usb.core.USBTimeoutError:chunk=b''
                    for frame in incoming.feed(chunk):
                        if frame.kind==PONG and len(frame.data)==38:
                            version,echo=struct.unpack_from('<HH',frame.data)
                            if version==2 and echo==ping_id:self.v2=True;pong_at=time.monotonic()
                        else:self.audio.receive(frame)
                with self.lock:published=self.image
                if published is not None and now-sent>=(.35 if self.audio.stream else .25):
                    image,stamp=published
                    packet=jpeg_packet(image,fid,limit)
                    if dev.write(3,packet,timeout=500)!=len(packet):raise RuntimeError('USB 图像未完整写入')
                    self.frames[fid]=time.monotonic()
                    self.frame_stamps[fid]=stamp
                    self.frames={k:v for k,v in self.frames.items() if now-v<2}
                    self.frame_stamps={k:v for k,v in self.frame_stamps.items() if k in self.frames}
                    fid=(fid+1)&65535;sent=now
                for message in parser.feed(ser.read(min(8192,max(1,ser.in_waiting)))):
                    event=self.validate(message,time.monotonic())
                    if event in ('wake','exit'):self.put(event,None)
                    elif event:self.put(event[0],dict(mask=event[1],stamp=self.frame_stamps.get(message['frame'])))
        except Exception as error:self.put('error','USB 未就绪：'+str(error))
        finally:
            self.audio.disconnect()
            if ser:ser.close()
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
        self.wire=UsbWire(controller.config)
        # Wait for a paired cartridge wake message before sending any display frames.
        self.wire.publish(None)
        if not quiet:controller.local_notice('正在检查 USB 接口…')

    def poll(self):
        if self.closed:return
        now=time.monotonic()
        for _ in range(128):
            try:kind,value,arrived=self.wire.events.get_nowait()
            except queue.Empty:break
            if kind in ('error','connected') and (not self.quiet or self.session.enabled):self.controller.local_notice(value)
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
                # 只有零按键唤醒画面；此报文不执行任何业务动作。
                self.streaming=True;self.exited=False
                if self.controller.local_session is not self.session:
                    self.controller.intent.cancel()
                self.controller.local_session=self.session
                self.session.enabled=True;self.last_input=now
                self.session.feed(0)
            elif kind in ('buttons','reset'):
                if now-arrived>.35 or self.exited or not display_matches(value['stamp'],self.controller.stamp(),value['mask']):
                    if self.controller.local_session is self.session:self.session.reset()
                    continue
                if kind=='reset' or not self.session.enabled:self.session.reset()
                if self.controller.local_session is not self.session:
                    self.controller.intent.cancel()
                self.controller.local_session=self.session
                self.session.enabled=True;self.last_input=now
                self.session.feed(value['mask'])
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
        elif now >= getattr(self, 'next_render', 0):
            self.next_render=now+.1
            self.wire.publish(self.session.render(),self.controller.stamp())

    def close(self):
        if self.closed:return
        owned=self.controller.local_session is self.session
        self.closed=True;self.session.enabled=False
        if owned:self.session.reset()
        self.wire.close()
        if self.controller.local_session is self.session:self.controller.local_session=None
        if not self.controller.connected():self.controller.set_active(False)
