"""Chạy một lô: từng run, từng chặng, ghi journal sau MỖI chặng.

Ghi journal sau mỗi chặng chứ không cuối run: một run ba chặng đứt ở chặng ba
thì resume chỉ chạy lại chặng ba. Ghi cuối run nghĩa là mất cả ba.

Tuần tự, không song song. Pod có một GPU, và run_enhance gọi comfy_recycle để xả
RAM/VRAM của Wan trước mỗi pha nặng (linux.py:9586/9650/9691/9740) — hai job
chồng nhau phá đúng giả định "lúc này GPU chỉ có mình tôi" mà các lời gọi đó dựa vào.
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .client import JobError, download_output, poll_job, submit_job
from .config import Settings
from .manifest import Manifest, Run, load_state, save_state, state_path_for
from .pipelines import PIPELINES, STAGES


@dataclass
class BatchResult:
    batch_id: str
    out_dir: Path
    done: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    gpu_seconds: int = 0


def batch_id_now(now: _dt.datetime | None = None) -> str:
    return f"{now or _dt.datetime.now():%Y-%m-%d-%H%M}"


def _resolve_files(run: Run, stage_name: str, prev_output: Path | None) -> dict[str, Path]:
    """Dịch Stage.inputs thành các file thật sẽ upload."""
    files: dict[str, Path] = {}
    for api_field, source in STAGES[stage_name].inputs.items():
        for alt in source.split("|"):
            if alt == "prev":
                if prev_output is not None:
                    files[api_field] = prev_output
                    break
                continue
            role = alt[len("material:"):]
            optional = role.endswith("?")
            role = role.rstrip("?")
            path = run.inputs.get(role)
            if path is not None:
                files[api_field] = path
                break
            if optional:
                break
        else:
            raise JobError(
                f"run {run.id!r} chặng {stage_name}: không có nguồn nào cho field {api_field!r} "
                f"(khai: {source})"
            )
    return files


def run_one(*, settings: Settings, run: Run, out_dir: Path, state: dict,
            state_file: Path, resume: bool,
            log: Callable[[str], None], now: Callable[[], float] = time.time) -> Path:
    run_dir = out_dir / "runs" / run.id
    run_dir.mkdir(parents=True, exist_ok=True)
    entry = state["runs"].setdefault(run.id, {"status": "pending", "stages": {}})
    entry["status"] = "running"

    prev_output: Path | None = None
    for index, stage_name in enumerate(PIPELINES[run.pipeline], start=1):
        stage = STAGES[stage_name]
        dest = run_dir / f"{index:02d}-{stage_name}{stage.output_ext}"
        recorded = entry["stages"].get(stage_name) or {}

        if resume and recorded.get("status") == "done" and dest.is_file():
            log(f"    {stage_name}: bỏ qua (đã xong, {dest.name})")
            prev_output = dest
            continue

        files = _resolve_files(run, stage_name, prev_output)
        params = run.stage_params.get(stage_name, {})
        started = now()
        log(f"    {stage_name}: gửi ({', '.join(f'{k}={v.name}' for k, v in files.items())})")
        job_id = submit_job(settings, stage.job_type, params, files)
        entry["stages"][stage_name] = {"job_id": job_id, "status": "running",
                                       "params_sent": params}
        save_state(state_file, state)

        try:
            poll_job(settings, job_id, stage.timeout_min,
                     on_progress=lambda d: log(
                         f"      {d.get('status')} {round((d.get('progress') or 0) * 100)}% "
                         f"{d.get('current_step') or ''}"))
            size = download_output(settings, job_id, dest, stage.min_bytes)
        except JobError:
            entry["stages"][stage_name]["status"] = "error"
            entry["stages"][stage_name]["elapsed_sec"] = int(now() - started)
            save_state(state_file, state)
            raise

        elapsed = int(now() - started)
        entry["stages"][stage_name].update(status="done", elapsed_sec=elapsed,
                                           file=str(dest), bytes=size)
        save_state(state_file, state)
        log(f"    {stage_name}: xong {elapsed}s · {size // 1024} KB → {dest.name}")
        prev_output = dest

    if prev_output is None:
        raise JobError(f"run {run.id!r}: pipeline không có chặng nào")

    final_dir = out_dir / "_final"
    final_dir.mkdir(parents=True, exist_ok=True)
    final = final_dir / f"{run.id}{prev_output.suffix}"
    if final.exists():
        final.unlink()
    try:
        final.hardlink_to(prev_output)
    except OSError:
        # Khác filesystem hoặc FS không hỗ trợ hardlink — chép, tốn đĩa nhưng vẫn đúng.
        shutil.copy2(prev_output, final)

    entry["status"] = "done"
    save_state(state_file, state)
    (run_dir / "run.json").write_text(
        json.dumps({"id": run.id, "pipeline": run.pipeline,
                    "inputs": {k: str(v) for k, v in run.inputs.items()},
                    "stages": entry["stages"], "final": str(final)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return final


def write_index(out_dir: Path, state: dict) -> None:
    """Một dòng mỗi CHẶNG, không phải mỗi run — giống hệt ab-results/run1/manifest.tsv
    mà người dùng đã tự tay dựng cho một phiên A/B thật (label/job_id/status/elapsed_sec/params).
    _index.tsv là bản tổng quát hoá của đúng file đó.

    Không gộp về một dòng mỗi run: params_sent là lý do cột này tồn tại — nó ghi lại
    param THẬT đã gửi cho từng chặng (sau khi API viết lại), và với pipeline nhiều
    chặng (vd tryon-motion-enhance), chặng người dùng thực sự chỉnh tay là motion —
    gộp về một dòng mỗi run chỉ giữ được chặng cuối (enhance) và param motion biến mất.

    Thứ tự chèn của dict `stages` vẫn hữu ích: với run lỗi, dòng cuối cùng thuộc về
    run đó chính là chặng đã làm nó dừng lại — không cần chọn gì thêm để biết run
    hỏng ở đâu.
    """
    lines = ["run\tstatus\tstage\tjob_id\telapsed_sec\tbytes\tparams_sent"]
    for run_id, entry in state["runs"].items():
        for stage_name, s in (entry.get("stages") or {}).items():
            lines.append("\t".join([
                run_id, str(entry.get("status", "")), stage_name,
                str(s.get("job_id", "")), str(s.get("elapsed_sec", "")),
                str(s.get("bytes", "")),
                json.dumps(s.get("params_sent") or {}, ensure_ascii=False),
            ]))
    (out_dir / "_index.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_batch(*, settings: Settings, manifest: Manifest, out_root: Path,
              batch_id: str | None = None, resume: bool = False, fail_fast: bool = False,
              log: Callable[[str], None] = print,
              now: Callable[[], float] = time.time) -> BatchResult:
    batch_id = batch_id or batch_id_now()
    out_dir = out_root / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Chép NGUYÊN VĂN, không qua PyYAML — comment của người dùng phải sống sót.
    shutil.copyfile(manifest.path, out_dir / "manifest.yaml")

    state_file = state_path_for(manifest.path)
    state = load_state(state_file) if resume else {"version": 1, "runs": {}}
    state["batch"] = batch_id

    result = BatchResult(batch_id=batch_id, out_dir=out_dir)
    for position, run in enumerate(manifest.runs, start=1):
        recorded = (state["runs"].get(run.id) or {})
        if resume and recorded.get("status") == "done":
            log(f"[{position}/{len(manifest.runs)}] {run.id}: bỏ qua (đã xong)")
            result.done.append(run.id)
            continue
        log(f"[{position}/{len(manifest.runs)}] {run.id} · {run.pipeline}")
        started = now()
        try:
            run_one(settings=settings, run=run, out_dir=out_dir, state=state,
                    state_file=state_file, resume=resume, log=log, now=now)
            result.done.append(run.id)
        except JobError as exc:
            state["runs"][run.id]["status"] = "error"
            state["runs"][run.id]["error"] = str(exc)
            save_state(state_file, state)
            result.failed[run.id] = str(exc)
            log(f"    ✗ {exc}")
            if fail_fast:
                result.gpu_seconds += int(now() - started)
                break
        result.gpu_seconds += int(now() - started)

    save_state(state_file, state)
    write_index(out_dir, state)
    latest = out_root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(out_dir.name)
    return result
