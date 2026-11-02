"""局域网地址发现；挑战应答证明桌面持有配对口令，广播中不包含口令。"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import threading
import time

DISCOVERY_PORT = 18744
PROTOCOL = 'TMCP_DISCOVER_1'


def local_addresses():
    """路由探测只调用 UDP connect 获取网卡地址，不向外发送数据。"""
    addresses = set()
    try:
        addresses.update(x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    addresses = {x for x in addresses if valid_host(x) and not ipaddress.ip_address(x).is_loopback}
    return sorted(addresses) or ['127.0.0.1']


def valid_host(value):
    try:
        ip = ipaddress.ip_address(value)
        return ip.version == 4 and not (ip.is_unspecified or ip.is_multicast or ip.is_reserved or str(ip) == '255.255.255.255')
    except (ValueError, TypeError):
        return False


def proof(token, nonce, config, host, port):
    payload = '\n'.join((PROTOCOL, nonce, config, host, str(port))).encode('ascii')
    # 固件公开的 sha1sum 返回原始摘要；HMAC 不依赖 SHA-1 的碰撞抗性。
    return hmac.new(token.encode('ascii'), payload, hashlib.sha1).hexdigest()


def response(data, token, config, host, port):
    if len(data) > 1024:
        return None
    try:
        query = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(query, dict) or query.get('protocol') != PROTOCOL or query.get('type') != 'discover':
        return None
    nonce = query.get('nonce')
    if query.get('config') != config or not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        return None
    return json.dumps(dict(protocol=PROTOCOL, type='endpoint', config=config, nonce=nonce,
                           host=host, port=port, proof=proof(token, nonce, config, host, port))).encode('ascii')


class DiscoveryService:
    def __init__(self, host, port, token, config, device_host='', discovery_port=DISCOVERY_PORT):
        self.host, self.port, self.token, self.config = host, port, token, config
        self.discovery_port = discovery_port
        self.stop_event = threading.Event()
        self.error = ''
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.socket.bind((host, discovery_port))
            self.socket.settimeout(.15)
        except OSError:
            self.socket.close()
            raise
        self.destinations = []
        if not ipaddress.ip_address(host).is_loopback:
            self.destinations.append(('255.255.255.255', discovery_port))
        if device_host:
            self.destinations.append((device_host, discovery_port))
        self.thread = threading.Thread(target=self.run, daemon=True, name='TalkMan-COLOPLAY自动发现')
        self.thread.start()

    def run(self):
        beacon = json.dumps(dict(protocol=PROTOCOL, type='beacon', config=self.config)).encode('ascii')
        sent_at = bucket = 0
        count = 0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - sent_at >= 1:
                sent_at = now
                for target in self.destinations:
                    try:
                        self.socket.sendto(beacon, target)
                    except OSError:
                        self.error = '自动发现受网络限制，可填写设备 IP 或使用纯 USB。'
            try:
                data, sender = self.socket.recvfrom(1025)
            except socket.timeout:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    self.error = '自动发现已停止，请重新保存当前网络设置。'
                break
            if now - bucket >= 1:
                bucket, count = now, 0
            count += 1
            if count > 20 or not valid_host(sender[0]):
                continue
            reply = response(data, self.token, self.config, self.host, self.port)
            if reply is not None:
                try:
                    self.socket.sendto(reply, sender)
                except OSError:
                    self.error = '自动发现应答未送达，请检查设备与电脑是否可互通。'

    def stop(self):
        self.stop_event.set()
        self.socket.close()
        self.thread.join(timeout=1)
