# Musik

A terminal YouTube Music and local audio player with visualizer bars, artist and album browsing, automatic radio queues, playlists, and background playback. Inspired by [Pear Desktop](https://github.com/pear-devs/pear-desktop) and [Limusic](https://github.com/SimoHypers/limusic).

Install Git and [GitHub CLI](https://cli.github.com/) first. Accept your repository invitation if it is private, then sign in once using your own GitHub account:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

The one-line commands below work for public and private repositories using that saved login.

## Windows

Install with this one-line PowerShell command (Windows 10/11 with WinGet):

```powershell
& { $ErrorActionPreference = "Stop"; gh api -H "Accept: application/vnd.github.raw+json" "repos/deenodadon/Musik/contents/scripts/install-windows.ps1?ref=main" | Set-Content -Encoding UTF8 "$env:TEMP\install-musik.ps1"; if ($LASTEXITCODE -ne 0) { throw "GitHub download failed; check your login and repository access." }; powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\install-musik.ps1" }
```

The installer sets up Musik, Python, Git, mpv and Node.js 22+ when needed. It does not install a browser. Open a **new PowerShell** and run `musik`.

## Linux

Install on Nobara/Fedora, Ubuntu/Debian, Arch, or openSUSE:

```bash
(task_script=$(mktemp) && trap 'rm -f "$task_script"' EXIT && gh api -H 'Accept: application/vnd.github.raw+json' 'repos/deenodadon/Musik/contents/scripts/install-linux.sh?ref=main' > "$task_script" && bash "$task_script")
```

The installer sets up dependencies and downloads a verified official Node.js runtime if Node 22+ is missing. Then run `~/.local/bin/musik`, or add `~/.local/bin` to PATH to use `musik`.

**Private repository:** Musik v0.9.1+ uses Git's saved credentials for automatic updates without prompting. The authenticated one-line commands also work when the repo is private. Never put a GitHub token in a command URL or share one between users.

## Use

| Command | Action |
| --- | --- |
| `musik` | Open the player or reconnect to background playback |
| `musik login` | Open a fresh sign-in window; close it after your library loads |
| `musik logout` | Stop playback and remove the saved account and playlist cache |
| `musik stop` | Stop music and shut down the background service |
| `musik reset` | Permanently clear all Musik data; preserve the installation and music files |
| `musik --local PATH` | Browse local music |

Closing the terminal or pressing **Q** keeps music playing. Launches check for updates in the background; installed changes appear on the next launch. Restart the service with `musik stop` to load playback-service changes.

| Key | Action |
| --- | --- |
| `/`, Enter | Search songs/artists/albums; select an artist or album to browse |
| Songs / Albums / Artists / Videos tabs | Browse separate result sections (F6 / F7 / F8 / F9) |
| Backspace | Return to the previous artist/album page |
| Ctrl+P | Pause/resume, including while searching |
| Ctrl+Up / Ctrl+Down | Volume up/down |
| `n` / `p` | Next/previous |
| `1`–`5` | Results, queue, favorites, history, playlists |
| Enter, then `o` | Open an album or playlist, then play it in order |
| `a` / `f` / `l` | Add to queue / favorite / lyrics |
| `s` / `r` / `e` / `v` | Shuffle / repeat / equalizer / speed |
| Ctrl+Left / Ctrl+Right | Seek backward / forward 10 seconds from any focus |
| Left / Right | Seek 10 seconds when the track list is focused |
| Esc / `q` | Focus track list / detach |

For YouTube streams rejected with HTTP 403, run `musik setup-playback` once, then `musik stop` and reopen Musik. Installers attempt this setup automatically.

The playback indicator shows Ready, Loading, Playing, Paused, or Failed. Technical setup details are available through `musik doctor`.

Playback troubleshooting: run `musik doctor` and share its output. It reports versions, session readiness, and audio-format availability without printing credentials.

Press `r` to cycle **Off → Song → Playlist**. Song repeats only the current track, including in background playback; Next still skips it.

Selecting a track already in your queue preserves that queue. With shuffle off, the next track follows its displayed order.

Click **Queue** at the top (or press `2`) to see your queue. The current track stays highlighted with a ▶ marker while you browse other rows. Enter plays a selected track, Delete removes it, and Shift+Up/Down reorders it.

Search a title and open **Videos** to choose from YouTube uploads, covers, remixes, and alternate versions. Paste a YouTube video link into search to select that exact upload. Enter plays its audio.

Exact artist-name searches use the artist’s own albums and songs, with at least 25 songs when available. Artist pages show top songs, an all-songs collection when available, and albums/singles. Select a song to start radio, or open an album and press `o` to play it in order.

Windows sign-in detects your default browser; the installer does not install a browser. Linux uses Brave by default. Override with `--browser chrome`, `edge`, `brave`, `chromium`, or `firefox`. Sign-in opens YouTube in a fresh temporary profile to establish playback cookies, including Firefox, and saves your session only after successful sign-in. It grants only access your Google account is eligible for. YouTube may restrict streaming even when signed in.

## Uninstall Windows

Removes Musik and its saved data, while keeping shared prerequisites and personal music:

```powershell
& { $ErrorActionPreference = "Stop"; gh api -H "Accept: application/vnd.github.raw+json" "repos/deenodadon/Musik/contents/scripts/uninstall-windows.ps1?ref=main" | Set-Content -Encoding UTF8 "$env:TEMP\uninstall-musik.ps1"; if ($LASTEXITCODE -ne 0) { throw "GitHub download failed; check your login and repository access." }; powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\uninstall-musik.ps1" }
```

Add `-KeepData` to keep saved Musik data. Use `-InstallDirectory PATH` for a custom installation. Edited source checkouts are preserved in a sibling backup folder. Close/reopen PowerShell afterward.

## Uninstall Linux

```bash
(task_script=$(mktemp) && trap 'rm -f "$task_script"' EXIT && gh api -H 'Accept: application/vnd.github.raw+json' 'repos/deenodadon/Musik/contents/scripts/uninstall-linux.sh?ref=main' > "$task_script" && bash "$task_script")
```

Stops playback and removes Musik and saved data, keeping shared prerequisites and personal music. Add `--keep-data` after `bash "$task_script"` to retain saved data. For custom locations, set `MUSIK_INSTALL_DIR` and `MUSIK_BIN_DIR`; edited source is backed up before removal.

Queued songs prepare their stream links in the background to reduce loading when selecting any queued song. This does not guarantee instant or gapless playback. Open **Plugins** (F10) to install or uninstall Nuclear music-provider plugins, choose providers, and configure settings. To remove a plugin from the command line, use `musik plugins --uninstall ID`; this deletes its files and settings and restores any selected providers to Musik defaults. Desktop-only plugins require separate terminal adapters.

**Torrent audio:** Open **Torrents (F11)**, save your TorBox API key, and search. Musik includes standalone qBittorrent Nova search with Solid Torrents and TorrentsCSV; qBittorrent does not need to be installed or running. Enter opens a torrent's audio files in TorBox; Enter again streams a file directly to mpv, or **O** queues the collection. No audio files are saved locally. Uncached torrents must finish on TorBox first; select the result again to check progress. Search sites may be unavailable on your network.

Click **Cached torrents** to return to completed torrents containing audio files. Videos, archives, torrents without audio, and unfinished downloads are excluded. The list survives restarts, refreshes your TorBox library, and reopens the exact torrent by its ID. Lists are kept separate for each TorBox API key.

Torrent search uses music categories where available, requires positive seed counts, and excludes archive-labelled releases. With TorBox connected, it resolves engine download links, checks cached contents, and asks TorBox for uncached torrent metadata before keeping audio results. Archive-only/non-audio releases, movies, shows, and unverifiable contents are excluded, even when the title says FLAC. Engine searches share a 30-second deadline; audio verification has a 20-second budget and checks up to 80 of the best-seeded candidates. Slow engines cannot hold up completed matches indefinitely. Non-audio and unverified results stay excluded; if nothing survives verification, Musik explains why. Without TorBox connected, only categories, titles, and seed counts can be checked.

You can also search `/torrent QUERY` or paste a magnet into Musik's search box. `musik torrents --connect-torbox` saves a key through a hidden prompt; `musik torrents --disconnect-torbox` removes it. Install additional trusted Nova plugins through F11 or `musik torrents --install-engine URL_OR_PATH`. Plugins run Python as your user. TorBox requires your own account/API key; magnet links and direct `.torrent` links are supported, while site-specific download pages may need a magnet link instead.

[Advanced options and development](docs/advanced.md) · [Upstream sources](UPSTREAMS.md) · [License](LICENSE)

GPL-3.0-or-later. Unofficial; unaffiliated with Google or YouTube.
