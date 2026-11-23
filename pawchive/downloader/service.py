from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from pawchive.api.base import PlatformApi

from .content import build_content_text
from .models import DateNamingMode, DownloadTask, FileItem, PostItem
from .naming import post_folder_name, rename_images, sanitize_filename


class DownloadService:
    def __init__(
        self,
        api: PlatformApi,
        include_other_attachments: bool | None = None,
        temp_root: Path | None = None,
    ):
        self.api = api
        # None 表示附件筛选交给 API（由 GUI 设置驱动）；False 表示强制只留图片。
        self.include_other_attachments = include_other_attachments
        # 自定义临时根目录；None 表示保存目录下的 .pawchive_tmp。
        self.temp_root = temp_root
        self._reserved_post_dirs: dict[str, Path] = {}
        self._reserved_paths: set[Path] = set()

    async def list_posts(self, creator_url: str) -> list[PostItem]:
        return await self.api.list_posts(creator_url)

    async def build_tasks(
        self,
        posts: list[PostItem],
        output_dir: Path,
        naming_mode: DateNamingMode,
    ) -> list[DownloadTask]:
        output_dir.mkdir(parents=True, exist_ok=True)
        tasks: list[DownloadTask] = []

        for post in posts:
            tasks.extend(await self.build_post_tasks(post, output_dir, naming_mode))

        return tasks

    async def iter_post_task_batches(
        self,
        posts: list[PostItem],
        output_dir: Path,
        naming_mode: DateNamingMode,
        post_concurrency: int = 1,
    ) -> AsyncIterator[list[DownloadTask]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        concurrency = max(1, int(post_concurrency))
        if concurrency <= 1 or len(posts) <= 1:
            for post in posts:
                yield await self.build_post_tasks(post, output_dir, naming_mode)
            return

        queue: asyncio.Queue[tuple[bool, PostItem, object]] = asyncio.Queue()
        semaphore = asyncio.Semaphore(concurrency)

        async def worker(post: PostItem) -> None:
            async with semaphore:
                try:
                    batch = await self.build_post_tasks(post, output_dir, naming_mode)
                except Exception as exc:  # 交给消费端统一抛出，保持原有失败语义
                    await queue.put((False, post, exc))
                else:
                    await queue.put((True, post, batch))

        workers = [asyncio.create_task(worker(post)) for post in posts]
        try:
            for _ in range(len(workers)):
                ok, post, payload = await queue.get()
                if not ok:
                    raise payload  # type: ignore[misc]
                yield payload  # type: ignore[misc]
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def build_post_tasks(
        self,
        post: PostItem,
        output_dir: Path,
        naming_mode: DateNamingMode,
    ) -> list[DownloadTask]:
        output_dir.mkdir(parents=True, exist_ok=True)
        files = await self.api.get_post_files(post)
        if self.include_other_attachments is False:
            files = [item for item in files if item.kind == "image"]

        content_html = await self.api.get_post_content(post)
        post.content_html = content_html

        content_item = FileItem(url=post.url, name="content.txt", kind="text", content="")
        items = rename_images([content_item, *files])
        files = [item for item in items if item.kind != "text"]
        for item in items:
            if item.kind == "text":
                item.content = build_content_text(post, files, external_links=[])
                break

        post_dir = self._resolve_post_dir(output_dir, post, naming_mode)
        temp_root = self.temp_root or (output_dir / ".pawchive_tmp")
        temp_dir = temp_root / post_dir.name
        tasks: list[DownloadTask] = []
        for item in items:
            save_path = post_dir / sanitize_filename(item.name)
            tasks.append(
                DownloadTask(
                    post=post,
                    file=item,
                    save_path=save_path,
                    temp_path=temp_dir / f"{sanitize_filename(item.name)}.part",
                )
            )
        return tasks

    def _resolve_post_dir(self, output_dir: Path, post: PostItem, naming_mode: DateNamingMode) -> Path:
        key = f"{post.service}:{post.user_id}:{post.id}"
        if key in self._reserved_post_dirs:
            return self._reserved_post_dirs[key]

        base_name = post_folder_name(post, naming_mode)
        base_path = output_dir / base_name
        candidate = base_path
        suffix = 1

        while candidate.exists() or candidate in self._reserved_paths:
            marker = candidate / ".post_id"
            if marker.exists():
                try:
                    if marker.read_text(encoding="utf-8").strip() == post.id:
                        break
                except Exception:
                    pass
            candidate = output_dir / f"{base_name}_{suffix}"
            suffix += 1

        candidate.mkdir(parents=True, exist_ok=True)
        marker = candidate / ".post_id"
        if not marker.exists():
            marker.write_text(post.id, encoding="utf-8")
        self._reserved_post_dirs[key] = candidate
        self._reserved_paths.add(candidate)
        return candidate
