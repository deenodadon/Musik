"""Read Nuclear's public plugin index; entries are not executable Musik plugins."""
import json
from urllib.request import Request, urlopen

REGISTRY_URL = 'https://raw.githubusercontent.com/NuclearPlayer/plugin-registry/master/plugins.json'


def list_plugins(plugin_id=None):
    request = Request(REGISTRY_URL, headers={'User-Agent': 'Musik plugin catalog'})
    with urlopen(request, timeout=20) as response:
        registry = json.loads(response.read(2_000_001))
    plugins = registry['plugins']
    if not isinstance(plugins, list):
        raise ValueError('Invalid Nuclear plugin registry')
    if plugin_id:
        plugins = [p for p in plugins if p.get('id') == plugin_id]
        if not plugins:
            raise ValueError('Plugin ID was not found in the Nuclear registry')
    print('Nuclear catalog — inspection only; Nuclear plugins cannot run in Musik yet.')
    for plugin in plugins:
        print(f"\n{plugin['id']} · {plugin.get('name', plugin['id'])} · {plugin.get('version', '?')}")
        print(plugin.get('description', ''))
        print(f"https://github.com/{plugin['repo']}")
    print('\nUse musik plugins --plugin ID to inspect a specific entry.')
