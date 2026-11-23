from __future__ import annotations

import asyncio
import ctypes
import hashlib
import os
import queue
import re
import shutil
import ssl
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QImageReader,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pawchive.api.pawchive import PawchiveApi, build_file_url, normalize_creator_path
from pawchive.downloader.engine import DownloadEngine
from pawchive.downloader.models import (
    DateNamingMode,
    DownloadEvent,
    DownloadTask,
    FailedDownload,
    FileItem,
    PostItem,
)
from pawchive.downloader.naming import is_image_name
from pawchive.downloader.service import DownloadService
from pawchive.downloader.state import StateStore, ensure_state_dir, state_dir_for
from pawchive.downloader.temp import (
    clear_temp_root,
    iter_part_files,
    iter_temp_entries,
    migrate_program_root,
)
from pawchive.image_audit import (
    PreviewAuditResult,
    PreviewCandidate,
    audit_preview_images,
    replace_preview_images,
)
from pawchive.release_notes import version_announcement_text
from pawchive.updater import (
    PROJECT_URL,
    UpdateError,
    cleanup_legacy_manifest,
    cleanup_previous_update_files,
    cleanup_update_temp,
    current_executable,
    download_update,
    expected_sha256,
    install_update,
    is_newer,
    launch_update_helper,
    pop_update_status,
    probe_github_connectivity,
    restart_application,
    verify_target,
)
from pawchive.selfcheck import needs_self_check, run_self_check
from pawchive.verify import (
    STATUS_LABELS,
    SavedPost,
    SaveScan,
    VerifyResult,
    path_key,
    scan_save_root,
    verify_save_root,
)
from pawchive.settings import (
    ATTACHMENT_CATEGORIES,
    ATTACHMENT_CATEGORY_EXTS,
    ATTACHMENT_CATEGORY_KEYS,
    ATTACHMENT_CATEGORY_LABELS,
    AppSettings,
    IMAGE_SOURCE_KEYS,
    IMAGE_SOURCE_LABELS,
    MAX_FILE_CONCURRENCY,
    MAX_POST_CONCURRENCY,
    MAX_RETRY_COUNT,
    MAX_RETRY_INTERVAL,
    MIN_RETRY_COUNT,
    MIN_RETRY_INTERVAL,
    PREVIEW_MODE_KEYS,
    PREVIEW_MODE_LABELS,
    PROXY_TYPE_LABELS,
    describe_settings,
    describe_settings_brief,
    ensure_program_layout,
    failed_export_path,
    load_settings,
    normalize_extra_extensions,
    normalize_extra_output_dirs,
    normalize_output_dir,
    normalize_proxy_address,
    normalize_storage_dir,
    program_config_path,
    program_log_dir,
    program_root_for,
    resolve_temp_root,
    save_settings,
    storage_paths_overlap,
)
from pawchive.theme import (
    THEME_KEYS,
    THEME_LABELS,
    apply_title_bar,
    resolve_theme,
)
from pawchive.app.style import (
    about_qss,
    announcement_qss,
    main_qss,
    message_qss,
    palette_for,
    settings_qss,
)
from pawchive.version import APP_NAME, APP_VERSION


ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"
BACKGROUND_PATH = ASSET_DIR / "background.jpg"
FONT_PATH = ASSET_DIR / "YeZiGongChangTangYingHei-2.ttf"
ICON_PATH = ASSET_DIR / "icon.ico"
CHECK_ICON_PATH = ASSET_DIR / "check.png"
ARROW_DOWN_PATH = ASSET_DIR / "arrow_down.png"
ARROW_UP_PATH = ASSET_DIR / "arrow_up.png"
ARROW_DOWN_LIGHT_PATH = ASSET_DIR / "arrow_down_light.png"
ARROW_UP_LIGHT_PATH = ASSET_DIR / "arrow_up_light.png"
BRANCH_CLOSED = {"dark": ASSET_DIR / "branch_closed_dark.png", "light": ASSET_DIR / "branch_closed_light.png"}
BRANCH_OPEN = {"dark": ASSET_DIR / "branch_open_dark.png", "light": ASSET_DIR / "branch_open_light.png"}
TITLE_ICON_MIN = {"dark": ASSET_DIR / "win_min_dark.svg", "light": ASSET_DIR / "win_min_light.svg"}
TITLE_ICON_MAX = {"dark": ASSET_DIR / "win_max_dark.svg", "light": ASSET_DIR / "win_max_light.svg"}
TITLE_ICON_RESTORE = {
    "dark": ASSET_DIR / "win_restore_dark.svg",
    "light": ASSET_DIR / "win_restore_light.svg",
}
TITLE_ICON_CLOSE = {"dark": ASSET_DIR / "win_close_dark.svg", "light": ASSET_DIR / "win_close_light.svg"}
TITLE_ICON_CLOSE_HOVER = {
    "dark": ASSET_DIR / "win_close_dark_hover.svg",
    "light": ASSET_DIR / "win_close_light_hover.svg",
}
DEFAULT_FONT_FAMILY = "Microsoft YaHei UI"
WINDOWS_APP_ID = "PawchiveDownloader.App"


def format_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_post_time(value: str) -> str:
    text = (value or "").strip().replace("T", " ")
    if not text:
        return ""
    if len(text) >= 16:
        return text[:16]
    return text


def post_thumbnail_url(post: PostItem) -> str:
    """从帖子列表原始数据取第一张主图/图片附件，生成预览缩略图地址。"""
    raw = post.raw if isinstance(post.raw, dict) else {}
    candidates: list[str] = []
    file_info = raw.get("file")
    if isinstance(file_info, dict):
        path = str(file_info.get("path") or "").strip()
        name = str(file_info.get("name") or "").strip()
        if path and (is_image_name(name) or "/data/" in path):
            candidates.append(path)
    attachments = raw.get("attachments")
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            name = str(item.get("name") or "").strip()
            if path and is_image_name(name):
                candidates.append(path)
    if not candidates:
        return ""
    return build_file_url(candidates[0], "image", "preview")


def card_title(post: PostItem) -> str:
    title = (post.title or "无标题").strip()
    if len(title) > 46:
        title = title[:45].rstrip() + "…"
    return title


def saved_post_title(post: SavedPost) -> str:
    title = (post.title or post.path.name).strip()
    if len(title) > 46:
        title = title[:45].rstrip() + "…"
    return title


def saved_post_time(post: SavedPost) -> str:
    text = format_post_time(post.published)
    if text:
        return text
    try:
        return datetime.fromtimestamp(post.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


class ThumbnailLoader(QObject):
    """小并发异步加载网络缩略图，结果按 URL 回传，避免阻塞界面。"""

    loaded = pyqtSignal(str, bytes)
    failed = pyqtSignal(str)

    _MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings.normalized()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._requested: set[str] = set()
        self._lock = threading.Lock()
        for _ in range(3):
            threading.Thread(target=self._worker, daemon=True).start()

    def request(self, url: str) -> None:
        if not url:
            return
        with self._lock:
            if url in self._requested:
                return
            self._requested.add(url)
        self._queue.put(url)

    def stop(self) -> None:
        for _ in range(3):
            self._queue.put(None)

    def _worker(self) -> None:
        proxy_url = self.settings.proxy_url() if self.settings.uses_proxy else None
        if proxy_url and self.settings.proxy_type in ("http", "https"):
            proxy = ProxyHandler({"http": proxy_url, "https": proxy_url})
        else:
            proxy = ProxyHandler({})
        https = HTTPSHandler(context=ssl.create_default_context())
        opener = build_opener(proxy, https)
        while True:
            url = self._queue.get()
            if url is None:
                return
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": f"PawchiveDownloader/{APP_VERSION}",
                        "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
                        "Referer": "https://pawchive.pw/",
                    },
                )
                with opener.open(request, timeout=20) as response:
                    data = response.read(self._MAX_BYTES + 1)
                if not data or len(data) > self._MAX_BYTES:
                    raise ValueError("缩略图内容为空或超过大小上限")
                self.loaded.emit(url, data)
            except BaseException:
                self.failed.emit(url)


class PreviewCard(QFrame):
    """瀑布流卡片；缩略图模式只显示图片，全部展示模式加标题和时间戳。"""

    selection_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        key: str,
        title: str,
        timestamp: str,
        mode: str,
        *,
        selectable: bool,
        checked: bool = False,
        thumbnail_url: str = "",
        local_image: Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("previewCard")
        self.key = key
        self.mode = mode
        self.thumbnail_url = thumbnail_url
        self.local_image = local_image
        self._title_text = title
        self._pixmap: QPixmap | None = None
        self._aspect = 4 / 3
        self._selectable = selectable
        self._checked = checked
        self._image_height = 180
        self.setCursor(Qt.CursorShape.PointingHandCursor if selectable else Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(8, 7, 8, 3)
        top.setSpacing(6)
        self.checkbox: QCheckBox | None = None
        if selectable:
            self.checkbox = QCheckBox()
            self.checkbox.setChecked(checked)
            self.checkbox.setFixedSize(18, 18)
            self.checkbox.setToolTip("选择这个帖子")
            self.checkbox.toggled.connect(self._on_checked)
            top.addWidget(self.checkbox, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.title_label: QLabel | None = None
        if mode == "full":
            self.title_label = QLabel(title)
            self.title_label.setObjectName("previewTitle")
            self.title_label.setToolTip(title)
            self.title_label.setWordWrap(False)
            self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            top.addWidget(self.title_label, 1)
        top.addStretch(1) if self.title_label is None else None
        outer.addLayout(top)

        self.image_label = QLabel()
        self.image_label.setObjectName("previewImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.image_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        outer.addWidget(self.image_label)

        self.time_label: QLabel | None = None
        if mode == "full":
            self.time_label = QLabel(timestamp)
            self.time_label.setObjectName("previewTime")
            self.time_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            outer.addWidget(self.time_label)
        outer.addStretch(1) if mode != "full" else None

        self._apply_selected_style()
        self.image_label.setText("无缩略图")

    def _on_checked(self, checked: bool) -> None:
        self._checked = checked
        self._apply_selected_style()
        self.selection_changed.emit(self.key, checked)

    def _apply_selected_style(self) -> None:
        self.setProperty("selected", "true" if self._checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_checked(self, checked: bool, *, emit: bool = False) -> None:
        self._checked = checked
        if self.checkbox is not None:
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(checked)
            self.checkbox.blockSignals(False)
        self._apply_selected_style()
        if emit:
            self.selection_changed.emit(self.key, checked)

    def is_checked(self) -> bool:
        return self._checked

    def set_local_image(self, path: Path) -> None:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.height() > 0:
            self._aspect = max(0.2, min(5.0, size.width() / size.height()))
        image = reader.read()
        if image.isNull():
            self.image_label.setText("缩略图读取失败")
            return
        self._pixmap = QPixmap.fromImage(image)
        self._render_image()

    def set_thumbnail_bytes(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.image_label.setText("缩略图读取失败")
            return False
        if pixmap.height() > 0:
            self._aspect = max(0.2, min(5.0, pixmap.width() / pixmap.height()))
        self._pixmap = pixmap
        self._render_image()
        return True

    def set_image_area_height(self, height: int) -> None:
        self._image_height = max(120, height)
        self.image_label.setFixedHeight(self._image_height)
        self._render_image()

    def preferred_height(self, width: int) -> int:
        photo_height = int(width / self._aspect)
        photo_height = max(150, min(560, photo_height))
        extra = 53 if self.mode == "full" else 30
        return photo_height + extra

    def _update_title_elide(self) -> None:
        if self.title_label is None:
            return
        gutter = 16 + (24 if self.checkbox is not None else 0)
        width = max(20, self.width() - gutter)
        self.title_label.setFixedWidth(width)
        metrics = QFontMetrics(self.title_label.font())
        self.title_label.setText(
            metrics.elidedText(self._title_text, Qt.TextElideMode.ElideRight, width)
        )

    def _render_image(self) -> None:
        if self._pixmap is None:
            return
        target = QSize(max(40, self.image_label.width() - 2), max(40, self._image_height - 2))
        self.image_label.setPixmap(
            self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_title_elide()
        self._render_image()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._selectable and event.button() == Qt.MouseButton.LeftButton:
            if self.checkbox is not None:
                self.checkbox.toggle()
                event.accept()
                return
        super().mousePressEvent(event)


class MasonryPanel(QWidget):
    """三列瀑布流；卡片高度按图片长宽比计算，尽量少留空白。"""

    selection_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        mode: str,
        *,
        selectable: bool,
        settings: AppSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.selectable = selectable
        self.settings = settings.normalized()
        self.cards: list[PreviewCard] = []
        self.loader: ThumbnailLoader | None = None
        self.setObjectName("previewPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(1)

    def clear(self) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self.setMinimumHeight(1)

    def add_card(self, card: PreviewCard) -> None:
        card.selection_changed.connect(self.selection_changed)
        card.setParent(self)
        card.show()
        self.cards.append(card)

    def ensure_loader(self) -> ThumbnailLoader:
        if self.loader is None:
            self.loader = ThumbnailLoader(self.settings)
            self.loader.loaded.connect(self._on_loaded)
            self.loader.failed.connect(self._on_failed)
        return self.loader

    def request_visible(self, top: int, height: int, margin: int = 360) -> None:
        for card in self.cards:
            if card.y() < top + height + margin and card.y() + card.height() > top - margin:
                self.request_card(card)

    def request_card(self, card: PreviewCard) -> None:
        if card.local_image is not None:
            if card._pixmap is None:
                card.set_local_image(card.local_image)
                self.relayout()
            return
        if not card.thumbnail_url:
            return
        self.ensure_loader().request(card.thumbnail_url)

    def _on_loaded(self, url: str, data: bytes) -> None:
        changed = False
        for card in self.cards:
            if card.thumbnail_url == url and card._pixmap is None:
                if card.set_thumbnail_bytes(data):
                    changed = True
        if changed:
            self.relayout()

    def _on_failed(self, url: str) -> None:
        for card in self.cards:
            if card.thumbnail_url == url and card._pixmap is None:
                card.image_label.setText("缩略图加载失败")

    def set_checked_keys(self, keys: set[str]) -> None:
        for card in self.cards:
            card.set_checked(card.key in keys)

    def checked_keys(self) -> set[str]:
        return {card.key for card in self.cards if card.is_checked()}

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()

    def relayout(self) -> None:
        width = max(1, self.width())
        gap = 10
        column_width = max(80, (width - gap * 2) // 3)
        heights = [0, 0, 0]
        for card in self.cards:
            column = min(range(3), key=lambda index: heights[index])
            x = column * (column_width + gap)
            y = heights[column]
            card.setFixedWidth(column_width)
            total = card.preferred_height(column_width)
            card.setFixedHeight(total)
            extra = 53 if self.mode == "full" else 30
            card.set_image_area_height(total - extra)
            card.move(x, y)
            heights[column] = y + total + gap
        self.setMinimumHeight(max(1, max(heights) - gap))


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def style_button(button: QPushButton, height: int = 36, width: int | None = None) -> QPushButton:
    button.setFixedHeight(height)
    if width:
        button.setFixedWidth(width)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


MESSAGE_KINDS = {
    "info": ("i", "#3478f6"),
    "warning": ("!", "#e8a33d"),
    "error": ("!", "#e06060"),
}


class MessageDialog(QDialog):
    """与主界面同一套深色风格的提示/确认弹窗，替代系统 QMessageBox。"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str = "info",
        confirm: bool = False,
        theme: str = "dark",
    ):
        super().__init__(parent)
        self.confirmed = False
        self.theme = theme if theme in ("light", "dark") else "dark"
        self.palette = palette_for(self.theme)
        self.setWindowTitle(title)
        self.setWindowIcon(load_app_icon())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(440)
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 20)
        self.card = QFrame()
        self.card.setObjectName("dialogCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, self.palette.shadow_alpha))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        glyph, color = MESSAGE_KINDS.get(kind, MESSAGE_KINDS["info"])
        badge = QLabel(glyph)
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {color}; color: #ffffff; border-radius: 12px;"
            "font-size: 13px; font-weight: 700;"
        )
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        header.addWidget(heading)
        header.addStretch(1)

        close_icon = QPushButton("✕")
        close_icon.setObjectName("dialogClose")
        close_icon.setFixedSize(26, 26)
        close_icon.setToolTip("关闭")
        close_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        close_icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_icon.clicked.connect(self.reject)
        header.addWidget(close_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        card_layout.addLayout(header)

        card_layout.addWidget(divider())

        body = QLabel(text)
        body.setObjectName("dialogBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMinimumWidth(356)
        card_layout.addWidget(body)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        if confirm:
            cancel_button = QPushButton("取消")
            cancel_button.setObjectName("dialogGhost")
            style_button(cancel_button, 34, 84)
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(cancel_button)
        ok_button = QPushButton("确定")
        ok_button.setObjectName("dialogPrimary")
        style_button(ok_button, 34, 96)
        ok_button.setDefault(True)
        ok_button.clicked.connect(self._accept)
        buttons.addWidget(ok_button)
        card_layout.addLayout(buttons)

        self.setStyleSheet(message_qss(self.palette))

        self.adjustSize()

    def _accept(self) -> None:
        self.confirmed = True
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.parentWidget() is not None:
            center = self.parentWidget().window().frameGeometry().center()
            self.move(center - self.rect().center())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    @classmethod
    def show_message(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str = "info",
        confirm: bool = False,
        theme: str = "dark",
    ) -> bool:
        dialog = cls(parent, title, text, kind, confirm, theme)
        dialog.exec()
        return dialog.confirmed


class AnnouncementDialog(QDialog):
    closed = pyqtSignal(bool)

    def __init__(
        self,
        text: str,
        font_family: str,
        parent: QWidget | None = None,
        version: str = "",
        theme: str = "dark",
    ):
        super().__init__(parent)
        self.theme = theme if theme in ("light", "dark") else "dark"
        self.setWindowTitle("版本更新")
        self.setWindowIcon(load_app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._drag_offset = None
        self.resize(600, 460)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("版本更新")
        title.setObjectName("announcementTitle")
        header.addWidget(title)
        header.addStretch(1)
        close_icon = QPushButton("✕")
        close_icon.setObjectName("ghostIconButton")
        close_icon.setFixedSize(30, 30)
        close_icon.setToolTip("关闭公告")
        close_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        close_icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_icon.clicked.connect(self.close)
        header.addWidget(close_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        layout.addWidget(divider())

        content = QPlainTextEdit()
        content.setObjectName("announcementContent")
        content.setReadOnly(True)
        content.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.setPlainText(text)
        layout.addWidget(content, stretch=1)

        self.dismiss_box = QCheckBox("下次版本更新前不再提示")
        self.dismiss_box.setToolTip(
            f"当前版本 v{version or '未知'}；程序版本更新后会重新显示公告"
        )
        self.dismiss_box.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.dismiss_box)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        note = QLabel(f"当前版本 v{version or '未知'} 的更新内容")
        note.setObjectName("dialogHint")
        footer.addWidget(note)
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("ghostButton")
        style_button(close_button, 34, 96)
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.setStyleSheet(
            announcement_qss(palette_for(self.theme), font_family, CHECK_ICON_PATH.as_posix())
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_title_bar(self, self.theme)
        if self.parentWidget() is not None:
            center = self.parentWidget().window().frameGeometry().center()
            self.move(center - self.rect().center())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit(self.dismiss_box.isChecked())
        super().closeEvent(event)


class UpdateWorker(QObject):
    """在后台线程执行连通性检查、下载、校验与替换。"""

    stage_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    log_line = pyqtSignal(str, bool)
    latest_version = pyqtSignal(str)
    install_complete = pyqtSignal(str)
    failed = pyqtSignal(str, str)
    finished = pyqtSignal(str)

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _emit_stage(self, text: str) -> None:
        self.stage_changed.emit(text)
        self.log_line.emit(text, False)

    def _run(self) -> None:
        temp_dir: Path | None = None
        try:
            target = current_executable()
            if target is None:
                raise UpdateError(
                    "检查安装环境",
                    "当前是源码运行模式，无法安全替换正在运行的源码；请使用发布版 exe 检查更新",
                )

            self._emit_stage("检查 GitHub 连通性与最新版本...")
            release = probe_github_connectivity()
            self.latest_version.emit(release.version)
            if not is_newer(release.version, self.current_version):
                self.finished.emit(f"当前已是最新版本 v{self.current_version}")
                return

            asset = release.exe_asset
            if asset is None:
                raise UpdateError("版本检查", "最新 Release 没有可下载的 exe 附件")
            expected_hash = expected_sha256(release, asset)

            self._emit_stage("校验当前程序...")
            verify_target(target)

            self._emit_stage(f"发现新版本 v{release.version}，准备下载 {asset.name}")
            temp_dir = Path(tempfile.mkdtemp(prefix="pawchive_update_"))
            downloaded = temp_dir / asset.name
            download_update(
                asset,
                downloaded,
                expected_hash=expected_hash,
                progress=self.progress_changed.emit,
                stage=self._emit_stage,
                cancel=self.cancel_event.is_set,
            )

            self._emit_stage("启动外置更新器...")
            launch_update_helper(
                downloaded,
                target,
                expected_hash=expected_hash,
                parent_pid=os.getpid(),
            )
            temp_dir = None
            self._emit_stage("更新器已启动，程序退出后完成替换并重启")
            self.install_complete.emit(str(target))
        except UpdateError as exc:
            self.failed.emit(exc.stage, exc.reason)
        except Exception as exc:
            self.failed.emit("更新异常", f"{type(exc).__name__}: {exc}")
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)


class UpdateDialog(QDialog):
    """更新过程对话框：阶段、完整性校验和复制进度均可见。"""

    update_result = pyqtSignal(str, bool)
    closed = pyqtSignal()

    def __init__(
        self,
        current_version: str,
        font_family: str,
        parent: QWidget | None = None,
        theme: str = "dark",
        log_callback=None,
    ):
        super().__init__(parent)
        self.theme = theme if theme in ("light", "dark") else "dark"
        self.font_family = font_family
        self.log_callback = log_callback
        self.current_version = current_version
        self._drag_offset = None
        self._busy = True
        self.setWindowTitle("检查更新")
        self.setWindowIcon(load_app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(620, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("程序更新")
        title.setObjectName("announcementTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.close_icon = QPushButton("✕")
        self.close_icon.setObjectName("ghostIconButton")
        self.close_icon.setFixedSize(30, 30)
        self.close_icon.setToolTip("更新进行中，暂不能关闭")
        self.close_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_icon.setEnabled(False)
        self.close_icon.clicked.connect(self.close)
        header.addWidget(self.close_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        layout.addWidget(divider())

        self.version_label = QLabel(f"当前版本：v{current_version}")
        self.version_label.setObjectName("dialogHint")
        layout.addWidget(self.version_label)
        self.latest_label = QLabel("最新版本：检查中...")
        self.latest_label.setObjectName("dialogHint")
        layout.addWidget(self.latest_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("updateProgress")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("准备中")
        layout.addWidget(self.progress)

        self.status_label = QLabel("正在检查 GitHub 连通性...")
        self.status_label.setObjectName("dialogHint")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("updateLog")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setMinimumHeight(180)
        layout.addWidget(self.log_view, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.hint_label = QLabel("更新过程中会校验更新包与当前程序完整性")
        self.hint_label.setObjectName("dialogHint")
        footer.addWidget(self.hint_label)
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("ghostButton")
        style_button(self.close_button, 34, 96)
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.setStyleSheet(
            about_qss(
                palette_for(self.theme), font_family, CHECK_ICON_PATH.as_posix()
            )
        )

        self.worker = UpdateWorker(current_version)
        self.worker.stage_changed.connect(self._on_stage)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.log_line.connect(self._append_log)
        self.worker.latest_version.connect(self._on_latest)
        self.worker.install_complete.connect(self._on_install_complete)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        QTimer.singleShot(0, self.worker.start)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_title_bar(self, self.theme)
        if self.parentWidget() is not None:
            center = self.parentWidget().window().frameGeometry().center()
            self.move(center - self.rect().center())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
        self.closed.emit()

    def _append_log(self, message: str, danger: bool = False) -> None:
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.log_view.document().characterCount() > 1:
            cursor.insertText("\n")
        fmt = QTextCharFormat()
        fmt.setForeground(
            QColor(palette_for(self.theme).danger_text if danger else palette_for(self.theme).dialog_body)
        )
        if danger:
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(str(message), fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def _on_stage(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_latest(self, version: str) -> None:
        self.latest_label.setText(f"最新版本：v{version}")

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(max(0, min(1000, int(received * 1000 / total))))
            self.progress.setFormat(f"{format_size(received)} / {format_size(total)}")
        else:
            self.progress.setRange(0, 0)
            self.progress.setFormat(f"{format_size(received)}")

    def _finish_busy(self) -> None:
        self._busy = False
        self.close_icon.setEnabled(True)
        self.close_button.setEnabled(True)

    def _on_failed(self, stage: str, reason: str) -> None:
        self._finish_busy()
        if self.latest_label.text() == "最新版本：检查中...":
            self.latest_label.setText("最新版本：未获取")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("更新失败")
        self.status_label.setText(f"更新失败：{stage}")
        message = f"更新失败 · {stage}：{reason}"
        self._append_log(message, danger=True)
        if self.log_callback is not None:
            self.log_callback(message, True)
        self.update_result.emit("更新失败", False)

    def _on_finished(self, message: str) -> None:
        self._finish_busy()
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.progress.setFormat("已是最新")
        self.status_label.setText(message)
        self._append_log(message, False)
        if self.log_callback is not None:
            self.log_callback(message, False)
        self.update_result.emit(message, True)

    def _on_install_complete(self, target: str) -> None:
        self._finish_busy()
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.progress.setFormat("更新器已启动")
        self.status_label.setText("程序即将退出，由外置更新器完成替换并重启")
        self._append_log("外置更新器已启动；程序退出后替换，完成后自动重启。", False)
        if self.log_callback is not None:
            self.log_callback("外置更新器已启动，准备退出主程序", False)
        self.update_result.emit("更新器已启动", True)
        self.close_button.setEnabled(False)
        self.close_icon.setEnabled(False)
        QTimer.singleShot(1200, QApplication.quit)

    def _restart(self, target: str) -> None:
        try:
            restart_application(Path(target))
        except UpdateError as exc:
            self._finish_busy()
            message = f"重启失败 · {exc.stage}：{exc.reason}"
            self.status_label.setText("更新完成，但重启失败")
            self._append_log(message, danger=True)
            if self.log_callback is not None:
                self.log_callback(message, True)
            return
        QApplication.quit()


class AboutDialog(QDialog):
    """产品信息、项目地址、版本公告与更新入口。"""

    def __init__(
        self,
        current_version: str,
        font_family: str,
        parent: QWidget | None = None,
        theme: str = "dark",
        log_callback=None,
    ):
        super().__init__(parent)
        self.theme = theme if theme in ("light", "dark") else "dark"
        self.font_family = font_family
        self.current_version = current_version
        self.log_callback = log_callback
        self.update_dialog: UpdateDialog | None = None
        self._drag_offset = None
        self.setWindowTitle("关于")
        self.setWindowIcon(load_app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(620, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("关于")
        title.setObjectName("announcementTitle")
        header.addWidget(title)
        header.addStretch(1)
        close_icon = QPushButton("✕")
        close_icon.setObjectName("ghostIconButton")
        close_icon.setFixedSize(30, 30)
        close_icon.setToolTip("关闭关于窗口")
        close_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        close_icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_icon.clicked.connect(self.close)
        header.addWidget(close_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        layout.addWidget(divider())

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = load_app_icon().pixmap(128, 128)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap)
            icon_label.setFixedSize(136, 136)
        layout.addWidget(icon_label)

        name_label = QLabel(APP_NAME)
        name_label.setObjectName("aboutName")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        version_label = QLabel(f"版本 v{current_version}")
        version_label.setObjectName("aboutVersion")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        description = QLabel(
            "Pawchive 帖子的 Windows 桌面批量下载器。\n"
            "支持创作者帖子加载、附件筛选、并发下载、多目录存储、\n"
            "预览图/原图处理、启动自检、日志导出与程序更新。"
        )
        description.setObjectName("aboutDescription")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        accent = palette_for(self.theme).accent_light
        project = QLabel(
            f'项目地址：<a style="color:{accent}; text-decoration:none;" '
            f'href="{PROJECT_URL}">{PROJECT_URL}</a>'
        )
        project.setObjectName("aboutLink")
        project.setTextFormat(Qt.TextFormat.RichText)
        project.setOpenExternalLinks(True)
        project.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(project, alignment=Qt.AlignmentFlag.AlignCenter)

        section = QLabel("版本公告")
        section.setObjectName("aboutSection")
        layout.addWidget(section)

        announcement = QPlainTextEdit()
        announcement.setObjectName("aboutAnnouncement")
        announcement.setReadOnly(True)
        announcement.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        announcement.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text = version_announcement_text(current_version) or "当前版本暂无更新公告。"
        announcement.setPlainText(text)
        announcement.setMaximumHeight(230)
        layout.addWidget(announcement)

        update_row = QHBoxLayout()
        update_row.setSpacing(12)
        self.update_state_label = QLabel("更新状态：尚未检查")
        self.update_state_label.setObjectName("dialogHint")
        update_row.addWidget(self.update_state_label)
        update_row.addStretch(1)
        self.update_button = QPushButton("检查更新")
        self.update_button.setObjectName("primaryButton")
        style_button(self.update_button, 34, 116)
        self.update_button.clicked.connect(self._start_update)
        update_row.addWidget(self.update_button)
        layout.addLayout(update_row)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        hint = QLabel("更新通过 GitHub Release 下载并校验完整性")
        hint.setObjectName("dialogHint")
        footer.addWidget(hint)
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("ghostButton")
        style_button(close_button, 34, 96)
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.setStyleSheet(
            about_qss(
                palette_for(self.theme), font_family, CHECK_ICON_PATH.as_posix()
            )
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_title_bar(self, self.theme)
        if self.parentWidget() is not None:
            center = self.parentWidget().window().frameGeometry().center()
            self.move(center - self.rect().center())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _start_update(self) -> None:
        self.update_button.setEnabled(False)
        self.update_state_label.setText("更新状态：检查中...")
        self.update_dialog = UpdateDialog(
            self.current_version,
            self.font_family,
            self,
            self.theme,
            self.log_callback,
        )
        self.update_dialog.update_result.connect(self._on_update_result)
        self.update_dialog.closed.connect(self._on_update_closed)
        self.update_dialog.show()

    def _on_update_result(self, message: str, success: bool) -> None:
        self.update_state_label.setText(f"更新状态：{message}")

    def _on_update_closed(self) -> None:
        self.update_button.setEnabled(True)


class SettingsDialog(QDialog):
    """下载并发、附件类型、代理设置。"""

    def __init__(
        self,
        settings: AppSettings,
        font_family: str,
        parent: QWidget | None = None,
        theme: str = "dark",
        output_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.result_settings = settings.normalized()
        self.theme = theme if theme in ("light", "dark") else "dark"
        self.font_family = font_family
        self.output_dir = program_root_for(output_dir)
        self.result_output_dir = str(self.output_dir) if self.output_dir else ""
        self._pending_program_root = self.output_dir
        self._extra_output_dirs = list(self.result_settings.extra_output_dirs)
        self._sync_guard = False
        self.setWindowTitle("设置")
        self.setWindowIcon(load_app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._drag_offset = None
        self.setModal(True)
        self.setMinimumWidth(620)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 900
        self.resize(760, min(760, max(560, available - 80)))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)

        body = QWidget()
        body.setObjectName("settingsBody")
        body.setAutoFillBackground(False)
        scroll.setWidget(body)

        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 10)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("下载设置")
        title.setObjectName("settingsTitle")
        subtitle = QLabel("界面、图片、并发、程序文件、附件与代理")
        subtitle.setObjectName("settingsSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)
        close_button = QPushButton("✕")
        close_button.setObjectName("settingsClose")
        close_button.setFixedSize(28, 28)
        close_button.setToolTip("关闭设置")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(self.reject)
        header.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        layout.addWidget(divider())

        appearance_form = QFormLayout()
        appearance_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        appearance_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        appearance_form.setHorizontalSpacing(16)
        appearance_form.setVerticalSpacing(10)
        self.theme_combo = QComboBox()
        for key in THEME_KEYS:
            self.theme_combo.addItem(THEME_LABELS[key], key)
        index = self.theme_combo.findData(self.result_settings.ui_theme)
        self.theme_combo.setCurrentIndex(index if index >= 0 else self.theme_combo.findData("dark"))
        self.theme_combo.setToolTip(
            "“跟随系统”读取 Windows 的浅色/深色应用模式；保存后立即生效，标题栏一起变。"
        )
        self.theme_combo.currentIndexChanged.connect(self._preview_theme)
        appearance_form.addRow("界面主题：", self.theme_combo)
        layout.addWidget(section_label("外观"))
        layout.addLayout(appearance_form)

        perf_form = QFormLayout()
        perf_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        perf_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        perf_form.setHorizontalSpacing(16)
        perf_form.setVerticalSpacing(10)

        self.file_spin = QSpinBox()
        self.file_spin.setRange(1, MAX_FILE_CONCURRENCY)
        self.file_spin.setValue(self.result_settings.file_concurrency)
        self.file_spin.setFixedWidth(132)
        self.file_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.file_spin.setToolTip(f"同时下载的文件数，1～{MAX_FILE_CONCURRENCY} 个")

        self.post_spin = QSpinBox()
        self.post_spin.setRange(1, MAX_POST_CONCURRENCY)
        self.post_spin.setValue(self.result_settings.post_concurrency)
        self.post_spin.setFixedWidth(132)
        self.post_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.post_spin.setToolTip(f"同时解析并下载的帖子数，1～{MAX_POST_CONCURRENCY} 个")

        self.retry_count_spin = QSpinBox()
        self.retry_count_spin.setRange(MIN_RETRY_COUNT, MAX_RETRY_COUNT)
        self.retry_count_spin.setValue(self.result_settings.retry_count)
        self.retry_count_spin.setFixedWidth(132)
        self.retry_count_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.retry_count_spin.setToolTip(
            "单个文件首次失败后的追加重试次数；总尝试次数 = 1 + 重试次数"
        )

        self.retry_interval_spin = QSpinBox()
        self.retry_interval_spin.setRange(MIN_RETRY_INTERVAL, MAX_RETRY_INTERVAL)
        self.retry_interval_spin.setValue(self.result_settings.retry_interval)
        self.retry_interval_spin.setSuffix(" 秒")
        self.retry_interval_spin.setFixedWidth(132)
        self.retry_interval_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.retry_interval_spin.setToolTip(
            f"每次自动重试前等待的固定时间，{MIN_RETRY_INTERVAL}～{MAX_RETRY_INTERVAL} 秒"
        )

        self.image_source_combo = QComboBox()
        for key in IMAGE_SOURCE_KEYS:
            self.image_source_combo.addItem(IMAGE_SOURCE_LABELS[key], key)
        index = self.image_source_combo.findData(self.result_settings.image_source)
        self.image_source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.image_source_combo.setFixedWidth(132)
        self.image_source_combo.setToolTip(
            "预览图走缩略图域名，文件更小、下载更快；原图走文件域名，清晰度更高但体积更大。"
        )

        self.proxy_type = QComboBox()
        for key in ("none", "http", "https", "socks5"):
            self.proxy_type.addItem(PROXY_TYPE_LABELS[key], key)
        index = self.proxy_type.findData(self.result_settings.proxy_type)
        self.proxy_type.setCurrentIndex(index if index >= 0 else 0)
        self.proxy_type.currentIndexChanged.connect(self._sync_proxy_state)

        self.proxy_edit = QLineEdit(self.result_settings.proxy_address)
        self.proxy_edit.setPlaceholderText("127.0.0.1:7890，或 user:pass@127.0.0.1:1080")

        self.attachment_boxes: dict[str, QCheckBox] = {}
        attachment_widget = QWidget()
        attachment_layout = QVBoxLayout(attachment_widget)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        attachment_layout.setSpacing(6)
        attachment_grid = QGridLayout()
        attachment_grid.setContentsMargins(0, 0, 0, 0)
        attachment_grid.setHorizontalSpacing(14)
        attachment_grid.setVerticalSpacing(4)
        for index, (key, label, exts) in enumerate(ATTACHMENT_CATEGORIES):
            box = QCheckBox(label)
            box.setMinimumHeight(22)
            box.setChecked(key in self.result_settings.attachment_types)
            if exts:
                box.setToolTip("包含扩展名：" + "  ".join(exts))
            else:
                box.setToolTip("勾选后允许下载所有未识别扩展名的附件")
            self.attachment_boxes[key] = box
            attachment_grid.addWidget(box, index // 2, index % 2)
            if key == "other":
                box.toggled.connect(self._sync_attachment_state)
        attachment_layout.addLayout(attachment_grid)

        image_note = QLabel("图片（jpg/png/gif/webp 等）始终下载，无需勾选。")
        image_note.setObjectName("settingsHint")
        image_note.setWordWrap(True)
        attachment_layout.addWidget(image_note)

        extra_text = ", ".join("." + ext for ext in self.result_settings.extra_extensions)
        self.extra_edit = QLineEdit(extra_text)
        self.extra_edit.setPlaceholderText("例如 psd, blend, bin（逗号分隔）")
        self.extra_edit.setToolTip(
            "默认不可填写：勾选“其他未知扩展名”可长期允许全部后缀，"
            "或勾选右侧“临时启用（仅本次任务）”后临时填写。"
        )

        self.temp_extra_box = QCheckBox("临时启用（仅本次任务）")
        self.temp_extra_box.setMinimumHeight(22)
        self.temp_extra_box.setChecked(self.result_settings.temp_custom_extensions)
        self.temp_extra_box.setToolTip(
            "未勾选“其他未知扩展名”时，用它可以只为下一次下载放行自定义后缀；任务结束自动取消。"
        )
        self.temp_extra_box.toggled.connect(self._sync_attachment_state)

        extra_widget = QWidget()
        extra_layout = QVBoxLayout(extra_widget)
        extra_layout.setContentsMargins(0, 0, 0, 0)
        extra_layout.setSpacing(6)
        extra_row = QHBoxLayout()
        extra_row.setContentsMargins(0, 0, 0, 0)
        extra_row.setSpacing(10)
        extra_tag = QLabel("自定义扩展名：")
        extra_tag.setObjectName("fieldLabel")
        extra_tag.setFixedWidth(96)
        extra_row.addWidget(extra_tag)
        extra_row.addWidget(self.extra_edit, stretch=1)
        extra_row.addWidget(self.temp_extra_box)
        extra_layout.addLayout(extra_row)
        extra_note = QLabel(
            "自定义扩展名默认关闭，无法填写。勾选“其他未知扩展名”可长期允许全部后缀；"
            "只想临时放行个别后缀时，勾选右侧“临时启用（仅本次任务）”，下载结束后会自动取消。"
        )
        extra_note.setObjectName("settingsHint")
        extra_note.setWordWrap(True)
        extra_layout.addWidget(extra_note)

        perf_form.addRow("下载并发数：", self.file_spin)
        perf_form.addRow("帖子并发数：", self.post_spin)
        perf_form.addRow("失败重试次数：", self.retry_count_spin)
        perf_form.addRow("重试间隔：", self.retry_interval_spin)
        perf_form.addRow("图片下载：", self.image_source_combo)
        layout.addWidget(section_label("性能"))
        layout.addLayout(perf_form)

        program_form = QFormLayout()
        program_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        program_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        program_form.setHorizontalSpacing(16)
        program_form.setVerticalSpacing(10)

        self.program_path_edit = QLineEdit()
        self.program_path_edit.setReadOnly(True)
        self.program_path_edit.setEnabled(False)
        self.program_path_edit.setPlaceholderText("先在主界面选择保存目录，程序会自动在下面创建 PawchiveDownload")
        self.program_path_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.temp_path_edit = QLineEdit()
        self.temp_path_edit.setReadOnly(True)
        self.temp_path_edit.setPlaceholderText("PawchiveDownload/Temp")
        self.temp_path_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.config_path_edit = QLineEdit()
        self.config_path_edit.setReadOnly(True)
        self.config_path_edit.setPlaceholderText("PawchiveDownload/Config/Settings.json")
        self.config_path_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.temp_migrate_button = QPushButton("迁移")
        self.temp_migrate_button.setObjectName("ghostButton")
        style_button(self.temp_migrate_button, 34, 84)
        self.temp_migrate_button.setToolTip(
            "选择新的位置，保存设置时把整个 PawchiveDownload（帖子、Config、Temp）一起迁移"
        )
        self.temp_migrate_button.setEnabled(self._pending_program_root is not None)
        self.temp_migrate_button.clicked.connect(self._choose_program_location)

        program_row = QHBoxLayout()
        program_row.setContentsMargins(0, 0, 0, 0)
        program_row.setSpacing(10)
        program_row.addWidget(self.program_path_edit, stretch=1)
        program_row.addWidget(self.temp_migrate_button)
        program_widget = QWidget()
        program_widget.setLayout(program_row)
        program_form.addRow("程序目录：", program_widget)
        program_form.addRow("临时文件：", self.temp_path_edit)
        program_form.addRow("配置文件：", self.config_path_edit)

        program_note = QLabel(
            "程序文件全部收进 PawchiveDownload：Temp 存 .part 与 State 下载记录，"
            "Config 存 Settings.json、Log 与 Failed_Downloads.txt；保存根只记录在注册表，不再写 AppData。"
            "创作者 URL 不保存。迁移会整体移动该目录。"
        )
        program_note.setObjectName("settingsHint")
        program_note.setWordWrap(True)
        layout.addWidget(section_label("程序文件设置"))
        layout.addLayout(program_form)
        layout.addWidget(program_note)

        storage_note = QLabel(
            "主保存目录含 Config/Temp/State/Log，只能整体迁移，不能手动改路径。"
            "额外保存目录只保存帖子内容，按添加顺序编号；可在主界面下拉菜单中切换。"
        )
        storage_note.setObjectName("settingsHint")
        storage_note.setWordWrap(True)
        self.extra_output_list = QListWidget()
        self.extra_output_list.setObjectName("extraStorageList")
        self.extra_output_list.setFixedHeight(92)
        self.extra_output_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.extra_output_list.setToolTip("额外保存目录不包含程序元数据，不能与主目录相同或互为父子目录")
        self.extra_add_button = QPushButton("添加目录")
        self.extra_add_button.setObjectName("ghostButton")
        style_button(self.extra_add_button, 32, 92)
        self.extra_add_button.clicked.connect(self._add_extra_output_dir)
        self.extra_remove_button = QPushButton("移除选中")
        self.extra_remove_button.setObjectName("ghostButton")
        style_button(self.extra_remove_button, 32, 92)
        self.extra_remove_button.clicked.connect(self._remove_extra_output_dir)
        extra_buttons = QHBoxLayout()
        extra_buttons.setContentsMargins(0, 0, 0, 0)
        extra_buttons.setSpacing(8)
        extra_buttons.addWidget(self.extra_add_button)
        extra_buttons.addWidget(self.extra_remove_button)
        extra_buttons.addStretch(1)
        storage_box = QVBoxLayout()
        storage_box.setContentsMargins(0, 0, 0, 0)
        storage_box.setSpacing(8)
        storage_box.addWidget(self.extra_output_list)
        storage_box.addLayout(extra_buttons)
        layout.addWidget(section_label("存储管理"))
        layout.addWidget(storage_note)
        layout.addLayout(storage_box)
        self._refresh_extra_output_list()
        self._refresh_program_paths()

        inner_card = QWidget()
        inner_card.setObjectName("innerCard")
        inner_box = QVBoxLayout(inner_card)
        inner_box.setContentsMargins(14, 12, 14, 12)
        inner_box.setSpacing(10)
        inner_box.addWidget(attachment_widget)
        inner_box.addWidget(extra_widget)
        layout.addWidget(section_label("附件"))
        layout.addWidget(inner_card)

        net_form = QFormLayout()
        net_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        net_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        net_form.setHorizontalSpacing(16)
        net_form.setVerticalSpacing(10)
        net_form.addRow("代理类型：", self.proxy_type)
        net_form.addRow("代理地址：", self.proxy_edit)
        layout.addWidget(section_label("网络"))
        layout.addLayout(net_form)

        hint = QLabel(
            "站点限制：同时下载不超过 5 个、请求频率约 1 次/秒；并发越高越容易被临时拒绝。\n"
            "未勾选任何类型、也没有临时扩展名时，只下载图片。代理填 host:port，可带账号：user:pass@host:port。"
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 10, 24, 14)
        buttons.setSpacing(10)
        buttons.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("ghostButton")
        style_button(cancel_button, 36, 96)
        save_button = QPushButton("保存")
        save_button.setObjectName("primaryButton")
        style_button(save_button, 36, 108)
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._on_save)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        footer = QWidget()
        footer.setObjectName("settingsFooter")
        footer.setLayout(buttons)
        outer.addWidget(footer)

        self._sync_proxy_state()
        self._sync_attachment_state()
        self._apply_style(self.font_family, self.theme)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _sync_attachment_state(self) -> None:
        if self._sync_guard:
            return
        self._sync_guard = True
        try:
            other = self.attachment_boxes["other"].isChecked()
            if other and self.temp_extra_box.isChecked():
                self.temp_extra_box.setChecked(False)
            self.temp_extra_box.setEnabled(not other)
            enabled = other or self.temp_extra_box.isChecked()
            self.extra_edit.setEnabled(enabled)
            if enabled:
                self.extra_edit.setPlaceholderText("例如 psd, blend, bin（逗号分隔）")
            else:
                self.extra_edit.setPlaceholderText("默认关闭，勾选右侧开关后可填")
        finally:
            self._sync_guard = False

    def _sync_proxy_state(self) -> None:
        proxy_type = self.proxy_type.currentData() or "none"
        enabled = proxy_type != "none"
        self.proxy_edit.setEnabled(enabled)
        if not enabled:
            self.proxy_edit.setPlaceholderText("不使用代理")
        elif proxy_type == "socks5":
            self.proxy_edit.setPlaceholderText("127.0.0.1:1080，或 user:pass@127.0.0.1:1080")
        else:
            self.proxy_edit.setPlaceholderText("127.0.0.1:7890，或 user:pass@127.0.0.1:7890")

    def _refresh_extra_output_list(self) -> None:
        if not hasattr(self, "extra_output_list"):
            return
        self.extra_output_list.clear()
        root = self._pending_program_root
        if root is not None:
            primary_item = QListWidgetItem(f"主目录（含程序文件，只可迁移）  {root}")
            primary_item.setFlags(Qt.ItemFlag.NoItemFlags)
            primary_item.setToolTip("主目录包含 Config/Temp/State/Log，不能在这里修改")
            self.extra_output_list.addItem(primary_item)
        for index, path in enumerate(self._extra_output_dirs, start=1):
            item = QListWidgetItem(f"{index}. {path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.extra_output_list.addItem(item)
        if not self._extra_output_dirs:
            if root is None:
                self.extra_output_list.addItem("先选择主保存目录，才能添加额外目录")
            else:
                self.extra_output_list.addItem("尚未添加额外保存目录")
        has_root = root is not None
        self.extra_add_button.setEnabled(has_root)
        self.extra_remove_button.setEnabled(has_root and bool(self._extra_output_dirs))

    def _add_extra_output_dir(self) -> None:
        root = self._pending_program_root
        if root is None:
            MessageDialog.show_message(
                self, "还没有主保存目录",
                "请先在主界面选择主保存目录，再添加额外保存目录。",
                kind="info", theme=self.theme,
            )
            return
        path = QFileDialog.getExistingDirectory(
            self, "选择额外保存目录（不包含程序文件）",
            str(root.parent),
        )
        if not path:
            return
        target = normalize_storage_dir(path)
        if not target:
            MessageDialog.show_message(
                self, "目录无效", "请选择本机上的绝对路径。",
                kind="warning", theme=self.theme,
            )
            return
        if storage_paths_overlap(target, root):
            MessageDialog.show_message(
                self, "目录不能与主目录重叠",
                "额外保存目录不能与主保存目录相同，也不能是它的上级或子目录。\n"
                "请换一个独立目录。",
                kind="warning", theme=self.theme,
            )
            return
        merged = normalize_extra_output_dirs(
            (*self._extra_output_dirs, target),
            root,
        )
        if len(merged) == len(self._extra_output_dirs):
            MessageDialog.show_message(
                self, "目录已存在", "这个目录已经在列表里了。",
                kind="info", theme=self.theme,
            )
            return
        self._extra_output_dirs = list(merged)
        self._refresh_extra_output_list()

    def _remove_extra_output_dir(self) -> None:
        item = self.extra_output_list.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if not path:
            return
        self._extra_output_dirs = [
            value for value in self._extra_output_dirs if value != str(path)
        ]
        self._refresh_extra_output_list()

    def _refresh_program_paths(self) -> None:
        root = self._pending_program_root
        if root is None:
            for field in (self.program_path_edit, self.temp_path_edit, self.config_path_edit):
                field.clear()
            self.program_path_edit.setToolTip("先在主界面选择保存目录")
            self.temp_path_edit.setToolTip("临时文件固定在 PawchiveDownload/Temp")
            self.config_path_edit.setToolTip("设置固定在 PawchiveDownload/Config/Settings.json")
            self.temp_migrate_button.setEnabled(False)
            self._refresh_extra_output_list()
            return

        temp_path = root / "Temp"
        config_path = program_config_path(root)
        self.program_path_edit.setText(str(root))
        self.program_path_edit.setCursorPosition(len(str(root)))
        self.program_path_edit.setToolTip(str(root))
        self.temp_path_edit.setText(str(temp_path))
        self.temp_path_edit.setCursorPosition(len(str(temp_path)))
        self.temp_path_edit.setToolTip(str(temp_path))
        self.config_path_edit.setText(str(config_path))
        self.config_path_edit.setCursorPosition(len(str(config_path)))
        self.config_path_edit.setToolTip(str(config_path))
        self.temp_migrate_button.setEnabled(True)
        self._refresh_extra_output_list()

    def _choose_program_location(self) -> None:
        if self._pending_program_root is None:
            MessageDialog.show_message(
                self, "还没有保存目录", "请先在主界面选择保存目录。",
                kind="info", theme=self.theme
            )
            return
        initial = str(self._pending_program_root.parent)
        path = QFileDialog.getExistingDirectory(
            self, "选择新的位置（将整体迁移 PawchiveDownload）", initial
        )
        if not path:
            return
        target = program_root_for(Path(path))
        if target is None:
            MessageDialog.show_message(
                self, "位置无效", "请选择本机上的绝对路径。",
                kind="warning", theme=self.theme
            )
            return
        if target == self._pending_program_root:
            return
        if target.exists() and not target.is_dir():
            MessageDialog.show_message(
                self,
                "目标位置已有同名文件",
                f"目标位置已存在同名文件：\n{target}\n\n请换一个位置，或先处理该文件。",
                kind="warning",
                theme=self.theme,
            )
            return
        if target.is_dir():
            try:
                has_content = any(target.iterdir())
            except OSError:
                has_content = True
            if has_content:
                MessageDialog.show_message(
                    self,
                    "目标目录已有内容",
                    f"目标位置已有非空的 PawchiveDownload：\n{target}\n\n"
                    "为避免覆盖已有下载与配置，请换一个位置，或先手动清空目标目录。",
                    kind="warning",
                    theme=self.theme,
                )
                return
        self._pending_program_root = target
        self._extra_output_dirs = list(
            normalize_extra_output_dirs(self._extra_output_dirs, target)
        )
        self._refresh_program_paths()

    def _on_save(self) -> None:
        proxy_type = str(self.proxy_type.currentData() or "none")
        proxy_address = self.proxy_edit.text().strip()
        if proxy_type != "none":
            try:
                proxy_address = normalize_proxy_address(proxy_address)
            except ValueError as exc:
                MessageDialog.show_message(self, "代理地址无效", str(exc), kind="warning", theme=self.theme)
                return

        attachment_types = tuple(
            key for key in ATTACHMENT_CATEGORY_KEYS if self.attachment_boxes[key].isChecked()
        )
        other_checked = "other" in attachment_types
        temp_enabled = self.temp_extra_box.isChecked() and not other_checked
        if other_checked or temp_enabled:
            extra_extensions = normalize_extra_extensions(self.extra_edit.text())
        else:
            extra_extensions = ()

        old_root = self.output_dir
        new_root = self._pending_program_root
        if old_root and new_root and old_root != new_root:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            try:
                moved, errors = migrate_program_root(old_root, new_root)
            except ValueError as exc:
                QApplication.restoreOverrideCursor()
                MessageDialog.show_message(
                    self, "程序目录不能迁移", str(exc), kind="warning", theme=self.theme
                )
                return
            except Exception as exc:
                QApplication.restoreOverrideCursor()
                MessageDialog.show_message(
                    self, "程序目录迁移失败", str(exc), kind="error", theme=self.theme
                )
                return
            QApplication.restoreOverrideCursor()
            if errors:
                MessageDialog.show_message(
                    self,
                    "程序目录未完整迁移",
                    f"已处理 {moved} 个文件；以下步骤失败：\n" + "\n".join(errors[:8]),
                    kind="error",
                    theme=self.theme,
                )
                return

        if new_root is not None:
            try:
                ensure_program_layout(new_root)
                ensure_state_dir(new_root)
            except OSError as exc:
                MessageDialog.show_message(
                    self, "程序目录不可用", f"无法创建 Config/Temp：\n{new_root}\n\n{exc}",
                    kind="error", theme=self.theme
                )
                return
        self.result_output_dir = str(new_root) if new_root else ""
        extra_outputs = normalize_extra_output_dirs(self._extra_output_dirs, self.result_output_dir)
        old_selected = self.result_settings.selected_output_dir
        available = ([self.result_output_dir] if self.result_output_dir else []) + list(extra_outputs)
        if old_selected == str(old_root or ""):
            selected_output = self.result_output_dir
        elif old_selected in available:
            selected_output = old_selected
        else:
            selected_output = self.result_output_dir

        self.result_settings = AppSettings(
            file_concurrency=self.file_spin.value(),
            post_concurrency=self.post_spin.value(),
            retry_count=self.retry_count_spin.value(),
            retry_interval=self.retry_interval_spin.value(),
            preview_mode=self.result_settings.preview_mode,
            image_source=str(self.image_source_combo.currentData() or "preview"),
            proxy_type=proxy_type,
            proxy_address=proxy_address,
            attachment_types=attachment_types,
            extra_extensions=extra_extensions,
            temp_custom_extensions=temp_enabled,
            announcement_dismissed_version=self.result_settings.announcement_dismissed_version,
            last_self_check_version=self.result_settings.last_self_check_version,
            output_dir=self.result_output_dir,
            extra_output_dirs=extra_outputs,
            selected_output_dir=selected_output,
            temp_dir="",
            ui_theme=str(self.theme_combo.currentData() or "dark"),
        ).normalized()
        self.accept()

    def _preview_theme(self, *_args) -> None:
        choice = str(self.theme_combo.currentData() or "dark")
        effective = choice if choice in ("light", "dark") else resolve_theme(choice)
        if effective == self.theme:
            return
        self.theme = effective
        self._apply_style(self.font_family, self.theme)

    def _apply_style(self, font_family: str, theme: str = "dark") -> None:
        palette = palette_for(theme)
        if theme == "light":
            arrow_down = ARROW_DOWN_LIGHT_PATH.as_posix()
            arrow_up = ARROW_UP_LIGHT_PATH.as_posix()
        else:
            arrow_down = ARROW_DOWN_PATH.as_posix()
            arrow_up = ARROW_UP_PATH.as_posix()
        self.setStyleSheet(
            settings_qss(
                palette,
                font_family,
                CHECK_ICON_PATH.as_posix(),
                arrow_down,
                arrow_up,
            )
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_title_bar(self, self.theme)


class PostLoader(QObject):
    posts_loaded = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url: str, settings: AppSettings | None = None):
        super().__init__()
        self.url = url
        self.settings = (settings or AppSettings()).normalized()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            posts = asyncio.run(self._main())
            self.posts_loaded.emit(posts)
        except Exception as exc:
            self.error.emit(str(exc))

    async def _main(self) -> list[PostItem]:
        self.log.emit("正在读取帖子列表...")
        service = DownloadService(PawchiveApi(settings=self.settings, image_source=self.settings.image_source))
        posts = await service.list_posts(self.url)
        self.log.emit(f"读取完成，共 {len(posts)} 条帖子")
        return posts


class PreviewAuditWorker(QObject):
    """在后台探测保存目录里能否用原图替换预览图。"""

    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(
        self,
        root: Path,
        state_dir: Path | None,
        settings: AppSettings,
    ):
        super().__init__()
        self.root = root
        self.state_dir = state_dir
        self.settings = settings

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = asyncio.run(
                audit_preview_images(self.root, self.state_dir, self.settings)
            )
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class PreviewReplaceWorker(QObject):
    """下载原图并原位替换预览图。"""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(int, list)
    error = pyqtSignal(str)

    def __init__(
        self,
        candidates: list[PreviewCandidate],
        temp_root: Path,
        settings: AppSettings,
    ):
        super().__init__()
        self.candidates = candidates
        self.temp_root = temp_root
        self.settings = settings

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            count, errors = asyncio.run(
                replace_preview_images(
                    self.candidates,
                    self.temp_root,
                    self.settings,
                    progress=lambda done, total: self.progress.emit(done, total),
                )
            )
            self.done.emit(count, errors)
        except Exception as exc:
            self.error.emit(str(exc))


class DownloadWorker(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)
    failed_file = pyqtSignal(str)
    failed_item = pyqtSignal(object)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        creator_url: str,
        posts: list[PostItem],
        output_dir: Path,
        settings: AppSettings,
        program_root: Path | None = None,
    ):
        super().__init__()
        self.creator_url = creator_url
        self.posts = posts
        self.output_dir = output_dir
        self.program_root = program_root or output_dir
        self.settings = settings.normalized()
        self.concurrency = self.settings.file_concurrency
        self.post_concurrency = self.settings.post_concurrency
        self.naming_mode = DateNamingMode.PREFIX
        self.failed_export_path = failed_export_path(self.program_root)
        self.state_key = ""
        self.state_root: Path | None = None
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.pause_event.set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error.emit(str(exc))

    async def _main(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.program_root.mkdir(parents=True, exist_ok=True)
        self.failed_export_path.parent.mkdir(parents=True, exist_ok=True)
        if self.failed_export_path.exists():
            self.failed_export_path.unlink()

        service = DownloadService(
            PawchiveApi(settings=self.settings, image_source=self.settings.image_source),
            temp_root=resolve_temp_root(self.settings, self.program_root),
        )
        state_root = ensure_state_dir(self.program_root)
        state_key = f"pawchive_{normalize_creator_path(self.creator_url)}"
        if path_key(self.output_dir) != path_key(self.program_root):
            digest = hashlib.sha1(
                path_key(self.output_dir).encode("utf-8", "ignore")
            ).hexdigest()[:10]
            state_key = f"{state_key}_{digest}"
        self.state_key = state_key
        self.state_root = state_root
        state = StateStore(
            state_key,
            root=state_root,
        )
        engine = DownloadEngine(
            concurrency=self.concurrency,
            retries=self.settings.retry_count,
            retry_interval=self.settings.retry_interval,
            pause_event=self.pause_event,
            stop_event=self.stop_event,
            settings=self.settings,
        )
        total_posts = len(self.posts)
        post_index = {post.id: index + 1 for index, post in enumerate(self.posts)}
        self.log.emit(f"当前设置: {describe_settings(self.settings)}")
        if self.settings.temp_custom_extensions and "other" not in self.settings.attachment_types:
            exts = ", ".join("." + ext for ext in self.settings.effective_extra_extensions)
            self.log.emit(f"注意：本次临时启用了自定义扩展名 {exts}，任务结束后自动取消")

        async def task_batches():
            async for batch in service.iter_post_task_batches(
                self.posts,
                self.output_dir,
                self.naming_mode,
                post_concurrency=self.post_concurrency,
            ):
                if batch:
                    post = batch[0].post
                    index = post_index.get(post.id, 0)
                    self.log.emit(f"已整理 {index}/{total_posts}: {post.title}")
                yield batch

        def on_event(event: DownloadEvent) -> None:
            if event.type == "started":
                self.progress.emit(0, event.total or 0, 0)
                self.log.emit(f"开始流式处理 {len(self.posts)} 条帖子")
            elif event.type == "tasks_added":
                self.log.emit(event.message)
            elif event.type == "progress":
                self.progress.emit(event.done or 0, event.total or 0, event.failed or 0)
            elif event.type == "file_finished":
                self.log.emit(f"完成: {event.file_name}")
            elif event.type == "file_retry":
                self.log.emit(event.message)
            elif event.type == "file_skipped":
                self.log.emit(f"跳过: {event.file_name}")
            elif event.type == "file_failed":
                text = self._format_failed_event(event)
                self._append_failed_export(text)
                self.failed_file.emit(f"{event.file_name} | {event.path}")
                failed = self._failed_download(event)
                if failed is not None:
                    self.failed_item.emit(failed)
                self.log.emit(f"失败: {event.file_name} - {event.message}")
            elif event.type == "post_finished":
                if event.failed:
                    self.log.emit(f"帖子处理完成，有失败文件: {event.post_id}")
            elif event.type in {"finished", "cancelled"}:
                self.finished.emit(event.message)

        await engine.run_stream(task_batches(), state, on_event)

    def _failed_download(self, event: DownloadEvent) -> FailedDownload | None:
        data = event.data or {}
        post = data.get("post")
        file_item = data.get("file")
        if not isinstance(post, PostItem) or not isinstance(file_item, FileItem):
            return None
        target = Path(str(data.get("target_path") or event.path or ""))
        temp = Path(str(data.get("temp_path") or ""))
        if not target or not temp:
            return None
        return FailedDownload(
            post=post,
            file=file_item,
            save_path=target,
            temp_path=temp,
            error=event.message or "下载失败",
            state_key=self.state_key,
            state_root=self.state_root,
        )

    def _format_failed_event(self, event: DownloadEvent) -> str:
        data = event.data or {}
        target_path = data.get("target_path") or str(event.path or "")
        target_dir = data.get("target_dir") or str(Path(target_path).parent if target_path else "")
        return "\n".join(
            [
                f"Post: {event.post_id or ''}",
                f"File: {event.file_name or ''}",
                f"URL: {data.get('url') or ''}",
                f"Target: {target_path}",
                f"Folder: {target_dir}",
                f"Error: {event.message}",
                "",
            ]
        )

    def _append_failed_export(self, text: str) -> None:
        first_write = not self.failed_export_path.exists()
        with self.failed_export_path.open("a", encoding="utf-8") as file:
            if first_write:
                file.write("# Pawchive Downloader failed files\n\n")
            file.write(text)


class RetryWorker(QObject):
    """手动重试失败文件；只处理传入的任务，不重新整理帖子。"""

    log = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)
    recovered = pyqtSignal(object)
    still_failed = pyqtSignal(object)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        items: list[FailedDownload],
        output_dir: Path,
        settings: AppSettings,
    ):
        super().__init__()
        self.items = list(items)
        self.output_dir = output_dir
        self.settings = settings.normalized()
        self.stop_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")

    async def _main(self) -> None:
        if not self.items:
            self.finished.emit("没有可重试的失败文件")
            return
        by_key = {item.key: item for item in self.items}
        first = self.items[0]
        state = None
        if first.state_key and first.state_root is not None:
            state = StateStore(first.state_key, root=first.state_root)
        tasks = [item.as_task() for item in self.items]
        engine = DownloadEngine(
            concurrency=self.settings.file_concurrency,
            retries=self.settings.retry_count,
            retry_interval=self.settings.retry_interval,
            stop_event=self.stop_event,
            settings=self.settings,
        )

        def lookup(event: DownloadEvent) -> FailedDownload | None:
            return by_key.get((event.post_id or "", event.file_name or "", str(event.path or "")))

        def on_event(event: DownloadEvent) -> None:
            if event.type == "progress":
                self.progress.emit(event.done or 0, event.total or 0, event.failed or 0)
            elif event.type == "file_retry":
                self.log.emit(event.message)
            elif event.type == "file_finished":
                item = lookup(event)
                if item is not None:
                    self.recovered.emit(item)
            elif event.type == "file_skipped":
                item = lookup(event)
                if item is not None:
                    self.recovered.emit(item)
            elif event.type == "file_failed":
                item = lookup(event)
                if item is not None:
                    item.error = event.message or item.error
                    self.still_failed.emit(item)
                self.log.emit(f"重试失败: {event.file_name} - {event.message}")

        self.log.emit(f"开始重试 {len(tasks)} 个失败文件（每次重试间隔 {self.settings.retry_interval} 秒）")
        await engine.run(tasks, state, on_event)
        self.finished.emit(f"重试结束，处理 {len(tasks)} 个文件")


class TitleBarButton(QPushButton):
    """标题栏窗口按钮：统一线性图标，不再用字符 — / □ / ✕。"""

    def __init__(self, kind: str, theme: str, tooltip: str, close: bool = False):
        super().__init__()
        self.kind = kind
        self.theme = theme if theme in ("light", "dark") else "dark"
        self._hover = False
        self.setObjectName("titleBarClose" if close else "titleBarButton")
        self.setFixedSize(46, 32)
        self.setIconSize(QSize(16, 16))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFlat(True)
        self._sync_icon()

    def _icon(self) -> QIcon:
        if self.kind == "close":
            table = TITLE_ICON_CLOSE_HOVER if self._hover else TITLE_ICON_CLOSE
        elif self.kind == "restore":
            table = TITLE_ICON_RESTORE
        elif self.kind == "max":
            table = TITLE_ICON_MAX
        else:
            table = TITLE_ICON_MIN
        return QIcon(table[self.theme].as_posix())

    def _sync_icon(self) -> None:
        self.setIcon(self._icon())

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self._sync_icon()

    def set_theme(self, theme: str) -> None:
        self.theme = theme if theme in ("light", "dark") else "dark"
        self._sync_icon()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._sync_icon()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._sync_icon()
        super().leaveEvent(event)


class TitleBar(QWidget):
    """自绘标题栏：Win10 无法可靠修改原生标题栏配色，改为自绘以跟随主题。"""

    def __init__(self, window: QMainWindow, title: str, theme: str = "dark"):
        super().__init__(window)
        self._window = window
        self._theme = theme if theme in ("light", "dark") else "dark"
        self._drag_offset = None
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(8)

        icon_label = QLabel()
        icon_label.setFixedSize(18, 18)
        icon_label.setPixmap(load_app_icon().pixmap(QSize(18, 18)))
        layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        title_label = QLabel(title)
        title_label.setObjectName("titleBarTitle")
        layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self.min_button = TitleBarButton("min", self._theme, "最小化")
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button = TitleBarButton("max", self._theme, "最大化 / 还原")
        self.max_button.clicked.connect(self._toggle_maximize)
        self.close_button = TitleBarButton("close", self._theme, "关闭", close=True)
        self.close_button.clicked.connect(window.close)
        for button in (self.min_button, self.max_button, self.close_button):
            layout.addWidget(button)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme if theme in ("light", "dark") else "dark"
        for button in (self.min_button, self.max_button, self.close_button):
            button.set_theme(self._theme)

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
            self.max_button.set_kind("max")
        else:
            self._window.showMaximized()
            self.max_button.set_kind("restore")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, font_family: str = DEFAULT_FONT_FAMILY):
        super().__init__()
        self.font_family = font_family
        self.posts: list[PostItem] = []
        self.loader: PostLoader | None = None
        self.worker: DownloadWorker | None = None
        self.retry_worker: RetryWorker | None = None
        self._failed_items: dict[tuple[str, str, str], FailedDownload] = {}
        self._selected_post_ids: set[str] = set()
        self._save_scan: SaveScan | None = None
        self._running = False
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._load_visible_thumbnails)
        self.preview_audit_worker: PreviewAuditWorker | None = None
        self.preview_replace_worker: PreviewReplaceWorker | None = None
        self._preview_busy = False
        self.announcement_dialog: AnnouncementDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.about_dialog: AboutDialog | None = None
        self.app_settings = load_settings()
        self.ui_theme = resolve_theme(self.app_settings.ui_theme)
        app_instance = QApplication.instance()
        if app_instance is not None:
            scheme_signal = getattr(app_instance.styleHints(), "colorSchemeChanged", None)
            if scheme_signal is not None:
                scheme_signal.connect(self._follow_system_theme)
        self.background_ratio = load_background_aspect_ratio()
        self.enforces_ratio = BACKGROUND_PATH.exists()
        self._ratio_resize_guard = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(load_app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        if self.enforces_ratio:
            self.setMinimumSize(self._ratio_size_for_width(900))
            self.resize(self._ratio_size_for_width(1120))
        else:
            self.setMinimumSize(QSize(960, 680))
            self.resize(QSize(1240, 800))

        shell = QWidget()
        shell.setObjectName("windowShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.title_bar = TitleBar(self, APP_NAME, self.ui_theme)
        shell_layout.addWidget(self.title_bar)

        root = QWidget()
        root.setObjectName("root")
        shell_layout.addWidget(root, 1)
        self.setCentralWidget(shell)
        self._shell = shell
        self.size_grip = QSizeGrip(shell)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.raise_()

        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("titleLabel")
        subtitle = QLabel(f"pawchive.pw · 帖子批量下载 · v{APP_VERSION}")
        subtitle.setObjectName("subtitleLabel")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch(1)
        self.settings_label = QLabel(describe_settings_brief(self.app_settings))
        self.settings_label.setObjectName("settingsSummary")
        self.settings_label.setToolTip(
            describe_settings(self.app_settings) + "\n点击右侧“设置”修改图片、并发、附件类型与代理"
        )
        header.addWidget(self.settings_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("ghostButton")
        style_button(self.settings_button, 34, 88)
        self.settings_button.setToolTip("下载图片、并发、附件类型与代理设置")
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.about_button = QPushButton("关于")
        self.about_button.setObjectName("ghostButton")
        style_button(self.about_button, 34, 88)
        self.about_button.setToolTip("程序说明、项目地址、版本公告与检查更新")
        self.about_button.clicked.connect(self._open_about)
        header.addWidget(self.about_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        layout.addWidget(self._build_top_panel())
        layout.addWidget(self._build_body(), stretch=1)

        self._apply_style()
        self._temp_timer = QTimer(self)
        self._temp_timer.setInterval(1000)
        self._temp_timer.timeout.connect(self._on_temp_timer)
        self._set_running(False)
        self._restore_output_dir()
        self._refresh_temp_view()
        self._log("就绪：填写创作者 URL 后点击“加载帖子”")
        if self._program_root() is None:
            self._log(
                "未设置保存目录：请先点击“设置主目录”，设置后才能释放完整程序文件并开始下载。",
                danger=True,
            )

    def _program_root(self) -> Path | None:
        return program_root_for(self.app_settings.output_dir)

    def _output_choices(self) -> list[Path]:
        primary = self._program_root()
        if primary is None:
            return []
        extras = [
            Path(value)
            for value in normalize_extra_output_dirs(
                self.app_settings.extra_output_dirs, primary
            )
        ]
        return [primary, *extras]

    def _save_root(self) -> Path | None:
        primary = self._program_root()
        if primary is None:
            return None
        selected = normalize_storage_dir(self.app_settings.selected_output_dir)
        if selected and any(
            path_key(selected) == path_key(choice) for choice in self._output_choices()
        ):
            return Path(selected)
        return primary

    def _set_selected_output(self, path: Path) -> None:
        self.app_settings.selected_output_dir = str(path)
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"保存目录选择保存失败: {exc}")

    def _rebuild_save_path_combo(self, preferred: Path | None = None) -> None:
        if not hasattr(self, "path_combo"):
            return
        self._combo_guard = True
        self.path_combo.blockSignals(True)
        self.path_combo.clear()
        primary = self._program_root()
        danger = QBrush(QColor(palette_for(self.ui_theme).danger_text))
        if primary is None:
            self.path_combo.addItem(
                "请先设置保存目录（含程序文件）", None
            )
            self.path_combo.setItemData(
                0, danger, Qt.ItemDataRole.ForegroundRole
            )
            self.path_combo.setItemData(
                0, "未设置主保存目录，完整程序文件尚未释放", Qt.ItemDataRole.ToolTipRole
            )
            warning_font = QFont(self.font())
            warning_font.setBold(True)
            self.path_combo.setItemData(
                0, warning_font, Qt.ItemDataRole.FontRole
            )
            self.browse_button.setText("设置主目录")
            self.browse_button.setEnabled(True)
            self.browse_button.setToolTip("首次设置主保存目录")
            self.open_dir_button.setEnabled(False)
            self.path_combo.blockSignals(False)
            self._combo_guard = False
            return

        choices = self._output_choices()
        self.path_combo.addItem(f"1. {choices[0]}（主目录 · 含程序文件）", str(choices[0]))
        for index, path in enumerate(choices[1:], start=2):
            self.path_combo.addItem(f"{index}. {path}", str(path))
        self.path_combo.addItem("＋ 新增保存目录（不含程序文件）", "__add__")

        selected = preferred or self._save_root() or primary
        selected_key = path_key(selected)
        target_index = 0
        for index in range(self.path_combo.count()):
            data = self.path_combo.itemData(index)
            if data and str(data) != "__add__" and path_key(str(data)) == selected_key:
                target_index = index
                break
        self.path_combo.setCurrentIndex(target_index)
        self.path_combo.blockSignals(False)
        self._combo_guard = False
        self.browse_button.setText("只可迁移")
        self.browse_button.setToolTip("主目录含程序文件，修改请到设置 → 程序文件设置 → 迁移")
        self.open_dir_button.setEnabled(selected is not None)

    def _restore_output_dir(self) -> None:
        """启动时恢复主保存目录，并补齐 Config/Temp。"""
        root = self._program_root()
        self._rebuild_save_path_combo()
        if root is None:
            return
        try:
            ensure_program_layout(root)
        except OSError as exc:
            self._log(f"恢复保存目录失败: {root}（{exc}）", danger=True)
            return
        self.open_dir_button.setEnabled(True)
        try:
            ensure_state_dir(root)
        except OSError as exc:
            self._log(f"State 目录创建失败: {root}（{exc}）", danger=True)
        active = self._save_root() or root
        self._log(f"已恢复主保存目录: {root}")
        if active != root:
            self._log(f"当前下载目录: {active}")

    def _remember_output_dir(self, root: Path) -> None:
        """记录主保存根，写入注册表与 PawchiveDownload/Config/Settings.json。"""
        self.app_settings.output_dir = str(root)
        self.app_settings.selected_output_dir = str(root)
        try:
            ensure_program_layout(root)
            ensure_state_dir(root)
        except OSError as exc:
            self._log(f"程序目录创建失败: {root}（{exc}）", danger=True)
            return
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"设置保存失败: {exc}")

    def _maybe_self_check(self) -> None:
        if not needs_self_check(
            self.app_settings.last_self_check_version, APP_VERSION
        ):
            return
        self._run_self_check(startup=True)

    def _run_self_check(self, startup: bool = False) -> None:
        root = self._program_root()
        extras = tuple(self.app_settings.extra_output_dirs) if root else ()
        result = run_self_check(root, extras, ASSET_DIR)
        self._log(
            f"环境自检（v{APP_VERSION}）：{'通过' if result.passed else '发现问题'}",
            danger=not result.passed,
        )
        for item in result.items:
            if not item.ok:
                self._log(f"自检失败 · {item.title}：{item.detail}", danger=True)

        self.app_settings.last_self_check_version = APP_VERSION
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"自检版本记录保存失败: {exc}", danger=True)

        if result.passed:
            if not startup:
                MessageDialog.show_message(
                    self,
                    "自检通过",
                    "运行环境、读写权限、程序资源与保存目录均正常。",
                    kind="success",
                    theme=self.ui_theme,
                )
            return
        lines = result.summary_lines()
        body = "\n".join(lines[:10])
        if len(lines) > 10:
            body += f"\n……其余 {len(lines) - 10} 项见日志"
        if root is None:
            body = (
                "首次使用尚未设置保存目录。\n"
                "请先点击“设置主目录”，程序会在所选目录下创建 PawchiveDownload，"
                "再释放完整程序文件并开始下载。\n\n" + body
            )
        MessageDialog.show_message(
            self, "环境自检发现问题", body, kind="warning", theme=self.ui_theme
        )

    def _build_top_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("card")
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 12, 18, 14)
        box.setSpacing(10)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://pawchive.pw/fanbox/user/xxxx")
        self.path_combo = QComboBox()
        self.path_combo.setFixedHeight(36)
        self.path_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.path_combo.setMinimumContentsLength(24)
        self.path_combo.setToolTip("主目录含程序文件，只能迁移；额外目录只保存帖子内容")
        self.path_combo.currentIndexChanged.connect(self._on_save_path_selected)
        self.url_input.setFixedHeight(36)
        self._combo_guard = False

        self.load_button = QPushButton("加载帖子")
        self.load_button.setObjectName("primaryButton")
        style_button(self.load_button, 36, 116)
        self.browse_button = QPushButton("设置主目录")
        self.browse_button.setObjectName("ghostButton")
        style_button(self.browse_button, 36, 100)
        self.browse_button.setToolTip("首次设置主保存目录；设置后主目录只能迁移")
        self.browse_button.clicked.connect(self._browse)
        self.open_dir_button = QPushButton("打开目录")
        self.open_dir_button.setObjectName("ghostButton")
        style_button(self.open_dir_button, 36, 96)
        self.open_dir_button.setToolTip("用文件资源管理器打开当前保存目录")
        self.open_dir_button.clicked.connect(self._open_save_dir)
        self.open_dir_button.setEnabled(False)
        self.load_button.clicked.connect(self._load_posts)

        url_tag = QLabel("创作者 URL")
        url_tag.setObjectName("fieldLabel")
        url_tag.setFixedWidth(80)
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        url_row.addWidget(url_tag)
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.load_button)

        path_tag = QLabel("保存目录")
        path_tag.setObjectName("fieldLabel")
        path_tag.setFixedWidth(80)
        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        path_row.addWidget(path_tag)
        path_row.addWidget(self.path_combo, stretch=1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.open_dir_button)

        box.addLayout(url_row)
        box.addLayout(path_row)
        return panel

    def _open_about(self) -> None:
        if self.about_dialog is not None and self.about_dialog.isVisible():
            self.about_dialog.raise_()
            self.about_dialog.activateWindow()
            return
        self.about_dialog = AboutDialog(
            APP_VERSION,
            self.font_family,
            self,
            self.ui_theme,
            self._log,
        )
        self.about_dialog.show()

    def _open_settings(self) -> None:
        output_root = self._program_root()
        dialog = SettingsDialog(
            self.app_settings,
            self.font_family,
            self,
            theme=self.ui_theme,
            output_dir=output_root,
        )
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.app_settings = dialog.result_settings
            new_root = program_root_for(dialog.result_output_dir)
            if new_root is not None:
                try:
                    ensure_state_dir(new_root)
                except OSError as exc:
                    self._log(f"State 目录创建失败: {new_root}（{exc}）", danger=True)
            try:
                save_settings(self.app_settings)
            except Exception as exc:
                self._log(f"设置保存失败: {exc}", danger=True)
            self._rebuild_save_path_combo(self._save_root())
            self._refresh_temp_view()
            if self.content_tabs.currentWidget() is self.save_page:
                self._refresh_save_view()
            new_theme = resolve_theme(self.app_settings.ui_theme)
            if new_theme != self.ui_theme:
                self.ui_theme = new_theme
                self._apply_style()
                apply_title_bar(self, self.ui_theme)
                self._rebuild_save_path_combo(self._save_root())
                self._log(f"界面主题已切换为{'浅色' if new_theme == 'light' else '深色'}")
            self._refresh_settings_label()
            if new_root is not None:
                self._log(f"设置已更新，主保存目录: {new_root}")
            else:
                self._log("设置已更新")

    def _refresh_settings_label(self) -> None:
        self.settings_label.setText(describe_settings_brief(self.app_settings))
        self.settings_label.setToolTip(
            describe_settings(self.app_settings) + "\n点击右侧“设置”修改图片、并发、附件类型、主题与代理"
        )

    def _end_temp_extensions(self) -> None:
        if not self.app_settings.temp_custom_extensions:
            return
        self.app_settings = self.app_settings.without_temp_extensions()
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"设置保存失败: {exc}")
        self._refresh_settings_label()
        self._log("临时自定义扩展名已取消（单次任务结束）")

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        control = QWidget()
        control.setObjectName("card")
        control_box = QVBoxLayout(control)
        control_box.setContentsMargins(18, 14, 18, 16)
        control_box.setSpacing(12)

        status_row = QHBoxLayout()
        status_row.setSpacing(14)
        self.summary_label = QLabel("等待加载帖子")
        self.summary_label.setObjectName("summaryLabel")
        status_row.addWidget(self.summary_label, stretch=1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        status_row.addWidget(self.progress, stretch=2)
        control_box.addLayout(status_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.start_button = QPushButton("开始下载")
        self.start_button.setObjectName("primaryButton")
        style_button(self.start_button, 36, 148)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setObjectName("ghostButton")
        style_button(self.pause_button, 36, 88)
        self.resume_button = QPushButton("继续")
        self.resume_button.setObjectName("ghostButton")
        style_button(self.resume_button, 36, 88)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        style_button(self.stop_button, 36, 88)
        self.start_button.clicked.connect(self._start_download)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.start_button)
        buttons.addSpacing(6)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.resume_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        site_hint = QLabel("站点限制：同时下载 ≤5 · 约 1 请求/秒")
        site_hint.setObjectName("hintLabel")
        buttons.addWidget(site_hint)
        control_box.addLayout(buttons)
        body_layout.addWidget(control)

        list_card = QWidget()
        list_card.setObjectName("card")
        list_box = QVBoxLayout(list_card)
        list_box.setContentsMargins(12, 12, 12, 14)
        list_box.setSpacing(8)

        self.content_tabs = QTabWidget()
        self.content_tabs.setObjectName("tabs")
        self.content_tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.content_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)

        post_page = QWidget()
        post_layout = QVBoxLayout(post_page)
        post_layout.setContentsMargins(0, 8, 0, 0)
        post_layout.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.post_count_label = QLabel("尚未加载")
        self.post_count_label.setObjectName("countLabel")
        title_row.addWidget(self.post_count_label)
        title_row.addWidget(QLabel("预览："))
        self.post_preview_combo = QComboBox()
        self.post_preview_combo.setFixedWidth(108)
        for key in PREVIEW_MODE_KEYS:
            self.post_preview_combo.addItem(PREVIEW_MODE_LABELS[key], key)
        current_mode = self.app_settings.preview_mode if self.app_settings.preview_mode in PREVIEW_MODE_KEYS else "text"
        self.post_preview_combo.setCurrentIndex(max(0, PREVIEW_MODE_KEYS.index(current_mode)))
        self.post_preview_combo.currentIndexChanged.connect(self._on_preview_mode_changed)
        title_row.addWidget(self.post_preview_combo)
        title_row.addStretch(1)
        self.select_all_button = QPushButton("全选")
        self.select_all_button.setObjectName("ghostButton")
        style_button(self.select_all_button, 28, 64)
        self.invert_button = QPushButton("反选")
        self.invert_button.setObjectName("ghostButton")
        style_button(self.invert_button, 28, 64)
        self.select_all_button.clicked.connect(self._select_all)
        self.invert_button.clicked.connect(self._invert_selection)
        title_row.addWidget(self.select_all_button)
        title_row.addWidget(self.invert_button)
        post_layout.addLayout(title_row)

        self.post_list = QListWidget()
        self.post_list.setObjectName("postList")
        self.post_list.setAlternatingRowColors(False)
        self.post_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.post_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.post_list.setMinimumHeight(150)
        self.post_list.itemSelectionChanged.connect(self._sync_selection_from_list)

        self.post_stack = QStackedWidget()
        self.post_stack.addWidget(self.post_list)
        self.post_scroll = QScrollArea()
        self.post_scroll.setObjectName("previewScroll")
        self.post_scroll.setWidgetResizable(True)
        self.post_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.post_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.post_preview_panel = MasonryPanel(
            current_mode,
            selectable=True,
            settings=self.app_settings,
            parent=self.post_scroll,
        )
        self.post_preview_panel.selection_changed.connect(self._on_card_selection)
        self.post_scroll.setWidget(self.post_preview_panel)
        self.post_scroll.verticalScrollBar().valueChanged.connect(self._queue_visible_thumbnails)
        self.post_stack.addWidget(self.post_scroll)
        post_layout.addWidget(self.post_stack, stretch=1)
        self._apply_post_preview_mode(current_mode, rebuild=True)
        self.content_tabs.addTab(post_page, "帖子列表")

        self.save_page = QWidget()
        self.content_tabs.addTab(self.save_page, "保存目录")
        self.content_tabs.currentChanged.connect(self._on_content_tab_changed)
        list_box.addWidget(self.content_tabs, stretch=1)

        log_card = QWidget()
        log_card.setObjectName("card")
        log_box = QVBoxLayout(log_card)
        log_box.setContentsMargins(12, 12, 12, 14)
        log_box.setSpacing(8)
        tabs = QTabWidget()
        tabs.setObjectName("tabs")
        tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_page = QWidget()
        log_layout = QVBoxLayout(self.log_page)
        log_layout.setContentsMargins(0, 8, 0, 0)
        log_layout.setSpacing(8)
        log_actions = QHBoxLayout()
        log_actions.setContentsMargins(0, 0, 0, 0)
        log_actions.setSpacing(8)
        log_actions.addStretch(1)
        self.log_export_button = QPushButton("导出日志")
        self.log_export_button.setObjectName("ghostButton")
        style_button(self.log_export_button, 30, 88)
        self.log_export_button.setToolTip(
            "默认保存到 PawchiveDownload/Config/Log，文件名按日期，例如 20261123.log"
        )
        self.log_export_button.clicked.connect(self._export_log)
        log_actions.addWidget(self.log_export_button)
        log_layout.addLayout(log_actions)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view, stretch=1)
        self.failed_page = QWidget()
        failed_layout = QVBoxLayout(self.failed_page)
        failed_layout.setContentsMargins(0, 8, 0, 0)
        failed_layout.setSpacing(8)
        failed_actions = QHBoxLayout()
        failed_actions.setContentsMargins(0, 0, 0, 0)
        failed_actions.setSpacing(8)
        self.failed_hint = QLabel("下载失败的文件可多选后重试；清除会删除失败内容、.part 缓存和对应状态记录。")
        self.failed_hint.setObjectName("settingsHint")
        self.failed_hint.setWordWrap(True)
        failed_actions.addWidget(self.failed_hint, stretch=1)
        self.retry_failed_button = QPushButton("重试选中")
        self.retry_failed_button.setObjectName("ghostButton")
        style_button(self.retry_failed_button, 30, 92)
        self.retry_failed_button.clicked.connect(self._retry_failed_items)
        self.clear_failed_button = QPushButton("清除选中")
        self.clear_failed_button.setObjectName("dangerButton")
        style_button(self.clear_failed_button, 30, 92)
        self.clear_failed_button.clicked.connect(self._clear_failed_items)
        failed_actions.addWidget(self.retry_failed_button)
        failed_actions.addWidget(self.clear_failed_button)
        failed_layout.addLayout(failed_actions)
        self.failed_list = QListWidget()
        self.failed_list.setObjectName("failedList")
        self.failed_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.failed_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        failed_layout.addWidget(self.failed_list, stretch=1)
        self._update_failed_buttons()
        self.temp_page = QWidget()
        temp_layout = QVBoxLayout(self.temp_page)
        temp_layout.setContentsMargins(0, 8, 0, 0)
        temp_layout.setSpacing(8)
        temp_path_row = QHBoxLayout()
        temp_path_row.setContentsMargins(0, 0, 0, 0)
        temp_path_row.setSpacing(8)
        self.temp_path_view = QLineEdit()
        self.temp_path_view.setReadOnly(True)
        self.temp_path_view.setPlaceholderText("先选择保存目录，临时文件会自动使用 PawchiveDownload/Temp")
        self.temp_path_view.setToolTip("下载中的 .part 与 State 下载记录所在位置（PawchiveDownload/Temp）")
        self.temp_open_button = QPushButton("打开目录")
        self.temp_open_button.setObjectName("ghostButton")
        style_button(self.temp_open_button, 30, 84)
        self.temp_open_button.clicked.connect(self._open_temp_dir)
        self.temp_clear_button = QPushButton("清理")
        self.temp_clear_button.setObjectName("dangerButton")
        style_button(self.temp_clear_button, 30, 64)
        self.temp_clear_button.setToolTip("删除当前临时根目录下的 .part 文件")
        self.temp_clear_button.clicked.connect(self._clear_temp_files)
        self.temp_refresh_button = QPushButton("刷新")
        self.temp_refresh_button.setObjectName("ghostButton")
        style_button(self.temp_refresh_button, 30, 64)
        self.temp_refresh_button.clicked.connect(self._refresh_temp_view)
        temp_path_row.addWidget(self.temp_path_view, stretch=1)
        temp_layout.addLayout(temp_path_row)
        temp_actions = QHBoxLayout()
        temp_actions.setContentsMargins(0, 0, 0, 0)
        temp_actions.setSpacing(8)
        temp_actions.addStretch(1)
        temp_actions.addWidget(self.temp_clear_button)
        temp_actions.addWidget(self.temp_refresh_button)
        temp_actions.addWidget(self.temp_open_button)
        temp_layout.addLayout(temp_actions)
        self.temp_list = QListWidget()
        self.temp_list.setObjectName("tempList")
        self.temp_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        temp_layout.addWidget(self.temp_list, stretch=1)
        temp_hint = QLabel(
            "临时文件固定放在 PawchiveDownload/Temp：.part 用于断点续传，"
            "State 保存下载记录；清理只删除 .part，不会删除 State 记录。"
        )
        temp_hint.setObjectName("settingsHint")
        temp_hint.setWordWrap(True)
        temp_layout.addWidget(temp_hint)

        self.save_page.setObjectName("savePage")
        save_layout = QVBoxLayout(self.save_page)
        save_layout.setContentsMargins(0, 8, 0, 0)
        save_layout.setSpacing(8)
        self.save_path_view = QLineEdit()
        self.save_path_view.setReadOnly(True)
        self.save_path_view.setPlaceholderText("先选择保存目录")
        self.save_path_view.setToolTip("已下载内容所在目录")
        save_layout.addWidget(self.save_path_view)
        self.save_summary_label = QLabel("先选择保存目录")
        self.save_summary_label.setObjectName("hintLabel")
        self.save_summary_label.setWordWrap(True)
        save_layout.addWidget(self.save_summary_label)
        self.save_tree = QTreeWidget()
        self.save_tree.setObjectName("saveTree")
        self.save_tree.setColumnCount(3)
        self.save_tree.setHeaderLabels(("名称", "大小", "状态"))
        self.save_tree.setRootIsDecorated(True)
        self.save_tree.setIndentation(16)
        self.save_tree.setUniformRowHeights(True)
        self.save_tree.setAnimated(False)
        self.save_tree.setExpandsOnDoubleClick(True)
        self.save_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.save_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.save_stack = QStackedWidget()
        self.save_stack.addWidget(self.save_tree)
        self.save_scroll = QScrollArea()
        self.save_scroll.setObjectName("previewScroll")
        self.save_scroll.setWidgetResizable(True)
        self.save_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.save_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.save_preview_panel = MasonryPanel(
            current_mode,
            selectable=False,
            settings=self.app_settings,
            parent=self.save_scroll,
        )
        self.save_scroll.setWidget(self.save_preview_panel)
        self.save_scroll.verticalScrollBar().valueChanged.connect(self._queue_visible_save_thumbnails)
        self.save_stack.addWidget(self.save_scroll)
        save_layout.addWidget(self.save_stack, stretch=1)
        save_actions = QHBoxLayout()
        save_actions.setContentsMargins(0, 0, 0, 0)
        save_actions.setSpacing(8)
        save_actions.addWidget(QLabel("预览："))
        self.save_preview_combo = QComboBox()
        self.save_preview_combo.setFixedWidth(108)
        for key in PREVIEW_MODE_KEYS:
            self.save_preview_combo.addItem(PREVIEW_MODE_LABELS[key], key)
        self.save_preview_combo.setCurrentIndex(max(0, PREVIEW_MODE_KEYS.index(current_mode)))
        self.save_preview_combo.currentIndexChanged.connect(self._on_preview_mode_changed)
        save_actions.addWidget(self.save_preview_combo)
        save_actions.addStretch(1)
        self.save_refresh_button = QPushButton("刷新预览")
        self.save_refresh_button.setObjectName("ghostButton")
        style_button(self.save_refresh_button, 30, 92)
        self.save_refresh_button.clicked.connect(self._refresh_save_view)
        self.save_verify_button = QPushButton("校验完整性")
        self.save_verify_button.setObjectName("ghostButton")
        style_button(self.save_verify_button, 30, 104)
        self.save_verify_button.clicked.connect(self._verify_save_view)
        save_actions.addWidget(self.save_refresh_button)
        save_actions.addWidget(self.save_verify_button)
        save_layout.addLayout(save_actions)
        save_hint = QLabel(
            "只预览带 .post_id 的帖子目录；仅文字模式保留树形结构，缩略图/全部展示模式改为卡片预览。"
            "校验会对照下载记录检查缺失/大小并解码图片。"
        )
        save_hint.setObjectName("settingsHint")
        save_hint.setWordWrap(True)
        save_layout.addWidget(save_hint)

        self.tabs = tabs
        tabs.addTab(self.log_page, "日志")
        tabs.addTab(self.failed_page, "失败文件")
        tabs.addTab(self.temp_page, "临时目录")
        tabs.currentChanged.connect(self._on_tab_changed)
        log_box.addWidget(tabs, stretch=1)

        main_row = QHBoxLayout()
        main_row.setSpacing(14)
        main_row.addWidget(list_card, stretch=3)
        main_row.addWidget(log_card, stretch=2)
        body_layout.addLayout(main_row, stretch=1)
        return body

    def _on_save_path_selected(self, index: int) -> None:
        if getattr(self, "_combo_guard", False):
            return
        data = self.path_combo.itemData(index) if index >= 0 else None
        if data == "__add__":
            current = self._save_root() or self._program_root()
            QTimer.singleShot(0, lambda: self._add_extra_path(current))
            return
        if not data:
            return
        path = Path(str(data))
        if not path.exists():
            dialog = MessageDialog(
                self,
                "目录不存在",
                f"{path}\n\n该保存目录当前不存在，是否创建？",
                kind="warning",
                confirm=True,
                theme=self.ui_theme,
            )
            dialog.exec()
            if not dialog.confirmed:
                self._rebuild_save_path_combo(self._save_root())
                return
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                MessageDialog.show_message(
                    self, "目录不可用", f"{path}\n\n{exc}",
                    kind="error", theme=self.ui_theme
                )
                self._rebuild_save_path_combo(self._save_root())
                return
        self._set_selected_output(path)
        self.open_dir_button.setEnabled(True)
        self._refresh_temp_view()
        if self.content_tabs.currentWidget() is self.save_page:
            self._refresh_save_view()
        self._log(f"当前下载目录: {path}")

    def _add_extra_path(self, previous: Path | None) -> None:
        primary = self._program_root()
        self._rebuild_save_path_combo(previous or primary)
        if primary is None:
            MessageDialog.show_message(
                self, "还没有主保存目录",
                "请先设置主保存目录（含程序文件），再添加额外保存目录。",
                kind="info", theme=self.ui_theme,
            )
            return
        path = QFileDialog.getExistingDirectory(
            self, "新增保存目录（不含程序文件）", str(primary.parent)
        )
        if not path:
            return
        target = normalize_storage_dir(path)
        if not target:
            MessageDialog.show_message(
                self, "目录无效", "请选择本机上的绝对路径。",
                kind="warning", theme=self.ui_theme,
            )
            return
        if storage_paths_overlap(target, primary):
            MessageDialog.show_message(
                self, "目录不能与主目录重叠",
                "额外保存目录不能与主保存目录相同，也不能是它的上级或子目录。\n"
                "请换一个独立目录。",
                kind="warning", theme=self.ui_theme,
            )
            return
        merged = normalize_extra_output_dirs(
            (*self.app_settings.extra_output_dirs, target), primary
        )
        if len(merged) == len(self.app_settings.extra_output_dirs):
            MessageDialog.show_message(
                self, "目录已存在", "这个目录已经在保存目录列表里了。",
                kind="info", theme=self.ui_theme,
            )
            return
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            MessageDialog.show_message(
                self, "目录不可用", f"{target}\n\n{exc}",
                kind="error", theme=self.ui_theme,
            )
            return
        self.app_settings.extra_output_dirs = merged
        self.app_settings.selected_output_dir = target
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"额外保存目录保存失败: {exc}", danger=True)
        self._rebuild_save_path_combo(Path(target))
        self._refresh_temp_view()
        if self.content_tabs.currentWidget() is self.save_page:
            self._refresh_save_view()
        self._log(f"已新增额外保存目录: {target}")

    def _browse(self) -> None:
        primary = self._program_root()
        if primary is not None:
            MessageDialog.show_message(
                self,
                "主保存目录只可迁移",
                "保存目录含有重要的程序文件与已下载内容，如需完整迁移请在设置界面的"
                "“程序文件设置”中使用迁移功能。\n\n"
                "如需增加只放帖子的目录，请使用保存目录下拉菜单最底部的"
                "“新增保存目录（不含程序文件）”。",
                kind="warning",
                theme=self.ui_theme,
            )
            return
        path = QFileDialog.getExistingDirectory(self, "选择主保存目录")
        if not path:
            return
        root = program_root_for(Path(path))
        if root is None:
            MessageDialog.show_message(
                self, "保存目录无效", "请选择本机上的绝对路径。",
                kind="warning", theme=self.ui_theme
            )
            return
        self._remember_output_dir(root)
        self._rebuild_save_path_combo(root)
        self._refresh_temp_view()
        self._refresh_save_view()
        self._log(f"主保存目录已设置: {root}")
        self._run_self_check(startup=False)

    def _load_posts(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            MessageDialog.show_message(self, "还没有创作者 URL", "请先填写创作者 URL，再加载帖子。", kind="info", theme=self.ui_theme)
            return

        self.post_list.clear()
        self.post_preview_panel.clear()
        self.posts = []
        self._selected_post_ids.clear()
        self.load_button.setEnabled(False)
        self.post_count_label.setText("加载中…")
        self.summary_label.setText("正在加载帖子...")
        self._log("开始加载帖子列表")
        self.loader = PostLoader(url, self.app_settings)
        self.loader.posts_loaded.connect(self._on_posts_loaded)
        self.loader.error.connect(self._on_loader_error)
        self.loader.log.connect(self._log)
        self.loader.start()

    def _on_posts_loaded(self, posts: list[PostItem]) -> None:
        self.posts = posts
        self._selected_post_ids = {post.id for post in posts}
        self.post_list.blockSignals(True)
        self.post_list.clear()
        for post in posts:
            item = QListWidgetItem(f"{post.day}  {post.title}")
            item.setData(Qt.ItemDataRole.UserRole, post.id)
            self.post_list.addItem(item)
            item.setSelected(True)
        self.post_list.blockSignals(False)
        self._apply_post_preview_mode(self._current_preview_mode(), rebuild=True)
        self.summary_label.setText(f"已加载 {len(posts)} 条帖子，默认全选")
        self.post_count_label.setText(f"{len(posts)} 条")
        self.load_button.setEnabled(True)

    def _on_loader_error(self, message: str) -> None:
        self.load_button.setEnabled(True)
        self.post_count_label.setText("加载失败")
        self.summary_label.setText("加载失败")
        self._log(f"加载失败: {message}")
        MessageDialog.show_message(self, "加载失败", message, kind="error", theme=self.ui_theme)

    def _current_preview_mode(self) -> str:
        value = str(self.post_preview_combo.currentData() or "text")
        return value if value in PREVIEW_MODE_KEYS else "text"

    def _populate_post_cards(self, mode: str) -> None:
        self.post_preview_panel.mode = mode
        self.post_preview_panel.clear()
        for post in self.posts:
            card = PreviewCard(
                post.id,
                card_title(post),
                format_post_time(post.published),
                mode,
                selectable=True,
                checked=post.id in self._selected_post_ids,
                thumbnail_url=post_thumbnail_url(post),
            )
            self.post_preview_panel.add_card(card)
        self.post_preview_panel.set_checked_keys(self._selected_post_ids)
        self.post_preview_panel.relayout()
        self._queue_visible_thumbnails()

    def _apply_post_preview_mode(self, mode: str, *, rebuild: bool = False) -> None:
        mode = mode if mode in PREVIEW_MODE_KEYS else "text"
        self.post_preview_panel.mode = mode
        self.post_stack.setCurrentIndex(0 if mode == "text" else 1)
        if mode != "text":
            if rebuild or len(self.post_preview_panel.cards) != len(self.posts):
                self._populate_post_cards(mode)
            else:
                for card in self.post_preview_panel.cards:
                    card.mode = mode
                self.post_preview_panel.relayout()
            QTimer.singleShot(0, self._load_visible_thumbnails)

    def _on_preview_mode_changed(self, index: int) -> None:
        mode = str(self.post_preview_combo.itemData(index) or "text")
        if mode not in PREVIEW_MODE_KEYS:
            mode = "text"
        self.app_settings.preview_mode = mode
        try:
            save_settings(self.app_settings)
        except Exception:
            pass
        for combo in (self.post_preview_combo, self.save_preview_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, PREVIEW_MODE_KEYS.index(mode)))
            combo.blockSignals(False)
        self._apply_post_preview_mode(mode, rebuild=True)
        self._apply_save_preview_mode(mode, rebuild=True)
        self._log(f"预览方式已切换：{PREVIEW_MODE_LABELS[mode]}")

    def _sync_selection_from_list(self) -> None:
        self._selected_post_ids = {
            str(self.post_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.post_list.count())
            if self.post_list.item(index).isSelected()
        }
        self.post_preview_panel.set_checked_keys(self._selected_post_ids)

    def _on_card_selection(self, post_id: str, checked: bool) -> None:
        if checked:
            self._selected_post_ids.add(post_id)
        else:
            self._selected_post_ids.discard(post_id)
        self.post_list.blockSignals(True)
        for index in range(self.post_list.count()):
            item = self.post_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole)) == post_id:
                item.setSelected(checked)
                break
        self.post_list.blockSignals(False)

    def _start_download(self) -> None:
        selected = self._selected_posts()
        root = self._save_root()
        program_root = self._program_root()
        url = self.url_input.text().strip()
        if not selected:
            MessageDialog.show_message(self, "还没有选择帖子", "请至少勾选一条帖子再开始下载。", kind="info", theme=self.ui_theme)
            return
        if root is None:
            MessageDialog.show_message(self, "还没有保存目录", "请先设置主保存目录，再开始下载。", kind="info", theme=self.ui_theme)
            return
        if program_root is None:
            MessageDialog.show_message(self, "程序目录不可用", "主保存目录丢失，请重新设置。", kind="error", theme=self.ui_theme)
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            ensure_program_layout(program_root)
            ensure_state_dir(program_root)
        except OSError as exc:
            MessageDialog.show_message(
                self, "保存目录不可用", f"无法创建保存目录或程序文件：\n{root}\n\n{exc}",
                kind="error", theme=self.ui_theme
            )
            return
        if str(root) != str(self._save_root()):
            self._rebuild_save_path_combo(root)
        self.failed_list.clear()
        self._failed_items.clear()
        self._update_failed_buttons()
        self.progress.setValue(0)
        self.progress.setMaximum(0)
        self.summary_label.setText("正在准备下载任务...")
        self._set_running(True)

        self.worker = DownloadWorker(
            creator_url=url,
            posts=selected,
            output_dir=root,
            program_root=program_root,
            settings=self.app_settings,
        )
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.failed_item.connect(self._on_failed_item)
        self.worker.finished.connect(self._on_download_finished)
        self.worker.error.connect(self._on_download_error)
        self.worker.start()

    def _selected_posts(self) -> list[PostItem]:
        return [post for post in self.posts if post.id in self._selected_post_ids]

    def _select_all(self) -> None:
        self._selected_post_ids = {post.id for post in self.posts}
        self._apply_selection_to_views()

    def _invert_selection(self) -> None:
        self._selected_post_ids = {post.id for post in self.posts if post.id not in self._selected_post_ids}
        self._apply_selection_to_views()

    def _apply_selection_to_views(self) -> None:
        self.post_list.blockSignals(True)
        for index in range(self.post_list.count()):
            item = self.post_list.item(index)
            item.setSelected(str(item.data(Qt.ItemDataRole.UserRole)) in self._selected_post_ids)
        self.post_list.blockSignals(False)
        self.post_preview_panel.set_checked_keys(self._selected_post_ids)

    def _queue_visible_thumbnails(self) -> None:
        if self.post_stack.currentIndex() == 1:
            self._thumb_timer.start()

    def _load_visible_thumbnails(self) -> None:
        if self.post_stack.currentIndex() != 1:
            return
        scroll = self.post_scroll.verticalScrollBar().value()
        self.post_preview_panel.request_visible(scroll, self.post_scroll.viewport().height())

    def _pause(self) -> None:
        if self.worker:
            self.worker.pause()
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self._log("已暂停")

    def _resume(self) -> None:
        if self.worker:
            self.worker.resume()
            self.pause_button.setEnabled(True)
            self.resume_button.setEnabled(False)
            self._log("已继续")

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self._log("正在停止...")

    def _on_progress(self, done: int, total: int, failed: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.summary_label.setText(f"文件进度 {done}/{total}，失败 {failed}")

    def _on_failed_file(self, text: str) -> None:
        self._log(f"失败文件: {text}")

    def _on_failed_item(self, item: FailedDownload) -> None:
        self._failed_items[item.key] = item
        self._add_failed_list_item(item)
        self._update_failed_buttons()
        self.failed_hint.setText(
            f"已记录 {len(self._failed_items)} 个失败文件；可多选后重试，清除会删除失败内容、.part 缓存和状态记录。"
        )

    def _add_failed_list_item(self, failed: FailedDownload) -> None:
        row = QListWidgetItem(
            f"{failed.post.title}  ·  {failed.file.name}\n"
            f"{failed.error}  ·  {failed.save_path.parent}"
        )
        row.setData(Qt.ItemDataRole.UserRole, failed.key)
        row.setToolTip(str(failed.save_path))
        self.failed_list.addItem(row)

    def _remove_failed_item(self, failed: FailedDownload) -> None:
        self._failed_items.pop(failed.key, None)
        for index in range(self.failed_list.count() - 1, -1, -1):
            row = self.failed_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == failed.key:
                self.failed_list.takeItem(index)
                break
        self._update_failed_buttons()
        self.failed_hint.setText(
            f"已记录 {len(self._failed_items)} 个失败文件；可多选后重试，清除会删除失败内容、.part 缓存和状态记录。"
        )

    def _selected_failed_items(self) -> list[FailedDownload]:
        result: list[FailedDownload] = []
        for row in self.failed_list.selectedItems():
            key = row.data(Qt.ItemDataRole.UserRole)
            item = self._failed_items.get(tuple(key)) if isinstance(key, (tuple, list)) else None
            if item is not None:
                result.append(item)
        return result

    def _update_failed_buttons(self) -> None:
        count = len(self._failed_items)
        enabled = count > 0 and not getattr(self, "_running", False)
        if hasattr(self, "retry_failed_button"):
            self.retry_failed_button.setEnabled(enabled)
        if hasattr(self, "clear_failed_button"):
            self.clear_failed_button.setEnabled(enabled)

    def _retry_failed_items(self) -> None:
        if getattr(self, "_running", False):
            return
        items = self._selected_failed_items()
        if not items:
            MessageDialog.show_message(
                self,
                "还没有选择失败文件",
                "请在失败文件列表中多选要重试的条目。",
                kind="info",
                theme=self.ui_theme,
            )
            return
        root = self._program_root()
        if root is None:
            MessageDialog.show_message(self, "还没有保存目录", "无法确定重试目标目录。", kind="warning", theme=self.ui_theme)
            return
        self._log(f"手动重试 {len(items)} 个失败文件")
        self.progress.setMaximum(0)
        self.summary_label.setText("正在重试失败文件...")
        self._set_running(True)
        self.pause_button.setEnabled(False)
        self.retry_worker = RetryWorker(items, root, self.app_settings)
        self.retry_worker.log.connect(self._log)
        self.retry_worker.progress.connect(self._on_progress)
        self.retry_worker.recovered.connect(self._on_retry_recovered)
        self.retry_worker.still_failed.connect(self._on_retry_still_failed)
        self.retry_worker.finished.connect(self._on_retry_finished)
        self.retry_worker.error.connect(self._on_retry_error)
        self.retry_worker.start()

    def _on_retry_recovered(self, failed: FailedDownload) -> None:
        self._remove_failed_item(failed)
        self._log(f"重试成功: {failed.file.name}")

    def _on_retry_still_failed(self, failed: FailedDownload) -> None:
        self._failed_items[failed.key] = failed
        for index in range(self.failed_list.count()):
            row = self.failed_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == failed.key:
                row.setText(
                    f"{failed.post.title}  ·  {failed.file.name}\n"
                    f"{failed.error}  ·  {failed.save_path.parent}"
                )
                row.setToolTip(str(failed.save_path))
                break

    def _on_retry_finished(self, message: str) -> None:
        self._log(message)
        self.summary_label.setText(message)
        self._set_running(False)
        self._update_failed_buttons()
        if self.content_tabs.currentWidget() is self.save_page:
            self._refresh_save_view()

    def _on_retry_error(self, message: str) -> None:
        self._log(f"重试出错: {message}")
        self.summary_label.setText("重试出错")
        self._set_running(False)
        self._update_failed_buttons()
        MessageDialog.show_message(self, "重试出错", message, kind="error", theme=self.ui_theme)

    def _clear_failed_items(self) -> None:
        if getattr(self, "_running", False):
            return
        items = self._selected_failed_items()
        if not items:
            MessageDialog.show_message(
                self,
                "还没有选择失败文件",
                "请先在失败文件列表中多选要清除的条目。",
                kind="info",
                theme=self.ui_theme,
            )
            return
        dialog = MessageDialog(
            self,
            "确认清除失败文件",
            f"确定删除选中的 {len(items)} 个失败文件吗？\n\n"
            "将删除未完成的本地内容、对应的 .part 临时缓存，以及 State 里的下载状态记录。"
            "已成功下载的文件不会被删除。",
            kind="warning",
            confirm=True,
            theme=self.ui_theme,
        )
        dialog.exec()
        if not dialog.confirmed:
            return
        removed = 0
        for failed in items:
            try:
                if failed.state_key and failed.state_root is not None:
                    StateStore(failed.state_key, root=failed.state_root).discard_file(failed.as_task())
            except OSError as exc:
                self._log(f"删除状态记录失败: {failed.file.name} - {exc}", danger=True)
            for path in (failed.save_path, failed.temp_path):
                try:
                    if path.is_file():
                        path.unlink()
                except OSError as exc:
                    self._log(f"删除失败缓存失败: {path} - {exc}", danger=True)
            self._remove_failed_item(failed)
            removed += 1
        self._rewrite_failed_export()
        self._log(f"已清除 {removed} 个失败文件及其缓存/状态记录")

    def _rewrite_failed_export(self) -> None:
        root = self._program_root()
        if root is None:
            return
        path = failed_export_path(root)
        if not self._failed_items:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                f"{item.file.name} | {item.post.title} | {item.save_path} | {item.error}"
                for item in sorted(self._failed_items.values(), key=lambda value: str(value.save_path))
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self._log(f"失败记录更新失败: {exc}", danger=True)

    def _on_download_finished(self, message: str) -> None:
        self._log(message)
        if self.failed_list.count():
            root = self._program_root()
            if root is not None:
                failed_path = failed_export_path(root)
                self._log(f"失败记录已导出: {failed_path}")
        self.summary_label.setText(message)
        self._set_running(False)
        self._end_temp_extensions()
        self._refresh_temp_view()
        if self.content_tabs.currentWidget() is self.save_page:
            self._refresh_save_view()

    def _on_download_error(self, message: str) -> None:
        self._log(f"下载出错: {message}")
        self.summary_label.setText("下载出错")
        self._set_running(False)
        self._end_temp_extensions()
        MessageDialog.show_message(self, "下载出错", message, kind="error", theme=self.ui_theme)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setEnabled(not running)
        self.load_button.setEnabled(not running)
        self.settings_button.setEnabled(not running)
        if hasattr(self, "about_button"):
            self.about_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(running)
        if hasattr(self, "temp_clear_button") and running:
            self.temp_clear_button.setEnabled(False)
        if hasattr(self, "_temp_timer"):
            if running:
                self._temp_timer.start()
            else:
                self._temp_timer.stop()
        if hasattr(self, "save_verify_button"):
            self.save_verify_button.setEnabled(
                not running
                and not getattr(self, "_preview_busy", False)
                and getattr(self, "_save_has_files", False)
            )
        if hasattr(self, "path_combo"):
            self.path_combo.setEnabled(not running)
        if hasattr(self, "browse_button"):
            self.browse_button.setEnabled(not running)
        if hasattr(self, "open_dir_button"):
            self.open_dir_button.setEnabled(not running and self._save_root() is not None)
        if hasattr(self, "_update_failed_buttons"):
            self._update_failed_buttons()

    def _temp_root(self) -> Path | None:
        return resolve_temp_root(self.app_settings, self._program_root())

    def _refresh_temp_path(self) -> None:
        root = self._temp_root()
        text = str(root) if root else ""
        self.temp_path_view.setText(text)
        self.temp_path_view.setCursorPosition(0)
        self.temp_path_view.setToolTip(text or "下载中的 .part 与 State 下载记录所在位置")
        self.temp_open_button.setEnabled(root is not None)

    def _refresh_temp_view(self) -> None:
        self._refresh_temp_path()
        self.temp_list.clear()
        root = self._temp_root()
        if root is None:
            self.temp_list.addItem("先选择保存目录，临时文件会自动使用 PawchiveDownload/Temp")
            self.temp_clear_button.setEnabled(False)
            return
        entries = iter_temp_entries(root)
        part_files = iter_part_files(root)
        if not entries:
            self.temp_list.addItem("暂无临时文件或下载记录（.part 与 State 会同步显示在这里）")
            self.temp_clear_button.setEnabled(False)
            return
        for path, size, kind in entries[:300]:
            relative = path.relative_to(root).as_posix()
            text = f"{relative}    {format_size(size)}"
            if kind == "record":
                text += "    [下载记录]"
            item = QListWidgetItem(text)
            item.setToolTip(str(path))
            self.temp_list.addItem(item)
        if len(entries) > 300:
            self.temp_list.addItem(f"……还有 {len(entries) - 300} 个临时项")
        self.temp_clear_button.setEnabled(
            bool(part_files) and not getattr(self, "_running", False)
        )

    def _on_temp_timer(self) -> None:
        if self.tabs.currentWidget() is self.temp_page:
            self._refresh_temp_view()

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.temp_page:
            self._refresh_temp_view()

    def _on_content_tab_changed(self, index: int) -> None:
        if self.content_tabs.widget(index) is self.save_page:
            self._refresh_save_view()
        else:
            QTimer.singleShot(0, self._load_visible_thumbnails)

    def _open_temp_dir(self) -> None:
        root = self._temp_root()
        if root is None:
            MessageDialog.show_message(
                self, "还没有临时目录", "请先选择保存目录，程序会自动创建 PawchiveDownload/Temp。",
                kind="info", theme=self.ui_theme
            )
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            MessageDialog.show_message(
                self, "无法创建临时目录", str(exc), kind="error", theme=self.ui_theme
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def _clear_temp_files(self) -> None:
        if getattr(self, "_running", False):
            MessageDialog.show_message(
                self, "下载进行中", "请先停止当前下载，再清理临时文件。",
                kind="info", theme=self.ui_theme
            )
            return
        root = self._temp_root()
        if root is None:
            MessageDialog.show_message(
                self, "还没有临时目录", "请先选择保存目录，程序会自动创建 PawchiveDownload/Temp。",
                kind="info", theme=self.ui_theme
            )
            return
        files = iter_part_files(root)
        if not files:
            MessageDialog.show_message(
                self, "没有可清理的文件", "当前临时目录里没有 .part 文件。",
                kind="info", theme=self.ui_theme
            )
            return
        total = sum(size for _, size in files)
        dialog = MessageDialog(
            self,
            "清理临时文件",
            f"将删除 {len(files)} 个未完成的 .part 文件，共 {format_size(total)}。\n"
            "已下载完成、位于保存目录的成品文件不会受影响；State 下载记录也不会删除。",
            kind="warning",
            confirm=True,
            theme=self.ui_theme,
        )
        dialog.exec()
        if not dialog.confirmed:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            deleted, freed, errors = clear_temp_root(root)
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_temp_view()
        self._log(f"已清理 {deleted} 个临时文件，释放 {format_size(freed)}")
        if errors:
            MessageDialog.show_message(
                self,
                "部分临时文件未清理",
                "\n".join(errors[:8]),
                kind="warning",
                theme=self.ui_theme,
            )

    def _open_save_dir(self) -> None:
        path = self._save_root()
        if path is None:
            MessageDialog.show_message(
                self, "还没有保存目录", "请先选择保存目录。", kind="info", theme=self.ui_theme
            )
            return
        if not path.exists() or not path.is_dir():
            MessageDialog.show_message(
                self, "保存目录不可用", f"目录不存在或不是文件夹：\n{path}",
                kind="warning", theme=self.ui_theme
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _refresh_save_view(self) -> None:
        root = self._save_root()
        self.save_tree.clear()
        self._save_has_files = False
        self._save_scan = None
        if root is None:
            self.save_path_view.clear()
            self.save_path_view.setToolTip("先选择保存目录")
            self.save_summary_label.setText("先选择保存目录")
            self._set_save_placeholder("先选择保存目录")
            self.save_refresh_button.setEnabled(False)
            self.save_verify_button.setEnabled(False)
            return
        self.save_path_view.setText(str(root))
        self.save_path_view.setCursorPosition(0)
        self.save_path_view.setToolTip(str(root))
        self.save_refresh_button.setEnabled(True)
        if not root.is_dir():
            self.save_summary_label.setText("目录不存在或不是文件夹")
            self._set_save_placeholder("目录不存在或不是文件夹")
            self.save_verify_button.setEnabled(False)
            return

        scan = scan_save_root(
            root,
            state_dir=state_dir_for(self._program_root()) if self._program_root() is not None else None,
        )
        self._save_scan = scan
        self._populate_save_tree(scan)
        self._apply_save_preview_mode(self._current_preview_mode(), rebuild=True)
        self._save_has_files = any(post.files for post in scan.posts)
        if not scan.posts:
            self.save_summary_label.setText("还没有本程序下载的帖子")
        else:
            text = f"{len(scan.posts)} 个帖子目录 · {scan.total_files} 个文件 · {format_size(scan.total_bytes)}"
            if scan.truncated:
                text += " · 文件较多，仅显示前 2000 项"
            self.save_summary_label.setText(text)
        self.save_verify_button.setEnabled(
            self._save_has_files and not getattr(self, "_running", False)
        )

    def _set_save_placeholder(self, text: str) -> None:
        item = QTreeWidgetItem([text, "", ""])
        item.setFirstColumnSpanned(True)
        self.save_tree.addTopLevelItem(item)
        self.save_preview_panel.clear()

    def _apply_save_preview_mode(self, mode: str, *, rebuild: bool = False) -> None:
        mode = mode if mode in PREVIEW_MODE_KEYS else "text"
        self.save_preview_panel.mode = mode
        self.save_stack.setCurrentIndex(0 if mode == "text" else 1)
        if mode == "text":
            return
        scan = self._save_scan
        if rebuild or len(self.save_preview_panel.cards) != len(scan.posts if scan else []):
            self.save_preview_panel.clear()
            if scan is not None:
                for post in scan.posts:
                    card = PreviewCard(
                        str(post.path),
                        saved_post_title(post),
                        saved_post_time(post),
                        mode,
                        selectable=False,
                        local_image=post.preview_image,
                    )
                    self.save_preview_panel.add_card(card)
        self.save_preview_panel.relayout()
        QTimer.singleShot(0, self._load_visible_save_thumbs)

    def _queue_visible_save_thumbnails(self) -> None:
        if self.save_stack.currentIndex() == 1:
            QTimer.singleShot(80, self._load_visible_save_thumbs)

    def _load_visible_save_thumbs(self) -> None:
        if self.save_stack.currentIndex() != 1:
            return
        scroll = self.save_scroll.verticalScrollBar().value()
        self.save_preview_panel.request_visible(scroll, self.save_scroll.viewport().height())

    def _populate_save_tree(
        self,
        scan: SaveScan,
        result: VerifyResult | None = None,
    ) -> None:
        self.save_tree.clear()
        if not scan.posts:
            self._set_save_placeholder("还没有已下载的帖子")
            return
        danger = QBrush(QColor(palette_for(self.ui_theme).danger_text))
        for post in scan.posts:
            post_status = ""
            if result is not None:
                if post.missing_dir:
                    post_status = "目录缺失"
                elif not post.files:
                    post_status = "空目录"
                elif post.issue_count:
                    post_status = "异常"
                else:
                    post_status = "正常"
            name = post.path.name
            if post.missing_dir:
                name += "（目录缺失）"
            post_item = QTreeWidgetItem([name, format_size(post.total_size), post_status])
            post_item.setToolTip(0, str(post.path))
            if result is not None and post_status in ("异常", "目录缺失"):
                post_item.setForeground(2, danger)
            self.save_tree.addTopLevelItem(post_item)
            for item in post.files:
                label = STATUS_LABELS.get(item.status, "—")
                size_text = "—" if item.status == "missing" else format_size(item.size)
                child = QTreeWidgetItem([item.path.name, size_text, label])
                tip = str(item.path)
                if item.detail:
                    tip += f"\n{item.detail}"
                child.setToolTip(0, tip)
                child.setToolTip(2, tip)
                if item.status and item.status != "ok":
                    child.setForeground(2, danger)
                post_item.addChild(child)
        self.save_tree.collapseAll()

    def _verify_save_view(self) -> None:
        root = self._save_root()
        program_root = self._program_root()
        if root is None or not root.is_dir():
            MessageDialog.show_message(
                self, "无法校验", "请先选择一个存在的保存目录。",
                kind="info", theme=self.ui_theme
            )
            return
        state_dir = state_dir_for(program_root) if program_root is not None else None
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            result = verify_save_root(root, state_dir=state_dir)
        finally:
            QApplication.restoreOverrideCursor()
        self._populate_save_tree(result.scan, result)
        self._save_scan = result.scan
        self._save_has_files = any(post.files for post in result.scan.posts)
        self._apply_save_preview_mode(self._current_preview_mode(), rebuild=True)
        checked = result.ok_count + result.issue_count
        if not checked:
            summary = "保存目录里没有可校验的文件"
        elif result.issue_count == 0:
            summary = f"校验完成：{checked} 个文件全部正常"
        else:
            detail = []
            if result.missing_count:
                detail.append(f"缺失 {result.missing_count}")
            if result.empty_count:
                detail.append(f"空文件 {result.empty_count}")
            if result.corrupt_count:
                detail.append(f"损坏 {result.corrupt_count}")
            if result.incomplete_count:
                detail.append(f"未完成 {result.incomplete_count}")
            if result.size_mismatch_count:
                detail.append(f"大小不符 {result.size_mismatch_count}")
            summary = f"校验完成：{result.ok_count} 正常 · {result.issue_count} 异常"
            if detail:
                summary += "（" + " / ".join(detail) + "）"
        if not result.has_records and checked:
            summary += " · 无下载记录，仅检查现有文件"
        if result.scan.truncated:
            summary += " · 仅显示前 2000 项"
        self.save_summary_label.setText(summary)
        self._log(summary)
        self.save_verify_button.setEnabled(
            self._save_has_files and not getattr(self, "_running", False)
        )
        if checked and self.app_settings.image_source == "original":
            self._start_preview_audit(root, state_dir)

    def _start_preview_audit(self, root: Path, state_dir: Path | None) -> None:
        if getattr(self, "_preview_busy", False):
            return
        self._set_preview_busy(True)
        self.save_summary_label.setText("正在检查是否有可替换的原图...")
        self._log("开始检查预览图能否替换为原图...")
        self.preview_audit_worker = PreviewAuditWorker(root, state_dir, self.app_settings)
        self.preview_audit_worker.done.connect(self._on_preview_audit_done)
        self.preview_audit_worker.error.connect(self._on_preview_audit_error)
        self.preview_audit_worker.start()

    def _set_preview_busy(self, busy: bool) -> None:
        self._preview_busy = busy
        busy_cursor = Qt.CursorShape.BusyCursor
        if busy:
            QApplication.setOverrideCursor(busy_cursor)
        else:
            QApplication.restoreOverrideCursor()
        if hasattr(self, "save_verify_button"):
            self.save_verify_button.setEnabled(
                not busy
                and not getattr(self, "_running", False)
                and getattr(self, "_save_has_files", False)
            )

    def _on_preview_audit_error(self, message: str) -> None:
        self._set_preview_busy(False)
        self._log(f"原图检查失败: {message}", danger=True)
        self.save_summary_label.setText("原图检查失败，可重试完整性校验")
        MessageDialog.show_message(
            self, "原图检查失败", message, kind="warning", theme=self.ui_theme
        )

    def _on_preview_audit_done(self, result: PreviewAuditResult) -> None:
        self._set_preview_busy(False)
        candidates = result.candidates or []
        if not candidates:
            if result.errors:
                self._log(
                    f"原图检查：扫描 {result.checked} 条图片记录，{len(result.errors)} 条请求失败",
                    danger=True,
                )
                self.save_summary_label.setText(
                    f"校验完成；原图检查 {len(result.errors)} 条请求失败，"
                    "未发现可确认的原图"
                )
                return
            self._log(
                f"原图检查完成：扫描 {result.checked} 条图片记录，没有发现可替换的原图"
            )
            self.save_summary_label.setText(
                f"校验完成；已检查 {result.checked} 条图片记录，无需替换原图"
            )
            return
        total_size = sum(item.remote_size for item in candidates)
        dialog = MessageDialog(
            self,
            "检测到预览图",
            f"检测到该目录下 {len(candidates)} 张图片的本地文件小于远端原图，是否下载替换？\n"
            f"预计下载 {format_size(total_size)}，替换后原预览图将被覆盖，"
            "下载记录中的地址会同步更新。",
            kind="warning",
            confirm=True,
            theme=self.ui_theme,
        )
        dialog.exec()
        if not dialog.confirmed:
            self._log(f"已取消原图替换：{len(candidates)} 张")
            self.save_summary_label.setText(
                f"校验完成；发现 {len(candidates)} 张可替换原图，用户取消"
            )
            return
        program_root = self._program_root()
        if program_root is None:
            MessageDialog.show_message(
                self, "无法替换", "主保存目录不可用。",
                kind="error", theme=self.ui_theme
            )
            return
        temp_root = resolve_temp_root(self.app_settings, program_root)
        self._set_preview_busy(True)
        self.save_summary_label.setText(f"正在替换原图 0/{len(candidates)}...")
        self._log(f"开始下载替换 {len(candidates)} 张原图，共 {format_size(total_size)}")
        self.preview_replace_worker = PreviewReplaceWorker(
            candidates, temp_root, self.app_settings
        )
        self.preview_replace_worker.progress.connect(self._on_preview_replace_progress)
        self.preview_replace_worker.done.connect(self._on_preview_replace_done)
        self.preview_replace_worker.error.connect(self._on_preview_replace_error)
        self.preview_replace_worker.start()

    def _on_preview_replace_progress(self, done: int, total: int) -> None:
        self.save_summary_label.setText(f"正在替换原图 {done}/{total}...")

    def _on_preview_replace_done(self, count: int, errors: list) -> None:
        self._set_preview_busy(False)
        self._log(f"原图替换完成：成功 {count} 张，失败 {len(errors)} 张")
        if errors:
            MessageDialog.show_message(
                self,
                "部分原图替换失败",
                "\n".join(errors[:8]),
                kind="warning",
                theme=self.ui_theme,
            )
        self._refresh_save_view()


    def _export_log(self) -> None:
        root = self._program_root()
        if root is None:
            MessageDialog.show_message(
                self, "还没有主保存目录", "请先设置主保存目录，再导出日志。",
                kind="info", theme=self.ui_theme
            )
            return
        log_dir = program_log_dir(root)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            MessageDialog.show_message(
                self, "日志目录不可用", f"无法创建：\n{log_dir}\n\n{exc}",
                kind="error", theme=self.ui_theme
            )
            return

        stamp = datetime.now().strftime("%Y%m%d")
        default_path = log_dir / f"{stamp}.log"
        if default_path.exists():
            for index in range(2, 1000):
                candidate = log_dir / f"{stamp}_{index}.log"
                if not candidate.exists():
                    default_path = candidate
                    break

        chosen, _filter = QFileDialog.getSaveFileName(
            self, "导出日志", str(default_path), "日志文件 (*.log);;文本文件 (*.txt)"
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() not in (".log", ".txt"):
            target = target.with_suffix(".log")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header = (
                f"Pawchive Downloader v{APP_VERSION}\n"
                f"导出时间：{exported_at}\n"
                f"主保存目录：{root}\n"
                f"当前下载目录：{self._save_root() or root}\n\n"
            )
            target.write_text(header + self.log_view.toPlainText(), encoding="utf-8")
        except OSError as exc:
            MessageDialog.show_message(
                self, "日志导出失败", f"{target}\n\n{exc}",
                kind="error", theme=self.ui_theme
            )
            return
        self._log(f"日志已导出: {target}")

    def _log(self, message: str, danger: bool = False) -> None:
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.log_view.document().characterCount() > 1:
            cursor.insertText("\n")
        fmt = QTextCharFormat()
        if danger:
            fmt.setForeground(QColor(palette_for(self.ui_theme).danger_text))
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(str(message), fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def _show_update_status(self, data: dict) -> None:
        if not data:
            return
        status = str(data.get("status") or "")
        stage = str(data.get("stage") or "")
        error = str(data.get("error") or "")
        if status == "failed":
            message = f"外置更新器失败：{stage}" + (f"\n{error}" if error else "")
            self._log(message, danger=True)
            QTimer.singleShot(
                0,
                lambda: MessageDialog.show_message(
                    self,
                    "上次更新未完成",
                    message + "\n\n旧程序已保留或已回滚，可重新检查更新。",
                    kind="error",
                    theme=self.ui_theme,
                ),
            )
        elif status == "success":
            self._log(f"外置更新器完成：{stage}")
        elif status == "pending":
            self._log(f"更新器状态：{stage}")

    def start_announcement_loader(self) -> None:
        """显示当前版本的更新公告；同一版本已屏蔽则不再显示。"""
        if self.app_settings.announcement_dismissed_version == APP_VERSION:
            return
        text = version_announcement_text(APP_VERSION)
        if not text:
            self._log("未找到当前版本的更新公告内容")
            return
        QTimer.singleShot(0, lambda: self._show_announcement(text))

    def _show_announcement(self, text: str) -> None:
        if self.app_settings.announcement_dismissed_version == APP_VERSION:
            return
        dialog = AnnouncementDialog(text, self.font_family, self, version=APP_VERSION, theme=self.ui_theme)
        dialog.closed.connect(self._on_announcement_closed)
        self.announcement_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_announcement_closed(self, dismissed: bool) -> None:
        self.announcement_dialog = None
        if not dismissed:
            return
        self.app_settings.announcement_dismissed_version = APP_VERSION
        try:
            save_settings(self.app_settings)
        except Exception as exc:
            self._log(f"公告设置保存失败: {exc}")
            return
        self._log(f"公告已屏蔽：v{APP_VERSION} 内不再弹出（版本更新后恢复）")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_title_bar(self, self.ui_theme)
        self._position_size_grip()

    def _position_size_grip(self) -> None:
        grip = getattr(self, "size_grip", None)
        if grip is None:
            return
        grip.setVisible(not self.isMaximized())
        grip.move(self._shell.width() - grip.width(), self._shell.height() - grip.height())
        grip.raise_()

    def _follow_system_theme(self, *_args) -> None:
        if self.app_settings.ui_theme != "system":
            return
        new_theme = resolve_theme("system")
        if new_theme == self.ui_theme:
            return
        self.ui_theme = new_theme
        self._apply_style()
        apply_title_bar(self, self.ui_theme)

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.stop()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_size_grip()
        if not self.enforces_ratio or self._ratio_resize_guard:
            return

        size = event.size()
        if size.height() <= 0:
            return
        if abs((size.width() / size.height()) - self.background_ratio) < 0.01:
            return

        old_size = event.oldSize()
        width_changed = old_size.isValid() and abs(size.width() - old_size.width()) >= abs(
            size.height() - old_size.height()
        )
        next_size = (
            self._ratio_size_for_width(size.width())
            if width_changed or not old_size.isValid()
            else self._ratio_size_for_height(size.height())
        )

        self._ratio_resize_guard = True
        self.resize(next_size)
        self._ratio_resize_guard = False

    def _ratio_size_for_width(self, width: int) -> QSize:
        return QSize(width, max(1, round(width / self.background_ratio)))

    def _ratio_size_for_height(self, height: int) -> QSize:
        return QSize(max(1, round(height * self.background_ratio)), height)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            main_qss(
                palette_for(self.ui_theme),
                self.font_family,
                background_path=BACKGROUND_PATH,
                branch_closed_path=BRANCH_CLOSED.get(self.ui_theme),
                branch_open_path=BRANCH_OPEN.get(self.ui_theme),
                arrow_down_path=(
                    ARROW_DOWN_LIGHT_PATH if self.ui_theme == "light" else ARROW_DOWN_PATH
                ),
            )
        )
        if hasattr(self, "title_bar"):
            self.title_bar.apply_theme(self.ui_theme)


def main() -> int:
    if "--apply-update" in sys.argv:
        from pawchive.update_helper import run_helper
        return run_helper(sys.argv)
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    font_family = load_app_font()
    app.setFont(QFont(font_family, 10))
    app.setWindowIcon(load_app_icon())
    try:
        cleanup_previous_update_files()
        cleanup_update_temp()
        cleanup_legacy_manifest()
    except OSError:
        pass
    window = MainWindow(font_family)
    window.show()
    window._show_update_status(pop_update_status())
    window._maybe_self_check()
    window.start_announcement_loader()
    return app.exec()


def load_app_font() -> str:
    if not FONT_PATH.exists():
        return DEFAULT_FONT_FAMILY

    font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
    if font_id < 0:
        return DEFAULT_FONT_FAMILY

    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else DEFAULT_FONT_FAMILY


def load_app_icon() -> QIcon:
    if not ICON_PATH.exists():
        return QIcon()
    return QIcon(str(ICON_PATH))


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:
        return


def load_background_aspect_ratio() -> float:
    pixmap = QPixmap(str(BACKGROUND_PATH))
    if pixmap.isNull() or pixmap.height() <= 0:
        return 16 / 9
    return pixmap.width() / pixmap.height()


if __name__ == "__main__":
    raise SystemExit(main())
