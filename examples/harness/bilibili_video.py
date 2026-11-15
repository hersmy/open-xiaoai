"""Search public video metadata and open the official macOS Bilibili player.

Uses an anonymous buvid3 from Bilibili's own endpoint for catalog requests.
Never reads user cookies, runs a browser, clicks UI, or downloads video.
"""
import argparse
import html
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from voice_config import WAKE_PHRASE

APP = '/Applications/哔哩哔哩.app'
BASE = 'https://api.bilibili.com'
BV = r'BV[0-9A-Za-z]{10}'
_anonymous_id = None
_anonymous_lock = threading.Lock()


def fetch(path, params=None, anonymous=False):
    global _anonymous_id
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'}
    if anonymous:
        with _anonymous_lock:
            if _anonymous_id is None:
                obj = fetch('/x/frontend/finger/spi')
                identity = obj.get('data', {}).get('b_3', '')
                if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', identity):
                    raise ValueError('Anonymous catalog identity unavailable')
                _anonymous_id = identity
            headers['Cookie'] = 'buvid3=' + _anonymous_id
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('Video metadata too large')
    obj = json.loads(body)
    if not isinstance(obj, dict) or obj.get('code') != 0:
        raise ValueError('Video metadata request rejected')
    return obj


def normalize(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def parse_command(text):
    text = re.sub('^' + re.escape(WAKE_PHRASE) + r'[，,：:\s]*', '', text.strip())
    # Require an explicit play/open action or an entire video reference. A
    # question containing a BV/link must still be allowed to reach the Agent.
    match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:在)?(?:B站|b站|哔哩哔哩|bilibili)(?:上)?(?:搜索并播放|搜索播放|播放|打开)(.+)', text)
    if not match:
        match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:播放|打开)(?:B站|b站|哔哩哔哩|bilibili)(?:上|的)?(.+)', text)
    if not match:
        match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:播放|打开)视频[：:\s]*(.+)', text)
    if not match:
        match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:播放|打开)(' + BV + r'|https://(?:www\.)?bilibili\.com/video/\S+)[。！？!?\s]*', text)
    if match:
        target = match[1].strip().rstrip('。！？!? ')
    elif re.fullmatch(BV + r'|https://(?:www\.)?bilibili\.com/video/\S+', text):
        target = text
    else:
        return None
    if not target:
        return None
    quoted = target.startswith('《') and target.endswith('》')
    if not quoted and re.search(r'^(?:推荐|适合|根据|帮我挑)|然后|并且|顺便|再帮我', target):
        return None
    return {'kind': 'play_video', 'query': target.removeprefix('《').removesuffix('》')}


def reference(target):
    if re.fullmatch(BV, target):
        return target, 1
    if target.startswith('https://'):
        url = urllib.parse.urlsplit(target)
        match = re.fullmatch('/video/(' + BV + r')/?', url.path)
        if url.hostname not in {'bilibili.com', 'www.bilibili.com'} or not match or url.username or url.password:
            raise ValueError('Invalid Bilibili video link')
        page = urllib.parse.parse_qs(url.query).get('p', ['1'])[0]
        if not page.isdigit() or int(page) != 1:
            raise ValueError('Only first video part is verified')
        return match[1], 1
    return None


def play_url(video):
    if (not re.fullmatch(BV, video['bvid']) or type(video['aid']) is not int or video['aid'] <= 0
            or type(video['cid']) is not int or video['cid'] <= 0):
        raise ValueError('Invalid video identifiers')
    # Verified from this official client's handler, and by actual playback in
    # macOS 1.17.6. Mobile bilibili:// and bilipc://video are different routes.
    return 'bilipc://page/player?' + urllib.parse.urlencode({
        'type': 'ugc', 'bvid': video['bvid'], 'aid': video['aid'], 'cid': video['cid']})


def request(text, opener=subprocess.run, transport=fetch):
    command = parse_command(text)
    if command is None:
        return {'handled': False}
    started, timing = time.monotonic(), {}

    def result(ok, status, message, **extra):
        return {'handled': True, 'ok': ok, 'status': status, 'source': 'bilibili',
                'message': message, 'timing': dict(timing), **extra}

    try:
        target = command['query']
        try:
            direct = reference(target)
        except ValueError:
            return result(False, 'unsupported_reference', '请提供有效 BV 号或普通视频链接，目前只验证了第一分 P。')
        if direct:
            bvid = direct[0]
        else:
            # Bilibili's search returned no rows for this exact title with a
            # Chinese comma, but matched when punctuation separated keywords.
            keyword = re.sub(r'[^\w]+', ' ', target).strip()
            data = transport('/x/web-interface/search/type', {'search_type': 'video', 'keyword': keyword, 'page': 1}, anonymous=True)
            rows = data.get('data', {}).get('result', [])
            if not isinstance(rows, list):
                raise ValueError('Invalid search results')
            videos = {}
            for raw in rows[:50]:
                if not isinstance(raw, dict) or not isinstance(raw.get('bvid'), str) or not re.fullmatch(BV, raw['bvid']):
                    continue
                title = html.unescape(re.sub('<[^>]*>', '', raw.get('title', '')))
                videos[raw['bvid']] = {'bvid': raw['bvid'], 'title': title}
            exact = [v for v in videos.values() if normalize(v['title']) == normalize(target)]
            matches = exact or [v for v in videos.values() if len(normalize(target)) >= 6 and normalize(target) in normalize(v['title'])]
            timing['search_ms'] = round((time.monotonic() - started) * 1000, 1)
            if not matches:
                return result(False, 'not_found', 'B站未找到标题匹配的视频，请说完整标题或提供 BV 号。')
            if len(matches) != 1:
                return result(False, 'ambiguous', 'B站找到多个匹配视频，请提供 BV 号来确定要播放的版本。')
            bvid = matches[0]['bvid']
        detail = transport('/x/web-interface/view', {'bvid': bvid}).get('data', {})
        if detail.get('bvid') != bvid or not isinstance(detail.get('title'), str):
            raise ValueError('Invalid video metadata')
        video = {key: detail[key] for key in ['bvid', 'aid', 'cid', 'title']}
        url = play_url(video)
        timing['metadata_ms'] = round((time.monotonic() - started) * 1000, 1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return result(False, 'lookup_failed', 'B站查询暂时未完成或视频不可用，请稍后重试；也可以提供 BV 号。')
    try:
        opened = time.monotonic()
        response = opener(['/usr/bin/open', '-a', APP, url], capture_output=True, text=True, timeout=5)
        timing['open_ms'] = round((time.monotonic() - opened) * 1000, 1)
        if response.returncode != 0:
            return result(False, 'open_failed', '哔哩哔哩客户端未接受播放请求，请在电脑检查应用。')
    except (OSError, subprocess.TimeoutExpired):
        return result(False, 'open_failed', '调用哔哩哔哩客户端失败，请在电脑检查应用。')
    return result(True, 'opened', '已交给哔哩哔哩客户端打开《' + video['title'] + '》。', silent=True, video=video)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command', required=True)
    print(json.dumps(request(parser.parse_args().command), ensure_ascii=False))
