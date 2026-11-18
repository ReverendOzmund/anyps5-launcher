#!/usr/bin/env bash
# Linux install. Run from a normal terminal on the host (not inside the distrobox).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/anyps5-launcher"
BOX="${ANYPS5_BOX:-anyps5-build}"

mkdir -p "$DEST"
cp "$HERE"/launcher.py "$HERE"/anyps5-launcher.svg "$DEST"/
rm -f "$DEST"/convert.sh "$DEST"/update.sh
chmod +x "$DEST"/launcher.py

if command -v distrobox >/dev/null && distrobox list --no-color 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); print $2}' | grep -qx "$BOX"; then
  echo "== installing PySide6 in the $BOX distrobox"
  distrobox enter "$BOX" -- bash -lc "rpm -q python3-pyside6 >/dev/null 2>&1 || sudo dnf install -y python3-pyside6 vulkan-tools"
  EXEC="distrobox enter $BOX -- python3 $DEST/launcher.py"
else
  echo "== no '$BOX' distrobox, using the host Python"
  python3 -c "import PySide6" 2>/dev/null || python3 -m pip install --user PySide6
  EXEC="python3 $DEST/launcher.py"
fi

mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps" "$HOME/.local/share/applications"
cp "$HERE/anyps5-launcher.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/anyps5-launcher.svg"

DESKTOP="$HOME/.local/share/applications/anyps5-launcher.desktop"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=AnyPS5 Launcher
Comment=Convert and play PS5 games with AnyPS5
Exec=$EXEC
Icon=anyps5-launcher
Terminal=false
Categories=Game;Emulator;
StartupWMClass=anyps5-launcher
DESK
chmod +x "$DESKTOP"

DESKDIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKDIR" ]; then
  cp "$DESKTOP" "$DESKDIR/anyps5-launcher.desktop"
  chmod +x "$DESKDIR/anyps5-launcher.desktop"
  gio set "$DESKDIR/anyps5-launcher.desktop" metadata::trusted true 2>/dev/null || true
fi
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "== done. Start it from the app menu (AnyPS5 Launcher) or the desktop icon."
