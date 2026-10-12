import asyncio

from musik.core import Store, Track
from musik.daemon import Daemon
from musik.service import MusicService
from test_app import FakePlayer


class RadioAPI:
    def get_watch_playlist(self, **kwargs):
        assert kwargs["radio"] is True
        return {"tracks": [{"videoId": "seed", "title": "Seed"},
                           {"videoId": "other", "title": "Related", "artists": [{"name": "Similar artist"}]},
                           {"videoId": "other", "title": "Duplicate"}, {"title": "Unavailable"}]}


def test_radio_api_filters_seed_and_duplicates():
    service = MusicService()
    service._api = RadioAPI()
    assert service.radio(Track("seed", "Seed")) == [Track("other", "Related", "Similar artist")]


async def test_new_search_seeds_radio_but_queue_selection_preserves_it(tmp_path):
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()

    class Service:
        def radio(self, track):
            return [Track(track.id + "-related", "Related")]

    daemon.service = Service()
    await daemon.dispatch("play", {"track": {"id": "a", "title": "A"}})
    await daemon.radio_task
    assert [t.id for t in daemon.state.queue] == ["a", "a-related"]
    await daemon.dispatch("next", {})
    assert daemon.state.current.id == "a-related"
    assert len(daemon.state.queue) == 2
    await daemon.dispatch("play", {"index": 1})
    assert daemon.state.index == 1
    assert [t.id for t in daemon.state.queue] == ["a", "a-related"]
    assert daemon.player.played[-1].id == "a-related"


async def test_stale_radio_and_failure_leave_new_selection_intact(tmp_path):
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    release = __import__('threading').Event()

    class Service:
        def radio(self, track):
            if track.id == "a":
                release.wait(2)
                return [Track("stale", "Stale")]
            raise RuntimeError("network unavailable")

    daemon.service = Service()
    await daemon.dispatch("play", {"track": {"id": "a", "title": "A"}})
    await asyncio.sleep(.05)
    await daemon.dispatch("play", {"track": {"id": "b", "title": "B"}})
    release.set()
    await daemon.radio_task
    assert [t.id for t in daemon.state.queue] == ["b"]
    assert daemon.player.played[-1].id == "b"
    assert "Radio unavailable" in daemon.message


async def test_background_repeat_song_on_eof_and_manual_next(tmp_path):
    from musik.core import State
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    daemon.state = State(queue=[Track('a', 'A'), Track('b', 'B')], index=0,
                         repeat=True, repeat_one=True, shuffle=True)
    monitor = asyncio.create_task(daemon.monitor())
    try:
        await daemon.player.events.put({'reason': 'eof'})
        await asyncio.sleep(.05)
        assert daemon.player.played[-1].id == 'a'
        assert daemon.state.index == 0
        await daemon.dispatch('next', {})
        assert daemon.player.played[-1].id == 'b'
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def test_queue_selection_keeps_manual_tracks_and_next_order(tmp_path):
    from musik.core import State
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    daemon.state = State(queue=[Track('a', 'A'), Track('b', 'B'), Track('c', 'C')], index=0)
    await daemon.dispatch('play', {'index': 1})
    assert [t.id for t in daemon.state.queue] == ['a', 'b', 'c']
    assert daemon.radio_task is None
    await daemon.dispatch('next', {'finished': True})
    assert daemon.state.index == 2
    assert daemon.player.played[-1] == daemon.state.queue[2]


async def test_radio_arrival_does_not_skip_idle_selected_song(tmp_path):
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    daemon.player.idle = True
    class Service:
        def radio(self, track):
            return [Track('b', 'B')]
    daemon.service = Service()
    await daemon.dispatch('play', {'track': {'id': 'a', 'title': 'A'}})
    await daemon.radio_task
    assert daemon.state.index == 0
    assert [t.id for t in daemon.player.played] == ['a']
    assert [t.id for t in daemon.state.queue] == ['a', 'b']


async def test_background_manual_next_ignores_shuffle_and_preserves_queue(tmp_path, monkeypatch):
    from musik.core import State
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    tracks = [Track(str(i), str(i)) for i in range(4)]
    daemon.state = State(queue=tracks.copy(), index=0, shuffle=True)
    monkeypatch.setattr('musik.core.random.choice', lambda choices: choices[-1])
    await daemon.dispatch('next', {})
    assert daemon.state.index == 1
    assert daemon.player.played[-1] == tracks[1]
    assert daemon.state.queue == tracks
    await daemon.dispatch('previous', {})
    assert daemon.state.index == 0
    await daemon.dispatch('next', {'finished': True})
    assert daemon.state.index == 3
