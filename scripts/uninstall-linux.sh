#!/usr/bin/env bash
# Remove Musik while preserving shared prerequisites and edited source files.
set -euo pipefail
install_dir="${MUSIK_INSTALL_DIR:-$HOME/Musik}"
bin_dir="${MUSIK_BIN_DIR:-$HOME/.local/bin}"
keep_data="${1:-}"
if [[ -n "$keep_data" && "$keep_data" != --keep-data ]]; then
  printf 'Usage: uninstall-linux.sh [--keep-data]\n' >&2
  exit 1
fi
if [[ ! -x "$install_dir/.venv/bin/python" ]]; then
  printf 'No Musik virtual environment found. Nothing was removed.\n' >&2
  exit 1
fi
"$install_dir/.venv/bin/python" -I - "$install_dir" "$bin_dir" "$keep_data" <<'PY'
import asyncio
import importlib.metadata
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import uuid

root = pathlib.Path(sys.argv[1]).expanduser().absolute()
venv = root / '.venv'
launcher = pathlib.Path(sys.argv[2]).expanduser().absolute() / 'musik'
if root.is_symlink() or venv.is_symlink() or root.resolve() in (pathlib.Path('/'), pathlib.Path.home().resolve(), pathlib.Path.home().parent.resolve()):
    raise SystemExit('Refusing to remove an unsafe installation path.')
if pathlib.Path(sys.prefix).resolve() != venv.resolve():
    raise SystemExit('The installation points to another Python environment. Nothing was removed.')
try:
    importlib.metadata.version('musik-tui')
except importlib.metadata.PackageNotFoundError:
    raise SystemExit('This is not a recognized Musik installation. Nothing was removed.')

# Recognize only this installation's launcher, including older symlink installs.
owned_launcher = launcher.is_symlink() and launcher.resolve().is_relative_to(venv.resolve())
if launcher.is_file() and not launcher.is_symlink():
    for line in launcher.read_text().splitlines():
        if line.startswith('exec '):
            arguments = shlex.split(line)
            owned_launcher |= len(arguments) > 1 and pathlib.Path(arguments[1]).is_relative_to(venv)

from musik.core import Store
from musik.daemon import stop_service
from musik.platform import lock_exclusive
from musik.reset import reset_data
store = Store()
if sys.argv[3] == '--keep-data':
    asyncio.run(stop_service(store))
    store.directory.mkdir(parents=True, exist_ok=True)
    with (store.directory / 'update.lock').open('a+') as handle:
        try:
            lock_exclusive(handle)
        except BlockingIOError:
            raise SystemExit('An update is running. Wait for it to finish and retry.')
else:
    asyncio.run(reset_data(store))

preserve_source = True
if (root / '.git').is_dir() and shutil.which('git'):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()
    try:
        changes = git('status', '--porcelain', '--untracked-files=all')
        commits = git('rev-list', '--count', '--branches', '--not', '--remotes')
        ignored = git('ls-files', '--others', '--ignored', '--exclude-standard').splitlines()
        unknown = [name for name in ignored if not re.match(r'^(\.venv/|build/|dist/|[^/]+\.egg-info/|(?:.*/)?__pycache__/|\.pytest_cache/)', name)]
        preserve_source = bool(changes or unknown or commits != '0')
    except (OSError, subprocess.CalledProcessError):
        pass

if owned_launcher:
    launcher.unlink(missing_ok=True)
shutil.rmtree(venv)
if preserve_source:
    backup = root.with_name(root.name + '-source-backup-' + uuid.uuid4().hex)
    root.rename(backup)
    print(f'Source files preserved at {backup}')
else:
    shutil.rmtree(root)
print('Musik uninstalled. Shared prerequisites and personal music were kept.')
if sys.argv[3] == '--keep-data':
    print('Saved Musik data was kept.')
PY
