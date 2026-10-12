import pytest
from textual.widgets import Input, Tabs
from musik.app import Musik
from musik.core import Store, Track
from musik.service import MusicService
from test_app import FakePlayer


def test_youtube_search_keeps_alternate_versions_and_exact_links(tmp_path, monkeypatch):
    targets = []
    class Extractor:
        def __init__(self, options):
            assert options['extract_flat'] and options['skip_download']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, target, download=False):
            targets.append(target)
            entry = {'id': 'TL4kq18ibjM', 'title': 'Alternate version', 'channel': 'Cover artist'}
            return {'entries': [entry, entry, None, {'id': 'invalid'}]} if target.startswith('ytsearch') else entry
    monkeypatch.setattr('yt_dlp.YoutubeDL', Extractor)
    service = MusicService(Store(tmp_path))
    assert service.search_videos('song remix') == [Track('TL4kq18ibjM', 'Alternate version', 'Cover artist', 'video')]
    for link in ('https://www.youtube.com/watch?v=TL4kq18ibjM&list=ignored',
                 'https://youtu.be/TL4kq18ibjM', 'https://www.youtube.com/shorts/TL4kq18ibjM'):
        assert service.search_videos(link)[0].url == 'https://www.youtube.com/watch?v=TL4kq18ibjM'
    assert targets[0] == 'ytsearch50:song remix'
    assert all(target == 'https://www.youtube.com/watch?v=TL4kq18ibjM' for target in targets[1:])
    with pytest.raises(ValueError):
        service.search_videos('https://example.com/watch?v=TL4kq18ibjM')


async def test_video_tab_play_and_direct_link_without_music_catalog(tmp_path):
    class Service:
        def search(self, query):
            raise RuntimeError('Music catalog unavailable')
        def search_videos(self, query):
            return [Track('TL4kq18ibjM', 'Alternate version', 'Channel', 'video')]
    player = FakePlayer()
    app = Musik(store=Store(tmp_path), service=Service(), player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one(Input).value = 'song remix'
        await pilot.press('enter')
        await pilot.pause()
        assert app.results_section == 'videos'
        assert str(app.query_one("#result-sections", Tabs).get_tab('section-videos').label) == 'Videos (1)'
        await pilot.press('enter')
        await pilot.pause()
        assert player.played[-1].id == 'TL4kq18ibjM'
        app.query_one(Input).value = 'https://www.youtube.com/watch?v=TL4kq18ibjM'
        app.query_one(Input).focus()
        await pilot.press('enter')
        await pilot.pause()
        assert app.tracks[0].source == 'video'
