# Advanced options

- Python 3.11+ and mpv are required. Online playback also needs Node.js 22+ or a supported Deno/Bun runtime. Google sign-in opens a fresh temporary browser profile, including Firefox. Windows detects the default browser without installing one; passwords are entered on Google's page.
- `musik login --browser brave --browser-session` imports an existing browser profile. Add `--account 1` for its second Google account, or `--profile PATH` for a specific profile. Existing-session import is optional; normal sign-in never uses your ordinary browser profile. `--import-only` skips the browser launch and prompt.
- Imported cookies are private credentials. Unix stores data in `$XDG_STATE_HOME/musik` (default `~/.local/state/musik`); Windows uses `%LOCALAPPDATA%\musik` with inherited user-directory access controls. Do not share the cookie file. Reset does not sign out your ordinary browser.
- Windows installs under `%LOCALAPPDATA%\Programs\Musik`. The installer supports `-SkipDependencies` and `-InstallDirectory PATH`; the uninstaller supports `-KeepData` and the same custom path.
- Set `MUSIK_AUTO_UPDATE=0` to disable update checks. Editable development installs do not auto-update. The updater changes the installed package, not your Git checkout. Offline/failed checks retry next launch.
- Visualizer bars show measured loudness history, not frequency bins. Local titles come from filenames. Lyrics are text-only; adjacent `.lrc` files are supported. Exact elapsed playback position is not restored after stopping the service.
- Delete removes queue entries; Shift+Up/Down reorders them. Left/Right seeks 10 seconds. `+` or `=` and `-` adjust volume with the track list focused.
- Song selection starts a new radio queue; playing a whole playlist preserves playlist order. Local radio queues other files in the same folder. Account library imports are read-only.
- Page-reload errors retry supported YouTube clients; public songs can fall back to anonymous streaming without removing your account. Explicit age/access denials remain errors. Sign-in and playback still depend on YouTube and browser keyring access.

## Development

Install mpv and Node, then:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

On Windows, use `.venv\Scripts\python.exe`. CI exercises Linux and Windows playback, background control, and PowerShell installation. mpv tests use null audio and skip if mpv is absent. Live Google sign-in needs a browser session on your own machine.

`MUSIK_MPV` selects an mpv executable; `MUSIK_AUDIO_OUTPUT=null` enables headless playback. Cloud setup: `bash scripts/bootstrap-cloud.sh` under `/workspace/Musik`, then include `/workspace/tools/bin` in PATH.


### Queue preparation and Nuclear catalog

Musik prepares stream links for all queued songs in the background, one lookup
at a time. Selecting a prepared song reuses its link; selecting a song currently
being prepared shares that lookup. Up to 256 links are cached; links expire and are refreshed. This reduces
lookup delays but does not provide guaranteed instant or gapless playback:
network buffering and audio decoding still take time. Alternate audio is only
requested after the original stream fails.

## Nuclear plugins

Open **Plugins** in the main menu or press **F10**. Choose a registry entry,
click **Install**, and select providers for search, playback, lyrics, charts,
playlists, or discovery. **Charts** opens the selected dashboard. Simple plugin
settings can be edited in the same menu; password/token fields hide typed values.
Plugins run as your user, so install only plugins you trust.

The installer verifies the indexed GitHub release's SHA-256 digest, installs
production dependencies without running package scripts, and bundles JavaScript
or TypeScript for a Node.js 22+ host. Installed release sources and manifests
remain in Musik's private data directory. Plugin licenses remain their authors'.
Missing GitHub checksums, unsupported desktop APIs, and malformed releases are
reported rather than silently accepted.

Command-line equivalents work on Linux and Windows:

```text
musik plugins
musik plugins --install nuclear-plugin-bandcamp
musik plugins --install nuclear-plugin-lrclib
musik plugins --installed
musik plugins --provider metadata=bandcamp
musik plugins --provider streaming=bandcamp-stream
musik plugins --provider streaming=builtin
musik plugins --disable nuclear-plugin-bandcamp
musik plugins --enable nuclear-plugin-bandcamp
musik plugins --plugin PLUGIN_ID --setting SETTING_ID=VALUE
```

Installing a plugin selects the first available provider of each kind, except
streaming: native YouTube playback remains the default. Bandcamp, SoundCloud,
and other metadata providers with their own streaming provider use it for their
own songs. Metadata-only songs use a YouTube audio match unless you select a
plugin streaming provider. Videos remain available in their existing result tab.
A selected plugin streaming provider can resolve audio for other providers by
artist/title, so it may choose a different upload. Choose **Musik default** to
restore the original playback method and its alternate-stream retry.

Paste a playlist URL into search after selecting a matching playlists provider,
then open it with Enter. Playlists created or imported by plugins appear in
Musik's Playlists menu without requiring a YouTube login. Plugin data and
settings are removed by `musik reset`.

The bridge covers music-provider APIs: metadata, streaming, lyrics, dashboards,
playlist URLs/storage, and discovery, plus settings, HTTP, and yt-dlp. Registry
installation/loading was checked for 17 provider plugins: Discogs, YouTube,
Bandcamp, SoundCloud, Spotify metadata, Deezer Dashboard, MusicBrainz,
ListenBrainz Dashboard, YouTube Playlists, KHInsider, OmniSource, Bandcamp
Dashboard, SoundCloud Dashboard, NetEase, NuclearTube, LRCLIB, and Kugou.
Loading confirms host compatibility, not availability of every remote service
or every provider feature. Services may still require credentials or impose
access restrictions.

Last.fm, YouTube Liked Songs Sync, Spotify Home, Mini Player, TXT Queue, and
MediaSession require additional desktop/UI/system adapters. React authentication
widgets and Tauri/browser windows are not rendered in the terminal. These plugins
are reported as unsupported instead of being presented as working music sources.

Development checks for actual installed releases can use
`MUSIK_NUCLEAR_TEST_STORE=/path/to/musik pytest tests/test_nuclear_upstream.py`.
Those checks run the unchanged Bandcamp, LRCLIB, and YouTube plugin code against
controlled network responses. Live Bandcamp requests are blocked by the cloud
proxy used during development; local users' remote-service playback is not
claimed as verified by those checks.
