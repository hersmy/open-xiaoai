import unittest
from feedback import install_script, play_script, prepare


class FeedbackProtocolTests(unittest.TestCase):
    def test_native_prompts_are_fixed_and_synthesized_without_playing(self):
        prompts = prepare()
        self.assertEqual(prompts['ready'], '哎。')
        self.assertEqual(prompts['received'], '收到。')
        self.assertEqual(prompts['done'], '已完成。')
        scripts = install_script(prompts)
        for script in scripts[2:]:
            self.assertIn('mibrain text_to_speech', script)
            self.assertIn('"play": 0', script)
            self.assertIn('[ -s ', script)

    def test_custom_text_cannot_be_injected_into_native_cache_script(self):
        with self.assertRaises(ValueError):
            install_script({'ready': 'not a fixed prompt; rm something'})

    def test_only_fixed_feedback_filenames_can_be_played(self):
        self.assertIn('/feedback-player -f ', play_script('ready'))
        with self.assertRaises(ValueError):
            play_script('../not-a-prompt; echo bad')
