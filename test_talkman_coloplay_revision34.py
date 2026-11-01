"""复制／临时滚轮组合回归；不访问用户草稿或真实模型。"""
import threading
import time
import unittest
import sys
import subprocess
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from talkman_coloplay_preview import DeviceRuntime, LocalDeviceSession
from talkman_coloplay_protocol import validate_config, DEFAULT_BINDINGS
from talkman_coloplay_scroll import ScrollWorker
import test_talkman_coloplay as fixtures


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.runtime=DeviceRuntime(); self.runtime.step(0,0)

    def run_keys(self, rows):
        return [a for mask,t in rows for a in self.runtime.step(mask,t)]

    def test_right_lower_short_and_long_release_do_nothing(self):
        self.assertEqual(self.run_keys([(8,100),(0,200)]),[])
        self.assertEqual(self.run_keys([(8,300),(0,900)]),[])

    def test_vertical_horizontal_and_all_release_orders(self):
        for releases in ([(8,400),(0,500)],[(16,400),(0,500)],[(0,400)]):
            self.setUp()
            events=self.run_keys([(8,100),(24,200),(88,300)]+releases)
            self.assertEqual(events,['arm_send_current','cancel_arm'])
        self.setUp()
        self.assertEqual(self.run_keys([(8,100),(72,200),(8,220),(40,300),(0,350)]),['target_next','target_previous','target_commit'])

    def test_reverse_press_order_cancels_fill_and_no_copy(self):
        self.assertEqual(self.run_keys([(16,100),(24,200),(56,300),(0,1000)]),
                         ['arm_knob','cancel_arm'])

    def test_combo_without_rotation_and_rewrite_never_copy(self):
        self.assertEqual(self.run_keys([(8,100),(24,200),(0,250)]),['arm_send_current','cancel_arm'])
        self.assertEqual(self.run_keys([(8,300),(12,350),(0,450)]),['rewrite'])

    def test_no_navigation_after_modifier_release_while_knob_stays_down(self):
        events=self.run_keys([(8,100),(24,200),(88,250),(16,300),(48,350),(0,900)])
        self.assertEqual(events,['arm_send_current','cancel_arm'])

    def test_existing_custom_keys_and_token_survive_migration(self):
        old=dict(list(DEFAULT_BINDINGS.items())[:8]); old['ai']='Control+Alt+Shift+F1'
        cfg=validate_config(dict(bindings=old,token='b'*48))
        self.assertEqual(cfg['token'],'b'*48)
        self.assertTrue(all(cfg['bindings'][k]==v for k,v in old.items()))
        self.assertEqual(len(set(cfg['bindings'].values())),19)


class EditorTests(unittest.TestCase):
    setUp=fixtures.DesktopTests.setUp
    tearDown=fixtures.DesktopTests.tearDown

    def test_legacy_copy_and_old_preference_cannot_change_clipboard(self):
        a=self.app; a.set_text('把苹果拿过来')
        with patch.object(a,'clipboard_clear'), patch.object(a,'clipboard_append') as copy:
            self.device.config['preferences']['right_lower_copy']=True
            self.assertIsNone(self.device.semantic('copy'))
            status=a.status_var.get()
            self.device.arm(dict(action='copy',id='legacy'))
            self.device.dispatch('coloplay_copy')
            self.assertEqual(a.status_var.get(),status)
            a.text.tag_add('sel','1.1','1.3');self.device.execute('copy')
            a.text.tag_remove('sel','1.0','end');self.device.execute('copy')
            copy.assert_not_called()
        self.assertNotIn('right_lower_copy',validate_config(self.device.config)['preferences'])
        self.assertEqual(a.current_text(),'把苹果拿过来')

    def test_scroll_never_switches_or_fills_and_clears_pending_navigation(self):
        session=LocalDeviceSession(self.device)
        with patch.object(self.device.scroll,'submit') as scroll, patch.object(self.app,'fill_to_target') as fill, patch.object(self.device,'preview_target') as target:
            self.device.target_due=time.monotonic()+1
            for mask,t in [(8,100),(72,200),(8,250),(24,300),(56,350),(0,400)]: session.feed(mask,t)
            scroll.assert_not_called();target.assert_called_once_with(1)
            fill.assert_not_called();self.assertEqual(self.device.target_due,0)
            self.assertFalse(self.device.hidden)

    def test_logo_and_version_visible_at_small_size(self):
        from talkman_version import FULL_VERSION
        a=self.app;a.minsize(1,1);a.geometry('720x480');a.update()
        badge=a.coloplay_status
        self.assertLess(badge.winfo_rooty(),a.text.winfo_rooty())
        self.assertLess(badge.winfo_rootx()+badge.winfo_width(),a.records_button.winfo_rootx())
        a.open_settings('coloplay');a.update()
        self.assertNotIn('right_lower_copy',a.coloplay_preferences_controls['values'])
        a._show_version_record();a.update()
        self.assertIn(FULL_VERSION,a.version_text.get('1.0','end'))
        self.assertEqual(str(a.version_text.cget('state')),'disabled')

    def test_recording_signal_waits_for_microphone_and_stops_on_cancel(self):
        a=self.app; a.update()
        def tick():
            self.device.tick(); a.after_cancel(self.device.job); self.device.job=None
        self.assertTrue(a.start_voice('insert',from_device=True))
        tick()
        self.assertFalse(a.matrix_status.find_withtag('rain'))
        fixtures.FakeVoice.sessions[-1].emit('ready','')
        a.poll_events(); a.after_cancel(a.poll_job); a.poll_job=None
        tick()
        self.assertTrue(a.matrix_status.find_withtag('rain'))
        a.withdraw(); a.update()
        with patch.object(a.matrix_status,'refresh') as redraw:
            tick(); redraw.assert_not_called()
        a.deiconify(); a.update(); a.stop_voice(); tick()
        self.assertFalse(a.matrix_status.find_withtag('rain'))
        self.assertEqual(a.matrix_status.phase,'processing')
        a.cancel_operation(); tick()
        self.assertFalse(a.matrix_status.find_withtag('rain'))
        self.assertEqual(a.matrix_status.phase,'idle')

    def test_small_bilingual_wordmark_and_status_fit_in_both_themes(self):
        a=self.app; a.minsize(1,1); a.geometry('720x480'); a.update()
        for theme in ('matrix','dark','light'):
            a.set_theme(theme)
            self.device.tick(); a.after_cancel(self.device.job); self.device.job=None
            a.update()
            for tag in ('TALKMAN','COLOPLAY'):
                x0,y0,x1,y1=a.wordmark.bbox(tag)
                self.assertGreaterEqual(x0,0);self.assertGreaterEqual(y0,0)
                self.assertLessEqual(x1,a.wordmark.winfo_width())
                self.assertLessEqual(y1,a.wordmark.winfo_height())
            self.assertLess(a.matrix_status.winfo_rootx()+a.matrix_status.winfo_width(),
                            a.records_button.winfo_rootx())
        self.ready.stop()
        self.device.tick(); a.after_cancel(self.device.job); self.device.job=None
        self.assertFalse(a.wordmark.connected)
        self.assertEqual(a.coloplay_status.cget('style'),'Device.Ghost.TButton')

    def test_theme_setting_preserves_edit_and_remembers_choice(self):
        a=self.app
        self.assertEqual(a.theme_key,'matrix')
        a.set_text('选中的文字仍能接着修改')
        a.text.tag_add('sel','1.0','1.3'); a.text.mark_set('insert','1.3')
        before=(a.current_text(),a.get_selection(),a.text.index('insert'))
        a.save_ui_preferences(unrelated_preference='保留')
        a.open_settings('coloplay');a.update()
        for theme in ('dark','light','matrix'):
            a.appearance_var.set(a.appearance_choices[theme])
            a.appearance_choice.event_generate('<<ComboboxSelected>>');a.update()
            self.assertEqual(a.theme_key,theme)
            self.assertEqual((a.current_text(),a.get_selection(),a.text.index('insert')),before)
            saved=json.loads(a.ui_path.read_text('utf-8'))
            self.assertEqual(saved['appearance_theme'],theme)
            self.assertEqual(saved['unrelated_preference'],'保留')
        with patch.object(a,'save_ui_preferences',side_effect=OSError('验收保存失败')):
            self.assertFalse(a.set_theme('dark'))
            self.assertEqual(a.theme_key,'matrix')
            self.assertEqual(a.appearance_var.get(),a.appearance_choices['matrix'])

    def test_connection_keys_are_separate_from_save_and_fit(self):
        from tkinter import ttk
        a=self.app;self.device.connection_settings();a.update()
        win=a.coloplay_window
        frames=[w for w in win.winfo_children()[0].winfo_children() if isinstance(w,ttk.LabelFrame)]
        self.assertEqual(len(frames),1)
        self.assertLess(win.winfo_reqheight(),760)
        canvas=frames[0].winfo_children()[0]
        self.assertLess(canvas.yview()[1],1)
        canvas.yview_moveto(1);a.update()
        self.assertAlmostEqual(canvas.yview()[1],1)
        win.destroy();a.coloplay_window=None


class ThemeStartupTests(unittest.TestCase):
    def test_restart_restores_style_and_unknown_theme_defaults_to_green(self):
        import tempfile
        from pathlib import Path
        from talkman_app import TalkManApp
        with tempfile.TemporaryDirectory(prefix='TalkMan-theme-test-') as directory:
            path=Path(directory)/'interface.json'
            for stored, expected in (('dark','dark'), ('light','light'), ('matrix','matrix'), ('unknown','matrix')):
                path.write_text(json.dumps({'appearance_theme':stored}),encoding='utf-8')
                app=TalkManApp(Path(directory),desktop_services=False)
                try:
                    self.assertEqual(app.theme_key,expected)
                    self.assertEqual(app.text.cget('fg'),app.palette['text'])
                finally: app.close_app()


class ScrollTests(unittest.TestCase):
    def test_mac_native_horizontal_event_targets_pointer_window_without_keys(self):
        import os
        from talkman_coloplay_scroll import native_scroll
        q=Mock()
        q.kCGWindowBounds='bounds';q.kCGWindowOwnerPID='pid'
        q.kCGWindowListOptionOnScreenOnly=1;q.kCGWindowListExcludeDesktopElements=16
        q.kCGScrollEventUnitLine=1
        q.CGEventGetLocation.return_value=SimpleNamespace(x=50,y=60)
        q.CGWindowListCopyWindowInfo.return_value=[{'bounds':dict(X=0,Y=0,Width=200,Height=200),'pid':os.getpid()+1}]
        ax=Mock();ax.AXIsProcessTrusted.return_value=True
        with patch('talkman_coloplay_scroll.sys.platform','darwin'), patch.dict(sys.modules,{'Quartz':q,'ApplicationServices':ax,'Foundation':Mock()}):
            native_scroll(1,0)
            q.CGEventCreateScrollWheelEvent.assert_called_once_with(None,1,2,0,-3)
            q.CGEventSetFlags.assert_called_once_with(q.CGEventCreateScrollWheelEvent.return_value,0)
            q.CGEventPostToPid.assert_called_once_with(os.getpid()+1,q.CGEventCreateScrollWheelEvent.return_value)
            ax.AXIsProcessTrusted.return_value=False
            with self.assertRaisesRegex(RuntimeError,'辅助功能'):native_scroll(0,1)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 原生消息测试')
    def test_native_wheel_reaches_external_window_both_axes_without_activating(self):
        import ctypes as C
        from ctypes import wintypes as W
        from talkman_coloplay_scroll import native_scroll
        # 仅创建本测试自己的空白窗口。父进程不操纵用户的 Agent。
        source='''import tkinter as tk, json
r=tk.Tk();r.title('TalkMan 隔离滚轮验收');r.geometry('420x220+40+60');r.attributes('-topmost',True)
t=tk.Text(r,wrap='none');t.pack(fill='both',expand=True)
t.insert('1.0',('ABCDEFGHIJKLMNOPQRSTUVWXYZ'*30+'\\n')*100)
t.bind('<MouseWheel>',lambda e:(t.yview_scroll(-int(e.delta/120)*3,'units'),'break')[-1])
r.update()
# Tk 8.6 在 Windows 会把原生横轮转换为纵轮；本验收窗显式支持 WM_MOUSEHWHEEL。
import ctypes as c
from ctypes import wintypes as w
u=c.WinDLL('user32');proc=c.WINFUNCTYPE(c.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
u.SetWindowLongPtrW.argtypes=[w.HWND,c.c_int,c.c_void_p];u.SetWindowLongPtrW.restype=c.c_void_p
u.CallWindowProcW.argtypes=[c.c_void_p,w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.CallWindowProcW.restype=c.c_ssize_t
@proc
def window_proc(hwnd,msg,wp,lp):
 if msg==0x20e:
  t.xview_scroll(int(c.c_short(wp>>16).value/120)*3,'units');return 0
 return u.CallWindowProcW(old,hwnd,msg,wp,lp)
old=u.SetWindowLongPtrW(t.winfo_id(),-4,c.cast(window_proc,c.c_void_p))
print(json.dumps([t.winfo_rootx()+40,t.winfo_rooty()+40]),flush=True)
def report():
 print(json.dumps([t.xview()[0],t.yview()[0]]),flush=True)
 r.after(100,report)
report();r.mainloop()
'''
        p=subprocess.Popen([sys.executable,'-u','-c',source],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        user=C.WinDLL('user32');user.GetForegroundWindow.restype=W.HWND
        point=W.POINT();user.GetCursorPos(C.byref(point))
        try:
            x,y=json.loads(p.stdout.readline());user.SetCursorPos(x,y);time.sleep(.12)
            before=user.GetForegroundWindow()
            native_scroll(0,1);native_scroll(1,0)
            seen=False
            samples=[]
            for _ in range(12):
                hx,vy=json.loads(p.stdout.readline())
                samples.append((hx,vy))
                if hx>0 and vy>0: seen=True;break
            self.assertTrue(seen, '横向、纵向滚动均应到达隔离窗口：'+str(samples))
            self.assertEqual(user.GetForegroundWindow(),before)
        finally:
            user.SetCursorPos(point.x,point.y)
            p.terminate();p.communicate(timeout=4)

    def test_worker_cancel_discards_queued_and_close_rejects_more(self):
        started=threading.Event();release=threading.Event();calls=[]
        def backend(x,y):
            calls.append((x,y));started.set();release.wait(1)
        worker=ScrollWorker(backend)
        try:
            worker.submit('scroll_down');self.assertTrue(started.wait(1))
            worker.submit('scroll_right');worker.cancel();release.set()
            time.sleep(.04);self.assertEqual(calls,[(0,1)])
            worker.close();worker.submit('scroll_down');time.sleep(.02)
            self.assertEqual(calls,[(0,1)])
        finally:release.set();worker.close()

    def test_native_error_returns_without_crashing(self):
        worker=ScrollWorker(Mock(side_effect=RuntimeError('权限未开')))
        try:
            worker.submit('scroll_left');self.assertEqual(worker.errors.get(timeout=1),'权限未开')
        finally:worker.close()
