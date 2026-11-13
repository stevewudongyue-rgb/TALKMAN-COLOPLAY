"""卡带版本与升级说明；版本只采用当前已配对设备的报告。"""
CARTRIDGE_BUILD = 72
FILL_HOLD_MS = 500


def valid_build(value):
    return value if type(value) is int and 1 <= value <= 100000 else None


def version_status(build, *, connected=False, simulator=False):
    if simulator:
        return f'模拟器 {CARTRIDGE_BUILD} · 不代表实体卡带版本'
    if not connected:
        return f'设备未连接 · 本包卡带 {CARTRIDGE_BUILD}'
    build = valid_build(build)
    if build is None:
        return f'当前卡带未报告版本 · 本包卡带 {CARTRIDGE_BUILD}'
    if build < CARTRIDGE_BUILD:
        return f'当前卡带 {build} · 可更新至 {CARTRIDGE_BUILD}'
    if build > CARTRIDGE_BUILD:
        return f'当前卡带 {build} · 高于本包 {CARTRIDGE_BUILD}，无需降级'
    return f'当前卡带 {build} · 与本包一致'


UPDATE_HELP = (
    '小屏仅在三键更新倒计时内显示卡带版本；桌面连接后也可在这里核对。\n'
    'USB 或 Wi-Fi 已连接电脑时，空闲同时按住右上、右下、左下 5 秒，松开任一键取消。'
    '电脑会从 TALKMAN 官网下载最新 ROM 并校验，打开官方 COLOPLAY Desktop 与 ROM 所在文件夹。'
    '请在官方程序的卡带管理中手动选择“上传 ROM”并安装；三键不会直接刷写设备。'
    '官网版本可能落后于本包；以电脑下载结果和设备当前版本为准。'
    '旧卡带的 Wi-Fi 三键仍使用原自更新协议，先升级卡带才能使用新流程。\n'
    '模拟器只演示手势，不下载或安装实体卡带。\n'
    '手动更新：下载通用卡带 TalkMan_COLOPLAY.rom，用官方 COLOPLAY Desktop 的卡带管理导入并启动。'
    '再选择 USB，保存并重新连接；不需要为每台电脑编译卡带。'
    '更新失败可重新打开原卡带，未确认版本不会自动重试。'
)
