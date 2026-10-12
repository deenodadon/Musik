"""Detached per-user playback service and a small Unix-socket JSON protocol."""
import asyncio
from importlib.metadata import version

ENGINE_VERSION = version("musik-tui")
from .platform import lock_exclusive, detached_options
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import secrets
from dataclasses import asdict
from pathlib import Path

from .core import State, Store, Track
from .player import FILTERS, Player
from .service import MusicService


def runtime_directory(store):
    identity = hashlib.sha256(str(store.directory.resolve()).encode()).hexdigest()[:16]
    user_id = os.getuid() if hasattr(os, "getuid") else "windows"
    path = Path(tempfile.gettempdir()) / f"musik-{user_id}-{identity}"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise RuntimeError(f"Unsafe Musik runtime directory: {path}")
    return path


def read_startup_error(store):
    try:
        return (runtime_directory(store) / 'startup-error.txt').read_text(encoding='utf-8')
    except OSError:
        return None


def decode_state(data):
    data = dict(data)
    for key in ("queue", "favorites", "history"):
        data[key] = [Track(**item) for item in data[key]]
    return State(**data)


async def request(store, operation, **arguments):
    socket = runtime_directory(store) / "service.sock"
    if os.name == "nt" or os.environ.get('MUSIK_TRANSPORT') == 'tcp':
        endpoint = json.loads(socket.read_text())
        reader, writer = await asyncio.open_connection('127.0.0.1', endpoint['port'], limit=2**20)
    else:
        endpoint = None
        reader, writer = await asyncio.open_unix_connection(str(socket), limit=2**20)
    try:
        writer.write((json.dumps({"operation": operation, "arguments": arguments, "token": endpoint["token"] if endpoint else None}) + "\n").encode())
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 60))
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response
    finally:
        writer.close()
        await writer.wait_closed()


class BackgroundPlayer:
    background = True

    def __init__(self, store):
        self.store = store
        self.state = store.load()
        self.position = self.duration = 0.0
        self.paused = False
        self.idle = True
        self.levels = [0.0] * 32
        self.events = asyncio.Queue()
        self.message = ""
        self.supports_repeat_song = None
        self.engine_version = None
        self.failure_detail = None

    async def start(self):
        try:
            await self.poll()
            return
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        self.store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # All standard streams are detached and setsid disconnects the terminal.
        (runtime_directory(self.store) / 'startup-error.txt').unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, "-m", "musik.daemon", str(self.store.directory)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **detached_options(),
                         cwd=tempfile.gettempdir())
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            try:
                await self.poll()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() not in (None, 0):
                    raise RuntimeError(read_startup_error(self.store) or "Background player exited during startup. Run musik doctor.")
                await asyncio.sleep(.05)
        raise RuntimeError(read_startup_error(self.store) or "Background player startup timed out. Run musik doctor.")

    def update(self, response):
        self.engine_version = response.get("version")
        self.failure_detail = response.get("playback_error")
        self.supports_repeat_song = "repeat_one" in response["state"]
        self.state = decode_state(response["state"])
        for key in ("position", "duration", "paused", "idle", "levels", "message"):
            setattr(self, key, response[key])

    async def rpc(self, operation, **arguments):
        response = await request(self.store, operation, **arguments)
        self.update(response)
        return self.state

    async def poll(self):
        await self.rpc("status")

    async def command(self, *command):
        await self.rpc("command", command=command)

    async def settings(self, state):
        if state.repeat_one and self.supports_repeat_song is False:
            raise RuntimeError("Background player needs restarting for Song repeat. Run musik stop, then musik.")
        await self.rpc("settings", volume=state.volume, speed=state.speed,
                       equalizer=state.equalizer, shuffle=state.shuffle, repeat=state.repeat, repeat_one=state.repeat_one)

    async def close(self):
        # Detaching a UI must never stop the music or overwrite daemon-owned state.
        pass


class Daemon:
    def __init__(self, store):
        self.store = store
        self.state = store.load()
        self.player = Player(audio_output=os.environ.get("MUSIK_AUDIO_OUTPUT"), store=store)
        self.lock = asyncio.Lock()
        self.stopping = asyncio.Event()
        self.message = "Background player ready"
        self.tasks = set()
        self.service = MusicService(store)
        self.radio_task = None
        self.radio_generation = 0
        self.token = None
        self.waiting_for_radio = False

    def snapshot(self):
        p = self.player
        return {"version": ENGINE_VERSION, "playback_error": getattr(p, "failure_detail", None), "state": asdict(self.state), "position": p.position, "duration": p.duration,
                "paused": p.paused, "idle": p.idle, "levels": p.levels, "message": self.message}

    async def play(self, track):
        self.message = f"Loading {track.title}…"
        try:
            await self.player.play(track, self.state)
            self.state.remember(track)
            self.store.save(self.state)
            self.message = f"Playing {track.title}"
            if getattr(self.player, "public_fallback", False):
                self.message += " · public stream (account library stays signed in)"
        except Exception as exc:
            self.message = f"Playback failed: {exc}"

    def seed_radio(self, track):
        self.radio_generation += 1
        generation = self.radio_generation
        if self.radio_task:
            self.radio_task.cancel()
        self.waiting_for_radio = False
        self.state.queue = [track]
        self.state.index = 0
        self.store.save(self.state)
        self.radio_task = asyncio.create_task(self.fill_radio(track, generation))

    async def fill_radio(self, track, generation):
        try:
            tracks = await asyncio.to_thread(self.service.radio, track)
            async with self.lock:
                if generation != self.radio_generation:
                    return
                seen = {(item.source, item.id) for item in self.state.queue}
                for item in tracks:
                    identity = (item.source, item.id)
                    if identity not in seen:
                        self.state.queue.append(item)
                        seen.add(identity)
                self.store.save(self.state)
                self.message = f"Radio ready · {len(self.state.queue)} songs" if tracks else "No related songs found"
                if self.waiting_for_radio:
                    self.waiting_for_radio = False
                    if next_track := self.state.advance(finished=True):
                        await self.play(next_track)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self.radio_generation:
                self.message = f"Radio unavailable; selected song can still play: {exc}"

    async def dispatch(self, operation, args):
        if operation == "status":
            return
        if operation == "stop":
            self.stopping.set()
            return
        async with self.lock:
            if operation == "play":
                previous = self.state.current
                if "index" in args:
                    index = int(args["index"])
                    if not 0 <= index < len(self.state.queue):
                        raise ValueError("Queue entry no longer exists")
                    track = self.state.queue[index]
                else:
                    track = Track(**args["track"])
                if previous and track.url == previous.url and not self.player.idle:
                    if "index" in args:
                        self.state.index = index
                        self.store.save(self.state)
                    if self.player.paused:
                        await self.player.command("cycle", "pause")
                    return
                if "index" in args:
                    self.state.index = index
                    self.waiting_for_radio = False
                else:
                    self.seed_radio(track)
                await self.play(track)
            elif operation == "playlist":
                self.waiting_for_radio = False
                tracks = [Track(**item) for item in args["tracks"]]
                if not tracks or any(t.source not in ("youtube", "video", "local") for t in tracks):
                    raise ValueError("Playlist has no playable tracks")
                self.radio_generation += 1
                if self.radio_task:
                    self.radio_task.cancel()
                self.state.queue = tracks
                self.state.index = 0
                await self.play(tracks[0])
            elif operation == "enqueue":
                track = Track(**args["track"])
                self.state.queue.append(track)
                self.message = f"Queued {track.title}"
            elif operation == "favorite":
                track = Track(**args["track"])
                if track in self.state.favorites:
                    self.state.favorites.remove(track)
                else:
                    self.state.favorites.append(track)
            elif operation in ("next", "previous"):
                expected = args.get("generation")
                if args.get("finished") and expected is not None and expected != getattr(self.player, "play_generation", expected):
                    return
                track = self.state.advance(1 if operation == "next" else -1, finished=bool(args.get("finished", False)))
                if track:
                    self.waiting_for_radio = False
                    await self.play(track)
                else:
                    self.waiting_for_radio = bool(args.get("finished") and self.radio_task and not self.radio_task.done())
                    self.message = "End of queue"
            elif operation == "remove":
                index = int(args["index"])
                if index == self.state.index:
                    await self.player.command("stop")
                self.state.remove(index)
            elif operation == "move":
                self.state.move(int(args["index"]), int(args["delta"]))
            elif operation == "settings":
                self.state.volume = max(0, min(100, int(args["volume"])))
                self.state.speed = max(.5, min(2., float(args["speed"])))
                if args["equalizer"] not in FILTERS:
                    raise ValueError("Unknown equalizer")
                self.state.equalizer = args["equalizer"]
                self.state.shuffle = bool(args["shuffle"])
                self.state.repeat = bool(args["repeat"])
                self.state.repeat_one = bool(args.get("repeat_one", False))
                await self.player.settings(self.state)
                self.message = f"Volume {self.state.volume}% · Speed {self.state.speed:.2f}× · EQ {self.state.equalizer}"
            elif operation == "command":
                command = args["command"]
                if not command or command[0] not in ("cycle", "seek", "stop"):
                    raise ValueError("Unsupported playback command")
                await self.player.command(*command)
            else:
                raise ValueError("Unknown service operation")
            self.store.save(self.state)

    async def connection(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            message = json.loads(await asyncio.wait_for(reader.readline(), 5))
            try:
                if self.token is not None and not secrets.compare_digest(str(message.get("token", "")), self.token):
                    raise RuntimeError("Invalid Musik service credentials")
                await self.dispatch(message["operation"], message.get("arguments", {}))
                response = self.snapshot()
            except Exception as exc:
                response = {"error": str(exc)}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        except (ConnectionError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)

    async def monitor(self):
        while True:
            event = await self.player.events.get()
            if event.get("event") == "file-loaded":
                async with self.lock:
                    expected = event.get("_musik_generation")
                    if expected is None or expected == getattr(self.player, "play_generation", expected):
                        if self.state.current:
                            self.player.failure_detail = None
                            self.message = f"Playing {self.state.current.title}"
            elif event.get("reason") == "eof":
                await self.dispatch("next", {"finished": True, "generation": event.get("_musik_generation")})
            elif event.get("reason") == "error":
                async with self.lock:
                    expected = event.get("_musik_generation")
                    if expected is not None and expected != getattr(self.player, "play_generation", expected):
                        continue
                    detail = event.get("_musik_error") or "mpv could not open this audio stream. Run musik doctor for diagnostics."
                    track = self.state.current
                    if ("HTTP 403" in detail and track and track.source != "local"
                            and not getattr(self.player, "stream_retry_used", False)):
                        self.player.stream_retry_used = True
                        self.player.failure_detail = None
                        self.message = f"Loading {track.title}…"
                        try:
                            await self.player.play(track, self.state, retry_stream=True)
                            self.message = f"Playing {track.title}"
                        except Exception:
                            self.player.failure_detail = "YouTube rejected both audio streams. Run musik doctor."
                            self.message = self.player.failure_detail
                    else:
                        self.message = detail

    async def run(self):
        directory = runtime_directory(self.store)
        with (directory / "service.lock").open("a+") as lock_file:
            try:
                lock_exclusive(lock_file)
            except BlockingIOError:
                return
            socket = directory / "service.sock"
            socket.unlink(missing_ok=True)
            loop = asyncio.get_running_loop()
            if os.name != "nt":
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self.stopping.set)
            server = monitor = None
            try:
                try:
                    await self.player.start()
                    await self.player.settings(self.state)
                except Exception as error:
                    (directory / 'startup-error.txt').write_text(f"Player startup failed: {type(error).__name__}: {str(error)[:300]}", encoding='utf-8')
                    raise
                (directory / 'startup-error.txt').unlink(missing_ok=True)
                if os.name == "nt" or os.environ.get('MUSIK_TRANSPORT') == 'tcp':
                    self.token = secrets.token_hex(32)
                    server = await asyncio.start_server(self.connection, '127.0.0.1', 0, limit=2**20)
                    temporary = directory / 'endpoint.tmp'
                    temporary.write_text(json.dumps({'port': server.sockets[0].getsockname()[1], 'token': self.token}))
                    temporary.chmod(0o600)
                    temporary.replace(socket)
                else:
                    server = await asyncio.start_unix_server(self.connection, str(socket), limit=2**20)
                socket.chmod(0o600)
                monitor = asyncio.create_task(self.monitor())
                await self.stopping.wait()
            finally:
                if server:
                    server.close()
                    await server.wait_closed()
                if self.radio_task:
                    self.radio_task.cancel()
                    await asyncio.gather(self.radio_task, return_exceptions=True)
                if monitor:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                for task in list(self.tasks):
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
                await self.player.close()
                self.store.save(self.state)
                socket.unlink(missing_ok=True)


async def stop_service(store):
    try:
        await request(store, "stop")
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    for _ in range(100):
        if not (runtime_directory(store) / "service.sock").exists():
            return True
        await asyncio.sleep(.05)
    raise RuntimeError("Background service did not finish shutting down")


if __name__ == "__main__":
    asyncio.run(Daemon(Store(Path(sys.argv[1]))).run())
