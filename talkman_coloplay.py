"""COLOPLAY 初代适配；桌面编辑事务仍由原 Talk Man 实现。"""
from __future__ import annotations

import json
import hashlib
import queue
import shutil
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

import regex

from talkman_coloplay_protocol import (ACTIONS, DEFAULT_BINDINGS, Intent,
    config_id, hid_binding, save_config, validate_config, validate_preferences)
from talkman_coloplay_server import StateServer
from talkman_coloplay_discovery import DiscoveryService, local_addresses
from talkman_target import is_agent_window
from talkman_coloplay_highlight import TargetHighlight
from talkman_coloplay_scroll import ScrollWorker, SCROLL_ACTIONS
from talkman_coloplay_cartridge import CARTRIDGE_BUILD, FILL_HOLD_MS, valid_build, version_status


TARGET_PAGE_SIZE = 4


class ColoPlayController:
    def __init__(self, app):
        self.app = app
        self.path = app.data_dir / 'coloplay.json'
        self.events = queue.Queue()
        self.server = None
        self.discovery = None
        self.discovery_error = ''
        self.wifi_error = ''
        self.peer = None
        self.device_build = None
        self.cartridge_offer = {}
        self.cartridge_download = None
        self.cartridge_request = ''
        self.active = False
        self.delivery_epoch = 0
        self.sending = False
        self.hidden = False
        self.cycling = False
        self.target_view_until = 0
        self.target_view_error = False
        self.target_preview = None
        self.target_candidates = None
        self.target_due = 0
        self.target_generation = 0
        self.target_scanning = False
        self.target_scan_serial = 0
        self.target_scan_steps = 0
        self.target_release_pending = False
        self.intent_steps = 1
        self.target_cancel = threading.Event()
        self.highlight = TargetHighlight(app)
        self.scroll = ScrollWorker()
        self.registered = set()
        self.intent = Intent()
        self.last_seen = 0
        self.job = None
        self.closed = False
        self.guide_offered = False
        self.saved_preferences = None
        self.local_session = None
        self.simulator = None
        self.usb_bridge = None
        self.usb_retry_at = 0
        self.usb_paused = False
        self.notice = ''
        self.notice_until = 0
        self.badge = tk.StringVar(app, '○ 未连接')
        self.usb_status = tk.StringVar(app, '等待连接 USB')
        self.cartridge_status = tk.StringVar(app, version_status(None))
        from talkman_coloplay_focus import EditorKnobFocus
        self.editor_focus = EditorKnobFocus(self)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            self.config = validate_config(data)
        except (ValueError, OSError, TypeError):
            self.config = validate_config({})
            app.status_var.set('COLOPLAY 配置未能读取，设备服务已关闭，请重新设置。')
        self.configure(self.config)
        self.tick()

    def bindings(self):
        return {'coloplay_'+k: v for k, v in self.config['bindings'].items()} if self.server else {}

    @property
    def preferences(self):
        return self.config['preferences']

    def save_preferences(self, values):
        # 仅调整桌面偏好，不重启设备服务，也不改变配对口令或卡带协议。
        config = {**self.config, 'preferences': validate_preferences(values)}
        save_config(self.path, config)
        self.config = config
        self.intent.cancel()
        self.cancel_navigation()
        if not self.preferences['highlight_target']:
            self.highlight.hide()
        self.apply_device_preferences()

    def apply_device_preferences(self):
        a = self.app
        if self.active and self.saved_preferences:
            a.topmost_var.set(True if self.preferences['keep_on_top'] else self.saved_preferences[0])
            a.window_controller.apply_preferences()
        for label in getattr(a, 'voice_hint_widgets', []):
            if self.active and self.preferences['hide_hints']:
                label.pack_forget()
            else:
                label.pack(fill='x', pady=(6, 0))
        a.set_device_controls_hidden(self.active and self.preferences['hide_device_buttons'])

    def configure(self, config):
        config = validate_config(config)
        self.disconnect_usb()
        self.stop_service()
        self.config = config
        self.usb_paused = config['connection_mode'] != 'usb'
        self.usb_retry_at = 0
        self.wifi_error = ''
        self.discovery_error = ''
        if self.config['enabled']:
            try:
                self.server = StateServer(self.config['host'], self.config['port'],
                    self.config['token'], config_id(self.config), self.events,
                    diagnostics_path=self.path.with_name('coloplay-connection.log'))
                if self.config['discovery']:
                    try:
                        self.discovery = DiscoveryService(self.config['host'], self.config['port'],
                            self.config['token'], config_id(self.config), self.config['device_host'])
                    except OSError:
                        self.discovery_error = '自动发现端口不可用，请检查其它实例或防火墙；原有连接服务仍可用。'
            except OSError as error:
                self.wifi_error = 'Wi-Fi 服务未启动，请检查本机 IP 和端口：'+str(error)
                self.app.status_var.set(self.wifi_error)
        self.app.window_controller.sync_hotkeys()

    def select_connection(self, mode, **changes):
        """保存单一连接选择后再切换；失败也不能偷偷回到另一种通道。"""
        if self.app.busy or self.app.voice or self.cartridge_download:
            raise ValueError('请先结束录音、处理或卡带更新，再切换连接。')
        config = validate_config({**self.config, **changes, 'connection_mode': mode})
        save_config(self.path, config)
        if self.simulator:self.simulator.close()
        self.intent.cancel();self.cancel_navigation();self.cancel_device_send();self.scroll.cancel()
        self.configure(config)
        self.local_notice(self.wifi_error or ('已切换 Wi-Fi，USB 已释放；等待卡带连接。'
                          if mode == 'wifi' else '已切换 USB，Wi-Fi 已停用。'))

    def stop_service(self):
        self.stop_cartridge_download()
        if getattr(self, 'discovery', None):
            self.discovery.stop()
            self.discovery = None
        self.registered.clear()
        self.peer = None
        self.device_build = None
        self.intent.cancel()
        self.set_active(False)
        if self.server:
            self.server.stop()
            self.server = None
        while not self.events.empty():
            kind,_,value=self.events.get_nowait()
            if kind=='cartridge_ready' and value[1]:
                threading.Thread(target=value[1].close,daemon=True).start()

    def ready(self):
        return bool(not self.local_session and self.server and self.peer and set(self.bindings()) <= self.registered
                    and not self.paused())

    def connected(self):
        """连接与按键可用性独立：打开设置不会断开已配对会话。"""
        if self.local_session:
            return bool(self.local_session.enabled)
        return bool(self.server and self.peer)

    def cartridge_version(self):
        if self.local_session:
            return getattr(self.local_session, 'device_build', None)
        return self.device_build if self.peer else None

    def stop_cartridge_download(self):
        self.cartridge_offer = {}
        self.cartridge_request = ''
        download = getattr(self, 'cartridge_download', None)
        self.cartridge_download = None
        if download:
            threading.Thread(target=download.close, daemon=True).start()

    def prepare_cartridge_update(self, request):
        identifier = request.get('id', '')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 32:return
        if identifier == self.cartridge_request:return
        self.stop_cartridge_download()
        self.cartridge_request = identifier
        if self.phase()[0] != 'idle' or self.paused() or self.app.input_picker.active:
            self.cartridge_offer = dict(id=identifier,status='error',message='先结束编辑操作')
            return
        self.intent.cancel();self.cancel_navigation();self.cancel_device_send()
        if self.device_build and self.device_build >= CARTRIDGE_BUILD:
            self.cartridge_offer = dict(id=identifier,status='current',message='已是电脑附带版本')
            return
        if self.peer is None:return
        self.cartridge_offer = dict(id=identifier,status='preparing')
        peer, host = self.peer, self.config['host']
        def prepare():
            from talkman_coloplay_update import CartridgeDownload
            download = None
            try:download=CartridgeDownload(host);error=''
            except (OSError,ValueError,KeyError):error='升级卡带未就绪，请升级电脑端'
            if self.closed or peer != self.peer or identifier != self.cartridge_request:
                if download:download.close()
                return
            self.events.put(('cartridge_ready',peer,(identifier,download,error)))
        threading.Thread(target=prepare, daemon=True, name='TalkMan-准备卡带').start()

    def paused(self):
        a = self.app
        return (any(win and win.winfo_exists() for win in
                    [getattr(a, 'settings_window', None), getattr(a, 'coloplay_window', None)])
                or bool(a.guide and a.guide.opened)
                or bool(getattr(self, 'guide', None) and self.guide.opened))

    def set_active(self, active):
        if not active:
            picker = getattr(self.app, 'input_picker', None)
            if self.active and picker and picker.active and picker.source == 'device':
                picker.cancel()
            if self.active: self.cancel_device_send()
            self.scroll.cancel()
            self.cancel_navigation()
            self.highlight.hide()
        if self.active == active:
            return
        self.active = active
        a = self.app
        if active:
            self.saved_preferences = (a.topmost_var.get(), a.edge_hide_var.get())
            a.topmost_var.set(True if self.preferences['keep_on_top'] else self.saved_preferences[0])
            a.sync_mode_var.set(False)
            a.live_sync.configure(False)
            a.preview_ai_var.set(True)
            a.window_controller.apply_preferences()
        else:
            if self.hidden:
                self.hidden = False
                a.window_controller.reveal(focus=False)
            if self.saved_preferences:
                a.topmost_var.set(self.saved_preferences[0])
                self.saved_preferences = None
                a.window_controller.apply_preferences()
        # 设备就绪时按偏好收起重复操作；断开或退出时恢复桌面入口。
        self.apply_device_preferences()

    def phase(self):
        a = self.app
        if a.target_filling: return 'filling', '正在发送' if self.sending else '正在填入'
        if a._mic_permission_pending: return 'permission', '请在电脑授权'
        if a.ai_review is not None: return 'review', '改写待确认'
        if a.convention_panel is not None: return 'blocked', '请完成词语设置'
        if a.voice and not a.voice_stopping:
            if getattr(a, 'voice_ready_token', None) != a.operation_id:
                return 'opening', '正在打开麦克风'
            return 'recording', '说修改要求' if a.voice_mode == 'rewrite' else '正在录音'
        if a.busy or a.auto_running: return 'processing', '正在处理'
        if getattr(a, 'input_picker', None) and a.input_picker.active:
            return 'input_select', '选择输入位置'
        return 'idle', '就绪'

    def gesture_context(self):
        phase=self.phase()[0]
        if phase=='opening':phase='recording'
        return f'{phase}:{self.app.operation_id}:{self.confirmation_marker()}'

    def stamp(self):
        a = self.app
        editor = self.editor_focus.sync()
        target = a.target_controller.selected if a.target_controller else None
        return (a.revision, a.current_text(), a.operation_id, a.auto_request, self.phase()[0],
                target.session_key() if target else None, a.get_selection(), a.text.index('insert'),
                self.cycling or bool(self.target_due) or self.target_scanning,
                a.sync_mode_var.get(), str(a.ai_review) if a.ai_review is not None else None, editor,
                (a.pending_delivery or {}).get('token',''),
                a.input_picker.stamp() if getattr(a, 'input_picker', None) else None)

    def cancel_device_send(self):
        self.delivery_epoch += 1
        if (self.app.pending_delivery or {}).get('device'):
            self.app.disarm_delivery()

    def finish_device_fill(self, epoch):
        a=self.app;pending=a.pending_delivery
        if not pending: return
        if epoch!=self.delivery_epoch or not self.active:
            a.disarm_delivery();return
        pending.update(device=True, expires=time.monotonic()+5, ready_at=0,
                       target=a.target_controller.selected.session_key() if a.target_controller.selected else None)
        a.status_var.set('已填入 · 5 秒内左上发送；右上取消；左下新录入，右下＋左下新指令')

    def device_send_pending(self):
        a=self.app
        if not (a.pending_delivery or {}).get('device'):return False
        a.check_delivery_confirmation()
        pending=a.pending_delivery
        if not pending or not pending.get('device'):return False
        target=a.target_controller.selected if a.target_controller else None
        if pending.get('target')!=(target.session_key() if target else None):
            self.cancel_device_send();return False
        return True

    def action_stamp(self, semantic):
        a=self.app
        if semantic == 'send':
            pending = a.pending_delivery or {}
            target = a.target_controller.selected if a.target_controller else None
            return (pending.get('token'), a.revision, a.current_text(),
                    target.session_key() if target else None)
        if semantic=='stop':
            # 同一轮的实时转写和 opening→recording 不使结束预约失效。
            return (a.operation_id,id(a.voice),a.voice_mode,a.voice_stopping)
        if semantic=='cancel' and a.busy:
            return ('cancel',a.operation_id,id(a.voice),a.voice_mode)
        return self.stamp()

    def semantic(self, action):
        a, phase = self.app, self.phase()[0]
        if a.target_filling or a.convention_panel is not None:
            return None
        picker = getattr(a, 'input_picker', None)
        if picker and picker.active:
            if picker.confirming:
                return None
            if action == 'cancel_delete': return 'input_cancel'
            if action in ('record', 'rewrite') and phase in ('opening', 'recording'): return 'stop'
            if action == 'rewrite' and not a.busy: return 'rewrite'
            if a.busy and getattr(a, 'voice_input_navigation', False): return None
            if action in ('target_previous', 'target_next'): return action
            return {'previous': 'input_previous', 'next': 'input_next', 'knob': 'input_confirm'}.get(action)
        if (self.target_due or self.target_scanning) and action == 'cancel_delete':
            return 'input_cancel'
        if self.device_send_pending():
            return {'ai': 'send', 'cancel_delete': 'cancel_send',
                    'record': 'record', 'rewrite': 'rewrite'}.get(action) if phase == 'idle' else None
        if action in ('previous', 'next'):
            if phase=='review':return 'review_'+action
            return 'move_'+action if a.editor_navigation_allowed() and not a._editor_selecting else None
        if action in ('target_previous','target_next'):
            if phase=='review':return 'review_'+action.removeprefix('target_')
            return action
        if action in SCROLL_ACTIONS: return None  # 旧鼠标滚动已由目标选择替代。
        if action == 'target_commit':
            return action if self.target_due or self.target_scanning else None
        if self.cycling: return None
        if action == 'cancel_delete':
            return 'delete' if phase == 'idle' else 'cancel'
        if action == 'clear':
            return 'clear' if phase == 'idle' and a.current_text() else None
        if action == 'toggle': return 'toggle'
        if action == 'knob':
            if phase!='idle': return None
            return 'select'
        if action == 'send_current':
            return action if (phase == 'idle' and not self.target_due and not self.target_scanning
                              and a.target_controller and a.target_controller.selected
                              and a.target_controller.selected_input is None) else None
        if action in ('record', 'rewrite'):
            return 'stop' if phase in ('opening','recording') else action if phase == 'idle' else None
        if action == 'ai': return 'confirm_review' if phase == 'review' else 'ai' if phase == 'idle' else None
        if action == 'fill':
            if self.device_send_pending(): return None
            if self.target_due or self.target_scanning:
                return None
            if phase == 'idle' and a.current_text().strip() and a.target_controller and a.target_controller.selected:
                return 'fill'
        return None

    def arm(self, message):
        action = message.get('action')
        # Wi-Fi 按下时所见状态必须仍成立，避免跨过倒计时边界变成另一种动作。
        if ('confirmation' in message and action == 'ai'
                and message['confirmation'] != self.confirmation_marker()):
            self.intent.cancel();self.local_notice('确认阶段已变化，请松开后重新操作');return
        if ('target_marker' in message and action == 'send_current'
                and message['target_marker'] != self.target_marker()):
            self.intent.cancel();self.local_notice('目标已变化，本次未发送');return
        if action == 'copy': return  # 旧版短按不再预约，也不提示错误。
        if action=='fill' and (self.target_due or self.target_scanning):
            self.cancel_navigation()
            self.local_notice('预选已取消，请松开右下选定目标后再长按')
            self.intent.cancel();return
        semantic = self.semantic(action) if action in ACTIONS and self.ready() else None
        self.intent.cancel()
        if semantic:
            steps = message.get('steps', 1)
            self.intent_steps = steps if type(steps) is int and 1 <= steps <= 32 else 1
            self.intent.arm(message['id'], action, self.action_stamp(semantic), semantic, ttl=3)
        else:
            self.notice = '此时不能执行'
            self.notice_until = time.monotonic()+3

    def dispatch(self, action):
        action = action.removeprefix('coloplay_')
        if action == 'copy': return
        reserved=self.intent.pending[3] if self.intent.pending else None
        semantic = self.intent.consume('knob' if action=='fill' else action, self.action_stamp(reserved)) if self.ready() else None
        if action=='fill': semantic='fill' if semantic=='select' and self.semantic('fill') else None
        if semantic=='send' and not self.device_send_pending():semantic=None
        if semantic is None:
            self.notice = '操作已过期'
            self.notice_until = time.monotonic()+3
            return
        self.execute(semantic, steps=self.intent_steps)

    def local_notice(self, text):
        self.notice=text
        self.notice_until=time.monotonic()+3
        self.app.status_var.set(text)

    def execute(self, semantic, *, steps=1):
        """已经过各连接入口校验的业务动作；与实体设备共用编辑事务。"""
        if semantic == 'copy': return  # 兼容旧 ROM 报文；右下已改为纯组合键。
        a = self.app
        if semantic not in ('send', 'cancel_send', 'record', 'rewrite') and self.device_send_pending(): return
        if semantic!='send': self.cancel_device_send()
        if semantic not in SCROLL_ACTIONS:
            self.scroll.cancel()
        if semantic not in ('target_previous', 'target_next', 'target_commit'):
            self.cancel_navigation()
        if semantic in ('record', 'rewrite', 'ai', 'move_previous', 'move_next') and not a.input_picker.active:
            a.window_controller.begin_device_edit()
        if semantic in ('input_previous', 'input_next'):
            direction = steps if semantic == 'input_next' else -steps
            a.input_picker.step(-direction if self.preferences['reverse_rotation'] else direction)
        elif semantic == 'input_confirm': a.input_picker.confirm()
        elif semantic == 'input_cancel': a.input_picker.cancel()
        elif semantic in SCROLL_ACTIONS: self.scroll.submit(semantic)
        elif semantic in ('move_previous', 'move_next'):
            self.editor_focus.move(steps if semantic=='move_next' else -steps)
        elif semantic in ('review_previous','review_next'):
            if a.ai_review is not None:
                a.ai_review_text.yview_scroll(3*steps if semantic=='review_next' else -3*steps,'units')
        elif semantic=='select': self.editor_focus.toggle_selection()
        elif semantic=='send':
            if self.device_send_pending():a.send_pending_delivery()
        elif semantic=='cancel_send': self.local_notice('已取消发送，目标输入框保留文字')
        elif semantic=='send_current':
            a.target_filling=True
            self.sending=True
            a._delivery_snapshot=None
            ticket=self.send_ticket()
            target=a.target_controller.selected
            epoch=self.delivery_epoch
            a.status_var.set('正在核对目标输入框并发送已有内容…')
            a.target_controller.send_current(target.session_key(), lambda state,value:
                a.events.put(('target_send_device', ticket, state, value)),
                cancelled=lambda: self.closed or epoch != self.delivery_epoch or not self.active)
        elif semantic == 'delete': self.backward_delete()
        elif semantic == 'clear':
            a.clear_text()
            self.local_notice('已清空，可用上一步恢复')
        elif semantic == 'cancel': a.cancel_operation()
        elif semantic == 'confirm_review': a.ai_review_finish(True)
        elif semantic == 'ai':
            a.organize_once()
            if not a.auto_running:
                self.notice = '请查看电脑提示'
                self.notice_until = time.monotonic()+4
        elif semantic == 'stop': a.stop_voice()
        elif semantic in ('record', 'rewrite'):
            mode = 'rewrite' if semantic == 'rewrite' else 'replace' if a.get_selection() else 'insert'
            if not a.start_voice(mode, from_device=True):
                self.notice = '请查看电脑提示'
                self.notice_until = time.monotonic()+4
        elif semantic == 'toggle':
            self.hidden = not self.hidden
            if self.hidden:
                a.withdraw()
                if a.window_controller.tab: a.window_controller.tab.withdraw()
            else:
                a.window_controller.reveal(focus=True)
        elif semantic in ('target_previous', 'target_next'):
            self.editor_focus.leave()
            direction = steps if semantic=='target_next' else -steps
            self.preview_target(-direction if self.preferences['reverse_rotation'] else direction)
        elif semantic == 'target_commit': self.commit_target()
        elif semantic == 'fill':
            # 长按仅填入；成功后须在短窗口内重新点按，才允许独立发送确认。
            a.sync_mode_var.set(False)
            a.live_sync.configure(False)
            a.fill_to_target(paste_only=True,device_confirm=True)

    def send_ticket(self):
        a=self.app
        return (getattr(a.window_controller, 'device_edit', None), a.revision, a.current_text())

    def finish_send(self, ticket, state):
        self.sending=False
        if state == 'ok' and ticket and ticket == self.send_ticket():
            self.app.window_controller.finish_device_edit(ticket[0])

    def cancel_navigation(self):
        self.target_cancel.set()
        self.target_generation += 1
        self.target_due = 0
        self.target_scanning = False
        self.target_scan_serial += 1
        self.target_scan_steps = 0
        self.target_release_pending = False
        self.target_preview = None
        self.target_candidates = None
        self.target_view_until = 0
        self.target_view_error = False

    def preview_target(self, direction):
        now=time.monotonic()
        draft_target = self.app.input_picker.target if self.app.input_picker.active else None
        self.app.input_picker.cancel(quiet=True)
        self.app.sync_mode_var.set(False)
        self.app.live_sync.configure(False)
        self.app.disarm_delivery()
        self.highlight.hide()
        self.target_cancel.set()
        self.target_generation += 1
        self.target_view_error=False
        self.target_view_until=now+1.8
        controller=self.app.target_controller
        if not controller:
            self.target_view_error=True
            self.app.status_var.set('当前系统不支持枚举目标窗口');return
        if self.target_scanning:
            self.target_scan_steps+=direction
            return
        if self.target_candidates is None or (not self.target_due and not self.cycling):
            self.target_candidates=self.target_windows()
        if not self.target_candidates:
            self.target_scanning=True
            self.target_scan_steps=direction
            self.target_scan_serial+=1
            serial=self.target_scan_serial
            controller.refresh(lambda state,value:self.events.put(('target_scan',state,(serial,value))))
            return
        current=self.target_preview or draft_target or controller.selected
        windows=self.target_candidates
        index=next((i for i,w in enumerate(windows) if current and w.session_key()==current.session_key()),
                   -1 if direction>0 else 0)
        self.target_preview=windows[(index+direction)%len(windows)]
        self.target_due=now
        self.app.status_var.set('预选 Agent：'+self.target_preview.app+' · 松开右下即选定')

    def commit_target(self):
        if self.target_scanning:
            self.target_release_pending = True
            return
        if self.cycling or not self.target_due:return
        if self.paused() or not self.active:
            self.cancel_navigation();return
        controller=self.app.target_controller
        target=self.target_preview
        self.target_due=0
        if not controller or target is None:return
        self.target_cancel = threading.Event()
        generation = self.target_generation
        self.cycling = True
        controller.cycle(1, lambda state, value: self.events.put(('cycle', state, (generation, value))),
                         expected_target=target, cancelled=self.target_cancel, commit_preview=sys.platform == 'darwin')

    def backward_delete(self):
        a = self.app
        if self.phase()[0] != 'idle': return
        text, selection = a.current_text(), a.get_selection()
        start = len(a.text.get('1.0', selection[0] if selection else 'insert'))
        if selection:
            end = len(a.text.get('1.0', selection[1]))
        else:
            # Unicode 字素：组合重音、国旗、肤色和家庭表情都是一次删除。
            segment = next((m for m in reversed(list(regex.finditer(r'\X', text))) if m.start() < start), None)
            if segment is None: return
            start, end = segment.span()
        if start == end: return
        a._prepare_delete()
        a.set_text(text[:start]+text[end:])
        a.auto_block_revision = a.revision
        a.text.mark_set('insert', f'1.0 + {start} chars')
        a.text.see('insert')
        a.status_var.set('已向前删除一次，可用「上一步」恢复')

    def target_windows(self):
        controller = self.app.target_controller
        return sorted((w for w in controller.windows if is_agent_window(w)),
                      key=lambda w:(w.app.casefold(),str(w.id),str(w.process_started))) if controller else []

    def target_marker(self):
        target = self.app.target_controller.selected if self.app.target_controller else None
        return hashlib.sha256(repr(target.session_key()).encode()).hexdigest()[:16] if target else ''

    def confirmation_marker(self):
        return self.app.pending_delivery['token'] if self.device_send_pending() else ''

    def snapshot(self):
        focused = self.editor_focus.sync()[0]
        phase, title = self.phase()
        if self.cycling: title = '正在切换目标'
        if self.notice_until > time.monotonic() and phase == 'idle': title = self.notice
        if focused and phase == 'idle': title = '编辑聚焦'
        a = self.app
        target = a.target_controller.selected if a.target_controller else None
        # 仅展示应用名；同名窗口加序号，不传对话标题或正文。
        windows = self.target_candidates if self.target_candidates is not None else self.target_windows()
        def name(window):
            base = ''.join(ch for ch in window.app if ch.isprintable())[:40]
            same = [w for w in windows if w.app == window.app]
            if len(same)>1:
                number = next((i+1 for i,w in enumerate(same) if w.session_key()==window.session_key()),1)
                return f'{base} ({number})'
            return base
        choice=self.target_preview or target
        index = next((i for i,w in enumerate(windows) if choice and w.session_key()==choice.session_key()),-1)
        target_name = name(target) if target else '未选目标'
        previous = name(windows[(index-1)%len(windows)]) if index>=0 and len(windows)>1 else ''
        following = name(windows[(index+1)%len(windows)]) if index>=0 and len(windows)>1 else ''
        page_start = max(index,0)//TARGET_PAGE_SIZE*TARGET_PAGE_SIZE
        page_names = [name(w) for w in windows[page_start:page_start+TARGET_PAGE_SIZE]]
        paused = self.paused()
        if paused:
            phase, title = 'blocked', '桌面设置中'
            target_name = '关闭后继续操作'
        return dict(ready=self.ready() or (self.connected() and paused), phase=phase, title=title,
                    action_context=self.gesture_context(),
                    device_build=self.cartridge_version(),
                    cartridge_update=self.cartridge_offer,
                    input_selecting=a.input_picker.active,
                    input_index=a.input_picker.index+1, input_count=len(a.input_picker.regions),
                    input_error=bool(a.input_picker.error), input_loading=a.input_picker.loading or a.input_picker.confirming,
                    input_target=a.input_picker.target.app if a.input_picker.active else '',
                    confirmation=self.confirmation_marker(), target_marker=self.target_marker(),
                    selecting=self.editor_focus.selecting,
                    send_seconds=max(1,int((a.pending_delivery['expires']-time.monotonic())+.999)) if self.device_send_pending() else 0,
                    editor_focused=focused, selection_count=self.editor_focus.selection_count(),
                    target=target_name, hidden=self.hidden, arm=self.intent.id(), cycling=self.cycling,
                    target_view=not paused and (self.cycling or self.target_scanning or bool(self.target_due) or time.monotonic()<self.target_view_until),
                    target_waiting=bool(self.target_due),target_scanning=self.target_scanning,
                    target_progress=1,
                    target_choice=name(choice) if choice else '',
                    target_previous=previous, target_next=following, target_index=index+1,
                    target_count=len(windows), target_error=self.target_view_error,
                    target_page_names=page_names, target_page_start=page_start+1,
                    target_page=page_start//TARGET_PAGE_SIZE+1 if windows else 0,
                    target_pages=(len(windows)+TARGET_PAGE_SIZE-1)//TARGET_PAGE_SIZE,
                    labels=dict(ai='发送' if self.device_send_pending() else '确认' if phase == 'review' else 'AI 改写', cancel_delete='取消发送' if self.device_send_pending() else '退格' if phase == 'idle' else '放弃' if phase == 'review' else '取消',
                                record='结束' if phase in ('opening','recording') else '录入' if phase == 'idle' else '等待',
                                rewrite='指令', modifier='组合', clear='清空'),
                    can_copy=False,
                    can_fill=not paused and self.semantic('fill') is not None)

    def tick(self):
        if self.closed: return
        self.editor_focus.sync()
        for _ in range(40):
            try: kind, peer, value = self.events.get_nowait()
            except queue.Empty: break
            usb_owned = bool(self.local_session and getattr(self.local_session, 'source', None) == 'usb')
            if kind == 'cycle':
                self.cycling = False
                generation,value=value
                if generation!=self.target_generation:continue
                self.target_preview = None
                self.target_candidates = None
                self.target_view_until = time.monotonic()+1.8
                self.target_view_error = peer != 'ok'
                if peer == 'ok':
                    self.app.target_label_var.set(self.app.target_controller.label)
                    self.app.status_var.set(('已选目标：' if sys.platform=='darwin' else '已切换目标：')
                                            +self.app.target_controller.label)
                    if self.preferences['highlight_target']:
                        self.highlight.show(value)
                else: self.app.status_var.set(str(value))
            elif kind == 'target_scan':
                serial,value=value
                if not self.target_scanning or serial!=self.target_scan_serial:continue
                self.target_scanning=False
                steps=self.target_scan_steps;self.target_scan_steps=0
                self.target_candidates=self.target_windows()
                if peer=='ok' and self.target_candidates:
                    self.preview_target(steps or 1)
                    if self.target_release_pending:
                        self.target_release_pending=False
                        self.commit_target()
                else:
                    self.target_view_error=True
                    self.target_view_until=time.monotonic()+1.8
                    self.app.status_var.set('未找到 Agent 窗口，请打开后再旋转')
            elif kind == 'connected':
                if not self.server or self.config['connection_mode'] != 'wifi':continue
                self.stop_cartridge_download()
                self.peer = peer
                self.device_build = valid_build(value)
                if not usb_owned:self.intent.cancel()
            elif kind == 'cartridge_ready':
                identifier,download,error=value
                if peer != self.peer or identifier != self.cartridge_request:
                    if download:threading.Thread(target=download.close,daemon=True).start()
                elif error:self.cartridge_offer=dict(id=identifier,status='error',message=error)
                else:
                    self.cartridge_download=download
                    self.cartridge_offer=dict(download.offer,id=identifier,status='ready')
            elif peer == self.peer:
                if kind in ('disconnected', 'exit'):
                    if not usb_owned and self.app.input_picker.active and self.app.input_picker.source == 'device':
                        self.app.input_picker.cancel()
                    self.peer = None
                    self.device_build = None
                    self.stop_cartridge_download()
                    if not usb_owned:
                        self.intent.cancel()
                        self.cancel_navigation()
                elif kind == 'intent' and not usb_owned: self.arm(value)
                elif kind == 'cartridge_check' and not usb_owned: self.prepare_cartridge_update(value)
                elif kind == 'cancel_intent' and not usb_owned:
                    self.intent.cancel(); self.cancel_navigation(); self.scroll.cancel()
        if self.usb_bridge:
            self.usb_bridge.poll()
            if self.usb_bridge.disconnected and not self.usb_bridge.exited:
                self.usb_bridge.close();self.usb_bridge=None
                self.usb_retry_at=time.monotonic()+3
        if (not self.usb_bridge and not self.simulator and not self.usb_paused
                and self.config['connection_mode'] == 'usb' and self.app.window_controller.native_enabled
                and time.monotonic()>=self.usb_retry_at and not self.app.busy):
            self.usb_retry_at=time.monotonic()+3
            self.connect_usb(automatic=True)
        if self.cartridge_download and time.monotonic() >= self.cartridge_download.expires:
            self.stop_cartridge_download()
        self.cartridge_status.set(version_status(self.cartridge_version(), connected=self.connected(),
                                                simulator=bool(self.simulator)))
        paused = self.paused()
        if paused and not getattr(self, '_was_paused', False):
            self.app.input_picker.cancel(quiet=True)
            self.intent.cancel()
            self.cancel_navigation()
            self.cancel_device_send()
            self.scroll.cancel()
            if self.local_session:
                self.local_session.reset()
        self._was_paused = paused
        local_active=bool(self.local_session and self.local_session.enabled and not self.paused())
        self.set_active(self.ready() or local_active or (self.connected() and paused))
        if self.local_session:
            source='模拟' if self.simulator else 'USB'
            status='已退出' if not self.local_session.enabled else '已连接 · 操作暂停' if paused else '已连接'
            self.badge.set('● '+source+' '+status)
        else:
            self.badge.set('● Wi-Fi 已连接 · 操作暂停' if self.connected() and paused else
                           '● Wi-Fi 已连接' if self.active else
                           '○ 快捷键未就绪' if self.peer else
                           '○ Wi-Fi 服务未启动' if self.wifi_error else
                           '○ Wi-Fi 等待卡带' if self.config['connection_mode'] == 'wifi' else '○ USB 待连接')
        if not self.scroll.errors.empty(): self.local_notice(self.scroll.errors.get_nowait())
        self.app.wordmark.set_connected(self.connected())
        self.app.coloplay_status.configure(style='Connected.Device.Ghost.TButton' if self.connected() else 'Device.Ghost.TButton')
        self.app.matrix_status.update_status(self.phase()[0], self.app.voice_mode, time.monotonic())
        if self.server: self.server.publish(self.snapshot())
        if (self.active and not self.guide_offered and self.app.window_controller.native_enabled
                and self.phase()[0] == 'idle' and not self.device_send_pending() and not self.paused()
                and not self.app.history_open and not self.app.grab_current()
                and self.app.ui_preferences.get('coloplay_guide_version', 0) < 1):
            self.guide_offered=True
            self.app.after_idle(self.show_guide)
        self.job = self.app.after(20, self.tick)

    def show_guide(self):
        if self.app.closing or self.phase()[0] != 'idle' or self.device_send_pending():
            self.guide_offered=False
            self.local_notice('请结束当前操作后再打开按键指南')
            return
        if self.app.guide and self.app.guide.opened:return
        from talkman_coloplay_guide import DeviceGuide
        self.cancel_navigation();self.intent.cancel()
        self.app.window_controller.reveal(focus=True)
        self.app.hover_help.hide();self.app.target_picker.close()
        self.app.guide=DeviceGuide(self.app)
        self.app.window_controller.sync_hotkeys()

    def open_simulator(self):
        if self.simulator:
            self.simulator.win.deiconify();self.simulator.win.lift();return
        if self.usb_bridge:self.usb_bridge.close();self.usb_bridge=None
        from talkman_coloplay_preview import ColoPlaySimulator
        self.intent.cancel()
        self.simulator=ColoPlaySimulator(self)

    def connect_usb(self, *, automatic=False):
        if self.config['connection_mode'] != 'usb':return
        self.usb_paused=False
        self.usb_status.set('正在检查 USB 接口…')
        if self.simulator:self.simulator.close()
        if self.usb_bridge:self.usb_bridge.close()
        from talkman_coloplay_usb import UsbBridge
        if not automatic:self.intent.cancel()
        self.usb_bridge=UsbBridge(self, quiet=automatic)

    def disconnect_usb(self):
        self.usb_paused=True
        if self.usb_bridge:self.usb_bridge.close();self.usb_bridge=None

    def export(self, destination):
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'TalkMan-设备卡带'
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for path in root.iterdir():
            if path.is_file() and path.suffix in ('.lua', '.clx'):
                shutil.copy2(path, destination/path.name)
        cfg = self.config
        lines = ['-- 本文件含本机配对口令，请勿公开上传。', 'TM_CONFIG = {',
                 f' url = "ws://{cfg["host"]}:{cfg["port"]}",',
                 f' discovery = {str(cfg.get("discovery", True)).lower()}, discovery_port = 18744,',
                 f' token = "{cfg["token"]}", config = "{config_id(cfg)}",',
                 f' center=4, left=5, right=6, fill_ms={FILL_HOLD_MS}, exit_ms=5000, keys={{']
        for action, binding in cfg['bindings'].items():
            modifier, key = hid_binding(binding)
            lines.append(f'  {action}={{{modifier},{key}}},')
        lines += [' }', '}']
        (destination/'TalkMan-连接配置.lua').write_text('\n'.join(lines)+'\n', encoding='utf-8')

    def settings(self):
        self.app.open_settings('coloplay')

    def connection_settings(self):
        a = self.app
        existing = getattr(a, 'coloplay_window', None)
        if existing and existing.winfo_exists(): existing.lift(); return
        win = tk.Toplevel(a)
        a.coloplay_window = win
        win.title('COLOPLAY · 设备设置')
        win.transient(a)
        outer = ttk.Frame(win, padding=24); outer.pack(fill='both', expand=True)
        ttk.Label(outer,text='连接方式',style='SettingsField.TLabel').pack(anchor='w')
        mode=tk.StringVar(win,self.config['connection_mode'])
        selector=ttk.Frame(outer);selector.pack(fill='x',pady=(8,8))
        for value,label in (('usb','USB'),('wifi','Wi-Fi')):
            ttk.Radiobutton(selector,text=label,value=value,variable=mode,
                            command=lambda:save()).pack(side='left',padx=(0,24))
        ttk.Label(outer,text='选择后立即切换并记住；另一条连接会自动停用。',style='Muted.TLabel').pack(anchor='w')
        ttk.Label(outer,textvariable=self.badge,style='Muted.TLabel').pack(anchor='w',pady=(8,4))
        transport=ttk.Frame(outer);transport.pack(fill='x',pady=(4,8))
        usb_box=ttk.Frame(transport)
        ttk.Label(usb_box,text='用 USB 数据线同步圆屏状态和按键，无需填写 IP。',style='Muted.TLabel').pack(anchor='w')
        ttk.Label(usb_box,text='首次使用需安装这台电脑的配对卡带。设备麦克风可在「语音」选择。',
                  wraplength=500,style='Muted.TLabel').pack(anchor='w',pady=4)
        ttk.Label(usb_box,textvariable=self.usb_status,wraplength=500,style='Muted.TLabel').pack(anchor='w',pady=4)
        box=ttk.Frame(transport)
        def show_mode():
            usb_box.pack_forget();box.pack_forget()
            (box if mode.get()=='wifi' else usb_box).pack(fill='x')
        ttk.Label(box, text='电脑与 COLOPLAY 需在同一局域网。换网络后刷新电脑地址。',
                  wraplength=500,style='Muted.TLabel').grid(row=1,columnspan=2,sticky='w',pady=(4,8))
        fields = {}
        addresses = local_addresses()
        for row, (name, label) in enumerate([('host','本机 IP · 这台电脑'), ('port','状态端口'), ('device_host','设备 IP · 可留空')], 2):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky='w')
            fields[name] = tk.StringVar(win, str(self.config[name]))
            if name == 'host':
                host_row=ttk.Frame(box);host_row.grid(row=row,column=1,pady=4,sticky='ew')
                chooser=ttk.Combobox(host_row, values=addresses, textvariable=fields[name], width=22)
                chooser.pack(side='left')
                def refresh_addresses():
                    found=local_addresses();chooser.configure(values=found)
                    if len(found)==1:fields['host'].set(found[0])
                    note.set('已刷新电脑地址，请选择当前网卡并保存。设备 IP 填 COLOPLAY 小屏里的地址。')
                a.button(host_row,'刷新',refresh_addresses,style='Ghost.TButton').pack(side='left',padx=4)
            else: ttk.Entry(box,textvariable=fields[name],width=32).grid(row=row,column=1,pady=4)
        discovery=tk.BooleanVar(win,self.config['discovery'])
        ttk.Checkbutton(box,text='自动寻找已配对电脑（需要新版卡带）',variable=discovery).grid(row=5,columnspan=2,sticky='w',pady=(8,0))
        ttk.Label(box,text='公司网络屏蔽广播时，填写设备 IP；电脑和设备需在可互通的局域网。',style='Muted.TLabel').grid(row=6,columnspan=2,sticky='w',pady=4)
        values = {}
        advanced=ttk.Frame(outer)
        def fold_keys():
            if keys.winfo_manager():
                keys.pack_forget();key_toggle.configure(text='▸ 按键映射')
            else:
                keys.pack(fill='x',pady=6);key_toggle.configure(text='▾ 收起按键映射')
        key_toggle=a.button(outer,'▸ 按键映射',fold_keys,style='Ghost.TButton')
        key_toggle.pack(anchor='w')
        advanced.pack(fill='x')
        keys = ttk.LabelFrame(advanced, text='设备快捷键 · 修改后需要重新配对卡带')
        canvas=tk.Canvas(keys,width=480,height=150,highlightthickness=0,bg=a.palette['bg'])
        canvas.pack(side='left',fill='both',expand=True)
        scrollbar=ttk.Scrollbar(keys,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.configure(yscrollcommand=scrollbar.set)
        key_rows=ttk.Frame(canvas,padding=(8,4))
        canvas.create_window((0,0),window=key_rows,anchor='nw')
        key_rows.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        def key_wheel(event):
            canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
            return 'break'
        canvas.bind('<MouseWheel>',key_wheel)
        for row, (action, label) in enumerate((item for item in ACTIONS.items()
                if item[0]!='copy' and item[0] not in SCROLL_ACTIONS)):
            label_widget=ttk.Label(key_rows, text=label)
            label_widget.grid(row=row,column=0,sticky='w',padx=(0,12))
            label_widget.bind('<MouseWheel>',key_wheel)
            values[action] = tk.StringVar(win,self.config['bindings'][action])
            entry=ttk.Entry(key_rows,textvariable=values[action],width=32)
            entry.grid(row=row,column=1,pady=3);entry.bind('<MouseWheel>',key_wheel)
        note = tk.StringVar(win,self.wifi_error or self.discovery_error or '')
        ttk.Label(outer,textvariable=note,wraplength=500,style='Muted.TLabel').pack(anchor='w',pady=8)
        def save(export=False):
            try:
                changes=dict(bindings={**self.config['bindings'],**{k:v.get() for k,v in values.items()}})
                if mode.get()=='wifi':
                    changes.update(host=fields['host'].get().strip(),port=fields['port'].get(),
                                   discovery=discovery.get(),device_host=fields['device_host'].get().strip())
                config = validate_config({**self.config,**changes,'connection_mode':mode.get()})
                if set(config['bindings'].values()) & set(a.shortcuts.values()):
                    raise ValueError('设备快捷键与桌面已有快捷键冲突，请换一组。')
                self.select_connection(mode.get(),**changes)
                note.set(self.wifi_error or self.discovery_error or ('已切换 Wi-Fi，USB 已停用。关闭设置后可用三键更新卡带。'
                         if mode.get()=='wifi' else '已切换 USB，Wi-Fi 已停用。等待本机配对卡带。'))
                if export:
                    parent = filedialog.askdirectory(parent=win, title='选择卡带导出的父目录')
                    if parent:
                        dest=Path(parent)/'TalkMan-COLOPLAY卡带'
                        self.export(dest)
                        note.set('已导出到：'+str(dest))
            except (ValueError, OSError) as error:
                mode.set(self.config['connection_mode']);note.set(str(error))
            show_mode()
        actions = ttk.Frame(outer); actions.pack(fill='x',pady=(4,8))
        a.button(actions,'保存并重新连接',save).pack(side='left')
        a.button(actions,'首次配对 · 导出源码',lambda:save(True),style='Ghost.TButton').pack(side='left',padx=8)
        a.button(outer,'设备模拟器',lambda:(close(),self.open_simulator()),style='Ghost.TButton').pack(anchor='w')
        ttk.Label(outer,textvariable=a.status_var,wraplength=500,style='Muted.TLabel').pack(anchor='w',pady=8)
        show_mode()
        def close():
            win.destroy(); a.coloplay_window=None; a.window_controller.sync_hotkeys()
        win.protocol('WM_DELETE_WINDOW',close)
        a.window_controller.sync_hotkeys()

    def close(self):
        self.editor_focus.close()
        self.closed = True
        self.scroll.close()
        self.cancel_navigation()
        self.highlight.close()
        if self.job: self.app.after_cancel(self.job)
        if self.simulator:self.simulator.close()
        if self.usb_bridge:self.usb_bridge.close();self.usb_bridge=None
        self.stop_service()


def smoke_check(report_path):
    """成品自检；报告同目录隔离数据，失败也更新报告，绝不借用真实草稿。"""
    import tempfile
    import traceback
    import uuid
    from datetime import datetime, timezone

    report_path = Path(report_path).resolve()
    timestamp = lambda: datetime.now(timezone.utc).isoformat()
    report = {'通过': False, '状态': '运行中', '运行编号': uuid.uuid4().hex,
              '开始时间': timestamp(), '真实录音及外部填入': '未执行', '检查': []}
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # 先使历史成功报告失效，之后才创建目录／窗口。
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        # 显式指定用户选定的报告目录，不依赖系统 TEMP 探测或共享真实数据。
        with tempfile.TemporaryDirectory(prefix='TalkMan-selfcheck-', dir=report_path.parent) as directory:
            _smoke_check_ui(Path(directory), report_path, report)
        report['通过'] = True
    except Exception:
        report['错误'] = traceback.format_exc()
    report['状态'] = '完成' if report['通过'] else '失败'
    report['结束时间'] = timestamp()
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        return 1
    return 0 if report['通过'] else 1


def _smoke_check_ui(directory, report_path, report):
    from websockets.sync.client import connect
    from talkman_app import TalkManApp
    from PIL import ImageGrab

    app = None
    def checked(condition, name):
        if not condition: raise AssertionError(name)
        report['检查'].append(name)
    def pump_until(predicate, seconds=4):
        deadline = time.monotonic()+seconds
        while not predicate() and time.monotonic()<deadline:
            app.window_controller.tick(reschedule=False)
            app.update()
            time.sleep(.02)
        checked(predicate(), '等待界面与状态同步')
    def capture(name, window=None):
        window=window or app
        app.update_idletasks()
        window.lift()
        app.update()
        previous_topmost = window.attributes('-topmost')
        window.attributes('-topmost', True)
        window.lift()
        window.update_idletasks()
        time.sleep(.1)
        import ctypes as C
        from ctypes import wintypes as W
        user=C.WinDLL('user32')
        user.SetThreadDpiAwarenessContext.argtypes=[C.c_void_p]
        user.SetThreadDpiAwarenessContext.restype=C.c_void_p
        previous=user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        try:
            user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
            hwnd=user.GetAncestor(window.winfo_id(),2)
            rect=W.RECT();point=W.POINT()
            user.GetClientRect(hwnd,C.byref(rect));user.ClientToScreen(hwnd,C.byref(point))
            ImageGrab.grab(bbox=(point.x,point.y,point.x+rect.right,point.y+rect.bottom)).save(report_path.parent/name)
        finally:
            user.SetThreadDpiAwarenessContext(previous)
            window.attributes('-topmost', previous_topmost)
    try:
        app = TalkManApp(Path(directory), desktop_services=False)
        app.geometry('900x700+60+60')
        app.set_text('先把想法放在这里。\n\n用 COLOPLAY 录入，在电脑上阅读、选择和修改。\n确认内容后，长按旋钮填入目标输入框。')
        app.global_voice_var.set(False)
        app.shortcuts['global_show']=''
        # 所有动作分批验证两个空闲键，避开现有用户实例的全部设备绑定。
        from talkman_global_hotkeys import GlobalHotkeys
        probe_keys=GlobalHotkeys(True)
        try:
            actions=list(ACTIONS)
            for offset in range(0,len(actions),2):
                batch={'coloplay_'+action:f'Control+Alt+F{i+11}'
                       for i,action in enumerate(actions[offset:offset+2])}
                registered=set()
                probe_keys.configure(batch)
                deadline=time.monotonic()+4
                while time.monotonic()<deadline and registered!=set(batch):
                    for kind,value in probe_keys.poll():
                        if kind=='registered': registered=set(value)
                    time.sleep(.02)
                checked(registered==set(batch),'设备动作分批注册 '+str(offset+1))
        finally: probe_keys.close()
        app.window_controller.hotkeys.enabled=False
        device=app.coloplay
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
        check_bindings=dict(DEFAULT_BINDINGS)
        device.configure(dict(device.config,host='127.0.0.1',port=port,connection_mode='wifi',bindings=check_bindings))
        device.registered=set(device.bindings())
        checked(len(device.registered)==len(ACTIONS),'全部设备动作离线预约校验就绪，不投递系统按键')
        from talkman_version import FULL_VERSION, version_record
        checked(FULL_VERSION in version_record(),'界面与打包共用版本记录')
        checked(app.theme_key=='matrix','新安装默认骇客绿屏')
        original_text=app.current_text()
        checked(app.set_theme('dark') and app.current_text()==original_text,'切换 Nothing 黑白保留正文')
        checked(json.loads(app.ui_path.read_text('utf-8'))['appearance_theme']=='dark','主题选择独立持久化')
        app.set_theme('matrix')
        capture('TalkMan-COLOPLAY桌面-独立模式.png')
        with connect(f'ws://127.0.0.1:{port}') as ws:
            ws.send(json.dumps(dict(type='hello',version=1,token=device.config['token'],config=config_id(device.config))))
            pump_until(lambda: device.active)
            checked(all(not w.winfo_manager() for w in app.voice_hint_widgets),'配对后收起重复提示')
            checked(not app.full_voice_dock.winfo_manager() and not app.one_filler_button.winfo_manager(),
                    '设备连接后收起录音／覆盖、指令和 AI 重复按钮')
            checked(app.topmost_var.get(),'设备模式置顶')
            capture('TalkMan-COLOPLAY桌面-设备模式.png')
            device.save_preferences({**device.preferences,'hide_device_buttons':False})
            checked(app.full_voice_dock.winfo_manager()=='grid' and app.one_filler_button.winfo_manager()=='pack',
                    '关闭隐藏开关立即恢复桌面按钮')
            device.save_preferences({**device.preferences,'hide_device_buttons':True})
            base = app.current_text()
            result = base.replace('想法', '灵感')
            app.review_result(base, result, 'AI 整理', lambda: app.apply_ai_result(base, result, 'AI 整理'))
            checked(device.snapshot()['labels']['ai'] == '确认', '改写对照时左上提示确认')
            device.arm(dict(action='ai', id='selfcheck-review'))
            device.dispatch('coloplay_ai')
            checked(app.current_text() == result and app.ai_review is None, '左上动作确认并应用离线改写')
            app.undo()
            checked(app.current_text() == base, '改写确认后可撤销回原文')
            ws.send(json.dumps(dict(type='exit')))
            pump_until(lambda: not device.active)
            checked(all(w.winfo_manager()=='pack' for w in app.voice_hint_widgets),'设备退出后恢复提示')
            checked(app.full_voice_dock.winfo_manager()=='grid' and app.one_filler_button.winfo_manager()=='pack',
                    '设备退出后恢复录音和 AI 按钮')
            checked('先把想法' in app.current_text(),'退出保留编辑正文与电脑客户端')
        device.export(Path(directory)/'TalkMan-卡带导出')
        checked((Path(directory)/'TalkMan-卡带导出/colox_ws.lua').is_file(),'成品内完整包含卡带与官方网络库')
        from talkman_coloplay_update import CartridgeDownload
        import urllib.request
        download=CartridgeDownload('127.0.0.1')
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(download.offer['url'],timeout=2) as response:payload=response.read()
            checked(hashlib.sha1(payload).hexdigest()==download.offer['sha1'],
                    '成品附带通用更新 ROM，回环下载摘要一致')
        finally:download.close()
        from talkman_coloplay_usb import state_packet, button_message
        import libusb_package
        checked(libusb_package.get_libusb1_backend() is not None,'成品包含可加载的 USB 运行库')
        app.open_settings('coloplay')
        settings=app.settings_window
        controls=app.coloplay_preferences_controls
        checked(len(controls['values'])==5,'成品包含五个设备开关，目标由松开右下选定')
        controls['values']['reverse_rotation'].set(True)
        controls['save']()
        checked(device.preferences['reverse_rotation'] and 'target_delay_ms' not in device.preferences,'设备设置保存后立即生效')
        checked(json.loads(device.path.read_text('utf-8'))['preferences']==device.preferences,'设备设置可持久化并保留配对')
        controls['restore']();controls['save']()
        checked(not device.preferences['reverse_rotation'],'恢复默认偏好不改变实体按键语义')
        capture('TalkMan-COLOPLAY设备设置.png',settings)
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        entry=next(w for w in children(settings) if 'text' in w.keys() and w.cget('text')=='设备模拟器')
        entry.invoke()
        sim=device.simulator
        app.update()
        checked(not settings.winfo_exists() and not device.paused(),'设置中的模拟器入口关闭设置并直接可用')
        checked(not any('text' in w.keys() and w.cget('text')=='设备模拟器'
                        for w in children(app) if w.winfo_toplevel()==app),'主窗口不再显示模拟器按钮')
        capture('TalkMan-COLOPLAY桌面模拟器.png',sim.win)
        checked(sim.photo.width()==240,'模拟器实际渲染同源 240×240 圆屏')
        app.text.focus_force();app.update()
        app.text.tag_remove('sel','1.0','end');app.text.mark_set('insert','1.0')
        device.editor_focus.enter(reset=True)
        sim.session.feed(16,100);sim.session.feed(0,200)
        sim.rotate(1)
        checked(app.get_selection()[2]==app.current_text()[:1] and device.target_due==0,
                '编辑聚焦旋钮选字，不切换目标')
        app.full_header.event_generate('<ButtonPress-1>',x=2,y=2)
        checked(not device.editor_focus.active and device.semantic('next')=='move_next',
                '点击编辑区外结束选框，旋钮恢复移动光标')
        app.text.tag_remove('sel','1.0','end')
        nav=dict(ready=True,phase='idle',target_view=True,target='千问',target_index=6,
                 target_count=7,target_page_names=['Gemini','千问','豆包'],
                 target_page_start=5,target_page=2,target_pages=2)
        frame=sim.session.runtime.render(nav,1000)
        checked(frame.getpixel((130,54))==(92,255,139),'成品四角表盘高亮第六个目标的右上角')
        rows=sim.session.runtime.text_layout
        checked(any(row['text']=='千问' for row in rows) and any('2/2 页' in row['text'] for row in rows),
                '成品圆屏显示目标名称和第二页序号')
        checked(state_packet(sim.session.display_state(),1,device.config)[:4]==b'CXUS',
                '成品可生成 USB 状态帧')
        app.text.tag_remove('sel','1.0','end');app.text.mark_set('insert','end-1c')
        before=app.current_text()
        sim.canvas.event_generate('<ButtonPress-1>',x=440,y=145)
        sim.canvas.event_generate('<ButtonRelease-1>',x=440,y=145)
        pump_until(lambda:app.current_text()==before[:-1])
        checked(app.current_text()==before[:-1],'模拟器右上删除光标前一个字')
        app.undo();checked(app.current_text()==before,'模拟器删除可以撤销')
        sim.canvas.event_generate('<ButtonPress-1>',x=440,y=145,state=1)
        sim.canvas.event_generate('<ButtonRelease-1>',x=440,y=145,state=1)
        pump_until(lambda:app.current_text()=='')
        checked(app.current_text()=='','模拟器 Shift＋右上清空整个编辑区')
        app.undo();checked(app.current_text()==before,'组合清空可以一步撤销')
        sim.canvas.event_generate('<ButtonPress-1>',x=80,y=145,state=1)
        sim.canvas.event_generate('<ButtonRelease-1>',x=80,y=145,state=1)
        checked(device.hidden,'模拟器右下加左上收起主窗口')
        checked(sim.win.winfo_exists(),'主窗口收起后模拟器仍可用')
        sim.close()
        device.show_guide();app.update()
        checked(app.guide.opened and device.paused(), '窗内按键教程暂停实体操作')
        capture('TalkMan-COLOPLAY按键教程.png')
        app.guide.show(2);app.update()
        app.guide.open_preview(1);app.update()
        preview=app.guide.preview
        preview.toggle();preview.index=1;preview.render();app.update()
        checked(preview.winfo_ismapped() and len(preview.canvas.find_all())>10,
                '成品教程可显示按键与桌面选区动画')
        capture('TalkMan-COLOPLAY悬停教程.png')
        app.guide.show(5);app.update()
        checked(preview.closed and preview.job is None, '成品教程翻页停止预览动画')
        capture('TalkMan-COLOPLAY发送教程.png')
        app.guide.close()
        checked(app.ui_preferences.get('coloplay_guide_version') == 1, '教程完成状态可保存，设置中可重看')
        checked(not device.hidden and device.local_session is None,'关闭模拟器恢复主窗口与设备连接入口')
    finally:
        if app:
            app.close_app()
