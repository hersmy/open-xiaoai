import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from netease_online import request, play_url, fetch


def song(identity, title='有何不可', artist='许嵩', st=0):
    return {'id': identity, 'name': title, 'ar': [{'name': artist}], 'privilege': {'st': st}}


class OnlineMusicTests(unittest.TestCase):
    def test_artist_request_uses_actual_performer_and_no_agent_selection(self):
        transport = Mock(return_value={'result': {'songs': [
            song(1, '模仿许嵩的歌', '其他歌手'), song(2, '其他歌曲', '许嵩', st=-200),
            song(3, '如果当时', '许嵩'), song(4, '有何不可', '许嵩')]}})
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        result = request('许嵩的歌', opener, transport)
        self.assertEqual(result['track']['id'], 3)
        self.assertEqual(transport.call_args.args[1]['s'], '许嵩')
        opener.assert_called_once()

    def test_search_then_native_url_without_browser_shell_or_agent(self):
        transport = Mock(return_value={'code': 200, 'result': {'songs': [song(167876)]}})
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        result = request('许嵩的《有何不可》', opener, transport)
        self.assertEqual(result['track']['id'], 167876)
        self.assertEqual(result['source'], 'netease_online')
        args = opener.call_args.args[0]
        self.assertEqual(args[:4], ['/usr/bin/open', '-g', '-a', '/Applications/NeteaseMusic.app'])
        payload = json.loads(base64.b64decode(args[-1].removeprefix('orpheus://')))
        self.assertEqual(payload, {'type': 'song', 'id': 167876, 'cmd': 'play'})
        self.assertNotIn('shell', opener.call_args.kwargs)
        self.assertEqual(opener.call_args.kwargs['timeout'], 25)
        self.assertEqual(result['status'], 'opened')

    def test_cover_artist_and_version_never_replace_requested_original(self):
        opener = Mock()
        transport = Mock(return_value={'result': {'songs': [
            song(1, '枫', '翻唱歌手'), song(2, '枫 (Live)', '周杰伦'),
            song(3, '枫 [原唱: 周杰伦]', '钢琴演奏者')]}})
        result = request('周杰伦的枫', opener, transport)
        self.assertEqual(result['status'], 'not_found')
        opener.assert_not_called()

    def test_cover_reference_resolves_original_rights_and_does_not_play_cover(self):
        cover = song(1, '枫 (钢琴版)', '钢琴演奏者')
        cover['originSongSimpleData'] = {'songId': 185912, 'name': '枫', 'artists': [{'name': '周杰伦'}]}
        transport = Mock(side_effect=[{'result': {'songs': [cover]}},
            {'songs': [song(185912, '枫', '周杰伦')], 'privileges': [{'id': 185912, 'st': -200}]}])
        opener = Mock()
        result = request('周杰伦的枫', opener, transport)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('不可播放', result['message'])
        opener.assert_not_called()

    def test_bare_title_with_different_artists_requires_clarification(self):
        opener = Mock()
        result = request('枫', opener, Mock(return_value={'result': {'songs': [song(1, '枫', 'A'), song(2, '枫', 'B')]}}))
        self.assertEqual(result['status'], 'ambiguous')
        opener.assert_not_called()

    def test_duplicate_releases_use_one_available_matching_version(self):
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        result = request('许嵩有何不可', opener,
                         Mock(return_value={'result': {'songs': [song(1, st=-200), song(2), song(3)]}}))
        self.assertEqual(result['track']['id'], 2)
        opener.assert_called_once()

    def test_anonymous_membership_flag_is_left_to_clients_own_login(self):
        raw = song(1)
        raw['fee'] = 1
        raw['privilege']['pl'] = 0
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        self.assertTrue(request('有何不可', opener, Mock(return_value={'result': {'songs': [raw]}}))['ok'])
        opener.assert_called_once()

    def test_timeout_bad_catalog_and_failed_open_never_report_success(self):
        for failure in [TimeoutError(), ValueError(), OSError()]:
            opener = Mock()
            result = request('有何不可', opener, Mock(side_effect=failure))
            self.assertFalse(result['ok'])
            opener.assert_not_called()
        result = request('有何不可', Mock(return_value=SimpleNamespace(returncode=1)),
                         Mock(return_value={'result': {'songs': [song(1)]}}))
        self.assertEqual(result['status'], 'open_failed')

    def test_play_url_only_accepts_positive_integer_id(self):
        for identity in [0, -1, '1;anything', True, None]:
            with self.assertRaises(ValueError):
                play_url(identity)

    def test_transport_checks_response_status_without_credentials(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"code":403}'
        with patch('netease_online.urllib.request.urlopen', return_value=response) as opened:
            with self.assertRaises(ValueError):
                fetch('/api/cloudsearch/pc', {'s': 'song'})
        req = opened.call_args.args[0]
        self.assertEqual(req.host, 'music.163.com')
        self.assertNotIn('Cookie', req.headers)
