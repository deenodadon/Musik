"""Detached per-user playback service and a small Unix-socket JSON protocol."""
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from .core import State, Store, Track
from .player import FILTERS, Player
from .service import MusicService


def runtime_directory(store):
    identity = hashlib.sha256(str(store.directory.resolve()).encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"musik-{os.getuid()}-{identity}"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Unsafe Musik runtime directory: {path}")
    return path


def decode_state(data):
    data = dict(data)
    for key in ("queue", "favorites", "history"):
        data[key] = [Track(**item) for item in data[key]]
    return State(**data)


async def request(store, operation, **arguments):
    socket = runtime_directory(store) / "service.sock"
    reader, writer = await asyncio.open_unix_connection(str(socket), limit=2**20)
    try:
        writer.write((json.dumps({"operation": operation, "arguments": arguments}) + "\n").encode())
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

    async def start(self):
        try:
            await self.poll()
            return
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        self.store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # All standard streams are detached and setsid disconnects the terminal.
        subprocess.Popen([sys.executable, "-m", "musik.daemon", str(self.store.directory)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                         cwd=self.store.directory)
        for _ in range(100):
            try:
                await self.poll()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(.05)
        raise RuntimeError("Background service could not start. Check mpv is installed.")

    def update(self, response):
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
        await self.rpc("settings", volume=state.volume, speed=state.speed,
                       equalizer=state.equalizer, shuffle=state.shuffle, repeat=state.repeat)

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

    def snapshot(self):
        p = self.player
        return {"state": asdict(self.state), "position": p.position, "duration": p.duration,
                "paused": p.paused, "idle": p.idle, "levels": p.levels, "message": self.message}

    async def play(self, track):
        self.message = f"Loading {track.title}…"
        try:
            await self.player.play(track, self.state)
            self.state.remember(track)
            self.store.save(self.state)
            self.message = f"Playing {track.title}"
        except Exception as exc:
            self.message = f"Playback failed: {exc}"

    def seed_radio(self, track):
        self.radio_generation += 1
        generation = self.radio_generation
        if self.radio_task:
            self.radio_task.cancel()
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
                if self.player.idle and not self.player.paused and self.state.index == 0 and len(self.state.queue) > 1:
                    await self.play(self.state.advance())
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
                if "index" in args:
                    index = int(args["index"])
                    if not 0 <= index < len(self.state.queue):
                        raise ValueError("Queue entry no longer exists")
                    track = self.state.queue[index]
                else:
                    track = Track(**args["track"])
                self.seed_radio(track)
                await self.play(track)
            elif operation == "playlist":
                tracks = [Track(**item) for item in args["tracks"]]
                if not tracks or any(t.source not in ("youtube", "local") for t in tracks):
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
                track = self.state.advance(1 if operation == "next" else -1)
                if track:
                    await self.play(track)
                else:
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
            if event.get("reason") == "eof":
                await self.dispatch("next", {})
            elif event.get("reason") == "error":
                self.message = "mpv could not play this source; try another track"

    async def run(self):
        directory = runtime_directory(self.store)
        with (directory / "service.lock").open("w") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            socket = directory / "service.sock"
            socket.unlink(missing_ok=True)
            loop = asyncio.get_running_loop()
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.stopping.set)
            server = monitor = None
            try:
                await self.player.start()
                await self.player.settings(self.state)
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
