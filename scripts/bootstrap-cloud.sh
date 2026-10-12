#!/usr/bin/env bash
set -euo pipefail
cd /workspace/Musik
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
if ! command -v mpv >/dev/null && [ ! -x /workspace/tools/bin/mpv ]; then
  # Rootless Debian installation. APT verifies signed metadata and package hashes.
  task_apt=$(mktemp -d /tmp/musik-apt.XXXXXX)
  trap 'rm -rf "$task_apt"' EXIT
  mkdir -p "$task_apt/lists/partial" "$task_apt/cache/archives/partial" /workspace/tools/mpv /workspace/tools/bin
  printf '%s\n' 'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main' > "$task_apt/sources.list"
  apt_options=(-o Dir::Etc::parts=- -o "Dir::Etc::sourcelist=$task_apt/sources.list" -o Dir::Etc::sourceparts=- -o "Dir::State::lists=$task_apt/lists" -o "Dir::Cache=$task_apt/cache")
  /usr/bin/apt-get "${apt_options[@]}" update
  (cd "$task_apt" && /usr/bin/apt-get "${apt_options[@]}" download mpv libmujs3 liblua5.2-0 libuchardet0 libsndio7.0 libsixel1 libva-wayland2)
  for package in "$task_apt"/*.deb; do dpkg-deb -x "$package" /workspace/tools/mpv; done
  cat > /workspace/tools/bin/mpv <<'WRAPPER'
#!/bin/sh
export LD_LIBRARY_PATH="/workspace/tools/mpv/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec /workspace/tools/mpv/usr/bin/mpv "$@"
WRAPPER
  chmod +x /workspace/tools/bin/mpv
fi
export PATH="/workspace/tools/bin:$PATH"
mpv --version
.venv/bin/python -m pytest -q
