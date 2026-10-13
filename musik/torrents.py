"""Standalone Nova torrent search and TorBox HTTP audio streaming."""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse, parse_qs, urlencode

import requests
from .core import Store, Track
from .platform import lock_exclusive

TORBOX_API = 'https://api.torbox.app/v1/api'
AUDIO_EXTENSIONS = {'.flac', '.mp3', '.wav', '.m4a', '.aac', '.alac', '.ogg', '.opus', '.aiff', '.aif', '.ape', '.wma'}


def bounded_results(function, values, *, deadline, workers=8):
    """Collect completed work without waiting for slow queued tasks at shutdown."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(function, value): value for value in values}
    try:
        for future in as_completed(futures, timeout=max(0, deadline-time.monotonic())):
            yield futures[future], future.result()
    except FutureTimeout:
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def audio_files(item):
    files = [file if isinstance(file, dict) else {'name': file} for file in (item.get('files') or []) if isinstance(file, (dict, str))]
    return [file for file in files
            if Path(file.get('short_name') or file.get('name') or '').suffix.lower() in AUDIO_EXTENSIONS]


def archive_release(title):
    return bool(re.search(r'(?i)\b(?:zip|rar|7z|tgz|gzip|tar\.gz)\b|\.r\d{2}\b', title))


def torrent_hash(url):
    import base64
    for value in parse_qs(urlparse(url).query).get('xt', []):
        if value.lower().startswith('urn:btih:'):
            value = value[9:]
            if re.fullmatch(r'[0-9a-fA-F]{40}', value):
                return value.lower()
            if re.fullmatch(r'[A-Za-z2-7]{32}', value):
                return base64.b32decode(value.upper()).hex()
    return None


def torrent_metadata(content):
    """Read bounded bencoded metadata; hash the original encoded info section."""
    if len(content) > 2_000_000:
        raise ValueError('Torrent metadata is too large')
    info_span = None
    def parse(position, depth=0):
        nonlocal info_span
        if depth > 100 or position >= len(content):
            raise ValueError('Invalid torrent metadata')
        token = content[position:position+1]
        if token in (b'd', b'l'):
            value = {} if token == b'd' else []
            position += 1
            while position < len(content) and content[position:position+1] != b'e':
                if token == b'd':
                    key, position = parse(position, depth+1)
                    if not isinstance(key, bytes):
                        raise ValueError('Invalid torrent key')
                    start = position
                    item, position = parse(position, depth+1)
                    value[key] = item
                    if depth == 0 and key == b'info':
                        info_span = content[start:position]
                else:
                    item, position = parse(position, depth+1)
                    value.append(item)
            if position >= len(content):
                raise ValueError('Invalid torrent metadata')
            return value, position+1
        if token == b'i':
            end = content.index(b'e', position)
            return int(content[position+1:end]), end+1
        colon = content.index(b':', position)
        length = int(content[position:colon])
        end = colon+1+length
        if length < 0 or end > len(content):
            raise ValueError('Invalid torrent string')
        return content[colon+1:end], end
    data, end = parse(0)
    if end != len(content) or not isinstance(data, dict) or not info_span or not isinstance(data.get(b'info'), dict):
        raise ValueError('Invalid torrent metadata')
    info = data[b'info']
    names = []
    if b'files' in info:
        for file in info[b'files']:
            path = file.get(b'path.utf-8') or file.get(b'path') or []
            if path and isinstance(path[-1], bytes):
                names.append({'name': path[-1].decode('utf-8', errors='replace')})
    else:
        name = info.get(b'name.utf-8') or info.get(b'name')
        if isinstance(name, bytes):
            names.append({'name': name.decode('utf-8', errors='replace')})
    trackers = []
    values = [data.get(b'announce')]
    for tier in data.get(b'announce-list', []):
        values.extend(tier if isinstance(tier, list) else [tier])
    for value in values:
        if isinstance(value, bytes):
            tracker = value.decode('utf-8', errors='replace')
            if tracker not in trackers:
                trackers.append(tracker)
    return hashlib.sha1(info_span).hexdigest(), {'files': names, 'trackers': trackers}


def resolve_search_link(row, store, *, timeout=25):
    payload = json.loads(row.id)
    identity = torrent_hash(payload['url'])
    if identity:
        return row, identity, False
    engine = payload.get('engine')
    if not engine or engine not in engine_names(store):
        return None, None, False
    try:
        with tempfile.TemporaryDirectory(prefix='musik-nova-metadata-') as folder:
            runner = Path(__file__).with_name('nova_runner.py')
            env = dict(os.environ, TMPDIR=folder, TEMP=folder, TMP=folder)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(runner), str(nova_root(store)), engine, payload['url'], 'link'],
                                    capture_output=True, env=env, timeout=timeout, **options)
            if result.returncode:
                return None, None, False
            lines = result.stdout.decode('utf-8', errors='replace').strip().splitlines()
            if not lines:
                return None, None, False
            output = lines[-1]
            if output.startswith('magnet:'):
                payload['url'] = output.split(' ', 1)[0]
                identity = torrent_hash(payload['url'])
                return Track(json.dumps(payload), row.title, row.artist, row.source), identity, False
            path = Path(output.rsplit(' ', 1)[0]).resolve()
            if not path.is_relative_to(Path(folder).resolve()) or path.stat().st_size > 2_000_000:
                return None, None, False
            identity, metadata = torrent_metadata(path.read_bytes())
            if not audio_files(metadata):
                return None, None, False
            parameters = [('xt', 'urn:btih:'+identity), ('dn', row.title)] + [('tr', value) for value in metadata['trackers']]
            payload['url'] = 'magnet:?' + urlencode(parameters)
            return Track(json.dumps(payload), row.title, row.artist, row.source), identity, True
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None, None, False


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.torrent-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def settings(store):
    return read_json(store.directory / 'torrents.json', {})


def configure(store, *, token=None, engines=None):
    config = settings(store)
    if token is not None:
        config['torbox_token'] = token.strip()
    if engines is not None:
        config['engines'] = engines
    write_private(store.directory / 'torrents.json', config)


def cached_torrents(store):
    token = settings(store).get('torbox_token', '')
    account = hashlib.sha256(token.encode()).hexdigest() if token else None
    data = read_json(store.directory / 'torbox-library.json', {})
    return [Track(json.dumps({'torrent_id': int(identity), 'account': account}), entry['title'],
                  entry.get('status', 'TorBox'), 'torrent')
            for identity, entry in data.get(account, {}).items() if entry.get('ready_audio')]


def update_library(store, account, entries, *, replace=False):
    path = store.directory / 'torbox-library.json'
    with (store.directory / 'torbox-library.lock').open('a+') as handle:
        deadline = time.monotonic() + 5
        while True:
            try:
                lock_exclusive(handle)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Saved torrent storage is busy') from None
                time.sleep(.01)
        data = read_json(path, {})
        if replace:
            data[account] = entries
        else:
            data.setdefault(account, {}).update(entries)
        write_private(path, data)


def nova_root(store):
    bundled = Path(__file__).parent / 'vendor/nova'
    root = store.directory / 'nova'
    (root / 'engines').mkdir(parents=True, exist_ok=True, mode=0o700)
    for source in bundled.glob('*.py'):
        target = root / source.name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
    for source in (bundled / 'engines').glob('*.py'):
        target = root / 'engines' / source.name
        if not target.exists():
            shutil.copyfile(source, target)
    return root


def engine_names(store):
    return sorted(p.stem for p in (nova_root(store) / 'engines').glob('*.py') if not p.stem.startswith('_'))


def install_engine(source, store):
    parsed = urlparse(source)
    if parsed.scheme in ('https', 'http'):
        try:
            with requests.get(source, timeout=20, stream=True) as response:
                response.raise_for_status()
                content = b''
                for chunk in response.iter_content(65536):
                    content += chunk
                    if len(content) > 2_000_000:
                        raise ValueError('Search plugin is too large')
        except requests.RequestException:
            raise RuntimeError('Could not fetch the search plugin') from None
        name = Path(parsed.path).stem
    else:
        path = Path(source).expanduser()
        if path.stat().st_size > 2_000_000:
            raise ValueError('Search plugin is too large')
        content, name = path.read_bytes(), path.stem
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,80}', name):
        raise ValueError('Invalid search plugin filename')
    module = ast.parse(content)
    if not any(isinstance(node, ast.ClassDef) and node.name == name for node in module.body):
        raise ValueError('A Nova plugin must define a class matching its filename')
    root = nova_root(store)
    (root / 'engines' / f'{name}.py').write_bytes(content)
    return name


def search_torrents(query, store, *, timeout=30, progress=None):
    if not query.strip():
        raise ValueError('Enter a torrent search query')
    root = nova_root(store)
    available = engine_names(store)
    engines = settings(store).get('engines', available)
    if not engines or any(engine not in available for engine in engines):
        raise RuntimeError('Select installed search engines in Torrents (F11)')
    runner = Path(__file__).with_name('nova_runner.py')
    deadline = time.monotonic() + timeout
    def search(engine):
        try:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(runner), str(root), engine, query],
                                    capture_output=True, timeout=max(.1, deadline-time.monotonic()), **options)
            output = result.stdout.decode('utf-8', errors='replace')
            return output, result.returncode == 0
        except subprocess.TimeoutExpired as error:
            # Preserve useful results already returned by a slow plugin.
            return (error.stdout or b'').decode('utf-8', errors='replace'), False
    rows, seen, succeeded = [], set(), False
    for engine, (output, success) in bounded_results(search, engines, deadline=deadline+.2):
        if progress: progress(f'Searched {engine} · checking matches…')
        succeeded |= success
        for line in output.splitlines():
            fields = line.split('|')
            if len(fields) < 6:
                continue
            url, title, size, seeds, _, provider = fields[:6]
            if url in seen or urlparse(url).scheme not in ('http', 'https', 'magnet'):
                continue
            try:
                size, seeds = int(size), int(seeds)
            except ValueError:
                continue
            title = unquote(title)
            if seeds <= 0 or archive_release(title):
                continue
            seen.add(url)
            payload = {'url': url, 'size': size, 'seeds': seeds, 'provider': provider, 'engine': engine}
            detail = f'{seeds} seeds · {size / 1024**2:.0f} MiB' if size >= 0 else f'{seeds} seeds'
            rows.append(Track(json.dumps(payload), title, detail, 'torrent'))
    if not rows and not succeeded:
        raise RuntimeError('Torrent search engines failed or timed out. Try another engine in F11.')
    if rows and settings(store).get('torbox_token'):
        if progress: progress(f'Checking audio contents for {len(rows)} matches…')
        rows = TorBox(store).filter_audio_results(rows)
        if not rows:
            raise RuntimeError('Engines found matches, but none had verified audio contents (non-audio files, unavailable metadata, or timed-out checks). Try fewer engines or a more specific artist/album query; check TorBox access in F11.')
    return sorted(rows, key=lambda track: json.loads(track.id)['seeds'], reverse=True)


class TorBox:
    def __init__(self, store, session=None):
        self.store = store
        self.token = settings(store).get('torbox_token', '')
        if not self.token:
            raise RuntimeError('Connect TorBox in Torrents (F11) first')
        self.session = session or requests.Session()
        self.account = hashlib.sha256(self.token.encode()).hexdigest()

    def filter_audio_results(self, rows, *, timeout=20):
        deadline = time.monotonic() + timeout
        # Prefer seeded candidates and cap network work for broad searches.
        rows = sorted(rows, key=lambda row: json.loads(row.id).get('seeds', 0), reverse=True)[:80]
        resolved = [result for _, result in bounded_results(
            lambda row: resolve_search_link(row, self.store, timeout=min(8, max(.1, deadline-time.monotonic()))),
            rows, deadline=min(deadline, time.monotonic()+8))]
        hashes = list(dict.fromkeys(identity for row, identity, verified in resolved if row and identity))
        accepted = {identity for row, identity, verified in resolved if row and verified}
        checked = set(accepted)
        for start in range(0, len(hashes), 100):
            if time.monotonic() >= deadline: break
            try:
                data = self.call('GET', 'torrents/checkcached', timeout=min(8, max(.1, deadline-time.monotonic())), params={
                    'hash': ','.join(hashes[start:start+100]), 'list_files': 'true', 'format': 'list'}) or []
            except RuntimeError:
                data = []
            entries = data.items() if isinstance(data, dict) else [(entry.get('hash', ''), entry) for entry in data if isinstance(entry, dict)]
            for identity, entry in entries:
                if isinstance(entry, dict) and entry.get('files'):
                    identity = str(entry.get('hash') or identity).lower()
                    checked.add(identity)
                    if audio_files(entry):
                        accepted.add(identity)
                    else:
                        accepted.discard(identity)
        def inspect(identity):
            try:
                item = self.call('GET', 'torrents/torrentinfo', timeout=min(6, max(.1, deadline-time.monotonic())), params={'hash': identity, 'timeout': 5})
                return identity if isinstance(item, dict) and audio_files(item) else None
            except RuntimeError:
                # Unknown contents are excluded rather than advertising a
                # potentially unplayable archive as an audio release.
                return None
        accepted.update(result for _, result in bounded_results(inspect,
            [value for value in hashes if value not in checked], deadline=deadline) if result)
        return [row for row, identity, verified in resolved if row and identity in accepted]

    def remember(self, identity, title, status='TorBox', *, ready_audio=False):
        update_library(self.store, self.account, {str(identity): {'title': title, 'status': status, 'ready_audio': ready_audio}})

    def library(self):
        data, seen, offset = [], set(), 0
        while True:
            page = self.call('GET', 'torrents/mylist', params={'limit': 1000, 'offset': offset, 'bypass_cache': 'true'}) or []
            if not isinstance(page, list):
                raise RuntimeError('TorBox returned an invalid torrent list')
            fresh = [item for item in page if int(item['id']) not in seen]
            data.extend(fresh)
            seen.update(int(item['id']) for item in fresh)
            if len(page) < 1000 or not fresh:
                break
            offset += len(page)
        path = self.store.directory / 'torbox-library.json'
        saved = read_json(path, {})
        entries = {}
        for item in data:
            if not item.get('download_finished') or not audio_files(item):
                continue
            identity = str(int(item['id']))
            title = item.get('name') or saved.get(self.account, {}).get(identity, {}).get('title') or f'Torrent {identity}'
            entries[identity] = {'title': title, 'status': 'Cached · ready', 'ready_audio': True}
        update_library(self.store, self.account, entries, replace=True)
        return cached_torrents(self.store)

    def call(self, method, endpoint, **kwargs):
        try:
            response = self.session.request(method, f'{TORBOX_API}/{endpoint}',
                                            headers={'Authorization': f'Bearer {self.token}'}, timeout=kwargs.pop('timeout', 25), **kwargs)
            if response.status_code in (401, 403):
                raise RuntimeError('TorBox authentication failed. Check your API key in F11.')
            if response.status_code == 429:
                raise RuntimeError('TorBox rate limit reached. Try again shortly.')
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            raise RuntimeError('TorBox request failed. Check network access and try again.') from None
        except ValueError:
            raise RuntimeError('TorBox returned an invalid response') from None
        if not data.get('success'):
            raise RuntimeError(f"TorBox could not complete this request ({data.get('error', 'unknown error')})")
        return data.get('data')

    def check(self):
        return self.call('GET', 'torrents/mylist', params={'limit': 1})

    def add(self, url):
        if urlparse(url).scheme not in ('magnet', 'http', 'https'):
            raise ValueError('Choose a magnet link or a direct .torrent URL')
        key = hashlib.sha256((self.token + '\0' + url).encode()).hexdigest()
        path = self.store.directory / 'torbox-items.json'
        with (self.store.directory / 'torbox-items.lock').open('a+') as lock:
            deadline = time.monotonic() + 5
            while True:
                try:
                    lock_exclusive(lock)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Another TorBox request is still loading') from None
                    time.sleep(.02)
            saved = read_json(path, {})
            if key not in saved:
                data = {'seed': '1', 'allow_zip': 'false', 'as_queued': 'false'}
                files = {}
                if urlparse(url).scheme == 'magnet':
                    data['magnet'] = url
                else:
                    # Only torrent metadata is fetched into memory. Audio is
                    # always fetched by TorBox and streamed directly by mpv.
                    try:
                        with requests.get(url, timeout=20, stream=True) as response:
                            response.raise_for_status()
                            content = b''
                            for chunk in response.iter_content(65536):
                                content += chunk
                                if len(content) > 2_000_000:
                                    raise ValueError('Torrent metadata is too large')
                    except requests.RequestException:
                        raise RuntimeError('Could not fetch torrent metadata. Try a magnet result.') from None
                    if not content.startswith(b'd') or not content.endswith(b'e') or b'4:info' not in content:
                        raise ValueError('This result is a web page, not a direct .torrent file. Choose a magnet result.')
                    files['file'] = ('torrent.torrent', content, 'application/x-bittorrent')
                result = self.call('POST', 'torrents/createtorrent', data=data, files=files)
                saved[key] = int(result['torrent_id'])
                write_private(path, saved)
            return saved[key]

    def files(self, track):
        payload = json.loads(track.id)
        if payload.get('account') and payload['account'] != self.account:
            raise RuntimeError('This torrent belongs to another TorBox account')
        torrent_id = int(payload['torrent_id']) if 'torrent_id' in payload else self.add(payload['url'])
        self.remember(torrent_id, track.title, 'Preparing')
        data = self.call('GET', 'torrents/mylist', params={'id': torrent_id, 'bypass_cache': 'true'})
        item = next((entry for entry in data if int(entry['id']) == torrent_id), None) if isinstance(data, list) else data
        if not item or not item.get('download_finished'):
            progress = float((item or {}).get('progress') or 0) * 100
            raise RuntimeError(f'TorBox is preparing this torrent ({progress:.0f}%). Select it again shortly; no audio is downloaded locally.')
        rows = []
        for file in audio_files(item):
            name = file.get('short_name') or file.get('name') or ''
            payload = {'torrent_id': torrent_id, 'file_id': int(file['id']), 'account': self.account}
            rows.append(Track(json.dumps(payload), Path(name).name, 'TorBox', 'torbox'))
        if not rows:
            raise RuntimeError('This torrent contains no supported audio files')
        self.remember(torrent_id, item.get('name') or track.title, 'Cached · ready', ready_audio=True)
        return rows

    def stream(self, track):
        payload = json.loads(track.id)
        if payload.get('account') and payload['account'] != self.account:
            raise RuntimeError('This audio belongs to another TorBox account')
        url = self.call('GET', 'torrents/requestdl', params={
            'token': self.token, 'torrent_id': int(payload['torrent_id']), 'file_id': int(payload['file_id']),
            'zip_link': 'false', 'torrent_file': 'false', 'redirect': 'false'})
        if not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https'):
            raise RuntimeError('TorBox did not return a playable audio URL')
        return {'url': url}


def torrent_command(args, store=None):
    store = store or Store()
    if args.connect_torbox:
        import getpass
        token = getpass.getpass('TorBox API key (hidden): ').strip()
        if not token:
            raise ValueError('API key cannot be empty')
        configure(store, token=token)
        print('TorBox API key saved. Open Musik and press F11 to check the connection and search torrents.')
    elif args.disconnect_torbox:
        configure(store, token='')
        print('TorBox disconnected. Your remote torrents were kept.')
    elif args.install_engine:
        print('Installed search engine: ' + install_engine(args.install_engine, store))
    else:
        print('Standalone Nova engines: ' + ', '.join(engine_names(store)))
        print('TorBox: ' + ('connected' if settings(store).get('torbox_token') else 'not connected'))
        print('Open F11 or search /torrent QUERY. Audio streams directly; no local music downloads.')
