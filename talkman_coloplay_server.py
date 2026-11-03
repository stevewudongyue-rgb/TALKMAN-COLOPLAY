"""独立的局域网状态服务。不接收正文、模型 Key 或远程编辑命令。"""
from __future__ import annotations

import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import secrets
import threading
import time

from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed
from talkman_coloplay_cartridge import valid_build


class StateServer:
    def __init__(self, host, port, token, config, events, diagnostics_path=None):
        self.events, self.token, self.config = events, token, config
        self.lock = threading.Lock()
        self.peer = None
        self.state = {}
        self.closed = False
        self.diagnostics = logging.Logger('TalkMan-COLOPLAY连接', level=logging.INFO)
        if diagnostics_path is not None:
            try:
                handler = RotatingFileHandler(diagnostics_path, maxBytes=262144,
                                              backupCount=1, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                self.diagnostics.addHandler(handler)
            except OSError:
                pass
        self.diagnostics.addHandler(logging.NullHandler())
        self.server = serve(self.handle, host, port, max_size=1024,
                            compression=None, ping_interval=None, open_timeout=3,
                            close_timeout=1, max_queue=4)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                       name='TalkMan-COLOPLAY状态')
        self.thread.start()

    def publish(self, state):
        with self.lock:
            self.state = dict(state)

    def handle(self, ws):
        peer_id = secrets.token_hex(8)
        accepted = False
        started = last_rx = time.monotonic()
        heartbeats = 0
        reason = '连接结束'
        close_code = None
        try:
            hello = json.loads(ws.recv(timeout=3))
            if (not isinstance(hello, dict) or hello.get('type') != 'hello'
                    or hello.get('version') != 1 or hello.get('config') != self.config
                    or not hmac.compare_digest(str(hello.get('token', '')), self.token)):
                reason = '配对或配置不匹配'
                ws.close(1008, reason)
                return
            with self.lock:
                if self.peer is not None:
                    reason = '已有设备连接'
                    ws.close(1008, reason)
                    return
                self.peer = (peer_id, ws)
                accepted = True
            build = valid_build(hello.get('device_build'))
            self.events.put(('connected', peer_id, build))
            self.diagnostics.info('连接建立 会话=%s 模式=%s 卡带构建=%s', peer_id,
                                  '顺序收发' if hello.get('heartbeat') == 'state-ack' else '旧版收发', build or '未知')
            # 新卡带以状态帧驱动下一轮收发，100ms 上限兼顾旋钮响应和设备负载。
            interval = .1 if hello.get('heartbeat') == 'state-ack' else .25
            last_rx = time.monotonic()
            bucket, count = last_rx, 0
            seq = 0
            sent_state, sent_at = None, 0.0
            while not self.closed and time.monotonic()-last_rx < 4:
                with self.lock:
                    state = dict(self.state)
                now = time.monotonic()
                # 输入预约仍每 20ms 检查；空闲状态只保活，不用 50Hz 重复灌满设备接收缓冲。
                if state != sent_state or now-sent_at >= interval:
                    seq += 1
                    ws.send(json.dumps(dict(type='state', version=1, session=peer_id,
                                            seq=seq, **state), ensure_ascii=False))
                    sent_state, sent_at = state, now
                try:
                    raw = ws.recv(timeout=.02)
                except TimeoutError:
                    continue
                now = time.monotonic()
                if now-bucket >= 1:
                    bucket, count = now, 0
                count += 1
                if count > 80:
                    reason = '消息过快'
                    ws.close(1008, '消息过快'); break
                message = json.loads(raw)
                if not isinstance(message, dict):
                    reason = '消息格式错误'
                    ws.close(1008, '消息格式错误'); break
                kind = message.get('type')
                if kind == 'heartbeat':
                    last_rx = now
                    heartbeats += 1
                elif kind in ('intent', 'cancel_intent', 'exit', 'cartridge_check'):
                    if self.events.qsize() >= 32:
                        reason = '动作队列已满'
                        ws.close(1013, '请稍后重试'); break
                    if kind in ('intent', 'cartridge_check'):
                        identifier = message.get('id')
                        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 32:
                            reason = '动作编号错误'
                            ws.close(1008, '动作编号错误'); break
                    last_rx = now
                    self.events.put((kind, peer_id, message))
                    if kind == 'exit':
                        reason = '设备退出模式'
                        break
                else:
                    reason = '不支持的消息'
                    ws.close(1008, '不支持的消息'); break
            if self.closed:
                reason = '服务关闭'
            elif time.monotonic()-last_rx >= 4:
                reason = '设备心跳超时'
                ws.close(1001, reason)
        except ConnectionClosed as exc:
            reason = '对端关闭连接' if exc.rcvd is not None else '传输中断'
            close_code = exc.rcvd.code if exc.rcvd is not None else None
        except TimeoutError:
            reason = '设备握手超时'
        except (ValueError, TypeError):
            reason = '消息解析失败'
        finally:
            self.diagnostics.info('连接结束 会话=%s 已配对=%s 原因=%s 对端关闭码=%s 持续秒=%.3f 心跳数=%d 接收空闲秒=%.3f',
                                  peer_id, accepted, reason, close_code, time.monotonic()-started,
                                  heartbeats, time.monotonic()-last_rx)
            if accepted:
                with self.lock:
                    if self.peer and self.peer[0] == peer_id:
                        self.peer = None
                self.events.put(('disconnected', peer_id, None))
            ws.close()

    def stop(self):
        self.closed = True
        with self.lock:
            peer = self.peer
        if peer:
            peer[1].close(1001, '服务关闭')
        self.server.shutdown()
        self.thread.join(timeout=2)
        for handler in self.diagnostics.handlers:
            handler.close()
