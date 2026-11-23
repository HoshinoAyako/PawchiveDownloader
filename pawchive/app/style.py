"""集中式主题色板与 QSS 生成。

颜色只在这里定义；各个窗口通过语义 token 引用，避免同一颜色散落在多个
样式表里、改一处漏三处。模板使用 ``@{token}`` 占位，由 :func:`render`
一次性替换。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Palette:
    """语义化色板。字段名描述用途，不描述具体色值。"""

    name: str
    # 主窗背景（完整 background 声明）
    main_bg: str
    # 文本层级
    text: str
    title: str
    subtitle: str
    section: str
    field: str
    summary: str
    settings_summary: str
    count_text: str
    count_bg: str
    hint: str
    # 卡片 / 分隔线
    card_bg: str
    card_border: str
    divider: str
    # 输入控件
    input_bg: str
    input_border: str
    input_text: str
    input_focus_border: str
    input_focus_bg: str
    placeholder: str
    disabled_bg: str
    disabled_border: str
    disabled_text: str
    # 主按钮
    primary_bg: str
    primary_hover: str
    primary_pressed: str
    primary_border: str
    primary_hover_border: str
    primary_disabled_bg: str
    primary_disabled_border: str
    primary_disabled_text: str
    on_primary: str
    # 次级按钮
    ghost_bg: str
    ghost_border: str
    ghost_text: str
    ghost_hover_bg: str
    ghost_hover_border: str
    ghost_pressed_bg: str
    ghost_disabled_bg: str
    ghost_disabled_border: str
    ghost_disabled_text: str
    # 危险按钮
    danger_bg: str
    danger_border: str
    danger_text: str
    danger_hover_bg: str
    danger_hover_border: str
    danger_pressed_bg: str
    # 列表 / 日志
    list_bg: str
    list_border: str
    list_text: str
    list_hover_bg: str
    selection_bg: str
    selection_text: str
    # 进度条
    progress_bg: str
    accent_light: str
    # Tab
    tab_text: str
    tab_hover: str
    tab_selected: str
    # Tooltip
    tooltip_bg: str
    tooltip_text: str
    tooltip_border: str
    # 自绘标题栏
    titlebar_bg: str
    titlebar_border: str
    titlebar_text: str
    titlebar_button: str
    # 弹窗
    dialog_bg: str
    dialog_card_bg: str
    dialog_card_border: str
    inner_bg: str
    inner_border: str
    dialog_text: str
    dialog_title: str
    dialog_body: str
    dialog_hint: str
    dialog_close: str
    dialog_close_hover_bg: str
    dialog_close_hover_text: str
    shadow_alpha: int
    # 勾选框
    checkbox_text: str
    checkbox_border: str
    checkbox_bg: str
    checkbox_hover_border: str
    checkbox_checked_bg: str
    checkbox_checked_border: str
    # 下拉框弹出层 / 数字框按钮
    combo_popup_bg: str
    combo_popup_border: str
    combo_selection_bg: str
    combo_selection_text: str
    spin_bg: str
    spin_border: str
    spin_hover_bg: str
    scrollbar: str


DARK = Palette(
    name="dark",
    main_bg=(
        "background: qlineargradient(x1:0, y1:0, x2:0.25, y2:1,"
        " stop:0 #090912, stop:0.55 #101021, stop:1 #16162b);"
    ),
    text="rgba(255, 255, 255, 232)",
    title="rgba(255, 255, 255, 242)",
    subtitle="rgba(160, 185, 240, 170)",
    section="rgba(140, 175, 255, 232)",
    field="rgba(255, 255, 255, 190)",
    summary="rgba(255, 255, 255, 212)",
    settings_summary="rgba(255, 255, 255, 142)",
    count_text="rgba(255, 255, 255, 155)",
    count_bg="rgba(255, 255, 255, 14)",
    hint="rgba(255, 255, 255, 118)",
    card_bg="rgba(255, 255, 255, 10)",
    card_border="rgba(255, 255, 255, 26)",
    divider="rgba(255, 255, 255, 34)",
    input_bg="rgba(255, 255, 255, 12)",
    input_border="rgba(255, 255, 255, 40)",
    input_text="rgba(255, 255, 255, 240)",
    input_focus_border="rgba(120, 170, 255, 200)",
    input_focus_bg="rgba(255, 255, 255, 18)",
    placeholder="rgba(255, 255, 255, 120)",
    disabled_bg="rgba(255, 255, 255, 6)",
    disabled_border="rgba(255, 255, 255, 24)",
    disabled_text="rgba(255, 255, 255, 100)",
    primary_bg="#3478f6",
    primary_hover="#4b86f8",
    primary_pressed="#2b66dd",
    primary_border="rgba(52, 120, 246, 240)",
    primary_hover_border="rgba(75, 134, 248, 240)",
    primary_disabled_bg="rgba(52, 120, 246, 70)",
    primary_disabled_border="rgba(52, 120, 246, 85)",
    primary_disabled_text="rgba(255, 255, 255, 110)",
    on_primary="#ffffff",
    ghost_bg="rgba(255, 255, 255, 9)",
    ghost_border="rgba(255, 255, 255, 50)",
    ghost_text="rgba(255, 255, 255, 218)",
    ghost_hover_bg="rgba(255, 255, 255, 22)",
    ghost_hover_border="rgba(255, 255, 255, 100)",
    ghost_pressed_bg="rgba(255, 255, 255, 14)",
    ghost_disabled_bg="rgba(255, 255, 255, 5)",
    ghost_disabled_border="rgba(255, 255, 255, 22)",
    ghost_disabled_text="rgba(255, 255, 255, 95)",
    danger_bg="rgba(240, 110, 110, 12)",
    danger_border="rgba(240, 110, 110, 105)",
    danger_text="#ff9c9c",
    danger_hover_bg="rgba(240, 110, 110, 30)",
    danger_hover_border="rgba(240, 110, 110, 165)",
    danger_pressed_bg="rgba(240, 110, 110, 45)",
    list_bg="rgba(255, 255, 255, 9)",
    list_border="rgba(255, 255, 255, 26)",
    list_text="rgba(255, 255, 255, 225)",
    list_hover_bg="rgba(255, 255, 255, 16)",
    selection_bg="rgba(52, 120, 246, 160)",
    selection_text="rgba(255, 255, 255, 248)",
    progress_bg="rgba(255, 255, 255, 18)",
    accent_light="#5b9bff",
    tab_text="rgba(255, 255, 255, 165)",
    tab_hover="rgba(255, 255, 255, 220)",
    tab_selected="rgba(255, 255, 255, 245)",
    tooltip_bg="#22223a",
    tooltip_text="rgba(255, 255, 255, 235)",
    tooltip_border="rgba(255, 255, 255, 60)",
    titlebar_bg="#111120",
    titlebar_border="rgba(255, 255, 255, 18)",
    titlebar_text="#E7E9F2",
    titlebar_button="#CDD2DF",
    dialog_bg="#17172a",
    dialog_card_bg="#1a1b2e",
    dialog_card_border="rgba(255, 255, 255, 46)",
    inner_bg="rgba(255, 255, 255, 10)",
    inner_border="rgba(255, 255, 255, 30)",
    dialog_text="rgba(255, 255, 255, 225)",
    dialog_title="rgba(255, 255, 255, 248)",
    dialog_body="rgba(255, 255, 255, 214)",
    dialog_hint="rgba(255, 255, 255, 130)",
    dialog_close="rgba(255, 255, 255, 165)",
    dialog_close_hover_bg="rgba(255, 255, 255, 24)",
    dialog_close_hover_text="rgba(255, 255, 255, 235)",
    shadow_alpha=150,
    checkbox_text="rgba(255, 255, 255, 230)",
    checkbox_border="rgba(255, 255, 255, 170)",
    checkbox_bg="rgba(255, 255, 255, 40)",
    checkbox_hover_border="rgba(255, 255, 255, 180)",
    checkbox_checked_bg="rgba(52, 120, 246, 220)",
    checkbox_checked_border="rgba(52, 120, 246, 235)",
    combo_popup_bg="#22223a",
    combo_popup_border="rgba(255, 255, 255, 45)",
    combo_selection_bg="rgba(52, 120, 246, 190)",
    combo_selection_text="#ffffff",
    spin_bg="rgba(255, 255, 255, 12)",
    spin_border="rgba(255, 255, 255, 35)",
    spin_hover_bg="rgba(255, 255, 255, 34)",
    scrollbar="rgba(255, 255, 255, 60)",
)

LIGHT = Palette(
    name="light",
    main_bg=(
        "background: qlineargradient(x1:0, y1:0, x2:0.2, y2:1,"
        " stop:0 #FAFBFE, stop:0.55 #F2F4F9, stop:1 #E8ECF5);"
    ),
    text="#1C2333",
    title="#141A27",
    subtitle="#5A6E93",
    section="#2F5FA8",
    field="#3E4961",
    summary="#2A3448",
    settings_summary="#717C94",
    count_text="#4A5670",
    count_bg="rgba(40, 70, 130, 12)",
    hint="#7883A0",
    card_bg="#FFFFFF",
    card_border="rgba(28, 40, 70, 30)",
    divider="rgba(28, 40, 70, 26)",
    input_bg="#FFFFFF",
    input_border="#D4DAE6",
    input_text="#1C2333",
    input_focus_border="#2F6FE4",
    input_focus_bg="#FFFFFF",
    placeholder="#9AA3B8",
    disabled_bg="#F1F3F8",
    disabled_border="#E1E5EE",
    disabled_text="#9AA3B8",
    primary_bg="#2F6FE4",
    primary_hover="#3F7EF0",
    primary_pressed="#285FD0",
    primary_border="#2F6FE4",
    primary_hover_border="#3F7EF0",
    primary_disabled_bg="rgba(47, 111, 228, 55)",
    primary_disabled_border="rgba(47, 111, 228, 70)",
    primary_disabled_text="rgba(255, 255, 255, 170)",
    on_primary="#ffffff",
    ghost_bg="rgba(28, 40, 70, 6)",
    ghost_border="rgba(28, 40, 70, 50)",
    ghost_text="#3A4560",
    ghost_hover_bg="rgba(28, 40, 70, 12)",
    ghost_hover_border="rgba(28, 40, 70, 95)",
    ghost_pressed_bg="rgba(28, 40, 70, 18)",
    ghost_disabled_bg="#F5F7FA",
    ghost_disabled_border="#E3E7EF",
    ghost_disabled_text="#A0A9BC",
    danger_bg="rgba(198, 60, 60, 8)",
    danger_border="rgba(198, 60, 60, 100)",
    danger_text="#C0392B",
    danger_hover_bg="rgba(198, 60, 60, 18)",
    danger_hover_border="rgba(198, 60, 60, 155)",
    danger_pressed_bg="rgba(198, 60, 60, 28)",
    list_bg="#FFFFFF",
    list_border="#DDE2EC",
    list_text="#1C2333",
    list_hover_bg="rgba(40, 70, 130, 7)",
    selection_bg="#DBE8FF",
    selection_text="#17233D",
    progress_bg="#E4E8F0",
    accent_light="#3F7EF0",
    tab_text="#6B768C",
    tab_hover="#2B3548",
    tab_selected="#1C2333",
    tooltip_bg="#FFFFFF",
    tooltip_text="#1C2333",
    tooltip_border="#D4DAE6",
    titlebar_bg="#EEF1F7",
    titlebar_border="#D8DDE8",
    titlebar_text="#1C2333",
    titlebar_button="#4A5670",
    dialog_bg="#F7F8FC",
    dialog_card_bg="#FFFFFF",
    dialog_card_border="rgba(28, 40, 70, 38)",
    inner_bg="#FFFFFF",
    inner_border="#DFE4EE",
    dialog_text="#2B3548",
    dialog_title="#161C2A",
    dialog_body="#3A4560",
    dialog_hint="#75809A",
    dialog_close="#5A6478",
    dialog_close_hover_bg="rgba(28, 40, 70, 12)",
    dialog_close_hover_text="#1C2333",
    shadow_alpha=60,
    checkbox_text="#2B3548",
    checkbox_border="#B9C1D0",
    checkbox_bg="#FFFFFF",
    checkbox_hover_border="#8E9AB2",
    checkbox_checked_bg="#2F6FE4",
    checkbox_checked_border="rgba(47, 111, 228, 235)",
    combo_popup_bg="#FFFFFF",
    combo_popup_border="#D4DAE6",
    combo_selection_bg="#DBE8FF",
    combo_selection_text="#17233D",
    spin_bg="#F7F8FC",
    spin_border="#E1E5EE",
    spin_hover_bg="#EDF0F6",
    scrollbar="rgba(28, 40, 70, 55)",
)


PALETTES = {"dark": DARK, "light": LIGHT}


def palette_for(theme: str) -> Palette:
    return PALETTES.get(theme, DARK)


def render(template: str, palette: Palette, **extra: Any) -> str:
    values = {**asdict(palette), **extra}
    for key, value in values.items():
        template = template.replace("@{" + key + "}", str(value))
    return template


MAIN_TEMPLATE = """
QWidget#root {
    @{main_bg}
    color: @{text};
    font-family: "@{font_family}", "Microsoft YaHei UI", "Segoe UI";
    font-size: 14px;
}
QWidget#titleBar {
    background: @{titlebar_bg};
    border-bottom: 1px solid @{titlebar_border};
}
QLabel#titleBarTitle {
    color: @{titlebar_text};
    font-size: 14px;
    font-weight: 600;
}
QPushButton#titleBarButton {
    min-height: 0;
    padding: 0;
    border: none;
    border-radius: 0;
    background: transparent;
    color: @{titlebar_button};
    font-size: 15px;
}
QPushButton#titleBarButton:hover {
    background: @{ghost_hover_bg};
    color: @{titlebar_text};
}
QPushButton#titleBarButton:pressed {
    background: @{ghost_pressed_bg};
}
QPushButton#titleBarClose {
    min-height: 0;
    padding: 0;
    border: none;
    border-radius: 0;
    background: transparent;
    color: @{titlebar_button};
    font-size: 14px;
}
QPushButton#titleBarClose:hover {
    background: #c42b1c;
    color: #ffffff;
}
QSizeGrip { background: transparent; }
QWidget#card {
    background: @{card_bg};
    border: 1px solid @{card_border};
    border-radius: 14px;
}
QLabel#titleLabel {
    font-size: 28px;
    font-weight: 700;
    color: @{title};
}
QLabel#subtitleLabel {
    font-size: 12px;
    color: @{subtitle};
}
QLabel#sectionLabel {
    font-size: 13px;
    font-weight: 600;
    color: @{section};
}
QLabel#fieldLabel {
    font-size: 13px;
    color: @{field};
}
QLabel#summaryLabel {
    font-size: 13px;
    color: @{summary};
}
QLabel#settingsSummary {
    font-size: 12px;
    color: @{settings_summary};
}
QLabel#countLabel {
    font-size: 12px;
    color: @{count_text};
    background: @{count_bg};
    border-radius: 9px;
    padding: 2px 9px;
}
QLabel#hintLabel {
    font-size: 12px;
    color: @{hint};
}
QLineEdit {
    min-height: 34px;
    border: 1px solid @{input_border};
    border-radius: 8px;
    background: @{input_bg};
    color: @{input_text};
    padding: 3px 10px;
    selection-background-color: @{primary_bg};
}
QLineEdit:focus {
    border: 1px solid @{input_focus_border};
    background: @{input_focus_bg};
}
QLineEdit::placeholder {
    color: @{placeholder};
}
QComboBox {
    min-height: 34px;
    border: 1px solid @{input_border};
    border-radius: 8px;
    background: @{input_bg};
    color: @{input_text};
    padding: 2px 34px 2px 10px;
}
QComboBox:focus {
    border: 1px solid @{input_focus_border};
}
QComboBox QAbstractItemView {
    background: @{combo_popup_bg};
    color: @{input_text};
    border: 1px solid @{combo_popup_border};
    selection-background-color: @{combo_selection_bg};
    selection-color: @{combo_selection_text};
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url("@{arrow_down}");
    width: 12px;
    height: 12px;
}
QPushButton, QListWidget, QListWidget::item, QTabBar::tab, QPlainTextEdit {
    outline: none;
}
QPushButton {
    min-height: 34px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: @{ghost_text};
    padding: 0 16px;
}
QPushButton#primaryButton {
    background: @{primary_bg};
    border: 1px solid @{primary_border};
    color: @{on_primary};
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: @{primary_hover};
    border-color: @{primary_hover_border};
}
QPushButton#primaryButton:pressed {
    background: @{primary_pressed};
}
QPushButton#primaryButton:disabled {
    background: @{primary_disabled_bg};
    border-color: @{primary_disabled_border};
    color: @{primary_disabled_text};
}
QPushButton#ghostButton {
    background: @{ghost_bg};
    border: 1px solid @{ghost_border};
    color: @{ghost_text};
}
QPushButton#ghostButton:hover {
    background: @{ghost_hover_bg};
    border-color: @{ghost_hover_border};
}
QPushButton#ghostButton:pressed {
    background: @{ghost_pressed_bg};
}
QPushButton#ghostButton:disabled {
    background: @{ghost_disabled_bg};
    border-color: @{ghost_disabled_border};
    color: @{ghost_disabled_text};
}
QPushButton#dangerButton {
    background: @{danger_bg};
    border: 1px solid @{danger_border};
    color: @{danger_text};
}
QPushButton#dangerButton:hover {
    background: @{danger_hover_bg};
    border-color: @{danger_hover_border};
}
QPushButton#dangerButton:pressed {
    background: @{danger_pressed_bg};
}
QPushButton#dangerButton:disabled {
    background: @{ghost_disabled_bg};
    border-color: @{ghost_disabled_border};
    color: @{ghost_disabled_text};
}
QListWidget, QPlainTextEdit {
    background: @{list_bg};
    border: 1px solid @{list_border};
    border-radius: 10px;
    color: @{list_text};
    padding: 6px;
    selection-background-color: @{selection_bg};
}
QTreeWidget#saveTree {
    background: @{list_bg};
    border: 1px solid @{list_border};
    border-radius: 10px;
    color: @{list_text};
    padding: 4px;
    selection-background-color: @{selection_bg};
    selection-color: @{selection_text};
}
QTreeWidget#saveTree::item {
    min-height: 26px;
    padding: 2px 4px;
    border-radius: 5px;
}
QTreeWidget#saveTree::item:hover {
    background: @{list_hover_bg};
}
QTreeWidget#saveTree::item:selected {
    background: @{selection_bg};
    color: @{selection_text};
}
QHeaderView::section {
    background: @{dialog_card_bg};
    color: @{tab_text};
    border: none;
    border-bottom: 1px solid @{list_border};
    padding: 5px 8px;
    font-weight: 600;
}
QListWidget::item {
    min-height: 32px;
    border-radius: 6px;
    padding: 3px 8px;
    color: @{list_text};
}
QListWidget::item:hover {
    background: @{list_hover_bg};
}
QListWidget::item:selected {
    background: @{selection_bg};
    color: @{selection_text};
}
QListWidget#failedList::item {
    min-height: 48px;
    padding: 4px 8px;
}
QListWidget#queueList::item {
    min-height: 48px;
    padding: 4px 8px;
}
QScrollArea#previewScroll {
    background: transparent;
    border: 1px solid @{list_border};
    border-radius: 10px;
}
QWidget#previewPanel {
    background: transparent;
}
QFrame#previewCard {
    background: @{list_bg};
    border: 1px solid @{list_border};
    border-radius: 10px;
}
QFrame#previewCard:hover {
    border-color: @{selection_bg};
}
QFrame#previewCard[selected="true"] {
    border: 1px solid @{selection_bg};
    background: @{card_bg};
}
QLabel#previewTitle {
    color: @{list_text};
    font-size: 13px;
    font-weight: 600;
}
QLabel#previewTime {
    color: @{hint};
    font-size: 11px;
    padding: 2px 8px 7px 8px;
}
QLabel#previewImage {
    background: transparent;
    color: @{hint};
    font-size: 12px;
    padding: 0px 4px;
}
QProgressBar {
    border: none;
    border-radius: 4px;
    background: @{progress_bg};
}
QProgressBar::chunk {
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 @{primary_bg}, stop:1 @{accent_light});
}
QTabWidget#tabs {
    background: transparent;
    border: none;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    min-height: 26px;
    padding: 5px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
    color: @{tab_text};
    margin-right: 8px;
}
QTabBar::tab:hover {
    color: @{tab_hover};
}
QTabBar::tab:selected {
    color: @{tab_selected};
    border-bottom: 2px solid @{primary_bg};
    font-weight: 600;
}
QScrollBar:vertical {
    background: @{dialog_card_bg};
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: @{scrollbar};
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: transparent;
    border: none;
}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
    width: 0px;
    height: 0px;
    image: none;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QToolTip {
    background: @{tooltip_bg};
    color: @{tooltip_text};
    border: 1px solid @{tooltip_border};
    padding: 6px 10px;
}
"""

SETTINGS_TEMPLATE = """
QDialog {
    background: @{dialog_bg};
    color: @{dialog_text};
    font-family: "@{font_family}", "Microsoft YaHei UI", "Segoe UI";
    font-size: 14px;
}
QScrollArea#settingsScroll, QWidget#settingsBody {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: @{dialog_card_bg};
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: @{scrollbar};
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: transparent;
    border: none;
}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
    width: 0px;
    height: 0px;
    image: none;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QWidget#settingsFooter {
    background: @{dialog_bg};
    border-top: 1px solid @{inner_border};
}
QLabel { color: @{dialog_text}; }
QLabel#settingsTitle {
    color: @{dialog_title};
    font-size: 20px;
    font-weight: 700;
}
QLabel#settingsSubtitle {
    color: @{dialog_hint};
    font-size: 12px;
}
QPushButton#settingsClose {
    min-height: 0;
    padding: 0;
    border: none;
    border-radius: 7px;
    background: transparent;
    color: @{dialog_close};
    font-size: 15px;
}
QPushButton#settingsClose:hover {
    background: @{dialog_close_hover_bg};
    color: @{dialog_close_hover_text};
}
QLabel#sectionLabel {
    color: @{section};
    font-size: 13px;
    font-weight: 600;
    padding: 6px 0 0 0;
}
QLabel#fieldLabel { color: @{field}; }
QLabel#settingsHint {
    color: @{hint};
    font-size: 12px;
}
QWidget#innerCard {
    background: @{inner_bg};
    border: 1px solid @{inner_border};
    border-radius: 10px;
}
QFrame#divider {
    background: @{divider};
    border: none;
}
QCheckBox {
    color: @{checkbox_text};
    spacing: 8px;
    padding: 2px 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid @{checkbox_border};
    border-radius: 4px;
    background: @{checkbox_bg};
}
QCheckBox::indicator:hover {
    border: 1px solid @{checkbox_hover_border};
}
QCheckBox::indicator:checked {
    border: 1px solid @{checkbox_checked_border};
    background: @{checkbox_checked_bg};
    image: url("@{check_icon}");
}
QLineEdit, QSpinBox, QComboBox {
    min-height: 34px;
    border: 1px solid @{input_border};
    border-radius: 8px;
    background: @{input_bg};
    color: @{input_text};
    padding: 2px 10px;
    selection-background-color: @{primary_bg};
}
QListWidget#extraStorageList {
    background: @{input_bg};
    color: @{input_text};
    border: 1px solid @{input_border};
    border-radius: 8px;
    padding: 4px;
}
QListWidget#extraStorageList::item {
    min-height: 24px;
    padding: 3px 8px;
    border-radius: 6px;
}
QListWidget#extraStorageList::item:selected {
    background: @{combo_selection_bg};
    color: @{combo_selection_text};
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid @{input_focus_border};
}
QLineEdit:disabled {
    color: @{disabled_text};
    background: @{disabled_bg};
    border: 1px solid @{disabled_border};
}
QComboBox QAbstractItemView {
    background: @{combo_popup_bg};
    color: @{input_text};
    border: 1px solid @{combo_popup_border};
    selection-background-color: @{combo_selection_bg};
    selection-color: @{combo_selection_text};
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url("@{arrow_down}");
    width: 12px;
    height: 12px;
}
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid @{spin_border};
    border-top-right-radius: 8px;
    background: @{spin_bg};
}
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border-left: 1px solid @{spin_border};
    border-bottom-right-radius: 8px;
    background: @{spin_bg};
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: @{spin_hover_bg};
}
QSpinBox::up-arrow {
    image: url("@{arrow_up}");
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow {
    image: url("@{arrow_down}");
    width: 10px;
    height: 10px;
}
QPushButton {
    min-height: 34px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: @{ghost_text};
    padding: 0 16px;
    outline: none;
}
QPushButton#primaryButton {
    background: @{primary_bg};
    border: 1px solid @{primary_border};
    color: @{on_primary};
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: @{primary_hover};
    border-color: @{primary_hover_border};
}
QPushButton#primaryButton:pressed {
    background: @{primary_pressed};
}
QPushButton#primaryButton:disabled {
    background: @{primary_disabled_bg};
    border-color: @{primary_disabled_border};
    color: @{primary_disabled_text};
}
QPushButton#ghostButton {
    background: @{ghost_bg};
    border: 1px solid @{ghost_border};
    color: @{ghost_text};
}
QPushButton#ghostButton:hover {
    background: @{ghost_hover_bg};
    border-color: @{ghost_hover_border};
}
QPushButton#ghostButton:disabled {
    background: @{ghost_disabled_bg};
    border-color: @{ghost_disabled_border};
    color: @{ghost_disabled_text};
}
"""

ANNOUNCEMENT_TEMPLATE = """
QDialog {
    background: @{dialog_bg};
    color: @{dialog_text};
    font-family: "@{font_family}", "Microsoft YaHei UI", "Segoe UI";
    font-size: 14px;
}
QLabel#announcementTitle {
    color: @{dialog_title};
    font-size: 20px;
    font-weight: 700;
}
QLabel#dialogHint {
    color: @{dialog_hint};
    font-size: 12px;
}
QPlainTextEdit#announcementContent {
    background: transparent;
    border: none;
    border-radius: 0;
    color: @{dialog_body};
    padding: 2px;
    selection-background-color: @{selection_bg};
}
QCheckBox {
    color: @{checkbox_text};
    spacing: 8px;
    padding: 2px 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid @{checkbox_border};
    border-radius: 4px;
    background: @{checkbox_bg};
}
QCheckBox::indicator:hover {
    border: 1px solid @{checkbox_hover_border};
}
QCheckBox::indicator:checked {
    border: 1px solid @{checkbox_checked_border};
    background: @{checkbox_checked_bg};
    image: url("@{check_icon}");
}
QPushButton {
    min-height: 32px;
    border: 1px solid @{ghost_border};
    border-radius: 8px;
    background: transparent;
    color: @{dialog_text};
    padding: 4px 16px;
    outline: none;
}
QPushButton:hover {
    background: @{ghost_hover_bg};
    border-color: @{ghost_hover_border};
}
QPushButton#ghostIconButton {
    min-height: 0;
    padding: 0;
    border-radius: 8px;
    color: @{dialog_close};
    font-size: 15px;
}
QPushButton#ghostIconButton:hover {
    background: @{dialog_close_hover_bg};
    color: @{dialog_close_hover_text};
}
QFrame#divider {
    background: @{divider};
    border: none;
}
"""

ABOUT_EXTRA = """
QLabel#aboutName {
    color: @{dialog_title};
    font-size: 22px;
    font-weight: 700;
}
QLabel#aboutVersion {
    color: @{dialog_hint};
    font-size: 12px;
}
QLabel#aboutDescription {
    color: @{dialog_body};
    font-size: 13px;
}
QLabel#aboutLink {
    color: @{accent_light};
    font-size: 12px;
}
QLabel#aboutSection {
    color: @{section};
    font-size: 13px;
    font-weight: 600;
}
QPushButton#primaryButton {
    background: @{primary_bg};
    border: 1px solid @{primary_border};
    color: @{on_primary};
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: @{primary_hover};
    border-color: @{primary_hover_border};
}
QPushButton#primaryButton:pressed {
    background: @{primary_pressed};
}
QPushButton#primaryButton:disabled {
    background: @{primary_disabled_bg};
    border-color: @{primary_disabled_border};
    color: @{primary_disabled_text};
}
QPlainTextEdit#aboutAnnouncement {
    background: @{inner_bg};
    border: 1px solid @{inner_border};
    border-radius: 10px;
    color: @{dialog_body};
    padding: 8px;
    selection-background-color: @{selection_bg};
}
QProgressBar#updateProgress {
    min-height: 18px;
    max-height: 18px;
    border: none;
    border-radius: 5px;
    background: @{progress_bg};
    color: @{dialog_hint};
    text-align: center;
    font-size: 11px;
}
QProgressBar#updateProgress::chunk {
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 @{primary_bg}, stop:1 @{accent_light});
}
QPlainTextEdit#updateLog {
    background: @{inner_bg};
    border: 1px solid @{inner_border};
    border-radius: 10px;
    color: @{dialog_body};
    padding: 8px;
    selection-background-color: @{selection_bg};
}
"""

MESSAGE_TEMPLATE = """
QFrame#dialogCard {
    background: @{dialog_card_bg};
    border: 1px solid @{dialog_card_border};
    border-radius: 14px;
}
QLabel#dialogTitle {
    color: @{dialog_title};
    font-size: 16px;
    font-weight: 700;
}
QLabel#dialogBody {
    color: @{dialog_body};
    font-size: 14px;
}
QFrame#divider {
    background: @{divider};
    border: none;
}
QPushButton#dialogClose {
    border: none;
    background: transparent;
    color: @{dialog_close};
    font-size: 15px;
    border-radius: 6px;
}
QPushButton#dialogClose:hover {
    background: @{dialog_close_hover_bg};
    color: @{dialog_close_hover_text};
}
QPushButton#dialogPrimary {
    background: @{primary_bg};
    border: 1px solid @{primary_bg};
    border-radius: 8px;
    color: @{on_primary};
    font-size: 14px;
    font-weight: 600;
}
QPushButton#dialogPrimary:hover {
    background: @{primary_hover};
    border-color: @{primary_hover};
}
QPushButton#dialogPrimary:pressed {
    background: @{primary_pressed};
    border-color: @{primary_pressed};
}
QPushButton#dialogGhost {
    background: @{ghost_bg};
    border: 1px solid @{ghost_border};
    border-radius: 8px;
    color: @{ghost_text};
    font-size: 14px;
}
QPushButton#dialogGhost:hover {
    background: @{ghost_hover_bg};
}
"""


def main_qss(
    palette: Palette,
    font_family: str,
    *,
    background_path: Path | None = None,
    branch_closed_path: Path | None = None,
    branch_open_path: Path | None = None,
    arrow_down_path: Path | None = None,
) -> str:
    if background_path is not None and background_path.exists():
        main_bg = f'border-image: url("{background_path.as_posix()}") 0 0 0 0 stretch stretch;'
    else:
        main_bg = palette.main_bg
    arrow_down = (
        arrow_down_path.as_posix()
        if arrow_down_path is not None and arrow_down_path.exists()
        else ""
    )
    rules = ""
    if branch_closed_path is not None and branch_closed_path.exists():
        rules += (
            "\nQTreeWidget#saveTree::branch:has-children:closed {\n"
            f'    image: url("{branch_closed_path.as_posix()}");\n'
            "}\n"
        )
    if branch_open_path is not None and branch_open_path.exists():
        rules += (
            "\nQTreeWidget#saveTree::branch:has-children:open {\n"
            f'    image: url("{branch_open_path.as_posix()}");\n'
            "}\n"
        )
    return render(
        MAIN_TEMPLATE,
        palette,
        font_family=font_family,
        main_bg=main_bg,
        arrow_down=arrow_down,
    ) + rules


def settings_qss(
    palette: Palette,
    font_family: str,
    check_icon: str,
    arrow_down: str,
    arrow_up: str,
) -> str:
    return render(
        SETTINGS_TEMPLATE,
        palette,
        font_family=font_family,
        check_icon=check_icon,
        arrow_down=arrow_down,
        arrow_up=arrow_up,
    )


def announcement_qss(palette: Palette, font_family: str, check_icon: str) -> str:
    return render(
        ANNOUNCEMENT_TEMPLATE, palette, font_family=font_family, check_icon=check_icon
    )


def about_qss(palette: Palette, font_family: str, check_icon: str) -> str:
    return render(
        ANNOUNCEMENT_TEMPLATE + ABOUT_EXTRA,
        palette,
        font_family=font_family,
        check_icon=check_icon,
    )


def message_qss(palette: Palette) -> str:
    return render(MESSAGE_TEMPLATE, palette)


__all__ = [
    "Palette",
    "DARK",
    "LIGHT",
    "PALETTES",
    "palette_for",
    "main_qss",
    "settings_qss",
    "announcement_qss",
    "about_qss",
    "message_qss",
]
