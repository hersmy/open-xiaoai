"""Open-XiaoAI events -> official dsh headless CLI -> speaker TTS.

No model credentials are read or copied by this bridge. Harness owns its model,
tools, permissions, and persisted conversation. The stock speaker owns ASR/TTS.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import signal
import time
import uuid

from aiohttp import ClientSession, WSMsgType, web

ROOT = Path(__file__).resolve().parent
DEFAULT_DSH = "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
LOG = logging.getLogger("harness-bridge")
PREFIXES = ("问一下助手", "问一下助理", "问助手", "问助理")


def recognize(event):
    """Return only final, nonempty native ASR results and their event identity."""
    if event.get("event") != "instruction":
        return None
    try:
        line = event.get("data", {}).get("NewLine")
        if not isinstance(line, str):
            return None
        obj = json.loads(line)
        header, payload = obj.get("header", {}), obj.get("payload", {})
        if (header.get("namespace"), header.get("name")) != (
            "SpeechRecognizer", "RecognizeResult") or payload.get("is_final") is not True:
            return None
        results = payload.get("results") or []
        text = results[0].get("text", "").strip() if results else ""
        return (text, header.get("id") or event.get("id")) if text else None
    except (ValueError, TypeError, AttributeError, IndexError):
        return None


def route(text):
    text = re.sub(r"^小爱同学[，,。！!：:\s]*", "", text.strip())
    for prefix in PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].lstrip("，,。！!：: ")
    return None


def speech_text(text):
    # Speak final assistant prose, leaving executable fenced blocks out of audio.
    text = re.sub(r"```.*?```", "代码已保留在本地会话中。", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*#`]+", "", text).strip()
    if len(text) > 1400:
        text = text[:1400] + "。回复较长，完整内容保留在会话中。"
    return text


def tts_script(text):
    # This is a shell RPC: quote untrusted model output as exactly one argument.
    return "/usr/sbin/tts_play.sh " + shlex.quote(speech_text(text))


def tts_completed(result):
    output = result.get("stdout", "") + result.get("stderr", "")
    return result.get("exit_code") == 0 or "event(EndReached)" in output


class HarnessRunner:
    def __init__(self, executable, state_dir, timeout=180):
        self.executable = executable
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.workspace = self.state_dir / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.timeout = timeout
        self.state_file = self.state_dir / "session.json"
        self.session_id = None
        if self.state_file.exists():
            self.session_id = json.loads(self.state_file.read_text()).get("session_id")
        self.process = None
        self.last_tools = []

    async def run(self, text):
        args = [self.executable, "headless", "--json"]
        if self.session_id:
            args.extend(["--session-id", self.session_id])
        args.append("-")
        prompt = (
            "这是小爱音箱的语音对话。请沿用现有 Agent 配置和权限，根据用户本轮请求处理。"
            "回复将直接朗读，请用自然中文，通常不超过一百五十字，避免表格和代码块。"
            "如需操作电脑，按用户明确表达的任务和现有权限执行；未实际完成时如实说明。\n"
            "用户本轮说：\n" + text
        )
        stderr_file = self.state_dir / "harness.stderr"
        with open(stderr_file, "wb") as stderr:
            os.chmod(stderr_file, 0o600)
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=stderr, cwd=self.workspace, start_new_session=True,
            )
            self.process = proc
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(prompt.encode()), timeout=self.timeout)
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()
                self.process = None
        final, next_session, completed = None, None, False
        tools = []
        for line in stdout.decode(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "session":
                next_session = event.get("sessionId")
            elif event.get("type") == "tool_call":
                tools.append(event.get("tool", "unknown"))
            elif event.get("type") == "status" and event.get("phase") == "turn_end":
                completed = event.get("reason", {}).get("kind") == "completed"
            elif event.get("type") == "final":
                final = event.get("text")
        if proc.returncode != 0 or not completed or not isinstance(final, str) or not final.strip():
            raise RuntimeError("Harness 未完成本轮回复；诊断保存在本地 harness.stderr")
        if next_session:
            temp = self.state_file.with_suffix(".tmp")
            temp.write_text(json.dumps({"session_id": next_session}) + "\n")
            os.chmod(temp, 0o600)
            temp.replace(self.state_file)
            self.session_id = next_session
        self.last_tools = tools
        return final.strip()


class DesktopRunner:
    """Calls a scoped plugin inside the already-running desktop Host."""
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "desktop-session.json"
        self.session_id = None
        self.last_tools = []

    async def run(self, text):
        descriptor = json.loads((self.state_dir / "desktop.json").read_text())
        url = descriptor["url"]
        if not url.startswith("http://127.0.0.1:"):
            raise RuntimeError("Desktop endpoint must be loopback")
        async with ClientSession(headers={"Authorization": "Bearer " + descriptor["token"]}) as session:
            async with session.post(url + "/ask", json={"text": text}) as response:
                data = await response.json()
                if response.status != 200:
                    raise RuntimeError(data.get("error", "Desktop Agent call failed"))
        if not isinstance(data.get("answer"), str) or not data["answer"].strip():
            raise RuntimeError("Desktop Agent returned no final answer")
        self.session_id = data["session_id"]
        self.last_tools = data.get("tools", [])
        return data["answer"]


class AutoRunner:
    """Select desktop only between turns; never resubmit a failed turn elsewhere."""
    def __init__(self, executable, state_dir, timeout):
        self.headless = HarnessRunner(executable, state_dir, timeout)
        self.desktop = DesktopRunner(state_dir)
        self.state_dir = Path(state_dir)
        self.current = self.headless

    @property
    def session_id(self):
        return self.current.session_id

    @property
    def last_tools(self):
        return self.current.last_tools

    @property
    def backend(self):
        return "desktop" if self.current is self.desktop else "headless"

    @property
    def state_file(self):
        return self.current.state_file

    @session_id.setter
    def session_id(self, value):
        if self.current is self.desktop:
            raise RuntimeError("请在 Harness 桌面管理语音会话；不通过语音删除桌面历史")
        self.current.session_id = value

    async def run(self, text):
        self.current = self.desktop if (self.state_dir / "desktop.json").exists() else self.headless
        return await self.current.run(text)


class Bridge:
    def __init__(self, speaker_ip, runner, spotter=None):
        self.speaker_ip = speaker_ip
        self.runner = runner
        self.ws = None
        self.pending = {}
        self.job = None
        self.seen = {}
        self.completed = 0
        self.last_error = None
        self.client_version = None
        self.spotter = spotter
        self.audio_queue = asyncio.Queue(maxsize=64)
        self.audio_bytes = 0
        self.audio_peak = 0
        self.audio_dropped = 0
        self.speaking = 0
        self.listening_until = 0
        self.wake_count = 0
        self.asr_count = 0
        self.wake_job = None
        self.listen_timer = None
        self.listen_timeout = 18
        self.reset_kws = False

    async def rpc(self, command, payload=None, timeout=35):
        ws = self.ws
        if ws is None or ws.closed:
            raise RuntimeError("音箱尚未连接")
        uid = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[uid] = future
        try:
            await ws.send_json({"Request": {"id": uid, "command": command, "payload": payload}})
            response = await asyncio.wait_for(future, timeout)
            if response.get("code", 0) != 0:
                raise RuntimeError("音箱 RPC 返回错误")
            return response.get("data")
        finally:
            self.pending.pop(uid, None)

    async def say(self, text):
        self.speaking += 1
        try:
            result = await self.rpc("run_shell", tts_script(text), timeout=75)
        finally:
            self.speaking -= 1
            self.reset_kws = True
        # The stock tts_play.sh can exit 1 despite successful playback. Check
        # the player's end event as well as the shell status (observed on LX06).
        if not tts_completed(result):
            raise RuntimeError("音箱播报未确认完成")

    async def websocket(self, request):
        if request.remote != self.speaker_ip:
            raise web.HTTPForbidden(text="Unexpected speaker address")
        if self.ws is not None and not self.ws.closed:
            raise web.HTTPConflict(text="Speaker already connected")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2**20)
        await ws.prepare(request)
        self.ws = ws
        LOG.info("Speaker connected: %s", request.remote)
        hello = asyncio.create_task(self.hello())
        audio = asyncio.create_task(self.audio_worker()) if self.spotter else None
        try:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    self.receive_audio(message.data)
                    continue
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    obj = json.loads(message.data)
                except ValueError:
                    continue
                if "Response" in obj:
                    response = obj["Response"]
                    future = self.pending.get(response.get("id"))
                    if future and not future.done():
                        future.set_result(response)
                elif "Request" in obj:
                    req = obj["Request"]
                    await ws.send_json({"Response": {"id": req.get("id"),
                        "data": "harness-bridge/0.1.0", "code": 0 if req.get("command") == "get_version" else -1}})
                elif "Event" in obj:
                    await self.event(obj["Event"])
        finally:
            if audio:
                audio.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await audio
            if self.wake_job:
                self.wake_job.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.wake_job
            await self.cancel_listen_timer()
            hello.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hello
            await self.cancel()
            self.ws = None
            self.client_version = None
            self.listening_until = 0
            while not self.audio_queue.empty():
                self.audio_queue.get_nowait()
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError("Speaker disconnected"))
            LOG.info("Speaker disconnected")
        return ws

    async def hello(self):
        try:
            self.client_version = await self.rpc("get_version")
            LOG.info("Client version: %s", self.client_version)
            if self.spotter:
                await self.rpc("start_recording", {"pcm": "noop", "channels": 1,
                    "bits_per_sample": 16, "sample_rate": 16000,
                    "period_size": 360, "buffer_size": 1440})
                LOG.info("Speaker microphone streaming started")
            await self.say("语音助手已就绪。")
            LOG.info("Greeting playback completed")
        except Exception as exc:
            LOG.warning("Greeting failed: %s", exc)

    def receive_audio(self, data):
        try:
            obj = json.loads(data)
            if obj.get("tag") != "record":
                return
            pcm = bytes(obj["bytes"])
        except (ValueError, KeyError, TypeError):
            return
        self.audio_bytes += len(pcm)
        if not self.spotter:
            return
        import numpy as np
        if pcm and len(pcm) % 2 == 0:
            self.audio_peak = int(np.max(np.abs(np.frombuffer(pcm, dtype="<i2").astype(np.int32))))
        if self.speaking or time.monotonic() < self.listening_until:
            self.reset_kws = True
            return
        if self.audio_queue.full():
            self.audio_queue.get_nowait()
            self.audio_dropped += 1
        self.audio_queue.put_nowait(pcm)

    async def audio_worker(self):
        while True:
            pcm = await self.audio_queue.get()
            if self.reset_kws:
                await asyncio.to_thread(self.spotter.reset)
                self.reset_kws = False
            if self.speaking or time.monotonic() < self.listening_until:
                continue
            word = await asyncio.to_thread(self.spotter.feed, pcm)
            if word and not self.speaking and time.monotonic() >= self.listening_until:
                if self.wake_job is None or self.wake_job.done():
                    self.wake_count += 1
                    LOG.info("Custom wake word detected: %s", word)
                    self.wake_job = asyncio.create_task(self.begin_listening())
                    self.wake_job.add_done_callback(self.observe_job)

    async def begin_listening(self):
        try:
            await self.cancel_listen_timer()
            await self.cancel()
            self.listening_until = time.monotonic() + 25
            await self.rpc("run_shell", "mphelper pause >/dev/null 2>&1")
            await self.say("我在。")
            # Native ASR is opened explicitly; no spoken stock wake word needed.
            result = await self.rpc("run_shell", "ubus call pnshelper event_notify '{\"src\":1,\"event\":0}'")
            if result.get("exit_code") != 0:
                raise RuntimeError("原生收音接口调用失败")
            self.listening_until = time.monotonic() + self.listen_timeout
            self.listen_timer = asyncio.create_task(self.wait_for_recognition())
            self.listen_timer.add_done_callback(self.observe_job)
            LOG.info("Waiting for native ASR after custom wake")
        except Exception as exc:
            self.listening_until = 0
            self.last_error = "Wake failed: " + str(exc)
            LOG.error("%s", self.last_error)

    async def cancel_listen_timer(self):
        timer, self.listen_timer = self.listen_timer, None
        if timer and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer

    async def wait_for_recognition(self):
        await asyncio.sleep(max(0, self.listening_until - time.monotonic()))
        if not self.listening_until:
            return
        self.listening_until = 0
        self.last_error = "ASR_TIMEOUT: 唤醒后未收到最终识别文字，未向 Agent 提交命令"
        LOG.warning(self.last_error)
        with contextlib.suppress(Exception):
            await self.say("没有收到语音识别结果，请再叫我一次，听到我在后说出问题。")

    async def restore_native_voice(self):
        # LX06 keeps frontend capture state across AIVS restarts. Restarting only
        # AIVS left event_notify returning code 0 without a Recognize request.
        # Recover its frontend too before allowing the next wake/listen cycle.
        result = await self.rpc("run_shell",
            "/etc/init.d/mico_aivs_lab restart >/dev/null 2>&1 && "
            "/etc/init.d/pns restart >/dev/null 2>&1", timeout=35)
        if result.get("exit_code") != 0:
            raise RuntimeError("小爱语音服务或收音前端恢复失败")
        await asyncio.sleep(3)
        LOG.info("Native AIVS and PNS frontend recovered together")

    async def cancel(self):
        job, self.job = self.job, None
        if job and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job

    async def event(self, event):
        result = recognize(event)
        if not result:
            return
        text, identity = result
        self.asr_count += 1
        LOG.info("Received final native ASR event; count=%d", self.asr_count)
        if identity and identity in self.seen:
            return
        if identity:
            self.seen[identity] = True
            if len(self.seen) > 256:
                self.seen.pop(next(iter(self.seen)))
        if time.monotonic() < self.listening_until:
            self.listening_until = 0
            await self.cancel_listen_timer()
            question = re.sub(r"^大爱同学[，,。！!：:\s]*", "", text).strip()
        else:
            question = route(text)
        if question is None:
            return
        LOG.info("Routed final ASR event to Harness")
        # The stock wake detector can also open ASR while the custom greeting is
        # playing. Once a final question arrives, prevent the greeting task from
        # reopening a second listening window or reporting an expected cut-off.
        if self.wake_job and not self.wake_job.done():
            self.wake_job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.wake_job
        await self.cancel()
        self.job = asyncio.create_task(self.dialog(question))
        self.job.add_done_callback(self.observe_job)

    @staticmethod
    def observe_job(task):
        # dialog logs failures; retrieving the exception avoids orphan task logs.
        if not task.cancelled():
            task.exception()

    async def dialog(self, question):
        try:
            self.last_error = None
            await self.restore_native_voice()
            if question in ("新对话", "重新开始", "清空对话"):
                self.runner.session_id = None
                self.runner.state_file.unlink(missing_ok=True)
                await self.say("已开始新的助手对话。")
                return "已开始新的助手对话。"
            if question in ("停止", "停下", "取消"):
                await self.say("已停止。")
                return "已停止。"
            if not question:
                await self.say("我在。请说小爱同学，问助手，加上你的问题。")
                return "等待问题"
            await self.say("我想一想。")
            answer = await self.runner.run(question)
            await self.say(answer)
            self.completed += 1
            self.last_error = None
            LOG.info("Harness reply played; completed=%d", self.completed)
            return answer
        except asyncio.CancelledError:
            LOG.info("Assistant turn interrupted")
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__ + ": " + str(exc)
            LOG.error("Assistant turn failed: %s", self.last_error)
            with contextlib.suppress(Exception):
                await self.say("这次助手连接遇到问题，请稍后再试。")
            raise

    async def health(self, request):
        return web.json_response({"speaker_connected": self.ws is not None and not self.ws.closed,
            "speaker_ip": self.speaker_ip, "client_version": self.client_version,
            "busy": self.job is not None and not self.job.done(),
            "completed_turns": self.completed, "session_id": self.runner.session_id,
            "last_tools": self.runner.last_tools,
            "backend": getattr(self.runner, "backend", "headless"),
            "desktop_plugin_ready": (self.runner.state_dir / "desktop.json").exists(),
            "wake_word": self.spotter.phrase if self.spotter else None,
            "wake_count": self.wake_count, "asr_count": self.asr_count,
            "listening": time.monotonic() < self.listening_until,
            "audio_bytes": self.audio_bytes, "audio_peak": self.audio_peak,
            "audio_dropped": self.audio_dropped,
            "last_error": self.last_error})

    async def ask(self, request):
        if not self.ws or self.ws.closed:
            raise web.HTTPServiceUnavailable(text="Speaker disconnected")
        if self.job and not self.job.done():
            raise web.HTTPConflict(text="Assistant busy")
        data = await request.json()
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise web.HTTPBadRequest(text="Invalid text")
        self.job = asyncio.create_task(self.dialog(text.strip()))
        try:
            answer = await self.job
        except asyncio.CancelledError:
            raise web.HTTPConflict(text="Turn interrupted")
        except Exception:
            raise web.HTTPBadGateway(text="Agent turn failed; inspect local service log")
        return web.json_response({"answer": answer, "session_id": self.runner.session_id})


async def serve(args):
    runner = AutoRunner(args.dsh, args.state, args.timeout)
    if args.no_kws:
        spotter = None
    else:
        from wakeword import WakeWord
        spotter = await asyncio.to_thread(WakeWord, Path(args.state) / "models")
        LOG.info("Custom keyword model loaded: %s", spotter.phrase)
    bridge = Bridge(args.speaker_ip, runner, spotter)
    public = web.Application(client_max_size=16384)
    public.router.add_get("/", bridge.websocket)
    local = web.Application(client_max_size=16384)
    local.router.add_get("/health", bridge.health)
    local.router.add_post("/ask", bridge.ask)
    servers = [web.AppRunner(public), web.AppRunner(local)]
    for app in servers:
        await app.setup()
    await web.TCPSite(servers[0], args.bind, args.port).start()
    await web.TCPSite(servers[1], "127.0.0.1", args.control_port).start()
    LOG.info("Speaker endpoint %s:%d; local control 127.0.0.1:%d", args.bind, args.port, args.control_port)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await bridge.cancel()
        if bridge.ws is not None:
            await bridge.ws.close()
        for app in servers:
            await app.cleanup()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speaker-ip", default=os.getenv("XIAOAI_SPEAKER_IP"))
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4399)
    parser.add_argument("--control-port", type=int, default=4400)
    parser.add_argument("--dsh", default=os.getenv("XIAOAI_DSH", DEFAULT_DSH))
    parser.add_argument("--state", default=str(ROOT / ".runtime"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--no-kws", action="store_true", help="Disable microphone keyword spotting for diagnostics")
    parser.add_argument("--ask", help="Send a test question through the running local service")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.ask or args.status:
        async with ClientSession() as session:
            if args.ask:
                response = await session.post(f"http://127.0.0.1:{args.control_port}/ask", json={"text": args.ask})
            else:
                response = await session.get(f"http://127.0.0.1:{args.control_port}/health")
            print(await response.text())
            response.raise_for_status()
        return
    if not args.speaker_ip:
        parser.error("--speaker-ip or XIAOAI_SPEAKER_IP is required")
    await serve(args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
