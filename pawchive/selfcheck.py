"""启动自检：环境、权限、读写路径与程序资源完整性。

自检只在首次运行和主/次版本变化时自动触发；日常启动不打扰用户。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pawchive.settings import (
    program_config_path,
    program_log_dir,
    program_temp_root,
)

REQUIRED_ASSETS = (
    "icon.ico",
    "check.png",
    "arrow_down.png",
    "arrow_up.png",
    "arrow_down_light.png",
    "arrow_up_light.png",
    "branch_closed_dark.png",
    "branch_closed_light.png",
    "branch_open_dark.png",
    "branch_open_light.png",
    "win_min_dark.svg",
    "win_min_light.svg",
    "win_max_dark.svg",
    "win_max_light.svg",
    "win_close_dark.svg",
    "win_close_light.svg",
)


@dataclass(slots=True)
class CheckItem:
    title: str
    ok: bool
    detail: str


@dataclass(slots=True)
class SelfCheckResult:
    items: list[CheckItem] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckItem]:
        return [item for item in self.items if not item.ok]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary_lines(self) -> list[str]:
        return [f"{'✓' if item.ok else '✕'} {item.title}：{item.detail}" for item in self.items]


def major_series(version: str) -> tuple[int, int] | None:
    parts = str(version or "").strip().lstrip("v").split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def needs_self_check(last_version: str, current_version: str) -> bool:
    last_series = major_series(last_version)
    current_series = major_series(current_version)
    if last_series is None or current_series is None:
        return True
    return last_series != current_series


def _write_test(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".pawchive_check.", dir=str(directory))
        os.close(fd)
        os.unlink(name)
        return True, "可读写"
    except OSError as exc:
        return False, f"不可写：{exc}"


def _check_path(label: str, directory: Path) -> CheckItem:
    try:
        if not directory.exists():
            return CheckItem(label, False, f"目录不存在：{directory}")
        ok, detail = _write_test(directory)
        return CheckItem(label, ok, f"{directory} · {detail}")
    except OSError as exc:
        return CheckItem(label, False, f"{directory} · {exc}")


def _check_assets(asset_dir: Path) -> CheckItem:
    missing = [name for name in REQUIRED_ASSETS if not (asset_dir / name).is_file()]
    zero = [
        name
        for name in REQUIRED_ASSETS
        if (asset_dir / name).is_file() and (asset_dir / name).stat().st_size <= 0
    ]
    if missing:
        return CheckItem("程序资源", False, "缺少 " + "、".join(missing[:4]))
    if zero:
        return CheckItem("程序资源", False, "空文件 " + "、".join(zero[:4]))
    return CheckItem("程序资源", True, f"{len(REQUIRED_ASSETS)} 个界面资源完整")


def _check_config(program_root: Path) -> CheckItem:
    path = program_config_path(program_root)
    if not path.is_file():
        return CheckItem("设置文件", True, "尚未生成，保存设置时会创建")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckItem("设置文件", False, f"无法读取：{exc}")
    if not isinstance(data, dict):
        return CheckItem("设置文件", False, "内容不是 JSON 对象")
    return CheckItem("设置文件", True, str(path))


def _check_disk(path: Path) -> CheckItem:
    try:
        usage = shutil.disk_usage(path)
        free = usage.free / (1024**3)
        if free < 1:
            return CheckItem("可用空间", False, f"{path.drive or path} 仅剩 {free:.1f} GB")
        return CheckItem("可用空间", True, f"{path.drive or path} 剩余 {free:.1f} GB")
    except OSError as exc:
        return CheckItem("可用空间", False, str(exc))


def _check_environment() -> CheckItem:
    bits = platform.architecture()[0]
    system = platform.platform(terse=True)
    if sys.platform != "win32":
        return CheckItem("运行环境", True, f"{system} · {bits}")
    return CheckItem("运行环境", True, f"{system} · {bits} · 当前用户可访问")


def run_self_check(
    program_root: Path | None,
    extra_dirs: tuple[str, ...] | list[str] = (),
    asset_dir: Path | None = None,
) -> SelfCheckResult:
    result = SelfCheckResult()
    result.items.append(_check_environment())
    if asset_dir is None:
        asset_dir = Path(__file__).resolve().parents[1] / "assets"
    result.items.append(_check_assets(asset_dir))

    if program_root is None:
        result.items.append(CheckItem("程序目录", False, "尚未设置保存目录"))
    else:
        root = Path(program_root)
        result.items.append(_check_path("程序目录", root))
        result.items.append(_check_path("设置目录", program_config_path(root).parent))
        result.items.append(_check_path("临时目录", program_temp_root(root)))
        result.items.append(_check_path("日志目录", program_log_dir(root)))
        result.items.append(_check_config(root))
        result.items.append(_check_disk(root))

    for index, value in enumerate(extra_dirs, start=1):
        path = Path(value)
        result.items.append(_check_path(f"额外目录 {index}", path))
    return result
