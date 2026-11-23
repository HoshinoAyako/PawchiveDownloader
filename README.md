# Pawchive Downloader

Pawchive 帖子的 Windows 桌面批量下载器，提供创作者帖子加载、附件筛选、并发控制、保存目录管理、日志导出和预览图/原图处理。

当前版本：`v1.12.0`

版本号规则：自 `1.9.0` 起遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)；不兼容变更升主版本，向下兼容的新功能升次版本，向下兼容的问题修正升修订号。完整规则见 `CHANGELOG.md`。

## 功能

- 创作者 URL 加载，支持帖子列表全选/反选
- 文件并发、帖子并发与站点限速提示
- 附件类型筛选：视频、音频、文档、压缩包、设计稿、未知扩展名
- 图片预览图/原图下载选择
- 预览图完整性校验：发现本地文件小于远端原图时可确认替换
- 主保存目录 + 多个额外保存目录
- 深色/浅色主题、日志导出、失败文件重试/清除与临时目录预览
- 帖子列表与保存目录共用「仅文字 / 缩略图 / 全部展示」三种预览，切换时不重建卡片、不丢失已加载图片
- 在线预览图磁盘缓存：默认 32 MB，可在设置 → 性能调整（0–512 MB），位于 `PawchiveDownload/Temp/PreviewCache`；设置页可单独清除缓存
- 临时目录清理支持勾选 `.part`、预览图缓存和 State 下载记录，按需删除
- 帖子页「刷新预览」按钮，可重新拉取当前可见帖子的预览图
- 失败自动重试次数与间隔可在设置 → 性能调整
- 启动自检：首次或主/次版本变化时检查环境、权限、路径、磁盘、资源和配置
- 关于页：产品说明、项目地址、当前版本公告与 GitHub Release 检查更新
- 程序更新：外置更新器等待主程序退出后替换，备份/重试/回滚/重启，更新包与目标文件双重 SHA-256 校验
- GitHub Actions 烟雾测试与确定性日志分析报告

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

## 程序更新

主界面“关于”弹窗中的“检查更新”通过 GitHub Release 执行更新：

1. 预检 `api.github.com` 的 DNS 解析、TLS 与 HTTPS 请求；失败时写明阶段和原因，并在主界面日志页用亮色文字提醒。
2. 读取 latest Release，比较版本号；只接受 `.exe` 更新包。
3. 更新包必须带 `SHA256SUMS.txt`、其他校验清单或 Release asset `digest`；缺失时拒绝更新。
4. 下载时按字节显示进度并校验 SHA-256。
5. 校验当前程序的 PE 结构、大小与可写性，下载并校验更新包 SHA-256。
6. 复制当前 exe 到 Temp 作为独立更新器；主程序启动它后退出。更新器等待父进程结束，备份旧程序，重试替换；每次替换后回读大小、PE 结构与 SHA-256，失败自动回滚。
7. 更新器写入一次性状态文件，成功则启动新程序；下次启动清理 `.old` 备份、更新器临时目录并读取上次状态。

发布新版本时，请在 Release 附件中同时上传 exe 与 `SHA256SUMS.txt`：

```text
<64 位 SHA-256>  PawchiveDownloader-vX.Y.Z.exe
```

可用 `python tools/prepare_release.py dist\PawchiveDownloader.exe --asset-name PawchiveDownloader-v1.12.0.exe` 生成校验文件。
仓库需要是 Public，程序才能匿名读取 latest Release；GitHub API 有每 IP 每小时 60 次的匿名限额。

新版更新器不再在 exe 同目录生成 `<exe 名>.sha256` 安装基线；旧版本留下的该文件会在启动时删除。更新包 SHA-256 校验与写入后的目标文件回读校验仍然保留。

## 持续集成与日志分析

- `.github/workflows/ci.yml` 在 push、pull request 和手动触发时运行 Windows 烟雾测试。
- `python tools/smoke_test.py` 覆盖模块导入、预览模式、重试参数、状态清理、关于页、版本规则、下载与更新替换路径。
- `python tools/ci_log_agent.py <日志> --report build/ci/ci-report.md` 生成确定性问题摘要、严重级别、证据、建议和置信度；CI 会上传日志与报告并写入运行摘要。分析不依赖外部模型或 Token。

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
