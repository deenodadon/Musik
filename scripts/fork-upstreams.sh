#!/usr/bin/env bash
set -euo pipefail
# Explicitly creates hosted forks; never places credentials in repository files.
gh api user --jq .login >/dev/null
gh repo fork pear-devs/pear-desktop --clone=false
gh repo fork SimoHypers/limusic --clone=false
