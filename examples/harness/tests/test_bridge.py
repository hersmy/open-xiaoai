import asyncio
import json
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from bridge import Bridge, HarnessRunner, recognize, route, speech_text, tts_script, tts_completed


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
