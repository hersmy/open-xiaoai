"""Install the fixed NetEase AX helper as an independently authorizable app."""
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
APP = Path.home() / 'Applications/小米音箱音乐控制.app'

def install():
    staged = ROOT / '.runtime/小米音箱音乐控制.app'
    contents = staged / 'Contents'
    executable = contents / 'MacOS/MusicControl'
    executable.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-O', str(ROOT / 'music_control.swift'), '-o', str(executable)], check=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'com.zhangyichi.xiaoai-music-control',
        'CFBundleName': '小米音箱音乐控制', 'CFBundleDisplayName': '小米音箱音乐控制',
        'CFBundleExecutable': 'MusicControl', 'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0.0', 'LSUIElement': True,
    }))
    subprocess.run(['codesign', '--force', '--sign', '-', str(staged)], check=True)
    APP.parent.mkdir(exist_ok=True)
    shutil.copytree(staged, APP, dirs_exist_ok=True)
    print(APP)

if __name__ == '__main__':
    install()
