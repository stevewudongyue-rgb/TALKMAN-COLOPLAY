-- USB 状态、配对按键及显式录音。画面由卡带本地绘制。
function TMUsbTransport(cfg)
 local json=require('cjson_safe')
 local u={active=false,seq=0,last=-999,retry_at=0,poll_at=0,hello_at=-999,oldmask=-1,frame=-1,exited=false,
          gesture=TMUpdateControls(cfg),audio=nil,closed={},state={},received=-999}
 function u:reply(action,s)
  if airdata_send then
   airdata_send(18,json.encode({protocol='TM_AUDIO_1',action=action,session=s.id,tag=s.tag}),action=='begin' and 0 or 1,0,0)
  end
 end
 function u:stop_audio()
  local s=self.audio
  if s then
   self.audio=nil;self.closed[#self.closed+1]=s.id
   if #self.closed>8 then table.remove(self.closed,1) end
   if micpoll then pcall(function() micpoll(1) end) end
   self:reply('stopped',s)
  end
 end
 function u:remember(id)
  for _,known in ipairs(self.closed) do if known==id then return end end
  self.closed[#self.closed+1]=id
  if #self.closed>8 then table.remove(self.closed,1) end
 end
 function u:audio_step(t)
  if not airdata_get or not airdata_send or not airdata_ready then return end
  if not self.active or not airdata_ready() then self:stop_audio();return end
  for _=1,8 do
   local d,ty=airdata_get(1)
   if d==nil then break end
   if ty==18 and type(d)=='string' and #d<=1024 then
    local m=json.decode(d)
    if type(m)=='table' and m.protocol=='TM_AUDIO_1' and cfg.token~='' and
       m.token==cfg.token and m.config==cfg.config and type(m.session)=='string' and
       #m.session==32 and type(m.tag)=='number' and m.tag%1==0 and m.tag>0 and m.tag<65536 then
     local same=self.audio and self.audio.id==m.session and self.audio.tag==m.tag
     if m.action=='stop' then
      if same then self:stop_audio() else self:remember(m.session) end
     elseif same and (m.action=='start' or m.action=='keep') then
      self.audio.deadline=t+1500
      if m.action=='start' then self:reply('started',self.audio) end
     elseif m.action=='start' and not self.audio then
      local closed=false
      for _,id in ipairs(self.closed) do if id==m.session then closed=true end end
      if not closed then
       local s={id=m.session,tag=m.tag,deadline=t+1500,began=t}
       local ok,result=pcall(function()
        if not usemod('mic') or not micpoll then return false end
        if statctl then statctl('oers',16000);statctl('oedm',60);statctl('orst',0) end
        micpoll(1);return true
       end)
       if ok and result then self.audio=s;self:reply('started',s);self:reply('begin',s)
       else self:reply('error',s) end
      end
     end
    end
   end
  end
  local s=self.audio
  if s and (t>=s.deadline or t-s.began>=120000) then self:stop_audio();return end
  if s then
   local ok,d=pcall(function() return micpoll(3,960,3) end)
   if not ok then self:reply('error',s);self:stop_audio();return end
   if type(d)=='string' and #d>=3 then
    -- tag=0 为官方16k默认档；sub为本轮标识，队列seq检测丢包。
    if not airdata_send(16,d,0,0,s.tag) then self:reply('error',s);self:stop_audio();return end
   end
  end
 end
  function u:send(mask,leave)
   if not airdata_send or not airdata_ready or not airdata_ready() then return false end
   local next_seq=self.seq+1
   local data=json.encode({v=1,token=cfg.token,config=cfg.config,
    seq=next_seq,mask=mask,frame=self.frame,exit=leave==true,device_build=TM_DEVICE_BUILD})
   if type(data)~='string' or #data>512 or not airdata_send(19,data,1,0,0) then return false end
   self.seq=next_seq
   return true
 end
 function u:step(t,force)
  -- 未就绪时不触碰固件队列；拔线后同时丢弃旧状态，等待新的配对帧。
  if not airdata_ready or not airdata_ready() then
   if self.active or self.frame>=0 then
    self:stop_audio();self.active=false;self.frame=-1;self.received=-999;self.state={}
    self.gesture=TMUpdateControls(cfg);self.oldmask=-1;self.retry_at=0;self.poll_at=0
   end
   return false
  end
  -- USB ready 只代表固件链路存在，不代表有电脑程序消费队列。
  -- 离线只低频读探测包；必须由已配对桌面主动握手，不主动写唤醒包。
  local hello=false
  if airdata_get and (self.active or t>=self.poll_at) then
   self.poll_at=t+100
   for _=1,8 do
    local d,ty=airdata_get(0)
    if d==nil then break end
    if ty==21 and type(d)=='string' and #d<=512 then
     local m=json.decode(d)
     if type(m)=='table' and m.protocol=='TM_USB_HELLO_1' and m.v==1 and
        cfg.token~='' and m.token==cfg.token and m.config==cfg.config then hello=true end
    elseif ty==20 and type(d)=='string' and #d<=4096 then
     local m=json.decode(d)
     if type(m)=='table' and m.v==1 and cfg.token~='' and
        m.token==cfg.token and m.config==cfg.config and type(m.state)=='table' and
        type(m.frame)=='number' and m.frame%1==0 and m.frame>=0 and m.frame<65536 then
      self.state=m.state;self.frame=m.frame;self.received=t
     end
    end
   end
  end
  local before=self.active
  self.active=self.frame>=0 and t-self.received<1500 and not self.exited
  if before~=self.active then
   self.gesture=TMUpdateControls(cfg);self.oldmask=-1
  end
  if not self.active then
   self:stop_audio()
   if self.frame>=0 then self.frame=-1;self.received=-999;self.state={} end
   -- 每个有效探测最多应答一次；失败或队列忙时等待电脑下一次探测。
   if hello and not self.exited and t-self.hello_at>=250 then
    self.hello_at=t
    local held=false
    for i=0,7 do if btn(i) then held=true end end
    local queued=airdata_depth and airdata_depth('tx',1) or 0
    if not held and (type(queued)~='number' or queued==0) then self:send(0,false) end
   end
   return false
  end
  local buttons={};local mask=0
  for i=0,7 do buttons[i]=btn(i);if buttons[i] then mask=mask+2^i end end
  for _,action in ipairs(self.gesture:step(buttons,t)) do
   if action=='exit' then
    self:stop_audio();self:send(0,true);self.exited=true;self.active=false;toidle();return true
   end
  end
   if t>=self.retry_at and (mask~=self.oldmask or t-self.last>=50) then
    if self:send(mask,false) then self.oldmask=mask;self.last=t;self.retry_at=0
    else self.retry_at=t+50 end
  end
  self:audio_step(t)
  return true
 end
 function u:draw()
  if not self.active then return false end
  local m=self.state
  m.now=stat(12);m.configured=true;m.device_build=TM_DEVICE_BUILD
  m.buttons={};for i=0,7 do m.buttons[i]=btn(i) end
  m.modifier=m.buttons[3];m.ul=self.gesture.ul;m.exit_ms=cfg.exit_ms
  m.hold_short=self.gesture.knob~=nil and m.now-self.gesture.knob<cfg.fill_ms
  m.fill_progress=self.gesture.knob~=nil and m.pending and
   math.min(1,math.max(0,(m.now-self.gesture.knob)/cfg.fill_ms)) or nil
  m.fill_cancelled=self.gesture.knob~=nil and self.gesture.cancelled
  m.send_current=self.gesture.knob~=nil and self.gesture.send_combo and not self.gesture.cancelled
  m.inactive=self.exited
  TMDraw(m);return true
 end
 function u:resume()
  self.exited=false;self.gesture=TMUpdateControls(cfg);self.oldmask=-1;self.last=-999;self.retry_at=0;self.poll_at=0;self.hello_at=-999
  self.active=false;self.received=-999;self.frame=-1;self.state={}
 end
 return u
end
