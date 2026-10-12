from pathlib import Path
import pytest
from musik import playback_setup


def test_helper_setup_builds_once_and_reuses_pinned_install(tmp_path, monkeypatch):
    destination = tmp_path / 'helper'
    npm = tmp_path / 'npm-cli.js'
    npm.write_text('')
    monkeypatch.setattr(playback_setup, 'helper_directory', lambda: destination)
    monkeypatch.setattr(playback_setup.shutil, 'which', lambda name: str(npm) if name == 'npm' else '/bin/' + name)
    calls = []
    def run(arguments, cwd=None):
        calls.append(arguments)
        if 'clone' in arguments:
            server = Path(arguments[-1]) / 'server'
            server.mkdir(parents=True)
        elif Path(arguments[-1]).name == 'tsc':
            output = Path(cwd) / 'build/generate_once.js'
            output.parent.mkdir()
            output.write_text('compiled helper')
    monkeypatch.setattr(playback_setup, 'run', run)
    assert playback_setup.ensure_helper() == destination / 'server'
    assert playback_setup.helper_ready()
    assert playback_setup.HELPER_VERSION in calls[0]
    assert len(calls) == 3
    playback_setup.ensure_helper()
    assert len(calls) == 3


def test_failed_setup_does_not_mark_helper_ready(tmp_path, monkeypatch):
    destination = tmp_path / 'helper'
    npm = tmp_path / 'npm-cli.js'
    npm.write_text('')
    monkeypatch.setattr(playback_setup, 'helper_directory', lambda: destination)
    monkeypatch.setattr(playback_setup.shutil, 'which', lambda name: str(npm) if name == 'npm' else '/bin/' + name)
    def fail(*args, **kwargs):
        raise RuntimeError('setup failed')
    monkeypatch.setattr(playback_setup, 'run', fail)
    with pytest.raises(RuntimeError):
        playback_setup.ensure_helper()
    assert not playback_setup.helper_ready()


def test_token_provider_configuration_survives_client_retry(tmp_path, monkeypatch):
    import yt_dlp
    from musik import stream
    monkeypatch.setattr(playback_setup, 'helper_directory', lambda: tmp_path)
    monkeypatch.setattr(playback_setup, 'helper_ready', lambda: True)
    attempts = []
    class Extractor:
        def __init__(self, options):
            attempts.append(options)
            assert options['extractor_args']['youtubepot-bgutilscript']['server_home'] == [str(tmp_path / 'server')]
            assert options['extractor_args']['youtube']['fetch_pot'] == ['always']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            if len(attempts) == 1:
                raise RuntimeError('The page needs to be reloaded')
            return {'url': 'audio'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    from musik.core import Store
    assert stream.resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))['url'] == 'audio'
    assert len(attempts) == 2
