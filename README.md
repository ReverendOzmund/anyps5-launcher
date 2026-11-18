# AnyPS5 Launcher

A desktop launcher for [AnyPS5](https://github.com/boykopovar/AnyPS5) on Linux. It keeps a library of your converted PS5 games, converts new dumps with the AnyPS5 relinker, and launches them with per-game settings and a live log.

Built and tested on Bazzite (KDE, NVIDIA). AnyPS5 is built in a Fedora distrobox, and the launcher runs inside that distrobox.

## Features

- **Library** of converted games with their title name, title ID and icon from `sce_sys/`
- **Add game:** pick a dump folder (the one with `eboot.bin` in it). The launcher unwraps SELF files if needed, relinks the executable and modules, and links the game files into `app0/` instead of copying them.
- **Play / Stop** with the game's output shown live and saved to `~/anyps5-logs/`
- **Per-game settings:** GPU, present mode, fullscreen, FPS limit, mimalloc, Feral GameMode, console language and extra environment variables
- **Reconvert** after an AnyPS5 update (settings are kept)
- **Remove** deletes only the converted copy, never your dump
- **Update AnyPS5** pulls the latest `main` and rebuilds the relinker and libraries

## Requirements

- AnyPS5 cloned to `~/AnyPS5` and built in `~/AnyPS5/build` (see AnyPS5's `docs/dev/BUILD.md`)
- A distrobox named `anyps5-build` with the AnyPS5 build dependencies
- Your own legally obtained, decrypted PS5 game dumps

## Install

From a normal terminal on the host, not inside the distrobox:

```bash
git clone https://github.com/ReverendOzmund/anyps5-launcher.git
bash anyps5-launcher/install.sh
```

This installs PySide6 in the `anyps5-build` distrobox, copies the launcher to `~/.local/share/anyps5-launcher`, and adds **AnyPS5 Launcher** to the app menu and the desktop.

## Paths

| What | Where |
|---|---|
| AnyPS5 source and build | `~/AnyPS5`, `~/AnyPS5/build` |
| Converted games | `~/PS5/anyps5/<title id>/` |
| Logs | `~/anyps5-logs/` |

## Notes

This is an unofficial front end. It is not part of AnyPS5 and is not endorsed by its developers. Report AnyPS5 bugs to AnyPS5 and launcher bugs here.
