# AnyPS5 Launcher

A desktop launcher for [AnyPS5](https://github.com/boykopovar/AnyPS5) on **Linux and Windows**. It keeps a library of your converted PS5 games, converts new dumps with the AnyPS5 relinker, and launches them with per-game settings and a live log.

## Features

- **Library** of converted games with their title name, title ID and icon from `sce_sys/`
- **Add game:** pick a dump folder (the one with `eboot.bin` in it). The launcher unwraps SELF files if needed, relinks the executable and modules, and links the game files into `app0/` instead of copying them.
- **Play / Stop** with the game's output shown live and saved to `~/anyps5-logs/`
- **Per-game settings:** GPU, present mode, fullscreen, FPS limit, mimalloc, game mode, console language and extra environment variables
- **Reconvert** after an AnyPS5 update (settings are kept)
- **Remove** deletes only the converted copy, never your dump
- **Update AnyPS5:** downloads the latest official release, or on Linux pulls and rebuilds a source checkout

## Where AnyPS5 comes from

| Mode | Platforms | What it uses |
|---|---|---|
| Official release (default on Windows) | Windows, Linux | `relinker-<tag>` and `prx-<platform>-<tag>.zip` from the latest [AnyPS5 release](https://github.com/boykopovar/AnyPS5/releases), downloaded on first start |
| Source build (default on Linux when `~/AnyPS5/build` exists) | Linux | Your own `~/AnyPS5` checkout and build, updated to the latest `main` |

Switch between them in **Preferences** (Linux). Reconvert your games after switching.

## Install

### Windows

1. Download `AnyPS5Launcher.exe` from [Releases](https://github.com/ReverendOzmund/anyps5-launcher/releases). Builds of the latest commit are under [Actions](https://github.com/ReverendOzmund/anyps5-launcher/actions).
2. Run it. On first start it offers to download AnyPS5.

Or run it from source: install Python 3.10 or newer, then `pip install PySide6` and `pythonw launcher.py`.

Windows notes:
- Game files are linked with directory junctions and hard links, so no administrator rights or Developer Mode are needed. If the dump is on a different drive from your user folder, its top-level files are copied instead (folders are still linked).
- If a game crashes while Vulkan devices are listed and you have the "OpenCL, OpenGL, and Vulkan Compatibility Pack" installed, add `VK_LOADER_DRIVERS_DISABLE=*dzn*` to the game's **Extra env** setting.

### Linux

```bash
git clone https://github.com/ReverendOzmund/anyps5-launcher.git
bash anyps5-launcher/install.sh
```

If a distrobox named `anyps5-build` exists (the AnyPS5 build box), the launcher is installed and run inside it. Otherwise it uses the host Python and installs PySide6 with pip. In both cases it adds **AnyPS5 Launcher** to the app menu and the desktop.

## Local AnyPS5 patches (source build)

**Update AnyPS5** re-applies every `*.patch` in the launcher's `patches/` folder and in `~/.local/share/anyps5-launcher/patches/` after pulling `main`, then rebuilds. A patch that no longer applies is skipped with a warning.

Bundled patches:

| Patch | What it does |
|---|---|
| `0001-present-flip-y.patch` | Adds `ANYPS5_PRESENT_FLIP_Y=1`, which flips the presented picture vertically. Use it with the **Flip picture vertically** game setting for titles that show upside down (GTA V PPSA04263 on an RTX 3080 Ti). It is a workaround, not a fix: screenshots and frame dumps stay unflipped. |
| `0002-diag-viewport.patch` | Adds `APS5_DIAG_VIEWPORT=1`, which logs each distinct viewport and render-target setup once (`[diag-viewport]` lines). Diagnostic only, for tracking down rendering bugs. |

## Paths

| What | Where |
|---|---|
| Converted games | `~/PS5/anyps5/<title id>/` |
| Logs | `~/anyps5-logs/` |
| Downloaded AnyPS5 release | Linux `~/.local/share/anyps5-launcher/anyps5/`, Windows `%LOCALAPPDATA%\anyps5-launcher\anyps5\` |
| Source build | `~/AnyPS5`, `~/AnyPS5/build` |

You need your own legally obtained, decrypted PS5 game dumps.

## Notes

This is an unofficial front end. It is not part of AnyPS5 and is not endorsed by its developers. Report AnyPS5 bugs to AnyPS5 and launcher bugs here.
