"""Check the official main branch and update only Musik's virtual environment."""
from .platform import lock_exclusive, detached_options
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import Store

REMOTE = "https://github.com/deenodadon/Musik.git"


def status_path(store):
    return store.directory / "update.json"


def save_status(store, data):
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(dir=store.directory, prefix=".update-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(path, status_path(store))
    finally:
        if os.path.exists(path):
            os.unlink(path)


def read_status(store):
    try:
        return json.loads(status_path(store).read_text())
    except (OSError, ValueError):
        return {}


def enabled():
    if os.environ.get("MUSIK_AUTO_UPDATE", "1") == "0" or sys.prefix == sys.base_prefix:
        return False
    try:
        direct = importlib.metadata.distribution("musik-tui").read_text("direct_url.json")
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            return False
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return False
    return True


def start_check(store):
    if enabled():
        store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.Popen([sys.executable, "-m", "musik.updater", str(store.directory)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **detached_options(),
                         cwd=tempfile.gettempdir())


def run_command(arguments, cwd=None, timeout=180):
    # Capture output so Git/pip never corrupt the terminal interface.
    # Reuse the user's credential helper for private repository access without
    # opening an authentication prompt from the detached background updater.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", PIP_NO_INPUT="1")
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{'Git' if arguments[0] == 'git' else 'Package installation'} update step failed")
    return result.stdout.strip()


def install_checkout(source):
    # Build first, so build failures leave the installed application untouched.
    wheels = source.parent / "wheels"
    run_command([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(source)])
    wheel = next(wheels.glob("musik_tui-*.whl"))
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", str(wheel)])


def check_and_update(store, remote=REMOTE, installer=install_checkout):
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (store.directory / "update.lock").open("a+") as lock:
        try:
            lock_exclusive(lock)
        except BlockingIOError:
            return
        previous = read_status(store)
        installed = previous.get("installed_commit")
        data = {"installed_commit": installed, "message": "Checking for updates…"}
        save_status(store, data)
        try:
            output = run_command(["git", "ls-remote", remote, "refs/heads/main"], timeout=30)
            tip = output.split()[0] if output else ""
            if len(tip) != 40 or any(c not in "0123456789abcdef" for c in tip):
                raise RuntimeError("Repository main branch is unavailable")
            if tip == installed:
                data["message"] = "Musik is up to date"
            else:
                data["message"] = "Installing update in the background…"
                save_status(store, data)
                with tempfile.TemporaryDirectory(prefix="musik-update-") as folder:
                    source = Path(folder) / "source"
                    run_command(["git", "clone", "--depth", "1", "--branch", "main", remote, str(source)], timeout=60)
                    # Install the commit actually fetched, including a newer push
                    # made between ls-remote and clone.
                    commit = run_command(["git", "-C", str(source), "rev-parse", "HEAD"], timeout=10)
                    installer(source)
                data = {"installed_commit": commit, "message": "Update installed · available next launch"}
        except (OSError, RuntimeError, subprocess.TimeoutExpired, StopIteration):
            data["message"] = "Update unavailable · will retry next launch"
        save_status(store, data)


if __name__ == "__main__":
    if enabled():
        check_and_update(Store(Path(sys.argv[1])))
