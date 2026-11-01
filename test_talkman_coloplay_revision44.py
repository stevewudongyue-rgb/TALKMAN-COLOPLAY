"""五秒确认期继续输入、Wi-Fi 去重与 Lua 接收公平调度。仅使用隔离服务。"""
import json
from pathlib import Path
import queue
import time
import unittest
from unittest.mock import patch

from websockets.sync.client import connect
from talkman_coloplay_server import StateServer
from talkman_coloplay_preview import LocalDeviceSession
from talkman_coloplay_usb import display_matches
import test_talkman_coloplay as fixtures
import test_talkman_coloplay_revision42 as previous


class ContinueInputTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    tearDown = fixtures.DesktopTests.tearDown
    drain = previous.InteractionTests.drain
    prepare_send = previous.InteractionTests.prepare_send

    def test_left_lower_starts_record_and_cancels_old_send_without_waiting(self):
        a, d = self.prepare_send()
        session = LocalDeviceSession(d)
        session.feed(4, 100)
        self.assertIsNotNone(a.voice)
        self.assertEqual(a.voice_mode, 'insert')
        self.assertFalse(d.device_send_pending())
        session.feed(0, 180)
        session.feed(4, 240)
        self.assertTrue(a.voice_stopping)
        a.target_controller.send_filled.assert_not_called()

    def test_lower_combo_starts_instruction_and_old_left_upper_lease_cannot_send(self):
        a, d = self.prepare_send()
        session = LocalDeviceSession(d)
        session.feed(1, 100)  # 提前按下左上，但尚未松手确认。
        session.feed(9, 120)
        session.feed(13, 150)
        self.assertIsNotNone(a.voice)
        self.assertEqual(a.voice_mode, 'rewrite')
        self.assertFalse(d.device_send_pending())
        session.feed(0, 200)
        a.target_controller.send_filled.assert_not_called()

    def test_wifi_record_reservation_cancels_send_but_rotation_stays_locked(self):
        a, d = self.prepare_send()
        self.assertIsNone(d.semantic('target_next'))
        self.assertIsNone(d.semantic('knob'))
        d.arm(dict(id='next-recording', action='record', confirmation=d.confirmation_marker()))
        d.dispatch('coloplay_record')
        self.assertIsNotNone(a.voice)
        self.assertIsNone(a.pending_delivery)
        a.target_controller.send_filled.assert_not_called()

    def test_usb_new_record_accepts_focus_lag_but_rejects_other_target(self):
        a, d = self.prepare_send()
        old = d.stamp()
        current = list(old)
        current[11] = (False, old[11][1]+1, False)
        for mask in (4, 8, 12):
            self.assertTrue(display_matches(old, tuple(current), mask))
        current[5] = ('different-target',)
        self.assertFalse(display_matches(old, tuple(current), 12))


class WifiFlowTests(unittest.TestCase):
    def test_idle_sends_four_per_second_and_changed_arm_is_prompt(self):
        events = queue.Queue()
        server = StateServer('127.0.0.1', 0, 'x'*48, 'test', events)
        server.publish(dict(ready=True, phase='idle', arm=''))
        try:
            with connect(f'ws://127.0.0.1:{server.port}', ping_interval=None) as ws:
                ws.send(json.dumps(dict(type='hello',version=1,token='x'*48,config='test')))
                count, until = 0, time.monotonic()+1.05
                while time.monotonic() < until:
                    try:
                        ws.recv(timeout=.03)
                        count += 1
                    except TimeoutError:
                        pass
                self.assertGreaterEqual(count, 4)
                self.assertLessEqual(count, 6)
                start = time.monotonic()
                server.publish(dict(ready=True, phase='idle', arm='new-action'))
                while True:
                    frame = json.loads(ws.recv(timeout=.2))
                    if frame.get('arm') == 'new-action':
                        break
                self.assertLess(time.monotonic()-start, .2)
        finally:
            server.stop()

    def test_slow_device_receives_fresh_frames_and_heartbeat_stays_connected(self):
        events = queue.Queue()
        server = StateServer('127.0.0.1', 0, 'x'*48, 'test', events)
        server.publish(dict(ready=True))
        try:
            with connect(f'ws://127.0.0.1:{server.port}', ping_interval=None) as ws:
                ws.send(json.dumps(dict(type='hello',version=1,token='x'*48,config='test')))
                until, beat, received = time.monotonic()+5, 0, []
                while time.monotonic() < until:
                    now = time.monotonic()
                    if now-beat > .5:
                        ws.send(json.dumps(dict(type='heartbeat')))
                        beat=now
                    time.sleep(.08)  # 模拟设备绘图及处理耗时。
                    try:
                        received.append(json.loads(ws.recv(timeout=.05)))
                    except TimeoutError:
                        pass
                self.assertIsNotNone(server.peer)
                self.assertLess(len(received), 25)
                self.assertGreater(len(received), 12)
                self.assertEqual(len({m['session'] for m in received}), 1)
                self.assertEqual([m['seq'] for m in received], sorted({m['seq'] for m in received}))
        finally:
            server.stop()

    def test_lua_receive_yields_between_cached_frames_so_heartbeat_can_run(self):
        from lupa.lua54 import LuaRuntime
        lua=LuaRuntime(unpack_returned_tuples=True)
        source=Path('TalkMan-设备卡带/TalkMan-coloplay.clx').read_text('utf-8').split('__lua__\n',1)[1]
        source='\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
        lua.execute('''
          clock=0; reads=0; beats=0; jobs={}
          function stat(i) return clock end
          function usemod(...) end
          function btnpcfg(...) end
          function printh(...) end
          function TMGesture(...) return {} end
          TM_CONFIG={url='test',token='fake',config='test'}
          copas={addthread=function(f) jobs[#jobs+1]=coroutine.create(f) end,
                 sleep=function(t) coroutine.yield(t) end}
          local s={}
          function s:isclosed() return false end
          function s:send(data) if data=='heartbeat' then beats=beats+1 end return true end
          function s:receive()
            reads=reads+1
            if reads>100 then return nil end
            return {op='text',data='state'}
          end
          function s:close() end
          function ws_connect(...) return s end
          package.preload.cjson_safe=function() return {
            encode=function(t) return t.type end,
            decode=function() return {type='state',version=1,session='same',seq=reads,ready=true} end
          } end
        ''')
        lua.execute(source)
        lua.execute('_init()')
        lua.execute('assert(coroutine.resume(jobs[1]))')
        self.assertEqual(lua.globals().reads, 1)
        self.assertEqual(lua.globals().beats, 1)
        lua.execute('clock=1000; assert(coroutine.resume(jobs[1]))')
        self.assertEqual(lua.globals().reads, 2)
        self.assertEqual(lua.globals().beats, 2)


if __name__ == '__main__':
    unittest.main()
