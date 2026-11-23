from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path


class ThumbnailCache:
    """在线预览图的磁盘 LRU 缓存；容量按字节计，0 表示关闭。"""

    def __init__(self, directory: Path | None = None, max_bytes: int = 0):
        self._lock = threading.RLock()
        self._directory = Path(directory) if directory is not None else None
        self._max_bytes = max(0, int(max_bytes))

    @property
    def directory(self) -> Path | None:
        return self._directory

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def configure(self, directory: Path | None, max_bytes: int) -> None:
        with self._lock:
            self._directory = Path(directory) if directory is not None else None
            self._max_bytes = max(0, int(max_bytes))
            if self._max_bytes <= 0:
                self._remove_all()
            else:
                self._trim()

    def _path_for(self, url: str) -> Path | None:
        if self._directory is None or self._max_bytes <= 0:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._directory / f"{digest}.img"

    def get(self, url: str) -> bytes | None:
        with self._lock:
            path = self._path_for(url)
            if path is None:
                return None
            try:
                data = path.read_bytes()
            except OSError:
                return None
            if not data or len(data) > self._max_bytes:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            try:
                os.utime(path, None)
            except OSError:
                pass
            return data

    def put(self, url: str, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            path = self._path_for(url)
            if path is None:
                return
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(
                    prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
                )
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, path)
                except Exception:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
                    raise
            except OSError:
                return
            self._trim()

    def invalidate(self, url: str) -> None:
        with self._lock:
            path = self._path_for(url)
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def clear(self) -> int:
        with self._lock:
            return self._remove_all()

    def size_bytes(self) -> int:
        with self._lock:
            if self._directory is None:
                return 0
            total = 0
            try:
                for path in self._directory.glob("*.img"):
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
            except OSError:
                return 0
            return total

    def _trim(self) -> None:
        if self._directory is None:
            return
        try:
            files = [path for path in self._directory.glob("*.img") if path.is_file()]
        except OSError:
            return
        entries: list[tuple[float, int, Path]] = []
        for path in files:
            try:
                stat = path.stat()
                entries.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue
        total = sum(item[1] for item in entries)
        for _, size, path in sorted(entries):
            if total <= self._max_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                continue

    def _remove_all(self) -> int:
        if self._directory is None:
            return 0
        removed = 0
        try:
            for path in self._directory.glob("*.img"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError:
            return removed
        return removed
