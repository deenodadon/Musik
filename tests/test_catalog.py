from textual.widgets import DataTable, Input, Static, Tabs

from musik.app import Musik
from musik.core import Store, Track
from musik.service import MusicService
from test_app import FakePlayer


class CatalogAPI:
    def search(self, query, filter=None, limit=40):
        if filter == "songs":
            return []
        if filter == "albums":
            return []
        return [{'resultType': 'artist', 'browseId': 'UC1', 'artist': 'Artist'}]

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
    assert [item.source for item in found] == ['artist', 'youtube', 'youtube', 'album', 'album']
    rows = api.artist_catalog(found[0])
    assert [item.id for item in rows] == ['v1', 'VLall', 'AL1', 'AL2']
    assert rows[0].artist == 'Artist'
    assert [item.id for item in api.album_tracks(found[3])] == ['v1', 'v2']


async def test_search_artist_album_back_and_play_collection(tmp_path):
    store = Store(tmp_path)
    player = FakePlayer()
    app = Musik(store=store, service=service(store), player=player)
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one(Input).value = 'Artist'
        await pilot.press('enter')
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 2
        assert app.tracks[0].source == 'youtube'
        tabs = app.query_one('#result-sections', Tabs)
        assert str(tabs.get_tab('section-albums').label) == 'Albums (2)'
        await pilot.click('#section-albums')
        await pilot.pause()
        assert [item.source for item in app.tracks] == ['album', 'album']
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


def test_exact_artist_search_returns_25_specific_songs_and_official_albums(tmp_path):
    class API(CatalogAPI):
        def search(self, query, filter=None, limit=40):
            if filter == 'artists':
                return [{'browseId': 'UC1', 'artist': 'Artist'}, {'browseId': 'UCother', 'artist': 'Artist Tribute'}]
            assert filter == 'songs'  # Broad album search must never run here.
            assert limit >= 25
            return ([{'videoId': f'v{i}', 'title': f'Song {i}', 'artists': [{'name': 'Artist', 'id': 'UC1'}]}
                     for i in range(30)] +
                    [{'videoId': 'cover', 'title': 'Artist cover', 'artists': [{'name': 'Another person'}]}])
        def get_playlist(self, playlist, limit=None):
            return {'tracks': []}
    api = MusicService(Store(tmp_path))
    api._api = API()
    found = api.search(' artist ')
    songs = [row for row in found if row.source == 'youtube']
    assert len(songs) >= 25
    assert all(row.artist == 'Artist' for row in songs)
    assert 'cover' not in {row.id for row in songs}
    assert [row.id for row in found if row.source == 'album'] == ['AL1', 'AL2']
    assert [row.title for row in found if row.source == 'artist'] == ['Artist']


def test_song_title_search_filters_unrelated_results(tmp_path):
    class API:
        def search(self, query, filter=None, limit=40):
            if filter == 'artists':
                return []
            if filter == 'albums':
                return [{'browseId': 'AL', 'title': 'Unrelated record', 'artists': [{'name': 'Someone'}]}]
            return [{'videoId': 'yes', 'title': 'Specific Song', 'artists': [{'name': 'Artist'}]},
                    {'videoId': 'no', 'title': 'Unrelated', 'artists': [{'name': 'Someone'}]}]
    api = MusicService(Store(tmp_path))
    api._api = API()
    assert [row.id for row in api.search('Specific Song')] == ['yes']
