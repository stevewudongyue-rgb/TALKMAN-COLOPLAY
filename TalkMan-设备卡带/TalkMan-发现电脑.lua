-- 只使用公开 UDP API；先验证配对口令证明，再向目标发送 WebSocket 握手。
local DISCOVERY_PROTOCOL='TMCP_DISCOVER_1'
local function hex(s)
 return (s:gsub('.',function(c) return string.format('%02x',string.byte(c)) end))
end
function TMDiscoveryProof(token,nonce,config,host,port)
 if #token>64 then token=sha1sum(token) end
 token=token..string.rep('\0',64-#token)
 local inner,outer={},{}
 for i=1,64 do
  inner[i]=string.char(string.byte(token,i) ~ 0x36)
  outer[i]=string.char(string.byte(token,i) ~ 0x5c)
 end
 local body=table.concat({DISCOVERY_PROTOCOL,nonce,config,host,tostring(port)},'\n')
 return hex(sha1sum(table.concat(outer)..sha1sum(table.concat(inner)..body)))
end
local function safe_host(host)
 if type(host)~='string' then return false end
 local a,b,c,d=host:match('^(%d+)%.(%d+)%.(%d+)%.(%d+)$')
 if not a then return false end
 a,b,c,d=tonumber(a),tonumber(b),tonumber(c),tonumber(d)
 return a>0 and a<224 and b<256 and c<256 and d<256
end
local function same(a,b)
 if type(a)~='string' or #a~=#b then return false end
 local diff=0
 for i=1,#a do diff=diff | (string.byte(a,i) ~ string.byte(b,i)) end
 return diff==0
end
function TMDiscovery(cfg)
 local json=require('cjson_safe')
 local self={sock=nil,url=nil,until_at=0,query=nil,retry_at=0,last_query=-9999}
 function self:close()
  if self.sock then pcall(function() self.sock:close() end) end
  self.sock=nil;self.query=nil;self.url=nil
 end
 function self:step(t,connected)
  if cfg.discovery~=true or not net_bind or not sha1sum then return end
  if not self.sock and t>=self.retry_at then
   self.retry_at=t+3000
   self.sock=net_bind('udp','0.0.0.0',cfg.discovery_port or 18744)
  end
  if not self.sock then return end
  if self.query and t>self.query.until_at then self.query=nil end
  -- 每帧最多处理四条报文；持续广播不能饿死按键、画面和心跳。
  for _=1,4 do
   local raw,addr,port=self.sock:recvfrom(1025)
   if raw==nil then self:close();self.retry_at=t+3000;return end
   if raw==false then break end
   if not connected and type(raw)=='string' and #raw<=1024 and safe_host(addr) then
    local v=json.decode(raw)
    if type(v)=='table' and v.protocol==DISCOVERY_PROTOCOL and v.config==cfg.config then
     if v.type=='beacon' and port==(cfg.discovery_port or 18744) and not self.query and t-self.last_query>=750 then
      local nonce={}
      for i=1,16 do nonce[i]=string.format('%02x',math.random(0,255)) end
      nonce=table.concat(nonce)
      self.last_query=t
      self.query={nonce=nonce,host=addr,port=port,until_at=t+2000}
      self.sock:sendto(json.encode({protocol=DISCOVERY_PROTOCOL,type='discover',config=cfg.config,nonce=nonce}),addr,port)
     elseif v.type=='endpoint' and self.query and v.nonce==self.query.nonce and addr==self.query.host and port==self.query.port
       and v.host==addr and type(v.port)=='number' and v.port%1==0 and v.port>=1024 and v.port<=65535
       and same(v.proof,TMDiscoveryProof(cfg.token,v.nonce,cfg.config,v.host,v.port)) then
      self.url='ws://'..v.host..':'..v.port;self.until_at=t+4000;self.query=nil
     end
    end
   end
  end
 end
 function self:take(t)
  local result=self.until_at>=t and self.url or nil
  self.url=nil
  return result
 end
 return self
end
