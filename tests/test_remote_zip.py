import io
import os
import shutil
import struct
import subprocess
import threading
import wave
import zipfile
import http.server
import json

import pytest
from musik import torrents
from musik.core import Store, Track
from musik.remote_zip import audio_entries


@pytest.fixture
def remote_archive():
    wav = io.BytesIO()
    with wave.open(wav, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack('<h', 1000)*64000)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Album [FLAC]/01 Song.wav', wav.getvalue())
        archive.writestr('cover.jpg', b'image')
        archive.writestr('movie.mp4', b'video')
        archive.writestr('../unsafe.mp3', b'unsafe')
        archive.writestr('padding.bin', os.urandom(200_000))
    content = data.getvalue()
    video = io.BytesIO()
    with zipfile.ZipFile(video, 'w') as archive:
        archive.writestr('movie.mp4', b'video')
    video_content = video.getvalue()
    reads = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            content = video_content if self.path == '/video-only' else data.getvalue()
            header = self.headers.get('Range')
            reads.append(header)
            if self.path == '/no-range' or not header:
                self.send_response(200)
                body = content
            else:
                first, last = header.removeprefix('bytes=').split('-')
                start = int(first)
                end = min(int(last) if last else len(content)-1, len(content)-1)
                body = content[start:end+1]
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(content)}')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', reads
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_zip_directory_uses_ranges_and_only_lists_audio(remote_archive):
    url, reads = remote_archive
    assert audio_entries(url+'/archive', torrents.AUDIO_EXTENSIONS) == ['Album [FLAC]/01 Song.wav']
    assert reads and all(read for read in reads)
    assert reads[0] == 'bytes=0-0'
    assert audio_entries(url+'/video-only', torrents.AUDIO_EXTENSIONS) == []
    with pytest.raises(RuntimeError, match='byte-range'):
        audio_entries(url+'/no-range', torrents.AUDIO_EXTENSIONS)


@pytest.mark.parametrize("video_only", [False, True])
def test_torbox_cached_zip_opens_audio_rows_and_uses_archive_url(tmp_path, remote_archive, video_only):
    from test_torrents import Response
    url, _ = remote_archive
    class API:
        def request(self, method, endpoint, **kwargs):
            if endpoint.endswith('mylist'):
                result = [{'id': 7, 'name': 'Album', 'download_finished': True,
                           'files': [{'id': 0, 'name': 'Album.zip'}]}]
            else:
                assert endpoint.endswith('requestdl')
                assert kwargs['params']['zip_link'] == 'false'
                assert kwargs['params']['file_id'] == 0
                result = url+('/video-only' if video_only else '/archive')
            return Response({'success': True, 'data': result})
    store = Store(tmp_path)
    torrents.configure(store, token='secret')
    client = torrents.TorBox(store, session=API())
    if video_only:
        with pytest.raises(RuntimeError, match='no supported audio'):
            client.files(Track(json.dumps({'torrent_id': 7}), 'Movie ZIP', source='torrent'))
        assert client.library() == []
        return
    rows = client.files(Track(json.dumps({'torrent_id': 7}), 'Album', source='torrent'))
    assert len(rows) == 1 and rows[0].title == '01 Song.wav'
    assert json.loads(rows[0].id)['zip_entry'] == 'Album [FLAC]/01 Song.wav'
    assert client.library()[0].title == 'Album'
    stream = client.stream(rows[0])['url']
    assert stream.startswith('archive://http%3A') and stream.endswith('|Album [FLAC]/01 Song.wav')
    assert not any(path.suffix in ('.wav', '.zip', '.mp3') for path in tmp_path.rglob('*'))


@pytest.mark.skipif(not (os.environ.get('MUSIK_MPV') or shutil.which('mpv')), reason='mpv missing')
def test_real_mpv_plays_audio_inside_remote_zip_without_saving_files(tmp_path, remote_archive):
    from urllib.parse import quote
    url, _ = remote_archive
    result = subprocess.run([os.environ.get('MUSIK_MPV') or shutil.which('mpv'), '--no-config', '--ao=null',
        '--vo=null', '--term-playing-msg=ZIP_AUDIO_READY', '--frames=20', 'archive://'+quote(url+'/archive', safe='')+'|Album [FLAC]/01 Song.wav'],
        cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert b'ZIP_AUDIO_READY' in result.stdout
    assert list(tmp_path.iterdir()) == []


async def test_player_resolves_caches_and_plays_remote_zip_with_bracketed_entry(tmp_path, remote_archive, monkeypatch):
    import asyncio
    from urllib.parse import quote
    from musik.player import Player
    from musik.core import State
    mpv = os.environ.get('MUSIK_MPV') or shutil.which('mpv')
    if not mpv:
        pytest.skip('mpv missing')
    url, _ = remote_archive
    source = url+'/archive?expire=9999999999'
    archive_url = 'archive://'+quote(source, safe='')+'|Album [FLAC]/01 Song.wav'
    class API:
        def stream(self, track):
            return {'url': archive_url}
    monkeypatch.setattr(torrents, 'TorBox', lambda store: API())
    store = Store(tmp_path)
    player = Player(store=store, audio_output='null')
    track = Track(json.dumps({'torrent_id': 7, 'file_id': 0, 'zip_entry': 'Album [FLAC]/01 Song.wav'}), 'Song', source='torbox')
    try:
        await player.start()
        await player.play(track, State())
        async with asyncio.timeout(8):
            while player.position <= 0:
                await asyncio.sleep(.02)
        assert not player.idle
        assert player.duration == pytest.approx(4)
        assert player.audio_cache[player.audio_cache_key(track)][1]['url'] == archive_url
        await player.command('seek', 1, 'absolute')
        await asyncio.sleep(.1)
        assert await player.command('get_property', 'time-pos') >= .9
        assert not any(path.suffix in ('.zip', '.wav') for path in store.directory.rglob('*'))
    finally:
        await player.close()


def test_archive_audio_cache_uses_underlying_signed_url_expiry(tmp_path):
    import time
    from urllib.parse import quote
    from musik.player import Player
    player = Player(store=Store(tmp_path), audio_output='null')
    source = 'https://cdn.example/Album[FLAC].zip?expire='+str(time.time()-1)
    player.cache_audio('expired', {'url': 'archive://'+quote(source, safe='')+'|[Album]/01. Pain.flac'})
    assert 'expired' not in player.audio_cache
    source = 'https://cdn.example/Album[FLAC].zip?expire='+str(time.time()+180)
    player.cache_audio('valid', {'url': 'archive://'+quote(source, safe='')+'|[Album]/01. Pain.flac'})
    assert 0 < player.audio_cache['valid'][0]-time.monotonic() < 121
