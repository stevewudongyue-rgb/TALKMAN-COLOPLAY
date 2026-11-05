"""位置记忆与默认主框的离线回归；不访问真实 Agent。"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from talkman_target import TargetController, TargetWindow, InputLocationError
from test_talkman_target import wait_result


class Backend:
    def __init__(self):
        self.a = TargetWindow(1, 10, 'Codex', '对话 A', 'codex.exe', 123.0)
        self.b = TargetWindow(2, 20, '豆包', '对话 B', 'doubao.exe', 456.0)
        self.windows = [self.a, self.b]
        self.bound = None
        self.dead = set()
        self.writes = []
        self.offset = 0

    def list_windows(self): return self.windows
    def validate_window(self, target):
        return next((t for t in self.windows if t.session_key() == target.session_key()
                     and t.process_started == target.process_started), None)
    def activate_window(self, target): pass
    def list_inputs(self, target):
        return [((target.id, target.title, i), (20, 50+i*100, 200, 80)) for i in range(2)]
    def inspect_input(self, target, handle):
        if handle in self.dead or handle[:2] != (target.id, target.title):
            raise InputLocationError('原位置失效')
        return (20+self.offset, 50+handle[2]*100, 200, 80)
    def bind_input(self, target, handle):
        if handle: self.inspect_input(target, handle)
        self.bound = handle
    def input_position_role(self, target, handle):
        return ('id', 'prompt-textarea') if handle[2] == 0 else None
    def resolve_main_input(self, target, role=None):
        return self.list_inputs(target)[0][0]
    def input_supports_enter(self, target, handle): return handle[2] == 0
    def capture_focus(self, target): return None
    def fill(self, target, text, token):
        self.writes.append(self.bound or (target.id, target.title, 0))
        return '已填入'


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TalkMan-67-')
        self.addCleanup(self.temp.cleanup)
        self.b = Backend()
        self.c = TargetController(Path(self.temp.name), backend=self.b)
        self.addCleanup(self.c.close)
        self.c.select(self.b.a)

    def result(self, method, *args, **kwargs):
        results = []
        method(*args, lambda *r: results.append(r), **kwargs)
        return wait_result(results)

    def choose(self, target, index):
        state, value = self.result(self.c.begin_inputs, target)
        self.assertEqual(state, 'ok', value)
        self.assertEqual(self.result(self.c.inspect_input, value['ticket'], index,
                                     confirm=True)[0], 'ok')

    def test_switch_back_restores_special_box_without_selection_or_old_send(self):
        self.choose(self.b.a, 1)
        self.c._pending_send = object()
        self.c.select(self.b.b)
        self.assertIsNone(self.c._pending_send)
        self.c.select(self.b.a)
        self.assertFalse(self.c.input_selecting)
        self.assertEqual(self.c.selected_input.number, 2)
        self.assertEqual(self.result(self.c.fill, '正文')[0], 'ok')
        self.assertEqual(self.b.writes, [(1, '对话 A', 1)])

    def test_new_conversation_uses_main_then_return_restores_special(self):
        self.choose(self.b.a, 1)
        new = replace(self.b.a, title='新对话')
        self.b.windows[0] = new
        self.assertEqual(self.result(self.c.fill_with_receipt, '新对话')[0], 'ok')
        self.assertEqual(self.b.writes[-1], (1, '新对话', 0))
        self.b.windows[0] = self.b.a
        self.assertEqual(self.result(self.c.fill_with_receipt, '返回')[0], 'ok')
        self.assertEqual(self.b.writes[-1], (1, '对话 A', 1))

    def test_window_motion_does_not_require_selection(self):
        self.choose(self.b.a, 1)
        self.b.offset = -1400
        self.assertEqual(self.result(self.c.fill, '移动后')[0], 'ok')
        self.assertFalse(self.c.input_selecting)
        self.assertEqual(self.b.writes[-1], (1, '对话 A', 1))

    def test_dead_special_box_never_falls_back_to_main(self):
        self.choose(self.b.a, 1)
        self.b.dead.add((1, '对话 A', 1))
        self.c.select(self.b.b); self.c.select(self.b.a)
        state, error = self.result(self.c.fill, '不得误填')
        self.assertEqual(state, 'error')
        self.assertIsInstance(error, InputLocationError)
        self.assertFalse(self.b.writes)

    def test_restart_special_box_requests_relocation_without_write(self):
        self.choose(self.b.a, 1)
        self.c.close()
        c = TargetController(Path(self.temp.name), backend=self.b)
        self.addCleanup(c.close)
        state, error = self.result(c.fill, '不得误填')
        self.assertEqual(state, 'error')
        self.assertIsInstance(error, InputLocationError)
        self.assertFalse(self.b.writes)

    def test_reused_window_id_does_not_restore_old_native_handle(self):
        self.choose(self.b.a, 1)
        new = replace(self.b.a, process_started=999.0)
        self.b.windows[0] = new
        self.c.select(new)
        self.assertIsNone(self.c.selected_input)
        self.assertEqual(self.result(self.c.fill, '新会话')[0], 'ok')
        self.assertEqual(self.b.writes[-1], (1, '对话 A', 0))

    def test_ambiguous_main_in_new_conversation_requires_relocation(self):
        self.choose(self.b.a, 1)
        self.b.windows[0] = replace(self.b.a, title='新对话')
        def ambiguous(*args): raise InputLocationError('无法唯一确认主框')
        self.b.resolve_main_input = ambiguous
        state, error = self.result(self.c.fill_with_receipt, '不得误填')
        self.assertEqual(state, 'error')
        self.assertIsInstance(error, InputLocationError)
        self.assertFalse(self.b.writes)


class MainPreferenceTests(unittest.TestCase):
    def test_mac_temporary_focused_answer_does_not_replace_known_main(self):
        from test_talkman_target_macos import FakeAPI
        from talkman_target_macos import MacTargetBackend
        api = FakeAPI()
        app, root = api.add_app()
        window = api.add_window(root)
        main = api.add_input(window, label='Message ChatGPT', count=0)
        answer = api.add_input(window, label='补充问题', count=0)
        api.focus(app['pid'], window, answer)
        backend = MacTargetBackend(api=api)
        target = backend.list_windows()[0]
        token = backend.capture_focus(target)
        backend.fill(target, '默认正文', token)
        self.assertIn(('set', main, 'AXFocused', True), api.actions)
        self.assertIs(root.attributes['AXFocusedUIElement'], main)
        self.assertEqual(sum(a[0] == 'paste' for a in api.actions), 1)

    def test_windows_known_main_beats_generic_editor_score(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from talkman_target_windows import WindowsTargetBackend, TargetWindow as NativeWindow
        backend = WindowsTargetBackend()
        root = Mock()
        main = SimpleNamespace(CurrentAutomationId='prompt-textarea', CurrentName='', CurrentClassName='')
        answer = SimpleNamespace(CurrentAutomationId='editor', CurrentName='chat message input', CurrentClassName='')
        root.FindAll.side_effect = [[main, answer], [], []]
        backend._uia = SimpleNamespace(CreatePropertyCondition=lambda *_: None)
        backend._window_element = lambda _: root
        backend._is_editable = lambda _: True
        window = NativeWindow(1, 10, 'ChatGPT', '对话', 'chatgpt.exe', 123.0)
        self.assertIs(backend._find_editable(window), main)


if __name__ == '__main__': unittest.main()
