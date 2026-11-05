"""Real Lua transport + fake native queues and USB: no hardware or user data."""
from collections import deque
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from lupa.lua54 import LuaRuntime, lua_type

from talkman_airdata import BUTTONS, HELLO, PING, PONG, STATE, HEADER, Frame, Frames, packet
from talkman_coloplay_protocol import config_id, validate_config
from talkman_coloplay_usb import UsbWire, hello_packet, state_packet
from test_talkman_usb_descriptors import Interface


class Cartridge:
    def __init__(self, config):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.rx, self.tx = deque(), deque()
        self.sent, self.reads, self.mask, self.now = [], 0, 0, 0
        self.ready, self.send_ok = True, True
        g = self.lua.globals()

        def plain(value):
            return {k: plain(v) for k, v in value.items()} if lua_type(value) == 'table' else value

        def decode(data):
            try:
                return self.lua.table_from(json.loads(data), recursive=True)
            except (ValueError, TypeError):
                return None

        g.decode, g.encode = decode, lambda value: json.dumps(plain(value))
        g.airdata_ready = lambda: self.ready
        g.airdata_get, g.airdata_send = self.read, self.send
        g.airdata_depth = lambda direction, priority: len(self.tx)
        g.btn = lambda i: bool(self.mask & (1 << i))
        g.stat = lambda i: self.now
        self.lua.execute('''
          package.preload.cjson_safe=function() return {encode=encode,decode=decode} end
          function usemod() error('microphone must remain off') end
          function svcreq() error('CDC must not be opened') end
          function toidle() end
        ''')
        root = Path(__file__).parent/'TalkMan-设备卡带'
        for name in ('TalkMan-版本.lua', 'TalkMan-手势.lua', 'TalkMan-更新组合.lua', 'TalkMan-USB通道.lua'):
            self.lua.execute((root/name).read_text('utf-8'))
        self.transport = g.TMUsbTransport(self.lua.table_from(dict(
            token=config['token'], config=config_id(config), fill_ms=500, exit_ms=5000, center=4)))

    def read(self, priority):
        self.reads += 1
        if priority == 0 and self.rx:
            kind, data = self.rx.popleft()
            return data, kind
        return None

    def send(self, kind, data, priority, tag, sub):
        self.sent.append((kind, json.loads(data)))
        if not self.send_ok:
            return False
        self.tx.append(packet(kind, data.encode(), priority=priority, tag=tag, sub=sub))
        return True

    def accept(self, data):
        for frame in Frames().feed(data):
            self.rx.append((frame.kind, frame.data.decode('utf-8')))

    def step(self, milliseconds):
        self.now = milliseconds
        return self.transport.step(self.transport, milliseconds)


class PassiveTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_config({})
        self.cart = Cartridge(self.config)

    def test_thirty_minute_idle_zero_tx_and_bounded_receive_polling(self):
        for t in range(0, 1800000, 16):
            self.cart.step(t)
        self.assertEqual(self.cart.sent, [])
        self.assertLessEqual(self.cart.reads, 18001)
        self.assertFalse(self.cart.transport.active)

    def test_only_matching_probe_replies_without_enabling_controls(self):
        other = dict(self.config, token='wrong')
        for data in (hello_packet(other), packet(HELLO, b'{'), packet(HELLO, b'[]'),
                     packet(HELLO, b'x'*513), packet(18, b'{}')):
            self.cart.accept(data)
        self.cart.step(0)
        self.assertFalse(self.cart.sent)
        self.cart.accept(hello_packet(self.config))
        self.cart.step(100)
        self.assertEqual(len(self.cart.tx), 1)
        kind, message = self.cart.sent[-1]
        self.assertEqual((kind, message['mask'], message['frame']), (BUTTONS, 0, -1))
        self.assertFalse(self.cart.transport.active)

    def test_unconsumed_reply_is_not_replaced_and_retry_requires_new_probe(self):
        for t in (0, 1000, 2000):
            self.cart.accept(hello_packet(self.config));self.cart.step(t)
        self.assertEqual(len(self.cart.sent), 1)
        self.cart.tx.clear()
        self.cart.step(3000)
        self.assertEqual(len(self.cart.sent), 1)
        self.cart.accept(hello_packet(self.config));self.cart.step(4000)
        self.assertEqual(len(self.cart.sent), 2)

    def test_failed_reply_does_not_retry_without_another_probe(self):
        self.cart.send_ok = False
        self.cart.accept(hello_packet(self.config));self.cart.step(0)
        for t in range(16, 30000, 16):self.cart.step(t)
        self.assertEqual(len(self.cart.sent), 1)
        self.cart.send_ok = True
        self.cart.accept(hello_packet(self.config));self.cart.step(31000)
        self.assertEqual(len(self.cart.tx), 1)

    def test_held_keys_wait_for_release_then_fresh_probe_and_state(self):
        self.cart.mask = 4
        self.cart.accept(hello_packet(self.config));self.cart.step(0)
        self.assertFalse(self.cart.sent)
        self.cart.mask = 0;self.cart.step(1000)
        self.assertFalse(self.cart.sent)
        self.cart.accept(hello_packet(self.config));self.cart.step(2000)
        self.cart.tx.clear()
        self.cart.accept(state_packet(dict(ready=True), 7, self.config));self.cart.step(2100)
        self.assertTrue(self.cart.transport.active)
        self.assertEqual(self.cart.sent[-1][1]['mask'], 0)
        self.assertEqual(self.cart.sent[-1][1]['frame'], 7)

    def test_state_timeout_quiets_device_and_next_probe_can_reconnect(self):
        self.cart.accept(state_packet(dict(ready=True), 7, self.config));self.cart.step(0)
        self.cart.tx.clear()
        self.cart.step(1500)
        sent = len(self.cart.sent)
        for t in range(1600, 31000, 100):self.cart.step(t)
        self.assertEqual(len(self.cart.sent), sent)
        self.assertEqual(self.cart.transport.frame, -1)
        self.assertFalse(self.cart.transport.active)
        self.cart.accept(hello_packet(self.config));self.cart.step(32000)
        self.assertEqual(self.cart.sent[-1][1]['frame'], -1)
        self.cart.accept(state_packet(dict(ready=True), 8, self.config));self.cart.step(32100)
        self.assertTrue(self.cart.transport.active)

    def test_probe_flood_rate_limited_and_exit_stays_quiet_until_resume(self):
        for t in range(0, 1000, 100):
            for _ in range(8):self.cart.accept(hello_packet(self.config))
            self.cart.step(t);self.cart.tx.clear()
        self.assertLessEqual(len(self.cart.sent), 4)
        self.cart.transport.exited = True
        count = len(self.cart.sent)
        self.cart.accept(hello_packet(self.config));self.cart.step(2000)
        self.assertEqual(len(self.cart.sent), count)
        self.cart.transport.resume(self.cart.transport)
        self.cart.accept(hello_packet(self.config));self.cart.step(2100)
        self.assertEqual(len(self.cart.sent), count+1)


class LoopDevice:
    def __init__(self, wire, clock, *, version=2, wrong=False, lose_reply=False, never_reply=False, outage=False):
        self.wire, self.clock, self.version = wire, clock, version
        self.cart = Cartridge(dict(wire.config, token='wrong') if wrong else wire.config)
        self.writes, self.responses, self.loops = [], deque(), 0
        self.lose_reply, self.never_reply = lose_reply, never_reply
        self.outage = outage

    def get_active_configuration(self):return [Interface()]

    def write(self, endpoint, data, **kwargs):
        _, kind, flags, _, seq, tag, sub, _ = HEADER.unpack_from(data)
        frame = Frame(kind, flags, seq, tag, sub, data[HEADER.size:])
        if frame.kind == PING:
            self.responses.append(packet(PONG, struct.pack('<HH', self.version, frame.seq)+bytes(34)))
        else:
            self.writes.append((self.clock[0], frame))
            if frame.kind in (HELLO, STATE) and not (self.outage and 15 <= self.loops <= 40):
                self.cart.accept(data)
        return len(data)

    def read(self, endpoint, size, **kwargs):
        self.loops += 1;self.clock[0] += .1
        self.cart.step(self.clock[0]*1000)
        if self.cart.tx:
            response = self.cart.tx.popleft()
            if not (self.lose_reply or self.never_reply or self.outage and 15 <= self.loops <= 40):
                self.responses.append(response)
            self.lose_reply = False
        if self.loops >= 60:self.wire.stop.set()
        return self.responses.popleft() if self.responses else b''


class DesktopHandshakeTests(unittest.TestCase):
    def run_wire(self, **options):
        clock = [100.0]
        with patch('threading.Thread.start'):
            wire = UsbWire(validate_config({}))
        device = LoopDevice(wire, clock, **options)
        wire.publish(dict(ready=True, phase='idle'), ('test',))
        with patch('talkman_coloplay_usb.time.monotonic', side_effect=lambda:clock[0]), \
             patch('usb.core.find', return_value=[device]), \
             patch('libusb_package.get_libusb1_backend', return_value=object()), \
             patch('usb.util.claim_interface'), patch('usb.util.release_interface'), \
             patch('usb.util.dispose_resources'):
            wire.run()
        events = list(wire.events.queue)
        self.assertFalse([event for event in events if event[0] == 'error'], events)
        return wire, device, events

    def test_passive_cartridge_handshakes_and_recovers_lost_first_reply(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                wire, device, events = self.run_wire(lose_reply=lost)
                self.assertTrue(wire.paired)
                self.assertTrue(device.cart.transport.active)
                kinds = [frame.kind for _, frame in device.writes]
                self.assertEqual(kinds[0], HELLO)
                self.assertIn(STATE, kinds)
                self.assertIn('wake', [e[0] for e in events])
                self.assertTrue(all(f.data and f.kind in (HELLO, STATE) for _, f in device.writes))

    def test_no_response_or_wrong_pair_never_publishes_state(self):
        for options in (dict(wrong=True), dict(never_reply=True)):
            with self.subTest(options=options):
                wire, device, _ = self.run_wire(**options)
                self.assertFalse(wire.paired)
                self.assertTrue(device.writes)
                self.assertTrue(all(f.kind == HELLO for _, f in device.writes))
                times = [t for t, _ in device.writes]
                self.assertTrue(all(b-a >= 1 for a,b in zip(times,times[1:])))

    def test_old_firmware_receives_no_application_probe(self):
        wire, device, _ = self.run_wire(version=1)
        self.assertFalse(wire.v2)
        self.assertFalse(device.writes)

    def test_stale_pair_stops_state_then_reconnects_via_probe(self):
        wire, device, events = self.run_wire(outage=True)
        self.assertTrue(wire.paired and device.cart.transport.active)
        self.assertGreaterEqual(sum(e[0] == 'wake' for e in events), 2)
        middle = [(t, f) for t, f in device.writes if 103.15 <= t <= 104]
        self.assertTrue(middle)
        self.assertTrue(all(f.kind == HELLO for _,f in middle))


if __name__ == '__main__':unittest.main()
