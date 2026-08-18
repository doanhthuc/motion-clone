"""Bốn tool của spec §9. Bọc MỎNG CLI — không có logic lô nào ở đây.

Nguyên tắc: validate dùng chính `validate_manifest` mà `batch_run.py` dùng, chạy
lô là spawn chính `batch_run.py`, tiến độ là đọc chính journal mà runner ghi. Chỗ
nào file này tự nghĩ ra một câu trả lời riêng là chỗ nó sẽ lệch với CLI.

ĐỊNH DANH LÀ ĐƯỜNG DẪN MANIFEST, không phải batch id — khác spec §9 ("chạy nền,
trả run id"), cố ý: batch id do `run_batch` mint tại thời điểm chạy (runner.py:
`batch_id_now`), MCP không biết trước được nó, và bịa ra một id ở đây thì phải
thêm cờ `--batch-id` vào một CLI đã nghiệm thu trên pod thật. Journal vốn đã
khoá theo đường dẫn manifest (`state_path_for`), nên dùng đúng khoá đó là nhất
quán với phần còn lại của hệ thống. `batch_status(file)` trả `lo` = batch id
thật ngay khi runner ghi nó xuống journal.

VÌ SAO CHẠY NỀN, KHÔNG CHẠY ĐỒNG BỘ: một lô 30-60 phút mà nằm trong tool call
thì nó chết theo phiên chat (§9). Process con tách hẳn session (`start_new_session`)
nên Claude Code đóng server MCP không giết được nó.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import (Manifest, ManifestError, load_manifest, load_state, save_state,
                       state_path_for, validate_manifest)
from .params import extract_from_ast, load_curated
from .rpc import Tool

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Ctx:
    """Mọi thứ tool cần chạm tới thế giới bên ngoài. Test tiêm cái khác vào đây.

    `root` là nơi có `out/` và là cwd của runner. `linux_py`/`curated` KHÔNG suy
    từ `root`: chúng là tài sản của repo (bảng param), luôn nằm ở repo thật kể cả
    khi test cho `root` trỏ vào thư mục tạm.
    """
    root: Path = REPO_ROOT
    runner: Path = field(default_factory=lambda: REPO_ROOT / "scripts" / "batch_run.py")
    linux_py: Path = field(
        default_factory=lambda: REPO_ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py")
    curated: Path = field(default_factory=lambda: REPO_ROOT / "scripts" / "batch-params.json")
    python: str = sys.executable


def mcp_path_for(manifest_path: Path) -> Path:
    """batch/lo.yaml → batch/lo.mcp.json (pid/argv/log của lượt chạy nền gần nhất).

    File RIÊNG, không nhét vào .state.json: journal là của runner và nó ghi đè cả
    dict sau mỗi chặng — hai process ghi chung một file là đúng cách mất pid.
    """
    return manifest_path.with_suffix(".mcp.json")


def _log_path(manifest_path: Path) -> Path:
    return mcp_path_for(manifest_path).with_suffix(".log")


def _rc_path(manifest_path: Path) -> Path:
    return mcp_path_for(manifest_path).with_suffix(".rc")


def _con_song(pid: int) -> bool:
    """Process còn sống không — chịu được cả trường hợp nó là con của chính ta.

    Runner được spawn từ process này, nên khi nó thoát mà không ai `wait()` thì nó
    thành ZOMBIE: `os.kill(pid, 0)` trần vẫn báo "còn sống", mãi mãi. Reap trước
    bằng WNOHANG. Server MCP khởi động lại thì pid không còn là con ta nữa —
    `ChildProcessError` — lúc đó signal 0 mới là câu trả lời đúng.
    """
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass          # không phải con ta (server vừa khởi động lại) — hỏi kernel bên dưới
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # tồn tại, chỉ là của user khác
    return True


def _lo_dang_chay(manifest_path: Path) -> int | None:
    """pid của lô đang chạy cho manifest này, hoặc None.

    `.rc` tồn tại = lệnh đã kết thúc và ghi lại mã thoát; đọc nó trước tránh cửa
    sổ hẹp lúc shell bọc ngoài chưa kịp thoát hẳn.
    """
    ghi = _doc_mcp(manifest_path)
    pid = ghi.get("pid")
    if not isinstance(pid, int):
        return None
    if _rc_path(manifest_path).is_file():
        return None
    return pid if _con_song(pid) else None


def _doc_mcp(manifest_path: Path) -> dict:
    try:
        data = json.loads(mcp_path_for(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ban(pid: int, manifest_path: Path) -> dict:
    return {"ok": False,
            "loi": f"đang có một lô chạy cho {manifest_path.name} (pid {pid}) — không gửi lô thứ hai.\n"
                   f"  Hai runner cùng ghi một journal là hỏng journal, và hai job chồng nhau phá\n"
                   f"  giả định 'lúc này GPU chỉ có mình tôi' mà comfy_recycle dựa vào (spec §5).\n"
                   f"  Xem tiến độ: batch_status. Muốn dừng: kill {pid}"}


def _doc_manifest(file: str) -> tuple[Path, Manifest]:
    """`load_manifest` để OSError rơi thẳng ra (CLI in traceback trần khi gõ sai tên
    file). Qua MCP thì đường dẫn do model gõ, nên gõ trượt là chuyện thường xuyên —
    dịch nó thành ManifestError để rpc.py trả về một câu đọc được thay vì traceback.
    """
    path = Path(file).expanduser()
    try:
        return path, load_manifest(path)
    except OSError as exc:
        raise ManifestError(
            f"không mở được manifest {path}: {exc.strerror or exc}.\n"
            f"  Đường dẫn tính từ {REPO_ROOT} nếu bạn gõ đường dẫn tương đối."
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# batch_validate
# ─────────────────────────────────────────────────────────────────────────────

def batch_validate(ctx: Ctx, *, file: str) -> dict:
    """Không tiêu GPU. Hai loại hỏng, cố ý trả về khác nhau:

      không ĐỌC được  → ManifestError ném ra ngoài → rpc.py biến thành isError.
      đọc được nhưng SAI → ok=False + danh sách lỗi, để model sửa được file.

    Ranh giới này chép từ chính docstring của ManifestError trong manifest.py.
    """
    path, manifest = _doc_manifest(file)
    loi = validate_manifest(manifest,
                            ast_params=extract_from_ast(ctx.linux_py),
                            curated=load_curated(ctx.curated))
    return {"ok": not loi, "manifest": str(path), "so_run": len(manifest.runs),
            "run": [r.id for r in manifest.runs], "loi": loi}


# ─────────────────────────────────────────────────────────────────────────────
# spawn nền
# ─────────────────────────────────────────────────────────────────────────────

def _spawn(ctx: Ctx, manifest_path: Path, argv: list[str]) -> dict:
    """Chạy `batch_run.py` tách hẳn phiên, stdout+stderr dồn vào một file log.

    Bọc trong `sh -c` chỉ để lấy đúng một thứ: `echo $? > <.rc>` sau khi lệnh
    xong. Không có nó thì "lô kết thúc chưa, và kết thúc thế nào" là câu không
    trả lời được — pid biến mất giống hệt nhau dù lô xong sạch hay ngã ở run đầu.
    """
    log, rc = _log_path(manifest_path), _rc_path(manifest_path)
    rc.unlink(missing_ok=True)      # mã thoát của lô TRƯỚC không được trả lời cho lô này

    lenh = shlex.join([ctx.python, str(ctx.runner), *argv])
    boc = f"{lenh} >> {shlex.quote(str(log))} 2>&1; echo $? > {shlex.quote(str(rc))}"

    # Ghi thêm, không ghi đè — cùng lý do run.log ghi thêm (runner.py): lượt trước
    # chính là thứ cần đọc để hiểu vì sao phải chạy lại.
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} · {lenh} ===\n")

    proc = subprocess.Popen(
        ["/bin/sh", "-c", boc], cwd=str(ctx.root),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,   # tách session: đóng server MCP không giết được lô
    )
    ghi = {"pid": proc.pid, "argv": argv, "log": str(log), "rc": str(rc),
           "bat_dau": _dt.datetime.now().isoformat(timespec="seconds")}
    mcp_path_for(manifest_path).write_text(json.dumps(ghi, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return ghi


# ─────────────────────────────────────────────────────────────────────────────
# batch_run
# ─────────────────────────────────────────────────────────────────────────────

def batch_run(ctx: Ctx, *, file: str, resume: bool = False, fail_fast: bool = False,
              allow_start: bool = False) -> dict:
    """Gửi cả lô lên pod, chạy nền. TIÊU TIỀN GPU.

    Validate TRƯỚC khi spawn, không phải trong runner: một manifest sai không được
    phép đẻ ra process nào cả. `allow_start=False` là mặc định — pod đang dừng thì
    dừng lại và bảo người dùng gõ `make gpu-up`, vì bật pod là bắt đầu tính tiền và
    đó phải là quyết định của người, không phải của một tool call.
    """
    path = Path(file).expanduser()
    kiem = batch_validate(ctx, file=file)
    if not kiem["ok"]:
        return {"ok": False,
                "loi": f"manifest sai {len(kiem['loi'])} chỗ — chưa tiêu đồng GPU nào, chưa chạy gì",
                "chi_tiet": kiem["loi"]}

    pid = _lo_dang_chay(path)
    if pid is not None:
        return _ban(pid, path)

    argv = ["--file", str(path)]
    if resume:
        argv.append("--resume")
    if fail_fast:
        argv.append("--fail-fast")
    if not allow_start:
        argv.append("--no-start")

    ghi = _spawn(ctx, path, argv)
    return {"ok": True, "pid": ghi["pid"], "log": ghi["log"], "so_run": kiem["so_run"],
            "ghi_chu": "lô chạy nền, sống qua phiên chat. Hỏi tiến độ bằng batch_status."}


# ─────────────────────────────────────────────────────────────────────────────
# batch_status
# ─────────────────────────────────────────────────────────────────────────────

def _duoi_log(path: Path, so_dong: int) -> list[str]:
    try:
        dong = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return dong[-so_dong:] if so_dong > 0 else []


def batch_status(ctx: Ctx, *, file: str, so_dong_log: int = 20) -> dict:
    """Không tiêu gì. Nguồn sự thật là journal runner ghi sau MỖI chặng.

    Đọc journal chứ không hỏi pod: journal đã có job_id, trạng thái, số giây và
    số byte của từng chặng, và nó đúng cả khi pod đã bị destroy.
    """
    path, manifest = _doc_manifest(file)
    state = load_state(state_path_for(path))
    ghi = _doc_mcp(path)
    pid = ghi.get("pid") if isinstance(ghi.get("pid"), int) else None

    runs = []
    for run in manifest.runs:
        e = state["runs"].get(run.id) or {}
        runs.append({
            "id": run.id, "pipeline": run.pipeline,
            "status": e.get("status", "chua_chay"), "loi": e.get("error"),
            "chang": [{"ten": ten, "status": s.get("status"), "job_id": s.get("job_id"),
                       "giay": s.get("elapsed_sec"), "bytes": s.get("bytes")}
                      for ten, s in (e.get("stages") or {}).items()],
        })
    xong = sum(1 for r in runs if r["status"] == "done")
    hong = sum(1 for r in runs if r["status"] == "error")

    lo = state.get("batch")
    rc = _rc_path(path)
    ma_thoat = None
    if rc.is_file():
        try:
            ma_thoat = int(rc.read_text(encoding="utf-8").strip())
        except ValueError:
            ma_thoat = None

    return {
        "manifest": str(path),
        "lo": lo,
        "thu_muc_ket_qua": str(ctx.root / "out" / lo) if lo else None,
        "dang_chay": pid is not None and ma_thoat is None and _con_song(pid),
        "pid": pid,
        "ma_thoat": ma_thoat,
        "tong": {"xong": xong, "hong": hong, "con_lai": len(runs) - xong - hong},
        "runs": runs,
        "log_file": str(_log_path(path)),
        "log": _duoi_log(_log_path(path), so_dong_log),
    }


# ─────────────────────────────────────────────────────────────────────────────
# batch_rerun
# ─────────────────────────────────────────────────────────────────────────────

def batch_rerun(ctx: Ctx, *, file: str, run_id: str) -> dict:
    """Chạy lại MỘT run, kể cả khi journal ghi nó đã `done`. TIÊU TIỀN GPU.

    Đây là việc CLI không làm được: `RESUME=1` cố ý bỏ qua run `status == "done"`
    (runner.py), nên khi video chạy xong mà nhìn xấu — job không lỗi, chỉ là kết
    quả không dùng được — không có đường nào bắt nó làm lại.

    Cách làm: xoá đúng entry của run đó khỏi journal rồi chạy `--resume`. Các run
    khác giữ nguyên `done` nên không ai bị làm lại. File chặng cũ trong
    `out/<lô>/runs/<run>/` không cần xoá: `run_one` thấy journal không có gì thì
    chạy lại từ chặng một và `download_output` ghi đè lên chúng.
    """
    path, manifest = _doc_manifest(file)
    ids = [r.id for r in manifest.runs]
    if run_id not in ids:
        return {"ok": False,
                "loi": f"không có run {run_id!r} trong {path.name}. Có: {', '.join(ids)}"}

    # Thứ tự quan trọng: từ chối TRƯỚC khi đụng vào journal. Xoá một entry trong
    # lúc runner đang ghi cùng file đó là hỏng journal của lô đang chạy.
    pid = _lo_dang_chay(path)
    if pid is not None:
        return _ban(pid, path)

    kiem = batch_validate(ctx, file=file)
    if not kiem["ok"]:
        return {"ok": False,
                "loi": f"manifest sai {len(kiem['loi'])} chỗ — chưa tiêu đồng GPU nào",
                "chi_tiet": kiem["loi"]}

    state_file = state_path_for(path)
    state = load_state(state_file)
    state["runs"].pop(run_id, None)
    save_state(state_file, state)

    ghi = _spawn(ctx, path, ["--file", str(path), "--resume"])
    return {"ok": True, "run_id": run_id, "pid": ghi["pid"], "log": ghi["log"],
            "ghi_chu": f"đã xoá {run_id} khỏi journal và chạy --resume — chỉ run này làm lại"}


# ─────────────────────────────────────────────────────────────────────────────
# đăng ký
# ─────────────────────────────────────────────────────────────────────────────

_FILE = {"type": "string",
         "description": "đường dẫn manifest .yaml (vd batch/2026-08-18.yaml)"}


def build_tools(*, ctx: Ctx | None = None) -> dict[str, Tool]:
    """Mô tả tool là thứ DUY NHẤT model đọc trước khi quyết định gọi — nên hai tool
    tiêu tiền phải nói ra điều đó ngay trong mô tả, không giấu xuống tài liệu."""
    ctx = ctx or Ctx()
    tools = [
        Tool(name="batch_validate",
             description="Kiểm một manifest lô: đường dẫn material có thật không, key param có "
                         "đúng không. KHÔNG tiêu GPU, không chạy gì. Chạy cái này trước batch_run.",
             schema={"type": "object", "properties": {"file": _FILE}, "required": ["file"]},
             fn=functools.partial(batch_validate, ctx)),
        Tool(name="batch_run",
             description="Chạy cả lô trên pod, nền (sống qua phiên chat), trả pid + file log. "
                         "TIÊU TIỀN GPU — mỗi run mất vài phút GPU. Validate trước, sai thì không "
                         "chạy gì. Pod đang dừng thì báo lỗi chứ không tự bật, trừ khi "
                         "allow_start=true (bật pod là bắt đầu tính tiền).",
             schema={"type": "object", "properties": {
                 "file": _FILE,
                 "resume": {"type": "boolean",
                            "description": "chạy tiếp lô dở: bỏ qua run đã xong, bắt lại job đang chạy"},
                 "fail_fast": {"type": "boolean", "description": "dừng cả lô ngay khi một run hỏng"},
                 "allow_start": {"type": "boolean",
                                 "description": "cho phép tự `make gpu-up` nếu pod đang dừng. "
                                                "Bắt đầu tính tiền ngay — chỉ bật khi người dùng đã đồng ý"},
             }, "required": ["file"]},
             fn=functools.partial(batch_run, ctx)),
        Tool(name="batch_status",
             description="Tiến độ của một lô: đang chạy hay đã xong, mã thoát, từng run/từng chặng "
                         "(job_id, số giây, số byte) và mấy dòng cuối của log. Không tiêu gì. "
                         "Đọc journal nên đúng cả khi pod đã destroy.",
             schema={"type": "object", "properties": {
                 "file": _FILE,
                 "so_dong_log": {"type": "integer", "description": "số dòng cuối của log, mặc định 20"},
             }, "required": ["file"]},
             fn=functools.partial(batch_status, ctx)),
        Tool(name="batch_rerun",
             description="Chạy lại MỘT run, kể cả khi nó đã xong — dùng khi video ra xấu chứ không "
                         "phải khi job lỗi (job lỗi thì batch_run resume=true đã tự chạy lại). "
                         "TIÊU TIỀN GPU. Các run khác trong lô không bị đụng tới.",
             schema={"type": "object", "properties": {
                 "file": _FILE,
                 "run_id": {"type": "string", "description": "id của run trong manifest"},
             }, "required": ["file", "run_id"]},
             fn=functools.partial(batch_rerun, ctx)),
    ]
    return {t.name: t for t in tools}
