"""Anonymous catalog search, followed by NetEase's native macOS play URL.

No browser/UI automation, credentials, audio downloads or third-party API host.
The catalog endpoint is undocumented; failures stay explicit and never call an Agent.
"""
import base64
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request

BASE = 'https://music.163.com'
APP = '/Applications/NeteaseMusic.app'


def normalize(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def fetch(path, params, timeout=5):
    req = urllib.request.Request(BASE + path,
        data=urllib.parse.urlencode(params).encode(),
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': BASE + '/'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('Catalog response too large')
    obj = json.loads(body)
    if not isinstance(obj, dict) or obj.get('code') != 200:
        raise ValueError('Catalog request rejected')
    return obj


def track(raw, privilege=None):
    if not isinstance(raw, dict):
        return None
    identity = raw.get('id', raw.get('songId'))
    artists = raw.get('ar', raw.get('artists', []))
    if type(identity) is not int or identity <= 0 or not isinstance(artists, list):
        return None
    names = [a['name'] for a in artists if isinstance(a, dict) and isinstance(a.get('name'), str)]
    title = raw.get('name')
    if not isinstance(title, str) or not title.strip() or not names:
        return None
    rights = privilege or raw.get('privilege') or {}
    if not isinstance(rights, dict) or type(rights.get('st', 0)) is not int:
        raise ValueError('Invalid catalog privileges')
    return {'id': identity, 'title': title, 'artist': '、'.join(names), 'artists': names,
            'source': 'netease_online', 'privilege': rights}


def matches(query, song):
    q, title = normalize(query), normalize(song['title'])
    if q == title:
        return True
    artists = song['artists'] + [''.join(song['artists'])]
    return any(q in {normalize(a) + '的' + title, normalize(a) + title,
                     title + normalize(a)} for a in artists)


def play_url(identity):
    # Native payload verified against Davied-H/ncm-cli internal/desktop/orpheus.go
    # and this Mac's NetEase 3.1.6, rather than mobile-only orpheus://song links.
    if type(identity) is not int or identity <= 0:
        raise ValueError('Invalid song ID')
    payload = json.dumps({'type': 'song', 'id': identity, 'cmd': 'play'}, separators=(',', ':'))
    return 'orpheus://' + base64.b64encode(payload.encode()).decode()


def request(query, opener=subprocess.run, transport=fetch):
    started = time.monotonic()
    timing = {}

    def result(ok, status, message, **extra):
        return {'handled': True, 'ok': ok, 'status': status, 'message': message,
                'source': 'netease_online', 'timing': dict(timing), **extra}

    try:
        # Spaces improve retrieval for artist/title requests. Matching below
        # still compares actual artist metadata, never a cover's title/alias.
        keyword = re.sub(r'^(.+?)的(.+)$', r'\1 \2', query).strip()
        obj = transport('/api/cloudsearch/pc', {'s': keyword, 'type': 1, 'limit': 30, 'offset': 0}, timeout=5)
        raw_songs = obj.get('result', {}).get('songs', [])
        if not isinstance(raw_songs, list):
            raise ValueError('Invalid catalog songs')
        candidates, originals = {}, {}
        for raw in raw_songs[:30]:
            song = track(raw)
            if song and matches(query, song):
                candidates[song['id']] = song
            original = track(raw.get('originSongSimpleData')) if isinstance(raw, dict) else None
            if original and matches(query, original):
                originals[original['id']] = original
        # Covers can refer to an unavailable original. Resolve that original's
        # own metadata/rights instead of accidentally playing the cover.
        missing = [identity for identity in originals if identity not in candidates][:3]
        if missing:
            ids = json.dumps([{'id': identity} for identity in missing], separators=(',', ':'))
            detail = transport('/api/v3/song/detail', {'c': ids}, timeout=3)
            privileges = {p['id']: p for p in detail.get('privileges', []) if isinstance(p, dict) and 'id' in p}
            for raw in detail.get('songs', []):
                song = track(raw, privileges.get(raw.get('id'))) if isinstance(raw, dict) else None
                if song and matches(query, song):
                    candidates[song['id']] = song
        timing['search_ms'] = round((time.monotonic() - started) * 1000, 1)
    except (OSError, ValueError, TypeError, AttributeError):
        timing['search_ms'] = round((time.monotonic() - started) * 1000, 1)
        return result(False, 'search_failed', '网易云在线搜索暂时不可用，请稍后再试。')
    if not candidates:
        return result(False, 'not_found', '本地未找到，网易云在线也未找到歌手和歌名匹配的版本，请补充歌手或完整歌名。')
    # A bare title shared by different performers needs clarification. Multiple
    # releases by the same artist may use the first matching catalog result.
    artists = {tuple(sorted(normalize(a) for a in s['artists'])) for s in candidates.values()}
    if len(artists) > 1:
        return result(False, 'ambiguous', '网易云找到多个歌手的同名歌曲，请同时说出歌手和歌名。')
    available = [s for s in candidates.values() if s['privilege'].get('st', 0) >= 0]
    if not available:
        song = next(iter(candidates.values()))
        return result(False, 'unavailable', f"找到了{song['artist']}的《{song['title']}》，但网易云当前返回该版本不可播放，请在客户端检查。")
    song = available[0]
    public_track = {key: value for key, value in song.items() if key not in {'privilege', 'artists'}}
    try:
        opened = time.monotonic()
        response = opener(['/usr/bin/open', '-g', '-a', APP, play_url(song['id'])],
                          capture_output=True, text=True, timeout=5)
        timing['open_ms'] = round((time.monotonic() - opened) * 1000, 1)
        if response.returncode != 0:
            return result(False, 'open_failed', '网易云未接受在线播放请求，请在电脑检查播放器。')
    except (OSError, subprocess.TimeoutExpired):
        return result(False, 'open_failed', '调用网易云在线播放失败，请在电脑检查播放器。')
    # App login/membership is used by the app itself, not copied by this code.
    # OS acceptance alone does not prove full-length or audible playback.
    return result(True, 'opened', f"已交给网易云在线播放{song['artist']}的《{song['title']}》。",
                  silent=True, track=public_track)
