"""卡带版本、500ms 边界与松手事务；不接触真实设备或 Agent。"""
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from websockets.sync.client import connect
from talkman_coloplay_cartridge import CARTRIDGE_BUILD, FILL_HOLD_MS, version_status
from talkman_coloplay_preview import DeviceRuntime, LocalDeviceSession
from talkman_coloplay_server import StateServer
from talkman_coloplay_usb import UsbWire
from talkman_coloplay_protocol import config_id, validate_config
import test_talkman_coloplay as fixture


class TimingTests(unittest.TestCase):
    def runtime(self):
        r=DeviceRuntime();r.step(0,0)
        return r

    def test_500ms_is_fill_boundary_and_hold_alone_never_fills(self):
        for elapsed, expected in ((499,'knob'),(500,'fill'),(900,'fill')):
            with self.subTest(elapsed=elapsed):
                r=self.runtime()
                self.assertEqual(r.step(16,100),['arm_knob'])
                self.assertEqual(r.step(16,100+elapsed),[])
                self.assertEqual(r.step(0,100+elapsed),[expected])
                self.assertEqual(r.step(0,1500),[])

    def test_turn_after_full_ring_cancels_and_exit_stays_five_seconds(self):
        r=self.runtime();r.step(16,100)
        self.assertEqual(r.step(80,700),['cancel_arm'])
        self.assertEqual(r.step(0,900),[])
        self.assertEqual(r.step(1,1000),['arm_ai'])
        self.assertEqual(r.step(1,5999),[])
        self.assertEqual(r.step(1,6000),['exit'])
        self.assertEqual(r.step(0,6100),[])

    def test_send_combo_keeps_original_short_press_window(self):
        r=self.runtime();r.step(8,50);r.step(24,100)
        self.assertEqual(r.step(24,700),[])
        self.assertEqual(r.step(8,700),['send_current'])

    def test_ring_and_hint_finish_at_same_time(self):
        r=self.runtime();r.step(16,100)
        state=dict(ready=True,phase='idle',device_build=CARTRIDGE_BUILD)
        r.render(state,599,pending=True,armed=True)
        self.assertIn('按住以填入',[v['text'] for v in r.text_layout])
        r.render(state,600,pending=True,armed=True)
        self.assertIn('松开填入',[v['text'] for v in r.text_layout])
        self.assertNotIn(f'卡带{CARTRIDGE_BUILD}',[v['text'] for v in r.text_layout])
        r.step(0,650)
        r.step(14,700)
        r.render(state,1700)
        self.assertIn(f'卡带{CARTRIDGE_BUILD}',[v['text'] for v in r.text_layout])
        r.step(0,1800)
        r.render(state,1800)
        self.assertNotIn(f'卡带{CARTRIDGE_BUILD}',[v['text'] for v in r.text_layout])


class VersionTests(unittest.TestCase):
    def test_update_request_requires_authenticated_current_session(self):
        events=queue.Queue();server=StateServer('127.0.0.1',0,'a'*48,'cfg',events)
        try:
            with connect(f'ws://127.0.0.1:{server.port}',proxy=None) as ws:
                ws.send(json.dumps(dict(type='hello',version=1,token='a'*48,config='cfg',device_build=60)))
                ws.recv(timeout=2);_,peer,_=events.get(timeout=2)
                ws.send(json.dumps(dict(type='cartridge_check',id='u1')))
                self.assertEqual(events.get(timeout=2),('cartridge_check',peer,dict(type='cartridge_check',id='u1')))
        finally:server.stop()

    def test_unknown_disconnected_newer_and_simulator_are_not_misreported(self):
        self.assertIn('未连接',version_status(61))
        for value in (None,True,'61',-1,100001):
            self.assertIn('未报告',version_status(value,connected=True))
        self.assertIn(f'可更新至 {CARTRIDGE_BUILD}',version_status(59,connected=True))
        self.assertIn('与本包一致',version_status(CARTRIDGE_BUILD,connected=True))
        self.assertIn('高于本包',version_status(CARTRIDGE_BUILD+1,connected=True))
        self.assertIn('不代表实体',version_status(61,connected=True,simulator=True))

    def test_authenticated_wifi_hello_reports_build_and_old_rom_is_unknown(self):
        for build,expected in ((61,61),(59,59),(None,None),(True,None),('61',None)):
            events=queue.Queue();server=StateServer('127.0.0.1',0,'a'*48,'cfg',events)
            try:
                with connect(f'ws://127.0.0.1:{server.port}',proxy=None) as ws:
                    ws.send(json.dumps(dict(type='hello',version=1,token='a'*48,config='cfg',device_build=build)))
                    ws.recv(timeout=2)
                    kind,peer,actual=events.get(timeout=2)
                    self.assertEqual(kind,'connected');self.assertEqual(actual,expected)
            finally:server.stop()

    def test_usb_rejects_unpaired_version_and_emits_only_changed_build(self):
        wire=object.__new__(UsbWire);wire.config=validate_config({});wire.frames={};wire.last_seq=None;wire.put=Mock()
        message=dict(v=1,token=wire.config['token'],config=config_id(wire.config),seq=1,mask=0,frame=-1,device_build=61)
        wire.validate(dict(message,token='wrong'),0)
        self.assertEqual(wire.put.call_args.args[0],'status')
        self.assertFalse(hasattr(wire,'device_build'))
        wire.put.reset_mock()
        wire.validate(message,0);wire.put.assert_called_once_with('device_build',61)
        wire.validate(message,0);self.assertEqual(wire.put.call_count,1)
        wire.validate(dict(message,device_build=True),0);wire.put.assert_called_with('device_build',None)


class Desktop61Tests(unittest.TestCase):
    setUp=fixture.DesktopTests.setUp
    tearDown=fixture.DesktopTests.tearDown

    def tick(self):
        self.device.tick();self.app.after_cancel(self.device.job);self.device.job=None

    def test_two_quick_record_taps_preserve_start_and_stop(self):
        session=LocalDeviceSession(self.device);self.device.local_session=session
        session.feed(4,100);session.feed(0,120)
        self.assertIsNotNone(self.app.voice)
        session.feed(4,150);session.feed(0,170)
        self.assertTrue(self.app.voice_stopping)

    def test_update_preparation_busy_current_stale_and_duplicate(self):
        d=self.device;d.peer='peer';d.device_build=60
        with patch.object(d,'phase',return_value=('recording','')):
            d.prepare_cartridge_update(dict(id='busy'))
            self.assertEqual(d.cartridge_offer['status'],'error')
        d.device_build=CARTRIDGE_BUILD;d.prepare_cartridge_update(dict(id='current'))
        self.assertEqual(d.cartridge_offer['status'],'current')
        d.device_build=60
        download=Mock(offer=dict(build=61,size=10,sha1='a'*40,url='http://127.0.0.1'),expires=10**12)
        with patch('talkman_coloplay.threading.Thread') as thread,patch('talkman_coloplay_update.CartridgeDownload',return_value=download):
            d.prepare_cartridge_update(dict(id='fresh'));worker=thread.call_args.kwargs['target']
            d.prepare_cartridge_update(dict(id='fresh'));self.assertEqual(thread.call_count,1)
            worker();self.tick();self.assertEqual(d.cartridge_offer['status'],'ready')
            self.assertIs(d.cartridge_download,download)
            late=Mock();d.events.put(('cartridge_ready','stale',('fresh',late,'')));self.tick()
            self.assertIs(d.cartridge_download,download)
            thread.call_args.kwargs['target']();late.close.assert_called_once()
        d.stop_cartridge_download()

    def test_connection_version_clears_and_stale_disconnect_does_not_override(self):
        d=self.device
        d.config=validate_config({**d.config,'connection_mode':'wifi'})
        d.server=Mock()
        d.events.put(('connected','old',59));self.tick()
        self.assertEqual(d.cartridge_version(),59)
        d.events.put(('connected','new',61));d.events.put(('disconnected','old',None));self.tick()
        self.assertEqual(d.cartridge_version(),61)
        d.events.put(('disconnected','new',None));self.tick()
        self.assertIsNone(d.cartridge_version())

    def test_half_second_release_fills_once_and_turn_cancels(self):
        a,d=self.app,self.device;a.set_text('隔离测试文字')
        session=LocalDeviceSession(d);d.local_session=session
        with patch.object(d,'semantic',side_effect=lambda k:'select' if k=='knob' else 'fill' if k=='fill' else None),patch.object(d,'execute') as execute:
            session.feed(0,0);session.feed(16,100);session.feed(16,600)
            execute.assert_not_called()
            session.feed(0,600);execute.assert_called_once_with('fill')
            session.feed(0,700);self.assertEqual(execute.call_count,1)
            session.feed(16,1000);session.feed(80,1550);session.feed(0,1700)
            self.assertEqual(execute.call_count,1)

    def test_export_has_matching_version_and_timing_without_changing_pairing(self):
        d=self.device;before=json.dumps(d.config,sort_keys=True)
        with tempfile.TemporaryDirectory() as folder:
            d.export(Path(folder));runtime=DeviceRuntime()
            runtime.lua.execute((Path(folder)/'TalkMan-连接配置.lua').read_text('utf-8'))
            runtime.lua.execute((Path(folder)/'TalkMan-版本.lua').read_text('utf-8'))
            self.assertEqual(runtime.lua.globals().TM_CONFIG.fill_ms,FILL_HOLD_MS)
            self.assertEqual(runtime.lua.globals().TM_DEVICE_BUILD,CARTRIDGE_BUILD)
            self.assertEqual(runtime.lua.globals().TM_CONFIG.token,d.config['token'])
            self.assertEqual(runtime.lua.globals().TM_CONFIG.config,config_id(d.config))
        self.assertEqual(json.dumps(d.config,sort_keys=True),before)

    def test_settings_opens_with_version_and_manual_update_help(self):
        self.app.open_settings('coloplay');self.app.update_idletasks()
        def widgets(w):
            for child in w.winfo_children():
                yield child;yield from widgets(child)
        buttons=[w for w in widgets(self.app.settings_window) if 'text' in w.keys() and w.cget('text')=='卡带版本与升级说明']
        self.assertEqual(len(buttons),1)
        buttons[0].invoke();self.app.update_idletasks()
        self.assertTrue(any('同时按住右上、右下、左下 5 秒' in str(w.cget('text')) for w in widgets(self.app.settings_window) if 'text' in w.keys()))
        buttons[0].invoke();self.app.update_idletasks()


if __name__=='__main__':unittest.main()
