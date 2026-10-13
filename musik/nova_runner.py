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
            import contextlib
            import io
            from helpers import download_file
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                instance.download_torrent(query)
            lines = output.getvalue().strip().splitlines()
            value = lines[-1] if lines else ''
            # Some Nova plugins hand the client an HTTP URL rather than
            # downloading metadata themselves. Resolve it within our sandbox.
            first = value.split(' ', 1)[0]
            if first.startswith(('https://', 'http://')):
                print(download_file(first))
            else:
                print(value)
        else:
            from helpers import download_file
            print(download_file(query))
        sys.exit(0)
    category = Category.music if 'music' in getattr(plugin, 'supported_categories', {}) else Category.all
    sys.exit(0 if run_search((plugin, quote(query), category)) else 1)
