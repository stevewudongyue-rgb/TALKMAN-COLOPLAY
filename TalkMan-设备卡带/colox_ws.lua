-------------------------------------------------------------------------------
-- coloX net WebSocket client (ws:// and wss://, client role, pure Lua)
--
-- Driven by the EXISTING copas scheduler: the cartridge must call
-- copas.step() inside its own loop (typically _draw). This module never
-- starts its own thread/timer.
--
-- wss:// requires the host build to provide TLS sockets, i.e. coloX_net.c
-- compiled with COLOX_USE_NET_TLS; net_connect(host, port, true) then does
-- a non-blocking tcp+tls handshake that the first copas.send drives
-- through. Without TLS support wss:// fails fast with "tls not compiled".
--
-- Public API:
--   ws_connect(url[, opts]) -> ws | nil, err
--     opts.headers  = {name=value,...} extra request headers
--     opts.timeout  = seconds budget for connect/handshake/upgrade io
--   ws:receive()            -> msg | nil, err      (msg={op=, data=})
--   ws:send(data[, kind])   -> ok | nil, err       kind="text"|"binary"
--   ws:send_control(kind[, payload]) -> ok|nil,err  kind="ping"|"pong"
--   ws:close([code[, reason]]) -> ok | nil, err
--   ws:isclosed()           -> bool
--
-- All blocking calls MUST be made from inside a copas coroutine.
-- Return values follow the net three-state convention.
-------------------------------------------------------------------------------

local _M = {}
-- __gc MUST be present on the metatable BEFORE setmetatable() is called in
-- ws_connect: Lua 5.4 marks a table for finalization only if its metatable
-- already has a __gc field at setmetatable time (luaC_checkfinalizer, invoked
-- from lua_setmetatable in lapi.c). Defining __gc later does NOT retroactively
-- mark already-created objects, so the finalizer would never run.
-- (The underlying socket userdata also has its own __gc, so this is a
-- belt-and-suspenders closer.)
local ws_mt = {
  __index = _M,
  __gc = function(t)
    pcall(function() return t.sock and t.sock:close() end)
  end,
}

-- opcode constants
local OP_CONT  = 0x0
local OP_TEXT  = 0x1
local OP_BIN   = 0x2
local OP_CLOSE = 0x8
local OP_PING  = 0x9
local OP_PONG  = 0xA

-- close status codes we send
local CLOSE_NORMAL     = 1000
local CLOSE_PROTOCOL   = 1002
local CLOSE_TOO_BIG    = 1009

-- sane hard cap on a single frame payload (64 MiB) to avoid OOM on a bad len
local MAX_PAYLOAD = 64 * 1024 * 1024

-- magic accept-key GUID
local WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

-------------------------------------------------------------------------------
-- small helpers
-------------------------------------------------------------------------------
local byte = string.byte
local char = string.char
local concat = table.concat
local mrandom = math.random

-- table of name -> opcode (string keys for the public message objects)
local opname = {
  [OP_CONT]  = "continuation",
  [OP_TEXT]  = "text",
  [OP_BIN]   = "binary",
  [OP_CLOSE] = "close",
  [OP_PING]  = "ping",
  [OP_PONG]  = "pong",
}

local function rand_bytes(n)
  local t = {}
  for i = 1, n do t[i] = char(mrandom(0, 255)) end
  return concat(t)
end

-------------------------------------------------------------------------------
-- b64 / sha1:宿主全局函数(C 实现,见 coloX_util.c / coloX_luamath.c 导出)
-------------------------------------------------------------------------------
local b64encode = b64encode
local sha1 = sha1sum

-------------------------------------------------------------------------------
-- tiny URL parser for ws://host[:port][/path] and wss://host[:port][/path]
-- avoids depending on colox_http.lua's url.parse availability.
-- returns host, port, path, scheme (scheme is "ws" or "wss", lowercased)
-------------------------------------------------------------------------------
local function parse_ws_url(u)
  if type(u) ~= "string" then return nil, "invalid url" end
  local scheme, host, port, rest = u:match("^([Ww][Ss][Ss]?)://([^/:]+)(:?%d*)(.*)$")
  if not scheme or not host then
    -- also accept ws://[ipv6]:port/path ? out of scope; require host name
    return nil, "invalid url"
  end
  scheme = scheme:lower()
  if scheme ~= "ws" and scheme ~= "wss" then
    return nil, "only ws:// and wss:// are supported"
  end
  local portnum
  if port and #port > 0 then
    portnum = tonumber(port:sub(2), 10)
    if not portnum then return nil, "invalid port" end
  else
    portnum = (scheme == "wss") and 443 or 80
  end
  local path = rest
  if not path or path == "" then path = "/" end
  if path:sub(1, 1) ~= "/" then path = "/" .. path end
  return host, portnum, path, scheme
end

-------------------------------------------------------------------------------
-- internal: read exactly N bytes off the socket via copas.
-- returns data | nil, err   (err in {"closed","timeout",...})
-- on partial data after close/timeout we cannot resume a half-frame, so we
-- treat any non-success as a hard error.
-------------------------------------------------------------------------------
local function recv_n(self, n)
  if n == 0 then return "" end
  local data, err = copas.receive(self.sock, n, nil, self.timeout)
  if type(data) == "string" and #data == n then
    return data
  end
  -- timeout or closed: surface the error
  if err == nil then err = "closed" end
  return nil, err
end

-------------------------------------------------------------------------------
-- internal: demask / mask helper. mask and demask are the SAME XOR.
-------------------------------------------------------------------------------
local function apply_mask(payload, mask)
  if not mask or #mask ~= 4 then return payload end
  local m0, m1, m2, m3 = byte(mask, 1), byte(mask, 2), byte(mask, 3), byte(mask, 4)
  local n = #payload
  local out = {}
  -- unroll in groups of 4
  local i = 1
  local full = n - (n % 4)
  while i <= full do
    out[#out + 1] = char(
      byte(payload, i) ~ m0,
      byte(payload, i + 1) ~ m1,
      byte(payload, i + 2) ~ m2,
      byte(payload, i + 3) ~ m3)
    i = i + 4
  end
  local rem = n - i + 1
  if rem >= 1 then out[#out + 1] = char(byte(payload, i) ~ m0) end
  if rem >= 2 then out[#out + 1] = char(byte(payload, i + 1) ~ m1) end
  if rem >= 3 then out[#out + 1] = char(byte(payload, i + 2) ~ m2) end
  return concat(out)
end

-------------------------------------------------------------------------------
-- internal: receive one frame off self.sock.
-- returns: frame_table { fin=, op=, data= } | nil, err
-- protocol errors are returned as err strings beginning with "protocol:".
-------------------------------------------------------------------------------
local function recv_frame(self)
  local hdr, err = recv_n(self, 2)
  if not hdr then return nil, err end
  local b0, b1 = byte(hdr, 1), byte(hdr, 2)
  local fin    = (b0 & 0x80) ~= 0
  local rsv    = b0 & 0x70
  local op     = b0 & 0x0F
  local masked = (b1 & 0x80) ~= 0
  -- RFC 6455 §5.1: a server MUST NOT mask any frame; receiving a masked
  -- frame is a protocol error (close 1002). Check before reading any more
  -- bytes off the wire so the framing stays parseable for the close reply.
  if masked then
    return nil, "protocol: server frame masked"
  end
  local len    = b1 & 0x7F

  if rsv ~= 0 then
    return nil, "protocol: reserved bits set"
  end
  -- unknown/control-vs-continuation validity is checked by the caller;
  -- here only reject truly undefined opcodes (3..7, B..F).
  if op >= 3 and op <= 7 then return nil, "protocol: unknown opcode" end
  if op >= 0xB and op <= 0xF then return nil, "protocol: unknown opcode" end

  if len == 126 then
    local ext, e2 = recv_n(self, 2)
    if not ext then return nil, e2 end
    len = (byte(ext, 1) << 8) | byte(ext, 2)
  elseif len == 127 then
    local ext, e2 = recv_n(self, 8)
    if not ext then return nil, e2 end
    -- 64-bit length: top 4 bytes must be zero, and high bit must be clear
    -- (else payload >= 2^31 -- reject as 1009 by caller).
    local hi32 = (byte(ext, 1) << 24) | (byte(ext, 2) << 16)
                | (byte(ext, 3) << 8) | byte(ext, 4)
    local lo32 = (byte(ext, 5) << 24) | (byte(ext, 6) << 16)
                | (byte(ext, 7) << 8) | byte(ext, 8)
    if hi32 ~= 0 or (lo32 & 0x80000000) ~= 0 then
      -- signal oversized specially so caller sends 1009
      return nil, "oversized"
    end
    len = lo32
  end

  -- control frames must be <=125 and unfragmented
  if op >= 0x8 then
    if len > 125 then return nil, "protocol: control frame too large" end
    if not fin then return nil, "protocol: fragmented control frame" end
  end

  if len > MAX_PAYLOAD then
    return nil, "oversized"
  end

  local payload, perr = recv_n(self, len)
  if not payload then return nil, perr end
  -- server->client frames MUST NOT be masked (RFC 6455 §5.1); any masked
  -- frame was already rejected as a protocol error above, so no demask here.

  return { fin = fin, op = op, data = payload }
end

-------------------------------------------------------------------------------
-- internal: build + send a single masked frame (client side).
-- returns: true | nil, err
-------------------------------------------------------------------------------
local function send_frame(self, fin, op, payload)
  if self.closed then return nil, "closed" end
  payload = payload or ""
  local plen = #payload

  -- control frames: payload <=125, never use extended length forms
  if op >= 0x8 and plen > 125 then
    return nil, "protocol: control payload too large"
  end

  local b0 = (fin and 0x80 or 0) | op
  local header
  if plen < 126 then
    header = char(b0, 0x80 | plen)
  elseif plen <= 0xFFFF then
    header = char(b0, 0x80 | 126,
      (plen >> 8) & 0xFF, plen & 0xFF)
  else
    -- 64-bit length form. plen is < 2^31 here (we only accept payloads below
    -- MAX_PAYLOAD=64MiB which is well under 2^31), so high 4 bytes are zero.
    header = char(b0, 0x80 | 127,
      0, 0, 0, 0,
      (plen >> 24) & 0xFF,
      (plen >> 16) & 0xFF,
      (plen >> 8) & 0xFF,
      plen & 0xFF)
  end

  local mask = rand_bytes(4)
  local masked = apply_mask(payload, mask)

  local ok, serr = copas.send(self.sock, header .. mask .. masked)
  if ok == nil or ok == false then
    -- Any send failure (timeout included) may follow a PARTIAL write:
    -- coloX_net.c send() returns nwr+from-1 on a short write and copas.send
    -- loops resuming from lastIndex, so a 'timeout' leaves the WS framing
    -- desynchronized. Treat the connection as dead. Return (nil, err) to
    -- match the documented `ok | nil, err` contract and the net three-state
    -- convention (recv_n / receive / ws_connect all use nil, not false).
    self.closed = true
    pcall(function() self.sock:close() end)
    if serr == "timeout" then return nil, "timeout" end
    return nil, serr or "closed"
  end
  return true
end

-------------------------------------------------------------------------------
-- internal: send a close frame and mark closed. idempotent.
-------------------------------------------------------------------------------
local function send_close(self, code, reason)
  if self.sent_close then
    self.closed = true
    return true
  end
  self.sent_close = true
  local payload = ""
  if code then
    payload = char((code >> 8) & 0xFF, code & 0xFF) .. (reason or "")
  end
  pcall(send_frame, self, true, OP_CLOSE, payload)
  self.closed = true
  return true
end

-------------------------------------------------------------------------------
-- internal: hard-fail a connection from a receive() error path.
-- Marks closed AND closes the underlying fd eagerly (matching ws_connect /
-- ws:close), so fds do not linger until __gc runs.
-------------------------------------------------------------------------------
local function fail_conn(self)
  self.closed = true
  pcall(function() return self.sock:close() end)
end

-------------------------------------------------------------------------------
-- PUBLIC: ws_connect
-------------------------------------------------------------------------------
local function ws_connect(url, opts)
  opts = opts or {}
  local host, port, path, scheme = parse_ws_url(url)
  if not host then
    return nil, port -- error message
  end

  -- wss goes through the (optional) tls-capable net_connect: the tcp+tls
  -- handshake is non-blocking and is driven by the copas.send below.
  local sock, cerr = net_connect(host, port, scheme == "wss")
  if not sock then
    return nil, "connect failed: " .. tostring(cerr)
  end

  -- generate client key (16 random bytes, base64)
  local key_bytes = rand_bytes(16)
  local key_b64 = b64encode(key_bytes)

  -- build the upgrade request manually
  local defport = (scheme == "wss") and 443 or 80
  local hosthdr = host
  if port ~= defport then hosthdr = host .. ":" .. port end
  local lines = {
    "GET " .. path .. " HTTP/1.1",
    "Host: " .. hosthdr,
    "Upgrade: websocket",
    "Connection: Upgrade",
    "Sec-WebSocket-Key: " .. key_b64,
    "Sec-WebSocket-Version: 13",
  }
  if type(opts.headers) == "table" then
    for k, v in pairs(opts.headers) do
      lines[#lines + 1] = tostring(k) .. ": " .. tostring(v)
    end
  end
  -- terminate with blank line
  local req = concat(lines, "\r\n") .. "\r\n\r\n"

  local self = setmetatable({
    sock    = sock,
    url     = url,
    host    = host,
    port    = port,
    scheme  = scheme,
    timeout = opts.timeout,         -- may be nil -> copas default
    closed  = false,
    sent_close = false,
  }, ws_mt)

  -- send the request (copas.send yields). the budget also covers the
  -- non-blocking tcp+tls handshake the send loop drives for wss sockets.
  local _, serr = copas.send(sock, req, nil, nil, opts.timeout or 15)
  if serr then
    pcall(function() sock:close() end)
    return nil, "send failed: " .. tostring(serr)
  end

  -- read status line
  local status, serr = copas.receive(sock, "*l", nil, opts.timeout or 15)
  if not status then
    pcall(function() sock:close() end)
    return nil, "no status line: " .. tostring(serr)
  end
  -- expect "HTTP/1.1 101 ..."
  local ver, code = status:match("^(HTTP/%d%.%d)%s+(%d%d%d)")
  if not ver or code ~= "101" then
    pcall(function() sock:close() end)
    return nil, "unexpected status: " .. tostring(status)
  end

  -- read headers until blank line
  local headers = {}
  while true do
    local line, herr = copas.receive(sock, "*l", nil, opts.timeout or 15)
    if not line then
      pcall(function() sock:close() end)
      return nil, "header read failed: " .. tostring(herr)
    end
    if line == "" then break end
    local name, value = line:match("^([^:]+):%s*(.*)$")
    if name then headers[name:lower()] = value end
  end

  -- RFC 6455 §4.2.2: require Upgrade: websocket (case-insensitive).
  local upg = headers["upgrade"]
  if not upg or not upg:lower():find("websocket", 1, true) then
    pcall(function() sock:close() end)
    return nil, "bad upgrade response"
  end
  -- require Connection to contain "upgrade" (header may be a comma-list).
  local conn = headers["connection"]
  if not conn or not ("," .. conn:lower() .. ","):find(",upgrade,", 1, true) then
    pcall(function() sock:close() end)
    return nil, "bad connection response"
  end

  -- Sec-WebSocket-Accept is the authoritative handshake check (§4.2.2).
  -- Default ON; caller may disable with opts.verify_accept == false.
  if opts.verify_accept ~= false then
    local accept = headers["sec-websocket-accept"]
    local expected = b64encode(sha1(key_b64 .. WS_GUID))
    if accept ~= expected then
      pcall(function() sock:close() end)
      return nil, "bad Sec-WebSocket-Accept"
    end
  end

  return self
end
_M.connect = ws_connect
_M.ws_connect = ws_connect

-------------------------------------------------------------------------------
-- PUBLIC: ws:isclosed()
-------------------------------------------------------------------------------
function _M:isclosed()
  return self.closed == true
end

-------------------------------------------------------------------------------
-- PUBLIC: ws:receive()  -> msg | nil, err
--   msg = { op = "text"|"binary"|"close"|"ping"|"pong", data = string }
--   on op=="close", data is the numeric status code (or nil) the peer sent.
-- MUST be called from a copas coroutine.
-------------------------------------------------------------------------------
function _M:receive()
  if self.closed then return nil, "closed" end

  while true do
    local f, err = recv_frame(self)
    if not f then
      -- oversized -> 1009 ; protocol -> 1002 ; else just close
      if err == "oversized" then
        pcall(send_close, self, CLOSE_TOO_BIG, "")
      elseif type(err) == "string" and err:sub(1, 9) == "protocol:" then
        pcall(send_close, self, CLOSE_PROTOCOL, "")
      end
      fail_conn(self)
      -- socket errors "closed"/"timeout" surface directly
      return nil, (err == "oversized" or (err and err:sub(1, 9) == "protocol:"))
                  and "closed" or err
    end

    local op = f.op

    if op == OP_CLOSE then
      -- peer initiated/echoed close. Validate body + status per
      -- RFC 6455 §7.1.5 / §7.4 before echoing anything.
      local cn = #f.data
      local code
      if cn == 1 then
        -- §7.1.5: body MUST be empty or >=2 bytes -> protocol error
        pcall(send_close, self, CLOSE_PROTOCOL, "")
        fail_conn(self)
        return { op = "close", data = nil }, "closed"
      elseif cn >= 2 then
        code = (byte(f.data, 1) << 8) | byte(f.data, 2)
        -- §7.4: forbidden/reserved codes 1004/1005/1006/1015 and out-of-range.
        -- Valid ranges are 1000-1003, 1007-1011, 3000-4999.
        if code == 1004 or code == 1005 or code == 1006 or code == 1015
           or code < 1000 or code > 4999
           or (code >= 1012 and code <= 2999) then
          pcall(send_close, self, CLOSE_PROTOCOL, "")
          fail_conn(self)
          return { op = "close", data = code }, "closed"
        end
      end
      -- echo a validated code back (idempotent); bodyless close if code nil
      pcall(send_close, self, code, nil)
      fail_conn(self)
      return { op = "close", data = code }, "closed"
    elseif op == OP_PING then
      -- MUST answer pong with identical payload before anything else
      pcall(send_frame, self, true, OP_PONG, f.data)
      -- continue loop (ping is not a deliverable message)
    elseif op == OP_PONG then
      -- discard; continue
    elseif op == OP_TEXT or op == OP_BIN then
      -- (re)assemble a possibly fragmented message
      local cur = op
      local bufs = { f.data }
      while not f.fin do
        local nf, nerr = recv_frame(self)
        if not nf then
          if nerr == "oversized" then
            pcall(send_close, self, CLOSE_TOO_BIG, "")
          elseif type(nerr) == "string" and nerr:sub(1, 9) == "protocol:" then
            pcall(send_close, self, CLOSE_PROTOCOL, "")
          end
          fail_conn(self)
          return nil, "closed"
        end
        -- RFC 6455 §5.4: control frames MAY be interleaved between
        -- fragments of a fragmented message and MUST be processed.
        if nf.op == OP_PING then
          pcall(send_frame, self, true, OP_PONG, nf.data)
          -- answer pong, then keep reading continuations
        elseif nf.op == OP_PONG then
          -- discard; keep reading
        elseif nf.op == OP_CLOSE then
          local ccode
          if #nf.data >= 2 then
            ccode = (byte(nf.data, 1) << 8) | byte(nf.data, 2)
          end
          pcall(send_close, self, ccode, nil)
          fail_conn(self)
          return { op = "close", data = ccode }, "closed"
        elseif nf.op ~= OP_CONT then
          -- a new data frame or invalid opcode mid-message is a 1002
          pcall(send_close, self, CLOSE_PROTOCOL, "")
          fail_conn(self)
          return nil, "closed"
        else
          bufs[#bufs + 1] = nf.data
        end
      end
      return { op = opname[cur], data = concat(bufs) }
    elseif op == OP_CONT then
      -- continuation with no open message
      pcall(send_close, self, CLOSE_PROTOCOL, "")
      fail_conn(self)
      return nil, "closed"
    else
      -- unknown opcode (shouldn't reach here; recv_frame rejects them)
      pcall(send_close, self, CLOSE_PROTOCOL, "")
      fail_conn(self)
      return nil, "closed"
    end
  end
end

-------------------------------------------------------------------------------
-- PUBLIC: ws:send(data[, kind])   kind = "text" (default) | "binary"
-------------------------------------------------------------------------------
function _M:send(data, kind)
  if self.closed then return nil, "closed" end
  if type(data) ~= "string" then data = tostring(data) end
  local op = (kind == "binary") and OP_BIN or OP_TEXT
  return send_frame(self, true, op, data)
end

-------------------------------------------------------------------------------
-- PUBLIC: ws:send_control(kind[, payload])   kind = "ping" | "pong"
-------------------------------------------------------------------------------
function _M:send_control(kind, payload)
  if self.closed then return nil, "closed" end
  local op
  if kind == "ping" then op = OP_PING
  elseif kind == "pong" then op = OP_PONG
  else return nil, "bad control kind" end
  payload = payload or ""
  if #payload > 125 then return nil, "control payload too large" end
  return send_frame(self, true, op, payload)
end

-------------------------------------------------------------------------------
-- PUBLIC: ws:close([code[, reason]])    default code 1000
-------------------------------------------------------------------------------
function _M:close(code, reason)
  if self.closed then return true end
  code = code or CLOSE_NORMAL
  send_close(self, code, reason)
  -- best-effort: close the underlying socket too
  pcall(function() self.sock:close() end)
  return true
end

netws=_M
