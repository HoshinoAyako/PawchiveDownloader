"""独立更新帮助进程。

主程序退出后，这个由当前 exe 复制出来的进程负责：
- 等待父进程退出；
- 重新校验更新包；
- 备份并替换目标程序；
- 替换失败时回滚；
- 写一次性状态文件并重启新程序。

设计参考 MAA 的 MAA.Updater：更新动作不留在主进程内，避免 Windows 锁定
正在运行的程序，同时保留失败回滚与重试。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

from pawchive.updater import (
    UpdateError,
    _has_pe_header,
    _sha256_file,
    install_update,
    restart_application,
    verify_target,
    write_update_status,
)


def _wait_for_parent(pid: int, timeout: int) -> bool:
    """等待父进程退出；超时返回 False，避免更新器永久卡住。"""
    if pid <= 0:
        return True
    if sys.platform != "win32":
        for _ in range(max(1, timeout)):
            try:
                Path(f"/proc/{pid}").exists()
            except OSError:
                return True
            time.sleep(1)
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True
    try:
        result = kernel32.WaitForSingleObject(handle, max(1, timeout) * 1000)
        return result in (0x00000000, 0x00000080)  # WAIT_OBJECT_0 / WAIT_ABANDONED
    finally:
        kernel32.CloseHandle(handle)


def _status(stage: str, *, status: str = "pending", error: str = "", attempts: int = 0, target: Path, path: Path) -> None:
    try:
        write_update_status(status, stage, target=target, error=error, attempts=attempts, path=path)
    except OSError:
        pass


def run_helper(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--apply-update", action="store_true")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--target", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args, _unknown = parser.parse_known_args(argv[1:])
    if not args.apply_update:
        return 2

    target = Path(args.target)
    package = Path(args.package)
    status_path = Path(args.status)
    _status("等待主程序退出", target=target, path=status_path)
    if not _wait_for_parent(args.parent_pid, args.timeout):
        _status(
            "等待主程序退出超时",
            status="failed",
            error="主程序未在限定时间内退出，更新器已停止",
            target=target,
            path=status_path,
        )
        return 3

    last_error = ""
    for attempt in range(1, 11):
        try:
            if not package.is_file():
                raise UpdateError("校验更新包", "更新包不存在，可能已被清理")
            if _sha256_file(package) != args.sha256.lower():
                raise UpdateError("校验更新包", "更新包 SHA-256 不匹配")
            if package.stat().st_size < 1024 * 1024:
                raise UpdateError("校验更新包", "更新包大小异常")
            if not _has_pe_header(package):
                raise UpdateError("校验更新包", "更新包不是有效的 Windows PE 文件")
            verify_target(target)
            _status(f"替换程序（第 {attempt}/10 次）", target=target, attempts=attempt, path=status_path)
            install_update(
                package,
                target,
                expected_hash=args.sha256,
                stage=lambda text: _status(text, target=target, attempts=attempt, path=status_path),
            )
            _status("更新完成，正在重启程序", status="success", target=target, attempts=attempt, path=status_path)
            restart_application(target)
            return 0
        except UpdateError as exc:
            last_error = f"{exc.stage}：{exc.reason}"
        except BaseException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < 10:
            time.sleep(min(4.0, 0.6 + attempt * 0.5))

    _status(
        "更新失败，已保留/恢复旧程序",
        status="failed",
        error=last_error,
        target=target,
        attempts=10,
        path=status_path,
    )
    try:
        restart_application(target)
    except UpdateError:
        pass
    return 1


__all__ = ["run_helper"]
