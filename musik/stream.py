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
    base = extractor_options()
    saved = cookie_jar(store)
    alternate = dict(base, extractor_args={'youtube': {
        'player_client': ['tv', 'web'], 'player_skip': ['webpage']}})
    routes = [(saved, base), (saved, alternate)]
    if saved is not None:
        # A public stream does not require account cookies. Only a session/page
        # reload failure reaches this route; account access denials remain errors.
        routes.append((None, base))
    for index, (session, options) in enumerate(routes):
        with YoutubeDL(options) as ydl:
            if session is not None:
                jar = YoutubeDLCookieJar()
                for cookie in session:
                    jar.set_cookie(cookie)
                ydl.cookiejar = jar
            try:
                info = ydl.extract_info(url, download=False)
                if saved is not None and session is None:
                    info['_musik_public_fallback'] = True
                return info
            except Exception as exc:
                message = str(exc).lower()
                reload_error = 'page needs to be reloaded' in message
                if reload_error and index + 1 < len(routes):
                    continue
                if reload_error:
                    raise RuntimeError('YouTube rejected playback, including the public-stream retry. Try this song in YouTube Music; restricted songs require an eligible account session.') from None
                if 'js_runtimes' not in options and any(word in message for word in ('javascript', 'signature', 'challenge', 'requested format')):
                    raise RuntimeError('YouTube playback needs a JavaScript runtime. On Nobara run sudo dnf install nodejs (Node 22+), then musik stop and reopen Musik.') from None
                raise
