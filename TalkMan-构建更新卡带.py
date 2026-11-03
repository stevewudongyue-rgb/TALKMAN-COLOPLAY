"""用官方工具生成无配对更新 ROM 及校验清单；产物随 Windows/Mac 安装包分发。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from talkman_coloplay_cartridge import CARTRIDGE_BUILD

ROOT=Path(__file__).resolve().parent


def main():
    source=ROOT/'TalkMan-设备卡带'
    config=(source/'TalkMan-连接配置.lua').read_text('utf-8')
    if "token=''" not in config or "config=''" not in config or "url=''" not in config:
        raise ValueError('只能编译无配对的仓库模板，禁止分发私人配对 ROM。')
    output=ROOT.parent/f'TalkMan-COLOPLAY通用卡带{CARTRIDGE_BUILD}'
    subprocess.run([sys.executable,str(ROOT/'TalkMan-构建卡带.py'),'--source',str(source),'--output',str(output)],check=True)
    data=(output/'TalkMan_COLOPLAY.rom').read_bytes()
    if not 0<len(data)<=184*1024:raise ValueError('卡带大小超出规格。')
    spec=importlib.util.spec_from_file_location('cartridge_builder',ROOT/'TalkMan-构建卡带.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    flattened=module.flatten(source/'TalkMan-coloplay.clx').encode('utf-8')
    metadata=dict(build=CARTRIDGE_BUILD,size=len(data),sha256=hashlib.sha256(data).hexdigest(),
                  sha1=hashlib.sha1(data).hexdigest(),source_sha256=hashlib.sha256(flattened).hexdigest())
    shutil.copy2(output/'TalkMan_COLOPLAY.rom',source/'TalkMan_update.rom')
    (source/'TalkMan-update.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(f'通用更新卡带 {CARTRIDGE_BUILD} 已生成，未嵌入配对信息。')


if __name__=='__main__':main()
