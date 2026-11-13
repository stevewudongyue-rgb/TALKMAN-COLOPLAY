"""The official ROM is only exposed after the website checksum matches."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from talkman_coloplay_official_rom import DOWNLOAD_PAGE, ROM_NAME, download_latest_rom


class _Response:
    def __init__(self, url, data):
        self.url, self.data = url, data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def geturl(self):
        return self.url

    def read(self, size):
        return self.data[:size]


class OfficialRomTests(unittest.TestCase):
    def setUp(self):
        self.root = DOWNLOAD_PAGE.split('/about/')[0]
        self.prefix = '/static/current/assets/downloads/'
        self.rom = b'ROM fixture 72'
        self.checksums = (hashlib.sha256(self.rom).hexdigest() +
                          f'  coloplay-cartridge-72/{ROM_NAME}\n').encode()
        self.html = (f'<a href="{self.prefix}coloplay-cartridge-71/{ROM_NAME}"></a>'
                     f'<a href="{self.prefix}coloplay-cartridge-72/{ROM_NAME}"></a>'
                     f'<a href="{self.prefix}coloplay-sha256.txt"></a>').encode()

    def opener(self, url, timeout):
        self.assertEqual(timeout, 15)
        if url == DOWNLOAD_PAGE:
            return _Response(url, self.html)
        if url.endswith('coloplay-sha256.txt'):
            return _Response(url, self.checksums)
        if url.endswith(f'coloplay-cartridge-72/{ROM_NAME}'):
            return _Response(url, self.rom)
        raise AssertionError(url)

    def test_latest_verified_rom_is_saved_under_its_build(self):
        with tempfile.TemporaryDirectory() as folder:
            path, build = download_latest_rom(Path(folder), opener=self.opener)
            self.assertEqual(build, 72)
            self.assertEqual(path.read_bytes(), self.rom)
            self.assertEqual(path.name, ROM_NAME)
            self.assertEqual(path.parent.name, '卡带-72')

    def test_bad_checksum_preserves_existing_rom(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = download_latest_rom(Path(folder), opener=self.opener)
            self.rom = b'changed without new checksum'
            with self.assertRaisesRegex(ValueError, '校验失败'):
                download_latest_rom(Path(folder), opener=self.opener)
            self.assertEqual(path.read_bytes(), b'ROM fixture 72')

    def test_unrelated_origin_is_rejected(self):
        def opener(url, timeout):
            return _Response('https://other.example/rom', self.html)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, '非官网域名'):
                download_latest_rom(Path(folder), opener=opener)


if __name__ == '__main__':
    unittest.main()
