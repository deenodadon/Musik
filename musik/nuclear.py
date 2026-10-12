"""Install Nuclear releases and bridge their provider APIs through a Node host."""
import atexit
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import queue
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from .core import Store, Track
from .platform import lock_exclusive

REGISTRY_URL = 'https://raw.githubusercontent.com/NuclearPlayer/plugin-registry/master/plugins.json'
ESBUILD_VERSION = '0.25.12'
SUPPORTED_KINDS = {'metadata', 'streaming', 'lyrics', 'dashboard', 'playlists', 'discovery'}


def registry():
    response = requests.get(REGISTRY_URL, timeout=20)
    response.raise_for_status()
    entries = response.json()['plugins']
    if not isinstance(entries, list):
        raise ValueError('Invalid Nuclear plugin registry')
    return entries


def plugin_root(store):
    root = store.directory / 'plugins'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def configuration(store):
    path = store.directory / 'plugins' / 'plugins.json'
    if not path.exists():
        return {'installed': {}, 'active': {}}
    return json.loads(path.read_text(encoding='utf-8'))


def save_configuration(store, config):
    root = plugin_root(store)
    fd, temporary = tempfile.mkstemp(dir=root, prefix='.plugins-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, indent=2)
        os.replace(temporary, root / 'plugins.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_id(plugin_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', plugin_id):
        raise ValueError('Invalid plugin ID')
    return plugin_id


def node_tools():
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node or not npm:
        raise RuntimeError('Plugins require Node.js and npm. Rerun the Musik installer.')
    version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10).stdout
    major = re.match(r'v(\d+)\.', version)
    if not major or int(major[1]) < 22:
        raise RuntimeError('Plugins require Node.js 22 or newer. Rerun the Musik installer.')
    npm_path = Path(npm).resolve()
    cli = npm_path if npm_path.suffix == '.js' else npm_path.parent / 'node_modules/npm/bin/npm-cli.js'
    if not cli.is_file():
        raise RuntimeError('npm CLI could not be found. Reinstall Node.js.')
    return node, cli


def unpack_release(data, destination):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 100_000_000:
            raise ValueError('Plugin release is too large')
        for entry in entries:
            name = entry.filename.replace('\\', '/')
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()) or ':' in name or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Plugin archive contains an unsafe path')
        archive.extractall(destination)
    manifests = [p for p in destination.rglob('package.json') if 'node_modules' not in p.parts]
    if (destination / 'package.json').exists():
        return destination
    if len(manifests) != 1:
        raise ValueError('Plugin release must contain one root package.json')
    return manifests[0].parent


def install_plugin(plugin_id, store=None, progress=print):
    store = store or Store()
    valid_id(plugin_id)
    entry = next((p for p in registry() if p['id'] == plugin_id), None)
    if not entry:
        raise ValueError('Plugin ID was not found in the Nuclear registry')
    node, npm = node_tools()
    root = plugin_root(store)
    with (root / 'install.lock').open('a+') as lock:
        try:
            lock_exclusive(lock)
        except BlockingIOError:
            raise RuntimeError('Another plugin installation is running') from None
        progress(f"Downloading {entry.get('name', plugin_id)}…")
        repo = entry['repo']
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('Invalid registry repository')
        # Verify the exact indexed asset using GitHub's release digest. Never
        # replace the expected checksum with the downloaded file's checksum.
        download_url = entry['downloadUrl']
        match = re.fullmatch(r'https://github.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)', download_url)
        if not match or match[1].casefold() != repo.casefold():
            raise ValueError('Plugin download must be a release of its indexed repository')
        release = requests.get(f'https://api.github.com/repos/{repo}/releases/tags/{match[2]}', timeout=20)
        release.raise_for_status()
        asset = next((a for a in release.json()['assets'] if a['name'] == match[3]), None)
        digest = asset.get('digest', '') if asset else ''
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise RuntimeError('GitHub has no SHA-256 digest for this release; it cannot be verified')
        with requests.get(download_url, timeout=60, stream=True) as response:
            response.raise_for_status()
            pieces, size = [], 0
            for block in response.iter_content(65536):
                size += len(block)
                if size > 50_000_000:
                    raise ValueError('Plugin download is too large')
                pieces.append(block)
        data = b''.join(pieces)
        if hashlib.sha256(data).hexdigest() != digest[7:]:
            raise ValueError('Plugin release checksum did not match GitHub')
        with tempfile.TemporaryDirectory(dir=root, prefix='.install-') as temporary:
            source = unpack_release(data, Path(temporary) / 'source')
            manifest = json.loads((source / 'package.json').read_text(encoding='utf-8'))
            main = (source / manifest.get('main', 'index.js')).resolve()
            if not main.is_relative_to(source.resolve()):
                raise ValueError('Plugin entry point is outside the release')
            if not main.is_file():
                # Some registry releases declare dist/index.js but ship source.
                main = next((candidate for candidate in (source / 'src/index.ts', source / 'src/index.js', source / 'index.ts', source / 'index.js') if candidate.is_file()), main)
            if not main.is_file():
                raise ValueError('Plugin entry point is missing from the release')
            # Build a separate package: do not execute the plugin's npm scripts.
            dependencies = dict(manifest.get('dependencies', {}))
            dependencies['esbuild'] = ESBUILD_VERSION
            dependencies['linkedom'] = '0.18.12'
            build = Path(temporary) / 'build'
            build.mkdir()
            (build / 'package.json').write_text(json.dumps({'private': True, 'dependencies': dependencies}), encoding='utf-8')
            progress('Installing dependencies and compiling the plugin…')
            result = subprocess.run([node, str(npm), 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(root / 'npm-cache')], cwd=build, capture_output=True, timeout=300)
            if result.returncode:
                raise RuntimeError('Plugin dependencies could not be installed')
            # Resolve imports against the isolated production dependencies.
            env = dict(os.environ, NODE_PATH=str(build / 'node_modules'))
            bundle = build / 'plugin.mjs'
            wrapper = build / 'entry.mjs'
            wrapper.write_text("import {DOMParser} from 'linkedom'; globalThis.DOMParser ??= DOMParser;\nimport plugin from " + json.dumps(str(main)) + "; export default plugin;\n", encoding='utf-8')
            result = subprocess.run([node, str(build / 'node_modules/esbuild/bin/esbuild'), str(wrapper), '--bundle', '--platform=node', '--format=esm', '--target=node22', '--banner:js=import {createRequire} from "node:module"; const require = createRequire(import.meta.url);', f'--outfile={bundle}', '--loader:.png=dataurl', '--loader:.svg=dataurl'], env=env, cwd=source, capture_output=True, timeout=120)
            if result.returncode:
                raise RuntimeError('Plugin could not be compiled for the Node host; it may require Nuclear desktop APIs')
            (build / 'release.json').write_text(json.dumps({'entry': entry, 'manifest': manifest, 'sha256': digest[7:]}), encoding='utf-8')
            shutil.copytree(source, build / 'release')
            config = configuration(store)
            # Do not replace code underneath a running host. Each version has
            # a separate immutable directory selected by the configuration.
            version_path = root / f'{plugin_id}-{digest[7:19]}-host1'
            if not version_path.exists():
                build.rename(version_path)
            config['installed'][plugin_id] = {'path': version_path.name, 'version': entry.get('version'), 'enabled': True, 'name': entry.get('name', plugin_id)}
            save_configuration(store, config)
        progress(f'{plugin_id} installed. Checking host compatibility…')
    runtime = NuclearRuntime(store)
    try:
        status = runtime.status()
        if plugin_id in status['failures']:
            # An incompatible plugin is retained for inspection, but disabled.
            set_enabled(plugin_id, False, store)
            failed = configuration(store)
            failed['installed'][plugin_id]['error'] = status['failures'][plugin_id]
            save_configuration(store, failed)
            raise RuntimeError(f"Plugin installed but disabled: {status['failures'][plugin_id]}")
        owned = [p for p in status['providers'] if p['pluginId'] == plugin_id]
        config = configuration(store)
        config['installed'][plugin_id]['providers'] = [{k:p[k] for k in ('id','kind')} for p in owned]
        for p in owned:
            config['active'].setdefault(p['kind'], 'builtin' if p['kind'] == 'streaming' else p['id'])
        save_configuration(store, config)
        return owned
    finally:
        runtime.close()


def set_enabled(plugin_id, enabled, store=None):
    store = store or Store()
    config = configuration(store)
    if plugin_id not in config['installed']:
        raise ValueError('Plugin is not installed')
    config['installed'][plugin_id]['enabled'] = bool(enabled)
    if not enabled:
        owned = {p['id'] for p in config['installed'][plugin_id].get('providers', [])}
        for kind, identity in list(config['active'].items()):
            if identity in owned:
                config['active'][kind] = 'builtin'
    save_configuration(store, config)
    if enabled:
        runtime = NuclearRuntime(store)
        try:
            report = runtime.status()
            if plugin_id not in report['loaded']:
                error = report['failures'].get(plugin_id, 'Plugin did not load')
                set_enabled(plugin_id, False, store)
                config = configuration(store)
                config['installed'][plugin_id]['error'] = error
                save_configuration(store, config)
                raise RuntimeError(f'Plugin remains disabled: {error}')
            config = configuration(store)
            config['installed'][plugin_id].pop('error', None)
            save_configuration(store, config)
        finally:
            runtime.close()


def select_provider(kind, provider_id, store=None):
    store = store or Store()
    if kind not in SUPPORTED_KINDS:
        raise ValueError('Unsupported provider kind')
    runtime = NuclearRuntime(store)
    try:
        if provider_id != 'builtin' and not any(p['kind'] == kind and p['id'] == provider_id for p in runtime.status()['providers']):
            raise ValueError('Enable a plugin that supplies this provider first')
    finally:
        runtime.close()
    config = configuration(store)
    config['active'][kind] = provider_id
    save_configuration(store, config)


class NuclearRuntime:
    """Serialized RPC client with bounded requests and Python host callbacks."""
    def __init__(self, store=None, *, control_enabled=True):
        self.control_enabled = control_enabled
        self.store = store or Store()
        self.process = None
        self.lock = threading.RLock()
        self.messages = queue.Queue()
        self.serial = 0
        self.stamp = None
        self.report = {'providers': [], 'loaded': [], 'failures': {}, 'settings': []}

    def _start(self):
        atexit.register(self.close)
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Enabled plugins require Node.js. Rerun the Musik installer.')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.messages = queue.Queue()
        self.process = subprocess.Popen([node, str(Path(__file__).with_name('nuclear_host.mjs'))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', creationflags=flags)
        messages, process = self.messages, self.process
        def read():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                messages.put({'exited': True})
        threading.Thread(target=read, daemon=True).start()

    def _rpc(self, method, args):
        self.serial += 1
        request_id = self.serial
        self.process.stdin.write(json.dumps({'id': request_id, 'method': method, 'args': args}) + '\n')
        self.process.stdin.flush()
        import time
        deadline = time.monotonic() + 90
        while True:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise RuntimeError('Nuclear plugin timed out') from None
            if message.get('exited'):
                self.close()
                raise RuntimeError('Nuclear plugin host stopped unexpectedly')
            if 'callback' in message:
                try:
                    result = self._callback(message['method'], message['args'])
                    reply = {'callbackResult': message['callback'], 'result': result}
                except Exception as exc:
                    reply = {'callbackResult': message['callback'], 'error': f'{message["method"]} failed ({type(exc).__name__})'}
                self.process.stdin.write(json.dumps(reply) + '\n')
                self.process.stdin.flush()
            elif message.get('id') == request_id:
                if message.get('error'):
                    raise RuntimeError(message['error'])
                return message.get('result')

    def ensure(self):
        config = configuration(self.store)
        settings = self.store.directory / 'plugins/settings.json'
        def current_stamp():
            return (json.dumps(config, sort_keys=True), settings.stat().st_mtime_ns if settings.exists() else None)
        stamp = current_stamp()
        if stamp == self.stamp and (self.process is None or self.process.poll() is None):
            return
        self.close()
        entries = [{'id': plugin_id, 'path': str(plugin_root(self.store) / info['path'] / 'plugin.mjs')} for plugin_id, info in config['installed'].items() if info['enabled']]
        if entries:
            self._start()
            self.report = self._rpc('init', {'root': str(plugin_root(self.store)), 'plugins': entries, 'active': config['active']})
        else:
            self.report = {'providers': [], 'loaded': [], 'failures': {}, 'settings': []}
        self.stamp = current_stamp()

    def status(self):
        with self.lock:
            self.ensure()
            return dict(self.report, active=configuration(self.store)['active'])

    def call(self, operation, **args):
        with self.lock:
            self.ensure()
            if not self.process:
                raise RuntimeError('No Nuclear plugins are enabled')
            return self._rpc(operation, args)

    def selected(self, kind):
        provider_id = configuration(self.store)['active'].get(kind)
        return provider_id if provider_id and provider_id != 'builtin' else None

    def _callback(self, method, args):
        if method.startswith('settings.'):
            return self._settings_callback(method.split('.')[1], args)
        if method == 'http':
            url, init = args
            if urlparse(url).scheme not in ('http', 'https'):
                raise ValueError('Plugin HTTP URL must use HTTP or HTTPS')
            response = requests.request(init.get('method', 'GET'), url, headers=init.get('headers'), data=init.get('body'), timeout=25)
            # requests has already decompressed this response.
            headers = {k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
            return {'status': response.status_code, 'headers': headers, 'body': response.text}
        if method.startswith('ytdlp.'):
            from .stream import extractor_options, resolve_audio
            from yt_dlp import YoutubeDL
            action = method.split('.')[1]
            if action == 'getStream':
                url = args[0]
                if not urlparse(url).scheme:
                    url = f'https://www.youtube.com/watch?v={url}'
                info = resolve_audio(url, self.store)
                return {'stream_url': info['url'], 'duration': info.get('duration'), 'title': info.get('title'), 'container': info.get('ext'), 'codec': info.get('acodec'), 'artists': info.get('artists', []), 'album_artists': []}
            options = extractor_options(self.store)
            options.update(extract_flat=True, playlistend=min(int(args[1]) if len(args)>1 else 10, 100))
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(f'ytsearch{options["playlistend"]}:{args[0]}' if action=='search' else args[0], download=False)
            entries = [{k:item.get(k) for k in ('id','title','duration','thumbnail','channel','thumbnails')} for item in info.get('entries', []) if item]
            return entries if action=='search' else {'id':info.get('id'),'title':info.get('title'),'entries':entries}
        if method == 'shell.openExternal':
            if urlparse(args[0]).scheme not in ('https', 'http'):
                raise ValueError('Only web links can be opened')
            import webbrowser
            return webbrowser.open(args[0])
        if method.split('.')[0] in ('Queue','Playback','Playlists','Favorites'):
            return self._control_callback(method, args)
        raise RuntimeError(f'{method} needs a Musik terminal adapter')

    def _settings_callback(self, action, args):
        root = plugin_root(self.store)
        path = root / 'settings.json'
        with (root / 'settings.lock').open('a+') as lock:
            # Hosts use separate processes. Share the same private settings
            # store and never overwrite another host's unrelated updates.
            import time
            deadline = time.monotonic() + 5
            while True:
                try:
                    lock_exclusive(lock)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Plugin settings storage is busy') from None
                    time.sleep(.01)
            values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            namespace = args[0]
            if action == 'get':
                return values.get(f'{namespace}:{args[1]}')
            changed = False
            if action == 'register':
                for definition in args[1]:
                    key = f'{namespace}:{definition["id"]}'
                    if key not in values:
                        values[key] = definition.get('default')
                        changed = True
            elif action == 'set':
                values[f'{namespace}:{args[1]}'] = args[2]
                changed = True
            else:
                raise ValueError('Unsupported settings operation')
            if changed:
                fd, temporary = tempfile.mkstemp(dir=root, prefix='.settings-')
                try:
                    with os.fdopen(fd,'w',encoding='utf-8') as handle:
                        json.dump(values,handle)
                    os.replace(temporary,path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            return None

    def _player_snapshot(self):
        import asyncio
        from .daemon import request
        try:
            return asyncio.run(request(self.store, 'status'))
        except (FileNotFoundError, ConnectionRefusedError):
            from dataclasses import asdict
            return {'state': asdict(self.store.load()), 'idle': True, 'paused': False, 'position': 0, 'duration': 0}

    def _player_request(self, operation, **arguments):
        import asyncio
        from .daemon import request
        return asyncio.run(request(self.store, operation, **arguments))

    def _playlist_callback(self, action, args):
        import uuid
        from datetime import datetime, timezone
        root = plugin_root(self.store)
        path = root / 'playlists.json'
        with (root / 'playlists.lock').open('a+') as lock:
            try:
                lock_exclusive(lock)
            except BlockingIOError:
                raise RuntimeError('Playlist storage is busy; retry shortly') from None
            playlists = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            now = datetime.now(timezone.utc).isoformat()
            def summary(p):
                return {k:p.get(k) for k in ('id','name','createdAtIso','lastModifiedIso','isReadOnly','artwork')} | {'itemCount':len(p['items']), 'totalDurationMs':sum(i['track'].get('durationMs',0) or 0 for i in p['items']), 'thumbnails':[]}
            if action == 'getIndex':
                return [summary(p) for p in playlists.values()]
            if action == 'getPlaylist':
                return playlists.get(args[0])
            if action in ('createPlaylist','importPlaylist','saveQueueAsPlaylist'):
                identity = str(uuid.uuid4())
                p = dict(args[0]) if action == 'importPlaylist' else {'name':args[0]}
                p.update(id=identity, createdAtIso=p.get('createdAtIso', now), lastModifiedIso=now, isReadOnly=False)
                p.setdefault('items', [])
                if action == 'saveQueueAsPlaylist':
                    p['items'] = [{'id':str(uuid.uuid4()), 'track':nuclear_track(Track(**item)), 'addedAtIso':now} for item in self._player_snapshot()['state']['queue']]
                playlists[identity] = p
                result = identity
            elif action == 'deletePlaylist':
                playlists.pop(args[0], None)
                result = None
            else:
                p = playlists[args[0]]
                if action == 'addTracks':
                    result = [{'id':str(uuid.uuid4()), 'track':track, 'addedAtIso':now} for track in args[1]]
                    p['items'].extend(result)
                elif action == 'removeTracks':
                    p['items'] = [i for i in p['items'] if i['id'] not in args[1]]
                    result = None
                elif action == 'reorderTracks':
                    p['items'].insert(args[2],p['items'].pop(args[1]))
                    result = None
                else:
                    raise ValueError('Unsupported playlist operation')
                p['lastModifiedIso'] = now
            fd, temporary = tempfile.mkstemp(dir=root, prefix='.playlists-')
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as handle:
                    json.dump(playlists,handle)
                os.replace(temporary,path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return result

    def _control_callback(self, method, args):
        from dataclasses import asdict
        area, action = method.split('.')
        if area == 'Playlists':
            return self._playlist_callback(action, args)
        snapshot = self._player_snapshot()
        state = snapshot['state']
        occurrences, items = {}, []
        for index, item in enumerate(state['queue']):
            identity = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:24]
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            items.append({'id': f'{identity}:{occurrence}', 'track': nuclear_track(Track(**item)), 'status': 'success' if index == state['index'] and not snapshot['idle'] else 'idle', 'addedAtIso': ''})
        read_only = {'getQueue','getCurrentItem','getState','getVolume','isMuted','isShuffleEnabled','getRepeatMode','getTracks','getAlbums','getArtists','isTrackFavorite'}
        if not self.control_enabled and action not in read_only:
            raise RuntimeError('A provider cannot change playback while resolving a stream')
        if area == 'Queue':
            if action == 'getQueue':
                return {'items':items, 'currentIndex':state['index']}
            if action == 'getCurrentItem':
                return items[state['index']] if 0 <= state['index'] < len(items) else None
            if action in ('goToNext', 'goToPrevious'):
                return self._player_request('next' if action=='goToNext' else 'previous')
            if action in ('goToIndex','goToId'):
                index = int(args[0]) if action == 'goToIndex' else next((i for i,item in enumerate(items) if item['id']==args[0]), -1)
                return self._player_request('play',index=index)
            if action in ('addToQueue','addNext','addAt'):
                tracks = [asdict(convert_item(t,'song',t.get('source',{}).get('provider',''))) for t in args[0]]
                index = len(items) if action=='addToQueue' else state['index']+1 if action=='addNext' else args[1]
                return self._player_request('plugin-queue', action='insert', tracks=tracks, index=index)
            if action == 'clearQueue':
                return self._player_request('plugin-queue',action='clear')
            if action in ('removeByIds','removeByIndices'):
                indices = [int(i) for i in args[0]] if action=='removeByIndices' else [i for i,item in enumerate(items) if item['id'] in args[0]]
                return self._player_request('plugin-queue',action='remove',indices=indices)
            if action == 'reorder':
                return self._player_request('move',index=int(args[0]),delta=int(args[1])-int(args[0]))
        if area == 'Playback':
            if action == 'getState':
                return {'status':'stopped' if snapshot['idle'] else 'paused' if snapshot['paused'] else 'playing', 'seek':snapshot['position'], 'duration':snapshot['duration']}
            getters = {'getVolume':state['volume']/100, 'isMuted':state['volume']==0, 'isShuffleEnabled':state['shuffle'], 'getRepeatMode':'off' if not state['repeat'] else 'one' if state.get('repeat_one') else 'all'}
            if action in getters:
                return getters[action]
            if action in ('play','pause','toggle'):
                if action=='play' and snapshot['idle']:
                    return self._player_request('play',index=max(0,state['index']))
                if action=='toggle' or (action=='play' and snapshot['paused']) or (action=='pause' and not snapshot['paused']):
                    return self._player_request('command',command=['cycle','pause'])
                return None
            if action=='stop':
                return self._player_request('command',command=['stop'])
            if action=='seekTo':
                return self._player_request('command',command=['seek',float(args[0]),'absolute'])
            changes = {}
            if action=='setVolume':
                changes['volume']=float(args[0])*100
            elif action=='setMuted':
                changes['volume']=0 if args[0] else 70
            elif action=='setShuffleEnabled':
                changes['shuffle']=bool(args[0])
            elif action=='setRepeatMode':
                if args[0] not in ('off','one','all'):
                    raise ValueError('Invalid repeat mode')
                changes.update(repeat=args[0]!='off',repeat_one=args[0]=='one')
            if changes:
                settings={k:state[k] for k in ('volume','speed','equalizer','shuffle','repeat','repeat_one')}
                settings.update(changes)
                return self._player_request('settings',**settings)
        if area == 'Favorites':
            favorites=[nuclear_track(Track(**item)) for item in state['favorites']]
            if action=='getTracks':
                return [{'ref':t,'addedAtIso':''} for t in favorites]
            if action in ('getAlbums','getArtists'):
                return []
            if action in ('isTrackFavorite','removeTrack'):
                match=next((t for t in favorites if t['source']==args[0]),None)
                if action=='isTrackFavorite':
                    return match is not None
                if match is None:
                    return None
                track=match
            elif action=='addTrack':
                if any(t['source']==args[0]['source'] for t in favorites):
                    return None
                track=args[0]
            else:
                raise RuntimeError('Artist/album favorites need a terminal adapter')
            return self._player_request('favorite',track=asdict(convert_item(track,'song',track['source']['provider'])))
        raise RuntimeError(f'{method} needs a Musik terminal adapter')

    def close(self):
        atexit.unregister(self.close)
        with self.lock:
            process, self.process = self.process, None
            self.stamp = None
            if process:
                if process.poll() is None:
                    try:
                        process.stdin.write(json.dumps({'id':0,'method':'close'})+'\n')
                        process.stdin.flush()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                        process.wait(timeout=3)
                for handle in (process.stdin, process.stdout):
                    if handle:
                        handle.close()


def nuclear_track(track):
    if track.source == 'nuclear':
        return json.loads(track.id)['track']
    return {'title': track.title, 'artists': [{'name': track.artist}], 'source': {'provider': 'youtube', 'id': track.id}}


def convert_item(item, kind, provider_id, streaming_id=None):
    source = item.get('source') or {'provider': provider_id, 'id': item.get('id', '')}
    data = dict(item, source=source)
    payload = {'kind': kind, 'provider': provider_id, 'streaming': streaming_id, 'track': data}
    artists = item.get('artists') or []
    artist_data = item.get('artist') or {}
    artist = ', '.join(a.get('name', '') for a in artists if isinstance(a, dict)) or (artist_data.get('name', '') if isinstance(artist_data, dict) else str(artist_data)) or provider_id
    title = item.get('title') or item.get('name') or 'Untitled'
    if kind == 'song' and source.get('provider') == 'youtube' and re.fullmatch(r'[A-Za-z0-9_-]{11}', str(source.get('id', ''))):
        return Track(source['id'], title, artist)
    return Track(json.dumps(payload, separators=(',', ':')), title, artist, 'nuclear')


def item_kind(track):
    return json.loads(track.id)['kind'] if track.source == 'nuclear' else track.source


def parse_setting(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def plugin_command(args, store=None):
    store = store or Store()
    if args.install_plugin:
        providers = install_plugin(args.install_plugin, store)
        print('Enabled providers: ' + ', '.join(f'{p["kind"]}: {p["id"]}' for p in providers))
    elif args.enable_plugin or args.disable_plugin:
        set_enabled(args.enable_plugin or args.disable_plugin, bool(args.enable_plugin), store)
        print('Plugin selection saved.')
    elif args.provider:
        kind, separator, provider_id = args.provider.partition('=')
        if not separator:
            raise ValueError('Use --provider metadata=PROVIDER_ID (or streaming/lyrics/dashboard/playlists/discovery)')
        select_provider(kind, provider_id, store)
        print(f'{kind} provider: {provider_id}')
    elif args.setting:
        if not args.plugin:
            raise ValueError('--setting needs --plugin ID')
        setting_id, sep, value = args.setting.partition('=')
        if not sep:
            raise ValueError('Use --setting ID=JSON_VALUE')
        runtime = NuclearRuntime(store)
        try:
            runtime.call('setting', plugin=args.plugin, id=setting_id, value=parse_setting(value))
        finally:
            runtime.close()
        print('Plugin setting saved.')
    elif args.installed_plugins:
        runtime = NuclearRuntime(store)
        try:
            print(json.dumps(runtime.status(), indent=2))
        finally:
            runtime.close()
    else:
        list_plugins(args.plugin, store)


def list_plugins(plugin_id=None, store=None):
    entries = registry()
    if plugin_id:
        entries = [p for p in entries if p['id'] == plugin_id]
        if not entries:
            raise ValueError('Plugin ID was not found in the Nuclear registry')
    config = configuration(store or Store())
    for entry in entries:
        installed = config['installed'].get(entry['id'])
        state = ('enabled' if installed['enabled'] else 'disabled') if installed else 'not installed'
        print(f"\n{entry['id']} · {entry.get('name', entry['id'])} · {state}")
        print(entry.get('description', ''))
        print(f"https://github.com/{entry['repo']}")
    print('\nInstall: musik plugins --install ID\nProviders/status: musik plugins --installed')
