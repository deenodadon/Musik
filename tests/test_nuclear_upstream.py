"""Optional contract checks using actual, separately installed registry releases."""
import html
import json
import os
from pathlib import Path
import pytest
from musik.core import Store
from musik.service import MusicService
from musik.player import Player
from musik.nuclear import item_kind

pytestmark = pytest.mark.skipif(not os.environ.get('MUSIK_NUCLEAR_TEST_STORE'),reason='Real registry releases are not installed in this test environment')


def test_official_bandcamp_search_album_and_audio(monkeypatch):
    service = MusicService(Store(Path(os.environ['MUSIK_NUCLEAR_TEST_STORE'])))
    original = service.nuclear._callback
    def callback(method,args):
        if method != 'http':
            return original(method,args)
        url,init = args
        if 'search' in url:
            kind = json.loads(init['body'])['search_filter']
            result = {'type':kind,'id':1,'name':'Fixture '+kind,'band_name':'Fixture Artist','item_url_root':'https://fixture.bandcamp.com','item_url_path':'https://fixture.bandcamp.com/'+('track/song' if kind=='t' else 'album/album' if kind=='a' else '')}
            return {'status':200,'headers':{},'body':json.dumps({'auto':{'results':[result]}})}
        tralbum = {'trackinfo':[{'title':'Song','track_num':1,'duration':120,'title_link':'/track/song','file':{'mp3-128':'https://audio.example/song.mp3'}}]}
        ld = {'name':'Fixture Album','byArtist':{'name':'Fixture Artist','url':'https://fixture.bandcamp.com'},'track':{'itemListElement':[{'position':1,'item':{'name':'Song','url':'https://fixture.bandcamp.com/track/song'}}]}}
        return {'status':200,'headers':{},'body':'<script data-tralbum="'+html.escape(json.dumps(tralbum),quote=True)+'"></script><script type="application/ld+json">'+json.dumps(ld)+'</script>'}
    monkeypatch.setattr(service.nuclear,'_callback',callback)
    player = Player(store=service.store)
    player.nuclear = service.nuclear
    try:
        rows = service.search('fixture')
        assert {item_kind(t) for t in rows}=={'song','artist','album'}
        song = next(t for t in rows if item_kind(t)=='song')
        assert player.resolve_track(song)['url']=='https://audio.example/song.mp3'
        album = next(t for t in rows if item_kind(t)=='album')
        assert len(service.album_tracks(album))==1
    finally:
        service.nuclear.close()


def test_official_lrclib_lyrics(monkeypatch):
    from musik.nuclear import NuclearRuntime
    runtime = NuclearRuntime(Store(Path(os.environ['MUSIK_NUCLEAR_TEST_STORE'])))
    original = runtime._callback
    def callback(method,args):
        if method != 'http':return original(method,args)
        return {'status':200,'headers':{},'body':json.dumps([{'id':1,'trackName':'Song','artistName':'Artist','duration':120,'plainLyrics':'Hello world','syncedLyrics':None,'instrumental':False}])}
    monkeypatch.setattr(runtime,'_callback',callback)
    try:
        result = runtime.call('lyrics',provider='lrclib',track={'title':'Song','artists':[{'name':'Artist'}],'durationMs':120000,'source':{'provider':'youtube','id':'song'}})
        assert result[0]['lyrics']['sections'][0]['lines'][0]['segments'][0]['text']=='Hello world'
    finally:runtime.close()


def test_official_youtube_uses_musik_ytdlp_bridge(monkeypatch):
    from musik.nuclear import NuclearRuntime
    runtime = NuclearRuntime(Store(Path(os.environ['MUSIK_NUCLEAR_TEST_STORE'])))
    calls=[]
    original=runtime._callback
    def callback(method,args):
        calls.append(method)
        if method=='ytdlp.search':return [{'id':'chosen','title':'Chosen song','duration':120,'thumbnail':None,'channel':'Artist'}]
        if method=='ytdlp.getStream':return {'stream_url':'https://audio.example/chosen','duration':120,'title':'Chosen song','container':'m4a','codec':'aac'}
        return original(method,args)
    monkeypatch.setattr(runtime,'_callback',callback)
    try:
        info=runtime.call('stream',provider='youtube',track={'title':'Chosen song','artists':[{'name':'Artist'}],'source':{'provider':'other','id':'unknown'}})
        assert info['url']=='https://audio.example/chosen'
        assert calls.count('ytdlp.search')==2
        assert 'ytdlp.getStream' in calls
    finally:runtime.close()
