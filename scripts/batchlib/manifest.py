"""Manifest (của người, .yaml) và journal (của máy, .state.json).

RANH GIỚI CỨNG: module này KHÔNG BAO GIỜ ghi vào file .yaml. PyYAML safe_dump
xoá sạch comment, mà comment chính là chỗ người dùng ghi "preset này cho khách
A, đừng đổi". Ghi đè một lần là mất hết, không lấy lại được. dump_runs() chỉ
dùng cho batch_scan.py — lúc đó file còn chưa tồn tại nên chưa có gì để mất.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .params import validate_params
from .pipelines import PIPELINES, optional_roles, required_roles

STAGE_KEYS = set()
for _stages in PIPELINES.values():
    STAGE_KEYS.update(_stages)


class ManifestError(Exception):
    """Manifest không đọc được. Khác với 'đọc được nhưng sai' — cái đó là validate."""


def _require_mapping(value: object, *, path: Path, where: str, key: str, hint: str) -> dict:
    """Chặn sớm khi một khối phải là mapping ("khoá: giá trị") nhưng không phải.

    Không chặn ở đây thì lỗi rơi xuống chỗ merge/iterate bên dưới, ra
    TypeError/AttributeError trần trụi của Python (vd. `dict.update(60)` →
    "'int' object is not iterable", `"c.jpg".items()` → "'str' object has no
    attribute 'items'") — không nói file nào, run nào, khoá nào, và viết lại
    thế nào. Cùng loại lỗi cấu trúc với YAML hỏng/run không phải mapping/thiếu
    id đã bắt riêng; đây là ba chỗ còn sót: defaults, inputs, khối tham số
    từng chặng.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ManifestError(
        f"{path}: {where}{key} phải là mapping \"khoá: giá trị\", không phải "
        f"{type(value).__name__}.\n"
        f"  Bạn viết:  {key}: {value!r}\n"
        f"{hint}"
    )


@dataclass
class Run:
    id: str
    pipeline: str
    inputs: dict[str, Path] = field(default_factory=dict)
    stage_params: dict[str, dict] = field(default_factory=dict)


@dataclass
class Manifest:
    path: Path
    runs: list[Run]


def load_manifest(path: Path) -> Manifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: YAML hỏng — {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: gốc file phải là một mapping có khoá 'runs:'")

    entries = raw.get("runs")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{path}: không có 'runs:' nào — chạy make batch-scan để đẻ ra")

    defaults = _require_mapping(
        raw.get("defaults"), path=path, where="", key="defaults",
        hint=('  Ý bạn là:  defaults: { enhance: { fpsInterp: "60" } }\n'
              '  Tra tên param: make batch-params TYPE=<chặng>'),
    )
    # defaults hợp lệ là mapping ở NGOÀI, nhưng từng khối bên trong (defaults.enhance,
    # defaults.tryon, …) cũng phải là mapping — batch/example.yaml (task 5) dạy đúng
    # dạng `defaults: { enhance: { fpsInterp: "60" } }`, và ai đơn giản hoá thành
    # `enhance: 60` rơi thẳng vào dict(60) → TypeError trần trụi ở chỗ merge bên dưới
    # nếu không chặn ở đây.
    for _stage_key, _stage_val in list(defaults.items()):
        _require_mapping(
            _stage_val, path=path, where="defaults.", key=_stage_key,
            hint=(f'  Ý bạn là:  {_stage_key}: {{ ... }}\n'
                  f'  Tra tên param: make batch-params TYPE={_stage_key}'),
        )
    base = path.parent.resolve()
    runs: list[Run] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: runs[{index}] phải là mapping")
        run_id = str(entry.get("id") or "").strip()
        if not run_id:
            raise ManifestError(f"{path}: runs[{index}] thiếu 'id'")
        if run_id in seen:
            raise ManifestError(
                f"{path}: hai run cùng id {run_id!r} — output sẽ đè lên nhau, đổi một cái"
            )
        seen.add(run_id)

        run_where = f"runs[{index}]."
        inputs_raw = _require_mapping(
            entry.get("inputs"), path=path, where=run_where, key="inputs",
            hint='  Ý bạn là:  inputs: { character: char.jpg, driver: drv.mp4 }',
        )
        inputs: dict[str, Path] = {}
        for role, value in inputs_raw.items():
            expanded = Path(str(value)).expanduser()
            resolved = expanded if expanded.is_absolute() else (base / expanded)
            inputs[str(role)] = resolved.resolve()

        pipeline = str(entry.get("pipeline") or "")
        # defaults CHỈ áp cho chặng mà pipeline của run thật sự chạy. Merge cho mọi chặng
        # thì đặt `defaults: { tryon: {...} }` sẽ làm MỌI run motion-enhance bị validate
        # báo sai "có param cho chặng tryon nhưng pipeline không chạy chặng đó" — người
        # dùng đặt một mặc định vô hại và cả lô bị chặn với lý do khó hiểu.
        # Param ghi THẲNG trong run thì vẫn merge bất kể pipeline: đó là lỗi cố ý của
        # người dùng và validate phải nói ra, khác hẳn với một default vô tình lan sang.
        # Pipeline lạ → merge hết; validate sẽ bắt chính cái pipeline đó trước.
        applicable = set(PIPELINES.get(pipeline, STAGE_KEYS))
        # Thứ tự chặng phải ổn định giữa các lần chạy: STAGE_KEYS là set, mà Python
        # random hash string theo từng process, nên `for stage in STAGE_KEYS` từng
        # cho ra thứ tự khác nhau mỗi lần — dump_runs() rồi sinh YAML khác nhau trên
        # CÙNG một input, và không ai biết bản nào mới là bản đã chạy thật. Ưu tiên
        # thứ tự pipeline thực thi (đọc dễ hơn), phần còn lại (chặng ghi tay ngoài
        # pipeline) xếp theo alphabet cho có quy tắc.
        if pipeline in PIPELINES:
            stage_order = list(PIPELINES[pipeline]) + sorted(STAGE_KEYS - set(PIPELINES[pipeline]))
        else:
            stage_order = sorted(STAGE_KEYS)

        stage_params: dict[str, dict] = {}
        for stage in stage_order:
            stage_raw = _require_mapping(
                entry.get(stage), path=path, where=run_where, key=stage,
                hint=(f'  Ý bạn là:  {stage}: {{ ... }}\n'
                      f'  Tra tên param: make batch-params TYPE={stage}'),
            )
            merged = dict(defaults.get(stage) or {}) if stage in applicable else {}
            merged.update(stage_raw)
            if merged:
                stage_params[stage] = merged

        runs.append(Run(id=run_id, pipeline=pipeline,
                        inputs=inputs, stage_params=stage_params))
    return Manifest(path=path, runs=runs)


def validate_manifest(m: Manifest, *, ast_params: dict, curated: dict) -> list[str]:
    """Gom HẾT lỗi rồi trả về, không dừng ở cái đầu tiên.

    Dừng sớm nghĩa là sửa một lỗi, chạy lại, gặp lỗi kế — với manifest 12 run thì
    đó là 12 vòng. Mà mục đích của validate là nổ TRƯỚC khi job đầu tiên tiêu GPU.
    """
    errors: list[str] = []
    for run in m.runs:
        where = f"run {run.id!r}"
        if run.pipeline not in PIPELINES:
            errors.append(
                f"{where}: pipeline không có thật {run.pipeline!r} — có: {', '.join(sorted(PIPELINES))}"
            )
            continue

        needed = required_roles(run.pipeline)
        for role in sorted(needed - set(run.inputs)):
            errors.append(f"{where}: pipeline {run.pipeline} cần material {role!r} nhưng inputs không có")
        for role in sorted(set(run.inputs) - needed - optional_roles(run.pipeline)):
            errors.append(f"{where}: material {role!r} không được pipeline {run.pipeline} dùng tới")
        for role, path in sorted(run.inputs.items()):
            if not path.is_file():
                errors.append(f"{where}: không thấy file {role} → {path}")

        for stage in PIPELINES[run.pipeline]:
            errors.extend(
                f"{where}: {msg}"
                for msg in validate_params(stage, run.stage_params.get(stage, {}),
                                           ast_params=ast_params, curated=curated)
            )
        for stage in sorted(set(run.stage_params) - set(PIPELINES[run.pipeline])):
            errors.append(
                f"{where}: có param cho chặng {stage!r} nhưng pipeline {run.pipeline} "
                f"không chạy chặng đó — param sẽ bị bỏ qua âm thầm"
            )
    return errors


def dump_runs(runs: list[Run]) -> str:
    """Sinh YAML cho batch_scan.py. KHÔNG dùng để ghi đè manifest đã có."""
    payload = []
    for run in runs:
        entry: dict = {"id": run.id, "pipeline": run.pipeline,
                       "inputs": {k: str(v) for k, v in run.inputs.items()}}
        for stage, values in run.stage_params.items():
            entry[stage] = values
        payload.append(entry)
    return yaml.safe_dump({"runs": payload}, allow_unicode=True, sort_keys=False, width=100)


def state_path_for(manifest_path: Path) -> Path:
    return manifest_path.with_suffix(".state.json")


def load_state(path: Path) -> dict:
    """State hỏng KHÔNG được giết lô đang chạy — tệ nhất là mất quyền resume."""
    empty = {"version": 1, "runs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    return data if isinstance(data, dict) and "runs" in data else empty


def save_state(path: Path, state: dict) -> None:
    """Ghi atomic: state ghi sau MỖI chặng, nên Ctrl-C giữa lúc ghi là chuyện sẽ xảy ra."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
