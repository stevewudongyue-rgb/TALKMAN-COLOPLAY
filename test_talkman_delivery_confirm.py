"""普通填入后的二次确认：假输入框与假回车，不操作真实 Agent。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from talkman_target import TargetController, TargetWindow, TargetError
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

    def test_readback_must_prove_exact_insertion(self):
        self.backend.fill = lambda *args: setattr(self.backend, 'text', '前正文后额外文字')
        with patch('talkman_target.time.sleep'):
            self.controller.fill_with_receipt('正文', self.callback)
            state, receipt = wait_result(self.results)
        self.assertEqual(state, 'ok')
        self.assertFalse(receipt.send_token)

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


if __name__ == '__main__': unittest.main()
