import fcntl

import pytest

from musik.core import Store
from musik.daemon import runtime_directory
from musik.reset import reset_data
from musik.auth import auth_headers
from musik.service import read_account, save_account
from test_auth import browser_jar


async def test_reset_removes_only_musik_data_and_is_repeatable(tmp_path):
    store = Store(tmp_path / 'musik-state')
    store.directory.mkdir()
    for name in ('youtube-cookies.txt', 'account.json', 'playlists.json', 'state.json', 'update.json'):
        (store.directory / name).write_text('{}')
    cache = tmp_path / 'musik-cache'
    cache.mkdir()
    (cache / 'old.json').write_text('{}')
    music = tmp_path / 'music.mp3'
    music.write_bytes(b'keep')
    runtime = runtime_directory(store)
    await reset_data(store, cache)
    assert not store.directory.exists()
    assert not cache.exists()
    assert not runtime.exists()
    assert music.read_bytes() == b'keep'
    await reset_data(store, cache)


async def test_reset_refuses_active_update_and_symlink(tmp_path):
    store = Store(tmp_path / 'state')
    store.directory.mkdir()
    with (store.directory / 'update.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='update is running'):
            await reset_data(store, tmp_path / 'cache')
    target = tmp_path / 'private'
    target.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match='unsafe'):
        await reset_data(Store(linked), tmp_path / 'cache')
    assert target.exists()


def test_account_index_routing_and_persistence(tmp_path):
    assert auth_headers(browser_jar(), 1)['x-goog-authuser'] == '1'
    store = Store(tmp_path)
    save_account(store, 1)
    assert read_account(store) == 1


@pytest.mark.skipif(not (__import__('os').environ.get('MUSIK_MPV') or __import__('shutil').which('mpv')), reason='mpv not installed')
async def test_reset_stops_live_service_before_removing_state(tmp_path, monkeypatch):
    from musik.daemon import BackgroundPlayer, stop_service
    monkeypatch.setenv('MUSIK_AUDIO_OUTPUT', 'null')
    store = Store(tmp_path / 'live-state')
    client = BackgroundPlayer(store)
    try:
        await client.start()
        runtime = runtime_directory(store)
        assert (runtime / 'service.sock').exists()
        await reset_data(store, tmp_path / 'cache')
        assert not runtime.exists()
        assert not store.directory.exists()
    finally:
        if store.directory.exists():
            await stop_service(store)


async def test_reset_waits_for_daemon_lock_release(tmp_path, monkeypatch):
    import asyncio
    from musik import reset
    store = Store(tmp_path / 'shutdown-state')
    store.directory.mkdir()
    (store.directory / 'youtube-cookies.txt').write_text('private session')
    runtime = runtime_directory(store)
    async def stopped(store):
        return True
    monkeypatch.setattr(reset, 'stop_service', stopped)
    with (runtime / 'service.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        async def release():
            await asyncio.sleep(.1)
            fcntl.flock(lock, fcntl.LOCK_UN)
        task = asyncio.create_task(release())
        await reset_data(store, tmp_path / 'cache')
        await task
    assert not store.directory.exists()
    assert not runtime.exists()
