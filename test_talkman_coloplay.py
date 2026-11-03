"""隔离草稿与假音频；真实回环 WebSocket；官方 Lua 手势代码离线验证。"""
import gc
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from lupa import LuaRuntime
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

from talkman_coloplay_protocol import Intent, validate_config, hid_binding, config_id, DEFAULT_BINDINGS
from talkman_coloplay_server import StateServer
from talkman_app import TalkManApp
from talkman_qwen import QwenSettings
from talkman_target import TargetWindow, TargetController
from test_talkman_app import FakeVoice, FakeClient
from test_talkman_target import FakeBackend, wait_result


class SmokeCheckTests(unittest.TestCase):
    def test_no_system_temp_needed_and_isolated_folder_is_cleaned(self):
        from talkman_coloplay import smoke_check
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)/'TalkMan-report.json'
            data_dirs = []
            def probe(directory, report_path, state):
                # macOS 的 /var 临时目录可能通过 /private/var 返回；比较实际目录。
                self.assertEqual(directory.parent.resolve(), Path(folder).resolve())
                self.assertNotEqual(directory.resolve(), Path(folder).resolve())
                self.assertTrue(directory.is_dir())
                data_dirs.append(directory)
                self.assertEqual(json.loads(report.read_text('utf-8'))['状态'], '运行中')
                (directory/'draft.txt').write_text('隔离自检草稿', encoding='utf-8')
            with patch('tempfile.gettempdir', side_effect=FileNotFoundError('系统 TEMP 不可用')), \
                 patch('talkman_coloplay._smoke_check_ui', side_effect=probe):
                self.assertEqual(smoke_check(report), 0, report.read_text('utf-8'))
            result = json.loads(report.read_text('utf-8'))
            self.assertTrue(result['通过'])
            self.assertEqual(result['状态'], '完成')
            self.assertTrue(result['运行编号'])
            self.assertTrue(result['结束时间'])
            self.assertFalse(data_dirs[0].exists())

    def test_temp_failure_replaces_old_pass_report_without_crashing(self):
        from talkman_coloplay import smoke_check
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)/'TalkMan-report.json'
            report.write_text('{"通过":true,"运行编号":"old"}', encoding='utf-8')
            with patch('tempfile.TemporaryDirectory', side_effect=PermissionError('不可写')), \
                 patch('talkman_coloplay._smoke_check_ui') as ui:
                self.assertEqual(smoke_check(report), 1)
                ui.assert_not_called()
            result = json.loads(report.read_text('utf-8'))
            self.assertFalse(result['通过'])
            self.assertEqual(result['状态'], '失败')
            self.assertNotEqual(result['运行编号'], 'old')
            self.assertIn('PermissionError', result['错误'])

    def test_unwritable_report_returns_failure_without_crashing(self):
        from talkman_coloplay import smoke_check
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, 'write_text', side_effect=PermissionError('不可写')), \
                 patch('talkman_coloplay._smoke_check_ui') as ui:
                self.assertEqual(smoke_check(Path(folder)/'TalkMan-report.json'), 1)
                ui.assert_not_called()


class ProtocolTests(unittest.TestCase):
    def test_preferences_migrate_without_changing_pairing_identity(self):
        from talkman_coloplay_protocol import DEFAULT_PREFERENCES
        base = validate_config({})
        self.assertEqual(base['preferences'], DEFAULT_PREFERENCES)
        changed = validate_config({**base, 'preferences': dict(target_delay_ms=400, reverse_rotation=True)})
        self.assertEqual(changed['token'], base['token'])
        self.assertEqual(config_id(changed), config_id(base))
        self.assertTrue(changed['preferences']['hide_hints'])
        self.assertTrue(changed['preferences']['hide_device_buttons'])
        for value in ({'keep_on_top':'false'}, {'hide_device_buttons':1}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config({**base, 'preferences':value})

    def test_lease_is_one_use_and_checks_snapshot(self):
        t=[0]; lease=Intent(lambda:t[0])
        lease.arm('1','fill',('text','target'),'fill')
        self.assertIsNone(lease.consume('fill',('text','other')))
        lease.arm('2','fill','stamp','fill')
        self.assertEqual(lease.consume('fill','stamp'),'fill')
        self.assertIsNone(lease.consume('fill','stamp'))
        lease.arm('3','fill','stamp','fill'); t[0]=4
        self.assertIsNone(lease.consume('fill','stamp'))

    def test_keys_are_safe_unique_and_match_hid(self):
        cfg=validate_config({})
        self.assertEqual([hid_binding(cfg['bindings'][k])[1] for k in list(DEFAULT_BINDINGS)[:8]],list(range(104,112)))
        self.assertEqual(len(set(cfg['bindings'].values())),19)
        for bad in ['space','Control+R','Control+Alt+Shift+F21','Control+Alt+Shift+F4']:
            with self.subTest(bad=bad), self.assertRaises(ValueError): hid_binding(bad)
        with self.assertRaises(ValueError):
            validate_config({'bindings':{'fill':DEFAULT_BINDINGS['ai']}})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.events=queue.Queue()
        self.server=StateServer('127.0.0.1',0,'a'*48,'cfg',self.events)
        self.server.publish(dict(ready=False,phase='idle'))
        self.url=f'ws://127.0.0.1:{self.server.port}'

    def tearDown(self): self.server.stop()

    def hello(self, ws, token='a'*48):
        ws.send(json.dumps(dict(type='hello',version=1,token=token,config='cfg')))

    def test_authentication_intent_exit_and_no_remote_edit(self):
        with connect(self.url) as ws:
            self.hello(ws)
            state=json.loads(ws.recv(timeout=2))
            self.assertFalse(state['ready'])
            self.assertNotIn('text',state)
            kind,peer,_=self.events.get(timeout=2)
            self.assertEqual(kind,'connected')
            ws.send(json.dumps(dict(type='intent',id='1',action='fill')))
            self.assertEqual(self.events.get(timeout=2)[:2],('intent',peer))
            ws.send(json.dumps(dict(type='exit')))
            self.assertEqual(self.events.get(timeout=2)[0],'exit')
        with connect(self.url) as ws:
            self.hello(ws,'wrong')
            with self.assertRaises(ConnectionClosed): ws.recv(timeout=2)
        with connect(self.url) as ws:
            self.hello(ws); ws.recv(timeout=2)
            ws.send(json.dumps(dict(type='edit',text='不能执行')))
            with self.assertRaises(ConnectionClosed):
                while True: ws.recv(timeout=2)

    def test_second_peer_is_rejected_and_server_stops(self):
        with connect(self.url) as first:
            self.hello(first); first.recv(timeout=2)
            with connect(self.url) as second:
                self.hello(second)
                with self.assertRaises(ConnectionClosed): second.recv(timeout=2)


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(Path('TalkMan-设备卡带/TalkMan-手势.lua').read_text(encoding='utf-8'))
        self.g=self.lua.globals().TMGesture(self.lua.table_from(dict(center=4,left=5,right=6,fill_ms=800,exit_ms=5000)))
        self.step([],0)

    def step(self, keys, ms):
        out=self.g.step(self.g,self.lua.table_from({i:True for i in keys}),ms)
        return list(out.values())

    def test_upper_left_short_and_five_second_exit_are_exclusive(self):
        self.assertEqual(self.step([0],100),['arm_ai'])
        self.assertEqual(self.step([0],5000),[])
        self.assertEqual(self.step([0],5100),['exit'])
        self.assertEqual(self.step([],5200),[])
        self.setUp()
        self.step([0],100)
        self.assertEqual(self.step([],300),['ai'])

    def test_modifier_order_and_no_repeat(self):
        self.assertEqual(self.step([3],100),[])
        self.assertEqual(self.step([3,2],200),['rewrite'])
        self.assertEqual(self.step([3,2],3000),[])
        self.assertEqual(self.step([2],3100),[])
        self.step([],3200)
        self.assertEqual(self.step([2,3],3300),['record'])

    def test_cancel_priority_and_hold_no_delete_repeat(self):
        self.assertEqual(self.step([1,2],100),['cancel_delete'])
        self.assertEqual(self.step([1],1000),[])
        self.assertEqual(self.step([],1200),[])

    def test_clear_requires_modifier_first_and_never_repeats_on_hold_or_release(self):
        self.assertEqual(self.step([3],100),[])
        self.assertEqual(self.step([3,1,2],200),['clear'])
        self.assertEqual(self.step([3,1],3000),[])
        self.assertEqual(self.step([1],3100),[])
        self.assertEqual(self.step([],3200),[])
        self.assertEqual(self.step([3,1],3300),['cancel_delete'])
        self.step([],3400)
        self.assertEqual(self.step([1],3500),['cancel_delete'])
        self.assertEqual(self.step([1,3],3600),[])

    def test_boot_with_clear_held_does_not_delete(self):
        self.g.boot=True
        self.assertEqual(self.step([3,1],100),[])
        self.assertEqual(self.step([3,1],6000),[])
        self.assertEqual(self.step([],6100),[])

    def test_knob_short_long_rotation_mutually_exclusive(self):
        self.assertEqual(self.step([4],100),['arm_knob'])
        self.assertEqual(self.step([],300),['knob'])
        self.step([4],400)
        self.assertEqual(self.step([],1400),['fill'])
        self.step([4],1500)
        self.assertEqual(self.step([4,6],1800),['cancel_arm'])
        self.assertEqual(self.step([],2500),[])

    def test_turn_while_held_only_cancels_until_release(self):
        for direction in (5,6):
            with self.subTest(direction=direction):
                self.setUp()
                self.assertEqual(self.step([4,direction],100),['arm_knob','cancel_arm'])
                self.assertEqual(self.step([4],400),[])
                self.assertEqual(self.step([4,11-direction],1000),[])
                self.assertEqual(self.step([direction],1500),[])
                self.step([],1600)
                self.assertEqual(self.step([direction],1700),['previous' if direction==5 else 'next'])
                self.step([],1800)
                self.assertEqual(self.step([4],1900),['arm_knob'])
                self.assertEqual(self.step([],2800),['fill'])

    def test_turn_after_fill_threshold_still_cancels_without_switching(self):
        self.step([4],100)
        self.assertEqual(self.step([4],900),[])
        self.assertEqual(self.step([4,6],1000),['cancel_arm'])
        self.assertEqual(self.step([],1200),[])

    def test_boot_with_held_button_does_not_act(self):
        self.g.boot=True
        self.assertEqual(self.step([0],0),[])
        self.assertEqual(self.step([0],6000),[])
        self.assertEqual(self.step([],7000),[])

    def test_complete_cartridge_compiles_and_draws_with_documented_host_api(self):
        root=Path('TalkMan-设备卡带')
        source=(root/'TalkMan-coloplay.clx').read_text(encoding='utf-8').split('__lua__\n',1)[1]
        source='\n'.join((root/line.split(' ',1)[1]).read_text(encoding='utf-8')
                         if line.startswith('#include ') else line for line in source.splitlines())
        self.lua.execute('''
          function time() return 1 end
          function stat(i) return 1000 end
          function usemod(m) end
          function btn(i) return false end
          function btnpcfg(a,b) end
          function cls(c) assert(c~=nil) end
          function line(a,b,c,d,color) assert(color~=nil) end
          function print(text,x,y,color,opt,size) assert(color~=nil); assert(size==16 or size==8) end
          function getcolor(r,g,b) return r*65536+g*256+b end
          function wline(w,a,b,c,d,color) assert(color~=nil) end
          function rect(a,b,c,d,color) assert(color~=nil) end
          function rectfill(a,b,c,d,color) assert(color~=nil) end
          function circ(a,b,r,color) assert(color~=nil) end
          function circfill(a,b,r,color) assert(color~=nil) end
          black=0;white=7;light_gray=6;red=8
          package.preload.cjson_safe=function() return {encode=function() return '{}' end,decode=function() return {} end} end
        ''')
        self.lua.execute(source)
        self.lua.execute('copas.step=function() end; _init(); _update(); _draw()')


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='TalkMan-coloplay-test-')
        Path(self.temp.name, 'interface.json').write_text('{"free_mode":false}', encoding='utf-8')
        self.app=TalkManApp(Path(self.temp.name),session_factory=FakeVoice,
            file_session_factory=FakeVoice,client_factory=FakeClient,desktop_services=False)
        self.app.update_idletasks()
        self.app.settings=QwenSettings(api_key='sk-offline-test')
        self.app.after_cancel(self.app.poll_job); self.app.poll_job=None
        self.app.after_cancel(self.app.coloplay.job); self.app.coloplay.job=None
        self.app.run_worker=Mock()
        self.device=self.app.coloplay
        self.ready=patch.object(self.device,'ready',return_value=True); self.ready.start()
        self.permission=patch('talkman_app.prepare_microphone_permission',return_value=True);self.permission.start()

    def tearDown(self):
        self.ready.stop(); self.permission.stop()
        self.app.target_filling=False
        self.app.close_app(); self.app=None
        FakeVoice.sessions.clear();gc.collect();self.temp.cleanup()

    def action(self, action):
        self.device.arm(dict(action='knob' if action=='fill' else action,id='test'))
        self.device.dispatch('coloplay_'+action)

    def test_settings_keep_same_wifi_session_and_pause_actions_only(self):
        a, d = self.app, self.device
        self.ready.stop()
        with patch('talkman_coloplay.StateServer', side_effect=lambda host, port, *args, **kwargs:
                   StateServer(host, 0, *args, **kwargs)):
            d.configure(dict(d.config, enabled=True, host='127.0.0.1', discovery=False))
        d.registered = set(d.bindings())
        server = d.server
        a.set_text('设置期间保留正文')
        def tick():
            d.tick()
            a.after_cancel(d.job)
            d.job = None
            a.update_idletasks()
        with connect(f'ws://127.0.0.1:{server.port}', proxy=None) as ws:
            ws.send(json.dumps(dict(type='hello', version=1, token=d.config['token'],
                                   config=config_id(d.config), heartbeat='state-ack')))
            first = json.loads(ws.recv(timeout=2))
            tick()
            peer = d.peer
            self.assertTrue(d.ready())
            d.arm(dict(action='cancel_delete', id='before-settings'))
            a.open_settings('coloplay')
            tick()
            self.assertTrue(d.connected())
            self.assertTrue(d.active)
            self.assertFalse(d.ready())
            self.assertEqual(d.intent.id(), '')
            self.assertIn('已连接', d.badge.get())
            self.assertEqual(d.snapshot()['phase'], 'blocked')
            self.assertTrue(d.snapshot()['ready'])
            self.assertFalse(d.snapshot()['can_fill'])
            d.dispatch('coloplay_cancel_delete')
            d.arm(dict(action='record', id='while-settings'))
            d.dispatch('coloplay_record')
            self.assertIsNone(a.voice)
            self.assertEqual(a.current_text(), '设置期间保留正文')
            # 超过服务端 4 秒心跳超时，实际回环收发必须维持同一会话。
            deadline = time.monotonic() + 4.3
            pages = iter(('window', 'target', 'coloplay'))
            switched = time.monotonic()
            while time.monotonic() < deadline:
                ws.send(json.dumps(dict(type='heartbeat')))
                frame = json.loads(ws.recv(timeout=2))
                self.assertEqual(frame['session'], first['session'])
                self.assertEqual(d.peer, peer)
                self.assertIs(d.server, server)
                tick()
                if time.monotonic()-switched > 1:
                    page = next(pages, None)
                    if page:
                        a.open_settings(page)
                    switched = time.monotonic()
            self.assertTrue(frame['ready'])
            self.assertEqual(frame['title'], '桌面设置中')
            a._close_settings(a.settings_window)
            d.registered = set(d.bindings())
            tick()
            self.assertTrue(d.ready())
            self.assertEqual(d.peer, peer)
            self.assertEqual(d.snapshot()['phase'], 'idle')
            self.action('cancel_delete')
            self.assertEqual(a.current_text(), '设置期间保留正')

    def test_delete_selection_and_unicode_cluster_is_one_undo(self):
        for value,result in [('中文','文'),('e\u0301文','文'),('👨‍👩‍👧‍👦文','文'),('🇨🇳文','文'),('👍🏽文','文')]:
            with self.subTest(value=value):
                self.app.set_text(value);self.app.text.mark_set('insert',f'1.0 + {len(value)-len(result)} chars')
                self.action('cancel_delete')
                self.assertEqual(self.app.current_text(),result)
                self.app.undo(); self.assertEqual(self.app.current_text(),value)
        self.app.set_text('不要误删全文')
        self.app.text.tag_add('sel','1.2','1.4')
        self.action('cancel_delete')
        self.assertEqual(self.app.current_text(),'不要全文')
        self.app.set_text('😀甲乙')
        self.app.text.mark_set('insert','1.3')
        self.action('cancel_delete')
        self.assertEqual(self.app.current_text(),'😀乙')
        self.assertEqual(self.app.text.get('1.0','insert'),'😀')

    def test_busy_cancel_never_becomes_delete_after_result_arrives(self):
        a=self.app;a.set_text('保留的文字');a.start_voice('insert',from_device=True)
        self.device.arm(dict(action='cancel_delete',id='cancel'))
        a.cancel_operation()
        self.device.dispatch('coloplay_cancel_delete')
        self.assertEqual(a.current_text(),'保留的文字')

    def test_clear_whole_editor_with_selection_is_one_undo_and_redo(self):
        a=self.app;value='第一段😀\n第二段保留历史'
        a.set_text(value);a.text.tag_add('sel','1.1','1.3')
        self.action('clear')
        self.assertEqual(a.current_text(),'')
        self.assertIsNone(a.get_selection())
        a.undo();self.assertEqual(a.current_text(),value)
        a.redo();self.assertEqual(a.current_text(),'')

    def test_clear_cannot_execute_unarmed_stale_busy_or_review(self):
        a=self.app;a.set_text('不能误删')
        self.device.dispatch('coloplay_clear')
        self.assertEqual(a.current_text(),'不能误删')
        self.device.arm(dict(action='clear',id='old-clear'))
        a.set_text('后来编辑的新内容')
        self.device.dispatch('coloplay_clear')
        self.assertEqual(a.current_text(),'后来编辑的新内容')
        for phase in ('opening','recording','processing','permission','review','filling'):
            with self.subTest(phase=phase), patch.object(self.device,'phase',return_value=(phase,'')):
                self.action('clear')
                self.assertEqual(a.current_text(),'后来编辑的新内容')

    def test_background_instruction_and_actual_ready_status(self):
        a=self.app;a.set_text('这里插入');a.text.mark_set('insert','1.2')
        a.withdraw()
        self.action('rewrite')
        self.assertEqual(a.voice_mode,'rewrite')
        self.assertEqual(self.device.phase()[0],'opening')
        FakeVoice.sessions[-1].emit('ready','')
        a.poll_events();a.after_cancel(a.poll_job);a.poll_job=None
        self.assertEqual(self.device.phase()[0],'recording')
        self.action('record');self.assertTrue(a.voice_stopping)

    def test_ai_once_and_no_unarmed_hid(self):
        a=self.app;a.set_text('想改写这句话')
        with patch.object(a,'organize_once') as organize:
            self.device.dispatch('coloplay_ai');organize.assert_not_called()
            self.action('ai');organize.assert_called_once()

    def make_review(self):
        a = self.app
        a.set_text('请把苹果拿过来。')
        a.text.tag_add('sel', '1.2', '1.4')
        a.preview_ai_var.set(True)
        a.organize_once()
        event, token, _ = a.run_worker.call_args.args
        a.events.put((event, token, 'ok', '请把梨拿过来。'))
        a.poll_events(); a.after_cancel(a.poll_job); a.poll_job = None
        a.update()
        self.assertIsNotNone(a.ai_review)
        return a

    def test_upper_left_confirms_review_once_and_supports_undo(self):
        a = self.make_review()
        self.assertEqual(a.current_text(), '请把苹果拿过来。')
        self.assertEqual(self.device.snapshot()['labels']['ai'], '确认')
        with patch.object(a, 'organize_once') as organize:
            self.action('ai')
            self.assertIsNone(a.ai_review)
            self.assertEqual(a.current_text(), '请把梨拿过来。')
            self.assertEqual(a.get_selection()[2], '梨')
            self.device.dispatch('coloplay_ai')
            organize.assert_not_called()
        self.assertEqual(self.device.snapshot()['labels']['ai'], 'AI 改写')
        a.undo()
        self.assertEqual(a.current_text(), '请把苹果拿过来。')

    def test_upper_right_discards_review_without_deleting_text(self):
        a = self.make_review()
        before = (a.timeline.items[:], a.timeline.index)
        self.action('cancel_delete')
        self.assertIsNone(a.ai_review)
        self.assertEqual(a.current_text(), '请把苹果拿过来。')
        self.assertEqual(a.get_selection()[2], '苹果')
        self.assertEqual((a.timeline.items, a.timeline.index), before)

    def test_old_confirmation_does_not_apply_another_review(self):
        a = self.make_review()
        self.device.arm(dict(action='ai', id='old-review'))
        old_stamp = self.device.stamp()
        a.ai_review_finish()
        applied = Mock()
        a.review_result(a.current_text(), '另一个改写结果', 'AI 整理', applied)
        new_stamp = self.device.stamp()
        self.assertEqual(old_stamp[:10]+old_stamp[11:], new_stamp[:10]+new_stamp[11:])
        self.assertNotEqual(old_stamp[10], new_stamp[10])
        self.device.dispatch('coloplay_ai')
        applied.assert_not_called()
        self.assertIsNotNone(a.ai_review)
        self.assertEqual(a.current_text(), '请把苹果拿过来。')

    def test_changed_text_invalidates_pending_confirmation(self):
        a = self.make_review()
        self.device.arm(dict(action='ai', id='old-text'))
        a.set_text('新正文要保留')
        self.device.dispatch('coloplay_ai')
        self.assertEqual(a.current_text(), '新正文要保留')
        self.assertIsNotNone(a.ai_review)

    def test_paste_only_never_triggers_send_even_if_sync_was_on(self):
        a=self.app;a.set_text('只填入，不发送')
        target=TargetWindow(1,2,'豆包','对话','doubao.exe')
        a.target_controller.close()
        a.target_controller=Mock(selected=target)
        a.sync_mode_var.set(True)
        with patch.object(a.live_sync,'flush') as flush:
            self.action('fill')
            flush.assert_not_called()
            a.target_controller.fill_with_receipt.assert_called_once()
            self.assertEqual(a.target_controller.fill_with_receipt.call_args.kwargs['expected_target'],target.session_key())
            self.assertTrue(a.target_controller.fill_with_receipt.call_args.kwargs['allow_send_confirmation'])
            a.target_controller.send_filled.assert_not_called()

    def test_device_fill_clears_existing_desktop_send_confirmation(self):
        a=self.app;a.set_text('设备仍然只填入')
        target=TargetWindow(1,2,'豆包','对话','doubao.exe')
        a.target_controller.close();a.target_controller=Mock(selected=target)
        a.pending_delivery=dict(token='old-token', expires=time.monotonic()+60,
            revision=a.revision, text=a.current_text(), ready_at=0)
        with patch.object(a,'send_pending_delivery') as send:
            a.fill_to_target(paste_only=True)
            send.assert_not_called()
        self.assertIsNone(a.pending_delivery)
        a.target_controller.stop_sync.assert_called_once()
        self.assertFalse(a.target_controller.fill_with_receipt.call_args.kwargs['allow_send_confirmation'])

    def test_preferences_apply_restore_and_do_not_restart_connection(self):
        a,d=self.app,self.device
        a.topmost_var.set(False);a.edge_hide_var.set(True)
        original_token=d.config['token'];original_id=config_id(d.config)
        d.set_active(True)
        self.assertTrue(a.topmost_var.get())
        with patch.object(d, 'stop_service') as stop, patch.object(d.highlight, 'hide') as hide:
            d.save_preferences(dict(target_delay_ms=1000, reverse_rotation=True,
                highlight_target=False, keep_on_top=False, hide_hints=False))
            stop.assert_not_called();hide.assert_called_once()
        self.assertFalse(a.topmost_var.get())
        self.assertTrue(all(w.winfo_manager()=='pack' for w in a.voice_hint_widgets))
        self.assertEqual(d.config['token'],original_token)
        self.assertEqual(config_id(d.config),original_id)
        loaded=validate_config(json.loads(d.path.read_text('utf-8')))
        self.assertEqual(loaded['preferences'],d.preferences)
        d.set_active(False)
        self.assertFalse(a.topmost_var.get());self.assertTrue(a.edge_hide_var.get())

    def test_failed_preferences_save_keeps_current_runtime(self):
        d=self.device;before=dict(d.preferences)
        with patch('talkman_coloplay.save_config', side_effect=OSError('只读')):
            with self.assertRaises(OSError):d.save_preferences({'reverse_rotation':True})
        self.assertEqual(d.preferences,before)

    def test_preferences_page_save_and_restore_preserve_editor(self):
        a=self.app;a.set_text('设置时保留正文与选区');a.text.tag_add('sel','1.0','1.3')
        selected=a.get_selection()
        self.device.settings();a.update_idletasks()
        self.assertEqual(a.settings_notebook.select(),str(a.settings_tabs['coloplay']))
        ui=a.coloplay_preferences_controls
        ui['values']['reverse_rotation'].set(True);ui['save']()
        self.assertNotIn('target_delay_ms', self.device.preferences)
        self.assertTrue(self.device.preferences['reverse_rotation'])
        ui['restore']();self.assertTrue(self.device.preferences['reverse_rotation'])
        ui['save']();self.assertFalse(self.device.preferences['reverse_rotation'])
        self.assertEqual(a.current_text(),'设置时保留正文与选区');self.assertEqual(a.get_selection(),selected)
        a._close_settings(a.settings_window)

    def test_configured_direction_ignores_obsolete_delay_and_release_commits(self):
        a,d=self.app,self.device
        windows=[TargetWindow(i,2,'Codex',str(i),'codex.exe') for i in range(1,4)]
        a.target_controller.close();a.target_controller=Mock(windows=windows,selected=windows[0])
        d.save_preferences(dict(target_delay_ms=1000, reverse_rotation=True))
        a.pending_delivery={'token':'stale'}
        with patch('talkman_coloplay.time.monotonic',return_value=10):
            d.execute('target_next')
            self.assertEqual(d.target_preview,windows[2]);self.assertEqual(d.target_due,10)
        self.assertIsNone(a.pending_delivery)
        with patch('talkman_coloplay.time.monotonic',return_value=10.5):
            self.assertEqual(d.snapshot()['target_progress'],1)
            d.active=True;a.target_controller.cycle.assert_not_called()
        with patch('talkman_coloplay.time.monotonic',return_value=11.01):
            d.commit_target();a.target_controller.begin_inputs.assert_called_once()
            self.assertEqual(a.target_controller.begin_inputs.call_args.args[0],windows[2])
            self.assertEqual(a.target_controller.selected,windows[0])

    def test_mac_release_starts_position_selection_without_committing_agent(self):
        a,d=self.app,self.device
        target=TargetWindow(12,43,'ZCode','项目','dev.zcode.app',200.0)
        a.target_controller.close()
        a.target_controller=Mock(selected=None,windows=[target])
        d.target_preview=target;d.target_due=1;d.active=True
        with patch('talkman_coloplay.sys.platform','darwin'):
            d.commit_target()
        self.assertEqual(a.target_controller.begin_inputs.call_args.args[0],target)
        a.target_controller.cycle.assert_not_called()
        self.assertIsNone(a.target_controller.selected)

    def test_mac_wifi_position_activation_failure_keeps_old_target(self):
        a,d=self.app,self.device
        doubao=TargetWindow(11,42,'豆包','豆包对话','com.bot.pc.doubao',100.0)
        zcode=TargetWindow(12,43,'ZCode','项目','dev.zcode.app',200.0)
        backend=FakeBackend();backend.windows=[doubao,zcode]
        backend.activate_window=Mock(side_effect=RuntimeError('后台窗口未获得焦点'))
        backend.list_inputs=Mock(return_value=[])
        backend.inspect_input=Mock()
        backend.bind_input=Mock()
        a.target_controller.close()
        a.target_controller=TargetController(Path(self.temp.name),backend=backend)
        refreshed=[];a.target_controller.refresh(lambda *r:refreshed.append(r))
        self.assertEqual(wait_result(refreshed)[0],'ok')
        backend.list_windows=Mock(side_effect=RuntimeError('后台扫描暂时不可用'))
        d.active=True
        a.set_text('保留编辑正文')
        with patch('talkman_coloplay.sys.platform','darwin'),patch.object(d.highlight,'show'):
            for expected in (zcode,doubao,zcode):
                old=a.target_controller.selected
                self.action('target_next')
                self.assertEqual(d.target_preview,expected)
                self.assertEqual(d.snapshot()['target_choice'],expected.app)
                self.assertEqual(a.target_controller.selected,old)
                self.action('target_commit')
                deadline=time.monotonic()+2
                while not a.input_picker.error and time.monotonic()<deadline:
                    if a.input_picker.job:a.after_cancel(a.input_picker.job);a.input_picker.job=None
                    a.input_picker.tick()
                    time.sleep(.01)
                self.assertTrue(a.input_picker.error)
                self.assertEqual(a.target_controller.selected,old)
                a.input_picker.cancel()
                # 再次预选从旧目标起步，不从失败草案起步。
                a.target_controller.select(expected)
        self.assertEqual(a.current_text(),'保留编辑正文')
        backend.list_windows.assert_not_called();self.assertEqual(backend.activate_window.call_count,3)
        self.assertFalse(any(isinstance(c,tuple) and c[0]=='fill' for c in backend.calls))

    def test_highlight_switch_controls_success_feedback(self):
        a,d=self.app,self.device
        target=TargetWindow(1,2,'Codex','对话','codex.exe')
        a.target_controller.close();a.target_controller=Mock(selected=target,windows=[target],label='Codex')
        for enabled in (False,True):
            d.save_preferences({'highlight_target':enabled})
            d.events.put(('cycle','ok',(d.target_generation,target)))
            with patch.object(d.highlight,'show') as show:
                d.tick();a.after_cancel(d.job);d.job=None
                self.assertEqual(show.call_count,int(enabled))


    def test_exit_restores_hints_and_keeps_draft_and_voice(self):
        a=self.app;a.set_text('退出不丢失')
        a.start_voice('insert',from_device=True)
        self.device.set_active(True)
        self.assertTrue(all(not w.winfo_manager() for w in a.voice_hint_widgets))
        self.device.hidden=True;a.withdraw()
        self.device.set_active(False)
        self.assertTrue(all(w.winfo_manager()=='pack' for w in a.voice_hint_widgets))
        self.assertFalse(self.device.hidden)
        self.assertIsNotNone(a.voice)
        self.assertEqual(a.current_text(),'退出不丢失')

    def test_hidden_window_does_not_reopen_on_layout_ticks(self):
        self.action('toggle')
        self.assertTrue(self.device.hidden)
        for _ in range(3):self.app.window_controller._layout_tick()
        self.assertEqual(self.app.state(),'withdrawn')
        self.action('toggle')
        self.assertFalse(self.device.hidden)

    def test_changing_text_or_target_invalidates_fill(self):
        a=self.app;a.set_text('旧正文')
        a.target_controller.close()
        a.target_controller=Mock(selected=TargetWindow(1,2,'豆包','对话','doubao.exe'))
        self.device.arm(dict(action='fill',id='old'))
        a.set_text('新正文')
        with patch.object(a,'fill_to_target') as fill:
            self.device.dispatch('coloplay_fill');fill.assert_not_called()

    def test_export_contains_config_and_all_official_libraries(self):
        dest=Path(self.temp.name)/'TalkMan-设备测试'
        self.device.export(dest)
        for name in ['TalkMan-coloplay.clx','TalkMan-手势.lua','TalkMan-连接配置.lua',
                     'colox_ws.lua','colox_http.lua','colox_net.lua']:
            self.assertTrue((dest/name).is_file())
        config=(dest/'TalkMan-连接配置.lua').read_text(encoding='utf-8')
        self.assertIn(config_id(self.device.config),config)


class DeviceControlsVisibilityTests(unittest.TestCase):
    setUp = DesktopTests.setUp
    tearDown = DesktopTests.tearDown

    def test_connection_expands_editor_without_changing_draft_selection_or_undo(self):
        a,d=self.app,self.device
        a.geometry('900x700');a.set_text('正文和选区保持不变')
        a.text.tag_add('sel','1.1','1.4');a.text.mark_set('insert','1.4');a.update()
        before=(a.current_text(),a.get_selection(),a.text.index('insert'),a.timeline.items[:],a.timeline.index)
        height=a.text.winfo_height()
        d.set_active(True);a.update()
        self.assertFalse(a.replace_button.winfo_ismapped())
        self.assertFalse(a.rewrite_button.winfo_ismapped())
        self.assertFalse(a.one_filler_button.winfo_ismapped())
        self.assertGreater(a.text.winfo_height(),height)
        self.assertTrue(a.translate_button.winfo_ismapped())
        self.assertTrue(a.target_button.winfo_ismapped())
        self.assertTrue(a.settings_button.winfo_ismapped())
        self.assertEqual(before,(a.current_text(),a.get_selection(),a.text.index('insert'),a.timeline.items[:],a.timeline.index))
        d.set_active(False);a.update()
        self.assertTrue(a.replace_button.winfo_ismapped())
        self.assertTrue(a.one_filler_button.winfo_ismapped())
        self.assertFalse(a.mic_button.winfo_ismapped())  # 旧的重复麦克风入口不复活。

    def test_setting_restores_buttons_and_pack_order_without_repairing(self):
        a,d=self.app,self.device
        order=a.one_filler_button.master.pack_slaves()
        identity=config_id(d.config);token=d.config['token']
        d.set_active(True)
        with patch.object(d,'stop_service') as restart:
            d.save_preferences({**d.preferences,'hide_device_buttons':False})
            restart.assert_not_called()
        self.assertEqual(a.full_voice_dock.winfo_manager(),'grid')
        self.assertEqual(a.one_filler_button.master.pack_slaves(),order)
        self.assertFalse(validate_config(json.loads(d.path.read_text('utf-8')))['preferences']['hide_device_buttons'])
        self.assertEqual(config_id(d.config),identity);self.assertEqual(d.config['token'],token)
        for theme in ('dark','light','matrix'):
            a.set_theme(theme)
            self.assertEqual(a.full_voice_dock.winfo_manager(),'grid')
        d.save_preferences({**d.preferences,'hide_device_buttons':True})
        self.assertEqual(a.full_voice_dock.winfo_manager(),'')

    def test_compact_transitions_and_disconnect_restore_correct_controls(self):
        a,d=self.app,self.device;d.set_active(True)
        a.set_compact_layout(True)
        self.assertEqual(a.compact_record.winfo_manager(),'')
        self.assertEqual(a.compact_rewrite.winfo_manager(),'')
        self.assertEqual(a.compact_cancel.winfo_manager(),'pack')
        self.assertEqual(a.compact_fill.winfo_manager(),'pack')
        d.set_active(False)
        self.assertEqual(a.full_voice_dock.winfo_manager(),'')
        self.assertEqual(a.compact_record.master.pack_slaves()[:3],[a.compact_record,a.compact_rewrite,a.compact_cancel])
        a.set_compact_layout(False)
        self.assertEqual(a.full_voice_dock.winfo_manager(),'grid')
        d.set_active(True);a.set_compact_layout(True);a.set_compact_layout(False)
        self.assertEqual(a.full_voice_dock.winfo_manager(),'')

    def test_disconnect_during_recording_restores_controls_without_stopping(self):
        a,d=self.app,self.device;a.set_text('断开仍保留')
        d.set_active(True);self.assertTrue(a.start_voice('insert',from_device=True))
        voice=a.voice;operation=a.operation_id
        d.set_active(False)
        self.assertEqual(a.full_voice_dock.winfo_manager(),'grid')
        self.assertIs(a.voice,voice);self.assertEqual(a.operation_id,operation)
        self.assertEqual(a.current_text(),'断开仍保留')

    def test_settings_can_disable_and_restore_default(self):
        a,d=self.app,self.device;a.open_settings('coloplay');a.update()
        ui=a.coloplay_preferences_controls
        self.assertTrue(ui['values']['hide_device_buttons'].get())
        ui['values']['hide_device_buttons'].set(False);ui['save']()
        a._close_settings(a.settings_window);d.set_active(True)
        self.assertEqual(a.full_voice_dock.winfo_manager(),'grid')
        a.open_settings('coloplay');a.update()
        ui=a.coloplay_preferences_controls
        self.assertFalse(ui['values']['hide_device_buttons'].get())
        ui['restore']();ui['save']();a._close_settings(a.settings_window)
        d.set_active(True);d.apply_device_preferences()
        self.assertEqual(a.full_voice_dock.winfo_manager(),'')


class TargetCycleTests(unittest.TestCase):
    def test_cancelling_during_enumeration_does_not_activate_any_window(self):
        import threading
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend();cancelled=threading.Event()
            target=TargetWindow(10,1,'Codex','旧标题','codex.exe')
            def scan():
                cancelled.set()
                return [replace(target,title='新标题')]
            backend.list_windows=scan;backend.activate_window=Mock()
            controller=TargetController(Path(folder),backend=backend)
            try:
                result=[];controller.cycle(1,lambda *x:result.append(x),expected_target=target.session_key(),cancelled=cancelled)
                self.assertEqual(wait_result(result)[0],'error')
                backend.activate_window.assert_not_called()
            finally:controller.close()

    def test_identity_survives_fresh_objects_and_changed_window_title(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend();backend.activate_window=Mock()
            shown=TargetWindow(30,1,'Codex','旧标题','codex.exe')
            backend.windows=[replace(shown,title='新标题')]
            controller=TargetController(Path(folder),backend=backend)
            try:
                result=[];controller.cycle(1,lambda *x:result.append(x),expected_target=shown.session_key())
                state,target=wait_result(result)
                self.assertEqual(state,'ok');self.assertEqual(target.title,'新标题')
            finally:controller.close()

    def test_previewed_window_is_pinned_when_windows_change(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend()
            first=TargetWindow(10,1,'Codex','原窗口','codex.exe')
            shown=TargetWindow(30,1,'豆包','预览窗口','doubao.exe')
            inserted=TargetWindow(20,1,'Cursor','新窗口','cursor.exe')
            backend.windows=[first,inserted,shown]
            backend.activate_window=Mock()
            controller=TargetController(Path(folder),backend=backend)
            try:
                controller.select(first)
                result=[];controller.cycle(1,lambda *x:result.append(x),expected_target=shown.session_key())
                state,target=wait_result(result)
                self.assertEqual(state,'ok');self.assertEqual(target.session_key(),shown.session_key())
                backend.activate_window.assert_called_once_with(shown)
                controller.select(first);backend.activate_window.reset_mock()
                backend.windows=[first,inserted]
                result=[];controller.cycle(1,lambda *x:result.append(x),expected_target=shown.session_key())
                self.assertEqual(wait_result(result)[0],'error')
                backend.activate_window.assert_not_called()
                self.assertEqual(controller.selected.session_key(),first.session_key())
            finally:controller.close()

    def test_cycle_stable_identity_and_failure_preserves_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend()
            backend.windows=[TargetWindow(30,1,'豆包','三','doubao.exe'),
                             TargetWindow(10,1,'豆包','一','doubao.exe'),
                             TargetWindow(20,1,'豆包','二','doubao.exe')]
            backend.activate_window=Mock()
            controller=TargetController(Path(folder),backend=backend)
            try:
                for target_id in [10,20,30,10]:
                    result=[];controller.cycle(1,lambda *x:result.append(x))
                    state,target=wait_result(result)
                    self.assertEqual(state,'ok');self.assertEqual(target.id,target_id)
                    backend.windows.reverse()
                backend.activate_window.side_effect=RuntimeError('无法激活')
                result=[];controller.cycle(1,lambda *x:result.append(x))
                self.assertEqual(wait_result(result)[0],'error')
                self.assertEqual(controller.selected.id,10)
                result=[];controller.fill_with_receipt('测试',lambda *x:result.append(x),expected_target=(99,None))
                self.assertEqual(wait_result(result)[0],'error')
                self.assertFalse(any(isinstance(c,tuple) and c[0]=='fill' for c in backend.calls))
            finally: controller.close()


if __name__=='__main__':unittest.main()
