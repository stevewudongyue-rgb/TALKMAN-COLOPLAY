"""松手选目标、发送隔离阶段、贴边编辑会话及首次教程。全部使用假音频和假目标。"""
import time
import sys
import unittest
from unittest.mock import Mock, patch
from talkman_coloplay_preview import LocalDeviceSession, DeviceRuntime
from talkman_coloplay_usb import display_matches
from talkman_target import TargetWindow, TargetError
import test_talkman_coloplay as fixtures
import test_talkman_coloplay_revision42 as previous
import test_talkman_delivery_confirm as delivery
from test_talkman_target import wait_result
import test_talkman_window as window
from talkman_coloplay_guide import PAGES


class Gesture43Tests(unittest.TestCase):
    setUp=fixtures.GestureTests.setUp
    step=fixtures.GestureTests.step

    def test_short_combo_sends_only_after_center_release(self):
        self.step([3],100)
        self.assertEqual(self.step([3,4],150),['arm_send_current'])
        self.assertEqual(self.step([3],250),['send_current'])
        self.assertEqual(self.step([],300),[])

    def test_combo_hold_turn_reverse_or_simultaneous_never_sends(self):
        for rows in ([(8,100),(24,150),(24,1000),(8,1100),(0,1200)],
                     [(8,100),(24,150),(88,200),(8,300),(0,400)],
                     [(16,100),(24,150),(8,200),(0,300)],
                     [(24,100),(8,200),(0,300)],
                     [(8,100),(72,150),(8,175),(24,200),(8,300),(0,350)]):
            runtime=DeviceRuntime();runtime.step(0,0)
            out=[a for mask,t in rows for a in runtime.step(mask,t)]
            self.assertNotIn('send_current',out);self.assertNotIn('fill',out)

    def test_release_selects_final_candidate_once(self):
        self.step([3],100);self.step([3,6],150);self.step([3],200)
        self.assertEqual(self.step([3],4000),[])
        self.assertEqual(self.step([],4050),['target_commit'])
        self.assertEqual(self.step([],4100),[])
        self.step([3],4200);self.assertEqual(self.step([],4300),[])


class Interaction43Tests(unittest.TestCase):
    setUp=fixtures.DesktopTests.setUp
    tearDown=fixtures.DesktopTests.tearDown
    act=previous.InteractionTests.act
    drain=previous.InteractionTests.drain
    prepare_send=previous.InteractionTests.prepare_send

    def test_confirmation_survives_knob_modifier_and_focus_reset_then_left_sends(self):
        a,d=self.prepare_send();s=LocalDeviceSession(d)
        for mask,t in ((64,100),(0,150),(8,200),(72,250),(8,300),(0,350),(16,400),(0,450)):
            s.feed(mask,t)
        self.assertTrue(d.device_send_pending());self.assertEqual(a.current_text(),'待填入文字')
        s.reset();s.feed(0,480);s.feed(1,500)
        d.editor_focus.leave();s.feed(0,600)
        a.target_controller.send_filled.assert_called_once()
        self.assertIsNone(a.pending_delivery)

    def test_right_upper_cancels_send_without_deleting_or_filling(self):
        a,d=self.prepare_send();s=LocalDeviceSession(d)
        s.feed(2,100);s.feed(0,200)
        self.assertFalse(d.device_send_pending());self.assertEqual(a.current_text(),'待填入文字')
        a.target_controller.send_filled.assert_not_called()

    def test_press_before_receipt_and_expired_press_never_turn_into_ai_or_send(self):
        a,d=self.prepare_send();s=LocalDeviceSession(d)
        with patch.object(a,'organize_once') as ai:
            s.feed(1,100);a.pending_delivery['expires']=0;s.feed(0,200)
            ai.assert_not_called();a.target_controller.send_filled.assert_not_called()
            a.target_filling=True;s.feed(1,300)
            a.target_filling=False
            a.pending_delivery=dict(device=True,token='new',expires=time.monotonic()+5,ready_at=0,
                revision=a.revision,text=a.current_text(),target=a.target_controller.selected.session_key())
            s.feed(0,400)
            ai.assert_not_called();a.target_controller.send_filled.assert_not_called()

    def test_usb_send_frame_tolerates_focus_only(self):
        a,d=self.prepare_send();old=d.stamp();changed=list(old)
        changed[11]=(False,old[11][1]+1,False)
        self.assertTrue(display_matches(old,tuple(changed),1))
        changed[1]='改变';self.assertFalse(display_matches(old,tuple(changed),1))
        changed=list(old);changed[5]=('wrong',);self.assertFalse(display_matches(old,tuple(changed),1))

    def test_wifi_stale_screen_cannot_change_ai_into_send_or_send_new_target(self):
        a,d=self.prepare_send()
        d.arm(dict(id='before',action='ai',confirmation=''))
        d.dispatch('coloplay_ai');a.target_controller.send_filled.assert_not_called()
        marker=d.confirmation_marker();a.pending_delivery['expires']=0
        with patch.object(a,'organize_once') as ai:
            d.arm(dict(id='expired',action='ai',confirmation=marker));d.dispatch('coloplay_ai')
            ai.assert_not_called()
        d.arm(dict(id='other-target',action='send_current',target_marker='old'))
        d.dispatch('coloplay_send_current');a.target_controller.send_current.assert_not_called()

    def test_batch_cursor_and_selection_use_cached_complete_graphemes(self):
        a,d=self.app,self.device;a.set_text('甲e\u0301👨‍👩‍👧‍👦乙丙丁');a.text.mark_set('insert','1.0')
        self.act('knob')
        d.execute('move_next',steps=3)
        self.assertEqual(a.get_selection()[2],'甲e\u0301👨‍👩‍👧‍👦')
        self.act('knob');a.text.tag_remove('sel','1.0','end')
        cached=d.editor_focus.boundaries
        d.execute('move_next',steps=2)
        self.assertEqual(a.text.get('1.0','insert'),'甲e\u0301👨‍👩‍👧‍👦乙丙')
        self.assertIs(d.editor_focus.boundaries,cached)
        d.execute('move_previous',steps=4)
        self.assertEqual(a.text.get('1.0','insert'),'甲')

    def test_native_release_commits_last_target_and_no_timer_commits(self):
        a,d=self.app,self.device;a.target_controller.close()
        windows=[TargetWindow(i,2,'Codex',str(i),'codex.exe') for i in range(1,5)]
        a.target_controller=Mock(windows=windows,selected=windows[0]);d.active=True
        s=LocalDeviceSession(d);d.local_session=s
        s.feed(8,100);s.feed(72,150);s.feed(8,200);s.feed(72,250);s.feed(8,300)
        d.tick();a.after_cancel(d.job);d.job=None
        a.target_controller.cycle.assert_not_called()
        s.feed(0,500)
        expected=windows[2] if sys.platform=='darwin' else windows[2].session_key()
        self.assertEqual(a.target_controller.cycle.call_args.kwargs['expected_target'],expected)
        s.feed(0,600);a.target_controller.cycle.assert_called_once()
        d.local_session=None

    def test_send_current_uses_selected_target_and_does_not_paste_editor(self):
        a,d=self.app,self.device;a.set_text('不应填入这段')
        a.target_controller.close();target=TargetWindow(1,2,'Codex','测试','x')
        a.target_controller=Mock(selected=target);d.active=True
        s=LocalDeviceSession(d);s.feed(8,100);s.feed(24,150)
        a.target_controller.send_current.assert_not_called()
        s.feed(8,250);s.feed(0,300)
        a.target_controller.send_current.assert_called_once()
        a.target_controller.fill_with_receipt.assert_not_called()
        self.assertEqual(a.current_text(),'不应填入这段')

    def test_guide_six_pages_same_window_pause_and_remember(self):
        a,d=self.app,self.device;a.set_text('教程保留草稿')
        d.show_guide();a.update();g=a.guide
        self.assertTrue(g.opened);self.assertTrue(d.paused());self.assertIs(g.panel.winfo_toplevel(),a)
        for page in range(len(PAGES)):
            g.show(page);a.update()
            self.assertGreater(len(g.canvas.find_all()),8)
            self.assertLess(g.canvas.winfo_x()+g.canvas.winfo_width(),a.winfo_width())
        g.close();self.assertEqual(a.ui_preferences['coloplay_guide_version'],1)
        self.assertEqual(a.current_text(),'教程保留草稿');self.assertFalse(d.paused())
        d.show_guide();self.assertTrue(a.guide.opened);a.guide.close()


class Dock43Tests(unittest.TestCase):
    setUp=window.WindowTests.setUp
    tearDown=window.WindowTests.tearDown
    tick=window.WindowTests.tick

    def test_left_bottom_edit_expands_and_send_success_returns_to_edge(self):
        a,c=self.app,self.controller;d=a.coloplay
        for edge in ('left','bottom'):
            c.undock(False);self.assertTrue(c.dock(edge));d.set_active(True)
            self.assertEqual(c.edge,edge);self.assertTrue(a.edge_hide_var.get())
            d.execute('move_next');self.assertFalse(c.hidden)
            ticket=d.send_ticket();self.tick(10);self.assertFalse(c.hidden)
            d.finish_send(ticket,'error');self.tick(10);self.assertFalse(c.hidden)
            d.finish_send(ticket,'ok');self.assertTrue(c.hidden)
            self.assertEqual(c.edge,edge)

    def test_old_send_cannot_hide_new_document_or_new_dock(self):
        a,c=self.app,self.controller;d=a.coloplay
        c.dock('left');c.begin_device_edit();ticket=d.send_ticket()
        a.set_text('发送期间又编辑的新内容');d.finish_send(ticket,'ok')
        self.assertFalse(c.hidden)
        ticket=d.send_ticket();c.undock(False);c.dock('bottom');c.begin_device_edit()
        d.finish_send(ticket,'ok');self.assertFalse(c.hidden)

    def test_record_expands_immediately_before_first_transcript(self):
        a,c=self.app,self.controller
        a.settings=__import__('talkman_qwen').QwenSettings(api_key='sk-offline-test')
        c.dock('bottom');a.coloplay.set_active(True)
        with patch('talkman_app.prepare_microphone_permission',return_value=True):
            a.coloplay.execute('record')
        self.assertFalse(c.hidden);self.assertFalse(c.compact);self.assertTrue(a.voice)
        a.cancel_operation();self.tick(10);self.assertFalse(c.hidden)


class SendCurrentTests(unittest.TestCase):
    setUp=delivery.DeliveryConfirmTests.setUp
    tearDown=delivery.DeliveryConfirmTests.tearDown
    callback=delivery.DeliveryConfirmTests.callback

    def test_existing_input_sent_once_without_fill(self):
        c=self.controller;c.send_current(c.selected.session_key(),self.callback)
        self.assertEqual(wait_result(self.results)[0],'ok');self.assertEqual(self.backend.sent,1)
        self.assertFalse(any(call[0]=='paste' for call in self.backend.calls))

    def test_empty_ambiguous_changed_or_cancelled_target_never_sends(self):
        c=self.controller;b=self.backend
        for mode in ('empty','ambiguous','changed','stale','expired','cancelled'):
            b.text='正文';b.open_sync=lambda target:'main-input'
            b.read_sync=lambda *args:b.text
            epoch=c._sync_epoch;key=c.selected.session_key();expires=time.monotonic()+3
            if mode=='empty':b.text=' '
            elif mode=='ambiguous':b.open_sync=lambda _:(_ for _ in ()).throw(TargetError('多个输入框'))
            elif mode=='changed':b.read_sync=Mock(side_effect=['正文','后来变化'])
            elif mode=='stale':key=('stale',)
            elif mode=='expired':expires=0
            c._send_current(self.callback,(epoch,key,expires,lambda:mode=='cancelled'))
            self.assertEqual(wait_result(self.results)[0],'error',mode)
            self.assertEqual(b.sent,0)


class Wifi43Tests(unittest.TestCase):
    setUp=previous.WifiRecordingTests.setUp
    step=previous.WifiRecordingTests.step

    def test_release_waits_for_last_rotation_batch_then_commits_once(self):
        self.step(8,100);self.assertEqual(self.step(72,150)['action'],'target_next')
        for mask,t in ((8,175),(72,200),(8,225),(72,250),(8,275)):
            self.step(mask,t)
        pending=self.step(0,300)
        self.assertTrue(pending['target_commit']);self.assertEqual(pending['turns'],2)
        self.assertEqual(self.step(0,325,'1')['sent'],1)
        batch=self.step(0,350)
        self.assertEqual((batch['action'],batch['steps']),('target_next',2))
        self.assertEqual(self.step(0,375,'2')['sent'],2)
        commit=self.step(0,400)
        self.assertEqual(commit['action'],'target_commit')
        self.assertEqual(self.step(0,425,'3')['sent'],3)
        self.assertEqual(self.step(0,450)['action'],'')

    def test_left_upper_is_reserved_on_press_and_only_commits_on_release(self):
        self.assertEqual(self.step(1,100)['action'],'ai')
        self.assertEqual(self.step(1,200,'1')['sent'],0)
        self.assertEqual(self.step(0,300,'1')['sent'],1)
        self.assertEqual(self.step(0,400)['sent'],1)

    def test_combo_send_reserves_without_turning_into_fill(self):
        self.step(8,100)
        self.assertEqual(self.step(24,200)['action'],'send_current')
        self.assertEqual(self.step(24,300,'1')['sent'],0)
        self.assertEqual(self.step(8,400,'1')['sent'],1)
        self.assertEqual(self.step(0,500)['action'],'')


if __name__=='__main__':unittest.main()
