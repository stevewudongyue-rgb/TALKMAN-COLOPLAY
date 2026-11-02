"""换网无需重编译：真实 UDP、跨 Lua 认证、旧配对迁移；无真实设备操作。"""
import hashlib
import json
from pathlib import Path
import queue
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from lupa.lua54 import LuaRuntime
from talkman_coloplay_discovery import DiscoveryService, PROTOCOL, proof, response
from talkman_coloplay_protocol import validate_config, config_id
from talkman_coloplay_server import StateServer
from websockets.sync.client import connect


TOKEN = 'a' * 48
CONFIG = '0123456789abcdef'


def lua_device():
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    lua.globals()[b'sha1sum'] = lambda data: hashlib.sha1(data).digest()
    def decode(raw):
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return lua.table_from({k.encode():v.encode() if isinstance(v,str) else v for k,v in value.items()})
        except (ValueError, UnicodeError):
            pass
        return None
    def encode(table):
        return json.dumps({k.decode():v.decode() if isinstance(v,bytes) else v for k,v in table.items()}).encode()
    lua.globals()[b'json_decode'], lua.globals()[b'json_encode'] = decode, encode
    lua.execute(b'''
      incoming={}; outgoing={}; connected=false
      package.preload.cjson_safe=function() return {decode=json_decode,encode=json_encode} end
      function net_bind(...)
       return {recvfrom=function()
         local v=table.remove(incoming,1)
         if v then return v[1],v[2],v[3] end
         return false
        end,
        sendto=function(_,data,host,port) outgoing[#outgoing+1]={data,host,port};return #data end,
        close=function() end}
      end
    ''')
    lua.execute(Path('TalkMan-设备卡带/TalkMan-发现电脑.lua').read_bytes())
    cfg = lua.table_from({b'discovery':True,b'token':TOKEN.encode(),b'config':CONFIG.encode()})
    device = lua.globals()[b'TMDiscovery'](cfg)
    return lua, device


def feed(lua, device, data, host='192.168.110.57', t=1000, connected=False, port=18744):
    incoming=lua.globals()[b'incoming']
    incoming[len(incoming)+1]=lua.table_from([data,host.encode(),port])
    device[b'step'](device,t,connected)


def beacon(lua, device, host='192.168.110.57', t=1000):
    feed(lua,device,json.dumps(dict(protocol=PROTOCOL,type='beacon',config=CONFIG)).encode(),host,t)
    return json.loads(lua.globals()[b'outgoing'][len(lua.globals()[b'outgoing'])][1])


class DiscoveryTests(unittest.TestCase):
    def test_address_switch_keeps_pairing_and_bindings(self):
        old=validate_config(dict(host='192.168.100.45',token=TOKEN))
        new=validate_config(dict(old,host='192.168.110.57',device_host='192.168.110.209'))
        self.assertEqual(new['token'],old['token'])
        self.assertEqual(new['bindings'],old['bindings'])
        self.assertEqual(config_id(new),config_id(old))
        self.assertTrue(new['discovery'])

    def test_bad_packets_never_receive_proof(self):
        for data in (b'[]', b'bad', b'x'*1025,
                     json.dumps(dict(protocol=PROTOCOL,type='discover',nonce='bad',config=CONFIG)).encode(),
                     json.dumps(dict(protocol=PROTOCOL,type='discover',nonce='0'*32,config='wrong')).encode()):
            self.assertIsNone(response(data,TOKEN,CONFIG,'192.168.110.57',18743))

    def test_lua_hmac_matches_python_with_non_ascii_digest_bytes(self):
        lua,_=lua_device()
        got=lua.globals()[b'TMDiscoveryProof'](TOKEN.encode(),b'0'*32,CONFIG.encode(),b'192.168.110.57',18743)
        self.assertEqual(got.decode(),proof(TOKEN,'0'*32,CONFIG,'192.168.110.57',18743))

    def test_spoof_replay_and_wrong_source_cannot_redirect(self):
        for failure in ('wrong-key','wrong-source','expired','wrong-nonce','wrong-port'):
            lua,d=lua_device();q=beacon(lua,d)
            reply=response(json.dumps(q).encode(), 'b'*48 if failure=='wrong-key' else TOKEN, CONFIG,'192.168.110.57',18743)
            if failure=='wrong-nonce':
                obj=json.loads(reply);obj['nonce']='f'*32;reply=json.dumps(obj).encode()
            feed(lua,d,reply,'192.168.110.99' if failure=='wrong-source' else '192.168.110.57',
                 4000 if failure=='expired' else 1100,port=18745 if failure=='wrong-port' else 18744)
            self.assertIsNone(d[b'take'](d,1200),failure)

    def test_same_rom_accepts_two_network_addresses_without_config_change(self):
        lua,d=lua_device()
        for index,host in enumerate(('192.168.100.45','192.168.110.57')):
            t=1000+index*5000;q=beacon(lua,d,host,t)
            raw=response(json.dumps(q).encode(),TOKEN,CONFIG,host,18743)
            self.assertNotIn(TOKEN.encode(),raw)
            feed(lua,d,raw,host,t+100)
            self.assertEqual(d[b'take'](d,t+200),f'ws://{host}:18743'.encode())
            self.assertIsNone(d[b'take'](d,t+200))

    def test_active_connection_ignores_other_hosts(self):
        lua,d=lua_device()
        raw=json.dumps(dict(protocol=PROTOCOL,type='beacon',config=CONFIG)).encode()
        feed(lua,d,raw,connected=True)
        self.assertEqual(len(lua.globals()[b'outgoing']),0)

    def test_real_udp_responder_then_websocket_on_discovered_address(self):
        state=StateServer('127.0.0.1',0,TOKEN,CONFIG,queue.Queue())
        service=DiscoveryService('127.0.0.1',state.port,TOKEN,CONFIG,discovery_port=0)
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as device:
                device.settimeout(2)
                query=dict(protocol=PROTOCOL,type='discover',config=CONFIG,nonce='1'*32)
                device.sendto(json.dumps(query).encode(),service.socket.getsockname())
                data,source=device.recvfrom(1024);answer=json.loads(data)
                self.assertEqual(answer['host'],source[0])
                self.assertEqual(answer['proof'],proof(TOKEN,'1'*32,CONFIG,answer['host'],answer['port']))
                with connect(f"ws://{answer['host']}:{answer['port']}",ping_interval=None) as ws:
                    ws.send(json.dumps(dict(type='hello',version=1,token=TOKEN,config=CONFIG,heartbeat='state-ack')))
                    self.assertEqual(json.loads(ws.recv(timeout=2))['type'],'state')
        finally:
            service.stop();state.stop()
        self.assertFalse(service.thread.is_alive())

    def test_discovery_socket_error_does_not_leak_socket(self):
        with patch('talkman_coloplay_discovery.socket.socket') as sock:
            sock.return_value.bind.side_effect=OSError('busy')
            with self.assertRaises(OSError):DiscoveryService('127.0.0.1',18743,TOKEN,CONFIG)
            sock.return_value.close.assert_called_once()


if __name__=='__main__':unittest.main()
