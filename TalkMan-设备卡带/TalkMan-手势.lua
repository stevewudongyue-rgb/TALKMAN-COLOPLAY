-- 纯状态机；时间由宿主注入，单位毫秒。按住不重复触发。
function TMGesture(config)
 local g={prev={}, armed=false, exited=false, ul=nil, knob=nil, cancelled=false, boot=true,
          target_turned=false,send_combo=false}
 function g:step(buttons,now)
  local out={}
  local function emit(a) out[#out+1]=a end
  local function down(i) return buttons[i]==true end
  local function rise(i) return down(i) and not self.prev[i] end
  local function fall(i) return not down(i) and self.prev[i] end
  if self.boot then
   local held=false
   for i=0,6 do if down(i) then held=true end end
   if not held then self.boot=false end
  elseif not self.exited then
   if rise(3) then self.target_turned=false end
   if rise(0) then
    self.target_turned=false
    if down(3) and self.prev[3] then self.ul=nil;emit('toggle')
    else self.ul=now;emit('arm_ai') end
   end
   if down(0) and self.ul and now-self.ul>=config.exit_ms then
    self.exited=true; self.ul=nil; emit('exit')
   elseif fall(0) and self.ul then self.ul=nil; emit('ai') end
   if not self.exited then
    -- 先按住右下再按右上才清空；同帧按下不升级为清空，按住不重复。
    if rise(1) then self.target_turned=false;emit(down(3) and self.prev[3] and 'clear' or 'cancel_delete')
    elseif rise(2) then
     self.target_turned=false
     emit(down(3) and self.prev[3] and 'rewrite' or 'record')
    end
    if rise(config.center) then
     self.send_combo=down(3) and self.prev[3] and not self.target_turned
     self.knob=now; self.cancelled=down(3) and not self.send_combo
     self.target_turned=false
     emit(self.cancelled and 'cancel_arm' or self.send_combo and 'arm_send_current' or 'arm_knob')
    end
    if self.knob and not self.cancelled and
       ((down(3) and not self.send_combo) or (self.send_combo and (not down(3) or now-self.knob>=config.fill_ms))) then
     self.cancelled=true;emit('cancel_arm')
    end
    if rise(config.left) or rise(config.right) then
     if self.knob then
      -- 按住时转动只取消本次填入；直到松开都不切换目标。
      if not self.cancelled then self.cancelled=true; emit('cancel_arm') end
     elseif rise(config.left)~=rise(config.right) then
      if down(3) then self.target_turned=true;emit(rise(config.left) and 'target_previous' or 'target_next')
      else emit(rise(config.left) and 'previous' or 'next') end
     end
    end
    if fall(config.center) and self.knob then
     if not self.cancelled then
      if self.send_combo then emit('send_current')
      elseif now-self.knob>=config.fill_ms then emit('fill')
      else emit('knob') end
     end
     self.knob=nil
    end
    if fall(3) and self.target_turned then self.target_turned=false;emit('target_commit') end
   end
  end
  self.prev={}
  for i=0,7 do self.prev[i]=down(i) end
  return out
 end
 return g
end
