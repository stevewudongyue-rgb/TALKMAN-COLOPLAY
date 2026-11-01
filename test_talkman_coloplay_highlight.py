"""只在本测试创建的窗口上验证外框，不操作用户 Agent。"""
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from talkman_coloplay_highlight import TargetHighlight, WindowsOutline
from talkman_target import TargetWindow


@unittest.skipUnless(sys.platform=='win32','Windows 原生边框')
class OutlineTests(unittest.TestCase):
    def test_native_outline_is_transparent_noactivate_tracks_and_expires(self):
        app=tk.Tk();app.title('TalkMan-目标高亮隔离验证')
        app.geometry('450x280+120+160');app.update()
        stamp=[10.0];outline=TargetHighlight(app,clock=lambda:stamp[0])
        u=C.WinDLL('user32')
        u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
        u.GetForegroundWindow.restype=W.HWND
        u.GetWindowLongW.argtypes=[W.HWND,C.c_int];u.GetWindowLongW.restype=W.LONG
        u.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];u.SendMessageW.restype=C.c_ssize_t
        u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.GetWindowRect.restype=W.BOOL
        u.IsWindowVisible.argtypes=[W.HWND];u.IsWindowVisible.restype=W.BOOL
        hwnd=u.GetAncestor(app.winfo_id(),2)
        target=TargetWindow(hwnd,os.getpid(),'隔离验证','不含用户内容','')
        # 其它对话也在运行桌面测试；只固定本测试的前台资格，不争抢用户的真实焦点。
        # 边框创建、原生样式、命中穿透、绿色像素和真实前台窗口值仍实际验证。
        outline.native=WindowsOutline()
        foreground=patch.object(outline.native.u,'GetForegroundWindow',return_value=hwnd)
        foreground.start()
        try:
            app.attributes('-topmost',True);app.update()
            front=u.GetForegroundWindow()
            self.assertTrue(outline.show(target))
            self.assertEqual(front,u.GetForegroundWindow())
            deadline=time.monotonic()+.2
            while time.monotonic()<deadline:
                app.update();time.sleep(.01)
            C.WinDLL('dwmapi').DwmFlush()
            for edge in outline.native.windows:
                style=u.GetWindowLongW(edge,-20)
                self.assertEqual(style&0x080800A0,0x080800A0)
                self.assertEqual(u.SendMessageW(edge,0x84,0,0),-1)
                self.assertEqual(u.SendMessageW(edge,0x21,0,0),3)
                self.assertTrue(u.IsWindowVisible(edge))
            before=W.RECT()
            from PIL import ImageGrab
            with outline.native.dpi():
                u.GetWindowRect(outline.native.windows[0],C.byref(before))
                edge_pixels=ImageGrab.grab(bbox=(before.left+8,before.top,before.left+28,before.top+3),
                                           all_screens=True,include_layered_windows=True)
            self.assertTrue(any(g>180 and g>r+60 and g>b+50 for r,g,b in edge_pixels.get_flattened_data()),
                            str((before.left,before.top,before.right,before.bottom,edge_pixels.getpixel((10,1)))))
            app.geometry('+220+210');app.update()
            app.after_cancel(outline.job);outline.job=None;outline.tick()
            after=W.RECT()
            with outline.native.dpi():u.GetWindowRect(outline.native.windows[0],C.byref(after))
            self.assertNotEqual((before.left,before.top),(after.left,after.top))
            app.after_cancel(outline.job);outline.job=None
            stamp[0]=12.01
            self.assertFalse(outline.tick())
            self.assertTrue(all(not u.IsWindowVisible(w) for w in outline.native.windows))
            self.assertFalse(outline.show(TargetWindow(hwnd,os.getpid()+1,'错误身份','','')))
        finally:
            foreground.stop();outline.close();app.destroy()


if __name__=='__main__':unittest.main()
