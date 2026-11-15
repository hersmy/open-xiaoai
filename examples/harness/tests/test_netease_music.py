from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from netease_music import library, request


class MusicTests(unittest.TestCase):
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
            self.assertFalse(request("播放不存在的歌", root, opener)['ok'])
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
