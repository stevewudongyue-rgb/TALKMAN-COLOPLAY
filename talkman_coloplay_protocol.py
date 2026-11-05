"""COLOPLAY 控制协议：状态通道仅预约动作，USB 热键才提交动作。"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import time
from pathlib import Path

from talkman_shortcuts import normalize_shortcut

ACTIONS = {
    'ai': 'AI 改写／确认', 'cancel_delete': '取消／退格',
    'record': '录音／覆盖', 'rewrite': '指令修改',
    'previous': '光标向前／预览向上', 'next': '光标向后／预览向下',
    'toggle': '显示／收起', 'fill': '填入目标',
}
DEFAULT_BINDINGS = {action: f'Control+Alt+Shift+F{i}'
                    for i, action in enumerate(ACTIONS, 13)}
# copy 仅保留旧版绑定与配对摘要；业务层拒绝，不再展示或触发。
ACTIONS.update(copy='旧版保留（不执行）', scroll_up='向上滚动', scroll_down='向下滚动',
               scroll_left='向左滚动', scroll_right='向右滚动')
DEFAULT_BINDINGS.update({action: f'Control+Alt+Shift+F{i}' for action, i in
                        zip(('copy', 'scroll_up', 'scroll_down', 'scroll_left', 'scroll_right'), (1, 2, 3, 5, 6))})
ACTIONS['clear'] = '清空编辑区（右下＋右上）'
DEFAULT_BINDINGS['clear'] = 'Control+Alt+Shift+F7'
ACTIONS.update(knob='选区开始／结束', target_previous='上一目标（右下＋旋转）', target_next='下一目标（右下＋旋转）')
DEFAULT_BINDINGS.update(knob='Control+Alt+Shift+F8', target_previous='Control+Alt+Shift+F9', target_next='Control+Alt+Shift+F10')
ACTIONS.update(target_commit='松开右下选定目标', send_current='发送目标已有内容（右下＋短按旋钮）')
DEFAULT_BINDINGS.update(target_commit='Control+Alt+Shift+F11', send_current='Control+Alt+Shift+F12')
DEFAULT_PREFERENCES = dict(reverse_rotation=False,
                           highlight_target=True, keep_on_top=True, hide_hints=True,
                           hide_device_buttons=True)


def validate_preferences(data):
    if not isinstance(data, dict):
        raise ValueError('COLOPLAY 偏好设置应为一个配置对象。')
    result = {**DEFAULT_PREFERENCES, **{k: v for k, v in data.items() if k in DEFAULT_PREFERENCES}}
    for key in ('reverse_rotation', 'highlight_target', 'keep_on_top', 'hide_hints', 'hide_device_buttons'):
        if type(result[key]) is not bool:
            raise ValueError('COLOPLAY 开关配置应为开启或关闭。')
    return result


def hid_binding(value):
    parts = normalize_shortcut(value).split('+')
    # 固定三修饰键前缀，避免裸按键以及系统常用快捷键流入前台应用。
    if parts[:-1] != ['Control', 'Alt', 'Shift']:
        raise ValueError('设备快捷键必须使用 Ctrl + Alt + Shift 加 F1–F20。')
    key = parts[-1]
    if not key.startswith('F') or not 1 <= int(key[1:]) <= 20:
        raise ValueError('设备快捷键末键请选择 F1–F20。')
    n = int(key[1:])
    return 7, (57+n if n <= 12 else 91+n)


def validate_config(data):
    host = str(data.get('host', '127.0.0.1'))
    address = ipaddress.ip_address(host)
    if address.version != 4 or address.is_multicast or address.is_unspecified:
        raise ValueError('请选择本机 IPv4 地址，不使用所有网卡监听。')
    port = int(data.get('port', 18743))
    if not 1024 <= port <= 65535:
        raise ValueError('端口应在 1024–65535 之间。')
    device_host = str(data.get('device_host', '')).strip()
    if device_host:
        device_address = ipaddress.ip_address(device_host)
        if device_address.version != 4 or device_address.is_multicast or device_address.is_unspecified or device_address.is_reserved:
            raise ValueError('设备 IP 应是 COLOPLAY 当前的 IPv4 地址，也可以留空。')
    discovery = data.get('discovery', True)
    if type(discovery) is not bool:
        raise ValueError('自动寻找电脑应为开启或关闭。')
    usb_auto = data.get('usb_auto', True)
    if type(usb_auto) is not bool:raise ValueError('USB 自动连接应为开启或关闭。')
    # 老版只有 Wi-Fi 配置时延续 Wi-Fi；明确开启 USB 自动连接的版本延续 USB。
    mode = data.get('connection_mode',
                    'wifi' if data.get('enabled') and data.get('usb_auto') is not True else 'usb')
    if mode not in ('usb', 'wifi'):
        raise ValueError('连接方式请选择 USB 或 Wi-Fi。')
    supplied = data.get('bindings', {})
    bindings = {k: normalize_shortcut(supplied.get(k, v)) for k, v in list(DEFAULT_BINDINGS.items())[:8]}
    bindings.update({k: normalize_shortcut(v) for k, v in supplied.items() if k in ACTIONS})
    # 保留旧八键及自定义值；新增动作自动避开已占键。口令保持不变。
    for action, default in DEFAULT_BINDINGS.items():
        if action not in bindings:
            available = [default] + [f'Control+Alt+Shift+F{i}' for i in range(1, 21) if i != 4]
            bindings[action] = next(v for v in available if v not in bindings.values())
    for value in bindings.values():
        hid_binding(value)
    if len(set(bindings.values())) != len(bindings):
        raise ValueError('设备快捷键不能重复。')
    token = str(data.get('token') or secrets.token_hex(24))
    if len(token) != 48 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('配对配置损坏，请重新生成。')
    return dict(host=host, port=port, enabled=mode == 'wifi',
                usb_auto=mode == 'usb', connection_mode=mode,
                discovery=discovery, device_host=device_host,
                token=token, bindings=bindings,
                preferences=validate_preferences(data.get('preferences', {})))


def config_id(config):
    payload = json.dumps(config['bindings'], sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def save_config(path: Path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Intent:
    """一次性的短期预约。文字、选区、目标或工作状态变化就作废。"""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending = None

    def arm(self, identifier, action, stamp, semantic, ttl=3):
        self.pending = (identifier, action, stamp, semantic, self.clock()+ttl)

    def cancel(self):
        self.pending = None

    def id(self):
        if self.pending and self.clock() < self.pending[4]:
            return self.pending[0]
        self.cancel()
        return ''

    def consume(self, action, stamp):
        pending, self.pending = self.pending, None
        if (pending and pending[1] == action and pending[2] == stamp
                and self.clock() < pending[4]):
            return pending[3]
        return None
