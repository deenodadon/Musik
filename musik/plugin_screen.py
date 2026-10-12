"""Terminal registry browser and provider selection."""
import asyncio
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, DataTable, Static, Select, Input
from .nuclear import registry, configuration, install_plugin, set_enabled, select_provider, parse_setting


class PluginScreen(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close')]
    DEFAULT_CSS = '''
    PluginScreen { align: center middle; }
    #plugin-panel { width: 95%; height: 90%; border: round $accent; padding: 1; background: $surface; overflow-y: auto; }
    #plugin-list { height: 1fr; min-height: 6; }
    #plugin-description { height: 3; }
    #plugin-progress { height: 3; }
    #plugin-buttons { height: 3; }
    #plugin-provider { width: 1fr; }
    '''

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.entries = []
        self.busy = False
        self.settings = []

    def compose(self) -> ComposeResult:
        with Vertical(id='plugin-panel'):
            yield Static('NUCLEAR PLUGINS · Escape to close', markup=False)
            yield DataTable(id='plugin-list', cursor_type='row')
            yield Static('Choose a plugin to see its description.', id='plugin-description', markup=False)
            with Horizontal(id='plugin-buttons'):
                yield Button('Install', id='plugin-install')
                yield Button('Enable / Disable', id='plugin-toggle')
                yield Button('Charts', id='plugin-charts')
                yield Button('Close', id='plugin-close')
            yield Select([], prompt='Choose a provider to use', id='plugin-provider')
            yield Button('Use selected provider', id='plugin-use')
            yield Select([], prompt='Optional plugin setting', id='plugin-setting')
            yield Input(placeholder='New setting value (JSON for booleans/numbers)', id='plugin-setting-value')
            yield Button('Save setting', id='plugin-save-setting')
            yield Static('Installed plugins run as your user. Install only plugins you trust.', id='plugin-progress', markup=False)

    async def on_mount(self):
        self.query_one('#plugin-list', DataTable).add_columns('Plugin', 'Kinds', 'Status')
        self.run_worker(self.refresh_catalog())

    async def refresh_catalog(self):
        try:
            self.entries = await asyncio.to_thread(registry)
        except Exception as error:
            config = configuration(self.service.store)
            self.entries = [{'id':id, 'name':info['name'], 'categories': [], 'description':'Installed plugin (registry unavailable)'} for id,info in config['installed'].items()]
            self.progress(f'Registry unavailable ({type(error).__name__}); installed plugins remain usable.')
        await self.refresh_installed()

    async def refresh_installed(self):
        config = configuration(self.service.store)
        report = await asyncio.to_thread(self.service.nuclear.status)
        self.settings = report['settings']
        table = self.query_one('#plugin-list', DataTable)
        previous = table.cursor_row
        table.clear()
        for entry in self.entries:
            info = config['installed'].get(entry['id'])
            status = 'Not installed' if not info else 'Enabled' if entry['id'] in report['loaded'] else 'Disabled'
            if entry['id'] in report['failures'] or (info and info.get('error')):
                status = 'Requires adapter'
            table.add_row(entry.get('name',entry['id']), ', '.join(entry.get('categories',[])), status, key=entry['id'])
        table.move_cursor(row=previous)
        options = [(f"{p['kind']}: {p['name']} ({p['id']})" + (" · active" if config['active'].get(p['kind']) == p['id'] else ""), f"{p['kind']}={p['id']}") for p in report['providers']]
        options += [(f'{kind}: Musik default' + (' · active' if config['active'].get(kind, 'builtin') == 'builtin' else ''), f'{kind}=builtin') for kind in ['metadata','streaming','lyrics','dashboard','playlists','discovery']]
        self.query_one('#plugin-provider', Select).set_options(options)
        self.update_setting_options()

    def progress(self, text):
        if self.is_mounted:
            self.query_one('#plugin-progress', Static).update(text)

    @on(DataTable.RowHighlighted, '#plugin-list')
    def highlight(self, event):
        if 0 <= event.cursor_row < len(self.entries):
            entry = self.entries[event.cursor_row]
            info = configuration(self.service.store)['installed'].get(entry['id'], {})
            detail = info.get('error') or entry.get('description', '')
            self.query_one('#plugin-description', Static).update(f"{entry['id']}\n{detail}")
            self.update_setting_options()

    def update_setting_options(self):
        row = self.query_one('#plugin-list', DataTable).cursor_row
        plugin_id = self.entries[row]['id'] if 0 <= row < len(self.entries) else None
        self.query_one('#plugin-setting', Select).set_options([(d.get('title',d['id']),d['id']) for d in self.settings if d['pluginId']==plugin_id and not d.get('hidden')])

    @on(Select.Changed, '#plugin-setting')
    def setting_changed(self, event):
        definition = next((d for d in self.settings if d['id']==event.value),{})
        value = self.query_one('#plugin-setting-value', Input)
        value.value = ''
        value.password = definition.get('widget',{}).get('type')=='password' or definition.get('format')=='token'

    @on(Button.Pressed)
    async def button(self, event):
        if event.button.id == 'plugin-close':
            self.dismiss()
            return
        if self.busy:
            return
        if event.button.id == 'plugin-charts':
            self.busy = True
            self.progress('Loading the selected dashboard provider…')
            try:
                rows = await asyncio.to_thread(self.service.plugin_dashboard)
                if not rows:
                    self.progress('Enable and select a dashboard provider first.')
                    return
                app = self.app
                app.remember_page()
                app.results = rows
                app.catalog_title = 'CHARTS'
                app.choose_section()
                self.dismiss()
                app.action_view('results')
            except Exception as error:
                self.progress(str(error))
            finally:
                self.busy = False
            return
        row = self.query_one('#plugin-list', DataTable).cursor_row
        if event.button.id == 'plugin-save-setting':
            setting_id = self.query_one('#plugin-setting', Select).value
            if setting_id is Select.BLANK or not 0 <= row < len(self.entries):
                return
            plugin_id = self.entries[row]['id']
            raw = self.query_one('#plugin-setting-value', Input).value
            definition = next(d for d in self.settings if d['id']==setting_id and d['pluginId']==plugin_id)
            value = raw if definition['kind'] in ('string','enum') else parse_setting(raw)
            operation = lambda: self.service.nuclear.call('setting', plugin=plugin_id, id=setting_id, value=value)
        elif event.button.id == 'plugin-use':
            value = self.query_one('#plugin-provider', Select).value
            if value is Select.BLANK:
                return
            kind, provider_id = str(value).split('=', 1)
            operation = lambda: select_provider(kind, provider_id, self.service.store)
        else:
            if not 0 <= row < len(self.entries):
                return
            plugin_id = self.entries[row]['id']
            if event.button.id == 'plugin-install':
                loop = asyncio.get_running_loop()
                operation = lambda: install_plugin(plugin_id, self.service.store, progress=lambda text: loop.call_soon_threadsafe(self.progress, text))
            elif event.button.id == 'plugin-toggle':
                info = configuration(self.service.store)['installed'].get(plugin_id)
                if not info:
                    self.progress('Install this plugin first.')
                    return
                operation = lambda: set_enabled(plugin_id, not info['enabled'], self.service.store)
            else:
                return
        self.busy = True
        self.progress('Applying plugin selection…')
        for button in self.query(Button):
            if button.id != 'plugin-close':
                button.disabled = True
        try:
            await asyncio.to_thread(operation)
            if self.is_mounted:
                await self.refresh_installed()
                self.progress('Saved. New searches and playback use the selected providers.')
        except Exception as error:
            self.progress(str(error))
        finally:
            self.busy = False
            if self.is_mounted:
                for button in self.query(Button):
                    button.disabled = False
