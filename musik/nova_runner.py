"""Run one qBittorrent Nova search plugin in its own process."""
import sys
from urllib.parse import quote

if __name__ == '__main__':
    root, engine, query, *mode = sys.argv[1:]
    sys.path.insert(0, root)
    from nova2 import Category, import_engine, run_search
    plugin = import_engine(engine)
    if plugin is None:
        sys.exit(1)
    if mode == ['link']:
        instance = plugin()
        if hasattr(instance, 'download_torrent'):
            instance.download_torrent(query)
        else:
            from helpers import download_file
            print(download_file(query))
        sys.exit(0)
    category = Category.music if 'music' in getattr(plugin, 'supported_categories', {}) else Category.all
    sys.exit(0 if run_search((plugin, quote(query), category)) else 1)
