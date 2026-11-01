"""旋钮聚焦选字、失焦回退与 USB 连续输入；全部使用隔离编辑器。"""
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_talkman_coloplay as fixtures
from test_talkman_app import FakeVoice
from talkman_coloplay_preview import LocalDeviceSession, DeviceRuntime
from talkman_coloplay_usb import display_matches


class FocusTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    tearDown = fixtures.DesktopTests.tearDown

    def focus(self, text='甲乙丙丁', index='1.2'):
        a=self.app
        a.set_text(text);a.text.mark_set('insert',index)
        a.text.focus_force();a.update()
        self.device.editor_focus.enter(reset=True)
        self.assertTrue(self.device.editor_focus.sync()[0])
        self.device.editor_focus.toggle_selection()
        return a

    def turn(self, direction=1):
        action='next' if direction>0 else 'previous'
        semantic=self.device.semantic(action)
        if semantic:self.device.execute(semantic)

    def selected(self):
        s=self.app.get_selection()
        return s[2] if s else ''

    def test_forward_reverse_cross_anchor_and_document_bounds(self):
        a=self.focus()
        with patch.object(self.device,'preview_target') as navigate:
            self.turn();self.assertEqual(self.selected(),'丙')
            self.turn();self.assertEqual(self.selected(),'丙丁')
            self.turn();self.assertEqual(self.selected(),'丙丁')
            self.turn(-1);self.assertEqual(self.selected(),'丙')
            self.turn(-1);self.assertEqual(self.selected(),'')
            self.turn(-1);self.assertEqual(self.selected(),'乙')
            self.turn(-1);self.turn(-1);self.assertEqual(self.selected(),'甲乙')
            navigate.assert_not_called()
        self.assertEqual(a.current_text(),'甲乙丙丁')
        self.assertIn('选区中',a.selection_var.get())

    def test_unicode_newlines_and_existing_mouse_selection(self):
        a=self.focus('甲👨‍👩‍👧‍👦e\u0301\n乙','1.1')
        self.turn();self.assertEqual(self.selected(),'👨‍👩‍👧‍👦')
        self.turn();self.assertEqual(self.selected(),'👨‍👩‍👧‍👦e\u0301')
        self.turn();self.assertTrue(self.selected().endswith('\n'))
        a=self.focus('甲乙丙丁','1.3')
        a.text.tag_add('sel','1.1','1.3')
        self.device.editor_focus.toggle_selection()
        self.turn(-1);self.assertEqual(self.selected(),'乙')
        self.turn();self.assertEqual(self.selected(),'乙丙')
        self.turn();self.assertEqual(self.selected(),'乙丙丁')
        a=self.focus('甲乙丙丁','1.1')
        a.text.tag_add('sel','1.1','1.3')
        self.device.editor_focus.toggle_selection()
        self.turn(-1);self.assertEqual(self.selected(),'甲乙丙')

    def test_click_blank_and_external_focus_restore_navigation_keep_selection(self):
        a=self.focus();self.turn();selection=a.get_selection()
        a.full_header.event_generate('<ButtonPress-1>',x=2,y=2);a.update()
        self.assertFalse(self.device.editor_focus.active)
        self.assertEqual(a.get_selection(),selection)
        with patch.object(self.device,'preview_target') as navigate:
            self.turn();navigate.assert_not_called()
            self.assertIsNone(a.get_selection())
        self.focus();self.turn()
        outside=tk.Toplevel(a);field=tk.Entry(outside);field.pack();outside.update()
        try:
            field.focus_force();a.update();a.check_focus()
            self.assertFalse(self.device.editor_focus.active)
            self.assertEqual(self.device.semantic('next'),'move_next')
        finally:outside.destroy()

    def test_pointer_focus_and_outside_button_before_break_binding(self):
        a=self.focus();self.device.editor_focus.leave()
        a.text.event_generate('<ButtonPress-1>',x=32,y=24)
        a.text.event_generate('<ButtonRelease-1>',x=32,y=24);a.update()
        self.assertTrue(self.device.editor_focus.sync()[0])
        # 胶囊绑定返回 break；模式观察仍先于控件绑定执行。
        a.replace_button.bind('<ButtonPress-1>',lambda _event:'break')
        a.replace_button.event_generate('<ButtonPress-1>',x=20,y=20)
        self.assertFalse(self.device.editor_focus.active)

    def test_pending_navigation_and_action_lease_are_invalidated(self):
        a=self.app;d=self.device
        d.arm(dict(action='next',id='old-target'));self.assertTrue(d.intent.id())
        d.target_due=999999999
        self.focus()
        self.assertEqual(d.target_due,0)
        with patch.object(d,'preview_target') as navigate:
            d.dispatch('coloplay_next');navigate.assert_not_called()
        d.arm(dict(action='next',id='old-selection'))
        d.editor_focus.leave()
        with patch.object(d,'preview_target') as navigate:
            d.dispatch('coloplay_next');navigate.assert_not_called()

    def test_recording_blocks_selection_and_external_focus_does_not_stop_voice(self):
        a=self.focus();self.turn();before=self.selected()
        self.assertTrue(a.start_voice('replace',from_device=True))
        self.assertIsNone(self.device.semantic('next'))
        FakeVoice.sessions[-1].emit('ready','')
        a.poll_events();a.after_cancel(a.poll_job);a.poll_job=None
        self.assertIsNone(self.device.semantic('previous'))
        self.assertEqual(self.selected(),before)
        with patch.object(a,'focus_displayof',return_value=None):a.check_focus()
        self.assertIsNone(self.device.semantic('next'))
        self.assertEqual(self.device.semantic('target_next'),'target_next')
        self.assertFalse(a.voice_stopping)

    def test_usb_allows_only_own_selection_lag_and_queue_does_not_change_modes(self):
        a=self.focus();d=self.device
        session=LocalDeviceSession(d);d.local_session=session
        initial=d.stamp()
        session.feed(64,100);session.feed(0,150)
        after=d.stamp()
        self.assertEqual(self.selected(),'丙')
        self.assertTrue(display_matches(initial,after,64))
        self.assertFalse(display_matches(initial,after,4))
        session.turns=3
        a.text.mark_set('insert','1.0')
        changed=d.stamp()
        self.assertFalse(display_matches(initial,changed,64))
        session.drain_turns();self.assertEqual(session.turns,0)
        d.editor_focus.leave()
        self.assertFalse(display_matches(after,d.stamp(),64))
        d.local_session=None

    def test_modifier_scroll_and_hold_turn_keep_original_meanings(self):
        self.focus();d=self.device
        session=LocalDeviceSession(d)
        with patch.object(d.scroll,'submit') as scroll,patch.object(d,'preview_target') as navigate:
            for mask,t in [(8,100),(72,200),(8,250),(24,300),(56,350),(0,400)]:session.feed(mask,t)
            scroll.assert_not_called()
            navigate.assert_called_once_with(1)
            for mask,t in [(16,500),(80,600),(0,700)]:session.feed(mask,t)
            self.assertFalse(self.selected());self.assertEqual(navigate.call_count,1)

    def test_simulator_is_device_proxy_and_focus_screen_has_no_body_text(self):
        a=self.focus('不应发给小屏的私密正文','1.0');d=self.device
        d.open_simulator();sim=d.simulator
        sim.win.focus_force();a.update()
        sim.rotate(1);self.assertEqual(self.selected(),'不')
        state=d.snapshot();state['ready']=True
        self.assertTrue(state['editor_focused']);self.assertEqual(state['selection_count'],1)
        runtime=DeviceRuntime();runtime.step(0,0);runtime.render(state,1000)
        shown=[row['text'] for row in runtime.text_layout]
        self.assertIn('选择文字',shown);self.assertIn('点按结束选区',shown)
        self.assertFalse(any('私密' in row for row in shown))
        sim.close()


if __name__=='__main__':unittest.main()
