"""Download a verified desktop update, then replace the application after exit."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib import request

from talkman_free_service import CA_FILE, SERVICE_URL


MANIFEST_URL = SERVICE_URL + '/updates/manifest.json'
APP_NAME = 'TALKMAN COLOPLAY.app'
BUNDLE_ID = 'com.cololight.talkman.coloplay'
MAX_MANIFEST_BYTES = 32_768
MAX_PACKAGE_BYTES = 300_000_000
VERSION_PATTERN = re.compile(r'^\d+\.\d+\.\d+\.\d+$')


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    platform: str
    path: str
    sha256: str
    size: int
    notes: str


def platform_key() -> str:
    if sys.platform == 'darwin' and platform.machine() == 'arm64':
        return 'mac-arm64'
    if sys.platform == 'win32' and platform.machine().lower() in ('amd64', 'x86_64'):
        return 'windows-x64'
    raise UpdateError('当前系统尚不支持应用内升级。')


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpdateError('更新服务器返回了跳转，已停止下载。')


def _opener():
    context = ssl.create_default_context(cafile=str(CA_FILE))
    return request.build_opener(request.HTTPSHandler(context=context), _NoRedirect())


def _version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise UpdateError('更新版本号格式不正确。')
    return tuple(map(int, value.split('.')))


def parse_manifest(payload: bytes, current_version: str, system: str) -> UpdateInfo | None:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError('更新清单过大。')
    try:
        manifest = json.loads(payload)
        if not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest['schema'] != 1:
            raise ValueError
        version = manifest['version']
        if _version(version) <= _version(current_version):
            return None
        asset = manifest['assets'][system]
        path, checksum, size = asset['path'], asset['sha256'], asset['size']
        suffix = '.dmg' if system == 'mac-arm64' else '.exe'
        if (not isinstance(path, str) or not re.fullmatch(
                r'/updates/TalkMan-COLOPLAY-[A-Za-z0-9._-]+', path)
                or not path.endswith(suffix)):
            raise ValueError
        if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError
        if type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES:
            raise ValueError
        notes = manifest.get('notes', '')
        if not isinstance(notes, str) or len(notes) > 300:
            raise ValueError
        return UpdateInfo(version, system, path, checksum, size, notes)
    except (KeyError, TypeError, ValueError, IndexError):
        raise UpdateError('更新清单无效，已停止升级。') from None


def check_update(current_version: str, *, opener=None, system=None) -> UpdateInfo | None:
    system = system or platform_key()
    try:
        with (opener or _opener()).open(MANIFEST_URL, timeout=12) as response:
            if response.status != 200:
                raise UpdateError('更新服务器暂不可用。')
            payload = response.read(MAX_MANIFEST_BYTES + 1)
        return parse_manifest(payload, current_version, system)
    except UpdateError:
        raise
    except (OSError, ValueError):
        raise UpdateError('暂时无法检查更新，请稍后重试。') from None


def download_update(info: UpdateInfo, *, opener=None) -> Path:
    folder = Path(tempfile.mkdtemp(prefix='talkman-coloplay-update-'))
    package = folder / Path(info.path).name
    digest = hashlib.sha256()
    count = 0
    try:
        with (opener or _opener()).open(SERVICE_URL + info.path, timeout=45) as response:
            if response.status != 200:
                raise UpdateError('安装包暂不可下载。')
            with package.open('xb') as output:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > info.size:
                        raise UpdateError('安装包大小不符，已停止升级。')
                    output.write(chunk)
                    digest.update(chunk)
        if count != info.size or digest.hexdigest() != info.sha256:
            raise UpdateError('安装包校验失败，未替换当前版本。')
        return package
    except UpdateError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except (OSError, ValueError):
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError('安装包下载失败，当前版本未改变。') from None


def installed_app_path() -> Path:
    if not getattr(sys, 'frozen', False):
        raise UpdateError('源码运行模式不能自动替换应用，请使用安装版。')
    executable = Path(sys.executable).resolve()
    if sys.platform == 'darwin':
        app = next((parent for parent in executable.parents if parent.suffix == '.app'), None)
        if app is None or app.name != APP_NAME:
            raise UpdateError('请先将 TALKMAN COLOPLAY.app 安装到“应用程序”。')
        return app
    if sys.platform == 'win32':
        if not (executable.parent / 'unins000.exe').is_file():
            raise UpdateError('当前是便携版；请先使用 Windows 安装器安装。')
        return executable
    raise UpdateError('当前系统尚不支持应用内升级。')


def _verify_mac_app(app: Path, version: str) -> None:
    try:
        with (app / 'Contents' / 'Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        if (info.get('CFBundleIdentifier') != BUNDLE_ID or
                info.get('CFBundleShortVersionString') + '.' + str(info.get('CFBundleVersion')) != version):
            raise UpdateError('新应用的标识或版本不符。')
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                       check=True, capture_output=True, timeout=45)
    except UpdateError:
        raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise UpdateError('新应用校验失败，当前版本未改变。') from None


def prepare_mac_update(package: Path, version: str, installed: Path) -> Path:
    if installed.suffix != '.app' or installed.name != APP_NAME or not installed.is_dir():
        raise UpdateError('未找到正在运行的 TALKMAN COLOPLAY.app。')
    _verify_mac_app(installed, _installed_mac_version(installed))
    try:
        stage_root = Path(tempfile.mkdtemp(prefix='.talkman-coloplay-update-', dir=installed.parent))
    except OSError:
        raise UpdateError('没有权限替换当前应用，请将 App 放在可写的“应用程序”目录。') from None
    stage = stage_root / APP_NAME
    mount = package.parent / 'mounted'
    attached = False
    ready = False
    try:
        mount.mkdir()
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                        '-mountpoint', str(mount), str(package)], check=True,
                       capture_output=True, timeout=90)
        attached = True
        source = mount / APP_NAME
        if not source.is_dir() or not source.resolve().is_relative_to(mount.resolve()):
            raise UpdateError('安装镜像中未找到预期的应用。')
        _verify_mac_app(source, version)
        subprocess.run(['/usr/bin/ditto', str(source), str(stage)], check=True,
                       capture_output=True, timeout=120)
        _verify_mac_app(stage, version)
        ready = True
        return stage
    except UpdateError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise UpdateError('准备 Mac 更新失败，当前版本未改变。') from None
    finally:
        if attached:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount), '-quiet'],
                           capture_output=True, timeout=45, check=False)
        try:
            mount.rmdir()
        except OSError:
            pass
        if not ready:
            shutil.rmtree(stage_root, ignore_errors=True)


def _installed_mac_version(app: Path) -> str:
    try:
        with (app / 'Contents' / 'Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        return str(info['CFBundleShortVersionString']) + '.' + str(info['CFBundleVersion'])
    except (OSError, KeyError, ValueError):
        raise UpdateError('无法确认当前 Mac 应用版本。') from None


MAC_HELPER = '''#!/bin/sh
pid="$1"
stage="$2"
target="$3"
ready="$4"
count=0
while [ ! -f "$ready" ]; do
    if [ "$count" -ge 45 ]; then exit 1; fi
    sleep 1
    count=$((count + 1))
done
count=0
while kill -0 "$pid" 2>/dev/null; do
    if [ "$count" -ge 45 ]; then exit 1; fi
    sleep 1
    count=$((count + 1))
done
backup="$(mktemp -d "$(dirname "$target")/.talkman-coloplay-backup.XXXXXX")" || exit 1
mv "$target" "$backup/" || exit 1
if ! mv "$stage" "$target"; then
    mv "$backup/$(basename "$target")" "$target"
    exit 1
fi
if ! /usr/bin/open "$target"; then
    mv "$target" "$stage"
    mv "$backup/$(basename "$target")" "$target"
    /usr/bin/open "$target"
    exit 1
fi
rmdir "$(dirname "$stage")" || true
'''


WINDOWS_HELPER = '''param([int]$OldPid, [string]$Installer, [string]$App, [string]$Ready)
$ErrorActionPreference = 'Stop'
$count = 0
while (-not (Test-Path -LiteralPath $Ready)) {
    if ($count -ge 45) { exit 1 }
    Start-Sleep -Seconds 1
    $count++
}
try { Wait-Process -Id $OldPid -Timeout 45 -ErrorAction SilentlyContinue } catch { }
if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) { exit 1 }
& $Installer '/VERYSILENT' '/SUPPRESSMSGBOXES' '/NORESTART' ("/DIR=$([IO.Path]::GetDirectoryName($App))")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath $App)) { exit 1 }
Start-Process -FilePath $App
'''


def launch_installer(package: Path, installed: Path, version: str, *, prepared_stage=None) -> Path:
    folder = package.parent
    ready = folder / 'ready-to-install'
    if sys.platform == 'darwin':
        stage = prepared_stage or prepare_mac_update(package, version, installed)
        helper = folder / 'install-mac.sh'
        helper.write_text(MAC_HELPER, encoding='utf-8')
        command = ['/bin/sh', str(helper), str(os.getpid()), str(stage), str(installed), str(ready)]
    elif sys.platform == 'win32':
        helper = folder / 'install-windows.ps1'
        helper.write_text(WINDOWS_HELPER, encoding='utf-8')
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', str(helper), '-OldPid', str(os.getpid()),
                   '-Installer', str(package), '-App', str(installed), '-Ready', str(ready)]
    else:
        raise UpdateError('当前系统尚不支持应用内升级。')
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise UpdateError('升级程序未能启动，当前版本未改变。') from None
    return ready


def discard_prepared_update(package: Path, stage: Path | None) -> None:
    if stage and stage.parent.name.startswith('.talkman-coloplay-update-'):
        shutil.rmtree(stage.parent, ignore_errors=True)
    if package.parent.name.startswith('talkman-coloplay-update-'):
        shutil.rmtree(package.parent, ignore_errors=True)
