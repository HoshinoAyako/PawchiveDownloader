"""界面主题解析：跟随系统 / 浅色 / 深色，含 Windows 标题栏配色。"""

from __future__ import annotations

import ctypes
import sys

THEME_KEYS = ("system", "light", "dark")
THEME_LABELS = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
DEFAULT_THEME = "dark"


def system_theme() -> str:
    """读取 Windows“应用模式”偏好；其它平台按深色处理。"""
    if sys.platform != "win32":
        return "dark"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except Exception:
        return "dark"


def resolve_theme(preference: str) -> str:
    preference = preference if preference in THEME_KEYS else DEFAULT_THEME
    return system_theme() if preference == "system" else preference


def _colorref(hex_color: str) -> int:
    """#RRGGBB -> Windows COLORREF (0x00BBGGRR)。"""
    text = hex_color.lstrip("#")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_title_bar(window, theme: str) -> None:
    """让原生标题栏跟随主题；自绘标题栏的无边框窗口无需处理。"""
    if sys.platform != "win32":
        return
    try:
        from PyQt6.QtCore import Qt

        if window.windowFlags() & Qt.WindowType.FramelessWindowHint:
            return
        hwnd = int(window.winId())
    except Exception:
        return

    dark = 1 if theme == "dark" else 0
    caption = "#101021" if theme == "dark" else "#F7F8FC"
    text = "#FFFFFF" if theme == "dark" else "#1C2333"
    border = "#26263D" if theme == "dark" else "#D8DDE8"

    try:
        dwm = ctypes.windll.dwmapi
    except Exception:
        return

    def set_attr(attribute: int, value: int) -> bool:
        data = ctypes.c_int(value)
        result = dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(data),
            ctypes.sizeof(data),
        )
        return result == 0

    # Win10 1809+ 的深色标题栏开关。
    if not set_attr(20, dark):
        set_attr(19, dark)
    # Win11 的自定义标题栏颜色/text/边框；不支持的版本会静默失败。
    if int(window.winId()) and sys.getwindowsversion().build >= 22000:
        set_attr(35, _colorref(caption))
        set_attr(36, _colorref(text))
        set_attr(34, _colorref(border))
