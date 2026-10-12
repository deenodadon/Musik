"""mpv JSON IPC, with no shell invocation or bundled browser."""
import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .core import State, Track, Store
from .visualizer import rms_level

FILTERS = {
    "flat": "",
    "bass": "lavfi=[bass=g=6:f=100]",
    "vocal": "lavfi=[equalizer=f=3000:t=q:w=1:g=4]",
}


def playback_error(message):
    """Classify mpv output without returning URLs, headers, or cookies."""
    text = str(message).lower()
    if any(marker in text for marker in ("403 forbidden", "http error 403", "server returned 403", "http 403")):
        return "YouTube's audio server rejected the stream (HTTP 403); its URL or playback token may be invalid."
    if any(marker in text for marker in ("404 not found", "http error 404", "server returned 404", "http 404")):
        return "The audio stream was not found (HTTP 404)."
    if any(marker in text for marker in ("connection timed out", "connection refused", "failed to resolve", "name or service not known")):
        return "mpv could not connect to the audio server. Check network or proxy settings."
    if any(marker in text for marker in ("failed to initialize audio", "could not open/initialize audio device", "audio output initialization failed")):
        return "mpv could not open your audio output device."
    if any(marker in text for marker in ("failed to recognize file format", "no audio", "unsupported codec", "no streams found")):
        return "mpv could not decode an audio stream from this source."
    return None


class Player:
    def __init__(self, audio_output: str | None = None, store=None):
        self.store = store or Store()
        self.audio_output = audio_output
        self.failure_detail = None
        self.public_fallback = False
        self.process = None
        self.writer = None
        self.reader = None
        self.task = None
        self.temp = None
        self.pending = {}
        self.counter = 0
        self.events = asyncio.Queue()
        self.play_generation = 0
        self.active_generation = 0
        self.position = 0.0
        self.duration = 0.0
        self.paused = False
        self.idle = True
        self.levels = [0.0] * 32
        self.visualizer_task = None

    async def start(self):
        executable = os.environ.get("MUSIK_MPV") or shutil.which("mpv")
        if not executable:
            raise RuntimeError("mpv is missing. Install mpv or set MUSIK_MPV to its executable.")
        self.temp = tempfile.TemporaryDirectory(prefix="musik-")
        socket = r"\\.\pipe\musik-" + uuid.uuid4().hex if os.name == "nt" else str(Path(self.temp.name) / "mpv.sock")
        args = [executable, "--no-config", "--idle=yes", "--no-video", "--terminal=no",
                "--audio-display=no", f"--input-ipc-server={socket}"]
        if self.audio_output:
            args.append(f"--ao={self.audio_output}")
        self.process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL)
        try:
            for _ in range(100):
                if self.process.returncode is not None:
                    raise RuntimeError("mpv exited during startup")
                try:
                    if os.name == "nt":
                        from .windows_pipe import Pipe
                        self.reader = self.writer = await Pipe.connect(socket)
                    else:
                        self.reader, self.writer = await asyncio.open_unix_connection(socket)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("mpv IPC startup timed out")
            self.task = asyncio.create_task(self._read())
            await self.command("request_log_messages", "warn")
            for i, prop in enumerate(("time-pos", "duration", "pause", "idle-active")):
                await self.command("observe_property", i, prop)
            self.visualizer_task = asyncio.create_task(self._levels())
        except BaseException:
            await self.close()
            raise

    async def _levels(self):
        while True:
            level = 0.0
            if not self.paused and not self.idle:
                try:
                    metadata = await self.command("get_property", "af-metadata/musikviz")
                    level = rms_level(metadata or {})
                except RuntimeError:
                    pass
            self.levels = self.levels[1:] + [level]
            await asyncio.sleep(0.08)

    async def _read(self):
        try:
            while line := await self.reader.readline():
                message = json.loads(line)
                future = self.pending.pop(message.get("request_id"), None)
                if future is not None and not future.done():
                    if message.get("error") == "success":
                        future.set_result(message.get("data"))
                    else:
                        future.set_exception(RuntimeError(f"mpv: {message.get('error')}"))
                if message.get("event") == "property-change":
                    value = message.get("data")
                    if message.get("name") == "time-pos":
                        self.position = value or 0.0
                    elif message.get("name") == "duration":
                        self.duration = value or 0.0
                    elif message.get("name") == "idle-active":
                        self.idle = bool(value)
                    elif message.get("name") == "pause":
                        self.paused = bool(value)
                elif message.get("event") == "log-message":
                    detail = playback_error(message.get("text", ""))
                    if detail:
                        self.failure_detail = detail
                elif message.get("event") == "start-file":
                    self.active_generation = self.play_generation
                elif message.get("event") == "end-file":
                    message["_musik_generation"] = self.active_generation
                    if message.get("reason") == "error":
                        self.failure_detail = self.failure_detail or "mpv could not open this audio stream. Run musik doctor for diagnostics."
                        message["_musik_error"] = self.failure_detail
                    await self.events.put(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("mpv disconnected"))
            self.pending.clear()

    async def command(self, *args):
        if not self.writer:
            raise RuntimeError("Player is not started")
        self.counter += 1
        request = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request] = future
        try:
            self.writer.write((json.dumps({"command": args, "request_id": request}) + "\n").encode())
            await self.writer.drain()
            return await asyncio.wait_for(future, 5)
        finally:
            self.pending.pop(request, None)

    async def play(self, track: Track, state: State):
        self.play_generation += 1
        self.failure_detail = None
        self.public_fallback = False
        url = track.url
        if track.source != "local":
            from .stream import resolve_audio
            info = await asyncio.to_thread(resolve_audio, url, self.store)
            self.public_fallback = bool(info.get("_musik_public_fallback"))
            url = info["url"]
            headers = info.get("http_headers") or {}
            # Preserve playback headers without forwarding authentication cookies
            # from youtube.com to an audio CDN.
            fields = [f"{key}: {value}" for key, value in headers.items()
                      if key.lower() in ("user-agent", "referer", "origin")]
            await self.command("set_property", "http-header-fields", fields)
        else:
            await self.command("set_property", "http-header-fields", [])
        await self.settings(state)
        self.failure_detail = None
        await self.command("loadfile", url, "replace")
        await self.command("set_property", "pause", False)

    async def settings(self, state: State):
        await self.command("set_property", "volume", state.volume)
        await self.command("set_property", "speed", state.speed)
        eq = FILTERS.get(state.equalizer, "")
        meter = "@musikviz:lavfi=[astats=metadata=1:reset=1]"
        await self.command("set_property", "af", f"{eq},{meter}" if eq else meter)

    async def close(self):
        if self.visualizer_task:
            self.visualizer_task.cancel()
            await asyncio.gather(self.visualizer_task, return_exceptions=True)
        if os.name == "nt" and self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.temp:
            self.temp.cleanup()
