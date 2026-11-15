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

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from netease_music import parse_command as music_command, request as music_request
from feedback import prepare as prepare_feedback, install_script as feedback_install, play_script as feedback_play, open_asr_script
from voice_config import WAKE_PHRASE
from voice_commands import dismiss_command
from bilibili_video import parse_command as video_command, request as video_request

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
        self.session_id = json.loads(self.state_file.read_text()).get('session_id') if self.state_file.exists() else None
        self.last_tools = []

    async def run(self, text):
        descriptor = json.loads((self.state_dir / "desktop.json").read_text())
        url = descriptor["url"]
        if not url.startswith("http://127.0.0.1:"):
            raise RuntimeError("Desktop endpoint must be loopback")
        async with ClientSession(headers={"Authorization": "Bearer " + descriptor["token"]},
                                 timeout=ClientTimeout(total=1830, sock_connect=5)) as session:
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
        self.current = self.desktop if (self.state_dir / 'desktop.json').exists() else self.headless

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
    def __init__(self, speaker_ip, runner, spotter=None, state_dir=None, prompts=None):
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
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.local_completed = 0
        self.last_turn = None
        self.native_recovery = None
        self.prompts = prompts
        self.feedback_installed = False
        self.feedback_lock = asyncio.Lock()
        self.wake_guard_until = 0
        self.control_followup_until = 0
        self.pending_question = None
        self.last_recognition = None
        self.listen_started = 0
        self.last_speech_at = 0
        self.rearm_needed = False
        self.rearm_attempts = 0
        self.rearm_job = None
        self.custom_dialogs = {}
        self.native_pause_job = None
        self.native_pause_requested = False
        self.native_music_suppressed = 0

    def clear_audio(self):
        while not self.audio_queue.empty():
            self.audio_queue.get_nowait()
        self.reset_kws = True

    async def ensure_feedback(self):
        async with self.feedback_lock:
            if not self.feedback_installed:
                if self.prompts is None:
                    raise RuntimeError('Local feedback files are not prepared')
                for command in feedback_install(self.prompts):
                    result = await self.rpc('run_shell', command)
                    if result.get('exit_code') != 0:
                        raise RuntimeError('Local feedback installation failed')
                self.feedback_installed = True

    async def feedback(self, name):
        await self.ensure_feedback()
        self.speaking += 1
        self.clear_audio()
        try:
            result = await self.rpc('run_shell', feedback_play(name), timeout=15)
            if not tts_completed(result):
                raise RuntimeError('Local feedback playback failed')
            LOG.info('Local feedback played: %s', name)
        finally:
            self.speaking -= 1
            self.wake_guard_until = time.monotonic() + .35
            self.clear_audio()

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
        self.feedback_installed = False
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
            await self.cancel_rearm()
            if self.native_pause_job:
                self.native_pause_job.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.native_pause_job
            self.custom_dialogs.clear()
            self.native_pause_requested = False
            self.rearm_needed = False
            self.rearm_attempts = 0
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
            await self.ensure_feedback()
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
        if self.speaking or time.monotonic() < max(self.listening_until, self.wake_guard_until):
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
            if self.speaking or time.monotonic() < max(self.listening_until, self.wake_guard_until):
                continue
            word = await asyncio.to_thread(self.spotter.feed, pcm)
            if word and not self.speaking and time.monotonic() >= max(self.listening_until, self.wake_guard_until):
                if self.wake_job is None or self.wake_job.done():
                    self.wake_count += 1
                    LOG.info("Custom wake word detected: %s", word)
                    self.wake_job = asyncio.create_task(self.begin_listening())
                    self.wake_job.add_done_callback(self.observe_job)

    async def begin_listening(self):
        try:
            await self.cancel_listen_timer()
            await self.cancel_rearm()
            self.rearm_needed = False
            self.rearm_attempts = 0
            self.listening_until = time.monotonic() + 25
            self.control_followup_until = time.monotonic() + 20
            self.clear_audio()
            # A fast music open can finish before PNS becomes ready. Preserve
            # that recovery when the user immediately wakes us again.
            if self.native_recovery is not None:
                await asyncio.shield(self.native_recovery)
            await self.cancel()
            self.listening_until = time.monotonic() + 25
            await self.rpc("run_shell", "mphelper pause >/dev/null 2>&1")
            result = await self.rpc('run_shell', open_asr_script(), timeout=8)
            if result.get('exit_code') != 0 or 'ASR_READY' not in result.get('stdout', ''):
                await self.restore_native_voice()
                result = await self.rpc('run_shell', open_asr_script(), timeout=8)
                if result.get('exit_code') != 0 or 'ASR_READY' not in result.get('stdout', ''):
                    raise RuntimeError('原生收音启动未确认')
            # Fixed cached speech is bounded and cannot arrive later via cloud TTS.
            await self.feedback('ready')
            if self.rearm_needed:
                await self.rearm_listening()
                if not self.listening_until:
                    return
            self.listen_started = time.monotonic()
            self.last_speech_at = 0
            self.listening_until = time.monotonic() + self.listen_timeout
            self.listen_timer = asyncio.create_task(self.wait_for_recognition())
            self.listen_timer.add_done_callback(self.observe_job)
            LOG.info("Waiting for native ASR after custom wake")
        except Exception as exc:
            self.listening_until = 0
            self.last_error = "Wake failed: " + str(exc)
            LOG.error("%s", self.last_error)
            with contextlib.suppress(Exception):
                await self.feedback('not_ready')

    async def cancel_listen_timer(self):
        timer, self.listen_timer = self.listen_timer, None
        if timer and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer

    async def cancel_rearm(self):
        task, self.rearm_job = self.rearm_job, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def rearm_listening(self):
        if not self.rearm_needed or self.rearm_attempts >= 1 or not self.listening_until:
            return
        self.rearm_needed = False
        self.rearm_attempts += 1
        try:
            result = await self.rpc('run_shell', open_asr_script(), timeout=8)
            if result.get('exit_code') != 0 or 'ASR_READY' not in result.get('stdout', ''):
                raise RuntimeError('Greeting-only ASR could not be rearmed')
            self.listen_started = time.monotonic()
            self.last_speech_at = 0
            self.listening_until = time.monotonic() + self.listen_timeout
            LOG.info('Rearmed ASR after greeting-only final; no second greeting')
        except Exception as exc:
            self.listening_until = 0
            self.last_error = str(exc)
            await self.feedback('not_ready')

    async def wait_for_recognition(self):
        while self.listening_until:
            deadline = min(self.listening_until,
                           self.last_speech_at + 6 if self.last_speech_at else self.listen_started + 8)
            delay = deadline - time.monotonic()
            if delay <= 0:
                break
            await asyncio.sleep(delay)
        if not self.listening_until:
            return
        self.listening_until = 0
        self.last_error = "ASR_TIMEOUT: 唤醒后未收到最终识别文字，未向 Agent 提交命令"
        LOG.warning(self.last_error)
        with contextlib.suppress(Exception):
            await self.feedback('unclear' if self.last_speech_at else 'no_input')

    async def restore_native_voice(self):
        # LX06 keeps frontend capture state across AIVS restarts. Restarting only
        # AIVS left event_notify returning code 0 without a Recognize request.
        # Recover its frontend too before allowing the next wake/listen cycle.
        result = await self.rpc("run_shell",
            # Restarting AIVS does NOT stop a track already handed to the
            # separate mediaplayer. Cover already-started and queued playback
            # on both sides of the voice-service restart.
            "mphelper pause >/dev/null 2>&1 && "
            "/etc/init.d/mico_aivs_lab restart >/dev/null 2>&1 && "
            "/etc/init.d/pns restart >/dev/null 2>&1 && "
            "mphelper pause >/dev/null 2>&1", timeout=35)
        if result.get("exit_code") != 0:
            raise RuntimeError("小爱语音服务或收音前端恢复失败")
        await asyncio.sleep(3)
        LOG.info("Native AIVS and PNS frontend recovered together")

    def guard_native_playback(self, event):
        """Own only ASR dialog IDs from custom listening, never stock requests."""
        if event.get('event') != 'instruction':
            return
        try:
            obj = json.loads(event.get('data', {}).get('NewLine', ''))
            header = obj.get('header', {})
            identity = header.get('dialog_id')
            if not isinstance(identity, str) or not identity:
                return
            now = time.monotonic()
            self.custom_dialogs = {key: expiry for key, expiry in self.custom_dialogs.items() if expiry > now}
            if (header.get('namespace'), header.get('name')) == ('SpeechRecognizer', 'RecognizeResult'):
                if now < self.listening_until:
                    self.custom_dialogs[identity] = now + 30
                    if len(self.custom_dialogs) > 32:
                        self.custom_dialogs.pop(next(iter(self.custom_dialogs)))
            elif ((header.get('namespace'), header.get('name')) == ('AudioPlayer', 'Play')
                  and identity in self.custom_dialogs):
                self.native_pause_requested = True
                if self.native_pause_job is None or self.native_pause_job.done():
                    self.native_pause_job = asyncio.create_task(self.pause_competing_native_music())
                    self.native_pause_job.add_done_callback(self.observe_job)
        except (ValueError, TypeError, AttributeError):
            pass

    async def pause_competing_native_music(self):
        # Never await RPC in the WebSocket event reader: its next messages carry
        # that RPC's response. Coalesce bursts without losing a late Play event.
        try:
            while self.native_pause_requested:
                self.native_pause_requested = False
                result = await self.rpc('run_shell', 'mphelper pause >/dev/null 2>&1', timeout=8)
                if result.get('exit_code') != 0:
                    raise RuntimeError('原生竞争播放暂停失败')
                self.native_music_suppressed += 1
                LOG.info('Paused competing native music for an owned custom ASR dialog')
        except Exception as exc:
            self.last_error = 'Native playback guard: ' + str(exc)
            LOG.error('%s', self.last_error)

    async def cancel(self):
        self.pending_question = None
        job, self.job = self.job, None
        if job and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job

    async def event(self, event):
        self.guard_native_playback(event)
        result = recognize(event)
        if not result:
            # Partial speech extends the timeout but never executes a command.
            if time.monotonic() < self.listening_until:
                try:
                    obj = json.loads(event.get('data', {}).get('NewLine', ''))
                    if obj.get('header', {}).get('name') == 'RecognizeResult' and any(
                            r.get('text', '').strip() for r in obj.get('payload', {}).get('results', [])):
                        self.last_speech_at = time.monotonic()
                except (TypeError, ValueError, AttributeError):
                    pass
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
        cleaned = re.sub('^' + re.escape(WAKE_PHRASE) + r'[，,。！!：:\s]*', '', text).strip()
        now = time.monotonic()
        active = now < self.listening_until
        without_echo = re.sub(r'^[哎诶唉][，,。！!：:\s]*', '', cleaned)
        if active and without_echo != cleaned and (music_command(without_echo) or video_command(without_echo) or dismiss_command(without_echo)):
            cleaned = without_echo
        command = video_command(cleaned) or music_command(cleaned)
        if active and cleaned.rstrip('，,。！!：: ') in {'', '哎', '诶', '唉', '小爱同学'}:
            self.last_recognition = {'decision': 'ignored_greeting'}
            LOG.info('Ignored wake-only/greeting ASR; listening remains open')
            self.rearm_needed = True
            if (self.wake_job is None or self.wake_job.done()) and (self.rearm_job is None or self.rearm_job.done()):
                self.rearm_job = asyncio.create_task(self.rearm_listening())
                self.rearm_job.add_done_callback(self.observe_job)
            return
        if active:
            question = cleaned
            decision = 'custom_window'
        elif now < self.control_followup_until and (dismiss_command(cleaned) or command and command['kind'] in {'pause', 'resume'}):
            question = cleaned
            decision = 'recent_wake_control'
        else:
            question = route(text)
            decision = 'prefix' if question is not None else 'ignored_no_context'
        self.last_recognition = {'decision': decision, 'kind': command['kind'] if command else 'other'}
        if question is None:
            LOG.info('Final ASR ignored: %s', json.dumps(self.last_recognition))
            return
        self.listening_until = 0
        if dismiss_command(question):
            self.control_followup_until = 0
            self.pending_question = None
        await self.cancel_listen_timer()
        await self.cancel_rearm()
        LOG.info("Routed final ASR event to command dispatcher")
        # The stock wake detector can also open ASR while the custom greeting is
        # playing. Once a final question arrives, prevent the greeting task from
        # reopening a second listening window or reporting an expected cut-off.
        if self.wake_job and not self.wake_job.done():
            self.wake_job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.wake_job
        if self.job and not self.job.done() and self.last_turn and self.last_turn['route'] in {'local_music', 'local_video'}:
            # Do not cancel PNS recovery or block the WebSocket reader on RPC.
            self.pending_question = question
            self.last_recognition['decision'] = 'queued_after_local_recovery'
            LOG.info('Queued recognized command until current local recovery completes')
            return
        await self.cancel()
        self.job = asyncio.create_task(self.dialog(question))
        self.job.add_done_callback(self.observe_job)

    def observe_job(self, task):
        # dialog logs failures; retrieving the exception avoids orphan task logs.
        if not task.cancelled():
            task.exception()
        if task is self.job and self.pending_question is not None:
            question, self.pending_question = self.pending_question, None
            self.job = asyncio.create_task(self.dialog(question))
            self.job.add_done_callback(self.observe_job)

    async def dialog(self, question):
        started = time.monotonic()
        metrics = {"id": uuid.uuid4().hex[:12], "route": "agent", "agent_called": False}
        self.last_turn = metrics
        owned = []
        local_result = None
        try:
            self.last_error = None
            if dismiss_command(question):
                metrics['route'] = 'local_control'
                self.listening_until = 0
                self.control_followup_until = 0
                self.pending_question = None
                await self.cancel_listen_timer()
                await self.cancel_rearm()
                self.clear_audio()
                recovery = asyncio.create_task(self.timed(metrics, 'native_recovery_ms', self.restore_native_voice()))
                self.native_recovery = recovery
                owned.append(recovery)
                await self.timed(metrics, 'receipt_ms', self.feedback('received'))
                metrics.update(spoken=True, status='dismissed', ok=True, reply_tts=False)
                await recovery
                self.completed += 1
                return '已退出本轮收音，等待下一次唤醒。'
            video = video_command(question)
            if video is not None or music_command(question) is not None:
                kind = 'video' if video is not None else 'music'
                execute = video_request if video is not None else music_request
                metrics['route'] = 'local_' + kind
                # Stop stock replies and restore capture state while the Mac
                # opens the file; playback need not wait for frontend recovery.
                recovery = asyncio.create_task(self.timed(metrics, 'native_recovery_ms', self.restore_native_voice()))
                self.native_recovery = recovery
                owned.append(recovery)
                await self.timed(metrics, 'receipt_ms', self.feedback('received'))
                metrics['spoken'] = True
                playback = asyncio.create_task(self.timed(metrics, kind + '_command_ms',
                    asyncio.to_thread(execute, question)))
                owned.append(playback)
                result = await playback
                local_result = result
                metrics.update({kind + '_' + key: value for key, value in result.get('timing', {}).items()})
                metrics['command_result_after_ms'] = round((time.monotonic() - started) * 1000, 1)
                metrics['status'] = result.get('status', 'answered' if result.get('ok') else 'failed')
                metrics[kind + '_source'] = result.get('source', 'local')
                metrics['ok'] = bool(result.get('ok'))
                LOG.info('Local %s result: %s', kind, json.dumps(metrics))
                if result.get('ok'):
                    await self.timed(metrics, 'confirmation_ms', self.feedback('done'))
                    metrics['confirmed'] = True
                    if self.control_followup_until > started:
                        self.control_followup_until = max(self.control_followup_until, time.monotonic() + 10)
                await recovery
                # Fixed native prompts acknowledge successful actions. Queries
                # and failures also need their actual result, without an Agent.
                metrics['reply_tts'] = not result.get('silent', result.get('status') == 'opened')
                if metrics['reply_tts']:
                    await self.timed(metrics, 'answer_tts_ms', self.say(result['message']))
                self.completed += 1
                self.local_completed += 1
                if not result.get('ok'):
                    self.last_error = result['message']
                return result['message']
            recovery = asyncio.create_task(self.timed(metrics, 'native_recovery_ms', self.restore_native_voice()))
            self.native_recovery = recovery
            owned.append(recovery)
            await self.timed(metrics, 'receipt_ms', self.feedback('received'))
            metrics['spoken'] = True
            await recovery
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
            metrics['agent_called'] = True
            answer = await self.timed(metrics, 'agent_ms', self.run_with_feedback(question))
            await self.timed(metrics, 'answer_tts_ms', self.say(answer))
            self.completed += 1
            self.last_error = None
            LOG.info("Harness reply played; completed=%d", self.completed)
            return answer
        except asyncio.CancelledError:
            metrics['status'] = 'cancelled'
            LOG.info("Assistant turn interrupted")
            raise
        except Exception as exc:
            metrics['status'] = 'error'
            self.last_error = type(exc).__name__ + ": " + str(exc)
            LOG.error("Assistant turn failed: %s", self.last_error)
            with contextlib.suppress(Exception):
                await self.say(self.failure_message(exc))
            raise
        finally:
            for task in owned:
                if not task.done():
                    task.cancel()
            if owned:
                await asyncio.gather(*owned, return_exceptions=True)
                if self.native_recovery in owned:
                    self.native_recovery = None
            metrics['total_ms'] = round((time.monotonic() - started) * 1000, 1)
            if local_result is not None:
                self.record_local_action(question, local_result, metrics)
            LOG.info('Turn timing: %s', json.dumps(metrics))

    @staticmethod
    async def timed(metrics, name, awaitable):
        started = time.monotonic()
        try:
            return await awaitable
        finally:
            metrics[name] = round((time.monotonic() - started) * 1000, 1)

    def record_local_action(self, question, result, metrics):
        if self.state_dir is None:
            return
        entry = {"time": time.time(), "command": question, "result": result, "timing": dict(metrics)}
        try:
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.state_dir / ('video-actions.jsonl' if metrics['route'] == 'local_video' else 'music-actions.jsonl')
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError as exc:
            LOG.warning('Local action journal unavailable: %s', exc)

    @staticmethod
    def failure_message(exc):
        detail = str(exc)
        if "VOICE_TIMEOUT" in detail:
            return "这项任务已达到三十分钟上限，请在电脑的语音对话窗口查看执行情况。"
        if "VOICE_CANCELLED" in detail or "Voice turn cancelled" in detail:
            return "这项任务被中断了，请在电脑的语音对话窗口查看执行情况。"
        if "busy" in detail.lower() or "already running" in detail.lower():
            return "助手会话正在处理其他输入，请等它完成后再试。"
        if isinstance(exc, asyncio.TimeoutError):
            return "等待助手回复超时，请在电脑的语音对话窗口查看执行情况。"
        return "这次助手处理没有完成，请在电脑的语音对话窗口查看具体原因。"

    async def run_with_feedback(self, question, interval=45):
        task = asyncio.create_task(self.runner.run(question))
        try:
            # Slow tool work remains one owned task. Feedback never resubmits it.
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if done:
                    return await task
                with contextlib.suppress(Exception):
                    await self.say("我还在处理这项任务，完成后会告诉你。")
                interval = 120
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def health(self, request):
        return web.json_response({"speaker_connected": self.ws is not None and not self.ws.closed,
            "speaker_ip": self.speaker_ip, "client_version": self.client_version,
            "busy": self.job is not None and not self.job.done(),
            "completed_turns": self.completed, "session_id": self.runner.session_id,
            "last_tools": self.runner.last_tools,
            "backend": getattr(self.runner, "backend", "headless"),
            "desktop_plugin_ready": (self.runner.state_dir / "desktop.json").exists(),
            "wake_word": self.spotter.phrase if self.spotter else None,
            "local_completed_turns": self.local_completed, "last_turn": self.last_turn,
            "last_recognition": self.last_recognition, "queued_control": self.pending_question is not None,
            "feedback_installed": self.feedback_installed,
            "native_music_suppressed": self.native_music_suppressed,
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
        self.job.add_done_callback(self.observe_job)
        try:
            answer = await self.job
        except asyncio.CancelledError:
            raise web.HTTPConflict(text="Turn interrupted")
        except Exception:
            raise web.HTTPBadGateway(text="Agent turn failed; inspect local service log")
        return web.json_response({"answer": answer, "session_id": self.runner.session_id,
                                  "execution": self.last_turn})


async def serve(args):
    runner = AutoRunner(args.dsh, args.state, args.timeout)
    if args.no_kws:
        spotter = None
    else:
        from wakeword import WakeWord
        spotter = await asyncio.to_thread(WakeWord, Path(args.state) / "models")
        LOG.info("Custom keyword model loaded: %s", spotter.phrase)
    prompts = await asyncio.to_thread(prepare_feedback, Path(args.state) / 'feedback')
    bridge = Bridge(args.speaker_ip, runner, spotter, state_dir=args.state, prompts=prompts)
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
        async with ClientSession(timeout=ClientTimeout(total=1900 if args.ask else 10)) as session:
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
