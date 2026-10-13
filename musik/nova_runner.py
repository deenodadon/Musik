"""Run one qBittorrent Nova search plugin in its own process."""
import sys
from urllib.parse import quote

if __name__ == '__main__':
    root, engine, query = sys.argv[1:]
    sys.path.insert(0, root)
    from nova2 import Category, import_engine, run_search
    plugin = import_engine(engine)
    if plugin is None:
        sys.exit(1)
    sys.exit(0 if run_search((plugin, quote(query), Category.all)) else 1)
