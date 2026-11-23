"""临时目录相关的小工具：迁移与枚举。"""

from __future__ import annotations

import shutil
from pathlib import Path


def migrate_program_root(old_root: Path, new_root: Path) -> tuple[int, list[str]]:
    """整体迁移 PawchiveDownload：帖子、Config、Temp 一起移动。

    目标必须不存在或为空目录；不覆盖已有程序目录，避免把两份下载记录混在一起。
    """
    old_root = Path(old_root)
    new_root = Path(new_root)
    if old_root == new_root or not old_root.exists():
        return 0, []
    if new_root.is_relative_to(old_root):
        raise ValueError("新的程序目录不能放在旧程序目录里面")
    if old_root.is_relative_to(new_root):
        raise ValueError("旧程序目录位于新目录内部，无法整体迁移")

    if new_root.exists():
        if not new_root.is_dir():
            raise ValueError(f"目标位置已有同名文件：{new_root}")
        try:
            next(new_root.iterdir())
        except StopIteration:
            new_root.rmdir()
        else:
            raise ValueError(f"目标位置已有非空的 PawchiveDownload：{new_root}")

    moved = sum(1 for item in old_root.rglob("*") if item.is_file())
    new_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(old_root), str(new_root))
    except Exception as exc:
        return moved, [f"{old_root} -> {new_root}: {exc}"]
    return moved, []


def iter_part_files(root: Path) -> list[tuple[Path, int]]:
    """列出临时根目录里尚未落盘的 .part 文件（只扫帖级子目录一层）。"""
    if not root.exists():
        return []
    result: list[tuple[Path, int]] = []
    for item in root.glob("*/*.part"):
        try:
            if item.is_file():
                result.append((item, item.stat().st_size))
        except OSError:
            continue
    return sorted(result, key=lambda pair: str(pair[0]).casefold())


def iter_temp_files(root: Path) -> list[tuple[Path, int]]:
    """兼容旧调用：只返回可清理的 .part 文件。"""
    return iter_part_files(root)


def iter_temp_entries(root: Path) -> list[tuple[Path, int, str]]:
    """列出预览项：.part 临时文件和 State 下的下载记录。

    返回 ``(路径, 字节, 类型)``，类型为 ``part`` 或 ``record``。
    记录排在前，方便确认某个创作者是否已有可续传状态。
    """
    if not root.exists():
        return []
    entries: list[tuple[Path, int, str]] = []
    for path, size in iter_part_files(root):
        entries.append((path, size, "part"))
    state_dir = root / "State"
    if state_dir.is_dir():
        for item in state_dir.glob("*.json"):
            try:
                if item.is_file():
                    entries.append((item, item.stat().st_size, "record"))
            except OSError:
                continue
    return sorted(
        entries,
        key=lambda entry: (entry[2] != "record", str(entry[0]).casefold()),
    )


def preview_cache_dir(root: Path) -> Path:
    return Path(root) / "PreviewCache"


def iter_preview_cache_files(root: Path) -> list[tuple[Path, int]]:
    """列出预览图缓存文件；缓存目录只保存哈希命名的图片块。"""
    directory = preview_cache_dir(root)
    if not directory.is_dir():
        return []
    result: list[tuple[Path, int]] = []
    for item in directory.iterdir():
        try:
            if item.is_file():
                result.append((item, item.stat().st_size))
        except OSError:
            continue
    return sorted(result, key=lambda pair: str(pair[0]).casefold())


def iter_state_files(root: Path) -> list[tuple[Path, int]]:
    """列出 Temp/State 下的下载记录，包含写入中断留下的临时文件。"""
    directory = Path(root) / "State"
    if not directory.is_dir():
        return []
    result: list[tuple[Path, int]] = []
    for pattern in ("*.json", "*.tmp"):
        for item in directory.glob(pattern):
            try:
                if item.is_file():
                    result.append((item, item.stat().st_size))
            except OSError:
                continue
    return sorted(result, key=lambda pair: str(pair[0]).casefold())


def clear_preview_cache(root: Path) -> tuple[int, int, list[str]]:
    """删除 PreviewCache 下的缓存文件，返回 (删除数, 释放字节, 错误列表)。"""
    return _clear_entries(iter_preview_cache_files(root))


def clear_state_files(root: Path) -> tuple[int, int, list[str]]:
    """删除 Temp/State 下的下载记录，返回 (删除数, 释放字节, 错误列表)。"""
    return _clear_entries(iter_state_files(root))


def _clear_entries(entries: list[tuple[Path, int]]) -> tuple[int, int, list[str]]:
    deleted = 0
    freed = 0
    errors: list[str] = []
    for path, size in entries:
        try:
            path.unlink()
            deleted += 1
            freed += size
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return deleted, freed, errors


def clear_temp_root(root: Path) -> tuple[int, int, list[str]]:
    """删除临时根下的 .part 文件，返回 (删除数, 释放字节, 错误列表)。

    只删除 ``<root>/<帖子目录>/*.part``，然后尝试移除变空的帖子目录；
    临时根目录与 ``State`` 下载记录本身保留。
    """
    deleted = 0
    freed = 0
    errors: list[str] = []
    touched_dirs: set[Path] = set()
    for path, size in iter_part_files(root):
        try:
            path.unlink()
            deleted += 1
            freed += size
            touched_dirs.add(path.parent)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    for directory in sorted(touched_dirs, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return deleted, freed, errors

