from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from netease_player import open_media


class PlayerStartTests(unittest.TestCase):
    def test_cold_start_then_readiness_then_one_media_request(self):
        steps = []
        def run(args, **kwargs):
            steps.append('media' if args[-1].startswith('orpheus:') else 'launch')
            return SimpleNamespace(returncode=0)
        states = Mock(side_effect=[{'running': False, 'ready': False}, {'ready': False}, {'ready': True}])
        sleep = Mock(side_effect=lambda t:steps.append('wait'))
        open_media(['/usr/bin/open', 'orpheus://song'], state_reader=states, run=run, sleep=sleep)
        self.assertEqual(steps, ['launch', 'wait', 'wait', 'media'])

    def test_warm_player_gets_only_one_media_request_without_launch_wait(self):
        run, sleep = Mock(return_value=SimpleNamespace(returncode=0)), Mock()
        open_media(['/usr/bin/open', 'orpheus://song'], state_reader=lambda:{'ready': True, 'running': True}, run=run, sleep=sleep)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_startup_failure_never_sends_media_request(self):
        run = Mock(return_value=SimpleNamespace(returncode=1))
        result = open_media(['/usr/bin/open', 'orpheus://song'], state_reader=lambda:{'running': False}, run=run, sleep=Mock())
        self.assertEqual(result.returncode, 1)
        run.assert_called_once()
