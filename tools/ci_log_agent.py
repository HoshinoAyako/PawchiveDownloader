"""Pawchive Downloader CI 日志分析 Agent。

确定性规则分析，不依赖外部模型或 Token。读取烟雾测试日志，输出：
问题摘要、严重级别、证据行、建议、以及每条结论和整体结论的置信度。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_RE = re.compile(
    r"\[SMOKE\] SUMMARY total=(\d+) passed=(\d+) failed=(\d+) duration_ms=(\d+)"
)
PASS_RE = re.compile(r"\[SMOKE\] PASS ([^\n]+)")
FAIL_RE = re.compile(r"\[SMOKE\] FAIL ([^\n]+)")
SEVERITY_ORDER = {"阻断": 0, "高": 1, "中": 2, "低": 3}


@dataclass
class Summary:
    total: int
    passed: int
    failed: int
    duration_ms: int


@dataclass
class Finding:
    title: str
    severity: str
    evidence: list[tuple[int, str]] = field(default_factory=list)
    suggestion: str = ""
    confidence: float = 0.0


def read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_summary(text: str) -> Summary | None:
    match = SUMMARY_RE.search(text)
    if not match:
        return None
    return Summary(*(int(value) for value in match.groups()))


def line_no(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def evidence_after(lines: list[str], start: int, limit: int = 7) -> list[tuple[int, str]]:
    evidence = []
    for index in range(start, min(len(lines), start + limit)):
        line = lines[index].rstrip()
        if not line:
            continue
        if line.startswith("[SMOKE]") and index != start:
            break
        evidence.append((index + 1, line))
    return evidence


def suggestion_for(title: str, evidence: list[tuple[int, str]]) -> str:
    text = "\n".join(line for _, line in evidence).lower()
    combined = (title + "\n" + text).lower()
    if "modulenotfound" in combined or "importerror" in combined:
        return "安装 `requirements.txt` 依赖，确认 CI 使用 Python 3.12 且 `pip install` 步骤成功。"
    if "updateerror" in combined or "sha" in combined or "checksum" in combined:
        return "检查 Release 的 `SHA256SUMS.txt` 或 asset `digest`，确认校验值与实际 exe 一致。"
    if "qt" in combined or "pyqt" in combined or "about" in combined or "icon" in combined:
        return "检查 PyQt6 安装、`QT_QPA_PLATFORM=offscreen`、`assets/icon.ico` 与关于页资源。"
    if "permission" in combined or "oserror" in combined or "拒绝访问" in combined:
        return "检查目标目录权限、文件占用，以及 Windows 是否允许重命名正在运行的程序。"
    if "download" in combined or "http" in combined or "timeout" in combined:
        return "检查本地测试 HTTP 服务、防火墙、读取超时与流式下载进度回调。"
    if "traceback" in combined:
        return "查看证据中的异常末行，先复现该检查项，再修复阻断项。"
    return "按证据行复现；修复阻断项后重新运行 `python tools/smoke_test.py`。"


def build_findings(text: str, summary: Summary | None) -> list[Finding]:
    lines = text.splitlines()
    findings: list[Finding] = []
    for index, line in enumerate(lines):
        match = FAIL_RE.search(line)
        if not match:
            continue
        title = match.group(1).strip()
        evidence = evidence_after(lines, index)
        has_traceback = any("Traceback" in item for _, item in evidence)
        findings.append(
            Finding(
                title=f"烟雾测试失败：{title}",
                severity="阻断",
                evidence=evidence,
                suggestion=suggestion_for(title, evidence),
                confidence=0.95 if has_traceback else 0.85,
            )
        )

    if summary is not None and summary.failed > 0 and not findings:
        findings.append(
            Finding(
                title="烟雾测试摘要报告失败，但日志中没有找到 FAIL 行",
                severity="高",
                evidence=[(1, f"failed={summary.failed}")],
                suggestion="检查日志是否被截断，或烟雾测试在打印摘要前异常退出。",
                confidence=0.7,
            )
        )

    if summary is None:
        index = next(
            (i for i, line in enumerate(lines) if "Traceback (most recent call last)" in line),
            None,
        )
        evidence = evidence_after(lines, index) if index is not None else []
        findings.append(
            Finding(
                title="烟雾测试摘要缺失",
                severity="高" if index is not None else "中",
                evidence=evidence or [(1, "未找到 [SMOKE] SUMMARY")],
                suggestion="确认 `tools/smoke_test.py` 完整执行，并检查 CI 日志尾部是否被截断。",
                confidence=0.8 if index is not None else 0.55,
            )
        )
    return sorted(findings, key=lambda item: SEVERITY_ORDER.get(item.severity, 9))


def overall_confidence(summary: Summary | None, findings: list[Finding]) -> float:
    if summary is None:
        return 0.55 if findings else 0.35
    if summary.failed == 0 and not findings:
        return 0.98
    if findings and all(item.confidence >= 0.8 for item in findings):
        return 0.92
    return 0.7


def render_report(log_path: Path, text: str, summary: Summary | None, findings: list[Finding]) -> str:
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    passes = PASS_RE.findall(text)
    fails = FAIL_RE.findall(text)
    confidence = overall_confidence(summary, findings)
    if summary is None:
        result = "无法判定"
    elif summary.failed == 0 and not findings:
        result = "通过"
    else:
        result = "失败"

    report = [
        "# Pawchive Downloader CI 日志分析报告",
        "",
        f"- 生成时间：{generated}",
        f"- 日志文件：`{log_path.name}`",
        "- 分析 Agent：确定性规则分析（无外部模型 / Token）",
        f"- 烟雾测试结果：**{result}**",
        f"- 整体分析置信度：**{confidence:.2f}**",
        "",
        "## 汇总",
        "",
    ]
    if summary is None:
        report.extend(["| 项目 | 数值 |", "| --- | --- |", "| 检查总数 | 未获取 |"])
    else:
        report.extend(
            [
                "| 项目 | 数值 |",
                "| --- | --- |",
                f"| 检查总数 | {summary.total} |",
                f"| 通过 | {summary.passed} |",
                f"| 失败 | {summary.failed} |",
                f"| 耗时 | {summary.duration_ms} ms |",
            ]
        )
    if passes:
        report.extend(["", "通过项：", ""])
        report.extend(f"- {item}" for item in passes)
    if fails:
        report.extend(["", "失败项：", ""])
        report.extend(f"- {item}" for item in fails)

    report.extend(["", "## 发现与建议", ""])
    if not findings:
        report.extend(
            [
                "未发现失败信号。烟雾测试通过。",
                "",
                "- 严重级别：无",
                "- 建议：保持 CI 必过；新增功能时同步扩展 `tools/smoke_test.py` 覆盖项。",
                f"- 置信度：{confidence:.2f}",
            ]
        )
    else:
        for number, finding in enumerate(findings, 1):
            report.extend(
                [
                    f"### {number}. {finding.title}",
                    "",
                    f"- 严重级别：{finding.severity}",
                    f"- 建议：{finding.suggestion}",
                    f"- 置信度：{finding.confidence:.2f}",
                    "- 证据：",
                ]
            )
            if finding.evidence:
                for number_line, line in finding.evidence:
                    report.append(f"  - L{number_line}: `{line.replace('`', '')}`")
            else:
                report.append("  - 无可用证据行")
            report.append("")

    report.extend(
        [
            "## 结论",
            "",
            (
                "本次日志具备明确的烟雾测试摘要，结论可直接用于合并判断。"
                if summary is not None
                else "日志缺少完整的烟雾测试摘要，结论只能作为排查线索，不能替代复现。"
            ),
            "",
            "分析 Agent 只依据日志文本与确定性规则给结论；网络、权限与真实 Release 更新仍需人工或端到端验证。",
        ]
    )
    return "\n".join(report).rstrip() + "\n"


def render_step_summary(result: str, confidence: float, findings: list[Finding]) -> str:
    section = [
        "## Pawchive CI 日志分析 Agent",
        "",
        f"- 结果：**{result}**",
        f"- 分析置信度：**{confidence:.2f}**",
        f"- 发现数：{len(findings)}",
    ]
    if findings:
        section.extend(["", "阻断项：" if any(f.severity == "阻断" for f in findings) else "", ""])
        section.extend(f"- {finding.title}（{finding.severity}）" for finding in findings[:6])
    section.append("")
    return "\n".join(section)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分析 Pawchive Downloader CI 烟雾测试日志")
    parser.add_argument("log", type=Path, help="烟雾测试日志路径")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("build/ci/ci-report.md"),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="可选：追加写入 GitHub Actions Step Summary",
    )
    args = parser.parse_args(argv)
    if not args.log.is_file():
        print(f"日志不存在：{args.log}", file=sys.stderr)
        return 2

    text = read_log(args.log)
    summary = parse_summary(text)
    findings = build_findings(text, summary)
    confidence = overall_confidence(summary, findings)
    result = (
        "无法判定"
        if summary is None
        else "通过"
        if summary.failed == 0 and not findings
        else "失败"
    )

    report = render_report(args.log, text, summary, findings)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"分析完成：{args.report}")
    print(f"结果：{result}；发现 {len(findings)} 项；置信度 {confidence:.2f}")

    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(render_step_summary(result, confidence, findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
