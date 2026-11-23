# Pawchive Downloader

Pawchive 帖子的 Windows 桌面批量下载器，提供创作者帖子加载、附件筛选、并发控制、保存目录管理、日志导出和预览图/原图处理。

当前版本：`v1.8.1`

## 功能

- 创作者 URL 加载，支持帖子列表全选/反选
- 文件并发、帖子并发与站点限速提示
- 附件类型筛选：视频、音频、文档、压缩包、设计稿、未知扩展名
- 图片预览图/原图下载选择
- 预览图完整性校验：发现本地文件小于远端原图时可确认替换
- 主保存目录 + 多个额外保存目录
- 深色/浅色主题、日志导出、失败文件与临时目录预览
- 启动自检：首次或主/次版本变化时检查环境、权限、路径、磁盘、资源和配置

## 环境

- Windows 10/11
- Python 3.10+（开发环境为 Python 3.12）
- 依赖见 `requirements.txt`

## 从源码运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python gui.py
```

CLI：

```powershell
python cli.py --help
python cli.py list "https://pawchive.pw/fanbox/user/xxxx"
python cli.py download "https://pawchive.pw/fanbox/user/xxxx" -o "D:\Downloads"
```

## 构建 exe

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean PawchiveDownloader.spec
```

产物位于 `dist\PawchiveDownloader.exe`。构建说明：

- 图标和界面资源在 `assets/`
- 端口与网络相关依赖需要 `aiohttp_socks` 和 `python_socks`
- `trio` 是 `anyio` 的可选后端，未安装不影响 asyncio 下的正常运行

## 数据目录

主保存目录的默认结构：

```text
<选择的目录>\PawchiveDownload\
├─ Config\
│  ├─ Settings.json
│  └─ Log\Failed_Downloads.txt
├─ Temp\
│  ├─ *.part
│  └─ State\*.json
└─ Log\
```

- `Config`、`Temp`、`State`、`Log` 和失败记录属于程序元数据，固定在主目录。
- 额外保存目录只写入帖子内容，不追加 `PawchiveDownload`，也不写入程序元数据。
- 额外目录不能与主目录相同或互为父子目录。

## 原图校验说明

“校验完整性”的原图检测依据是：

1. State 中记录为 `img.pawchive.pw/thumbnail/data` 的图片；
2. 文件仍位于当前保存目录；
3. 远端 `file.pawchive.pw/data` 的 Range 请求返回总大小；
4. 远端总大小大于本地文件才列为候选。

这是大小启发式检测，不是像素级哈希或尺寸比对。只有用户确认后才会下载并原位覆盖，State 中的 `url/size/source` 会同步更新。请求默认串行，间隔约 0.9 秒。

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE) 发布。

项目使用 PyQt6，而 PyQt6 采用 GPLv3 / 商业双许可；如需以非 GPL 的宽松许可证分发，需要先取得 PyQt 商业许可。使用本项目下载内容时，请自行确认内容授权和站点条款。
