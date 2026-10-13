"""Standalone torrent search and optional TorBox connection."""
import asyncio
import json
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, SelectionList, Static
from .core import Track
from .torrents import configure, engine_names, install_engine, settings, search_torrents, TorBox


class TorrentScreen(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close')]
    DEFAULT_CSS = '''
    TorrentScreen { align: center middle; }
    #torrent-panel { width: 90%; height: 90%; padding: 1; border: round $accent; background: $surface; overflow-y: auto; }
    #torrent-engines { height: 6; }
    #torrent-progress { height: auto; min-height: 2; }
    .torrent-actions { height: 3; }
    '''

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.busy = False

    def compose(self) -> ComposeResult:
        config = settings(self.store)
        names = engine_names(self.store)
        selected = config.get('engines', names)
        with Vertical(id='torrent-panel'):
            yield Static('TORRENTS · Standalone Nova search · No qBittorrent required', markup=False)
            yield Static('Choose search engines:', markup=False)
            yield SelectionList(*[(name, name, name in selected) for name in names], id='torrent-engines')
            yield Input(placeholder='Search artist / album / FLAC, or paste a magnet link', id='torrent-query')
            yield Button('Search', id='torrent-search')
            yield Static('TorBox streams directly to mpv. No music files are saved locally.', markup=False)
            yield Input(placeholder='TorBox API key (blank keeps saved key)', password=True, id='torbox-key')
            with Horizontal(classes='torrent-actions'):
                yield Button('Save', id='torrent-save')
                yield Button('Check TorBox', id='torrent-check')
                yield Button('Disconnect', id='torrent-disconnect')
            yield Static('Optional: install a trusted qBittorrent search plugin. Plugins execute Python as your user.', markup=False)
            yield Input(placeholder='Plugin .py URL or local path', id='torrent-engine-source')
            with Horizontal(classes='torrent-actions'):
                yield Button('Install engine', id='torrent-engine-install')
                yield Button('Close', id='torrent-close')
            yield Static('TorBox key saved' if config.get('torbox_token') else 'TorBox not connected', id='torrent-progress', markup=False)

    def progress(self, text):
        if self.is_mounted:
            self.query_one('#torrent-progress', Static).update(text)

    def save(self):
        token = self.query_one('#torbox-key', Input)
        configure(self.store, token=token.value if token.value.strip() else None,
                  engines=self.query_one('#torrent-engines', SelectionList).selected)
        token.value = ''

    @on(Input.Submitted, '#torrent-query')
    async def submit_query(self, event):
        self.run_worker(self.search(), group='torrent-search', exclusive=True)

    async def search(self):
        query = self.query_one('#torrent-query', Input).value.strip()
        if self.busy or not query:
            return
        self.save()
        self.busy = True
        self.progress('Searching standalone Nova engines…')
        try:
            rows = [Track(json.dumps({'url': query}), 'Open torrent in TorBox', 'TorBox', 'torrent')] if query.startswith('magnet:') else await asyncio.to_thread(search_torrents, query, self.store, progress=lambda message: self.app.call_from_thread(self.progress, message))
            app = self.app
            app.remember_page()
            app.results = rows
            app.playlist_contents = []
            app.catalog_title = 'TORRENTS'
            app.choose_section()
            self.dismiss()
            app.action_view('results')
            app.status(f'{len(rows)} torrents · Enter sends a selection to TorBox and opens its audio files')
        except Exception as error:
            self.progress(str(error))
        finally:
            self.busy = False

    @on(Button.Pressed)
    async def button(self, event):
        identity = event.button.id
        if identity == 'torrent-close':
            self.dismiss()
            return
        if self.busy:
            return
        if identity == 'torrent-search':
            self.run_worker(self.search(), group='torrent-search', exclusive=True)
            return
        self.busy = True
        try:
            if identity == 'torrent-disconnect':
                configure(self.store, token='')
                self.query_one('#torbox-key', Input).value = ''
                self.progress('TorBox disconnected. Remote torrents were kept.')
            elif identity == 'torrent-save':
                self.save()
                self.progress('Settings saved.')
            elif identity == 'torrent-check':
                self.save()
                self.progress('Checking TorBox…')
                await asyncio.to_thread(TorBox(self.store).check)
                self.progress('TorBox connection ready.')
            elif identity == 'torrent-engine-install':
                source = self.query_one('#torrent-engine-source', Input).value.strip()
                if source:
                    self.progress('Installing search engine…')
                    name = await asyncio.to_thread(install_engine, source, self.store)
                    choices = self.query_one('#torrent-engines', SelectionList)
                    selected = set(choices.selected) | {name}
                    choices.clear_options()
                    choices.add_options([(engine, engine, engine in selected) for engine in engine_names(self.store)])
                    self.progress(f'{name} installed. Save to use selected engines.')
        except Exception as error:
            self.progress(str(error))
        finally:
            self.busy = False
