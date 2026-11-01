"""向鼠标下方的外部窗口投递原生滚轮；不激活窗口、不注入文字。"""
from __future__ import annotations

import ctypes as C
import os
import queue
import sys
import threading
import time


SCROLL_ACTIONS = {'scroll_up': (0, -1), 'scroll_down': (0, 1),
                  'scroll_left': (-1, 0), 'scroll_right': (1, 0)}


def native_scroll(dx, dy):
    if sys.platform == 'win32':
        from ctypes import wintypes as W
        user = C.WinDLL('user32', use_last_error=True)
        user.WindowFromPoint.argtypes = [W.POINT]
        user.WindowFromPoint.restype = W.HWND
        user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        user.PostMessageW.restype = W.BOOL
        point = W.POINT()
        if not user.GetCursorPos(C.byref(point)):
            raise RuntimeError('无法读取鼠标位置')
        hwnd = user.WindowFromPoint(point)
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        # 模拟器上再次产生 MouseWheel 会形成递归。外部窗口才接受设备滚动。
        if not hwnd or pid.value == os.getpid():
            raise RuntimeError('请把鼠标移到要滚动的外部窗口')
        delta = (dx if dx else -dy) * 120
        position = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
        if not user.PostMessageW(hwnd, 0x020E if dx else 0x020A,
                                 (delta & 0xffff) << 16, position):
            raise RuntimeError('目标窗口无法接收滚轮，请检查权限')
    elif sys.platform == 'darwin':
        import Quartz as q
        import ApplicationServices as ax
        from Foundation import NSAutoreleasePool
        pool = NSAutoreleasePool.alloc().init()
        try:
            if not ax.AXIsProcessTrusted():
                raise RuntimeError('请在系统设置中允许 TalkMan 使用辅助功能')
            point = q.CGEventGetLocation(q.CGEventCreate(None))
            windows = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements, 0)
            pid = None
            for window in windows or []:
                bounds = window.get(q.kCGWindowBounds, {})
                if (bounds.get('X', 0) <= point.x < bounds.get('X', 0)+bounds.get('Width', 0)
                        and bounds.get('Y', 0) <= point.y < bounds.get('Y', 0)+bounds.get('Height', 0)):
                    pid = window.get(q.kCGWindowOwnerPID)
                    break
            if not pid or pid == os.getpid():
                raise RuntimeError('请把鼠标移到要滚动的外部窗口')
            event = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitLine, 2, -dy * 3, -dx * 3)
            q.CGEventSetLocation(event, point)
            q.CGEventSetFlags(event, 0)
            q.CGEventPostToPid(pid, event)
        finally:
            del pool
    else:
        raise RuntimeError('当前系统尚不支持设备滚轮')


class ScrollWorker:
    """独立工作线程，限制队列长度；断连／暂停会废弃未执行的滚动。"""
    def __init__(self, backend=native_scroll, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.queue = queue.Queue(maxsize=16)
        self.errors = queue.Queue()
        self.epoch = 0
        self.closed = False
        self.thread = None

    def submit(self, action):
        if self.closed or action not in SCROLL_ACTIONS:
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='TalkMan-scroll', daemon=True)
            self.thread.start()
        try:
            self.queue.put_nowait((self.epoch, self.clock(), SCROLL_ACTIONS[action]))
        except queue.Full:
            pass

    def cancel(self):
        self.epoch += 1
        while True:
            try: self.queue.get_nowait()
            except queue.Empty: break

    def close(self):
        self.closed = True
        self.cancel()

    def _run(self):
        while not self.closed:
            try: epoch, created, delta = self.queue.get(timeout=.1)
            except queue.Empty: continue
            if epoch != self.epoch or self.clock()-created > .3:
                continue
            try: self.backend(*delta)
            except Exception as error:
                if self.errors.empty(): self.errors.put(str(error))
