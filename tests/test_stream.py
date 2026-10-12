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


def test_signed_in_reload_can_fall_back_without_changing_saved_session(tmp_path, monkeypatch):
    import yt_dlp
    import http.cookiejar
    from musik import stream
    from test_auth import browser_jar
    saved = browser_jar()
    monkeypatch.setattr(stream, 'cookie_jar', lambda store: saved)
    routes = []
    class Extractor:
        def __init__(self, options):
            self.cookiejar = http.cookiejar.CookieJar()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            authenticated = bool(list(self.cookiejar))
            routes.append(authenticated)
            if authenticated:
                raise RuntimeError('The page needs to be reloaded.')
            return {'url': 'https://example.org/public'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    result = resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))
    assert routes == [True, True, False]
    assert result['_musik_public_fallback'] is True
    assert len(list(saved)) == 2


def test_signed_in_access_denial_never_falls_back_to_anonymous(tmp_path, monkeypatch):
    import yt_dlp
    from musik import stream
    from test_auth import browser_jar
    monkeypatch.setattr(stream, 'cookie_jar', lambda store: browser_jar())
    attempts = []
    class Extractor:
        def __init__(self, options):
            attempts.append(options)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            raise RuntimeError('Sign in to confirm your age')
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    with pytest.raises(RuntimeError, match='complete signed-in session'):
        resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))
    assert len(attempts) == 1


@pytest.mark.parametrize('succeeds', [True, False])
def test_age_error_retries_only_same_authenticated_session(tmp_path, monkeypatch, succeeds):
    import yt_dlp
    import http.cookiejar
    from musik import stream
    from test_auth import browser_jar
    saved = browser_jar()
    saved.set_cookie(http.cookiejar.Cookie(0, 'LOGIN_INFO', 'test-login', None, False,
        '.youtube.com', True, True, '/', True, True, None, True, None, None, {}))
    monkeypatch.setattr(stream, 'cookie_jar', lambda store: saved)
    attempts = []
    class Extractor:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            assert any(cookie.name == 'LOGIN_INFO' for cookie in self.cookiejar)
            attempts.append(self.options)
            if len(attempts) == 1 or not succeeds:
                raise RuntimeError('Sorry, this content is age-restricted')
            return {'url': 'https://example.org/signed-audio'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    if succeeds:
        assert stream.resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))['url'].endswith('signed-audio')
    else:
        with pytest.raises(RuntimeError, match='saved account session'):
            stream.resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))
    assert len(attempts) == 2
    assert attempts[1]['extractor_args']['youtube']['player_client'] == ['web', 'mweb']


@pytest.mark.parametrize('succeeds', [True, False])
def test_unavailable_format_retries_same_session_without_anonymous_fallback(tmp_path, monkeypatch, succeeds):
    import yt_dlp
    from musik import stream
    from test_auth import browser_jar
    saved = browser_jar()
    monkeypatch.setattr(stream, 'cookie_jar', lambda store: saved)
    monkeypatch.setattr(stream, 'extractor_options', lambda: {
        'format': 'bestaudio/best[acodec!=none]', 'js_runtimes': {'node': {'path': '/node'}}})
    attempts = []
    class Extractor:
        def __init__(self, options):
            assert options['format'] == 'bestaudio/best[acodec!=none]'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            assert list(self.cookiejar) == list(saved)
            attempts.append(True)
            if len(attempts) == 1 or not succeeds:
                raise RuntimeError('Requested format is not available. Use --list-formats')
            return {'url': 'https://example.org/audio'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    if succeeds:
        assert resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))['url'].endswith('/audio')
    else:
        with pytest.raises(RuntimeError, match='playable audio stream'):
            resolve_audio('https://www.youtube.com/watch?v=test', Store(tmp_path))
    assert len(attempts) == 2
