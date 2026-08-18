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
    source: str
    # "ast"   — worker đọc trực tiếp (params.get("ten")), thấy được qua AST.
    # "extra" — worker đọc ĐỘNG (vd. next((k for k in (...) if k in params))), AST mù,
    #           nên phải khai tay trong batch-params.json.
    # "api"   — worker KHÔNG BAO GIỜ đọc; api/src/routes/jobs.js tiêu thụ hoặc dịch
    #           param này trước khi ghi DB (vd. "quality" → width/height).


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


def missing_source_hint(path: Path, root: Path) -> str:
    """Câu nói khi một file NGUỒN của bảng param không có trên đĩa.

    Dùng chung cho batch_params.py và batch_run.py: cả hai đều gọi
    extract_from_ast(LINUX_PY) + load_curated(CURATED), và cả hai chết bằng
    FileNotFoundError trần nếu thiếu — mà nguyên nhân thật gần như luôn là một
    trong hai điều dưới đây, không phải "file này biến mất bí ẩn".
    """
    try:
        rel: Path | str = path.relative_to(root)
    except ValueError:
        rel = path
    if "motions-studio" in str(rel):
        cause = ("hoặc motions-studio/ chưa được checkout/rsync về máy này — kéo "
                 "submodule/thư mục đó về trước.")
    else:
        cause = f"hoặc file đó đã bị xoá/đổi tên — khôi phục bằng: git checkout -- {rel}"
    return (f"không thấy {rel}\n"
            f"  Hoặc bạn đang chạy lệnh này từ ngoài thư mục repo (cd vào {root} rồi "
            f"chạy lại), {cause}")


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
    """Chặn param không có thật, giá trị ngoài danh sách, VÀ param sẽ bị bỏ qua âm thầm.

    Nhóm thứ ba là loại tệ nhất, vì nó qua được mọi cổng khác: param hợp lệ, giá trị
    hợp lệ, job chạy, tính tiền, ra kết quả của giá trị KHÁC. Hai ca thật đang có:
      - `quality` không kèm preset drv-* → enforceMotionResolution return sớm (xem
        khối "requires" trong batch-params.json).
      - `render_profile: max` → jobs.js ép về "fast" trước khi ghi DB (khối
        "overridden"). Muốn 20 step thật thì phải qua env của worker.
    Cả hai đều lấy từ file khai tay, không hardcode ở đây: sửa API thì sửa một chỗ.
    """
    known = known_params(job_type, ast_params=ast_params, curated=curated)
    block = curated.get(job_type, {})
    allowed = block.get("allowed", {}) or {}
    requires = block.get("requires", {}) or {}
    overridden = block.get("overridden", {}) or {}
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
            continue

        rule = requires.get(key) or {}
        need_key = str(rule.get("param") or "")
        need_values = [str(v) for v in (rule.get("values") or [])]
        if need_key and str((params or {}).get(need_key, "")) not in need_values:
            shown = " | ".join(need_values)
            errors.append(
                f"{job_type}.{key}: {value!r} sẽ bị API BỎ QUA âm thầm — nó chỉ có tác dụng "
                f"khi {need_key} là {shown} ({rule.get('where') or 'tầng API'}).\n"
                f"      Thêm `{need_key}: {need_values[0] if need_values else '?'}` vào khối "
                f"{job_type} của run này, hoặc bỏ {key} đi cho khỏi tưởng nó có tác dụng."
            )

        forced = overridden.get(key) or {}
        if forced:
            # "when" (tùy chọn) = ghi đè CÓ ĐIỀU KIỆN. Cần nhánh riêng vì đây là quan hệ
            # thứ ba, khác cả `requires` lẫn `overridden` không điều kiện:
            #   requires    X chỉ có tác dụng KHI Y ∈ values      (thiếu Y → X bị bỏ)
            #   overridden  X luôn bị ép thành một giá trị cố định
            #   + when      X bị ghi đè KHI Y ∈ values, được tôn trọng khi không
            # frames/render_fps thuộc loại thứ ba: preset drv-* làm worker ffprobe driver
            # rồi tự tính, còn không có preset thì giá trị của người dùng được dùng thật.
            when = forced.get("when") or {}
            when_key = str(when.get("param") or "")
            when_values = [str(v) for v in (when.get("values") or [])]
            if when_key:
                if str((params or {}).get(when_key, "")) in when_values:
                    shown = " | ".join(when_values)
                    errors.append(
                        f"{job_type}.{key}: {value!r} sẽ bị GHI ĐÈ vì {when_key} đang là "
                        f"{str((params or {}).get(when_key))!r} — với {when_key} ∈ {shown} thì "
                        f"{key} là {forced.get('forced')} ({forced.get('where') or 'tầng API'}).\n"
                        f"      {forced.get('escape') or 'Không có đường nào khác.'}"
                    )
            elif str(value) != str(forced.get("forced")):
                errors.append(
                    f"{job_type}.{key}: {value!r} sẽ bị API ép thành "
                    f"{str(forced.get('forced'))!r} VÔ ĐIỀU KIỆN "
                    f"({forced.get('where') or 'tầng API'}) — validate xong là mất, job vẫn "
                    f"chạy và vẫn tính tiền với giá trị bị ép.\n"
                    f"      {forced.get('escape') or 'Không có đường nào khác qua API.'}"
                )
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
        # .allowed/.requires/.overridden đều nói về một param CÓ THẬT. Key mồ côi ở đây
        # nghĩa là luật im lặng: validate không bao giờ chạm tới nó, mà đọc file thì
        # tưởng đã có luật. Cùng lý do với .allowed từ đầu.
        for source in ("allowed", "requires", "overridden"):
            for name in sorted(set(block.get(source, {}) or {}) - known):
                errors.append(
                    f"{job_type}: .{source} có {name!r} nhưng không nằm trong AST, .extra hay .api "
                    f"→ khai báo cũ, gỡ đi."
                )
        for name, rule in sorted((block.get("requires", {}) or {}).items()):
            if not (rule or {}).get("param") or not (rule or {}).get("values"):
                errors.append(
                    f"{job_type}: .requires[{name!r}] thiếu \"param\" hoặc \"values\" → luật này "
                    f"không chặn được gì, mà đọc file thì tưởng có. Khai đủ hoặc gỡ đi."
                )
        for name, rule in sorted((block.get("overridden", {}) or {}).items()):
            if "forced" not in (rule or {}):
                errors.append(
                    f"{job_type}: .overridden[{name!r}] thiếu \"forced\" (giá trị API ép thành) → "
                    f"validate không biết so với cái gì. Khai đủ hoặc gỡ đi."
                )
            # "when" tùy chọn, nhưng khai nửa vời thì luật im lặng: nhánh có điều kiện chỉ
            # chạy khi có "param", nên thiếu nó là biến một luật thành không luật.
            when = (rule or {}).get("when")
            if when is not None and (not (when or {}).get("param") or not (when or {}).get("values")):
                errors.append(
                    f"{job_type}: .overridden[{name!r}].when thiếu \"param\" hoặc \"values\" → "
                    f"luật ghi đè có điều kiện này không chặn được gì, mà đọc file thì tưởng có. "
                    f"Khai đủ hoặc gỡ \"when\" đi."
                )
    return errors
