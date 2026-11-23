from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from pawchive.api.pawchive import PawchiveApi, normalize_creator_path
from pawchive.settings import AppSettings, resolve_temp_root

from .models import DateNamingMode, DownloadTask, PostItem
from .service import DownloadService


def _path_key(path: str | Path) -> str:
    try:
        text = os.path.realpath(os.path.abspath(os.fspath(path)))
    except Exception:
        text = os.path.abspath(os.fspath(path))
    return os.path.normcase(text)


QUEUE_STATUS_LABELS = {
    "waiting": "待下载",
    "preparing": "预建中",
    "ready": "已预建",
    "running": "下载中",
    "completed": "已完成",
    "failed": "有失败",
    "stopped": "已停止",
    "error": "出错",
}


def queue_key(post: PostItem, output_dir: Path) -> tuple[str, str, str, str]:
    return (
        str(post.service),
        str(post.user_id),
        str(post.id),
        _path_key(output_dir),
    )


@dataclass(slots=True)
class QueueItem:
    post: PostItem
    creator_url: str
    output_dir: Path
    program_root: Path
    settings: AppSettings
    naming_mode: DateNamingMode = DateNamingMode.PREFIX
    precreate: bool = True
    status: str = "waiting"
    tasks: list[DownloadTask] | None = None
    error: str = ""
    progress_done: int = 0
    progress_total: int = 0
    progress_failed: int = 0

    @property
    def key(self) -> tuple[str, str, str, str]:
        return queue_key(self.post, self.output_dir)

    @property
    def file_count(self) -> int:
        return len(self.tasks or ())

    @property
    def status_label(self) -> str:
        return QUEUE_STATUS_LABELS.get(self.status, self.status)

    @property
    def post_folder(self) -> Path | None:
        if self.tasks:
            return self.tasks[0].save_path.parent
        return None

    @property
    def state_key(self) -> str:
        return queue_state_key(self.creator_url, self.output_dir, self.program_root)

    @property
    def state_root(self) -> Path:
        return self.program_root / "Temp" / "State"


def queue_state_key(creator_url: str, output_dir: Path, program_root: Path) -> str:
    key = f"pawchive_{normalize_creator_path(creator_url)}"
    if _path_key(output_dir) != _path_key(program_root):
        digest = hashlib.sha1(
            _path_key(output_dir).encode("utf-8", "ignore")
        ).hexdigest()[:10]
        key = f"{key}_{digest}"
    return key


async def build_queue_tasks(
    item: QueueItem,
    api: PawchiveApi,
    *,
    write_marker: bool = False,
) -> list[DownloadTask]:
    """读取帖子详情并整理任务；默认不写 .post_id，避免空目录被当成已下载。"""
    service = DownloadService(
        api,
        temp_root=resolve_temp_root(item.settings, item.output_dir),
    )
    tasks = await service.build_post_tasks(
        item.post,
        item.output_dir,
        item.naming_mode,
        write_marker=write_marker,
    )
    item.tasks = tasks
    item.status = "ready" if item.precreate else "waiting"
    item.error = ""
    item.progress_done = 0
    item.progress_total = len(tasks)
    item.progress_failed = 0
    return tasks


def ensure_post_marker(item: QueueItem) -> None:
    """开始实际下载时才写 .post_id，让保存目录只显示真正开始下载的帖子。"""
    folder = item.post_folder
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / ".post_id"
        if not marker.exists():
            marker.write_text(item.post.id, encoding="utf-8")
        try:
            (folder / ".pawchive_queue").unlink(missing_ok=True)
        except OSError:
            pass
    except OSError:
        return


def create_temp_placeholders(tasks: list[DownloadTask]) -> int:
    """为任务建立空白 .part 占位；已有非空临时文件不会被截断。"""
    created = 0
    for task in tasks:
        if task.file.kind != "text" and task.save_path.exists() and task.save_path.stat().st_size > 0:
            continue
        try:
            task.temp_path.parent.mkdir(parents=True, exist_ok=True)
            if not task.temp_path.exists():
                task.temp_path.touch()
                created += 1
        except OSError:
            continue
    return created


def remove_temp_placeholders(tasks: list[DownloadTask] | None) -> int:
    """只删除空白 .part 占位，保留已经写入内容的断点缓存。"""
    removed = 0
    parents: set[Path] = set()
    post_dirs: set[Path] = set()
    for task in tasks or ():
        try:
            if not task.temp_path.exists():
                continue
            if task.temp_path.stat().st_size == 0 and not task.save_path.exists():
                task.temp_path.unlink()
                removed += 1
            parents.add(task.temp_path.parent)
        except OSError:
            continue
        post_dirs.add(task.save_path.parent)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass
    for post_dir in post_dirs:
        if not post_dir.is_dir() or (post_dir / ".post_id").exists():
            continue
        try:
            (post_dir / ".pawchive_queue").unlink(missing_ok=True)
            for child in post_dir.iterdir():
                break
            else:
                post_dir.rmdir()
        except OSError:
            pass
    return removed
