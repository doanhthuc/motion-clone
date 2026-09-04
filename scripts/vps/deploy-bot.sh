#!/usr/bin/env bash
# The ONLY command the GitHub Actions deploy key is allowed to run — see the
# `command=` restriction on its authorized_keys entry. Fast-forwards
# /opt/motion-clone to origin/main and restarts motion-bot. Never hand-edit
# this checkout: anything not committed to main is destroyed on the next
# deploy by `git reset --hard`.
set -euo pipefail
cd /opt/motion-clone
git fetch origin main
git reset --hard origin/main
systemctl restart motion-bot
sleep 2
systemctl is-active motion-bot
