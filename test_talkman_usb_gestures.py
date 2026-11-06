"""Exercise delayed USB screen acknowledgements with real gestures and an isolated app."""
import queue
import sys
import time
import unittest
from unittest.mock import Mock, patch

import test_talkman_coloplay as fixture
from talkman_coloplay_usb import UsbBridge
from talkman_target import TargetController, TargetWindow
from test_talkman_app import FakeVoice
from test_talkman_target import FakeBackend


class UsbGestureTests(unittest.TestCase):
    def setUp(self):
        fixture.DesktopTests.setUp(self)
        self.ready.stop()
        self.wire = Mock(events=queue.Queue())
        with patch('talkman_coloplay_usb.UsbWire', return_value=self.wire):
            self.bridge = UsbBridge(self.device, quiet=True)
        self.clock = [time.monotonic()]
        timer = patch('talkman_coloplay_usb.time.monotonic', side_effect=lambda: self.clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        self.bridge.session.clock = lambda: self.clock[0]
        self.packet(0, kind='reset')
        self.device.active = True

    def tearDown(self):
        self.bridge.close()
        fixture.DesktopTests.tearDown(self)

    def packet(self, mask, stamp=None, *, elapsed=.05, kind='buttons'):
        self.clock[0] += elapsed
        self.wire.events.put((kind, dict(mask=mask, stamp=self.device.stamp() if stamp is None else stamp), self.clock[0]))
        self.bridge.poll()

    def drain_voice(self):
        self.app.poll_events()
        self.app.after_cancel(self.app.poll_job)
        self.app.poll_job = None

    def start_voice(self):
        self.packet(4)
        self.packet(0)
        voice = FakeVoice.sessions[-1]
        voice.emit('ready', '')
        self.drain_voice()
        return voice

    def targets(self):
        self.app.target_controller.close()
        windows = [TargetWindow(i, 2, 'Codex', str(i), 'codex.exe') for i in range(1, 4)]
        self.app.target_controller = Mock(selected=windows[0], windows=windows, selected_input=None)
        return self.app.target_controller

    def test_one_short_stop_survives_transcript_between_press_and_release(self):
        voice = self.start_voice()
        displayed = self.device.stamp()
        self.packet(4, displayed)
        voice.emit('partial', '结束按下后仍在更新的转写')
        self.drain_voice()
        self.packet(0, displayed)
        self.assertTrue(self.app.voice_stopping)
        self.packet(0, displayed)
        self.assertEqual(len(FakeVoice.sessions), 1)

    def test_recording_heartbeat_does_not_disarm_next_stop(self):
        voice = self.start_voice()
        displayed = self.device.stamp()
        voice.emit('partial', '持续更新的转写')
        self.drain_voice()
        self.packet(0, displayed)
        self.packet(4)
        self.packet(0)
        self.assertTrue(self.app.voice_stopping)

    def test_target_release_survives_focus_left_by_own_preview(self):
        target = self.targets()
        self.app.set_text('保留文本及光标')
        with patch.object(self.app, 'focus_displayof', return_value=self.app.text):
            self.device.editor_focus.enter()
            displayed = self.device.stamp()
            self.packet(8, displayed)
            self.packet(72, displayed)
            self.assertEqual(self.device.target_preview, target.windows[1])
            self.packet(8, displayed)
            self.packet(72, displayed)
            self.packet(8, displayed)
            target.cycle.assert_not_called()
            self.packet(0, displayed)
        target.cycle.assert_called_once()
        self.assertEqual(target.cycle.call_args.kwargs['expected_target'], target.windows[2])

    def test_first_target_scan_does_not_invalidate_held_usb_gesture(self):
        windows = [TargetWindow(i, 2, 'Codex', str(i), 'codex.exe') for i in range(1, 4)]
        backend = FakeBackend()
        backend.windows = windows
        backend.activate_window = Mock()
        self.app.target_controller.close()
        target = TargetController(self.app.data_dir, backend=backend)
        self.app.target_controller = target
        target.selection.set(windows[0])
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.assertTrue(self.device.target_scanning)
        deadline = time.time() + 2
        while self.device.events.empty() and time.time() < deadline:
            time.sleep(.01)
        self.assertFalse(self.device.events.empty(), '等待候选扫描超时')
        self.assertIsNone(target.selected)
        self.packet(8, displayed)
        self.device.tick()
        self.assertEqual(self.device.target_preview, windows[0])
        self.assertEqual(self.bridge.target_after, self.device.stamp())
        self.packet(0, displayed)
        self.assertTrue(self.device.cycling)
        deadline = time.time() + 2
        while self.device.events.empty() and time.time() < deadline:
            time.sleep(.01)
        self.assertFalse(self.device.events.empty(), '等待目标确认超时')
        self.device.tick()
        self.assertEqual(target.selected, windows[0])
        if sys.platform == 'darwin':
            backend.activate_window.assert_not_called()
        else:
            backend.activate_window.assert_called_once_with(windows[0])

    def test_external_edit_still_cancels_target_release(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.app.set_text('用户在另一处重新编辑')
        self.packet(0, displayed)
        target.cycle.assert_not_called()

    def test_new_recording_cannot_be_stopped_by_old_recording_packet(self):
        self.start_voice()
        old = self.device.stamp()
        self.app.operation_id += 1
        self.packet(4, old)
        self.packet(0, old)
        self.assertFalse(self.app.voice_stopping)

    def test_rewrite_stop_releases_left_key_while_modifier_remains_held(self):
        self.app.set_text('原有文字')
        self.packet(8)
        self.packet(12)
        self.packet(8)
        voice = FakeVoice.sessions[-1]
        voice.emit('ready', '')
        self.drain_voice()
        displayed = self.device.stamp()
        self.packet(12, displayed)
        self.app.text.mark_set('insert', '1.1')
        self.packet(8, displayed)
        self.assertTrue(self.app.voice_stopping)

    def test_rejected_release_rearms_next_press_without_extra_click(self):
        self.targets()
        self.app.set_text('不能填入旧文字')
        displayed = self.device.stamp()
        with patch.object(self.app, 'fill_to_target') as fill:
            self.packet(16, displayed)
            self.app.set_text('用户已经改写')
            self.packet(0, displayed, elapsed=.6)
            fill.assert_not_called()
            self.packet(4)
            self.packet(0)
        self.assertEqual(len(FakeVoice.sessions), 1)

    def test_gap_cannot_commit_preview_or_replay_held_button(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.packet(8, displayed, kind='reset')
        self.packet(0)
        target.cycle.assert_not_called()
        self.packet(8)
        self.packet(72)
        self.packet(0)
        target.cycle.assert_called_once()

    def test_pending_window_switch_survives_old_neutral_heartbeat(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.packet(0, displayed)
        cancellation = target.cycle.call_args.kwargs['cancelled']
        target.selected = target.windows[1]
        self.packet(0, displayed)
        self.assertFalse(cancellation.is_set())

    def test_changed_target_does_not_reuse_preview(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        target.selected = target.windows[2]
        self.packet(0, displayed)
        target.cycle.assert_not_called()

    def test_changed_phase_does_not_reuse_preview(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.app.busy = True
        self.packet(0, displayed)
        target.cycle.assert_not_called()
        self.app.busy = False

    def test_expired_release_never_commits_preview(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.wire.events.put(('buttons', dict(mask=0, stamp=displayed), self.clock[0] - .5))
        self.bridge.poll()
        target.cycle.assert_not_called()

    def test_target_rotation_survives_live_transcript_without_stopping_voice(self):
        target = self.targets()
        voice = self.start_voice()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        voice.emit('partial', '转动时仍在录入')
        self.drain_voice()
        self.packet(72, displayed)
        self.packet(8, displayed)
        voice.emit('partial', '松开前仍在录入')
        self.drain_voice()
        self.packet(0, displayed)
        target.cycle.assert_called_once()
        self.assertEqual(target.cycle.call_args.kwargs['expected_target'], target.windows[1])
        self.assertFalse(self.app.voice_stopping)

    def test_manual_target_change_during_recording_cancels_old_preview(self):
        target = self.targets()
        self.start_voice()
        displayed = self.device.stamp()
        self.packet(8, displayed)
        self.packet(72, displayed)
        target.selected = target.windows[2]
        self.packet(0, displayed)
        target.cycle.assert_not_called()

    def test_new_navigation_ignores_editor_lag_before_first_turn(self):
        target = self.targets()
        displayed = self.device.stamp()
        self.app.set_text('刚结束编辑，屏幕尚未收到新正文')
        self.app.text.mark_set('insert', '1.3')
        self.packet(8, displayed)
        self.packet(72, displayed)
        self.assertEqual(self.device.target_preview, target.windows[1])
        self.packet(0, displayed)
        target.cycle.assert_called_once()

    def test_new_navigation_ignores_focus_lag_before_first_turn(self):
        target = self.targets()
        self.app.set_text('光标刚刚进入编辑区')
        displayed = self.device.stamp()
        with patch.object(self.app, 'focus_displayof', return_value=self.app.text):
            self.device.editor_focus.enter()
            self.packet(72, displayed)
            self.assertEqual(self.device.target_preview, target.windows[1])
            self.packet(0, displayed)
        target.cycle.assert_called_once()

    def test_new_navigation_still_rejects_changed_target_and_recording_session(self):
        target = self.targets()
        displayed = self.device.stamp()
        target.selected = target.windows[2]
        self.packet(8, displayed);self.packet(72, displayed);self.packet(0, displayed)
        target.cycle.assert_not_called()
        displayed = self.device.stamp()
        self.app.operation_id += 1
        self.packet(8, displayed);self.packet(72, displayed);self.packet(0, displayed)
        target.cycle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
