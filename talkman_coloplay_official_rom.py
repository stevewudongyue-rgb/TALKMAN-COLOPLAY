"""Prepare a verified ROM from the public TalkMan download page for official import."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen


DOWNLOAD_PAGE = 'https://beats-manufacturers-flush-review.trycloudflare.com/about/downloads/'
ROM_NAME = 'TalkMan_COLOPLAY.rom'
MAX_ROM_BYTES = 184 * 1024
ROM_LINK = re.compile(r'^/static/[^/]+/assets/downloads/coloplay-cartridge-(\d+)/TalkMan_COLOPLAY\.rom$')
HASH_LINK = re.compile(r'^/static/[^/]+/assets/downloads/coloplay-sha256\.txt$')


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.hrefs.append(href)


def _read(url, *, max_bytes, opener=urlopen):
    with opener(url, timeout=15) as response:
        final = response.geturl()
        if urlparse(final).scheme != 'https' or urlparse(final).netloc != urlparse(DOWNLOAD_PAGE).netloc:
            raise ValueError('下载地址已跳转到非官网域名')
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('下载内容超出卡带大小限制')
    return data


def download_latest_rom(downloads: Path, *, opener=urlopen):
    """Return (ROM path, build); never replace a local ROM before hash verification."""
    page = _read(DOWNLOAD_PAGE, max_bytes=512 * 1024, opener=opener)
    parser = _Links()
    parser.feed(page.decode('utf-8'))
    candidates = [(int(match.group(1)), href) for href in parser.hrefs if (match := ROM_LINK.fullmatch(href))]
    hashes = [href for href in parser.hrefs if HASH_LINK.fullmatch(href)]
    if not candidates or not hashes:
        raise ValueError('官网未提供卡带文件或校验表')
    build, rom_href = max(candidates)
    hash_href = next((href for href in hashes if href.split('/assets/')[0] == rom_href.split('/assets/')[0]), None)
    if not hash_href:
        raise ValueError('卡带与校验表不属于同一官网版本')
    checksums = _read(urljoin(DOWNLOAD_PAGE, hash_href), max_bytes=32 * 1024, opener=opener).decode('ascii')
    relative = f'coloplay-cartridge-{build}/{ROM_NAME}'
    expected = next((line.split()[0].lower() for line in checksums.splitlines()
                     if len(line.split()) == 2 and line.split()[1] == relative
                     and re.fullmatch(r'[0-9a-fA-F]{64}', line.split()[0])), None)
    if not expected:
        raise ValueError('官网校验表缺少当前卡带')
    data = _read(urljoin(DOWNLOAD_PAGE, rom_href), max_bytes=MAX_ROM_BYTES, opener=opener)
    if not data or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('卡带校验失败，未替换本地文件')
    directory = Path(downloads) / 'TalkMan' / 'COLOPLAY' / f'卡带-{build}'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ROM_NAME
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        temporary = directory / (ROM_NAME + '.download')
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path, build


def open_official_app_and_reveal(path: Path):
    """Open the official app and reveal the ROM; it has no known import deep link."""
    path = Path(path).resolve()
    opened = False
    if sys.platform == 'win32':
        roots = [os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')]
        executable = next((candidate for root in roots if root
                           if (candidate := Path(root) / 'LifeSmart Inc' / 'Colo Play Desktop' / 'ColoPlayDesktop.exe').is_file()), None)
        if executable:
            os.startfile(str(executable))
            opened = True
        subprocess.Popen(['explorer.exe', '/select,' + str(path)])
    elif sys.platform == 'darwin':
        opened = subprocess.run(['open', '-a', 'Colo Play Desktop'], check=False).returncode == 0
        subprocess.Popen(['open', '-R', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path.parent)])
    return opened
