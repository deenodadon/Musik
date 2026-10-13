import os
from pathlib import Path
import shlex
import subprocess
import sysconfig
import venv

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='Linux shell uninstaller')
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/uninstall-linux.sh'


def installation(tmp_path):
    upstream = tmp_path/'upstream'
    upstream.mkdir()
    def git(*args, cwd=upstream):
        subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)
    git('init', '-b', 'main')
    git('config', 'user.email', 'fixture@example.org')
    git('config', 'user.name', 'Fixture')
    (upstream/'.gitignore').write_text('.venv/\n')
    (upstream/'README.md').write_text('Musik source')
    git('add', '.')
    git('commit', '-m', 'fixture')
    root = tmp_path/'My Musik'
    git('clone', str(upstream), str(root))
    environment = root/'.venv'
    venv.EnvBuilder(with_pip=False).create(environment)
    packages = next((environment/'lib').glob('python*/site-packages'))
    # Use the test installation's dependencies without touching its environment.
    (packages/'test-dependencies.pth').write_text(f'import site; site.addsitedir({sysconfig.get_paths()["purelib"]!r})\n')
    bin_dir = tmp_path/'bin'
    bin_dir.mkdir()
    (bin_dir/'musik').write_text('#!/bin/sh\nexec ' + shlex.quote(str(environment/'bin/python')) + ' -I -m musik.app "$@"\n')
    state = tmp_path/'state/musik'
    state.mkdir(parents=True)
    (state/'saved.json').write_text('private data')
    music = tmp_path/'Music/song.flac'
    music.parent.mkdir()
    music.write_bytes(b'personal audio')
    env = dict(os.environ, MUSIK_INSTALL_DIR=str(root), MUSIK_BIN_DIR=str(bin_dir),
               XDG_STATE_HOME=str(tmp_path/'state'), XDG_CACHE_HOME=str(tmp_path/'cache'), MUSIK_AUTO_UPDATE='0')
    return root, bin_dir, state, music, env


@pytest.mark.parametrize('keep', [False, True])
def test_linux_uninstaller_removes_only_owned_installation(tmp_path, keep):
    root, bin_dir, state, music, env = installation(tmp_path)
    result = subprocess.run(['bash', str(SCRIPT)] + (['--keep-data'] if keep else []), env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()
    assert not (bin_dir/'musik').exists()
    assert music.read_bytes() == b'personal audio'
    assert (state/'saved.json').exists() == keep


def test_linux_uninstaller_preserves_source_changes_and_other_launcher(tmp_path):
    root, bin_dir, _, music, env = installation(tmp_path)
    (root/'personal-note.txt').write_text('keep me')
    launcher = bin_dir/'musik'
    launcher.write_text('#!/bin/sh\nexec /usr/bin/other-program "$@"\n')
    result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    backup = next(tmp_path.glob('My Musik-source-backup-*'))
    assert (backup/'personal-note.txt').read_text() == 'keep me'
    assert not (backup/'.venv').exists()
    assert launcher.read_text() == '#!/bin/sh\nexec /usr/bin/other-program "$@"\n'
    assert music.exists()


def test_linux_uninstaller_rejects_redirected_environment(tmp_path):
    root = tmp_path/'install'
    root.mkdir()
    shared = Path(sysconfig.get_paths()['purelib']).parents[2]
    (root/'.venv').symlink_to(shared, target_is_directory=True)
    result = subprocess.run(['bash', str(SCRIPT)], env=dict(os.environ, MUSIK_INSTALL_DIR=str(root)),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert (root/'.venv').is_symlink()
    assert shared.exists()


def test_authenticated_one_liners_never_execute_a_failed_download(tmp_path):
    import re
    readme = (SCRIPT.parent.parent/'README.md').read_text()
    commands = [line for block in re.findall(r'```bash\n(.*?)```', readme, re.S)
                for line in block.splitlines() if line.startswith('(task_script=$(mktemp)')]
    assert len(commands) == 2
    tools = tmp_path/'tools'
    tools.mkdir()
    scratch = tmp_path/'scratch'
    scratch.mkdir()
    marker = tmp_path/'must-not-run'
    gh = tools/'gh'
    gh.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote('touch ' + shlex.quote(str(marker))) + '\nexit 1\n')
    gh.chmod(0o755)
    for command in commands:
        result = subprocess.run(['bash', '-c', command], env=dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'], TMPDIR=str(scratch)), timeout=5)
        assert result.returncode != 0
        assert not marker.exists()
        assert list(scratch.iterdir()) == []
