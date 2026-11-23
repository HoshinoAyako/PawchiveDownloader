"""读取 CHANGELOG.md 中当前版本的更新内容，供版本更新公告使用。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HEADING_RE = re.compile(r"^##\s+([0-9]+(?:\.[0-9]+)+)\b.*$", re.MULTILINE)


def changelog_candidates() -> list[Path]:
    """源码运行与 PyInstaller 单文件运行时的 CHANGELOG 查找位置。"""
    here = Path(__file__).resolve()
    candidates = [here.parents[1] / "CHANGELOG.md"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "CHANGELOG.md")
    return candidates


def release_notes_for(version: str, changelog: Path | None = None) -> str:
    """返回指定版本的 CHANGELOG 正文；找不到时返回空字符串。"""
    paths = [changelog] if changelog is not None else changelog_candidates()
    text = ""
    for path in paths:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                break
        except OSError:
            continue
    if not text:
        return ""

    matches = list(_HEADING_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1) != version:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def version_announcement_text(version: str, changelog: Path | None = None) -> str:
    notes = release_notes_for(version, changelog)
    if not notes:
        return ""
    return f"v{version} 版本更新\n\n{notes}"


__all__ = ["changelog_candidates", "release_notes_for", "version_announcement_text"]
