"""Remove Musik-owned user data after stopping playback and locking updates."""
import fcntl
import os
import shutil
from pathlib import Path

from .core import Store
from .daemon import runtime_directory, stop_service


async def reset_data(store=None, cache_directory=None):
    store = store or Store()
    directory = store.directory
    cache = cache_directory or Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'musik'
    # Never traverse a redirected data directory or remove a broad parent.
    for path in (directory, cache):
        if path.is_symlink() or path.resolve() in (Path('/'), Path.home().resolve(), Path.cwd().resolve()):
            raise RuntimeError('Refusing to reset an unsafe Musik data path')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'update.lock').open('a') as update_lock:
        try:
            fcntl.flock(update_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An update is running. Wait for it to finish, then run musik reset again.') from None
        await stop_service(store)
        runtime = runtime_directory(store)
        with (runtime / 'service.lock').open('a') as service_lock:
            try:
                fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Playback is still shutting down. Retry musik reset.') from None
            shutil.rmtree(directory)
            if cache.exists():
                shutil.rmtree(cache)
            shutil.rmtree(runtime)
