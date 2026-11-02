"""Mac 引导回归：隔离数据，假权限与假连接，不录音、不调用在线服务。"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import talkman_mac_guide as guide
from talkman_macos_setup import MacSetupStatus, settings_path
import test_talkman_window as window_tests


class MacGuideTests(unittest.TestCase):
    def setUp(self):
        window_tests.WindowTests.setUp(self)
        self.state = MacSetupStatus('26.1', 0, False, Path('/Applications/TALKMAN COLOPLAY.app'))
        self.permissions = patch.object(guide, 'check_permissions', side_effect=lambda: self.state)
        self.addresses = patch.object(guide, 'local_addresses', return_value=['192.168.110.57'])
        self.permissions.start(); self.addresses.start()
        self.addCleanup(self.permissions.stop); self.addCleanup(self.addresses.stop)

    tearDown = window_tests.WindowTests.tearDown

    def open(self):
        with patch('talkman_app.mac_guide_enabled', return_value=True):
            self.app.show_quick_guide()
        self.app.update()
        return self.app.guide

    def test_mac_first_run_migration_skip_resume_and_no_fake_completion(self):
        self.assertTrue(guide.should_offer_mac_guide({'guide_seen': True, 'guide_version': 2}))
        g = self.open(); g.show(3); g.close()
        self.assertFalse(guide.should_offer_mac_guide(self.app.ui_preferences))
        g = self.open()
        self.assertEqual(g.page, 3)
        self.assertIn('尚未连接', g.status.get())
        self.assertEqual(g.labels[1].get(), '等待首次授权')
        self.assertIs(self.app.grab_current(), g.panel)

    def test_permissions_and_connection_are_never_requested_by_browsing(self):
        with patch.object(guide, 'prepare_microphone_permission') as microphone, \
                patch.object(guide, 'open_permission_settings') as settings, \
                patch.object(self.app.coloplay, 'configure') as connect, \
                patch.object(self.app.free_service, 'call') as service:
            g = self.open()
            for page in range(6): g.show(page)
            microphone.assert_not_called(); settings.assert_not_called()
            connect.assert_not_called(); service.assert_not_called()
        self.assertFalse(self.app.busy)

    def test_microphone_callback_after_navigation_never_starts_recording(self):
        g = self.open(); g.show(1)
        callbacks = []
        def request(callback): callbacks.append(callback); return False
        with patch.object(guide, 'prepare_microphone_permission', side_effect=request) as permission:
            g.allow_microphone(); g.allow_microphone()
            permission.assert_called_once()
        self.assertTrue(self.app._mic_permission_pending)
        g.show(2)
        callbacks[0](True, '已允许')
        self.app.poll_events()
        self.state = MacSetupStatus('26.1', 3, False, self.state.app)
        g.refresh_status()
        self.assertFalse(self.app._mic_permission_pending)
        self.assertFalse(self.app.busy)
        self.assertEqual(g.labels[1].get(), '已允许')

    def test_closed_guide_callback_is_safe_and_releases_recording_lock(self):
        g = self.open(); callback = []
        with patch.object(guide, 'prepare_microphone_permission', side_effect=lambda cb: callback.append(cb) or False):
            g.allow_microphone()
        g.close(); callback[0](False, '未允许'); self.app.poll_events()
        self.assertFalse(self.app._mic_permission_pending)
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.grab_current())
        self.assertIsNone(g.job)

    def test_connection_save_preserves_pairing_keys_and_preferences(self):
        a = self.app; d = a.coloplay; g = self.open(); g.show(3)
        original = dict(d.config)
        g.host.set('192.168.110.57'); g.device_host.set('192.168.110.209')
        def configure(cfg): d.config = cfg; d.server = Mock()
        try:
            with patch.object(d, 'configure', side_effect=configure):
                g.save_connection()
            import json
            saved = json.loads(d.path.read_text('utf-8'))
            for name in ('token', 'bindings', 'preferences'):
                self.assertEqual(saved[name], original[name])
            self.assertEqual(saved['host'], '192.168.110.57')
            self.assertEqual(saved['device_host'], '192.168.110.209')
            self.assertTrue(saved['enabled']); self.assertTrue(saved['discovery'])
            self.assertIn('等待设备', g.status.get())
        finally:
            d.server = None

    def test_invalid_ip_and_port_failure_do_not_claim_connected(self):
        g = self.open(); g.show(3); g.host.set('不是IP')
        with patch.object(self.app.coloplay, 'configure') as configure:
            g.save_connection(); configure.assert_not_called()
        self.assertIn('连接未完成', g.feedback.get())
        g.host.set('192.168.110.57')
        with patch.object(self.app.coloplay, 'configure'):
            g.save_connection()
        self.assertIn('服务未启动', g.feedback.get())
        self.assertFalse(self.app.coloplay.path.exists())

    def test_refresh_does_not_replace_device_ip_and_multiple_interfaces_require_choice(self):
        g = self.open(); g.show(3); g.device_host.set('192.168.110.209')
        with patch.object(guide, 'local_addresses', return_value=['192.168.1.12', '10.1.2.3']):
            old = g.host.get(); g.refresh_addresses()
            self.assertEqual(g.host.get(), old)
        with patch.object(guide, 'local_addresses', return_value=['192.168.1.12']):
            g.refresh_addresses(); self.assertEqual(g.host.get(), '192.168.1.12')
        self.assertEqual(g.device_host.get(), '192.168.110.209')

    def test_service_opt_in_keeps_key_and_does_not_probe_model(self):
        self.app.settings.api_key = 'sk-offline-test'
        g = self.open(); g.show(2)
        with patch.object(self.app.free_service, 'call') as request:
            g.use_free_service(); request.assert_not_called()
        self.assertTrue(self.app.free_mode_var.get())
        self.assertEqual(self.app.settings.api_key, 'sk-offline-test')
        self.assertIn('待使用验证', g.status.get())

    def test_settings_detour_returns_to_same_page_without_changing_draft(self):
        a = self.app; a.set_text('测试草稿，请保留。'); a.text.tag_add('sel', '1.0', '1.4')
        before = (a.current_text(), a.get_selection(), a.text.index('insert'))
        g = self.open(); g.show(2); g.open_settings('model'); a.update()
        self.assertTrue(g.waiting_settings); self.assertIsNone(a.grab_current())
        a._close_settings(a.settings_window); a.update()
        self.assertEqual(g.page, 2); self.assertFalse(g.waiting_settings)
        self.assertIs(a.grab_current(), g.panel)
        self.assertEqual((a.current_text(), a.get_selection(), a.text.index('insert')), before)

    def test_connection_state_distinguishes_simulator_and_real_peer(self):
        g = self.open(); g.show(3); d = self.app.coloplay
        d.peer = object(); g.refresh_status(); self.assertIn('Wi-Fi 已连通', g.status.get())
        d.simulator = SimpleNamespace(close=lambda: None)
        g.refresh_status(); self.assertIn('仅模拟', g.status.get())
        d.simulator = None; d.peer = None

    def test_all_pages_fit_small_window_and_three_themes(self):
        g = self.open(); a = self.app
        a.geometry('760x520'); a.update()
        for theme in ('matrix', 'dark', 'light'):
            a.set_theme(theme); a.update()
            for page in range(6):
                g.show(page); a.update()
                self.assertLessEqual(g.next.winfo_rooty()+g.next.winfo_height(), g.panel.winfo_rooty()+g.panel.winfo_height())
                for child in g.body.winfo_children():
                    self.assertTrue(child.winfo_viewable(), (theme, page, child))
                    self.assertLessEqual(child.winfo_rooty()+child.winfo_height(), g.body.winfo_rooty()+g.body.winfo_height(), (theme, page, child))
                self.assertFalse(a.busy)

    def test_installation_and_local_network_path_are_truthful(self):
        self.assertTrue(guide.installed_location(Path('/Applications/TALKMAN COLOPLAY.app')))
        self.assertFalse(guide.installed_location(Path('/Volumes/TalkMan/TALKMAN COLOPLAY.app')))
        self.assertFalse(guide.installed_location(None))
        self.assertEqual(settings_path('local_network', '26.1'), '系统设置 → 隐私与安全性 → 本地网络')


if __name__ == '__main__':
    unittest.main()
