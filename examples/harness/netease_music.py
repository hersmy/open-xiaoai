"""Find local music and open it with the installed NetEase Music application.

NCM stays unchanged: NetEase handles decoding and the Mac handles audio output.
An accepted OS open request is not proof of audible playback.
"""
import argparse
import json
import os
from pathlib import Path
import random
import re
import subprocess
import time
import uuid
import tempfile
from netease_online import request as online_request
from voice_config import WAKE_PHRASE
from netease_player import open_media

ROOT = Path(os.getenv("XIAOAI_MUSIC_DIR", "/Volumes/PHILIPS/小米音箱/music"))
APP = "/Applications/NeteaseMusic.app"
FORMATS = {".ncm", ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".ape", ".wma"}
STATE = Path(__file__).resolve().parent / '.runtime/netease-state.json'
CONTROL = Path.home() / 'Applications/小米音箱音乐控制.app/Contents/MacOS/MusicControl'
CONTROLS = {
    'pause': {'暂停', '暂停播放', '暂停音乐', '暂停一下', '先暂停', '停一下', '别放了', '不放了',
              '停止', '停止播放', '停止音乐', '关闭音乐', '关掉音乐', '音乐暂停', '把音乐暂停', '把音乐关掉'},
    'resume': {'继续', '继续播放', '继续放歌', '继续音乐', '恢复播放', '恢复音乐', '播放'},
    'next': {'下一首', '下首', '下一曲', '换一首', '换首歌', '换歌', '切歌'},
    'previous': {'上一首', '上首', '上一曲', '前一首'},
}


def control_player(action):
    if not CONTROL.is_file():
        return {'ok': False, 'status': 'unavailable', 'message': '音乐控制程序未安装，请在电脑完成安装。'}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='music-control-', dir=STATE.parent) as tmp:
            output = Path(tmp) / 'result.json'
            errors = Path(tmp) / 'stderr'
            output.touch(mode=0o600)
            errors.touch(mode=0o600)
            # Launch the authorized application as an application. Direct exec
            # from launchd attributes Accessibility access to the parent service.
            result = subprocess.run(['/usr/bin/open', '-n', '-W', '-g', '-a', str(CONTROL.parents[2]),
                '--stdout', str(output), '--stderr', str(errors), '--args', action],
                capture_output=True, text=True, timeout=10)
            data = json.loads(output.read_text())
            if not isinstance(data, dict) or (result.returncode != 0 and data.get('ok')):
                raise ValueError('Invalid controller result')
            return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'ok': False, 'status': 'failed', 'message': '网易云控制没有完成，请在电脑检查播放器。'}


def save_track(path, song):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temp, 'x') as stream:
            os.chmod(temp, 0o600)
            json.dump(song, stream, ensure_ascii=False)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def normalize(text):
    return "".join(c.lower() for c in text if c.isalnum())


def library(root=ROOT):
    if not root.is_dir():
        return []
    songs = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.name.startswith(".") or file.suffix.lower() not in FORMATS:
            continue
        real = file.resolve()
        if not real.is_relative_to(root.resolve()):
            continue
        artist, sep, title = file.stem.partition(" - ")
        if not sep:
            title, artist = artist, ""
        songs.append({"title": title, "artist": artist, "path": str(real), "format": file.suffix[1:].lower()})
    return songs


def select(query, songs):
    q = normalize(query)
    if q in {"", "本地音乐", "本地歌曲", "音乐", "一首歌", "首歌", "歌曲", "歌", "随机音乐"}:
        return random.choice(songs) if songs else None
    exact = [s for s in songs if q in {normalize(s['title']), normalize(s['artist']+'的'+s['title']),
                                     normalize(s['artist']+s['title']), normalize(s['title']+s['artist'])}]
    if len(exact) == 1:
        return exact[0]
    artist_query = re.sub(r"的(?:歌|歌曲|音乐)$", "", query)
    by_artist = [s for s in songs if normalize(s['artist']) == normalize(artist_query)]
    if by_artist:
        return random.choice(by_artist)
    partial = [s for s in songs if len(q) >= 2 and q in normalize(s['artist']+s['title'])]
    return partial[0] if len(partial) == 1 else None


def parse_command(text):
    """Accept simple music requests only; leave compound/inferential work to the Agent."""
    text = re.sub('^(?:' + re.escape(WAKE_PHRASE) + r'|大爱同学|小爱同学)[，,：:\s]*', '', text.strip())
    text = text.rstrip("。！？!?，, ")
    bare = re.sub(r'^(?:请帮我|帮我|请)', '', text)
    for action, phrases in CONTROLS.items():
        if bare in phrases or any(re.fullmatch('(?:' + re.escape(phrase) + r'[，,\s]*){2,}', bare) for phrase in phrases):
            return {'kind': action}
    if text in {"曲库有多少首歌", "本地曲库有多少首歌"}:
        return {"kind": "count"}
    if re.fullmatch(r"(?:请)?(?:查看|列出|查询)?本地(?:曲库|音乐|歌曲)(?:列表)?", text):
        return {"kind": "list"}
    match = re.fullmatch(r"(?:请帮我|请|帮我)?(?:播放|放一下|放首|放一首|我想听|我要听|听一下)(.+?)", text)
    if not match or re.search(r"新闻|天气|故事|视频|电视|播客|有声书", match[1]):
        return None
    # A song can contain 如果 (e.g. 如果当时 / 如果没有你). Only a
    # complete conditional action needs inference, not that word on its own.
    if re.search(r'(?:如果|要是|假如).+(?:就|则|的话)(?:请|再|先|帮我)?(?:播放|放|听|换|暂停|继续|停止|打开|关|跳过|推荐|搜索|用|选)', match[1]):
        return None
    if re.search(r"[,，;；。]|推荐|适合|根据|分析|整理|搜索|上网|下载|创建|生成|歌单|然后|并且|同时|再帮|顺便|为什么|怎么|如何|什么|哪些|并(?:帮|把|关|开|执行|告诉|播放|给)|接着|之后|之前", match[1]):
        return None
    query = re.sub(r"^(?:网易云音乐|网易云|本地的|本地)[的，,\s]*", "", match[1]).strip()
    return {"kind": "play", "query": query, 'local_only': match[1].startswith(('本地', '本地的'))}


def request(text, root=ROOT, opener=open_media, state_path=None, controller=control_player,
            online=online_request):
    command = parse_command(text)
    if command is None:
        return {"handled": False}
    if command['kind'] in {'pause', 'resume'}:
        result = controller(command['kind'])
        message = '已暂停。' if command['kind'] == 'pause' else '已继续播放。'
        return {'handled': True, **result, 'silent': bool(result.get('ok')),
                'message': message if result.get('ok') else result.get('message', '播放控制未完成。')}
    if state_path is None and Path(root) == ROOT:
        state_path = STATE
    started = time.monotonic()
    songs = library(root)
    lookup_ms = round((time.monotonic() - started) * 1000, 1)
    if command['kind'] in {'count', 'list'}:
        message = f"本地曲库共有{len(songs)}首歌。"
        if command['kind'] == 'list':
            message += "；".join(s['artist']+'《'+s['title']+'》' for s in songs)
        return {"handled": True, "ok": True, "message": message, "count": len(songs)}
    if command['kind'] in {'next', 'previous'}:
        try:
            last_path = json.loads(state_path.read_text())['path'] if state_path is not None else None
        except (OSError, ValueError, KeyError, TypeError):
            last_path = None
        index = next((i for i, song in enumerate(songs) if song['path'] == last_path), None)
        if index is None:
            return {'handled': True, 'ok': False, 'message': '请先点播一首本地歌曲，再切换上一首或下一首。'}
        step = 1 if command['kind'] == 'next' else -1
        song = songs[(index + step) % len(songs)]
    else:
        song = select(command['query'], songs)
    if song is None:
        if not command.get('local_only'):
            result = online(command['query'], opener=opener)
            if result.get('ok') and result.get('track'):
                try:
                    save_track(state_path, result['track'])
                except OSError:
                    pass  # Never repeat a playback request because bookkeeping failed.
            return result
        return {"handled": True, "ok": False, "message": "本地曲库未找到这首歌，请说出文件中的歌名或歌手。"}
    try:
        opened = time.monotonic()
        result = opener(["/usr/bin/open", "-g", "-a", APP, song['path']], capture_output=True, text=True, timeout=25)
        open_ms = round((time.monotonic() - opened) * 1000, 1)
        if result.returncode != 0:
            return {"handled": True, "ok": False, "message": "网易云未接受打开请求，请在电脑查看网易云是否正常安装。"}
    except (OSError, subprocess.TimeoutExpired):
        return {"handled": True, "ok": False, "message": "调用网易云失败，请在电脑检查播放器。"}
    try:
        save_track(state_path, song)
    except OSError:
        # The open already happened. Never retry it because bookkeeping failed.
        return {"handled": True, "ok": True, "status": "opened", "silent": True, "track": song,
                "message": "已打开歌曲，但未能保存切歌位置。"}
    return {"handled": True, "ok": True, "status": "opened", "track": song,
            "silent": True,
            "timing": {"lookup_ms": lookup_ms, "open_ms": open_ms},
            "message": f"已交给网易云打开{song['artist']}的《{song['title']}》。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        result = {"songs": library()}
    elif args.command:
        result = request(args.command)
    else:
        parser.error("use --list or --command")
    print(json.dumps(result, ensure_ascii=False))
