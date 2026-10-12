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

`musik plugins` reads the live Nuclear registry. Use
`musik plugins --plugin nuclear-plugin-bandcamp` to inspect a specific plugin.
This is a catalog, not an installer or plugin runtime. Nuclear plugins require
Nuclear's JavaScript host APIs; they cannot execute directly in Musik's Python
and mpv backend. A provider adapter or a compatible host must be implemented
before a plugin can be enabled. UI plugins additionally need a terminal interface.
