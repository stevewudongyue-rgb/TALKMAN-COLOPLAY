# TALKMAN COLOPLAY

**说话输入，在电脑上编辑，用 COLOPLAY 操作，确认后填入 Agent。**

配合初代 COLOPLAY 的独立桌面客户端：电脑负责文字编辑，设备旋钮、四个按键和圆屏负责操作与提醒。没有设备时也能使用桌面按钮和设置中的模拟器。

## 下载

**测试版 0.1.28.46 · 2026-11-02**

| 安装文件 | 适用设备 |
|---|---|
| [Windows 安装器](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.46/TalkMan-COLOPLAY-0.1.28.46-Windows-x64-Setup.exe) | Windows 10／11 x64 |
| [Mac 安装镜像 DMG](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.46/TalkMan-COLOPLAY-0.1.28.46-Mac-AppleSilicon.dmg) | Apple 芯片 M1 及以后，macOS 14 及以上 |
| [Windows 便携版](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.46/TalkMan-COLOPLAY-0.1.28.46-Windows-x64-Portable.zip) | 完整解压运行，保留 `_internal` 文件夹 |
| [校验值](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.46/TalkMan-SHA256SUMS.txt) | SHA-256 |

[本版发布说明](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/tag/coloplay-v0.1.28.46)

## 开始使用

1. Windows 运行安装器；Mac 打开 DMG，把 **TALKMAN COLOPLAY.app 拖到 Applications**，再从“应用程序”打开。均无需 Python。
2. 新安装默认使用**免费语音识别与星辰文字改写**，无需 API Key。点击“录音和覆盖”开始，再点停止；选中文字时覆盖选区。
3. “指令修改”按口述要求修改文字；AI 改写可先检查再确认。
4. 选择目标 Agent，先填入输入框，核对后再发送。

Mac 首次录音允许麦克风，填入 Agent 时开启辅助功能。此 DMG 是未公证测试版；若系统阻止，尝试打开后到 **系统设置 → 隐私与安全性 → 仍要打开**，无需关闭系统安全保护。Intel Mac 暂不支持。

联网时，主动录制的音频及待处理文字会发给在线服务。免费服务使用 SenseVoiceSmall 和星辰 Xing4.0-29B，受额度及服务策略限制。推荐长期使用者在 **设置 → 模型连接** 填入自己的千问 API Key，再关闭免费服务；按自己的账户计费。

## 设备操作

| 操作 | 功能 |
|---|---|
| 转旋钮 | 移动光标；AI 对照时滚动预览 |
| 短按旋钮 | 开始／结束选框 |
| 右下＋旋转 | 预选 Agent，松开右下确定 |
| 长按旋钮后松开 | 填入目标；按住时转动取消 |
| 左下 | 开始／结束录入 |
| 右下＋左下 | 指令修改 |
| 左上 | AI 改写／确认；长按 5 秒退出设备模式 |
| 右上 | 退格／删除选区，录入中取消 |
| 右下＋右上 | 清空全文，可撤销 |
| 右下＋左上 | 显示／收起窗口 |
| 右下＋短按旋钮 | 发送目标输入框已有内容 |

填入后的 **5 秒内，左上发送、右上取消**。左下可开启新录入，右下＋左下可开启新指令，均取消旧的发送机会。

**设置 → COLOPLAY** 提供连接配置、设备模拟器和图解教程；教程说明可悬停查看动画。支持骇客绿屏与 Nothing 黑白主题。

## Wi-Fi 修复与升级

本版修复设备发送任务停住后心跳缺失、电脑每四秒断开的问题。**USB＋Wi-Fi 模式必须同时更新桌面客户端与对应的配对卡带，只更新 EXE／DMG 不够。**新卡带逐连接重建完整收发循环；电脑保留脱敏断线日志。

通用安装包不含个人配对口令。已配对设备使用本机生成的新版卡带；换电脑需重新配置该电脑的连接。纯 USB 图流要求设备固件支持 airimg。免费模型接入与设备配对相互独立。

退出旧程序后覆盖安装即可，已保存的 Key、历史、快捷键与设备配置保留。Windows 数据存于 `%LOCALAPPDATA%\TalkMan-COLOPLAY`；Mac 使用独立用户目录和钥匙串。

## 验证范围

Windows 安装、覆盖升级、设置保留与成品检查通过。Wi-Fi 已取得旧版实机心跳超时证据；修复版通过故障注入和官方完整卡带模拟器单连接验证。Mac 通过原生构建、回归、成品启动及 DMG 挂载检查。

**修复版仍待实体 COLOPLAY 长时间复测；真实麦克风和真实 Agent 操作需在目标电脑验收。**
