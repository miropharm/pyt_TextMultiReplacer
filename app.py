from __future__ import annotations

import json
import os
import re
import shutil
import sys
import html
import difflib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "TextMultiReplacer Pro"
APP_VERSION = "1.0.0"
SESSION_FILE = "session.json"
RULESET_VERSION = 1


def app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".textmultireplacer"
    path = root / "TextMultiReplacerPro"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class ReplacementRule:
    find: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    enabled: bool = True


class SessionStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / SESSION_FILE

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class TextIO:
    ENCODINGS = ["utf-8-sig", "utf-8", "cp1254", "cp1252", "latin-1"]

    @classmethod
    def read_text(cls, file_path: Path) -> tuple[str, str]:
        raw = file_path.read_bytes()
        for enc in cls.ENCODINGS:
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
        # latin-1 fallback decode guarantees success
        return raw.decode("latin-1"), "latin-1"

    @staticmethod
    def write_text(file_path: Path, content: str, encoding: str) -> None:
        file_path.write_text(content, encoding=encoding)


class ProcessorWorker(QObject):
    progress = Signal(int, int, str)
    file_done = Signal(str, int, bool, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        files: List[str],
        rules: List[ReplacementRule],
        create_backup: bool,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self.files = files
        self.rules = rules
        self.create_backup = create_backup
        self.dry_run = dry_run
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    @staticmethod
    def apply_rules(content: str, rules: List[ReplacementRule]) -> tuple[str, int]:
        total_replacements = 0
        current = content

        for rule in rules:
            if not rule.enabled or not rule.find:
                continue

            flags = 0 if rule.case_sensitive else re.IGNORECASE
            if rule.use_regex:
                pattern_text = rule.find
                if rule.whole_word:
                    pattern_text = rf"\b(?:{pattern_text})\b"
                try:
                    pattern = re.compile(pattern_text, flags)
                    updated, count = pattern.subn(rule.replace, current)
                except re.error as exc:
                    raise ValueError(f"Regex hatası ({rule.find}): {exc}") from exc
            else:
                pattern_text = re.escape(rule.find)
                if rule.whole_word:
                    pattern_text = rf"\b{pattern_text}\b"
                pattern = re.compile(pattern_text, flags)
                updated, count = pattern.subn(rule.replace, current)

            current = updated
            total_replacements += count

        return current, total_replacements

    def run(self) -> None:
        summary = {
            "processed": 0,
            "changed": 0,
            "replacements": 0,
            "errors": 0,
            "dry_run": self.dry_run,
        }

        total = len(self.files)
        for idx, path_str in enumerate(self.files, start=1):
            if self._stopped:
                break

            try:
                file_path = Path(path_str)
                original, encoding = TextIO.read_text(file_path)
                updated, replaced_count = self.apply_rules(original, self.rules)
                changed = updated != original

                if changed and not self.dry_run:
                    if self.create_backup:
                        backup_path = Path(str(file_path) + ".bak")
                        shutil.copy2(file_path, backup_path)
                    TextIO.write_text(file_path, updated, encoding)

                summary["processed"] += 1
                summary["replacements"] += replaced_count
                if changed:
                    summary["changed"] += 1

                note = "değişiklik yok"
                if changed:
                    note = "simülasyon" if self.dry_run else "güncellendi"

                self.file_done.emit(path_str, replaced_count, changed, note)
                self.progress.emit(idx, total, path_str)
            except Exception as exc:  # pragma: no cover - UI-facing error channel
                summary["processed"] += 1
                summary["errors"] += 1
                self.file_done.emit(path_str, 0, False, f"hata: {exc}")
                self.progress.emit(idx, total, path_str)

        self.finished.emit(summary)


class RuleRow(QWidget):
    remove_requested = Signal(QWidget)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RuleRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.enabled_cb = QCheckBox()
        self.enabled_cb.setChecked(True)
        self.enabled_cb.setToolTip("Kuralı aktif/pasif yap")
        layout.addWidget(self.enabled_cb)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Bul (örn: aa)")
        self.find_edit.setClearButtonEnabled(True)
        layout.addWidget(self.find_edit, 2)

        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        layout.addWidget(arrow)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Değiştir (boş bırak = sil)")
        self.replace_edit.setClearButtonEnabled(True)
        layout.addWidget(self.replace_edit, 2)

        self.regex_cb = QCheckBox("Regex")
        self.case_cb = QCheckBox("Aa")
        self.case_cb.setToolTip("Büyük/küçük harfe duyarlı")
        self.word_cb = QCheckBox("W")
        self.word_cb.setToolTip("Sadece tam kelime eşleşmesi")
        layout.addWidget(self.regex_cb)
        layout.addWidget(self.case_cb)
        layout.addWidget(self.word_cb)

        self.remove_btn = QPushButton("x")
        self.remove_btn.setToolTip("Kuralı sil")
        self.remove_btn.setProperty("danger", True)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        self.remove_btn.setFixedSize(28, 24)
        layout.addWidget(self.remove_btn)

    def to_rule(self) -> ReplacementRule:
        return ReplacementRule(
            find=self.find_edit.text(),
            replace=self.replace_edit.text(),
            use_regex=self.regex_cb.isChecked(),
            case_sensitive=self.case_cb.isChecked(),
            whole_word=self.word_cb.isChecked(),
            enabled=self.enabled_cb.isChecked(),
        )

    def from_rule(self, rule: ReplacementRule) -> None:
        self.find_edit.setText(rule.find)
        self.replace_edit.setText(rule.replace)
        self.regex_cb.setChecked(rule.use_regex)
        self.case_cb.setChecked(rule.case_sensitive)
        self.word_cb.setChecked(getattr(rule, "whole_word", False))
        self.enabled_cb.setChecked(rule.enabled)


class FileListWidget(QListWidget):
    files_dropped = Signal(list)
    preview_requested = Signal(str)
    diff_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                files.append(local)
        if files:
            self.files_dropped.emit(files)
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        point = event.position().toPoint()
        item = self.itemAt(point)
        if event.button() == Qt.RightButton and item:
            self.setCurrentItem(item)
            self.preview_requested.emit(item.text())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        point = event.position().toPoint()
        item = self.itemAt(point)
        if event.button() == Qt.LeftButton and item:
            self.setCurrentItem(item)
            self.diff_requested.emit(item.text())
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = SessionStore()
        self.known_files: set[str] = set()
        self.worker_thread: QThread | None = None
        self.worker: ProcessorWorker | None = None
        self.dialog_dirs: dict[str, str] = {
            "add_files": "",
            "add_folder": "",
            "import_rules": "",
            "export_rules": "",
        }

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1200, 760)

        self._create_actions()
        self._build_ui()
        self._connect_events()
        self._load_session()

    def _create_actions(self) -> None:
        self.act_import_rules = QAction("Kural Seti İçe Aktar", self)
        self.act_export_rules = QAction("Kural Seti Dışa Aktar", self)
        self.act_save_session = QAction("Oturumu Kaydet", self)
        self.act_exit = QAction("Çıkış", self)
        self.act_exit.setShortcut(QKeySequence.Quit)

        self.act_start = QAction("Uygula", self)
        self.act_start.setShortcut(QKeySequence("Ctrl+R"))

        self.act_about = QAction("Hakkında", self)

    def _build_ui(self) -> None:
        self._build_menu()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(12, 12, 12, 12)
        central_layout.setSpacing(10)

        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(self._build_files_panel())
        top_split.addWidget(self._build_rules_panel())
        top_split.addWidget(self._build_controls_panel())
        top_split.setSizes([340, 580, 300])

        self.bottom_tabs = QTabWidget()
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.diff_edit = QTextEdit()
        self.diff_edit.setReadOnly(True)
        self.file_preview_edit = QTextEdit()
        self.file_preview_edit.setReadOnly(True)
        self.bottom_tabs.addTab(self.log_edit, "İşlem Günlüğü")
        self.bottom_tabs.addTab(self.diff_edit, "Diff Önizleme")
        self.bottom_tabs.addTab(self.file_preview_edit, "Dosya Önizleme")

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.addWidget(top_split)
        self.main_splitter.addWidget(self.bottom_tabs)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([520, 280])

        central_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Hazır")

        self._apply_style()

    def _build_menu(self) -> None:
        menu_file = self.menuBar().addMenu("Dosya")
        menu_file.addAction(self.act_import_rules)
        menu_file.addAction(self.act_export_rules)
        menu_file.addSeparator()
        menu_file.addAction(self.act_save_session)
        menu_file.addSeparator()
        menu_file.addAction(self.act_exit)

        menu_run = self.menuBar().addMenu("İşlem")
        menu_run.addAction(self.act_start)

        menu_help = self.menuBar().addMenu("Yardım")
        menu_help.addAction(self.act_about)

    def _build_files_panel(self) -> QWidget:
        panel = QGroupBox("Dosya Havuzu")
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        buttons = QHBoxLayout()
        self.btn_add_files = QPushButton("Dosya Ekle")
        self.btn_add_folder = QPushButton("Klasör Ekle")
        buttons.addWidget(self.btn_add_files)
        buttons.addWidget(self.btn_add_folder)
        layout.addLayout(buttons)

        line2 = QHBoxLayout()
        self.btn_remove_selected = QPushButton("Seçileni Kaldır")
        self.btn_clear_files = QPushButton("Listeyi Temizle")
        line2.addWidget(self.btn_remove_selected)
        line2.addWidget(self.btn_clear_files)
        layout.addLayout(line2)

        form = QFormLayout()
        self.ext_filter_edit = QLineEdit("txt, md, json, csv, yaml, yml, ini, log")
        self.ext_filter_edit.setPlaceholderText("Uzantılar: txt, md, json ... (boş = tüm dosyalar)")
        self.cb_recursive = QCheckBox("Alt klasörleri tara")
        self.cb_recursive.setChecked(True)
        form.addRow("Uzantı filtresi", self.ext_filter_edit)
        form.addRow("", self.cb_recursive)
        layout.addLayout(form)

        self.file_list = FileListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.file_list, 1)

        self.file_count_label = QLabel("0 dosya")
        layout.addWidget(self.file_count_label)

        return panel

    def _build_rules_panel(self) -> QWidget:
        panel = QGroupBox("Dönüşüm Kuralları")
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.btn_add_rule = QPushButton("+ Kural Ekle")
        self.btn_clear_rules = QPushButton("Kuralları Temizle")
        self.btn_import_rules = QPushButton("İçe Aktar")
        self.btn_export_rules = QPushButton("Dışa Aktar")
        toolbar.addWidget(self.btn_add_rule)
        toolbar.addWidget(self.btn_clear_rules)
        toolbar.addStretch(1)
        toolbar.addWidget(self.btn_import_rules)
        toolbar.addWidget(self.btn_export_rules)
        layout.addLayout(toolbar)

        hint = QLabel(
            "İpucu: Değiştir alanını boş bırakmak, bulunan metni siler. "
            "Regex aktifken Bul alanı düzenli ifade olarak değerlendirilir."
        )
        hint.setWordWrap(True)
        hint.setObjectName("HintLabel")
        layout.addWidget(hint)

        self.rules_scroll = QScrollArea()
        self.rules_list = QListWidget()
        self.rules_list.setDragEnabled(True)
        self.rules_list.setAcceptDrops(True)
        self.rules_list.setDropIndicatorShown(True)
        self.rules_list.setDragDropMode(QListWidget.InternalMove)
        self.rules_list.setDefaultDropAction(Qt.MoveAction)
        self.rules_list.setSelectionMode(QListWidget.SingleSelection)
        self.rules_list.setSpacing(2)
        self.rules_list.setAlternatingRowColors(False)
        layout.addWidget(self.rules_list, 1)

        self.rule_count_label = QLabel("0 aktif kural")
        layout.addWidget(self.rule_count_label)

        return panel

    def _build_controls_panel(self) -> QWidget:
        panel = QGroupBox("İşlem ve Güvenlik")
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.cb_backup = QCheckBox("Değişen dosyaların .bak yedeğini al")
        self.cb_backup.setChecked(False)

        self.cb_dry_run = QCheckBox("Dry-run (dosyaya yazmadan simüle et)")
        self.cb_remember = QCheckBox("Kapanışta son oturumu hatırla")
        self.cb_remember.setChecked(True)

        self.btn_restore = QPushButton(".bak Yedeklerinden Geri Yükle")

        self.btn_run = QPushButton("Tüm Değişiklikleri Uygula")
        self.btn_run.setMinimumHeight(42)
        self.btn_run.setProperty("primary", True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.summary_label = QLabel("Bekliyor")
        self.summary_label.setWordWrap(True)

        layout.addWidget(self.cb_backup)
        layout.addWidget(self.cb_dry_run)
        layout.addWidget(self.cb_remember)
        layout.addWidget(self._line())
        layout.addWidget(self.btn_restore)
        layout.addWidget(self._line())
        layout.addWidget(self.btn_run)
        layout.addWidget(self.progress)
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

        return panel

    @staticmethod
    def _line() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _connect_events(self) -> None:
        self.btn_add_files.clicked.connect(self.add_files_dialog)
        self.btn_add_folder.clicked.connect(self.add_folder_dialog)
        self.btn_remove_selected.clicked.connect(self.remove_selected_files)
        self.btn_clear_files.clicked.connect(self.clear_files)

        self.btn_add_rule.clicked.connect(self.add_rule)
        self.btn_clear_rules.clicked.connect(self.clear_rules)
        self.btn_export_rules.clicked.connect(self.export_rules)
        self.btn_import_rules.clicked.connect(self.import_rules)

        self.btn_run.clicked.connect(self.start_processing)
        self.btn_restore.clicked.connect(self.restore_backups)

        self.act_export_rules.triggered.connect(self.export_rules)
        self.act_import_rules.triggered.connect(self.import_rules)
        self.act_save_session.triggered.connect(self.save_session)
        self.act_exit.triggered.connect(self.close)
        self.act_start.triggered.connect(self.start_processing)
        self.act_about.triggered.connect(self.show_about)

        self.file_list.files_dropped.connect(self.add_paths)
        self.file_list.itemSelectionChanged.connect(self.on_file_selection_changed)
        self.file_list.preview_requested.connect(self.open_file_preview_for_path)
        self.file_list.diff_requested.connect(self.open_diff_preview_for_path)
        self.rules_list.model().rowsMoved.connect(self._on_rules_reordered)

    def _apply_style(self) -> None:
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #f3f6fb, stop:0.55 #edf2f8, stop:1 #e8eef6);
            }
            QMenuBar, QMenu {
                background-color: #e6ebf2;
                color: #1f2937;
                border: 1px solid #c9d4e5;
            }
            QMenu::item:selected {
                background-color: #bfdbfe;
                color: #0f172a;
            }
            QWidget {
                color: #1f2937;
            }
            QGroupBox {
                border: 1px solid #c7d3e6;
                border-radius: 10px;
                margin-top: 10px;
                padding: 9px;
                background-color: rgba(255, 255, 255, 0.9);
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: #334155;
            }
            QLineEdit, QTextEdit, QListWidget, QScrollArea, QTabWidget::pane {
                border: 1px solid #c7d3e6;
                border-radius: 8px;
                background-color: #fbfdff;
                selection-background-color: #bfdbfe;
                color: #0f172a;
            }
            QListWidget {
                alternate-background-color: #f2f6fc;
            }
            QListWidget::item {
                padding: 3px 6px;
                color: #0f172a;
            }
            QListWidget::item:hover {
                background-color: #dbeafe;
                color: #0f172a;
            }
            QListWidget::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QPushButton {
                background-color: #e2e8f0;
                border: 1px solid #c0cedf;
                border-radius: 8px;
                padding: 6px 10px;
                color: #0f172a;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #d3deeb;
            }
            QPushButton[primary="true"] {
                background-color: #0f766e;
                border-color: #0f766e;
                color: #ffffff;
                font-size: 14px;
            }
            QPushButton[primary="true"]:hover {
                background-color: #0d6861;
            }
            QPushButton[danger="true"] {
                background-color: #fee2e2;
                border-color: #fca5a5;
                color: #7f1d1d;
                padding: 2px 4px;
            }
            QProgressBar {
                border: 1px solid #c7d3e6;
                border-radius: 7px;
                text-align: center;
                color: #1f2937;
                background: #eef3f9;
                min-height: 20px;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0f766e, stop:1 #14b8a6);
            }
            QCheckBox {
                spacing: 8px;
            }
            QLabel#HintLabel {
                color: #475569;
                background-color: #f3f7fd;
                border: 1px solid #d2ddec;
                border-radius: 8px;
                padding: 8px;
            }
            QTabBar::tab {
                background: #e8eef7;
                border: 1px solid #c7d3e6;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                padding: 6px 10px;
                margin-right: 2px;
                color: #334155;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0f172a;
            }
            """
        )

    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Dosya Seç",
            self._resolve_dialog_dir("add_files"),
            "Metin Dosyaları (*.txt *.md *.csv *.json *.yaml *.yml *.ini *.log *.xml *.html *.py);;Tüm Dosyalar (*.*)",
        )
        if files:
            self._set_dialog_dir("add_files", str(Path(files[0]).parent))
            self.add_paths(files)

    def parse_extensions(self) -> List[str]:
        raw = self.ext_filter_edit.text().strip()
        if not raw:
            return []

        normalized = (
            raw.replace(";", ",")
            .replace("|", ",")
            .replace(" ", ",")
            .split(",")
        )
        result = []
        for ext in normalized:
            ext = ext.strip().lower().lstrip(".")
            if ext:
                result.append(ext)
        return list(dict.fromkeys(result))

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Klasör Seç",
            self._resolve_dialog_dir("add_folder"),
        )
        if not folder:
            return

        self._set_dialog_dir("add_folder", folder)

        base = Path(folder)
        recursive = self.cb_recursive.isChecked()
        exts = self.parse_extensions()

        files: List[str] = []
        iterator = base.rglob("*") if recursive else base.glob("*")
        for p in iterator:
            if p.is_file():
                if exts and p.suffix.lower().lstrip(".") not in exts:
                    continue
                files.append(str(p))

        self.add_paths(files)

    def add_paths(self, paths: List[str]) -> None:
        added = 0
        for p in paths:
            path = Path(p)
            if path.is_dir():
                recursive = self.cb_recursive.isChecked()
                exts = self.parse_extensions()
                iterator = path.rglob("*") if recursive else path.glob("*")
                for sub in iterator:
                    if not sub.is_file():
                        continue
                    if exts and sub.suffix.lower().lstrip(".") not in exts:
                        continue
                    added += self._append_file(str(sub))
            else:
                added += self._append_file(str(path))

        if added:
            self.log(f"{added} yeni dosya eklendi.")

        self._update_file_counter()

    def _append_file(self, file_path: str) -> int:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return 0
        norm = str(path.resolve())
        if norm in self.known_files:
            return 0

        item = QListWidgetItem(norm)
        item.setToolTip(norm)
        self.file_list.addItem(item)
        self.known_files.add(norm)
        return 1

    def remove_selected_files(self) -> None:
        selected = self.file_list.selectedItems()
        if not selected:
            return

        for item in selected:
            path = item.text()
            self.known_files.discard(path)
            self.file_list.takeItem(self.file_list.row(item))

        self._update_file_counter()

    def clear_files(self) -> None:
        self.file_list.clear()
        self.known_files.clear()
        self._update_file_counter()

    def add_rule(self, preset: ReplacementRule | None = None) -> None:
        row = RuleRow()
        row.remove_requested.connect(self.remove_rule)
        row.enabled_cb.toggled.connect(self._on_rules_changed)
        row.find_edit.textChanged.connect(self._on_rules_changed)
        row.replace_edit.textChanged.connect(self.preview_selected_file)
        row.regex_cb.toggled.connect(self.preview_selected_file)
        row.case_cb.toggled.connect(self.preview_selected_file)
        row.word_cb.toggled.connect(self.preview_selected_file)
        if preset:
            row.from_rule(preset)

        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        self.rules_list.addItem(item)
        self.rules_list.setItemWidget(item, row)
        self._update_rule_counter()

    def remove_rule(self, row_widget: QWidget) -> None:
        for index in range(self.rules_list.count()):
            item = self.rules_list.item(index)
            if self.rules_list.itemWidget(item) is row_widget:
                self.rules_list.takeItem(index)
                row_widget.deleteLater()
                break
        self._update_rule_counter()

    def clear_rules(self) -> None:
        while self.rules_list.count():
            item = self.rules_list.takeItem(0)
            widget = self.rules_list.itemWidget(item)
            if widget:
                widget.deleteLater()
        self._update_rule_counter()

    def collect_rules(self) -> List[ReplacementRule]:
        rules: List[ReplacementRule] = []
        for index in range(self.rules_list.count()):
            item = self.rules_list.item(index)
            row = self.rules_list.itemWidget(item)
            if isinstance(row, RuleRow):
                rules.append(row.to_rule())
        return rules

    def active_rules(self) -> List[ReplacementRule]:
        return [r for r in self.collect_rules() if r.enabled and r.find]

    def export_rules(self) -> None:
        rules = self.collect_rules()
        if not rules:
            QMessageBox.information(self, APP_NAME, "Dışa aktarılacak kural yok.")
            return

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Kural Setini Kaydet",
            str(Path(self._resolve_dialog_dir("export_rules")) / "ruleset.json"),
            "JSON (*.json)",
        )
        if not target:
            return

        self._set_dialog_dir("export_rules", str(Path(target).parent))

        payload = {
            "version": RULESET_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "rules": [asdict(r) for r in rules],
        }
        Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"Kural seti kaydedildi: {target}")

    def import_rules(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Kural Seti Seç",
            self._resolve_dialog_dir("import_rules"),
            "JSON (*.json)",
        )
        if not source:
            return

        self._set_dialog_dir("import_rules", str(Path(source).parent))

        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            rows = payload.get("rules", [])
            parsed = [ReplacementRule(**item) for item in rows]
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Kural seti okunamadı:\n{exc}")
            return

        self.clear_rules()
        for rule in parsed:
            self.add_rule(rule)

        self.log(f"Kural seti yüklendi: {source}")

    def on_file_selection_changed(self) -> None:
        self.preview_file_content()
        self.preview_selected_file()

    def _select_file_item(self, path_str: str) -> None:
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if item.text() == path_str:
                self.file_list.setCurrentItem(item)
                return

    def open_file_preview_for_path(self, path_str: str) -> None:
        self._select_file_item(path_str)
        self.preview_file_content(Path(path_str))
        self.bottom_tabs.setCurrentWidget(self.file_preview_edit)

    def open_diff_preview_for_path(self, path_str: str) -> None:
        self._select_file_item(path_str)
        self.preview_selected_file(Path(path_str), switch_tab=True)

    def preview_file_content(self, file_path: Path | None = None) -> None:
        if file_path is None:
            current = self.file_list.currentItem()
            if not current:
                self.file_preview_edit.setPlainText("Dosya önizleme için listeden bir dosya seçin.")
                return
            file_path = Path(current.text())

        if not file_path:
            self.file_preview_edit.setPlainText("Dosya önizleme için listeden bir dosya seçin.")
            return

        if not file_path.exists():
            self.file_preview_edit.setPlainText("Dosya mevcut değil.")
            return

        try:
            content, encoding = TextIO.read_text(file_path)
            if len(content) > 400_000:
                content = content[:400_000] + "\n\n... içerik uzun olduğu için kısaltıldı ..."
            self.file_preview_edit.setPlainText(content)
            self.file_preview_edit.setToolTip(f"Kodlama: {encoding}")
        except Exception as exc:
            self.file_preview_edit.setPlainText(f"Dosya okunamadı: {exc}")

    def render_diff_html(self, diff_lines: List[str]) -> str:
        html_lines: List[str] = [
            "<pre style='font-family:Consolas, "
            "\"Cascadia Mono\", "
            "monospace; font-size:12px; line-height:1.38; margin:0;'>"
        ]

        for line in diff_lines:
            escaped = html.escape(line)
            style = "color:#334155;"

            if line.startswith("@@"):
                style = "color:#6d28d9; background:#f5f3ff; font-weight:600;"
            elif line.startswith("---") or line.startswith("+++"):
                style = "color:#1e293b; background:#e2e8f0; font-weight:600;"
            elif line.startswith("+") and not line.startswith("+++"):
                style = "color:#14532d; background:#dcfce7;"
            elif line.startswith("-") and not line.startswith("---"):
                style = "color:#7f1d1d; background:#fee2e2;"

            html_lines.append(f"<span style='{style}'>{escaped}</span>")

        html_lines.append("</pre>")
        return "".join(html_lines)

    def preview_selected_file(self, file_path: Path | None = None, switch_tab: bool = False) -> None:
        if file_path is None:
            current = self.file_list.currentItem()
            if not current:
                self.diff_edit.setPlainText("Diff önizleme için listeden bir dosya seçin.")
                return
            file_path = Path(current.text())

        if not file_path:
            self.diff_edit.setPlainText("Diff önizleme için listeden bir dosya seçin.")
            return

        rules = self.active_rules()
        if not rules:
            self.diff_edit.setPlainText("Önizleme için en az bir aktif kural gerekli.")
            return

        if not file_path.exists():
            self.diff_edit.setPlainText("Dosya mevcut değil.")
            return

        try:
            original, _ = TextIO.read_text(file_path)
            updated, _ = ProcessorWorker.apply_rules(original, rules)
            if original == updated:
                self.diff_edit.setPlainText("Bu dosyada mevcut kurallara göre değişiklik oluşmuyor.")
                return

            diff_lines = list(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=f"{file_path.name} (orijinal)",
                    tofile=f"{file_path.name} (yeni)",
                    n=2,
                )
            )

            if len(diff_lines) > 2000:
                diff_lines = diff_lines[:2000]
                diff_lines.append("\n... diff çıktısı kesildi (çok uzun) ...\n")

            self.diff_edit.setHtml(self.render_diff_html(diff_lines))
            if switch_tab:
                self.bottom_tabs.setCurrentWidget(self.diff_edit)
        except Exception as exc:
            self.diff_edit.setPlainText(f"Önizleme hatası: {exc}")

    def start_processing(self) -> None:
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        rules = self.active_rules()

        if not files:
            QMessageBox.warning(self, APP_NAME, "Önce en az bir dosya eklemelisiniz.")
            return
        if not rules:
            QMessageBox.warning(self, APP_NAME, "Önce en az bir aktif kural girmelisiniz.")
            return

        self.btn_run.setEnabled(False)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.summary_label.setText("İşlem başladı...")
        self.log(f"İşlem başladı. Dosya: {len(files)}, Kural: {len(rules)}")

        self.worker_thread = QThread(self)
        self.worker = ProcessorWorker(
            files=files,
            rules=rules,
            create_backup=self.cb_backup.isChecked(),
            dry_run=self.cb_dry_run.isChecked(),
        )
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.finished.connect(self.on_finished)

        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.worker_thread.start()

    def on_progress(self, current: int, total: int, file_path: str) -> None:
        self.progress.setValue(current)
        self.statusBar().showMessage(f"{current}/{total} işlendi - {file_path}")

    def on_file_done(self, file_path: str, replaced_count: int, changed: bool, note: str) -> None:
        color = "#b0e87f" if changed else "#9ab1d9"
        self.log(
            f"<span style='color:{color}'>• {file_path}</span> | "
            f"eşleşme: {replaced_count}, durum: {note}"
        )

    def on_finished(self, summary: dict) -> None:
        self.btn_run.setEnabled(True)
        self.statusBar().showMessage("İşlem tamamlandı")

        text = (
            f"Tamamlandı | işlenen: {summary['processed']}, "
            f"değişen: {summary['changed']}, "
            f"toplam eşleşme: {summary['replacements']}, "
            f"hata: {summary['errors']}"
        )
        if summary.get("dry_run"):
            text += " (dry-run modu)"

        self.summary_label.setText(text)
        self.log(f"<b>{text}</b>")

        if summary["errors"]:
            QMessageBox.warning(self, APP_NAME, text)
        else:
            QMessageBox.information(self, APP_NAME, text)

    def restore_backups(self) -> None:
        selected = self.file_list.selectedItems()
        targets = [i.text() for i in selected] if selected else [self.file_list.item(i).text() for i in range(self.file_list.count())]

        if not targets:
            QMessageBox.information(self, APP_NAME, "Önce dosya listesi oluşturun.")
            return

        reply = QMessageBox.question(
            self,
            APP_NAME,
            "Seçili dosyalar için .bak yedeklerinden geri yükleme yapılsın mı?",
        )
        if reply != QMessageBox.Yes:
            return

        restored = 0
        missing = 0
        for path_str in targets:
            path = Path(path_str)
            bak = Path(str(path) + ".bak")
            if bak.exists():
                shutil.copy2(bak, path)
                restored += 1
            else:
                missing += 1

        msg = f"Geri yüklenen: {restored}, yedeği bulunamayan: {missing}"
        self.log(msg)
        QMessageBox.information(self, APP_NAME, msg)

    def save_session(self) -> None:
        rules = [asdict(r) for r in self.collect_rules()]
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]

        data = {
            "window": {
                "width": self.width(),
                "height": self.height(),
            },
            "options": {
                "backup": self.cb_backup.isChecked(),
                "dry_run": self.cb_dry_run.isChecked(),
                "remember": self.cb_remember.isChecked(),
                "recursive": self.cb_recursive.isChecked(),
                "extensions": self.ext_filter_edit.text(),
            },
            "dialog_dirs": self.dialog_dirs,
            "files": files,
            "rules": rules,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.store.save(data)
        self.log("Oturum kaydedildi.")

    def _load_session(self) -> None:
        data = self.store.load()
        if not data:
            self.add_rule(ReplacementRule())
            return

        win = data.get("window", {})
        w = int(win.get("width", 1200))
        h = int(win.get("height", 760))
        self.resize(w, h)

        opts = data.get("options", {})
        self.cb_backup.setChecked(bool(opts.get("backup", False)))
        self.cb_dry_run.setChecked(bool(opts.get("dry_run", False)))
        self.cb_remember.setChecked(bool(opts.get("remember", True)))
        self.cb_recursive.setChecked(bool(opts.get("recursive", True)))
        self.ext_filter_edit.setText(opts.get("extensions", self.ext_filter_edit.text()))

        loaded_dirs = data.get("dialog_dirs", {})
        if isinstance(loaded_dirs, dict):
            for key in self.dialog_dirs:
                value = loaded_dirs.get(key, "")
                if isinstance(value, str):
                    self.dialog_dirs[key] = value

        for f in data.get("files", []):
            self._append_file(f)

        loaded_rules = data.get("rules", [])
        if loaded_rules:
            for r in loaded_rules:
                try:
                    self.add_rule(ReplacementRule(**r))
                except TypeError:
                    continue
        else:
            self.add_rule(ReplacementRule())

        self._update_file_counter()
        self.preview_file_content()
        self.preview_selected_file()
        self.log("Son oturum yüklendi.")

    def _on_rules_changed(self) -> None:
        self._update_rule_counter()
        self.preview_selected_file()

    def _on_rules_reordered(self, *args) -> None:
        self._update_rule_counter()
        self.preview_selected_file()

    def _program_default_dir(self) -> str:
        cwd = Path.cwd()
        if cwd.exists() and cwd.is_dir():
            return str(cwd)
        return str(Path(__file__).resolve().parent)

    def _resolve_dialog_dir(self, key: str) -> str:
        raw = self.dialog_dirs.get(key, "").strip()
        if raw:
            path = Path(raw)
            if path.exists() and path.is_dir():
                return str(path)
        return self._program_default_dir()

    def _set_dialog_dir(self, key: str, path_str: str) -> None:
        path = Path(path_str)
        if path.exists() and path.is_file():
            path = path.parent

        if path.exists() and path.is_dir():
            self.dialog_dirs[key] = str(path)
        else:
            self.dialog_dirs[key] = self._program_default_dir()

    def closeEvent(self, event):
        if self.cb_remember.isChecked():
            self.save_session()
        event.accept()

    def _update_file_counter(self) -> None:
        count = self.file_list.count()
        self.file_count_label.setText(f"{count} dosya")

    def _update_rule_counter(self) -> None:
        active = len([r for r in self.collect_rules() if r.enabled and r.find])
        self.rule_count_label.setText(f"{active} aktif kural")

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_edit.insertHtml(f"[{ts}] {message}<br>")
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "Hakkında",
            (
                f"<b>{APP_NAME}</b><br>"
                f"Sürüm: {APP_VERSION}<br><br>"
                "Çoklu dosya + çoklu kural metin dönüşümü için gelişmiş masaüstü aracı.<br>"
                "Özellikler: import/export, dry-run, yedek alma, diff önizleme, oturum hatırlama."
            ),
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon())

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
