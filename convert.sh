#!/usr/bin/env bash
# convert.sh <dump-dir> <out-dir>
set -euo pipefail
DUMP="$(realpath "$1")"
OUT="$2"
SRC="$HOME/AnyPS5"
cd "$SRC"

[ -f "$DUMP/eboot.bin" ] || { echo "No eboot.bin in $DUMP"; exit 1; }

RELINKER=$(find build -type f -name relinker -perm -u+x 2>/dev/null | head -n1)
if [ -z "$RELINKER" ] || [ ! -d build/core/libs/libs ]; then
  echo "== building relinker and libs"
  cmake --build build --target libs relinker
  RELINKER=$(find build -type f -name relinker -perm -u+x | head -n1)
fi
[ -n "$RELINKER" ] || { echo "relinker binary not found"; exit 1; }

is_elf() { [ "$(head -c4 "$1" | od -An -tx1 | tr -d ' ')" = 7f454c46 ]; }

STAGE="$OUT.stage"
rm -rf "$STAGE" "$OUT"
mkdir -p "$STAGE/sce_module" "$OUT"

echo "== staging executable and modules"
if is_elf "$DUMP/eboot.bin"; then cp "$DUMP/eboot.bin" "$STAGE/eboot.elf"
else python3 -I tools/unwrap_self.py "$DUMP/eboot.bin" "$STAGE/eboot.elf"; fi
if [ -d "$DUMP/sce_module" ]; then
  for m in "$DUMP"/sce_module/*; do
    [ -f "$m" ] || continue
    b=$(basename "$m")
    if is_elf "$m"; then cp "$m" "$STAGE/sce_module/$b"
    else python3 -I tools/unwrap_self.py "$m" "$STAGE/sce_module/$b" || echo "skip $b (could not unwrap)"; fi
  done
fi

echo "== relinking"
"$RELINKER" "$STAGE/eboot.elf" "$OUT/app.elf"

echo "== linking game files into app0"
mkdir -p "$OUT/app0"
for e in "$DUMP"/* "$DUMP"/.[!.]*; do
  [ -e "$e" ] || continue
  b=$(basename "$e")
  [ "$b" = sce_module ] && continue
  [ -e "$OUT/app0/$b" ] || ln -s "$e" "$OUT/app0/$b"
done
ln -sfn "$SRC/build/core/libs/libs" "$OUT/libs"
rm -rf "$STAGE"
echo "== done"
