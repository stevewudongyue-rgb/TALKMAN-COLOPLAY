"""真实 Copas 调度故障注入及指定位置发送，所有目标/音频均隔离。"""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from lupa.lua54 import LuaRuntime
from talkman_target import InputLocationError
import test_talkman_input_locations as location_fixture
import test_talkman_input_picker as picker_fixture


class Network59Tests(unittest.TestCase):
    def runtime(self, send, writable=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''clock=0; writes=0
            function time() return clock/1000 end
            function utcsec() return math.floor(clock/1000) end
            function printh(...) end
            math.random=function(...) return 100 end
            function net_select() return {},{} end''')
        lua.execute(Path('TalkMan-设备卡带/colox_net.lua').read_text('utf-8'))
        lua.execute('sock={timeout=function() return 3 end,send=function(self,data,first,last) writes=writes+1; '+send+' end}')
        if writable:
            lua.execute('function net_select() if clock>=100 then return {},{sock,[sock]=1} end; return {},{} end')
        lua.execute('copas.addthread(function() ok,err,last=copas.send(sock,"already sent"); finished=clock end)')
        for clock in range(0,4100,20):
            lua.globals().clock=clock;lua.execute('copas.step()')
        return lua.globals()

    def test_completed_send_yields_without_waiting_for_nonexistent_write_event(self):
        g=self.runtime('return #data,nil,#data')
        self.assertEqual(g.ok,12);self.assertIsNone(g.err)
        self.assertEqual(g.writes,1);self.assertLess(g.finished,100)

    def test_partial_send_waits_and_resumes_at_next_unsent_byte(self):
        g=self.runtime('if writes==1 then return false,"again",4 end; assert(first==5); return #data,nil,#data',True)
        self.assertEqual(g.ok,12);self.assertEqual(g.writes,2);self.assertGreaterEqual(g.finished,100)

    def test_blocked_or_failed_send_is_not_reported_as_success(self):
        g=self.runtime('return false,"again",0')
        self.assertIsNone(g.ok);self.assertEqual(g.err,'timeout')
        g=self.runtime('return nil,"closed",0')
        self.assertIsNone(g.ok);self.assertEqual(g.err,'closed');self.assertEqual(g.finished,0)


class Send59Tests(unittest.TestCase):
    setUp=location_fixture.TransactionTests.setUp
    result=location_fixture.TransactionTests.result
    begin=location_fixture.TransactionTests.begin
    confirm=location_fixture.TransactionTests.confirm

    def test_known_chat_location_restores_receipt_but_sends_only_on_confirmation(self):
        self.backend.input_supports_enter=lambda target,handle:handle[1]==1
        self.confirm()
        state,receipt=self.result(self.c.fill_with_receipt,'聊天内容')
        self.assertEqual(state,'ok');self.assertTrue(receipt.send_token)
        self.assertFalse(self.backend.sends)
        self.assertEqual(self.result(self.c.send_filled,receipt.send_token)[0],'ok')
        self.assertEqual(len(self.backend.sends),1)
        self.assertEqual(self.result(self.c.send_filled,receipt.send_token)[0],'error')

    def test_explicit_opt_in_is_per_location_and_changed_text_cannot_send(self):
        ticket=self.begin()
        self.result(self.c.inspect_input,ticket,1,confirm=True,enter_send=True)
        _,receipt=self.result(self.c.fill_with_receipt,'已填入')
        self.assertTrue(receipt.send_token)
        self.backend.fields[2][1]+='外部修改'
        self.assertEqual(self.result(self.c.send_filled,receipt.send_token)[0],'error')
        self.assertFalse(self.backend.sends)
        self.confirm(index=0)
        _,receipt=self.result(self.c.fill_with_receipt,'另一位置')
        self.assertFalse(receipt.send_token)

    def test_opt_out_overrides_known_composer(self):
        self.backend.input_supports_enter=lambda *_:True
        self.result(self.c.inspect_input,self.begin(),1,confirm=True,enter_send=False)
        _,receipt=self.result(self.c.fill_with_receipt,'只填入')
        self.assertFalse(receipt.send_token)


class Windows59Tests(unittest.TestCase):
    def test_only_recognized_chat_composer_defaults_to_enter_send(self):
        from talkman_target_windows import WindowsTargetBackend
        backend = WindowsTargetBackend()
        backend.inspect_input = Mock(return_value=(0, 0, 200, 80))
        for identity, name, classes, expected in (
                ('C:/Apps/ChatGPT.exe', 'Work with ChatGPT', 'ProseMirror', True),
                ('C:/Apps/OpenAI.Codex/ChatGPT.exe', 'Work with Codex', 'tiptap ProseMirror', True),
                ('C:/Apps/ChatGPT.exe', '补充内容', 'ProseMirror', False),
                ('C:/Apps/ChatGPT.exe', 'Work with ChatGPT', 'Edit', False),
                ('C:/Apps/Other.exe', 'Work with ChatGPT', 'ProseMirror', False)):
            with self.subTest(identity=identity, name=name, classes=classes):
                element = SimpleNamespace(CurrentName=name, CurrentClassName=classes)
                handle = (1, 2, identity, '标题', element, (1, 2))
                self.assertEqual(backend.input_supports_enter(SimpleNamespace(identity=identity), handle), expected)

    def test_destroyed_uia_control_requests_reselection(self):
        from talkman_target_windows import WindowsTargetBackend, TargetWindow
        backend = WindowsTargetBackend()
        target = TargetWindow(100, 42, 'Agent', '标题', 'agent.exe')
        backend.validate_window = lambda _: target
        element = Mock()
        element.GetRuntimeId.side_effect = OSError('UIA control unavailable')
        with self.assertRaises(InputLocationError):
            backend.inspect_input(target, (100, 42, 'agent.exe', '旧标题', element, (1,)))


class Picker59Tests(unittest.TestCase):
    setUp=picker_fixture.PickerTests.setUp
    tearDown=picker_fixture.PickerTests.tearDown
    pump=picker_fixture.PickerTests.pump
    begin_device=picker_fixture.PickerTests.begin_device

    def test_verified_fill_can_clear_editor_then_left_top_confirms_once(self):
        a,d,p=self.app,self.device,self.picker
        self.backend.input_supports_enter=lambda *_:True
        self.begin_device();p.confirm();self.pump(lambda:not p.active)
        a.set_text('设备确认内容');a.clear_after_fill_var.set(True)
        d.execute('fill');self.pump(lambda:not a.target_filling)
        self.assertEqual(a.current_text(),'');self.assertTrue(d.device_send_pending())
        self.assertEqual(d.snapshot()['labels']['ai'],'发送')
        self.assertFalse(self.backend.sends)
        self.assertEqual(d.semantic('ai'),'send')
        d.execute('send');self.pump(lambda:not a.target_filling)
        self.assertEqual(len(self.backend.sends),1)

    def test_gone_control_opens_position_selection_without_retrying_paste(self):
        self.begin_device();self.picker.confirm();self.pump(lambda:not self.picker.active)
        a=self.app;a.set_text('保留内容')
        with patch.object(self.backend,'bind_input',side_effect=InputLocationError('所选输入区域已消失')):
            a.fill_to_target();self.pump(lambda:not a.target_filling)
        self.assertTrue(self.picker.active);self.assertEqual(a.current_text(),'保留内容')
        self.assertFalse(self.backend.writes)

    def test_submit_checkbox_does_not_leak_between_positions(self):
        self.begin_device();p=self.picker
        p.send_toggle.invoke();self.assertTrue(p.enter_send.get())
        p.step(1);self.assertFalse(p.enter_send.get())
        p.step(-1);self.assertTrue(p.enter_send.get())

    def test_failed_highlight_blocks_confirmation_and_preserves_previous_target(self):
        self.begin_device()
        self.show.return_value=False
        self.picker.confirm()
        self.assertFalse(self.picker.confirming)
        self.assertIn('无法显示',self.picker.error)
        self.assertEqual(self.app.target_controller.selected,self.backend.windows[0])


if __name__=='__main__':unittest.main()
