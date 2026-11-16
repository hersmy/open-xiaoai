import asyncio
import json
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from bridge import Bridge, HarnessRunner, AutoRunner, recognize, route, speech_text, tts_script, tts_completed
from voice_commands import dismiss_command


class ProtocolTests(unittest.TestCase):
    def test_collected_fragments_join_without_duplicating_whole_utterance(self):
        self.assertEqual(Bridge.join_command('播放', '许嵩的如果当时'), '播放许嵩的如果当时')
        self.assertEqual(Bridge.join_command('播放许嵩', '的如果当时'), '播放许嵩的如果当时')
        self.assertEqual(Bridge.join_command('播放', '播放许嵩的如果当时'), '播放许嵩的如果当时')

    def test_dismiss_is_exact_and_does_not_swallow_other_requests(self):
        for text in ['退下', '退下吧。', '你好小七，退下', '小七，退下吧']:
            self.assertTrue(dismiss_command(text))
        for text in ['退下是什么意思', '退下后播放音乐', '告诉我退下的意思']:
            self.assertFalse(dismiss_command(text))

    def test_only_final_recognition_is_routed(self):
        line = {"header": {"id": "one", "namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                "payload": {"is_final": True, "results": [{"text": "问助手你好"}]}}
        event = {"event": "instruction", "data": {"NewLine": json.dumps(line)}}
        self.assertEqual(recognize(event), ("问助手你好", "one"))
        line["payload"]["is_final"] = False
        event["data"]["NewLine"] = json.dumps(line)
        self.assertIsNone(recognize(event))
        self.assertIsNone(recognize({"event": "instruction", "data": None}))
        self.assertIsNone(recognize({"event": "instruction", "data": {"NewLine": "not json"}}))

    def test_stock_commands_are_not_intercepted(self):
        self.assertIsNone(route("播放音乐"))
        self.assertIsNone(route("告诉我怎么问助手"))
        self.assertEqual(route("小爱同学，问助手，解释一下月亮"), "解释一下月亮")
        self.assertEqual(route("问一下助手新对话"), "新对话")

    def test_model_output_is_one_shell_argument(self):
        text = "你好'; touch /tmp/not-a-command; echo '$(whoami) `id`"
        self.assertEqual(shlex.split(tts_script(text)), ["/usr/sbin/tts_play.sh", speech_text(text)])

    def test_code_is_not_read_as_prose(self):
        self.assertNotIn("rm -rf", speech_text("说明\n```sh\nrm -rf xxx\n```\n结束"))

    def test_stock_tts_completion_can_be_on_stderr(self):
        self.assertTrue(tts_completed({"exit_code": 1, "stdout": "", "stderr": "event(EndReached) is posted"}))
        self.assertFalse(tts_completed({"exit_code": 1, "stderr": "network failed"}))


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settle_patch = patch('bridge.COMMAND_SETTLE_SECONDS', 0)
        settle_patch.start()
        self.addCleanup(settle_patch.stop)
        feedback_patch = patch.object(Bridge, 'feedback', new_callable=AsyncMock)
        self.feedback = feedback_patch.start()
        self.addCleanup(feedback_patch.stop)

    async def test_cloud_finals_are_collected_before_one_dispatch(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = .06
        bridge.dialog = AsyncMock(return_value='完成')
        bridge.continue_capture = AsyncMock()
        bridge.listening_until = time.monotonic() + 1
        bridge.listen_started = time.monotonic()
        def final(text, identity):
            return {'event': 'instruction', 'data': {'NewLine': json.dumps({
                'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult',
                           'dialog_id': identity, 'id': identity},
                'payload': {'is_final': True, 'results': [{'text': text}]}})}}
        await bridge.event(final('播放许嵩的歌', 'first'))
        timer = bridge.command_timer
        await asyncio.sleep(.025)
        bridge.dialog.assert_not_awaited()
        await bridge.event(final('不要现场版', 'second'))
        await timer
        await bridge.job
        bridge.dialog.assert_awaited_once_with('播放许嵩的歌，不要现场版')
        self.assertEqual(bridge.listening_until, 0)
        self.assertIsNone(bridge.command_timer)

    async def test_partial_continuation_prevents_early_execution(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = .04
        bridge.dialog = AsyncMock()
        bridge.continue_capture = AsyncMock()
        bridge.listening_until = time.monotonic() + .2
        bridge.last_recognition = {}
        await bridge.collect_command('播放许嵩的歌', {'data': {}})
        timer = bridge.command_timer
        bridge.last_speech_at = time.monotonic()
        bridge.continuation_partial = True
        await timer
        bridge.dialog.assert_not_awaited()
        await bridge.cancel_command_collection()

    async def test_corrected_final_keeps_prior_collected_segments(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = 10
        bridge.listening_until = time.monotonic() + 30
        bridge.last_recognition = {}
        bridge.continue_capture = AsyncMock()
        def event(identity):
            return {'data': {'NewLine': json.dumps({'header': {'dialog_id': identity}})}}
        await bridge.collect_command('播放', event('first'))
        await bridge.collect_command('许嵩的歌', event('second'))
        await bridge.collect_command('许嵩的如果当时', event('second'))
        self.assertEqual(bridge.command_text, '播放许嵩的如果当时')
        await bridge.cancel_command_collection()

    async def test_failed_reopen_reminds_user_without_executing_fragment(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = 0
        bridge.command_text = '播放'
        bridge.listening_until = time.monotonic() + 1
        bridge.last_speech_at = time.monotonic() - 1
        bridge.continuation_failed = True
        bridge.dialog = AsyncMock()
        await bridge.finish_command()
        bridge.dialog.assert_not_awaited()
        self.feedback.assert_awaited_once_with('unclear')

    async def test_failed_optional_reopen_keeps_complete_final_and_executes_once(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = 0
        bridge.command_text = '播放许嵩的如果当时'
        bridge.listening_until = time.monotonic() + 1
        bridge.last_speech_at = time.monotonic() - 1
        bridge.continuation_failed = True
        bridge.dialog = AsyncMock(return_value='已播放')
        await bridge.finish_command()
        await bridge.job
        bridge.dialog.assert_awaited_once_with('播放许嵩的如果当时')
        self.feedback.assert_not_awaited()

    def test_bare_actions_are_incomplete_but_known_music_and_questions_are_not(self):
        for text in ['播放', '请帮我播放', '播放许嵩的', '打开']:
            self.assertTrue(Bridge.incomplete_command(text))
        for text in ['播放许嵩的如果当时', '暂停播放', '你能做什么', '播放许嵩的歌']:
            self.assertFalse(Bridge.incomplete_command(text))

    async def test_dismiss_discards_collected_command_without_waiting(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.command_settle = 3
        bridge.continue_capture = AsyncMock()
        bridge.dialog = AsyncMock(return_value='退下')
        bridge.listening_until = time.monotonic() + 30
        def final(text, identity):
            return {'event': 'instruction', 'data': {'NewLine': json.dumps({
                'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult', 'id': identity},
                'payload': {'is_final': True, 'results': [{'text': text}]}})}}
        await bridge.event(final('播放音乐', 'first'))
        await bridge.event(final('退下', 'dismiss'))
        await bridge.job
        bridge.dialog.assert_awaited_once_with('退下')
        self.assertFalse(bridge.command_text)
        self.assertIsNone(bridge.command_timer)

    async def test_dismiss_ends_capture_and_pending_control_without_agent_or_player(self):
        runner = AsyncMock()
        bridge = Bridge('192.0.2.1', runner)
        bridge.say = AsyncMock()
        bridge.restore_native_voice = AsyncMock()
        bridge.listening_until = time.monotonic() + 20
        bridge.control_followup_until = time.monotonic() + 20
        bridge.pending_question = '继续播放'
        timer = bridge.listen_timer = asyncio.create_task(asyncio.sleep(60))
        with patch('bridge.music_request') as execute:
            await bridge.dialog('退下。')
        self.assertEqual(bridge.listening_until, 0)
        self.assertEqual(bridge.control_followup_until, 0)
        self.assertIsNone(bridge.pending_question)
        self.assertTrue(timer.cancelled())
        runner.run.assert_not_awaited()
        execute.assert_not_called()
        bridge.say.assert_not_awaited()
        self.assertEqual(bridge.last_turn['route'], 'local_control')
        self.assertEqual(bridge.last_turn['status'], 'dismissed')
        self.feedback.assert_awaited_once_with('received')

    async def test_recent_wake_dismiss_is_handled_but_requires_new_wake_afterwards(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.restore_native_voice = AsyncMock()
        bridge.control_followup_until = time.monotonic() + 10
        def final(text, identity):
            return {'event': 'instruction', 'data': {'NewLine': json.dumps({
                'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult', 'id': identity},
                'payload': {'is_final': True, 'results': [{'text': text}]}})}}
        await bridge.event(final('退下', 'dismiss'))
        await bridge.job
        self.assertEqual(bridge.last_turn['status'], 'dismissed')
        original_job = bridge.job
        await bridge.event(final('继续播放', 'late-control'))
        self.assertIs(bridge.job, original_job)

    def test_local_only_turns_keep_desktop_session_identity_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'desktop-session.json').write_text(json.dumps({'session_id': 'voice-session'}))
            (root / 'desktop.json').write_text('{}')
            runner = AutoRunner('/unused', root, 180)
            self.assertEqual(runner.backend, 'desktop')
            self.assertEqual(runner.session_id, 'voice-session')

    async def test_local_play_opens_while_recovering_without_agent_or_tts(self):
        runner = AsyncMock()
        bridge = Bridge('192.0.2.1', runner)
        bridge.say = AsyncMock()
        recovering = asyncio.Event()
        release = asyncio.Event()
        async def recover():
            recovering.set()
            await release.wait()
        bridge.restore_native_voice = recover
        result = {'handled': True, 'ok': True, 'status': 'opened', 'message': '已交给网易云打开'}
        with patch('bridge.music_request', return_value=result):
            task = asyncio.create_task(bridge.dialog('播放如约而至'))
            await recovering.wait()
            for _ in range(100):
                if 'command_result_after_ms' in bridge.last_turn:
                    break
                await asyncio.sleep(.001)
            self.assertIn('command_result_after_ms', bridge.last_turn)
            self.assertFalse(task.done())
            bridge.say.assert_not_awaited()
            runner.run.assert_not_awaited()
            release.set()
            self.assertEqual(await task, result['message'])
        self.assertEqual(bridge.local_completed, 1)
        self.assertEqual(bridge.last_turn['route'], 'local_music')
        self.assertFalse(bridge.last_turn['agent_called'])
        self.assertTrue(bridge.last_turn['spoken'])
        self.assertEqual(bridge.control_followup_until, 0)

    async def test_local_failure_is_spoken_once_without_agent_retry(self):
        runner = AsyncMock()
        bridge = Bridge('192.0.2.1', runner)
        bridge.say = AsyncMock()
        bridge.restore_native_voice = AsyncMock()
        result = {'handled': True, 'ok': False, 'message': '本地没有这首歌'}
        with patch('bridge.music_request', return_value=result) as execute:
            await bridge.dialog('播放不存在的歌曲')
        execute.assert_called_once()
        bridge.say.assert_awaited_once_with(result['message'])
        runner.run.assert_not_awaited()
        self.assertEqual(bridge.last_error, result['message'])

    async def test_pause_has_native_receipt_and_completion_without_agent(self):
        runner = AsyncMock()
        bridge = Bridge('192.0.2.1', runner)
        bridge.say = AsyncMock()
        bridge.restore_native_voice = AsyncMock()
        result = {'handled': True, 'ok': True, 'status': 'controlled', 'silent': True, 'message': '已暂停。'}
        with patch('bridge.music_request', return_value=result) as execute:
            await bridge.dialog('暂停播放')
        execute.assert_called_once_with('暂停播放')
        runner.run.assert_not_awaited()
        bridge.say.assert_not_awaited()
        self.assertTrue(bridge.last_turn['spoken'])
        self.assertEqual([call.args[0] for call in self.feedback.await_args_list], ['received', 'done'])

    async def test_receipt_precedes_execution_and_completion_follows_it(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.restore_native_voice = AsyncMock()
        steps = []
        async def prompt(name):
            steps.append(name)
        def execute(text):
            steps.append('execute')
            return {'ok': True, 'status': 'controlled', 'silent': True, 'message': '已暂停'}
        bridge.feedback = prompt
        with patch('bridge.music_request', side_effect=execute):
            await bridge.dialog('暂停播放')
        self.assertEqual(steps, ['received', 'execute', 'done'])

    async def test_specific_song_with_if_word_never_calls_agent(self):
        runner = AsyncMock()
        bridge = Bridge('192.0.2.1', runner)
        bridge.restore_native_voice = AsyncMock()
        with patch('bridge.music_request', return_value={
                'ok': True, 'status': 'opened', 'silent': True,
                'source': 'netease_online', 'message': '已交给网易云打开'}) as execute:
            await bridge.dialog('播放许嵩的如果当时')
        execute.assert_called_once_with('播放许嵩的如果当时')
        runner.run.assert_not_awaited()
        self.assertEqual(bridge.last_turn['music_source'], 'netease_online')

    async def test_explicit_bilibili_video_skips_music_and_agent_and_records_own_journal(self):
        runner = AsyncMock()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Bridge('192.0.2.1', runner, state_dir=tmp)
            bridge.restore_native_voice = AsyncMock()
            bridge.say = AsyncMock()
            with patch('bridge.video_request', return_value={'handled': True, 'ok': True,
                    'status': 'opened', 'silent': True, 'source': 'bilibili', 'message': '已打开视频'}) as video, \
                 patch('bridge.music_request') as music:
                await bridge.dialog('播放视频BV18V4i6WEje')
            video.assert_called_once()
            music.assert_not_called()
            runner.run.assert_not_awaited()
            bridge.say.assert_not_awaited()
            self.assertEqual(bridge.last_turn['route'], 'local_video')
            self.assertTrue((Path(tmp) / 'video-actions.jsonl').is_file())
            self.assertFalse((Path(tmp) / 'music-actions.jsonl').exists())

    async def test_query_uses_fixed_answer_and_compound_request_uses_agent(self):
        runner = AsyncMock()
        runner.run.return_value = '复杂请求处理完成'
        bridge = Bridge('192.0.2.1', runner)
        bridge.say = AsyncMock()
        bridge.restore_native_voice = AsyncMock()
        with patch('bridge.music_request', return_value={'ok': True, 'message': '共有11首歌'}):
            await bridge.dialog('本地曲库有多少首歌')
        bridge.say.assert_awaited_once_with('共有11首歌')
        runner.run.assert_not_awaited()
        bridge.say.reset_mock()
        with patch('bridge.music_request') as execute:
            await bridge.dialog('播放适合写论文的音乐')
        execute.assert_not_called()
        runner.run.assert_awaited_once_with('播放适合写论文的音乐')
        self.assertTrue(bridge.last_turn['agent_called'])

    async def test_cancelling_local_turn_also_cancels_recovery(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        cancelled = asyncio.Event()
        started = asyncio.Event()
        async def recover():
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        bridge.restore_native_voice = recover
        with patch('bridge.music_request', return_value={'ok': True, 'status': 'opened', 'message': '已打开'}):
            task = asyncio.create_task(bridge.dialog('播放如约而至'))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(bridge.last_turn['status'], 'cancelled')

    async def test_immediate_rewake_waits_for_native_recovery_before_opening_asr(self):
        bridge = Bridge('192.0.2.1', AsyncMock())
        bridge.say = AsyncMock()
        bridge.rpc = AsyncMock(return_value={'exit_code': 0, 'stdout': 'ASR_READY'})
        release = asyncio.Event()
        async def recover():
            await release.wait()
        bridge.restore_native_voice = recover
        result = {'ok': True, 'status': 'opened', 'message': '已打开'}
        with patch('bridge.music_request', return_value=result):
            original_job = asyncio.create_task(bridge.dialog('播放如约而至'))
            bridge.job = original_job
            for _ in range(100):
                if bridge.last_turn and 'command_result_after_ms' in bridge.last_turn:
                    break
                await asyncio.sleep(.001)
            wake = asyncio.create_task(bridge.begin_listening())
            await asyncio.sleep(.01)
            bridge.rpc.assert_not_awaited()
            self.assertFalse(bridge.native_recovery.cancelled())
            release.set()
            await wake
            await bridge.cancel_listen_timer()
            await asyncio.gather(original_job, return_exceptions=True)
        self.assertIn(unittest.mock.call('ready'), self.feedback.await_args_list)
        self.assertTrue(any('pnshelper' in call.args[1] for call in bridge.rpc.call_args_list))

    async def test_slow_task_feedback_does_not_restart_agent(self):
        async def slow_answer(text):
            await asyncio.sleep(.06)
            return "完成"
        runner = AsyncMock()
        runner.run.side_effect = slow_answer
        bridge = Bridge("192.0.2.1", runner)
        bridge.say = AsyncMock()
        self.assertEqual(await bridge.run_with_feedback("任务", interval=.01), "完成")
        runner.run.assert_awaited_once_with("任务")
        bridge.say.assert_awaited_once()

    async def test_cancel_feedback_cancels_owned_agent_call(self):
        stopped = asyncio.Event()
        async def wait_forever(text):
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        runner = AsyncMock()
        runner.run.side_effect = wait_forever
        bridge = Bridge("192.0.2.1", runner)
        task = asyncio.create_task(bridge.run_with_feedback("任务"))
        await asyncio.sleep(.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    def test_timeout_is_not_reported_as_connection_failure(self):
        self.assertIn("三十分钟", Bridge.failure_message(RuntimeError("VOICE_TIMEOUT")))
        self.assertIn("中断", Bridge.failure_message(RuntimeError("Voice turn cancelled")))
        self.assertIn("其他输入", Bridge.failure_message(RuntimeError("Voice session busy")))

    async def test_native_interrupt_recovers_frontend_too(self):
        bridge = Bridge("192.0.2.1", None)
        bridge.rpc = AsyncMock(return_value={"exit_code": 0})
        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            await bridge.restore_native_voice()
        command = bridge.rpc.call_args.args[1]
        self.assertIn("mico_aivs_lab restart", command)
        self.assertIn("&& /etc/init.d/pns restart", command)
        self.assertLess(command.index('mphelper pause'), command.index('mico_aivs_lab restart'))
        self.assertGreater(command.rindex('mphelper pause'), command.index('/etc/init.d/pns restart'))
        bridge.rpc.return_value = {"exit_code": 1}
        with self.assertRaises(RuntimeError):
            await bridge.restore_native_voice()

    async def test_late_native_music_is_paused_only_for_owned_custom_asr_dialog(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.rpc = AsyncMock(return_value={'exit_code': 0})
        def event(namespace, name, identity):
            return {'event': 'instruction', 'data': {'NewLine': json.dumps({
                'header': {'namespace': namespace, 'name': name, 'dialog_id': identity}, 'payload': {}})}}
        # A partial ASR already establishes ownership before the cloud intent.
        bridge.listening_until = time.monotonic() + 10
        await bridge.event(event('SpeechRecognizer', 'RecognizeResult', 'custom-dialog'))
        bridge.listening_until = 0
        await bridge.event(event('AudioPlayer', 'Play', 'stock-dialog'))
        bridge.rpc.assert_not_awaited()
        await bridge.event(event('AudioPlayer', 'Play', 'custom-dialog'))
        await bridge.native_pause_job
        self.assertEqual(bridge.native_music_suppressed, 1)
        self.assertIn('mphelper pause', bridge.rpc.call_args.args[1])
        bridge.rpc.reset_mock()
        bridge.custom_dialogs['custom-dialog'] = time.monotonic() - 1
        await bridge.event(event('AudioPlayer', 'Play', 'custom-dialog'))
        bridge.rpc.assert_not_awaited()

    async def test_native_music_guard_does_not_block_websocket_rpc_reader(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.custom_dialogs['custom-dialog'] = time.monotonic() + 10
        release = asyncio.Event()
        async def rpc(*args, **kwargs):
            await release.wait()
            return {'exit_code': 0}
        bridge.rpc = rpc
        event = {'event': 'instruction', 'data': {'NewLine': json.dumps({
            'header': {'namespace': 'AudioPlayer', 'name': 'Play', 'dialog_id': 'custom-dialog'}})}}
        await asyncio.wait_for(bridge.event(event), timeout=.1)
        await asyncio.sleep(0)
        self.assertFalse(bridge.native_pause_job.done())
        # A second Play arriving during the first RPC still needs a final pause.
        await bridge.event(event)
        release.set()
        await bridge.native_pause_job
        self.assertEqual(bridge.native_music_suppressed, 2)

    async def test_missing_recognition_reports_failure_without_agent_call(self):
        runner = AsyncMock()
        bridge = Bridge("192.0.2.1", runner)
        bridge.say = AsyncMock()
        bridge.listening_until = time.monotonic() - .01
        await bridge.wait_for_recognition()
        self.assertEqual(bridge.listening_until, 0)
        self.assertIn("ASR_TIMEOUT", bridge.last_error)
        self.feedback.assert_awaited_once_with('no_input')
        runner.run.assert_not_awaited()

    async def test_asr_is_confirmed_before_cached_ready_prompt(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.rearm_attempts = 1
        calls = []
        async def rpc(command, script, **kwargs):
            if 'pnshelper' in script:
                calls.append('asr')
            return {'exit_code': 0, 'stdout': 'ASR_READY'}
        async def feedback(kind):
            calls.append(kind)
        bridge.rpc = rpc
        bridge.feedback = feedback
        await bridge.begin_listening()
        await bridge.cancel_listen_timer()
        self.assertEqual(calls, ['asr', 'ready'])
        self.assertEqual(bridge.rearm_attempts, 0)

    async def test_unconfirmed_capture_never_plays_ready_prompt(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.rpc = AsyncMock(return_value={'exit_code': 0, 'stdout': 'ASR_NOT_CONFIRMED'})
        bridge.restore_native_voice = AsyncMock()
        await bridge.begin_listening()
        bridge.restore_native_voice.assert_awaited_once()
        self.feedback.assert_awaited_once_with('not_ready')
        self.assertEqual(bridge.listening_until, 0)

    async def test_greeting_only_final_does_not_close_listening(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.dialog = AsyncMock()
        bridge.rpc = AsyncMock(return_value={'exit_code': 0, 'stdout': 'ASR_READY'})
        bridge.listening_until = time.monotonic() + 18
        event = {'event': 'instruction', 'data': {'NewLine': json.dumps({
            'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult', 'id': 'greeting'},
            'payload': {'is_final': True, 'results': [{'text': '你好小七'}]}})}}
        await bridge.event(event)
        await bridge.rearm_job
        self.assertGreater(bridge.listening_until, time.monotonic())
        bridge.dialog.assert_not_awaited()
        self.assertEqual(bridge.last_recognition['decision'], 'ignored_greeting')
        self.feedback.assert_not_awaited()
        self.assertEqual(bridge.rearm_attempts, 1)

    async def test_recent_wake_control_is_not_discarded_after_window_closes(self):
        bridge = Bridge('192.0.2.1', None)
        bridge.dialog = AsyncMock(return_value='完成')
        bridge.control_followup_until = time.monotonic() + 10
        def event(text, identity):
            return {'event': 'instruction', 'data': {'NewLine': json.dumps({
                'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult', 'id': identity},
                'payload': {'is_final': True, 'results': [{'text': text}]}})}}
        await bridge.event(event('暂停播放', 'pause'))
        await bridge.job
        bridge.dialog.assert_awaited_once_with('暂停播放')
        self.assertEqual(bridge.last_recognition['decision'], 'recent_wake_control')
        bridge.dialog.reset_mock()
        await bridge.event(event('播放音乐', 'stock-music'))
        bridge.dialog.assert_not_awaited()
        bridge.control_followup_until = 0
        await bridge.event(event('暂停播放', 'stock-pause'))
        bridge.dialog.assert_not_awaited()

    async def test_local_command_is_queued_without_cancelling_recovery(self):
        bridge = Bridge('192.0.2.1', None)
        release = asyncio.Event()
        previous = asyncio.create_task(release.wait())
        bridge.job = previous
        bridge.last_turn = {'route': 'local_music'}
        bridge.control_followup_until = time.monotonic() + 10
        previous.add_done_callback(bridge.observe_job)
        bridge.dialog = AsyncMock(return_value='已暂停')
        await bridge.event({'event': 'instruction', 'data': {'NewLine': json.dumps({
            'header': {'namespace': 'SpeechRecognizer', 'name': 'RecognizeResult', 'id': 'queued'},
            'payload': {'is_final': True, 'results': [{'text': '暂停播放'}]}})}})
        self.assertFalse(previous.cancelled())
        self.assertEqual(bridge.pending_question, '暂停播放')
        release.set()
        await previous
        await asyncio.sleep(0)
        await bridge.job
        bridge.dialog.assert_awaited_once_with('暂停播放')

    async def test_received_text_cancels_timeout(self):
        bridge = Bridge("192.0.2.1", None)
        bridge.dialog = AsyncMock(return_value="answer")
        bridge.listening_until = time.monotonic() + 60
        timer = asyncio.create_task(bridge.wait_for_recognition())
        bridge.listen_timer = timer
        await bridge.event({"event": "instruction", "data": {"NewLine": json.dumps({
            "header": {"id": "recognition", "namespace": "SpeechRecognizer", "name": "RecognizeResult"},
            "payload": {"is_final": True, "results": [{"text": "你好"}]}})}})
        await bridge.job
        self.assertTrue(timer.cancelled())
        bridge.dialog.assert_awaited_once_with("你好")

    async def test_custom_wake_routes_one_unprefixed_question(self):
        bridge = Bridge("192.0.2.1", None)
        bridge.dialog = AsyncMock(return_value="answer")
        bridge.listening_until = time.monotonic() + 10
        bridge.wake_job = asyncio.create_task(asyncio.sleep(60))
        def event(identity, text):
            return {"event": "instruction", "data": {"NewLine": json.dumps({
                "header": {"id": identity, "namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                "payload": {"is_final": True, "results": [{"text": text}]}})}}
        await bridge.event(event("first", "你能做什么"))
        await bridge.job
        self.assertTrue(bridge.wake_job.cancelled())
        bridge.dialog.assert_awaited_once_with("你能做什么")
        await bridge.event(event("first", "你能做什么"))
        await bridge.event(event("second", "播放音乐"))
        bridge.dialog.assert_awaited_once()

    def fake_cli(self, directory, events, code=0, sleep=0):
        path = Path(directory) / "fake-dsh"
        path.write_text("#!/usr/bin/env python3\nimport json,sys,time\n"
                        "sys.stdin.read()\n"
                        f"time.sleep({sleep})\n"
                        f"events={events!r}\n"
                        "for e in events: print(json.dumps(e),flush=True)\n"
                        f"sys.exit({code})\n")
        path.chmod(0o700)
        return str(path)

    async def test_only_successful_final_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = [{"type": "session", "sessionId": "session-test"},
                      {"type": "thinking", "text": "不要朗读的推理"},
                      {"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}},
                      {"type": "final", "text": "实际答案"}]
            runner = HarnessRunner(self.fake_cli(tmp, events), Path(tmp) / "state")
            self.assertEqual(await runner.run("问题"), "实际答案")
            self.assertEqual(runner.session_id, "session-test")
            restored = HarnessRunner(runner.executable, runner.state_dir)
            self.assertEqual(restored.session_id, "session-test")

    async def test_failed_turn_does_not_replace_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = [{"type": "session", "sessionId": "not-complete"},
                      {"type": "status", "phase": "turn_end", "reason": {"kind": "error"}},
                      {"type": "final", "text": ""}]
            runner = HarnessRunner(self.fake_cli(tmp, events, code=1), Path(tmp) / "state")
            with self.assertRaises(RuntimeError):
                await runner.run("问题")
            self.assertIsNone(runner.session_id)
            self.assertFalse(runner.state_file.exists())

    async def test_timeout_stops_owned_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = HarnessRunner(self.fake_cli(tmp, [], sleep=20), Path(tmp) / "state", timeout=.1)
            with self.assertRaises(asyncio.TimeoutError):
                await runner.run("问题")
            self.assertIsNone(runner.process)
            self.assertFalse(runner.state_file.exists())


if __name__ == "__main__":
    unittest.main()
