"""新旋钮模式、短期发送确认及结束录音；只用隔离草稿、假麦克风和假目标。"""
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import test_talkman_coloplay as fixtures
from test_talkman_app import FakeVoice
from talkman_coloplay_preview import LocalDeviceSession, DeviceRuntime
from talkman_target import TargetWindow, FillReceipt


class NewGestureTests(unittest.TestCase):
    setUp=fixtures.GestureTests.setUp
    step=fixtures.GestureTests.step

    def test_modifier_upper_left_toggles_once_without_ai_or_exit(self):
        self.step([3],100)
        self.assertEqual(self.step([3,0],200),['toggle'])
        self.assertEqual(self.step([3,0],6000),[])
        self.assertEqual(self.step([0],6200),[])
        self.assertEqual(self.step([],6300),[])

    def test_selection_click_target_turn_and_hold_turn_are_distinct(self):
        self.assertEqual(self.step([4],100),['arm_knob'])
        self.assertEqual(self.step([],200),['knob'])
        self.step([3],300)
        self.assertEqual(self.step([3,6],400),['target_next'])
        self.step([3],500)
        self.assertEqual(self.step([3,5],600),['target_previous'])
        self.step([],700)
        self.assertEqual(self.step([4],800),['arm_knob'])
        self.assertEqual(self.step([4,6],1700),['cancel_arm'])
        self.assertEqual(self.step([],1800),[])


class WifiRecordingTests(unittest.TestCase):
    """执行真实 Lua 主循环，只替换网络、时钟与 USB 输出。"""
    def setUp(self):
        from lupa.lua54 import LuaRuntime
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        root=Path('TalkMan-设备卡带')
        source=(root/'TalkMan-coloplay.clx').read_text('utf-8').split('__lua__\n',1)[1]
        source='\n'.join((root/line.split(' ',1)[1]).read_text('utf-8')
                         if line.startswith('#include ') else line for line in source.splitlines())
        self.lua.execute('''
          clock=0;mask=0;sent=0
          function stat(i) return clock end
          function time() return clock/1000 end
          function usemod(m) end
          function btn(i) return (mask & (1 << i))~=0 end
          function btnpcfg(a,b) end
          function svcset(...) sent=sent+1 end
          function svcreq(...) return true end
          function getcolor(r,g,b) return r*65536+g*256+b end
          function toidle() end
          package.preload.cjson_safe=function() return {encode=function() return '{}' end,decode=function() return {} end} end
        ''')
        self.lua.execute(source+'''
          copas.step=function() end
          ws={};state={ready=true,phase='idle'};received=0
          function test_step(bits,t,arm,phase,ready)
            mask=bits;clock=t;received=t
            state={ready=ready,phase=phase,arm=arm}
            _update()
            return {action=pending and pending.action or '', id=pending and pending.id or '',
              deferred=deferred~=nil, sent=sent, target_commit=target_commit, turns=turns,
              steps=outgoing[#outgoing] and outgoing[#outgoing].steps or 0}
          end
          _init();_update()
        ''')

    def step(self,mask,t,arm='',phase='idle',ready=True):
        return dict(self.lua.globals().test_step(mask,t,arm,phase,ready))

    def test_rapid_second_click_waits_for_start_then_requests_stop_once(self):
        self.assertEqual(self.step(4,100)['action'],'')
        self.step(0,120)
        self.step(4,150)
        self.assertEqual(self.step(0,280)['action'],'record')
        self.assertTrue(self.step(0,330)['deferred'])
        self.assertEqual(self.step(0,360,'1')['sent'],1)
        second=self.step(0,390,phase='opening')
        self.assertEqual((second['action'],second['id']),('record','2'))
        self.assertEqual(self.step(0,420,'2','recording')['sent'],2)
        self.assertEqual(self.step(0,440,phase='processing')['action'],'')

    def test_cancel_disconnect_timeout_and_failed_start_drop_queued_stop(self):
        for reason in ('cancel','disconnect','timeout','failed'):
            with self.subTest(reason=reason):
                self.setUp();self.step(4,100);self.step(0,120);self.step(4,150)
                self.step(0,280);self.step(0,330)
                if reason=='cancel': self.step(2,360);result=self.step(0,540)
                elif reason=='disconnect': result=self.step(0,360,ready=False)
                elif reason=='timeout': result=self.step(0,1300)
                else:
                    self.step(0,360,'1');result=self.step(0,390,phase='idle')
                self.assertFalse(result['deferred'])
                self.assertNotEqual(result['action'],'rewrite')
                if reason=='failed':self.assertEqual(result['action'],'')


class InteractionTests(unittest.TestCase):
    setUp=fixtures.DesktopTests.setUp
    tearDown=fixtures.DesktopTests.tearDown

    def act(self, action):
        d=self.device;d.arm(dict(id='test',action=action));d.dispatch('coloplay_'+action)

    def drain(self):
        self.app.poll_events();self.app.after_cancel(self.app.poll_job);self.app.poll_job=None

    def test_default_cursor_click_select_click_finish_and_unicode(self):
        a,d=self.app,self.device;a.set_text('甲👨‍👩‍👧‍👦乙丙');a.text.mark_set('insert','1.0')
        with patch.object(d,'preview_target') as target:
            self.act('next');self.assertIsNone(a.get_selection());self.assertEqual(a.text.get('1.0','insert'),'甲')
            self.act('knob');self.assertTrue(d.editor_focus.selecting)
            self.act('next');self.assertEqual(a.get_selection()[2],'👨‍👩‍👧‍👦')
            self.act('next');self.assertEqual(a.get_selection()[2],'👨‍👩‍👧‍👦乙')
            self.act('knob');self.assertFalse(d.editor_focus.selecting)
            self.assertEqual(a.get_selection()[2],'👨‍👩‍👧‍👦乙')
            self.act('next');self.assertIsNone(a.get_selection())
            self.assertEqual(a.text.get('1.0','insert'),'甲👨‍👩‍👧‍👦乙')
            target.assert_not_called()

    def test_review_turn_scrolls_without_moving_original_or_target(self):
        a,d=self.app,self.device
        base='原文\n'*90;result='修改后\n'*90
        a.geometry('900x700');a.set_text(base);a.preview_ai_var.set(True)
        a.review_result(base,result,'测试改写',lambda:None);a.update()
        a.ai_review_text.yview_moveto(0)
        cursor=a.text.index('insert');before=a.ai_review_text.yview()[0]
        with patch.object(d,'preview_target') as target:
            self.act('next');self.assertGreater(a.ai_review_text.yview()[0],before)
            self.act('target_previous');self.assertEqual(a.ai_review_text.yview()[0],before)
            target.assert_not_called()
        self.assertEqual(a.text.index('insert'),cursor);self.assertEqual(a.current_text(),base)

    def test_upper_right_after_voice_and_clear_combo_are_undoable(self):
        a=self.app;a.set_text('甲👨‍👩‍👧‍👦');a.text.mark_set('insert','end-1c')
        self.act('cancel_delete');self.assertEqual(a.current_text(),'')
        a.undo();self.assertEqual(a.current_text(),'甲👨‍👩‍👧‍👦')
        session=LocalDeviceSession(self.device)
        session.feed(8,100);session.feed(10,200);session.feed(0,380)
        self.assertEqual(a.current_text(),'');a.undo()
        self.assertEqual(a.current_text(),'甲👨‍👩‍👧‍👦')

    def test_stop_survives_partial_text_and_microphone_ready_transition(self):
        a,d=self.app,self.device
        self.act('record');voice=a.voice
        self.assertEqual(d.phase()[0],'opening')
        d.arm(dict(action='record',id='stop-opening'))
        voice.emit('ready','');voice.emit('partial','实时转写变化');self.drain()
        d.snapshot()  # 真实设备持续取屏幕状态，期间也不能丢失结束预约。
        d.dispatch('coloplay_record')
        self.assertTrue(voice.stopped);self.assertTrue(a.voice_stopping)
        voice.emit('done','实时转写变化');self.drain()
        self.assertEqual(a.current_text(),'实时转写变化')
        self.assertIsNone(a.voice)
        d.dispatch('coloplay_record');self.assertIsNone(a.voice)

    def test_stale_stop_cannot_stop_new_recording(self):
        a,d=self.app,self.device;self.act('record')
        d.arm(dict(action='record',id='old-stop'));a.cancel_operation()
        a.start_voice('insert',from_device=True);new=a.voice
        d.dispatch('coloplay_record');self.assertFalse(new.stopped)

    def prepare_send(self):
        a,d=self.app,self.device
        a.set_text('待填入文字');a.target_controller.close()
        target=TargetWindow(1,2,'测试目标','隔离','test.exe')
        a.target_controller=Mock(selected=target,windows=[target])
        d.set_active(True);a.clear_after_fill_var.set(False)
        d.execute('fill')
        call=a.target_controller.fill_with_receipt.call_args
        self.assertTrue(call.kwargs['allow_send_confirmation'])
        a.target_controller.send_filled.assert_not_called()
        call.args[1]('ok',FillReceipt('已填入','待填入文字','测试目标','隔离','2026-11-01',send_token='test-token'))
        self.drain()
        self.assertTrue(d.device_send_pending())
        a.pending_delivery['ready_at']=0
        return a,d

    def test_fill_then_new_click_sends_once_within_five_seconds(self):
        a,d=self.prepare_send()
        self.assertLessEqual(a.pending_delivery['expires']-time.monotonic(),5)
        self.act('ai');a.target_controller.send_filled.assert_called_once()
        d.dispatch('coloplay_knob');a.target_controller.send_filled.assert_called_once()

    def test_rotation_edit_target_timeout_or_disconnect_cancel_send(self):
        for reason in ('edit','target','timeout','disconnect'):
            with self.subTest(reason=reason):
                a,d=self.prepare_send()
                if reason=='rotate':self.act('previous')
                elif reason=='edit':a.set_text('已改变正文')
                elif reason=='target':a.target_controller.selected=TargetWindow(3,4,'另一个','隔离','x')
                elif reason=='timeout':a.pending_delivery['expires']=0
                else:d.set_active(False)
                self.assertFalse(d.device_send_pending())
                self.act('knob');a.target_controller.send_filled.assert_not_called()

    def test_press_before_receipt_or_hold_cannot_become_send(self):
        a,d=self.prepare_send();session=LocalDeviceSession(d)
        session.feed(16,100);session.feed(0,1100)
        a.target_controller.send_filled.assert_not_called()
        a.target_filling=False;a.disarm_delivery()
        session.feed(16,1200)
        a.pending_delivery=dict(device=True,token='later',expires=time.monotonic()+5,ready_at=0,
            revision=a.revision,text=a.current_text(),target=a.target_controller.selected.session_key())
        session.feed(0,1300)
        a.target_controller.send_filled.assert_not_called()


if __name__=='__main__':unittest.main()
