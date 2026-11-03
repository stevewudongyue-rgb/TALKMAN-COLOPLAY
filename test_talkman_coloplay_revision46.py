"""Wi-Fi 实机缺少心跳的回归；真实 Copas 调度、故障注入与隔离 WebSocket。"""
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest

from lupa.lua54 import LuaRuntime
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

from talkman_coloplay_server import StateServer


def device_runtime(source=None, failure='none'):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        clock=0; sent={}; errors={}; sockets={}; attempts=0; failed=false
        function time() return clock/1000 end
        function utcsec() return math.floor(clock/1000) end
        function stat(i) return clock end
        function net_select(...) return {},{} end
        function printh(...) errors[#errors+1]='network error' end
        function usemod(...) end
        function btnpcfg(...) end
        function btn(...) return false end
        function toidle() end
        function TMDraw(...) end
        function TMUpdateControls(...) return {step=function() return {} end} end
        function TMCartridgeUpdate(...) return {init=function() end,connected=function() end} end
        TM_CONFIG={url='isolated',token='fake',config='isolated',keys={}}
    ''')
    lua.execute(Path('TalkMan-设备卡带/colox_net.lua').read_text('utf-8'))
    lua.globals().failure = failure
    lua.execute('''
        function ws_connect(...)
            attempts=attempts+1
            local s={number=attempts,closed=false,seq=0}
            sockets[#sockets+1]=s
            function s:isclosed() return self.closed end
            function s:send(kind)
                if kind=='heartbeat' and not failed and failure~='none' then
                    failed=true
                    if failure=='exception' then error('injected send error') end
                    self.closed=true;return nil,'closed'
                end
                sent[#sent+1]={kind=kind,clock=clock,session=self.number}
                return true
            end
            function s:receive()
                copas.sleep(.1)
                if self.closed then return nil,'closed' end
                self.seq=self.seq+1
                return {op='text',data=tostring(self.seq)}
            end
            function s:close() self.closed=true end
            return s
        end
        package.preload.cjson_safe=function() return {
            encode=function(t) return t.type end,
            decode=function(s) return {type='state',version=1,session='one',seq=tonumber(s),ready=true} end
        } end
    ''')
    if source is None:
        source = Path('TalkMan-设备卡带/TalkMan-coloplay.clx').read_text('utf-8')
    source = source.split('__lua__\n', 1)[1]
    lua.execute('\n'.join(line for line in source.splitlines() if not line.startswith('#include')))
    return lua


class DeviceNetworkTests(unittest.TestCase):
    def run_device(self, failure='none'):
        lua = device_runtime(failure=failure)
        self.assertEqual(lua.globals().attempts, 0, '初始化前不启动网络任务')
        lua.execute('_init()')
        for clock in range(0, 6001, 20):
            lua.globals().clock = clock
            lua.execute('copas.step()')
        sent = [dict(item.items()) for item in lua.globals().sent.values()]
        return lua, sent

    def test_same_session_sends_heartbeat_for_longer_than_desktop_timeout(self):
        lua, sent = self.run_device()
        beats = [m for m in sent if m['kind'] == 'heartbeat']
        self.assertEqual(lua.globals().attempts, 1)
        self.assertGreaterEqual(len(beats), 6)
        self.assertTrue(all(b['clock']-a['clock'] <= 1000 for a,b in zip(beats, beats[1:])))
        self.assertEqual(sent[0]['kind'], 'hello')

    def test_send_exception_recreates_entire_connection_and_heartbeat(self):
        lua, sent = self.run_device('exception')
        self.assertEqual(lua.globals().attempts, 2)
        self.assertTrue(lua.globals().sockets[1].closed)
        beats = [m for m in sent if m['kind'] == 'heartbeat' and m['session'] == 2]
        self.assertGreaterEqual(len(beats), 5)
        self.assertEqual(len(lua.globals().errors), 1)

    def test_send_failure_cannot_leave_receiver_running_without_heartbeat(self):
        lua, sent = self.run_device('closed')
        self.assertEqual(lua.globals().attempts, 2)
        self.assertGreaterEqual(sum(m['kind'] == 'heartbeat' for m in sent), 5)
        self.assertTrue(all(m['session'] == 2 for m in sent if m['kind'] == 'heartbeat'))


class ServerDiagnosticsTests(unittest.TestCase):
    def test_missing_heartbeat_reports_timeout_without_secret_or_text(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder)/'connection.log'
            events = queue.Queue()
            server = StateServer('127.0.0.1', 0, 'private-token', 'cfg', events, log)
            try:
                with connect(f'ws://127.0.0.1:{server.port}', ping_interval=None) as ws:
                    ws.send(json.dumps(dict(type='hello',version=1,token='private-token',config='cfg')))
                    with self.assertRaises(ConnectionClosed) as stopped:
                        while True: ws.recv(timeout=5)
                    self.assertEqual(stopped.exception.rcvd.code, 1001)
                    until = time.monotonic()+1
                    while '设备心跳超时' not in log.read_text('utf-8') and time.monotonic()<until:
                        time.sleep(.01)
            finally: server.stop()
            text = log.read_text('utf-8')
            self.assertIn('设备心跳超时', text)
            self.assertIn('心跳数=0', text)
            self.assertNotIn('private-token', text)

    def test_state_driven_device_gets_bounded_100ms_updates_and_stays_connected(self):
        server = StateServer('127.0.0.1', 0, 'fake', 'cfg', queue.Queue())
        sessions = set(); received = 0
        try:
            with connect(f'ws://127.0.0.1:{server.port}', ping_interval=None) as ws:
                ws.send(json.dumps(dict(type='hello',version=1,token='fake',config='cfg',heartbeat='state-ack')))
                until=time.monotonic()+4.5
                while time.monotonic()<until:
                    state=json.loads(ws.recv(timeout=.5))
                    sessions.add(state['session']);received+=1
                    ws.send(json.dumps(dict(type='heartbeat')))
                self.assertIsNotNone(server.peer)
        finally: server.stop()
        self.assertEqual(len(sessions),1)
        # 原生 CI 的线程调度存在抖动；验证持续有帧且有节流，不把目标周期当硬实时承诺。
        self.assertGreaterEqual(received,20)
        self.assertLess(received,65)


if __name__ == '__main__':
    unittest.main()
