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


async def test_playlist_import_and_open_from_keyboard(tmp_path):
    class Library(FakeService):
        def playlists(self):
            return [Track('PLtest', 'My mix', 'Your playlist', 'playlist')]
        def playlist_tracks(self, playlist):
            assert playlist.id == 'PLtest'
            return [Track('one', 'First'), Track('two', 'Second')]
    app = Musik(store=Store(tmp_path), service=Library(), player=FakePlayer())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press('escape', '5')
        await pilot.pause()
        assert app.view == 'playlists'
        assert app.query_one(DataTable).row_count == 1
        await pilot.press('enter')
        await pilot.pause()
        assert app.view == 'results'
        assert app.query_one(DataTable).row_count == 2
        assert [track.id for track in app.playlist_contents] == ['one', 'two']


async def test_click_queue_menu_shows_tracks_and_removal(tmp_path):
    from textual.widgets import Tabs
    app = Musik(store=Store(tmp_path), service=FakeService(), player=FakePlayer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click('#menu-queue')
        await pilot.pause()
        assert app.view == 'queue'
        assert 'Queue is empty' in str(app.query_one('#queue-help', Static).content)
        app.state.queue = [Track('one', 'First'), Track('two', 'Second')]
        app.state.index = 1
        app.refresh_tracks()
        table = app.query_one(DataTable)
        assert table.get_row_at(0)[1] == '1. First'
        assert table.get_row_at(1)[0].plain == '▶ Playing'
        assert str(app.query_one('#main-menu', Tabs).get_tab('menu-queue').label) == 'Queue (2)'
        table.focus()
        table.move_cursor(row=0)
        await pilot.press('delete')
        assert [track.id for track in app.state.queue] == ['two']
        await pilot.click('#menu-results')
        await pilot.pause()
        assert app.view == 'results'


async def test_repeat_shortcut_cycles_song_playlist_off(tmp_path):
    app = Musik(store=Store(tmp_path), service=FakeService(), player=FakePlayer())
    async with app.run_test(size=(120, 45)) as pilot:
        assert not list(app.query('#repeat-toggle'))
        await pilot.press('escape', 'r')
        await pilot.pause()
        assert app.state.repeat_mode == 'Song'
        await pilot.press('escape', 'r')
        assert app.state.repeat_mode == 'Playlist'
        await pilot.press('r')
        assert app.state.repeat_mode == 'Off'


async def test_playing_highlight_remains_while_browsing_and_tracks_playback(tmp_path):
    from rich.text import Text
    player = FakePlayer()
    app = Musik(store=Store(tmp_path), service=FakeService(), player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        app.state.queue = [Track('one', 'First'), Track('two', 'Second')]
        app.state.index = 1
        app.action_view('queue')
        table = app.query_one(DataTable)
        assert table.cursor_row == 1
        table.move_cursor(row=0)
        assert isinstance(table.get_row_at(1)[1], Text)
        assert table.get_row_at(1)[1].style == 'bold #66d9bb on #203a3a'
        player.paused = True
        await app.tick()
        assert table.cursor_row == 0
        assert table.get_row_at(1)[0].plain == 'Ⅱ Paused'
        app.state.index = 0
        player.paused = False
        await app.tick()
        assert table.get_row_at(0)[0].plain == '▶ Playing'
        assert table.get_row_at(1)[0] == 'Song'
        player.idle = True
        await app.tick()
        assert table.get_row_at(0)[0] == 'Song'
