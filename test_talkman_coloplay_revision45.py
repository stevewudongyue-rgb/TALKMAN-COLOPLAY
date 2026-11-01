"""新安装自动免费接入；所有网络与 Mac 钥匙串请求均隔离。"""
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import talkman_free_service as free
from talkman_credentials import CredentialError
from talkman_qwen import QwenError


def response(value):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(value).encode()
    return result


class AutomaticServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TalkMan-免费测试-')
        self.directory = Path(self.temp.name)
        self.service = free.FreeService(self.directory)
        self.opener = Mock()
        self.opener.open.side_effect = [response({'ready': True}), response({'asr_configured': True})]
        self.patch = patch.object(self.service, '_https_opener', return_value=self.opener)
        self.patch.start()

    def tearDown(self):
        self.service.close()
        self.patch.stop()
        self.temp.cleanup()

    def test_new_install_enrolls_over_https_without_ssh_and_persists_encrypted_identity(self):
        with patch('talkman_free_service.subprocess.Popen') as ssh:
            self.assertTrue(self.service.call('/v1/status')['asr_configured'])
            ssh.assert_not_called()
        urls = [call.args[0].full_url for call in self.opener.open.call_args_list]
        self.assertEqual(urls, [free.SERVICE_URL+'/v1/enroll', free.SERVICE_URL+'/v1/status'])
        token = self.service._profile['token']
        self.assertNotIn(token.encode(), (self.directory/free.PROFILE).read_bytes())
        reopened = free.FreeService(self.directory)
        self.assertEqual(reopened._load_profile()['token'], token)
        reopened.close()

    def test_two_installations_never_share_identity(self):
        first = self.service._load_profile()['token']
        other = free.FreeService(self.directory/'other')
        self.assertNotEqual(other._load_profile()['token'], first)
        other.close()

    def test_network_error_preserves_identity_and_retry_is_idempotent(self):
        self.opener.open.side_effect = URLError('offline')
        with self.assertRaises(QwenError):
            self.service.call('/v1/status')
        token = self.service._profile['token']
        self.opener.open.side_effect = [response({'ready': True}), response({'asr_configured': True})]
        self.service.call('/v1/status')
        self.assertEqual(self.service._profile['token'], token)

    def test_mac_uses_keychain_and_same_https_path(self):
        secrets = {}
        def save(_directory, group, value):
            secrets[group] = value
        with patch.object(free.sys, 'platform', 'darwin'), \
             patch.object(free, 'save_keychain_secret', side_effect=save), \
             patch.object(free, 'load_keychain_secret', side_effect=lambda *_: secrets.get('free')):
            self.service.call('/v1/status')
            reopened = free.FreeService(self.directory)
            self.assertEqual(reopened._load_profile()['token'], self.service._profile['token'])
            self.assertEqual([p.name for p in self.directory.iterdir()], ['free-service.enabled'])
            reopened.close()

    def test_locked_keychain_uses_session_without_plaintext_or_dialog(self):
        with patch.object(free.sys, 'platform', 'darwin'), \
             patch.object(free, 'load_keychain_secret', side_effect=CredentialError('locked')), \
             patch.object(free, 'save_keychain_secret', side_effect=CredentialError('locked')):
            self.service.call('/v1/status')
            self.assertTrue(self.service.storage_notice)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_certificate_error_and_redirect_never_fall_back_to_http(self):
        for exc in (URLError(ssl.SSLCertVerificationError('bad certificate')),
                    HTTPError(free.SERVICE_URL, 302, 'redirect', {}, io.BytesIO())):
            self.opener.open.side_effect = exc
            with self.assertRaises(QwenError):
                self.service.call('/v1/status')
        self.assertIsNone(self.service._process)
        self.assertTrue(all(call.args[0].full_url.startswith('https://') for call in self.opener.open.call_args_list))

    def test_revoked_identity_is_not_regenerated_to_bypass_server(self):
        self.opener.open.side_effect = HTTPError(free.SERVICE_URL, 403, 'revoked', {}, io.BytesIO())
        for _ in range(2):
            with self.assertRaisesRegex(QwenError, '停用'):
                self.service.call('/v1/status')
        tokens = [json.loads(call.args[0].data)['token'] for call in self.opener.open.call_args_list]
        self.assertEqual(tokens[0], tokens[1])

    def test_default_new_install_and_preserved_existing_model_choice(self):
        self.assertTrue(free.default_free_mode({}))
        self.assertFalse(free.default_free_mode({}, own_model=True))
        self.assertFalse(free.default_free_mode({'free_mode': False}))
        self.assertTrue(free.default_free_mode({'free_mode': True}, own_model=True))

    def test_close_prevents_enrollment_and_network(self):
        self.service.close()
        with self.assertRaises(QwenError):
            self.service.call('/v1/status')
        self.opener.open.assert_not_called()

    def test_fresh_gui_defaults_to_free_and_guide_has_no_key_requirement(self):
        from talkman_app import TalkManApp
        app = TalkManApp(self.directory/'fresh-ui', desktop_services=False)
        try:
            app.withdraw()
            self.assertTrue(app.free_mode_var.get())
            self.assertTrue(app.text_model_ready())
            self.assertEqual(app.settings.api_key, '')
            app.show_quick_guide()
            app.guide.show(0)
            self.assertFalse(hasattr(app.guide, 'key_entry'))
            self.assertEqual(app.current_text(), '')
        finally:
            app.close_app()


if __name__ == '__main__':
    unittest.main()
