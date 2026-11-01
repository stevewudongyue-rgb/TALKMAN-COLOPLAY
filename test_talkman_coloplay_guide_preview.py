"""教程动画隔离验收：不连接设备、模型或真实目标窗口。"""
import time
import unittest
from unittest.mock import patch

import test_talkman_coloplay as fixtures
from talkman_coloplay_guide import PAGES
from talkman_coloplay_guide_preview import SCENES


class GuidePreviewTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    tearDown = fixtures.DesktopTests.tearDown

    def guide(self):
        self.app.geometry('760x520+20+20')
        self.app.set_text('用户真实草稿保持原样。')
        self.app.text.tag_add('sel', '1.2', '1.6')
        self.device.show_guide()
        self.app.update()
        return self.app.guide

    def pump(self, seconds=.32):
        until = time.monotonic()+seconds
        while time.monotonic() < until:
            self.app.update()
            time.sleep(.01)

    def test_hover_opens_without_focus_or_draft_changes_and_keeps_devices_paused(self):
        g = self.guide()
        focus = self.app.focus_get()
        selection = self.app.text.tag_ranges('sel')
        g.point_rows[1].winfo_children()[0].event_generate('<Enter>')
        self.assertIsNone(g.preview)
        self.pump()
        self.assertEqual(g.preview_index, 1)
        self.assertEqual(self.app.focus_get(), focus)
        self.assertIs(g.preview.winfo_toplevel(), self.app)
        self.assertTrue(self.device.paused())
        self.assertEqual(self.app.current_text(), '用户真实草稿保持原样。')
        self.assertEqual(self.app.text.tag_ranges('sel'), selection)
        self.app.run_worker.assert_not_called()

    def test_brief_hover_leave_and_switch_only_shows_latest_sentence(self):
        g = self.guide()
        g.queue_preview(0)
        with patch.object(g.panel, 'winfo_containing', return_value=None):
            g.queue_leave()
            self.pump()
        self.assertIsNone(g.preview)
        g.queue_preview(0)
        g.queue_preview(2)
        self.pump()
        self.assertEqual(g.preview_index, 2)
        old = g.preview
        g.queue_preview(1)
        self.pump()
        self.assertTrue(old.closed)
        self.assertIsNone(old.job)
        self.assertEqual(g.preview_index, 1)

    def test_preview_controls_pause_replay_and_stop_at_final_frame(self):
        g = self.guide()
        g.open_preview(0)
        p = g.preview
        p.toggle()
        self.assertIsNone(p.job)
        self.assertTrue(p.paused)
        p.advance()
        self.assertEqual(p.index, 0)
        p.replay()
        self.assertFalse(p.paused)
        while p.index < len(p.scenes)-1:
            p._cancel()
            p.advance()
        self.assertIsNone(p.job)
        self.assertEqual(p.pause_button.cget('state'), 'disabled')
        p.replay()
        self.assertEqual(p.index, 0)
        with patch.object(g.panel, 'winfo_containing', return_value=p.pause_button):
            g.leave_preview()
        self.assertIs(g.preview, p)

    def test_keyboard_escape_then_close_and_page_change_clean_all_timers(self):
        g = self.guide()
        g.point_rows[0].focus_force()
        self.app.update()
        self.assertIsNotNone(g.preview)
        p = g.preview
        g.point_rows[0].event_generate('<Escape>')
        self.app.update()
        self.assertTrue(g.opened)
        self.assertTrue(p.closed)
        self.assertIsNone(g.preview)
        g.queue_preview(1)
        g.show(3)
        self.pump()
        self.assertIsNone(g.preview)
        g.open_preview(2)
        p = g.preview
        g.close()
        self.pump()
        self.assertTrue(p.closed)
        self.assertIsNone(p.job)
        self.assertIsNone(g.hover_job)
        self.assertIsNone(g.leave_job)

    def test_all_eighteen_previews_fit_small_window_and_theme_switch(self):
        g = self.guide()
        self.assertEqual(len(SCENES), len(PAGES))
        for theme in ('matrix', 'dark', 'light'):
            self.app.set_theme(theme)
            for page, points in enumerate(SCENES):
                self.assertEqual(len(points), 3)
                g.show(page)
                self.app.update()
                for number, frames in enumerate(points):
                    g.open_preview(number)
                    p = g.preview
                    p.toggle()
                    for index, scene in enumerate(frames):
                        with self.subTest(theme=theme, page=page, point=number, scene=index):
                            p.index=index
                            p.render()
                            self.app.update()
                            self.assertGreater(p.canvas.winfo_height(), 100)
                            self.assertLessEqual(p.winfo_rootx()+p.winfo_width(), self.app.winfo_rootx()+self.app.winfo_width())
                            for row in g.point_rows:
                                self.assertLess(row.winfo_rooty()+row.winfo_height(), g.back.winfo_rooty())
                            for item in p.canvas.find_all():
                                bounds=p.canvas.bbox(item)
                                if bounds:
                                    self.assertGreaterEqual(bounds[0], -1)
                                    self.assertGreaterEqual(bounds[1], -1)
                                    self.assertLessEqual(bounds[2], p.canvas.winfo_width()+1)
                                    self.assertLessEqual(bounds[3], p.canvas.winfo_height()+1)
        self.assertEqual(self.app.current_text(), '用户真实草稿保持原样。')

    def test_text_demo_highlights_correct_word(self):
        for page in (1, 2, 3):
            for frames in SCENES[page]:
                for scene in frames:
                    if scene.selected:
                        self.assertIn(scene.text[slice(*scene.selected)], ('苹果', '苹'))
                    if scene.cursor >= 0:
                        self.assertLessEqual(scene.cursor, len(scene.text))


if __name__ == '__main__':
    unittest.main()
