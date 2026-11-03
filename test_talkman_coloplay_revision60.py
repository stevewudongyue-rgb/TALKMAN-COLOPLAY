"""坐标源头与绿框端到端校验；原生测试只操作专门创建的 EDIT 窗口。"""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import talkman_target_windows as backend


class CoordinateTests(unittest.TestCase):
    def test_read_enters_physical_context_and_restores_original(self):
        switch = Mock(side_effect=[1234, 1])
        rect = SimpleNamespace(left=-420, top=80, right=-100, bottom=160)
        class Field:
            @property
            def CurrentBoundingRectangle(self):
                self_context.assertEqual(switch.call_count, 1)
                self_context.assertEqual(switch.call_args.args[0].value, C.c_void_p(-4).value)
                return rect
        self_context = self
        with patch.object(backend.os, 'name', 'nt'), patch.object(
                backend.ctypes, 'WinDLL', return_value=SimpleNamespace(SetThreadDpiAwarenessContext=switch), create=True):
            self.assertEqual(backend.WindowsTargetBackend._element_rect(Field()), (-420,80,320,80))
        switch.assert_called_with(1234)
        self.assertIs(switch.restype, C.c_void_p)

    def test_failed_dpi_context_does_not_read_unverified_coordinates(self):
        switch = Mock(return_value=0)
        field = Mock()
        with patch.object(backend.os, 'name', 'nt'), patch.object(
                backend.ctypes, 'WinDLL', return_value=SimpleNamespace(SetThreadDpiAwarenessContext=switch), create=True):
            with self.assertRaisesRegex(backend.TargetError, '屏幕缩放'):
                backend.WindowsTargetBackend._element_rect(field)
        self.assertEqual(switch.call_count, 1)
        self.assertFalse(field.mock_calls)

    def test_unreadable_control_still_restores_original_context(self):
        switch = Mock(side_effect=[5678, 1])
        class Field:
            @property
            def CurrentBoundingRectangle(self):
                raise OSError('control gone')
        with patch.object(backend.os, 'name', 'nt'), patch.object(
                backend.ctypes, 'WinDLL', return_value=SimpleNamespace(SetThreadDpiAwarenessContext=switch), create=True):
            with self.assertRaises(OSError):
                backend.WindowsTargetBackend._element_rect(Field())
        switch.assert_called_with(5678)


def native_fixture(path):
    user = C.WinDLL('user32')
    user.SetThreadDpiAwarenessContext.argtypes = [C.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = C.c_void_p
    user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
    create = user.CreateWindowExW
    create.argtypes = [W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,
                       W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID]
    create.restype = W.HWND
    # 无激活测试窗口，测试结束只销毁该子进程；不读取用户 Agent 正文。
    root = create(0x08000000,'STATIC','TalkMan 坐标隔离验证',0x10CF0000,80,80,480,320,None,None,None,None)
    fields = [create(0x200,'EDIT','',0x50010004,20,40+i*100,420,70,root,100+i,None,None)
              for i in range(2)]
    assert root and all(fields)
    Path(path).write_text(json.dumps({'root':root,'fields':fields}),encoding='utf-8')
    user.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]
    user.DispatchMessageW.argtypes=[C.POINTER(W.MSG)];user.DispatchMessageW.restype=C.c_ssize_t
    msg=W.MSG()
    while user.GetMessageW(C.byref(msg),None,0,0)>0:
        user.TranslateMessage(C.byref(msg));user.DispatchMessageW(C.byref(msg))


@unittest.skipUnless(sys.platform == 'win32', '需要 Windows 原生 UIA 与多屏坐标')
class NativeCoordinateTests(unittest.TestCase):
    def test_real_edit_to_outline_all_monitors_and_caller_dpi_modes(self):
        from talkman_coloplay_highlight import WindowsOutline
        from talkman_target import TargetWindow
        u=C.WinDLL('user32')
        u.SetThreadDpiAwarenessContext.argtypes=[C.c_void_p];u.SetThreadDpiAwarenessContext.restype=C.c_void_p
        u.GetThreadDpiAwarenessContext.restype=C.c_void_p
        u.AreDpiAwarenessContextsEqual.argtypes=[C.c_void_p,C.c_void_p];u.AreDpiAwarenessContextsEqual.restype=W.BOOL
        u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.GetWindowRect.restype=W.BOOL
        u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT];u.SetWindowPos.restype=W.BOOL
        monitor_callback=C.WINFUNCTYPE(W.BOOL,W.HANDLE,W.HDC,C.POINTER(W.RECT),W.LPARAM)
        u.EnumDisplayMonitors.argtypes=[W.HDC,C.POINTER(W.RECT),monitor_callback,W.LPARAM]
        monitors=[]
        @monitor_callback
        def monitor(handle, dc, rect, data):
            monitors.append((rect.contents.left,rect.contents.top));return True
        with backend._physical_screen_coordinates():
            self.assertTrue(u.EnumDisplayMonitors(None,None,monitor,0))
        with tempfile.TemporaryDirectory(prefix='TalkMan-coordinate-test-') as folder:
            state=Path(folder)/'window.json'
            process=subprocess.Popen([sys.executable,__file__,'--fixture',str(state)],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            b=backend.WindowsTargetBackend();outline=None
            try:
                deadline=time.monotonic()+10
                while not state.exists() and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue(state.exists())
                data=json.loads(state.read_text('utf-8'))
                target=backend.TargetWindow(data['root'],process.pid,'测试工具','TalkMan 坐标隔离验证',backend._process_path(process.pid))
                view=TargetWindow(target.id,target.pid,target.app,target.title,target.identity)
                outline=WindowsOutline()
                with patch.object(outline.u,'GetForegroundWindow',return_value=target.id):
                    for mx,my in monitors:
                        with backend._physical_screen_coordinates():
                            self.assertTrue(u.SetWindowPos(target.id,None,mx+60,my+80,480,320,0x0014))
                        time.sleep(.08)
                        for mode in (-1,-2,-4):
                            old=u.SetThreadDpiAwarenessContext(C.c_void_p(mode))
                            try:
                                entries=b.list_inputs(target)
                                self.assertEqual(len(entries),2)
                                for number,(handle,rect) in enumerate(entries,1):
                                    self.assertEqual(b.inspect_input(target,handle),rect)
                                    self.assertTrue(u.AreDpiAwarenessContextsEqual(u.GetThreadDpiAwarenessContext(),C.c_void_p(mode)))
                                    expected=W.RECT()
                                    with backend._physical_screen_coordinates():
                                        self.assertTrue(u.GetWindowRect(data['fields'][number-1],C.byref(expected)))
                                    self.assertEqual(rect,(expected.left,expected.top,expected.right-expected.left,expected.bottom-expected.top))
                                    self.assertTrue(outline.draw_region(view,rect,number))
                                    x,y,w,h=rect
                                    boxes=[(x,y,w,3),(x,y+h-3,w,3),(x,y,3,h),(x+w-3,y,3,h),(x,y,32,26)]
                                    with backend._physical_screen_coordinates():
                                        for hwnd,box in zip([*outline.windows,outline.badge],boxes):
                                            actual=W.RECT();self.assertTrue(u.GetWindowRect(hwnd,C.byref(actual)))
                                            self.assertEqual((actual.left,actual.top,actual.right-actual.left,actual.bottom-actual.top),box)
                                    self.assertTrue(u.AreDpiAwarenessContextsEqual(u.GetThreadDpiAwarenessContext(),C.c_void_p(mode)))
                            finally:u.SetThreadDpiAwarenessContext(old)
                print(f'原生坐标：{len(monitors)} 个显示器 × 3 种调用线程缩放 × 2 个 EDIT，读取与五个高亮窗口一致。',flush=True)
            finally:
                if outline:outline.close()
                b.close()
                process.terminate();process.wait(timeout=10)


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--fixture':native_fixture(sys.argv[2])
    else:unittest.main()
