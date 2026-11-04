"""Explicit recording sessions over CXUS. The USB worker owns all USB writes."""
import json
import queue
import secrets
import threading
import time

from talkman_airdata import AUDIO, CONTROL, opus_units, packet
from talkman_opus import Decoder
from talkman_coloplay_protocol import config_id


class AudioStream:
    def __init__(self, manager, capture, failure):
        self.manager, self.capture, self.failure = manager, capture, failure
        self.session = secrets.token_hex(16)
        self.tag = secrets.randbelow(65535)+1
        self.started = threading.Event()
        self.error = ''
        self.stopped = False
        self.decoder = None
        self.sequence = None
        self.last_audio = self.keep_at = time.monotonic()

    def start(self):
        self.manager.begin(self)
        if not self.started.wait(2.5):
            self.abort()
            raise RuntimeError('COLOPLAY 未确认录音，请更新卡带并检查 USB 连接。')
        if self.error:
            self.abort()
            raise RuntimeError(self.error)

    def abort(self):
        self.manager.end(self)

    close = abort


class AudioManager:
    def __init__(self, config, decoder_factory=Decoder):
        self.config, self.decoder_factory = config, decoder_factory
        self.lock = threading.RLock()
        self.commands = queue.Queue(maxsize=16)
        self.stream = None
        self.available = False

    def create(self, capture, failure):
        return AudioStream(self, capture, failure)

    def command(self, stream, action):
        data = dict(protocol='TM_AUDIO_1', action=action, session=stream.session,
                    tag=stream.tag, token=self.config['token'], config=config_id(self.config))
        self.commands.put_nowait(packet(CONTROL, json.dumps(data).encode(), priority=1))

    def begin(self, stream):
        with self.lock:
            if not self.available:
                raise RuntimeError('请先连接支持录音上传的 COLOPLAY USB 卡带。')
            if self.stream:
                raise RuntimeError('设备麦克风已有录音会话。')
            self.stream = stream
            try:
                stream.decoder = self.decoder_factory()
                self.command(stream, 'start')
            except Exception:
                self.end(stream)
                raise

    def end(self, stream):
        with self.lock:
            if self.stream is not stream:
                return
            self.stream = None
            stream.stopped = True
            stream.started.set()
            try:
                self.command(stream, 'stop')
            except queue.Full:
                pass  # Firmware lease expires even if stop cannot be delivered.
            if stream.decoder:
                stream.decoder.close()

    def fail(self, message):
        with self.lock:
            stream = self.stream
            if stream:
                stream.error = message
                stream.started.set()
                stream.failure(message)
                self.end(stream)

    def disconnect(self):
        self.available = False
        self.fail('COLOPLAY USB 已断开，本轮录音未提交；请重新连接后录入。')

    def tick(self, now):
        with self.lock:
            stream = self.stream
            if stream:
                if now-stream.last_audio > 2.0:
                    self.fail('COLOPLAY 音频已中断，本轮未提交，请检查设备麦克风。')
                elif now-stream.keep_at >= .25:
                    try:
                        self.command(stream, 'keep' if stream.started.is_set() else 'start')
                        stream.keep_at = now
                    except queue.Full:
                        self.fail('USB 控制队列繁忙，录音已停止，请重新连接。')

    def receive(self, frame):
        with self.lock:
            stream = self.stream
            if not stream:
                return
            if frame.kind == CONTROL:
                try:
                    data = json.loads(frame.data)
                except (ValueError, UnicodeError):
                    return
                if not isinstance(data, dict) or data.get('protocol') != 'TM_AUDIO_1' or data.get('session') != stream.session:
                    return
                if data.get('action') == 'started' and data.get('tag') == stream.tag:
                    stream.started.set()
                elif data.get('action') == 'begin' and data.get('tag') == stream.tag and frame.priority == 0:
                    if stream.sequence is None:stream.sequence=frame.seq
                elif data.get('action') == 'error':
                    self.fail('COLOPLAY 麦克风无法启动，请检查新固件与录音卡带。')
            elif frame.kind == AUDIO and frame.tag == 0 and frame.sub == stream.tag and stream.started.is_set():
                if stream.sequence is not None:
                    gap = (frame.seq-stream.sequence) & 65535
                    if gap == 0:
                        return
                    if gap != 1:
                        self.fail('USB 录音发生丢包，本轮未提交，请检查数据线后重录。')
                        return
                else:
                    self.fail('USB 录音起始音频缺失，请重新录入。')
                    return
                try:
                    for unit in opus_units(frame.data):
                        pcm = stream.decoder.decode(unit)
                        stream.capture(pcm, len(pcm)//2, None, None)
                except (ValueError, RuntimeError):
                    self.fail('设备音频格式异常，本轮未提交，请更新卡带后重试。')
                    return
                stream.sequence = frame.seq
                stream.last_audio = time.monotonic()
