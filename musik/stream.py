"""Resolve YouTube audio with the installed challenge solver and real session."""
import os
import shutil
from pathlib import Path

from .auth import cookie_jar, QuietLogger, has_playback_login
from .platform import cache_home


def extractor_options():
    options = {'format': 'bestaudio/best[acodec!=none]', 'quiet': True, 'no_warnings': True,
               'logger': QuietLogger(), 'socket_timeout': 20, 'noplaylist': True,
               'cachedir': str(Path(os.environ.get('XDG_CACHE_HOME', str(cache_home()))) / 'musik' / 'yt-dlp')}
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
    login_info = has_playback_login(saved) if saved is not None else False
    age_retry_used = False
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
                age_error = any(text in message for text in ('age-restricted', 'confirm your age', 'age verification', 'age-verification'))
                if age_error:
                    if saved is None or not login_info:
                        raise RuntimeError("YouTube playback did not recognize a complete signed-in session. Run musik login again, sign in on YouTube with your age-verified account, then close the sign-in window. Library access alone does not confirm playback authentication.") from None
                    if not age_retry_used:
                        age_retry_used = True
                        # Retry ordinary signed-in web clients with the same
                        # account cookies. Never retry this denial anonymously.
                        routes.insert(index + 1, (saved, dict(base, extractor_args={'youtube': {
                            'player_client': ['web', 'mweb']}})))
                        continue
                    raise RuntimeError("YouTube still denied age-restricted playback with your saved account session. Confirm this exact video plays while signed in on youtube.com, then refresh Musik's login. The session may have expired or YouTube may require additional account verification.") from None
                format_error = 'requested format is not available' in message
                if format_error and index + 1 < len(routes) and routes[index + 1][0] is session:
                    continue
                if format_error and 'js_runtimes' in options:
                    raise RuntimeError("YouTube did not provide a playable audio stream after retrying supported clients with your session. Try another upload of this song; YouTube may require a playback token that Musik cannot obtain automatically.") from None
                reload_error = 'page needs to be reloaded' in message
                if reload_error and index + 1 < len(routes) and (not age_retry_used or routes[index + 1][0] is not None):
                    continue
                if reload_error:
                    raise RuntimeError('YouTube rejected playback, including the public-stream retry. Try this song in YouTube Music; restricted songs require an eligible account session.') from None
                if 'js_runtimes' not in options and any(word in message for word in ('javascript', 'signature', 'challenge', 'requested format')):
                    raise RuntimeError('YouTube playback needs a JavaScript runtime. On Nobara run sudo dnf install nodejs (Node 22+), then musik stop and reopen Musik.') from None
                raise
