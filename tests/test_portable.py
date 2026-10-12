import os
import shutil

import pytest

from musik.core import Store
from musik.daemon import BackgroundPlayer, request, runtime_directory, stop_service
from musik.platform import lock_exclusive


def test_portable_exclusive_lock(tmp_path):
    path = tmp_path / 'lock'
    with path.open('a+') as first, path.open('a+') as second:
        lock_exclusive(first)
        with pytest.raises(BlockingIOError):
            lock_exclusive(second)
    with path.open('a+') as second:
        lock_exclusive(second)


@pytest.mark.skipif(not (os.environ.get('MUSIK_MPV') or shutil.which('mpv')), reason='mpv not installed')
async def test_authenticated_loopback_service(tmp_path, monkeypatch):
    import json
    import asyncio
    monkeypatch.setenv('MUSIK_TRANSPORT', 'tcp')
    monkeypatch.setenv('MUSIK_AUDIO_OUTPUT', 'null')
    store = Store(tmp_path / 'state')
    client = BackgroundPlayer(store)
    try:
        await client.start()
        assert (await request(store, 'status'))['idle']
        endpoint = json.loads((runtime_directory(store) / 'service.sock').read_text())
        reader, writer = await asyncio.open_connection('127.0.0.1', endpoint['port'])
        writer.write(b'{"operation":"stop","token":"wrong"}\n')
        await writer.drain()
        assert 'Invalid' in json.loads(await reader.readline())['error']
        writer.close()
        await writer.wait_closed()
        assert (await request(store, 'status'))['idle']
    finally:
        await stop_service(store)


async def test_older_background_player_cannot_silently_downgrade_song_repeat(tmp_path):
    from dataclasses import asdict
    from musik.core import State
    client = BackgroundPlayer(Store(tmp_path))
    state = asdict(State(repeat=True))
    state.pop('repeat_one')
    client.update({'state': state, 'position': 0, 'duration': 0, 'paused': False,
                   'idle': True, 'levels': [], 'message': ''})
    assert client.supports_repeat_song is False
    with pytest.raises(RuntimeError, match='musik stop'):
        await client.settings(State(repeat=True, repeat_one=True))
    assert client.state.repeat_mode == 'Playlist'


def test_windows_mpv_found_in_winget_folder_without_path(tmp_path, monkeypatch):
    from musik.platform import windows_mpv_executable
    root = tmp_path / 'Program Files'
    executable = root / 'MPV Player/mpv.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture')
    monkeypatch.setenv('PROGRAMFILES', str(root))
    assert windows_mpv_executable() == str(executable)


def test_windows_per_user_mpv_location(tmp_path, monkeypatch):
    from musik.platform import windows_mpv_executable
    executable = tmp_path / 'Programs/MPV Player/mpv.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture')
    monkeypatch.setenv('PROGRAMFILES', str(tmp_path / 'empty'))
    monkeypatch.setenv('PROGRAMFILES(X86)', str(tmp_path / 'empty-x86'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert windows_mpv_executable() == str(executable)
