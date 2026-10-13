"""mpv JSON IPC, with no shell invocation or bundled browser."""
import asyncio
import json
import math
import time
from collections import OrderedDict
import os
import tempfile
import subprocess
import uuid
from pathlib import Path

from .core import State, Track, Store
from .visualizer import rms_level
from .platform import mpv_executable

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
        self.pending_failure = None
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
        self.entry_generations = {}
        self.online_stream = False
        self.youtube_url = None
        self.loadfile_has_index = True
        self.resume_offset = 0.0
        self.position = 0.0
        self.duration = 0.0
        self.track_duration = None
        self.timestamp_origins = {}
        self.demuxer_start = None
        self.paused = False
        self.idle = True
        self.levels = [0.0] * 32
        self.visualizer_task = None
        self.format_id = None
        self.stream_retry_used = False
        self.playback_started = False
        self.audio_cache = OrderedDict()
        self.audio_resolutions = {}
        from .nuclear import NuclearRuntime
        self.nuclear = NuclearRuntime(self.store, control_enabled=False)

    async def start(self):
        executable = mpv_executable()
        if not executable:
            raise RuntimeError("mpv is missing. Install mpv or set MUSIK_MPV to its executable.")
        self.temp = tempfile.TemporaryDirectory(prefix="musik-")
        socket = r"\\.\pipe\musik-" + uuid.uuid4().hex if os.name == "nt" else str(Path(self.temp.name) / "mpv.sock")
        args = [executable, "--no-config", "--idle=yes", "--no-video", "--terminal=no",
                "--audio-display=no", f"--input-ipc-server={socket}"]
        if self.audio_output:
            args.append(f"--ao={self.audio_output}")
        launch_options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        self.process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL, **launch_options)
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
            await self.detect_loadfile_command()
            await self.command("request_log_messages", "warn")
            for i, prop in enumerate(("time-pos", "duration", "pause", "idle-active", "audio-pts", "demuxer-start-time")):
                await self.command("observe_property", i, prop)
            self.visualizer_task = asyncio.create_task(self._levels())
        except BaseException:
            await self.close()
            raise

    async def detect_loadfile_command(self):
        commands = await self.command('get_property', 'command-list')
        loadfile = next(command for command in commands if command['name'] == 'loadfile')
        self.loadfile_has_index = any(argument.get('name') == 'index' for argument in loadfile['args'])

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

    def update_position(self, source, value):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        duration = self.track_duration or self.duration
        if value < -1 or (duration > 0 and value > duration + 1):
            start = self.demuxer_start
            if isinstance(start, (int, float)) and 0 <= value - start <= duration:
                value -= start
            else:
                origin = self.timestamp_origins.setdefault(source, value)
                value = value - origin + self.resume_offset
        self.position = max(0.0, min(value, duration) if duration > 0 else value)

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
                    if message.get("name") in ("time-pos", "audio-pts"):
                        if self.active_generation == self.play_generation:
                            self.update_position(message["name"], value)
                    elif message.get("name") == "demuxer-start-time":
                        self.demuxer_start = value
                    elif message.get("name") == "duration":
                        self.duration = self.track_duration or value or 0.0
                    elif message.get("name") == "idle-active":
                        self.idle = bool(value)
                    elif message.get("name") == "pause":
                        self.paused = bool(value)
                elif message.get("event") == "log-message":
                    detail = playback_error(message.get("text", ""))
                    if detail:
                        self.pending_failure = detail
                elif message.get("event") == "file-loaded":
                    if self.active_generation == self.play_generation:
                        self.failure_detail = None
                    await self.events.put({"event": "file-loaded", "_musik_generation": self.active_generation})
                elif message.get("event") == "playback-restart":
                    if self.active_generation == self.play_generation:
                        self.playback_started = True
                        self.failure_detail = None
                        self.pending_failure = None
                elif message.get("event") == "start-file":
                    self.active_generation = self.play_generation
                    entry = message.get("playlist_entry_id")
                    if entry is not None:
                        self.entry_generations[entry] = self.active_generation
                        if len(self.entry_generations) > 16:
                            self.entry_generations.pop(next(iter(self.entry_generations)))
                elif message.get("event") == "end-file":
                    # Retain completed IDs so a delayed/duplicate terminal event
                    # cannot be attributed to the replacement stream.
                    ending_generation = self.entry_generations.get(message.get("playlist_entry_id"), self.active_generation)
                    message["_musik_generation"] = ending_generation
                    if message.get("reason") == "eof" and ending_generation == self.play_generation:
                        completed = self.duration > 0 and self.position >= self.duration - 1
                        rejected = self.pending_failure and "HTTP 403" in self.pending_failure
                        if not self.playback_started or (rejected and not completed):
                            message["reason"] = "error"
                    if message.get("reason") == "error":
                        detail = self.pending_failure or self.failure_detail or "mpv could not open this audio stream. Run musik doctor for diagnostics."
                        if ending_generation == self.play_generation:
                            recoverable = "HTTP 403" in detail and self.online_stream and not self.stream_retry_used
                            self.failure_detail = None if recoverable else detail
                        message["_musik_error"] = detail
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

    def audio_cache_key(self, track):
        from .auth import cookie_path
        from .playback_setup import helper_ready
        paths = (cookie_path(self.store), self.store.directory / 'account.json', self.store.directory / 'plugins/plugins.json', self.store.directory / 'plugins/settings.json', self.store.directory / 'torrents.json')
        stamps = tuple(path.stat().st_mtime_ns if path.exists() else None for path in paths)
        return track.url, stamps, helper_ready()

    def cache_audio(self, key, info):
        from urllib.parse import urlparse, parse_qs, unquote
        lifetime = 300.0
        url = info.get('url', '')
        # mpv's archive protocol is not an HTTP URL. Its entry name can
        # contain brackets, which urllib otherwise treats as an IPv6 host.
        if url.startswith('archive://'):
            url = unquote(url[len('archive://'):].split('|', 1)[0])
        parsed = urlparse(url)
        expiry = parse_qs(parsed.query).get('expire', [None])[0]
        pieces = parsed.path.split('/')
        if expiry is None and 'expire' in pieces:
            index = pieces.index('expire')
            expiry = pieces[index + 1] if index + 1 < len(pieces) else None
        if expiry:
            try:
                lifetime = min(lifetime, float(expiry) - time.time() - 60)
            except ValueError:
                pass
        if lifetime <= 0:
            return
        self.audio_cache[key] = (time.monotonic() + lifetime, info)
        self.audio_cache.move_to_end(key)
        while len(self.audio_cache) > 256:
            self.audio_cache.popitem(last=False)

    def resolve_track(self, track):
        if track.source == 'torbox':
            from .torrents import TorBox
            return TorBox(self.store).stream(track)
        if track.source == 'torrent':
            raise RuntimeError('Open this torrent with Enter and select an audio file first')
        from .nuclear import nuclear_track
        if track.source == 'nuclear':
            payload = json.loads(track.id)
            preferred = self.nuclear.selected('streaming') or payload.get('streaming')
            source = payload['track'].get('source') or {}
            if not preferred:
                for descriptor in self.nuclear.status()['providers']:
                    if descriptor['id'] == source.get('provider'):
                        preferred = descriptor['id'] if descriptor['kind'] == 'streaming' else descriptor.get('streamingProviderId')
                        if preferred:
                            break
            if preferred:
                info = self.nuclear.call('stream', track=nuclear_track(track), provider=preferred)
                return {'url': info['url'], 'duration': (info.get('durationMs') or 0) / 1000, 'http_headers': info.get('headers', {}), '_musik_youtube_url': info.get('_musik_youtube_url'), 'format_id': info.get('format_id')}
            # Metadata-only providers need a separate audio source. The Musik
            # default uses its existing YouTube search/extractor, not whichever
            # unrelated plugin happened to register first.
            from .service import MusicService
            candidates = MusicService(self.store).search_videos(f'{track.artist} {track.title}')
            if not candidates:
                raise RuntimeError('No audio match found; select a streaming provider in Plugins')
            from .stream import resolve_audio
            info = resolve_audio(candidates[0].url, self.store)
            info['_musik_youtube_url'] = candidates[0].url
            return info
        if provider := self.nuclear.selected('streaming'):
            info = self.nuclear.call('stream', track=nuclear_track(track), provider=provider)
            return {'url': info['url'], 'duration': (info.get('durationMs') or 0) / 1000, 'http_headers': info.get('headers', {}), '_musik_youtube_url': info.get('_musik_youtube_url'), 'format_id': info.get('format_id')}
        from .stream import resolve_audio
        info = resolve_audio(track.url, self.store)
        info['_musik_youtube_url'] = track.url
        return info

    async def prepare_audio(self, track):
        """Share background and foreground lookups without touching playback."""
        key = self.audio_cache_key(track)
        cached = self.audio_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        task = self.audio_resolutions.get(key)
        if task is None:
            async def resolve():
                try:
                    info = await asyncio.to_thread(self.resolve_track, track)
                    if key == self.audio_cache_key(track):
                        self.cache_audio(key, info)
                    return info
                finally:
                    self.audio_resolutions.pop(key, None)
            task = asyncio.create_task(resolve())
            # Background cancellation must not leave an unobserved exception.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            self.audio_resolutions[key] = task
        return await asyncio.shield(task)

    async def play(self, track: Track, state: State, *, retry_stream=False):
        # Decoder/buffer timestamps can advance even if the rejected stream
        # never produced audio. Only resume a stream that actually started.
        resume = self.position if retry_stream and self.playback_started else 0.0
        self.playback_started = False
        self.resume_offset = resume
        if not retry_stream:
            self.online_stream = track.source in ("youtube", "video") and not self.nuclear.selected("streaming")
            self.youtube_url = None
        self.play_generation += 1
        self.position = resume
        self.duration = 0.0
        self.track_duration = None
        self.timestamp_origins.clear()
        self.demuxer_start = None
        if not retry_stream:
            self.stream_retry_used = False
        self.failure_detail = None
        self.pending_failure = None
        self.public_fallback = False
        url = track.url
        if track.source != "local":
            from .stream import resolve_audio
            key = self.audio_cache_key(track)
            cached = self.audio_cache.get(key)
            if retry_stream:
                self.stream_retry_used = True
                self.audio_cache.pop(key, None)
                info = await asyncio.to_thread(resolve_audio, self.youtube_url or url, self.store,
                                               alternate_audio=True, rejected_format=self.format_id)
                if self.youtube_url:
                    info['_musik_youtube_url'] = self.youtube_url
                self.cache_audio(key, info)
            elif cached and cached[0] > time.monotonic():
                info = cached[1]
                self.audio_cache.move_to_end(key)
            else:
                info = await self.prepare_audio(track)
            self.youtube_url = info.get('_musik_youtube_url')
            self.online_stream = bool(self.youtube_url) or self.online_stream
            duration = info.get("duration")
            if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 and not info.get("is_live"):
                self.track_duration = self.duration = duration
            self.format_id = info.get("format_id")
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
        self.pending_failure = None
        if resume > 0:
            arguments = ["loadfile", url, "replace"]
            if self.loadfile_has_index:
                arguments.append(-1)
            await self.command(*arguments, {"start": str(resume)})
        else:
            await self.command("loadfile", url, "replace")
        await self.command("set_property", "pause", False)

    async def settings(self, state: State):
        await self.command("set_property", "volume", state.volume)
        await self.command("set_property", "speed", state.speed)
        eq = FILTERS.get(state.equalizer, "")
        meter = "@musikviz:lavfi=[astats=metadata=1:reset=1]"
        await self.command("set_property", "af", f"{eq},{meter}" if eq else meter)

    async def close(self):
        resolutions = list(self.audio_resolutions.values())
        for resolution in resolutions:
            resolution.cancel()
        await asyncio.gather(*resolutions, return_exceptions=True)
        await asyncio.to_thread(self.nuclear.close)
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
