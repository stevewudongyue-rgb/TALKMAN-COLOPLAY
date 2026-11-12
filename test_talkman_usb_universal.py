"""Universal ROM sessions against real Lua; all native hardware APIs are fake."""
import json
import unittest

from talkman_airdata import HELLO, packet
from talkman_coloplay_protocol import validate_config, config_id
from talkman_coloplay_usb import hello_packet, state_packet
from test_talkman_usb_handshake import Cartridge, PassiveTests


class UniversalIdleTests(PassiveTests):
    def setUp(self):
        self.config = validate_config({})
        self.cart = Cartridge(self.config, universal=True)

    def test_state_timeout_quiets_device_and_next_probe_can_reconnect(self):
        self.cart.accept(hello_packet(self.config))
        super().test_state_timeout_quiets_device_and_next_probe_can_reconnect()


class UniversalTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = validate_config({}), validate_config({})
        self.cart = Cartridge(self.a, universal=True, stored=self.b)

    def connect(self, config, t=0, frame=7):
        self.cart.accept(hello_packet(config));self.cart.step(t)
        self.cart.tx.clear()
        self.cart.accept(state_packet(dict(ready=True), frame, config));self.cart.step(t+100)
        self.assertTrue(self.cart.transport.active)
        self.assertEqual(self.cart.transport.token, config['token'])

    def test_first_hello_at_zero_and_wifi_pair_remains_separate(self):
        self.connect(self.a)
        self.assertEqual(self.cart.sent[0][1]['frame'], -1)
        self.assertEqual(self.cart.sent[0][1]['token'], self.a['token'])
        self.assertEqual(self.cart.cfg.token, self.b['token'])
        self.assertEqual(self.cart.cfg.config, config_id(self.b))
        self.assertEqual(self.cart.cfg.url, 'ws://192.0.2.1:18743')
        self.assertIsNone(self.cart.transport.audio)

    def test_state_without_hello_cannot_connect_even_with_stored_wifi_pair(self):
        for config in (self.a, self.b):self.cart.accept(state_packet(dict(ready=True), 7, config))
        self.cart.step(0)
        self.assertFalse(self.cart.transport.active)
        self.assertFalse(self.cart.sent)

    def test_invalid_hello_never_enables_state_or_controls(self):
        good=dict(protocol='TM_USB_HELLO_1',v=1,token=self.a['token'],config=config_id(self.a))
        changes=[dict(v=2),dict(v=True),dict(protocol='other'),dict(token=''),dict(token=1),
                 dict(token='a'*47),dict(token='a'*49),dict(token='g'*48),dict(token='A'*48),
                 dict(config='b'*15),dict(config='b'*17),dict(config='z'*16),dict(config=False)]
        for i,change in enumerate(changes):
            with self.subTest(change=change):
                self.cart.accept(packet(HELLO,json.dumps(good|change).encode()))
                self.cart.accept(state_packet(dict(ready=True),7,self.a));self.cart.step(i*100)
                self.assertFalse(self.cart.transport.active)
                self.assertFalse(self.cart.sent)

    def test_same_rom_can_move_between_computers_after_unplug(self):
        self.connect(self.a)
        self.cart.ready=False;self.cart.step(150)
        self.assertEqual(self.cart.transport.token,'')
        self.assertEqual(self.cart.transport.frame,-1)
        self.cart.tx.clear();self.cart.ready=True
        self.connect(self.b,200,frame=12)
        self.cart.accept(state_packet(dict(ready=True,target='old'),99,self.a));self.cart.step(350)
        self.assertEqual(self.cart.transport.frame,12)
        self.assertIsNone(self.cart.transport.state.target)
        self.cart.mask=8;self.cart.step(400)
        self.assertEqual(self.cart.sent[-1][1]['token'],self.b['token'])
        self.assertEqual(self.cart.sent[-1][1]['mask'],8)

    def test_active_session_rejects_other_hello_and_state(self):
        self.connect(self.a)
        self.cart.accept(hello_packet(self.b))
        self.cart.accept(state_packet(dict(ready=True,target='other'),12,self.b));self.cart.step(200)
        self.assertEqual(self.cart.transport.token,self.a['token'])
        self.assertEqual(self.cart.transport.frame,7)

    def test_lease_expiry_allows_another_pc_even_if_firmware_stays_ready(self):
        self.connect(self.a)
        self.cart.tx.clear()
        self.connect(self.b,1700,frame=12)
        self.assertEqual(self.cart.sent[-1][1]['frame'],12)

    def test_stale_state_cannot_revive_expired_or_resumed_session(self):
        self.connect(self.a)
        self.cart.accept(state_packet(dict(ready=True),8,self.a));self.cart.step(1600)
        self.assertFalse(self.cart.transport.active)
        self.assertEqual(self.cart.transport.token,'')
        self.connect(self.a,1700)
        self.cart.transport.resume(self.cart.transport)
        self.cart.accept(state_packet(dict(ready=True),9,self.a));self.cart.step(1850)
        self.assertFalse(self.cart.transport.active)

    def test_probe_without_state_holds_only_short_lease(self):
        self.cart.accept(hello_packet(self.a));self.cart.step(0)
        self.cart.accept(hello_packet(self.b));self.cart.step(1000)
        self.assertEqual(self.cart.transport.token,self.a['token'])
        self.connect(self.b,1500)

    def test_audio_requires_current_session_explicit_start_and_stops_on_pc_change(self):
        self.cart.lua.execute('''
          loads=0;clears=0
          function usemod(name) assert(name=='mic');loads=loads+1;return true end
          function micpoll(mode) if mode==1 then clears=clears+1 end;return nil end
        ''')
        self.connect(self.a)
        def command(config, session='a'*32):
            self.cart.audio_rx.append(json.dumps(dict(protocol='TM_AUDIO_1',action='start',
                token=config['token'],config=config_id(config),session=session,tag=77)))
        command(self.b);self.cart.step(150)
        self.assertEqual(self.cart.lua.globals().loads,0)
        command(self.a);self.cart.step(200)
        self.assertEqual(self.cart.lua.globals().loads,1)
        self.assertIsNotNone(self.cart.transport.audio)
        # Switching after a lost link also invalidates an old queued start.
        command(self.a,session='c'*32)
        self.connect(self.b,1700,frame=12)
        self.assertIsNone(self.cart.transport.audio)
        self.assertEqual(self.cart.lua.globals().loads,1)
        self.assertIn('stopped',[m['action'] for kind,m in self.cart.sent if kind==18])
        command(self.b,session='b'*32);self.cart.step(1850)
        self.assertEqual(self.cart.lua.globals().loads,2)
        self.cart.ready=False;self.cart.step(1900)
        self.assertIsNone(self.cart.transport.audio)


if __name__=='__main__':unittest.main()
