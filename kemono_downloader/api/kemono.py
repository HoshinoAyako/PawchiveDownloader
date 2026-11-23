from __future__ import annotations

import re
from enum import Enum
from urllib.parse import urlparse

import aiohttp

from kemono_downloader.config import INCLUDE_OTHER_ATTACHMENTS
from kemono_downloader.downloader.models import FileItem, PostItem
from kemono_downloader.downloader.naming import is_image_name, sanitize_filename
from kemono_downloader.net import build_network_options
from kemono_downloader.settings import AppSettings
from kemono_downloader.version import APP_VERSION


USER_AGENT = f"PawchiveDownloader/{APP_VERSION}"
BASE = "https://pawchive.pw"
# Pawchive 主站不代理 /data（会 404），文件统一走 file.* 数据域名。
DATA_BASE = "https://file.pawchive.pw/data"
PREVIEW_DATA_BASE = "https://img.pawchive.pw/thumbnail/data"
PAGINATION_END_STATUSES = {400, 404}
DEFAULT_HEADERS = {
    # Kemono/DDG currently rejects or misbehaves with normal SPA/JSON-looking
    # requests, so keep this odd Accept header for scraping/API calls.
    "Accept": "text/css",
    "User-Agent": USER_AGENT,
    "Referer": BASE,
}


class ImageSource(Enum):
    ORIGINAL = "original"
    PREVIEW = "preview"

    @classmethod
    def parse(cls, value: "ImageSource | str") -> "ImageSource":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError as exc:
            raise ValueError(f"未知图片源模式: {value}") from exc


class KemonoApi:
    def __init__(
        self,
        timeout: int = 30,
        image_source: ImageSource | str = ImageSource.PREVIEW,
        include_other_attachments: bool = INCLUDE_OTHER_ATTACHMENTS,
        settings: AppSettings | None = None,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._detail_cache: dict[str, dict] = {}
        self.image_source = ImageSource.parse(image_source)
        self.include_other_attachments = include_other_attachments
        self.settings = (settings or AppSettings()).normalized()

    async def list_posts(self, creator_url: str) -> list[PostItem]:
        user_path = normalize_creator_path(creator_url)
        posts: list[PostItem] = []
        offset = 0

        async with aiohttp.ClientSession(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            **build_network_options(self.settings),
        ) as session:
            while True:
                url = build_posts_url(user_path, offset)
                status, data = await self._get_json(session, url, user_for_refresh=user_path)
                if offset > 0 and status in PAGINATION_END_STATUSES:
                    break
                if status != 200:
                    raise RuntimeError(f"获取帖子列表失败: HTTP {status}")
                if not data:
                    break
                posts.extend(parse_post_summary(item) for item in data)
                offset += 50

        return posts

    async def get_post_files(self, post: PostItem) -> list[FileItem]:
        data = await self._get_detail(post)
        post_data = extract_post(data)
        attachments = normalize_attachments(post_data)
        files: list[FileItem] = []

        for item in attachments:
            name = sanitize_filename(item.get("name", "file"))
            path = item.get("path", "")
            if not name or not path:
                continue
            kind = "image" if is_image_name(name) else "file"
            if kind != "image" and not self._allows_attachment(name):
                continue
            url = build_file_url(path, kind, self.image_source)
            files.append(FileItem(url=url, name=name, kind=kind))

        return files

    def _allows_attachment(self, name: str) -> bool:
        if self.include_other_attachments:
            return True
        return self.settings.allows_attachment(name)

    async def get_post_content(self, post: PostItem) -> str:
        data = await self._get_detail(post)
        return extract_post(data).get("content", "") or ""

    async def _get_detail(self, post: PostItem) -> dict:
        key = f"{post.service}:{post.user_id}:{post.id}"
        if key in self._detail_cache:
            return self._detail_cache[key]

        url = f"{BASE}/api/v1/{post.service}/user/{post.user_id}/post/{post.id}"
        async with aiohttp.ClientSession(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            **build_network_options(self.settings),
        ) as session:
            status, data = await self._get_json(
                session,
                url,
                user_for_refresh=f"{post.service}/user/{post.user_id}",
            )
        if status != 200 or not data:
            raise RuntimeError(f"获取帖子详情失败: HTTP {status} {post.title}")

        self._detail_cache[key] = data
        return data

    async def _refresh_cookie(self, session: aiohttp.ClientSession, user_for_refresh: str | None) -> None:
        url = f"{BASE}/{user_for_refresh.strip('/')}" if user_for_refresh else BASE
        try:
            async with session.get(url, headers=DEFAULT_HEADERS):
                return
        except Exception:
            return

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        user_for_refresh: str | None = None,
        retry_once: bool = True,
    ) -> tuple[int, object | None]:
        async def one_try() -> tuple[int, object | None]:
            async with session.get(url, headers=DEFAULT_HEADERS) as resp:
                if resp.status != 200:
                    return resp.status, None
                try:
                    return 200, await resp.json(content_type=None)
                except Exception:
                    return 200, None

        status, data = await one_try()
        if (status != 200 or data is None) and retry_once:
            await self._refresh_cookie(session, user_for_refresh)
            status, data = await one_try()
        return status, data


def extract_post(data: object) -> dict:
    """兼容两种详情响应: Kemono 的 {"post": {...}} 与 Pawchive 的扁平帖子对象。"""
    if not isinstance(data, dict):
        return {}
    post = data.get("post")
    return post if isinstance(post, dict) else data


def normalize_creator_path(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("创作者 URL 不能为空")

    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    path = path.strip("/")

    if path.startswith("api/v1/"):
        path = path[len("api/v1/") :]
    if path.endswith("/posts"):
        path = path[: -len("/posts")]
    match = re.match(r"([^/]+)/user/([^/]+)", path)
    if not match:
        raise ValueError(f"无法识别创作者 URL: {value}")
    return f"{match.group(1)}/user/{match.group(2)}"


def build_posts_url(user_path: str, offset: int) -> str:
    base_url = f"{BASE}/api/v1/{user_path}/posts"
    if offset <= 0:
        return base_url
    return f"{base_url}?o={offset}"


def build_file_url(path: str, kind: str, image_source: ImageSource | str = ImageSource.PREVIEW) -> str:
    source = ImageSource.parse(image_source)
    if path.startswith("http"):
        if kind == "image" and source == ImageSource.PREVIEW:
            return PREVIEW_DATA_BASE + strip_known_data_prefix(path)
        return path

    normalized_path = strip_known_data_prefix(path)
    if kind == "image" and source == ImageSource.PREVIEW:
        return PREVIEW_DATA_BASE + normalized_path
    return DATA_BASE + normalized_path


def strip_known_data_prefix(path: str) -> str:
    for prefix in (
        "https://file.pawchive.pw/data",
        "https://pawchive.pw/data",
        "https://n4.pawchive.pw/data",
        "https://n3.pawchive.pw/data",
        "https://n2.pawchive.pw/data",
        "https://n1.pawchive.pw/data",
    ):
        if path.startswith(prefix):
            path = path[len(prefix) :] or "/"
            break
    # 接口也可能只给相对路径 /data/...；DATA_BASE 已经包含 /data，
    # 这里先去掉重复的 /data，避免拼出 /data/data/...。
    if path.startswith("data/"):
        path = "/" + path
    if path.startswith("/data/"):
        path = path[len("/data") :]
    return path if path.startswith("/") else f"/{path}"


def parse_post_summary(raw: dict) -> PostItem:
    service = str(raw.get("service", ""))
    user_id = str(raw.get("user", ""))
    post_id = str(raw.get("id", ""))
    title = raw.get("title") or "untitled"
    return PostItem(
        id=post_id,
        title=str(title),
        published=str(raw.get("published") or ""),
        url=f"{BASE}/{service}/user/{user_id}/post/{post_id}",
        user_id=user_id,
        service=service,
        raw=raw,
    )


def normalize_attachments(post_data: dict) -> list[dict]:
    result: list[dict] = []
    main_file = post_data.get("file")
    if main_file:
        result.append(main_file)
    result.extend(item for item in (post_data.get("attachments") or []) if item)
    return result
