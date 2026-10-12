"""Platform paths, advisory locks and detached subprocess options."""
import os
import subprocess
from pathlib import Path


def state_home():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    return Path.home() / '.local/state'


def cache_home():
    if os.name == 'nt':
        return state_home() / 'Cache'
    return Path.home() / '.cache'


def detached_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def lock_exclusive(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write('0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError('Musik lock is held') from exc
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def windows_mpv_executable():
    """Find WinGet/Inno's installation even when it does not modify PATH."""
    from pathlib import Path
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(variable)
        if not root:
            continue
        for folder in ('MPV Player', 'mpv', 'Programs/MPV Player', 'Programs/mpv'):
            candidate = Path(root) / folder / 'mpv.exe'
            if candidate.is_file():
                return str(candidate)
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths\mpv.exe') as key:
                    candidate = Path(winreg.QueryValueEx(key, None)[0].strip('"'))
                    if candidate.is_file():
                        return str(candidate)
            except OSError:
                continue
    except ImportError:
        pass
    return None


def mpv_executable():
    import shutil
    return os.environ.get('MUSIK_MPV') or shutil.which('mpv') or (windows_mpv_executable() if os.name == 'nt' else None)
