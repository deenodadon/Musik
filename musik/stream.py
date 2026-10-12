"""Resolve YouTube audio with the installed challenge solver and real session."""
import os
import shutil
from pathlib import Path

from .auth import cookie_jar, QuietLogger


def extractor_options():
    options = {'format': 'bestaudio/best', 'quiet': True, 'no_warnings': True,
               'logger': QuietLogger(), 'socket_timeout': 20, 'noplaylist': True,
               'cachedir': str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'musik' / 'yt-dlp')}
    runtimes = {}
    for runtime in ('deno', 'node', 'bun'):
        if executable := shutil.which(runtime):
            runtimes[runtime] = {'path': executable}
    if runtimes:
        options['js_runtimes'] = runtimes
    return options


def resolve_audio(url, store):
    from yt_dlp import YoutubeDL
    from yt_dlp.cookies import YoutubeDLCookieJar
    options = extractor_options()
    saved = cookie_jar(store)
    for attempt in range(2):
        # Fresh extractor state, but the same authorized account on each attempt.
        with YoutubeDL(options) as ydl:
            if saved is not None:
                jar = YoutubeDLCookieJar()
                for cookie in saved:
                    jar.set_cookie(cookie)
                ydl.cookiejar = jar
            try:
                info = ydl.extract_info(url, download=False)
                return info
            except Exception as exc:
                reload_error = 'page needs to be reloaded' in str(exc).lower()
                if reload_error and attempt == 0:
                    options = dict(options, extractor_args={'youtube': {
                        'player_client': ['tv', 'web'], 'player_skip': ['webpage']}})
                    continue
                if reload_error:
                    raise RuntimeError('YouTube rejected both playback requests. Check this song in the sign-in window and refresh the session with musik login. No reset is needed.') from None
                if 'js_runtimes' not in options and any(word in str(exc).lower() for word in ('javascript', 'signature', 'challenge', 'requested format')):
                    raise RuntimeError('YouTube playback needs a JavaScript runtime. On Nobara run sudo dnf install nodejs (Node 22+), then musik stop and reopen Musik.') from None
                raise
