from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from .platform import state_home


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str = "Unknown artist"
    source: str = "youtube"

    @property
    def url(self) -> str:
        if self.source == "local":
            return self.id
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class State:
    queue: list[Track] = field(default_factory=list)
    favorites: list[Track] = field(default_factory=list)
    history: list[Track] = field(default_factory=list)
    index: int = -1
    volume: int = 70
    speed: float = 1.0
    repeat: bool = False
    repeat_one: bool = False
    shuffle: bool = False
    equalizer: str = "flat"

    @property
    def current(self) -> Track | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    @property
    def repeat_mode(self):
        return "Song" if self.repeat and self.repeat_one else "Playlist" if self.repeat else "Off"

    def cycle_repeat(self):
        mode = self.repeat_mode
        self.repeat = mode != "Playlist"
        self.repeat_one = mode == "Off"

    def advance(self, direction: int = 1, *, finished: bool = False) -> Track | None:
        if not self.queue:
            return None
        if finished and self.repeat and self.repeat_one and self.current:
            return self.current
        if finished and self.shuffle and direction > 0 and len(self.queue) > 1:
            self.index = random.choice([i for i in range(len(self.queue)) if i != self.index])
        else:
            target = self.index + direction
            if self.repeat and not self.repeat_one:
                target %= len(self.queue)
            if not 0 <= target < len(self.queue):
                return None
            self.index = target
        return self.current

    def remove(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            return
        self.queue.pop(index)
        if index == self.index:
            self.index = -1
        elif index < self.index:
            self.index -= 1

    def move(self, index: int, delta: int) -> int:
        target = index + delta
        if 0 <= index < len(self.queue) and 0 <= target < len(self.queue):
            self.queue[index], self.queue[target] = self.queue[target], self.queue[index]
            if self.index == index:
                self.index = target
            elif self.index == target:
                self.index = index
            return target
        return index

    def remember(self, track: Track) -> None:
        self.history = [track] + [t for t in self.history if t != track][:199]


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(os.environ.get("XDG_STATE_HOME", str(state_home()))) / "musik"
        self.path = self.directory / "state.json"

    def load(self) -> State:
        if not self.path.exists():
            return State()
        try:
            data = json.loads(self.path.read_text())
            for key in ("queue", "favorites", "history"):
                data[key] = [Track(**t) for t in data.get(key, [])]
            state = State(**data)
            state.volume = max(0, min(100, int(state.volume)))
            state.speed = max(0.5, min(2.0, float(state.speed)))
            if not -1 <= state.index < len(state.queue):
                state.index = -1
            return state
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid saved state at {self.path}; back it up before resetting it") from exc

    def save(self, state: State) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(asdict(state), handle, indent=2)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def scan_local(directory: Path) -> list[Track]:
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    extensions = {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac"}
    return [Track(str(p.resolve()), p.stem, "Local file", "local")
            for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix.lower() in extensions]
