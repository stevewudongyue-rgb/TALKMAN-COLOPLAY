"""普通填入后的二次确认：假输入框与假回车，不操作真实 Agent。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from talkman_target import TargetController, TargetWindow, TargetError, SendSnapshot, DeliveryReadError
from test_talkman_target import FakeBackend, wait_result


class ConfirmBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.text = '前后'
        self.sent = 0
        self.fail_after_send = False
    def open_sync(self, target): return 'main-input'
    def read_sync(self, target, handle): return self.text
    def fill(self, target, text, token):
        self.calls.append(('paste', text))
        self.text = self.text[:1]+text+self.text[1:]
        return '已填入'
    def send_sync(self, target, handle, text, current):
        if not current() or text != self.text: raise TargetError('内容变化')
        self.sent += 1
        if self.fail_after_send: raise TargetError('结果未知')
        return '已按回车'


class DeliveryConfirmTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.backend = ConfirmBackend()
        self.controller = TargetController(Path(self.folder.name), backend=self.backend)
        self.controller.select(self.backend.windows[0])
        self.results = []

    def tearDown(self):
        self.controller.close()
        self.folder.cleanup()

    def callback(self, state, value): self.results.append((state, value))

    def fill(self):
        self.controller.fill_with_receipt('正文', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertTrue(receipt.send_token)
        return receipt

    def test_fill_then_send_preserves_prefix_and_suffix_and_pastes_only_once(self):
        receipt = self.fill()
        self.assertEqual(self.backend.text, '前正文后')
        self.assertEqual(self.backend.sent, 0)
        self.controller.send_filled(receipt.send_token, self.callback)
        state, value = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertEqual(value.text, '前正文后')
        self.assertEqual(value.status, '已触发下发')
        self.assertEqual(self.backend.sent, 1)
        self.assertEqual([x for x in self.backend.calls if x[0]=='paste'], [('paste','正文')])
        self.controller.send_filled(receipt.send_token, self.callback)
        self.assertEqual(wait_result(self.results)[0], 'error')
        self.assertEqual(self.backend.sent, 1)

    def test_external_edits_or_target_switch_or_timeout_invalidate_send(self):
        for mode in ['external', 'target', 'expired']:
            with self.subTest(mode=mode):
                receipt = self.fill()
                if mode == 'external': self.backend.text += '目标里的修改'
                elif mode == 'target': self.controller.select(self.backend.windows[0])
                else:
                    p = self.controller._pending_send
                    self.controller._pending_send = (*p[:-1], 0)
                self.controller.send_filled(receipt.send_token, self.callback)
                self.assertEqual(wait_result(self.results)[0], 'error')
                self.assertEqual(self.backend.sent, 0)
                self.assertIn({'external': '目标正文已变化', 'target': '目标或操作已变化',
                               'expired': '确认时间已过'}[mode], self.controller.last_send_report)

    def test_unknown_send_result_consumes_confirmation(self):
        receipt = self.fill()
        self.backend.fail_after_send = True
        for _ in range(2):
            self.controller.send_filled(receipt.send_token, self.callback)
            self.assertEqual(wait_result(self.results)[0], 'error')
        self.assertEqual(self.backend.sent, 1)

    def test_unreadable_target_still_fills_without_arming_send(self):
        self.backend.open_sync = lambda target: (_ for _ in ()).throw(TargetError('不能读回'))
        self.controller.fill_with_receipt('正文', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)
        self.assertIn('手动发送', receipt.message)
        self.assertEqual(self.backend.text, '前正文后')
        self.assertIn('填入前', self.controller.last_send_report)
        self.assertIn('读取失败', self.controller.last_send_report)

    def test_readback_must_prove_exact_insertion(self):
        self.backend.fill = lambda *args: setattr(self.backend, 'text', '前正文后额外文字')
        with patch('talkman_target.time.sleep'):
            self.controller.fill_with_receipt('正文', self.callback)
            state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)
        self.assertIn('读回内容与预期插入不符', self.controller.last_send_report)
        self.assertNotIn('额外文字', self.controller.last_send_report)

    def test_delayed_readback_is_distinguished_from_a_permanent_old_value(self):
        for stale_count in (2, 5):
            with self.subTest(stale_count=stale_count):
                self.backend.text = '前后'
                values = iter(['前后'] + ['前后'] * stale_count + ['前正文后'] * 5)
                self.backend.read_sync = lambda *_: next(values)
                with patch('talkman_target.time.sleep'):
                    self.controller.fill_with_receipt('正文', self.callback)
                    state, receipt = wait_result(self.results)
                self.assertEqual(state, 'ok')
                self.assertEqual(bool(receipt.send_token), stale_count == 2)
                self.assertIn('读回次数：3' if stale_count == 2 else '仍读到旧值', self.controller.last_send_report)
                self.assertEqual(self.backend.sent, 0)

    def test_control_change_reports_stage_without_exception_content(self):
        values = iter(['前后', DeliveryReadError('input_changed', 'private target content')])
        def read(*_):
            value = next(values)
            if isinstance(value, Exception): raise value
            return value
        self.backend.read_sync = read
        self.controller.fill_with_receipt('正文', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)
        self.assertIn('填入后 · 主输入框控件变化', self.controller.last_send_report)
        self.assertNotIn('private', self.controller.last_send_report)

    def test_switching_target_during_readback_cannot_arm_send(self):
        reads = [0]
        def read(*_):
            reads[0] += 1
            if reads[0] > 1:
                self.controller.stop_sync()
            return self.backend.text
        self.backend.read_sync = read
        self.controller.fill_with_receipt('正文', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)
        self.assertIsNone(self.controller._pending_send)
        self.assertIn('目标或操作已变化', self.controller.last_send_report)

    def test_snapshot_failure_does_not_fall_back_to_empty_before(self):
        self.backend.capture_delivery_snapshot = Mock(side_effect=TargetError('private before'))
        self.controller.fill_with_receipt('正文', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)
        self.assertNotIn('private before', self.controller.last_send_report)

    def test_repeated_prefix_still_confirms_exact_insertion(self):
        self.backend.text = '前后'
        self.controller.fill_with_receipt('后前后', self.callback)
        state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertEqual(self.backend.text, '前后前后后')
        self.assertTrue(receipt.send_token)
        self.controller.send_filled(receipt.send_token, self.callback)
        self.assertEqual(wait_result(self.results)[0], 'ok')
        self.assertEqual(self.backend.sent, 1)

    def test_queued_fill_does_not_follow_newly_selected_target(self):
        epoch = self.controller._sync_epoch
        self.controller.select(TargetWindow(99, 42, '其他', '其他', 'other.exe'))
        self.controller._fill(self.callback, '正文', receipt=True, requested_epoch=epoch)
        self.assertEqual(wait_result(self.results)[0], 'error')
        self.assertEqual(self.backend.text, '前后')

    def test_device_fill_never_arms_or_uses_existing_send_token(self):
        old=self.fill()
        self.controller.fill_with_receipt('设备文字',self.callback,
            expected_target=self.controller.selected.session_key(),allow_send_confirmation=False)
        state,receipt=wait_result(self.results)
        self.assertEqual(state,'ok');self.assertFalse(receipt.send_token)
        self.assertNotIn('无法核对',receipt.message)
        self.assertIsNone(self.controller._pending_send)
        self.controller.send_filled(old.send_token,self.callback)
        self.assertEqual(wait_result(self.results)[0],'error')
        self.assertEqual(self.backend.sent,0)

    def test_device_fill_skips_send_readback_and_rejects_stale_target(self):
        target=self.controller.selected;epoch=self.controller._sync_epoch
        with patch.object(self.backend,'open_sync',side_effect=AssertionError('设备不应读回发送快照')):
            self.controller.fill_with_receipt('设备文字',self.callback,
                expected_target=target.session_key(),allow_send_confirmation=False)
            self.assertEqual(wait_result(self.results)[0],'ok')
        text=self.backend.text
        self.controller.select(target)
        self.controller._fill(self.callback,'过期内容',receipt=True,expected_target=target.session_key(),
            requested_epoch=epoch,allow_send_confirmation=False)
        self.assertEqual(wait_result(self.results)[0],'error');self.assertEqual(self.backend.text,text)
        self.controller._fill(self.callback,'错误目标',receipt=True,expected_target=('wrong',),
            requested_epoch=self.controller._sync_epoch,allow_send_confirmation=False)
        self.assertEqual(wait_result(self.results)[0],'error');self.assertEqual(self.backend.text,text)


class NativePlaceholderConfirmTests(unittest.TestCase):
    """模拟实测 UIA 的 Name/Value/Text 提示；不操作任何真实窗口。"""

    def setUp(self):
        from talkman_target_windows import WindowsTargetBackend
        self.folder = tempfile.TemporaryDirectory()
        self.backend = b = WindowsTargetBackend()
        self.window = TargetWindow(1, 2, 'ChatGPT.exe', '测试对话', 'ChatGPT.exe')
        self.element = SimpleNamespace(CurrentClassName='ProseMirror', CurrentName='Work with Codex')
        self.pattern = SimpleNamespace(CurrentValue='Work with Codex\n', CurrentIsReadOnly=False)
        b.validate_window = Mock(return_value=self.window)
        b._find_editable = Mock(return_value=self.element)
        b._pattern = Mock(return_value=self.pattern)
        b._is_editable = Mock(return_value=True)
        b._is_descendant_of = Mock(return_value=True)
        b._window_element = Mock(return_value=object())
        b._ensure_uia = Mock(return_value=SimpleNamespace(CompareElements=lambda a, c: a is c))
        b.capture_focus = Mock(return_value=None)
        self.after = '新正文\n'
        def fill(*_):
            self.pattern.CurrentValue = self.after
            return '已填入'
        b.fill = Mock(side_effect=fill)
        b.send_delivery = Mock(return_value='假回车')
        self.controller = TargetController(Path(self.folder.name), backend=b)
        self.controller.select(self.window)
        self.results = []
        self.callback = lambda *args: self.results.append(args)

    def tearDown(self):
        self.controller.close()
        self.folder.cleanup()

    def fill(self, text='新正文'):
        with patch('talkman_target.time.sleep'):
            self.controller.fill_with_receipt(text, self.callback)
            state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        return receipt

    def test_hint_snapshot_is_kept_intact_and_does_not_authorize_sync(self):
        snapshot = self.backend.capture_delivery_snapshot(self.window)
        self.assertIsInstance(snapshot, SendSnapshot)
        self.assertEqual(snapshot.before, 'Work with Codex\n')
        self.assertTrue(snapshot.empty_hint)
        self.assertEqual(snapshot.paragraph_end, '\n')
        with self.assertRaisesRegex(TargetError, '关闭同步'):
            self.backend.read_sync(self.window, snapshot.handle)
        self.backend.fill.assert_not_called()

    def test_empty_hint_then_exact_insert_arms_one_shot_send(self):
        receipt = self.fill()
        self.assertTrue(receipt.send_token)
        self.backend.fill.assert_called_once()
        self.backend.send_delivery.assert_not_called()
        self.assertEqual(self.controller._pending_send[4], '新正文\n')
        report = self.controller.last_send_report
        self.assertIn('空框提示已消失', report)
        self.assertNotIn('Work with Codex', report)
        self.assertNotIn('新正文', report)
        from talkman_settings_ui import SettingsPanelMixin
        copied = SettingsPanelMixin._target_diagnostic_text(SimpleNamespace(target_controller=self.controller))
        self.assertIn(report, copied)
        self.assertNotIn('新正文', copied)
        self.controller.send_filled(receipt.send_token, self.callback)
        self.assertEqual(wait_result(self.results)[0], 'ok')
        self.backend.send_delivery.assert_called_once()
        self.controller.send_filled(receipt.send_token, self.callback)
        self.assertEqual(wait_result(self.results)[0], 'error')
        self.assertEqual(self.backend.send_delivery.call_count, 1)

    def test_empty_hint_requires_exact_body_not_substring_or_trimmed_whitespace(self):
        for after in ('', 'Work with Codex\n', '新正文另加文字\n', ' 新正文\n', '新正文\n\n', '别的正文\n'):
            with self.subTest(after=after):
                self.pattern.CurrentValue = 'Work with Codex\n'
                self.after = after
                receipt = self.fill()
                self.assertFalse(receipt.send_token)
                self.assertIsNone(self.controller._pending_send)
        self.backend.send_delivery.assert_not_called()

    def test_user_newlines_are_preserved_and_receipt_checks_entire_value(self):
        self.after = '第一行\n第二行\n\n'
        receipt = self.fill('第一行\r\n第二行\n')
        self.assertTrue(receipt.send_token)
        self.assertEqual(self.controller._pending_send[4], self.after)
        self.pattern.CurrentValue += '后加文字'
        self.controller.send_filled(receipt.send_token, self.callback)
        self.assertEqual(wait_result(self.results)[0], 'error')
        self.backend.send_delivery.assert_not_called()

    def test_existing_literal_hint_is_preserved_by_normal_exact_insertion(self):
        self.after = 'Work with Codex新正文\n'
        receipt = self.fill()
        self.assertTrue(receipt.send_token)
        self.assertIn('保留原有正文', self.controller.last_send_report)

    def test_prompt_name_mismatch_does_not_assume_empty(self):
        self.element.CurrentName = '别的控件标签'
        receipt = self.fill()
        self.assertFalse(receipt.send_token)

    def test_unrecognized_hint_is_not_assumed_empty(self):
        self.element.CurrentName = '别的提示'
        self.pattern.CurrentValue = '别的提示\n'
        receipt = self.fill()
        self.assertFalse(receipt.send_token)

    def test_changed_control_rejects_empty_hint_confirmation(self):
        original_fill = self.backend.fill.side_effect
        def fill(*args):
            result = original_fill(*args)
            self.backend._find_editable.return_value = object()
            return result
        self.backend.fill.side_effect = fill
        receipt = self.fill()
        self.assertFalse(receipt.send_token)
        self.assertIn('主输入框控件变化', self.controller.last_send_report)
        self.backend.send_delivery.assert_not_called()

    def test_same_readback_as_hint_is_never_proof_of_paste(self):
        self.after = 'Work with Codex\n'
        receipt = self.fill('Work with Codex')
        self.assertFalse(receipt.send_token)
        self.backend.send_delivery.assert_not_called()


if __name__ == '__main__': unittest.main()
