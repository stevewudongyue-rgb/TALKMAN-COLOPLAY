"""安装即用的免费服务；HTTPS 自动接入，仅持有独立设备令牌。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import ssl
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib import request, error

from talkman_credentials import load_keychain_secret, save_keychain_secret, CredentialError
from talkman_qwen import QwenClient, QwenSettings, QwenError, _dpapi, _atomic_write, _NoRedirect

NOTICE = '免费服务受服务方政策和额度影响，可能调整、暂停或不可用。'
PROFILE = 'free-service.dpapi'
SERVICE_URL = 'https://124.222.127.127'
CA_FILE = Path(__file__).resolve().parent / 'TalkMan-assets' / 'TalkMan-free-service-ca.pem'


def default_free_mode(preferences, *, own_model=False):
    """尊重明确的服务选择；全新安装和未配模型的旧安装默认免费。"""
    if isinstance(preferences.get('free_mode'), bool):
        return preferences['free_mode']
    return not own_model


def save_profile(directory, profile):
    """设备凭证加密存本机；安装包仅包含公开地址和 CA 证书。"""
    data = json.dumps(profile).encode('utf-8')
    if sys.platform == 'darwin':
        save_keychain_secret(Path(directory), 'free', data)
        _atomic_write(Path(directory) / 'free-service.enabled', b'1')
    elif os.name == 'nt':
        _atomic_write(Path(directory) / PROFILE, _dpapi(data, decrypt=False))
    else:
        raise QwenError('本轮免费内测支持 Windows 和 Mac。')


class FreeService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._process = None
        self._port = None
        self._profile = None
        self._closed = False
        self._text_retry_at = 0
        self._enrolled_token = None
        self.storage_notice = ''

    @property
    def configured(self):
        name = 'free-service.enabled' if sys.platform == 'darwin' else PROFILE
        return CA_FILE.is_file() or (self.directory / name).is_file()

    def _new_public_profile(self):
        value = {'transport': 'https', 'token': secrets.token_urlsafe(32)}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            save_profile(self.directory, value)
        except (OSError, CredentialError, QwenError):
            # 自动生成的免费身份可仅在当前会话使用；不降级为明文文件。
            self.storage_notice = '免费连接本次可用；系统未能保存连接，下次打开会自动重新接入。'
        self._profile = value
        return value

    def _load_profile(self):
        if self._profile is not None:
            return self._profile
        try:
            if sys.platform != 'darwin' and (self.directory / PROFILE).stat().st_size > 65536:
                raise ValueError
            data = (load_keychain_secret(self.directory, 'free') if sys.platform == 'darwin'
                    else _dpapi((self.directory / PROFILE).read_bytes(), decrypt=True))
            value = json.loads(data)
            if (isinstance(value, dict) and value.get('transport') == 'https'
                    and re.fullmatch(r'[A-Za-z0-9_-]{43}', value.get('token', ''))):
                self._profile = value
                return value
            if CA_FILE.is_file():
                return self._new_public_profile()
            if (not isinstance(value, dict) or not re.fullmatch(r'[a-zA-Z0-9.-]+', value['host'])
                    or not re.fullmatch(r'[a-zA-Z0-9_-]+', value['user'])
                    or not re.fullmatch(r'[a-zA-Z0-9_-]{32,256}', value['token'])
                    or any(not Path(value[k]).is_file() for k in ('identity', 'known_hosts'))):
                raise ValueError
            self._profile = value
            return value
        except (OSError, ValueError, TypeError, KeyError, CredentialError, QwenError):
            if CA_FILE.is_file():
                return self._new_public_profile()
            raise QwenError('这台电脑的免费内测连接未开通或已失效，请联系维护者；也可切回自己的模型。') from None

    def _https_opener(self):
        try:
            context = ssl.create_default_context(cafile=str(CA_FILE))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return request.build_opener(request.ProxyHandler({}), _NoRedirect(),
                                        request.HTTPSHandler(context=context))
        except (OSError, ssl.SSLError):
            raise QwenError('免费服务证书缺失或无效，请重新安装新版程序。') from None

    def _enroll(self, token):
        if self._enrolled_token == token:
            return
        req = request.Request(SERVICE_URL + '/v1/enroll',
            data=json.dumps({'token': token}).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with self._https_opener().open(req, timeout=12) as response:
                raw = response.read(4097)
                if len(raw) > 4096:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('ready') is not True:
                    raise ValueError
            if self._closed:
                raise QwenError('免费连接已关闭。')
            self._enrolled_token = token
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            messages = {403: '免费连接已停用，请在设置中切换自己的模型。',
                        429: '免费连接请求较多，请稍后重试。'}
            raise QwenError(messages.get(status, '免费服务暂时无法接入，请稍后重试。')) from None
        except (OSError, ValueError, error.URLError):
            raise QwenError('免费服务未连接，请检查网络后重试；原文已保留。') from None

    def warmup(self):
        """后台连接检查，不上传录音或草稿；失败留待下次用户操作重试。"""
        try:
            self.call('/v1/status')
        except QwenError:
            pass

    def _connect(self):
        with self._lock:
            if self._closed:
                raise QwenError('免费连接已关闭，请重新打开 Talk Man。')
            profile = self._load_profile()
            if profile.get('transport') == 'https':
                self._enroll(profile['token'])
                return SERVICE_URL, profile['token']
            if self._process is not None and self._process.poll() is None:
                return self._port, profile['token']
            executable = shutil.which('ssh')
            if not executable:
                raise QwenError('系统缺少 OpenSSH 客户端，请启用后再使用免费内测连接。')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            args = [executable, '-F', os.devnull, '-N', '-T', '-i', profile['identity'],
                    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                    '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile="'+Path(profile['known_hosts']).as_posix()+'"',
                    '-o', 'GlobalKnownHostsFile=none', '-o', 'ExitOnForwardFailure=yes',
                    '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                    '-L', f'127.0.0.1:{port}:127.0.0.1:8787', profile['user']+'@'+profile['host']]
            try:
                process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self._process = process
                deadline = time.monotonic()+10
                while not self._closed and process.poll() is None and time.monotonic() < deadline:
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.15):
                            if self._closed:
                                break
                            self._process, self._port = process, port
                            return port, profile['token']
                    except OSError:
                        time.sleep(.05)
                self._stop_process(process)
                self._process, self._port = None, None
            except OSError:
                pass
            raise QwenError('免费内测连接暂不可用，请检查网络或联系维护者；原文已保留。')

    def call(self, path, data=None, *, audio=False):
        if path not in ('/v1/status', '/v1/chat/completions', '/v1/audio/transcriptions'):
            raise QwenError('免费服务请求不受支持。')
        if path == '/v1/chat/completions' and time.monotonic() < self._text_retry_at:
            raise QwenError('免费文字服务正在繁忙，请稍后重试；已录入的文字保留。')
        endpoint, token = self._connect()
        body = data if audio or data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        origin = endpoint if endpoint == SERVICE_URL else f'http://127.0.0.1:{endpoint}'
        req = request.Request(origin+path, data=body,
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'audio/wav' if audio else 'application/json'})
        try:
            opener = (self._https_opener() if origin == SERVICE_URL else
                      request.build_opener(request.ProxyHandler({}), _NoRedirect()))
            with opener.open(req, timeout=55) as response:
                raw = response.read(2*1024*1024+1)
                if len(raw) > 2*1024*1024:
                    raise QwenError('免费服务回复过大，原文已保留。')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 503 and path == '/v1/chat/completions':
                self._text_retry_at = time.monotonic()+30
                raise QwenError('免费文字服务暂不可用或繁忙，请稍后重试；已录入的文字保留。') from None
            if status == 401 and origin == SERVICE_URL:
                self._enrolled_token = None
            messages = {401: '免费连接需要更新，请再试一次。', 403: '免费服务已停用，请切换自己的模型。',
                        429: '免费测试额度已用完或请求过于频繁，请稍后再试。',
                        413: '本次内容超过免费测试限制，请缩短录音或文字。',
                        504: '免费模型响应超时，请稍后再试。'}
            raise QwenError(messages.get(status, '免费模型暂不可用，原文已保留。')) from None
        except QwenError:
            raise
        except (OSError, ValueError, error.URLError):
            raise QwenError('免费服务连接或回复异常，原文已保留。') from None

    def close(self):
        # 连接等待发生在 worker 中；退出界面不等待网络锁。
        self._closed = True
        process = self._process
        self._profile = None
        if process is not None:
            threading.Thread(target=self._stop_process, args=(process,), daemon=True).start()

    @staticmethod
    def _stop_process(process):
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        except OSError:
            pass


class FreeClient(QwenClient):
    """沿用严格的选区编辑、移动、翻译结果校验，只更换请求通道。"""
    def __init__(self, service):
        super().__init__(QwenSettings())
        self.service = service

    def _request_json(self, path, payload, **kwargs):
        return self.service.call('/v1/chat/completions',
            {'messages': payload['messages'], 'max_tokens': payload.get('max_tokens', 4096)})

    def transcribe(self, wav_bytes):
        result = self.service.call('/v1/audio/transcriptions', wav_bytes, audio=True)
        text = result.get('text')
        if not isinstance(text, str) or not text.strip():
            raise QwenError('未识别到可用文字，原文已保留。')
        return text


def self_check(report_path):
    """成品的联网接入验收；隔离身份，只查服务状态，不上传正文或音频。"""
    import tempfile
    from talkman_version import FULL_VERSION
    target = Path(report_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    result = {'版本': FULL_VERSION, '通过': False, '真实麦克风': False, '模型调用': False}
    try:
        with tempfile.TemporaryDirectory(prefix='TalkMan-自动连接-', dir=target.parent) as directory:
            service = FreeService(directory)
            try:
                state = service.call('/v1/status')
                result.update({'公开证书随包提供': CA_FILE.is_file(), '公网HTTPS': True,
                               '无需SSH': service._process is None,
                               '语音已配置': state.get('asr_configured') is True,
                               '文字已配置': state.get('text_configured') is True,
                               '文字模型': state.get('text_model'),
                               '新安装默认免费': default_free_mode({})})
                result['通过'] = all(result[k] for k in ('公开证书随包提供','公网HTTPS','无需SSH','语音已配置','文字已配置','新安装默认免费'))
            finally:
                service.close()
    except (OSError, QwenError) as exc:
        result['错误'] = str(exc)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['通过'] else 1
