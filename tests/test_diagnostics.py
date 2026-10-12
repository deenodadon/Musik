import pytest
from musik import diagnostics
from musik.core import Store


@pytest.mark.parametrize('formats,count', [([], 0), ([{'url': 'secret-stream', 'acodec': 'opus'}], 1)])
def test_diagnostics_reports_audio_and_sanitizes_warnings(tmp_path, monkeypatch, capsys, formats, count):
    import yt_dlp
    from test_auth import browser_jar
    async def status(*args):
        return {'version': 'test-engine'}
    monkeypatch.setattr(diagnostics, 'request', status)
    monkeypatch.setattr(diagnostics, 'cookie_jar', lambda store: browser_jar())
    monkeypatch.setattr(diagnostics, 'probe_stream', lambda info: ['Audio server probe: HTTP 200'])
    class Extractor:
        def __init__(self, options):
            self.logger = options['logger']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False, process=True):
            assert process is False
            assert list(self.cookiejar)
            self.logger.warning('PO token needed; secret-cookie-value https://private-stream')
            return {'formats': formats}
        def process_ie_result(self, data, download=False):
            return {'url': 'private-stream'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    diagnostics.doctor(Store(tmp_path), 'https://www.youtube.com/watch?v=test')
    output = capsys.readouterr().out
    assert f'Audio formats available: {count}' in output
    assert 'Background player: test-engine' in output
    assert 'YouTube requires a playback token' in output
    assert 'secret' not in output and 'private-stream' not in output


def test_audio_probe_reports_denial_without_sending_cookies_or_printing_urls(monkeypatch):
    import requests
    calls = []
    class Response:
        status_code = 403
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(requests, 'get', get)
    labels = diagnostics.probe_stream({'url': 'https://example.org/audio?pot=private-token',
        'protocol': 'https', 'http_headers': {'Cookie': 'private-cookie', 'User-Agent': 'agent'}})
    assert 'Audio server probe: HTTP 403' in labels
    assert 'Playback token in stream URL: present' in labels
    assert 'Cookie' not in calls[0]['headers']
    assert 'private-token' not in str(labels) and 'private-cookie' not in str(labels)


def test_probe_checks_hls_segment_after_successful_manifest(monkeypatch):
    import io
    import requests
    results = [(200, b'#EXTM3U\n#EXTINF:4\nsegment.aac\n'), (403, b'')]
    calls = []
    class Response:
        def __init__(self, url, status, data):
            self.url, self.status_code, self.raw = url, status, io.BytesIO(data)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def get(url, **kwargs):
        calls.append(url)
        return Response(url, *results.pop(0))
    monkeypatch.setattr(requests, 'get', get)
    labels = diagnostics.probe_stream({'url': 'https://example.org/audio/list.m3u8', 'protocol': 'm3u8_native'})
    assert 'Audio server probe: HTTP 200' in labels
    assert 'Audio segment probe: HTTP 403' in labels
    assert calls[-1] == 'https://example.org/audio/segment.aac'
