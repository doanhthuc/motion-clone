"""Trả lời câu "param của chặng này gồm những gì" mà không bắt ai đi đọc linux.py.

Hai nguồn, cộng lại:
  1. AST của worker_runtime/linux.py — mọi params.get("ten") trong run_<type>.
  2. scripts/batch-params.json — khai TAY phần (1) không thấy, + giá trị hợp lệ.

Vì sao cần (2): param đọc động thì AST mù. Ví dụ linux.py:9569
    _fps_key = next((k for k in ("fpsInterp","fps_interp","fpsTarget") if k in params), None)
tức là ĐÚNG cái param quan trọng nhất của quy trình này (48/60fps) lại vô hình.
check_drift() bắt được đúng lớp bug đó bằng cách đọc chính các tuple kiểu này.

Vì sao validate đáng tồn tại: worker nhận CẢ HAI kiểu viết (targetRes và
target_res, cleanOnly và clean_only). Gõ sai biến thể thứ ba thì params.get()
trả None, job VẪN chạy, VẪN tính tiền, và ra kết quả của giá trị mặc định —
không lỗi, không log, không ai biết.
"""
from __future__ import annotations

import ast
import difflib
import json
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "run_"
# Tên biến giữ dict params trong linux.py.
_PARAM_VARS = {"params", "p"}


@dataclass(frozen=True)
class ParamInfo:
    name: str
    default: object
    line: int
    source: str          # "ast" | "curated"


def _job_type(func_name: str) -> str:
    # LƯU Ý: map này chỉ đổi "_" cuối cùng thành gạch nối kiểu naive — run_text2video
    # ra "text2video", nhưng job type thật trong DB là "text-to-video". Không sao với
    # kế hoạch này (chỉ dùng motion/tryon/enhance, đều một từ), nhưng ai thêm pipeline
    # nhiều từ sau này sẽ vấp đúng chỗ này — không sửa tổng quát ở đây, chỉ cảnh báo.
    return func_name[len(_PREFIX):].replace("_", "-")


def _param_get_calls(node: ast.AST):
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if not isinstance(sub.func, ast.Attribute) or sub.func.attr != "get":
            continue
        base = sub.func.value
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if name in _PARAM_VARS:
            yield sub


def extract_from_ast(linux_py: Path) -> dict[str, dict[str, ParamInfo]]:
    tree = ast.parse(linux_py.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, dict[str, ParamInfo]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(_PREFIX):
            continue
        found: dict[str, ParamInfo] = {}
        for call in _param_get_calls(node):
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            key = call.args[0].value
            if not isinstance(key, str) or key in found:
                continue
            default: object = None
            if len(call.args) > 1:
                try:
                    default = ast.literal_eval(call.args[1])
                except (ValueError, SyntaxError):
                    default = "<biểu thức>"
            found[key] = ParamInfo(key, default, call.lineno, "ast")
        if found:
            out[_job_type(node.name)] = found
    return out


def dynamic_param_names(linux_py: Path) -> dict[str, set[str]]:
    """Các tên param đọc động: next((k for k in (…) if k in params), …).

    Đây là lỗ của extract_from_ast(), và đọc chính cái idiom đó là cách duy nhất
    bắt được nó mà không phải nhớ bằng đầu.
    """
    tree = ast.parse(linux_py.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(_PREFIX):
            continue
        names: set[str] = set()
        for gen in (n for n in ast.walk(node) if isinstance(n, ast.GeneratorExp)):
            uses_params = any(
                isinstance(c, ast.Compare)
                and isinstance(c.ops[0], ast.In)
                and isinstance(c.comparators[0], ast.Name)
                and c.comparators[0].id in _PARAM_VARS
                for c in ast.walk(gen) if isinstance(c, ast.Compare) and c.ops
            )
            if not uses_params:
                continue
            for comp in gen.generators:
                if isinstance(comp.iter, (ast.Tuple, ast.List)):
                    for element in comp.iter.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            names.add(element.value)
        if names:
            out[_job_type(node.name)] = names
    return out


def load_curated(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def known_params(job_type: str, *, ast_params: dict, curated: dict) -> dict[str, ParamInfo]:
    """AST ∪ extra ∪ api.

    'api' là tầng thứ hai mà AST của worker không bao giờ nhìn thấy: param do
    api/src/routes/jobs.js tiêu thụ hoặc dịch trước khi ghi DB. Ví dụ 'quality'
    — run_motion không hề gọi params.get("quality"); enforceMotionResolution
    (motion-resolution.js:23) đổi nó thành width/height. Bỏ tầng này thì runner
    chặn đúng những param mà chính repo đang gửi (pod-smoke.sh:292).
    """
    block = curated.get(job_type, {})
    out = dict(ast_params.get(job_type, {}))
    for source in ("extra", "api"):
        for name, meta in (block.get(source, {}) or {}).items():
            out.setdefault(name, ParamInfo(name, None, int(meta.get("line") or 0), source))
    return out


def validate_params(job_type: str, params: dict, *, ast_params: dict, curated: dict) -> list[str]:
    known = known_params(job_type, ast_params=ast_params, curated=curated)
    allowed = curated.get(job_type, {}).get("allowed", {}) or {}
    errors: list[str] = []
    for key, value in (params or {}).items():
        if key not in known:
            near = difflib.get_close_matches(key, list(known), n=3, cutoff=0.6)
            hint = f" — ý bạn là {', '.join(near)}?" if near else \
                   f" — xem: make batch-params TYPE={job_type}"
            errors.append(f"{job_type}: param không có thật {key!r}{hint}")
            continue
        if key in allowed and str(value) not in [str(v) for v in allowed[key]]:
            shown = ", ".join(repr(v) for v in allowed[key])
            errors.append(f"{job_type}.{key}: {value!r} không hợp lệ — chỉ nhận {shown}")
    return errors


def check_drift(linux_py: Path, curated_path: Path) -> list[str]:
    """Cổng: khai tay phải khớp linux.py. Cùng vai trò với check-job-types.mjs."""
    ast_params = extract_from_ast(linux_py)
    dynamic = dynamic_param_names(linux_py)
    curated = load_curated(curated_path)
    errors: list[str] = []

    for job_type, names in dynamic.items():
        declared = set(curated.get(job_type, {}).get("extra", {}) or {})
        seen_by_ast = set(ast_params.get(job_type, {}))
        for name in sorted(names - declared - seen_by_ast):
            errors.append(
                f"{job_type}: {name!r} đọc động trong linux.py nên AST không thấy, "
                f"mà batch-params.json cũng chưa khai → validate sẽ chặn nhầm nó. "
                f"Thêm vào \"{job_type}\".extra."
            )

    for job_type, block in curated.items():
        seen_by_ast = set(ast_params.get(job_type, {}))
        for name in sorted(set(block.get("extra", {}) or {}) & seen_by_ast):
            errors.append(
                f"{job_type}: {name!r} khai tay ở .extra nhưng AST ĐÃ thấy nó → "
                f"khai báo thừa, gỡ khỏi batch-params.json."
            )
        # .api CỐ Ý không bị kiểm theo AST: worker không bao giờ đọc những key đó
        # (jobs.js dịch/tiêu thụ chúng trước), nên đòi chúng xuất hiện trong AST là
        # đòi một điều không bao giờ đúng. Chúng được kiểm bằng trường "where" trỏ
        # tới dòng code API thật, và bằng mắt người khi thêm.
        known = seen_by_ast | set(block.get("extra", {}) or {}) | set(block.get("api", {}) or {})
        for name in sorted(set(block.get("allowed", {}) or {}) - known):
            errors.append(
                f"{job_type}: .allowed có {name!r} nhưng không nằm trong AST, .extra hay .api "
                f"→ khai báo cũ, gỡ đi."
            )
    return errors
