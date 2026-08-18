#!/usr/bin/env python3
"""Xoá file TRUNG GIAN của các lô cũ. Không bao giờ đụng _final/.

    make batch-clean            # giữ 3 lô gần nhất
    make batch-clean KEEP=1
    make batch-clean DRY=1      # chỉ liệt kê

Vì sao chỉ xoá runs/: file trung gian tồn tại để trả lời "tryon ra ảnh gì" khi
kết quả cuối xấu. Sau vài lô thì câu hỏi đó không còn ai hỏi nữa, nhưng bản
cuối thì vẫn phải giữ — nên hai thứ có vòng đời khác nhau và được dọn khác nhau.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prune(out_root: Path, keep: int, dry_run: bool = False) -> list[Path]:
    if keep < 1:
        raise ValueError("KEEP phải ≥ 1 — giữ 0 lô nghĩa là xoá cả lô vừa chạy xong")
    if not out_root.is_dir():
        return []
    batches = sorted(
        (p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: p.name,
    )
    removed: list[Path] = []
    for batch in batches[:-keep] if len(batches) > keep else []:
        runs = batch / "runs"
        if not runs.is_dir():
            continue
        removed.append(runs)
        if not dry_run:
            shutil.rmtree(runs)
    return removed


def main(argv: list[str]) -> int:
    # --keep nhận str, tự parse int ở đây (không để argparse type=int làm) — vì
    # lỗi của argparse thoát bằng SystemExit(2) và in tiếng Anh, tránh mất luôn
    # khối try/except ValueError bên dưới cùng thông điệp tiếng Việt.
    ap = argparse.ArgumentParser(description="Xoá file trung gian của lô cũ")
    ap.add_argument("--keep", default="3")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        keep = int(args.keep)
    except ValueError:
        print(
            f"✗ KEEP='{args.keep}' không phải số nguyên — cần một số ≥ 1, "
            f"ví dụ: make batch-clean KEEP=3",
            file=sys.stderr,
        )
        return 1

    try:
        removed = prune(ROOT / "out", keep, args.dry_run)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    verb = "sẽ xoá" if args.dry_run else "đã xoá"
    if not removed:
        print(f"  Không có gì để dọn (giữ {keep} lô gần nhất)")
        return 0
    for path in removed:
        print(f"  {verb}: {path.relative_to(ROOT)}")
    print(f"  _final/ của các lô đó được giữ nguyên.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
