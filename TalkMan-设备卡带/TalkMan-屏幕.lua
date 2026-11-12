-- 240×240 圆屏：黑底荧光绿。仅以状态驱动，动效不代表麦克风音量。
local C={bg=getcolor(0,4,2),ink=getcolor(92,255,139),text=getcolor(183,255,204),
 muted=getcolor(110,185,138),off=getcolor(72,128,91),edge=getcolor(25,67,41),
 dim=getcolor(10,29,18),warn=getcolor(255,193,92)}
local function stroke(x1,y1,x2,y2,c) wline(2,x1,y1,x2,y2,c) end
local function progress_ring(progress,color)
 circ(120,120,116,C.edge)
 local count=math.floor(math.min(1,math.max(0,progress))*96)
 for i=0,count-1 do
  local a=-math.pi/2+i*math.pi/48;local b=a+math.pi/48
  stroke(120+math.cos(a)*116,120+math.sin(a)*116,
   120+math.cos(b)*116,120+math.sin(b)*116,color)
 end
end
local function text(value,cx,y,maxw,c,size,bold)
 value=tostring(value or '')
 -- 桌面/USB 图流按真实字体测宽；实体位图仍使用固件规定的 8/16 字宽。
 if TMText then TMText(value,cx,y,maxw,c,size or 16,bold==true);return end
 local width=0;local finish=0
 for offset,cp in utf8.codes(value) do
  local w=cp<128 and 8 or 16
  if cp>=32 and cp~=127 then
   if width+w>maxw then break end
   width=width+w
  end
  finish=offset
 end
 local x=math.floor(cx-width/2)
 for offset,cp in utf8.codes(value) do
  if offset>finish then break end
  if cp>=32 and cp~=127 then
   print(utf8.char(cp),x,y,c,0,16);x=x+(cp<128 and 8 or 16)
  end
 end
end
local glyphs={T={'11111','00100','00100','00100','00100','00100','00100'},
 M={'10001','11011','10101','10101','10001','10001','10001'}}
local mark_positions={{'T',97},{'M',123}}
local rain_colors={C.text,C.ink,getcolor(42,172,84),getcolor(21,94,49),C.dim}
local target_positions={{68,59},{172,59},{172,161},{68,161}}
local function wordmark()
 -- 点阵 T. M.；不依赖固件字体，顶部居中。
 for _,g in ipairs(mark_positions) do
  for row,bits in ipairs(glyphs[g[1]]) do
   for col=1,5 do
    if bits:sub(col,col)=='1' then
     local x=g[2]+(col-1)*2;local y=18+(row-1)*2
     rectfill(x,y,x+1,y+1,C.ink)
    end
   end
  end
 end
 rectfill(111,29,113,31,C.ink);rectfill(137,29,139,31,C.ink)
end
local function rain(t)
 -- 装饰性代码雨，不代表音量或识别到的文本；仅录音时播放。
 local symbols='0123456789ABCDEF'
 for col=0,12 do
  local speed=18+(col*7)%17
  local head=(t*speed+col*31)%84
  for tail=0,4 do
   local y=76+head-tail*10
   if y>=82 and y<=122 then
    local n=(col*7+math.floor(t*6)+tail*3)%#symbols+1
    print(symbols:sub(n,n),43+col*12,math.floor(y),rain_colors[tail+1],0,8)
   end
  end
 end
end
local function target_picker(m)
 if m.target_scanning or (m.cycling and (not m.target_choice or m.target_choice=='')) then
  text('正在切换',120,108,174,C.text,20,true)
  text('读取已打开的窗口',120,141,174,C.muted,14)
 elseif m.target_error then
  text('未能切换',120,100,174,C.warn,20,true)
  text(m.target or '未选目标',120,130,174,C.muted)
  text('请在电脑检查目标',120,157,174,C.muted,14)
 elseif not m.target_index or m.target_index==0 then
  text('未选目标',120,108,174,C.text,20,true)
  text('请在电脑打开 Agent',120,141,174,C.muted,14)
 else
  -- 顺时针：左上、右上、右下、左下；名字与指针同页，末页空位不参与轮询。
  local names=m.target_page_names or {m.target_choice or m.target or ''}
  local start=m.target_page_start or m.target_index
  local selected=m.target_index-start+1
  for i=1,math.min(4,#names) do
   local x=target_positions[i][1];local y=target_positions[i][2];local active=i==selected
   if active then
    rectfill(x-42,y-5,x+42,y+23,C.dim)
    line(x-42,y-5,x-29,y-5,C.ink);line(x-42,y-5,x-42,y+3,C.ink)
    line(x+42,y+23,x+29,y+23,C.ink);line(x+42,y+23,x+42,y+15,C.ink)
   end
   text(names[i],x,y,80,active and C.text or C.muted,active and 17 or 14,active)
  end
  -- 外圈光标对应四角；松开右下才选定，没有自动切换计时。
  local progress=m.target_waiting and (m.target_progress or 0) or 1
  local angle=(-135+(selected-1)*90)*math.pi/180
  for i=0,15 do
   local a=angle-math.pi/12+i*math.pi/96;local b=a+math.pi/96
   wline(4,120+math.cos(a)*114,120+math.sin(a)*114,120+math.cos(b)*114,120+math.sin(b)*114,C.ink)
   if i<math.floor(progress*16) then
    line(120+math.cos(a)*108,120+math.sin(a)*108,120+math.cos(b)*108,120+math.sin(b)*108,C.text)
   end
  end
  text(m.target_waiting and '预选' or m.cycling and '连接中' or '已选定',120,114,80,C.off,14)
  local page=tostring(m.target_page or 1)..'/'..tostring(m.target_pages or 1)..' 页'
  text(tostring(m.target_index)..'/'..tostring(m.target_count)..'   '..page,120,193,150,C.muted,12)
 end
 text(m.target_waiting and '松开后选位置' or m.cycling and '正在切换' or m.target_error and '请重新旋转' or '旋转选目标',120,212,112,C.muted,14)
end
local function icon(kind,x,y,c)
 if kind=='mic' then
  rect(x-4,y-10,x+4,y+2,c);line(x-3,y-11,x+3,y-11,c)
  stroke(x-8,y-1,x-8,y+4,c);stroke(x+8,y-1,x+8,y+4,c)
  stroke(x-8,y+4,x-4,y+8,c);stroke(x-4,y+8,x+4,y+8,c);stroke(x+4,y+8,x+8,y+4,c)
  stroke(x,y+8,x,y+12,c);stroke(x-5,y+12,x+5,y+12,c)
 elseif kind=='spark' then
  stroke(x,y-11,x+3,y-3,c);stroke(x+3,y-3,x+11,y,c)
  stroke(x+11,y,x+3,y+3,c);stroke(x+3,y+3,x,y+11,c)
  stroke(x,y+11,x-3,y+3,c);stroke(x-3,y+3,x-11,y,c)
  stroke(x-11,y,x-3,y-3,c);stroke(x-3,y-3,x,y-11,c)
  line(x+10,y-11,x+10,y-5,c);line(x+7,y-8,x+13,y-8,c)
 elseif kind=='delete' then
  stroke(x-9,y-7,x+9,y-7,c);rect(x-6,y-5,x+6,y+10,c)
  line(x-3,y-11,x+3,y-11,c);line(x-3,y-11,x-3,y-8,c);line(x+3,y-11,x+3,y-8,c)
  line(x-2,y-1,x-2,y+6,c);line(x+2,y-1,x+2,y+6,c)
 elseif kind=='cancel' then
  stroke(x-7,y-7,x+7,y+7,c);stroke(x+7,y-7,x-7,y+7,c)
 elseif kind=='stop' then rectfill(x-7,y-7,x+7,y+7,c)
 elseif kind=='combo' then
  rect(x-10,y-8,x+1,y+3,c);rect(x-1,y-2,x+10,y+9,c)
  stroke(x-7,y-12,x-7,y-5,c);stroke(x-10,y-9,x-4,y-9,c)
 elseif kind=='link' then
  rect(x-13,y-4,x-3,y+4,c);rect(x+3,y-4,x+13,y+4,c)
  line(x-2,y,x+2,y,c);line(x,y-10,x,y-6,c);line(x,y+6,x,y+10,c)
 elseif kind=='check' then stroke(x-9,y,x-3,y+6,c);stroke(x-3,y+6,x+10,y-8,c)
 elseif kind=='send' then
  stroke(x-10,y+7,x+10,y-8,c);stroke(x+10,y-8,x+3,y+10,c)
  stroke(x+3,y+10,x-1,y+2,c);stroke(x-1,y+2,x-10,y-2,c);stroke(x-10,y-2,x+10,y-8,c)
 elseif kind=='knob' then
  circ(x,y,7,c);line(x,y-11,x,y-6,c);circfill(x,y,2,c)
 end
end
local function corner(kind,label,x,y,active,enabled)
 local c=active and C.ink or enabled and C.muted or C.off
 if active then
  line(x-20,y-15,x-20,y-7,C.ink);line(x-20,y-15,x-12,y-15,C.ink)
  line(x+20,y+14,x+20,y+6,C.ink);line(x+12,y+14,x+20,y+14,C.ink)
 end
 icon(kind,x,y,c);text(label,x,y+17,48,c)
end
function TMDraw(m)
 cls(C.bg)
 local t=m.now/1000
 local live=m.ready;local phase=m.phase or 'idle'
 local confirming=live and (m.send_seconds or 0)>0
 -- 暗色电路环与刻度只作背景，不穿过正文。
 circ(120,120,113,C.edge);circ(120,120,110,C.dim)
 for i=0,47 do
  local a=i*math.pi/24;local r=i%4==0 and 106 or 109
  line(120+math.cos(a)*r,120+math.sin(a)*r,120+math.cos(a)*112,120+math.sin(a)*112,C.edge)
 end
 wordmark()
 if m.update_hold or (m.update_phase and m.update_phase~='idle') then
  if m.update_hold and (not m.update_phase or m.update_phase=='idle') then
   if m.device_build then text('卡带'..tostring(m.device_build),120,35,70,C.off,12) end
   local progress=math.min(1,(m.now-m.update_hold)/5000)
   progress_ring(progress,C.ink)
   text('更新卡带',120,84,180,C.text,20,true)
   text(tostring(math.max(0,math.ceil(5-(m.now-m.update_hold)/1000)))..' 秒',120,117,160,C.ink,22,true)
   text('松开任一键取消',120,159,190,C.muted,14)
  else
   icon('link',120,83,C.ink)
   text(m.update_message or '正在检查',120,119,202,C.text,16)
   text('原卡带保留',120,165,180,C.muted,14)
  end
  return
 end
 if phase=='blocked' and live then
  icon('link',120,83,C.muted)
  text('桌面设置中',120,119,190,C.text,20,true)
  text('关闭后继续操作',120,151,190,C.muted,14)
  text('三键长按查看升级提示',120,181,202,C.off,12)
  return
 end
 if m.input_selecting and live and not m.inactive then
  text('选择输入位置',120,55,168,C.muted)
  text(m.input_target or '',120,72,174,C.text)
  if phase=='recording' or phase=='opening' then
   text('说位置指令',120,106,174,C.ink,22,true)
   text('左下结束录音',120,147,174,C.muted)
  elseif m.input_error then
   text('位置已失效',120,103,174,C.warn,22,true)
   text('请在电脑重新查找',120,140,174,C.muted)
  elseif m.input_loading then
   text('正在核对',120,107,174,C.text,22,true)
  else
   text(tostring(m.input_index or 1)..' / '..tostring(m.input_count or 0),120,104,170,C.ink,28,true)
   text('旋转切换',120,144,160,C.muted)
   text('点按旋钮确认',120,169,160,C.text)
  end
  text('右上取消',120,202,120,C.muted)
  return
 end
 if m.target_view and live and phase~='review' and not m.pending and not m.fill_cancelled and not m.ul and not m.inactive then
  target_picker(m);return
 end
 line(22,94,22,145,C.edge);line(218,94,218,145,C.edge)
 for i=0,5 do
  rectfill(18,99+i*7,19,101+i*7,C.edge);rectfill(221,99+i*7,222,101+i*7,C.edge)
 end
 local cancel=live and (phase=='idle' and '清空' or phase=='review' and '放弃' or '取消') or '取消'
 if m.modifier and live then cancel='清空' end
 if confirming then cancel='取消' end
 if not m.inactive then
  corner(confirming and 'send' or phase=='review' and 'check' or 'spark',confirming and '发送' or m.modifier and '显隐' or phase=='review' and '确认' or '改写',
   59,43,confirming or m.ul~=nil or (live and phase=='review'),live and (phase=='idle' or phase=='review'))
  corner(confirming and 'cancel' or (m.modifier or phase=='idle') and live and 'delete' or 'cancel',cancel,181,43,m.buttons[1],live and (not m.modifier or phase=='idle'))
  corner((phase=='recording' or phase=='opening') and 'stop' or 'mic',(phase=='recording' or phase=='opening') and '结束' or m.modifier and '指令' or confirming and '新录入' or '录入',
   59,177,m.buttons[2] or phase=='recording',live and (phase=='idle' or phase=='recording' or phase=='opening'))
  corner('combo','组合',181,177,m.modifier,live)
 end
 local title=m.title or '等待配对';local detail=m.target or '';local hero='knob'
 if not live then
  title=m.configured and '连接中' or '等待配对';detail=m.connection_hint or (m.configured and '检查电脑与网络' or '请在电脑配对');hero='link'
 elseif phase=='recording' then title=m.title=='说修改要求' and '说修改要求' or '录音中';hero='mic'
 elseif phase=='review' then title='改写待确认';detail='旋转滚动预览';hero='check'
 elseif phase=='filling' then title=m.title=='正在发送' and '正在发送' or '正在填入';detail=m.title=='正在发送' and '正在核对目标' or '仅填入，不发送';hero='send'
 elseif phase=='idle' and (title=='就绪' or title=='可以录入') then title='待录入' end
 if live and phase=='idle' then
  title=m.selecting and '选择文字' or '移动光标'
  detail=m.selecting and ('已选 '..tostring(m.selection_count or 0)..' 字') or '点按开始选区'
 end
 if m.pending then
  title=m.hold_short and '按住以填入' or m.armed and '松开填入' or '等待确认';hero='send'
 end
 if m.fill_progress and m.pending and live and not m.ul then
  progress_ring(m.fill_progress,C.ink)
 end
 if m.fill_cancelled and live then title='已取消填入';detail='目标保持不变';hero='cancel' end
 if m.modifier and live then
  title=phase=='review' and '浏览改写' or '选择目标'
  detail=phase=='review' and '旋转滚动预览' or '旋转选择 Agent';hero='knob'
  if m.buttons[1] then
   title=phase=='idle' and '清空全文' or '暂不能清空'
   detail=phase=='idle' and '上一步可恢复' or '先结束当前操作';hero='delete'
  end
 end
 if live and (m.send_seconds or 0)>0 then
  title='左上发送';detail=tostring(m.send_seconds)..' 秒内 · 右上取消';hero='send'
 end
 if m.send_current and live and phase=='idle' and not confirming then
  title='松开旋钮发送';detail='目标已有文字 · 转动取消';hero='send'
 end
 if m.fill_cancelled and live and not confirming then title='已取消操作';detail='松开后重新操作';hero='cancel' end
 if m.inactive then title='已退出';detail='电脑仍可使用';hero='check' end
 if m.ul then
  title=tostring(math.max(0,math.ceil((m.exit_ms-(m.now-m.ul))/1000)))..' 秒后退出'
  detail='松开取消退出';hero='cancel'
  progress_ring((m.now-m.ul)/m.exit_ms,C.warn)
 end
 -- 中央为主信息区，短角线提供边界而不铺满栅格。
 for _,x in ipairs({34,205}) do
  local dx=x<120 and 7 or -7
  line(x,85,x+dx,85,C.edge);line(x,85,x,93,C.edge)
  line(x,153,x+dx,153,C.edge);line(x,145,x,153,C.edge)
 end
 if phase=='recording' and live and not m.modifier and not m.ul and not m.fill_cancelled and not m.pending then
  rain(t)
  text(title,120,137,160,C.text)
 else
  icon(hero,120,95,m.ul and C.warn or C.ink)
  text(title,120,115,160,m.ul and C.warn or C.text)
  text(detail,120,137,160,C.muted)
 end
 local hint='点按开始选区'
 if m.modifier and live then hint=m.buttons[4] and '松开旋钮' or '左上显隐'
 elseif m.fill_cancelled then hint='松开旋钮'
 elseif m.pending then hint='转动取消'
 elseif (phase=='recording' or phase=='opening') and live then hint='左下结束'
 elseif phase=='review' and live then hint='短按左上'
 elseif live and (m.send_seconds or 0)>0 then hint='左下可开始新录入'
 elseif m.selecting and live then hint='点按结束选区'
 elseif live then
  local step=math.floor(t/4)%3
  if step==1 then hint='右下转选目标'
  elseif step==2 and m.can_fill then hint='长按填入' end
 end
 if m.inactive then hint='系统键返回' end
 -- 中下方箭头指示旋钮方向，底部短提示随状态切换。
 line(108,170,103,174,C.muted);line(103,174,108,178,C.muted)
 line(132,170,137,174,C.muted);line(137,174,132,178,C.muted)
 icon('knob',120,174,C.muted)
 text(hint,120,211,96,C.muted)
end
