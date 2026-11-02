# TALKMAN COLOPLAY

**说话输入，在电脑上编辑，用 COLOPLAY 操作，确认后填入 Agent。**

配合初代 COLOPLAY 的独立桌面客户端：电脑负责文字编辑，设备旋钮、四个按键和圆屏负责操作与提醒。没有设备时也能使用桌面按钮和设置中的模拟器。

## 下载

**测试版 0.1.28.47 · 2026-11-02**

| 安装文件 | 适用设备 |
|---|---|
| [Windows 安装器](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.47/TalkMan-COLOPLAY-0.1.28.47-Windows-x64-Setup.exe) | Windows 10／11 x64 |
| [Mac 安装镜像 DMG](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.47/TalkMan-COLOPLAY-0.1.28.47-Mac-AppleSilicon.dmg) | Apple 芯片 M1 及以后，macOS 14 及以上 |
| [Windows 便携版](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.47/TalkMan-COLOPLAY-0.1.28.47-Windows-x64-Portable.zip) | 完整解压运行，保留 `_internal` 文件夹 |
| [校验值](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/download/coloplay-v0.1.28.47/TalkMan-SHA256SUMS.txt) | SHA-256 |

[本版发布说明](https://github.com/stevewudongyue-rgb/TALKMAN-COLOPLAY/releases/tag/coloplay-v0.1.28.47)

## 开始使用

1. Windows 运行安装器；Mac 打开 DMG，把 **TALKMAN COLOPLAY.app 拖到 Applications**，再从“应用程序”打开。均无需 Python。
2. 新安装默认使用**免费语音识别与星辰文字改写**，无需 API Key。点击“录音和覆盖”开始，再点停止；选中文字时覆盖选区。
3. “指令修改”按口述要求修改文字；AI 改写可先检查再确认。
4. 选择目标 Agent，先填入输入框，核对后再发送。

Mac 首次连接设备允许“本地网络”，录音允许麦克风，填入 Agent 时开启辅助功能。此 DMG 是未公证测试版；若系统阻止，尝试打开后到 **系统设置 → 隐私与安全性 → 仍要打开**，无需关闭系统安全保护。Intel Mac 暂不支持。

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

## 换网络，只在桌面改地址

**先升级一次客户端与新版配对卡带，之后同一台电脑换网无需重新编译或安装卡带。**

1. 电脑和 COLOPLAY 加入可互通的局域网，保留 USB 数据线用于实体按键。
2. 在 **设置 → COLOPLAY → 设备连接** 启用服务和“自动寻找已配对电脑”。
3. 点击“本机 IP”旁的**刷新**，选择这台电脑的当前地址，再保存。
4. “设备 IP”通常留空；网络限制广播时，填入 COLOPLAY 当前 IP 后保存。

本机 IP 是电脑地址，设备 IP 是 COLOPLAY 地址，两者不要填反。若填写了设备 IP，换网后也要更新它。macOS 提示“本地网络”时允许；网络隔离或防火墙仍可能阻止连接。

新版设备会寻找持有原配对信息的电脑，忽略其它未配对电脑。已有连接不会被发现广播打断。关闭自动寻找可继续使用原固定地址方式。

旧卡带没有发现能力，首次必须更新。通用安装包不含个人配对口令；“首次配对 · 导出源码”导出的是源码，首次配对仍需用开发工具编译为 ROM。换到另一台未配对电脑也需重新配对。ROM 使用固定短文件名 `TalkMan_COLOPLAY.rom`，勿增加点分版本号。

沿用 .46 的 Wi-Fi 心跳恢复修复及断线日志。纯 USB 图流另需设备固件支持 airimg。免费模型服务与设备连接相互独立。

退出旧程序后覆盖安装即可，已保存的 Key、历史、快捷键与设备配置保留。Windows 数据存于 `%LOCALAPPDATA%\TalkMan-COLOPLAY`；Mac 使用独立用户目录和钥匙串。

## 验证范围

Windows 两次覆盖安装、测试 Key／历史／配对保留与成品检查通过。Mac 在 Apple 芯片环境通过 208 项回归、成品启动及 DMG 挂载检查。官方完整卡带模拟器用同一 ROM 在两个电脑地址间断线后自动重连，每个地址持续约 15 秒、各 17 次心跳。

公司局域网与实体 COLOPLAY 的换网、长时间连接仍待实测；上述模拟器结果不代表真机已验收。真实麦克风与 Agent 操作需在目标电脑测试。
