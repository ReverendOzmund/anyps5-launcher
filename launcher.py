#!/usr/bin/env python3
"""AnyPS5 Launcher - game library, conversion and launch front end for AnyPS5 on Linux."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

HERE = Path(__file__).resolve().parent
HOME = Path.home()
SRC = HOME / "AnyPS5"
GAMES = HOME / "PS5" / "anyps5"
LOGS = HOME / "anyps5-logs"
CONFIG = HOME / ".config" / "anyps5-launcher"
META = "launcher.json"

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


def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return fallback


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def title_info(game_dir):
    param = read_json(game_dir / "app0" / "sce_sys" / "param.json", {})
    title_id = param.get("titleId") or game_dir.name
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
    return name or game_dir.name, title_id


def game_icon(game_dir):
    for candidate in ["icon0.png", "pic0.png"]:
        p = game_dir / "app0" / "sce_sys" / candidate
        if p.exists():
            pix = QPixmap(str(p))
            if not pix.isNull():
                return QIcon(pix)
    return QIcon.fromTheme("applications-games")


def vulkan_gpus():
    try:
        out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        m = re.match(r"\s*deviceName\s*=\s*(.+)", line)
        if m and "llvmpipe" not in m.group(1):
            names.append(m.group(1).strip())
    return names


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
        form.addRow("Feral GameMode", self.game_mode)

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


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AnyPS5 Launcher")
        self.setWindowIcon(QIcon(str(HERE / "anyps5-launcher.svg")))
        self.resize(1150, 720)
        self.task = None
        self.task_after = None
        self.game = None
        self.game_dir = None
        self.log_file = None
        self.gpus = None

        bar = QToolBar()
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        self.addToolBar(bar)
        self.act_add = bar.addAction(QIcon.fromTheme("list-add"), "Add game", self.add_game)
        self.act_update = bar.addAction(QIcon.fromTheme("system-software-update"), "Update AnyPS5", self.update_anyps5)
        bar.addAction(QIcon.fromTheme("view-refresh"), "Refresh", self.refresh)
        bar.addAction(QIcon.fromTheme("folder-open"), "Logs folder", lambda: self.open_dir(LOGS))

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

        self.btn_play = QPushButton(QIcon.fromTheme("media-playback-start"), "Play")
        self.btn_stop = QPushButton(QIcon.fromTheme("media-playback-stop"), "Stop")
        self.btn_settings = QPushButton(QIcon.fromTheme("configure"), "Settings")
        self.btn_reconvert = QPushButton(QIcon.fromTheme("view-refresh"), "Reconvert")
        self.btn_folder = QPushButton(QIcon.fromTheme("folder"), "Folder")
        self.btn_remove = QPushButton(QIcon.fromTheme("edit-delete"), "Remove")
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
        mono = QFont("monospace")
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
        if not (SRC / "build").exists():
            self.append(f"AnyPS5 build not found at {SRC}/build - run the setup script first.\n")

    # ---------- library ----------
    def refresh(self):
        current = self.selected()
        self.list.clear()
        GAMES.mkdir(parents=True, exist_ok=True)
        for d in sorted(GAMES.iterdir()):
            if not d.is_dir() or d.name.endswith(".stage") or not (d / "app.elf").exists():
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
        self.subtitle.setText(f"{tid}\nDump: {meta.get('dump', '?')}\nConverted: {meta.get('converted', '?')} at AnyPS5 {meta.get('anyps5', '?')}")
        self.update_buttons()

    def settings_for(self, d):
        s = dict(DEFAULTS)
        s.update(read_json(d / META, {}).get("settings", {}))
        return s

    def update_buttons(self):
        has = self.selected() is not None
        busy = self.task is not None
        running = self.game is not None
        self.btn_play.setEnabled(has and not busy and not running)
        self.btn_stop.setEnabled(running)
        self.btn_settings.setEnabled(has)
        self.btn_reconvert.setEnabled(has and not busy and not running)
        self.btn_folder.setEnabled(has)
        self.btn_remove.setEnabled(has and not busy and not (running and self.game_dir == self.selected()))
        self.act_add.setEnabled(not busy)
        self.act_update.setEnabled(not busy and not running)
        if running:
            self.status.setText(f"Running: {self.game_dir.name}   log: {self.log_file}")
        elif busy:
            self.status.setText("Working...")
        else:
            self.status.setText("")

    # ---------- log ----------
    def append(self, text):
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def open_dir(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def new_log(self, prefix):
        LOGS.mkdir(parents=True, exist_ok=True)
        path = LOGS / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        return path

    # ---------- background tasks ----------
    def run_task(self, args, log_prefix, after=None):
        self.log.clear()
        self.task_log = open(self.new_log(log_prefix), "w")
        self.task = QProcess(self)
        self.task.setProcessChannelMode(QProcess.MergedChannels)
        self.task.readyReadStandardOutput.connect(self.task_output)
        self.task.finished.connect(self.task_done)
        self.task_after = after
        self.append("$ " + " ".join(args) + "\n")
        self.task.start(args[0], args[1:])
        self.update_buttons()

    def task_output(self):
        text = bytes(self.task.readAllStandardOutput()).decode(errors="replace")
        self.task_log.write(text)
        self.task_log.flush()
        self.append(text)

    def task_done(self, code, _status):
        self.task_log.close()
        ok = code == 0
        self.append(f"\n== finished ({'OK' if ok else f'exit {code}'})\n")
        after = self.task_after
        self.task = None
        self.task_after = None
        if after:
            after(ok)
        self.update_buttons()

    def anyps5_rev(self):
        try:
            return subprocess.run(["git", "-C", str(SRC), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        except Exception:
            return "?"

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
        self.convert(dump, GAMES / folder, keep_settings={})

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
        self.convert(dump, d, keep_settings=meta.get("settings", {}))

    def convert(self, dump, out, keep_settings):
        if out.exists() and (out / "app.elf").exists() and out != self.selected():
            if QMessageBox.question(self, "Replace?", f"{out.name} is already converted. Convert again?") != QMessageBox.Yes:
                return

        def after(ok):
            if ok:
                write_json(out / META, {
                    "dump": str(dump),
                    "converted": time.strftime("%Y-%m-%d %H:%M"),
                    "anyps5": self.anyps5_rev(),
                    "settings": keep_settings,
                })
            self.refresh()
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == str(out):
                    self.list.setCurrentRow(i)
            if not ok:
                QMessageBox.warning(self, "Conversion failed", "Conversion failed - see the log.")

        self.run_task(["bash", str(HERE / "convert.sh"), str(dump), str(out)], "convert-" + out.name, after)

    def update_anyps5(self):
        self.run_task(["bash", str(HERE / "update.sh")], "update", lambda ok: ok or QMessageBox.warning(self, "Update failed", "Update/build failed - see the log."))

    def remove(self):
        d = self.selected()
        if not d:
            return
        name, _ = title_info(d)
        if QMessageBox.question(self, "Remove", f"Remove the converted copy of {name}?\n\n{d}\n\nYour original dump is not touched.") != QMessageBox.Yes:
            return
        shutil.rmtree(d)
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
    def game_env(self, s):
        env = QProcessEnvironment.systemEnvironment()
        for key in ["ANYPS5_GPU", "ANYPS5_PRESENT_MODE", "ANYPS5_FPS_LIMIT", "ANYPS5_FULLSCREEN", "APS5_MIMALLOC", "APS5_GAME_MODE", "ANYPS5_LANGUAGE"]:
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
        if not d or self.game or self.task:
            return
        if not (d / "libs").exists():
            (d / "libs").unlink(missing_ok=True)
            (d / "libs").symlink_to(SRC / "build" / "core" / "libs" / "libs")
        s = self.settings_for(d)
        self.log.clear()
        self.log_file = self.new_log("run-" + d.name)
        self.game_log = open(self.log_file, "w")
        self.game_dir = d
        self.game = QProcess(self)
        self.game.setProcessChannelMode(QProcess.MergedChannels)
        self.game.setWorkingDirectory(str(d))
        self.game.setProcessEnvironment(self.game_env(s))
        self.game.readyReadStandardOutput.connect(self.game_output)
        self.game.finished.connect(self.game_done)
        self.game.errorOccurred.connect(lambda e: self.append(f"\n[launcher] process error: {e}\n"))
        name, tid = title_info(d)
        header = f"[launcher] {name} ({tid}) at AnyPS5 {self.anyps5_rev()}\n[launcher] settings: {json.dumps(s)}\n"
        self.game_log.write(header)
        self.append(header)
        self.game.start("./app.elf", [])
        self.update_buttons()

    def game_output(self):
        text = bytes(self.game.readAllStandardOutput()).decode(errors="replace")
        self.game_log.write(text)
        self.game_log.flush()
        self.append(text)

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
        self.game.terminate()
        game = self.game

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
        if self.task:
            self.task.kill()
            self.task.waitForFinished(3000)
        event.accept()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("AnyPS5 Launcher")
    app.setDesktopFileName("anyps5-launcher")
    w = Launcher()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
