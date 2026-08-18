#!/usr/bin/env python3
"""Quét thư mục material → đẻ manifest nháp để bạn soát.

    make batch-scan DIR=~/materials MODE=pair
    make batch-scan DIR=~/materials MODE=cross OUT=batch/thu-nghiem.yaml

Manifest sinh ra là BẢN NHÁP, không phải lệnh chạy: bạn xoá bớt dòng không muốn
rồi mới `make batch`. Đó là lý do bước này tách khỏi bước chạy — đoán sai chỉ
tốn một lần liếc mắt, không tốn tiền GPU.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.manifest import dump_runs
from batchlib.scan import ROLE_DIRS, ScanError, build_runs, collect

ROOT = Path(__file__).resolve().parents[1]

HEADER = """\
# Sinh bởi: make batch-scan DIR={dir} MODE={mode}
# Đây là BẢN NHÁP — xoá bớt run không muốn, chỉnh param, rồi:
#     make batch-validate FILE={out}
#     make batch FILE={out}
#
# Param đều TÙY CHỌN. Bỏ trống = chạy đúng mặc định như bấm UI.
# Tra param: make batch-params TYPE=motion   (hoặc tryon / enhance)
#
# Ví dụ thêm vào một run:
#     motion:  {{ preset: drv-30s }}
#     enhance: {{ targetRes: 1080p, fpsInterp: "60" }}
#
# Hoặc áp cho MỌI run bằng khối defaults ở đầu file:
#     defaults:
#       enhance: {{ targetRes: 1080p, fpsInterp: "60" }}
"""

# Ước lượng THÔ để cảnh báo quy mô, không phải để hứa hẹn. Số đo 17/08/2026 trên
# ab-results/run1: motion 81 frame ≈ 3 phút. Clip drv-30s dài gấp ~6 lần, cộng
# enhance 1080p60 thường lâu hơn chính motion sinh ra nó.
MINUTES_PER_RUN = {"motion-enhance": 25, "tryon-motion-enhance": 30}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Quét thư mục material → manifest nháp")
    ap.add_argument("--dir", required=True, help="thư mục chứa 4 ngăn: " + ", ".join(ROLE_DIRS.values()))
    ap.add_argument("--mode", default="pair", help="pair (ghép theo thứ tự) | cross (tích Descartes)")
    ap.add_argument("--out", default="", help="mặc định batch/<hôm nay>.yaml")
    ap.add_argument("--force", action="store_true", help="cho phép ghi đè file đã có")
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else ROOT / "batch" / f"{date.today():%Y-%m-%d}.yaml"
    if out.exists() and not args.force:
        print(f"✗ {out} đã tồn tại. Đặt --out khác, hoặc --force nếu chắc chắn muốn đè.",
              file=sys.stderr)
        return 1

    try:
        runs = build_runs(collect(Path(args.dir)), args.mode)
    except ScanError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    minutes = sum(MINUTES_PER_RUN.get(r.pipeline, 30) for r in runs)
    print(f"  {len(runs)} run · pipeline {runs[0].pipeline} · MODE={args.mode}")
    print(f"  Ước tính THÔ: ~{minutes} phút GPU (~{minutes / 60:.1f} giờ). Con số này để bạn")
    print("  giật mình khi cross đẻ ra 60 run, không phải để tin.")
    if len(runs) > 20:
        print(f"  ⚠ {len(runs)} run là nhiều. Soát kỹ file trước khi make batch.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        HEADER.format(dir=args.dir, mode=args.mode, out=out) + "\n" + dump_runs(runs),
        encoding="utf-8",
    )
    print(f"\n  → {out}")
    print(f"  Soát xong thì: make batch-validate FILE={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
