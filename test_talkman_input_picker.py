"""真实 Tk 和 Lua 手势共享的位置选择流程，所有目标均为隔离假后端。"""
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_talkman_coloplay as fixture
from test_talkman_input_locations import InputBackend
from talkman_target import TargetController
from talkman_coloplay_preview import LocalDeviceSession, DeviceRuntime
from talkman_coloplay_usb import display_matches


class PickerTests(unittest.TestCase):
    tearDown = fixture.DesktopTests.tearDown

    def setUp(self):
        fixture.DesktopTests.setUp(self)
        a, d = self.app, self.device
        a.target_controller.close()
        self.backend = InputBackend()
        a.target_controller = TargetController(Path(self.temp.name), backend=self.backend)
        a.target_controller.select(self.backend.windows[0])
        with a.target_controller._lock:
            a.target_controller._windows = list(self.backend.windows)
        self.outline = patch.object(a.input_picker.outline, 'show', return_value=True)
        self.show = self.outline.start(); self.addCleanup(self.outline.stop)
        self.picker = a.input_picker
        self.session = LocalDeviceSession(d)
        d.local_session = self.session; d.active = True

    def pump(self, predicate):
        end = time.monotonic()+3
        while not predicate() and time.monotonic()<end:
            if self.picker.job:
                self.app.after_cancel(self.picker.job); self.picker.job = None
            self.picker.tick()
            self.app.poll_events()
            if self.app.poll_job:
                self.app.after_cancel(self.app.poll_job); self.app.poll_job = None
            self.app.update_idletasks()
            time.sleep(.01)
        self.assertTrue(predicate())

    def begin_device(self):
        s = self.session
        s.feed(8,100); s.feed(72,150); s.feed(8,200)
        self.assertEqual(self.app.target_controller.selected, self.backend.windows[0])
        self.app.set_text('预选中也不得填入'); self.app.fill_to_target()
        self.assertFalse(self.backend.writes)
        s.feed(0,250)
        self.pump(lambda: bool(self.picker.regions))

    def test_complete_gesture_confirms_region_then_fills_only_it(self):
        self.begin_device()
        a, p, s = self.app, self.picker, self.session
        self.assertTrue(p.active); self.assertEqual(p.index,0)
        self.assertEqual(a.target_controller.selected,self.backend.windows[0])
        s.feed(64,300);s.feed(0,350)
        self.assertEqual(p.index,1)
        s.feed(16,400);s.feed(0,500)
        self.pump(lambda: not p.active)
        self.assertEqual(a.target_controller.selected,self.backend.windows[1])
        self.assertEqual(a.target_controller.selected_input.number,2)
        a.fill_to_target()
        self.pump(lambda: not a.target_filling)
        self.assertEqual(self.backend.writes,[(2,1)])
        self.assertIsNone(a.pending_delivery)
        self.assertIn('位置 2', a.target_label_var.get())

    def test_cancel_and_invalid_location_preserve_target_document_and_block_fill(self):
        self.begin_device()
        a,p,s=self.app,self.picker,self.session
        before=a.current_text()
        self.backend.valid=False;p.last_check=0
        self.pump(lambda:bool(p.error))
        s.feed(16,300);s.feed(0,400)
        self.assertTrue(p.active)
        a.fill_to_target();self.assertFalse(self.backend.writes)
        s.feed(2,500);s.feed(0,550)
        self.assertFalse(p.active)
        self.assertEqual(a.current_text(),before)
        self.assertEqual(a.target_controller.selected,self.backend.windows[0])

    def test_hold_and_hold_turn_do_not_confirm_or_fill(self):
        self.begin_device();s=self.session
        for mask,t in ((16,300),(16,1200),(0,1300),(16,1400),(80,1500),(16,1550),(0,1600)):
            s.feed(mask,t)
        self.assertTrue(self.picker.active)
        self.assertEqual(self.picker.index,0)
        self.assertEqual(self.app.target_controller.selected,self.backend.windows[0])
        self.assertFalse(self.backend.writes)

    def test_voice_navigation_and_stale_results_never_call_text_model(self):
        self.begin_device();a=self.app;p=self.picker
        self.device.execute('rewrite');self.assertTrue(a.busy)
        a.finish_voice('选择第二个输入框')
        self.assertEqual(p.index,1);a.run_worker.assert_not_called()
        self.device.execute('rewrite')
        a.finish_voice('确认这个位置')
        self.pump(lambda:not p.active)
        self.assertEqual(a.target_controller.selected_input.number,2)
        p.begin(self.backend.windows[0]);self.pump(lambda:bool(p.regions))
        self.device.execute('rewrite');p.cancel()
        a.finish_voice('确认这个位置')
        self.assertEqual(a.target_controller.selected,self.backend.windows[1])
        a.run_worker.assert_not_called()

    def test_changing_target_does_not_interrupt_existing_recording(self):
        a=self.app;a.set_text('原文');a.start_voice('insert',from_device=True)
        voice=a.voice
        self.begin_device()
        self.assertIs(a.voice,voice)
        self.assertFalse(a.voice_stopping)
        self.session.feed(4,300);self.session.feed(0,350)
        self.assertTrue(a.voice_stopping)

    def test_cancel_navigation_stops_only_its_voice_session_and_escape_keeps_draft(self):
        self.begin_device();a=self.app
        original=a.current_text()
        self.device.execute('rewrite')
        self.assertTrue(a.busy)
        self.session.feed(2,300);self.session.feed(0,350)
        self.assertFalse(self.picker.active)
        self.assertFalse(a.busy);self.assertIsNone(a.voice)
        self.assertEqual(a.current_text(),original)
        self.picker.begin(self.backend.windows[1])
        a.cancel_operation()
        self.assertFalse(self.picker.active)
        self.assertEqual(a.current_text(),original)

    def test_settings_and_disconnect_cancel_device_draft(self):
        self.begin_device()
        d=self.device;a=self.app
        d.peer='fake-peer';d.events.put(('disconnected','fake-peer',None))
        d.tick();a.after_cancel(d.job);d.job=None
        self.assertFalse(self.picker.active)
        self.assertEqual(a.target_controller.selected,self.backend.windows[0])
        self.picker.begin(self.backend.windows[1],source='device')
        a.open_settings('target')
        d.tick();a.after_cancel(d.job);d.job=None
        self.assertFalse(self.picker.active)
        a._close_settings(a.settings_window)

    def test_usb_rotation_can_lag_frame_but_confirmation_cannot(self):
        self.begin_device()
        old=self.device.stamp();self.picker.step(1);new=self.device.stamp()
        self.assertTrue(display_matches(old,new,64))
        self.assertFalse(display_matches(old,new,16))

    def test_panel_is_in_main_window_and_fits_compact_and_full(self):
        self.begin_device()
        a,p=self.app,self.picker
        for compact in (False,True):
            a.set_compact_layout(compact)
            a.minsize(480,330) if compact else a.minsize(900,640)
            a.geometry('480x330' if compact else '1000x700')
            a.update_idletasks();p.reposition();a.update_idletasks()
            self.assertIs(p.panel.winfo_toplevel(),a)
            self.assertGreaterEqual(p.panel.winfo_x(),0)
            self.assertGreaterEqual(p.panel.winfo_y(),0)
            self.assertLessEqual(p.panel.winfo_x()+p.panel.winfo_width(),a.winfo_width())
            self.assertLessEqual(p.panel.winfo_y()+p.panel.winfo_height(),a.winfo_height())
            record = a.compact_record if compact else a.replace_button
            record_y = record.winfo_rooty()-a.winfo_rooty()
            self.assertLessEqual(p.panel.winfo_y()+p.panel.winfo_height(), record_y)


class ScreenTests(unittest.TestCase):
    def test_new_phase_labels_fit_round_screen(self):
        runtime=DeviceRuntime()
        for error,loading in ((False,False),(True,False),(False,True)):
            runtime.render(dict(ready=True,phase='input_select',input_selecting=True,
                input_index=2,input_count=3,input_target='豆包',input_error=error,input_loading=loading),1000)
            labels=[row['text'] for row in runtime.text_layout]
            self.assertIn('选择输入位置',labels);self.assertIn('右上取消',labels)
            if not error and not loading:self.assertIn('2 / 3',labels)
            for row in runtime.text_layout:
                x,y,w,h=row['x'],row['y'],row['width'],row['size']
                for px,py in ((x,y),(x+w,y),(x,y+h),(x+w,y+h)):
                    self.assertLess((px-120)**2+(py-120)**2,120**2, row)


if __name__=='__main__':unittest.main()
