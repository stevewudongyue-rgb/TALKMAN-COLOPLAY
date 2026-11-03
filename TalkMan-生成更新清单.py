"""Generate a checked manifest for the private update service; does not upload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def asset(path: Path, version: str, extension: str) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'安装包不存在：{path}')
    if not path.name.startswith(f'TalkMan-COLOPLAY-{version}-') or path.suffix != extension:
        raise ValueError(f'安装包名称与版本不符：{path.name}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {'path': '/updates/' + path.name, 'sha256': digest.hexdigest(),
            'size': path.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--mac', type=Path, required=True)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.version.split('.')) != 4 or not all(part.isdigit() for part in args.version.split('.')):
        parser.error('版本号应为四段数字')
    manifest = {'schema': 1, 'version': args.version, 'notes': '保留原有设置、草稿和设备配对。',
                'assets': {'mac-arm64': asset(args.mac, args.version, '.dmg'),
                           'windows-x64': asset(args.windows, args.version, '.exe')}}
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
