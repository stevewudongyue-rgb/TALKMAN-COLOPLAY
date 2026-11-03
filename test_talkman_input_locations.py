"""两阶段目标选择；隔离窗口、音频和目标，不访问用户 Agent。"""
import json
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from talkman_input_locations import InputRegion, navigation_instruction
from talkman_target import TargetController, TargetWindow, TargetError, TARGET_FILE
from test_talkman_target import wait_result


class InputBackend:
    def __init__(self):
        self.windows = [TargetWindow(1, 10, 'Codex', '旧窗口', 'codex.exe'),
                        TargetWindow(2, 20, '豆包', '新窗口', 'doubao.exe')]
        self.fields = {1: ['', ''], 2: ['', '']}
        self.bound = None
        self.valid = True
        self.activated = []
        self.writes = []
        self.reads = []
        self.sends = []

    def list_windows(self): return list(self.windows)
    def validate_window(self, target): return target if target in self.windows else None
    def activate_window(self, target): self.activated.append(target)
    def list_inputs(self, target):
        return [((target.id, i), (30, 60+i*90, 240, 80)) for i in range(len(self.fields[target.id]))]
    def inspect_input(self, target, handle):
        if not self.valid or target not in self.windows or handle[0] != target.id or handle[1] >= len(self.fields[target.id]):
            raise TargetError('输入位置已失效，请重新选择。')
        return (30, 60+handle[1]*90, 240, 80)
    def bind_input(self, target, handle):
        self.bound = handle
        if handle: self.inspect_input(target, handle)
    def capture_focus(self, target): return None
    def open_delivery(self, target): return self.bound or (target.id, 0)
    def read_delivery(self, target, handle):
        self.inspect_input(target, handle)
        self.reads.append(handle)
        return self.fields[handle[0]][handle[1]]
    def fill(self, target, text, token):
        handle = self.bound or (target.id, 0)
        self.inspect_input(target, handle)
        self.fields[handle[0]][handle[1]] += text
        self.writes.append(handle)
        return '已填入'
    def send_delivery(self, *args): self.sends.append(args)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TalkMan-input-test-')
        self.addCleanup(self.temp.cleanup)
        self.backend = InputBackend()
        self.c = TargetController(Path(self.temp.name), backend=self.backend)
        self.addCleanup(self.c.close)
        self.old, self.new = self.backend.windows
        self.c.select(self.old)

    def result(self, method, *args, **kwargs):
        results = []
        method(*args, lambda *r: results.append(r), **kwargs)
        return wait_result(results)

    def begin(self):
        state, value = self.result(self.c.begin_inputs, self.new)
        self.assertEqual(state, 'ok', value)
        return value['ticket']

    def confirm(self, index=1):
        ticket = self.begin()
        return self.result(self.c.inspect_input, ticket, index, confirm=True)

    def test_second_region_fill_and_readback_never_use_main(self):
        ticket = self.begin()
        self.assertEqual(self.c.selected, self.old)
        state, _ = self.result(self.c.fill_with_receipt, '禁止提前填入')
        self.assertEqual(state, 'error'); self.assertFalse(self.backend.writes)
        self.assertEqual(self.result(self.c.inspect_input, ticket, 1, confirm=True)[0], 'ok')
        self.assertEqual(self.c.selected, self.new)
        state, value = self.result(self.c.fill_with_receipt, '第二框内容')
        self.assertEqual(state, 'ok', value)
        self.assertEqual(self.backend.fields[2], ['', '第二框内容'])
        self.assertTrue(self.backend.reads)
        self.assertEqual(set(self.backend.reads), {(2,1)})
        self.assertFalse(value.send_token)
        self.assertIn('提交方式尚未验证', str(value))
        self.assertEqual(self.result(self.c.send_current, self.new.session_key())[0], 'error')
        self.assertEqual(self.result(self.c.sync, '不会同步')[0], 'error')
        self.assertFalse(self.backend.sends)

    def test_single_region_still_requires_confirm_and_cancel_preserves_old(self):
        self.backend.fields[2] = ['']
        ticket = self.begin()
        self.assertEqual(self.c.selected, self.old)
        self.c.cancel_inputs()
        self.assertEqual(self.result(self.c.inspect_input, ticket, 0, confirm=True)[0], 'error')
        self.assertEqual(self.c.selected, self.old)

    def test_cancel_preserves_previous_region_and_rebinds_before_fill(self):
        self.confirm()
        results = []
        self.c.begin_inputs(self.old, lambda *r: results.append(r)); wait_result(results)
        self.c.cancel_inputs()
        self.assertEqual(self.result(self.c.fill_with_receipt, '仍到第二框')[0], 'ok')
        self.assertEqual(self.backend.writes, [(2,1)])

    def test_missing_region_or_window_does_not_fall_back(self):
        self.confirm()
        self.backend.valid = False
        self.assertEqual(self.result(self.c.fill_with_receipt, '不得误填')[0], 'error')
        self.assertFalse(self.backend.writes)
        self.backend.windows = [self.old]
        self.result(self.c.refresh)
        self.assertEqual(self.c.selected, self.new)
        self.assertEqual(self.result(self.c.fill_with_receipt, '不得误填')[0], 'error')

    def test_cancel_late_scan_and_stale_confirmation(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.list_inputs
        def slow(target):
            entered.set(); release.wait(3)
            return original(target)
        self.backend.list_inputs = slow
        late = []
        self.c.begin_inputs(self.new, lambda *r: late.append(r))
        self.assertTrue(entered.wait(1)); self.c.cancel_inputs(); release.set()
        self.result(self.c.refresh)
        self.assertFalse(late); self.assertEqual(self.c.selected, self.old)

    def test_save_failure_reports_success_with_warning_and_restart_requires_selection(self):
        ticket = self.begin()
        with patch.object(self.c, 'save', side_effect=OSError()):
            state, value = self.result(self.c.inspect_input, ticket, 0, confirm=True)
        self.assertEqual(state, 'ok'); self.assertIn('未能保存', value['warning'])
        self.c.save()
        data = json.loads(Path(self.temp.name, TARGET_FILE).read_text('utf-8'))
        self.assertEqual(data['target']['input_required'], '1')
        self.assertNotIn('rect', str(data)); self.assertNotIn('ticket', str(data))
        self.c.close()
        restored = TargetController(Path(self.temp.name), backend=self.backend)
        try:
            self.assertEqual(self.result(restored.fill_with_receipt, '不得恢复默认框')[0], 'error')
        finally:
            restored.close()

    def test_invalid_rect_and_excess_candidates_are_rejected(self):
        for entries in ([('x', (0,0,0,80))], [('x', (0,0,200,80))]*33):
            self.backend.list_inputs = lambda _t, entries=entries: entries
            self.assertEqual(self.result(self.c.begin_inputs, self.new)[0], 'error')
            self.c.cancel_inputs()


class MacBindingTests(unittest.TestCase):
    def test_empty_tree_uses_existing_recovery_once_then_lists_all_regions(self):
        from test_talkman_target_macos import FakeAPI
        from talkman_target_macos import MacTargetBackend
        api=FakeAPI();app,root=api.add_app();window=api.add_window(root)
        api.input_rect=lambda e:(10,50,300,80)
        b=MacTargetBackend(api=api);target=b.list_windows()[0]
        def recover(*args, **kwargs):
            api.add_input(window,label='第一个问题');api.add_input(window,label='第二个问题')
        b._recover_input_tree=Mock(side_effect=recover)
        self.assertEqual(len(b.list_inputs(target)),2)
        b._recover_input_tree.assert_called_once()
        window.attributes['AXChildren']=[]
        b._recover_input_tree=Mock()
        self.assertEqual(b.list_inputs(target),[])
        b._recover_input_tree.assert_called_once()

    def test_two_controls_focus_fill_read_and_removal(self):
        from test_talkman_target_macos import FakeAPI
        from talkman_target_macos import MacTargetBackend, FocusToken
        api = FakeAPI(); app, root = api.add_app(); window = api.add_window(root)
        first = api.add_input(window, count=0); second = api.add_input(window, label='补充问题', count=0)
        first.attributes['AXValue'] = ''; second.attributes['AXValue'] = ''
        api.input_rect = lambda e: (10, 50 if e is first else 160, 300, 80)
        api.focus(app['pid'], window, first)
        b = MacTargetBackend(api=api); target = b.list_windows()[0]
        entries = b.list_inputs(target); self.assertEqual(len(entries), 2)
        b.bind_input(target, entries[1][0])
        old_focus = FocusToken(target.hwnd, target.pid, target.process_started, first)
        def paste():
            field = root.attributes['AXFocusedUIElement']
            field.attributes['AXValue'] = '内容'
            api.actions.append(('paste',))
        api.paste = paste
        b.fill(target, '内容', old_focus)
        self.assertEqual(first.attributes['AXValue'], '')
        self.assertEqual(second.attributes['AXValue'], '内容')
        handle = b.open_delivery(target)
        self.assertEqual(b.read_delivery(target, handle), '内容')
        second.attributes['AXParent'] = None
        before = list(api.actions)
        with self.assertRaises(TargetError): b.fill(target, '不应落入主框')
        self.assertEqual(before, api.actions)


class WindowsBindingTests(unittest.TestCase):
    def test_selected_control_overrides_main_and_old_focus_until_disappears(self):
        from talkman_target_windows import WindowsTargetBackend, TargetWindow as NativeWindow
        b = WindowsTargetBackend(); target = NativeWindow(100, 42, 'Agent', '窗口', 'agent.exe')
        root = Mock(); first = Mock(); second = Mock()
        for i, field in enumerate((first, second)):
            field.CurrentBoundingRectangle = SimpleNamespace(left=-600, top=50+i*100, right=-300, bottom=130+i*100)
            field.GetRuntimeId.return_value = (100,i)
            field.CurrentValue = ''; field.CurrentIsReadOnly = False
        root.FindAll.return_value = [first,second]
        b._uia = SimpleNamespace(CreatePropertyCondition=lambda *_:None, CompareElements=lambda a,c:a is c,
                                 GetFocusedElement=lambda:first)
        b._ensure_uia = lambda:b._uia
        b._window_element = lambda _:root
        b.validate_window = lambda _:target
        b._is_descendant_of = lambda e,r: e in (first,second)
        b._is_editable = lambda e:True
        b._pattern = lambda e,*_:e
        entries = b.list_inputs(target); self.assertEqual(len(entries),2)
        b.bind_input(target, entries[1][0])
        self.assertIs(b._find_editable(target),second)
        self.assertIsNone(b._find_editable(target,focused_only=True))
        b._uia.GetFocusedElement = lambda:second
        user = SimpleNamespace(IsIconic=lambda _:False, SetForegroundWindow=Mock(), GetForegroundWindow=lambda:100)
        b._send_key = Mock()
        b._paste_unicode = lambda text,**_:setattr(second, 'CurrentValue', text)
        with patch('talkman_target_windows._win_api', return_value=user), patch('talkman_target_windows.time.sleep'):
            b.fill(target, '第二框')
        self.assertEqual(first.CurrentValue,'')
        self.assertEqual(b.read_delivery(target,b.open_delivery(target)), '第二框')
        second.GetRuntimeId.return_value = (100,99)
        with self.assertRaises(TargetError):b._find_editable(target)


class NavigationTests(unittest.TestCase):
    def test_only_explicit_commands_are_navigation(self):
        self.assertEqual(navigation_instruction('选择第二个输入框。'), ('index',1))
        self.assertEqual(navigation_instruction('确认这个位置'), ('confirm',0))
        for phrase in ('把第二个输入框改为英文', '下一个', '取消修改', '选择第0个输入框'):
            self.assertIsNone(navigation_instruction(phrase))


@unittest.skipUnless(sys.platform == 'darwin', '需要 Mac 原生坐标桥接')
class MacNativeRectTests(unittest.TestCase):
    def test_native_axvalue_position_and_size_decode(self):
        import ApplicationServices as AX
        import Quartz
        from talkman_target_macos import _NativeAPI
        native = _NativeAPI.__new__(_NativeAPI)
        native.ax = AX
        values = {'AXPosition': AX.AXValueCreate(AX.kAXValueCGPointType, Quartz.CGPointMake(-450,70)),
                  'AXSize': AX.AXValueCreate(AX.kAXValueCGSizeType, Quartz.CGSizeMake(300,80))}
        native.get = lambda _element, name: values[name]
        self.assertEqual(native.input_rect(None), (-450,70,300,80))


if __name__ == '__main__': unittest.main()
