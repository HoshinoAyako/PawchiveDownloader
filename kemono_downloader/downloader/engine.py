from __future__ import annotations

import asyncio
import errno
import inspect
import os
import re
import shutil
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterable, Awaitable, Callable

import aiofiles
import aiohttp

from .models import DownloadEvent, DownloadTask
from .state import StateStore
from ..net import build_network_options
from ..settings import AppSettings


# Pawchive 的文件 CDN 会拦截伪装成浏览器的 UA（返回 403），要求可识别的 UA。
USER_AGENT = "PawchiveDownloader/1.0 (+https://github.com/slbidd/KemonoDownloaderGUI)"
DOWNLOAD_HEADERS = {"User-Agent": USER_AGENT}
EventCallback = Callable[[DownloadEvent], None | Awaitable[None]]


@dataclass
class ProgressTracker:
    """O(1) 维护全局与单帖进度，避免每个文件都全量 sum 计数器。"""

    total: int = 0
    done: int = 0
    failed: int = 0
    post_total: Counter[str] = field(default_factory=Counter)
    post_done: Counter[str] = field(default_factory=Counter)
    post_failed: Counter[str] = field(default_factory=Counter)
    _settled: set[tuple[str, str, str]] = field(default_factory=set)

    def add_tasks(self, tasks: list[DownloadTask]) -> None:
        self.total += len(tasks)
        for task in tasks:
            self.post_total[task.post.id] += 1

    def _key(self, task: DownloadTask) -> tuple[str, str, str]:
        # 不用 id(task)：任务对象会被回收，id 可能被新任务复用，导致漏计数。
        return (task.post.id, task.file.name, str(task.save_path))

    def mark(self, task: DownloadTask, status: str) -> bool:
        key = self._key(task)
        if key in self._settled:
            return False
        self._settled.add(key)
        self.done += 1
        self.post_done[task.post.id] += 1
        if status == "failed":
            self.failed += 1
            self.post_failed[task.post.id] += 1
        return True

    def is_post_complete(self, task: DownloadTask) -> bool:
        return self.post_done[task.post.id] >= self.post_total[task.post.id]

    def post_failed_count(self, task: DownloadTask) -> int:
        return self.post_failed[task.post.id]


class DownloadEngine:
    def __init__(
        self,
        concurrency: int = 8,
        retries: int = 3,
        chunk_size: int = 1024 * 1024,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        settings: AppSettings | None = None,
    ):
        self.concurrency = max(1, concurrency)
        self.retries = max(1, retries)
        self.chunk_size = chunk_size
        self.pause_event = pause_event or threading.Event()
        self.stop_event = stop_event or threading.Event()
        self.pause_event.set()
        self.settings = (settings or AppSettings()).normalized()

    async def run(
        self,
        tasks: list[DownloadTask],
        state_store: StateStore | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        tracker = ProgressTracker()
        tracker.add_tasks(tasks)
        lock = asyncio.Lock()

        await emit(
            on_event,
            DownloadEvent(type="started", total=tracker.total, done=0, failed=0),
        )

        queue: asyncio.Queue[DownloadTask | None] = asyncio.Queue()
        for task in tasks:
            queue.put_nowait(task)
        for _ in range(self.concurrency):
            queue.put_nowait(None)

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
        async with aiohttp.ClientSession(
            headers=DOWNLOAD_HEADERS,
            timeout=timeout,
            **build_network_options(self.settings),
        ) as session:
            workers = [
                asyncio.create_task(
                    self._worker(
                        queue,
                        session,
                        state_store,
                        on_event,
                        lock,
                        tracker,
                    )
                )
                for _ in range(self.concurrency)
            ]

            await self._run_workers(workers, queue)
            if state_store:
                try:
                    state_store.flush()
                except Exception:
                    pass

        event_type = "cancelled" if self.stop_event.is_set() else "finished"
        await emit(
            on_event,
            DownloadEvent(
                type=event_type,
                total=tracker.total,
                done=tracker.done,
                failed=tracker.failed,
                message="已停止" if event_type == "cancelled" else "全部下载处理完成",
            ),
        )

    async def run_stream(
        self,
        task_batches: AsyncIterable[list[DownloadTask]],
        state_store: StateStore | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        tracker = ProgressTracker()
        lock = asyncio.Lock()
        queue: asyncio.Queue[DownloadTask | None] = asyncio.Queue()

        await emit(on_event, DownloadEvent(type="started", total=0, done=0, failed=0))

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
        async with aiohttp.ClientSession(
            headers=DOWNLOAD_HEADERS,
            timeout=timeout,
            **build_network_options(self.settings),
        ) as session:
            workers = [
                asyncio.create_task(
                    self._worker(
                        queue,
                        session,
                        state_store,
                        on_event,
                        lock,
                        tracker,
                    )
                )
                for _ in range(self.concurrency)
            ]

            try:
                async for batch in task_batches:
                    if self.stop_event.is_set():
                        break
                    if not batch:
                        continue

                    async with lock:
                        tracker.add_tasks(batch)

                    post = batch[0].post
                    await emit(
                        on_event,
                        DownloadEvent(
                            type="tasks_added",
                            post_id=post.id,
                            total=tracker.total,
                            done=tracker.done,
                            failed=tracker.failed,
                            data=batch,
                            message=f"已加入 {len(batch)} 个文件任务: {post.title}",
                        ),
                    )
                    await emit(
                        on_event,
                        DownloadEvent(
                            type="progress",
                            total=tracker.total,
                            done=tracker.done,
                            failed=tracker.failed,
                        ),
                    )

                    for task in batch:
                        await queue.put(task)
            finally:
                for _ in range(self.concurrency):
                    await queue.put(None)
                await self._run_workers(workers, queue)
                if state_store:
                    try:
                        state_store.flush()
                    except Exception:
                        pass

        event_type = "cancelled" if self.stop_event.is_set() else "finished"
        await emit(
            on_event,
            DownloadEvent(
                type=event_type,
                total=tracker.total,
                done=tracker.done,
                failed=tracker.failed,
                message="已停止" if event_type == "cancelled" else "全部下载处理完成",
            ),
        )

    async def _run_workers(
        self,
        workers: list[asyncio.Task],
        queue: asyncio.Queue,
    ) -> None:
        try:
            await queue.join()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(
        self,
        queue: asyncio.Queue[DownloadTask | None],
        session: aiohttp.ClientSession,
        state_store: StateStore | None,
        on_event: EventCallback | None,
        lock: asyncio.Lock,
        tracker: ProgressTracker,
    ) -> None:
        while True:
            task = await queue.get()
            settled = False
            try:
                if task is None:
                    return

                await self._wait_if_paused()
                if self.stop_event.is_set():
                    settled = True
                    await self._mark_task(task, "cancelled", state_store, on_event, lock, tracker)
                    continue

                if state_store and state_store.is_file_finished(task):
                    settled = True
                    await self._mark_task(task, "skipped", state_store, on_event, lock, tracker)
                    continue

                if task.file.kind != "text" and task.save_path.exists() and task.save_path.stat().st_size > 0:
                    if state_store:
                        state_store.mark_file(task, "finished")
                    settled = True
                    await self._mark_task(task, "skipped", state_store, on_event, lock, tracker)
                    continue

                await emit(
                    on_event,
                    DownloadEvent(
                        type="file_started",
                        post_id=task.post.id,
                        file_name=task.file.name,
                        path=task.save_path,
                        message=f"开始: {task.file.name}",
                    ),
                )
                await self._run_task(session, task)
                if state_store:
                    state_store.mark_file(task, "finished")
                settled = True
                await self._mark_task(task, "finished", state_store, on_event, lock, tracker)
            except Exception as exc:
                # 已经计入结果后再抛异常（例如回调异常）不能二次计数，也不能把 finished 覆盖成 failed。
                if task is not None and not settled:
                    settled = True
                    if state_store:
                        try:
                            state_store.mark_file(task, "failed", str(exc))
                        except Exception:
                            pass
                    await self._mark_task(
                        task, "failed", state_store, on_event, lock, tracker, error=str(exc)
                    )
            finally:
                queue.task_done()

    async def _mark_task(
        self,
        task: DownloadTask,
        status: str,
        state_store: StateStore | None,
        on_event: EventCallback | None,
        lock: asyncio.Lock,
        tracker: ProgressTracker,
        error: str | None = None,
    ) -> None:
        async with lock:
            if not tracker.mark(task, status):
                return
            done = tracker.done
            failed = tracker.failed
            total = tracker.total
            event_type = {
                "finished": "file_finished",
                "skipped": "file_skipped",
                "failed": "file_failed",
                "cancelled": "file_cancelled",
            }.get(status, "file_finished")

            await emit(
                on_event,
                DownloadEvent(
                    type=event_type,
                    post_id=task.post.id,
                    file_name=task.file.name,
                    path=task.save_path,
                    done=done,
                    total=total,
                    failed=failed,
                    data={
                        "url": task.file.url,
                        "target_dir": str(task.save_path.parent),
                        "target_path": str(task.save_path),
                    },
                    message=error or f"{status}: {task.file.name}",
                ),
            )
            await emit(
                on_event,
                DownloadEvent(type="progress", total=total, done=done, failed=failed),
            )

            if tracker.is_post_complete(task):
                post_failed = tracker.post_failed_count(task)
                post_status = "failed" if post_failed else "finished"
                if state_store:
                    state_store.mark_post(task.post, post_status)
                await emit(
                    on_event,
                    DownloadEvent(
                        type="post_finished",
                        post_id=task.post.id,
                        failed=post_failed,
                        message=f"帖子完成: {task.post.title}",
                    ),
                )

    async def _run_task(self, session: aiohttp.ClientSession, task: DownloadTask) -> None:
        task.save_path.parent.mkdir(parents=True, exist_ok=True)
        task.temp_path.parent.mkdir(parents=True, exist_ok=True)
        if task.file.kind == "text":
            data = task.file.content or ""
            if isinstance(data, bytes):
                await write_bytes_atomic(task.save_path, task.temp_path, data)
            else:
                await write_text_atomic(task.save_path, task.temp_path, str(data))
            return

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            await self._wait_if_paused()
            if self.stop_event.is_set():
                raise RuntimeError("任务已停止")
            try:
                await self._download_file(session, task)
                return
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(min(3, attempt))
        raise RuntimeError(str(last_error) if last_error else "下载失败")

    async def _download_file(self, session: aiohttp.ClientSession, task: DownloadTask) -> None:
        existing_size = task.temp_path.stat().st_size if task.temp_path.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}

        async with session.get(task.file.url, headers=headers) as resp:
            if resp.status == 416 and existing_size:
                # 416 说明请求区间越界：只有本地长度正好等于远端总长才算完成，
                # 否则（远端文件变小/换了文件）丢掉临时文件重下。
                match = re.match(r"bytes\s+\*/(\d+)", resp.headers.get("Content-Range", ""))
                if match and int(match.group(1)) != existing_size:
                    task.temp_path.unlink(missing_ok=True)
                    raise RuntimeError("远端文件长度已变化，重新下载")
                replace_final_file(task.temp_path, task.save_path)
                drop_empty_temp_dir(task.temp_path)
                return
            if resp.status not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status}")

            if existing_size and resp.status == 200:
                existing_size = 0
                mode = "wb"
            elif existing_size:
                # 断点续传必须确认服务端返回的起点与本地临时文件长度一致，
                # 否则（CDN 忽略 Range 或返回了别的区间）继续追加会写出损坏文件。
                match = re.match(r"bytes\s+(\d+)-", resp.headers.get("Content-Range", ""))
                if not match or int(match.group(1)) != existing_size:
                    existing_size = 0
                    mode = "wb"
                else:
                    mode = "ab"
            else:
                mode = "wb"

            async with aiofiles.open(task.temp_path, mode) as file:
                async for chunk in resp.content.iter_chunked(self.chunk_size):
                    await self._wait_if_paused()
                    if self.stop_event.is_set():
                        raise RuntimeError("任务已停止")
                    if chunk:
                        await file.write(chunk)

        replace_final_file(task.temp_path, task.save_path)
        drop_empty_temp_dir(task.temp_path)

    async def _wait_if_paused(self) -> None:
        while not self.pause_event.is_set():
            if self.stop_event.is_set():
                return
            await asyncio.sleep(0.2)


async def write_text_atomic(path: Path, temp_path: Path, text: str) -> None:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(temp_path, "w", encoding="utf-8") as file:
        await file.write(text)
    replace_final_file(temp_path, path)
    drop_empty_temp_dir(temp_path)


async def write_bytes_atomic(path: Path, temp_path: Path, data: bytes) -> None:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(temp_path, "wb") as file:
        await file.write(data)
    replace_final_file(temp_path, path)
    drop_empty_temp_dir(temp_path)


def replace_final_file(temp_path: Path, save_path: Path) -> None:
    """把临时文件落到最终位置；跨盘时复制到目标盘再原子替换，避免 WinError 17。"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(temp_path, save_path)
        return
    except OSError as exc:
        cross_device = exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17
        if not cross_device:
            raise

    fd, staging_name = tempfile.mkstemp(
        prefix=save_path.name + ".", suffix=".movepart", dir=str(save_path.parent)
    )
    os.close(fd)
    staging = Path(staging_name)
    try:
        shutil.copyfile(temp_path, staging)
        os.replace(staging, save_path)
    except Exception:
        try:
            staging.unlink()
        except OSError:
            pass
        raise
    temp_path.unlink(missing_ok=True)


def drop_empty_temp_dir(temp_path: Path) -> None:
    """下载完成后移除空的帖级临时目录；默认根目录下的 .pawchive_tmp 也一并清理。"""
    parent = temp_path.parent
    try:
        parent.rmdir()
    except OSError:
        return
    if parent.parent.name == ".pawchive_tmp":
        try:
            parent.parent.rmdir()
        except OSError:
            pass


async def emit(callback: EventCallback | None, event: DownloadEvent) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result
