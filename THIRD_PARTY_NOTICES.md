# 第三方组件与许可

TALKMAN COLOPLAY 的项目源码按根目录 [LICENSE](LICENSE) 中的 Apache License 2.0 提供。以下素材、依赖及其衍生部分保留原有许可与版权声明，项目许可不替代它们的条款。

| 组件 | 仓库位置 | 许可与说明 |
|---|---|---|
| Lucide图标及部分Feather衍生图标 | `TalkMan-assets/icons/` | ISC／MIT，完整文本见 [TalkMan-Lucide-LICENSE.txt](TalkMan-assets/TalkMan-Lucide-LICENSE.txt)；项目自绘部分见资源来源说明 |
| Space Grotesk | `TalkMan-assets/fonts/SpaceGrotesk.ttf` | SIL Open Font License 1.1，见 [字体许可](TalkMan-assets/fonts/TalkMan-SpaceGrotesk-OFL.txt) |
| Space Mono | `TalkMan-assets/fonts/SpaceMono-Regular.ttf` | SIL Open Font License 1.1，见 [字体许可](TalkMan-assets/fonts/TalkMan-SpaceMono-OFL.txt) |
| 通过PyOgg分发的原生音频库／Opus | `TalkMan-assets/native/windows-x64/` | 分发许可见 [PyOgg-LICENSE.txt](TalkMan-assets/native/windows-x64/PyOgg-LICENSE.txt)；各原生库条款见 [PyOgg-COPYING.txt](TalkMan-assets/native/windows-x64/PyOgg-COPYING.txt) |
| Python、Tk、PyInstaller及Python依赖 | 构建环境、`TalkMan-requirements.txt` | 按各上游包随附许可提供；构建安装包时保留其许可文件 |
| Mac Opus库 | 由构建脚本使用Homebrew或指定路径取得 | 属于Opus上游组件；本仓库不授予替代许可 |

图标与字体的来源、项目自绘素材说明见 [TalkMan-资源来源.md](TalkMan-assets/TalkMan-资源来源.md)。字体许可与本仓库同版本字体对应，字体文件未修改。

COLOPLAY整机固件、官方编译器、官方Flutter桌面程序及模型托管服务不是本仓库完整交付的一部分。卡带内的 `colox_*.lua` 为随应用使用的coloX网络适配代码，保留文件中已有说明。依赖这些平台或服务不表示本项目重新许可其独立工程或运营资源。
