"""卡带三键与官方 API 离线故障注入；不连接实体设备。"""
import hashlib
import itertools
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from lupa.lua54 import LuaRuntime, lua_type
from talkman_coloplay_preview import DeviceRuntime
from talkman_coloplay_update import CartridgeDownload

ROOT=Path(__file__).parent/'TalkMan-设备卡带'


class ChordTests(unittest.TestCase):
    def test_delayed_action_does_not_change_meaning_when_phase_changes(self):
        r=DeviceRuntime();r.step(0,0)
        r.step(2,100,'recording')
        self.assertEqual(r.step(0,280,'idle'),[])
        r.step(4,300,'opening')
        self.assertEqual(r.step(0,480,'recording'),['record'])
    def test_staggered_three_keys_never_record_delete_or_clear(self):
        for order in itertools.permutations((1,2,3)):
            with self.subTest(order=order):
                r=DeviceRuntime();r.step(0,0);mask=0;actions=[]
                for i,key in enumerate(order):
                    mask|=1<<key;actions+=r.step(mask,100+i*60)
                self.assertEqual(actions,['update_hold'])
                self.assertEqual(r.step(14,5219),[])
                self.assertEqual(r.step(14,5220),['cartridge_update'])
                self.assertEqual(r.step(14,9000),[])
                self.assertEqual(r.step(12,9100),[])
                self.assertEqual(r.step(0,9200),[])
                self.assertEqual(r.step(0,10000),[])

    def test_early_release_cancels_until_all_keys_released(self):
        r=DeviceRuntime();r.step(0,0)
        self.assertEqual(r.step(14,100),['update_hold'])
        self.assertEqual(r.step(12,4999),[])
        self.assertEqual(r.step(14,6000),[])
        self.assertEqual(r.step(14,12000),[])
        r.step(0,12100)
        self.assertEqual(r.step(14,12200),['update_hold'])

    def test_quick_taps_survive_and_normal_combos_still_work(self):
        for mask,expected in ((2,'cancel_delete'),(4,'record'),(10,'clear'),(12,'rewrite')):
            r=DeviceRuntime();r.step(0,0)
            if mask&8:r.step(8,10)
            self.assertEqual(r.step(mask,100),[])
            self.assertEqual(r.step(0,120),[expected])
            self.assertEqual(r.step(0,279),[])
            self.assertEqual(r.step(0,280),[])
            self.assertEqual(r.step(0,500),[])
            r.step(mask,600)
            self.assertEqual(r.step(mask,779),[])
            self.assertEqual(r.step(mask,780),['record' if mask==12 else 'cancel_delete' if mask==10 else expected])

    def test_boot_held_keys_and_fill_are_not_replayed(self):
        r=DeviceRuntime();self.assertEqual(r.step(14,0),[])
        self.assertEqual(r.step(14,6000),[]);r.step(0,6100)
        r.step(16,6200);r.step(14,6300)
        self.assertEqual(r.step(0,6400),[])


class DeviceHarness:
    def __init__(self,store=None,build=60,embedded=True,full_config=False):
        self.lua=LuaRuntime(unpack_returned_tuples=True);g=self.lua.globals()
        self.store={} if store is None else store
        self.requests=[];self.sent=[];self.loaded=[];self.response=None;self.closed=False
        self.flush=Mock();self.load_result=True
        def plain(v):
            if lua_type(v)=='table':
                items={k:plain(x) for k,x in v.items()}
                if items and set(items)==set(range(1,len(items)+1)):
                    return [items[i] for i in range(1,len(items)+1)]
                return items
            return v
        self.plain=plain
        g.require=lambda _:self.lua.table_from(dict(encode=lambda v:json.dumps(plain(v),ensure_ascii=True,separators=(',',':')),
          decode=lambda s:self.lua.table_from(json.loads(s),recursive=True)))
        g.sha1sumhexa=lambda s:hashlib.sha1(s.encode()).hexdigest()
        g.cartdata=lambda _:True;g.dget=lambda n:self.store.get(n,0)
        g.dset=lambda n,v:self.store.__setitem__(n,v);g.dflush=lambda:self.flush()
        g.svcset=lambda a,b,r:self.requests.append(plain(r)) or True
        g.svcget=lambda _:self.lua.table_from(self.response,recursive=True) if self.response else None
        def request(kind,value):
            if kind=='bios':self.loaded.append(value);return self.load_result
            return True
        g.svcreq=request
        self.lua.execute((ROOT/'TalkMan-卡带更新.lua').read_text('utf-8'))
        self.cfg=self.lua.table_from(dict(token='paired-test-token' if embedded else '',config='digest',url='ws://127.0.0.1:17642',fill_ms=500,keys={'record':[7,106]}),recursive=True)
        if full_config:
            self.lua.execute((ROOT/'TalkMan-连接配置.lua').read_text('utf-8'))
            self.cfg=g.TM_CONFIG;self.cfg.token='a'*48;self.cfg.config='a'*64
            self.cfg.url='ws://192.168.100.254:65535';self.cfg.discovery=True;self.cfg.discovery_port=17643
        def close():self.closed=True
        self.u=g.TMCartridgeUpdate(self.cfg,build,self.lua.table_from(dict(send=lambda v:self.sent.append(plain(v)),disconnect=close)))
        self.call('init',0)

    def call(self,name,*args):return self.u[name](self.u,*args)
    def step(self,t,state=None):self.call('step',t,self.lua.table_from(state or {},recursive=True))
    def start(self):
        self.call('check',100,True,'idle')
        data='verified ROM test bytes'
        offer=dict(id=self.sent[-1]['id'],status='ready',build=61,size=len(data),sha1=hashlib.sha1(data.encode()).hexdigest(),
                   url='http://127.0.0.1:1234/cartridge/abcdef/TalkMan_update.rom')
        self.step(200,{'cartridge_update':offer});return data
    def finish(self,data):
        self.response=dict(sn=self.requests[-1]['sn'],stat=3,ret=200,rdata='');self.step(300)
        self.response=dict(sn=self.requests[-1]['sn'],stat=3,ret=0,rdata=data);self.step(400)


class UpdateTests(unittest.TestCase):
    def test_full_pairing_and_two_versions_fit_storage_and_keys_survive(self):
        h=DeviceHarness(full_config=True);h.finish(h.start())
        self.assertEqual(h.u.phase,'switching')
        h.u.stored.active=h.u.stored.pending
        self.assertTrue(h.call('save'));self.assertLessEqual(h.store[1],1016)
        new=DeviceHarness(h.store,61,embedded=False)
        self.assertEqual(new.cfg['keys']['record'][1],h.cfg['keys']['record'][1])
        self.assertEqual(new.cfg['keys']['record'][2],h.cfg['keys']['record'][2])
        self.assertEqual(new.cfg.url,h.cfg.url)

    def test_download_verify_load_then_confirm_preserves_pairing(self):
        h=DeviceHarness();h.finish(h.start())
        self.assertEqual([r['method'] for r in h.requests],[2,3])
        self.assertEqual(h.loaded,['load:TalkMan_U61.rom','jump:TalkMan_U61.rom'])
        self.assertTrue(h.closed)
        self.assertIsNone(h.u.stored.active);self.assertEqual(h.u.stored.pending.build,61)
        new=DeviceHarness(h.store,build=61,embedded=False)
        self.assertEqual(new.cfg.token,h.cfg.token);self.assertEqual(new.cfg.config,h.cfg.config)
        self.assertEqual(new.cfg.fill_ms,500)
        new.call('connected',500);self.assertEqual(new.u.stored.active.build,61)
        self.assertIsNone(new.u.stored.pending)
        old=DeviceHarness(h.store)
        self.assertEqual(old.requests[0]['method'],3)
        self.assertFalse(old.loaded)

    def test_bad_hash_short_file_and_download_error_never_load(self):
        for payload in ('corrupted ROM','verified ROM test byteX',''):
            h=DeviceHarness();h.start();h.finish(payload)
            self.assertEqual(h.u.phase,'error');self.assertFalse(h.loaded)
        h=DeviceHarness();h.start();h.response=dict(sn=h.requests[-1]['sn'],stat=3,ret=-1)
        h.step(300);self.assertFalse(h.loaded);self.assertEqual(h.u.phase,'error')

    def test_stale_response_timeout_busy_and_invalid_offer(self):
        h=DeviceHarness();h.call('check',100,True,'recording');self.assertFalse(h.sent)
        h.call('check',200,False,'idle');self.assertFalse(h.sent)
        h.call('check',300,True,'idle');h.step(400,{'cartridge_update':dict(id='stale',status='current')})
        self.assertEqual(h.u.phase,'checking');h.step(13000)
        self.assertEqual(h.u.phase,'error');self.assertFalse(h.loaded)
        h=DeviceHarness();h.call('check',100,True,'idle')
        h.step(200,{'cartridge_update':dict(id=h.sent[-1]['id'],status='ready',build=99999999)})
        self.assertEqual(h.u.phase,'error');self.assertFalse(h.requests)

    def test_current_version_no_download_or_switch(self):
        h=DeviceHarness(build=61);h.call('check',100,True,'idle')
        h.step(200,{'cartridge_update':dict(id=h.sent[-1]['id'],status='current')})
        self.assertEqual(h.u.phase,'done');self.assertFalse(h.requests);self.assertFalse(h.loaded)

    def test_save_failure_or_load_failure_retains_original(self):
        h=DeviceHarness();data=h.start();h.flush.side_effect=OSError('full');h.finish(data)
        self.assertEqual(h.u.phase,'error');self.assertFalse(h.loaded)
        h=DeviceHarness();data=h.start();h.load_result=False;h.finish(data)
        self.assertEqual(h.u.phase,'error');self.assertFalse(h.u.switching)
        # 未确认 pending 不会让原卡带自动跳到失败版本。
        old=DeviceHarness(h.store);self.assertFalse(old.requests);self.assertFalse(old.loaded)

    def test_torn_store_and_manual_new_pairing_do_not_restore_old_target(self):
        h=DeviceHarness();h.finish(h.start());h.store[4]=123
        new=DeviceHarness(h.store,build=61,embedded=False)
        self.assertFalse(new.u.available);self.assertEqual(new.cfg.token,'')
        self.assertFalse(new.loaded)


class DownloadTests(unittest.TestCase):
    def test_packaged_rom_matches_current_source_and_metadata(self):
        spec=importlib.util.spec_from_file_location('builder',ROOT.parent/'TalkMan-构建卡带.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        source=builder.flatten(ROOT/'TalkMan-coloplay.clx')
        metadata=json.loads((ROOT/'TalkMan-update.json').read_text('utf-8'))
        self.assertEqual(metadata['source_sha256'],hashlib.sha256(source.encode()).hexdigest())
        self.assertIn("token=''",(ROOT/'TalkMan-连接配置.lua').read_text('utf-8'))
        download=CartridgeDownload('127.0.0.1')
        try:
            with urlopen(download.offer['url'],timeout=2) as r:
                data=r.read();self.assertEqual(hashlib.sha256(data).hexdigest(),metadata['sha256'])
        finally:download.close()

    def test_bundle_is_verified_and_http_is_expiring_and_path_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data=b'ROM fixture';(p/'TalkMan_update.rom').write_bytes(data)
            meta=dict(build=61,size=len(data),sha1=hashlib.sha1(data).hexdigest(),sha256=hashlib.sha256(data).hexdigest())
            (p/'TalkMan-update.json').write_text(json.dumps(meta),'utf-8')
            d=CartridgeDownload('127.0.0.1',p)
            try:
                with urlopen(d.offer['url'],timeout=2) as r:self.assertEqual(r.read(),data)
                with self.assertRaises(HTTPError):urlopen(d.offer['url']+'/wrong',timeout=2)
                with patch('talkman_coloplay_update.time.monotonic',return_value=d.expires+1):
                    with self.assertRaises(HTTPError):urlopen(d.offer['url'],timeout=2)
            finally:d.close()
            (p/'TalkMan_update.rom').write_bytes(b'broken')
            with self.assertRaises(ValueError):CartridgeDownload('127.0.0.1',p)


if __name__=='__main__':unittest.main()
