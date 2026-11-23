from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DownloadTask, PostItem

SAVE_INTERVAL = 0.75
STATE_DIR_NAME = "State"
_SAVE_LOCK = threading.Lock()


def legacy_state_dir() -> Path:
    """旧版状态目录，仅用于一次性迁移，不会创建目录。"""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "PawchiveDownloader" / "state"


def state_dir_for(output_dir: Path | str | None) -> Path | None:
    """返回 ``<保存根>/Temp/State``；保存根必须是绝对路径。"""
    if not output_dir:
        return None
    root = Path(output_dir)
    if not root.is_absolute():
        return None
    return root / "Temp" / STATE_DIR_NAME


def migrate_legacy_state(target: Path) -> int:
    """把旧 AppData 的 state 记录搬到新目录。

    只移动 json；目标已有同名文件时保留目标，不覆盖。旧目录搬空后删除。
    """
    target = Path(target)
    source = legacy_state_dir()
    if not source.is_dir() or source == target:
        return 0
    moved = 0
    for item in sorted(source.glob("*.json"), key=lambda path: path.name.casefold()):
        destination = target / item.name
        if destination.exists():
            continue
        try:
            shutil.move(str(item), str(destination))
            moved += 1
        except OSError:
            continue
    try:
        source.rmdir()
    except OSError:
        pass
    return moved


def ensure_state_dir(output_dir: Path | str | None, *, migrate_legacy: bool = True) -> Path | None:
    """创建新的 State 目录，并默认迁移旧版 AppData 下载记录。"""
    target = state_dir_for(output_dir)
    if target is None:
        return None
    target.mkdir(parents=True, exist_ok=True)
    if migrate_legacy:
        migrate_legacy_state(target)
    return target


class StateStore:
    def __init__(self, creator_key: str, root: Path | None = None):
        if root is None:
            raise ValueError("StateStore 需要明确的 Temp/State 目录")
        safe_key = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in creator_key)
        self.path = Path(root) / f"{safe_key}.json"
        self.data: dict[str, Any] = self._load()
        self._dirty = False
        self._last_save = 0.0

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "posts": {}}
        try:
            with _SAVE_LOCK:
                return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "posts": {}}

    def save(self) -> None:
        """原子写入整份状态；同目录唯一临时文件，避免崩溃留下半截 JSON。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _SAVE_LOCK:
            fd, temp_name = tempfile.mkstemp(
                prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(self.data, ensure_ascii=False))
                    handle.flush()
                    os.fsync(handle.fileno())
                for attempt in range(5):
                    try:
                        os.replace(temp_name, self.path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.02)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
        self._last_save = time.monotonic()
        self._dirty = False

    def _touch(self) -> None:
        """高频 mark 时最多每 SAVE_INTERVAL 秒落一次盘，避免 O(n²) 写放大。"""
        self._dirty = True
        if time.monotonic() - self._last_save >= SAVE_INTERVAL:
            self.save()

    def flush(self) -> None:
        if self._dirty:
            self.save()

    def is_post_finished(self, post: PostItem) -> bool:
        return self.data.get("posts", {}).get(post.id, {}).get("status") == "finished"

    def is_file_finished(self, task: DownloadTask) -> bool:
        post = self.data.get("posts", {}).get(task.post.id, {})
        file_state = post.get("files", {}).get(task.file.name, {})
        return file_state.get("status") == "finished" and task.save_path.exists()

    def mark_file(self, task: DownloadTask, status: str, error: str | None = None) -> None:
        post_state = self._post_state(task.post)
        file_state = {
            "status": status,
            "url": task.file.url,
            "path": str(task.save_path),
            "kind": task.file.kind,
            "updated_at": now_text(),
        }
        try:
            if task.save_path.is_file():
                file_state["size"] = task.save_path.stat().st_size
        except OSError:
            pass
        if error:
            file_state["error"] = error
        post_state.setdefault("files", {})[task.file.name] = file_state
        self._touch()

    def mark_post(self, post: PostItem, status: str) -> None:
        post_state = self._post_state(post)
        post_state["status"] = status
        post_state["updated_at"] = now_text()
        self._touch()

    def _post_state(self, post: PostItem) -> dict[str, Any]:
        posts = self.data.setdefault("posts", {})
        return posts.setdefault(
            post.id,
            {
                "status": "pending",
                "title": post.title,
                "url": post.url,
                "published": post.published,
                "files": {},
            },
        )


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

