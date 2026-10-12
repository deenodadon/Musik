import http.cookiejar
import stat

import pytest

from musik.auth import auth_headers, cookie_path, login, logout
from musik.core import Store, Track
from musik.service import MusicService
from musik.daemon import Daemon
from test_app import FakePlayer


def browser_jar():
    jar = http.cookiejar.CookieJar()
    for domain, name in [(".youtube.com", "__Secure-3PAPISID"), (".example.org", "unrelated")]:
        jar.set_cookie(http.cookiejar.Cookie(0, name, "test-cookie-not-a-real-session", None, False,
                                            domain, True, True, "/", True, True, None, True, None, None, {}))
    return jar


def test_login_scopes_private_cookies_and_imports_library(tmp_path, monkeypatch):
    import ytmusicapi
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda *a, **kw: browser_jar())
    class API:
        def __init__(self, **kwargs):
            assert "unrelated" not in kwargs["auth"]["cookie"]
        def get_library_playlists(self, limit=None):
            return [{"playlistId": "PLtest", "title": "My playlist"}]
    monkeypatch.setattr(ytmusicapi, "YTMusic", API)
    store = Store(tmp_path)
    assert login(store=store) == 1
    path = cookie_path(store)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "example.org" not in path.read_text()
    assert (tmp_path / "playlists.json").exists()
    service = MusicService(store)
    assert service.playlists() == [Track("PLtest", "My playlist", "Your playlist", "playlist")]
    logout(store)
    assert not path.exists()
    with pytest.raises(RuntimeError, match="Sign in first"):
        service.playlists()


def test_failed_login_does_not_replace_existing_credentials(tmp_path, monkeypatch):
    import yt_dlp.cookies
    store = Store(tmp_path)
    cookie_path(store).write_text("existing session")
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive details should never be displayed")
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", fail)
    with pytest.raises(RuntimeError, match="Sign-in failed") as error:
        login(store=store)
    assert "sensitive" not in str(error.value)
    assert cookie_path(store).read_text() == "existing session"
    with pytest.raises(RuntimeError):
        auth_headers(http.cookiejar.CookieJar())


async def test_play_whole_playlist_preserves_order_without_radio(tmp_path):
    daemon = Daemon(Store(tmp_path))
    daemon.player = FakePlayer()
    await daemon.dispatch("playlist", {"tracks": [{"id": "one", "title": "First"}, {"id": "two", "title": "Second"}]})
    assert [t.id for t in daemon.state.queue] == ["one", "two"]
    assert daemon.radio_task is None
    await daemon.dispatch("next", {})
    assert daemon.player.played[-1].id == "two"


async def test_playback_uses_ytdlp_compatible_cookies_without_rewriting(tmp_path, monkeypatch):
    import yt_dlp
    from musik.player import Player
    from musik.core import State
    jar = http.cookiejar.MozillaCookieJar()
    for cookie in browser_jar():
        if cookie.domain == '.youtube.com':
            jar.set_cookie(cookie)
    store = Store(tmp_path)
    jar.save(str(cookie_path(store)), ignore_discard=True)
    before = cookie_path(store).read_bytes()
    class Extractor:
        def __init__(self, options):
            assert 'cookiefile' not in options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            assert self.cookiejar.get_cookie_header('https://www.youtube.com/')
            return {'url': 'https://example.org/audio'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    player = Player(store=store)
    calls = []
    async def command(*args):
        calls.append(args)
    monkeypatch.setattr(player, 'command', command)
    await player.play(Track('test', 'Song'), State())
    assert ('loadfile', 'https://example.org/audio', 'replace') in calls
    assert cookie_path(store).read_bytes() == before


def test_brave_login_opens_browser_then_waits_before_import(monkeypatch):
    from musik import auth
    events = []
    monkeypatch.setattr(auth, 'open_sign_in', lambda browser: events.append(('open', browser)) or True)
    monkeypatch.setattr('builtins.input', lambda prompt: events.append(('wait',)) or '')
    monkeypatch.setattr(auth, 'login', lambda browser, profile, store: events.append(('import', browser)) or 4)
    assert auth.interactive_login('brave') == 4
    assert events == [('open', 'brave'), ('wait',), ('import', 'brave')]


def test_brave_native_launcher_and_manual_fallback(monkeypatch):
    import shutil
    import subprocess
    from musik.auth import open_sign_in
    calls = []
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/brave-browser' if name == 'brave-browser' else None)
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kwargs: calls.append(args))
    assert open_sign_in('brave') is True
    assert calls == [['/usr/bin/brave-browser', 'https://music.youtube.com/']]
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert open_sign_in('brave') is False


def test_brave_flatpak_launcher(monkeypatch):
    import shutil
    import subprocess
    from types import SimpleNamespace
    from musik.auth import open_sign_in
    calls = []
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/flatpak' if name == 'flatpak' else None)
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kwargs: calls.append(args))
    assert open_sign_in('brave') is True
    assert calls == [['flatpak', 'run', 'com.brave.Browser', 'https://music.youtube.com/']]
