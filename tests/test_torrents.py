import asyncio
import json
import os
from pathlib import Path
import shutil

import pytest
import requests
from musik.core import Store, Track, State
from musik.player import Player
from musik import torrents

MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40


def search_fixture(tmp_path):
    store = Store(tmp_path)
    plugin = tmp_path / 'fixture.py'
    plugin.write_text('''
from novaprinter import prettyPrinter
class fixture:
    name = 'Fixture'
    url = 'https://search.example'
    supported_categories = {'all': ''}
    def search(self, query, category):
        assert query == 'Artist%20album'
        prettyPrinter({'link': 'magnet:?xt=urn:btih:' + 'a'*40, 'name': 'Album | FLAC', 'size': '200 MB', 'seeds': 12, 'leech': 0, 'engine_url': self.url})
        prettyPrinter({'link': 'magnet:?xt=urn:btih:' + 'a'*40, 'name': 'Duplicate', 'size': '200 MB', 'seeds': 1, 'leech': 0, 'engine_url': self.url})
''')
    torrents.install_engine(str(plugin), store)
    torrents.configure(store, engines=['fixture'])
    return store


def test_standalone_nova_search_without_qbittorrent(tmp_path):
    store = search_fixture(tmp_path)
    rows = torrents.search_torrents('Artist album', store)
    assert len(rows) == 1
    assert rows[0].title == 'Album | FLAC'
    assert rows[0].source == 'torrent'
    assert json.loads(rows[0].id)['size'] == 200 * 1024**2
    assert json.loads(rows[0].id)['seeds'] == 12


def test_search_timeout_keeps_partial_results(tmp_path):
    store = search_fixture(tmp_path)
    plugin = store.directory / 'nova/engines/fixture.py'
    plugin.write_text(plugin.read_text().replace("assert query == 'Artist%20album'", "import time") + '        time.sleep(10)\n')
    rows = torrents.search_torrents('anything', store, timeout=2)
    assert len(rows) == 1
    assert rows[0].title == 'Album | FLAC'


def test_search_reports_failed_engines(tmp_path):
    store = search_fixture(tmp_path)
    with pytest.raises(RuntimeError, match='failed or timed out'):
        torrents.search_torrents('wrong query', store)


class Response:
    def __init__(self, data=None, status=200, content=b''):
        self.data, self.status_code, self.content = data, status, content
    def json(self):
        return self.data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('private URL token must not leak')
    def iter_content(self, size):
        yield self.content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class TorBoxAPI:
    def __init__(self, *, ready=True, stream_url='https://cdn.example/audio.flac'):
        self.ready, self.stream_url = ready, stream_url
        self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs['headers'] == {'Authorization': 'Bearer secret'}
        if url.endswith('/createtorrent'):
            data = {'torrent_id': 7}
        elif url.endswith('/mylist'):
            data = [{'id': 7, 'download_finished': self.ready, 'progress': .25,
                     'files': [{'id': 0, 'name': 'Album/01 Song.flac'}, {'id': 1, 'name': 'cover.jpg'}]}]
        elif url.endswith('/requestdl'):
            assert kwargs['params']['file_id'] == 0
            assert kwargs['params']['torrent_id'] == 7
            assert kwargs['params']['zip_link'] == 'false'
            data = self.stream_url
        else:
            data = {'email': 'example@example.org'}
        return Response({'success': True, 'data': data})


def torbox_fixture(tmp_path, api=None):
    store = Store(tmp_path)
    torrents.configure(store, token='secret')
    return store, torrents.TorBox(store, api or TorBoxAPI())


def test_torbox_reuses_remote_torrent_filters_audio_and_resolves_file_zero(tmp_path):
    api = TorBoxAPI()
    store, client = torbox_fixture(tmp_path, api)
    torrent = Track(json.dumps({'url': MAGNET}), 'Album', source='torrent')
    rows = client.files(torrent)
    assert len(rows) == 1 and rows[0].title == '01 Song.flac'
    assert rows[0].source == 'torbox'
    client.files(torrent)
    assert len([call for call in api.calls if call[1].endswith('/createtorrent')]) == 1
    assert client.stream(rows[0]) == {'url': 'https://cdn.example/audio.flac'}
    assert 'secret' not in rows[0].id
    assert not any(path.suffix in torrents.AUDIO_EXTENSIONS for path in store.directory.rglob('*'))
    if os.name != 'nt':
        assert (store.directory/'torrents.json').stat().st_mode & 0o077 == 0
    torrents.configure(store, token='')
    with pytest.raises(RuntimeError, match='Connect TorBox'):
        torrents.TorBox(store)


def test_uncached_torrent_keeps_remote_job_and_reports_progress(tmp_path):
    api = TorBoxAPI(ready=False)
    _, client = torbox_fixture(tmp_path, api)
    torrent = Track(json.dumps({'url': MAGNET}), 'Album', source='torrent')
    for _ in range(2):
        with pytest.raises(RuntimeError, match=r'25%'):
            client.files(torrent)
    assert len([call for call in api.calls if call[1].endswith('/createtorrent')]) == 1
    assert not any(call[1].endswith('/requestdl') for call in api.calls)


def test_saved_torrents_survive_restart_and_reopen_by_id_without_reupload(tmp_path):
    api = TorBoxAPI(ready=False)
    store, client = torbox_fixture(tmp_path, api)
    torrent = Track(json.dumps({'url': MAGNET}), 'Exact album', source='torrent')
    with pytest.raises(RuntimeError, match='preparing'):
        client.files(torrent)
    assert torrents.cached_torrents(Store(tmp_path)) == []
    api.ready = True
    client.library()
    saved = torrents.cached_torrents(Store(tmp_path))
    assert len(saved) == 1 and saved[0].title == 'Exact album'
    assert json.loads(saved[0].id)['torrent_id'] == 7
    assert client.files(saved[0])[0].source == 'torbox'
    assert len([call for call in api.calls if call[1].endswith('/createtorrent')]) == 1
    assert 'secret' not in (store.directory/'torbox-library.json').read_text()
    torrents.configure(store, token='another-account')
    assert torrents.cached_torrents(store) == []
    with pytest.raises(RuntimeError, match='another TorBox account'):
        torrents.TorBox(store, api).files(saved[0])


def test_concurrent_saved_torrents_do_not_overwrite_other_entries(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, client = torbox_fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda identity: client.remember(identity, f'Album {identity}', ready_audio=True), range(20)))
    assert len(torrents.cached_torrents(store)) == 20


def test_library_excludes_non_audio_unfinished_and_old_unfiltered_entries(tmp_path):
    class MixedAPI(TorBoxAPI):
        def request(self, method, url, **kwargs):
            assert url.endswith('/mylist')
            return Response({'success': True, 'data': [
                {'id': 1, 'name': 'Album', 'download_finished': True, 'files': [{'name': 'song.FLAC'}, {'name': 'cover.jpg'}]},
                {'id': 2, 'name': 'Movie', 'download_finished': True, 'files': [{'name': 'movie.mkv'}]},
                {'id': 3, 'name': 'Archive', 'download_finished': True, 'files': [{'name': 'music.zip'}]},
                {'id': 4, 'name': 'Pending', 'download_finished': False, 'files': [{'name': 'song.mp3'}]},
                {'id': 5, 'name': 'Empty', 'download_finished': True, 'files': []},
            ]})
    store, client = torbox_fixture(tmp_path, MixedAPI())
    torrents.write_private(store.directory/'torbox-library.json', {client.account: {
        '2': {'title': 'Old movie', 'status': 'Cached · ready'},
        '6': {'title': 'Removed torrent', 'ready_audio': True}}})
    rows = client.library()
    assert [track.title for track in rows] == ['Album']
    assert json.loads(rows[0].id)['torrent_id'] == 1
    assert [track.title for track in torrents.cached_torrents(Store(tmp_path))] == ['Album']
    assert list(json.loads((store.directory/'torbox-library.json').read_text())[client.account]) == ['1']


async def test_cached_torrents_tab_opens_remote_library_exact_item(tmp_path, monkeypatch):
    from musik.app import Musik
    from textual.widgets import DataTable
    from test_app import FakePlayer, FakeService
    store, client = torbox_fixture(tmp_path)
    monkeypatch.setattr(torrents, 'TorBox', lambda store: client)
    client.remember(7, 'Exact cached album', 'Cached · ready', ready_audio=True)
    app = Musik(store=store, service=FakeService(), player=FakePlayer())
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.click('#menu-cached')
        await pilot.pause(.1)
        assert app.view == 'cached'
        assert app.query_one('#tracks', DataTable).row_count == 1
        assert app.tracks[0].title == 'Exact cached album'
        app.query_one('#tracks', DataTable).focus()
        await pilot.press('enter')
        await pilot.pause(.1)
        assert app.results[0].source == 'torbox'
        assert app.player.played == []
        assert not any(call[1].endswith('/createtorrent') for call in client.session.calls)
        await pilot.press('backspace')
        assert app.view == 'cached'
        assert app.tracks[0].title == 'Exact cached album'


def test_direct_torrent_metadata_is_uploaded_from_memory(tmp_path, monkeypatch):
    content = b'd4:infod4:name4:testee'
    monkeypatch.setattr(torrents.requests, 'get', lambda *a, **k: Response(content=content))
    api = TorBoxAPI()
    store, client = torbox_fixture(tmp_path, api)
    assert client.add('https://example.org/album.torrent') == 7
    assert api.calls[0][2]['files']['file'][1] == content
    assert not list(store.directory.glob('*.torrent'))
    monkeypatch.setattr(torrents.requests, 'get', lambda *a, **k: Response(content=b'<html>page</html>'))
    with pytest.raises(ValueError, match='web page'):
        client.add('https://example.org/download-page')


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_torbox_errors_do_not_expose_key_or_request_url(tmp_path, status):
    class Failed:
        def request(self, *args, **kwargs):
            return Response(status=status)
    _, client = torbox_fixture(tmp_path, Failed())
    with pytest.raises(RuntimeError) as error:
        client.stream(Track(json.dumps({'torrent_id': 7, 'file_id': 0}), 'Song', source='torbox'))
    assert 'secret' not in str(error.value)
    assert 'private URL' not in str(error.value)


async def test_torrents_menu_saves_hidden_key_searches_and_opens_audio(tmp_path, monkeypatch):
    from musik.app import Musik
    from musik.torrent_screen import TorrentScreen
    from textual.widgets import Input, DataTable, Static
    from test_app import FakePlayer, FakeService
    store, client = torbox_fixture(tmp_path)
    monkeypatch.setattr(torrents, 'TorBox', lambda store: client)
    monkeypatch.setattr('musik.torrent_screen.TorBox', lambda store: client)
    app = Musik(store=store, service=FakeService(), player=FakePlayer())
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press('f11')
        await pilot.pause()
        assert isinstance(app.screen, TorrentScreen)
        key = app.screen.query_one('#torbox-key', Input)
        assert key.password and key.value == ''
        key.value = 'secret'
        key.focus()
        await pilot.press('enter')
        assert app.results == []
        await pilot.click('#torrent-save')
        assert key.value == ''
        await pilot.click('#torrent-check')
        await pilot.pause(.1)
        assert 'TorBox connection ready' in str(app.screen.query_one('#torrent-progress', Static).content)
        query = app.screen.query_one('#torrent-query', Input)
        query.value = MAGNET
        query.focus()
        await pilot.press('enter')
        await pilot.pause()
        assert app.results_section == 'torrents'
        assert app.query_one('#tracks', DataTable).get_row_at(0)[0] == 'Torrent'
        await pilot.press('a')
        assert app.state.queue == []
        await pilot.press('enter')
        await pilot.pause(.1)
        assert app.results_section == 'songs'
        assert len(app.playlist_contents) == 1
        assert app.results[0].source == 'torbox'
        assert app.player.played == []
        await pilot.press('backspace')
        assert app.results_section == 'torrents'


@pytest.mark.skipif(not (os.environ.get('MUSIK_MPV') or shutil.which('mpv')), reason='mpv not installed')
async def test_torbox_audio_url_streams_in_real_mpv_without_local_audio_copy(tmp_path, monkeypatch):
    import functools
    import http.server
    import struct
    import threading
    import wave
    origin = tmp_path / 'origin'
    origin.mkdir()
    with wave.open(str(origin/'audio.wav'), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack('<h', 1000)*64000)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(origin)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = TorBoxAPI(stream_url=f'http://127.0.0.1:{server.server_port}/audio.wav')
    store, client = torbox_fixture(tmp_path/'state', api)
    monkeypatch.setattr(torrents, 'TorBox', lambda store: client)
    monkeypatch.setattr('musik.stream.resolve_audio', lambda *a, **k: pytest.fail('TorBox must not go through YouTube'))
    player = Player(store=store, audio_output='null')
    try:
        await player.start()
        await player.play(Track(json.dumps({'torrent_id': 7, 'file_id': 0}), 'Song', source='torbox'), State())
        async with asyncio.timeout(5):
            while player.position <= 0:
                await asyncio.sleep(.02)
        assert not player.idle
        assert player.duration == pytest.approx(4)
        assert await player.command('get_property', 'http-header-fields') == []
        await player.command('seek', 1, 'absolute')
        await asyncio.sleep(.1)
        assert await player.command('get_property', 'time-pos') >= .9
        assert not any(path.suffix in torrents.AUDIO_EXTENSIONS for path in store.directory.rglob('*'))
    finally:
        await player.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)
