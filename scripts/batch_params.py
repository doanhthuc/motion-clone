#!/usr/bin/env python3
"""In bảng param của một job type, và làm cổng chống trôi.

    python3 scripts/batch_params.py motion      # bảng param
    python3 scripts/batch_params.py --check     # cổng (make check-batch-params)
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.params import check_drift, extract_from_ast, known_params, load_curated

ROOT = Path(__file__).resolve().parents[1]
LINUX_PY = ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = ROOT / "scripts" / "batch-params.json"


def main(argv: list[str]) -> int:
    if not LINUX_PY.is_file():
        print(f"✗ không thấy {LINUX_PY.relative_to(ROOT)}", file=sys.stderr)
        print(
            "  Hoặc bạn đang chạy lệnh này từ ngoài thư mục repo (cd vào "
            f"{ROOT} rồi chạy lại), hoặc motions-studio/ chưa được checkout/rsync "
            "về máy này — kéo submodule/thư mục đó về trước.",
            file=sys.stderr,
        )
        return 1

    if "--check" in argv:
        errors = check_drift(LINUX_PY, CURATED)
        if errors:
            print("✗ batch-params.json đã trôi khỏi linux.py:", file=sys.stderr)
            for e in errors:
                print(f"    {e}", file=sys.stderr)
            return 1
        print("✓ batch-params.json khớp linux.py")
        return 0

    ast_params = extract_from_ast(LINUX_PY)
    curated = load_curated(CURATED)
    job_type = next((a for a in argv if not a.startswith("-")), "")
    if not job_type:
        print(f"Job type có param: {', '.join(sorted(ast_params))}")
        print("Dùng: make batch-params TYPE=motion")
        return 0
    if job_type not in ast_params and job_type not in curated:
        available = sorted(set(ast_params) | set(curated))
        near = difflib.get_close_matches(job_type, available, n=3, cutoff=0.6)
        hint = f" — ý bạn là {', '.join(near)}?" if near else ""
        print(f"✗ không có job type {job_type!r}{hint}", file=sys.stderr)
        print(f"  Job type có param: {', '.join(available)}", file=sys.stderr)
        return 1

    known = known_params(job_type, ast_params=ast_params, curated=curated)
    allowed = curated.get(job_type, {}).get("allowed", {}) or {}
    print(f"{job_type} — {len(known)} param\n")
    print(f"  {'param':28} {'mặc định':18} {'giá trị hợp lệ':26} nguồn")
    print(f"  {'-' * 28} {'-' * 18} {'-' * 26} -----")
    api_block = curated.get(job_type, {}).get("api", {}) or {}
    for name in sorted(known):
        info = known[name]
        values = "/".join(str(v) if v != "" else "(rỗng)" for v in allowed.get(name, [])) or "—"
        if info.source == "ast":
            where = f"linux.py:{info.line}"
        elif info.source == "api":
            where = str(api_block.get(name, {}).get("where") or "tầng API")
        else:
            where = f"linux.py:{info.line} (đọc động)"
        print(f"  {name:28} {str(info.default):18} {values:26} {where}")
    # Hai khối dưới đây là phần bảng param KHÔNG nói được: param hợp lệ, giá trị hợp lệ,
    # nhưng API vẫn bỏ qua hoặc ép lại. In thẳng ra đây để không ai phải đi đọc jobs.js
    # mới biết knob mình vừa gõ có tác dụng hay không.
    overridden = curated.get(job_type, {}).get("overridden", {}) or {}
    if overridden:
        print("\n  API ÉP GIÁ TRỊ — gõ gì cũng mất:")
        for name, meta in sorted(overridden.items()):
            print(f"    {name} → luôn {str(meta.get('forced'))!r}   ({meta.get('where') or 'tầng API'})")
            print(f"      {meta.get('why') or ''}")
            print(f"      Đường khác: {meta.get('escape') or 'không có'}")
    requires = curated.get(job_type, {}).get("requires", {}) or {}
    if requires:
        print("\n  CHỈ CÓ TÁC DỤNG KHI:")
        for name, meta in sorted(requires.items()):
            values = " | ".join(str(v) for v in (meta.get("values") or []))
            print(f"    {name} cần {meta.get('param')} là {values}   "
                  f"({meta.get('where') or 'tầng API'})")
            print(f"      {meta.get('why') or ''}")

    print("\n  Đọc comment gốc ở đúng dòng trên — repo này comment dày, đó là tài liệu thật.")
    if api_block:
        print("  Dòng 'tầng API' là param worker KHÔNG đọc: jobs.js dịch/tiêu thụ nó trước khi ghi DB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
