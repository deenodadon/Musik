"""Local, user-authorized browser-cookie sign-in. Never store passwords."""
import http.cookiejar
import os
import tempfile
from pathlib import Path

from .core import Store


def cookie_path(store=None):
    return (store or Store()).directory / "youtube-cookies.txt"


def cookie_jar(store=None):
    path = cookie_path(store)
    if not path.exists():
        return None
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=False)
    return jar


def auth_headers(jar):
    cookies = {cookie.name: cookie.value for cookie in jar
               if cookie.domain.lstrip('.') == 'youtube.com' or cookie.domain.endswith('.youtube.com')}
    if not cookies.get('__Secure-3PAPISID'):
        raise RuntimeError("No signed-in YouTube session found. Sign in at music.youtube.com in that browser first.")
    return {"cookie": "; ".join(f"{key}={value}" for key, value in cookies.items()),
            "authorization": "SAPISIDHASH", "origin": "https://music.youtube.com",
            "x-goog-authuser": "0"}


class QuietLogger:
    def debug(self, *args, **kwargs):
        pass
    info = warning = error = debug


def login(browser="firefox", profile=None, store=None):
    from yt_dlp.cookies import extract_cookies_from_browser
    from ytmusicapi import YTMusic
    from .service import TimeoutSession
    store = store or Store()
    try:
        extracted = extract_cookies_from_browser(browser, profile, logger=QuietLogger())
        filtered = http.cookiejar.MozillaCookieJar()
        for cookie in extracted:
            domain = cookie.domain.lstrip('.')
            if domain == 'youtube.com' or domain.endswith('.youtube.com'):
                filtered.set_cookie(cookie)
        api = YTMusic(auth=auth_headers(filtered), requests_session=TimeoutSession())
        # A successful authenticated library operation validates the session.
        playlists = api.get_library_playlists(limit=None)
    except Exception:
        raise RuntimeError("Sign-in failed. Sign in to YouTube Music in the selected browser, close the browser, and retry. Browser keyring access and network access are required.") from None
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=store.directory, prefix='.cookies-')
    os.close(fd)
    try:
        filtered.save(name, ignore_discard=True, ignore_expires=False)
        os.chmod(name, 0o600)
        os.replace(name, cookie_path(store))
    finally:
        Path(name).unlink(missing_ok=True)
    from .service import save_playlists
    save_playlists(store, playlists)
    return len(playlists)


def logout(store=None):
    cookie_path(store).unlink(missing_ok=True)
