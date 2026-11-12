"""Prepare an isolated Mac build archive from the current COLOPLAY source tree."""
from pathlib import Path
import hashlib
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / 'TalkMan-COLOPLAY文件' / '17-桌面77与通用卡带71'
ARCHIVE = OUTPUT / 'TalkMan-COLOPLAY-77-source.tar.gz'


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    selected = [name for name in tracked if name and (
        ('/' not in name and (name.endswith('.py') or name in (
            'TalkMan-requirements.txt', 'TalkMan-Mac.entitlements', 'TalkMan-构建Mac.command')))
        or name.startswith(('TalkMan-assets/', 'TalkMan-设备卡带/')))]
    selected += ['TalkMan-安装器77.iss', 'TalkMan-准备77.py', 'test_talkman_usb_universal.py']
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, 'w:gz') as archive:
        for name in sorted(set(selected)):
            source = ROOT / name
            if source.is_file():
                archive.add(source, arcname='tree/' + name, recursive=False)
    data = ARCHIVE.read_bytes()
    print(f'{ARCHIVE}\n{len(data)} bytes\nSHA256 {hashlib.sha256(data).hexdigest()}')


if __name__ == '__main__':
    main()
