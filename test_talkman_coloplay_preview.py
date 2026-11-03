"""桌面模拟器与纯 USB 的离线事务测试；不录音、不调用模型、不注入键盘。"""
import io
import json
import queue
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from talkman_coloplay_preview import DeviceRuntime, LocalDeviceSession
from talkman_coloplay_usb import SerialFrames, UsbWire, UsbBridge, jpeg_packet, select_pair, display_matches
from talkman_coloplay_protocol import config_id, validate_config
from talkman_target import TargetWindow
from test_talkman_coloplay import DesktopTests
from test_talkman_app import FakeVoice


class SimulatorTests(DesktopTests):
    # 复用隔离 App fixture，不重复继承的测试。
    def setUp(self):
        super().setUp();self.ready.stop()
        self.app.update_idletasks()
        self.clock=[0.0]
        self.session=LocalDeviceSession(self.device,clock=lambda:self.clock[0])
        self.device.local_session=self.session

    def tearDown(self):
        self.device.local_session=None
        super().tearDown()

    def step(self,mask,ms):
        self.clock[0]=ms/1000
        self.session.feed(mask,ms)

    def target(self):
        self.app.set_text('填入目标的文字')
        self.app.target_controller.close()
        target=TargetWindow(1,2,'豆包','对话','doubao.exe')
        self.app.target_controller=Mock(selected=target,windows=[target])

    def test_sim_fill_transaction_cancel_and_stale_selection(self):
        self.target()
        with patch.object(self.app,'fill_to_target') as fill,patch.object(self.app.target_controller,'cycle') as cycle:
            self.step(16,100);self.step(16|64,900);self.step(16|32,950);self.step(0,1000)
            fill.assert_not_called();cycle.assert_not_called()
            self.step(16,1100);self.step(0,2000)
            fill.assert_called_once_with(paste_only=True,device_confirm=True)
            fill.reset_mock()
            self.step(16,2100);self.app.text.mark_set('insert','1.2');self.step(0,3000)
            fill.assert_not_called()

    def test_sim_ai_review_confirmation_undo_and_one_delete(self):
        a=self.make_review()
        self.step(1,100);self.step(0,200)
        self.assertEqual(a.current_text(),'请把梨拿过来。')
        a.undo();self.assertEqual(a.current_text(),'请把苹果拿过来。')
        a.text.tag_remove('sel','1.0','end');a.text.mark_set('insert','1.1')
        self.step(2,300);self.step(2,900);self.step(0,1000)
        self.assertEqual(a.current_text(),'把苹果拿过来。')

    def test_clear_combo_cancels_fill_and_preserves_undo(self):
        self.target();a=self.app;before=a.current_text()
        a.text.tag_add('sel','1.0','1.2')
        with patch.object(a,'fill_to_target') as fill:
            self.step(16,100)
            self.step(24,200)
            self.step(26,300)
            self.assertEqual(a.current_text(),'')
            self.step(26,1500);self.step(0,1600)
            fill.assert_not_called()
        a.undo();self.assertEqual(a.current_text(),before)

    def test_modifier_screen_labels_clear_and_busy_state(self):
        runtime=self.session.runtime
        self.step(8,100)
        runtime.render(dict(ready=True,phase='idle'),100)
        self.assertIn('清空',[row['text'] for row in runtime.text_layout])
        runtime.step(10,200)
        runtime.render(dict(ready=True,phase='recording'),200)
        self.assertIn('暂不能清空',[row['text'] for row in runtime.text_layout])

    def test_simulator_shift_upper_right_button_clears_all(self):
        from types import SimpleNamespace
        a=self.app;a.set_text('有选区也应清空全部')
        a.text.tag_add('sel','1.0','1.2')
        self.device.local_session=None
        self.device.open_simulator();sim=self.device.simulator
        try:
            sim.press(SimpleNamespace(x=440,y=145,state=1))
            sim.release()
            self.assertEqual(a.current_text(),'')
            a.undo();self.assertEqual(a.current_text(),'有选区也应清空全部')
        finally:
            sim.close();self.device.local_session=self.session

    def test_sim_combination_preserves_selection_and_cancel(self):
        a=self.app;a.set_text('把苹果拿过来');a.text.tag_add('sel','1.1','1.3')
        self.step(8,100);self.step(12,200)
        self.assertEqual(a.voice_mode,'rewrite')
        self.assertEqual(a.get_selection()[2],'苹果')
        FakeVoice.sessions[-1].emit('ready','');a.poll_events();a.after_cancel(a.poll_job);a.poll_job=None
        self.step(8,300);self.step(0,350);self.step(2,400)
        self.assertIsNone(a.voice);self.assertEqual(a.current_text(),'把苹果拿过来')

    def test_device_recording_continues_when_target_takes_focus(self):
        a=self.app;a.set_text('当前正文')
        self.step(4,100);self.step(0,200)
        FakeVoice.sessions[-1].emit('ready','');a.poll_events();a.after_cancel(a.poll_job);a.poll_job=None
        with patch.object(a,'focus_displayof',return_value=None):a.check_focus()
        self.assertFalse(a.voice_stopping)
        self.assertIsNotNone(a.voice)
        self.step(4,300);self.step(0,400)
        self.assertTrue(a.voice_stopping)

    def test_rotation_view_tracks_confirmed_target_timeout_and_error(self):
        self.target()
        windows=[TargetWindow(1,2,'Codex','私密标题一','codex.exe'),TargetWindow(2,3,'豆包','私密标题二','doubao.exe')]
        controller=self.app.target_controller
        controller.windows=windows;controller.selected=windows[0];controller.label='豆包'
        with patch('talkman_coloplay.time.monotonic',return_value=10):
            self.device.execute('target_next')
            state=self.device.snapshot()
            self.assertTrue(state['target_view']);self.assertTrue(state['target_waiting'])
            self.assertFalse(state['cycling']);controller.cycle.assert_not_called()
            self.assertEqual(state['target_choice'],'豆包')
            self.assertEqual(state['target'],'Codex')
            self.assertNotIn('私密标题',json.dumps(state,ensure_ascii=False))
        with patch('talkman_coloplay.time.monotonic',return_value=10.7):
            self.device.active=True;self.device.commit_target()
            self.assertTrue(self.app.input_picker.active)
            callback=controller.begin_inputs.call_args.args[1];callback('error','无法识别位置')
            self.app.after_cancel(self.app.input_picker.job);self.app.input_picker.tick()
            state=self.device.snapshot()
            self.assertTrue(state['input_selecting']);self.assertTrue(state['input_error'])
            self.assertEqual(state['input_target'],'豆包');self.assertEqual(state['target'],'Codex')
            self.assertNotIn('私密标题',json.dumps(state,ensure_ascii=False))
            self.app.input_picker.cancel()
        with patch('talkman_coloplay.time.monotonic',return_value=13):
            self.assertFalse(self.device.snapshot()['target_view'])
            self.device.execute('target_next')
        with patch('talkman_coloplay.time.monotonic',return_value=13.7):
            self.device.commit_target()
            callback=controller.begin_inputs.call_args.args[1];callback('error','无法激活')
            self.app.after_cancel(self.app.input_picker.job);self.app.input_picker.tick()
            self.assertTrue(self.device.snapshot()['input_error'])
            self.assertEqual(self.device.snapshot()['target'],'Codex')
            self.device.execute('input_cancel')
            self.assertFalse(self.device.snapshot()['target_view'])

    def test_target_pages_and_preview_use_agent_only_order(self):
        self.target()
        windows=[TargetWindow(i,2,'Codex','私密对话'+str(i),'codex.exe') for i in range(1,8)]
        other=TargetWindow(20,2,'记事本','私密笔记','notepad.exe')
        controller=self.app.target_controller
        controller.windows=list(reversed(windows))+[other]
        for index in range(7):
            controller.selected=windows[index]
            state=self.device.snapshot()
            self.assertEqual(state['target_count'],7)
            self.assertEqual(state['target_index'],index+1)
            self.assertEqual(state['target_page'],index//4+1)
            self.assertEqual(state['target_pages'],2)
            self.assertEqual(state['target_page_start'],index//4*4+1)
            self.assertEqual(state['target_page_names'],
                ['Codex ('+str(i+1)+')' for i in range(index//4*4,min(7,index//4*4+4))])
            self.assertNotIn('私密',json.dumps(state,ensure_ascii=False))
        self.device.execute('target_next')
        self.assertEqual(self.device.snapshot()['target_index'],1)
        self.assertEqual(self.device.snapshot()['target_page'],1)
        controller.cycle.assert_not_called()
        self.assertEqual(self.device.target_preview.session_key(),windows[0].session_key())
        self.device.cancel_navigation();controller.selected=windows[0]
        self.device.execute('target_previous')
        self.assertEqual(self.device.snapshot()['target_index'],7)
        self.assertEqual(self.device.snapshot()['target_page'],2)
        self.device.cancel_navigation();controller.windows=[];controller.selected=None
        state=self.device.snapshot()
        self.assertEqual(state['target_page_names'],[])
        self.assertEqual((state['target_page'],state['target_pages']),(0,0))

    def test_fast_rotation_waits_for_release_and_blocks_fill(self):
        self.target();c=self.app.target_controller;d=self.device
        c.windows=[TargetWindow(i,2,'Codex',str(i),'codex.exe') for i in range(1,8)]
        c.selected=c.windows[0];d.active=True
        for t in [20,20.1,20.2,20.3,20.4]:
            with patch('talkman_coloplay.time.monotonic',return_value=t):
                d.execute('target_next')
                self.assertIsNone(d.semantic('fill'))
        c.cycle.assert_not_called()
        self.assertEqual(d.snapshot()['target_index'],6)
        with patch('talkman_coloplay.time.monotonic',return_value=21):d.snapshot()
        c.cycle.assert_not_called()
        with patch('talkman_coloplay.time.monotonic',return_value=21.06):d.commit_target()
        c.begin_inputs.assert_called_once()
        self.assertEqual(c.begin_inputs.call_args.args[0],c.windows[5])
        callback=c.begin_inputs.call_args.args[1]
        with patch('talkman_coloplay.time.monotonic',return_value=21.1):d.execute('target_previous')
        self.assertFalse(self.app.input_picker.active)
        c.cancel_inputs.assert_called_once()
        self.assertEqual(d.snapshot()['target_index'],5)
        callback('error','已取消')
        with patch('talkman_coloplay.time.monotonic',return_value=21.2):d.tick()
        self.app.after_cancel(d.job);d.job=None
        self.assertFalse(d.target_view_error)
        d.cancel_navigation()
        with patch('talkman_coloplay.time.monotonic',return_value=23):d.commit_target()
        self.assertEqual(c.begin_inputs.call_count,1)

    def test_pause_or_disconnect_cancels_pending_target_switch(self):
        self.target();d=self.device;c=self.app.target_controller
        d.active=True
        with patch('talkman_coloplay.time.monotonic',return_value=30):d.execute('target_next')
        with patch('talkman_coloplay.time.monotonic',return_value=31),patch.object(d,'paused',return_value=True):d.commit_target()
        c.cycle.assert_not_called();self.assertIsNone(d.target_preview)
        with patch('talkman_coloplay.time.monotonic',return_value=32):d.execute('target_next')
        self.session.reset()
        with patch('talkman_coloplay.time.monotonic',return_value=33):d.commit_target()
        c.cycle.assert_not_called()

    def test_first_scan_only_previews_and_then_waits(self):
        self.target();d=self.device;c=self.app.target_controller
        c.windows=[];c.selected=None;d.active=True
        with patch('talkman_coloplay.time.monotonic',return_value=40):
            d.execute('target_next');d.execute('target_next')
            c.refresh.assert_called_once();c.cycle.assert_not_called()
        c.windows=[TargetWindow(i,2,'Codex',str(i),'codex.exe') for i in range(1,5)]
        c.refresh.call_args.args[0]('ok',c.windows)
        with patch('talkman_coloplay.time.monotonic',return_value=41):
            d.tick();self.app.after_cancel(d.job);d.job=None
            self.assertEqual(d.snapshot()['target_index'],2)
            c.cycle.assert_not_called()
        with patch('talkman_coloplay.time.monotonic',return_value=41.7):d.commit_target()
        self.assertEqual(c.begin_inputs.call_args.args[0],c.windows[1])

    def test_simulator_opens_from_settings_without_remaining_paused(self):
        self.app.open_settings()
        win=self.app.settings_window
        def descendants(w):
            for child in w.winfo_children():
                yield child
                yield from descendants(child)
        entry=next(w for w in descendants(win) if 'text' in w.keys() and w.cget('text')=='设备模拟器')
        entry.invoke();self.app.update_idletasks()
        self.assertFalse(win.winfo_exists())
        self.assertIsNotNone(self.device.simulator)
        self.assertFalse(self.device.paused())
        self.assertFalse(any('text' in w.keys() and w.cget('text')=='设备模拟器'
                            for w in descendants(self.app) if w.winfo_toplevel()==self.app))
        self.device.simulator.close()

    def test_sim_toggle_exit_and_hid_cannot_enter_local_session(self):
        self.device.set_active(True)
        self.step(8,100);self.step(9,150);self.step(0,200)
        self.assertTrue(self.device.hidden)
        self.step(8,300);self.step(9,350);self.step(0,400)
        self.assertFalse(self.device.hidden)
        self.assertFalse(self.device.ready())
        with patch.object(self.device,'backward_delete') as delete:
            self.device.arm(dict(action='cancel_delete',id='old'))
            self.device.dispatch('coloplay_cancel_delete');delete.assert_not_called()
        self.step(1,500);self.step(1,5500);self.step(0,5600)
        self.assertFalse(self.session.enabled);self.assertFalse(self.device.active)

    def test_sim_actual_canvas_click_drag_and_modifier(self):
        self.device.local_session=None
        self.device.open_simulator();sim=self.device.simulator
        self.app.update()
        e=lambda x,y:SimpleNamespace(x=x,y=y,state=0)
        with patch.object(self.device,'execute') as execute:
            sim.press(e(440,440));sim.release()
            self.assertEqual(sim.mask,0)
            execute.assert_not_called(); execute.reset_mock()
            sim.press(SimpleNamespace(x=80,y=440,state=1));sim.release()
            execute.assert_called_once_with('rewrite');self.assertEqual(sim.mask,0)
            execute.reset_mock()
            sim.press(e(410,290));sim.drag(e(365,395))
            with patch.object(sim.win,'focus_displayof',return_value=None):sim.check_focus()
            self.assertEqual(sim.hit,'rim')
            sim.drag(e(265,440));sim.release()
            self.assertTrue(any(call.args==('move_next',) for call in execute.call_args_list))
            execute.reset_mock()
            sim.press(e(345,290));sim.drag(e(265,380));sim.release()
            execute.assert_not_called()
        sim.close();self.assertIsNone(self.device.local_session)

    def test_sim_pause_and_lost_focus_cancel_hold(self):
        self.target();self.device.local_session=None;self.device.open_simulator()
        sim=self.device.simulator
        with patch.object(self.app,'fill_to_target') as fill:
            sim.press(SimpleNamespace(x=265,y=290,state=0))
            sim.abort();sim.release();fill.assert_not_called()
            with patch.object(self.device,'paused',return_value=True):
                sim.press(SimpleNamespace(x=80,y=440,state=0));sim.release()
                self.assertIsNone(self.app.voice)
        sim.close()

    def test_expected_focus_transfer_keeps_navigation_but_cancels_fill(self):
        self.target();self.device.local_session=None;self.device.open_simulator()
        sim=self.device.simulator;d=self.device
        d.cycling=True;d.target_cancel.clear();generation=d.target_generation
        sim.session.lease.arm('local','fill','stamp','fill');sim.mask=16
        with patch.object(sim.win,'focus_displayof',return_value=None):sim.check_focus()
        self.assertEqual(d.target_generation,generation)
        self.assertFalse(d.target_cancel.is_set())
        self.assertFalse(sim.session.lease.id());self.assertEqual(sim.mask,0)
        d.cycling=False;d.target_due=123
        with patch.object(sim.win,'focus_displayof',return_value=None):sim.check_focus()
        self.assertEqual(d.target_due,0)
        self.assertGreater(d.target_generation,generation)
        sim.close()

    def test_usb_bridge_rejects_old_display_and_recovers_from_disconnect(self):
        wire=Mock();wire.events=queue.Queue()
        self.device.local_session=None
        with patch('talkman_coloplay_usb.UsbWire',return_value=wire):
            bridge=UsbBridge(self.device)
        def packet(mask,stamp=None,kind='buttons',delay=0):
            wire.events.put((kind,dict(mask=mask,stamp=self.device.stamp() if stamp is None else stamp),time.monotonic()-delay))
            bridge.poll()
        try:
            a=self.app;a.set_text('保留正文');a.text.mark_set('insert','1.0')
            packet(0,kind='reset')
            self.assertTrue(bridge.session.enabled)
            old=self.device.stamp();a.text.mark_set('insert','1.2')
            packet(2,old);packet(0)
            self.assertEqual(a.current_text(),'保留正文')
            packet(2,delay=1);packet(0)
            self.assertEqual(a.current_text(),'保留正文')
            packet(2);packet(0)
            self.assertEqual(a.current_text(),'保正文')
            bridge.last_input=time.monotonic()-2;bridge.poll()
            self.assertFalse(bridge.session.enabled)
            packet(2);packet(0) # 断线恢复时先松开，不补发旧按键。
            self.assertEqual(a.current_text(),'保正文')
            packet(2);packet(0)
            self.assertEqual(a.current_text(),'正文')
            wire.events.put(('exit',None,time.monotonic()));bridge.poll()
            packet(2);self.assertEqual(a.current_text(),'正文')
            self.assertFalse(bridge.streaming)
        finally:bridge.close()

    def test_usb_then_simulator_is_exclusive(self):
        self.device.local_session=None
        with patch('talkman_coloplay_usb.UsbWire') as wire:
            wire.return_value.events=queue.Queue()
            self.device.connect_usb()
            old=self.device.usb_bridge
            self.device.open_simulator()
            self.assertTrue(old.closed)
            self.assertIsNone(self.device.usb_bridge)
            self.assertIs(self.device.local_session,self.device.simulator.session)
            self.device.simulator.close()


# unittest 收集 fixture 基类的原测试会重复；只收新测试。
for _name in list(DesktopTests.__dict__):
    if _name.startswith('test_') and _name not in SimulatorTests.__dict__:
        setattr(SimulatorTests,_name,None)
del DesktopTests


class UsbProtocolTests(unittest.TestCase):
    def test_rotation_accepts_pending_flag_change_but_not_stale_edits(self):
        base=(1,'文字',2,3,'idle',(10,20),None,'1.0',False,False,None)
        pending=base[:8]+(True,)+base[9:]
        for mask in (0,8,40,72,104):self.assertTrue(display_matches(base,pending,mask))
        for mask in (1,2,4,16,32,64):self.assertFalse(display_matches(base,pending,mask))
        self.assertFalse(display_matches(base,(2,'新文字')+pending[2:],64))

    def test_split_coalesced_noise_invalid_and_oversized_serial(self):
        p=SerialFrames()
        self.assertEqual(p.feed(b'log\nTMCP1 {"mask":'),[])
        self.assertEqual(p.feed(b'0}\nTMCP1 {"mask":1}\n'),[{'mask':0},{'mask':1}])
        self.assertEqual(p.feed(b'TMCP1 invalid\nTMCP1 '+b'x'*3000),[])
        self.assertEqual(p.feed(b'\nTMCP1 {"mask":2}\n'),[{'mask':2}])

    def test_jpeg_header_crc_frame_wrap_and_dimensions(self):
        import binascii
        raw=jpeg_packet(Image.new('RGB',(240,240),'green'),65537,65536)
        h=struct.unpack('<IHHIHHHH',raw[:20])
        self.assertEqual(h[:3],(0x53555843,1,1));self.assertEqual(h[3],len(raw)-20)
        self.assertEqual(h[4:7],(1,240,240))
        self.assertEqual(h[7],binascii.crc_hqx(raw[20:],0xffff))
        self.assertEqual(Image.open(io.BytesIO(raw[20:])).size,(240,240))
        with self.assertRaises(ValueError):jpeg_packet(Image.new('RGB',(240,240)),1,10)

    def test_serial_auth_fresh_image_replay_gap_and_types(self):
        wire=UsbWire.__new__(UsbWire);wire.config=validate_config({});wire.frames={100:10};wire.last_seq=None
        m=dict(v=1,token=wire.config['token'],config=config_id(wire.config),seq=1,mask=0,frame=100)
        self.assertEqual(wire.validate(m,10.1),('reset',0))
        self.assertIsNone(wire.validate(m,10.1))
        m['seq']=2;m['mask']=16
        self.assertEqual(wire.validate(m,10.2),('buttons',16))
        self.assertIsNone(wire.validate(dict(m,seq=3,token='bad'),10.3))
        self.assertIsNone(wire.validate(dict(m,seq=3,mask=True),10.3))
        self.assertIsNone(wire.validate(dict(m,seq=3),12))
        self.assertEqual(wire.validate(dict(m,seq=4),10.4),('reset',16))
        old=(1,'刚',5,8,'recording');new=(2,'刚才',5,8,'recording')
        self.assertTrue(display_matches(old,new,4))
        self.assertTrue(display_matches(old,new,2))
        self.assertFalse(display_matches(old,new[:4]+('idle',),2))
        self.assertFalse(display_matches(old,(2,'新一轮',6,8,'recording'),4))

    def test_unique_device_pair_and_wrong_identity(self):
        dev=lambda s:SimpleNamespace(serial_number=s)
        port=lambda s:SimpleNamespace(serial_number=s,vid=0x303a,pid=0x4005)
        d,p=dev('one'),port('one')
        self.assertEqual(select_pair([d],[p]),(d,p))
        for ds,ps in [([d],[port('two')]),([d,dev('two')],[p,port('two')])]:
            with self.assertRaises(ValueError):select_pair(ds,ps)

    def test_lua_usb_cartridge_emits_mask_and_frame_without_network_or_hid(self):
        from lupa.lua54 import LuaRuntime
        from pathlib import Path
        import importlib.util
        spec=importlib.util.spec_from_file_location('builder','TalkMan-构建卡带.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        src=builder.flatten(Path('TalkMan-设备卡带/TalkMan-usb.clx')).split('__lua__\n',1)[1]
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
        timer=0;mask=0;frames={};requests={}
        function stat(i) return timer end
        function btn(i) return mask & (1<<i) ~= 0 end
        function btnpcfg() end
        function getcolor(r,g,b) return 0 end
        function img_airinfo() return 240,240,7,1000,20 end
        function toidle() exited=true end
        function svcreq(kind,data) requests[#requests+1]={kind,data} end
        package.preload.cjson_safe=function() return {encode=function(t) frames[#frames+1]=t;return '{}' end} end
        ''')
        lua.execute(src);lua.execute('_init();_update();timer=100;mask=4;_update()')
        frames=lua.globals().frames
        self.assertEqual(frames[1].mask,0);self.assertEqual(frames[2].mask,4)
        self.assertEqual(frames[2].frame,7)
        self.assertTrue(all(v[1]=='ex' and v[2].startswith('usb:tx:TMCP1 ') for v in lua.globals().requests.values()))
        lua.execute('timer=200;mask=1;_update();timer=5300;_update()')
        self.assertTrue(lua.globals().exited)
        self.assertTrue(frames[len(frames)]['exit'])


class ScreenTests(unittest.TestCase):
    def test_target_name_fits_by_real_font_width_and_selector_replaces_idle(self):
        r=DeviceRuntime();r.step(0,0)
        state=dict(ready=True,phase='idle',title='就绪',target='WWW Claude Code 研究助手 (12)',
                   target_view=True,target_page_names=['Codex','WWW Claude Code 研究助手 (12)','豆包','千问'],
                   target_page_start=1,target_page=1,target_pages=2,target_index=2,target_count=7)
        r.render(state,1000)
        rows=r.text_layout
        chosen=next(row for row in rows if row['y']==59 and row['x']>120)
        self.assertLessEqual(chosen['width'],80)
        self.assertAlmostEqual(chosen['x']+chosen['width']/2,172,delta=1)
        self.assertFalse(any(row['text'] in ('就绪','待录入','改写','录入') for row in rows))
        self.assertTrue(any(row['text']=='Codex' for row in rows))
        self.assertTrue(any(row['text']=='豆包' for row in rows))
        self.assertTrue(any('1/2 页' in row['text'] for row in rows))

    def test_outer_cursor_matches_four_corners_and_second_page(self):
        r=DeviceRuntime();r.step(0,0)
        state=dict(ready=True,phase='idle',target_view=True,target_count=7,
                   target_page_names=['Codex','Cursor','豆包','千问'],
                   target_page=1,target_pages=2,target_page_start=1)
        for index in range(1,5):
            frame=r.render(dict(state,target_index=index),1000)
            x,y=[(68,59),(172,59),(172,161),(68,161)][index-1]
            self.assertEqual(frame.getpixel((x-42,y-5)),(92,255,139))
            ox,oy=[(39,39),(201,39),(201,201),(39,201)][index-1]
            self.assertTrue(any(frame.getpixel((ox+dx,oy+dy))==(92,255,139)
                                for dx in range(-2,3) for dy in range(-2,3)))
            self.assertNotEqual(frame.getpixel((120,120)),(183,255,204))
            names=[row for row in r.text_layout if row['text'] in state['target_page_names']]
            self.assertEqual(len(names),4)
            self.assertEqual([row['text'] for row in names if row['size']==17],
                             [state['target_page_names'][index-1]])
        frame=r.render(dict(state,target_index=5,target_page_start=5,target_page=2,
                            target_page_names=['通义','智谱','文心']),1000)
        text=[row['text'] for row in r.text_layout]
        self.assertTrue(any('2/2 页' in row for row in text))
        self.assertNotIn('Codex',text)
        self.assertEqual(frame.getpixel((26,54)),(92,255,139))

    def test_waiting_is_distinct_from_switching_and_confirmation(self):
        r=DeviceRuntime();r.step(0,0)
        base=dict(ready=True,phase='idle',target_view=True,target_index=2,target_count=4,
                  target_page_names=['Codex','豆包','千问','Cursor'],target_page_start=1)
        for state,expected in [(dict(target_waiting=True,target_progress=.5),'松开后选位置'),
                               (dict(cycling=True,target_choice='豆包'),'正在切换'),({},'旋转选目标')]:
            r.render(dict(base,**state),1000)
            self.assertIn(expected,[row['text'] for row in r.text_layout])

    def test_empty_and_failed_targets_do_not_show_successful_progress(self):
        r=DeviceRuntime();r.step(0,0)
        for state,expected in [
            (dict(target_index=0,target_count=0,target_page_names=[]),'未选目标'),
            (dict(target_error=True,target='Codex',target_index=2,target_count=4),'未能切换')]:
            r.render(dict(ready=True,phase='idle',target_view=True,**state),1000)
            text=[row['text'] for row in r.text_layout]
            self.assertIn(expected,text)
            self.assertFalse(any(' 页' in row for row in text))

    def test_rain_moves_only_when_recording_and_wordmark_stays_still(self):
        from PIL import ImageChops
        r=DeviceRuntime();r.step(0,0)
        state=dict(ready=True,phase='recording',title='录音中',target='豆包')
        first=r.render(state,1100);second=r.render(state,1500)
        diff=ImageChops.difference(first,second)
        self.assertIsNotNone(diff.crop((35,78,205,132)).getbbox())
        self.assertIsNone(diff.crop((90,15,150,33)).getbbox())
        state.update(phase='idle',title='就绪')
        diff=ImageChops.difference(r.render(state,1100),r.render(state,1500))
        self.assertIsNone(diff.crop((35,78,205,132)).getbbox())


if __name__=='__main__':unittest.main()
