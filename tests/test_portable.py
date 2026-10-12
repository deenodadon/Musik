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
