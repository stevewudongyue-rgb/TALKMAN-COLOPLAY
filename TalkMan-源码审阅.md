# TALKMAN COLOPLAY 源码审阅

日期：2026-11-13。范围：初代 COLOPLAY（240×240 圆屏、四键＋旋钮）的独立桌面客户端与应用卡带。

## 版本与当前结论

| 对象 | 本次源码 | 已发布安装包／实机情况 |
|---|---|---|
| Windows／Mac 共用桌面源码 | 0.1.28.80，见 `talkman_version.py` | 正式分发渠道仍为77；Mac80为手动测试包，未制作Windows80安装器 |
| 通用卡带源码 | 72，见 `TalkMan-设备卡带/TalkMan-版本.lua` | 分发渠道仍为71；用户本次实机为71 |
| USB录音 | 按bulk IN端点的 `wMaxPacketSize` 读取，初代64字节 | 用户反馈80＋71已可以录音；不等于长期稳定性验收完成 |
| 设备重启 | 尚未修复 | 用户确认退出桌面后约15–30秒出现整机开机画面 |

本次是源码审阅交付，不修改升级服务器、网站下载、GitHub Release或设备。仓库按所有者要求公开，并采用Apache License 2.0；第三方组件保留原许可。`.rom` 是应用卡带，不是整机固件；本仓库不含官方Flutter客户端或USB底层固件的完整工程。

## 从哪里开始读

| 模块 | 文件／目录 | 负责内容 |
|---|---|---|
| 桌面入口 | `talkman_app.py` | Python＋Tkinter 主程序 |
| 设备业务 | `talkman_coloplay.py` | 设备状态、动作、连接与桌面交互协调 |
| USB连接 | `talkman_coloplay_usb.py` | 识别设备、握手、单线程收发、状态与按键 |
| CXUS帧 | `talkman_airdata.py` | 有界组帧、CRC、优先级、包头诊断 |
| USB音频 | `talkman_usb_audio.py`、`talkman_opus.py` | 显式录音会话、Opus解码、序号检查与停止 |
| 语音流程 | `talkman_voice.py`、`talkman_asr*.py` | 录音来源、识别、诊断和终止处理 |
| Wi-Fi | `talkman_coloplay_server.py`、`talkman_coloplay_discovery.py`、`talkman_coloplay_protocol.py` | WebSocket、发现、配对和消息校验 |
| 桌面设备模拟 | `talkman_coloplay_preview.py` | 设置内模拟器和输入状态机 |
| 目标输入 | `talkman_target*.py`、`talkman_input*.py` | Windows／Mac目标窗口、输入框定位与填入 |
| 卡带主入口 | `TalkMan-设备卡带/TalkMan-coloplay.clx` | 初始化、更新、绘图及USB／Wi-Fi切换 |
| 卡带USB | `TalkMan-设备卡带/TalkMan-USB通道.lua` | 应用握手、按键、状态租约、麦克风与断连清理 |
| 卡带交互 | 同目录 `TalkMan-手势.lua`、`TalkMan-更新组合.lua`、`TalkMan-屏幕.lua` | 四键／旋钮、三键长按与圆屏绘制 |
| 卡带更新 | 同目录 `TalkMan-卡带更新.lua`、桌面 `talkman_coloplay_official_rom.py` | 旧Wi-Fi自更新兼容，以及新三键下载／校验／交给官方程序导入 |

卡带使用定制Lua／coloX，电脑源码使用Python。`TalkMan-usb.clx` 是历史纯USB入口；当前通用卡带的构建入口是 `TalkMan-coloplay.clx`。

## USB对接边界

- USB VID／PID：`0x303A / 0x4005`。使用vendor数据接口3，bulk OUT `0x03`、bulk IN `0x83`。必须同时核对接口与端点，不能只凭设备名称判定成功。
- 仅同步状态、按键和按需录音，画面由卡带本地绘制；不占CDC串口、不推JPEG、不reset设备。多个程序不能同时消费同一个端点，官方程序共存仍需核对接口所有权。
- CXUS v2消息类型：音频16、音频控制18、按键19、状态20、应用HELLO21、PING32、PONG33；实际字段和CRC以 `talkman_airdata.py` 为准。
- 通用USB配对仅在当前会话内存中保存。仓库模板的 `url/token/config` 为空，不内置某台电脑的IP或口令，也不覆盖设备已保存的Wi-Fi配对。
- `airdata_ready()` 不表示电脑应用正在读取；`airdata_send()` 成功只表示入队。队列满可能丢旧数据，PONG的设备接收计数不能当作上行音频发送丢弃数。
- 设备麦克风仅作为TalkMan内可选来源，不提供系统级UAC麦克风。USB状态连接为主任务，不能为了收音破坏连接与官方程序共存。

## 本次相对已发布77的变化

1. **78／卡带72：** 三键长按由电脑准备官网ROM、校验SHA-256、唤起官方桌面并定位文件；仍需用户在官方程序中手动导入。新增模块及测试已一同提交。
2. **79：** 增加USB录音失败原因、开始确认、包数／解码统计、序号缺口、CRC／重同步及最近包头诊断；不保存原始音频、正文、会话口令或Key。
3. **80：** 将USB每次读取512字节改为端点单包大小，保留跨读取组帧、CRC和缺包保护；增加最多4条损坏包头元数据。Mac成品启动验收使用回环Wi-Fi配置，避免自动打开真实USB设备。

## 优先审阅：退出桌面后的整机重启

**已确认：** 用户在USB录音恢复后反馈，关闭TalkMan桌面约15–30秒，COLOPLAY整机重启。当前用户卡带71；源码72相较71的USB模块只修改三键更新提示，断连与音频清理逻辑相同。

**尚未确认：** 是否必须先录过设备麦克风才复现；固件reset reason、panic、watchdog、原生堆和TX队列数据均未取得。不能把现象直接定性为内存溢出，也不能把离线模拟结果当成实机修复。

建议先沿以下路径审阅，以下均为待验证风险，不是根因结论：

- 桌面 `UsbWire.close()` 先调用 `audio.disconnect()`，随后设置worker退出；确认音频stop命令是否确实发出，而不是只进入队列。
- 卡带 `u:step()` 在状态／HELLO过期约1500ms后 `reset_link()`；检查电脑不再消费时，这段租约内的按键／音频发送及固件TX残留。
- `reset_link()` 调用 `stop_audio()`；后者清麦克风缓冲后仍尝试发送 `stopped` 回复。确认无消费者时的行为。
- 官方文档中 `micpoll(1)` 是清缓冲，不能把它解释为已卸载麦克风驱动；没有已确认的通用卸载API。
- `airdata_clear('tx'/'all')` 影响共享队列，不能为规避重启直接清掉官方程序的数据。

此前79录音中断曾记录CRC错误1、重同步跳过64字节和普通队列序号缺1。离线截断构造能复现这一特征；完整混合流的所有切分位置及1–1024字节分段可正常解析。80之后用户已反馈可以录音，但没有由此证明固件截断的根因。

## 源码运行与验证

建议Python 3.12，带Tk；在独立克隆目录准备虚拟环境，安装 `TalkMan-requirements.txt`。源码入口为 `python talkman_app.py`，实际运行会按设置连接设备。开发测试可用 `TALKMAN_DATA_DIR` 指向临时目录，避免混用用户配置。

下面的回归使用假USB／Lua宿主和模拟音频，不需要真实麦克风、模型或Agent：

```text
python -m unittest test_talkman_usb_audio test_talkman_voice test_talkman_usb_handshake test_talkman_usb_gestures test_talkman_usb_descriptors test_talkman_usb_universal test_talkman_coloplay_official_rom test_talkman_coloplay_revision61 test_talkman_cartridge_update test_talkman_cartridge_idle -q
```

- 本次上传前Windows：上述174项通过；根目录Python源码可解析，本地 `talkman_*` 导入无缺失。
- 此前Mac80：Windows／M2各136项相关测试通过；244份源码双端摘要一致；Mac arm64启动10秒、签名结构、DMG挂载、品牌和Opus验收通过。未做Apple公证。
- 卡带72：已编译ROM为36,542字节，源码展开摘要与 `TalkMan-update.json` 一致，ROM摘要也一致。SHA-256：`20a264e3c03199042ddcd4236d6eb1c4f437e442250e384c4337458b4cd6d747`。
- 设备关闭桌面后重启仍待实机证据；本次源码上传不包含刷写、真实录音或Agent操作。

## 构建入口

### 卡带

从官方文档站取得coloX工具，保持 `TalkMan-设备卡带` 内的模块和空配对模板。Windows示例（替换工具路径）：

```powershell
python .\TalkMan-构建卡带.py --source .\TalkMan-设备卡带 --tool C:\ColoDev\coloXtool_win32\coloXtool.exe --output C:\ColoDev\talkman-build
```

输出 `TalkMan_COLOPLAY.rom`；脚本展开中文include以规避旧工具路径编码问题。文件名保持固定，通过官方COLOPLAY Desktop卡带管理导入。编译不会安装设备。`TalkMan-构建更新卡带.py` 另用于更新仓库内附ROM及metadata，含本机工具目录默认值，换电脑需按实际路径调整。

### Mac

在原生Apple Silicon环境准备Python／Tk和Opus，再执行：

```sh
brew install opus
python3 TalkMan-构建Mac.py --test-dmg
TalkMan-venv-mac/bin/python TalkMan-Mac成品验收.py
```

构建脚本会创建专用虚拟环境并安装依赖，输出位于 `dist-mac`；`--opus-library` 可指定已验证的arm64库。该模式生成未公证测试DMG。GitHub手动工作流为 `.github/workflows/TalkMan-COLOPLAY-Mac.yml`。

### Windows

入口 `TalkMan-构建Windows.ps1`，用 `-PythonExecutable` 指定Python 3.12，`-DependencyDirectory` 指向准备好的依赖根，`-OutputDirectory` 指定成品目录。脚本要求该根下有 `TalkMan-libs`（包含requirements依赖）与设备依赖路径，并要求PyInstaller可导入；不会自动完成全部依赖安装。

根目录带数字的 `.iss` 和准备脚本是对应历史版本的安装器／归档脚本，部分路径和文件名固定。不要直接运行77脚本就把产物当作80安装器。本次仅提供Windows80源码与测试，未重新构建Windows80安装器。

## 修改约束

保持当前实体按键定义、长按填入与二次发送确认、右下组合目标选择、设置内模拟器入口、USB／Wi-Fi二选一和应用外配置存储。不要把历史版本说明当作当前按键规范。涉及官方Flutter整合、USB底层固件或系统麦克风能力需另行划定范围。

不要提交真实API Key、配对配置、用户草稿、原始录音、个人日志或SSH凭证。现有构建产物中的通用ROM与公开CA证书是应用资源，不含私人配对配置。
