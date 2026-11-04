"""原生 Apple 芯片检查成品启动、签名和 DMG；隔离草稿并关闭在线服务。"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time

from talkman_version import FULL_VERSION, VERSION, BUILD
from talkman_coloplay_cartridge import CARTRIDGE_BUILD


def check_brand_assets(app, info):
    """核对实际包内引用、资源摘要及 Cocoa 解码，不改动只读安装镜像。"""
    import AppKit

    source = Path('TalkMan-assets/app')
    resources = app/'Contents/Resources'
    icon_name = info['CFBundleIconFile']
    if not icon_name.endswith('.icns'):
        icon_name += '.icns'
    bundle_icon = resources/icon_name
    assert bundle_icon.read_bytes() == (source/'TalkMan.icns').read_bytes()
    hashes = {}
    for name in ('TalkMan.icns', 'TalkMan-menu-18.png', 'TalkMan-menu-36.png'):
        packed = resources/'TalkMan-assets/app'/name
        payload = packed.read_bytes()
        assert payload == (source/name).read_bytes(), name
        native = AppKit.NSImage.alloc().initWithContentsOfFile_(str(packed))
        assert native is not None and native.isValid(), name
        if name.startswith('TalkMan-menu-'):
            native.setSize_((18.0, 18.0))
            native.setTemplate_(True)
            assert native.isTemplate()
        hashes[name] = hashlib.sha256(payload).hexdigest()
    return hashes


def main():
    root = Path('dist-mac').resolve()
    app = root/'TALKMAN COLOPLAY.app'
    executable = app/'Contents/MacOS/TalkMan'
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
    architecture = subprocess.check_output(['file',str(executable)],text=True).strip()
    assert 'arm64' in architecture
    with tempfile.TemporaryDirectory(prefix='TalkMan-COLOPLAY验收-') as folder:
        (Path(folder)/'interface.json').write_text('{"free_mode":false,"guide_seen":true}',encoding='utf-8')
        env = dict(os.environ,TALKMAN_DATA_DIR=folder)
        with (root/'TalkMan-启动检查.log').open('w') as log:
            process=subprocess.Popen([str(executable)],env=env,stdout=log,stderr=log)
            try:
                time.sleep(10)
                assert process.poll() is None,'应用提前退出'
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        output=(root/'TalkMan-启动检查.log').read_text(errors='replace')
        assert 'Traceback' not in output and 'Exception in Tkinter callback' not in output,output
    image=root/'TalkMan-Mac-AppleSilicon-测试版.dmg'
    subprocess.run(['hdiutil','verify',str(image)],check=True)
    with tempfile.TemporaryDirectory(prefix='TalkMan-mount-') as mount:
        subprocess.run(['hdiutil','attach','-readonly','-nobrowse','-mountpoint',mount,str(image)],check=True)
        try:
            installed=Path(mount)/'TALKMAN COLOPLAY.app'
            info=plistlib.loads((installed/'Contents/Info.plist').read_bytes())
            assert info['CFBundleIdentifier']=='com.cololight.talkman.coloplay'
            assert info['CFBundleShortVersionString']==VERSION and info['CFBundleVersion']==str(BUILD)
            assert info['NSMicrophoneUsageDescription']
            brand_assets = check_brand_assets(installed, info)
            assert (Path(mount)/'Applications').is_symlink()
            assert list(installed.rglob('TalkMan-free-service-ca.pem'))
            assert not list(installed.rglob('*.dpapi'))
            assert not list(installed.rglob('TalkMan-配对.json'))
            cartridge=next(installed.rglob('TalkMan-update.json'))
            metadata=json.loads(cartridge.read_text('utf-8'))
            payload=(cartridge.parent/'TalkMan_update.rom').read_bytes()
            assert metadata['build']==CARTRIDGE_BUILD and metadata['size']==len(payload)
            assert hashlib.sha256(payload).hexdigest()==metadata['sha256']
            assert "token=''" in (cartridge.parent/'TalkMan-连接配置.lua').read_text('utf-8')
            subprocess.run(['codesign','--verify','--deep','--strict',str(installed)],check=True)
        finally:subprocess.run(['hdiutil','detach',mount],check=True)
    packages=[image,root/'TalkMan-Mac-AppleSilicon.zip']
    (root/'TalkMan-SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in packages),encoding='utf-8')
    report={'通过':True,'版本':FULL_VERSION,'架构':architecture,'品牌资源SHA256':brand_assets,'检查':['应用持续启动10秒，无异常回调','临时身份，免费服务关闭','应用签名完整','DMG挂载及内含应用版本、公开CA、Applications入口正确','安装镜像内图标引用及资源逐字节一致，AppKit解码及菜单模板通过'],
            '未执行':['真实麦克风','真实Agent填入发送','实体COLOPLAY','Apple公证']}
    (root/'TalkMan-Mac验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
