"""Offline cartridge idle checks. Native firmware APIs are fakes, not hardware validation."""
import importlib.util
import json
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parent


def runtime(source=None, usb_ready=False, send_ok=True, udp_failure=False):
    spec = importlib.util.spec_from_file_location('idle_builder', ROOT/'TalkMan-构建卡带.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = source or ROOT/'TalkMan-设备卡带'
    lua = LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    g.usb_ready, g.send_ok, g.udp_failure = usb_ready, send_ok, udp_failure
    # Only the unused host APIs are stubbed. Run real scheduler, gestures,
    # discovery, USB, update logic and drawing code, without sockets/devices.
    lua.execute('''
      clock=0; binds=0; closes=0; sends=0; errors=0; usb_reads=0; net_polls=0
      function time() return clock/1000 end
      function utcsec() return math.floor(clock/1000) end
      function stat(i) if i==0 then return collectgarbage('count') else return clock end end
      function usemod(name) assert(name=='net', 'must never open microphone');return true end
      function btnpcfg() end
      function btn() return false end
      function cartdata() return false end
      function getcolor(r,g,b) return r*65536+g*256+b end
      function printh() errors=errors+1 end
      function net_select() net_polls=net_polls+1;return {},{} end
      function net_connect() error('no endpoint: must not connect') end
      function net_bind()
        binds=binds+1
        return {recvfrom=function() if udp_failure then return nil,'network down' end;return false end,
                close=function() closes=closes+1 end}
      end
      function sha1sum() return string.rep('0',20) end
      function airdata_ready() return usb_ready end
      function airdata_get() usb_reads=usb_reads+1;return nil end
      function airdata_send() sends=sends+1;return send_ok end
      function svcreq() error('no firmware service expected') end
      function toidle() error('no unsolicited exit expected') end
      for _,name in ipairs({'cls','print','circ','circfill','rect','rectfill','line','wline','pset'}) do
        _G[name]=function() end
      end
      package.preload.cjson_safe=function() return {
        encode=function() return '{"test":true}' end, decode=function() return nil end
      } end
    ''')
    lua.execute(builder.flatten(source/'TalkMan-coloplay.clx').split('__lua__\n', 1)[1])
    lua.execute("TM_CONFIG.token='offline-test';TM_CONFIG.config='offline-test';TM_CONFIG.discovery=true;_init()")
    lua.execute('''
      function sample(frames)
        local peak=0
        for i=1,frames do
          clock=clock+1000/60;_update();_draw()
          if i%60==0 then peak=math.max(peak,collectgarbage('count')) end
        end
        collectgarbage('collect')
        return collectgarbage('count'),peak
      end
    ''')
    return lua


class IdleTests(unittest.TestCase):
    def test_unready_usb_never_polls_native_data_queues(self):
        lua = runtime()
        lua.globals().sample(120)
        self.assertEqual(lua.globals().usb_reads, 0)

    def test_unpaired_usb_never_sends_without_desktop_even_if_send_would_fail(self):
        lua = runtime(usb_ready=True, send_ok=False)
        lua.globals().sample(600)
        self.assertEqual(lua.globals().sends, 0)

    def test_usb_without_desktop_leaves_native_transmit_queue_untouched(self):
        lua = runtime(usb_ready=True)
        lua.execute('''
          queued=0
          function airdata_depth(dir,prio) assert(dir=='tx' and prio==1);return queued,8 end
          function airdata_send() sends=sends+1;queued=queued+1;return true end
        ''')
        lua.globals().sample(600)
        self.assertEqual(lua.globals().sends, 0)
        lua.execute('queued=0')
        lua.globals().sample(60)
        self.assertEqual(lua.globals().sends, 0)

    def test_disconnect_clears_state_before_reconnect(self):
        lua = runtime(usb_ready=True)
        lua.execute('''
          package.loaded.cjson_safe.decode=function()
            return {v=1,token='offline-test',config='offline-test',frame=9,
                    state={ready=true}}
          end
          incoming=true
          function airdata_get(channel)
            usb_reads=usb_reads+1
            if channel==0 and incoming then incoming=false;return '{}',20 end
          end
          -- Existing dedicated cartridges still accept their paired state directly.
          TM_CONFIG.usb_pairing=nil
          u=TMUsbTransport(TM_CONFIG);assert(u:step(0));usb_ready=false
        ''')
        reads = lua.globals().usb_reads
        self.assertFalse(lua.eval('u:step(10)'))
        self.assertEqual(lua.globals().usb_reads, reads)
        lua.execute('usb_ready=true')
        self.assertFalse(lua.eval('u:step(20)'), '旧配对状态不得在重新插线后直接恢复')
        lua.execute('incoming=true')
        self.assertTrue(lua.eval('u:step(130)'))

    def test_offline_without_usb_does_not_accumulate_lua_state(self):
        lua = runtime()
        start, _ = lua.globals().sample(600)
        end, _ = lua.globals().sample(36000)
        self.assertLess(end-start, 16)
        self.assertEqual(lua.globals().binds, 1)
        self.assertEqual(lua.globals().sends, 0)
        self.assertEqual(lua.globals().errors, 0)


if __name__ == '__main__':
    for name, options in [
        ('unplugged', {}), ('usb_without_desktop', {'usb_ready': True}),
        ('usb_send_failure', {'usb_ready': True, 'send_ok': False}),
        ('network_unavailable', {'udp_failure': True}),
    ]:
        lua = runtime(**options)
        start, _ = lua.globals().sample(600)
        end, peak = lua.globals().sample(108000)
        g = lua.globals()
        print(json.dumps(dict(scenario=name, simulated_seconds=1810, lua_kb_start=start,
            lua_kb_end=end, lua_kb_peak=peak, usb_send_attempts=g.sends,
            udp_binds=g.binds, udp_closes=g.closes, errors=g.errors)))
