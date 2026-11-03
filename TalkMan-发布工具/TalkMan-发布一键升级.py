"""上传已验收的两端安装包，客户端下载校验后原子发布清单，保留旧清单备份。"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from talkman_updates import (
    MANIFEST_URL, MAX_MANIFEST_BYTES, _opener, _version, check_update,
    download_update, parse_manifest,
)

HOST = 'ubuntu@124.222.127.127'
KEY = Path.home() / '.ssh' / 'talkman-lighthouse-test'
OPTIONS = ['-i', str(KEY), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=8']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def live_manifest():
    with _opener().open(MANIFEST_URL, timeout=15) as response:
        assert response.status == 200
        return response.read(MAX_MANIFEST_BYTES + 1)


def remote(code):
    result = subprocess.run(['ssh', *OPTIONS, HOST, 'sudo -n /usr/bin/python3 -'],
                            input=code.encode('utf-8'), capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[-1600:])
    print(result.stdout.decode('utf-8', errors='replace').strip(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--mac', type=Path, required=True)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--expected-current', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    payload = args.manifest.read_bytes()
    version = json.loads(payload)['version']
    assert _version(version) > _version(args.expected_current)
    infos = {system: parse_manifest(payload, args.expected_current, system)
             for system in ('mac-arm64', 'windows-x64')}
    for path, system in ((args.mac, 'mac-arm64'), (args.windows, 'windows-x64')):
        info = infos[system]
        assert path.is_file() and not path.is_symlink()
        assert path.name == Path(info.path).name and path.name.startswith(f'TalkMan-COLOPLAY-{version}-')
        assert path.stat().st_size == info.size and sha(path.read_bytes()) == info.sha256
    old = live_manifest()
    assert json.loads(old)['version'] == args.expected_current, '线上版本已变化，停止发布'
    old_hash, new_hash = sha(old), sha(payload)
    stage = '/home/ubuntu/talkman-upload/coloplay-update-' + version
    subprocess.run(['ssh', *OPTIONS, HOST, 'mkdir -p ' + shlex.quote(stage)], check=True, timeout=20)
    subprocess.run(['scp', *OPTIONS, str(args.mac), str(args.windows), HOST + ':' + stage + '/'],
                   check=True, timeout=600)
    prelude = (
        'import base64,hashlib,json,os,pathlib,shutil\n'
        "root=pathlib.Path('/var/lib/talkman-coloplay/updates')\n"
        f'payload=base64.b64decode({base64.b64encode(payload).decode()!r})\n'
        'manifest=json.loads(payload)\n'
        "live=root/'manifest.json'\n"
        'def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()\n'
        'def owned(path):\n'
        '    state=live.stat();os.chown(path,state.st_uid,state.st_gid);path.chmod(0o640)\n'
    )
    remote(prelude + f'''
assert sha(live)=={old_hash!r}, '线上清单已变化，停止暂存'
stage=pathlib.Path({stage!r})
for asset in manifest['assets'].values():
    source=stage/pathlib.Path(asset['path']).name
    assert source.is_file() and not source.is_symlink()
    assert source.stat().st_size==asset['size'] and sha(source)==asset['sha256']
    destination=root/source.name
    if destination.exists():
        assert sha(destination)==asset['sha256'], '同版本已有不同安装包，禁止覆盖'
    else:
        pending=root/(source.name+'.pending')
        shutil.copyfile(source,pending)
        assert sha(pending)==asset['sha256']
        owned(pending);pending.replace(destination)
print('服务器安装包已就绪，线上清单暂不切换。')
''')
    checked = {}
    for system, info in infos.items():
        print('正在通过客户端校验下载：' + system, flush=True)
        package = download_update(info)
        try:
            checked[system] = {'sha256': sha(package.read_bytes()), 'size': package.stat().st_size}
            assert checked[system] == {'sha256': info.sha256, 'size': info.size}
        finally:
            # 仅清理本次 download_update 新建的专属临时目录。
            assert (package.parent.name.startswith('talkman-coloplay-update-')
                    and not package.parent.is_symlink()
                    and package.parent.resolve().parent == Path(tempfile.gettempdir()).resolve())
            shutil.rmtree(package.parent)
    remote(prelude + f'''
assert sha(live)=={old_hash!r}, '线上清单已变化，停止发布'
for asset in manifest['assets'].values():
    path=root/pathlib.Path(asset['path']).name
    assert path.stat().st_size==asset['size'] and sha(path)==asset['sha256']
backup=root/{('manifest.' + args.expected_current + '.backup.json')!r}
if backup.exists():
    assert sha(backup)=={old_hash!r}, '已有备份不一致，停止发布'
else:
    backup.write_bytes(live.read_bytes());owned(backup)
pending=root/{('manifest.' + version + '.pending.json')!r}
pending.write_bytes(payload);owned(pending);pending.replace(live)
print('一键升级清单已切换到 '+manifest['version'])
''')
    try:
        assert live_manifest() == payload
        for system in infos:
            assert check_update(args.expected_current, system=system) == infos[system]
            assert check_update(version, system=system) is None
    except Exception:
        remote(prelude + f'''
assert sha(live)=={new_hash!r}, '线上清单再次变化，禁止覆盖其它发布'
backup=root/{('manifest.' + args.expected_current + '.backup.json')!r}
assert sha(backup)=={old_hash!r}
pending=root/'manifest.rollback.pending.json'
pending.write_bytes(backup.read_bytes());owned(pending);pending.replace(live)
print('公网核验失败，已恢复原清单。')
''')
        raise
    report = {'版本': version, '原版本': args.expected_current,
              '更新清单SHA256': new_hash, '原清单SHA256': old_hash,
              '客户端下载验证': checked, '旧版发现更新': True, '新版不重复更新': True,
              '设备卡带': '按更新说明从各电脑导出；不上传私人配对 ROM。'}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
