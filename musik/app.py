import argparse
import asyncio
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from .core import Store, scan_local
from .player import FILTERS
from .daemon import BackgroundPlayer, stop_service
from .visualizer import Visualizer
from dataclasses import asdict
from .service import MusicService


class Musik(App):
    TITLE = "Musik"
    SUB_TITLE = "Music lives in your terminal"
    CSS = """
    Screen { background: #10151f; }
    Header { background: #1b2638; }
    #search { margin: 1 2; border: round #66d9bb; }
    #body { height: 1fr; margin: 0 2; }
    #browse { width: 2fr; }
    #side { width: 1fr; margin-left: 2; }
    #tracks { height: 1fr; border: round #435674; }
    #lyrics { height: 1fr; border: round #435674; padding: 1; overflow-y: auto; }
    #now { height: 5; margin: 1 2; padding: 0 1; border: round #66d9bb; }
    #status { height: 2; margin: 0 2; color: #66d9bb; }
    .label { height: 2; color: #66d9bb; text-style: bold; }
    """
    BINDINGS = [
        ("slash", "search", "Search"), ("space", "pause", "Pause"),
        ("n", "next", "Next"), ("p", "previous", "Previous"),
        ("a", "enqueue", "Queue +"), ("f", "favorite", "Favorite"),
        ("1", "view('results')", "Results"), ("2", "view('queue')", "Queue"),
        ("3", "view('favorites')", "Favorites"), ("4", "view('history')", "History"),
        ("delete", "remove", "Remove"), ("shift+up", "move(-1)", "Move up"),
        ("shift+down", "move(1)", "Move down"), ("s", "shuffle", "Shuffle"),
        ("r", "repeat", "Repeat"), ("l", "lyrics", "Lyrics"),
        ("plus", "volume(5)", "Volume +"), ("minus", "volume(-5)", "Volume -"),
        ("right", "seek(10)", "+10s"), ("left", "seek(-10)", "-10s"),
        ("e", "equalizer", "EQ"), ("v", "speed", "Speed"),
        ("q", "quit", "Detach"), ("escape", "browse", "Browse"),
    ]

    def __init__(self, store=None, service=None, player=None, local=None):
        super().__init__()
        self.store = store or Store()
        self.state = self.store.load()
        self.service = service or MusicService()
        self.player = player or BackgroundPlayer(self.store)
        self.background = getattr(self.player, "background", False)
        self.results = scan_local(Path(local).expanduser()) if local else []
        self.view = "results"
        self.ready = False
        self.play_lock = asyncio.Lock()
        self.generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search YouTube Music, or /local /path/to/music", id="search")
        with Horizontal(id="body"):
            with Vertical(id="browse"):
                yield Static("RESULTS", id="heading", classes="label")
                yield DataTable(id="tracks", cursor_type="row")
            with Vertical(id="side"):
                yield Static("LYRICS", classes="label")
                yield Static("Select a song and press L.\n\n1 Results · 2 Queue\n3 Favorites · 4 History\nA Add to queue · Enter Play\nS Shuffle · R Repeat\nE Equalizer · V Speed", id="lyrics", markup=False)
        yield Visualizer(id="visualizer", markup=False)
        yield Static("Nothing playing", id="now", markup=False)
        yield Static("Ready to browse", id="status", markup=False)
        yield Footer()

    async def on_mount(self):
        self.query_one(DataTable).add_columns("Title", "Artist", "Source")
        self.refresh_tracks()
        self.set_interval(0.25, self.tick)
        try:
            await self.player.start()
            if self.background:
                self.state = self.player.state
                self.refresh_tracks()
            else:
                await self.player.settings(self.state)
            self.ready = True
            self.status("Player ready. Q detaches; musik stop stops background playback.")
        except Exception as exc:
            self.status(str(exc))

    def status(self, text):
        for widget in self.query("#status"):
            widget.update(text)

    @property
    def tracks(self):
        return self.results if self.view == "results" else getattr(self.state, self.view)

    def selected(self):
        row = self.query_one(DataTable).cursor_row
        return self.tracks[row] if 0 <= row < len(self.tracks) else None

    def refresh_tracks(self):
        tables = list(self.query(DataTable))
        if not tables:
            return
        table = tables[0]
        table.clear()
        for track in self.tracks:
            table.add_row(track.title, track.artist, track.source)
        self.query_one("#heading", Static).update(f"{self.view.upper()} · {len(self.tracks)} songs")

    def persist(self):
        if self.background:
            return
        try:
            self.store.save(self.state)
        except OSError as exc:
            self.status(f"Could not save: {exc}")

    @on(Input.Submitted)
    async def submitted(self, event):
        query = event.value.strip()
        if not query:
            return
        self.generation += 1
        generation = self.generation
        self.status("Searching…")
        async def search():
            try:
                if query.startswith("/local "):
                    results = await asyncio.to_thread(scan_local, Path(query[7:]).expanduser())
                else:
                    results = await asyncio.to_thread(self.service.search, query)
                if generation != self.generation:
                    return
                self.results = results
                self.action_view("results")
                self.status(f"Found {len(results)} tracks")
            except Exception as exc:
                if generation == self.generation:
                    self.status(f"Search failed: {exc}")
        self.run_worker(search(), group="search")
        self.query_one(DataTable).focus()

    @on(DataTable.RowSelected)
    async def selected_row(self, event):
        track = self.selected()
        if track is None:
            return
        if self.background:
            args = {"index": event.cursor_row} if self.view == "queue" else {"track": asdict(track)}
            self.run_worker(self.remote("play", **args), group="play")
            return
        if self.view == "queue":
            index = event.cursor_row
        else:
            self.state.queue.append(track)
            index = len(self.state.queue) - 1
        self.state.index = index
        self.persist()
        self.schedule_play(track)

    def schedule_play(self, track):
        self.run_worker(self.play(track), group="play", exclusive=True)

    async def play(self, track):
        async with self.play_lock:
            if not self.ready:
                self.status("Playback unavailable: install mpv, then restart Musik")
                return
            self.status(f"Loading {track.title}…")
            try:
                await self.player.play(track, self.state)
                self.state.remember(track)
                self.persist()
                self.status(f"Playing {track.title}")
                self.refresh_tracks()
            except Exception as exc:
                self.status(f"Playback failed: {exc}")

    async def remote(self, operation, **arguments):
        try:
            self.state = await self.player.rpc(operation, **arguments)
            self.refresh_tracks()
            self.status(self.player.message)
        except Exception as exc:
            self.status(str(exc))

    async def tick(self):
        if not list(self.query("#status")):
            return
        if self.background and self.ready:
            try:
                previous = self.tracks.copy()
                await self.player.poll()
                if not list(self.query("#status")):
                    return
                self.state = self.player.state
                if self.tracks != previous:
                    self.refresh_tracks()
                self.status(self.player.message)
            except (OSError, RuntimeError) as exc:
                self.status(f"Background service stopped: {exc}")
                self.ready = False
                self.exit()
                return
        track = self.state.current
        p = self.player
        if track:
            def time(seconds):
                return f"{int(seconds)//60}:{int(seconds)%60:02}"
            self.query_one("#now", Static).update(
                f"{'Stopped' if p.idle else 'Paused' if p.paused else 'Playing'}  {track.title} — {track.artist}\n"
                f"{time(p.position)} / {time(p.duration)}  · Vol {self.state.volume}%  · {self.state.speed:.2f}×\n"
                f"EQ {self.state.equalizer}  · Shuffle {'on' if self.state.shuffle else 'off'}  · Repeat {'on' if self.state.repeat else 'off'}")
        levels = getattr(p, "levels", [0.0] * 32)
        self.query_one(Visualizer).show_levels([0.0] * 32 if p.paused or p.idle else levels)
        if not track:
            self.query_one("#now", Static).update("Nothing playing")
        while not p.events.empty():
            event = p.events.get_nowait()
            if event.get("reason") == "eof":
                await self.action_next()
            elif event.get("reason") == "error":
                self.status("mpv could not play this source; try another track")

    def action_search(self):
        self.query_one(Input).focus()

    def action_browse(self):
        self.query_one(DataTable).focus()

    def action_view(self, view):
        self.view = view
        self.refresh_tracks()
        self.action_browse()

    async def action_enqueue(self):
        if track := self.selected():
            if self.background:
                await self.remote("enqueue", track=asdict(track))
                return
            self.state.queue.append(track)
            self.persist()
            self.refresh_tracks()
            self.status(f"Queued {track.title}")

    async def action_favorite(self):
        if track := self.selected():
            if self.background:
                await self.remote("favorite", track=asdict(track))
                return
            if track in self.state.favorites:
                self.state.favorites.remove(track)
            else:
                self.state.favorites.append(track)
            self.persist()
            self.refresh_tracks()

    async def safe_command(self, *command):
        try:
            await self.player.command(*command)
        except Exception as exc:
            self.status(str(exc))

    async def action_pause(self):
        await self.safe_command("cycle", "pause")

    async def action_next(self):
        if self.background:
            await self.remote("next")
            return
        if track := self.state.advance():
            self.persist()
            self.schedule_play(track)
        else:
            self.status("End of queue")

    async def action_previous(self):
        if self.background:
            await self.remote("previous")
            return
        if track := self.state.advance(-1):
            self.persist()
            self.schedule_play(track)

    async def action_seek(self, seconds):
        await self.safe_command("seek", seconds, "relative")

    async def apply_settings(self):
        self.persist()
        if self.ready:
            try:
                await self.player.settings(self.state)
                if self.background:
                    self.state = self.player.state
            except Exception as exc:
                self.status(str(exc))

    async def action_volume(self, delta):
        self.state.volume = max(0, min(100, self.state.volume + delta))
        await self.apply_settings()

    async def action_speed(self):
        speeds = [0.75, 1.0, 1.25, 1.5, 2.0]
        self.state.speed = next((v for v in speeds if v > self.state.speed), speeds[0])
        await self.apply_settings()

    async def action_equalizer(self):
        presets = list(FILTERS)
        self.state.equalizer = presets[(presets.index(self.state.equalizer) + 1) % len(presets)] if self.state.equalizer in presets else "flat"
        await self.apply_settings()

    async def action_shuffle(self):
        self.state.shuffle = not self.state.shuffle
        await self.apply_settings()

    async def action_repeat(self):
        self.state.repeat = not self.state.repeat
        await self.apply_settings()

    async def action_remove(self):
        if self.view != "queue":
            return
        row = self.query_one(DataTable).cursor_row
        if self.background:
            await self.remote("remove", index=row)
            return
        if row == self.state.index:
            await self.safe_command("stop")
        self.state.remove(row)
        self.persist()
        self.refresh_tracks()

    async def action_move(self, delta):
        if self.view != "queue":
            return
        if self.background:
            row = self.query_one(DataTable).cursor_row
            await self.remote("move", index=row, delta=delta)
            self.query_one(DataTable).move_cursor(row=max(0, min(len(self.state.queue)-1, row+delta)))
            return
        row = self.state.move(self.query_one(DataTable).cursor_row, delta)
        self.persist()
        self.refresh_tracks()
        self.query_one(DataTable).move_cursor(row=row)

    def action_lyrics(self):
        track = self.state.current or self.selected()
        if not track:
            return
        async def load():
            self.query_one("#lyrics", Static).update("Loading lyrics…")
            try:
                lyrics = await asyncio.to_thread(self.service.lyrics, track)
                self.query_one("#lyrics", Static).update(f"{track.title}\n\n{lyrics}")
            except Exception as exc:
                self.query_one("#lyrics", Static).update(f"Lyrics unavailable: {exc}")
        self.run_worker(load(), group="lyrics", exclusive=True)

    async def on_unmount(self):
        self.persist()
        await self.player.close()


def main():
    parser = argparse.ArgumentParser(description="Musik terminal music player")
    parser.add_argument("command", nargs="?", choices=["start", "stop"], help="Start the terminal interface (default), or stop background playback")
    parser.add_argument("--local", help="Browse a directory of local audio files")
    args = parser.parse_args()
    try:
        if args.command == "stop":
            stopped = asyncio.run(stop_service(Store()))
            print("Musik stopped." if stopped else "Musik is not running.")
            return
        Musik(local=args.local).run()
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Musik: {exc}\n")


if __name__ == "__main__":
    main()
