# 图标与字体来源

图标：官方 Lucide 1.8.0，https://lucide.dev ，https://github.com/lucide-icons/lucide 。使用官方已安装 JavaScript 包中的节点，渲染为透明 24×24 PNG；黑色、白色和录音状态红色各一组。来源许可证为 ISC，部分 Feather 衍生图标另适用 MIT；完整许可见同目录 `TalkMan-Lucide-LICENSE.txt`。

字体：Google Fonts 官方仓库的 Space Grotesk 可变字体和 Space Mono Regular，均为 SIL Open Font License 1.1。保留原始 TTF，不改字体内容；完整许可见 `fonts/TalkMan-SpaceGrotesk-OFL.txt` 与 `fonts/TalkMan-SpaceMono-OFL.txt`。

- Space Grotesk：https://github.com/google/fonts/tree/main/ofl/spacegrotesk
- Space Mono：https://github.com/google/fonts/tree/main/ofl/spacemono

字体加载：中文正文优先系统中文字体；Windows 支持只向当前进程注册随程序打包的 TTF，不修改系统字体安装。Mac 不注册字体，未安装 Space 字体时使用 PingFang SC、Helvetica Neue、Menlo 等系统字体。

「AI 整理一次」使用同库 Brain 大脑线条图标，24×24、2 像素圆头线条，与其他按钮一致。

程序运行和图标加载不需要网络。字体或图标缺失不阻止正文编辑。

0.1.26 新增「翻译全文」及「记住这个词」图标为本项目绘制的简洁线稿，沿用 24×24 网格和约 2 像素线宽；不增加字体或网络依赖。

0.1.27 新增删除选区（退格）与完成对勾图标，均为本项目自绘的 24×24 单色圆头线稿，约 1.75 像素线宽。

0.1.28 同步 Desktop `a2931fa` 的项目自绘麦克风＋文字光标素材（`app/`）；16–24 px 简化为麦克风，Mac 菜单栏使用透明模板。Windows 安装器及应用继续使用 COLOPLAY 专属图标，非外部品牌商标。

2026-11-04 统一品牌：沿用上述麦克风＋文字光标基础，增加 COLOPLAY 的圆形屏幕与四角按键几何。项目自绘，由 `TalkMan-生成品牌图标.py` 离线生成；主色 `#63E994`、底色 `#111111`，另有黑白单色。Windows ICO、窗口 PNG、Mac ICNS 与菜单栏模板统一轮廓；顶部点阵组合字标沿用 TALKMAN，连接节点改用同一设备符号。Steve已认可COLOPLAY此方案，随后明确iOS需采用独立轮廓与构图。上级 `TalkMan-品牌素材/` 中旧带iOS名称的整图仅保留为历史参考，不作为新的iOS图标规范。本地更新尚未进入已发布63安装包，也未重新打包此前64候选。
