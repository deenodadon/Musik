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
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Extractor)
    diagnostics.doctor(Store(tmp_path), 'https://www.youtube.com/watch?v=test')
    output = capsys.readouterr().out
    assert f'Audio formats available: {count}' in output
    assert 'Background player: test-engine' in output
    assert 'YouTube requires a playback token' in output
    assert 'secret' not in output and 'private-stream' not in output
