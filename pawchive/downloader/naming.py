from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .models import DateNamingMode, FileItem, PostItem


IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif"}
_RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_filename(value: str, max_length: int = 150) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", value or "untitled")
    value = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    value = value.rstrip(" .")
    if not value:
        value = "untitled"
    value = value[:max_length].rstrip(" .") or "untitled"
    # Windows 的设备名（CON、NUL、COM1…）即使带扩展名也无法创建。
    if value.split(".", 1)[0].casefold() in _RESERVED_STEMS:
        value = f"_{value}"
    return value


def post_folder_name(post: PostItem, mode: DateNamingMode) -> str:
    title = sanitize_filename(post.title)
    if mode == DateNamingMode.PREFIX:
        return sanitize_filename(f"{post.day}_{title}")
    if mode == DateNamingMode.SUFFIX:
        return sanitize_filename(f"{title}_{post.day}")
    return title


def rename_images(files: list[FileItem]) -> list[FileItem]:
    images = [item for item in files if item.kind == "image"]
    digits = max(2, len(str(len(images)))) if images else 2
    image_index = 0
    renamed: list[FileItem] = []
    used_names: set[str] = set()

    for item in files:
        name = sanitize_filename(item.name)
        if item.kind == "image":
            image_index += 1
            ext = Path(name).suffix.lower() or ".jpg"
            name = f"{image_index:0{digits}d}{ext}"
        name = unique_name(name, used_names)
        renamed.append(FileItem(url=item.url, name=name, kind=item.kind, content=item.content))
    return renamed


def unique_name(name: str, used: set[str]) -> str:
    candidate = sanitize_filename(name)
    if candidate.casefold() not in used:
        used.add(candidate.casefold())
        return candidate

    stem = Path(candidate).stem
    suffix = Path(candidate).suffix
    index = 1
    while True:
        next_name = f"{stem}_{index}{suffix}"
        if next_name.casefold() not in used:
            used.add(next_name.casefold())
            return next_name
        index += 1


def _suffix(name: str) -> str:
    text = (name or "").strip()
    if "." not in text:
        return ""
    ext = "." + text.rsplit(".", 1)[1].lower()
    return ext if 0 < len(ext) <= 17 else ""


def _name_from_path(path: str) -> str:
    """从 /data/.../hash.psd?f=dn.psd 这类文件地址取可读文件名。"""
    try:
        parsed = urlparse(path or "")
    except ValueError:
        return ""
    for key in ("f", "filename", "name"):
        values = parse_qs(parsed.query).get(key)
        if values:
            candidate = unquote(values[0]).strip()
            if candidate:
                return candidate
    return unquote(Path(parsed.path).name)


def resolve_attachment_name(name: object, path: object = "") -> str:
    """补全附件名扩展名，避免接口只给标题/哈希时把 .psd 当成未知附件。"""
    raw_input = str(name or "").strip()
    raw = sanitize_filename(raw_input) if raw_input else ""
    candidate = _name_from_path(str(path or ""))
    raw_ext = _suffix(raw)
    candidate_ext = _suffix(candidate)
    if raw and not raw_ext:
        if candidate_ext:
            base = raw[: max(1, 150 - len(candidate_ext))].rstrip(" .")
            return sanitize_filename(f"{base}{candidate_ext}")
        if candidate:
            return sanitize_filename(candidate)
    if raw:
        return raw
    return sanitize_filename(candidate)


def is_image_name(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in IMAGE_EXTS

