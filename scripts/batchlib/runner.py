"""Chạy một lô: từng run, từng chặng, ghi journal sau MỖI chặng.

Ghi journal sau mỗi chặng chứ không cuối run: một run ba chặng đứt ở chặng ba
thì resume chỉ chạy lại chặng ba. Ghi cuối run nghĩa là mất cả ba.

Và "chạy lại chặng ba" trước tiên nghĩa là BẮT LẠI đúng job cũ bằng job_id trong
journal (_try_reattach), không phải gửi job mới: chặng đứt ở phút 39 của một
enhance 40 phút mà gửi lại là trả tiền GPU hai lần cho cùng một việc.

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

from .client import JobError, JobFailed, JobGone, download_output, poll_job, submit_job
from .config import Settings
from .manifest import Manifest, Run, load_state, save_state, state_path_for
from .pipelines import PIPELINES, STAGES, Stage


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
            roles = [alt[len("material:"):].rstrip("?") for alt in source.split("|")
                      if alt.startswith("material:")]
            role_hint = " hoặc ".join(roles) if roles else api_field
            raise JobError(
                f"run {run.id!r} chặng {stage_name}: không có nguồn nào cho field {api_field!r} "
                f"(khai: {source}).\n"
                f"  Thêm {role_hint!r} vào inputs: của run {run.id!r} trong manifest.\n"
                f"  make batch-validate bắt lỗi này TRƯỚC khi tốn GPU — chạy nó trước khi make batch."
            )
    return files


def _log_line(path: Path, msg: str) -> None:
    """Ghi một dòng vào run.log (spec §6 đòi file này, và trước bản sửa nó chưa từng
    được sinh ra).

    Mở/đóng từng dòng chứ không giữ handle: một lô 30-60 phút không tính bằng số lần
    open(), còn máy ngủ / Ctrl-C / mất điện giữa chặng thì mọi dòng đã ghi phải còn
    trên đĩa — giữ handle mở là cách mất đúng những dòng cuối, tức phần cần đọc nhất.
    Giờ chỉ có trong FILE, không có trên stdout: stdout phải giữ nguyên từng ký tự.
    """
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _tee(log: Callable[[str], None], path: Path) -> Callable[[str], None]:
    """stdout GIỮ NGUYÊN (đó là dấu hiệu duy nhất "còn sống" suốt một lô 40 phút),
    thêm một bản xuống run.log. Tool này chạy không người trông 30-60 phút, mà trước
    bản sửa stdout là bản ghi DUY NHẤT: đóng terminal là mất hết.
    """
    def wrapped(msg: str) -> None:
        log(msg)
        _log_line(path, msg)
    return wrapped


def _try_reattach(*, settings: Settings, stage: Stage, job_id: str, recorded_status: str,
                  log: Callable[[str], None],
                  on_progress: Callable[[dict], None]) -> dict | None:
    """--resume: BẮT LẠI job đã gửi ở lần chạy trước, thay vì gửi một job MỚI.

    Ba thông báo trong repo đã hứa đúng điều này — client.py ("Nó VẪN đang chạy trên
    pod — chạy lại với RESUME=1 để bắt tiếp, đừng submit job mới") và batch_run.py
    ("đừng chạy lại từ đầu (tốn tiền GPU hai lần)") — và spec §8 nói rõ "--resume bắt
    lại bằng job_id đã ghi trong journal". Trước bản sửa, journal GHI job_id nhưng
    không ai đọc lại: một enhance 40 phút bị Ctrl-C ở phút 39 tốn thêm 40 phút GPU.

    Kết cục nào được phép gửi job mới, và vì sao chỉ hai cái:
      - poll xong done   -> trả DTO, caller tải output như thường. KHÔNG tốn GPU thêm.
      - JobGone (404)    -> hàng job không còn (pod dựng lại, DB mới): không còn gì để
                            bắt -> None, gửi mới.
      - JobFailed        -> job đã CHẠY và hỏng thật -> None, gửi mới.
      - JobError khác    -> NÉM TIẾP, tuyệt đối không gửi mới. Đây là chỗ dễ sai nhất:
                            "quá hạn" và "mất liên lạc" nghĩa là job VẪN đang chạy trên
                            pod. Gửi mới ở đó chính là cái bug hàm này tồn tại để sửa.
    """
    log(f"    {stage.name}: journal có job {job_id} (trạng thái đã ghi: "
        f"{recorded_status or 'không rõ'}) — thử BẮT LẠI job cũ trước, không gửi job mới")
    try:
        dto = poll_job(settings, job_id, stage.timeout_min, on_progress=on_progress)
    except JobGone as exc:
        log(f"    {stage.name}: job cũ {job_id} không còn trên pod ({exc}) — gửi job MỚI")
        return None
    except JobFailed as exc:
        log(f"    {stage.name}: job cũ {job_id} đã chạy và hỏng thật ({exc}) — gửi job MỚI")
        return None
    log(f"    {stage.name}: ✓ BẮT LẠI được job cũ {job_id} — không tốn thêm GPU")
    return dto


def run_one(*, settings: Settings, run: Run, out_dir: Path, state: dict,
            state_file: Path, resume: bool,
            log: Callable[[str], None], now: Callable[[], float] = time.time) -> Path:
    run_dir = out_dir / "runs" / run.id
    run_dir.mkdir(parents=True, exist_ok=True)
    entry = state["runs"].setdefault(run.id, {"status": "pending", "stages": {}})
    entry["status"] = "running"

    # run.log: cùng nội dung với stdout, cộng dấu thời gian. GHI THÊM, không ghi đè —
    # một lần --resume là một lượt mới của cùng run đó, và lượt trước chính là thứ cần
    # đọc để hiểu vì sao phải resume.
    log_file = run_dir / "run.log"
    _log_line(log_file, f"=== {run.id} · {run.pipeline} · "
                        f"{'RESUME=1 (chạy tiếp)' if resume else 'lô mới'} ===")
    log = _tee(log, log_file)

    prev_output: Path | None = None
    for index, stage_name in enumerate(PIPELINES[run.pipeline], start=1):
        stage = STAGES[stage_name]
        dest = run_dir / f"{index:02d}-{stage_name}{stage.output_ext}"
        recorded = entry["stages"].get(stage_name) or {}
        params = run.stage_params.get(stage_name, {})

        if resume and recorded.get("status") == "done" and dest.is_file():
            log(f"    {stage_name}: bỏ qua (đã xong, {dest.name})")
            prev_output = dest
            continue

        def _progress(d: dict) -> None:
            log(f"      {d.get('status')} {round((d.get('progress') or 0) * 100)}% "
                f"{d.get('current_step') or ''}")

        job_id = str(recorded.get("job_id") or "")
        started = now()
        try:
            dto: dict | None = None
            # Chặng đã có job_id trong journal mà chưa "done" = job đã được gửi ở lượt
            # trước và có thể VẪN đang chạy trên pod (timeout, rớt mạng, Ctrl-C). Bắt lại
            # trước, gửi mới chỉ khi job đó thật sự đã mất hoặc đã hỏng — xem _try_reattach.
            if resume and job_id and recorded.get("status") != "done":
                dto = _try_reattach(settings=settings, stage=stage, job_id=job_id,
                                    recorded_status=str(recorded.get("status") or ""),
                                    log=log, on_progress=_progress)
            if dto is None:
                files = _resolve_files(run, stage_name, prev_output)
                log(f"    {stage_name}: gửi ({', '.join(f'{k}={v.name}' for k, v in files.items())})")
                job_id = submit_job(settings, stage.job_type, params, files)
                entry["stages"][stage_name] = {"job_id": job_id, "status": "running",
                                               "params_manifest": params}
                save_state(state_file, state)
                dto = poll_job(settings, job_id, stage.timeout_min, on_progress=_progress)
            size = download_output(settings, job_id, dest, stage.min_bytes)
        except JobError as exc:
            # setdefault-KHÔNG: chặng chưa kịp gửi job (vd _resolve_files thiếu material)
            # thì không được đẻ ra một dòng chặng rỗng trong journal/_index.tsv — xem
            # "NGOẠI LỆ đã biết" ở docstring của write_index.
            stage_entry = entry["stages"].get(stage_name)
            if stage_entry is not None:
                stage_entry["status"] = "error"
                stage_entry["elapsed_sec"] = int(now() - started)
                save_state(state_file, state)
            # Lý do hỏng phải nằm trong run.log: run_batch in nó ra stdout, mà stdout là
            # thứ mất khi đóng terminal — đúng dòng cần nhất cho một lô chạy không người trông.
            _log_line(log_file, f"✗ {stage_name}: {exc}")
            raise

        elapsed = int(now() - started)
        # params THẬT đã được ghi vào DB, lấy từ chính DTO của GET /jobs/<id> (jobs.js:73
        # trả nguyên cột params). API nắn params trước khi ghi: normalizeMotionDriverSegment
        # ép renderProfile=fast/steps=4/cfg=1/frame_window_size=81 (jobs.js:33-46),
        # enforceMotionResolution dịch quality→width/height, và jobs.js ép
        # detailUpscale=false cho mọi job motion. Ghi lại manifest thì run.json chỉ là
        # tiếng vọng của chính manifest — không giải thích được vì sao hai lô khác nhau.
        stored = dto.get("params") if isinstance(dto, dict) else None
        entry["stages"][stage_name].update(
            status="done", elapsed_sec=elapsed, file=str(dest), bytes=size,
            params_sent=stored if isinstance(stored, dict) else {})
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

    HAI cột param, cố ý:
      params_sent      = param API ĐÃ GHI VÀO DB (đọc từ DTO của GET /jobs/<id>). Đây là
                         sự thật. Chặng chưa bao giờ "done" thì không có DTO nào để đọc,
                         nên cột này là {} — đúng, vì lúc đó ta THẬT SỰ không biết.
      params_manifest  = param mình định gửi (từ .yaml). Giữ lại để so được "xin gì" với
                         "được gì": chính chỗ lệch đó (renderProfile=max -> fast,
                         steps -> 4, detailUpscale -> false) là câu trả lời cho "vì sao
                         hai lô khác nhau" sáu tuần sau.

    Thứ tự chèn của dict `stages` vẫn hữu ích: với run lỗi, dòng cuối cùng thuộc về
    run đó chính là chặng đã làm nó dừng lại — không cần chọn gì thêm để biết run
    hỏng ở đâu.

    NGOẠI LỆ đã biết: nếu _resolve_files ném JobError vì thiếu material (chặng chưa
    kịp gửi job, nên entry["stages"][stage_name] chưa từng được tạo), run đó không để
    lại dòng chặng nào ở đây — chỉ "status": "error" ở run.json/state phản ánh lỗi.
    Đây là lỗi cấu hình manifest mà `make batch-validate` đã bắt được TRƯỚC khi tốn
    GPU, nên chấp nhận được là _index.tsv thiếu một dòng cho đúng trường hợp này.
    """
    lines = ["run\tstatus\tstage\tjob_id\telapsed_sec\tbytes\tparams_sent\tparams_manifest"]
    for run_id, entry in state["runs"].items():
        for stage_name, s in (entry.get("stages") or {}).items():
            lines.append("\t".join([
                run_id, str(entry.get("status", "")), stage_name,
                str(s.get("job_id", "")), str(s.get("elapsed_sec", "")),
                str(s.get("bytes", "")),
                json.dumps(s.get("params_sent") or {}, ensure_ascii=False),
                json.dumps(s.get("params_manifest") or {}, ensure_ascii=False),
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
