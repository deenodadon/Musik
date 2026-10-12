import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from musik import nuclear
from musik.core import Store, Track, State
from musik.service import MusicService
from musik.player import Player


def test_registry_can_inspect_any_indexed_plugin(monkeypatch, capsys, tmp_path):
    entries = [{'id': 'provider', 'name': 'Provider', 'repo': 'owner/provider'},
               {'id': 'effect', 'name': 'Effect', 'repo': 'owner/effect'}]
    monkeypatch.setattr(nuclear, 'registry', lambda: entries)
    nuclear.list_plugins('effect', Store(tmp_path))
    output = capsys.readouterr().out
    assert 'https://github.com/owner/effect' in output
    assert 'https://github.com/owner/provider' not in output
    assert 'musik plugins --install' in output
    with pytest.raises(ValueError, match='not found'):
        nuclear.list_plugins('missing', Store(tmp_path))


@pytest.mark.parametrize('filename', ['../outside.js', '/outside.js', '..\\outside.js', 'C:/outside.js'])
def test_archive_rejects_paths_outside_plugin(tmp_path, filename):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(filename, 'bad')
    with pytest.raises(ValueError, match='unsafe path'):
        nuclear.unpack_release(data.getvalue(), tmp_path/'plugin')
    assert not (tmp_path/'outside.js').exists()


def plugin_store(tmp_path):
    store = Store(tmp_path)
    root = nuclear.plugin_root(store)
    plugin = root/'test-provider'
    plugin.mkdir()
    (plugin/'plugin.mjs').write_text('''
    export default {
      async onEnable(api) {
        await api.Settings.register([{id:'prefix',title:'Prefix',kind:'string',default:'Test'}]);
        api.Providers.register({id:'test-metadata',kind:'metadata',name:'Test',streamingProviderId:'test-stream',
          searchTracks: async ({query}) => [{title:await api.Settings.get('prefix')+' '+query,artists:[{name:'Artist'}],source:{provider:'test-metadata',id:'chosen'}}],
          searchArtists: async()=>[{name:'Artist',source:{provider:'test-metadata',id:'artist'}}],
          searchAlbums: async()=>[{title:'Album',source:{provider:'test-metadata',id:'album'}}],
          fetchArtistAlbums: async()=>[{title:'Album',source:{provider:'test-metadata',id:'album'}}],
          fetchAlbumDetails: async()=>({tracks:[{title:'Album song',artists:[{name:'Artist'}],source:{provider:'test-stream',id:'chosen'}}]})
        });
        api.Providers.register({id:'test-stream',kind:'streaming',name:'Test',
          getStreamUrl: async id=>({url:'https://audio.example/'+id,durationMs:120000}),
          searchForTrack: async (artist,title)=>[{id:'matched',source:{provider:'test-stream',id:'matched'}}]
        });
        api.Providers.register({id:'test-lyrics',kind:'lyrics',name:'Lyrics',getLyrics:async()=>({type:'plain',sections:[{lines:[{segments:[{text:'Hello '},{text:'world'}]}]}]})});
        api.Providers.register({id:'test-playlists',kind:'playlists',name:'Playlists',matchesUrl:url=>url.startsWith('https://playlist.example/'),fetchPlaylistByUrl:async()=>({items:[{track:{title:'Playlist song',source:{provider:'test-stream',id:'playlist-song'}}}]})});
        api.Providers.register({id:'test-dashboard',kind:'dashboard',name:'Charts',fetchTopTracks:async()=>[{title:'Chart song',source:{provider:'test-stream',id:'chart-song'}}]});
      },
      onDisable(api) {api.Providers.unregister('test-metadata');api.Providers.unregister('test-stream');}
    };
    ''',encoding='utf-8')
    nuclear.save_configuration(store, {'installed':{'test':{'path':'test-provider','enabled':True,'version':'1','name':'Test'}},'active':{'metadata':'test-metadata','streaming':'test-stream','lyrics':'test-lyrics','playlists':'test-playlists','dashboard':'test-dashboard'}})
    return store


@pytest.mark.skipif(not shutil.which('node'),reason='Node is required for plugin execution')
async def test_plugin_search_browse_lyrics_and_stream_reaches_player(tmp_path):
    store = plugin_store(tmp_path)
    service, player = MusicService(store), Player(store=store)
    commands = []
    async def command(*args):
        commands.append(args)
    player.command = command
    try:
        rows = service.search('query')
        song = next(t for t in rows if nuclear.item_kind(t)=='song')
        assert song.title == 'Test query'
        assert song.source == 'nuclear'
        album = next(t for t in rows if nuclear.item_kind(t)=='album')
        assert service.album_tracks(album)[0].title == 'Album song'
        artist = next(t for t in rows if nuclear.item_kind(t)=='artist')
        assert service.artist_catalog(artist)[0].title == 'Album'
        assert service.lyrics(song) == 'Hello world'
        assert service.playlist_tracks(service.search('https://playlist.example/1')[0])[0].title == 'Playlist song'
        assert service.plugin_dashboard()[0].title == 'Chart song'
        await player.play(song,State())
        assert ('loadfile','https://audio.example/chosen','replace') in commands
        assert player.duration == 120
        service.nuclear.call('setting',plugin='test',id='prefix',value='Changed')
        assert service.search('again')[0].title == 'Changed again'
        service.nuclear.close()
        assert service.search('persisted')[0].title == 'Changed persisted'
        nuclear.set_enabled('test',False,store)
        assert not service.nuclear.status()['providers']
    finally:
        service.nuclear.close()
        player.nuclear.close()


@pytest.mark.skipif(not shutil.which('node'),reason='Node is required for plugin execution')
def test_host_callbacks_return_real_http_response_and_lifecycle_failures(tmp_path, monkeypatch):
    store = plugin_store(tmp_path)
    main = tmp_path/'plugins/test-provider/plugin.mjs'
    main.write_text('''export default {async onEnable(api) {
      const response=await api.Http.fetch('https://example.org/data');
      const data=await response.json();
      api.Providers.register({id:'http',kind:'metadata',name:data.name});
    }};''')
    class Response:
        status_code = 200
        headers = {'Content-Encoding':'gzip','Content-Length':'999'}
        text = '{"name":"Fetched through Python"}'
    monkeypatch.setattr(nuclear.requests,'request',lambda *a,**kw: Response())
    runtime = nuclear.NuclearRuntime(store)
    try:
        assert runtime.status()['providers'][0]['name']=='Fetched through Python'
        runtime.close()
        main.write_text('''export default {onEnable(api) {
          api.Providers.register({id:'partial',kind:'metadata',name:'Partial'});
          api.Settings.registerWidget('widget',()=>null);
        }};''')
        report = runtime.status()
        assert not report['providers']
        assert 'terminal adapter' in report['failures']['test']
    finally:
        runtime.close()


def test_plugin_playlists_are_persistent_and_do_not_need_youtube_login(tmp_path):
    store = Store(tmp_path)
    runtime = nuclear.NuclearRuntime(store)
    try:
        identity = runtime._playlist_callback('createPlaylist',['My songs'])
        added = runtime._playlist_callback('addTracks',[identity,[{'title':'A','durationMs':1000}]])
        assert runtime._playlist_callback('getIndex',[])[0]['itemCount']==1
        assert runtime._playlist_callback('getPlaylist',[identity])['items'][0]['track']['title']=='A'
        runtime._playlist_callback('removeTracks',[identity,[added[0]['id']]])
        assert runtime._playlist_callback('getIndex',[])[0]['itemCount']==0
    finally:
        runtime.close()


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for plugin execution')
async def test_plugin_menu_opens_selects_provider_and_closes(tmp_path, monkeypatch):
    from musik.app import Musik
    from musik.plugin_screen import PluginScreen
    from textual.widgets import Select
    from test_app import FakePlayer
    store = plugin_store(tmp_path)
    monkeypatch.setattr('musik.plugin_screen.registry',lambda:[{'id':'test','name':'Test','categories':['metadata','streaming'],'description':'Fixture'}])
    service = MusicService(store)
    app = Musik(store=store,service=service,player=FakePlayer())
    try:
        async with app.run_test(size=(110,45)) as pilot:
            await pilot.press('f10')
            for _ in range(40):
                if isinstance(app.screen,PluginScreen) and app.screen.query_one('#plugin-list').row_count:
                    break
                await pilot.pause(.02)
            assert isinstance(app.screen,PluginScreen)
            assert app.screen.query_one('#plugin-list').row_count==1
            app.screen.query_one('#plugin-provider',Select).value='streaming=builtin'
            await pilot.click('#plugin-use')
            await pilot.pause(.2)
            assert nuclear.configuration(store)['active']['streaming']=='builtin'
            await pilot.press('escape')
            assert not isinstance(app.screen,PluginScreen)
    finally:
        service.nuclear.close()


@pytest.mark.skipif(not shutil.which('node'),reason='Node is required for plugin execution')
def test_external_plugin_setting_changes_refresh_a_running_host(tmp_path):
    store=plugin_store(tmp_path)
    service=MusicService(store)
    other=nuclear.NuclearRuntime(store)
    try:
        assert service.search('song')[0].title=='Test song'
        other.call('setting',plugin='test',id='prefix',value='Updated externally')
        assert service.search('song')[0].title=='Updated externally song'
    finally:
        other.close()
        service.nuclear.close()


@pytest.mark.skipif(not (shutil.which('node') and (__import__('os').environ.get('MUSIK_MPV') or shutil.which('mpv'))),reason='Node and mpv are required')
async def test_resolved_plugin_audio_actually_plays_in_mpv(tmp_path):
    import asyncio
    import functools
    import http.server
    import threading
    import wave
    import struct
    store=plugin_store(tmp_path/'state')
    audio=tmp_path/'tone.wav'
    with wave.open(str(audio),'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(struct.pack('<h',1000)*64000)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(tmp_path)))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    main=tmp_path/'state/plugins/test-provider/plugin.mjs'
    main.write_text(main.read_text().replace("'https://audio.example/'+id",repr(f'http://127.0.0.1:{server.server_port}/tone.wav')).replace('durationMs:120000','durationMs:4000'))
    service=MusicService(store)
    player=Player(store=store,audio_output='null')
    try:
        song=service.search('tone')[0]
        await player.start()
        await player.play(song,State())
        async with asyncio.timeout(5):
            while player.position<=0:
                await asyncio.sleep(.05)
        assert player.duration==pytest.approx(4)
        assert not player.idle
        assert player.failure_detail is None
    finally:
        await player.close()
        service.nuclear.close()
        server.shutdown()
        server.server_close()


async def test_metadata_only_audio_keeps_builtin_alternate_stream_retry(tmp_path,monkeypatch):
    store=Store(tmp_path)
    player=Player(store=store)
    track=nuclear.convert_item({'title':'Song','artists':[{'name':'Artist'}],'source':{'provider':'metadata-only','id':'123'}},'song','metadata-only')
    youtube=Track('abcdefghijk','Song')
    monkeypatch.setattr(MusicService,'search_videos',lambda self,query:[youtube])
    calls=[]
    def resolve(url,store,**kwargs):
        calls.append((url,kwargs))
        return {'url':'https://audio.example/alternate' if kwargs else 'https://audio.example/original','duration':120,'format_id':'audio'}
    monkeypatch.setattr('musik.stream.resolve_audio',resolve)
    async def command(*args):pass
    player.command=command
    try:
        await player.play(track,State())
        assert player.online_stream
        assert player.youtube_url==youtube.url
        await player.play(track,State(),retry_stream=True)
        assert calls==[(youtube.url,{}),(youtube.url,{'alternate_audio':True,'rejected_format':'audio'})]
    finally:
        await player.close()


def test_plugin_checksum_is_verified_before_building(tmp_path,monkeypatch):
    import hashlib
    entry={'id':'test','name':'Test','repo':'owner/repo','downloadUrl':'https://github.com/owner/repo/releases/download/v1/plugin.zip'}
    monkeypatch.setattr(nuclear,'registry',lambda:[entry])
    monkeypatch.setattr(nuclear,'node_tools',lambda:('node',Path('/unused/npm.js')))
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'assets':[{'name':'plugin.zip','digest':'sha256:'+hashlib.sha256(b'expected').hexdigest()}]}
        def iter_content(self,*args):yield b'changed archive'
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(nuclear.requests,'get',lambda *a,**kw:Response())
    with pytest.raises(ValueError,match='checksum'):
        nuclear.install_plugin('test',Store(tmp_path),progress=lambda text:None)
    assert not nuclear.configuration(Store(tmp_path))['installed']


def test_settings_updates_from_multiple_hosts_do_not_overwrite_each_other(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Store(tmp_path)
    hosts=[nuclear.NuclearRuntime(store),nuclear.NuclearRuntime(store)]
    try:
        def update(index):
            host=hosts[index%2]
            host._settings_callback('set',['test',str(index),index])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(update,range(30)))
        values=json.loads((tmp_path/'plugins/settings.json').read_text())
        assert len(values)==30
        assert values['test:29']==29
    finally:
        for host in hosts:host.close()


def test_plugin_queue_ids_survive_reordering_and_control_routes_correctly(tmp_path,monkeypatch):
    from dataclasses import asdict
    runtime=nuclear.NuclearRuntime(Store(tmp_path))
    tracks=[Track('abcdefghijk','A'),Track('lmnopqrstuv','B')]
    state=State(queue=tracks,index=0)
    monkeypatch.setattr(runtime,'_player_snapshot',lambda:{'state':asdict(state),'idle':False,'paused':False,'position':1,'duration':120})
    operations=[]
    monkeypatch.setattr(runtime,'_player_request',lambda operation,**args:operations.append((operation,args)))
    try:
        identity=runtime._control_callback('Queue.getQueue',[])['items'][0]['id']
        state.queue.reverse()
        runtime._control_callback('Queue.goToId',[identity])
        assert operations[-1]==('play',{'index':1})
        runtime._control_callback('Queue.removeByIds',[[identity]])
        assert operations[-1]==('plugin-queue',{'action':'remove','indices':[1]})
        runtime._control_callback('Playback.setVolume',[.8])
        assert operations[-1][0]=='settings'
        assert operations[-1][1]['volume']==80
    finally:runtime.close()


@pytest.mark.skipif(not shutil.which('node'),reason='Node is required for plugin execution')
async def test_enter_in_plugin_settings_never_submits_a_music_search(tmp_path,monkeypatch):
    from musik.app import Musik
    from musik.plugin_screen import PluginScreen
    from textual.widgets import Input
    from test_app import FakePlayer
    store=plugin_store(tmp_path)
    service=MusicService(store)
    searches=[]
    monkeypatch.setattr(service,'search',lambda query:searches.append(query) or [])
    monkeypatch.setattr(service,'search_videos',lambda query:searches.append(query) or [])
    monkeypatch.setattr('musik.plugin_screen.registry',lambda:[{'id':'test','name':'Test','categories':['metadata'],'description':'Fixture'}])
    app=Musik(store=store,service=service,player=FakePlayer())
    try:
        async with app.run_test(size=(110,45)) as pilot:
            await pilot.press('f10')
            for _ in range(40):
                if isinstance(app.screen,PluginScreen) and app.screen.query_one('#plugin-list').row_count:
                    break
                await pilot.pause(.02)
            value=app.screen.query_one('#plugin-setting-value',Input)
            value.value='private-setting-value'
            value.focus()
            await pilot.press('enter')
            await pilot.pause(.1)
            assert searches==[]
    finally:service.nuclear.close()
