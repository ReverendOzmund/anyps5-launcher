#!/usr/bin/env python3
"""AnyPS5 Launcher - game library, conversion and launch front end for AnyPS5 on Linux and Windows."""
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QStyle, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

VERSION = "0.2.0"
IS_WIN = sys.platform == "win32"
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HOME = Path.home()
GAMES = HOME / "PS5" / "anyps5"
LOGS = HOME / "anyps5-logs"
if IS_WIN:
    CONFIG = Path(os.environ.get("APPDATA", HOME)) / "anyps5-launcher"
    DATA = Path(os.environ.get("LOCALAPPDATA", HOME)) / "anyps5-launcher"
else:
    CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "anyps5-launcher"
    DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "anyps5-launcher"
PREFS = CONFIG / "prefs.json"
RELEASES = DATA / "anyps5"
META = "launcher.json"
UPSTREAM = "boykopovar/AnyPS5"
GAME_EXE = "app.exe" if IS_WIN else "app.elf"
NO_WINDOW = 0x08000000 if IS_WIN else 0

PRESENT_MODES = ["fifo", "fifo_latest_ready", "mailbox", "immediate"]
DEFAULTS = {
    "gpu": "",
    "present_mode": "fifo",
    "fullscreen": False,
    "fps_limit": "",
    "mimalloc": False,
    "game_mode": "auto",
    "language": "",
    "extra_env": "",
}


class Failed(Exception):
    pass


STYLE_ICONS = {
    "list-add": "SP_FileDialogNewFolder",
    "system-software-update": "SP_BrowserReload",
    "view-refresh": "SP_BrowserReload",
    "folder-open": "SP_DirOpenIcon",
    "folder": "SP_DirIcon",
    "configure": "SP_FileDialogDetailedView",
    "media-playback-start": "SP_MediaPlay",
    "media-playback-stop": "SP_MediaStop",
    "edit-delete": "SP_TrashIcon",
    "applications-games": "SP_ComputerIcon",
}


def icon(name):
    themed = QIcon.fromTheme(name)
    if not themed.isNull():
        return themed
    std = STYLE_ICONS.get(name)
    app = QApplication.instance()
    if std and app:
        return app.style().standardIcon(getattr(QStyle.StandardPixmap, std))
    return QIcon()


# ---------------------------------------------------------------- helpers

def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_link(path):
    path = Path(path)
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction and is_junction(path):
        return True
    if IS_WIN:
        try:
            return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except OSError:
            return False
    return False


def remove_link(path):
    path = Path(path)
    try:
        path.unlink()
    except (IsADirectoryError, PermissionError):
        os.rmdir(path)


def safe_rmtree(path):
    """Delete a converted game folder without ever following links into the dump."""
    path = Path(path)
    if not path.exists() and not is_link(path):
        return
    if is_link(path):
        remove_link(path)
        return
    for entry in os.scandir(path):
        p = Path(entry.path)
        if is_link(p):
            remove_link(p)
        elif entry.is_dir(follow_symlinks=False):
            safe_rmtree(p)
        else:
            try:
                p.unlink()
            except PermissionError:
                os.chmod(p, stat.S_IWRITE)
                p.unlink()
    os.rmdir(path)


def link_entry(source, target):
    source, target = Path(source), Path(target)
    if not IS_WIN:
        os.symlink(source, target)
        return "link"
    if source.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(source), str(target))
        except Exception:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)], check=True,
                           capture_output=True, creationflags=NO_WINDOW)
        return "junction"
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def link_libs(game_dir, libs):
    target = Path(game_dir) / "libs"
    if is_link(target):
        remove_link(target)
    elif target.exists():
        safe_rmtree(target)
    link_entry(libs, target)


def is_elf(path):
    with open(path, "rb") as f:
        return f.read(4) == b"\x7fELF"


def run_cmd(args, log, cwd=None):
    log("$ " + " ".join(str(a) for a in args) + "\n")
    proc = subprocess.Popen([str(a) for a in args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
    for line in proc.stdout:
        log(line)
    code = proc.wait()
    if code != 0:
        raise Failed(f"{Path(str(args[0])).name} exited with code {code}")


def load_unwrapper(script):
    spec = importlib.util.spec_from_file_location("anyps5_unwrap_self", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def title_info(game_dir):
    param = read_json(Path(game_dir) / "app0" / "sce_sys" / "param.json", {})
    title_id = param.get("titleId") or Path(game_dir).name
    name = None
    loc = param.get("localizedParameters") or {}
    default = loc.get("defaultLanguage") or param.get("defaultLanguage")
    for key in [default, "en-US"]:
        if key and isinstance(loc.get(key), dict) and loc[key].get("titleName"):
            name = loc[key]["titleName"]
            break
    if not name:
        for value in loc.values():
            if isinstance(value, dict) and value.get("titleName"):
                name = value["titleName"]
                break
    return name or Path(game_dir).name, title_id


def game_icon(game_dir):
    for candidate in ["icon0.png", "pic0.png"]:
        p = Path(game_dir) / "app0" / "sce_sys" / candidate
        if p.exists():
            pix = QPixmap(str(p))
            if not pix.isNull():
                return QIcon(pix)
    return icon("applications-games")


def vulkan_gpus():
    try:
        out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        m = re.match(r"\s*deviceName\s*=\s*(.+)", line)
        if m and "llvmpipe" not in m.group(1):
            names.append(m.group(1).strip())
    return names


def load_prefs():
    prefs = {"backend": "", "source_dir": str(HOME / "AnyPS5")}
    prefs.update(read_json(PREFS, {}))
    if IS_WIN:
        prefs["backend"] = "release"
    elif prefs["backend"] not in ("source", "release"):
        prefs["backend"] = "source" if (Path(prefs["source_dir"]) / "build").exists() else "release"
    return prefs


# ---------------------------------------------------------------- backends

class SourceBackend:
    """AnyPS5 built from source (Linux, ~/AnyPS5/build)."""
    name = "source build"

    def __init__(self, src):
        self.src = Path(src)

    def relinker(self):
        for p in [self.src / "build" / "core" / "relinker" / "relinker"]:
            if p.exists():
                return p
        found = [p for p in (self.src / "build").rglob("relinker") if p.is_file() and os.access(p, os.X_OK)]
        return found[0] if found else None

    def libs(self):
        return self.src / "build" / "core" / "libs" / "libs"

    def unwrap_script(self):
        return self.src / "tools" / "unwrap_self.py"

    def version(self):
        try:
            rev = subprocess.run(["git", "-C", str(self.src), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            return rev or "?"
        except Exception:
            return "?"

    def ready(self):
        return self.relinker() is not None and self.libs().is_dir()

    def update(self, log):
        src = str(self.src)
        subprocess.run(["git", "-C", src, "am", "--abort"], capture_output=True)
        run_cmd(["git", "-C", src, "checkout", "-q", "-f", "main"], log)
        run_cmd(["git", "-C", src, "pull", "--ff-only", "origin", "main"], log)
        run_cmd(["git", "-C", src, "submodule", "update", "--init", "--recursive"], log)
        self.build(log)

    def build(self, log):
        run_cmd(["cmake", "--build", str(self.src / "build"), "--target", "libs", "relinker"], log)


class ReleaseBackend:
    """Official AnyPS5 release downloads from GitHub (Windows, or Linux without a source build)."""
    name = "official release"

    def __init__(self, root=RELEASES):
        self.root = Path(root)
        self.platform = "windows" if IS_WIN else "linux"

    def tag(self):
        return read_json(self.root / "current.json", {}).get("tag")

    def dir(self):
        tag = self.tag()
        return self.root / tag if tag else None

    def relinker(self):
        d = self.dir()
        if not d:
            return None
        p = d / ("relinker.exe" if IS_WIN else "relinker")
        return p if p.exists() else None

    def libs(self):
        d = self.dir()
        return d / "libs" if d else self.root / "missing"

    def unwrap_script(self):
        d = self.dir()
        return d / "unwrap_self.py" if d else self.root / "missing"

    def version(self):
        return self.tag() or "not downloaded"

    def ready(self):
        return self.relinker() is not None and self.libs().is_dir()

    def update(self, log):
        log(f"Checking {UPSTREAM} for the latest release...\n")
        req = urllib.request.Request(f"https://api.github.com/repos/{UPSTREAM}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "anyps5-launcher"})
        with urllib.request.urlopen(req, timeout=30) as r:
            release = json.load(r)
        tag = release["tag_name"]
        log(f"Latest release: {tag} ({release.get('published_at', '')[:10]})\n")
        if tag == self.tag() and self.ready():
            log("Already up to date.\n")
            return
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        exe_name = f"relinker-{tag}.exe" if IS_WIN else f"relinker-{tag}"
        libs_name = f"prx-{self.platform}-{tag}.zip"
        for needed in (exe_name, libs_name):
            if needed not in assets:
                raise Failed(f"release {tag} has no {needed}")
        target = self.root / tag
        staging = self.root / (tag + ".download")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        exe = staging / ("relinker.exe" if IS_WIN else "relinker")
        self.download(assets[exe_name], exe, log)
        if not IS_WIN:
            exe.chmod(0o755)
        archive = staging / libs_name
        self.download(assets[libs_name], archive, log)
        log(f"Extracting {libs_name}...\n")
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
        archive.unlink()
        if not (staging / "libs").is_dir():
            raise Failed(f"{libs_name} did not contain a libs/ folder")
        self.download(f"https://raw.githubusercontent.com/{UPSTREAM}/{tag}/tools/unwrap_self.py",
                      staging / "unwrap_self.py", log)
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
        write_json(self.root / "current.json", {"tag": tag, "downloaded": time.strftime("%Y-%m-%d %H:%M")})
        for old in self.root.iterdir():
            if old.is_dir() and old.name != tag and not old.name.endswith(".download"):
                try:
                    shutil.rmtree(old)
                except OSError:
                    pass
        log(f"AnyPS5 {tag} is ready.\n")

    @staticmethod
    def download(url, dest, log):
        log(f"Downloading {url.rsplit('/', 1)[-1]}...\n")
        req = urllib.request.Request(url, headers={"User-Agent": "anyps5-launcher"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, -1
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct // 10 != last // 10:
                        log(f"  {pct}% of {total / 1048576:.0f} MiB\n")
                        last = pct


def make_backend(prefs):
    if prefs["backend"] == "source":
        return SourceBackend(prefs["source_dir"])
    return ReleaseBackend()


# ---------------------------------------------------------------- conversion

def convert_game(dump, out, backend, log):
    dump, out = Path(dump), Path(out)
    if not (dump / "eboot.bin").exists():
        raise Failed(f"no eboot.bin in {dump}")
    if not backend.ready():
        if isinstance(backend, SourceBackend):
            log("Relinker or libraries missing, building them...\n")
            backend.build(log)
        else:
            backend.update(log)
    relinker = backend.relinker()
    if not relinker:
        raise Failed("relinker not found")
    unwrapper = None

    def unwrap(source, dest):
        nonlocal unwrapper
        if unwrapper is None:
            script = backend.unwrap_script()
            if not script.exists():
                raise Failed(f"{source.name} is a SELF container and unwrap_self.py is missing ({script})")
            unwrapper = load_unwrapper(script)
        unwrapper.unwrap(source, dest)

    stage = out.with_name(out.name + ".stage")
    safe_rmtree(stage)
    safe_rmtree(out)
    (stage / "sce_module").mkdir(parents=True)
    out.mkdir(parents=True)

    log("== staging executable and modules\n")
    if is_elf(dump / "eboot.bin"):
        shutil.copy2(dump / "eboot.bin", stage / "eboot.elf")
    else:
        log("eboot.bin is a SELF container, unwrapping\n")
        unwrap(dump / "eboot.bin", stage / "eboot.elf")
    modules = dump / "sce_module"
    if modules.is_dir():
        for m in sorted(modules.iterdir()):
            if not m.is_file():
                continue
            if is_elf(m):
                shutil.copy2(m, stage / "sce_module" / m.name)
            else:
                try:
                    unwrap(m, stage / "sce_module" / m.name)
                except Exception as e:
                    log(f"skip {m.name}: {e}\n")

    log("== relinking\n")
    args = [relinker]
    if IS_WIN:
        args.append("--windows")
    args += [stage / "eboot.elf", out / GAME_EXE]
    run_cmd(args, log)

    log("== linking game files into app0\n")
    app0 = out / "app0"
    app0.mkdir(exist_ok=True)
    kinds = {}
    for entry in sorted(dump.iterdir()):
        if entry.name == "sce_module":
            continue
        target = app0 / entry.name
        if target.exists() or is_link(target):
            continue
        kind = link_entry(entry, target)
        kinds[kind] = kinds.get(kind, 0) + 1
    log("   " + ", ".join(f"{n} {k}" for k, n in kinds.items()) + "\n")
    if kinds.get("copy"):
        log("   note: some files were copied because the dump is on a different drive\n")
    link_libs(out, backend.libs())
    safe_rmtree(stage)
    log("== done\n")


# ---------------------------------------------------------------- UI

class Worker(QThread):
    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.fn(self.line.emit)
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e) or e.__class__.__name__)


class SettingsDialog(QDialog):
    def __init__(self, parent, title, settings, gpus):
        super().__init__(parent)
        self.setWindowTitle(f"Settings - {title}")
        form = QFormLayout()

        self.gpu = QComboBox()
        self.gpu.addItem("Auto (discrete first)", "")
        for name in gpus:
            self.gpu.addItem(name, name)
        idx = self.gpu.findData(settings["gpu"])
        if idx < 0 and settings["gpu"]:
            self.gpu.addItem(settings["gpu"], settings["gpu"])
            idx = self.gpu.count() - 1
        self.gpu.setCurrentIndex(max(idx, 0))
        form.addRow("GPU", self.gpu)

        self.present = QComboBox()
        self.present.addItems(PRESENT_MODES)
        self.present.setCurrentText(settings["present_mode"])
        form.addRow("Present mode", self.present)

        self.fullscreen = QCheckBox("Start fullscreen (F11 toggles)")
        self.fullscreen.setChecked(settings["fullscreen"])
        form.addRow("", self.fullscreen)

        self.fps = QLineEdit(settings["fps_limit"])
        self.fps.setPlaceholderText("none (e.g. 30, 60, 59.94)")
        form.addRow("FPS limit", self.fps)

        self.mimalloc = QCheckBox("Use mimalloc allocator")
        self.mimalloc.setChecked(settings["mimalloc"])
        form.addRow("", self.mimalloc)

        self.game_mode = QComboBox()
        self.game_mode.addItems(["off", "auto", "on"])
        self.game_mode.setCurrentText(settings["game_mode"])
        form.addRow("Game mode" if IS_WIN else "Feral GameMode", self.game_mode)

        self.language = QLineEdit(settings["language"])
        self.language.setPlaceholderText("default English US; 'auto' or 0-30")
        form.addRow("Console language", self.language)

        self.extra = QPlainTextEdit(settings["extra_env"])
        self.extra.setPlaceholderText("Extra environment, one per line:\nNAME=value")
        self.extra.setFixedHeight(110)
        form.addRow("Extra env", self.extra)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self):
        return {
            "gpu": self.gpu.currentData() or "",
            "present_mode": self.present.currentText(),
            "fullscreen": self.fullscreen.isChecked(),
            "fps_limit": self.fps.text().strip(),
            "mimalloc": self.mimalloc.isChecked(),
            "game_mode": self.game_mode.currentText(),
            "language": self.language.text().strip(),
            "extra_env": self.extra.toPlainText().strip(),
        }


class PrefsDialog(QDialog):
    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        form = QFormLayout()
        self.backend = QComboBox()
        self.backend.addItem("Source build (~/AnyPS5, latest main)", "source")
        self.backend.addItem("Official release (downloaded from GitHub)", "release")
        self.backend.setCurrentIndex(0 if prefs["backend"] == "source" else 1)
        form.addRow("AnyPS5 from", self.backend)
        row = QHBoxLayout()
        self.src = QLineEdit(prefs["source_dir"])
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse)
        row.addWidget(self.src)
        row.addWidget(browse)
        form.addRow("Source folder", row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Games converted with one backend should be reconverted after switching."))
        layout.addWidget(buttons)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "AnyPS5 source folder", self.src.text())
        if d:
            self.src.setText(d)

    def values(self):
        return {"backend": self.backend.currentData(), "source_dir": self.src.text().strip()}


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"AnyPS5 Launcher {VERSION}")
        self.setWindowIcon(QIcon(str(HERE / "anyps5-launcher.svg")))
        self.resize(1150, 720)
        self.prefs = load_prefs()
        self.backend = make_backend(self.prefs)
        self.worker = None
        self.worker_after = None
        self.task_log = None
        self.game = None
        self.game_dir = None
        self.game_log = None
        self.log_file = None
        self.gpus = None

        bar = QToolBar()
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        self.act_add = bar.addAction(icon("list-add"), "Add game", self.add_game)
        self.act_update = bar.addAction(icon("system-software-update"), "Update AnyPS5", self.update_anyps5)
        bar.addAction(icon("view-refresh"), "Refresh", self.refresh)
        bar.addAction(icon("folder-open"), "Logs folder", lambda: self.open_dir(LOGS))
        self.act_prefs = bar.addAction(icon("configure"), "Preferences", self.edit_prefs)
        if IS_WIN:
            self.act_prefs.setVisible(False)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(150, 150))
        self.list.setGridSize(QSize(180, 205))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self.show_details)
        self.list.itemDoubleClicked.connect(lambda _: self.play())

        self.title = QLabel("No game selected")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.subtitle = QLabel("")
        self.subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle.setWordWrap(True)

        self.btn_play = QPushButton(icon("media-playback-start"), "Play")
        self.btn_stop = QPushButton(icon("media-playback-stop"), "Stop")
        self.btn_settings = QPushButton(icon("configure"), "Settings")
        self.btn_reconvert = QPushButton(icon("view-refresh"), "Reconvert")
        self.btn_folder = QPushButton(icon("folder"), "Folder")
        self.btn_remove = QPushButton(icon("edit-delete"), "Remove")
        self.btn_play.clicked.connect(self.play)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_settings.clicked.connect(self.edit_settings)
        self.btn_reconvert.clicked.connect(self.reconvert)
        self.btn_folder.clicked.connect(lambda: self.selected() and self.open_dir(self.selected()))
        self.btn_remove.clicked.connect(self.remove)
        buttons = QHBoxLayout()
        for b in [self.btn_play, self.btn_stop, self.btn_settings, self.btn_reconvert, self.btn_folder, self.btn_remove]:
            buttons.addWidget(b)
        buttons.addStretch()

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        mono = QFont("Consolas" if IS_WIN else "monospace")
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)
        self.status = QLabel("")

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.title)
        rl.addWidget(self.subtitle)
        rl.addLayout(buttons)
        rl.addWidget(self.log, 1)
        rl.addWidget(self.status)

        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setSizes([520, 630])
        self.setCentralWidget(split)

        self.refresh()
        self.update_buttons()
        QTimer.singleShot(300, self.first_run)

    def first_run(self):
        if self.backend.ready():
            return
        if isinstance(self.backend, ReleaseBackend):
            if QMessageBox.question(self, "Download AnyPS5",
                                    "AnyPS5 hasn't been downloaded yet.\n\nDownload the latest official release now?") == QMessageBox.Yes:
                self.update_anyps5()
        else:
            self.append(f"AnyPS5 build not found in {self.backend.src}/build.\n"
                        "Build it first, or switch to the official release in Preferences.\n")

    # ---------- library ----------
    def refresh(self):
        current = self.selected()
        self.list.clear()
        GAMES.mkdir(parents=True, exist_ok=True)
        for d in sorted(GAMES.iterdir()):
            if not d.is_dir() or d.name.endswith(".stage") or not (d / GAME_EXE).exists():
                continue
            name, tid = title_info(d)
            item = QListWidgetItem(game_icon(d), f"{name}\n{tid}")
            item.setData(Qt.UserRole, str(d))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(item)
            if current and d == current:
                self.list.setCurrentItem(item)
        if self.list.count() and not self.list.currentItem():
            self.list.setCurrentRow(0)
        if not self.list.count():
            self.title.setText("No games yet - click Add game")
            self.subtitle.setText("")
        self.update_buttons()

    def selected(self):
        item = self.list.currentItem()
        return Path(item.data(Qt.UserRole)) if item else None

    def show_details(self, *_):
        d = self.selected()
        if not d:
            return
        name, tid = title_info(d)
        meta = read_json(d / META, {})
        self.title.setText(name)
        self.subtitle.setText(f"{tid}\nDump: {meta.get('dump', '?')}\n"
                              f"Converted: {meta.get('converted', '?')} with AnyPS5 {meta.get('anyps5', '?')} ({meta.get('backend', '?')})")
        self.update_buttons()

    def settings_for(self, d):
        s = dict(DEFAULTS)
        s.update(read_json(d / META, {}).get("settings", {}))
        return s

    def update_buttons(self):
        has = self.selected() is not None
        busy = self.worker is not None
        running = self.game is not None
        self.btn_play.setEnabled(has and not busy and not running)
        self.btn_stop.setEnabled(running)
        self.btn_settings.setEnabled(has)
        self.btn_reconvert.setEnabled(has and not busy and not running)
        self.btn_folder.setEnabled(has)
        self.btn_remove.setEnabled(has and not busy and not (running and self.game_dir == self.selected()))
        self.act_add.setEnabled(not busy)
        self.act_update.setEnabled(not busy and not running)
        self.act_prefs.setEnabled(not busy and not running)
        if running:
            self.status.setText(f"Running: {self.game_dir.name}   log: {self.log_file}")
        elif busy:
            self.status.setText("Working...")
        else:
            self.status.setText(f"AnyPS5 {self.backend.version()} ({self.backend.name})")

    # ---------- log ----------
    def append(self, text):
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def open_dir(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def new_log(prefix):
        LOGS.mkdir(parents=True, exist_ok=True)
        return LOGS / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.log"

    # ---------- background work ----------
    def start_work(self, fn, log_prefix, after=None):
        self.log.clear()
        self.task_log = open(self.new_log(log_prefix), "w", encoding="utf-8")
        self.worker = Worker(fn, self)
        self.worker.line.connect(self.work_line)
        self.worker.done.connect(self.work_done)
        self.worker_after = after
        self.worker.start()
        self.update_buttons()

    def work_line(self, text):
        self.task_log.write(text)
        self.task_log.flush()
        self.append(text)

    def work_done(self, ok, error):
        msg = "\n== finished OK\n" if ok else f"\n== FAILED: {error}\n"
        self.task_log.write(msg)
        self.task_log.close()
        self.append(msg)
        self.worker.wait()
        self.worker = None
        after = self.worker_after
        self.worker_after = None
        if after:
            after(ok, error)
        self.update_buttons()

    def add_game(self):
        dump = QFileDialog.getExistingDirectory(self, "Select the game dump folder (contains eboot.bin)", str(HOME))
        if not dump:
            return
        dump = Path(dump)
        if not (dump / "eboot.bin").exists():
            QMessageBox.warning(self, "Not a dump", f"No eboot.bin in:\n{dump}\n\nPick the folder that has eboot.bin directly inside it.")
            return
        param = read_json(dump / "sce_sys" / "param.json", {})
        folder = re.sub(r"[^A-Za-z0-9._-]", "_", param.get("titleId") or dump.name)
        out = GAMES / folder
        if (out / GAME_EXE).exists():
            if QMessageBox.question(self, "Replace?", f"{folder} is already in the library. Convert it again?") != QMessageBox.Yes:
                return
        self.convert(dump, out, read_json(out / META, {}).get("settings", {}))

    def reconvert(self):
        d = self.selected()
        if not d:
            return
        meta = read_json(d / META, {})
        dump = Path(meta.get("dump", ""))
        if not (dump / "eboot.bin").exists():
            dump = QFileDialog.getExistingDirectory(self, "Dump folder not found - select it", str(HOME))
            if not dump:
                return
            dump = Path(dump)
        self.convert(dump, d, meta.get("settings", {}))

    def convert(self, dump, out, keep_settings):
        backend = self.backend

        def after(ok, error):
            if ok:
                write_json(out / META, {
                    "dump": str(dump),
                    "converted": time.strftime("%Y-%m-%d %H:%M"),
                    "anyps5": backend.version(),
                    "backend": backend.name,
                    "settings": keep_settings,
                })
            self.refresh()
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == str(out):
                    self.list.setCurrentRow(i)
            if not ok:
                QMessageBox.warning(self, "Conversion failed", f"{error}\n\nSee the log for details.")

        self.start_work(lambda log: convert_game(dump, out, backend, log), "convert-" + out.name, after)

    def update_anyps5(self):
        def after(ok, error):
            if not ok:
                QMessageBox.warning(self, "Update failed", f"{error}\n\nSee the log for details.")
        self.start_work(self.backend.update, "update", after)

    def edit_prefs(self):
        dlg = PrefsDialog(self, self.prefs)
        if dlg.exec() == QDialog.Accepted:
            self.prefs.update(dlg.values())
            write_json(PREFS, self.prefs)
            self.backend = make_backend(self.prefs)
            self.update_buttons()
            self.first_run()

    def remove(self):
        d = self.selected()
        if not d:
            return
        name, _ = title_info(d)
        if QMessageBox.question(self, "Remove", f"Remove the converted copy of {name}?\n\n{d}\n\nYour original dump is not touched.") != QMessageBox.Yes:
            return
        try:
            safe_rmtree(d)
        except OSError as e:
            QMessageBox.warning(self, "Remove failed", str(e))
        self.refresh()

    def edit_settings(self):
        d = self.selected()
        if not d:
            return
        if self.gpus is None:
            self.gpus = vulkan_gpus()
        name, _ = title_info(d)
        dlg = SettingsDialog(self, name, self.settings_for(d), self.gpus)
        if dlg.exec() == QDialog.Accepted:
            meta = read_json(d / META, {})
            meta["settings"] = dlg.values()
            write_json(d / META, meta)

    # ---------- running ----------
    @staticmethod
    def game_env(s):
        env = QProcessEnvironment.systemEnvironment()
        for key in ["ANYPS5_GPU", "ANYPS5_PRESENT_MODE", "ANYPS5_FPS_LIMIT", "ANYPS5_FULLSCREEN",
                    "APS5_MIMALLOC", "APS5_GAME_MODE", "ANYPS5_LANGUAGE"]:
            env.remove(key)
        if s["gpu"]:
            env.insert("ANYPS5_GPU", s["gpu"])
        if s["present_mode"] != "fifo":
            env.insert("ANYPS5_PRESENT_MODE", s["present_mode"])
        if s["fps_limit"]:
            env.insert("ANYPS5_FPS_LIMIT", s["fps_limit"])
        if s["fullscreen"]:
            env.insert("ANYPS5_FULLSCREEN", "borderless")
        if s["mimalloc"]:
            env.insert("APS5_MIMALLOC", "1")
        env.insert("APS5_GAME_MODE", s["game_mode"])
        if s["language"]:
            env.insert("ANYPS5_LANGUAGE", s["language"])
        for line in s["extra_env"].splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.insert(k.strip(), v.strip())
        return env

    def play(self):
        d = self.selected()
        if not d or self.game or self.worker:
            return
        libs = d / "libs"
        if not libs.exists():
            try:
                link_libs(d, self.backend.libs())
            except OSError as e:
                QMessageBox.warning(self, "Missing libraries", f"Could not link AnyPS5 libraries:\n{e}")
                return
        s = self.settings_for(d)
        self.log.clear()
        self.log_file = self.new_log("run-" + d.name)
        self.game_log = open(self.log_file, "w", encoding="utf-8")
        self.game_dir = d
        self.game = QProcess(self)
        self.game.setProcessChannelMode(QProcess.MergedChannels)
        self.game.setWorkingDirectory(str(d))
        self.game.setProcessEnvironment(self.game_env(s))
        self.game.readyReadStandardOutput.connect(self.game_output)
        self.game.finished.connect(self.game_done)
        self.game.errorOccurred.connect(self.game_error)
        name, tid = title_info(d)
        meta = read_json(d / META, {})
        header = (f"[launcher] {name} ({tid}) converted with AnyPS5 {meta.get('anyps5', '?')}, "
                  f"current {self.backend.version()}\n[launcher] settings: {json.dumps(s)}\n")
        self.game_log.write(header)
        self.append(header)
        self.game.start(str(d / GAME_EXE), [])
        self.update_buttons()

    def game_output(self):
        text = bytes(self.game.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.game_log.write(text)
        self.game_log.flush()
        self.append(text)

    def game_error(self, error):
        if error == QProcess.FailedToStart and self.game:
            msg = f"\n[launcher] could not start {GAME_EXE}: {self.game.errorString()}\n"
            self.append(msg)
            self.game_log.write(msg)
            self.game_log.close()
            self.game = None
            self.update_buttons()

    def game_done(self, code, status):
        how = "crashed" if status == QProcess.CrashExit else f"exited with code {code}"
        msg = f"\n[launcher] game {how}\n"
        self.game_log.write(msg)
        self.game_log.close()
        self.append(msg)
        self.game = None
        self.update_buttons()

    def stop(self):
        if not self.game:
            return
        game = self.game
        if IS_WIN:
            game.kill()
            return
        game.terminate()

        def hard_kill():
            if self.game is game and game.state() != QProcess.NotRunning:
                game.kill()

        QTimer.singleShot(4000, hard_kill)

    def closeEvent(self, event):
        if self.game:
            if QMessageBox.question(self, "Quit", "A game is running. Stop it and quit?") != QMessageBox.Yes:
                event.ignore()
                return
            self.game.kill()
            self.game.waitForFinished(3000)
        if self.worker:
            if QMessageBox.question(self, "Quit", "A conversion or update is still running. Quit anyway?") != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.terminate()
            self.worker.wait(3000)
        event.accept()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("AnyPS5 Launcher")
    app.setDesktopFileName("anyps5-launcher")
    if IS_WIN:
        app.setStyle("Fusion")
    w = Launcher()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
