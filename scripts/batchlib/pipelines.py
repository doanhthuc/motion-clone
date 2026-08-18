"""Pipeline là DỮ LIỆU: một danh sách chặng. Thêm pipeline = thêm một dòng.

Tên field phải khớp đúng cái worker thật đọc — đã đối chiếu ngày 18/08/2026:
  tryon    motions-studio/worker/worker_runtime/linux.py:4734,4735,4744,4765
             inputs.get("model") or ref or image
             inputs.get("product") or garment
             inputs.get("product2") or garment2
             inputs.get("background") or bg or scene
  motion   scripts/pod-smoke.sh:294-295        -F ref=@… -F motion=@…
  enhance  linux.py:9544                       inputs.get("input") or video or motion or image

Gõ sai tên field ở đây KHÔNG gây lỗi HTTP: api/src/routes/jobs.js:118-129 nhận
mọi fieldname và cứ thế ghi vào inputs. Worker mới là chỗ phát hiện thiếu, và nó
phát hiện SAU khi job đã được nhận, đã vào hàng đợi, đã đánh thức GPU.
"""
from __future__ import annotations

from dataclasses import dataclass


class PipelineError(Exception):
    """Pipeline không tồn tại hoặc khai báo sai."""


@dataclass(frozen=True)
class Stage:
    name: str
    job_type: str
    inputs: dict[str, str]   # tên field API -> nguồn
    output_ext: str
    min_bytes: int           # sàn kích thước tải về; dưới ngưỡng = MinIO trả về rỗng
    timeout_min: int


# min_bytes lấy đúng hai ngưỡng pod-smoke.sh đã dùng và đã chứng minh:
#   mp4 100_000 (pod-smoke.sh:293) · ảnh 5_000 (pod-smoke.sh:44-49, đo thật tryon 1378 KB).
STAGES: dict[str, Stage] = {
    "tryon": Stage(
        name="tryon", job_type="tryon",
        inputs={"model": "material:character",
                "product": "material:outfit",
                "background": "material:background?"},
        output_ext=".png", min_bytes=5_000, timeout_min=20,
    ),
    "motion": Stage(
        name="motion", job_type="motion",
        inputs={"ref": "prev|material:character",
                "motion": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60,
    ),
    # enhance 1080p60 nội suy RIFE ×4 rồi encode lại — luôn lâu hơn motion sinh ra nó.
    "enhance": Stage(
        name="enhance", job_type="enhance",
        inputs={"input": "prev"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=90,
    ),
}

PIPELINES: dict[str, list[str]] = {
    "motion-enhance": ["motion", "enhance"],
    "tryon-motion-enhance": ["tryon", "motion", "enhance"],
}


def _stages(pipeline: str) -> list[Stage]:
    if pipeline not in PIPELINES:
        raise PipelineError(
            f"Pipeline không có thật: {pipeline!r}\n"
            f"  Có: {', '.join(sorted(PIPELINES))}"
        )
    return [STAGES[s] for s in PIPELINES[pipeline]]


def _roles(pipeline: str, want_optional: bool) -> set[str]:
    found: set[str] = set()
    for index, stage in enumerate(_stages(pipeline)):
        for source in stage.inputs.values():
            for alt in source.split("|"):
                # "prev" chỉ là material ở chặng ĐẦU (chưa có gì đứng trước nó).
                # Quy tắc: "prev" phải đứng đầu trong "|"-chain (test_prev_phai_dung_dau...)
                if alt == "prev" and index > 0:
                    break
                if not alt.startswith("material:"):
                    continue
                role = alt[len("material:"):]
                if role.endswith("?"):
                    if want_optional:
                        found.add(role[:-1])
                elif not want_optional:
                    found.add(role)
    return found


def required_roles(pipeline: str) -> set[str]:
    return _roles(pipeline, want_optional=False)


def optional_roles(pipeline: str) -> set[str]:
    return _roles(pipeline, want_optional=True)
