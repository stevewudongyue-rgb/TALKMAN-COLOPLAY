"""Update verification never accesses a real server or installer."""
import hashlib
import io
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import talkman_updates as updates
from talkman_settings_ui import SettingsPanelMixin


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def open(self, url, timeout):
        self.urls.append(url)
        return Response(self.payload)


def manifest(content, *, version='0.1.28.52', path=None):
    return json.dumps({'schema': 1, 'version': version, 'assets': {
        'mac-arm64': {'path': path or '/updates/TalkMan-COLOPLAY-0.1.28.52-Mac-AppleSilicon.dmg',
                      'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)},
    }, 'notes': '测试更新'}, ensure_ascii=False).encode()


class UpdateTests(unittest.TestCase):
    def test_newer_version_and_current_version(self):
        content = b'installer'
        opener = Opener(manifest(content))
        found = updates.check_update('0.1.28.51', opener=opener, system='mac-arm64')
        self.assertEqual(found.version, '0.1.28.52')
        self.assertEqual(opener.urls, [updates.MANIFEST_URL])
        self.assertIsNone(updates.parse_manifest(manifest(content, version='0.1.28.51'),
                                                 '0.1.28.51', 'mac-arm64'))

    def test_rejects_untrusted_package_path_and_oversize_manifest(self):
        for path in ('https://other.example/evil.dmg', '/updates/../evil.dmg',
                     '/updates/TalkMan-COLOPLAY-evil.exe'):
            with self.subTest(path=path), self.assertRaises(updates.UpdateError):
                updates.parse_manifest(manifest(b'x', path=path), '0.1.28.51', 'mac-arm64')
        with self.assertRaises(updates.UpdateError):
            updates.parse_manifest(b'x' * (updates.MAX_MANIFEST_BYTES + 1),
                                   '0.1.28.51', 'mac-arm64')

    def test_download_requires_exact_size_and_sha256(self):
        content = b'a small test package'
        info = updates.parse_manifest(manifest(content), '0.1.28.51', 'mac-arm64')
        with tempfile.TemporaryDirectory() as parent, patch.object(updates.tempfile, 'tempdir', parent):
            package = updates.download_update(info, opener=Opener(content))
            self.assertEqual(package.read_bytes(), content)
            with self.assertRaises(updates.UpdateError):
                updates.download_update(info, opener=Opener(content + b'wrong'))
            with self.assertRaises(updates.UpdateError):
                updates.download_update(info, opener=Opener(b'wrong'))

    def test_portable_windows_build_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'TALKMAN COLOPLAY.exe'
            executable.touch()
            with patch.object(updates.sys, 'platform', 'win32'), \
                    patch.object(updates.sys, 'frozen', True, create=True), \
                    patch.object(updates.sys, 'executable', str(executable)):
                with self.assertRaises(updates.UpdateError):
                    updates.installed_app_path()
                (Path(folder) / 'unins000.exe').touch()
                self.assertEqual(updates.installed_app_path(), executable)

    def test_one_click_starts_installer_only_after_close(self):
        class Widget:
            def winfo_exists(self):
                return True
            def state(self, flags):
                pass
        class Feedback:
            value = ''
            def set(self, value):
                self.value = value
        class Panel(SettingsPanelMixin):
            busy = target_filling = closing = False
            ai_review = None
            events = queue.Queue()
            def close_app(self):
                self.closing = True

        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / 'package.dmg'
            ready = Path(folder) / 'ready'
            info = updates.UpdateInfo('0.1.28.52', 'mac-arm64', '/updates/package.dmg', '0' * 64, 1, '')
            panel, feedback = Panel(), Feedback()
            with patch('talkman_settings_ui.installed_app_path', return_value=Path(folder)), \
                    patch('talkman_settings_ui.check_update', return_value=info), \
                    patch('talkman_settings_ui.download_update', return_value=package), \
                    patch('talkman_settings_ui.prepare_mac_update', return_value=Path(folder) / 'stage'), \
                    patch('talkman_settings_ui.launch_installer', return_value=ready) as launch:
                panel._start_app_update(Widget(), Widget(), feedback)
                kind, callback, state, result = panel.events.get(timeout=3)
                self.assertEqual(kind, 'check')
                self.assertFalse(panel.closing)
                callback(state, result)
            launch.assert_called_once()
            self.assertTrue(panel.closing)
            self.assertTrue(ready.is_file())

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS shell only')
    def test_mac_install_helper_has_valid_shell_syntax(self):
        result = subprocess.run(['/bin/sh', '-n'], input=updates.MAC_HELPER,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS bundle only')
    def test_built_mac_dmg_stages_verified_application(self):
        from talkman_version import FULL_VERSION
        root = Path(__file__).resolve().parent
        images = list((root / 'dist-mac').glob('*.dmg'))
        apps = list((root / 'dist-mac').glob('*.app'))
        if not images or not apps:
            self.skipTest('Mac build artifact not present')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            package = base / images[0].name
            target = base / updates.APP_NAME
            shutil.copy2(images[0], package)
            subprocess.run(['/usr/bin/ditto', str(apps[0]), str(target)], check=True)
            staged = updates.prepare_mac_update(package, FULL_VERSION, target)
            self.assertTrue((staged / 'Contents' / 'Info.plist').is_file())
            shutil.rmtree(staged.parent)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS shell only')
    def test_mac_helper_swaps_bundle_after_ready_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / updates.APP_NAME
            target.mkdir()
            (target / 'old').touch()
            stage_root = base / '.talkman-coloplay-update-test'
            stage_root.mkdir()
            stage = stage_root / updates.APP_NAME
            stage.mkdir()
            (stage / 'new').touch()
            ready = base / 'ready'
            ready.touch()
            script = updates.MAC_HELPER.replace('/usr/bin/open "$target"', '/usr/bin/true')
            result = subprocess.run(['/bin/sh', '-c', script, 'update-test', '999999999',
                                     str(stage), str(target), str(ready)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / 'new').is_file())
            backups = list(base.glob('.talkman-coloplay-backup.*'))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / updates.APP_NAME / 'old').is_file())


if __name__ == '__main__':
    unittest.main()
