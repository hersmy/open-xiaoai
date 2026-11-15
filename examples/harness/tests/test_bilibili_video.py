import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from bilibili_video import parse_command, request, play_url, fetch

TARGET = 'BV18V4i6WEje'
TITLE = '女生视角告诉你，女生是怎么感受到被爱的'
DETAIL = {'bvid': TARGET, 'title': TITLE, 'aid': 117567549082566, 'cid': 43371006088}


class VideoTests(unittest.TestCase):
    def test_explicit_video_only_and_title_punctuation_does_not_mean_reasoning(self):
        for text in ['在B站播放' + TITLE, '播放B站的《' + TITLE + '》', '播放视频' + TITLE,
                     '你好小七，播放' + TARGET, TARGET]:
            self.assertEqual(parse_command(text)['kind'], 'play_video', text)
        for text in ['播放许嵩的如果当时', '暂停播放', '介绍一下' + TARGET, '在B站播放适合学习的视频',
                     '在B站播放这个然后关灯']:
            self.assertIsNone(parse_command(text), text)

    def test_direct_bv_uses_metadata_and_native_client_url_without_search(self):
        transport = Mock(return_value={'data': DETAIL})
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        result = request('播放' + TARGET, opener, transport)
        self.assertTrue(result['ok'])
        transport.assert_called_once_with('/x/web-interface/view', {'bvid': TARGET})
        args = opener.call_args.args[0]
        self.assertEqual(args[:3], ['/usr/bin/open', '-a', '/Applications/哔哩哔哩.app'])
        url = urlsplit(args[-1])
        self.assertEqual((url.scheme, url.netloc, url.path), ('bilipc', 'page', '/player'))
        self.assertEqual(parse_qs(url.query)['bvid'], [TARGET])
        self.assertEqual(parse_qs(url.query)['aid'], [str(DETAIL['aid'])])
        self.assertNotIn('shell', opener.call_args.kwargs)
        self.assertEqual(result['status'], 'opened')

    def test_web_link_tracking_is_not_forwarded_to_native_client(self):
        opener = Mock(return_value=SimpleNamespace(returncode=0))
        result = request('播放https://www.bilibili.com/video/' + TARGET + '?vd_source=tracking',
                         opener, Mock(return_value={'data': DETAIL}))
        self.assertTrue(result['ok'])
        self.assertNotIn('vd_source', opener.call_args.args[0][-1])

    def test_search_exact_title_beats_unrelated_first_result_and_strips_highlight(self):
        transport = Mock(side_effect=[{'data': {'result': [
            {'bvid': 'BV1xx411c7mD', 'title': '无关的第一条'},
            {'bvid': TARGET, 'title': '<em>' + TITLE + '</em>'}]}}, {'data': DETAIL}])
        result = request('在B站播放' + TITLE, Mock(return_value=SimpleNamespace(returncode=0)), transport)
        self.assertEqual(result['video']['bvid'], TARGET)
        self.assertTrue(transport.call_args_list[0].kwargs['anonymous'])
        self.assertEqual(transport.call_args_list[0].args[1]['keyword'], TITLE.replace('，', ' '))

    def test_multiple_matches_do_not_open_first_result(self):
        transport = Mock(return_value={'data': {'result': [
            {'bvid': TARGET, 'title': TITLE}, {'bvid': 'BV1xx411c7mD', 'title': TITLE}]}})
        opener = Mock()
        self.assertEqual(request('在B站播放' + TITLE, opener, transport)['status'], 'ambiguous')
        opener.assert_not_called()
        transport.assert_called_once()

    def test_missing_title_does_not_play_popular_results(self):
        opener = Mock()
        self.assertEqual(request('在B站播放不存在的完整标题', opener,
            Mock(return_value={'data': {'result': [{'bvid': TARGET, 'title': TITLE}]}}))['status'], 'not_found')
        opener.assert_not_called()

    def test_non_first_part_is_explicitly_rejected_not_silently_played(self):
        opener, transport = Mock(), Mock()
        result = request('播放https://www.bilibili.com/video/' + TARGET + '?p=2', opener, transport)
        self.assertEqual(result['status'], 'unsupported_reference')
        opener.assert_not_called()
        transport.assert_not_called()

    def test_failures_never_claim_video_played(self):
        opener = Mock()
        self.assertFalse(request(TARGET, opener, Mock(side_effect=TimeoutError()))['ok'])
        opener.assert_not_called()
        result = request(TARGET, Mock(return_value=SimpleNamespace(returncode=1)),
                         Mock(return_value={'data': DETAIL}))
        self.assertEqual(result['status'], 'open_failed')
        self.assertFalse(request(TARGET, opener, Mock(return_value={'data': {}}))['ok'])

    def test_native_url_rejects_bad_identifiers(self):
        for key, value in [('bvid', 'BVbad?url=other'), ('aid', '1;command'), ('cid', -1)]:
            with self.assertRaises(ValueError):
                play_url({**DETAIL, key: value})

    def test_search_uses_only_new_anonymous_identity_not_user_cookie_files(self):
        spi, search = Mock(), Mock()
        for response, body in [(spi, {'code': 0, 'data': {'b_3': 'anonymous-test-id'}}),
                               (search, {'code': 0, 'data': {'result': []}})]:
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps(body).encode()
        with patch('bilibili_video._anonymous_id', None), \
             patch('bilibili_video.urllib.request.urlopen', side_effect=[spi, search]) as opened:
            fetch('/x/web-interface/search/type', {'keyword': TITLE}, anonymous=True)
        self.assertNotIn('Cookie', opened.call_args_list[0].args[0].headers)
        self.assertEqual(opened.call_args_list[1].args[0].headers['Cookie'], 'buvid3=anonymous-test-id')
