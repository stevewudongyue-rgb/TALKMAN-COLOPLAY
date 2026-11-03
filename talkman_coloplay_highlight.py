"""Windows 目标外框：短暂显示，鼠标穿透且不激活窗口；其它平台保留桌面状态提示。"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import sys
import time
from contextlib import contextmanager


class TargetHighlight:
    def __init__(self, app, clock=time.monotonic):
        self.app, self.clock = app, clock
        self.native = None
        self.target = None
        self.job = None
        self.until = 0

    def show(self, target):
        self.hide()
        if sys.platform != 'win32': return False
        try:
            if self.native is None: self.native = WindowsOutline()
            self.target = target
            self.until = self.clock()+2
            return self.tick()
        except (OSError, ValueError, TypeError):
            self.hide()
            return False

    def tick(self):
        self.job = None
        try:
            if self.clock() >= self.until or not self.native.draw(self.target):
                self.hide(); return False
        except (OSError, ValueError, TypeError):
            self.hide(); return False
        self.job = self.app.after(50, self.tick)
        return True

    def hide(self):
        if self.job is not None:
            self.app.after_cancel(self.job); self.job = None
        self.target = None
        if self.native: self.native.hide()

    def close(self):
        self.hide()
        if self.native: self.native.close(); self.native = None


class WindowsOutline:
    """四条原生窄窗口组成边框，不覆盖目标内容，不采集目标画面。"""
    def __init__(self):
        self.u=C.WinDLL('user32',use_last_error=True)
        self.g=C.WinDLL('gdi32',use_last_error=True)
        self.k=C.WinDLL('kernel32',use_last_error=True)
        self.d=C.WinDLL('dwmapi',use_last_error=True)
        self.callback_type=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class WindowClass(C.Structure):
            _fields_=[('style',W.UINT),('proc',self.callback_type),('extra',C.c_int),('window_extra',C.c_int),
                      ('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('brush',W.HBRUSH),
                      ('menu',W.LPCWSTR),('name',W.LPCWSTR)]
        class PaintStruct(C.Structure):
            _fields_=[('dc',W.HDC),('erase',W.BOOL),('rect',W.RECT),('restore',W.BOOL),
                      ('update',W.BOOL),('reserved',W.BYTE*32)]
        def signature(dll,name,args,result):
            f=getattr(dll,name);f.argtypes=args;f.restype=result;return f
        signature(self.u,'DefWindowProcW',[W.HWND,W.UINT,W.WPARAM,W.LPARAM],C.c_ssize_t)
        signature(self.u,'RegisterClassW',[C.POINTER(WindowClass)],W.ATOM)
        signature(self.u,'UnregisterClassW',[W.LPCWSTR,W.HINSTANCE],W.BOOL)
        signature(self.u,'CreateWindowExW',[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,
                                          W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p],W.HWND)
        signature(self.u,'SetWindowPos',[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL)
        signature(self.u,'SetLayeredWindowAttributes',[W.HWND,W.DWORD,W.BYTE,W.DWORD],W.BOOL)
        signature(self.u,'ShowWindow',[W.HWND,C.c_int],W.BOOL)
        signature(self.u,'DestroyWindow',[W.HWND],W.BOOL)
        signature(self.u,'GetWindowRect',[W.HWND,C.POINTER(W.RECT)],W.BOOL)
        signature(self.u,'GetDC',[W.HWND],W.HDC)
        signature(self.u,'ReleaseDC',[W.HWND,W.HDC],C.c_int)
        signature(self.u,'DrawTextW',[W.HDC,W.LPCWSTR,C.c_int,C.POINTER(W.RECT),W.UINT],C.c_int)
        signature(self.u,'BeginPaint',[W.HWND,C.POINTER(PaintStruct)],W.HDC)
        signature(self.u,'EndPaint',[W.HWND,C.POINTER(PaintStruct)],W.BOOL)
        signature(self.u,'InvalidateRect',[W.HWND,C.POINTER(W.RECT),W.BOOL],W.BOOL)
        signature(self.u,'UpdateWindow',[W.HWND],W.BOOL)
        signature(self.g,'SetTextColor',[W.HDC,W.DWORD],W.DWORD)
        signature(self.g,'SetBkColor',[W.HDC,W.DWORD],W.DWORD)
        signature(self.u,'GetWindowThreadProcessId',[W.HWND,C.POINTER(W.DWORD)],W.DWORD)
        signature(self.u,'GetForegroundWindow',[],W.HWND)
        signature(self.u,'IsWindowVisible',[W.HWND],W.BOOL)
        signature(self.u,'IsIconic',[W.HWND],W.BOOL)
        signature(self.u,'SetThreadDpiAwarenessContext',[C.c_void_p],C.c_void_p)
        signature(self.g,'CreateSolidBrush',[W.DWORD],W.HBRUSH)
        signature(self.g,'DeleteObject',[W.HANDLE],W.BOOL)
        signature(self.k,'GetModuleHandleW',[W.LPCWSTR],W.HMODULE)
        signature(self.d,'DwmGetWindowAttribute',[W.HWND,W.DWORD,C.c_void_p,W.DWORD],C.c_long)
        self.instance=self.k.GetModuleHandleW(None)
        self.name=f'TalkManTargetOutline-{os.getpid()}-{id(self)}'
        self.brush=self.g.CreateSolidBrush(0x8BFF5C)  # COLORREF：绿色 #5cff8b。
        @self.callback_type
        def proc(hwnd,message,wparam,lparam):
            if message==0x84: return -1  # HTTRANSPARENT
            if message==0x21: return 3   # MA_NOACTIVATE
            if message==0x0F and hwnd==getattr(self,'badge',None):
                paint=PaintStruct();dc=self.u.BeginPaint(hwnd,C.byref(paint))
                if dc:
                    self.g.SetTextColor(dc,0);self.g.SetBkColor(dc,0x8BFF5C)
                    area=W.RECT(0,0,32,26)
                    self.u.DrawTextW(dc,str(self.region_number),-1,C.byref(area),0x25)
                    self.u.EndPaint(hwnd,C.byref(paint))
                return 0
            return self.u.DefWindowProcW(hwnd,message,wparam,lparam)
        self.proc=proc
        cls=WindowClass(0,proc,0,0,self.instance,None,None,self.brush,None,self.name)
        self.atom=self.u.RegisterClassW(C.byref(cls))
        self.windows=[]
        self.badge = None
        if not self.atom:
            self.g.DeleteObject(self.brush)
            raise C.WinError(C.get_last_error())
        try:
            with self.dpi():
                for _ in range(4):
                    # 分层、工具窗、透明命中、无激活；不出现在任务栏和 Alt+Tab。
                    hwnd=self.u.CreateWindowExW(0x080800A0,self.name,'',0x80000000,0,0,1,1,None,None,self.instance,None)
                    if not hwnd: raise C.WinError(C.get_last_error())
                    self.windows.append(hwnd)
                    self.u.SetLayeredWindowAttributes(hwnd,0,235,2)
        except Exception:
            self.close();raise

    @contextmanager
    def dpi(self):
        old=self.u.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not old:
            raise C.WinError(C.get_last_error())
        try: yield
        finally:
            if old:self.u.SetThreadDpiAwarenessContext(old)

    def draw(self,target, region=None):
        if target is None or not isinstance(target.hwnd,int) or not target.pid:return False
        hwnd=target.hwnd
        pid=W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value!=target.pid or not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):return False
        front=self.u.GetForegroundWindow();front_pid=W.DWORD()
        self.u.GetWindowThreadProcessId(front,C.byref(front_pid))
        if front!=hwnd and front_pid.value!=os.getpid():return False
        with self.dpi():
            rect=W.RECT()
            if self.d.DwmGetWindowAttribute(hwnd,9,C.byref(rect),C.sizeof(rect))!=0:
                if not self.u.GetWindowRect(hwnd,C.byref(rect)):return False
            x,y,w,h=rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top
            if region is not None:
                x,y,w,h=map(round, region)
            if w<12 or h<12:return False
            edges=[(x,y,w,3),(x,y+h-3,w,3),(x,y,3,h),(x+w-3,y,3,h)]
            for edge,(a,b,width,height) in zip(self.windows,edges):
                if not self.u.SetWindowPos(edge,W.HWND(-1),a,b,width,height,0x0050):
                    return False  # SWP_NOACTIVATE | SWP_SHOWWINDOW
        return True

    def hide(self):
        for hwnd in self.windows:self.u.ShowWindow(hwnd,0)
        if self.badge:self.u.ShowWindow(self.badge,0)

    def draw_region(self, target, rect, number):
        if not self.draw(target, rect):
            return False
        x,y,w,h = map(round, rect)
        self.region_number = number
        with self.dpi():
            edges = [(x,y,w,3),(x,y+h-3,w,3),(x,y,3,h),(x+w-3,y,3,h)]
            for edge, (a,b,width,height) in zip(self.windows, edges):
                if not self.u.SetWindowPos(edge,W.HWND(-1),a,b,width,height,0x0050):return False
            if self.badge is None:
                self.badge=self.u.CreateWindowExW(0x080800A0,self.name,'',0x80000000,0,0,32,26,None,None,self.instance,None)
                if not self.badge:return False
                self.u.SetLayeredWindowAttributes(self.badge,0,255,2)
            if not self.u.SetWindowPos(self.badge,W.HWND(-1),x,y,32,26,0x0050):
                self.hide()
                return False
            self.u.InvalidateRect(self.badge,None,True)
            self.u.UpdateWindow(self.badge)
        return True

    def close(self):
        if self.badge:self.u.DestroyWindow(self.badge);self.badge=None
        for hwnd in self.windows:self.u.DestroyWindow(hwnd)
        self.windows=[]
        if self.atom:self.u.UnregisterClassW(self.name,self.instance);self.atom=0
        if self.brush:self.g.DeleteObject(self.brush);self.brush=None
