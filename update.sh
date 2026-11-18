#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/AnyPS5"
git am --abort 2>/dev/null || true
echo "== updating AnyPS5 main"
git checkout -q -f main
git pull --ff-only origin main
git submodule update --init --recursive
echo "== building relinker and libs"
cmake --build build --target libs relinker
echo "== now at $(git rev-parse --short HEAD)"
