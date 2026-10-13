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

**Torrent audio:** Musik and its maintainers do not host or distribute torrent content and accept no responsibility for users’ use of it. Users are responsible for what they access and for complying with applicable laws, permissions, and service terms.

Open **Torrents (F11)**, choose engines, save your own **TorBox API key**, and search. Solid Torrents, TorrentsCSV, and RockBox are included; qBittorrent is not required. Press **Enter** to open a torrent, then **Enter** to stream a track or **O** to queue the collection. TorBox must finish caching first; no audio files are saved locally.

- **Filter results:** Type `flac`, `lossless`, or `24bit` below the list. This matches release labels, not verified quality.
- **Cached torrents:** Reopen completed audio collections, including supported ZIPs. ZIP streaming needs server byte-range support and mpv archive support; encrypted ZIPs are unsupported and seeking may be slower.
- **Search rules:** Positive seed counts only; music categories and content checks exclude unsupported/non-audio releases. ZIP contents are checked when opened. Site availability and metadata checks can limit results.
- **More engines:** Install trusted Nova `.py` plugins through F11. Plugins execute Python as your user. You can also search `/torrent QUERY` or paste a magnet.

[Advanced options and development](docs/advanced.md) · [Upstream sources](UPSTREAMS.md) · [License](LICENSE)

GPL-3.0-or-later. Unofficial; unaffiliated with Google or YouTube.
