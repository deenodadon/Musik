import asyncio

from textual.widgets import DataTable, Input, Static

from musik.app import Musik
from musik.core import Store, Track


class FakePlayer:
    position = 0
    duration = 120
    paused = False
    idle = False

    def __init__(self):
        self.events = asyncio.Queue()
        self.played = []
        self.commands = []

    async def start(self):
        pass

    async def settings(self, state):
        self.volume = state.volume

    async def play(self, track, state):
        self.played.append(track)

    async def command(self, *args):
        self.commands.append(args)

    async def close(self):
        pass


class FakeService:
    def search(self, query):
        return [Track("abc", query, "Artist")]

    def lyrics(self, track):
        return "A line of lyrics"


async def test_keyboard_search_queue_play_and_restore(tmp_path):
    player = FakePlayer()
    app = Musik(store=Store(tmp_path), service=FakeService(), player=player)
    async with app.run_test(size=(110, 35)) as pilot:
        app.query_one(Input).value = "Test song"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 1
        await pilot.press("a", "f", "2", "enter")
        await pilot.pause()
        assert player.played[0].title == "Test song"
        assert len(app.state.queue) == 1
        assert len(app.state.favorites) == 1
        await pilot.press("l")
        await pilot.pause()
        assert "A line of lyrics" in str(app.query_one("#lyrics", Static).content)
        await pilot.press("s", "r", "e", "v")
        assert app.state.shuffle and app.state.repeat
        assert app.state.equalizer == "bass"
        assert app.state.speed == 1.25
    restored = Store(tmp_path).load()
    assert restored.history[0].title == "Test song"
    assert restored.equalizer == "bass"


async def test_volume_shortcuts_with_search_and_track_focus(tmp_path):
    player = FakePlayer()
    app = Musik(store=Store(tmp_path), service=FakeService(), player=player)
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one(Input).focus()
        await pilot.press("ctrl+up")
        assert app.state.volume == 75
        assert player.volume == 75
        assert app.query_one(Input).value == ""
        await pilot.press("ctrl+down")
        assert app.state.volume == 70
        await pilot.press("escape", "plus")
        assert app.state.volume == 75
        await pilot.press("equals_sign")
        assert app.state.volume == 80
        await pilot.press("minus")
        assert app.state.volume == 75
        app.state.volume = 100
        await pilot.press("ctrl+up")
        assert player.volume == 100
        app.state.volume = 0
        await pilot.press("ctrl+down")
        assert player.volume == 0


async def test_pause_shortcut_works_in_search(tmp_path):
    player = FakePlayer()
    app = Musik(store=Store(tmp_path), service=FakeService(), player=player)
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one(Input).focus()
        await pilot.press("ctrl+p")
        assert player.commands[-1] == ("cycle", "pause")
        assert app.query_one(Input).value == ""
        await pilot.press("escape", "space")
        assert player.commands == [("cycle", "pause"), ("cycle", "pause")]
