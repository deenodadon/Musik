"""Standalone Nova torrent search and TorBox HTTP audio streaming."""
import ast
from concurrent.futures import ThreadPoolExecutor
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
from urllib.parse import unquote, urlparse

import requests
from .core import Store, Track
from .platform import lock_exclusive

TORBOX_API = 'https://api.torbox.app/v1/api'
AUDIO_EXTENSIONS = {'.flac', '.mp3', '.wav', '.m4a', '.aac', '.alac', '.ogg', '.opus', '.aiff', '.aif', '.ape', '.wma'}


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


def search_torrents(query, store, *, timeout=30):
    if not query.strip():
        raise ValueError('Enter a torrent search query')
    root = nova_root(store)
    available = engine_names(store)
    engines = settings(store).get('engines', available)
    if not engines or any(engine not in available for engine in engines):
        raise RuntimeError('Select installed search engines in Torrents (F11)')
    runner = Path(__file__).with_name('nova_runner.py')
    def search(engine):
        try:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(runner), str(root), engine, query],
                                    capture_output=True, timeout=timeout, **options)
            output = result.stdout.decode('utf-8', errors='replace')
            return output, result.returncode == 0
        except subprocess.TimeoutExpired as error:
            # Preserve useful results already returned by a slow plugin.
            return (error.stdout or b'').decode('utf-8', errors='replace'), False
    rows, seen, succeeded = [], set(), False
    with ThreadPoolExecutor(max_workers=3) as pool:
        for output, success in pool.map(search, engines):
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
                seen.add(url)
                payload = {'url': url, 'size': size, 'seeds': seeds, 'provider': provider}
                detail = f'{seeds} seeds · {size / 1024**2:.0f} MiB' if size >= 0 else f'{seeds} seeds'
                rows.append(Track(json.dumps(payload), unquote(title), detail, 'torrent'))
    if not rows and not succeeded:
        raise RuntimeError('Torrent search engines failed or timed out. Try another engine in F11.')
    return sorted(rows, key=lambda track: json.loads(track.id)['seeds'], reverse=True)


class TorBox:
    def __init__(self, store, session=None):
        self.store = store
        self.token = settings(store).get('torbox_token', '')
        if not self.token:
            raise RuntimeError('Connect TorBox in Torrents (F11) first')
        self.session = session or requests.Session()

    def call(self, method, endpoint, **kwargs):
        try:
            response = self.session.request(method, f'{TORBOX_API}/{endpoint}',
                                            headers={'Authorization': f'Bearer {self.token}'}, timeout=25, **kwargs)
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
        torrent_id = self.add(json.loads(track.id)['url'])
        data = self.call('GET', 'torrents/mylist', params={'id': torrent_id, 'bypass_cache': 'true'})
        item = next((entry for entry in data if int(entry['id']) == torrent_id), None) if isinstance(data, list) else data
        if not item or not item.get('download_finished'):
            progress = float((item or {}).get('progress') or 0) * 100
            raise RuntimeError(f'TorBox is preparing this torrent ({progress:.0f}%). Select it again shortly; no audio is downloaded locally.')
        rows = []
        for file in item.get('files', []):
            name = file.get('short_name') or file.get('name') or ''
            if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                payload = {'torrent_id': torrent_id, 'file_id': int(file['id'])}
                rows.append(Track(json.dumps(payload), Path(name).name, 'TorBox', 'torbox'))
        if not rows:
            raise RuntimeError('This torrent contains no supported audio files')
        return rows

    def stream(self, track):
        payload = json.loads(track.id)
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
