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


def auth_headers(jar, account=0):
    cookies = {cookie.name: cookie.value for cookie in jar
               if cookie.domain.lstrip('.') == 'youtube.com' or cookie.domain.endswith('.youtube.com')}
    if not cookies.get('__Secure-3PAPISID'):
        raise RuntimeError("No signed-in YouTube session found. Sign in at music.youtube.com in that browser first.")
    return {"cookie": "; ".join(f"{key}={value}" for key, value in cookies.items()),
            "authorization": "SAPISIDHASH", "origin": "https://music.youtube.com",
            "x-goog-authuser": str(account)}


class QuietLogger:
    def debug(self, *args, **kwargs):
        pass
    info = warning = error = debug


def login(browser="firefox", profile=None, store=None, account=0):
    if account < 0:
        raise ValueError("Account index must be zero or greater")
    from yt_dlp.cookies import extract_cookies_from_browser
    from ytmusicapi import YTMusic
    from .service import TimeoutSession
    store = store or Store()
    if profile is None:
        flatpak_paths = {"brave": ("com.brave.Browser", "BraveSoftware/Brave-Browser"),
                         "firefox": ("org.mozilla.firefox", "mozilla/firefox"),
                         "chrome": ("com.google.Chrome", "google-chrome"),
                         "chromium": ("org.chromium.Chromium", "chromium")}
        if browser in flatpak_paths:
            application, directory = flatpak_paths[browser]
            native = Path.home() / '.config' / directory
            sandboxed = Path.home() / '.var/app' / application / 'config' / directory
            if browser == 'firefox':
                native = Path.home() / '.mozilla/firefox'
                sandboxed = Path.home() / '.var/app' / application / '.mozilla/firefox'
            if not native.exists() and sandboxed.is_dir():
                profile = str(sandboxed)
    try:
        extracted = extract_cookies_from_browser(browser, profile, logger=QuietLogger())
        filtered = http.cookiejar.MozillaCookieJar()
        for cookie in extracted:
            domain = cookie.domain.lstrip('.')
            if domain == 'youtube.com' or domain.endswith('.youtube.com'):
                filtered.set_cookie(cookie)
        api = YTMusic(auth=auth_headers(filtered, account), requests_session=TimeoutSession())
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
    # Account routing contains no credentials; keep it private alongside the session.
    from .service import save_account
    save_account(store, account)
    from .service import save_playlists
    save_playlists(store, playlists)
    return len(playlists)


def logout(store=None):
    cookie_path(store).unlink(missing_ok=True)


def open_sign_in(browser, account=0):
    """Launch the requested browser, including common Linux Flatpak installs."""
    import shutil
    import subprocess
    commands = {
        "firefox": ("firefox",), "brave": ("brave-browser", "brave-browser-stable", "brave"),
        "chrome": ("google-chrome", "google-chrome-stable"),
        "chromium": ("chromium", "chromium-browser"), "edge": ("microsoft-edge", "microsoft-edge-stable"),
    }
    url = 'https://music.youtube.com/' if account == 0 else f'https://music.youtube.com/?authuser={account}'
    arguments = next(([executable, url] for name in commands[browser]
                      if (executable := shutil.which(name))), None)
    if arguments is None and shutil.which('flatpak'):
        applications = {"brave": "com.brave.Browser", "firefox": "org.mozilla.firefox",
                        "chrome": "com.google.Chrome", "chromium": "org.chromium.Chromium",
                        "edge": "com.microsoft.Edge"}
        application = applications[browser]
        result = subprocess.run(['flatpak', 'info', application], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode == 0:
            arguments = ['flatpak', 'run', application, url]
    if arguments is None:
        return False
    subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def interactive_login(browser, profile=None, store=None, account=0):
    print(f"Opening YouTube Music in {browser}…", flush=True)
    if not (open_sign_in(browser) if account == 0 else open_sign_in(browser, account)):
        print(f"Could not open {browser}. Open https://music.youtube.com/ in it manually.", flush=True)
    print(f"Using browser account index {account}. Sign in to the account you want and complete any age verification.", flush=True)
    print("Then fully quit that browser so Musik can read its saved session.", flush=True)
    try:
        input("Return here and press Enter when finished (Ctrl+C cancels): ")
    except EOFError:
        raise RuntimeError("Login needs an interactive terminal. Use --import-only if already signed in.") from None
    print("Importing your session and playlists…", flush=True)
    return login(browser, profile, store) if account == 0 else login(browser, profile, store, account=account)
