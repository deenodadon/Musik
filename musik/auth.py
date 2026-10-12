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
    stage = "browser extraction"
    try:
        extracted = extract_cookies_from_browser(browser, profile, logger=QuietLogger())
        filtered = http.cookiejar.MozillaCookieJar()
        for cookie in extracted:
            domain = cookie.domain.lstrip('.')
            if domain == 'youtube.com' or domain.endswith('.youtube.com'):
                filtered.set_cookie(cookie)
        stage = "session reading"
        headers = auth_headers(filtered, account)
        stage = "account validation"
        api = YTMusic(auth=headers, requests_session=TimeoutSession())
        # A successful authenticated library operation validates the session.
        playlists = api.get_library_playlists(limit=None)
    except Exception as exc:
        if stage == "browser extraction":
            detail = f"Browser cookies could not be read ({type(exc).__name__}). Close the browser and unlock your desktop keyring."
        elif stage == "session reading":
            detail = "No readable signed-in YouTube session was found. Wait for your music library before closing the window; unlock KDE Wallet if requested."
        else:
            from .service import explain_library_error
            detail = explain_library_error(exc)
        raise RuntimeError(f"Sign-in failed during {stage}. {detail}") from None
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
    store = store or Store()
    cookie_path(store).unlink(missing_ok=True)
    for name in ('account.json', 'playlists.json'):
        (store.directory / name).unlink(missing_ok=True)


def windows_default_browser():
    """Read the user's HTTPS association without changing it."""
    import winreg
    import re
    import ntpath
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as key:
            command = winreg.ExpandEnvironmentStrings(winreg.QueryValueEx(key, None)[0])
    except OSError:
        raise RuntimeError("Could not detect your Windows default browser. Choose one with musik login --browser edge/chrome/firefox/brave.") from None
    match = re.match(r'^\s*(?:"([^"\n]+)"|(.+?\.exe)(?:\s|$))', command, re.IGNORECASE)
    executable = (match.group(1) or match.group(2)) if match else ""
    name = ntpath.basename(executable).lower()
    browser = {"brave.exe": "brave", "msedge.exe": "edge", "firefox.exe": "firefox",
               "chromium.exe": "chromium", "chrome.exe": "chrome"}.get(name)
    if browser == "chrome" and "chromium" in progid.lower():
        browser = "chromium"
    if browser is None:
        raise RuntimeError("Your default browser is not supported for session import. Use musik login --browser edge/chrome/firefox/brave with an installed browser.")
    return browser, executable


def default_browser():
    import sys
    return windows_default_browser()[0] if sys.platform == "win32" else "brave"


def browser_executable(browser):
    import shutil
    names = {'firefox': ('firefox',), 'brave': ('brave-browser', 'brave-browser-stable', 'brave'),
             'chrome': ('google-chrome', 'google-chrome-stable'),
             'chromium': ('chromium', 'chromium-browser'), 'edge': ('microsoft-edge', 'microsoft-edge-stable')}
    found = next((path for name in names[browser] if (path := shutil.which(name))), None)
    if found or os.name != 'nt':
        return found
    try:
        default, executable = windows_default_browser()
        if default == browser and Path(executable).is_file():
            return executable
    except RuntimeError:
        pass
    relatives = {'brave': 'BraveSoftware/Brave-Browser/Application/brave.exe',
                 'chrome': 'Google/Chrome/Application/chrome.exe',
                 'chromium': 'Chromium/Application/chrome.exe',
                 'edge': 'Microsoft/Edge/Application/msedge.exe', 'firefox': 'Mozilla Firefox/firefox.exe'}
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if root := os.environ.get(variable):
            path = Path(root) / relatives[browser]
            if path.is_file():
                return str(path)
    return None


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
    executable = browser_executable(browser)
    arguments = [executable, url] if executable else None
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


def login_window(browser='brave', store=None, account=0):
    """Use Chromium app mode with an isolated, temporary sign-in profile.

    This is a real browser window, not an embedded OAuth webview. Google may
    reject embedded webviews; app mode preserves the browser's normal sandbox.
    """
    import shutil
    import subprocess
    store = store or Store()
    commands = {'brave': ('brave-browser', 'brave-browser-stable', 'brave'),
                'chrome': ('google-chrome', 'google-chrome-stable'),
                'chromium': ('chromium', 'chromium-browser'),
                'edge': ('microsoft-edge', 'microsoft-edge-stable')}
    if browser not in commands:
        raise RuntimeError('The dedicated login window requires Brave, Chrome, Chromium or Edge. For Firefox, use --browser-session.')
    executable = browser_executable(browser)
    flatpak_id = {'brave': 'com.brave.Browser', 'chrome': 'com.google.Chrome',
                  'chromium': 'org.chromium.Chromium', 'edge': 'com.microsoft.Edge'}[browser]
    prefix = [executable] if executable else None
    if prefix is None and shutil.which('flatpak'):
        result = subprocess.run(['flatpak', 'info', flatpak_id], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode == 0:
            prefix = ['flatpak', 'run', flatpak_id]
    if prefix is None:
        raise RuntimeError(f'{browser} is not installed. Install it or choose --browser chrome/chromium; --browser-session imports an existing session.')
    # Flatpak must be able to see the profile from both inside and outside its
    # sandbox. Native browsers use a private directory in Musik's data folder.
    base = store.directory
    if prefix[0] == 'flatpak':
        base = Path.home() / '.var/app' / flatpak_id / 'config'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='musik-login-', dir=base) as folder:
        profile = Path(folder)
        url = f'https://music.youtube.com/?authuser={account}'
        arguments = prefix + [f'--user-data-dir={profile}', f'--app={url}',
                              '--no-first-run', '--no-default-browser-check', '--disable-background-mode']
        print('Opening the Musik sign-in window…', flush=True)
        print('Sign in, complete any Google verification, and wait for your YouTube Music library to appear.', flush=True)
        print('Then close the sign-in window. Musik will import your session and playlists automatically.', flush=True)
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            code = process.wait()
            if code != 0:
                raise RuntimeError('The login window could not start or closed with an error. Try --browser-session instead.')
            print('Importing the account from the sign-in window…', flush=True)
            return login(browser, str(profile), store, account=account)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
