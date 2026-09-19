"""stage (+ params) -> which comfyui/catalog-motion-transfer.json ids a Vast box needs.

Vast has no persistent Network Volume (spec docs/superpowers/specs/2026-09-19-vast-fallback-design.md
section 3.3): every rental starts from an empty disk, so the exact model set a manifest's pipelines
need must be known BEFORE the pod boots, to (a) give vast_select.rank's bandwidth-cost term a real
download size instead of a flat 60 GB guess, and (b) start the download in parallel with the backend
install instead of leaving ComfyUI to discover a missing model at job time.

Scope, matching the spec's approved table (section 3.3), corrected against the real runtime
default `run_character_swap` actually uses (verified against
motions-studio/worker/worker_runtime/linux.py:5466-5468, 2193-2210 while implementing this task
-- the spec's table alone would have under-specified this):
  - motion, camera-motion                 -> the Wan 2.2 Animate group (8 ids, ~34.4 GB)
  - character-swap, engine=wananimate     -> DEFAULT (run_character_swap's own default when no
    (default, or unset)                      `engine` param is given, linux.py:5466). Reuses
                                              build_wan_workflow (the Wan Animate group) plus
                                              _apply_swap_to_wan_workflow, which also loads
                                              sam3.1_multiplex_fp16.safetensors (linux.py:2203) ->
                                              the Wan Animate group PLUS swap-sam3.
  - character-swap, engine=scail2         -> the SCAIL-2/SAM3 group (5 ids, ~28.3 GB) -- opt-in
                                              only, matches the spec's section 3.3 table exactly.
  - enhance, engine=flashvsr (default)    -> the 4 flashvsr-* ids (~9.3 GB)
  - enhance, any other engine             -> nothing extra: ffmpeg lanczos needs no model, and
                                              seedvr2 is not in this catalog (not offered on Vast)
  - tryon, camera-tryon                   -> nothing extra

faceLock/faceLockRestore (camera-motion's on-by-default identity fix) are NOT modelled here: they
are installed unconditionally by pod-bootstrap.sh (scripts/pod-facelock.sh) on both providers
regardless of manifest content, so no per-batch registry entry could turn that install on or off.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .manifest import Manifest
from .pipelines import PIPELINES, effective_stage_params

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "motions-studio" / "comfyui" / "catalog-motion-transfer.json"

WAN_ANIMATE_IDS = frozenset({
    "wan-animate-14b", "wan-umt5-enc", "wan-vae", "wan-clip-vision-h",
    "wan-relight-lora", "wan-lightx2v-lora", "wan-vitpose-onnx", "wan-yolo10m-onnx",
})
CHARACTER_SWAP_IDS = frozenset({
    "swap-sam3", "swap-scail2-unet", "swap-scail2-umt5-fp8",
    "swap-scail2-lightx2v-r64", "swap-scail2-dpo",
})
# character-swap's DEFAULT engine (linux.py:5466 -- "wananimate" when the manifest gives no
# `engine` override, or gives that value explicitly): reuses the Wan Animate diffusion model plus
# the SAM3 segmentation checkpoint _apply_swap_to_wan_workflow loads (linux.py:2203).
WANANIMATE_SWAP_IDS = WAN_ANIMATE_IDS | frozenset({"swap-sam3"})
FLASHVSR_IDS = frozenset({
    "flashvsr-lq-proj", "flashvsr-tc-decoder", "flashvsr-streaming-dmd", "flashvsr-wan22-vae",
})

# Every id any resolver below can ever return -- check_drift() verifies each one is still a real
# catalog entry, without needing to enumerate every possible `params` value a resolver branches on.
ALL_REGISTRY_IDS = WAN_ANIMATE_IDS | CHARACTER_SWAP_IDS | FLASHVSR_IDS

Resolver = Callable[[dict], "frozenset[str]"]


def _enhance_ids(params: dict) -> frozenset[str]:
    engine = str(params.get("engine") or "flashvsr").strip().lower()
    if engine in ("flashvsr", "flash", "fvsr"):
        return FLASHVSR_IDS
    return frozenset()


def _character_swap_ids(params: dict) -> frozenset[str]:
    # run_character_swap (linux.py:5466) raises on anything other than these two values, and
    # defaults to "wananimate" when `engine` is not given -- so an unset/blank engine must
    # resolve the same way as the explicit default, not fall through to some third case.
    engine = str(params.get("engine") or "wananimate").strip().lower()
    if engine == "scail2":
        return CHARACTER_SWAP_IDS
    return WANANIMATE_SWAP_IDS


# One resolver per Stage name (scripts/batchlib/pipelines.py's STAGES keys). Every stage any
# PIPELINES entry can reach MUST have an entry here, or check_drift() below fails -- the same
# drift class check-job-types.mjs guards, for this registry instead of the job-type lists.
STAGE_MODEL_IDS: dict[str, Resolver] = {
    "tryon": lambda params: frozenset(),
    "camera-tryon": lambda params: frozenset(),
    "motion": lambda params: WAN_ANIMATE_IDS,
    "camera-motion": lambda params: WAN_ANIMATE_IDS,
    "character-swap": _character_swap_ids,
    "enhance": _enhance_ids,
}


def resolve_stage(stage_name: str, effective_params: dict) -> frozenset[str]:
    return STAGE_MODEL_IDS[stage_name](effective_params)


def models_for_manifest(manifest: Manifest) -> frozenset[str]:
    """Every catalog id every run's pipeline could need, unioned over the whole manifest.

    Uses effective_stage_params (defaults + manifest overrides), so a stage that reaches
    engine=flashvsr only through its own default (enhance's default today) is still counted.
    """
    ids: set[str] = set()
    for run in manifest.runs:
        for stage_name in PIPELINES[run.pipeline]:
            params = effective_stage_params(stage_name, run.stage_params.get(stage_name))
            ids |= resolve_stage(stage_name, params)
    return frozenset(ids)


def load_catalog_sizes(catalog_path: Path = CATALOG_PATH) -> dict[str, int]:
    data = json.loads(catalog_path.read_text())
    return {entry["id"]: int(entry.get("sizeBytes") or 0) for entry in data.get("comfy", [])}


def total_download_gb(manifest: Manifest, *, catalog_path: Path = CATALOG_PATH) -> float:
    """Sum of sizeBytes for models_for_manifest()'s ids, in GB (1e9 bytes) -- vast_rent.py's --gb."""
    ids = models_for_manifest(manifest)
    if not ids:
        return 0.0
    sizes = load_catalog_sizes(catalog_path)
    return sum(sizes.get(i, 0) for i in ids) / 1e9


def check_drift(catalog_path: Path = CATALOG_PATH) -> list[str]:
    """Gate: make check-vast-models.

    Fails when a stage any PIPELINES entry can reach has no STAGE_MODEL_IDS resolver, or when a
    registry id is not (or no longer) in the catalog -- the two ways this registry can silently
    stop matching reality: a new pipeline stage, or a catalog id renamed/removed.
    """
    errors: list[str] = []
    reachable_stages = {stage for stages in PIPELINES.values() for stage in stages}
    for stage_name in sorted(reachable_stages):
        if stage_name not in STAGE_MODEL_IDS:
            errors.append(
                f"stage {stage_name!r} is reachable from PIPELINES but has no entry in "
                f"STAGE_MODEL_IDS (scripts/batchlib/vast_models.py) -- add one, even if empty."
            )

    try:
        known_ids = set(load_catalog_sizes(catalog_path))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read catalog {catalog_path}: {exc}")
        return errors

    for missing in sorted(ALL_REGISTRY_IDS - known_ids):
        errors.append(
            f"catalog id {missing!r} is referenced in vast_models.py but not in "
            f"{catalog_path.name} -- the catalog moved and this registry did not follow."
        )
    return errors
