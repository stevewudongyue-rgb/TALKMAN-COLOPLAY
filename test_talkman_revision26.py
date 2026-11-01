"""翻译与二次下发的隔离界面事务测试。"""
import gc
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from talkman_app import TalkManApp
from talkman_qwen import QwenSettings
from talkman_target import FillReceipt
from test_talkman_app import FakeVoice, FakeClient


class Revision26Tests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='TalkMan-revision26-')
        self.app = TalkManApp(Path(self.folder.name), session_factory=FakeVoice,
            file_session_factory=FakeVoice, client_factory=FakeClient, desktop_services=False)
        self.app.settings = QwenSettings(api_key='sk-offline-test')
        self.app.after_cancel(self.app.poll_job)
        self.app.poll_job = None
        self.app.run_worker = Mock()
        self.app.geometry('900x640+20+20')
        self.app.update()

    def tearDown(self):
        self.app.target_filling = False
        self.app.close_app()
        self.app = None
        FakeVoice.sessions.clear()
        gc.collect()
        self.folder.cleanup()

    def prepare_filled(self, clear=False):
        a=self.app
        a.target_controller=Mock()
        a.clear_after_fill_var.set(clear)
        a.set_text('待下发正文')
        a.fill_to_target()
        self.assertTrue(a.target_filling)
        a.target_filling=False
        r=FillReceipt('填入成功', '待下发正文', '测试 Agent', '测试对话', '2026-10-31 12:00', send_token='verified')
        a.finish_normal_fill('ok', r)

    def test_fill_becomes_send_even_with_clear_option_and_never_pastes_again(self):
        a=self.app
        self.prepare_filled(clear=True)
        self.assertEqual(a.current_text(), '')
        self.assertEqual(a.target_button.cget('text'),'下发')
        self.assertEqual(a.compact_fill.cget('text'),'下发')
        a.fill_to_target()
        a.target_controller.send_filled.assert_not_called()
        a.pending_delivery['ready_at']=0
        a.fill_to_target()
        self.assertTrue(a.target_filling)
        self.assertIsNone(a.pending_delivery)
        a.target_controller.send_filled.assert_called_once()
        self.assertEqual(a.target_controller.fill_with_receipt.call_count, 1)

    def test_body_edit_and_expiry_cancel_second_step(self):
        a=self.app
        self.prepare_filled()
        a.set_text('修改了正文')
        self.assertIsNone(a.pending_delivery)
        self.assertEqual(a.target_button.cget('text'),'填入 Agent')
        self.prepare_filled()
        a.pending_delivery['expires']=time.monotonic()-1
        a.fill_to_target()
        a.target_controller.send_filled.assert_not_called()
        self.assertIn('失效', a.status_var.get())

    def test_late_fill_receipt_does_not_arm_changed_document(self):
        a=self.app
        a.set_text('原文')
        a._delivery_snapshot=(a.revision, '原文')
        a.set_text('新正文')
        a.finish_normal_fill('ok', FillReceipt('成功','原文','测试','对话','今天',send_token='stale'))
        self.assertIsNone(a.pending_delivery)

    def translate(self):
        a=self.app
        a.set_text('打开 Colo Play')
        a.text.tag_add('sel', '1.0', 'end-1c')
        a.translate_document()
        kind, token, work=a.run_worker.call_args.args
        self.assertEqual(kind,'translation')
        plan=token[2]
        result=plan.combine(plan.fragment.restore('Open '+next(iter(plan.fragment.tokens))))
        return token,result

    def test_translation_preview_cancel_apply_undo_and_provenance_saved(self):
        a=self.app
        a.preview_ai_var.set(True)
        token,result=self.translate()
        a.finish_translation(token,'ok',result)
        self.assertEqual(a.current_text(),'打开 Colo Play')
        a.ai_review_finish(False)
        self.assertEqual(a.current_text(),'打开 Colo Play')
        token,result=self.translate()
        a.finish_translation(token,'ok',result)
        a.ai_review_finish(True)
        self.assertEqual(a.current_text(),'Open Colo Play')
        a.save_current()
        self.assertEqual(a.saved_history[0]['translation_protected'], ((5,14),))
        a.undo()
        self.assertEqual(a.current_text(),'打开 Colo Play')
        a.redo()
        self.assertEqual(a.current_text(),'Open Colo Play')
        self.assertEqual(a.translation_memory.protection(a.current_text()), ((5,14),))

    def test_changed_document_cancelled_request_and_language_switch_reject_old_translation(self):
        a=self.app
        token,result=self.translate()
        a.set_text('我的新正文')
        a.finish_translation(token,'ok',result)
        self.assertEqual(a.current_text(),'我的新正文')
        token,result=self.translate()
        a.cancel_operation()
        a.finish_translation(token,'ok',result)
        self.assertEqual(a.current_text(),'打开 Colo Play')
        token,result=self.translate()
        a.translation_language.set('日语')
        a.translation_language_changed()
        a.finish_translation(token,'ok',result)
        self.assertEqual(a.current_text(),'打开 Colo Play')
        self.assertFalse(a.translate_button.instate(['disabled']))

    def test_layout_places_remember_with_tools_and_translation_in_main_actions(self):
        a=self.app
        a.update()
        self.assertGreater(a.remember_button.winfo_rootx(), a.count_label.winfo_rootx()+a.count_label.winfo_width()+30)
        self.assertTrue(a.remember_button.cget('image'))
        self.assertTrue(a.translate_button.cget('image'))
        self.assertGreater(a.translate_button.winfo_rootx(), a.one_filler_button.winfo_rootx()+a.one_filler_button.winfo_width())
        self.assertLessEqual(a.translate_button.winfo_rootx()+a.translate_button.winfo_width(),a.winfo_rootx()+a.winfo_width())


if __name__ == '__main__': unittest.main()
