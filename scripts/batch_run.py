#!/usr/bin/env python3
"""Chạy một lô.

    make batch-validate FILE=batch/2026-08-18.yaml   # chỉ kiểm, không tiêu GPU
    make batch          FILE=batch/2026-08-18.yaml
    make batch          FILE=batch/2026-08-18.yaml RESUME=1
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.client import health_ok
from batchlib.config import ConfigError, load_settings
from batchlib.manifest import ManifestError, load_manifest, validate_manifest
from batchlib.params import extract_from_ast, load_curated
from batchlib.runner import batch_id_now, run_batch

ROOT = Path(__file__).resolve().parents[1]
LINUX_PY = ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = ROOT / "scripts" / "batch-params.json"


def preflight(settings, *, allow_start: bool) -> bool:
    if health_ok(settings):
        print(f"  ✓ pod đang chạy → {settings.base_url}")
        return True
    if not settings.instance_id:
        print("✗ Backend không trả lời, và .env chưa có GPU_INSTANCE_ID.\n"
              "  Chưa thuê pod thì chạy: make gpu-provision   (nó hỏi xác nhận trước khi tiêu tiền)",
              file=sys.stderr)
        return False
    if not allow_start:
        print("✗ Backend không trả lời. Chạy: make gpu-up", file=sys.stderr)
        return False
    print("  pod đang dừng → make gpu-up (bật là thao tác đảo được, nên tự làm)")
    if subprocess.run(["make", "gpu-up"], cwd=ROOT).returncode != 0:
        print("✗ make gpu-up thất bại — đọc output ở trên", file=sys.stderr)
        return False
    return health_ok(settings)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Chạy một lô material")
    ap.add_argument("--file", required=True)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--no-start", action="store_true", help="không tự make gpu-up")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.file))
    except ManifestError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    errors = validate_manifest(manifest, ast_params=extract_from_ast(LINUX_PY),
                               curated=load_curated(CURATED))
    if errors:
        print(f"✗ {len(errors)} lỗi trong {args.file} — chưa tiêu đồng GPU nào:", file=sys.stderr)
        for e in errors:
            print(f"    {e}", file=sys.stderr)
        return 1
    print(f"  ✓ manifest hợp lệ · {len(manifest.runs)} run")
    if args.validate_only:
        return 0

    try:
        settings = load_settings(ROOT)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    if not preflight(settings, allow_start=not args.no_start):
        return 1

    # submit_job/download_output (batchlib/client.py) CỐ Ý để URLError/OSError rơi thẳng
    # ra ngoài — chỉ poll_job tự bắt rớt mạng. Ở đây là nơi cuối cùng bắt nó: rớt Wi-Fi
    # giữa lô KHÔNG được biến thành traceback trần trụi, và job vừa gửi có thể vẫn đang
    # chạy trên pod — tuyệt đối không được khuyên gửi lại, vì gửi lại là trả tiền GPU
    # hai lần cho cùng một việc.
    try:
        result = run_batch(settings=settings, manifest=manifest, out_root=ROOT / "out",
                           batch_id=batch_id_now(), resume=args.resume, fail_fast=args.fail_fast)
    except (urllib.error.URLError, OSError) as exc:
        print(f"\n✗ Mất kết nối tới pod giữa chừng: {exc}", file=sys.stderr)
        print("  Job vừa gửi có thể VẪN đang chạy trên pod — đừng chạy lại từ đầu (tốn tiền GPU hai lần).",
              file=sys.stderr)
        print(f"  Kiểm tra mạng/pod rồi chạy lại: make batch FILE={args.file} RESUME=1", file=sys.stderr)
        return 1

    minutes = result.gpu_seconds / 60
    print(f"\n  Lô {result.batch_id}: {len(result.done)} xong · {len(result.failed)} hỏng "
          f"· ~{minutes:.0f} phút GPU")
    print(f"  Kết quả: {result.out_dir / '_final'}")
    print(f"  Bảng tra: {result.out_dir / '_index.tsv'}")
    for run_id, why in result.failed.items():
        print(f"    ✗ {run_id}: {why}")

    if result.failed:
        print("\n  Pod VẪN chạy — có run hỏng thì đây đúng là lúc cần nó nhất:")
        print("      make gpu-logs LOG=worker")
        print(f"      make batch FILE={args.file} RESUME=1     (chỉ chạy lại phần thiếu)")
        return 1

    print("\n  Xong hết. Pod vẫn đang chạy và vẫn tính tiền — tắt khi không dùng nữa:")
    print("      make gpu-destroy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
