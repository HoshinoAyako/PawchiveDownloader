"""保存目录预览与完整性校验。

只把带 ``.post_id`` 标记的目录视为本程序生成的帖子目录；校验时结合
``<保存根>/Temp/State`` 里的下载记录，检查文件是否缺失、大小是否变化，
并对图片做一次完整解码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtGui import QImageReader

from kemono_downloader.downloader.state import state_dir_for
from kemono_downloader.settings import IMAGE_EXTENSIONS

MAX_IMAGE_DEEP_CHECK = 96 * 1024 * 1024

STATUS_LABELS = {
    "ok": "正常",
    "empty": "空文件",
    "corrupt": "损坏",
    "incomplete": "未完成",
    "missing": "缺失",
    "size_mismatch": "大小不符",
}


def path_key(path: str | Path) -> str:
    """用于跨 8.3 短路径/长路径比较的规范键。"""
    try:
        text = os.path.realpath(os.path.abspath(os.fspath(path)))
    except Exception:
        text = os.path.abspath(os.fspath(path))
    return os.path.normcase(text)


def is_under(path: str | Path, root_key: str) -> bool:
    key = path_key(path)
    return key == root_key or key.startswith(root_key + os.sep)


@dataclass(slots=True)
class SavedFile:
    path: Path
    size: int
    status: str = ""
    detail: str = ""


@dataclass(slots=True)
class SavedPost:
    path: Path
    files: list[SavedFile] = field(default_factory=list)
    missing_dir: bool = False

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def issue_count(self) -> int:
        return sum(1 for item in self.files if item.status and item.status != "ok")


@dataclass(slots=True)
class SaveScan:
    posts: list[SavedPost] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    truncated: bool = False


@dataclass(slots=True)
class VerifyResult:
    scan: SaveScan
    ok_count: int = 0
    issue_count: int = 0
    missing_count: int = 0
    empty_count: int = 0
    corrupt_count: int = 0
    incomplete_count: int = 0
    size_mismatch_count: int = 0
    has_records: bool = False


def scan_save_root(root: Path, max_files: int = 2000) -> SaveScan:
    """预览保存目录：每个帖子目录及其直接文件，不做文件内容检查。"""
    result = SaveScan()
    if not root.is_dir():
        return result

    shown = 0
    for post_dir in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not post_dir.is_dir():
            continue
        if not (post_dir / ".post_id").is_file():
            continue
        post = SavedPost(path=post_dir)
        result.posts.append(post)
        try:
            children = sorted(post_dir.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for child in children:
            if not child.is_file() or child.name == ".post_id":
                continue
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            result.total_files += 1
            result.total_bytes += size
            if shown < max_files:
                post.files.append(SavedFile(path=child, size=size))
                shown += 1
            else:
                result.truncated = True
    return result


def load_expected_files(root: Path, state_dir: Path | None = None) -> dict[str, dict]:
    """读取状态目录里的完成记录，返回 ``{规范路径: {path,size,...}}``。

    只接受位于当前保存目录下的 finished 记录。
    """
    root_key = path_key(root)
    expected: dict[str, dict] = {}
    directory = Path(state_dir) if state_dir is not None else state_dir_for(root)
    if directory is None or not directory.is_dir():
        return expected

    for state_file in sorted(directory.glob("*.json"), key=lambda item: item.name.casefold()):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        posts = data.get("posts") if isinstance(data, dict) else None
        if not isinstance(posts, dict):
            continue
        for post in posts.values():
            if not isinstance(post, dict):
                continue
            files = post.get("files")
            if not isinstance(files, dict):
                continue
            for name, meta in files.items():
                if not isinstance(meta, dict):
                    continue
                if meta.get("status") != "finished":
                    continue
                raw_path = meta.get("path")
                if not raw_path:
                    continue
                try:
                    file_path = Path(str(raw_path))
                except Exception:
                    continue
                if not is_under(file_path, root_key):
                    continue
                key = path_key(file_path)
                size = meta.get("size")
                if not isinstance(size, int) or size < 0:
                    size = None
                expected[key] = {
                    "path": file_path,
                    "size": size,
                    "name": str(name),
                }
    return expected


def _deep_check(file: SavedFile) -> tuple[str, str]:
    suffix = file.path.suffix.lower()
    if file.size == 0:
        return "empty", "文件大小为 0"
    if suffix in (".part", ".movepart"):
        return "incomplete", "下载未完成的临时残留"

    if suffix in IMAGE_EXTENSIONS:
        supported = {
            bytes(item).decode("ascii", "ignore").lower()
            for item in QImageReader.supportedImageFormats()
        }
        if suffix.lstrip(".") not in supported:
            return "ok", "图片格式未内置解码器，仅检查大小"
        if file.size > MAX_IMAGE_DEEP_CHECK:
            return "ok", "图片超过 96 MB，仅检查大小"
        reader = QImageReader(str(file.path))
        reader.setAutoTransform(False)
        if not reader.canRead():
            return "corrupt", "无法识别图片头或格式不完整"
        image = reader.read()
        if image.isNull():
            detail = reader.errorString() or "解码失败"
            return "corrupt", detail[:120]
        return "ok", ""

    if file.path.name.lower() == "content.txt":
        try:
            sample = file.path.read_bytes()[:4096].decode("utf-8")
        except Exception:
            return "corrupt", "正文不是有效的 UTF-8 文本"
        if not sample.strip():
            return "empty", "正文内容为空"
    return "ok", ""


def verify_save_root(
    root: Path,
    max_files: int = 2000,
    state_dir: Path | None = None,
) -> VerifyResult:
    """检查保存目录：文件结构、图片解码、状态记录里的缺失与大小变化。"""
    scan = scan_save_root(root, max_files=max_files)
    expected = load_expected_files(root, state_dir=state_dir)
    result = VerifyResult(scan=scan, has_records=bool(expected))

    post_by_key = {path_key(post.path): post for post in scan.posts}
    file_by_key: dict[str, SavedFile] = {}
    for post in scan.posts:
        for item in post.files:
            file_by_key[path_key(item.path)] = item

    for post in scan.posts:
        for item in post.files:
            status, detail = _deep_check(item)
            item.status = status
            item.detail = detail

    # 状态记录里存在、但磁盘上已经不存在的文件，补成“缺失”节点。
    for key, meta in expected.items():
        file_path: Path = meta["path"]
        expected_size = meta["size"]
        item = file_by_key.get(key)
        if item is not None:
            if expected_size is not None and item.status == "ok" and item.size != expected_size:
                item.status = "size_mismatch"
                item.detail = f"记录 {expected_size} 字节，实际 {item.size} 字节"
            continue

        # 文件可能因为预览条数上限没有进入树；只要还在磁盘上，就不算缺失。
        if file_path.exists():
            continue

        parent_key = path_key(file_path.parent)
        post = post_by_key.get(parent_key)
        if post is None:
            post = SavedPost(path=file_path.parent, missing_dir=not file_path.parent.exists())
            scan.posts.append(post)
            post_by_key[parent_key] = post
        missing = SavedFile(
            path=file_path,
            size=expected_size or 0,
            status="missing",
            detail="下载记录中有，磁盘上不存在",
        )
        post.files.append(missing)
        file_by_key[key] = missing

    for post in scan.posts:
        post.files.sort(key=lambda item: item.path.name.casefold())

    result.ok_count = 0
    for post in scan.posts:
        for item in post.files:
            if item.status == "ok":
                result.ok_count += 1
            else:
                result.issue_count += 1
                if item.status == "missing":
                    result.missing_count += 1
                elif item.status == "empty":
                    result.empty_count += 1
                elif item.status == "corrupt":
                    result.corrupt_count += 1
                elif item.status == "incomplete":
                    result.incomplete_count += 1
                elif item.status == "size_mismatch":
                    result.size_mismatch_count += 1
    return result
