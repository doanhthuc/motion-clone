# Vast model registry — per-batch model download (Plan 3 of 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a Vast rental (no persistent Network Volume) the exact set of ComfyUI models a
batch manifest needs, downloaded in parallel with the rest of `pod-bootstrap.sh`'s install — and
give `vast_rent.py`'s bandwidth-cost ranking the real download size instead of a flat guess.

**Architecture:** A pure-Python registry (`scripts/batchlib/vast_models.py`) maps every
`scripts/batchlib/pipelines.py` `Stage` name (+ its effective params) to the
`comfyui/catalog-motion-transfer.json` ids it needs. `drain.py` resolves a manifest's ids and total
size once, and exports two env vars only for a Vast run: `VAST_GB` (before renting, for the
bandwidth-cost term in `vast_select.rank`) and `VAST_MODEL_IDS` (before bootstrapping). A new
`make check-vast-models` gate keeps the registry from drifting the way `check-job-types` guards
`PIPELINES` itself. `preload-models.sh` — until now a manual, volume-only, one-time tool — gets a
`MODELS_DIR` escape hatch so it can also write straight into a plain directory
(`$COMFY_DIR/models`, Vast's case), and `pod-bootstrap.sh`'s remote setup backgrounds one
`preload-models.sh` run right after ComfyUI is cloned, in parallel with the pip/custom-node
install that already runs there, and waits for it before moving on.

**Tech Stack:** Python 3 (batchlib), Bash (setup scripts, Makefile), existing `unittest` +
`Makefile`/`pod-bootstrap.sh`-testing conventions from this repo (scratch temp dirs, no network,
no GPU spend).

**Spec:** `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` §3.3 "Models per batch" (the
table of stage → catalog ids), §4 "Testing" (free vs. paid), §8 item 4 ("Model registry, parallel
download in bootstrap, `check-vast-models`"). This plan implements exactly that item; the bot
picker/spend-button half of the design (§3.5) is Plan 4, not this one.

## Global Constraints

- Registry scope is exactly the spec's §3.3 table, corrected against the real default this plan
  got wrong on first draft: `run_character_swap`'s public `engine` param defaults to
  `"wananimate"`, not `"scail2"` (`motions-studio/worker/worker_runtime/linux.py:5466-5468`,
  `raise`s on anything else). `wananimate` reuses `build_wan_workflow` (the Wan 2.2 Animate group)
  plus `_apply_swap_to_wan_workflow`, which also loads `sam3.1_multiplex_fp16.safetensors`
  (`linux.py:2193-2210`) — so the DEFAULT character-swap case needs the Wan Animate group **plus**
  `swap-sam3`, and the spec's §3.3 table (the SCAIL-2/SAM3 group) applies only when a manifest
  sets `engine: scail2` explicitly. `enhance`'s `seedvr2` engine is still correctly unmodelled
  (zero extra ids — it is not in this catalog at all, spec §5 "Not proven").
- faceLock/faceLockRestore are **not** modelled in the registry: `scripts/pod-facelock.sh` is
  already installed unconditionally by `pod-bootstrap.sh` on both providers regardless of manifest
  content (`scripts/pod-bootstrap.sh:366-373`), so no per-batch entry can turn it on or off.
- The new parallel-download behavior in `pod-bootstrap.sh`/`lib-feature.sh` is gated on
  `VAST_MODEL_IDS` being non-empty, which `drain.py` only ever sets for an **explicit**
  `--provider vast|<non-runpod>` run (same `chosen and chosen != "runpod"` condition
  `drain.py`'s existing `provision()` already uses for `POD_VOLUME=`, at `scripts/drain.py:124-125`)
  — RunPod's bootstrap path is byte-for-byte unchanged.
- Write all new code, comments, and docs in English (CLAUDE.md convention). Existing Vietnamese
  code/comments in files this plan touches stay as they are — do not translate in passing.
  `scripts/batchlib/pipelines.py`'s Vietnamese docstrings/comments are untouched by this plan.
- Every free gate must still pass after every task: `make batch-test`, `make check-job-types`,
  `make check-comfy-nodes`, `make check-batch-params`, `make check-vast-models` (new, from Task 2
  onward), `motions-studio/setup/scrub-secrets.sh --check`.
- No task in this plan spends money or requires a rented pod to verify. The one piece that
  genuinely cannot be verified without a live GPU box — the background-download/`wait` logic
  inside `lib-feature.sh`'s `phase_comfyui` — is implemented carefully, shellchecked, and flagged
  explicitly in `docs/gpu-pod.md`'s existing "assumptions NOT verified yet" list (Task 4) rather
  than covered by an invented fake-infrastructure test that wouldn't buy real confidence.
- Commit trailer, exactly, on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
  ```

## File Structure

| File | Responsibility |
|---|---|
| `scripts/batchlib/vast_models.py` | **New.** The registry: stage → catalog ids, `models_for_manifest()`, `total_download_gb()`, `check_drift()`. |
| `scripts/tests/test_batch_vast_models.py` | **New.** Unit tests for the above. |
| `scripts/check_vast_models.py` | **New.** Thin CLI wrapper for `make check-vast-models` (mirrors `scripts/batch_params.py`). |
| `Makefile` | Add `check-vast-models` target + `.PHONY` entry. |
| `CLAUDE.md` | Add `make check-vast-models` to the gates list. |
| `motions-studio/setup/preload-models.sh` | Add `MODELS_DIR` override for a no-volume plain destination. |
| `scripts/tests/test_batch_preload_models.py` | **New.** Black-box tests of the above, real script + fake local catalog, no network. |
| `scripts/drain.py` | `provision()` exports `VAST_GB`; `wait_and_bootstrap()` exports `VAST_MODEL_IDS` — both only for an explicit non-runpod `--provider`. |
| `scripts/tests/test_batch_drain.py` | Extend `TestProvision`, add `TestWaitAndBootstrap`. |
| `scripts/pod-bootstrap.sh` | Forward `VAST_MODEL_IDS` (process env, no `.env` fallback) into the remote setup ssh command. |
| `motions-studio/setup/lib-feature.sh` | `phase_comfyui` backgrounds `preload-models.sh` right after the ComfyUI clone, `wait`s for it before returning. |
| `docs/gpu-pod.md` | Update the `VAST_GB` note (§`vast-provider`) to say it's implemented; add the parallel-download step to the "NOT verified yet" list. |

---

### Task 1: The model registry

**Files:**
- Create: `scripts/batchlib/vast_models.py`
- Test: `scripts/tests/test_batch_vast_models.py`

**Interfaces:**
- Consumes: `scripts/batchlib/pipelines.py`'s `PIPELINES: dict[str, list[str]]`,
  `effective_stage_params(stage_name, manifest_params) -> dict` (both already exist, unchanged).
  `scripts/batchlib/manifest.py`'s `Manifest` (has `.runs: list[Run]`, each `Run` has
  `.pipeline: str` and `.stage_params: dict[str, dict]` — both already exist, unchanged).
- Produces (used by Task 4): `models_for_manifest(manifest: Manifest) -> frozenset[str]`,
  `total_download_gb(manifest: Manifest, *, catalog_path: Path = CATALOG_PATH) -> float`.
  Produces (used by Task 2): `check_drift(catalog_path: Path = CATALOG_PATH) -> list[str]`.

- [ ] **Step 1: Write `scripts/batchlib/vast_models.py`**

```python
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
```

- [ ] **Step 2: Write `scripts/tests/test_batch_vast_models.py`**

```python
import json
import tempfile
import unittest
from pathlib import Path

from batchlib.manifest import Manifest, Run
from batchlib.vast_models import (ALL_REGISTRY_IDS, CHARACTER_SWAP_IDS, FLASHVSR_IDS,
                                  WAN_ANIMATE_IDS, WANANIMATE_SWAP_IDS, check_drift,
                                  models_for_manifest, resolve_stage, total_download_gb)


def _run(run_id: str, pipeline: str, stage_params: dict | None = None) -> Run:
    return Run(id=run_id, pipeline=pipeline, stage_params=stage_params or {})


def _manifest(*runs: Run) -> Manifest:
    return Manifest(path=Path("t.yaml"), runs=list(runs))


class TestResolveStage(unittest.TestCase):
    def test_motion_needs_the_wan_animate_group(self):
        self.assertEqual(resolve_stage("motion", {}), WAN_ANIMATE_IDS)

    def test_camera_motion_needs_the_same_group_as_plain_motion(self):
        self.assertEqual(resolve_stage("camera-motion", {}), WAN_ANIMATE_IDS)

    def test_character_swap_defaults_to_wananimate_which_reuses_the_wan_animate_group(self):
        # run_character_swap's own default when no `engine` param is given (linux.py:5466).
        self.assertEqual(resolve_stage("character-swap", {}), WANANIMATE_SWAP_IDS)

    def test_character_swap_explicit_wananimate_matches_the_default(self):
        self.assertEqual(resolve_stage("character-swap", {"engine": "wananimate"}),
                         WANANIMATE_SWAP_IDS)

    def test_character_swap_explicit_scail2_needs_the_scail2_sam3_group(self):
        self.assertEqual(resolve_stage("character-swap", {"engine": "scail2"}),
                         CHARACTER_SWAP_IDS)

    def test_wananimate_swap_group_is_the_wan_animate_group_plus_sam3(self):
        self.assertEqual(WANANIMATE_SWAP_IDS, WAN_ANIMATE_IDS | {"swap-sam3"})

    def test_tryon_and_camera_tryon_need_nothing_extra(self):
        self.assertEqual(resolve_stage("tryon", {}), frozenset())
        self.assertEqual(resolve_stage("camera-tryon", {}), frozenset())

    def test_enhance_defaults_to_flashvsr(self):
        self.assertEqual(resolve_stage("enhance", {}), FLASHVSR_IDS)

    def test_enhance_engine_lanczos_needs_nothing_extra(self):
        self.assertEqual(resolve_stage("enhance", {"engine": "lanczos"}), frozenset())

    def test_enhance_engine_seedvr2_needs_nothing_from_this_catalog(self):
        # seedvr2 has its own models, but none are in catalog-motion-transfer.json -- this
        # registry correctly has nothing extra to add, it does not silently misclassify it.
        self.assertEqual(resolve_stage("enhance", {"engine": "seedvr2"}), frozenset())

    def test_enhance_engine_is_case_and_whitespace_insensitive(self):
        self.assertEqual(resolve_stage("enhance", {"engine": " FlashVSR "}), FLASHVSR_IDS)


class TestModelsForManifest(unittest.TestCase):
    def test_unions_ids_across_every_run_and_stage(self):
        m = _manifest(
            _run("r1", "motion-enhance"),
            _run("r2", "character-swap", {"character-swap": {"engine": "scail2"}}),
        )
        ids = models_for_manifest(m)
        self.assertEqual(ids, WAN_ANIMATE_IDS | FLASHVSR_IDS | CHARACTER_SWAP_IDS)

    def test_a_default_character_swap_run_needs_the_wananimate_group(self):
        m = _manifest(_run("r1", "character-swap"))
        self.assertEqual(models_for_manifest(m), WANANIMATE_SWAP_IDS)

    def test_a_manifest_with_only_tryon_needs_nothing_extra(self):
        m = _manifest(_run("r1", "tryon-motion-enhance",
                            {"enhance": {"engine": "lanczos"}}))
        ids = models_for_manifest(m)
        self.assertEqual(ids, WAN_ANIMATE_IDS)  # tryon: nothing, motion: wan, enhance: lanczos=nothing

    def test_empty_manifest_needs_nothing(self):
        self.assertEqual(models_for_manifest(_manifest()), frozenset())


class TestTotalDownloadGb(unittest.TestCase):
    def test_matches_the_measured_wan_animate_group_total(self):
        m = _manifest(_run("r1", "motion-enhance", {"enhance": {"engine": "lanczos"}}))
        gb = total_download_gb(m)
        # Measured 2026-09-19 (spec section 3.3): ~34.4 GB for the Wan 2.2 Animate group.
        self.assertAlmostEqual(gb, 34.4, delta=0.1)

    def test_empty_manifest_is_zero(self):
        self.assertEqual(total_download_gb(_manifest()), 0.0)

    def test_uses_a_custom_catalog_path_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({
                "comfy": [{"id": i, "sizeBytes": 1_000_000_000} for i in WAN_ANIMATE_IDS],
            }))
            m = _manifest(_run("r1", "motion-enhance", {"enhance": {"engine": "lanczos"}}))
            gb = total_download_gb(m, catalog_path=catalog)
            self.assertAlmostEqual(gb, len(WAN_ANIMATE_IDS) * 1.0, delta=0.01)


class TestCheckDrift(unittest.TestCase):
    def test_the_real_catalog_and_registry_agree(self):
        self.assertEqual(check_drift(), [])

    def test_a_stage_missing_from_the_registry_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({"comfy": [
                {"id": i, "sizeBytes": 1} for i in ALL_REGISTRY_IDS
            ]}))
            import batchlib.vast_models as vm
            old = dict(vm.STAGE_MODEL_IDS)
            try:
                del vm.STAGE_MODEL_IDS["enhance"]
                errors = check_drift(catalog_path=catalog)
            finally:
                vm.STAGE_MODEL_IDS.clear()
                vm.STAGE_MODEL_IDS.update(old)
            self.assertTrue(any("enhance" in e for e in errors))

    def test_a_registry_id_missing_from_the_catalog_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({"comfy": [
                {"id": i, "sizeBytes": 1} for i in ALL_REGISTRY_IDS if i != "wan-vae"
            ]}))
            errors = check_drift(catalog_path=catalog)
            self.assertTrue(any("wan-vae" in e for e in errors))

    def test_an_unreadable_catalog_is_one_clear_error_not_a_crash(self):
        errors = check_drift(catalog_path=Path("/nonexistent/catalog.json"))
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot read catalog", errors[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests**

Run: `cd /Users/thucpham/Desktop/motion-clone && python3 -m unittest scripts.tests.test_batch_vast_models -v`
(or, matching the repo's own convention: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_models.py' -v`)
Expected: all tests PASS. `test_the_real_catalog_and_registry_agree` is the one that proves this
task's ids actually match the live `catalog-motion-transfer.json` — read the actual file first if
it fails, don't just adjust the test.

Check the `Manifest`/`Run` dataclass constructor signatures against the real
`scripts/batchlib/manifest.py` before writing the test file (field names/defaults may not be
exactly `batch_id=`/`stage_params=` as sketched above) — this plan was written from reading that
file, but copy the exact `@dataclass` field list from the source, not from this plan's prose.

- [ ] **Step 4: Run the existing full free-gate suite once, to prove nothing else broke**

Run: `make batch-test && make check-job-types && make check-comfy-nodes && make check-batch-params`
Expected: all green, unchanged from before this task (this task adds a new file; it does not
modify `pipelines.py`, `manifest.py`, or any existing gate).

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/vast_models.py scripts/tests/test_batch_vast_models.py
git commit -m "batchlib: add the Vast per-batch model registry"
```

---

### Task 2: `make check-vast-models` gate

**Files:**
- Create: `scripts/check_vast_models.py`
- Modify: `Makefile` (add target + `.PHONY` entry)
- Modify: `CLAUDE.md` (add to the gates list, two places)

**Interfaces:**
- Consumes: `scripts/batchlib/vast_models.check_drift() -> list[str]` (Task 1, unchanged).

- [ ] **Step 1: Write `scripts/check_vast_models.py`**

```python
#!/usr/bin/env python3
"""Gate: every PIPELINES stage has a Vast model-registry entry, and every id it names is real.

    python3 scripts/check_vast_models.py     # make check-vast-models
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.vast_models import check_drift


def main() -> int:
    errors = check_drift()
    if errors:
        print("✗ scripts/batchlib/vast_models.py has drifted from PIPELINES or the catalog:",
              file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        return 1
    print("✓ vast model registry matches PIPELINES and the catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it once by hand to see it pass**

Run: `python3 scripts/check_vast_models.py`
Expected: `✓ vast model registry matches PIPELINES and the catalog`, exit 0.

- [ ] **Step 3: Wire it into the Makefile**

In `Makefile`, add `check-vast-models` to the `.PHONY` line (line 2), and add this target
immediately after `check-batch-params` (currently `Makefile:85-86`):

```makefile
check-vast-models: ## Gate: every batch-manifest stage has a Vast model-registry entry, ids match the catalog
	@python3 scripts/check_vast_models.py
```

- [ ] **Step 4: Add it to CLAUDE.md's gates list**

In `CLAUDE.md`, in the "Money is a first-class constraint" section's free-gates sentence (currently
naming `gpu-preflight`, `batch-validate`, `batch-test`, `check-job-types`, `check-comfy-nodes`,
`check-batch-params`), add `make check-vast-models` to that list. In the "Commands" fenced block,
add a line right after the existing `make check-batch-params` line:

```
make check-vast-models                            # scripts/batchlib/vast_models.py vs PIPELINES/catalog
```

- [ ] **Step 5: Verify the gate actually gates**

Temporarily break it (e.g. comment out the `"enhance"` line in `STAGE_MODEL_IDS`), confirm
`make check-vast-models` exits 1 with a message naming `enhance`, then restore the file and
confirm it's back to exit 0. Do not leave the temporary breakage committed.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_vast_models.py Makefile CLAUDE.md
git commit -m "make: add check-vast-models gate"
```

---

### Task 3: `preload-models.sh` — a no-volume destination

**Files:**
- Modify: `motions-studio/setup/preload-models.sh`
- Test: `scripts/tests/test_batch_preload_models.py`

**Interfaces:**
- Produces (used by Task 4): a `MODELS_DIR` env var that, when set, makes the script install
  straight into that directory instead of requiring `POD_VOLUME` + a `comfy-models/` subfolder.
  `POD_VOLUME`'s existing behavior is completely unchanged when `MODELS_DIR` is unset.

Read `motions-studio/setup/preload-models.sh` in full before editing — this task only touches the
destination-resolution lines near the top and the disk-usage measurement further down; everything
else (catalog selection, aria2c download loop, size verification) is untouched.

- [ ] **Step 1: Add the `MODELS_DIR` override**

Replace this block (currently around line 31-32):

```bash
CATALOG="${CATALOG:-$ROOT/comfyui/catalog.json}"
VOL="${POD_VOLUME:-}"
```

with:

```bash
CATALOG="${CATALOG:-$ROOT/comfyui/catalog.json}"
VOL="${POD_VOLUME:-}"
# A plain destination directory, no volume needed -- Vast has no Network Volume (spec
# docs/superpowers/specs/2026-09-19-vast-fallback-design.md section 3.3), so on Vast this points
# straight at $COMFY_DIR/models instead of $POD_VOLUME/comfy-models. MODELS_DIR wins when both
# happen to be set (see MODELS= below); RunPod only ever sets POD_VOLUME, so its flow is
# completely unchanged when this is left unset.
DEST="${MODELS_DIR:-}"
```

- [ ] **Step 2: Make the volume-mount check conditional on `DEST` being unset**

Replace this line (currently around line 69-70):

```bash
[ -n "$VOL" ] || die "cần POD_VOLUME=<đường mount volume>, vd POD_VOLUME=/workspace"
[ -d "$VOL" ] || die "$VOL không tồn tại — volume chưa mount?"
```

with:

```bash
if [ -z "$DEST" ]; then
  [ -n "$VOL" ] || die "cần POD_VOLUME=<đường mount volume> hoặc MODELS_DIR=<thư mục đích>"
  [ -d "$VOL" ] || die "$VOL không tồn tại — volume chưa mount?"
fi
```

- [ ] **Step 3: Resolve `MODELS` from `DEST` when set**

Replace this line (currently around line 77):

```bash
MODELS="$VOL/comfy-models"
```

with:

```bash
MODELS="${DEST:-$VOL/comfy-models}"
```

- [ ] **Step 4: Point the disk-usage measurement at whichever one is actually being written to**

Replace this line (currently around line 167):

```bash
USED=$(du -sb "$VOL" 2>/dev/null | awk '{printf "%.0f", $1}')
```

with:

```bash
USED=$(du -sb "${DEST:-$VOL}" 2>/dev/null | awk '{printf "%.0f", $1}')
```

(The `VOLUME_GB`-gated disk-space check downstream is otherwise untouched — on a Vast box there is
usually no `VOLUME_GB` equivalent to set, so it falls through to the existing "cannot check, proceed
at your own risk" warning, which is correct: disk was already sized by `vast_rent.py --disk` at
rent time, not by a monthly-billed volume quota.)

- [ ] **Step 5: Write `scripts/tests/test_batch_preload_models.py`**

This runs the REAL script end-to-end with `--dry-run` against a tiny local fake catalog whose
`url` fields are `file://` URLs — Python's `urllib.request` sets a real `Content-Length` header
for local files, so `remote_size()` inside the script's embedded Python works with zero network
access.

```python
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "motions-studio" / "setup" / "preload-models.sh"


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                           capture_output=True, text=True, timeout=30)


def _fake_catalog(tmp: Path) -> Path:
    payload = tmp / "payload.bin"
    payload.write_bytes(b"x" * 4096)
    catalog = tmp / "catalog.json"
    catalog.write_text(json.dumps({
        "comfy": [{"id": "fake-id", "group": "Fake", "type": "checkpoints",
                   "filename": "fake.safetensors", "url": payload.as_uri(),
                   "sizeBytes": 4096}],
        "ollama": [],
    }))
    return catalog


class TestModelsDirOverride(unittest.TestCase):
    def _env(self, tmp: Path, **extra) -> dict:
        import os
        env = os.environ.copy()
        env.pop("POD_VOLUME", None)
        env["CATALOG"] = str(_fake_catalog(tmp))
        env.update(extra)
        return env

    def test_downloads_straight_into_models_dir_no_volume_needed(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            dest = tmp / "models"
            env = self._env(tmp, MODELS_DIR=str(dest))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("comfy-models", result.stdout)

    def test_actually_writes_the_file_directly_under_models_dir_when_not_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            dest = tmp / "models"
            env = self._env(tmp, MODELS_DIR=str(dest))
            result = _run(env, "--id", "fake-id")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((dest / "checkpoints" / "fake.safetensors").is_file())
            self.assertFalse((dest / "comfy-models").exists())

    def test_no_models_dir_and_no_pod_volume_dies_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            env = self._env(Path(tmp_s))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MODELS_DIR", result.stderr)

    def test_pod_volume_path_is_completely_unchanged_when_models_dir_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            vol = tmp / "workspace"; vol.mkdir()
            env = self._env(tmp, POD_VOLUME=str(vol))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("comfy-models", result.stdout + str(list(vol.glob("*"))) or "", "")
            # the plan's real assertion: comfy-models subfolder convention still used
            self.assertTrue((vol / "comfy-models").is_dir() or result.returncode == 0)


if __name__ == "__main__":
    unittest.main()
```

While writing this step, replace the last, slightly awkward assertion in
`test_pod_volume_path_is_completely_unchanged_when_models_dir_is_unset` with a clean one once the
script's actual `--dry-run` output text is known from a real run (e.g. assert
`(vol / "comfy-models").is_dir()` after `mkdir -p "$MODELS"` runs even in dry-run mode, or drop to
a non-dry-run variant like the `MODELS_DIR` test above if `--dry-run` does not create the
directory) — do not leave a placeholder assertion that always passes regardless of behavior.

- [ ] **Step 6: Run the tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_preload_models.py' -v`
Expected: all PASS, no network access (verify by running with network disabled or by checking no
`huggingface.co`/real URL ever appears in the fake catalog).

- [ ] **Step 7: Shellcheck**

Run: `shellcheck motions-studio/setup/preload-models.sh` (if shellcheck is available; if not,
read the diff carefully by eye — this script has `set -uo pipefail`, so a newly-empty `$VOL` used
unquoted anywhere new would be the main risk, and both new uses above are quoted).

- [ ] **Step 8: Commit**

```bash
git add motions-studio/setup/preload-models.sh scripts/tests/test_batch_preload_models.py
git commit -m "preload-models: support a plain MODELS_DIR destination, no volume required"
```

---

### Task 4: Wire it end to end — rent, bootstrap, and the parallel download

**Files:**
- Modify: `scripts/drain.py`
- Modify: `scripts/tests/test_batch_drain.py`
- Modify: `scripts/pod-bootstrap.sh`
- Modify: `motions-studio/setup/lib-feature.sh`
- Modify: `docs/gpu-pod.md`

**Interfaces:**
- Consumes: `batchlib.vast_models.models_for_manifest`, `total_download_gb` (Task 1).
  `vast_rent.py`'s existing `--gb`/`VAST_GB` (already implemented, Plan 2) and
  `preload-models.sh`'s new `MODELS_DIR` (Task 3).
- Produces: nothing further downstream — this is the last hook in the chain for this plan.

Read `scripts/drain.py`'s `provision()` and the `main()` call sites in full before editing — this
task changes both functions' signatures, so every caller (including tests) must be updated in the
same commit or the test suite will fail with a `TypeError`, not a useful assertion failure.

- [ ] **Step 1: `drain.py` — export `VAST_GB` from `provision()`**

In `scripts/drain.py`, add the import near the top (alongside the existing `from batchlib.manifest
import Manifest, load_manifest, load_state, state_path_for`):

```python
from batchlib.vast_models import models_for_manifest, total_download_gb
```

Change `provision()`'s signature to accept the already-loaded manifest, and use it exactly where
`no_volume` is already computed (currently `scripts/drain.py:98-128`):

```python
def provision(*, ceiling_min: int, manifest_path: Path, manifest: Manifest) -> str:
    """Rent a pod and return its instance id. Does NOT wait or bootstrap.
    ...  # (existing docstring, unchanged, plus:)

    `manifest` is used only to size VAST_GB (the bandwidth-cost term vast_select.rank uses) to
    this run's actual download -- never to decide what pipeline/params to run; batch_run.py owns
    that. Only set for the same explicit non-runpod --provider case POD_VOLUME= already is,
    below, so a RunPod run's rent command is unchanged.
    """
    hours = pod_max_hours(ceiling_min, env_get(ROOT / ".env", "POD_MAX_HOURS"))
    chosen = os.environ.get("GPU_PROVIDER", "")
    no_volume = "POD_VOLUME= " if chosen and chosen != "runpod" else ""
    vast_gb = ""
    if chosen and chosen != "runpod":
        gb = total_download_gb(manifest)
        if gb > 0:
            vast_gb = f"VAST_GB={gb:.1f} "
    result = subprocess.run(
        f"{no_volume}{vast_gb}POD_MAX_HOURS={hours} CONFIRM=yes bash scripts/pod-provision.sh",
        shell=True, cwd=ROOT, stderr=subprocess.PIPE, text=True)
    # ... rest of the function unchanged
```

- [ ] **Step 2: `drain.py` — export `VAST_MODEL_IDS` from `wait_and_bootstrap()`**

Change (currently `scripts/drain.py:156-159`):

```python
def wait_and_bootstrap() -> None:
    """Block until the pod answers SSH, then install the backend on it."""
    sh("bash", "scripts/pod-wait.sh")
    sh("bash", "scripts/pod-bootstrap.sh")
```

to:

```python
def wait_and_bootstrap(manifest: Manifest) -> None:
    """Block until the pod answers SSH, then install the backend on it.

    Exports VAST_MODEL_IDS (space-separated catalog ids) for pod-bootstrap.sh to forward to the
    remote setup script, which backgrounds a preload-models.sh run for them in parallel with its
    own install -- only for the same explicit non-runpod --provider case provision() gates
    VAST_GB on, so a RunPod bootstrap is unchanged.
    """
    sh("bash", "scripts/pod-wait.sh")
    chosen = os.environ.get("GPU_PROVIDER", "")
    if chosen and chosen != "runpod":
        ids = models_for_manifest(manifest)
        if ids:
            os.environ["VAST_MODEL_IDS"] = " ".join(sorted(ids))
    sh("bash", "scripts/pod-bootstrap.sh")
```

- [ ] **Step 3: Update the call sites in `main()`**

Change (currently `scripts/drain.py:379` and `:394`):

```python
    pod_id = provision(ceiling_min=ceiling, manifest_path=manifest_path)
```
to:
```python
    pod_id = provision(ceiling_min=ceiling, manifest_path=manifest_path, manifest=manifest)
```

and:
```python
        wait_and_bootstrap()
```
to:
```python
        wait_and_bootstrap(manifest)
```

(`manifest` is already loaded earlier in `main()` via `manifest = load_manifest(manifest_path)` —
no new loading needed.)

- [ ] **Step 4: Update `scripts/tests/test_batch_drain.py`'s existing `TestProvision` calls**

Add `Manifest, Run` to the existing `from batchlib.manifest import load_manifest, state_path_for`
import line (top of the file, currently line 6).

Every existing `provision(ceiling_min=..., manifest_path=...)` call in `TestProvision` (currently
`scripts/tests/test_batch_drain.py:72-151`) must add `manifest=_empty_manifest()` (the fixture
added in Step 5 below — define it before this point in the file, or move it above
`TestProvision`). Since none of those tests set `GPU_PROVIDER` in `os.environ`, `chosen == ""` and
the new `vast_gb` code path is never exercised by them — confirm each still asserts exactly the
command string it did before (no `VAST_GB=` should appear).

Do the same for any existing `wait_and_bootstrap()` call/reference in that file, if one exists.

- [ ] **Step 5: Add `TestWaitAndBootstrap` and extend `TestProvision` for the Vast case**

Add these two fixture helpers near the top of the test file (exact `Manifest`/`Run` fields, from
`scripts/batchlib/manifest.py`: `Run(id, pipeline, inputs={}, stage_params={})`,
`Manifest(path, runs)`):

```python
def _empty_manifest() -> Manifest:
    return Manifest(path=Path("x.yaml"), runs=[])


def _manifest_with_a_motion_run() -> Manifest:
    return Manifest(path=Path("x.yaml"), runs=[
        Run(id="r1", pipeline="motion-enhance",
            stage_params={"enhance": {"engine": "lanczos"}}),
    ])
```

```python
class TestWaitAndBootstrap(unittest.TestCase):
    def test_runpod_does_not_set_vast_model_ids(self):
        m = _empty_manifest()
        with mock.patch.object(drain, "sh") as mock_sh, \
             mock.patch.dict(os.environ, {"GPU_PROVIDER": "runpod"}, clear=False):
            drain.wait_and_bootstrap(m)
        self.assertNotIn("VAST_MODEL_IDS", os.environ)
        mock_sh.assert_any_call("bash", "scripts/pod-bootstrap.sh")

    def test_vast_with_models_sets_vast_model_ids(self):
        m = _manifest_with_a_motion_run()
        os.environ.pop("VAST_MODEL_IDS", None)
        with mock.patch.object(drain, "sh"), \
             mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}, clear=False):
            drain.wait_and_bootstrap(m)
            self.assertIn("wan-animate-14b", os.environ.get("VAST_MODEL_IDS", ""))
        os.environ.pop("VAST_MODEL_IDS", None)  # don't leak into later tests

    def test_vast_with_no_models_needed_does_not_set_the_env_var(self):
        m = _empty_manifest()
        os.environ.pop("VAST_MODEL_IDS", None)
        with mock.patch.object(drain, "sh"), \
             mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}, clear=False):
            drain.wait_and_bootstrap(m)
        self.assertNotIn("VAST_MODEL_IDS", os.environ)
```

Add the matching `provision()` case:

```python
    def test_explicit_vast_provider_adds_vast_gb(self):
        m = _manifest_with_a_motion_run()
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]), \
             mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}, clear=False):
            drain.provision(ceiling_min=60, manifest_path=Path("x.yaml"), manifest=m)
        cmd = mock_run.call_args[0][0]
        self.assertIn("VAST_GB=34.4", cmd)
```

Use `mock.patch.dict(os.environ, ..., clear=False)` (not a bare assignment) so a test failure never
leaves `GPU_PROVIDER`/`VAST_MODEL_IDS` set for whatever unittest test runs next in the same
process — the same isolation concern that made `test_batch_vast_rent.py`'s `TestMain` need a
`mock.patch.dict(os.environ, {}, clear=False)` wrapper in Plan 2.

- [ ] **Step 6: Run the tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: all PASS, including every pre-existing `TestProvision`/`TestTeardown`/etc. test
(updated signatures only, no behavior change for the runpod path).

- [ ] **Step 7: `pod-bootstrap.sh` — forward `VAST_MODEL_IDS`**

`VAST_MODEL_IDS` is a process-environment variable `drain.py` sets directly (like `GPU_PROVIDER`),
never written to `.env` — there is no `.env` fallback to read. Add it to the big ssh command's
`${VAR:+...}` block (currently `scripts/pod-bootstrap.sh:179-196`), next to
`JOB_TYPES_OVERRIDE`:

```
${JOB_TYPES_OVERRIDE:+JOB_TYPES_OVERRIDE='$JOB_TYPES_OVERRIDE'} \
${VAST_MODEL_IDS:+VAST_MODEL_IDS='$VAST_MODEL_IDS'} \
${POD_VOLUME:+POD_VOLUME='$POD_VOLUME'} \
```

(Insert the new line as shown; the surrounding lines are quoted from the existing file only to
show placement — do not otherwise reorder or reformat that block.)

- [ ] **Step 8: `lib-feature.sh` — background the download in `phase_comfyui`**

Read `motions-studio/setup/lib-feature.sh`'s `phase_comfyui()` (currently lines 627-763) in full
before editing. Insert the background-launch right where `$COMFY_DIR` (and therefore
`$COMFY_DIR/models`, which ships inside the ComfyUI git repo) is guaranteed to exist — immediately
after the clone-or-repair block closes, currently:

```bash
      else
        git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR" || warn "clone ComfyUI lỗi."
      fi
    fi
    [ -x "$COMFY_DIR/venv/bin/python" ] || python3 -m venv "$COMFY_DIR/venv"
```

Insert a new block between the closing `fi` and the `venv` line:

```bash
      else
        git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR" || warn "clone ComfyUI lỗi."
      fi
    fi

    # Background the manifest's model download here (Vast only -- RunPod already has a
    # persistent Network Volume and its own manual CPU-pod preload workflow, see
    # docs/gpu-pod.md#preload). $COMFY_DIR/models already exists from the ComfyUI clone above.
    # Measured 2026-09-19 (spec section 3.3): ~34.4 GB in 137s, well inside the ~200s the
    # pip/custom-node install below takes on its own, so running both together costs about what
    # the slower one costs alone instead of the sum of the two.
    PRELOAD_PID=""
    if [ -n "${VAST_MODEL_IDS:-}" ]; then
      say "    Vast: bắt đầu tải model của manifest, chạy nền song song với cài đặt bên dưới…"
      ID_ARGS=()
      for _id in $VAST_MODEL_IDS; do ID_ARGS+=(--id "$_id"); done
      ( MODELS_DIR="$COMFY_DIR/models" bash "$ROOT/setup/preload-models.sh" "${ID_ARGS[@]}" \
          >/tmp/preload-models.log 2>&1 ) &
      PRELOAD_PID=$!
    fi

    [ -x "$COMFY_DIR/venv/bin/python" ] || python3 -m venv "$COMFY_DIR/venv"
```

Then insert a `wait` right after the custom-node/SageAttention install finishes, currently:

```bash
    ok "ComfyUI + custom node ($(echo $COMFY_NODES | wc -w) node) ở $COMFY_DIR"

    # Thư mục uploads (model user tự upload) + extra_model_paths.
```

becomes:

```bash
    ok "ComfyUI + custom node ($(echo $COMFY_NODES | wc -w) node) ở $COMFY_DIR"

    if [ -n "$PRELOAD_PID" ]; then
      say "    đợi tải model của manifest xong (chạy nền ở trên)…"
      if wait "$PRELOAD_PID"; then
        ok "model của manifest đã tải xong (/tmp/preload-models.log)"
      else
        warn "tải model của manifest LỖI — xem /tmp/preload-models.log. Chạy lại tay:"
        warn "  MODELS_DIR=$COMFY_DIR/models bash setup/preload-models.sh --id <id> [--id <id> ...]"
      fi
    fi

    # Thư mục uploads (model user tự upload) + extra_model_paths.
```

Both edits are inside the existing `if [ "$GPU_OK" = "1" ]; then ... fi` block — correct, since
without a GPU there is no ComfyUI/models directory to download into either.

- [ ] **Step 9: Syntax-check the shell changes**

Run: `bash -n motions-studio/setup/lib-feature.sh && bash -n scripts/pod-bootstrap.sh`
Expected: no output, exit 0 (pure syntax check — this cannot verify the runtime behavior, which
needs a real pod; see the next step).

If `shellcheck` is available, also run it against both files and read through any new warnings
introduced by this task's lines specifically (pre-existing warnings elsewhere in these large files
are out of scope).

- [ ] **Step 10: Update `docs/gpu-pod.md`**

In the `<a id="vast-provider"></a>` section, find the sentence (currently around line 569):

> `VAST_GB` (default 60) is what the rental will download; Plan 3 passes the exact figure per batch.

Replace it with:

> `VAST_GB` is the manifest's actual download size (`batchlib.vast_models.total_download_gb`),
> computed and exported by `drain.py`'s `provision()` before renting; it falls back to the
> `vast_rent.py` default of 60 only when nothing in the manifest needs any extra model (or when
> `pod-provision.sh`/`vast_rent.py` is run directly, outside `drain.py`).

In the "First paid session — assumptions that are NOT verified yet" list (starts around line 591),
add a new bullet:

> - The parallel model download inside `phase_comfyui` (`lib-feature.sh`): backgrounding
>   `preload-models.sh` right after the ComfyUI clone and `wait`-ing for it before the rest of
>   bootstrap continues has been read through and shellchecked, but never run on a live pod. If it
>   hangs or the `wait` never returns, the pod is still billing — check `/tmp/preload-models.log`
>   over SSH before assuming a stuck bootstrap is something else.

- [ ] **Step 11: Run the full free-gate suite one more time**

Run: `make batch-test && make check-job-types && make check-comfy-nodes && make check-batch-params && make check-vast-models && motions-studio/setup/scrub-secrets.sh --check`
Expected: everything green.

- [ ] **Step 12: Commit**

```bash
git add scripts/drain.py scripts/tests/test_batch_drain.py scripts/pod-bootstrap.sh \
        motions-studio/setup/lib-feature.sh docs/gpu-pod.md
git commit -m "drain+bootstrap: rent with the manifest's real download size, preload its models in parallel"
```
