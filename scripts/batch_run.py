#!/usr/bin/env python3
"""Chạy một lô.

    make batch-validate FILE=batch/2026-08-18.yaml   # chỉ kiểm, không tiêu GPU
    make batch          FILE=batch/2026-08-18.yaml
    make batch          FILE=batch/2026-08-18.yaml RESUME=1
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.client import health_ok
from batchlib.config import ConfigError, load_settings
from batchlib.manifest import ManifestError, load_manifest, load_state, state_path_for, validate_manifest
from batchlib.params import extract_from_ast, load_curated, missing_source_hint
from batchlib.runner import batch_id_now, run_batch

ROOT = Path(__file__).resolve().parents[1]
LINUX_PY = ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = ROOT / "scripts" / "batch-params.json"


@dataclass(frozen=True)
class BatchIdDecision:
    batch_id: str
    resumed: bool   # True nếu đang tiếp tục một lô có thật đã tồn tại trong state
    note: str       # câu in ra cho người dùng biết quyết định này, trước khi tốn GPU


def resolve_batch_id(manifest_path: Path, *, resume: bool,
                      now: _dt.datetime | None = None) -> BatchIdDecision:
    """--resume phải chạy TIẾP vào out_dir CŨ, không phải mint một out_dir mới.

    Bug đã xảy ra: main() từng gọi batch_id_now() vô điều kiện, kể cả khi resume.
    run_batch tự ghi batch id đã dùng vào state["batch"] (runner.py) — không đọc
    lại nó thì mỗi lần --resume lại có một thư mục MỚI, rỗng: run đã xong không
    được hardlink vào _final/ mới (run_batch chỉ 'continue' qua nó, không gọi lại
    run_one để tạo hardlink), và run dở thì các chặng đã xong bị coi là "chưa có
    file nào ở đây" nên chạy lại TỪ ĐẦU — đúng hai lần tiền GPU mà --resume tồn
    tại để tránh.

    Ba nhánh, tường minh:
      - resume + state có batch id → dùng lại nguyên id đó.
      - resume + không có state (hoặc state không có khoá "batch") → đây là lần
        chạy ĐẦU, không có gì để tiếp — lô mới, và phải NÓI RÕ điều đó thay vì
        im lặng coi như đang resume một thứ không tồn tại.
      - resume + state có id, nhưng thư mục out/<id>/ đã bị xoá tay (vd
        `make batch-clean` chỉ xoá runs/ và giữ _final/) → VẪN dùng lại đúng id
        đó, không bịa id mới. Chặng nào mất file thì run_one tự phát hiện qua
        dest.is_file() và chạy lại chặng đó; bịa id mới ở đây sẽ mồ côi _final/
        cũ đang tồn tại thật.
    """
    if not resume:
        return BatchIdDecision(batch_id_now(now), resumed=False, note="lô mới")

    state = load_state(state_path_for(manifest_path))
    batch_id = state.get("batch")
    if not batch_id:
        fresh = batch_id_now(now)
        return BatchIdDecision(
            fresh, resumed=False,
            note=f"RESUME=1 nhưng chưa có lô nào ghi lại để tiếp — chạy như lô MỚI: {fresh}",
        )
    return BatchIdDecision(batch_id, resumed=True, note=f"tiếp tục lô {batch_id} (RESUME=1)")


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

    # Hai nguồn của bảng param. Không chặn ở đây thì extract_from_ast/load_curated ném
    # FileNotFoundError trần — không nói repo thiếu gì, cũng không nói làm gì tiếp.
    # batch_params.py đã có đúng câu đó cho đúng tình huống này; dùng lại nó.
    for source in (LINUX_PY, CURATED):
        if not source.is_file():
            print(f"✗ {missing_source_hint(source, ROOT)}", file=sys.stderr)
            print("  Không tra được bảng param thì không validate được manifest — mà validate "
                  "chính là thứ chặn lỗi TRƯỚC khi tiêu GPU.", file=sys.stderr)
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

    # --resume PHẢI chạy tiếp vào out_dir CŨ (xem resolve_batch_id) — mint id mới ở đây
    # từng là bug: run đã xong không được hardlink vào _final/ mới, run dở thì chạy lại
    # từ đầu. In ra quyết định khi resume để người dùng thấy nó không bắt đầu lại từ đầu
    # (hoặc biết rõ vì sao nó lại bắt đầu từ đầu, nếu quả thật chưa có gì để tiếp).
    decision = resolve_batch_id(manifest.path, resume=args.resume)
    if args.resume:
        print(f"  {decision.note}")

    # submit_job/download_output (batchlib/client.py) CỐ Ý để URLError/OSError rơi thẳng
    # ra ngoài — chỉ poll_job tự bắt rớt mạng. Ở đây là nơi cuối cùng bắt nó: rớt Wi-Fi
    # giữa lô KHÔNG được biến thành traceback trần trụi, và job vừa gửi có thể vẫn đang
    # chạy trên pod — tuyệt đối không được khuyên gửi lại, vì gửi lại là trả tiền GPU
    # hai lần cho cùng một việc.
    try:
        # resume=decision.resumed, KHÔNG phải args.resume: hai giá trị đó lệch nhau ở đúng
        # nhánh "RESUME=1 nhưng chưa có lô nào để tiếp" (resolve_batch_id nhánh 2). Ở đó
        # batch_id là id MỚI, nên truyền resume=True lại là dựng lô mới bằng journal cũ:
        # run đã "done" từ lô trước bị bỏ qua, không được hardlink vào _final/ mới, và
        # chặng đã xong bị coi là mất file nên chạy lại — đúng bug đã sửa một lần rồi.
        # Nó chưa nổ được chỉ vì run_batch luôn ghi state["batch"], tức một invariant ở
        # FILE KHÁC, không phải một rào chắn ở đây.
        result = run_batch(settings=settings, manifest=manifest, out_root=ROOT / "out",
                           batch_id=decision.batch_id, resume=decision.resumed,
                           fail_fast=args.fail_fast)
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
