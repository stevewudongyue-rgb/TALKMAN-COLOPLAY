"""互斥连接、持久化、配对诊断；隔离配置及模拟 USB，不访问真实设备。"""
import json
import queue
import socket
import unittest
from unittest.mock import Mock, patch

from websockets.sync.client import connect
from talkman_coloplay_protocol import config_id, validate_config
from talkman_coloplay_usb import UsbWire
from test_talkman_coloplay import DesktopTests


class ConfigTests(unittest.TestCase):
    def test_migration_and_roundtrip_preserve_pairing(self):
        for old, mode in (({}, 'usb'), ({'enabled': True}, 'wifi'),
                          ({'enabled': True, 'usb_auto': True}, 'usb'),
                          ({'enabled': True, 'usb_auto': False}, 'wifi')):
            with self.subTest(old=old):
                cfg=validate_config(old)
                self.assertEqual(cfg['connection_mode'],mode)
                for next_mode in ('wifi','usb'):
                    new=validate_config({**cfg,'connection_mode':next_mode})
                    self.assertEqual(new['enabled'],next_mode=='wifi')
                    self.assertEqual(new['usb_auto'],next_mode=='usb')
                    self.assertEqual(new['token'],cfg['token'])
                    self.assertEqual(config_id(new),config_id(cfg))
                    self.assertEqual(new,validate_config(json.loads(json.dumps(new))))
        with self.assertRaises(ValueError):validate_config({'connection_mode':'both'})

    def test_pair_failure_is_diagnosed_without_exposing_secret(self):
        wire=object.__new__(UsbWire)
        wire.config=validate_config({});wire.put=Mock();wire.paired=False
        bad=dict(v=1,token='',config='',seq=1,mask=0,frame=-1)
        self.assertIsNone(wire.validate(bad,10))
        self.assertFalse(wire.paired)
        self.assertIn('未配对',wire.put.call_args.args[1])
        wire.validate(bad,11)
        self.assertEqual(wire.put.call_count,1)
        bad['token']='b'*48
        wire.validate(bad,12)
        self.assertIn('不一致',wire.put.call_args.args[1])
        self.assertNotIn('b'*48,str(wire.put.call_args_list))


class ConnectionTests(DesktopTests):
    def test_switch_wifi_closes_usb_and_cannot_automatically_reclaim(self):
        d=self.device
        bridge=Mock();d.usb_bridge=bridge
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        d.select_connection('wifi',host='127.0.0.1',port=port,discovery=False)
        bridge.close.assert_called_once()
        self.assertIsNone(d.usb_bridge)
        self.assertTrue(d.usb_paused)
        self.assertIsNotNone(d.server)
        token=d.config['token'];identity=config_id(d.config)
        with patch('talkman_coloplay_usb.UsbBridge') as factory:
            d.connect_usb();d.connect_usb(automatic=True)
            d.tick();self.app.after_cancel(d.job);d.job=None
            factory.assert_not_called()
        with connect(f'ws://127.0.0.1:{port}') as ws:
            ws.send(json.dumps(dict(type='hello',version=1,token=token,config=identity)))
            self.assertEqual(json.loads(ws.recv(timeout=2))['type'],'state')
        d.select_connection('usb')
        self.assertIsNone(d.server);self.assertIsNone(d.discovery)
        self.assertFalse(d.usb_paused)
        self.assertEqual(d.config['token'],token)
        self.assertEqual(config_id(d.config),identity)
        with socket.socket() as probe:
            probe.settimeout(.2)
            self.assertNotEqual(probe.connect_ex(('127.0.0.1',port)),0)
        saved=validate_config(json.loads(d.path.read_text('utf8')))
        self.assertEqual(saved['connection_mode'],'usb')
        self.assertEqual(saved['host'],'127.0.0.1')

    def test_wifi_bind_failure_does_not_resume_usb(self):
        d=self.device
        with patch('talkman_coloplay.StateServer',side_effect=OSError('address unavailable')):
            d.select_connection('wifi')
        self.assertEqual(d.config['connection_mode'],'wifi')
        self.assertTrue(d.usb_paused)
        self.assertIsNone(d.server)
        self.assertIn('Wi-Fi 服务未启动',d.wifi_error)
        self.assertEqual(json.loads(d.path.read_text('utf8'))['connection_mode'],'wifi')

    def test_failed_save_does_not_disconnect_existing_transport(self):
        d=self.device;bridge=Mock();d.usb_bridge=bridge
        with patch('talkman_coloplay.save_config',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):d.select_connection('wifi')
        bridge.close.assert_not_called()
        self.assertEqual(d.config['connection_mode'],'usb')
        d.usb_bridge=None

    def test_late_wifi_connection_cannot_restore_peer_in_usb_mode(self):
        d=self.device
        d.events.put(('connected','late-wifi',66))
        d.tick();self.app.after_cancel(d.job);d.job=None
        self.assertIsNone(d.peer)
        self.assertIsNone(d.cartridge_version())

    def test_recording_does_not_get_interrupted_by_switch(self):
        d=self.device;self.app.voice=Mock()
        try:
            with self.assertRaisesRegex(ValueError,'结束录音'):d.select_connection('wifi')
            self.assertEqual(d.config['connection_mode'],'usb')
        finally:self.app.voice=None

    def test_ui_has_single_choice_and_surfaces_selected_transport(self):
        from tkinter import ttk
        d=self.device;d.connection_settings();a=self.app;a.update()
        def descendants(w):
            for child in w.winfo_children():
                yield child
                yield from descendants(child)
        radios=[w for w in descendants(a.coloplay_window) if isinstance(w,ttk.Radiobutton)]
        self.assertEqual([w.cget('text') for w in radios],['USB','Wi-Fi'])
        self.assertEqual(str(radios[0].cget('variable')),str(radios[1].cget('variable')))
        with patch('talkman_coloplay.StateServer'),patch('talkman_coloplay.DiscoveryService'):
            radios[1].invoke();a.update_idletasks()
        self.assertEqual(d.config['connection_mode'],'wifi')
        self.assertTrue(d.usb_paused)
        self.assertLess(a.coloplay_window.winfo_reqheight(),760)
        radios[0].invoke()
        self.assertIsNone(d.server)
        self.assertEqual(d.config['connection_mode'],'usb')
        a.coloplay_window.destroy();a.coloplay_window=None


for _name in list(DesktopTests.__dict__):
    if _name.startswith('test_') and _name not in ConnectionTests.__dict__:
        setattr(ConnectionTests,_name,None)
del DesktopTests
