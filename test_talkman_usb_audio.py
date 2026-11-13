"""USB v2 offline checks: generated audio and fake transport, never a microphone."""
import ctypes as C
import json
import math
from pathlib import Path
import queue
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch

from talkman_airdata import AUDIO, CONTROL, HEADER, MAGIC, PONG, Frames, Frame, packet, opus_units
from talkman_usb_audio import AudioManager
from talkman_coloplay_protocol import validate_config, config_id


class FramingTests(unittest.TestCase):
    def test_valid_mixed_frames_survive_every_split_and_usb_packet_size(self):
        frames = [packet(AUDIO, b'\0\x7a'+bytes(range(122)), seq=100),
                  packet(19, b'B'*158, seq=361, priority=1),
                  packet(PONG, bytes(38), seq=500),
                  packet(AUDIO, b'\0\x72'+bytes(range(114)), seq=101)]
        raw = b''.join(frames)
        expected = [(16, 100), (19, 361), (33, 500), (16, 101)]
        for split in range(len(raw)+1):
            parser = Frames()
            result = parser.feed(raw[:split])+parser.feed(raw[split:])
            self.assertEqual([(f.kind, f.seq) for f in result], expected)
            self.assertFalse(any(parser.stats.values()))
        for size in (1, 8, 16, 20, 32, 64, 512, 1024):
            parser, result = Frames(), []
            for start in range(0, len(raw), size):
                result += parser.feed(raw[start:start+size])
                self.assertEqual(parser.feed(b''), [])  # Idle/timeout between reads.
            self.assertEqual([(f.kind, f.seq) for f in result], expected)
            self.assertFalse(any(parser.stats.values()))

    def test_truncated_64_byte_prefix_reproduces_report_without_accepting_audio(self):
        audio = packet(AUDIO, b'\0\x7a'+bytes(range(122)), seq=100)
        buttons = packet(19, b'B'*158, seq=361, priority=1)
        following = packet(AUDIO, b'\0\x72'+bytes(range(114)), seq=101)
        raw = audio[:64]+buttons+following
        for size in (1, 64, 512):
            parser, result = Frames(), []
            for start in range(0, len(raw), size):
                result += parser.feed(raw[start:start+size])
            self.assertEqual([(f.kind, f.seq) for f in result], [(19, 361), (16, 101)])
            self.assertEqual(parser.stats, dict(crc_errors=1, invalid_headers=0, discarded_bytes=64))
            self.assertEqual(list(parser.crc_frames), [(1, 16, 0, 100, 124, 64)])

    def test_crc_metadata_is_bounded_and_does_not_record_payload(self):
        raw = bytearray(packet(AUDIO, b'private microphone audio', seq=20, sub=1234))
        raw[-1] ^= 1
        parser = Frames()
        for _ in range(12):
            parser.feed(raw)
        self.assertEqual(parser.stats['crc_errors'], 12)
        self.assertEqual(len(parser.crc_frames), 4)
        self.assertEqual(parser.crc_frames[-1][:5], (12, 16, 0, 20, 24))
        self.assertNotIn('private', repr(parser.crc_frames))
        self.assertNotIn('1234', repr(parser.crc_frames))

    def test_split_coalesced_noise_crc_and_bounds(self):
        good=packet(AUDIO,b'\0\3abc',seq=65535,sub=55)
        broken=bytearray(good);broken[-1]^=1
        huge=HEADER.pack(MAGIC,AUDIO,0,9000,0,0,0,0)
        parser=Frames()
        self.assertEqual(parser.feed(b'noise'+bytes(broken)+huge+good[:17]),[])
        result=parser.feed(good[17:]+good)
        self.assertEqual(len(result),2)
        self.assertEqual((result[0].seq,result[0].sub,result[0].data),(65535,55,b'\0\3abc'))
        self.assertEqual(parser.stats['crc_errors'], 1)
        self.assertEqual(parser.stats['invalid_headers'], 1)
        for _ in range(10):parser.feed(b'garbage'*10000)
        self.assertLess(len(parser.buffer),20)

    def test_control_size_zero_crc_and_reserved_flags(self):
        with self.assertRaises(ValueError):packet(PONG,b'x'*45)
        raw=HEADER.pack(MAGIC,AUDIO,0,3,1,0,5,0)+b'abc'
        bad=HEADER.pack(MAGIC,AUDIO,2,3,1,0,5,0)+b'bad'
        self.assertEqual([f.data for f in Frames().feed(bad+raw)],[b'abc'])

    def test_opus_units_reject_whole_malformed_payload(self):
        self.assertEqual(opus_units(b'\0\3abc\0\1d'),[b'abc',b'd'])
        for data in (b'',b'\0',b'\0\0',b'\0\3ab',b'\0\1a\0',b'\x05\0'+b'a'*1280):
            with self.subTest(data=data[:8]),self.assertRaises(ValueError):opus_units(data)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.decoder=Mock();self.decoder.decode.return_value=b'\1\0'*960
        self.manager=AudioManager(validate_config({}),lambda:self.decoder)
        self.manager.available=True
        self.capture,self.failure=Mock(),Mock()
        self.stream=self.manager.create(self.capture,self.failure)

    def tearDown(self):self.manager.disconnect()

    def control(self,action,seq=0,**kw):
        data=dict(protocol='TM_AUDIO_1',session=self.stream.session,tag=self.stream.tag,action=action)
        data.update(kw)
        self.manager.receive(Frame(CONTROL,0 if action=='begin' else 1,seq,0,0,json.dumps(data).encode()))

    def begin(self,seq=10):
        self.manager.begin(self.stream);self.control('started');self.control('begin',seq)

    def audio(self,seq,sub=None,payload=b'\0\1a'):
        self.manager.receive(Frame(AUDIO,0,seq,0,self.stream.tag if sub is None else sub,payload))

    def test_start_ack_sequence_wrap_and_stop(self):
        self.begin(65535);self.audio(0);self.audio(0);self.audio(1)
        self.assertEqual(self.capture.call_count,2)
        self.failure.assert_not_called()
        self.stream.close();self.audio(2)
        self.assertEqual(self.capture.call_count,2)
        self.decoder.close.assert_called_once()
        commands=[]
        while not self.manager.commands.empty():
            commands.append(json.loads(Frames().feed(self.manager.commands.get_nowait())[0].data)['action'])
        self.assertEqual(commands,['start','stop'])

    def test_wrong_session_and_old_audio_are_ignored(self):
        self.manager.begin(self.stream)
        self.control('started',session='old')
        self.assertFalse(self.stream.started.is_set())
        self.control('started');self.control('begin',30)
        self.audio(31,sub=(self.stream.tag+1)&65535)
        self.capture.assert_not_called();self.audio(31)
        self.capture.assert_called_once()

    def test_missing_begin_or_audio_gap_aborts(self):
        self.manager.begin(self.stream);self.control('started');self.audio(5)
        self.failure.assert_called_once();self.capture.assert_not_called()
        self.assertIsNone(self.manager.stream)

    def test_dropped_audio_aborts_and_does_not_decode(self):
        self.begin();self.audio(12)
        self.decoder.decode.assert_not_called();self.failure.assert_called_once()

    def test_gap_diagnostic_retains_counts_after_cleanup_without_payloads(self):
        self.begin(65534)
        self.audio(65535);self.audio(65535);self.audio(0);self.audio(2)
        self.assertIsNone(self.manager.stream)
        self.assertEqual(self.capture.call_count, 2)
        stats = self.stream.stats
        self.assertEqual((stats['packets'], stats['payload_bytes'], stats['opus_units'],
                          stats['pcm_frames'], stats['duplicates']), (4, 12, 2, 1920, 1))
        self.assertEqual((stats['begin_seq'], stats['last_seq'], stats['gap_expected'],
                          stats['gap_received'], stats['gap_missing']), (65534, 0, 1, 2, 1))
        self.assertEqual(stats['failure_code'], 'sequence_gap')
        self.assertTrue(stats['confirmed'])
        self.assertNotIn(self.stream.session, repr(stats))
        self.assertNotIn(self.manager.config['token'], repr(stats))
        self.assertFalse(any(isinstance(value, bytes) for value in stats.values()))
        before = dict(stats)
        self.manager.disconnect()
        self.assertEqual(stats, before)

    def test_format_timeout_and_device_stop_are_distinguishable(self):
        self.begin();self.audio(11, payload=b'\0\1a\0')
        self.assertEqual(self.stream.stats['format_errors'], 1)
        self.assertEqual(self.stream.stats['failure_code'], 'audio_format')
        self.assertEqual(self.stream.stats['opus_units'], 0)

        self.stream = self.manager.create(self.capture, self.failure)
        self.begin();self.audio(11)
        self.control('stopped')
        self.manager.receive(Frame(17, 0, 12, 0, 0, b'input'))
        self.manager.tick(self.stream.last_audio+2.01)
        self.assertEqual(self.stream.stats['failure_code'], 'audio_timeout')
        self.assertTrue(self.stream.stats['device_stopped'])
        self.assertEqual(self.stream.stats['other_normal'], 1)
        self.assertEqual(self.stream.stats['pcm_frames'], 960)

    def test_bounded_header_trace_and_per_session_parser_counters(self):
        self.manager.transport_stats = dict(crc_errors=5, invalid_headers=2, discarded_bytes=100)
        self.manager.transport_crc_frames = [(5, 16, 0, 100, 124, 64)]
        self.manager.read_packet_bytes = 64
        self.stream = self.manager.create(self.capture, self.failure)
        self.begin()
        for seq in range(200):
            self.manager.observe_frame(Frame(AUDIO, 0, seq, 0, self.stream.tag, b'private audio'))
        self.manager.transport_stats.update(crc_errors=6, discarded_bytes=120)
        self.manager.transport_crc_frames.append((6, 16, 0, 200, 130, 64))
        self.stream.close()
        self.assertEqual(self.stream.stats['parser'], dict(crc_errors=1, invalid_headers=0, discarded_bytes=20))
        recent = self.stream.stats['recent_frames']
        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[-1], (16, 0, 199, 13))
        self.assertNotIn('private audio', repr(self.stream.stats))
        self.assertEqual(self.stream.stats['crc_frames'], [(16, 0, 200, 130, 64)])
        self.assertEqual(self.stream.stats['read_packet_bytes'], 64)

    def test_bad_payload_does_not_commit_its_valid_prefix(self):
        self.begin();self.audio(11,payload=b'\0\1a\0')
        self.capture.assert_not_called();self.failure.assert_called_once()

    def test_waiting_start_and_disconnect(self):
        errors=[]
        def start():
            try:self.stream.start()
            except RuntimeError as e:errors.append(str(e))
        worker=threading.Thread(target=start);worker.start()
        # A command means the manager has installed the pending session.
        self.manager.commands.get(timeout=1)
        self.manager.disconnect();worker.join(1)
        self.assertFalse(worker.is_alive());self.assertTrue(errors)
        self.decoder.close.assert_called_once()

    def test_silent_stream_times_out(self):
        self.begin();self.manager.tick(self.stream.last_audio+2.01)
        self.failure.assert_called_once();self.assertIsNone(self.manager.stream)

    def test_unavailable_does_not_open_decoder(self):
        self.manager.available=False
        with self.assertRaises(RuntimeError):self.stream.start()
        self.assertIsNone(self.manager.stream);self.decoder.decode.assert_not_called()

    def test_full_queue_ends_session(self):
        self.begin()
        while not self.manager.commands.full():self.manager.commands.put(b'x')
        self.manager.tick(self.stream.keep_at+.3)
        self.failure.assert_called_once();self.decoder.close.assert_called_once()


class OpusTests(unittest.TestCase):
    def test_bundled_opus_decodes_generated_60ms_tone(self):
        from talkman_opus import Decoder
        decoder=Decoder();self.addCleanup(decoder.close)
        lib=decoder.lib
        lib.opus_encoder_create.argtypes=[C.c_int,C.c_int,C.c_int,C.POINTER(C.c_int)]
        lib.opus_encoder_create.restype=C.c_void_p
        lib.opus_encoder_destroy.argtypes=[C.c_void_p]
        lib.opus_encode.argtypes=[C.c_void_p,C.POINTER(C.c_int16),C.c_int,C.c_void_p,C.c_int]
        lib.opus_encode.restype=C.c_int
        error=C.c_int();enc=lib.opus_encoder_create(16000,1,2049,C.byref(error))
        self.assertTrue(enc);self.assertEqual(error.value,0)
        self.addCleanup(lib.opus_encoder_destroy,enc)
        tone=(C.c_int16*960)(*[int(math.sin(i*2*math.pi*440/16000)*10000) for i in range(960)])
        buf=C.create_string_buffer(1275)
        n=lib.opus_encode(enc,tone,960,buf,1275)
        self.assertGreater(n,0)
        pcm=decoder.decode(buf.raw[:n])
        self.assertEqual(len(pcm),1920)
        samples=struct.unpack('<960h',pcm)
        self.assertGreater(max(abs(s) for s in samples),5000)


class FirmwareAudioTests(unittest.TestCase):
    def setUp(self):
        from lupa.lua54 import LuaRuntime
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        g=self.lua.globals()
        g.decode=lambda s:self.lua.table_from(json.loads(s),recursive=True)
        self.messages=[]
        g.encode=lambda t:json.dumps(dict(t))
        g.emit=lambda ty,data,prio,tag,sub:self.messages.append((ty,json.loads(data) if ty==18 else data,prio,tag,sub)) or True
        self.lua.execute('''
        package.preload.cjson_safe=function() return {decode=decode,encode=encode} end
        function TMUpdateControls() return {step=function() return {} end} end
        function btn() return false end
        function svcreq() error('CDC must not be used') end
        loads=0;clears=0;reads=0;cmds={};fresh=true
        function usemod() loads=loads+1;return true end
        function statctl() end
        function micpoll(mode) if mode==1 then clears=clears+1 else reads=reads+1;return 'abc' end end
        function airdata_get(channel)
         if channel==0 then
          if fresh then return '{"v":1,"token":"test","config":"binding","frame":7,"state":{"ready":true}}',20 end
         else local v=table.remove(cmds,1);if v then return v,18 end end
        end
        function airdata_ready() return true end
        function airdata_send(...) return emit(...) end
        ''')
        self.lua.execute(Path('TalkMan-设备卡带/TalkMan-USB通道.lua').read_text('utf-8'))
        self.lua.execute("cfg={token='test',config='binding'};u=TMUsbTransport(cfg)")

    def command(self,action,**kw):
        data=dict(protocol='TM_AUDIO_1',action=action,session='a'*32,tag=77,token='test',config='binding')
        data.update(kw)
        g=self.lua.globals();g.cmds[len(g.cmds)+1]=json.dumps(data)

    def step(self,t):self.lua.globals().u.step(self.lua.globals().u,t)

    def test_only_paired_explicit_start_uploads_and_stop_stays_stopped(self):
        self.step(0);self.assertEqual(self.lua.globals().loads,0)
        self.command('start',token='wrong');self.step(10)
        self.assertEqual(self.lua.globals().loads,0)
        self.command('start');self.step(20)
        self.assertEqual(self.lua.globals().loads,1)
        self.assertEqual([m[1]['action'] for m in self.messages if m[0]==18],['started','begin'])
        self.assertEqual([m for m in self.messages if m[0]==16][-1],(16,'abc',0,0,77))
        self.command('stop');self.step(30)
        count=len([m for m in self.messages if m[0] in (16,18)])
        self.command('start');self.step(40);self.step(100)
        self.assertEqual(len([m for m in self.messages if m[0] in (16,18)]),count)

    def test_lease_and_loss_of_state_stop_upload(self):
        self.command('start');self.step(0);self.step(1501)
        self.assertIsNone(self.lua.globals().u.audio)
        self.command('start',session='b'*32);self.step(2000)
        self.lua.globals().fresh=False
        self.lua.globals().u.received=0
        self.step(2050)
        self.assertIsNone(self.lua.globals().u.audio)

    def test_stop_before_start_prevents_delayed_start(self):
        self.command('stop');self.command('start');self.step(0)
        self.assertEqual(self.lua.globals().loads,0)

    def test_keep_renews_lease_but_not_two_minute_limit(self):
        self.command('start');self.step(0)
        self.command('keep');self.step(1400);self.step(1600)
        self.assertIsNotNone(self.lua.globals().u.audio)
        self.command('keep');self.step(120001)
        self.assertIsNone(self.lua.globals().u.audio)


if __name__=='__main__':unittest.main()
