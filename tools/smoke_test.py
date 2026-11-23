"""Pawchive Downloader 快速烟雾测试。

只使用标准库与项目依赖，覆盖导入、关于页、版本规则、校验解析、下载与更新替换路径。
设计目标是本地和 GitHub Actions 都能在几秒内跑完。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from pawchive import updater
from pawchive.version import APP_VERSION, APP_NAME


class SmokeFailure(AssertionError):
    pass


def assert_true(value: object, message: str) -> None:
    if not value:
        raise SmokeFailure(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_fixture() -> Path:
    """找一个大于 1 MB 的真实 Windows PE 文件作为替换测试素材。"""
    major, minor = sys.version_info[:2]
    candidates = [
        Path(sys.base_prefix) / f"python{major}{minor}.dll",
        Path(sys.base_prefix) / "python3.dll",
        Path(sys.executable),
    ]
    candidates.extend(Path(sys.base_prefix).glob("*.dll"))
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size >= 1024 * 1024:
                if updater._has_pe_header(candidate):
                    return candidate
        except OSError:
            continue
    raise SmokeFailure("找不到大于 1 MB 的 Windows PE 测试文件")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


def test_imports() -> None:
    from pawchive.app import gui  # noqa: F401
    from pawchive.app import style  # noqa: F401
    from pawchive.api import pawchive  # noqa: F401
    from pawchive.downloader import engine, service, state  # noqa: F401
    from pawchive import release_notes  # noqa: F401


def test_version_changelog() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert_true(APP_NAME == "Pawchive Downloader", "APP_NAME 不一致")
    assert_true(f"## {APP_VERSION} —" in changelog, "CHANGELOG 缺少当前版本条目")


def test_version_rules() -> None:
    assert_true(updater.version_tuple("v1.9.0") == (1, 9, 0), "版本元组解析失败")
    assert_true(updater.version_tuple("1.10.2") == (1, 10, 2), "多位版本解析失败")
    assert_true(updater.is_newer("1.9.0", "1.8.1"), "新版本判断失败")
    assert_true(not updater.is_newer("1.8.1", "1.9.0"), "旧版本判断失败")
    assert_true(not updater.is_newer("1.9.0", "1.9.0"), "同版本判断失败")


def test_digest_checksum() -> None:
    digest = "a" * 64
    asset = updater.UpdateAsset(
        name="PawchiveDownloader-v1.9.0.exe",
        size=10,
        browser_url="https://example.invalid/update.exe",
        digest=f"sha256:{digest}",
    )
    release = updater.ReleaseInfo(
        tag="v1.9.0",
        version="1.9.0",
        title="",
        body="",
        assets=(asset,),
    )
    assert_true(release.exe_asset == asset, "exe 附件选择失败")
    assert_true(
        updater.expected_sha256(release, asset) == digest,
        "Release asset digest 解析失败",
    )


def test_download_and_hash() -> None:
    payload = (b"pawchive-smoke-" * 8192) + b"end"
    digest = hashlib.sha256(payload).hexdigest()
    with tempfile.TemporaryDirectory(prefix="paw_smoke_http_") as temp:
        temp_path = Path(temp)
        (temp_path / "update.bin").write_bytes(payload)
        (temp_path / "SHA256SUMS.txt").write_text(
            f"{digest}  update.bin\n", encoding="utf-8"
        )
        handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
            *args, directory=str(temp_path), **kwargs
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            asset = updater.UpdateAsset(
                name="update.bin",
                size=len(payload),
                browser_url=f"{base_url}/update.bin",
                digest="",
            )
            checksum_asset = updater.UpdateAsset(
                name="SHA256SUMS.txt",
                size=0,
                browser_url=f"{base_url}/SHA256SUMS.txt",
                digest="",
            )
            release = updater.ReleaseInfo(
                tag="v1.9.0",
                version="1.9.0",
                title="",
                body="",
                assets=(asset, checksum_asset),
            )
            expected = updater.expected_sha256(release, asset)
            assert_true(expected == digest, "SHA256SUMS.txt 解析失败")
            target = temp_path / "downloaded.bin"
            events: list[tuple[int, int]] = []
            updater.download_update(
                asset,
                target,
                expected_hash=expected,
                progress=lambda received, total: events.append((received, total)),
                cancel=lambda: False,
            )
            assert_true(sha256(target) == digest, "下载文件哈希不一致")
            assert_true(events and events[-1][0] == len(payload), "下载进度未完成")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def test_install_and_rollback() -> None:
    fixture = pe_fixture()
    old_hash = sha256(fixture)
    with tempfile.TemporaryDirectory(prefix="paw_smoke_install_") as temp:
        work = Path(temp)
        target = work / "PawchiveDownloader.exe"
        shutil.copy2(fixture, target)
        info = updater.ensure_install_baseline(target)
        assert_true(info is not None and info.baseline, "安装基线未建立")
        assert_true(info.manifest.is_file(), "安装基线文件未写入")

        update = work / "PawchiveDownloader-v1.9.0.exe"
        shutil.copy2(fixture, update)
        with update.open("ab") as handle:
            handle.write(b"\0" * 4096)
        new_hash = sha256(update)
        backup = updater.install_update(
            update,
            target,
            expected_hash=new_hash,
        )
        assert_true(sha256(target) == new_hash, "替换后的目标文件哈希不一致")
        assert_true(backup.is_file() and sha256(backup) == old_hash, "旧文件备份不完整")
        info = updater.verify_target(target)
        assert_true(info.sha256 == new_hash and not info.baseline, "更新后基线未刷新")
        removed = updater.cleanup_previous_update_files(target)
        assert_true(removed >= 1 and not backup.exists(), "旧备份未清理")

        rollback_target = work / "rollback.exe"
        shutil.copy2(fixture, rollback_target)
        rollback_update = work / "rollback-update.exe"
        shutil.copy2(fixture, rollback_update)
        try:
            updater.install_update(
                rollback_update,
                rollback_target,
                expected_hash="0" * 64,
            )
        except updater.UpdateError as exc:
            assert_true(exc.stage == "校验目标文件", "回滚失败阶段不符合预期")
        else:
            raise SmokeFailure("错误哈希未触发回滚")
        assert_true(sha256(rollback_target) == old_hash, "回滚后目标文件未恢复")


def test_about_dialog() -> None:
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QLabel

    from pawchive.app import gui

    app = QApplication.instance() or QApplication([])
    font = gui.load_app_font()
    app.setFont(QFont(font, 10))
    about = gui.AboutDialog(APP_VERSION, font)
    assert_true(about.windowTitle() == "关于", "关于窗口标题不正确")
    assert_true(about.update_button.text() == "检查更新", "检查更新按钮缺失")
    assert_true(about.update_state_label.text().startswith("更新状态"), "更新状态标签缺失")
    icons = [
        label.pixmap()
        for label in about.findChildren(QLabel)
        if not label.pixmap().isNull()
    ]
    assert_true(icons, "关于窗口缺少产品图标")
    about.close()

    window = gui.MainWindow(font)
    assert_true(hasattr(window, "about_button"), "主界面缺少关于按钮")
    assert_true(window.about_button.text() == "关于", "主界面关于按钮文本不正确")
    window.close()


def run_check(name: str, func: Callable[[], None], failures: list[str]) -> None:
    started = time.perf_counter()
    try:
        func()
    except Exception:
        failures.append(name)
        print(f"[SMOKE] FAIL {name}")
        print(traceback.format_exc())
        return
    elapsed = int((time.perf_counter() - started) * 1000)
    print(f"[SMOKE] PASS {name} ({elapsed} ms)")


def main() -> int:
    checks: list[tuple[str, Callable[[], None]]] = [
        ("imports", test_imports),
        ("version-changelog", test_version_changelog),
        ("version-rules", test_version_rules),
        ("digest-checksum", test_digest_checksum),
        ("download-hash", test_download_and_hash),
        ("install-rollback", test_install_and_rollback),
        ("about-dialog", test_about_dialog),
    ]
    failures: list[str] = []
    started = time.perf_counter()
    for name, func in checks:
        run_check(name, func, failures)
    elapsed = int((time.perf_counter() - started) * 1000)
    print(
        f"[SMOKE] SUMMARY total={len(checks)} passed={len(checks) - len(failures)} "
        f"failed={len(failures)} duration_ms={elapsed}"
    )
    if failures:
        print("[SMOKE] FAILED " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
