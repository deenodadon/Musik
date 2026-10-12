import asyncio
import math
import os
import shutil
import struct
import wave

import pytest

from musik.core import State, Track
from musik.player import Player


@pytest.mark.skipif(not (os.environ.get("MUSIK_MPV") or shutil.which("mpv")), reason="mpv not installed")
async def test_real_mpv_playback_controls_and_eof(tmp_path):
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"".join(struct.pack("<h", int(1000 * math.sin(i * 2 * math.pi * 440 / 16000))) for i in range(48000)))
    player = Player(audio_output="null")
    try:
        await player.start()
        state = State(volume=45, speed=1.25, equalizer="bass")
        await player.play(Track(str(path), "Tone", source="local"), state)
        for _ in range(50):
            if player.duration > 0 and player.position > 0:
                break
            await asyncio.sleep(0.05)
        assert player.duration == pytest.approx(3.0)
        assert player.position > 0
        assert await player.command("get_property", "volume") == 45
        assert await player.command("get_property", "speed") == 1.25
        await player.command("seek", 1, "absolute")
        await asyncio.sleep(.12)
        resume = await player.command("get_property", "time-pos")
        await player.play(Track(str(path), "Tone", source="local"), state, retry_stream=True)
        await asyncio.sleep(.12)
        assert await player.command("get_property", "time-pos") >= resume - .1
        await player.command("set_property", "pause", True)
        assert await player.command("get_property", "pause") is True
        await player.command("seek", 1, "absolute")
        state.equalizer = "vocal"
        await player.settings(state)
        state.equalizer = "flat"
        await player.settings(state)
        await player.command("set_property", "pause", False)
        async with asyncio.timeout(6):
            while True:
                event = await player.events.get()
                if event.get("reason") == "eof":
                    assert event["_musik_generation"] == player.play_generation == 2
                    break
                assert event.get("reason") != "error"
    finally:
        await player.close()
    assert player.process.returncode is not None


@pytest.mark.parametrize('message,category', [
    ('HTTP error 403 Forbidden https://private-stream?secret=COOKIE', 'HTTP 403'),
    ('Server returned 404 Not Found', 'HTTP 404'),
    ('Connection timed out', 'network or proxy'),
    ('Failed to initialize audio', 'audio output device'),
    ('Failed to recognize file format', 'decode'),
])
def test_playback_failure_categories_do_not_expose_stream_data(message, category):
    from musik.player import playback_error
    detail = playback_error(message)
    assert category in detail
    assert 'private-stream' not in detail and 'COOKIE' not in detail


async def test_audio_retry_refreshes_link_and_excludes_failed_format(tmp_path, monkeypatch):
    from musik import stream
    from musik.core import Store
    calls = []
    def resolve(url, store, **options):
        calls.append(options)
        return {'url': 'https://example.org/audio.m3u8', 'format_id': 'hls',
                'http_headers': {'User-Agent': 'test-agent', 'Cookie': 'private-cookie'}}
    monkeypatch.setattr(stream, 'resolve_audio', resolve)
    player = Player(store=Store(tmp_path))
    player.format_id = 'failed-format'
    commands = []
    async def command(*args):
        commands.append(args)
    monkeypatch.setattr(player, 'command', command)
    await player.play(Track('a', 'A'), State(), retry_stream=True)
    assert calls == [{'alternate_audio': True, 'rejected_format': 'failed-format'}]
    assert player.stream_retry_used and player.format_id == 'hls'
    assert ('loadfile', 'https://example.org/audio.m3u8', 'replace') in commands
    assert not any('private-cookie' in str(command) for command in commands)


@pytest.mark.parametrize('failed', [False, True])
async def test_recoverable_http_warning_is_not_reported_as_playback_failure(failed):
    import json
    player = Player()
    player.play_generation = 1
    player.reader = asyncio.StreamReader()
    messages = [
        {'event': 'start-file'},
        {'event': 'log-message', 'text': 'HTTP error 403 Forbidden'},
    ]
    if failed:
        messages.append({'event': 'end-file', 'reason': 'error'})
    else:
        messages.extend([{'event': 'file-loaded'}, {'event': 'playback-restart'}])
    for message in messages:
        player.reader.feed_data((json.dumps(message) + '\n').encode())
    player.reader.feed_eof()
    await player._read()
    if failed:
        assert 'HTTP 403' in player.failure_detail
        assert 'HTTP 403' in (await player.events.get())['_musik_error']
    else:
        assert player.failure_detail is None
        assert player.pending_failure is None


async def test_hls_uses_track_duration_and_advancing_audio_clock(tmp_path, monkeypatch):
    import json
    from musik import stream
    from musik.core import Store
    monkeypatch.setattr(stream, 'resolve_audio', lambda *args, **kwargs: {
        'url': 'https://example.org/audio.m3u8', 'duration': 215, 'protocol': 'm3u8_native'})
    player = Player(store=Store(tmp_path))
    async def command(*args):
        pass
    monkeypatch.setattr(player, 'command', command)
    await player.play(Track('song', 'Song'), State())
    player.reader = asyncio.StreamReader()
    messages = [
        {'event': 'start-file', 'playlist_entry_id': 1},
        {'event': 'property-change', 'name': 'duration', 'data': 95383},
        {'event': 'property-change', 'name': 'time-pos', 'data': None},
        {'event': 'property-change', 'name': 'audio-pts', 'data': 90000},
        {'event': 'property-change', 'name': 'audio-pts', 'data': 90002.5},
    ]
    for message in messages:
        player.reader.feed_data((json.dumps(message) + '\n').encode())
    player.reader.feed_eof()
    await player._read()
    assert player.duration == 215
    assert player.position == 2.5
    player.update_position('audio-pts', 90012.5)
    assert player.position == 12.5
    player.update_position('audio-pts', 90002.5)
    assert player.position == 2.5


async def test_recent_audio_url_is_reused_but_rejected_or_changed_login_is_refreshed(tmp_path, monkeypatch):
    from musik import stream
    from musik.auth import cookie_path
    from musik.core import Store
    player = Player(store=Store(tmp_path))
    calls = []
    def resolve(*args, **kwargs):
        calls.append(kwargs)
        return {'url': 'https://example.org/audio', 'format_id': 'audio', 'duration': 200}
    async def command(*args):
        pass
    monkeypatch.setattr(stream, 'resolve_audio', resolve)
    monkeypatch.setattr(player, 'command', command)
    track = Track('song', 'Song')
    await player.play(track, State())
    await player.play(track, State())
    assert len(calls) == 1
    cookie_path(player.store).write_text('changed session')
    await player.play(track, State())
    assert len(calls) == 2
    await player.play(track, State(), retry_stream=True)
    assert len(calls) == 3 and calls[-1]['alternate_audio'] is True


def test_expired_stream_url_is_not_cached(tmp_path):
    from musik.core import Store
    player = Player(store=Store(tmp_path))
    player.cache_audio('key', {'url': 'https://example.org/audio?expire=1'})
    assert not player.audio_cache


async def test_alternate_stream_starts_at_existing_position(tmp_path, monkeypatch):
    from musik import stream
    from musik.core import Store
    monkeypatch.setattr(stream, 'resolve_audio', lambda *a, **k: {'url': 'https://example.org/hls', 'duration': 200})
    player = Player(store=Store(tmp_path))
    player.position = 2.5
    player.playback_started = True
    commands = []
    async def command(*args):
        commands.append(args)
    monkeypatch.setattr(player, 'command', command)
    await player.play(Track('song', 'Song'), State(), retry_stream=True)
    assert ('loadfile', 'https://example.org/hls', 'replace', -1, {'start': '2.5'}) in commands
    player.update_position('audio-pts', 90000)
    assert player.position == 2.5
    player.update_position('audio-pts', 90001)
    assert player.position == 3.5


async def test_alternate_starts_at_zero_when_rejected_stream_never_played(tmp_path, monkeypatch):
    from musik.core import Store
    monkeypatch.setattr('musik.stream.resolve_audio', lambda *a, **k: {'url': 'https://example.org/hls', 'duration': 200})
    player = Player(store=Store(tmp_path))
    player.position = 2.5
    commands = []
    async def command(*args):
        commands.append(args)
    player.command = command
    await player.play(Track('song', 'Song'), State(), retry_stream=True)
    assert ('loadfile', 'https://example.org/hls', 'replace') in commands
    assert player.position == player.resume_offset == 0


async def test_fallback_eof_keeps_selected_song_until_alternate_really_finishes(tmp_path, monkeypatch):
    import json
    from musik.core import Store
    from musik.daemon import Daemon
    store = Store(tmp_path)
    daemon = Daemon(store)
    player = daemon.player
    daemon.state = State(queue=[Track('song', 'Song'), Track('next', 'Next')], index=0)
    monkeypatch.setattr('musik.stream.resolve_audio', lambda url, store, **kw: {
        'url': 'https://example.org/alternate' if kw.get('alternate_audio') else url, 'duration': 200})
    player.reader = asyncio.StreamReader()
    def feed(*messages):
        for message in messages:
            player.reader.feed_data((json.dumps(message) + '\n').encode())
    loads = []
    async def command(*args):
        if args[0] == 'loadfile':
            loads.append(args)
            feed({'event': 'start-file', 'playlist_entry_id': 9 + len(loads)})
            if args[1] == 'https://example.org/alternate':
                feed({'event': 'file-loaded'}, {'event': 'playback-restart'},
                     {'event': 'property-change', 'name': 'time-pos', 'data': .25},
                     {'event': 'end-file', 'playlist_entry_id': 10, 'reason': 'eof'})
    player.command = command
    reader = asyncio.create_task(player._read())
    monitor = asyncio.create_task(daemon.monitor())
    try:
        await daemon.play(daemon.state.current)
        feed({'event': 'log-message', 'text': 'HTTP error 403 Forbidden'},
             {'event': 'file-loaded'},
             {'event': 'property-change', 'name': 'time-pos', 'data': 2.5},
             {'event': 'end-file', 'playlist_entry_id': 10, 'reason': 'eof'})
        async with asyncio.timeout(3):
            while not player.playback_started:
                await asyncio.sleep(.01)
        await asyncio.sleep(.05)
        assert loads == [('loadfile', daemon.state.queue[0].url, 'replace'),
                         ('loadfile', 'https://example.org/alternate', 'replace')]
        assert daemon.state.index == 0
        assert player.position == .25
        assert player.failure_detail is None and player.pending_failure is None
        assert daemon.message == 'Playing Song'
        feed({'event': 'property-change', 'name': 'time-pos', 'data': 200},
             {'event': 'end-file', 'playlist_entry_id': 11, 'reason': 'eof'})
        async with asyncio.timeout(3):
            while daemon.state.index != 1:
                await asyncio.sleep(.01)
        assert len(loads) == 3 and loads[-1][1] == daemon.state.queue[1].url
    finally:
        monitor.cancel()
        reader.cancel()
        await asyncio.gather(reader, monitor, return_exceptions=True)
        await player.close()
        daemon.service.nuclear.close()


@pytest.mark.skipif(not (os.environ.get('MUSIK_MPV') or shutil.which('mpv')), reason='mpv not installed')
async def test_real_http_403_recovers_without_seek_or_queue_change(tmp_path, monkeypatch):
    import functools
    import http.server
    import threading
    from musik.core import Store
    from musik.daemon import Daemon
    audio = tmp_path / 'audio.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack('<h', 1000) * 96000)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/rejected':
                self.send_error(403)
            else:
                super().do_GET()
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []
    def resolve(url, store, **kwargs):
        calls.append(kwargs)
        path = 'audio.wav' if kwargs.get('alternate_audio') else 'rejected'
        return {'url': f'http://127.0.0.1:{server.server_port}/{path}', 'duration': 6}
    monkeypatch.setattr('musik.stream.resolve_audio', resolve)
    daemon = Daemon(Store(tmp_path / 'state'))
    daemon.player.audio_output = 'null'
    daemon.state = State(queue=[Track('song', 'Song'), Track('next', 'Next')], index=0)
    monitor = None
    try:
        await daemon.player.start()
        monitor = asyncio.create_task(daemon.monitor())
        await daemon.play(daemon.state.current)
        async with asyncio.timeout(8):
            while not daemon.player.playback_started or daemon.player.position <= 0:
                await asyncio.sleep(.02)
        assert len(calls) == 2 and calls[1]['alternate_audio'] is True
        assert daemon.state.index == 0
        assert daemon.player.resume_offset == 0
        assert daemon.player.failure_detail is None
        assert daemon.player.pending_failure is None
        assert daemon.message == 'Playing Song'
    finally:
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        await daemon.player.close()
        daemon.service.nuclear.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)


async def test_late_error_from_replaced_entry_does_not_poison_new_stream():
    import json
    player = Player()
    player.play_generation = 1
    player.reader = asyncio.StreamReader()
    player.reader.feed_data((json.dumps({'event': 'start-file', 'playlist_entry_id': 10}) + '\n').encode())
    # Feed the first start separately to capture its original generation.
    task = asyncio.create_task(player._read())
    await asyncio.sleep(0)
    player.play_generation = 2
    for message in [
        {'event': 'start-file', 'playlist_entry_id': 11},
        {'event': 'file-loaded'},
        {'event': 'end-file', 'playlist_entry_id': 10, 'reason': 'error'},
    ]:
        player.reader.feed_data((json.dumps(message) + '\n').encode())
    player.reader.feed_eof()
    await task
    assert player.failure_detail is None
    events = [await player.events.get(), await player.events.get()]
    assert events[-1]['_musik_generation'] == 1


async def test_queue_preparation_shares_foreground_lookup_without_restarting(tmp_path, monkeypatch):
    from musik.core import Store
    import threading
    entered, release = threading.Event(), threading.Event()
    calls = []
    def resolve(url, store):
        calls.append(url)
        entered.set()
        release.wait(3)
        return {'url': 'https://example.org/audio', 'duration': 120}
    monkeypatch.setattr('musik.stream.resolve_audio', resolve)
    player = Player(store=Store(tmp_path))
    commands = []
    async def command(*args):
        commands.append(args)
    player.command = command
    track = Track('song', 'Song')
    background = asyncio.create_task(player.prepare_audio(track))
    assert await asyncio.to_thread(entered.wait, 2)
    foreground = asyncio.create_task(player.play(track, State()))
    await asyncio.sleep(.02)
    release.set()
    await asyncio.gather(background, foreground)
    assert calls == [track.url]
    assert sum(c[0] == 'loadfile' for c in commands) == 1
    assert player.play_generation == 1
    await player.prepare_audio(track)
    assert len(calls) == 1


def test_queue_cache_keeps_more_than_eight_prepared_songs(tmp_path):
    from musik.core import Store
    player = Player(store=Store(tmp_path))
    for index in range(26):
        player.cache_audio(str(index), {'url': 'https://example.org/audio'})
    assert len(player.audio_cache) == 26


@pytest.mark.parametrize('supports_index',[False,True])
async def test_resume_uses_the_loadfile_protocol_advertised_by_mpv(tmp_path,supports_index):
    from musik.core import Store
    player=Player(store=Store(tmp_path))
    calls=[]
    async def command(*args):
        calls.append(args)
        if args==('get_property','command-list'):
            arguments=[{'name':'url'},{'name':'flags'}]
            if supports_index:arguments.append({'name':'index'})
            arguments.append({'name':'options'})
            return [{'name':'loadfile','args':arguments}]
    player.command=command
    try:
        await player.detect_loadfile_command()
        player.position=12.5
        player.playback_started=True
        await player.play(Track(str(tmp_path/'tone.wav'),'Tone',source='local'),State(),retry_stream=True)
        expected=('loadfile',str(tmp_path/'tone.wav'),'replace')
        if supports_index:expected+=(-1,)
        expected+=({'start':'12.5'},)
        assert expected in calls
    finally:await player.close()
