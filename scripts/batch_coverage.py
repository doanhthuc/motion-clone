#!/usr/bin/env python3
"""Dòng nào của batch runner KHÔNG có test nào chạm tới.

    make batch-coverage            # bảng tổng + dòng chưa chạy của file tệ nhất
    make batch-coverage FULL=1     # liệt kê dòng chưa chạy của MỌI file

VÌ SAO CÓ FILE NÀY — nó trả lời câu hỏi mà cả một phiên xây dựng runner này không ai hỏi:
"dòng nào không test nào chạm tới?". Hậu quả của việc không hỏi, đo được 18/08/2026:

    batchlib/*  (7 module)   696 dòng · 659 chạy · 37 không
    batch_scan.py             40 dòng ·   0 chạy · 40 không   ← không một dòng nào
    batch_params.py           72 dòng ·  25 chạy · 47 không
    batch_run.py             104 dòng ·  72 chạy · 32 không
    batch_clean.py            45 dòng ·  22 chạy · 23 không

Thư viện kín, bốn CLI entry point gần như trắng. Và ĐÚNG hai bug đắt nhất đều sống trong
vùng trắng đó:
  - `--resume` mint batch id mới (batch_run.py): 99 test xanh, không cái nào đi qua dòng đó.
    Chỉ review toàn nhánh bắt được, sau 8 task.
  - Cloudflare chặn User-Agent của urllib (đường _request, chỉ chạm được qua CLI thật):
    143 test xanh, mà runner không nói được với pod một câu nào.

Cả hai không phải "khó test". Chúng là "không ai đếm xem cái gì chưa được test".

Dùng `trace` của stdlib, KHÔNG phải coverage.py — máy dev này chỉ có stdlib + PyYAML, và
thêm một dependency chỉ để đếm dòng thì lại là một thứ nữa phải cài trước khi dùng được.
`trace` không đo nhánh (branch), nên bảng này là SÀN chứ không phải trần: dòng có chạy
không có nghĩa cả hai nhánh if của nó đã chạy.
"""
from __future__ import annotations

import ast
import collections
import io
import os
import sys
import trace
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "batchlib"
# KHÔNG đưa batch_coverage.py vào: công cụ đo tự đo mình là nhiễu, và nó không phải
# bề mặt sản phẩm — không pod nào, không lô nào phụ thuộc nó.
CLI = ["batch_run.py", "batch_scan.py", "batch_params.py", "batch_clean.py"]


def statement_lines(path: Path) -> set[int]:
    """Dòng đầu của mỗi statement — xấp xỉ "dòng code thật", bỏ comment/docstring/dòng trắng."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt)}


def run_suite_traced() -> dict[str, set[int]]:
    sys.path.insert(0, str(ROOT / "scripts"))
    tracer = trace.Trace(count=1, trace=0,
                         ignoremods=("unittest", "trace", "threading", "socketserver", "http", "yaml"))

    def go():
        suite = unittest.TestLoader().discover(str(ROOT / "scripts" / "tests"), pattern="test_batch_*.py")
        unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)

    tracer.runfunc(go)
    hit: dict[str, set[int]] = collections.defaultdict(set)
    for filename, lineno in tracer.results().counts:
        hit[os.path.realpath(filename)].add(lineno)
    return hit


def main(argv: list[str]) -> int:
    full = "--full" in argv
    hit = run_suite_traced()

    targets = sorted(p for p in LIB.glob("*.py") if p.name != "__init__.py")
    targets += [ROOT / "scripts" / c for c in CLI if (ROOT / "scripts" / c).is_file()]

    rows, worst = [], []
    for t in targets:
        stmts = statement_lines(t)
        covered = hit.get(os.path.realpath(t), set())
        miss = sorted(stmts - covered)
        rows.append((t.relative_to(ROOT), len(stmts), len(stmts) - len(miss), miss))
        if miss:
            worst.append((len(miss), t.relative_to(ROOT), miss))

    print(f"  {'file':38} {'dòng':>6} {'chạy':>6} {'KHÔNG chạy':>11}")
    print(f"  {'-' * 38} {'-' * 6} {'-' * 6} {'-' * 11}")
    tot = run = 0
    for rel, n, c, miss in rows:
        tot += n
        run += c
        flag = "  ← 0%" if c == 0 else ""
        print(f"  {str(rel):38} {n:>6} {c:>6} {len(miss):>11}{flag}")
    print(f"  {'-' * 38} {'-' * 6} {'-' * 6} {'-' * 11}")
    pct = (run / tot * 100) if tot else 0
    print(f"  {'TỔNG':38} {tot:>6} {run:>6} {tot - run:>11}   ({pct:.1f}% dòng có chạy)")

    if full:
        for _, rel, miss in sorted(worst, reverse=True):
            print(f"\n  {rel} — dòng chưa chạy:\n    {miss}")
    elif worst:
        n, rel, miss = max(worst)
        print(f"\n  Tệ nhất: {rel} ({n} dòng chưa chạy)\n    {miss[:30]}{' …' if len(miss) > 30 else ''}")
        print("  Xem hết: make batch-coverage FULL=1")

    print("\n  `trace` không đo NHÁNH — dòng có chạy không có nghĩa cả hai nhánh if đã chạy.")
    print("  Con số này là SÀN của phần chưa kiểm, không phải trần.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
