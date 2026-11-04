"""主输入框失效恢复及进程身份：离线假目标，不写真实Agent。"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import test_talkman_input_locations as fixtures
from talkman_input_locations import main_input_role
from talkman_target import InputLocationError, TargetWindow


class MainRecoveryTests(unittest.TestCase):
    setUp = fixtures.TransactionTests.setUp
    result = fixtures.TransactionTests.result
    begin = fixtures.TransactionTests.begin

    def choose(self, role=('id', 'prompt-textarea')):
        b=self.backend
        self.new=replace(self.new,process_started='same-process')
        b.windows[1]=self.new
        b.input_position_role=lambda *_:role
        b.input_supports_enter=lambda *_:True
        ticket=self.begin()
        self.assertEqual(self.result(self.c.inspect_input,ticket,0,confirm=True)[0],'ok')
        old=b.inspect_input
        self.stale=False
        def inspect(target,handle):
            if self.stale and handle==(2,0):
                raise InputLocationError('旧控件消失')
            return old(target,handle)
        b.inspect_input=inspect
        b.resolve_main_input=Mock(return_value=(2,1))

    def test_rebuilt_main_recovers_only_for_active_fill_and_can_rearm_verified_send(self):
        self.choose();self.stale=True
        state,receipt=self.result(self.c.fill_with_receipt,'新内容')
        self.assertEqual(state,'ok',receipt)
        self.assertEqual(self.backend.fields[2],['','新内容'])
        self.assertEqual(self.backend.writes,[(2,1)])
        self.assertTrue(receipt.send_token)
        self.backend.resolve_main_input.assert_called_once()

    def test_no_role_or_ambiguous_candidate_does_not_write(self):
        self.choose(role=None);self.stale=True
        self.assertEqual(self.result(self.c.fill_with_receipt,'禁止')[0],'error')
        self.backend.resolve_main_input.assert_not_called()
        self.c._chosen_input_role=('id','prompt-textarea')
        self.backend.resolve_main_input.side_effect=InputLocationError('主框不唯一')
        self.assertEqual(self.result(self.c.fill_with_receipt,'禁止')[0],'error')
        self.assertFalse(self.backend.writes)

    def test_changed_process_or_cancel_during_resolution_cannot_write(self):
        self.choose();self.stale=True
        self.backend.validate_window=lambda target:replace(target,process_started='other-process')
        self.assertEqual(self.result(self.c.fill_with_receipt,'禁止')[0],'error')
        self.backend.resolve_main_input.assert_not_called()
        self.backend.validate_window=lambda target:self.new
        self.backend.resolve_main_input.side_effect=lambda *args:(self.c.clear(),(2,1))[1]
        self.assertEqual(self.result(self.c.fill_with_receipt,'禁止')[0],'error')
        self.assertFalse(self.backend.writes)

    def test_old_send_and_monitor_never_recover(self):
        self.choose()
        state,receipt=self.result(self.c.fill_with_receipt,'旧内容')
        self.assertEqual(state,'ok',receipt)
        self.stale=True
        self.c._monitor()
        self.assertEqual(self.result(self.c.send_filled,receipt.send_token)[0],'error')
        self.backend.resolve_main_input.assert_not_called()
        self.assertFalse(self.backend.sends)
        self.assertEqual(self.backend.fields[2],['旧内容',''])

    def test_rebuilt_control_does_not_inherit_manual_send_override(self):
        self.choose();self.stale=True
        self.backend.input_supports_enter=lambda *_:False
        state,receipt=self.result(self.c.fill_with_receipt,'仅填入')
        self.assertEqual(state,'ok',receipt)
        self.assertFalse(receipt.send_token)
        self.assertFalse(self.c.selected_input.enter_send)

    def test_title_change_and_rebuilt_control_keep_new_window_snapshot(self):
        self.choose();self.stale=True
        self.new=replace(self.new,title='新对话')
        self.backend.windows[1]=self.new
        self.backend.validate_window=lambda _:self.new
        state,receipt=self.result(self.c.fill_with_receipt,'新对话内容')
        self.assertEqual(state,'ok',receipt)
        self.assertEqual(receipt.title,'新对话')
        self.assertEqual(self.backend.writes,[(2,1)])

    def test_unknown_process_identity_requires_manual_reselection(self):
        self.choose();self.stale=True
        self.new=replace(self.new,process_started=None)
        self.c._chosen_input=(self.new,*self.c._chosen_input[1:])
        self.backend.validate_window=lambda _:self.new
        self.assertEqual(self.result(self.c.fill_with_receipt,'禁止')[0],'error')
        self.backend.resolve_main_input.assert_not_called()
        self.assertFalse(self.backend.writes)


class NativeRecoveryTests(unittest.TestCase):
    def test_windows_role_resolution_uses_handles_and_rejects_ambiguity(self):
        from talkman_target_windows import WindowsTargetBackend
        b=WindowsTargetBackend();target=TargetWindow(1,10,'Codex','聊天','codex.exe','start')
        field=SimpleNamespace(CurrentAutomationId='prompt-textarea',CurrentName='')
        handle=(1,10,'codex.exe','聊天',field,(1,2))
        b.list_inputs=Mock(return_value=[(handle,(10,20,200,80))])
        b.inspect_input=Mock(return_value=(10,20,200,80))
        role=b.input_position_role(target,handle)
        self.assertEqual(role,('id','prompt-textarea'))
        self.assertIs(b.resolve_main_input(target,role),handle)
        b.list_inputs.return_value=[(handle,(10,20,200,80)),(handle,(10,110,200,80))]
        with self.assertRaises(InputLocationError):b.resolve_main_input(target,role)

    def test_mac_role_recovery_requires_unique_visible_main(self):
        from test_talkman_target_macos import FakeAPI
        from talkman_target_macos import MacTargetBackend
        api=FakeAPI();app,root=api.add_app();window=api.add_window(root)
        first=api.add_input(window)
        first.attributes['AXIdentifier']='prompt-textarea'
        api.input_rect=lambda _:(10,50,300,80)
        b=MacTargetBackend(api=api);target=b.list_windows()[0]
        handle=b.list_inputs(target)[0][0]
        role=b.input_position_role(target,handle)
        first.attributes['AXParent']=None
        window.attributes['AXChildren']=[]
        second=api.add_input(window);second.attributes['AXIdentifier']='prompt-textarea'
        self.assertIs(b.resolve_main_input(target,role)[2],second)
        third=api.add_input(window);third.attributes['AXIdentifier']='prompt-textarea'
        with self.assertRaises(InputLocationError):b.resolve_main_input(target,role)

    def test_generic_editor_label_never_becomes_main_role(self):
        t=TargetWindow(1,10,'Codex','Chat','codex.exe','started')
        self.assertEqual(main_input_role(t,'',['Work with Codex']),('label','work with codex'))
        self.assertIsNone(main_input_role(t,'editor',['输入','代码编辑器']))
        self.assertIsNone(main_input_role(replace(t,app='Notepad',identity='notepad.exe'),'prompt-textarea',[]))

    def test_windows_reused_process_id_is_rejected(self):
        from talkman_target_windows import WindowsTargetBackend,TargetWindow as Native
        def pid(_hwnd,pointer):
            pointer._obj.value=42
        def title(_hwnd,buf,_length):buf.value='标题'
        user=SimpleNamespace(IsWindow=lambda _:True,GetWindowThreadProcessId=pid,
            GetWindowTextLengthW=lambda _:2,GetWindowTextW=title)
        t=Native(100,42,'Codex','标题','codex.exe','old')
        with patch('talkman_target_windows._win_api',return_value=user), \
             patch('talkman_target_windows._process_path',return_value='codex.exe'), \
             patch('talkman_target_windows._process_started',return_value='new'):
            self.assertIsNone(WindowsTargetBackend().validate_window(t))

    def test_windows_creation_time_read_closes_handle(self):
        from talkman_target_windows import _process_started
        def times(_handle,created,*rest):
            created._obj.dwLowDateTime=9;created._obj.dwHighDateTime=1
            return True
        kernel=SimpleNamespace(OpenProcess=Mock(return_value=88),GetProcessTimes=Mock(side_effect=times),CloseHandle=Mock())
        with patch('talkman_target_windows.os.name','nt'),patch('talkman_target_windows._kernel_api',return_value=kernel):
            self.assertEqual(_process_started(42),str((1<<32)|9))
        kernel.CloseHandle.assert_called_once_with(88)


if __name__=='__main__':unittest.main()
