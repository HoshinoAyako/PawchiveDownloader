# CI 工具

## 日志分析 Agent

`ci_log_agent.py` 是仓库内的确定性日志分析 Agent，不调用外部模型，也不需要 Token。

- 输入：`tools/smoke_test.py` 或 GitHub Actions 生成的日志文本。
- 识别：`[SMOKE] PASS`、`[SMOKE] FAIL`、`[SMOKE] SUMMARY`、Traceback 与常见异常关键字。
- 输出：问题摘要、严重级别、证据行号、建议、单条发现置信度、整体置信度。
- 置信度：有明确摘要且无失败为 `0.98`；有明确 FAIL 与 Traceback 的阻断项为 `0.95`；摘要缺失会降到 `0.55` 或 `0.35`，避免把不完整日志当成结论。
- 限制：只依据日志文本做规则判断；网络、权限、真实 Release 更新仍需端到端验证。

本地运行：

```powershell
python tools/smoke_test.py 2>&1 | Tee-Object -FilePath build/ci/smoke.log
python tools/ci_log_agent.py build/ci/smoke.log --report build/ci/ci-report.md
```

GitHub Actions 在 `.github/workflows/ci.yml` 中运行烟雾测试，上传 `build/ci/`，并把结论写入运行摘要。

## 烟雾测试

`smoke_test.py` 的目标是几秒内覆盖最容易回归的路径：

- 模块导入与版本/CHANGELOG 一致性
- 版本比较、Release digest 解析
- 本地 HTTP 下载、进度回调与 SHA-256
- 强制覆盖式分块替换、目标文件回读校验、回滚、旧备份与旧 `.sha256` 清理
- 更新状态文件一次性读写、设置范围、State 失败记录清理、三种预览模式卡片
- 关于弹窗、主界面关于入口和产品图标

它不访问 GitHub，不启动真实更新，也不会替换正在运行的程序，因此适合本地反复运行。外置更新器复制当前 exe 后的实际替换需单独做端到端验证。

## Release 校验文件

```powershell
python tools/prepare_release.py dist\PawchiveDownloader.exe --asset-name PawchiveDownloader-v1.14.0.exe
```

输出 `dist\SHA256SUMS.txt`。发布时把 exe 和校验文件一起上传到 GitHub Release；程序更新器会拒绝没有校验信息的更新包。
