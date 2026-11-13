"""Explicit recording sessions over CXUS. The USB worker owns all USB writes."""
import json
from collections import deque
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
        # Counts only; never retain encoded audio, PCM, pairing or session IDs.
        self.stats = dict(confirmed=False, begin_seq=None, packets=0, payload_bytes=0,
                          opus_units=0, pcm_frames=0, duplicates=0, format_errors=0,
                          last_seq=None, gap_expected=None, gap_received=None,
                          gap_missing=0, other_normal=0, device_stopped=False,
                          last_audio_age_ms=0, failure_code='')
        self.stats['recent_frames'] = deque(maxlen=20)
        self.stats['parser'] = dict(crc_errors=0, invalid_headers=0, discarded_bytes=0)
        self.stats['crc_frames'] = []
        self.stats['read_packet_bytes'] = manager.read_packet_bytes
        self.parser_baseline = dict(manager.transport_stats)

    def start(self):
        self.manager.begin(self)
        if not self.started.wait(2.5):
            self.stats['failure_code'] = 'start_timeout'
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
        self.transport_stats = {}
        self.transport_crc_frames = ()
        self.read_packet_bytes = 0

    def observe_frame(self, frame):
        """Header metadata only, including other queues; no USB payload is logged."""
        with self.lock:
            if self.stream:
                self.stream.stats['recent_frames'].append((frame.kind, frame.priority,
                                                          frame.seq, len(frame.data)))

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
            stream.stats['last_audio_age_ms'] = max(0, round((time.monotonic()-stream.last_audio)*1000))
            stream.stats['parser'] = {key: max(0, self.transport_stats.get(key, 0)-stream.parser_baseline.get(key, 0))
                                      for key in stream.stats['parser']}
            stream.stats['crc_frames'] = [item[1:] for item in self.transport_crc_frames
                                          if item[0] > stream.parser_baseline.get('crc_errors', 0)]
            stream.started.set()
            try:
                self.command(stream, 'stop')
            except queue.Full:
                pass  # Firmware lease expires even if stop cannot be delivered.
            if stream.decoder:
                stream.decoder.close()

    def fail(self, message, code=''):
        with self.lock:
            stream = self.stream
            if stream:
                stream.error = message
                stream.stats['failure_code'] = code
                stream.started.set()
                stream.failure(message)
                self.end(stream)

    def disconnect(self):
        self.available = False
        self.fail('COLOPLAY USB 已断开，本轮录音未提交；请重新连接后录入。', 'usb_disconnected')

    def tick(self, now):
        with self.lock:
            stream = self.stream
            if stream:
                if now-stream.last_audio > 2.0:
                    self.fail('COLOPLAY 音频已中断，本轮未提交，请检查设备麦克风。', 'audio_timeout')
                elif now-stream.keep_at >= .25:
                    try:
                        self.command(stream, 'keep' if stream.started.is_set() else 'start')
                        stream.keep_at = now
                    except queue.Full:
                        self.fail('USB 控制队列繁忙，录音已停止，请重新连接。', 'control_queue_full')

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
                    stream.stats['confirmed'] = True
                    stream.started.set()
                elif data.get('action') == 'begin' and data.get('tag') == stream.tag and frame.priority == 0:
                    if stream.sequence is None:
                        stream.sequence=frame.seq
                        stream.stats['begin_seq'] = frame.seq
                elif data.get('action') == 'stopped' and data.get('tag') == stream.tag:
                    stream.stats['device_stopped'] = True
                elif data.get('action') == 'error':
                    self.fail('COLOPLAY 麦克风无法启动，请检查新固件与录音卡带。', 'device_error')
            elif frame.kind == AUDIO and frame.tag == 0 and frame.sub == stream.tag and stream.started.is_set():
                stream.stats['packets'] += 1
                stream.stats['payload_bytes'] += len(frame.data)
                if stream.sequence is not None:
                    gap = (frame.seq-stream.sequence) & 65535
                    if gap == 0:
                        stream.stats['duplicates'] += 1
                        return
                    if gap != 1:
                        stream.stats.update(gap_expected=(stream.sequence+1)&65535,
                                            gap_received=frame.seq, gap_missing=gap-1)
                        self.fail('USB 录音包序号不连续，本轮未提交。请重新录入；若重复发生，请复制录音诊断。', 'sequence_gap')
                        return
                else:
                    self.fail('USB 录音起始音频缺失，请重新录入。', 'missing_begin')
                    return
                try:
                    for unit in opus_units(frame.data):
                        pcm = stream.decoder.decode(unit)
                        stream.stats['opus_units'] += 1
                        stream.stats['pcm_frames'] += len(pcm)//2
                        stream.capture(pcm, len(pcm)//2, None, None)
                except (ValueError, RuntimeError):
                    stream.stats['format_errors'] += 1
                    self.fail('设备音频格式异常，本轮未提交，请更新卡带后重试。', 'audio_format')
                    return
                stream.sequence = frame.seq
                stream.stats['last_seq'] = frame.seq
                stream.last_audio = time.monotonic()
            elif frame.priority == 0 and 16 <= frame.kind < 32:
                stream.stats['other_normal'] += 1
