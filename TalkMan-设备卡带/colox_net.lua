copas = {}

local function statusHandler(status, ...)
	if status then return ... end
	local err = (...)
	if type(err) == "table" then
		return nil, err[1]
	else
		error(err)
	end
end

function net_protect(func)
	return function (...)
		return statusHandler(pcall(func, ...))
	end
end

function net_newtry(finalizer)
	return function (...)
		local status = (...)
		if not status then
			pcall(finalizer, select(2, ...))
			error({ (select(2, ...)) }, 0)
		end
		return ...
	end
end

do
local coroutine=require'coroutine'
local coroutine_running,coroutine_resume,coroutine_create,coroutine_yield=
coroutine.running,coroutine.resume,coroutine.create,coroutine.yield
local table_insert,table_remove=table.insert,table.remove
local math_max,math_min=math.max,math.min
local pcall,type,pairs,select,next=pcall,type,pairs,select,next

local gettime = time

local RECV_BROKEN_TIMEOUT=1e10
local SEND_BROKEN_TIMEOUT=10
local CONNECT_TIMEOUT=10
local BROKEN_CHECK=1
local WATCH_DOG_TIMEOUT = 120
local UDP_DATAGRAM_MAX = 8192


-- Close the socket associated with the current connection after the handler finishes
copas.autoclose = true

-------------------------------------------------------------------------------
-- Simple set implementation based on LuaSocket's tinyirc.lua example
-- adds a FIFO queue for each value in the set
-------------------------------------------------------------------------------
local function newset()
	local reverse = {}
	local set = {}
	local q = {}
	setmetatable(set, { __index = {
			insert = function(set, value)
				if not reverse[value] then
					set[#set + 1] = value
					reverse[value] = #set
				end
			end,

			remove = function(set, value)
				local index = reverse[value]
				if index then
					reverse[value] = nil
					local top = set[#set]
					set[#set] = nil
					if top ~= value then
						reverse[top] = index
						set[index] = top
					end
				end
			end,

			push = function (set, key, itm)
				local qKey = q[key]
				if qKey == nil then
					q[key] = {itm}
				else
					qKey[#qKey + 1] = itm
				end
			end,

			pop = function (set, key)
				local t = q[key]
				if t ~= nil then
					local ret = table_remove (t, 1)
					if t[1] == nil then
						q[key] = nil
					end
					return ret
				end
			end,
			
			removeitm=function (set,itm)
				local findfd
				for k,v in pairs(q) do
					for j=#v,1,-1 do
						if v[j]==itm then
							findfd=k
							table_remove(v,j)
						end
					end
					if #v==0 then
						q[k]=nil
					end
				end
				if findfd then
					set:remove(findfd)
				end
				return findfd
			end
		}})
	return set
end

local fnil = function()end
local _sleeping = {
	times = {},  -- list with wake-up times
	cos = {},    -- list with coroutines, index matches the 'times' list
	lethargy = {}, -- list of coroutines sleeping without a wakeup time

	insert = fnil,
	remove = fnil,
	push = function(self, sleeptime, co)
		if not co then return end
		if sleeptime<0 then
			--sleep until explicit wakeup through copas.wakeup
			self.lethargy[co] = true
			return
		else
			sleeptime = gettime() + sleeptime
		end
		local t, c = self.times, self.cos
		local i, cou = 1, #t
		--TODO: do a binary search
		while i<=cou and t[i]<=sleeptime do i=i+1 end
		table_insert(t, i, sleeptime)
		table_insert(c, i, co)
	end,
	getnext = function(self)  -- returns delay until next sleep expires, or nil if there is none
		local t = self.times
		local delay = t[1] and t[1] - gettime() or nil

		return delay and math_max(delay, 0) or nil
	end,
	-- find the thread that should wake up to the time
	pop = function(self, time)
		local t, c = self.times, self.cos
		if #t==0 or time<t[1] then return end
		local co = c[1]
		table_remove(t, 1)
		table_remove(c, 1)
		return co
	end,
	wakeup = function(self, co)
		local let = self.lethargy
		if let[co] then
			self:push(0, co)
			let[co] = nil
		else
			let = self.cos
			for i=1,#let do
				if let[i]==co then
					table_remove(let, i)
					table_remove(self.times, i)
					self:push(0, co)
					return
				end
			end
		end
	end,
	removeitm=function(self,co)
		local t, c = self.times, self.cos
		for i=#c,1,-1 do
			if c[i]==co then
				table_remove(t, i)
				table_remove(c, i)
				return co
			end
		end
	end,
} --_sleeping

local _servers = newset() -- servers being handled
local _reading_log = {}
local _writing_log = {}

local _reading = newset() -- sockets currently being read
local _writing = newset() -- sockets currently being written

function copas.receive(client, pattern, part,recvbrokentimeout)
	local s, err
	pattern = pattern or "*l"
	repeat
		s, err, part = client:recv(pattern, part)
		if s or s==nil then
			_reading_log[client] = nil
			return s, err, part
		end
		_reading_log[client] = recvbrokentimeout or client:timeout() or RECV_BROKEN_TIMEOUT
		_reading:insert (client)
		_reading:push (client, coroutine_running())
		coroutine_yield(true)
		recvbrokentimeout=_reading_log[client]
		if not recvbrokentimeout then
			return nil,'timeout'
		end
	until false
end

-- receives data from a client over UDP. Not available for TCP.
function copas.receivefrom(client, size,recvbrokentimeout)
	local s, err, port
	size = size or UDP_DATAGRAM_MAX
	repeat
		s, err, port = client:recvfrom(size) -- upon success err holds ip address
		if s or s==nil then
			_reading_log[client] = nil
			return s, err, port
		end
		_reading_log[client] = recvbrokentimeout or client:timeout() or RECV_BROKEN_TIMEOUT
		_reading:insert (client)
		_reading:push (client, coroutine_running())
		coroutine_yield(true)
		recvbrokentimeout=_reading_log[client]
		if not recvbrokentimeout then
			return nil,'timeout'
		end
	until false
end

-- same as above but with special treatment when reading chunks,
-- unblocks on any data received.
function copas.receivePartial(client, pattern,part,recvbrokentimeout)
	local s, err, part
	pattern = pattern or "*l"
	repeat
		s, err, part = client:recv(pattern,part)
		if s or ( (type(pattern)=="number") and part~="" and part ~=nil ) or
		s==nil then
			_reading_log[client] = nil
			return s, err, part
		end
		_reading_log[client] = recvbrokentimeout or client:timeout() or RECV_BROKEN_TIMEOUT
		_reading:insert (client)
		_reading:push (client, coroutine_running())
		coroutine_yield(true)
		recvbrokentimeout=_reading_log[client]
		if not recvbrokentimeout then
			return nil,'timeout'
		end
	until false
end

-- sends data to a client. The operation is buffered and
-- yields to the writing set on timeouts
-- Note: from and to parameters will be ignored by/for UDP sockets
function copas.send(client, data, from, to,sendbrokentimeout)
	local s, err,sent
	from = from or 1
	local lastIndex = from - 1

	repeat
		s, err, lastIndex = client:send(data, lastIndex + 1, to)
		-- adds extra corrotine swap
		-- garantees that high throuput dont take other threads to starvation
		if (math.random(100) > 90) then
			_writing_log[client] = sendbrokentimeout or client:timeout() or SEND_BROKEN_TIMEOUT
			_writing:insert (client)
			_writing:push (client, coroutine_running())
			coroutine_yield(true)
			sendbrokentimeout=_writing_log[client]
			if not sendbrokentimeout then
				return nil,'timeout'
			end
		end
		if s or s==nil then
			_writing_log[client] = nil
			return s, err,lastIndex
		end
		_writing_log[client] = sendbrokentimeout or client:timeout() or SEND_BROKEN_TIMEOUT
		_writing:insert (client)
		_writing:push (client, coroutine_running())
		coroutine_yield(true)
		sendbrokentimeout=_writing_log[client]
		if not sendbrokentimeout then
			return nil,'timeout'
		end
	until false
end

-- sends data to a client over UDP. Not available for TCP.
function copas.sendto(client, data, ip, port,sendbrokentimeout)
	local s, err,sent

	repeat
		s, err = client:sendto(data, ip, port)
		-- adds extra corrotine swap
		-- garantees that high throuput dont take other threads to starvation
		if (math.random(100) > 90) then
			_writing_log[client] = sendbrokentimeout or client:timeout() or SEND_BROKEN_TIMEOUT
			_writing:insert (client)
			_writing:push (client, coroutine_running())
			coroutine_yield(true)
		end
		if s or s==nil then
			_writing_log[client] = nil
			return s, err
		end
		_writing_log[client] = sendbrokentimeout or client:timeout() or SEND_BROKEN_TIMEOUT
		_writing:insert (client)
		_writing:push (client, coroutine_running())
		coroutine_yield(true)
		sendbrokentimeout=_writing_log[client]
		if not sendbrokentimeout then
			return nil,'timeout'
		end
	until false
end

--------------------------------------------------
-- Error handling
--------------------------------------------------

local _errhandlers = {}   -- error handler per coroutine

function copas.setErrorHandler (err)
	local co = coroutine_running()
	if co then
		_errhandlers [co] = err
	end
end

local function _deferror (msg, co, skt)
	printh(msg, co, skt)
end

-------------------------------------------------------------------------------
-- Thread handling
-------------------------------------------------------------------------------

local function _doTick (co, skt, ...)
	if not co then return end

	local ok, res, new_q = coroutine_resume(co, skt, ...)

	if ok then
		if res and new_q then
			new_q:insert (res)
			new_q:push (res, co)
		end
	else
		if not ok then pcall (_errhandlers [co] or _deferror, res, co, skt) end
		if skt and copas.autoclose then skt:close() end
		_errhandlers [co] = nil
	end
end

-- accepts a connection on socket input
local function _accept(input, handler)
	local client = input:accept()
	if client then
		local co = coroutine_create(handler)
		_doTick (co, client)
		--_reading:insert(client)
		--_reading:insert(client.socket)
	end
	return client
end

-- handle threads on a queue
local function _tickRead (skt)
	_doTick (_reading:pop (skt), skt)
end

local function _tickWrite (skt)
	_doTick (_writing:pop (skt), skt)
end

-------------------------------------------------------------------------------
-- Adds a server/handler pair to Copas dispatcher
-------------------------------------------------------------------------------
local function addTCPserver(server, handler)
	_servers[server] = handler
	_reading:insert(server)
end

local function addUDPserver(server, handler)
	local co = coroutine_create(handler)
	_reading:insert(server)
	_doTick (co, server)
end

local function _socket_is_udp(s)
	local ok, inf = pcall(function() return s:info() end)
	return ok and type(inf) == "table" and inf.type == "udp"
end

function copas.addserver(server, handler)
	if _socket_is_udp(server) then
		addUDPserver(server, handler)
	else
		addTCPserver(server, handler)
	end
end

function copas.removeserver(server, keep_open)
  _servers[server] = nil 
  _reading:remove(server) 
  if keep_open then
    return true
  end
  return server:close() 
end

-------------------------------------------------------------------------------
-- Adds an new courotine thread to Copas dispatcher
-------------------------------------------------------------------------------
function copas.addthread(thread, ...)
	if type(thread) ~= "thread" then
		local handle=thread
		thread = coroutine_create(function(_, ...) return handle(...) end)
	end
	_doTick (thread, nil, ...)
	return thread
end

function copas.removethread(thread)
	local fd=_reading:removeitm(thread)
	if fd then
		_reading_log[fd]=nil
		if copas.autoclose then fd:close() end
		_errhandlers [thread] = nil
	end
	fd=_writing:removeitm(thread)
	if fd then
		_writing_log[fd]=nil
		if copas.autoclose then fd:close() end
		_errhandlers [thread] = nil
	end
	fd=_sleeping:removeitm(thread)
	if fd then
		_errhandlers [thread] = nil
	end
end
-------------------------------------------------------------------------------
-- tasks registering
-------------------------------------------------------------------------------

local _tasks = {}

local function addtaskRead (tsk)
	-- lets tasks call the default _tick()
	tsk.def_tick = _tickRead

	_tasks [tsk] = true
end

local function addtaskWrite (tsk)
	-- lets tasks call the default _tick()
	tsk.def_tick = _tickWrite

	_tasks [tsk] = true
end

local function tasks ()
	return next, _tasks
end

-------------------------------------------------------------------------------
-- main tasks: manage readable and writable socket sets
-------------------------------------------------------------------------------
-- a task to check ready to read events
local _readable_t = {
	events = function(self)
		local i = 0
		return function ()
			i = i + 1
			return self._evs [i]
		end
	end,

	tick = function (self, input)
		local handler = _servers[input]
		if handler then
			input = _accept(input, handler)
		else
			_reading:remove (input)
			self.def_tick (input)
		end
	end
}

addtaskRead (_readable_t)


-- a task to check ready to write events
local _writable_t = {
	events = function (self)
		local i = 0
		return function ()
			i = i + 1
			return self._evs [i]
		end
	end,

	tick = function (self, output)
		_writing:remove (output)
		self.def_tick (output)
	end
}

addtaskWrite (_writable_t)
--
--sleeping threads task
local _sleeping_t = {
	tick = function (self, time, ...)
		_doTick(_sleeping:pop(time), ...)
	end
}

-- yields the current coroutine and wakes it after 'sleeptime' seconds.
-- If sleeptime<0 then it sleeps until explicitly woken up using 'wakeup'
function copas.sleep(sleeptime)
    coroutine_yield((sleeptime or 0), _sleeping)
end

-- Wakes up a sleeping coroutine 'co'.
function copas.wakeup(co)
	_sleeping:wakeup(co)
end

local last_cleansing = 0

-------------------------------------------------------------------------------
-- Checks for reads and writes on sockets
-------------------------------------------------------------------------------
local function _select ()
	local err
	local now = utcsec()
	_readable_t._evs, _writable_t._evs, err = net_select(_reading, _writing)
	local r_evs, w_evs = _readable_t._evs, _writable_t._evs
	if not r_evs and not w_evs then return false end
	local ddtime=now-last_cleansing
	if ddtime >= BROKEN_CHECK then
		last_cleansing = now
		for k,v in pairs(_reading_log) do
			if not r_evs[k] then
				if v<=1 then
					_reading_log[k] = nil
					r_evs[#r_evs + 1] = k
					r_evs[k] = #r_evs
				else
					_reading_log[k]=v-ddtime
				end
			end
		end

		for k,v in pairs(_writing_log) do
			if not w_evs[k] then
				if v<=1 then
					_writing_log[k] = nil
					w_evs[#w_evs + 1] = k
					w_evs[k] = #w_evs
				else
					_writing_log[k]=v-ddtime
				end
			end
		end
	end

	if #r_evs + #w_evs > 0 then
		return nil
	else
		return err
	end
end

function copas.step(timeout)
	_sleeping_t:tick(gettime())

	-- Need to wake up the select call it time for the next sleeping event
	local nextwait = _sleeping:getnext()
	if nextwait then
		
	else
		if copas.finished() then
			return false
		end
	end

	local err = _select ()
	if err == false then return false end

	if err then
		error(err)
	end

	for tsk in tasks() do
		for ev in tsk:events() do
			tsk:tick (ev)
		end
	end
	return true
end

-------------------------------------------------------------------------------
-- Check whether there is something to do.
-- returns false if there are no sockets for read/write nor tasks scheduled
-- (which means Copas is in an empty spin)
-------------------------------------------------------------------------------
function copas.finished()
	return not (next(_reading) or next(_writing) or _sleeping:getnext())
end

end
