"""Install the pinned yt-dlp playback-token helper in Musik's own cache."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .platform import cache_home, lock_exclusive

HELPER_VERSION = '2.0.2'
HELPER_REMOTE = 'https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git'


def helper_directory():
    return Path(os.environ.get('XDG_CACHE_HOME', str(cache_home()))) / 'musik' / f'playback-helper-{HELPER_VERSION}'


def helper_ready():
    directory = helper_directory()
    return (directory / '.ready').is_file() and (directory / 'server/build/generate_once.js').is_file()


def run(arguments, cwd=None):
    try:
        result = subprocess.run(arguments, cwd=cwd, capture_output=True, timeout=300,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT='0', NPM_CONFIG_UPDATE_NOTIFIER='false'))
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Playback helper setup could not run or timed out. Check git, Node.js and npm.') from None
    if result.returncode:
        raise RuntimeError('Playback helper dependency installation or build failed. Check your network and rerun musik setup-playback.')


def ensure_helper():
    if helper_ready():
        return helper_directory() / 'server'
    node, npm, git = shutil.which('node'), shutil.which('npm'), shutil.which('git')
    if not all((node, npm, git)):
        raise RuntimeError('Playback helper needs Node.js 22+, npm and git. Rerun the Musik installer, then musik setup-playback.')
    # Invoke npm through Node, avoiding shell quoting and .cmd execution on Windows.
    npm_path = Path(npm).resolve()
    npm_cli = npm_path if npm_path.suffix == '.js' else npm_path.parent / 'node_modules/npm/bin/npm-cli.js'
    if not npm_cli.is_file():
        raise RuntimeError('npm CLI was not found beside Node.js. Reinstall Node.js with the Musik installer.')
    directory = helper_directory()
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory.parent / 'playback-helper.lock').open('a+') as lock:
        try:
            lock_exclusive(lock)
        except BlockingIOError:
            raise RuntimeError('Playback helper setup is already running. Retry shortly.') from None
        if helper_ready():
            return directory / 'server'
        with tempfile.TemporaryDirectory(prefix='.helper-', dir=directory.parent) as temporary:
            source = Path(temporary) / 'source'
            print("[1/3] Downloading playback helper…", flush=True)
            run([git, '-c', 'credential.helper=', 'clone', '--depth', '1', '--branch', HELPER_VERSION, HELPER_REMOTE, str(source)])
            server = source / 'server'
            print("[2/3] Installing helper dependencies; this may take a few minutes…", flush=True)
            run([node, str(npm_cli), 'ci', '--cache', str(directory.parent / 'npm-cache'), '--no-audit', '--no-fund'], cwd=server)
            print("[3/3] Building playback helper…", flush=True)
            run([node, str(server / 'node_modules/typescript/bin/tsc')], cwd=server)
            if not (server / 'build/generate_once.js').is_file():
                raise RuntimeError('Playback helper build did not produce its token-generation script.')
            (source / '.ready').write_text(HELPER_VERSION, encoding='utf-8')
            if directory.exists():
                shutil.rmtree(directory)
            source.rename(directory)
    return directory / 'server'
