do

local base = _G
local _M = {}

local url=_M

-----------------------------------------------------------------------------
-- Encodes a string into its escaped hexadecimal representation
-- Input
--   s: binary string to be encoded
-- Returns
--   escaped representation of string binary
-----------------------------------------------------------------------------
function _M.escape(s)
	return (string.gsub(s, "([^A-Za-z0-9_])", function(c)
		return string.format("%%%02x", string.byte(c))
	end))
end

-----------------------------------------------------------------------------
-- Protects a path segment, to prevent it from interfering with the
-- url parsing.
-- Input
--   s: binary string to be encoded
-- Returns
--   escaped representation of string binary
-----------------------------------------------------------------------------
local function make_set(t)
	local s = {}
	for i,v in base.ipairs(t) do
		s[t[i]] = 1
	end
	return s
end

-- these are allowed withing a path segment, along with alphanum
-- other characters must be escaped
local segment_set = make_set {
	"-", "_", ".", "!", "~", "*", "'", "(",
	")", ":", "@", "&", "=", "+", "$", ",",
}

local function protect_segment(s)
	return string.gsub(s, "([^A-Za-z0-9_])", function (c)
		if segment_set[c] then return c
		else return string.format("%%%02x", string.byte(c)) end
	end)
end

-----------------------------------------------------------------------------
-- Encodes a string into its escaped hexadecimal representation
-- Input
--   s: binary string to be encoded
-- Returns
--   escaped representation of string binary
-----------------------------------------------------------------------------
function _M.unescape(s)
	return (string.gsub(s, "%%(%x%x)", function(hex)
		return string.char(base.tonumber(hex, 16))
	end))
end

-----------------------------------------------------------------------------
-- Builds a path from a base path and a relative path
-- Input
--   base_path
--   relative_path
-- Returns
--   corresponding absolute path
-----------------------------------------------------------------------------
local function absolute_path(base_path, relative_path)
	if string.sub(relative_path, 1, 1) == "/" then return relative_path end
	local path = string.gsub(base_path, "[^/]*$", "")
	path = path .. relative_path
	path = string.gsub(path, "([^/]*%./)", function (s)
		if s ~= "./" then return s else return "" end
	end)
	path = string.gsub(path, "/%.$", "/")
	local reduced
	while reduced ~= path do
		reduced = path
		path = string.gsub(reduced, "([^/]*/%.%./)", function (s)
			if s ~= "../../" then return "" else return s end
		end)
	end
	path = string.gsub(reduced, "([^/]*/%.%.)$", function (s)
		if s ~= "../.." then return "" else return s end
	end)
	return path
end

-----------------------------------------------------------------------------
-- Parses a url and returns a table with all its parts according to RFC 2396
-- The following grammar describes the names given to the URL parts
-- <url> ::= <scheme>://<authority>/<path>;<params>?<query>#<fragment>
-- <authority> ::= <userinfo>@<host>:<port>
-- <userinfo> ::= <user>[:<password>]
-- <path> :: = {<segment>/}<segment>
-- Input
--   url: uniform resource locator of request
--   default: table with default values for each field
-- Returns
--   table with the following fields, where RFC naming conventions have
--   been preserved:
--	 scheme, authority, userinfo, user, password, host, port,
--	 path, params, query, fragment
-- Obs:
--   the leading '/' in {/<path>} is considered part of <path>
-----------------------------------------------------------------------------
function _M.parse(url, default)
	-- initialize default parameters
	local parsed = {}
	for i,v in base.pairs(default or parsed) do parsed[i] = v end
	-- empty url is parsed to nil
	if not url or url == "" then return nil, "invalid url" end
	-- remove whitespace
	-- url = string.gsub(url, "%s", "")
	-- get fragment
	url = string.gsub(url, "#(.*)$", function(f)
		parsed.fragment = f
		return ""
	end)
	-- get scheme
	url = string.gsub(url, "^([%w][%w%+%-%.]*)%:",
		function(s) parsed.scheme = s; return "" end)
	-- get authority
	url = string.gsub(url, "^//([^/%?#]*)", function(n)
		parsed.authority = n
		return ""
	end)
	-- get query string
	url = string.gsub(url, "%?(.*)", function(q)
		parsed.query = q
		return ""
	end)
	-- get params
	url = string.gsub(url, "%;(.*)", function(p)
		parsed.params = p
		return ""
	end)
	-- path is whatever was left
	if url ~= "" then parsed.path = url end
	local authority = parsed.authority
	if not authority then return parsed end
	authority = string.gsub(authority,"^([^@]*)@",
		function(u) parsed.userinfo = u; return "" end)
	authority = string.gsub(authority, ":([^:%]]*)$",
		function(p) parsed.port = p; return "" end)
	if authority ~= "" then 
		-- IPv6?
		parsed.host = string.match(authority, "^%[(.+)%]$") or authority 
	end
	local userinfo = parsed.userinfo
	if not userinfo then return parsed end
	userinfo = string.gsub(userinfo, ":([^:]*)$",
		function(p) parsed.password = p; return "" end)
	parsed.user = userinfo
	return parsed
end

-----------------------------------------------------------------------------
-- Rebuilds a parsed URL from its components.
-- Components are protected if any reserved or unallowed characters are found
-- Input
--   parsed: parsed URL, as returned by parse
-- Returns
--   a stringing with the corresponding URL
-----------------------------------------------------------------------------
function _M.build(parsed)
	local ppath = _M.parse_path(parsed.path or "")
	local url = _M.build_path(ppath)
	if parsed.params then url = url .. ";" .. parsed.params end
	if parsed.query then url = url .. "?" .. parsed.query end
	local authority = parsed.authority
	if parsed.host then
		authority = parsed.host
		if string.find(authority, ":") then -- IPv6?
			authority = "[" .. authority .. "]"
		end
		if parsed.port then authority = authority .. ":" .. parsed.port end
		local userinfo = parsed.userinfo
		if parsed.user then
			userinfo = parsed.user
			if parsed.password then
				userinfo = userinfo .. ":" .. parsed.password
			end
		end
		if userinfo then authority = userinfo .. "@" .. authority end
	end
	if authority then url = "//" .. authority .. url end
	if parsed.scheme then url = parsed.scheme .. ":" .. url end
	if parsed.fragment then url = url .. "#" .. parsed.fragment end
	-- url = string.gsub(url, "%s", "")
	return url
end

-----------------------------------------------------------------------------
-- Builds a absolute URL from a base and a relative URL according to RFC 2396
-- Input
--   base_url
--   relative_url
-- Returns
--   corresponding absolute url
-----------------------------------------------------------------------------
function _M.absolute(base_url, relative_url)
	local base_parsed
	if base.type(base_url) == "table" then
		base_parsed = base_url
		base_url = _M.build(base_parsed)
	else
		base_parsed = _M.parse(base_url)
	end
	local relative_parsed = _M.parse(relative_url)
	if not base_parsed then return relative_url
	elseif not relative_parsed then return base_url
	elseif relative_parsed.scheme then return relative_url
	else
		relative_parsed.scheme = base_parsed.scheme
		if not relative_parsed.authority then
			relative_parsed.authority = base_parsed.authority
			if not relative_parsed.path then
				relative_parsed.path = base_parsed.path
				if not relative_parsed.params then
					relative_parsed.params = base_parsed.params
					if not relative_parsed.query then
						relative_parsed.query = base_parsed.query
					end
				end
			else	
				relative_parsed.path = absolute_path(base_parsed.path or "",
					relative_parsed.path)
			end
		end
		return _M.build(relative_parsed)
	end
end

-----------------------------------------------------------------------------
-- Breaks a path into its segments, unescaping the segments
-- Input
--   path
-- Returns
--   segment: a table with one entry per segment
-----------------------------------------------------------------------------
function _M.parse_path(path)
	local parsed = {}
	path = path or ""
	--path = string.gsub(path, "%s", "")
	string.gsub(path, "([^/]+)", function (s) table.insert(parsed, s) end)
	for i = 1, #parsed do
		parsed[i] = _M.unescape(parsed[i])
	end
	if string.sub(path, 1, 1) == "/" then parsed.is_absolute = 1 end
	if string.sub(path, -1, -1) == "/" then parsed.is_directory = 1 end
	return parsed
end

-----------------------------------------------------------------------------
-- Builds a path component from its segments, escaping protected characters.
-- Input
--   parsed: path segments
--   unsafe: if true, segments are not protected before path is built
-- Returns
--   path: corresponding path stringing
-----------------------------------------------------------------------------
function _M.build_path(parsed, unsafe)
	local path = ""
	local n = #parsed
	if unsafe then
		for i = 1, n-1 do
			path = path .. parsed[i]
			path = path .. "/"
		end
		if n > 0 then
			path = path .. parsed[n]
			if parsed.is_directory then path = path .. "/" end
		end
	else
		for i = 1, n-1 do
			path = path .. protect_segment(parsed[i])
			path = path .. "/"
		end
		if n > 0 then
			path = path .. protect_segment(parsed[n])
			if parsed.is_directory then path = path .. "/" end
		end
	end
	if parsed.is_absolute then path = "/" .. path end
	return path
end

-----------------------------------------------------------------------------
-- LTN12 - Filters, sources, sinks and pumps.
-- LuaSocket toolkit.
-- Author: Diego Nehab
-----------------------------------------------------------------------------

-----------------------------------------------------------------------------
-- Declare module
-----------------------------------------------------------------------------

-- 2048 seems to be better in windows...
_M.BLOCKSIZE = 2048

local sourcet, sinkt = {}, {}
local socket={}
_M.socket=socket
socket.sourcet = sourcet
socket.sinkt = sinkt

socket.try=net_newtry()


function socket.choose(table)
	return function(name, opt1, opt2)
		if base.type(name) ~= "string" then
			name, opt1, opt2 = "default", name, opt1
		end
		local f = table[name or "nil"]
		if not f then base.error("unknown key (".. base.tostring(name) ..")", 3)
		else return f(opt1, opt2) end
	end
end

sinkt["close-when-done"] = function(sock)
	return base.setmetatable({

	}, {
		__call = function(self, chunk, err)
			if not chunk then
				sock:close()
				return 1
			else return copas.send(sock,chunk) end
		end
	})
end

sinkt["keep-open"] = function(sock)
	return base.setmetatable({

	}, {
		__call = function(self, chunk, err)
			if chunk then return copas.send(sock,chunk)
			else return 1 end
		end
	})
end

sinkt["default"] = sinkt["keep-open"]

socket.sink = socket.choose(sinkt)

sourcet["by-length"] = function(sock, length)
	return base.setmetatable({

	}, {
		__call = function()
			if length <= 0 then return nil end
			local size = math.min(_M.BLOCKSIZE, length)
			local chunk, err = copas.receive(sock,size,nil,nil,sock:timeout())
			if chunk==nil then return nil, err end
			length = length - string.len(chunk)
			return chunk
		end
	})
end

sourcet["until-closed"] = function(sock)
	local done
	return base.setmetatable({

	}, {
		__call = function()
			if done then return nil end
			local chunk, err, partial = copas.receive(sock,_M.BLOCKSIZE)
			if not err then return chunk
			elseif err == "closed" then
				sock:close()
				done = 1
				return partial
			else return nil, err end
		end
	})
end


sourcet["default"] = sourcet["until-closed"]

socket.source = socket.choose(sourcet)

local filter,source,sink,pump = {},{},{},{}

local ltn12={}
_M.ltn12=ltn12
ltn12.filter = filter
ltn12.source = source
ltn12.sink = sink
ltn12.pump = pump
-----------------------------------------------------------------------------
-- Filter stuff
-----------------------------------------------------------------------------
-- returns a high level filter that cycles a low-level filter
function filter.cycle(low, ctx, extra)
	base.assert(low)
	return function(chunk)
		local ret
		ret, ctx = low(ctx, chunk, extra)
		return ret
	end
end

-- chains a bunch of filters together
-- (thanks to Wim Couwenberg)
function filter.chain(...)
	local arg = {...}
	local n = select('#',...)
	local top, index = 1, 1
	local retry = ""
	return function(chunk)
		retry = chunk and retry
		while true do
			if index == top then
				chunk = arg[index](chunk)
				if chunk == "" or top == n then return chunk
				elseif chunk then index = index + 1
				else
					top = top+1
					index = top
				end
			else
				chunk = arg[index](chunk or "")
				if chunk == "" then
					index = index - 1
					chunk = retry
				elseif chunk then
					if index == n then return chunk
					else index = index + 1 end
				else base.error("filter returned inappropriate nil") end
			end
		end
	end
end

-----------------------------------------------------------------------------
-- Source stuff
-----------------------------------------------------------------------------
-- create an empty source
local function empty()
	return nil
end

function source.empty()
	return empty
end

-- returns a source that just outputs an error
function source.error(err)
	return function()
		return nil, err
	end
end

-- creates a file source
function source.file(handle, io_err)
	if handle then
		return function()
			local chunk = handle:read(_M.BLOCKSIZE)
			if not chunk then handle:close() end
			return chunk
		end
	else return source.error(io_err or "unable to open file") end
end

-- turns a fancy source into a simple source
function source.simplify(src)
	base.assert(src)
	return function()
		local chunk, err_or_new = src()
		src = err_or_new or src
		if not chunk then return nil, err_or_new
		else return chunk end
	end
end

-- creates string source
function source.string(s)
	if s then
		local i = 1
		return function()
			local chunk = string.sub(s, i, i+_M.BLOCKSIZE-1)
			i = i + _M.BLOCKSIZE
			if chunk ~= "" then return chunk
			else return nil end
		end
	else return source.empty() end
end

-- creates rewindable source
function source.rewind(src)
	base.assert(src)
	local t = {}
	return function(chunk)
		if not chunk then
			chunk = table.remove(t)
			if not chunk then return src()
			else return chunk end
		else
			table.insert(t, chunk)
		end
	end
end

-- chains a source with one or several filter(s)
function source.chain(src, f, ...)
	if ... then f=filter.chain(f, ...) end
	base.assert(src and f)
	local last_in, last_out = "", ""
	local state = "feeding"
	local err
	return function()
		if not last_out then
			base.error('source is empty!', 2)
		end
		while true do
			if state == "feeding" then
				last_in, err = src()
				if err then return nil, err end
				last_out = f(last_in)
				if not last_out then
					if last_in then
						base.error('filter returned inappropriate nil')
					else
						return nil
					end
				elseif last_out ~= "" then
					state = "eating"
					if last_in then last_in = "" end
					return last_out
				end
			else
				last_out = f(last_in)
				if last_out == "" then
					if last_in == "" then
						state = "feeding"
					else
						base.error('filter returned ""')
					end
				elseif not last_out then
					if last_in then
						base.error('filter returned inappropriate nil')
					else
						return nil
					end
				else
					return last_out
				end
			end
		end
	end
end

-- creates a source that produces contents of several sources, one after the
-- other, as if they were concatenated
-- (thanks to Wim Couwenberg)
function source.cat(...)
	local arg = {...}
	local src = table.remove(arg, 1)
	return function()
		while src do
			local chunk, err = src()
			if chunk then return chunk end
			if err then return nil, err end
			src = table.remove(arg, 1)
		end
	end
end

-----------------------------------------------------------------------------
-- Sink stuff
-----------------------------------------------------------------------------
-- creates a sink that stores into a table
function sink.table(t)
	t = t or {}
	local f = function(chunk, err)
		if chunk then table.insert(t, chunk) end
		return 1
	end
	return f, t
end

-- turns a fancy sink into a simple sink
function sink.simplify(snk)
	base.assert(snk)
	return function(chunk, err)
		local ret, err_or_new = snk(chunk, err)
		if not ret then return nil, err_or_new end
		snk = err_or_new or snk
		return 1
	end
end

-- creates a file sink
function sink.file(handle, io_err)
	if handle then
		return function(chunk, err)
			if not chunk then
				handle:close()
				return 1
			else return handle:write(chunk) end
		end
	else return sink.error(io_err or "unable to open file") end
end

-- creates a sink that discards data
local function null()
	return 1
end

function sink.null()
	return null
end

-- creates a sink that just returns an error
function sink.error(err)
	return function()
		return nil, err
	end
end

-- chains a sink with one or several filter(s)
function sink.chain(f, snk, ...)
	if ... then
		local args = { f, snk, ... }
		snk = table.remove(args, #args)
		f = filter.chain(unpack(args))
	end
	base.assert(f and snk)
	return function(chunk, err)
		if chunk ~= "" then
			local filtered = f(chunk)
			local done = chunk and ""
			while true do
				local ret, snkerr = snk(filtered, err)
				if not ret then return nil, snkerr end
				if filtered == done then return 1 end
				filtered = f(done)
			end
		else return 1 end
	end
end

-----------------------------------------------------------------------------
-- Pump stuff
-----------------------------------------------------------------------------
-- pumps one chunk from the source to the sink
function pump.step(src, snk)
	local chunk, src_err = src()
	local ret, snk_err = snk(chunk, src_err)
	if chunk and ret then return 1
	else return nil, src_err or snk_err end
end

-- pumps all data from a source to a sink, using a step function
function pump.all(src, snk, step)
	base.assert(src and snk)
	step = step or pump.step
	while true do
		local ret, err = step(src, snk)
		if not ret then
			if err then return nil, err
			else return 1 end
		end
	end
end


-----------------------------------------------------------------------------
-- Program constants
-----------------------------------------------------------------------------
-- connection timeout in seconds
_M.TIMEOUT = 60
-- default port for document retrieval
_M.PORT = 80
-- user agent field sent in request
_M.USERAGENT = 'coloX'

-----------------------------------------------------------------------------
-- Reads MIME headers from a connection, unfolding where needed
-----------------------------------------------------------------------------
local function receiveheaders(sock, headers)
	local line, name, value, err
	headers = headers or {}
	-- get first line
	line, err = copas.receive(sock)
	if err then return nil, err end
	-- headers go until a blank line is found
	while line ~= "" do
		-- get field-name and value
		local _
		_,_,name, value = string.find(line, "^(.-):%s*(.*)")
		if not (name and value) then return nil, "malformed reponse headers" end
		name = string.lower(name)
		-- get next line (value might be folded)
		line, err  = copas.receive(sock)
		if err then return nil, err end
		-- unfold any folded values
		while string.find(line, "^%s") do
			value = value .. line
			line = copas.receive(sock)
			if err then return nil, err end
		end
		-- save pair in table
		if headers[name] then headers[name] = headers[name] .. ", " .. value
		else headers[name] = value end
	end
	return headers
end

local function receiveboundary(sock, reqt)
	while true do
		local line, err = copas.receive(sock)
		if err then return nil, err end
		if line:sub(1,2) == '--' then return line end
	end
end
-----------------------------------------------------------------------------
-- Extra sources and sinks
-----------------------------------------------------------------------------
socket.sourcet["http-chunked"] = function(sock, headers)
	local nosize=false
	local done
	return base.setmetatable({

	}, {
		__call = function()
			if nosize and nosize>0 then
				if done then return nil end
				local chunk, err, partial = copas.receive(sock,2048)
				if not err then
					if chunk then nosize=nosize-#chunk end
					return chunk
				elseif err == "closed" then
					sock:close()
					done = 1
					return partial
				else return nil, err end
			end
			-- get chunk size, skip extention
			local line, err = copas.receive(sock)
			if err then return nil, err end
			local size = base.tonumber(string.gsub(line, ";.*", ""), 16)
			if not size then
				--do return nil, "invalid chunk size" end
				--hack by pulleyzzz .
				nosize=32768 --limt
				if line then nosize=nosize-#line end
				return line
					--return nil, "invalid chunk size"
			end
			-- was it the last chunk?
			if size > 0 then
				-- if not, get chunk and skip terminating CRLF
				local chunk, err, part = copas.receive(sock,size)
				if chunk then copas.receive(sock) end
				return chunk, err
			else
				-- if it was, read trailers into headers table
				headers, err = receiveheaders(sock, headers)
				if not headers then return nil, err end
			end
		end
	})
end

socket.sinkt["http-chunked"] = function(sock)
	return base.setmetatable({

	}, {
		__call = function(self, chunk, err)
			if not chunk then return copas.send(sock,"0\r\n\r\n") end
			local size = string.format("%X\r\n", string.len(chunk))
			return copas.send(sock,size ..  chunk .. "\r\n")
		end
	})
end

-----------------------------------------------------------------------------
-- Low level HTTP API
-----------------------------------------------------------------------------
local metat = { __index = {} }

function _M.open(host, port, create,tmout)
	local c = net_connect(host, port or _M.PORT)
	if not c then
		return 
	end
	-- create socket with user connect function, or with default
	local h = base.setmetatable({c=c,tmout=tmout or _M.TIMEOUT}, metat)
	-- create finalized try
	h.try = net_newtry(function() h:close() end)
	c:timeout(tmout or _M.TIMEOUT)
	-- here everything worked
	return h
end

function metat.__index:sendreqheaderline(method, uri,optline,headers)
	local reqline
	if not optline then
		reqline = string.format("%s %s HTTP/1.1\r\n", method or "GET", uri)
	else
		reqline=optline
	end
	local h = "\r\n"
	for i, v in base.pairs(headers) do
		h = i .. ": " .. v .. "\r\n" .. h
	end
	return self.try(copas.send(self.c,reqline..h))
end

function metat.__index:sendbody(headers, source, step)
	source = source or ltn12.source.empty()
	step = step or ltn12.pump.step
	-- if we don't know the size in advance, send chunked and hope for the best
	local mode = "http-chunked"
	if headers["Content-Length"] then mode = "keep-open" end
	return self.try(ltn12.pump.all(source, socket.sink(mode, self.c), step))
end

function metat.__index:receivestatusline()
	local status = self.try(copas.receive(self.c,5))
	-- identify HTTP/0.9 responses, which do not contain a status line
	-- this is just a heuristic, but is what the RFC recommends
	if status ~= "HTTP/" then return nil, status end
	-- otherwise proceed reading a status line
	status = self.try(copas.receive(self.c,"*l", status))
	local _,_,code = string.find(status, "HTTP/%d*%.%d* (%d%d%d)")
	return self.try(base.tonumber(code), status)
end

function metat.__index:receiveheaders()
	return self.try(receiveheaders(self.c))
end

function metat.__index:receiveboundary()
	return self.try(receiveboundary(self.c))
end

function metat.__index:receivebody(headers, sink, step)
	sink = sink or ltn12.sink.null()
	step = step or ltn12.pump.step
	local length = base.tonumber(headers["content-length"])
	local t = headers["transfer-encoding"] -- shortcut
	local mode = "default" -- connection close
	if t and t ~= "identity" then mode = "http-chunked"
	elseif base.tonumber(headers["content-length"]) then mode = "by-length" end
	return self.try(ltn12.pump.all(socket.source(mode, self.c, length),
	sink, step))
end

function metat.__index:receive09body(status, sink, step)
	local source = ltn12.source.rewind(socket.source("until-closed", self.c))
	source(status)
	return self.try(ltn12.pump.all(source, sink, step))
end

function metat.__index:close()
	if self.c then
		return self.c:close()
	end
end

-----------------------------------------------------------------------------
-- High level HTTP API
-----------------------------------------------------------------------------
local function adjusturi(reqt)
	local u = reqt
	-- if there is a proxy, we need the full url. otherwise, just a part.
	if not reqt.proxy and not _M.PROXY then
		u = {
			path = socket.try(reqt.path, "invalid path 'nil'"),
			params = reqt.params,
			query = reqt.query,
			fragment = reqt.fragment
		}
	end
	return url.build(u)
end

local function adjustproxy(reqt)
	local proxy = reqt.proxy or _M.PROXY
	if proxy then
		proxy = url.parse(proxy)
		return proxy.host, proxy.port or 3128
	else
		return reqt.host, reqt.port
	end
end

local function adjustheaders(reqt)
	-- default headers
	local lower = {
		["User-Agent"] = _M.USERAGENT,
		["Host"] = reqt.host,
		["Connection"] = "close, TE",
		["Te"] = "trailers"
	}
	-- if we have authentication information, pass it along
	if reqt.user and reqt.password then
		lower["Authorization"] = "Basic " .. b64encode(reqt.user .. ":" .. reqt.password)
	end
	-- override with user headers
	for i,v in base.pairs(reqt.headers or lower) do
		lower[i] = v
	end
	return lower
end

-- default url parts
local default = {
	host = "",
	--port = _M.PORT
	path ="/",
	scheme = "http"
}

local function adjustrequest(reqt)
	-- parse url if provided
	local nreqt = reqt.url and url.parse(reqt.url, default) or {}
	-- explicit components override url
	for i,v in base.pairs(reqt) do nreqt[i] = v end
	if not nreqt.port or nreqt.port == "" then
		if nreqt.scheme=='https' then
			nreqt.port = 443
		else
			nreqt.port = _M.PORT
		end
	end
	socket.try(nreqt.host and nreqt.host ~= "",
	"invalid host '" .. base.tostring(nreqt.host) .. "'")
	-- compute uri if user hasn't overriden
	nreqt.uri = reqt.uri or adjusturi(nreqt)
	-- ajust host and port if there is a proxy
	nreqt.host, nreqt.port = adjustproxy(nreqt)
	-- adjust headers in request
	nreqt.headers = adjustheaders(nreqt)
	return nreqt
end

local function shouldredirect(reqt, code, headers)
	return headers.location and
	string.gsub(headers.location, "%s", "") ~= "" and
	(reqt.redirect ~= false) and
		(code == 301 or code == 302 or code == 303 or code == 307) and
	(not reqt.method or reqt.method == "GET" or reqt.method == "HEAD")
	and (not reqt.nredirects or reqt.nredirects < 5)
end

local function shouldreceivebody(reqt, code)
	if reqt.method == "HEAD" then return nil end
	if code == 204 or code == 304 then return nil end
	if code >= 100 and code < 200 then return nil end
	return 1
end

-- forward declarations
local trequest, tredirect

function tredirect(reqt, location,timeout)
	local result, code, headers, status = trequest ({
		-- the RFC says the redirect URL has to be absolute, but some
		-- servers do not respect that
		url = url.absolute(reqt.url, location),
		source = reqt.source,
		sink = reqt.sink,
		headers = reqt.headers,
		proxy = reqt.proxy,
		nredirects = (reqt.nredirects or 0) + 1,
		create = reqt.create,
		connecttimeout=reqt.connecttimeout,
	},timeout)
	-- pass location header back as a hint we redirected
	headers = headers or {}
	headers.location = headers.location or location
	return result, code, headers, status
end

function trequest(reqt,timeout)
	-- we loop until we get what we want, or
	-- until we are sure there is no way to get it
	local nreqt = adjustrequest(reqt)
	local h
	if nreqt._h then
		h=nreqt._h
		nreqt._h=nil
	else
		h = _M.open(nreqt.host, nreqt.port, nreqt.create,nreqt.connecttimeout or timeout)
	end
	h.tmout=timeout or _M.TIMEOUT
	h.c:timeout(h.tmout)
	-- send request line and headers
	-- we must send req and header by batch mode , avoid some bad impl of tcp stack such as hue
	h:sendreqheaderline(nreqt.method, nreqt.uri,nreqt.requestline,nreqt.headers)
	-- if there is a body, send it
	if nreqt.source then
		h:sendbody(nreqt.headers, nreqt.source, nreqt.step)
	end
	local code, status = h:receivestatusline()
	-- if it is an HTTP/0.9 server, simply get the body and we are done
	if not code then
		h:receive09body(status, nreqt.sink, nreqt.step)
		return 1, 200
	end
	local headers
	-- ignore any 100-continue messages
	while code == 100 do
		headers = h:receiveheaders()
		code, status = h:receivestatusline()
	end
	headers = h:receiveheaders()
	-- at this point we should have a honest reply from the server
	-- we can't redirect if we already used the source, so we report the error
	if shouldredirect(nreqt, code, headers) and not nreqt.source then
		h:close()
		return tredirect(reqt, headers.location,timeout)
	end
	if nreqt._on_header then
		if not nreqt._on_header(headers) then
			h:close()
			return nil, 'bad headers'
		end
	end
	-- here we are finally done
	if shouldreceivebody(nreqt, code) then
		if string.find(headers["content-type"] or '','multipart') then
			if nreqt._multipart then
				while true do
--					local sepstr, err =h.c:receive()
					local sepstr, err = h:receiveboundary()
					if not sepstr then return nil, err end
					if not nreqt._multipart._part_start() then break end
					local subheaders = h:receiveheaders()
					if not subheaders then return nil,'headers break' end
					if not nreqt._multipart._part_header(subheaders) then break end
					h:receivebody(subheaders, nreqt._multipart.sink, nreqt._multipart.step)
					if not nreqt._multipart._part_end() then break end
					copas.receive(h.c)
				end
				h:close()
				return 1,code, headers, status
			else
				local sepstr, err =copas.receive(h.c)
				if err then return nil, err end
				headers = h:receiveheaders()
			end
		end
		h:receivebody(headers, nreqt.sink, nreqt.step)
	end
	if nreqt._noclose and headers.connection=='keep-alive' then
		reqt._h=h
	else
		h:close()
	end
	return 1, code, headers, status
end

local function srequest(u, b,timeout)
	local t = {}
	local reqt = {
		url = u,
		sink = ltn12.sink.table(t)
	}
	if b then
		reqt.source = ltn12.source.string(b)
		reqt.headers = {
			["Content-Length"] = string.len(b),
			["Content-type"] = "application/x-www-form-urlencoded"
		}
		reqt.method = "POST"
	end
	local _,code, headers, status = trequest(reqt,timeout)
	return table.concat(t), code, headers, status
end

_M.request = net_protect(function(reqt, body,timeout)
	if base.type(reqt) == "string" then return srequest(reqt, body,timeout)
	else return trequest(reqt,timeout) end
end)
nethttp=_M
end
