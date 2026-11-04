-- USB 图流、配对按键及显式录音。没有 start 命令时不加载 mic、不上传音频。
function TMUsbTransport(cfg)
 local json=require('cjson_safe')
 local u={active=false,seq=0,last=-999,oldmask=-1,frame=-1,exited=false,
          gesture=TMUpdateControls(cfg),audio=nil,closed={}}
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
  self.seq=self.seq+1
  svcreq('ex','usb:tx:TMCP1 '..json.encode({v=1,token=cfg.token,config=cfg.config,
   seq=self.seq,mask=mask,frame=self.frame,exit=leave==true,device_build=TM_DEVICE_BUILD})..'\n')
 end
 function u:step(t,force)
  local fresh=false
  if img_airinfo then
   local w,h,id,sz,age=img_airinfo()
   if w==240 and h==240 and age and age<1500 then fresh=true;self.frame=id end
  end
  local before=self.active
  self.active=(fresh or force==true) and not self.exited
  if before~=self.active then
   self.gesture=TMUpdateControls(cfg);self.oldmask=-1
  end
  if not self.active then
   self:stop_audio()
   -- 重开卡带但主机已停图时，零按键报文只唤醒图流，不执行操作。
   if not self.exited and airdata_ready and airdata_ready() and t-self.last>=250 then
    local held=false
    for i=0,7 do if btn(i) then held=true end end
    if not held then self.frame=-1;self:send(0,false);self.last=t end
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
  if mask~=self.oldmask or t-self.last>=50 then
   self:send(mask,false);self.oldmask=mask;self.last=t
  end
  self:audio_step(t)
  return true
 end
 function u:draw()
  if self.active and img_scrair then return img_scrair() end
  return false
 end
 function u:resume()
  self.exited=false;self.gesture=TMUpdateControls(cfg);self.oldmask=-1;self.last=-999
 end
 return u
end
