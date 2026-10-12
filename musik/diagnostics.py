"""Read-only playback diagnostics; never print credentials or stream URLs."""
import asyncio
from importlib.metadata import version

from .auth import cookie_jar, has_playback_login, QuietLogger
from .core import Store
from .daemon import request
from .stream import extractor_options


class DiagnosticLogger(QuietLogger):
    def __init__(self):
        self.notes = set()

    def warning(self, message):
        message = str(message).lower()
        for text, label in (
            ('po token', 'YouTube requires a playback token'),
            ('javascript', 'JavaScript runtime/challenge problem'),
            ('signature', 'YouTube signature challenge failed'),
            ('n challenge', 'YouTube stream challenge failed'),
            ('cookies', 'YouTube reported a session/cookie problem'),
        ):
            if text in message:
                self.notes.add(label)


def doctor(store=None, url=None):
    from yt_dlp import YoutubeDL
    from yt_dlp.cookies import YoutubeDLCookieJar
    store = store or Store()
    print(f"Musik installed: {version('musik-tui')}")
    try:
        response = asyncio.run(request(store, 'status'))
        engine = response.get('version', 'older version (restart required)')
        print(f"Background player: {engine}")
        if response.get('playback_error'):
            print('Last playback error: ' + response['playback_error'])
    except (OSError, RuntimeError, ValueError):
        print('Background player: stopped')
    saved = cookie_jar(store)
    print('Playback session: ' + ('complete' if saved and has_playback_login(saved) else 'missing or incomplete'))
    from .playback_setup import helper_ready
    print('Playback-token helper: ' + ('ready' if helper_ready() else 'missing; run musik setup-playback'))
    options = extractor_options()
    print('JavaScript runtimes: ' + (', '.join(options.get('js_runtimes', {})) or 'none'))
    track = store.load().current
    url = url or (track.url if track and track.source != 'local' else None)
    if not url:
        print('Select an online song first, or run musik doctor --url YOUTUBE_LINK.')
        return
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('youtube.com', 'www.youtube.com', 'music.youtube.com', 'm.youtube.com', 'youtu.be'):
        raise ValueError('Diagnostics require an HTTPS YouTube link.')
    logger = DiagnosticLogger()
    options.update(logger=logger, skip_download=True)
    print('Checking available audio streams…')
    try:
        with YoutubeDL(options) as ydl:
            if saved:
                jar = YoutubeDLCookieJar()
                for cookie in saved:
                    jar.set_cookie(cookie)
                ydl.cookiejar = jar
            data = ydl.extract_info(url, download=False, process=False)
        formats = [item for item in (data or {}).get('formats', [])
                   if item.get('url') and item.get('acodec') not in (None, 'none')]
        print(f'Audio formats available: {len(formats)}')
    except Exception as error:
        message = str(error).lower()
        category = ('age/account access denied' if any(text in message for text in ('age-restricted', 'confirm your age'))
                    else 'network request failed' if any(text in message for text in ('proxy', 'connection', 'timed out'))
                    else 'YouTube extraction failed')
        print(f'Audio check: {category} ({type(error).__name__})')
    for note in sorted(logger.notes):
        print('YouTube notice: ' + note)
