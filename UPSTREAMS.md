# Upstream projects

Musik is a new Python/Textual TUI implementation informed by these projects. It is not a merged Electron/Tauri application and does not claim parity with either desktop client. Source checkouts were obtained for inspection, not hosted GitHub forks.

| Project | Inspected commit | Features selected |
| --- | --- | --- |
| [Pear Desktop](https://github.com/pear-devs/pear-desktop) | `122e72a7399dac24e87b4317a9f1a8ed00836fb3` | Keyboard controls, playback speed, precise volume, equalizer presets, lyrics |
| [Limusic](https://github.com/SimoHypers/limusic) | `927cf5d5bd1528514e1f542ae9154d54ceea7934` | Browser-free mpv playback, local music, restored queue, search, favorites and history |

The desktop source trees are not shipped inside the Python package. Their browser-dependent UI cannot run in a terminal, so Musik implements its own interface and orchestration using Textual, ytmusicapi, yt-dlp and mpv. Limusic's distinction between paused and idle playback informed the event handling. The equalizer presets are Musik's own FFmpeg filter settings, not a port of Pear's Web Audio filters.

Musik is licensed GPL-3.0-or-later, compatible with Limusic's GPL-3.0-or-later and Pear's MIT license. The GPL license is provided in LICENSE; Pear's notice is retained in third_party/pear-desktop-MIT.txt for attribution. The upstream repositories retain their own copyrights and licenses.

GitHub fork creation was blocked by a Forbidden response from the authenticated GitHub API. After GitHub CLI authentication is restored, run `bash scripts/fork-upstreams.sh` to create both hosted forks in the authenticated user's account. This does not push or publish Musik.

YouTube playback-token support uses [BgUtils POT Provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider), version 2.0.2 (GPL-3.0-only), and its pinned yt-dlp plugin. The helper is downloaded and built locally during setup.
