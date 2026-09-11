# Camera-aware Try-on Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the opt-in `tryon-camera-motion-enhance` pipeline, which prepares a try-on still against the midpoint framing of the selected driver segment and then runs Motion Control with `poseStrength=0.9`, without changing existing pipelines.

**Architecture:** Extend the shared batch registry so a stage alias has an underlying parameter schema, defaults, and contractual locked parameters. Normalize the two camera-stage driver segments once in manifest loading, then use one checked-in camera-composition prompt and strict midpoint extraction in both local and pod try-on paths. Preserve `fitDriver=true` only for motion requests carrying the explicit `cameraAwareMotion=true` flag, and let Telegram continue discovering pipeline choices from the registry.

**Tech Stack:** Python 3 `dataclasses`, `unittest`, `ffprobe`/`ffmpeg`, Express/Node.js ESM, Nuxt-adjacent workflow API, Telegram bot, ComfyUI/Qwen/Gemini image editing.

**Spec:** `docs/superpowers/specs/2026-09-10-camera-aware-tryon-motion-design.md`

## Global Constraints

- Keep `tryon-motion-enhance`, `tryon-character-swap-enhance`, `character-swap-enhance`, and every existing stage default unchanged.
- The new pipeline name is exactly `tryon-camera-motion-enhance` and its stage flow is exactly `camera-tryon -> camera-motion -> enhance`.
- Require exactly the four material roles `character`, `outfit`, `background`, and `driver` for the new pipeline.
- Match only the temporal midpoint of the selected driver segment; do not implement full-video camera tracking.
- Use the driver guide only for aspect ratio, shot size, camera height, camera angle, perspective, horizon, subject scale, and subject placement.
- Preserve the supplied background as a recognizable location with the same principal objects, palette, and lighting; light geometric regeneration is allowed.
- Ship `bodyProportionLock=false`, `poseStrength=0.9`, `clipStrength=1.2`, and `fitDriver=true`; lock `cameraAware=true`, `cameraAwareMotion=true`, and `fitDriver=true` for the corresponding aliases.
- Do not silently fall back to ordinary try-on when guide extraction, camera composition, or image decoding fails.
- Journal keys, filenames, progress text, and Telegram flow text use the actual aliases `camera-tryon` and `camera-motion`.
- Do not start a paid GPU pod for local validation; report paid A/B validation separately.
- Before every commit, run `motions-studio/setup/scrub-secrets.sh --check` and require exit code 0.

---

### Task 1: Stage Aliases, Defaults, and Segment Contract

**Files:**
- Modify: `scripts/batchlib/pipelines.py`
- Modify: `scripts/batchlib/manifest.py`
- Modify: `scripts/batch-params.json`
- Test: `scripts/tests/test_batch_pipelines.py`
- Test: `scripts/tests/test_batch_manifest.py`
- Test: `scripts/tests/test_batch_params.py`

**Interfaces:**
- Produces: `Stage.param_type: str`, `Stage.defaults: dict[str, object]`, and `Stage.locked_params: dict[str, object]`.
- Produces: `effective_stage_params(stage_name: str, manifest_params: dict | None) -> dict` with merge order `defaults -> manifest -> locked_params`.
- Produces in `manifest.py`: `synchronize_camera_stage_segments(pipeline: str, stage_params: dict[str, dict]) -> dict[str, dict]` using canonical camelCase keys and raising `ManifestError` on invalid or conflicting segments.
- Produces: `locked_stage_param_errors(stage_name: str, manifest_params: dict | None) -> list[str]`, so a manifest cannot silently request a value that its alias contract replaces.
- Consumes later: runner, manifest validation, local Phase A, and Telegram use the stage metadata without hard-coding alias behavior.

- [ ] **Step 1: Write failing registry tests**

Add tests that preserve the legacy registry and specify the new aliases:

```python
from batchlib.pipelines import effective_stage_params

def test_camera_pipeline_is_distinct_and_requires_all_four_materials(self):
    self.assertEqual(
        PIPELINES["tryon-camera-motion-enhance"],
        ["camera-tryon", "camera-motion", "enhance"],
    )
    self.assertEqual(
        required_roles("tryon-camera-motion-enhance"),
        {"character", "outfit", "background", "driver"},
    )
    self.assertEqual(optional_roles("tryon-camera-motion-enhance"), set())

def test_camera_aliases_keep_job_types_and_parameter_schemas_separate(self):
    self.assertEqual(STAGES["camera-tryon"].job_type, "tryon")
    self.assertEqual(STAGES["camera-tryon"].param_type, "tryon")
    self.assertEqual(STAGES["camera-motion"].job_type, "motion")
    self.assertEqual(STAGES["camera-motion"].param_type, "motion")

def test_camera_motion_defaults_and_contractual_values(self):
    got = effective_stage_params("camera-motion", {"poseStrength": 0.85,
                                                     "fitDriver": False,
                                                     "cameraAwareMotion": False})
    self.assertEqual(got["poseStrength"], 0.85)
    self.assertEqual(got["clipStrength"], 1.2)
    self.assertFalse(got["bodyProportionLock"])
    self.assertTrue(got["fitDriver"])
    self.assertTrue(got["cameraAwareMotion"])

def test_contractual_values_report_an_explicit_conflict(self):
    errors = locked_stage_param_errors("camera-motion", {"fitDriver": False})
    self.assertEqual(len(errors), 1)
    self.assertIn("fitDriver", errors[0])
    self.assertIn("True", errors[0])

def test_legacy_pipeline_definitions_are_unchanged(self):
    self.assertEqual(PIPELINES["tryon-motion-enhance"], ["tryon", "motion", "enhance"])
    self.assertEqual(required_roles("tryon-motion-enhance"),
                     {"character", "outfit", "driver"})
    self.assertEqual(optional_roles("tryon-motion-enhance"), {"background"})
```

- [ ] **Step 2: Run the registry tests and confirm the missing aliases fail**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_pipelines
```

Expected: FAIL because `tryon-camera-motion-enhance`, stage metadata, and `effective_stage_params` do not exist.

- [ ] **Step 3: Implement the stage metadata and pipeline registry entry**

Extend the frozen stage data model with safe mapping factories and add the aliases:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Stage:
    name: str
    job_type: str
    inputs: dict[str, str]
    output_ext: str
    min_bytes: int
    timeout_min: int
    param_type: str = ""
    defaults: dict[str, object] = field(default_factory=dict)
    locked_params: dict[str, object] = field(default_factory=dict)

def effective_stage_params(stage_name: str, manifest_params: dict | None = None) -> dict:
    stage = STAGES[stage_name]
    return {**stage.defaults, **dict(manifest_params or {}), **stage.locked_params}

def locked_stage_param_errors(stage_name: str, manifest_params: dict | None = None) -> list[str]:
    supplied = dict(manifest_params or {})
    return [f"{stage_name}.{key} is locked to {expected!r}"
            for key, expected in STAGES[stage_name].locked_params.items()
            if key in supplied and supplied[key] != expected]
```

Define `param_type="tryon"`/`"motion"`/`"character-swap"`/`"enhance"` explicitly on all stages. Define the new stages exactly as follows:

```python
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
    defaults={"bodyProportionLock": False, "poseStrength": 0.9,
              "clipStrength": 1.2},
    locked_params={"cameraAwareMotion": True, "fitDriver": True},
),
```

Append the new pipeline without editing the existing entries:

```python
"tryon-camera-motion-enhance": ["camera-tryon", "camera-motion", "enhance"],
```

- [ ] **Step 4: Write failing manifest synchronization tests**

Add a fixture for the new pipeline and tests for copy, preset derivation, and conflicts:

```python
CAMERA = """
runs:
  - id: cameraA
    pipeline: tryon-camera-motion-enhance
    inputs:
      character: char.jpg
      outfit: vay.jpg
      background: bg.jpg
      driver: drv.mp4
    camera-tryon: { provider: gemini, driverStartSec: 5 }
    camera-motion: { preset: drv-15s, quality: 720p }
"""

def test_camera_segment_is_shared_and_duration_derives_from_driver_preset(self):
    with tempfile.TemporaryDirectory() as d:
        run = load_manifest(_fixture(Path(d), CAMERA)).runs[0]
        self.assertEqual(run.stage_params["camera-tryon"]["driverStartSec"], 5)
        self.assertEqual(run.stage_params["camera-motion"]["driverStartSec"], 5)
        self.assertEqual(run.stage_params["camera-tryon"]["driverDurSec"], 15)
        self.assertEqual(run.stage_params["camera-motion"]["driverDurSec"], 15)

def test_conflicting_camera_segments_are_rejected_before_gpu(self):
    text = CAMERA.replace("preset: drv-15s, quality: 720p",
                          "preset: drv-15s, quality: 720p, driverStartSec: 6")
    with tempfile.TemporaryDirectory() as d:
        with self.assertRaises(ManifestError) as cm:
            load_manifest(_fixture(Path(d), text))
        self.assertIn("driverStartSec", str(cm.exception))
        self.assertIn("camera-tryon", str(cm.exception))
        self.assertIn("camera-motion", str(cm.exception))

def test_camera_pipeline_requires_background_and_driver(self):
    text = CAMERA.replace("      background: bg.jpg\n", "")
    with tempfile.TemporaryDirectory() as d:
        errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                 ast_params=AST, curated=CURATED)
        self.assertTrue(any("background" in error for error in errs))
```

- [ ] **Step 5: Run manifest tests and confirm alias validation or synchronization fails**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_manifest
```

Expected: FAIL because alias stages are currently validated as unknown job types and segment values are not synchronized.

- [ ] **Step 6: Implement camera segment synchronization and alias-aware validation**

In `manifest.py`, implement these rules in `synchronize_camera_stage_segments`:

```python
_CAMERA_PIPELINE = "tryon-camera-motion-enhance"
_CAMERA_STAGES = ("camera-tryon", "camera-motion")
_DRV_PRESET = re.compile(r"^drv-(5|10|15|20|30)s$")
```

- Return a copied mapping unchanged for every other pipeline.
- Read both camelCase and snake_case forms of start and duration from both camera stages.
- Convert present values to finite floats; reject negative start and non-positive duration with `ManifestError` naming the run-stage field.
- Treat numerically different values as a conflict and raise `ManifestError` naming both aliases.
- When duration is absent from both stages, derive it from `camera-motion.preset=drv-Ns`.
- Copy each resolved value to canonical `driverStartSec`/`driverDurSec` on both stage dictionaries.
- Remove the consumed `driver_start_sec`/`driver_dur_sec` spellings after canonicalization so alias-schema validation sees one spelling only.
- Do not infer a start; absent start remains absent on both stages.

Call this function from `load_manifest` after defaults/run-level blocks merge and before constructing `Run`. In `validate_manifest`, validate:

```python
stage = STAGES[stage_name]
params = effective_stage_params(stage_name, run.stage_params.get(stage_name))
validate_params(stage.param_type, params, ast_params=ast_params, curated=curated)
```

Before schema validation, append `locked_stage_param_errors(stage_name, run.stage_params.get(stage_name))` with the current run context. Do not replace `stage_params` keys with job types; aliases remain manifest and journal keys.

- [ ] **Step 7: Make the camera parameters valid and keep the curated registry drift-free**

Add the camera fields read by helper functions outside `run_tryon`/`run_motion` to `scripts/batch-params.json`. Put these entries under `tryon.extra`:

```json
"cameraGuideFrame": {
  "why": "camera-aware try-on accepts only the representative middle frame policy"
},
"cameraAware": {
  "why": "camera-aware try-on selects strict guide extraction and three-reference composition"
},
"driverStartSec": {
  "why": "camera guide extraction uses the same selected driver segment as camera-motion"
},
"driverDurSec": {
  "why": "camera guide extraction uses the same selected driver segment as camera-motion"
}
```

Put this entry under `motion.extra`:

```json
"cameraAwareMotion": {
  "why": "camera-aware Motion is the explicit exception that preserves driver fitting for drv-Ns presets"
}
```

Add `cameraGuideFrame: ["middle"]` to `tryon.allowed`. These entries remain in `.extra` because the camera helpers sit outside the `run_<job_type>` function bodies scanned by `extract_from_ast`; do not also add direct `params.get(...)` calls solely to satisfy the AST scanner. Add focused tests proving `validate_manifest` accepts both alias blocks and rejects `cameraGuideFrame: first`.

- [ ] **Step 8: Run task tests and registry gates**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_pipelines scripts.tests.test_batch_manifest scripts.tests.test_batch_params
make check-job-types check-batch-params
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 9: Commit the registry and manifest contract**

```bash
git add scripts/batchlib/pipelines.py scripts/batchlib/manifest.py scripts/batch-params.json \
  scripts/tests/test_batch_pipelines.py scripts/tests/test_batch_manifest.py scripts/tests/test_batch_params.py
git commit -m "Batch: add camera-aware pipeline contract"
```

---

### Task 2: Shared Prompt and Strict Midpoint Extraction

**Files:**
- Create: `motions-studio/worker/assets/camera-aware-tryon.json`
- Modify: `scripts/batchlib/local_tryon.py`
- Modify: `motions-studio/worker/worker_runtime/linux.py`
- Test: `scripts/tests/test_batch_local_tryon.py`
- Create: `motions-studio/worker/tests/test_camera_aware_tryon.py`

**Interfaces:**
- Produces: checked-in JSON keys `version`, `positive`, and `negative`.
- Produces local: `load_camera_compose_prompt() -> tuple[str, str]` and `extract_camera_guide_frame(video_path: Path, out_path: Path, params: dict) -> tuple[int, int]`.
- Produces pod: `_load_camera_compose_prompt() -> tuple[str, str]` and `_extract_camera_guide_frame(job_id: str, video_path: str, out_path: str, params: dict) -> tuple[int, int]`.
- The extraction functions return decoded guide width/height and raise `JobError` locally or `RuntimeError` on the pod for every invalid guide.

- [ ] **Step 1: Write failing local extraction and prompt tests**

Patch `subprocess.run` so no media binary is required:

```python
def test_camera_prompt_is_loaded_from_the_worker_asset(self):
    positive, negative = lt.load_camera_compose_prompt()
    self.assertIn("Image 1", positive)
    self.assertIn("Image 2", positive)
    self.assertIn("Image 3", positive)
    self.assertIn("do not copy", positive.lower())
    self.assertIn("different location", negative.lower())

def test_midpoint_respects_selected_segment(self):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe" and "format=duration" in cmd:
            return mock.Mock(returncode=0, stdout="40.0\n", stderr="")
        if cmd[0] == "ffprobe":
            return mock.Mock(returncode=0, stdout="720x1280\n", stderr="")
        Path(cmd[-1]).write_bytes(b"png")
        return mock.Mock(returncode=0, stdout="", stderr="")
    with tempfile.TemporaryDirectory() as d, mock.patch.object(lt.subprocess, "run", fake_run):
        dims = lt.extract_camera_guide_frame(Path("driver.mp4"), Path(d) / "guide.png",
                                             {"driverStartSec": 10, "driverDurSec": 12})
    ffmpeg = next(cmd for cmd in calls if cmd[0] == "ffmpeg")
    self.assertEqual(ffmpeg[ffmpeg.index("-ss") + 1], "16.000000")
    self.assertEqual(dims, (720, 1280))

def test_empty_or_zero_duration_guide_is_strictly_rejected(self):
    with mock.patch.object(lt.subprocess, "run",
                           return_value=mock.Mock(returncode=0, stdout="0\n", stderr="")):
        with self.assertRaises(JobError):
            lt.extract_camera_guide_frame(Path("driver.mp4"), Path("guide.png"), {})
```

- [ ] **Step 2: Run the local tests and verify missing helpers fail**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_local_tryon
```

Expected: FAIL because the prompt loader and midpoint extractor do not exist.

- [ ] **Step 3: Add the shared camera-composition prompt asset**

Create valid UTF-8 JSON with the following semantic content:

```json
{
  "version": 1,
  "positive": "Create one photorealistic image from three references with strict roles. Image 1 is the only source for the exact person, face, hair, skin tone, body identity, and complete outfit; preserve those attributes. Image 2 is the only source for the location, architecture, furniture, plants, principal objects, palette, and lighting; keep the setting recognizably the same and preserve its principal objects. Image 3 is a camera guide only: match only its aspect ratio, shot size, camera height, camera angle, perspective, horizon, subject scale, and subject placement. Do not copy any person, face, hair, body, clothing, object, text, logo, or location from Image 3. Lightly regenerate the geometry of Image 2 only where necessary for the new viewpoint. Blend Image 1 naturally into Image 2 with coherent perspective, lighting, and contact shadows. Do not stretch the person or background.",
  "negative": "different person, changed face, changed hair, changed body identity, changed outfit, wrong garment, copied person from image 3, copied clothing from image 3, copied objects from image 3, copied text, different location, missing principal objects, changed palette, changed lighting, stretched body, stretched background, pasted cutout, floating person, mismatched perspective, watermark, signature"
}
```

Both runtime loaders must validate `version == 1` and non-empty string values. Resolve local path from `Path(__file__).resolve().parents[2] / "motions-studio/worker/assets/camera-aware-tryon.json"`; resolve pod path from `Path(__file__).resolve().parents[1] / "assets/camera-aware-tryon.json"`. Never embed a second prompt string in Python.

- [ ] **Step 4: Implement strict local midpoint extraction**

Use `ffprobe` to read `format=duration`, calculate:

```python
source_duration = parsed_positive_duration
start = max(0.0, float(params.get("driverStartSec") or 0))
available = source_duration - start
duration = float(params.get("driverDurSec") or available)
effective_duration = min(duration, available)
midpoint = start + effective_duration / 2.0
```

Reject non-finite values, `start >= source_duration`, and `effective_duration <= 0`. Extract one PNG with:

```bash
ffmpeg -nostdin -y -v error -ss <midpoint> -i <video> -frames:v 1 <output.png>
```

Require a non-empty output, then call existing `img_size` and require positive dimensions. Convert subprocess, probe, and decode failures to a `JobError` containing `camera guide` and the source path.

- [ ] **Step 5: Write and implement equivalent pod helper tests**

In `test_camera_aware_tryon.py`, stub `requests` only when unavailable and import `worker_runtime.linux`, matching `test_motion_frame_budget.py`. Assert:

- `driverStartSec=4`, `driverDurSec=10`, and source duration `30` seek to `9.000000`.
- Missing/zero duration raises `RuntimeError` before an image-edit function is called.
- The pod prompt loader returns byte-identical strings to the JSON asset.

Implement `_extract_camera_guide_frame` with the same math and strict failures as the local function, using `api_log` only for the successful resolved midpoint and dimensions. Do not reuse `_cut_motion_driver_segment`, because its documented failure policy falls back to the full source while camera-aware extraction is contractual and must fail.

- [ ] **Step 6: Run extraction tests and static checks**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_local_tryon
python3 -m unittest discover -s motions-studio/worker/tests -p 'test_camera_aware_tryon.py'
python3 -m json.tool motions-studio/worker/assets/camera-aware-tryon.json >/dev/null
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the shared prompt and extractor contract**

```bash
git add motions-studio/worker/assets/camera-aware-tryon.json \
  motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_camera_aware_tryon.py \
  scripts/batchlib/local_tryon.py scripts/tests/test_batch_local_tryon.py
git commit -m "Try-on: add strict camera guide extraction"
```

---

### Task 3: Camera-aware Composition in Local Phase A and Pod Try-on

**Files:**
- Modify: `scripts/batchlib/runner.py`
- Modify: `scripts/batchlib/local_tryon.py`
- Modify: `motions-studio/worker/worker_runtime/linux.py`
- Test: `scripts/tests/test_batch_runner.py`
- Test: `scripts/tests/test_batch_local_tryon.py`
- Test: `motions-studio/worker/tests/test_camera_aware_tryon.py`

**Interfaces:**
- Consumes: `effective_stage_params`, prompt loaders, and midpoint extractors from Tasks 1-2.
- Produces runner: `_local_tryon_stage(run: Run) -> str | None`, returning the first local-eligible stage whose `job_type == "tryon"`.
- Produces local: `_camera_compose_local(provider, edited, background, guide, prompt, keys, out_path) -> Path` using three references in exact order.
- Produces pod: `_tryon_compose_camera(job_id, provider, person_path, background_path, guide_path, prefix, params) -> str` using three references in exact order.

- [ ] **Step 1: Write failing runner alias tests**

Specify local Phase A and file resolution by job type rather than literal stage name:

```python
def test_camera_alias_resolves_the_same_driver_to_guide_and_motion(self):
    run = load_manifest(camera_manifest(tmp)).runs[0]
    guide_files = runner._resolve_files(run, "camera-tryon", None)
    motion_files = runner._resolve_files(run, "camera-motion", Path("prepared.png"))
    self.assertEqual(guide_files["cameraGuide"], run.inputs["driver"])
    self.assertEqual(motion_files["motion"], run.inputs["driver"])
    self.assertEqual(motion_files["ref"], Path("prepared.png"))

def test_camera_tryon_alias_runs_in_local_phase_and_keeps_alias_journal_key(self):
    # Configure provider=gemini, patch run_local_tryon, and execute run_local_phase.
    self.assertIn("camera-tryon", state["runs"]["runA"]["stages"])
    self.assertNotIn("tryon", state["runs"]["runA"]["stages"])
    self.assertTrue((out_dir / "runs/runA/01-camera-tryon.png").is_file())
```

- [ ] **Step 2: Run runner tests and verify the literal `tryon` assumptions fail**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_runner
```

Expected: FAIL because `_local_tryon_eligible`, `needs_pod`, job collection, journal writes, and destination paths currently hard-code `"tryon"`.

- [ ] **Step 3: Generalize local Phase A to stage aliases**

Implement `_local_tryon_stage` by iterating the selected pipeline and checking `STAGES[stage_name].job_type == "tryon"`, then applying the current provider and `cleanOnly` eligibility rules to that alias's effective parameters. Update these call sites together:

- `_local_tryon_eligible` delegates to `_local_tryon_stage`.
- `needs_pod` compares each stage's `job_type`, not its alias text.
- `run_local_phase` collects `(run, stage_name, effective_params)`.
- `_one` receives `stage_name`, calls `stage_dest(..., stage_name)`, and writes/logs `entry["stages"][stage_name]`.
- `run_one` uses `effective_stage_params(stage_name, run.stage_params.get(stage_name))` before submit and journal writes.

Keep every existing ordinary `tryon` test passing, including clean-only deferral to the pod and local provider concurrency.

- [ ] **Step 4: Write failing local three-reference composition tests**

Build the new pipeline run with real temporary character/outfit/background/driver files and patch extraction plus provider calls:

```python
def test_camera_aware_local_pass_sends_person_background_guide_in_that_order(self):
    with mock.patch.object(lt, "extract_camera_guide_frame", return_value=(720, 1280)), \
         mock.patch.object(lt, "_gemini_or_qwen_max") as edit:
        edit.side_effect = write_each_requested_output
        lt.run_local_tryon(run, effective_params, settings, out_path)
    compose = edit.call_args_list[1]
    images = compose.args[2]
    self.assertEqual([data for data, _mime in images],
                     [b"dressed-person", b"supplied-background", b"driver-midpoint"])
    self.assertEqual(compose.args[6], "9:16")

def test_camera_compose_failure_does_not_publish_the_garment_pass(self):
    # First edit writes the dressed person; second edit raises JobError.
    with self.assertRaises(JobError):
        lt.run_local_tryon(run, effective_params, settings, out_path)
    self.assertFalse(out_path.exists())
```

Also assert that ordinary `tryon` still sends two references in pass 1 and two in the legacy background pass, never reads `driver`, and keeps its old output aspect ratio.

- [ ] **Step 5: Implement strict local camera composition**

In `run_local_tryon`:

- Read `camera_aware = bool(params.get("cameraAware"))` with the repository's existing string-bool convention.
- If false, preserve the current code path.
- If true, require both `background` and `driver`; extract the guide before submitting the garment pass so an invalid driver cannot spend the first image call.
- Perform the garment pass unchanged.
- Replace the legacy two-reference background pass only in camera-aware mode with `(dressed person, supplied background, guide frame)`.
- Use `gemini_aspect(guide_dims)` for Gemini output.
- For Qwen-Max, supply the same three references and prompt; it already accepts at most three.
- Require the returned image to be decodable via `img_size` and postprocess only that prepared image.
- Write `out_path` only after the complete strict path succeeds.

- [ ] **Step 6: Write failing pod composition tests**

Add focused tests around a small helper rather than invoking a real ComfyUI/API backend. Patch `_gemini_edit`, `_qwen_max_edit`, `comfy_submit`, and `_hf_call` per provider, and assert the helper always sends:

```text
reference 1 = dressed person
reference 2 = supplied background
reference 3 = extracted driver midpoint
```

Assert target aspect/dimensions come from the guide. Assert an empty provider output raises and never returns `person_path`. Include a legacy `_tryon_compose_background` assertion so the ordinary path remains two-reference and unchanged.

- [ ] **Step 7: Wire the pod `run_tryon` path**

At the start of `run_tryon`, read `cameraAware`, require `background` and `cameraGuide`, download the guide video, and extract its midpoint before the garment provider call. After each provider-specific garment pass:

- camera-aware: call `_tryon_compose_camera` exactly once;
- ordinary with background: keep the existing legacy background branch;
- ordinary without background: keep the existing direct postprocess branch.

For self-hosted Qwen, pass three uploaded images to `build_qwen_create_workflow` with the shared prompt, `realism=False`, `width`/`height` from `_fit_aligned(guide_width, guide_height, mp=TRYON_MP, align=64)`, and `force_size=True`. After every provider, use one shared postprocess helper that center-crops or letterboxes to the resolved guide aspect ratio without stretching. For Gemini, Qwen-Max, and HF, use their existing multi-reference API and request the closest supported guide aspect ratio. Any image decode failure raises before `api_upload_output`.

- [ ] **Step 8: Run all image-path tests**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_runner scripts.tests.test_batch_local_tryon
python3 -m unittest discover -s motions-studio/worker/tests -p 'test_camera_aware_tryon.py'
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: all commands exit 0 and no network or GPU is used.

- [ ] **Step 9: Commit camera-aware composition**

```bash
git add scripts/batchlib/runner.py scripts/batchlib/local_tryon.py \
  scripts/tests/test_batch_runner.py scripts/tests/test_batch_local_tryon.py \
  motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_camera_aware_tryon.py
git commit -m "Try-on: compose against driver midpoint framing"
```

---

### Task 4: Preserve Driver Fitting Only for Camera-aware Motion

**Files:**
- Create: `motions-studio/api/src/motion-camera-policy.js`
- Create: `motions-studio/api/src/test-camera-aware-motion.mjs`
- Modify: `motions-studio/api/src/routes/jobs.js`
- Modify: `motions-studio/api/src/wf-worker/handlers.js`
- Modify: `motions-studio/api/src/motion-resolution.js`
- Modify: `motions-studio/worker/worker_runtime/linux.py`
- Test: `motions-studio/worker/tests/test_camera_aware_tryon.py`

**Interfaces:**
- Produces API: `isCameraAwareMotion(params: object) -> boolean` accepting boolean `true` and string forms `1|true|yes|on`.
- Produces API: `enforceMotionFitPolicy(params: object) -> object`, shared by direct jobs, workflow jobs, and resolution normalization while leaving their other existing rules in place.
- Consumes worker: `cameraAwareMotion` is the sole exception to the ordinary `drv-*` rule that forces driver fitting off.

- [ ] **Step 1: Write the failing API normalization assertions**

Create an ESM assertion script:

```javascript
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { enforceMotionFitPolicy, isCameraAwareMotion } from "./motion-camera-policy.js"
import { enforceMotionResolution } from "./motion-resolution.js"

const camera = enforceMotionResolution("motion", enforceMotionFitPolicy({
  preset: "drv-15s", quality: "720p", cameraAwareMotion: true,
  bodyProportionLock: false, poseStrength: 0.9, clipStrength: 1.2, fitDriver: true,
}))
assert.equal(camera.cameraAwareMotion, true)
assert.equal(camera.bodyProportionLock, false)
assert.equal(camera.poseStrength, 0.9)
assert.equal(camera.clipStrength, 1.2)
assert.equal(camera.fitDriver, true)
assert.equal(camera.fit_driver, true)

const ordinary = enforceMotionResolution("motion", enforceMotionFitPolicy({
  preset: "drv-15s", quality: "720p", fitDriver: true,
}))
assert.equal(ordinary.fitDriver, false)
assert.equal(ordinary.fit_driver, false)

for (const file of ["routes/jobs.js", "wf-worker/handlers.js"]) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8")
  assert.match(source, /enforceMotionFitPolicy/, `${file} must apply the shared fit policy`)
}
```

Add string-boolean assertions for `"true"` and `"0"`, plus `None` and primitive-input assertions proving the helper returns unsupported inputs unchanged.

- [ ] **Step 2: Run the API assertion script and confirm the missing shared module fails**

Run:

```bash
node motions-studio/api/src/test-camera-aware-motion.mjs
```

Expected: FAIL because `motion-camera-policy.js` does not exist and current resolution normalization forces `fitDriver=false`.

- [ ] **Step 3: Add a shared fit policy and preserve both existing normalizers**

Create a small policy module; do not move the two existing `normalizeMotionDriverSegment` functions because their surrounding legacy normalization is not identical:

```javascript
export function isCameraAwareMotion(params) {
  return ["1", "true", "yes", "on"].includes(
    String(params?.cameraAwareMotion ?? params?.camera_aware_motion ?? "").trim().toLowerCase(),
  )
}

export function enforceMotionFitPolicy(params) {
  if (!params || typeof params !== "object") return params
  const out = { ...params }
  const cameraAware = isCameraAwareMotion(out)
  out.fitDriver = cameraAware
  out.fit_driver = cameraAware
  return out
}
```

Import `enforceMotionFitPolicy` in `routes/jobs.js` and `wf-worker/handlers.js`. In each existing `normalizeMotionDriverSegment`, replace only the initial `{ ...params }` plus unconditional `fitDriver=false` assignments with `let out = enforceMotionFitPolicy(params)`; leave every other current assignment, deletion, and segment rule in that file untouched. In `enforceMotionResolution`, return `enforceMotionFitPolicy(out)` after calculating width/height/quality rather than unconditionally setting fitting false. This must not broaden the exception to `fitDriver=true` alone.

- [ ] **Step 4: Write failing worker normalization assertions**

Import `worker_runtime.linux` with the same optional `requests` stub as the existing worker tests and test `_normalize_motion_params` directly:

```python
camera = normalize({"preset": "drv-15s", "cameraAwareMotion": True,
                    "bodyProportionLock": False, "poseStrength": 0.9,
                    "clipStrength": 1.2, "fitDriver": True})
self.assertTrue(camera["fitDriver"])
self.assertTrue(camera["fit_driver"])
self.assertFalse(camera["bodyProportionLock"])
self.assertEqual(camera["poseStrength"], 0.9)
self.assertEqual(camera["clipStrength"], 1.2)

ordinary = normalize({"preset": "drv-15s", "fitDriver": True})
self.assertFalse(ordinary["fitDriver"])
self.assertFalse(ordinary["fit_driver"])
```

- [ ] **Step 5: Update worker normalization with the same explicit flag rule**

Read `cameraAwareMotion`/`camera_aware_motion` through `_motion_bool`. In the `preset.startswith("drv-")` block, continue forcing fitting false when neither `_swapEngine` nor camera-aware motion is active. When camera-aware motion is active, explicitly restore both camelCase and snake_case forms to true after preset normalization. Keep Character Swap's `_swapEngine` behavior unchanged.

Ensure `_normalize_motion_params` retains `bodyProportionLock=false`, so its current cap/min block does not change `0.9/1.2`.

- [ ] **Step 6: Run API and worker normalization tests**

Run:

```bash
node motions-studio/api/src/test-camera-aware-motion.mjs
python3 -m unittest discover -s motions-studio/worker/tests -p 'test_camera_aware_tryon.py'
make check-job-types check-batch-params
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: all commands exit 0; ordinary Motion and Character Swap assertions remain unchanged.

- [ ] **Step 7: Commit the motion normalization exception**

```bash
git add motions-studio/api/src/motion-camera-policy.js \
  motions-studio/api/src/test-camera-aware-motion.mjs \
  motions-studio/api/src/routes/jobs.js motions-studio/api/src/wf-worker/handlers.js \
  motions-studio/api/src/motion-resolution.js \
  motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_camera_aware_tryon.py
git commit -m "Motion: preserve camera-aware driver framing"
```

---

### Task 5: Telegram Pipeline Selection and Manifest Routing

**Files:**
- Modify: `scripts/tgbot/job.py`
- Test: `scripts/tests/test_batch_bot.py`
- Test: `scripts/tests/test_batch_job.py`

**Interfaces:**
- Consumes: the new `PIPELINES`/`STAGES` registry entry.
- Produces: `_driver_stage(pipeline: str) -> str | None` preferring driver-consuming stages with `job_type in {"motion", "character-swap"}`.
- Telegram remains registry-driven; no new command handler or hard-coded pipeline button is introduced.

- [ ] **Step 1: Write failing pure manifest-routing tests**

Add to `test_batch_job.py`:

```python
def test_camera_pipeline_routes_driver_preset_to_camera_motion(self):
    self.assertEqual(_driver_stage("tryon-camera-motion-enhance"), "camera-motion")
    text = render_manifest([camera_job], now="2026-09-11 10:00:00")
    self.assertIn("    camera-motion: { preset: drv-15s }", text)
    self.assertNotIn("    camera-tryon: { preset:", text)

def test_existing_driver_stage_routing_is_unchanged(self):
    self.assertEqual(_driver_stage("tryon-motion-enhance"), "motion")
    self.assertEqual(_driver_stage("tryon-character-swap-enhance"), "character-swap")
```

- [ ] **Step 2: Write failing bot behavior tests**

Add tests that use the real registry and existing fake Telegram harness:

```python
def test_camera_pipeline_is_offered_and_shows_its_alias_flow(self):
    with mock.patch.object(bot, "_maybe_show_manifest"):
        bot.handle(tg, cmd_from(ME, "/pipeline tryon-camera-motion-enhance"),
                   allowed_user_id=ME)
    chooser = FakeTg()
    bot.handle(chooser, cmd_from(ME, "/pipeline"), allowed_user_id=ME)
    self.assertIn("tryon-camera-motion-enhance", chooser.messages[0])
    self.assertIn("camera-tryon", chooser.messages[0])
    self.assertIn("camera-motion", chooser.messages[0])
    offered = {data for data in chooser.callback_data() if data.startswith(bot._CB_PIPE)}
    self.assertNotIn(bot._CB_PIPE + "tryon-camera-motion-enhance", offered)

def test_switching_to_camera_pipeline_keeps_all_compatible_slots(self):
    job = bot._job_for(ME)
    job.slots.update({"character": Path("c.png"), "outfit": Path("o.png"),
                      "background": Path("b.png"), "driver": Path("d.mp4")})
    bot.handle(tg, cmd_from(ME, "/pipeline tryon-camera-motion-enhance"), allowed_user_id=ME)
    self.assertEqual(set(bot._job_for(ME).slots),
                     {"character", "outfit", "background", "driver"})
    self.assertFalse(missing_slots(bot._job_for(ME)))

def test_camera_pipeline_draft_round_trip_preserves_name_and_slots(self):
    job = bot._job_for(ME)
    job.pipeline = "tryon-camera-motion-enhance"
    job.slots.update({"character": Path("/s/c.png"), "outfit": Path("/s/o.png"),
                      "background": Path("/s/b.png"), "driver": Path("/s/d.mp4")})
    job.probes["driver"] = self.driver
    bot._save_draft(ME)
    self._restart()
    bot._load_draft(ME)
    restored = bot._STATE[ME]
    self.assertEqual(restored.pipeline, "tryon-camera-motion-enhance")
    self.assertEqual(set(restored.slots), {"character", "outfit", "background", "driver"})
```

Place the persistence test in `TestDraftPersistence`, where `self.driver` and `_restart()` are already defined. Import `_driver_stage` in `test_batch_job.py` and `missing_slots` where each test module needs them.

- [ ] **Step 3: Run Telegram and job tests and confirm preset routing fails**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_job scripts.tests.test_batch_bot
```

Expected: the registry-driven picker tests may already pass after Task 1, but `_driver_stage` chooses `camera-tryon` and the manifest-routing test fails.

- [ ] **Step 4: Fix `_driver_stage` preference without pipeline-name conditions**

Implement two passes over the selected pipeline:

```python
driver_consumers = [
    stage_name for stage_name in PIPELINES.get(pipeline, [])
    if any("material:driver" in source.split("|")
           for source in STAGES[stage_name].inputs.values())
]
for stage_name in driver_consumers:
    if STAGES[stage_name].job_type in {"motion", "character-swap"}:
        return stage_name
return driver_consumers[0] if driver_consumers else None
```

This routes the duration preset to `camera-motion` while retaining the generic fallback for future image-only driver consumers. Do not modify the default pipeline constant or `TG_PIPELINE` behavior.

- [ ] **Step 5: Run Telegram tests and secret checks**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_job scripts.tests.test_batch_bot
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: all commands exit 0, `/pipeline` exposes the new option, background is required, all compatible slots survive switching, and the alias flow appears in confirmation text.

- [ ] **Step 6: Commit Telegram support**

```bash
git add scripts/tgbot/job.py scripts/tests/test_batch_job.py scripts/tests/test_batch_bot.py
git commit -m "Telegram bot: offer camera-aware try-on pipeline"
```

---

### Task 6: User Documentation, Example, and Full Local Gates

**Files:**
- Modify: `docs/batch-runner.md`
- Modify: `scripts/vps/README.md`
- Modify: `batch/example.yaml`
- Verify: all files changed by Tasks 1-5

**Interfaces:**
- Documents: exact pipeline name, required materials, stage aliases, representative-frame limitation, strict failure policy, and extra image-generation cost.
- Produces: a copyable example manifest whose parameters pass the real registry validator.

- [ ] **Step 1: Update the batch runner guide**

Add a row to the pipeline table:

```text
tryon-camera-motion-enhance | camera-tryon -> camera-motion -> enhance | character, outfit, background, driver | none
```

Add a concise subsection explaining:

- `camera-tryon` performs the ordinary garment edit, then a second billable image-edit call.
- It takes only the midpoint of the selected driver segment as a framing guide.
- It preserves the supplied scene but may regenerate background geometry lightly for camera angle/perspective.
- It does not reproduce full camera movement and does not use Character Swap's replacement mask.
- Missing/unreadable driver, missing background, or failed composition stops the run before Motion Control.
- Existing `tryon-motion-enhance` behavior is unchanged.

- [ ] **Step 2: Add the validated example manifest**

Append a third illustrative run to the existing `runs` list in `batch/example.yaml`:

```yaml
  - id: model-a__outfit-a__camera-motion
    pipeline: tryon-camera-motion-enhance
    inputs:
      character:  .smoke/model-a.jpg
      outfit:     .smoke/outfit-a.jpg
      background: .smoke/scene-a.jpg
      driver:     .smoke/driver-a.mp4
    camera-tryon:
      provider: gemini
      driverStartSec: 0
    camera-motion:
      preset: drv-15s
      quality: 720p
```

The derived `driverDurSec=15` must reach both camera stages. Do not write `cameraAware`, `cameraAwareMotion`, or `fitDriver` in the example because the stage registry locks them.

- [ ] **Step 3: Update the Telegram/VPS guide**

Document both selection paths:

```text
/pipeline
/pipeline tryon-camera-motion-enhance
```

State that the bot requests all four slots, stores the chosen pipeline in its draft, reuses the same driver for guide and motion, and routes its `drv-Ns` preset to `camera-motion`. State that the default stays unchanged unless `TG_PIPELINE=tryon-camera-motion-enhance` is explicitly configured.

- [ ] **Step 4: Run focused suites**

Run:

```bash
python3 -m unittest scripts.tests.test_batch_pipelines
python3 -m unittest scripts.tests.test_batch_manifest
python3 -m unittest scripts.tests.test_batch_params
python3 -m unittest scripts.tests.test_batch_runner
python3 -m unittest scripts.tests.test_batch_local_tryon
python3 -m unittest scripts.tests.test_batch_job
python3 -m unittest scripts.tests.test_batch_bot
python3 -m unittest discover -s motions-studio/worker/tests -p 'test_camera_aware_tryon.py'
node motions-studio/api/src/test-camera-aware-motion.mjs
```

Expected: every suite exits 0 without network access, a GPU, or a paid pod.

- [ ] **Step 5: Run full repository gates**

Run:

```bash
make batch-test
make check-job-types check-comfy-nodes check-batch-params
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected: every command exits 0. If a gate depends on unavailable external infrastructure, record the exact command and error separately; do not describe that as a regression pass.

- [ ] **Step 6: Review the frozen artifact contract**

Using one local manifest load plus mocked submissions, confirm these observable values:

```text
pipeline: tryon-camera-motion-enhance
required roles: character, outfit, background, driver
01-camera-tryon.png
02-camera-motion.mp4
03-enhance.mp4
camera-tryon inputs: model, product, background, cameraGuide
camera-motion inputs: ref, motion
camera-motion stored params: cameraAwareMotion=true, bodyProportionLock=false,
                             poseStrength=0.9, clipStrength=1.2, fitDriver=true
```

Also confirm an ordinary `tryon-motion-enhance` mocked run retains its existing fields, optional background, output names, and `fitDriver=false` motion policy.

- [ ] **Step 7: Commit documentation and the example**

```bash
git add docs/batch-runner.md scripts/vps/README.md batch/example.yaml
git commit -m "Docs: explain camera-aware try-on motion"
```

- [ ] **Step 8: Report paid validation as a separate status**

In the handoff or PR description, state exactly one of:

```text
GPU A/B smoke: not run; local implementation and contract gates only. No paid pod was started.
```

or, only after an explicitly authorized paid run:

```text
GPU A/B smoke: run with identical character/outfit/background/driver and seed against tryon-motion-enhance; include artifact paths plus observations for midpoint framing, background recognizability, body shape, face/outfit identity, and stretching.
```

Do not claim visual-quality improvement from local mocked tests alone.
