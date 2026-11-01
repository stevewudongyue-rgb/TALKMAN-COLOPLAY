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
            assert (Path(mount)/'Applications').is_symlink()
            assert list(installed.rglob('TalkMan-free-service-ca.pem'))
            assert not list(installed.rglob('*.dpapi'))
            assert not list(installed.rglob('TalkMan-配对.json'))
            subprocess.run(['codesign','--verify','--deep','--strict',str(installed)],check=True)
        finally:subprocess.run(['hdiutil','detach',mount],check=True)
    packages=[image,root/'TalkMan-Mac-AppleSilicon.zip']
    (root/'TalkMan-SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in packages),encoding='utf-8')
    report={'通过':True,'版本':FULL_VERSION,'架构':architecture,'检查':['应用持续启动10秒，无异常回调','临时身份，免费服务关闭','应用签名完整','DMG挂载及内含应用版本、公开CA、Applications入口正确'],
            '未执行':['真实麦克风','真实Agent填入发送','实体COLOPLAY','Apple公证']}
    (root/'TalkMan-Mac验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
