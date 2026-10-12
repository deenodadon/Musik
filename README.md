# Musik

A terminal YouTube Music and local audio player with visualizer bars, automatic radio queues, playlists, and background playback. Inspired by [Pear Desktop](https://github.com/pear-devs/pear-desktop) and [Limusic](https://github.com/SimoHypers/limusic).

## Windows

Install with this one-line PowerShell command (Windows 10/11 with WinGet):

```powershell
& { $ErrorActionPreference = "Stop"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/deenodadon/Musik/main/scripts/install-windows.ps1" -OutFile "$env:TEMP\install-musik.ps1"; powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\install-musik.ps1" }
```

The installer sets up Musik, Python, Git, mpv, Node.js and Brave. Open a **new PowerShell** and run `musik`.

## Nobara Linux

```bash
sudo dnf install -y git python3 python3-pip mpv nodejs
git clone https://github.com/deenodadon/Musik.git ~/Musik
cd ~/Musik
python3 -m venv .venv
.venv/bin/python -m pip install .
mkdir -p ~/.local/bin
ln -s "$HOME/Musik/.venv/bin/musik" ~/.local/bin/musik
export PATH="$HOME/.local/bin:$PATH"
musik
```

Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` once to keep the command available.

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
| `/`, Enter | Search, then play a selected song and its radio queue |
| Ctrl+P | Pause/resume, including while searching |
| Ctrl+Up / Ctrl+Down | Volume up/down |
| `n` / `p` | Next/previous |
| `1`–`5` | Results, queue, favorites, history, playlists |
| Enter, then `o` | Open a playlist, then play it in order |
| `a` / `f` / `l` | Add to queue / favorite / lyrics |
| `s` / `r` / `e` / `v` | Shuffle / repeat / equalizer / speed |
| Esc / `q` | Focus track list / detach |

Sign-in uses Brave by default; Chrome, Chromium and Edge can be selected with `--browser`. It grants only access your Google account is eligible for. YouTube may restrict streaming even when signed in.

## Uninstall Windows

Removes Musik and its saved data, while keeping shared prerequisites and personal music:

```powershell
& { $ErrorActionPreference = "Stop"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/deenodadon/Musik/main/scripts/uninstall-windows.ps1" -OutFile "$env:TEMP\uninstall-musik.ps1"; powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\uninstall-musik.ps1" }
```

Add `-KeepData` to keep saved Musik data. Use `-InstallDirectory PATH` for a custom installation. Edited source checkouts are preserved in a sibling backup folder. Close/reopen PowerShell afterward.

[Advanced options and development](docs/advanced.md) · [Upstream sources](UPSTREAMS.md) · [License](LICENSE)

GPL-3.0-or-later. Unofficial; unaffiliated with Google or YouTube.
