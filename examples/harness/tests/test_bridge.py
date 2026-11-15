import asyncio
import json
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from bridge import Bridge, HarnessRunner, AutoRunner, recognize, route, speech_text, tts_script, tts_completed


class ProtocolTests(unittest.TestCase):
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
        self.assertFalse(bridge.last_turn['spoken'])

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

    async def test_pause_is_silent_and_does_not_call_agent(self):
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
        self.assertFalse(bridge.last_turn['spoken'])

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
        bridge.rpc = AsyncMock(return_value={'exit_code': 0})
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
        bridge.say.assert_awaited_once_with('我在。')
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
        bridge.rpc.return_value = {"exit_code": 1}
        with self.assertRaises(RuntimeError):
            await bridge.restore_native_voice()

    async def test_missing_recognition_reports_failure_without_agent_call(self):
        runner = AsyncMock()
        bridge = Bridge("192.0.2.1", runner)
        bridge.say = AsyncMock()
        bridge.listening_until = time.monotonic() - .01
        await bridge.wait_for_recognition()
        self.assertEqual(bridge.listening_until, 0)
        self.assertIn("ASR_TIMEOUT", bridge.last_error)
        bridge.say.assert_awaited_once()
        runner.run.assert_not_awaited()

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
