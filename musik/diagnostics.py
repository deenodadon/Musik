"""Read-only playback diagnostics; never print credentials or stream URLs."""
import asyncio
from importlib.metadata import version

from .auth import cookie_jar, has_playback_login, QuietLogger
from .core import Store
from .daemon import request
from .stream import extractor_options, configure_audio_options


class DiagnosticLogger(QuietLogger):
    def __init__(self):
        self.notes = set()

    def debug(self, message, **kwargs):
        text = str(message).lower()
        if 'generating a' in text and 'po token' in text:
            self.notes.add('Playback-token generation attempted')
        if ('bgutil:script' in text or '_get_pot_via_script' in text) and any(word in text for word in ('failed', 'timeout', 'unable to run', 'did not respond')):
            self.notes.add('Playback-token provider reported a failure')

    info = debug

    def warning(self, message):
        self.debug(message)
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


def probe_stream(info):
    """Read at most 16 KiB per request; return safe status labels only."""
    import requests
    from urllib.parse import urlparse, urljoin, parse_qs
    url = info.get('url')
    if not url:
        return ['Selected stream: unavailable']
    protocol = str(info.get('protocol') or urlparse(url).scheme)
    labels = ['Selected stream: HLS' if protocol.startswith('m3u8') else 'Selected stream: direct audio']
    parsed = urlparse(url)
    attached = 'pot' in parse_qs(parsed.query) or '/pot/' in parsed.path
    labels.append('Playback token in stream URL: ' + ('present' if attached else 'not visible'))
    headers = {key: value for key, value in (info.get('http_headers') or {}).items()
               if key.lower() in ('user-agent', 'referer', 'origin')}
    headers['Range'] = 'bytes=0-1023'
    for depth in range(3):
        try:
            with requests.get(url, headers=headers, timeout=15, stream=True) as response:
                labels.append(('Audio server probe' if depth == 0 else 'Audio segment probe') + f': HTTP {response.status_code}')
                if response.status_code not in (200, 206):
                    break
                if not protocol.startswith('m3u8'):
                    break
                raw = response.raw.read(16384)
                content = raw.decode('utf-8', errors='replace')
                if len(raw) == 16384 and not content.endswith('\n'):
                    content = content.rsplit('\n', 1)[0]
                if not content.lstrip().startswith('#EXTM3U'):
                    break
                next_url = next((line.strip() for line in content.splitlines()
                                 if line.strip() and not line.startswith('#')), None)
                if not next_url:
                    break
                url = urljoin(response.url, next_url)
        except requests.RequestException:
            labels.append('Audio server probe: network request failed')
            break
    return labels


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
    options = configure_audio_options(extractor_options(), authenticated=saved is not None)
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
            if formats:
                selected = ydl.process_ie_result(data, download=False)
                labels = probe_stream(selected)
                for label in labels:
                    print(label)
                if any('HTTP 403' in label for label in labels):
                    print('Checking browser-style alternate audio stream…')
                    from .stream import resolve_audio
                    try:
                        alternate = resolve_audio(url, store, alternate_audio=True,
                                                  rejected_format=selected.get('format_id'))
                        for label in probe_stream(alternate):
                            print('Alternate ' + label)
                    except Exception:
                        print('Alternate audio stream: unavailable')
    except Exception as error:
        message = str(error).lower()
        category = ('age/account access denied' if any(text in message for text in ('age-restricted', 'confirm your age'))
                    else 'network request failed' if any(text in message for text in ('proxy', 'connection', 'timed out'))
                    else 'YouTube extraction failed')
        print(f'Audio check: {category} ({type(error).__name__})')
    for note in sorted(logger.notes):
        print('YouTube notice: ' + note)
