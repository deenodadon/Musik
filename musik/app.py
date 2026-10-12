import argparse
from functools import partial
import asyncio
from pathlib import Path

from textual.screen import ModalScreen
from textual import on
from textual.binding import Binding
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static, Tabs, Tab, Button

from .core import Store, scan_local
from .player import FILTERS
from .daemon import BackgroundPlayer, stop_service
from .visualizer import Visualizer
from .updater import start_check, read_status
from dataclasses import asdict
from .service import MusicService, cached_playlists, explain_library_error


class BrowserTable(DataTable):
    """Activate a row on one click, as a music browser does."""
    async def _on_click(self, event):
        event.prevent_default()
        previous = self.cursor_coordinate
        await super()._on_click(event)
        row = event.style.meta.get("row")
        if isinstance(row, int) and 0 <= row < self.row_count and self.cursor_coordinate != previous:
            self._post_selected_message()


class RepeatMenu(ModalScreen[str]):
    DEFAULT_CSS = """
    RepeatMenu { align: center middle; background: #00000088; }
    #repeat-menu { width: 44; height: auto; padding: 1 2; border: round #66d9bb; background: #10151f; }
    #repeat-menu Button { width: 1fr; margin-top: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        with Vertical(id="repeat-menu"):
            yield Static("Choose repeat mode", markup=False)
            yield Button("Off", id="repeat-off")
            yield Button("Song — repeat the current track", id="repeat-song")
            yield Button("Queue — repeat the whole queue", id="repeat-queue")

    @on(Button.Pressed)
    def choose(self, event):
        self.dismiss(event.button.id.removeprefix("repeat-"))

    def action_cancel(self):
        self.dismiss(None)


class Musik(App):
    AUTO_FOCUS = "#search"
    TITLE = "Musik"
    SUB_TITLE = "Music lives in your terminal"
    CSS = """
    Screen { background: #10151f; }
    Header { background: #1b2638; }
    #search { margin: 1 2; border: round #66d9bb; }
    #body { height: 1fr; margin: 0 2; }
    #browse { width: 2fr; }
    #side { width: 1fr; margin-left: 2; }
    #repeat-toggle { height: 3; width: auto; margin: 0 2; }
    #queue-help { height: auto; color: #8899aa; }
    #tracks { height: 1fr; border: round #435674; }
    #lyrics { height: 1fr; border: round #435674; padding: 1; overflow-y: auto; }
    #now { height: 5; margin: 1 2; padding: 0 1; border: round #66d9bb; }
    #update { height: 1; margin: 0 2; color: #8899aa; }
    #status { height: 2; margin: 0 2; color: #66d9bb; }
    .label { height: 2; color: #66d9bb; text-style: bold; }
    """
    BINDINGS = [
        ("slash", "search", "Search"), ("space", "pause", "Pause"),
        Binding("ctrl+p", "pause", "Pause", priority=True),
        ("n", "next", "Next"), ("p", "previous", "Previous"),
        ("a", "enqueue", "Queue +"), ("f", "favorite", "Favorite"),
        ("f6", "section('songs')", "Songs"), ("f7", "section('albums')", "Albums"),
        ("f8", "section('artists')", "Artists"), ("f9", "section('videos')", "Videos"),
        ("1", "view('results')", "Results"), ("2", "view('queue')", "Queue"),
        ("5", "playlists", "Playlists"),
        ("ctrl+enter", "play_playlist", "Play collection"),
        ("backspace", "back", "Back"),
        Binding("o", "play_playlist", "Play playlist", show=False),
        ("3", "view('favorites')", "Favorites"), ("4", "view('history')", "History"),
        ("delete", "remove", "Remove"), ("shift+up", "move(-1)", "Move up"),
        ("shift+down", "move(1)", "Move down"), ("s", "shuffle", "Shuffle"),
        ("r", "repeat", "Repeat"), ("l", "lyrics", "Lyrics"),
        Binding("ctrl+up", "volume(5)", "Volume +", priority=True),
        Binding("ctrl+down", "volume(-5)", "Volume -", priority=True),
        Binding("plus,equals_sign", "volume(5)", "Volume +", show=False),
        Binding("minus", "volume(-5)", "Volume -", show=False),
        ("right", "seek(10)", "+10s"), ("left", "seek(-10)", "-10s"),
        ("e", "equalizer", "EQ"), ("v", "speed", "Speed"),
        ("q", "quit", "Detach"), ("escape", "browse", "Browse"),
    ]

    def __init__(self, store=None, service=None, player=None, local=None):
        super().__init__()
        self.store = store or Store()
        self.state = self.store.load()
        self.service = service or MusicService(self.store)
        self.playlists = cached_playlists(self.store)
        self.playlist_contents = []
        self.player = player or BackgroundPlayer(self.store)
        self.background = getattr(self.player, "background", False)
        self.results = scan_local(Path(local).expanduser()) if local else []
        self.view = "results"
        self.ready = False
        self.play_lock = asyncio.Lock()
        self.generation = 0
        self.catalog_title = "RESULTS"
        self.browse_history = []
        self.results_section = "songs"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(Tab("Results", id="menu-results"), Tab("Queue", id="menu-queue"),
                   Tab("Favorites", id="menu-favorites"), Tab("History", id="menu-history"),
                   Tab("Playlists", id="menu-playlists"), id="main-menu")
        yield Input(placeholder="Search music / YouTube videos, paste a YouTube link, or /local PATH", id="search")
        with Horizontal(id="body"):
            with Vertical(id="browse"):
                yield Static("RESULTS", id="heading", classes="label", markup=False)
                yield Tabs(Tab("Songs", id="section-songs"), Tab("Albums", id="section-albums"),
                           Tab("Artists", id="section-artists"), Tab("Videos", id="section-videos"), id="result-sections")
                yield Static("", id="queue-help", markup=False)
                yield BrowserTable(id="tracks", cursor_type="row")
            with Vertical(id="side"):
                yield Static("LYRICS", classes="label")
                yield Static("Select a song and press L.\n\n1 Results · 2 Queue\n3 Favorites · 4 History\nA Add to queue · Enter Play\nS Shuffle · R Repeat\nE Equalizer · V Speed", id="lyrics", markup=False)
        yield Button("Repeat: Off", id="repeat-toggle")
        yield Visualizer(id="visualizer", markup=False)
        yield Static("Nothing playing", id="now", markup=False)
        yield Static("Ready to browse", id="status", markup=False)
        yield Static("", id="update", markup=False)
        yield Footer()

    async def on_mount(self):
        table = self.query_one(DataTable)
        table.add_column("Type", width=8)
        table.add_columns("Title", "Artist")
        table.fixed_columns = 1
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
        if self.view == "playlists":
            return self.playlists
        if self.view == "results":
            return [item for item in self.results if self.category(item) == self.results_section]
        return getattr(self.state, self.view)

    @staticmethod
    def category(item):
        return {"artist": "artists", "album": "albums", "video": "videos"}.get(item.source, "songs")

    def choose_section(self):
        self.results_section = next((kind for kind in ("songs", "albums", "artists", "videos")
                                     if any(self.category(item) == kind for item in self.results)), "songs")

    @on(Tabs.TabActivated, "#main-menu")
    def menu_activated(self, event):
        if event.tab.id != self.query_one("#main-menu", Tabs).active:
            return
        view = event.tab.id.removeprefix("menu-")
        if view == self.view:
            return
        if view == "playlists":
            self.action_playlists()
        else:
            self.action_view(view)

    @on(Tabs.TabActivated, "#result-sections")
    def section_activated(self, event):
        if event.tab.id != self.query_one("#result-sections", Tabs).active:
            return
        self.results_section = event.tab.id.removeprefix("section-")
        if self.view == "results":
            self.refresh_tracks()

    def action_section(self, section):
        self.results_section = section
        self.action_view("results")

    def selected(self):
        row = self.query_one(DataTable).cursor_row
        return self.tracks[row] if 0 <= row < len(self.tracks) else None

    def refresh_tracks(self):
        tables = list(self.query(DataTable))
        if not tables:
            return
        table = tables[0]
        menu = self.query_one("#main-menu", Tabs)
        menu.get_tab("menu-queue").label = f"Queue ({len(self.state.queue)})"
        if menu.active != f"menu-{self.view}":
            menu.active = f"menu-{self.view}"
        help_text = self.query_one("#queue-help", Static)
        help_text.display = self.view == "queue"
        help_text.update("Enter Play · Delete Remove · Shift+↑/↓ Reorder · Selected = current track" if self.state.queue
                         else "Queue is empty. Search for music and press A to add a track, or Enter to play.")
        tabs = self.query_one("#result-sections", Tabs)
        tabs.display = self.view == "results"
        for section in ("songs", "albums", "artists", "videos"):
            count = sum(self.category(item) == section for item in self.results)
            tabs.get_tab(f"section-{section}").label = f"{section.title()} ({count})"
        desired = f"section-{self.results_section}"
        if tabs.active != desired:
            tabs.active = desired
        table.clear()
        for index, track in enumerate(self.tracks):
            kind = {"youtube": "Song", "video": "Video", "local": "Local", "artist": "Artist", "album": "Album", "playlist": "Playlist"}.get(track.source, track.source)
            if self.view == "queue" and index == self.state.index:
                kind = "Selected"
            title = f"{index + 1}. {track.title}" if self.view == "queue" else track.title
            table.add_row(kind, title, track.artist)
        heading = self.catalog_title if self.view == "results" else self.view.upper()
        self.query_one("#heading", Static).update(f"{heading} · {len(self.tracks)} items")

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
            errors = []
            try:
                if query.startswith("/local "):
                    results = await asyncio.to_thread(scan_local, Path(query[7:]).expanduser())
                elif query.startswith(("https://", "http://")):
                    results = await asyncio.to_thread(self.service.search_videos, query)
                else:
                    searches = [asyncio.to_thread(self.service.search, query)]
                    if hasattr(self.service, "search_videos"):
                        searches.append(asyncio.to_thread(self.service.search_videos, query))
                    outcomes = await asyncio.gather(*searches, return_exceptions=True)
                    results = [row for outcome in outcomes if isinstance(outcome, list) for row in outcome]
                    errors = [str(outcome) for outcome in outcomes if isinstance(outcome, Exception)]
                    if errors and not results:
                        raise RuntimeError("; ".join(errors))
                if generation != self.generation:
                    return
                self.playlist_contents = []
                self.catalog_title = "RESULTS"
                self.browse_history = []
                self.results = results
                self.choose_section()
                self.action_view("results")
                self.status(f"Found {len(results)} items" + (" · Some searches failed: " + "; ".join(errors) if errors else ""))
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
        if track.source in ("artist", "album", "playlist"):
            self.run_worker(partial(self.open_catalog, track), group="catalog", exclusive=True)
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

    def remember_page(self):
        self.browse_history.append((self.view, self.results.copy(), self.catalog_title,
                                    self.playlist_contents.copy(), self.results_section, self.query_one(DataTable).cursor_row))

    async def open_catalog(self, track):
        self.generation += 1
        generation = self.generation
        self.status(f"Loading {track.title}…")
        try:
            method = getattr(self.service, {"artist": "artist_catalog", "album": "album_tracks",
                                           "playlist": "playlist_tracks"}[track.source])
            rows = await asyncio.to_thread(method, track)
            if generation != self.generation or not list(self.query("#heading")):
                return False
            self.remember_page()
            self.results = rows
            self.choose_section()
            self.catalog_title = track.title
            self.playlist_contents = rows.copy() if track.source != "artist" else []
            self.action_view("results")
            self.status("Enter opens or plays · O plays this collection · Backspace goes back" if rows else "No available releases or tracks found")
            return True
        except Exception as exc:
            if generation == self.generation:
                self.status(f"Could not open {track.source}: {explain_library_error(exc)}")
            return False

    async def open_playlist(self, track):
        return await self.open_catalog(track)

    def action_back(self):
        if not self.browse_history:
            return
        self.generation += 1
        view, self.results, self.catalog_title, self.playlist_contents, self.results_section, row = self.browse_history.pop()
        self.action_view(view)
        self.query_one(DataTable).move_cursor(row=row)

    def action_playlists(self):
        self.action_view("playlists")
        async def refresh():
            try:
                self.playlists = await asyncio.to_thread(self.service.playlists)
                if self.view == "playlists":
                    self.refresh_tracks()
                self.status(f"Imported {len(self.playlists)} playlists")
            except Exception as exc:
                self.status(explain_library_error(exc))
        self.run_worker(refresh(), group="library", exclusive=True)

    async def action_play_playlist(self):
        if self.view == "playlists" and (track := self.selected()):
            if not await self.open_playlist(track):
                return
        if not self.playlist_contents:
            self.status("Open an album or playlist with Enter first")
            return
        if self.background:
            await self.remote("playlist", tracks=[asdict(t) for t in self.playlist_contents])
            self.action_view("queue")
        else:
            self.state.queue = self.playlist_contents.copy()
            self.state.index = 0
            self.persist()
            self.schedule_play(self.state.current)
            self.action_view("queue")

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
            if operation == "play":
                self.view = "queue"
            self.refresh_tracks()
            self.status(self.player.message)
        except Exception as exc:
            self.status(str(exc))

    async def tick(self):
        for widget in self.query("#update"):
            widget.update(read_status(self.store).get("message", ""))
        if not list(self.query("#status")):
            return
        if self.background and self.ready:
            try:
                previous = self.tracks.copy()
                previous_index = self.state.index
                await self.player.poll()
                if not list(self.query("#status")):
                    return
                self.state = self.player.state
                if self.tracks != previous or self.state.index != previous_index:
                    self.refresh_tracks()
                if self.view == "queue":
                    self.status(self.player.message)
            except (OSError, RuntimeError) as exc:
                self.status(f"Background service stopped: {exc}")
                self.ready = False
                self.exit()
                return
        self.query_one("#repeat-toggle", Button).label = f"Repeat: {self.state.repeat_mode}"
        track = self.state.current
        p = self.player
        if track:
            def time(seconds):
                return f"{int(seconds)//60}:{int(seconds)%60:02}"
            self.query_one("#now", Static).update(
                f"{'Stopped' if p.idle else 'Paused' if p.paused else 'Playing'}  {track.title} — {track.artist}\n"
                f"{time(p.position)} / {time(p.duration)}  · Vol {self.state.volume}%  · {self.state.speed:.2f}×\n"
                f"EQ {self.state.equalizer}  · Shuffle {'on' if self.state.shuffle else 'off'}  · Repeat {self.state.repeat_mode}")
        levels = getattr(p, "levels", [0.0] * 32)
        self.query_one(Visualizer).show_levels([0.0] * 32 if p.paused or p.idle else levels)
        if not track:
            self.query_one("#now", Static).update(f"Nothing playing · Vol {self.state.volume}%")
        while not p.events.empty():
            event = p.events.get_nowait()
            if event.get("reason") == "eof":
                if track := self.state.advance(finished=True):
                    self.persist()
                    self.schedule_play(track)
                else:
                    self.status("End of queue")
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
            if track.source in ("artist", "album", "playlist"):
                self.status("Open this artist, album or playlist with Enter first")
                return
            if self.background:
                await self.remote("enqueue", track=asdict(track))
                return
            self.state.queue.append(track)
            self.persist()
            self.refresh_tracks()
            self.status(f"Queued {track.title}")

    async def action_favorite(self):
        if track := self.selected():
            if track.source in ("artist", "album", "playlist"):
                self.status("Open this artist, album or playlist with Enter first")
                return
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
                return False
        return True

    async def action_volume(self, delta):
        self.state.volume = max(0, min(100, self.state.volume + delta))
        await self.apply_settings()
        self.status(f"Volume {self.state.volume}%")

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

    @on(Button.Pressed, "#repeat-toggle")
    def repeat_clicked(self):
        self.push_screen(RepeatMenu(), self.set_repeat_mode)

    async def set_repeat_mode(self, mode):
        if mode is None:
            return
        previous = self.state.repeat, self.state.repeat_one
        self.state.repeat = mode != "off"
        self.state.repeat_one = mode == "song"
        if not await self.apply_settings():
            self.state.repeat, self.state.repeat_one = previous
            self.notify("Restart background playback: run musik stop, then musik, and select Song again.", severity="warning", timeout=12)
            return
        self.query_one("#repeat-toggle", Button).label = f"Repeat: {self.state.repeat_mode}"
        self.status(f"Repeat: {self.state.repeat_mode}")

    async def action_repeat(self):
        mode = {"Off": "song", "Song": "queue", "Queue": "off"}[self.state.repeat_mode]
        await self.set_repeat_mode(mode)

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
    parser.add_argument("command", nargs="?", choices=["start", "stop", "login", "logout", "reset"], help="Open the TUI (default), stop playback, import a browser sign-in, or sign out")
    parser.add_argument("--local", help="Browse a directory of local audio files")
    parser.add_argument("--browser", choices=["firefox", "chrome", "chromium", "brave", "edge"], default="brave", help="Browser engine for the dedicated login window (default: brave)")
    parser.add_argument("--profile", help="Optional browser profile path for musik login")
    parser.add_argument("--browser-session", action="store_true", help="Open your existing browser profile instead of an isolated login window")
    parser.add_argument("--import-only", action="store_true", help="Import an existing browser session without opening the browser or prompting")
    parser.add_argument("--account", type=int, default=0, help="Browser Google account index for login: 0 first, 1 second, etc.")
    args = parser.parse_args()
    try:
        if args.command == "login":
            from .auth import login, interactive_login, login_window
            print("This imports your YouTube session locally; no passwords or cookies are sent to GitHub.")
            if args.import_only or args.browser_session or args.profile:
                flow = login if args.import_only else interactive_login
                count = flow(args.browser, args.profile, account=args.account)
            else:
                count = login_window(args.browser, account=args.account)
            print(f"Signed in to browser account {args.account}. Imported {count} playlists. Open musik and press 5.")
            return
        if args.command == "reset":
            from .reset import reset_data
            asyncio.run(reset_data())
            print("Musik reset: all saved sessions, playlists, queue, history, preferences and cache removed.")
            print("No saved Musik login remains. The next musik login opens a fresh sign-in window.")
            return
        if args.command == "logout":
            from .auth import logout
            asyncio.run(stop_service(Store()))
            logout()
            print("Signed out and playback stopped. Saved session, account selection and playlist cache removed.")
            return
        if args.command == "stop":
            stopped = asyncio.run(stop_service(Store()))
            print("Musik stopped." if stopped else "Musik is not running.")
            return
        start_check(Store())
        Musik(local=args.local).run()
    except KeyboardInterrupt:
        parser.exit(130, "\nLogin cancelled.\n")
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Musik: {exc}\n")


if __name__ == "__main__":
    main()
