#!/usr/bin/env bash
# Per-user Linux installation; Node is downloaded only when Node 22+ is absent.
set -euo pipefail
install_dir="${MUSIK_INSTALL_DIR:-$HOME/Musik}"
bin_dir="${MUSIK_BIN_DIR:-$HOME/.local/bin}"
missing=()
command -v git >/dev/null || missing+=(git)
command -v python3 >/dev/null || missing+=(python3)
command -v mpv >/dev/null || missing+=(mpv)
if command -v apt-get >/dev/null && command -v python3 >/dev/null && ! python3 -c 'import ensurepip' 2>/dev/null; then
  missing+=(python3-venv)
fi
if ((${#missing[@]})); then
  if command -v dnf >/dev/null; then sudo dnf install -y "${missing[@]}" python3-pip
  elif command -v apt-get >/dev/null; then sudo apt-get update; sudo apt-get install -y "${missing[@]}" python3-venv python3-pip
  elif command -v pacman >/dev/null; then sudo pacman -S --needed "${missing[@]/python3/python}" python-pip
  elif command -v zypper >/dev/null; then sudo zypper --non-interactive install "${missing[@]}" python3-pip
  else echo 'Install git, Python 3.11+ and mpv, then rerun this installer.' >&2; exit 1
  fi
fi
node_major=0
if command -v node >/dev/null; then node_major=$(node -p 'process.versions.node.split(".")[0]'); fi
runtime_dir="${XDG_DATA_HOME:-$HOME/.local/share}/musik/runtime"
if ((node_major < 22)) && [[ -x "$runtime_dir/bin/node" ]]; then
  node_major=$("$runtime_dir/bin/node" -p 'process.versions.node.split(".")[0]')
fi
if ((node_major < 22)); then
  case "$(uname -m)" in x86_64) node_arch=x64;; aarch64|arm64) node_arch=arm64;; *) echo 'Automatic Node installation supports Linux x86_64 and ARM64.' >&2; exit 1;; esac
  task_tmp=$(mktemp -d)
  trap 'rm -rf "$task_tmp"' EXIT
  # Python verifies the official release checksum before extracting anything.
  python3 - "$task_tmp" "$node_arch" <<'PY'
import hashlib, pathlib, sys, urllib.request
folder, arch = pathlib.Path(sys.argv[1]), sys.argv[2]
base = 'https://nodejs.org/dist/latest-v24.x/'
checksums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30).read().decode()
expected, name = next(line.split() for line in checksums.splitlines() if line.endswith(f'-linux-{arch}.tar.xz'))
archive = folder / name
with urllib.request.urlopen(base + name, timeout=60) as response, archive.open('wb') as output:
    while chunk := response.read(1024 * 1024):
        output.write(chunk)
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('Node checksum validation failed')
(folder / 'archive-name').write_text(name)
PY
  node_archive=$(cat "$task_tmp/archive-name")
  mkdir -p "$runtime_dir"
  tar -xJf "$task_tmp/$node_archive" -C "$runtime_dir" --strip-components=1
fi
if [[ -x "$runtime_dir/bin/node" ]]; then export PATH="$runtime_dir/bin:$PATH"; fi
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)'
if [[ ! -e "$install_dir" ]]; then git clone --branch main https://github.com/deenodadon/Musik.git "$install_dir"
else
  [[ -d "$install_dir/.git" ]] || { echo 'Install directory is not a Git checkout.' >&2; exit 1; }
  [[ "$(git -C "$install_dir" remote get-url origin)" == 'https://github.com/deenodadon/Musik.git' ]] || { echo 'Install directory has a different origin.' >&2; exit 1; }
  [[ -z "$(git -C "$install_dir" status --porcelain)" ]] || { echo 'Preserve local source changes before reinstalling.' >&2; exit 1; }
  git -C "$install_dir" pull --ff-only origin main
fi
python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade "$install_dir"
mkdir -p "$bin_dir"
# Generate a launcher with safely quoted paths, including the private runtime.
python3 - "$bin_dir/musik" "$install_dir/.venv/bin/python" "$runtime_dir/bin" <<'PY'
import pathlib, shlex, sys
launcher, python, runtime = sys.argv[1:]
path = pathlib.Path(launcher)
if path.is_symlink():
    path.unlink()
path.write_text('#!/bin/sh\nexport PATH=' + shlex.quote(runtime) + ':"$PATH"\nexec ' + shlex.quote(python) + ' -I -m musik.app "$@"\n')
path.chmod(0o755)
PY
printf 'Musik installed. Run %s/musik. Add %s to PATH to use musik everywhere.\n' "$bin_dir" "$bin_dir"
