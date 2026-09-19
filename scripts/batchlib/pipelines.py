"""Pipeline là DỮ LIỆU: một danh sách chặng. Thêm pipeline = thêm một dòng.

Tên field phải khớp đúng cái worker thật đọc — đã đối chiếu ngày 18/08/2026:
  tryon    motions-studio/worker/worker_runtime/linux.py:4734,4735,4744,4765
             inputs.get("model") or ref or image
             inputs.get("product") or garment
             inputs.get("product2") or garment2
             inputs.get("background") or bg or scene
  motion   scripts/pod-smoke.sh:294-295        -F ref=@… -F motion=@…
  enhance  linux.py:9544                       inputs.get("input") or video or motion or image
  character-swap  linux.py run_character_swap    inputs.ref (ảnh người mẫu) + inputs.video (video nguồn)

Gõ sai tên field ở đây KHÔNG gây lỗi HTTP: api/src/routes/jobs.js:118-129 nhận
mọi fieldname và cứ thế ghi vào inputs. Worker mới là chỗ phát hiện thiếu, và nó
phát hiện SAU khi job đã được nhận, đã vào hàng đợi, đã đánh thức GPU.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    param_type: str = ""
    defaults: dict[str, object] = field(default_factory=dict)
    locked_params: dict[str, object] = field(default_factory=dict)


# min_bytes lấy đúng hai ngưỡng pod-smoke.sh đã dùng và đã chứng minh:
#   mp4 100_000 (pod-smoke.sh:293) · ảnh 5_000 (pod-smoke.sh:44-49, đo thật tryon 1378 KB).
STAGES: dict[str, Stage] = {
    "tryon": Stage(
        name="tryon", job_type="tryon",
        inputs={"model": "material:character",
                "product": "material:outfit",
                "background": "material:background?"},
        output_ext=".png", min_bytes=5_000, timeout_min=20, param_type="tryon",
    ),
    "motion": Stage(
        name="motion", job_type="motion",
        inputs={"ref": "prev|material:character",
                "motion": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60, param_type="motion",
    ),
    "character-swap": Stage(
        name="character-swap", job_type="character-swap",
        inputs={"ref": "prev|material:character",
                "video": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60, param_type="character-swap",
    ),
    # enhance 1080p60 nội suy RIFE ×4 rồi encode lại — luôn lâu hơn motion sinh ra nó.
    "enhance": Stage(
        name="enhance", job_type="enhance",
        inputs={"input": "prev"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=90, param_type="enhance",
    ),
    "camera-tryon": Stage(
        name="camera-tryon", job_type="tryon", param_type="tryon",
        inputs={"model": "material:character",
                "product": "material:outfit",
                "background": "material:background",
                "cameraGuide": "material:driver"},
        output_ext=".png", min_bytes=5_000, timeout_min=20,
        defaults={"cameraGuideFrame": "middle"},
        locked_params={"cameraAware": True},
    ),
    "camera-motion": Stage(
        name="camera-motion", job_type="motion", param_type="motion",
        inputs={"ref": "prev", "motion": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60,
        # faceLock: this stage follows the driver harder than plain motion (pose 0.9, CLIP 1.2)
        # and Wan feeds it the driver's face crop every frame, so the face drifts off the
        # try-on image toward the driver. Re-swapping job 4cf001de's output on 18/09/2026 put
        # the try-on face back on all 452 frames in 22s of GPU time; judged by eye on video.
        # A default rather than a lock, so a manifest can still set faceLock: 0 to A/B.
        #
        # faceLockRestore: CodeFormer after the swap, fixing inswapper_128's waxy/flat texture.
        # Stable frame-to-frame, but pulls toward CodeFormer's own fuller-lip/bigger-eye prior —
        # see batch/2026-09-18-face-identity-ab.yaml arm E and the 18/09 addendum in
        # motions-studio/feature/face-restore-motion-delivery.md.
        #
        # faceLockBlend stays at 1.0 (swap_video.py's fast path, the unmodified upstream merge).
        # 19/09/2026 it was defaulted to 0.3 to counter inswapper's lip-fullness bias, on the
        # strength of ONE still frame from a local CPU test. Reverted the same day: the first
        # video batch to run with it came back with the face visibly blended toward the driver.
        # That is what the knob does by construction — _swap_blended scales the merge mask, so
        # blend=0.3 composites 30% swapped face over 70% of Wan's own face, and Wan's face is the
        # one following the driver (poseStrength 0.9, clipStrength 1.2, driver face crop per frame).
        # Diluting the swap cannot fix lip size without diluting identity by the same factor; a
        # future attempt at the lip bias needs a knob that does not scale the whole merge.
        defaults={"bodyProportionLock": False, "poseStrength": 0.9,
                  "clipStrength": 1.2, "naturalNails": True,
                  "removeWristAccessories": True, "faceLock": 1,
                  "faceLockRestore": 1, "faceLockBlend": 1.0},
        locked_params={"cameraAwareMotion": True, "fitDriver": True},
    ),
}

PIPELINES: dict[str, list[str]] = {
    # ALD 23/08/2026 - swap TRẦN, không enhance: dùng cho A/B màu. Đo trên 4 cặp 23/08 thì enhance
    # (Lanczos lẫn FlashVSR) lệch màu ≤2/255 so với đầu vào, nên với A/B về MÀU nó chỉ tốn GPU chứ
    # không đổi kết luận. A/B về NÉT thì vẫn phải chạy character-swap-enhance.
    "character-swap": ["character-swap"],
    "motion-enhance": ["motion", "enhance"],
    "tryon-motion-enhance": ["tryon", "motion", "enhance"],
    "character-swap-enhance": ["character-swap", "enhance"],
    "tryon-character-swap-enhance": ["tryon", "character-swap", "enhance"],
    "tryon-camera-motion-enhance": ["camera-tryon", "camera-motion", "enhance"],
}


def effective_stage_params(stage_name: str, manifest_params: dict | None = None) -> dict:
    stage = STAGES[stage_name]
    return {**stage.defaults, **dict(manifest_params or {}), **stage.locked_params}


def locked_stage_param_errors(stage_name: str, manifest_params: dict | None = None) -> list[str]:
    supplied = dict(manifest_params or {})
    return [
        f"{stage_name}.{key} is locked to {expected!r}"
        for key, expected in STAGES[stage_name].locked_params.items()
        if key in supplied and supplied[key] != expected
    ]


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
