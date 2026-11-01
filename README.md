# TALKMAN COLOPLAY

**说话输入，在电脑上编辑，用 COLOPLAY 操作，确认后填入 Agent。**

TALKMAN COLOPLAY 是配合初代 COLOPLAY 使用的独立桌面客户端。文字在电脑上阅读和修改；设备的旋钮、四个按键和圆屏负责快捷操作与状态提示。没有设备时，也可使用桌面按钮和设置中的设备模拟器。

## 下载

当前测试版：**0.1.28.45 · 2026-11-02**

| 文件 | 用途 |
|---|---|
| [Windows 安装器](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.45/TalkMan-COLOPLAY-0.1.28.45-Windows-x64-Setup.exe) | 推荐；适用于 Windows 10／11 x64，无需安装 Python |
| [Windows 便携版](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.45/TalkMan-COLOPLAY-0.1.28.45-Windows-x64-Portable.zip) | 完整解压后运行 TalkMan.exe，须保留旁边的 `_internal` 文件夹 |
| [Mac 构建源码包](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.45/TalkMan-COLOPLAY-0.1.28.45-Mac-Build.zip) | 面向 Apple 芯片 Mac；不是已编译的 .app／.dmg，需在 Mac 上构建 |
| [文件校验值](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.45/TalkMan-SHA256SUMS.txt) | SHA-256 校验 |

[查看本版发布说明](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/tag/coloplay-v0.1.28.45)

## 第一次使用

1. 安装并打开程序。全新安装默认接入**免费语音识别和星辰文字改写**，无需填写 API Key，也无需逐台申请开通。
2. 点击“录音和覆盖”，说完再点一次停止；也支持按住说、松开停止。选中文字时覆盖选区，没有选区时在光标处输入。
3. 选一段文字后使用“指令修改”，说出修改要求；也可以用 AI 整理，检查改写结果后确认。
4. 选择目标 Agent，点击“填入”。核对后再发送。

联网时，主动录制的音频和需要处理的文字会发送到在线服务。免费服务目前使用 SenseVoiceSmall 识别语音、星辰 Xing4.0-29B 处理文字；受额度和服务策略影响，可能调整、暂停或不可用。请求失败会保留原文，不自动切换到收费模型。

推荐长期使用者在 **设置 → 模型连接** 配置自己的千问 API Key，然后关闭“使用免费服务”；用量按自己的千问账户计费。

## COLOPLAY 操作

以下为当前初代设备的操作定义，需要匹配的 TalkMan 卡带与桌面客户端。

| 操作 | 功能 |
|---|---|
| 旋转旋钮 | 在预输入框中前后移动光标；改写对照时滚动预览 |
| 短按旋钮 | 开始／结束选框；选框过程中旋转可扩展或缩小选区 |
| 按住右下＋旋转 | 预选 Agent，松开右下时确定目标 |
| 长按旋钮后松开 | 填入目标输入框，不直接发送；按住时旋转会取消 |
| 左下 | 开始／结束录入 |
| 右下＋左下 | 指令修改 |
| 左上 | AI 改写／确认改写；长按 5 秒退出设备模式，桌面客户端保留 |
| 右上 | 退格或删除选区；录入／处理过程中取消 |
| 右下＋右上 | 清空编辑区，可撤销 |
| 右下＋左上 | 显示／隐藏桌面窗口 |
| 右下＋短按旋钮 | 在允许的状态下，发送目标输入框已有内容；长按或转动取消 |

**填入成功后的 5 秒内：**短按左上确认发送，右上取消。左下可直接开始新录入，右下＋左下可开始新指令，这两种操作会取消上一次发送机会。

**设置 → COLOPLAY** 可以配置设备连接、重看图解教程；悬停教程中的数字或说明可查看小动画。设备模拟器也放在设置中。

通用安装器不刷写设备，也不包含任何人的配对口令或私人 ROM。新电脑需配置自己的设备连接，免费模型接入不依赖 COLOPLAY 是否连接。现有的 USB／Wi-Fi 协议继续支持；纯 USB 图流需要设备固件支持 airimg。

## 升级与设置

退出旧客户端后，直接覆盖安装，无需先卸载。已保存的 API Key、模型选择、历史、快捷键和设备配对会保留；已有用户明确关闭免费服务或使用自备模型的选择也会保留。仅临时使用、未勾选本机保存的 Key 不会跨重启恢复。

Windows 的用户数据位于 `%LOCALAPPDATA%\TalkMan-COLOPLAY`，与程序安装目录分开。免费连接身份自动生成并由系统加密保存；安装包不包含供应商 Key、共享用户身份或管理员私钥。

## Mac 状态

自动免费接入和钥匙串保存已纳入同一套 Mac 代码。当前提供的是 **Apple 芯片 Mac 的完整构建包**，尚未在 Mac 上生成、运行和验收本版 .app／.dmg。

在 Mac 安装 python.org 的 Python 3.12 或 3.13，解压构建包，在该目录执行：

```bash
bash TalkMan-构建Mac.command --test-dmg
```

生成的应用和测试镜像位于 `dist-mac`。此测试构建未完成 Apple 公证；首次录音需要麦克风权限，填入 Agent 需要辅助功能权限。构建不要求填写模型 Key。

## 本版验证

- 两份空白安装身份经公网自动接入，免费合成语音识别和星辰改写实测通过。
- Windows 成品 52 项界面自检、成品联网检查通过。
- 隔离目录连续两次覆盖安装，测试 Key、设置、历史及配对逐字节保留；安装后运行和卸载通过。
- Wi-Fi 状态推送和心跳调度已修正并通过隔离测试；实体设备长时间稳定性仍待验收。Mac 原生运行、真实麦克风及真实 Agent 操作不属于本轮验证结果。
