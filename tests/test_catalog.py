from textual.widgets import DataTable, Input, Static, Tabs

from musik.app import Musik
from musik.core import Store, Track
from musik.service import MusicService
from test_app import FakePlayer


class CatalogAPI:
    def search(self, query, limit=40):
        return [{'resultType': 'artist', 'browseId': 'UC1', 'artist': 'Artist'},
                {'resultType': 'album', 'browseId': 'AL1', 'title': 'Record', 'artists': [{'name': 'Artist'}]},
                {'resultType': 'song', 'videoId': 'v1', 'title': 'Song', 'artists': None}]

    def get_artist(self, artist):
        return {'name': 'Artist', 'songs': {'results': [{'videoId': 'v1', 'title': 'Song'}], 'browseId': 'VLall'},
                'albums': {'browseId': 'UC1', 'params': 'albums', 'results': []},
                'singles': {'results': [{'browseId': 'AL2', 'title': 'Single', 'year': '2025'}]}}

    def get_artist_albums(self, artist, params, limit=None):
        assert limit is None
        return [{'browseId': 'AL1', 'title': 'Record', 'year': '2024'}]

    def get_album(self, album):
        return {'artists': [{'name': 'Artist'}], 'tracks': [
            {'videoId': 'v1', 'title': 'First', 'artists': None},
            {'videoId': 'v2', 'title': 'Second'}, {'title': 'Unavailable'}]}

    def get_playlist(self, playlist, limit=None):
        return {'tracks': [{'videoId': 'v1', 'title': 'First'}]}


def service(store):
    result = MusicService(store)
    result._api = CatalogAPI()
    return result


def test_artist_search_complete_releases_and_ordered_album(tmp_path):
    api = service(Store(tmp_path))
    found = api.search('Artist')
    assert [item.source for item in found] == ['artist', 'album', 'youtube']
    rows = api.artist_catalog(found[0])
    assert [item.id for item in rows] == ['v1', 'VLall', 'AL1', 'AL2']
    assert rows[0].artist == 'Artist'
    assert [item.id for item in api.album_tracks(found[1])] == ['v1', 'v2']


async def test_search_artist_album_back_and_play_collection(tmp_path):
    store = Store(tmp_path)
    player = FakePlayer()
    app = Musik(store=store, service=service(store), player=player)
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one(Input).value = 'Artist'
        await pilot.press('enter')
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 1
        assert app.tracks[0].source == 'youtube'
        tabs = app.query_one('#result-sections', Tabs)
        assert str(tabs.get_tab('section-albums').label) == 'Albums (1)'
        await pilot.click('#section-albums')
        await pilot.pause()
        assert [item.source for item in app.tracks] == ['album']
        await pilot.click('#section-artists')
        await pilot.pause()
        await pilot.click("#tracks", offset=(5, 2))
        await pilot.pause()
        assert app.catalog_title == 'Artist'
        assert app.query_one(DataTable).row_count == 2
        await pilot.press('f7')
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 2
        await pilot.click("#tracks", offset=(5, 2))
        await pilot.pause()
        assert app.catalog_title == 'Record'
        assert app.query_one(DataTable).row_count == 2
        await pilot.press('backspace')
        assert app.catalog_title == 'Artist'
        assert app.results_section == "albums"
        assert app.query_one(DataTable).cursor_row == 0
        await pilot.press('enter')
        await pilot.pause()
        await pilot.press('o')
        await pilot.pause()
        assert app.view == 'queue'
        assert [item.id for item in app.state.queue] == ['v1', 'v2']
        assert player.played[-1].id == 'v1'
