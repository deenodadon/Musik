from .platform import cache_home
"""Remove Musik-owned user data after stopping playback and locking updates."""
import asyncio
from .platform import lock_exclusive, detached_options
import os
import shutil
from pathlib import Path

from .core import Store
from .daemon import runtime_directory, stop_service


async def reset_data(store=None, cache_directory=None):
    store = store or Store()
    directory = store.directory
    cache = cache_directory or Path(os.environ.get('XDG_CACHE_HOME', str(cache_home()))) / 'musik'
    # Never traverse a redirected data directory or remove a broad parent.
    for path in (directory, cache):
        if path.is_symlink() or path.resolve() in (Path('/'), Path.home().resolve(), Path.cwd().resolve()):
            raise RuntimeError('Refusing to reset an unsafe Musik data path')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'update.lock').open('a+') as update_lock:
        try:
            lock_exclusive(update_lock)
        except BlockingIOError:
            raise RuntimeError('An update is running. Wait for it to finish, then run musik reset again.') from None
        await stop_service(store)
        runtime = runtime_directory(store)
        with (runtime / 'service.lock').open('a+') as service_lock:
            # mpv/socket cleanup can precede release of the daemon's lock.
            # Wait for that final release instead of requiring a second reset.
            for _ in range(100):
                try:
                    lock_exclusive(service_lock)
                    break
                except BlockingIOError:
                    await asyncio.sleep(.05)
            else:
                raise RuntimeError('Playback did not finish shutting down; no saved data was deleted.')
            for base, held in ((directory, 'update.lock'), (runtime, 'service.lock')):
                for entry in base.iterdir():
                    if entry.name == held:
                        continue
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
            if cache.exists():
                shutil.rmtree(cache)
    # Windows cannot delete an open lock file; release handles first.
    (directory / 'update.lock').unlink(missing_ok=True)
    (runtime / 'service.lock').unlink(missing_ok=True)
    directory.rmdir()
    runtime.rmdir()
