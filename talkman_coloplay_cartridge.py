"""卡带版本与升级说明；版本只采用当前已配对设备的报告。"""
CARTRIDGE_BUILD = 68
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
        return f'当前卡带 {build} · 高于本包 {CARTRIDGE_BUILD}，请先升级桌面程序'
    return f'当前卡带 {build} · 与本包一致'


UPDATE_HELP = (
    '小屏仅在三键更新倒计时内显示卡带版本；桌面连接后也可在这里核对。\n'
    'Wi-Fi 卡带：空闲时同时按住右上、右下、左下 5 秒，倒计时内松开任一键取消。'
    '检查并安装已配对电脑附带的新版卡带；请先完成电脑端一键升级。'
    '保留配对，下载校验失败保留原卡带；更新期间不要断电。\n'
    '61 及以上 Wi-Fi 卡带支持三键更新；更早版本需先手动安装本机配对卡带。'
    '纯 USB 暂需手动更新；模拟器只演示手势，不安装实体卡带。\n'
    '手动更新：在「USB 连接与 Wi-Fi 备用」导出本机源码，用官方 coloX 工具编译并安装。'
    'Wi-Fi 文件名为 TalkMan_COLOPLAY.rom，纯 USB 为 TalkMan_COLOPLAY_USB.rom。'
    '更新失败可重新打开原卡带；已确认版本会先校验再进入，未确认版本不会自动重试。'
)
