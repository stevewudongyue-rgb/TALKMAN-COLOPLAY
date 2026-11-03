-- 三键更新独占；延后右上/左下动作 180ms，容纳手指先后落下的时间。
-- 原手势仍按真实时间采样，旋钮移动和长按计时不增加延迟。
function TMUpdateControls(config)
 local base=TMGesture(config)
 local g=setmetatable({base=base,queue={},prev={},boot=true,blocked=false,fired=false}, {__index=base})
 function g:step(b,t,context)
  if context=='opening' then context='recording' end
  local out={}
  local all=b[1] and b[2] and b[3] and not b[0] and not b[4]
  local any=b[1] or b[2] or b[3]
  if self.boot then
   if not any and not b[0] and not b[4] then self.boot=false end
   self.base:step(b,t);return out
  end
  if all and not self.blocked then
   self.blocked=true;self.started=t;self.fired=false;self.queue={}
   self.base=TMGesture(config);self.base.boot=false
   setmetatable(self,{__index=self.base})
   out[#out+1]='update_hold'
  end
  if self.blocked then
   if not all then self.started=nil end
   if self.started and t-self.started>=5000 and not self.fired then
    self.fired=true;out[#out+1]='cartridge_update'
   end
   if not any and not b[0] and not b[4] then
    self.blocked=false;self.started=nil;self.base.prev={}
   end
   return out
  end
  for _,a in ipairs(self.base:step(b,t)) do
   if a=='record' or a=='rewrite' or a=='cancel_delete' or a=='clear' then
    self.queue[#self.queue+1]={action=a,due=t+180,context=context,key=(a=='record' or a=='rewrite') and 2 or 1}
   else out[#out+1]=a end
  end
  -- 已松开的短点按不可能再构成三键长按；立即执行，连续点按不会丢结束。
  while #self.queue>0 and (self.queue[1].due<=t or not b[self.queue[1].key]) do
   local item=table.remove(self.queue,1)
   if item.context==context then out[#out+1]=item.action end
  end
  return out
 end
 return g
end
