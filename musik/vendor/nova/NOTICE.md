# qBittorrent Nova attribution

This directory bundles the standalone Python Nova search engine from
https://github.com/qbittorrent/qBittorrent, revision
df402f91d4567248507b09fa1efb503270be2776, and two official search plugins from
https://github.com/qbittorrent/search-plugins, revision
ade5eee5e174b758588f4fbbc540d151de7b3e16.

The Nova files and torrentscsv plugin carry BSD-style redistribution terms;
solidtorrents carries MIT terms. Each file retains its upstream authorship,
license conditions, and warranty disclaimer. The embedded socks module retains
its upstream license and copyright notices. See the headers of the distributed
source files for their complete terms. Musik does not claim authorship of these
files or endorsement by qBittorrent or the plugin authors.

Musik runs search plugins through its own subprocess adapter. No qBittorrent
GUI, Web UI, torrent client, or local BitTorrent downloader is required.
