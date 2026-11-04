"""设置独立窗口回归；临时配置、假语音，不操作真实设备和 Agent。"""
import gc
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from talkman_app import TalkManApp
from talkman_system_style import keep_mac_windows_separate
from test_talkman_app import FakeClient, FakeVoice


class StartupTests(unittest.TestCase):
    def test_pre_tk_policy_does_not_create_native_application(self):
        kit = SimpleNamespace(NSWindow=Mock(), NSApplication=Mock())
        kit.NSApplication.sharedApplication.side_effect = AssertionError('须由 Tk 初始化原生应用')
        with patch('talkman_system_style.sys', SimpleNamespace(platform='darwin')), patch.dict(sys.modules, AppKit=kit):
            keep_mac_windows_separate(existing=False)
        kit.NSWindow.setAllowsAutomaticWindowTabbing_.assert_called_once_with(False)
        kit.NSApplication.sharedApplication.assert_not_called()


class SettingsWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TalkMan-settings63-')
        Path(self.temp.name, 'interface.json').write_text('{"free_mode":false}', 'utf-8')
        self.app = TalkManApp(Path(self.temp.name), session_factory=FakeVoice,
            file_session_factory=FakeVoice, client_factory=FakeClient, desktop_services=False)
        self.app.geometry('900x640+60+60')
        self.app.update()
        self.app.window_controller.fit_to_screen(force=True)
        self.app.update()
        self.app.cancel_auto_timer()

    def tearDown(self):
        self.app.busy = False
        self.app.ai_review = None
        self.app.close_app()
        self.app = None
        gc.collect()
        self.temp.cleanup()

    def test_first_frame_page_switch_and_close_preserve_editor(self):
        a = self.app
        a.set_text('主窗口保持原来的形状和正在编辑的文字。')
        a.text.tag_add('sel', '1.3', '1.7')
        a.update()
        original, text = a.geometry(), a.current_text()
        history = list(a.timeline.items)
        maps = []
        a.bind_all('<Map>', lambda e: maps.append((e.widget, e.widget.winfo_width(), e.widget.winfo_height())), add=True)
        a.open_settings('coloplay')
        a.update()
        win = a.settings_window
        size = win.geometry()
        frames = [(w, h) for obj, w, h in maps if obj is win]
        self.assertTrue(frames)
        self.assertEqual(frames[0], (win.winfo_width(), win.winfo_height()))
        for page in a.settings_tabs:
            a.open_settings(page)
            a.update()
            self.assertIs(a.settings_window, win)
            self.assertEqual(win.geometry(), size)
            self.assertEqual(a.geometry(), original)
            self.assertEqual(a.current_text(), text)
        self.assertEqual(a.text.get('sel.first', 'sel.last'), '保持原来')
        a._close_settings(win)
        a.update()
        self.assertEqual(a.geometry(), original)
        self.assertEqual(a.timeline.items, history)
        self.assertFalse(a.closing)

    def test_reopen_remembers_user_size_instead_of_page_content(self):
        a = self.app
        a.open_settings('asr')
        win = a.settings_window
        win.geometry('780x600')
        a.update()
        size = win.winfo_width(), win.winfo_height()
        a._close_settings(win)
        a.open_settings('coloplay')
        a.update()
        self.assertEqual((a.settings_window.winfo_width(), a.settings_window.winfo_height()), size)

    @unittest.skipUnless(sys.platform == 'darwin', '需要 Mac 原生窗口')
    def test_mac_always_tabs_preference_still_keeps_settings_separate(self):
        import AppKit
        a = self.app
        defaults = AppKit.NSUserDefaults.standardUserDefaults()
        domain = dict(defaults.volatileDomainForName_(AppKit.NSArgumentDomain) or {})
        # 只覆盖测试进程，绝不写用户的系统偏好。
        defaults.setVolatileDomain_forName_({**domain, 'AppleWindowTabbingMode': 'always'}, AppKit.NSArgumentDomain)
        try:
            for pinned in (False, True):
                a.topmost_var.set(pinned)
                a.window_controller.apply_preferences()
                a.update()
                original = a.geometry()
                AppKit.NSWindow.setAllowsAutomaticWindowTabbing_(True)
                a.open_settings('window')
                a.update()
                self.assertFalse(AppKit.NSWindow.allowsAutomaticWindowTabbing())
                windows = {str(w.title()): w for w in AppKit.NSApplication.sharedApplication().windows()}
                main, settings = windows[a.title()], windows[a.settings_window.title()]
                self.assertNotEqual(main.windowNumber(), settings.windowNumber())
                for window in (main, settings):
                    self.assertEqual(window.tabbingMode(), AppKit.NSWindowTabbingModeDisallowed)
                    self.assertLessEqual(len(window.tabbedWindows() or []), 1)
                self.assertEqual(a.geometry(), original)
                self.assertFalse(a.tk.getboolean(a.attributes('-topmost')))
                self.assertEqual(a.tk.getboolean(a.settings_window.attributes('-topmost')), pinned)
                a._close_settings(a.settings_window)
                a.update()
                self.assertEqual(a.geometry(), original)
                self.assertEqual(a.tk.getboolean(a.attributes('-topmost')), pinned)
        finally:
            defaults.setVolatileDomain_forName_(domain, AppKit.NSArgumentDomain)


if __name__ == '__main__':
    unittest.main()
