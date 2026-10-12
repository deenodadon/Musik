import pytest

from musik.core import Store
from musik.stream import extractor_options, resolve_audio


def test_node_runtime_is_explicitly_enabled(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/node' if name == 'node' else None)
    assert extractor_options()['js_runtimes'] == {'node': {'path': '/usr/bin/node'}}


def test_page_reload_retries_with_different_client_then_succeeds(tmp_path, monkeypatch):
    import yt_dlp
    attempts = []
    class Extractor:
        def __init__(self, options):
            attempts.append(options)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            if len(attempts) == 1:
                raise RuntimeError('ERROR: The page needs to be reloaded.')
            return {'url': 'https://example.org/audio'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    assert resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))['url'].endswith('/audio')
    assert len(attempts) == 2
    assert attempts[1]['extractor_args']['youtube']['player_client'] == ['tv', 'web']


def test_access_denial_is_not_retried(tmp_path, monkeypatch):
    import yt_dlp
    attempts = []
    class Extractor:
        def __init__(self, options):
            attempts.append(options)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            raise RuntimeError('Account is not eligible for this video')
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    with pytest.raises(RuntimeError):
        resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))
    assert len(attempts) == 1
