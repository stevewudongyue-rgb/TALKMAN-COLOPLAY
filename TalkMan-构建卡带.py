"""将中文文件名的 include 合并后交给官方工具，规避 Windows 路径编码问题。"""
from pathlib import Path
import argparse
import re
import subprocess


def flatten(path, parents=()):
    path = path.resolve()
    if path in parents:
        raise ValueError(f'循环引用：{path.name}')
    text = path.read_text(encoding='utf-8')
    return re.sub(r'^#include\s+(.+?)\s*$',
        lambda m: f'-- 来源：{m[1]}\n' + flatten(path.parent/m[1], (*parents, path)),
        text, flags=re.M)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).parent/'TalkMan-设备卡带')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent.parent/'TalkMan-COLOPLAY-simulator')
    parser.add_argument('--usb', action='store_true', help='编译纯 USB 卡带')
    parser.add_argument('--tool', type=Path, default=Path(__file__).parent.parent/'TalkMan-COLOPLAY研究资料'/'TalkMan-coloX工具'/'coloXtool_win32'/'coloXtool.exe')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    name = 'TalkMan_COLOPLAY_USB' if args.usb else 'TalkMan_COLOPLAY'
    source = args.output/(name+'.clx')
    source.write_text(flatten(args.source/('TalkMan-usb.clx' if args.usb else 'TalkMan-coloplay.clx')), encoding='utf-8')
    rom = args.output/(name+'.rom')
    command = [str(args.tool.resolve()), '-d', str(args.output.resolve())+'/',
               '-r', source.name, '-b', str(rom.resolve())]
    result = subprocess.run(command, cwd=args.output, capture_output=True, timeout=60)
    log = result.stdout+result.stderr
    (args.output/'TalkMan-编译.log').write_bytes(log)
    if result.returncode or not rom.exists() or rom.stat().st_size == 0:
        raise RuntimeError(f'编译未成功，请查看 TalkMan-编译.log；退出码 {result.returncode}')
    print(f'已生成：{rom}（{rom.stat().st_size} 字节）')


if __name__ == '__main__':
    main()
