"""只向已配对卡带提供安装包附带的通用 ROM；不包含配对信息。"""
from hashlib import sha1, sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time

from talkman_coloplay_cartridge import CARTRIDGE_BUILD


class CartridgeDownload:
    def __init__(self, host, root=None):
        root = Path(root or Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'TalkMan-设备卡带')
        data = (root/'TalkMan_update.rom').read_bytes()
        metadata = json.loads((root/'TalkMan-update.json').read_text('utf-8'))
        if (metadata.get('build') != CARTRIDGE_BUILD or metadata.get('size') != len(data)
                or not 0 < len(data) <= 184*1024
                or metadata.get('sha256') != sha256(data).hexdigest()
                or metadata.get('sha1') != sha1(data).hexdigest()):
            raise ValueError('本包卡带校验未通过，请重新升级桌面程序。')
        route = '/cartridge/' + secrets.token_hex(24) + '/TalkMan_update.rom'
        expires = time.monotonic()+180
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != route or time.monotonic() >= expires:
                    self.send_error(404);return
                self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer((host,0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True,name='TalkMan-卡带下载')
        self.thread.start()
        self.offer=dict(build=CARTRIDGE_BUILD,size=len(data),sha1=metadata['sha1'],
                        url=f'http://{host}:{self.server.server_port}{route}')
        self.expires=expires
        self.close_lock=threading.Lock()
        self.closed=False
        self.timer=threading.Timer(180,self.close)
        self.timer.daemon=True
        self.timer.start()

    def close(self):
        with self.close_lock:
            if self.closed:return
            self.closed=True
            self.timer.cancel()
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
