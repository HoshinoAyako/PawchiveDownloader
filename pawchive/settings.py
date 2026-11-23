from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

_SAVE_LOCK = threading.Lock()

APP_DIR_NAME = "PawchiveDownloader"
LOCATOR_KEY = r"Software\PawchiveDownloader"
LOCATOR_VALUE = "LastOutputDir"
# 保存目录下的统一程序文件夹：帖子、Config、Temp 都放在这里，避免散落到用户目录。
PROGRAM_DIR_NAME = "PawchiveDownload"
CONFIG_DIR_NAME = "Config"
TEMP_DIR_NAME = "Temp"
LOG_DIR_NAME = "Log"
CONFIG_FILE_NAME = "Settings.json"
LEGACY_CONFIG_FILE_NAME = "settings.json"
FAILED_EXPORT_FILE_NAME = "Failed_Downloads.txt"
LEGACY_FAILED_EXPORT_FILE_NAME = "failed_downloads.txt"

PROXY_TYPES = ("none", "http", "https", "socks5")
PROXY_TYPE_LABELS = {
    "none": "不使用代理",
    "http": "HTTP",
    "https": "HTTPS",
    "socks5": "SOCKS5",
}

# 图片下载源：预览图走缩略图域名，原图走文件域名。
IMAGE_SOURCE_KEYS = ("preview", "original")
IMAGE_SOURCE_LABELS = {
    "preview": "预览图",
    "original": "原图",
}

# 帖子列表与保存目录共用的预览方式。
PREVIEW_MODE_KEYS = ("text", "thumbnail", "full")
PREVIEW_MODE_LABELS = {
    "text": "仅文字",
    "thumbnail": "缩略图",
    "full": "全部展示",
}

# 在线预览图磁盘缓存：默认 32 MB，0 表示关闭缓存。
DEFAULT_PREVIEW_CACHE_MB = 32
MIN_PREVIEW_CACHE_MB = 0
MAX_PREVIEW_CACHE_MB = 512

# 下载失败后的自动重试：次数是首次失败后的追加次数，间隔为每次重试前的固定等待。
DEFAULT_RETRY_COUNT = 3
MIN_RETRY_COUNT = 1
MAX_RETRY_COUNT = 9
DEFAULT_RETRY_INTERVAL = 5
MIN_RETRY_INTERVAL = 3
MAX_RETRY_INTERVAL = 9

# 站点限制：同时最多 5 个下载、约 1 请求/秒。并发上限按此封顶。
MAX_FILE_CONCURRENCY = 5
MAX_POST_CONCURRENCY = 5
DEFAULT_FILE_CONCURRENCY = 3
DEFAULT_POST_CONCURRENCY = 1

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")

# 附件分类：(键, 界面文案, 归类扩展名)。界面文案直接用扩展名，方便一眼看懂。
# 图片通过 AppSettings.include_images 单独控制；"other" 没有固定扩展名，
# 勾选后表示允许下载所有未识别的扩展名。
ATTACHMENT_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("video", "MP4/MKV/AVI 等视频", (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".wmv", ".ts", ".flv", ".m2ts")),
    ("archive", "ZIP/RAR/7Z 等压缩包", (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst", ".001")),
    ("audio", "MP3/WAV/FLAC 等音频", (".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus", ".wma")),
    (
        "design",
        "PSD/AI/CLIP 等设计稿",
        (".psd", ".psb", ".ai", ".clip", ".sai", ".sketch", ".fig", ".blend", ".c4d", ".indd", ".cdr", ".xcf"),
    ),
    (
        "document",
        "PDF/DOC/XLSX 等文档",
        (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".epub", ".mobi", ".json", ".srt", ".ass"),
    ),
    ("other", "其他未知扩展名", ()),
)
ATTACHMENT_CATEGORY_KEYS = tuple(key for key, _, _ in ATTACHMENT_CATEGORIES)
ATTACHMENT_CATEGORY_LABELS = {key: label for key, label, _ in ATTACHMENT_CATEGORIES}
# 摘要行用更短的写法，避免把一长串扩展名塞进状态栏。
ATTACHMENT_CATEGORY_SHORT = {
    "video": "视频",
    "archive": "压缩包",
    "audio": "音频",
    "design": "PSD等",
    "document": "文档",
    "other": "全部",
}
ATTACHMENT_CATEGORY_EXTS = {key: exts for key, _, exts in ATTACHMENT_CATEGORIES}
_EXT_TO_CATEGORY = {ext: key for key, _, exts in ATTACHMENT_CATEGORIES for ext in exts}


def legacy_settings_path() -> Path:
    """旧版启动定位文件；只用于升级迁移，不再创建新文件。"""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / APP_DIR_NAME / LEGACY_CONFIG_FILE_NAME


def _read_output_locator() -> str:
    """从注册表读取上次保存根，避免在 AppData 留定位文件。"""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LOCATOR_KEY) as key:
            value, _ = winreg.QueryValueEx(key, LOCATOR_VALUE)
    except OSError:
        return ""
    return normalize_output_dir(value)


def _write_output_locator(output_dir: str) -> None:
    """把保存根写入注册表；空值表示清除定位。"""
    try:
        import winreg
    except ImportError:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, LOCATOR_KEY) as key:
        winreg.SetValueEx(key, LOCATOR_VALUE, 0, winreg.REG_SZ, output_dir)


def normalize_output_dir(value: object) -> str:
    """规范化保存根：自动落到所选目录下的 PawchiveDownload。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text = os.path.expandvars(os.path.expanduser(text))
        path = Path(text)
        if not path.is_absolute():
            return ""
        if path.name.casefold() != PROGRAM_DIR_NAME.casefold():
            path = path / PROGRAM_DIR_NAME
        return str(path)
    except Exception:
        return ""


def normalize_storage_dir(value: object) -> str:
    """额外保存目录：使用用户选择的绝对路径，不再追加 PawchiveDownload。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text = os.path.expandvars(os.path.expanduser(text))
        path = Path(text)
        if not path.is_absolute():
            return ""
        return str(path)
    except Exception:
        return ""


def storage_path_key(value: object) -> str:
    text = normalize_storage_dir(value)
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(text))


def storage_paths_overlap(first: object, second: object) -> bool:
    """两个目录相同、互为父子时返回 True，避免额外目录混进程序目录。"""
    first_key = storage_path_key(first)
    second_key = storage_path_key(second)
    if not first_key or not second_key:
        return False
    try:
        common = os.path.commonpath([first_key, second_key])
    except ValueError:
        return False
    return common in (first_key, second_key)


def normalize_extra_output_dirs(value: object, program_root: object) -> tuple[str, ...]:
    """去重、去程序目录及父子目录，保留用户添加顺序。"""
    raw_items = value if isinstance(value, (list, tuple, set)) else ()
    primary_key = storage_path_key(program_root)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        path = normalize_storage_dir(item)
        key = storage_path_key(path)
        if not key or key in seen:
            continue
        if primary_key and storage_paths_overlap(path, program_root):
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def normalize_selected_output_dir(value: object, program_root: object, extra_dirs: object) -> str:
    """当前选择必须属于“主目录 + 额外目录”，否则回落到主目录。"""
    primary = normalize_output_dir(program_root)
    if not primary:
        return ""
    candidates = (primary, *normalize_extra_output_dirs(extra_dirs, primary))
    selected = normalize_storage_dir(value)
    key = storage_path_key(selected)
    for candidate in candidates:
        if storage_path_key(candidate) == key:
            return candidate
    return primary


def program_root_for(output_dir: Path | str | None) -> Path | None:
    text = normalize_output_dir(output_dir)
    return Path(text) if text else None


def program_config_dir(program_root: Path) -> Path:
    return Path(program_root) / CONFIG_DIR_NAME


def program_config_path(program_root: Path) -> Path:
    return program_config_dir(program_root) / CONFIG_FILE_NAME


def program_temp_root(program_root: Path) -> Path:
    return Path(program_root) / TEMP_DIR_NAME


def program_log_dir(program_root: Path) -> Path:
    return program_config_dir(program_root) / LOG_DIR_NAME


def failed_export_path(program_root: Path) -> Path:
    """失败记录固定写在 Config/Log；旧小写文件名在首次访问时迁移。"""
    target = program_log_dir(program_root) / FAILED_EXPORT_FILE_NAME
    _ensure_file_name_case(target)
    legacy = target.with_name(LEGACY_FAILED_EXPORT_FILE_NAME)
    if not target.exists() and legacy.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, target)
        except OSError:
            pass
    return target


def _ensure_file_name_case(path: Path) -> None:
    """Windows 不区分大小写时，把已存在的文件真正改成目标大小写。"""
    if not path.parent.exists():
        return
    try:
        actual = next(
            (
                item
                for item in path.parent.iterdir()
                if item.is_file() and item.name.casefold() == path.name.casefold()
            ),
            None,
        )
    except OSError:
        return
    if actual is None or actual.name == path.name:
        return
    temp = path.parent / (path.name + ".case.tmp")
    try:
        os.replace(actual, temp)
        os.replace(temp, path)
    except OSError:
        try:
            if temp.exists():
                os.replace(temp, actual)
        except OSError:
            pass


def ensure_program_layout(program_root: Path) -> Path:
    """创建 Config、Temp、Config/Log，返回程序根目录。"""
    root = Path(program_root)
    root.mkdir(parents=True, exist_ok=True)
    program_config_dir(root).mkdir(parents=True, exist_ok=True)
    program_temp_root(root).mkdir(parents=True, exist_ok=True)
    program_log_dir(root).mkdir(parents=True, exist_ok=True)
    return root


def clamp_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def normalize_proxy_address(address: str) -> str:
    """校验并规范化代理地址，支持 host:port 与 user:pass@host:port。"""
    text = (address or "").strip()
    if not text:
        raise ValueError("代理地址不能为空")
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.strip().strip("/")
    if not text:
        raise ValueError("代理地址不能为空")

    parsed = urlparse(f"//{text}")
    if not parsed.hostname:
        raise ValueError("代理地址缺少主机名，例如 127.0.0.1:7890")
    if parsed.port is None:
        raise ValueError("代理地址缺少端口，例如 127.0.0.1:7890")
    if parsed.path.strip("/"):
        raise ValueError("代理地址不应包含路径，仅填 host:port")
    return text


def extension_of(name: str) -> str:
    """取小写扩展名（含点），没有扩展名时返回空串。"""
    text = (name or "").strip().lower()
    if "." not in text:
        return ""
    ext = "." + text.rsplit(".", 1)[1]
    return ext if len(ext) <= 16 else ""


def attachment_category(name: str) -> str:
    ext = extension_of(name)
    return _EXT_TO_CATEGORY.get(ext, "other")


def normalize_extra_extensions(value: object) -> tuple[str, ...]:
    """把用户填写的自定义扩展名解析成 ("psd", "bin", ...)。"""
    if isinstance(value, str):
        parts: list[str] = re.split(r"[,，;；、\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        parts = []

    result: list[str] = []
    for part in parts:
        ext = part.strip().lower().lstrip("*").lstrip(".")
        if not ext or len(ext) > 16:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9+_-]*", ext):
            continue
        if ext not in result:
            result.append(ext)
    return tuple(result[:40])


def normalize_temp_dir(value: object) -> str:
    """临时目录只接受绝对路径；空值表示使用保存目录下的 .pawchive_tmp。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text = os.path.expandvars(os.path.expanduser(text))
        path = Path(text)
        if not path.is_absolute():
            return ""
        return str(path)
    except Exception:
        return ""


def resolve_temp_root(settings: "AppSettings", output_dir: Path | None) -> Path | None:
    """临时文件统一放在 <保存根>/Temp；保存根本身已是 PawchiveDownload。"""
    root = program_root_for(output_dir)
    if root is None:
        return None
    return program_temp_root(root)


@dataclass
class AppSettings:
    file_concurrency: int = DEFAULT_FILE_CONCURRENCY
    post_concurrency: int = DEFAULT_POST_CONCURRENCY
    retry_count: int = DEFAULT_RETRY_COUNT
    retry_interval: int = DEFAULT_RETRY_INTERVAL
    # 帖子列表 / 保存目录预览方式：text / thumbnail / full。
    preview_mode: str = "text"
    # 在线预览图磁盘缓存容量（MB），0 表示关闭。
    preview_cache_mb: int = DEFAULT_PREVIEW_CACHE_MB
    # 图片下载源：preview 走缩略图域名，original 走文件域名。
    image_source: str = "preview"
    proxy_type: str = "none"
    proxy_address: str = ""
    attachment_types: tuple[str, ...] = ()
    # 是否下载图片附件；旧配置缺少此字段时按 True 处理，保持原有“图片始终下载”行为。
    include_images: bool = True
    # 加入下载列表时是否建立空白 .part 占位；不提前下载文件内容。
    queue_precreate_temps: bool = True
    extra_extensions: tuple[str, ...] = ()
    # 仅本次任务生效的自定义扩展名开关；勾了“其他未知扩展名”时无需它。
    temp_custom_extensions: bool = False
    # 公告在哪个版本被设为“不再提示”；与当前版本不同则重新显示。
    announcement_dismissed_version: str = ""
    # 上次自检完成的版本；首次运行或主/次版本变化时会重新自检。
    last_self_check_version: str = ""
    # 上次使用的保存根（已规范化到 <所选目录>/PawchiveDownload）；空表示尚未选择。
    output_dir: str = ""
    # 额外保存目录：只存帖子，不创建 Config/Temp/State 等程序元数据。
    extra_output_dirs: tuple[str, ...] = ()
    # 主界面下拉菜单当前选中的保存目录。
    selected_output_dir: str = ""
    # 旧版自定义临时目录；新版固定使用 <保存根>/Temp，仅保留字段兼容旧配置。
    temp_dir: str = ""
    # 界面主题：system / light / dark。
    ui_theme: str = "dark"

    @property
    def uses_proxy(self) -> bool:
        return self.proxy_type in ("http", "https", "socks5")

    @property
    def custom_extensions_active(self) -> bool:
        return bool(self.extra_extensions) and (
            "other" in self.attachment_types or self.temp_custom_extensions
        )

    @property
    def effective_extra_extensions(self) -> tuple[str, ...]:
        return self.extra_extensions if self.custom_extensions_active else ()

    def proxy_url(self) -> str | None:
        if not self.uses_proxy:
            return None
        return f"{self.proxy_type}://{normalize_proxy_address(self.proxy_address)}"

    def allows_attachment(self, name: str) -> bool:
        """图片以外的附件是否允许下载。"""
        ext = extension_of(name)
        if ext and ext.lstrip(".") in self.effective_extra_extensions:
            return True
        category = attachment_category(name)
        return category in self.attachment_types

    def without_temp_extensions(self) -> "AppSettings":
        """单次任务结束后清掉临时开关，同时丢弃只服务于它的扩展名。"""
        return AppSettings(
            file_concurrency=self.file_concurrency,
            post_concurrency=self.post_concurrency,
            retry_count=self.retry_count,
            retry_interval=self.retry_interval,
            preview_mode=self.preview_mode,
            preview_cache_mb=self.preview_cache_mb,
            image_source=self.image_source,
            proxy_type=self.proxy_type,
            proxy_address=self.proxy_address,
            attachment_types=self.attachment_types,
            include_images=self.include_images,
            queue_precreate_temps=self.queue_precreate_temps,
            extra_extensions=self.extra_extensions if "other" in self.attachment_types else (),
            temp_custom_extensions=False,
            announcement_dismissed_version=self.announcement_dismissed_version,
            last_self_check_version=self.last_self_check_version,
            output_dir=self.output_dir,
            extra_output_dirs=self.extra_output_dirs,
            selected_output_dir=self.selected_output_dir,
            temp_dir="",
            ui_theme=self.ui_theme,
        ).normalized()

    def normalized(self) -> "AppSettings":
        proxy_type = self.proxy_type if self.proxy_type in PROXY_TYPES else "none"
        address = (self.proxy_address or "").strip()
        if proxy_type != "none":
            try:
                address = normalize_proxy_address(address)
            except ValueError:
                # 地址无效时退回“不使用代理”，避免带着坏配置去请求。
                proxy_type = "none"
                address = ""
        else:
            address = ""

        raw_types = self.attachment_types or ()
        if isinstance(raw_types, str):
            raw_types = (raw_types,)
        chosen = {str(item).strip().lower() for item in raw_types}
        types = tuple(key for key in ATTACHMENT_CATEGORY_KEYS if key in chosen)

        extras = normalize_extra_extensions(self.extra_extensions)
        temp_enabled = bool(self.temp_custom_extensions)
        if "other" in types:
            # “其他未知扩展名”已经全部放行，自定义列表和临时开关都没有意义。
            temp_enabled = False
        elif temp_enabled and not extras:
            temp_enabled = False
        if not ("other" in types or temp_enabled):
            # 未启用自定义扩展名时不允许它悄悄生效。
            extras = ()

        primary_output = normalize_output_dir(self.output_dir)
        extra_outputs = normalize_extra_output_dirs(self.extra_output_dirs, primary_output)
        selected_output = normalize_selected_output_dir(
            self.selected_output_dir or primary_output,
            primary_output,
            extra_outputs,
        )

        return AppSettings(
            file_concurrency=clamp_int(
                self.file_concurrency, 1, MAX_FILE_CONCURRENCY, DEFAULT_FILE_CONCURRENCY
            ),
            post_concurrency=clamp_int(
                self.post_concurrency, 1, MAX_POST_CONCURRENCY, DEFAULT_POST_CONCURRENCY
            ),
            retry_count=clamp_int(
                self.retry_count, MIN_RETRY_COUNT, MAX_RETRY_COUNT, DEFAULT_RETRY_COUNT
            ),
            retry_interval=clamp_int(
                self.retry_interval, MIN_RETRY_INTERVAL, MAX_RETRY_INTERVAL, DEFAULT_RETRY_INTERVAL
            ),
            preview_mode=self.preview_mode if self.preview_mode in PREVIEW_MODE_KEYS else "text",
            preview_cache_mb=clamp_int(
                self.preview_cache_mb,
                MIN_PREVIEW_CACHE_MB,
                MAX_PREVIEW_CACHE_MB,
                DEFAULT_PREVIEW_CACHE_MB,
            ),
            image_source=self.image_source if self.image_source in IMAGE_SOURCE_KEYS else "preview",
            proxy_type=proxy_type,
            proxy_address=address,
            attachment_types=types,
            include_images=bool(self.include_images),
            queue_precreate_temps=bool(self.queue_precreate_temps),
            extra_extensions=extras,
            temp_custom_extensions=temp_enabled,
            announcement_dismissed_version=str(self.announcement_dismissed_version or "").strip()[:32],
            last_self_check_version=str(self.last_self_check_version or "").strip()[:32],
            output_dir=primary_output,
            extra_output_dirs=extra_outputs,
            selected_output_dir=selected_output,
            temp_dir="",
            ui_theme=self.ui_theme if self.ui_theme in ("system", "light", "dark") else "dark",
        )


def _settings_from_dict(data: dict) -> AppSettings:
    return AppSettings(
        file_concurrency=data.get("file_concurrency", DEFAULT_FILE_CONCURRENCY),
        post_concurrency=data.get("post_concurrency", DEFAULT_POST_CONCURRENCY),
        retry_count=data.get("retry_count", DEFAULT_RETRY_COUNT),
        retry_interval=data.get("retry_interval", DEFAULT_RETRY_INTERVAL),
        preview_mode=str(data.get("preview_mode", "text") or "text"),
        preview_cache_mb=data.get("preview_cache_mb", DEFAULT_PREVIEW_CACHE_MB),
        image_source=str(data.get("image_source", "preview") or "preview"),
        proxy_type=str(data.get("proxy_type", "none")),
        proxy_address=str(data.get("proxy_address", "") or ""),
        attachment_types=data.get("attachment_types", ()) or (),
        include_images=bool(data.get("include_images", True)),
        queue_precreate_temps=bool(data.get("queue_precreate_temps", True)),
        extra_extensions=data.get("extra_extensions", ()) or (),
        temp_custom_extensions=bool(data.get("temp_custom_extensions", False)),
        announcement_dismissed_version=str(data.get("announcement_dismissed_version", "") or ""),
        last_self_check_version=str(data.get("last_self_check_version", "") or ""),
        output_dir=str(data.get("output_dir", "") or ""),
        extra_output_dirs=data.get("extra_output_dirs", ()) or (),
        selected_output_dir=str(data.get("selected_output_dir", "") or ""),
        temp_dir="",
        ui_theme=str(data.get("ui_theme", "dark") or "dark"),
    ).normalized()


def _read_settings_file(path: Path) -> tuple[dict | None, int]:
    try:
        with _SAVE_LOCK:
            stat = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, 0
    if not isinstance(data, dict):
        return None, 0
    return data, stat.st_mtime_ns


def load_settings(path: Path | None = None) -> AppSettings:
    """读取设置。

    保存根从注册表定位，完整配置只放在 ``PawchiveDownload/Config/Settings.json``。
    旧版的 AppData settings.json 只在首次升级时用于补写注册表。
    """
    if path is not None:
        data, _ = _read_settings_file(path)
        if data is None:
            return AppSettings()
        return _settings_from_dict(data)

    output_dir = _read_output_locator()
    if not output_dir:
        legacy_path = legacy_settings_path()
        legacy_data, _ = _read_settings_file(legacy_path)
        if legacy_data is not None:
            output_dir = normalize_output_dir(legacy_data.get("output_dir", ""))
            if output_dir:
                try:
                    _write_output_locator(output_dir)
                except OSError:
                    pass
    if not output_dir:
        return AppSettings()

    config_path = program_config_path(Path(output_dir))
    _ensure_file_name_case(config_path)
    config_data, _ = _read_settings_file(config_path)
    if config_data is None:
        # 1.7.0 及更早版本使用小写文件名，首次读取时顺手迁移。
        legacy_config = config_path.with_name(LEGACY_CONFIG_FILE_NAME)
        legacy_data, _ = _read_settings_file(legacy_config)
        if legacy_data is not None:
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy_config, config_path)
            except OSError:
                pass
            config_data = legacy_data
    if config_data is None:
        return AppSettings(output_dir=output_dir).normalized()
    settings = _settings_from_dict(config_data)
    if not settings.output_dir:
        settings.output_dir = output_dir
    elif settings.output_dir != output_dir:
        try:
            _write_output_locator(settings.output_dir)
        except OSError:
            pass
    return settings


def _write_settings_json(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    # 同目录唯一临时文件 + 进程内串行替换：避免多个线程同时 os.replace 同一目标时
    # 在 Windows 上出现共享冲突（PermissionError），也避免读者看到半截 JSON。
    with _SAVE_LOCK:
        fd, temp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_name, target)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    return target


def save_settings(settings: AppSettings, path: Path | None = None) -> Path | None:
    normalized = settings.normalized()
    payload = asdict(normalized)
    if path is not None:
        return _write_settings_json(Path(path), payload)

    # 保存根只写入注册表；程序文件不再落到 AppData。
    _write_output_locator(normalized.output_dir)
    if not normalized.output_dir:
        return None
    config_path = program_config_path(Path(normalized.output_dir))
    return _write_settings_json(config_path, payload)


def describe_attachments(settings: AppSettings) -> str:
    if "other" in settings.attachment_types:
        return "附件全收（含图片）" if settings.include_images else "附件全收（不含图片）"
    parts = [ATTACHMENT_CATEGORY_SHORT[key] for key in settings.attachment_types]
    if settings.include_images:
        parts.insert(0, "图片")
    extras = settings.effective_extra_extensions
    if extras:
        text = ", ".join("." + ext for ext in extras[:4])
        if len(extras) > 4:
            text += f" 等 {len(extras)} 种"
        prefix = "临时 " if settings.temp_custom_extensions else "自定义 "
        parts.append(prefix + text)
    return "+".join(parts) or "不下载附件"


def describe_settings(settings: AppSettings) -> str:
    label = f"并发 {settings.file_concurrency} / 帖子 {settings.post_concurrency}"
    label += f" / 重试 {settings.retry_count} 次×{settings.retry_interval}s"
    label += f" / 预览缓存 {settings.preview_cache_mb}M"
    label += f" / 图片 {IMAGE_SOURCE_LABELS.get(settings.image_source, '预览图')}"
    label += f" / 附件 {describe_attachments(settings)}"
    if settings.uses_proxy:
        label += f" / 代理 {PROXY_TYPE_LABELS[settings.proxy_type]} {settings.proxy_address}"
    else:
        label += " / 无代理"
    return label


def describe_settings_brief(settings: AppSettings, limit: int = 62) -> str:
    """摘要行宽度有限，过长时截断，完整内容放在 tooltip。"""
    text = describe_settings(settings)
    return text if len(text) <= limit else text[: limit - 1] + "…"
