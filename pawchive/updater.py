from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pawchive.version import APP_VERSION

OWNER = "HoshinoAyako"
REPO = "PawchiveDownloader"
PROJECT_URL = f"https://github.com/{OWNER}/{REPO}"
RELEASES_URL = f"{PROJECT_URL}/releases"
LATEST_API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
USER_AGENT = f"PawchiveDownloader/{APP_VERSION} (+{PROJECT_URL})"
CHECKSUM_NAMES = ("sha256sums.txt", "sha256sums", "checksums.txt")
CHUNK_SIZE = 64 * 1024


class UpdateError(RuntimeError):
    """带阶段与原因说明的更新错误。"""

    def __init__(self, stage: str, reason: str, detail: str = ""):
        self.stage = stage
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    size: int
    browser_url: str
    digest: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    title: str
    body: str
    assets: tuple[UpdateAsset, ...] = field(default_factory=tuple)

    @property
    def exe_asset(self) -> UpdateAsset | None:
        candidates = [item for item in self.assets if item.name.lower().endswith(".exe")]
        if not candidates:
            return None
        preferred = [item for item in candidates if "pawchive" in item.name.lower()]
        return (preferred or candidates)[0]

    @property
    def checksum_asset(self) -> UpdateAsset | None:
        for item in self.assets:
            name = item.name.lower()
            if name in CHECKSUM_NAMES or name.endswith(".sha256"):
                return item
        return None


@dataclass(frozen=True)
class TargetInfo:
    path: Path
    size: int
    sha256: str


def version_tuple(value: str) -> tuple[int, ...]:
    """把 ``v1.9.0`` 转成用于比较的版本元组，忽略非数字后缀。"""
    cleaned = (value or "").strip().lstrip("vV")
    numbers: list[int] = []
    for part in cleaned.split("."):
        digits = ""
        for char in part:
            if char.isdigit():
                digits += char
            else:
                break
        numbers.append(int(digits or "0"))
    return tuple(numbers)


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def _http_get(url: str, *, timeout: int = 15) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return response.read()


def _reason_text(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        if exc.code == 403:
            return "GitHub 拒绝访问（可能是匿名 API 限流或权限不足，HTTP 403）"
        if exc.code == 404:
            return "GitHub 返回 404：仓库或 Release 不可访问，可能仍为 Private"
        if 500 <= exc.code < 600:
            return f"GitHub 服务异常（HTTP {exc.code}）"
        return f"GitHub 返回 HTTP {exc.code}"
    if isinstance(exc, ssl.SSLError):
        return f"TLS 握手或证书校验失败：{exc}"
    if isinstance(exc, socket.gaierror):
        return f"DNS 解析失败：{exc}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "连接超时，GitHub 在当前网络不可达或响应过慢"
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _reason_text(reason)
        return f"网络连接失败：{reason}"
    return f"{type(exc).__name__}: {exc}"


def probe_github_connectivity(timeout: int = 12) -> ReleaseInfo:
    """连接检查并查询 latest release；失败时抛出带阶段说明的 UpdateError。"""
    try:
        raw = _http_get(LATEST_API, timeout=timeout)
    except BaseException as exc:
        raise UpdateError("GitHub 连接检查", _reason_text(exc)) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub 响应解析", f"返回内容不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub 响应解析", "latest release 响应结构异常")

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("版本检查", "GitHub latest release 没有 tag_name")
    assets = []
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        assets.append(
            UpdateAsset(
                name=name,
                size=int(item.get("size") or 0),
                browser_url=str(item.get("browser_download_url") or ""),
                digest=str(item.get("digest") or ""),
            )
        )
    return ReleaseInfo(
        tag=tag,
        version=tag.lstrip("vV"),
        title=str(payload.get("name") or tag),
        body=str(payload.get("body") or ""),
        assets=tuple(assets),
    )


def _parse_checksum(text: str, asset_name: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    digests: list[str] = []
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        digest = parts[0].lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            continue
        if len(parts) > 1 and parts[-1].lstrip("*") == asset_name:
            return digest
        digests.append(digest)
    unique = set(digests)
    return digests[0] if len(unique) == 1 else ""


def expected_sha256(release: ReleaseInfo, asset: UpdateAsset, timeout: int = 15) -> str:
    """读取 SHA256SUMS.txt；没有校验清单时退回 GitHub asset digest。"""
    checksum_asset = release.checksum_asset
    if checksum_asset is not None:
        try:
            text = _http_get(checksum_asset.browser_url, timeout=timeout).decode("utf-8")
        except BaseException as exc:
            raise UpdateError("读取校验清单", _reason_text(exc)) from exc
        parsed = _parse_checksum(text, asset.name)
        if parsed:
            return parsed
        raise UpdateError("校验更新文件", "SHA256SUMS.txt 中没有匹配的校验值")

    digest = asset.digest.strip().lower()
    if digest.startswith("sha256:"):
        digest = digest[7:].strip()
    if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest):
        return digest
    raise UpdateError("校验更新文件", "发布方没有提供 SHA-256 校验清单，已拒绝更新")


def download_update(
    asset: UpdateAsset,
    destination: Path,
    *,
    expected_hash: str,
    progress: Callable[[int, int], None] | None = None,
    stage: Callable[[str], None] | None = None,
    timeout: int = 30,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """下载更新包并校验 SHA-256；返回下载后的文件路径。"""
    if stage:
        stage(f"下载更新包：{asset.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(asset.browser_url, headers={"User-Agent": USER_AGENT})
    total = asset.size or 0
    received = 0
    digest = hashlib.sha256()
    try:
        with urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            header_total = response.headers.get("Content-Length")
            if not total and header_total and header_total.isdigit():
                total = int(header_total)
            with destination.open("wb") as handle:
                while True:
                    if cancel and cancel():
                        raise UpdateError("下载更新包", "用户取消更新")
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, total)
                handle.flush()
                os.fsync(handle.fileno())
    except UpdateError:
        destination.unlink(missing_ok=True)
        raise
    except BaseException as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError("下载更新包", _reason_text(exc)) from exc

    if total and received != total:
        destination.unlink(missing_ok=True)
        raise UpdateError(
            "下载更新包",
            f"下载不完整：收到 {received} 字节，预期 {total} 字节",
        )
    if digest.hexdigest().lower() != expected_hash.lower():
        destination.unlink(missing_ok=True)
        raise UpdateError("校验更新文件", "下载文件 SHA-256 与发布方校验值不一致")
    if stage:
        stage("更新包完整性校验通过")
    return destination


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _has_pe_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(offset)
            return handle.read(4) == b"PE\0\0"
    except (OSError, struct.error):
        return False


def current_executable() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def verify_target(target: Path) -> TargetInfo:
    """校验当前目标 exe 的结构、大小与目录可写性，不建立本地哈希基线。"""
    if not target.is_file():
        raise UpdateError("校验当前程序", f"目标文件不存在：{target}")
    if target.stat().st_size < 1024 * 1024:
        raise UpdateError("校验当前程序", "目标文件大小异常，疑似不是完整程序")
    if not _has_pe_header(target):
        raise UpdateError("校验当前程序", "目标文件不是有效的 Windows PE 可执行文件")
    if not os.access(target.parent, os.W_OK):
        raise UpdateError("校验当前程序", f"目标目录不可写：{target.parent}")
    return TargetInfo(
        path=target,
        size=target.stat().st_size,
        sha256=_sha256_file(target),
    )


def install_update(
    downloaded: Path,
    target: Path,
    *,
    expected_hash: str,
    progress: Callable[[int, int], None] | None = None,
    stage: Callable[[str], None] | None = None,
) -> Path:
    """原地替换当前 exe。

    先重命名运行中的旧 exe，再分块写入新文件；失败时回滚。
    返回旧文件备份路径，调用方可在本进程退出后清理。
    """
    if stage:
        stage("准备替换当前程序")
    target_parent = target.parent
    try:
        probe = tempfile.NamedTemporaryFile(
            prefix=".pawchive-write-", dir=str(target_parent), delete=True
        )
        probe.close()
    except OSError as exc:
        raise UpdateError("替换当前程序", f"目标目录不可写：{exc}") from exc

    backup = target.with_name(target.name + ".old")
    if backup.exists():
        try:
            backup.unlink()
        except OSError:
            backup = target.with_name(target.name + f".old.{int(time.time())}")
    try:
        os.replace(target, backup)
    except OSError as exc:
        raise UpdateError(
            "替换当前程序",
            f"无法重命名正在运行的程序，当前目录可能被占用或无写权限：{exc}",
        ) from exc

    total = downloaded.stat().st_size
    written = 0
    try:
        with downloaded.open("rb") as source, target.open("wb") as handle:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if progress:
                    progress(written, total)
            handle.flush()
            os.fsync(handle.fileno())
        if stage:
            stage("校验目标文件完整性")
        if not target.is_file() or target.stat().st_size != total:
            raise UpdateError("校验目标文件", "写入后的程序文件大小与更新包不一致")
        if not _has_pe_header(target):
            raise UpdateError("校验目标文件", "写入后的文件不是有效的 Windows PE 可执行文件")
        actual_digest = _sha256_file(target)
        if actual_digest.lower() != expected_hash.lower():
            raise UpdateError("校验目标文件", "写入后的目标文件 SHA-256 与更新包不一致")
    except BaseException as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            os.replace(backup, target)
        except OSError:
            pass
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("写入新程序", f"替换失败，已尝试回滚：{exc}") from exc

    if stage:
        stage("新程序完整性校验通过")
    return backup


def restart_application(target: Path) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise UpdateError("启动新程序", f"无法启动更新后的程序：{exc}") from exc


def cleanup_previous_update_files(target: Path | None = None) -> int:
    """启动时清理上次更新留下的旧 exe 备份。"""
    target = target or current_executable()
    if target is None:
        return 0
    removed = 0
    for path in target.parent.glob(target.name + ".old*"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def cleanup_legacy_manifest(target: Path | None = None) -> int:
    """删除旧版安装基线 sidecar；新版不再生成该文件。"""
    target = target or current_executable()
    if target is None:
        return 0
    path = target.with_name(target.name + ".sha256")
    try:
        path.unlink()
        return 1
    except OSError:
        return 0


def cleanup_update_temp(prefix: str = "pawchive_update_") -> int:
    """清理上次中断的临时下载目录。"""
    root = Path(tempfile.gettempdir())
    removed = 0
    for path in root.glob(prefix + "*"):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


__all__ = [
    "CHECKSUM_NAMES",
    "LATEST_API",
    "OWNER",
    "PROJECT_URL",
    "RELEASES_URL",
    "REPO",
    "ReleaseInfo",
    "TargetInfo",
    "UpdateAsset",
    "UpdateError",
    "cleanup_legacy_manifest",
    "cleanup_previous_update_files",
    "cleanup_update_temp",
    "current_executable",
    "download_update",
    "expected_sha256",
    "install_update",
    "is_newer",
    "probe_github_connectivity",
    "restart_application",
    "verify_target",
    "version_tuple",
]
