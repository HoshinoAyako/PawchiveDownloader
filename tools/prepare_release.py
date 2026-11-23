"""为 GitHub Release 生成 SHA256SUMS.txt。"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pawchive.updater import _has_pe_header


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Release 用的 SHA256SUMS.txt")
    parser.add_argument("paths", nargs="+", type=Path, help="要发布的 exe 文件")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/SHA256SUMS.txt"),
        help="输出校验文件路径",
    )
    parser.add_argument(
        "--asset-name",
        default="",
        help="单个文件在 Release 中的名称；文件名与发布名不同时使用",
    )
    args = parser.parse_args(argv)
    if args.asset_name and len(args.paths) != 1:
        parser.error("--asset-name 只能和单个文件一起使用")

    lines: list[str] = []
    for path in args.paths:
        if not path.is_file():
            parser.error(f"文件不存在：{path}")
        if path.stat().st_size < 1024 * 1024:
            parser.error(f"文件小于 1 MB，疑似不是完整程序：{path}")
        if not _has_pe_header(path):
            parser.error(f"文件不是有效的 Windows PE 可执行文件：{path}")
        name = args.asset_name or path.name
        digest = sha256(path)
        lines.append(f"{digest}  {name}")
        print(f"{digest}  {name}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
