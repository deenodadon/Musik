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

    async def start(self):
        pass

    async def settings(self, state):
        pass

    async def play(self, track, state):
        self.played.append(track)

    async def command(self, *args):
        pass

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
