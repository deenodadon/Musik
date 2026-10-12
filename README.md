# Musik

A terminal music player with audio-reactive bars and persistent background playback, combining selected ideas from [Pear Desktop](https://github.com/pear-devs/pear-desktop) and [Limusic](https://github.com/SimoHypers/limusic). Built with Python, Textual and mpv, with no Electron, Tauri or browser window.

## Install on Nobara Linux

Run these commands in your terminal:

```sh
sudo dnf install -y git python3 python3-pip mpv
git clone https://github.com/deenodadon/Musik.git
cd Musik
python3 -m venv .venv
.venv/bin/python -m pip install .
mkdir -p ~/.local/bin
ln -s "$PWD/.venv/bin/musik" ~/.local/bin/musik
export PATH="$HOME/.local/bin:$PATH"
musik
```

To keep the short command available in future Bash terminals (Nobara's default), add the PATH setting once:

```sh
printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> ~/.bashrc
```

Then simply run `musik` to open the TUI. `musik start` does the same thing; `musik stop` stops background playback. For Zsh, add the PATH setting to `~/.zshrc` instead. Omit `--local` to open the YouTube search interface. Public YouTube requests may be restricted by YouTube; local playback requires no account or online service.

Close the terminal to leave music playing. Reopen `musik` to reconnect, or run `musik stop` to stop playback and shut down Musik.

## Run

Requires Python 3.11+, mpv and an audio output device. Linux and macOS are supported by the Unix socket player; Windows is not supported yet.

```sh
# Install mpv using your package manager first (apt install mpv / brew install mpv).
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/musik
# Or browse your own music without YouTube:
.venv/bin/musik --local ~/Music
```

For the prepared Debian cloud environment:

```sh
bash scripts/bootstrap-cloud.sh
PATH="/workspace/tools/bin:$PATH" .venv/bin/musik
```

The cloud can validate decoding using mpv's null audio output; audible playback requires a real audio device on the machine running Musik.

## Background playback and visualizer

When a song plays, the TUI displays live bars measured from mpv's decoded audio RMS levels. The bars show recent loudness history, not a frequency spectrum. Paused or stopped playback clears the display. mpv must include FFmpeg's `astats` filter (the tested cloud build does).

Playback runs in a detached per-user service. Closing the terminal, pressing Q, or leaving the TUI keeps the current song and queue playing. Open `musik` again to reconnect without restarting the song. Queue advancement, shuffle and repeat continue without an open terminal. The service stays available when the queue finishes, until explicitly stopped:

```sh
musik stop
```

This stops audio, terminates mpv and the Musik service, and closes any connected TUI when it next polls. Running it again safely reports that Musik is not running. It affects Musik's service for your current user/state directory, not unrelated players. Restarting your computer or an operating-system policy that kills all user processes ends background playback.

For commands without the `.venv/bin/` prefix, activate your environment first:

```sh
cd /workspace/Musik
source .venv/bin/activate
export PATH="/workspace/tools/bin:$PATH"  # Prepared cloud mpv
musik --local ~/Music
# In another terminal with the same environment activated:
musik stop
```

Only the background service writes shared state. Its private Unix control socket lives in a user-owned directory under the system temporary directory. A lock prevents duplicate services. `MUSIK_AUDIO_OUTPUT=null` is available for headless tests; leave it unset for audible playback.

## Features and keys

| Key | Action |
| --- | --- |
| `/` | Focus song search; enter `/local /path/to/music` to scan local files |
| Enter | Play selected track; add it to the queue if outside the queue |
| `1` / `2` / `3` / `4` / `5` | Search results / queue / favorites / history / account playlists |
| Ctrl+Enter or `o` | Play the opened playlist in order |
| `a` / `f` | Add to queue / toggle local favorite |
| Ctrl+P (anywhere) / Space (track list) | Pause or resume |
| `n` / `p` | Next / previous |
| Left / Right | Seek backward / forward 10 seconds |
| Ctrl+Up / Ctrl+Down | Adjust volume in 5% steps, including while searching |
| `+` or `=` / `-` | Alternative volume keys when the track list has focus |
| `s` / `r` | Toggle shuffle / repeat queue |
| `e` / `v` | Cycle flat, bass, vocal EQ / playback speed |
| Delete / Shift+Up / Shift+Down | Remove / reorder a queue entry |
| `l` | Show YouTube lyrics or adjacent local `.lrc` file |
| Escape / `q` | Focus track list / detach (music keeps playing) |

State is saved atomically to `$XDG_STATE_HOME/musik/state.json` (default `~/.local/state/musik`). Queue, position in queue, favorites, history and playback settings survive restarts. Reopening the TUI reconnects to active background playback. After the service is stopped, playback resumes only when requested; the exact elapsed song position is not restored. Corrupt state is reported without overwriting it. Local file titles come from filenames; embedded tags are not parsed yet.

Public song search uses ytmusicapi; stream resolution uses yt-dlp; decoding and audio output use mpv. Network errors appear in the TUI. YouTube can restrict requests or require sign-in; sign-in using an imported browser session is supported (see below). Favorites/history belong to Musik; account playlists can be imported. Lyrics display as text; `.lrc` timestamps are shown, not synchronized.

This version does not include public album/artist browsing, account write actions, gapless lookahead, crossfade, downloads, scrobbling, Discord, MPRIS or shared listening. See [UPSTREAMS.md](UPSTREAMS.md) for source provenance and hosted fork status.

## Sign in and import playlists

On your own computer, sign in at https://music.youtube.com in Firefox (or Chrome/Chromium/Brave/Edge) and complete any age-verification requirements in that browser. Then import that signed-in browser session:

```sh
musik login --browser firefox
# Other supported examples:
# musik login --browser chrome
# musik login --browser chromium
musik
```

If extraction fails, close the browser and retry. Browser profiles and keyring access must be available to your user. Use `--profile /path/to/browser/profile` for a non-default profile. The default Google account in that profile is used.

Login validates library access and imports playlist names. Press **5** in the TUI to refresh your account playlists; press **Enter** to open one, then **Ctrl+Enter** (or **O**) to play its full ordered queue. Enter on an individual song continues to start song radio. Imports are read-only and do not change the playlists in your Google account.

The imported YouTube cookies are stored with owner-only permissions in `$XDG_STATE_HOME/musik/youtube-cookies.txt` (default `~/.local/state/musik/youtube-cookies.txt`). Musik does not store your password, upload the session to GitHub, or extract unrelated-site cookies into that file. Treat the cookie file as a credential and do not share it. Expired sessions require repeating `musik login`.

Authenticated playback uses the same session through yt-dlp. This may allow age-restricted content your account is eligible to access; it does not bypass YouTube age verification, regional restrictions, membership requirements, or other access checks. YouTube may still restrict extraction or require additional playback tokens.

```sh
musik logout
```

Logout stops background playback and removes the imported cookies; it does not sign out your browser. Playlist names remain cached locally. Real account sign-in cannot be validated in the cloud without your browser session; tests use synthetic cookies and mocked account responses.

## Automatic radio queues

Press Enter on any song to replace the current queue with that song and its YouTube Music radio recommendations (up to 25 related songs, including similar artists). The queue view opens automatically. The selected song starts while recommendations load; failure to fetch recommendations does not prevent its playback. Each manual selection starts a new radio queue, including selecting a song already in the queue. Automatic next-track playback continues the current queue. Manually adding tracks with A keeps them alongside recommendations. Local songs queue other audio files in the same folder, since online recommendations require a YouTube song ID.

## Automatic updates

Each `musik` or `musik start` launch checks the official GitHub main branch in a detached background process. If it changed, Musik builds and installs the update into its own virtual environment. The TUI reports check/update status; startup and current playback continue. Updates appear on the next launch. An already-running playback service keeps its loaded code until stopped with `musik stop` or the computer restarts.

The updater uses a temporary checkout and leaves your `~/Musik` files and local edits unchanged. It requires Git and access to GitHub and Python package servers. Failed/offline checks retry on the next launch. It does not run on `musik stop`, in editable development installs, or in a system Python installation. Set `MUSIK_AUTO_UPDATE=0` to disable it. The first check establishes the installed commit receipt and may reinstall the current release once.

You need to install this version once to get the updater:

```sh
musik stop
cd ~/Musik
git pull origin main
.venv/bin/python -m pip install .
musik
```

After that, normal launches perform update checks automatically. Developer checkouts are not synchronized by the updater; use Git normally if editing the source yourself.

## Develop and test

```sh
.venv/bin/pytest -q
```

Tests exercise queue persistence, shuffle/repeat, real keyboard flows, measured visualizer levels, actual mpv decoding and controls, terminal hangup survival, background queue advancement, reconnecting, and `musik stop`. The mpv integration test skips only if mpv is absent. Set `MUSIK_MPV` to use a specific executable. The test uses `--ao=null`, so no speaker or audio server is needed.

YouTube requests need network access to `music.youtube.com`, `www.youtube.com`, `youtube.com`, `youtubei.googleapis.com` and `*.googlevideo.com`; artwork may use `i.ytimg.com`. In this cloud session, search returned a proxy 403. Required destinations were saved in the environment configuration draft; review/save and publish through environment settings before retrying online validation.

GPL-3.0-or-later. This is an unofficial client, unaffiliated with Google or YouTube.
