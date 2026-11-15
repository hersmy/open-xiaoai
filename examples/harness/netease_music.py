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

ROOT = Path(os.getenv("XIAOAI_MUSIC_DIR", "/Volumes/PHILIPS/小米音箱/music"))
APP = "/Applications/NeteaseMusic.app"
FORMATS = {".ncm", ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".ape", ".wma"}


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


def request(text, root=ROOT, opener=subprocess.run):
    text = re.sub(r"^(?:大爱同学|小爱同学)[，,：:\s]*", "", text.strip())
    if re.fullmatch(r"(?:请)?(?:查看|列出|查询)?本地(?:曲库|音乐|歌曲)(?:列表)?[。？?]?", text) or text in {"曲库有多少首歌", "本地曲库有多少首歌"}:
        songs = library(root)
        return {"handled": True, "ok": True, "message": f"本地曲库共有{len(songs)}首歌，" + "；".join(s['artist']+'《'+s['title']+'》' for s in songs), "count": len(songs)}
    match = re.fullmatch(r"(?:请|帮我)?(?:播放|放一下|放首|放一首|我想听|我要听|听一下)(.+?)[。！!]?", text)
    if not match or re.search(r"新闻|天气|故事|视频|电视|播客|有声书", match[1]):
        return {"handled": False}
    query = re.sub(r"^(?:网易云音乐|网易云|本地的|本地)[的，,\s]*", "", match[1]).strip()
    songs = library(root)
    song = select(query, songs)
    if song is None:
        return {"handled": True, "ok": False, "message": "本地曲库未找到这首歌，请说出文件中的歌名或歌手。"}
    try:
        result = opener(["/usr/bin/open", "-a", APP, song['path']], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {"handled": True, "ok": False, "message": "网易云未接受打开请求，请在电脑查看网易云是否正常安装。"}
    except (OSError, subprocess.TimeoutExpired):
        return {"handled": True, "ok": False, "message": "调用网易云失败，请在电脑检查播放器。"}
    return {"handled": True, "ok": True, "status": "opened", "track": song,
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
