from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from netease_music import library, request, parse_command, control_player


class MusicTests(unittest.TestCase):
    def test_conditional_word_inside_song_title_is_not_an_agent_request(self):
        for text in ['播放许嵩的如果当时', '播放如果没有你', '播放如果这就是爱情', '播放如果还有明天']:
            self.assertEqual(parse_command(text)['kind'], 'play', text)
        for text in ['播放许嵩的歌如果没有就播放周杰伦的歌', '播放如果没有这首就换一首']:
            self.assertIsNone(parse_command(text), text)

    def test_authorized_control_app_is_launched_via_launchservices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'Control.app/Contents/MacOS/MusicControl'
            binary.parent.mkdir(parents=True)
            binary.touch()
            def launch(args, **kwargs):
                self.assertEqual(args[0], '/usr/bin/open')
                self.assertIn('-W', args)
                self.assertEqual(args[args.index('--args') + 1], 'pause')
                output = Path(args[args.index('--stdout') + 1])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                output.write_text('{"ok":true,"status":"controlled","state":"paused"}')
                return SimpleNamespace(returncode=0)
            with patch('netease_music.CONTROL', binary), patch('netease_music.STATE', root / 'state.json'), \
                 patch('netease_music.subprocess.run', side_effect=launch):
                self.assertTrue(control_player('pause')['ok'])

    def test_pause_resume_are_idempotent_commands_not_a_toggle_or_agent(self):
        controller = Mock(return_value={'ok': True, 'status': 'controlled', 'state': 'paused'})
        opener = Mock()
        for phrase in ['暂停', '暂停播放', '停止播放', '关掉音乐', '请帮我暂停播放。']:
            result = request(phrase, opener=opener, controller=controller)
            self.assertTrue(result['silent'])
            self.assertTrue(result['ok'])
            controller.assert_called_with('pause')
        request('继续播放', opener=opener, controller=controller)
        controller.assert_called_with('resume')
        opener.assert_not_called()
        self.assertIsNone(parse_command('暂停播放然后关灯'))

    def test_repeated_identical_controls_are_one_command(self):
        self.assertEqual(parse_command('暂停播放，暂停播放')['kind'], 'pause')
        self.assertEqual(parse_command('暂停播放暂停播放')['kind'], 'pause')
        self.assertEqual(parse_command('继续播放，继续播放。')['kind'], 'resume')
        self.assertIsNone(parse_command('暂停播放，继续播放'))

    def test_failed_control_reports_failure_without_fake_success(self):
        controller = Mock(return_value={'ok': False, 'status': 'permission_required', 'message': '请授权'})
        result = request('暂停播放', controller=controller)
        self.assertFalse(result['silent'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], '请授权')

    def test_skip_uses_only_project_library_and_wraps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'A - One.ncm').write_bytes(b'audio')
            (root / 'B - Two.mp3').write_bytes(b'audio')
            state = root / 'state.json'
            opener = Mock(return_value=SimpleNamespace(returncode=0))
            first = request('播放One', root, opener, state_path=state)
            self.assertEqual(first['track']['title'], 'One')
            following = request('下一首', root, opener, state_path=state)
            self.assertEqual(following['track']['title'], 'Two')
            wrapped = request('下一首', root, opener, state_path=state)
            self.assertEqual(wrapped['track']['title'], 'One')
            previous = request('上一首', root, opener, state_path=state)
            self.assertEqual(previous['track']['title'], 'Two')

    def test_skip_does_not_guess_when_last_track_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opener = Mock()
            result = request('下一首', root, opener, state_path=root / 'missing.json')
            self.assertFalse(result['ok'])
            opener.assert_not_called()

    def test_compound_and_inferential_requests_do_not_execute(self):
        opener = Mock()
        for text in ['播放适合写论文的歌', '播放如约而至，然后关灯', '播放如约而至并关灯',
                     '播放推荐歌单', '播放这首歌之前分析一下歌词']:
            self.assertIsNone(parse_command(text), text)
            self.assertFalse(request(text, opener=opener)['handled'])
        opener.assert_not_called()

    def test_punctuation_and_count_have_fixed_meaning(self):
        self.assertEqual(parse_command('请帮我播放许嵩的如约而至。')['kind'], 'play')
        self.assertEqual(parse_command('本地曲库有多少首歌？')['kind'], 'count')
        with tempfile.TemporaryDirectory() as tmp:
            result = request('本地曲库有多少首歌？', Path(tmp), Mock())
            self.assertEqual(result['message'], '本地曲库共有0首歌。')

    def test_ncm_is_passed_unchanged_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song = root / "许嵩 - 如约而至.ncm"
            song.write_bytes(b"CTENFDAM")
            (root / "如约而至.lrc").write_text("lyrics")
            opener = Mock(return_value=SimpleNamespace(returncode=0))
            result = request("播放许嵩的如约而至", root, opener)
            self.assertEqual(result['status'], 'opened')
            self.assertEqual(opener.call_args.args[0][-1], str(song.resolve()))
            self.assertNotIn('shell', opener.call_args.kwargs)
            self.assertEqual(len(library(root)), 1)

    def test_non_music_and_missing_song_never_launch_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = Mock()
            root = Path(tmp)
            self.assertFalse(request("播放新闻", root, opener)['handled'])
            online = Mock(return_value={'handled': True, 'ok': False, 'status': 'not_found', 'message': '未找到'})
            self.assertFalse(request("播放不存在的歌", root, opener, online=online)['ok'])
            online.assert_called_once_with('不存在的歌', opener=opener)
            opener.assert_not_called()

    def test_local_hit_and_explicit_local_miss_never_search_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '许嵩 - 有何不可.ncm').write_bytes(b'audio')
            online = Mock()
            opener = Mock(return_value=SimpleNamespace(returncode=0))
            self.assertTrue(request('你好小七，播放许嵩的有何不可', root, opener, online=online)['ok'])
            self.assertFalse(request('播放本地不存在的歌', root, opener, online=online)['ok'])
            online.assert_not_called()

    def test_online_track_replaces_local_pointer_so_skip_cannot_use_stale_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '许嵩 - 有何不可.ncm').write_bytes(b'audio')
            state = root / 'state.json'
            opener = Mock(return_value=SimpleNamespace(returncode=0))
            request('播放有何不可', root, opener, state_path=state)
            online = Mock(return_value={'handled': True, 'ok': True, 'status': 'opened',
                'track': {'id': 66842, 'title': '十年', 'artist': '陈奕迅', 'source': 'netease_online'}})
            request('播放陈奕迅的十年', root, opener, state_path=state, online=online)
            opener.reset_mock()
            self.assertFalse(request('下一首', root, opener, state_path=state)['ok'])
            opener.assert_not_called()

    def test_external_symlink_is_not_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'music'; root.mkdir()
            outside = Path(tmp) / 'outside.mp3'; outside.write_bytes(b'audio')
            (root / 'link.mp3').symlink_to(outside)
            self.assertEqual(library(root), [])

    def test_player_failure_is_not_reported_as_playing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '歌手 - 歌曲.ncm').write_bytes(b'audio')
            result = request('播放歌曲', root, Mock(return_value=SimpleNamespace(returncode=1)))
            self.assertFalse(result['ok'])
