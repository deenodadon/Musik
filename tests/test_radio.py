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


async def test_each_selection_reseeds_and_automatic_next_does_not(tmp_path):
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
    await daemon.radio_task
    assert [t.id for t in daemon.state.queue] == ["a-related", "a-related-related"]


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
