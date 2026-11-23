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
        info = updater.verify_target(target)
        assert_true(info.sha256 == old_hash, "当前程序结构校验失败")

        update = work / "PawchiveDownloader-v1.9.1.exe"
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
        assert_true(info.sha256 == new_hash, "更新后目标文件校验失败")
        assert_true(
            not target.with_name(target.name + ".sha256").exists(),
            "新版不应生成 .sha256 sidecar",
        )
        removed = updater.cleanup_previous_update_files(target)
        assert_true(removed >= 1 and not backup.exists(), "旧备份未清理")

        legacy = target.with_name(target.name + ".sha256")
        legacy.write_text("old-baseline", encoding="utf-8")
        assert_true(updater.cleanup_legacy_manifest(target) == 1, "旧 sidecar 未清理")
        assert_true(not legacy.exists(), "旧 sidecar 仍存在")

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


def test_update_status() -> None:
    with tempfile.TemporaryDirectory(prefix="paw_smoke_status_") as temp:
        path = Path(temp) / "status.json"
        updater.write_update_status("failed", "替换程序", target=Path("C:/tmp/app.exe"), error="locked", path=path)
        data = updater.pop_update_status(path)
        assert_true(data.get("status") == "failed", "更新状态读取失败")
        assert_true(not path.exists(), "更新状态未清理")


def test_settings_ranges() -> None:
    from pawchive.settings import (
        AppSettings,
        DEFAULT_PREVIEW_CACHE_MB,
        DEFAULT_RETRY_COUNT,
        DEFAULT_RETRY_INTERVAL,
        MAX_PREVIEW_CACHE_MB,
        MAX_RETRY_COUNT,
        MAX_RETRY_INTERVAL,
    )

    settings = AppSettings(
        retry_count=0,
        retry_interval=99,
        preview_mode="bad",
        preview_cache_mb=9999,
    ).normalized()
    assert_true(settings.retry_count == 1, "重试次数下限未生效")
    assert_true(settings.retry_interval == MAX_RETRY_INTERVAL, "重试间隔上限未生效")
    assert_true(settings.preview_mode == "text", "旧预览模式未回退")
    assert_true(settings.preview_cache_mb == MAX_PREVIEW_CACHE_MB, "缓存容量上限未生效")
    assert_true(AppSettings().retry_count == DEFAULT_RETRY_COUNT, "默认重试次数错误")
    assert_true(AppSettings().retry_interval == DEFAULT_RETRY_INTERVAL, "默认重试间隔错误")
    assert_true(
        AppSettings().preview_cache_mb == DEFAULT_PREVIEW_CACHE_MB,
        "默认预览缓存容量错误",
    )
    assert_true(AppSettings().retry_count <= MAX_RETRY_COUNT, "默认重试次数越界")


def test_thumbnail_cache() -> None:
    from pawchive.thumbnail_cache import ThumbnailCache

    with tempfile.TemporaryDirectory(prefix="paw_smoke_cache_") as temp:
        cache = ThumbnailCache(Path(temp), 4096)
        payload = b"x" * 2048
        cache.put("https://example.invalid/1.png", payload)
        assert_true(cache.get("https://example.invalid/1.png") == payload, "缓存读取失败")
        cache.put("https://example.invalid/2.png", payload)
        cache.put("https://example.invalid/3.png", payload)
        assert_true(cache.size_bytes() <= 4096, "缓存容量未生效")
        assert_true(
            cache.get("https://example.invalid/1.png") is None,
            "缓存未按 LRU 淘汰最旧项",
        )
        assert_true(cache.get("https://example.invalid/3.png") == payload, "缓存新项丢失")
        cache.configure(Path(temp), 0)
        assert_true(cache.size_bytes() == 0, "关闭缓存时未清空旧文件")


def test_preview_cache_path() -> None:
    from PyQt6.QtWidgets import QApplication

    from pawchive.app import gui
    from pawchive.settings import AppSettings

    with tempfile.TemporaryDirectory(prefix="paw_smoke_cache_path_") as temp:
        settings = AppSettings(output_dir=temp, preview_cache_mb=32).normalized()
        original = gui.load_settings
        gui.load_settings = lambda: settings
        try:
            app = QApplication.instance() or QApplication([])
            window = gui.MainWindow(gui.load_app_font())
            expected = Path(temp) / "PawchiveDownload" / "Temp" / "PreviewCache"
            assert_true(window.post_preview_panel.cache_dir == expected, "预览缓存目录错误")
            assert_true(
                window.post_preview_panel.cache_max_bytes == 32 * 1024 * 1024,
                "预览缓存容量未传给加载器",
            )
            window.close()
        finally:
            gui.load_settings = original


def test_state_discard() -> None:
    from pawchive.downloader.models import DownloadTask, FileItem, PostItem
    from pawchive.downloader.state import StateStore

    with tempfile.TemporaryDirectory(prefix="paw_smoke_state_") as temp:
        state_dir = Path(temp) / "State"
        state_dir.mkdir()
        post = PostItem("1", "title", "2026-01-01T00:00:00", "https://example.invalid", "1", "patreon")
        good = FileItem("https://example.invalid/ok.png", "01.png", "image")
        bad = FileItem("https://example.invalid/bad.png", "02.png", "image")
        target_good = Path(temp) / "01.png"
        target_bad = Path(temp) / "02.png"
        target_good.write_bytes(b"good")
        task_good = DownloadTask(post, good, target_good, Path(temp) / "01.part")
        task_bad = DownloadTask(post, bad, target_bad, Path(temp) / "02.part")
        store = StateStore("creator", root=state_dir)
        store.mark_file(task_good, "finished")
        store.mark_file(task_bad, "failed", "boom")
        store.mark_post(post, "failed")
        assert_true(store.discard_file(task_bad), "失败状态未删除")
        store.flush()
        data = store.path.read_text(encoding="utf-8")
        assert_true("02.png" not in data and "01.png" in data, "状态记录删除范围错误")
        assert_true('"finished"' in data, "剩余成功文件状态未恢复")


def test_preview_modes() -> None:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QPixmap
    from PyQt6.QtWidgets import QApplication

    from pawchive.app import gui
    from pawchive.downloader.models import PostItem

    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(gui.load_app_font())
    posts = [
        PostItem(
            str(index),
            f"Post {index}",
            f"2026-11-0{index}T12:00:00",
            "https://example.invalid",
            "1",
            "patreon",
            raw={"file": {"path": "/data/a.png", "name": "a.png"}},
        )
        for index in range(1, 3)
    ]
    window._on_posts_loaded(posts)
    window.post_preview_combo.setCurrentIndex(1)
    window._on_preview_mode_changed(1)
    assert_true(len(window.post_preview_panel.cards) == 2, "缩略图模式卡片数错误")
    first_card = window.post_preview_panel.cards[0]
    image = QPixmap(40, 30)
    image.fill(QColor("#336699"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.ReadWrite)
    assert_true(image.save(buffer, "PNG"), "测试图片编码失败")
    assert_true(first_card.set_thumbnail_bytes(bytes(buffer.data())), "测试缩略图解码失败")
    assert_true(first_card.has_thumbnail(), "测试缩略图未保留")
    assert_true(first_card.time_label is not None, "缩略图模式应保留时间标签")
    assert_true(first_card.time_label.isHidden(), "缩略图模式不应显示时间戳")
    assert_true(first_card.title_label.isHidden(), "缩略图模式不应显示标题")
    window.post_preview_combo.setCurrentIndex(2)
    window._on_preview_mode_changed(2)
    card = window.post_preview_panel.cards[0]
    assert_true(card is first_card, "切换预览方式不应重建卡片")
    assert_true(card.has_thumbnail(), "切换预览方式不应丢失缩略图")
    assert_true(card.title_label.text() == "Post 1", "全部展示标题错误")
    assert_true(card.time_label.text() == "2026-11-01 12:00", "全部展示时间戳错误")
    assert_true(not card.title_label.isHidden(), "全部展示标题应可见")
    assert_true(not card.time_label.isHidden(), "全部展示时间戳应可见")
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
        ("update-status", test_update_status),
        ("settings-ranges", test_settings_ranges),
        ("thumbnail-cache", test_thumbnail_cache),
        ("preview-cache-path", test_preview_cache_path),
        ("state-discard", test_state_discard),
        ("preview-modes", test_preview_modes),
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
