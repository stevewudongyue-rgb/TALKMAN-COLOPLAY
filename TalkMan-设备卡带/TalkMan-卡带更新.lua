-- 官方 cartdata/http/bios API；不覆盖原卡带，不改整机固件。
function TMCartridgeUpdate(cfg,build,io)
 local json=require('cjson_safe')
 local u={phase='idle',message='',serial=0,stored={},available=false}
 local function valid(n) return type(n)=='number' and n%1==0 and n>0 and n<=100000 end
 local function record(r)
  return type(r)=='table' and valid(r.build) and type(r.sha1)=='string' and
   #r.sha1==40 and r.sha1:match('^%x+$') and type(r.size)=='number' and
   r.size%1==0 and r.size>0 and r.size<=184*1024
 end
 local function filename(r) return 'TalkMan_U'..tostring(math.floor(r.build))..'.rom' end
 local function read_store()
  if dget(0)~=1414352945 then return {} end
  local len=dget(1)
  if len<42 or len>1016 or len%1~=0 then return {} end
  local bytes={}
  for i=0,math.ceil(len/4)-1 do
   local n=dget(i+2);if n<0 then n=n+4294967296 end
   for j=0,3 do bytes[#bytes+1]=string.char(math.floor(n/256^j)%256) end
  end
  local s=table.concat(bytes):sub(1,len);local body=s:sub(41)
  if sha1sumhexa(body)~=s:sub(1,40) then return {} end
  return json.decode(body) or {}
 end
 function u:save()
  if not self.available then return false end
  local body=json.encode(self.stored);if not body then return false end
  local s=sha1sumhexa(body)..body
  if #s>1016 then return false end
  local ok,result=pcall(function()
   dset(0,0);dset(1,#s)
   for i=0,math.ceil(#s/4)-1 do
    local n=0
    for j=0,3 do n=n+(s:byte(i*4+j+1) or 0)*256^j end
    if n>=2147483648 then n=n-4294967296 end
    dset(i+2,n)
   end
   dset(0,1414352945);return dflush()
  end)
  return ok and result~=false
 end
 function u:fail(message,t)
  self.phase='error';self.message=message;self.until_time=t+5000;self.switching=false
 end
 function u:init(t)
  local ok,result=pcall(function() return cartdata('TalkMan_COLOPLAY') end)
  self.available=ok and result~=false
  if not self.available then return end
  ok,result=pcall(read_store)
  self.stored=ok and type(result)=='table' and result or {}
  if cfg.token~='' then
   -- 手动安装的配对卡带权威；换电脑会清除旧升级重定向。
   local old=self.stored.pair
   if type(old)~='table' or old.token~=cfg.token or old.config~=cfg.config then self.stored={} end
   local pair={}
   for _,key in ipairs({'url','token','config','discovery','discovery_port','keys'}) do pair[key]=cfg[key] end
   self.stored.pair=pair
   if not self:save() then self.available=false;return end
  elseif type(self.stored.pair)=='table' and type(self.stored.pair.token)=='string' then
   for _,key in ipairs({'url','token','config','discovery','discovery_port','keys'}) do
    if self.stored.pair[key]~=nil then cfg[key]=self.stored.pair[key] end
   end
  else self.available=false;return end
  -- 只自动进入已连上电脑验证过的版本；失败的 pending 永不自动重试。
  local active=self.stored.active
  if record(active) and active.build>build then
   self.offer=active;self.boot_redirect=true;self:read_file(t)
  end
 end
 function u:connected(t)
  local r=self.stored.pending
  if record(r) and r.build==build then
   self.stored.active=r;self.stored.pending=nil
   if self:save() then self.phase='done';self.message='卡带已更新';self.until_time=t+5000 end
  end
 end
 function u:check(t,ready,phase)
  if self:busy() then return end
  if not ready then self:fail('请先连接电脑 Wi-Fi',t);return end
  if phase~='idle' then self:fail('请先结束编辑操作',t);return end
  if not self.available then self:fail('无法保存配对，请手动更新',t);return end
  self.boot_redirect=false
  self.serial=self.serial+1;self.id='u'..tostring(self.serial)
  self.phase='checking';self.message='检查电脑附带版本';self.deadline=t+12000
  io.send({type='cartridge_check',id=self.id})
 end
 function u:busy() return self.phase=='checking' or self.phase=='downloading' or self.phase=='verifying' or self.phase=='switching' end
 function u:http(method,t)
  self.sn=(self.sn or 0)+1
  local req={stat=0,sn=self.sn,method=method,filename=filename(self.offer),filemode='wb',
   filerl=self.offer.size+1,limitlen=self.offer.size+1,timeout=45000,conntm=5000,
   url=method==2 and self.offer.url or '',ret=0,rdata=''}
  if svcset('talkman','update',req)==false or svcreq('http','talkman.update')==false then
   self:fail('固件不支持，请手动更新',t);return
  end
  self.deadline=t+50000
 end
 function u:read_file(t)
  self.phase='verifying';self.message='校验卡带文件';self:http(3,t)
 end
 function u:step(t,state)
  if (self.phase=='done' or self.phase=='error') and t>=(self.until_time or 0) then self.phase='idle' end
  if not self:busy() then return end
  if self.phase=='switching' then return end
  if t>self.deadline then self:fail('更新超时，请升级电脑端后重试',t);return end
  if self.phase=='checking' then
   local r=state.cartridge_update
   if type(r)~='table' or r.id~=self.id then return end
   if r.status=='current' then self.phase='done';self.message='已是电脑附带版本';self.until_time=t+5000
   elseif r.status=='error' then self:fail('电脑未就绪，请检查设置',t)
   elseif r.status=='ready' then
    if not record(r) or r.build<=build or type(r.url)~='string' or
     not r.url:match('^http://[%d%.]+:%d+/cartridge/%x+/TalkMan_update%.rom$') then self:fail('更新信息无效',t);return end
    self.offer=r;self.phase='downloading';self.message='正在下载卡带';self:http(2,t)
   end
  elseif self.phase=='downloading' or self.phase=='verifying' then
   local r=svcget('talkman.update')
   if type(r)~='table' or r.sn~=self.sn or r.stat~=3 then return end
   if type(r.ret)~='number' or r.ret<0 then self:fail('文件读写失败，请手动更新',t);return end
   if self.phase=='downloading' then self:read_file(t);return end
   if type(r.rdata)~='string' or #r.rdata~=self.offer.size or sha1sumhexa(r.rdata)~=self.offer.sha1 then
    self:fail('卡带校验失败，保留原版',t);return
   end
   if not self.boot_redirect then
    self.stored.pending={build=self.offer.build,size=self.offer.size,sha1=self.offer.sha1}
    if not self:save() then self:fail('配对保存失败，保留原版',t);return end
   end
   self.phase='switching';self.message='正在切换新卡带';self.switching=true
   io.disconnect()
   if self.boot_redirect and svcreq('bios','jump:'..filename(self.offer)) then return end
   local ok,result=pcall(function() return svcreq('bios','load:'..filename(self.offer)) end)
   if not ok or result~=true then self:fail('无法加载，请重开原卡带',t);return end
   -- load 只加载实例；jump 切换前台。使用同一个合法文件名。
   svcreq('bios','jump:'..filename(self.offer))
  end
 end
 return u
end
