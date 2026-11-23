"""检查并替换预览图为原图。

只处理 State 中记录了 ``img.pawchive.pw`` 缩略图地址、且文件仍在的图片。
远端用 Range 请求探测原图大小，确认原图确实更大后才提示替换。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import aiofiles
import aiohttp

from kemono_downloader.api.kemono import (
    DATA_BASE,
    DEFAULT_HEADERS,
    PREVIEW_DATA_BASE,
)
from kemono_downloader.downloader.engine import replace_final_file
from kemono_downloader.net import build_network_options
from kemono_downloader.settings import AppSettings, IMAGE_EXTENSIONS
from kemono_downloader.verify import is_under, path_key


@dataclass(slots=True)
class PreviewCandidate:
    post_id: str
    path: Path
    preview_url: str
    original_url: str
    local_size: int
    remote_size: int = 0
    state_file: Path | None = None


@dataclass(slots=True)
class PreviewAuditResult:
    checked: int = 0
    candidates: list[PreviewCandidate] | None = None
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []
        if self.errors is None:
            self.errors = []

    @property
    def total_bytes(self) -> int:
        return sum(item.remote_size for item in self.candidates or [])


def original_url_for_preview(url: str) -> str:
    text = (url or "").strip()
    if text.startswith(PREVIEW_DATA_BASE):
        return DATA_BASE + text[len(PREVIEW_DATA_BASE) :]
    if "img.pawchive.pw/thumbnail/data" in text:
        return DATA_BASE + text.split("/thumbnail/data", 1)[1]
    return ""


def collect_preview_candidates(
    root: Path,
    state_dir: Path | None,
) -> list[PreviewCandidate]:
    """从下载记录中收集位于当前保存目录的预览图。"""
    if state_dir is None or not state_dir.is_dir():
        return []
    root_key = path_key(root)
    result: list[PreviewCandidate] = []
    seen: set[str] = set()
    for state_file in sorted(state_dir.glob("*.json"), key=lambda item: item.name.casefold()):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        posts = data.get("posts") if isinstance(data, dict) else None
        if not isinstance(posts, dict):
            continue
        for post_id, post in posts.items():
            if not isinstance(post, dict):
                continue
            files = post.get("files")
            if not isinstance(files, dict):
                continue
            for _name, meta in files.items():
                if not isinstance(meta, dict) or meta.get("status") != "finished":
                    continue
                if meta.get("kind") != "image":
                    continue
                raw_path = meta.get("path")
                raw_url = meta.get("url")
                if not raw_path or not raw_url:
                    continue
                file_path = Path(str(raw_path))
                if not is_under(file_path, root_key) or not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                original = original_url_for_preview(str(raw_url))
                if not original:
                    continue
                key = path_key(file_path)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    local_size = file_path.stat().st_size
                except OSError:
                    local_size = 0
                result.append(
                    PreviewCandidate(
                        post_id=str(post_id),
                        path=file_path,
                        preview_url=str(raw_url),
                        original_url=original,
                        local_size=local_size,
                        state_file=state_file,
                    )
                )
    return result


def _remote_size_from_headers(status: int, headers: dict[str, str]) -> int:
    content_range = headers.get("content-range", "")
    match = re.match(r"bytes\s+\d+-\d+/(\d+)", content_range)
    if match:
        return int(match.group(1))
    length = headers.get("content-length", "")
    if status in (200, 206) and length.isdigit():
        return int(length)
    return 0


async def audit_preview_images(
    root: Path,
    state_dir: Path | None,
    settings: AppSettings,
    concurrency: int = 1,
) -> PreviewAuditResult:
    candidates = collect_preview_candidates(root, state_dir)
    result = PreviewAuditResult(checked=len(candidates))
    if not candidates:
        return result

    semaphore = asyncio.Semaphore(max(1, min(8, int(concurrency))))
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        **build_network_options(settings),
    ) as session:

        async def check(item: PreviewCandidate) -> None:
            async with semaphore:
                try:
                    async with session.get(
                        item.original_url,
                        headers={"Range": "bytes=0-0"},
                    ) as resp:
                        if resp.status not in (200, 206):
                            result.errors.append(f"{item.path.name}: HTTP {resp.status}")
                            return
                        headers = {key.lower(): value for key, value in resp.headers.items()}
                        remote = _remote_size_from_headers(resp.status, headers)
                        await resp.read()
                except Exception as exc:
                    result.errors.append(f"{item.path.name}: {exc}")
                    return
                finally:
                    # 站点明确要求约 1 次/秒，检查也不该并发轰炸文件域名。
                    await asyncio.sleep(0.9)
                item.remote_size = remote
                if remote > item.local_size > 0:
                    result.candidates.append(item)

        await asyncio.gather(*(check(item) for item in candidates))
    return result


def _update_state_file(candidate: PreviewCandidate) -> None:
    if candidate.state_file is None:
        return
    try:
        data = json.loads(candidate.state_file.read_text(encoding="utf-8"))
    except Exception:
        return
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, dict):
        return
    wanted = path_key(candidate.path)
    for post in posts.values():
        if not isinstance(post, dict):
            continue
        for meta in (post.get("files") or {}).values():
            if not isinstance(meta, dict) or not meta.get("path"):
                continue
            if path_key(Path(str(meta["path"]))) != wanted:
                continue
            meta["url"] = candidate.original_url
            meta["size"] = candidate.remote_size
            meta["source"] = "original"
            temp_name = ""
            try:
                fd, temp_name = tempfile.mkstemp(
                    prefix=candidate.state_file.name + ".", suffix=".tmp",
                    dir=str(candidate.state_file.parent),
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(data, ensure_ascii=False))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, candidate.state_file)
            except OSError:
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
            return


async def replace_preview_images(
    candidates: list[PreviewCandidate],
    temp_root: Path,
    settings: AppSettings,
    progress: Callable[[int, int], None] | None = None,
    concurrency: int = 1,
) -> tuple[int, list[str]]:
    """下载原图并原子替换预览图，返回（成功数, 错误列表）。"""
    if not candidates:
        return 0, []
    semaphore = asyncio.Semaphore(max(1, min(5, int(concurrency))))
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    done = 0
    errors: list[str] = []
    total = len(candidates)

    async def replace_one(item: PreviewCandidate) -> None:
        nonlocal done
        async with semaphore:
            temp_dir = temp_root / "Replace"
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in item.path.name)
            temp_path = temp_dir / f"{item.post_id}_{safe_name}.part"
            temp_path.unlink(missing_ok=True)
            try:
                async with session.get(item.original_url) as resp:
                    if resp.status not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status}")
                    async with aiofiles.open(temp_path, "wb") as handle:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            if chunk:
                                await handle.write(chunk)
                if temp_path.stat().st_size <= 0:
                    raise RuntimeError("下载结果为空")
                replace_final_file(temp_path, item.path)
                item.remote_size = item.path.stat().st_size
                _update_state_file(item)
            except Exception as exc:
                errors.append(f"{item.path.name}: {exc}")
                temp_path.unlink(missing_ok=True)
            finally:
                done += 1
                if progress is not None:
                    progress(done, total)
                await asyncio.sleep(0.9)

    async with aiohttp.ClientSession(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        **build_network_options(settings),
    ) as session:
        await asyncio.gather(*(replace_one(item) for item in candidates))
    return total - len(errors), errors
