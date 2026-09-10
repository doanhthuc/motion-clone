# Camera-aware Try-on Motion Pipeline

Date: 2026-09-10  
Status: approved in chat; pending written-spec review

## 1. Goal

Add a new batch pipeline that keeps the existing try-on background while preparing the generated image to match the driver video's camera framing before Motion Control runs. The new pipeline must make the animated subject follow the driver's body pose more closely without changing the behavior of `tryon-motion-enhance`.

The new pipeline is named:

```text
tryon-camera-motion-enhance
```

It uses the middle frame of the selected driver segment as a camera guide. The background may be regenerated lightly to match that frame's perspective, camera height, shot size, aspect ratio, and subject placement, but it must remain recognizably the same location with the same principal objects, colors, and lighting.

## 2. User-visible behavior

The new pipeline requires four material roles:

- `character`: the person to dress and animate.
- `outfit`: the garment or outfit used by try-on.
- `background`: the scene that must remain recognizable in the prepared image and final motion video.
- `driver`: the source of camera framing and body motion.

The pipeline runs these stages:

```text
camera-tryon -> camera-motion -> enhance
```

It appears as a selectable option in the Telegram bot's `/pipeline` panel and can also be selected with:

```text
/pipeline tryon-camera-motion-enhance
```

The bot continues to discover choices from the shared pipeline registry. No pipeline-specific Telegram command is added.

## 3. Compatibility boundary

The existing pipelines and their defaults remain unchanged, especially:

- `tryon-motion-enhance`
- `tryon-character-swap-enhance`
- `character-swap-enhance`

The existing `tryon` stage does not begin consuming the driver implicitly. The new behavior is exposed through distinct stage names so manifests, journals, output filenames, progress displays, resume behavior, and Telegram panels identify which path actually ran.

The worker may reuse the existing `tryon` and `motion` job types, but camera-aware behavior must be enabled only by explicit parameters supplied by the new stages. Requests from existing stages must follow their current code paths and defaults byte-for-byte where practical.

## 4. Pipeline and stage definitions

Add two batch stages to the shared stage registry.

### `camera-tryon`

- Job type: `tryon`
- Inputs:
  - `model` from `material:character`
  - `product` from `material:outfit`
  - `background` from `material:background`
  - `cameraGuide` from `material:driver`
- Output: PNG
- Default parameters injected by the stage:
  - `cameraAware: true`
  - `cameraGuideFrame: "middle"`

### `camera-motion`

- Job type: `motion`
- Inputs:
  - `ref` from the previous `camera-tryon` output
  - `motion` from `material:driver`
- Output: MP4
- Default parameters injected by the stage:
  - `cameraAwareMotion: true`
  - `bodyProportionLock: false`
  - `poseStrength: 0.9`
  - `clipStrength: 1.2`
  - `fitDriver: true`

Stage defaults are merged before manifest-level parameters so an explicit pipeline configuration can override tuning values such as `poseStrength` and `clipStrength`. The stage then injects the contractual flags `cameraAware=true` and `cameraAwareMotion=true`; manifests cannot disable them while claiming to run this pipeline. `fitDriver=true` is also contractual because the prepared image and Motion render must use the same driver aspect ratio. `poseStrength: 0.9` is the shipped default and must be covered by tests. The API currently forces `fitDriver=false` for every `motion` job; it must preserve `fitDriver=true` only when `cameraAwareMotion=true`. Normal motion jobs remain forced to their existing framing policy.

The pipeline registry entry is:

```text
tryon-camera-motion-enhance = camera-tryon -> camera-motion -> enhance
```

## 5. Camera-aware image generation

Camera alignment belongs in the image-generation path, before Motion Control. It is a separate pass after garment try-on, preserving the current separation between garment editing and background composition.

### 5.1 Driver frame extraction

The try-on worker downloads `cameraGuide` only when `cameraAware` is explicitly enabled. It applies the same requested `driverStartSec` and `driverDurSec` segment boundaries used for the later motion stage when those parameters are provided, then extracts the temporal midpoint of that segment with `ffmpeg`.

The extracted guide is an image input, not a source of identity, clothing, or scenery. The worker probes the segment duration before invoking the image model. An unreadable, empty, or zero-duration guide fails the try-on job before the video-generation stage begins.

Local API-based try-on follows the same rule. The local runner extracts the midpoint frame and includes it in the camera-compose request, so Gemini/Qwen local Phase A and pod execution have equivalent semantics.

### 5.2 Two-pass image flow

The camera-aware stage performs:

1. Garment pass: `character + outfit -> dressed person`, using the current provider-specific try-on implementation unchanged.
2. Camera-compose pass: `dressed person + background + driver midpoint -> prepared reference`.

The camera-compose prompt assigns one role to each image:

- Image 1 supplies the exact person, face, hair, body identity, and outfit.
- Image 2 supplies the location, architecture, furniture, plants, principal objects, palette, and lighting.
- Image 3 supplies only aspect ratio, shot size, camera height, camera angle, perspective, horizon, subject scale, and subject placement.

The prompt explicitly forbids copying the person, clothing, objects, text, or location from image 3. It permits light geometric regeneration of image 2 where required by the new viewpoint. It requires the result to remain recognizably the same setting and preserve the principal objects and lighting of image 2.

The output aspect ratio follows the driver guide rather than the original character image. Provider-specific image-size constraints may round to the nearest supported ratio, but the final postprocess must crop or pad without stretching.

### 5.3 Failure policy

Camera-aware execution is strict:

- Missing `background` or `cameraGuide`: reject during manifest validation when running through batch or Telegram.
- Driver frame extraction failure: fail `camera-tryon` with a clear error.
- Camera-compose provider failure: fail `camera-tryon`; do not return the ordinary try-on image and do not silently continue to Motion Control.
- Model output with no decodable image: fail the stage.

This prevents a successful-looking run from quietly using the old camera geometry.

## 6. Camera-aware Motion Control

The second stage reuses the current Wan Motion Control implementation with an explicit camera-aware flag. Its shipped defaults are:

```json
{
  "cameraAwareMotion": true,
  "bodyProportionLock": false,
  "poseStrength": 0.9,
  "clipStrength": 1.2,
  "fitDriver": true
}
```

Disabling `bodyProportionLock` stops normalization from capping pose conditioning at `0.7` and raising CLIP conditioning to at least `1.35`. A pose strength of `0.9` is intentionally below Character Swap's `1.0`: it should follow the driver's joint spacing more closely while leaving a small margin against fast-motion blur and hand artifacts.

`fitDriver=true` preserves the driver aspect ratio for this pipeline. The API and worker must recognize the explicit camera-aware flag before applying the ordinary Motion Control rule that forces driver fitting off for `drv-*` presets. Existing motion requests retain that rule.

This pipeline does not add Character Swap's SAM3 replacement mask or preserve the driver's background. It should narrow the body/framing difference, not claim pixel-equivalent Character Swap behavior.

## 7. Telegram behavior

The Telegram bot reads pipeline names and stage sequences from `batchlib.pipelines.PIPELINES`. Adding the registry entry makes the new choice appear in `/pipeline` without a hard-coded button.

For the new pipeline:

- `background` is required, so the bot must request it rather than label it optional.
- `driver` remains a required material and is reused by both `camera-tryon` and `camera-motion`.
- Switching from another four-material pipeline keeps compatible uploaded slots.
- Draft persistence stores the new pipeline name through restarts.
- The confirmation panel shows `camera-tryon -> camera-motion -> enhance` so users can distinguish it from the legacy flow.

The default Telegram pipeline remains unchanged unless `TG_PIPELINE` is explicitly configured to the new name.

## 8. Parameter routing

Because stage names no longer equal their underlying job types, parameter validation and lookup must understand aliases:

- `camera-tryon` uses the `tryon` parameter schema plus its camera-aware parameters.
- `camera-motion` uses the `motion` parameter schema plus its camera-aware flag.

Manifest blocks use the stage names that appear in the pipeline:

```yaml
camera-tryon:
  provider: gemini
  driverStartSec: 0
  driverDurSec: 15

camera-motion:
  preset: drv-15s
  quality: 720p
```

The same segment values must reach both stages. If the user supplies segment values to only one stage, manifest normalization copies them to the other camera-aware stage. Conflicting values are rejected rather than allowing the generated camera guide and animated segment to refer to different parts of the video.

## 9. Example manifest

```yaml
defaults:
  camera-tryon:
    provider: gemini
  camera-motion:
    preset: drv-15s
    quality: 720p
  enhance:
    engine: lanczos
    targetRes: 1080p
    fpsInterp: ""

runs:
  - id: model-a__outfit-a__camera-motion
    pipeline: tryon-camera-motion-enhance
    inputs:
      character:  ~/Desktop/materials/characters/model-a.jpg
      outfit:     ~/Desktop/materials/outfits/outfit-a.jpg
      background: ~/Desktop/materials/backgrounds/scene-a.jpg
      driver:     ~/Desktop/materials/drivers/driver-a.mp4
```

## 10. Testing and validation

Local automated coverage must verify:

1. The new pipeline has the expected stages and four required roles.
2. Existing pipeline definitions and required/optional roles remain unchanged.
3. Stage file resolution sends the same driver to `cameraGuide` and `motion`.
4. The midpoint extraction calculation respects a selected driver segment.
5. Camera-aware try-on sends three images in the documented role order and uses the driver aspect ratio.
6. Missing or unreadable camera guide fails before submitting Motion Control.
7. Camera-compose failure is not silently replaced with an ordinary try-on output.
8. Camera-aware motion stores `cameraAwareMotion=true`, `bodyProportionLock=false`, `poseStrength=0.9`, `clipStrength=1.2`, and `fitDriver=true` after API normalization.
9. Ordinary motion normalization still forces its current framing defaults.
10. Telegram `/pipeline` offers the new name, preserves compatible slots when switching, persists it, and renders the new stage flow.
11. Local Gemini/Qwen Phase A and pod try-on construct equivalent camera-compose prompts.

Run the relevant local suites and static gates:

```text
python3 -m unittest scripts.tests.test_batch_pipelines
python3 -m unittest scripts.tests.test_batch_manifest
python3 -m unittest scripts.tests.test_batch_runner
python3 -m unittest scripts.tests.test_batch_local_tryon
python3 -m unittest scripts.tests.test_batch_bot
make batch-test
make check-job-types check-comfy-nodes check-batch-params
motions-studio/setup/scrub-secrets.sh --check
```

Paid GPU validation is separate from local completion. One short smoke run should compare the new pipeline against `tryon-motion-enhance` with identical materials and seed, checking:

- the prepared still matches the driver midpoint framing;
- the background remains recognizably the supplied scene;
- the video's subject shape follows the driver more closely;
- face and garment identity remain acceptable;
- no unexpected stretch occurs at the final resolution.

The PR must state clearly whether this paid smoke run was performed.

## 11. Cost and operational impact

The pipeline adds one image-edit request after ordinary try-on. For API providers this adds one billable image generation call. For self-hosted Qwen it adds one ComfyUI image pass before Wan video generation. Midpoint extraction uses local `ffmpeg` and has negligible cost.

No additional paid GPU pod should be started solely for local tests. The existing pod preflight and smoke procedure remains the gate for end-to-end validation.

## 12. Documentation updates

Update the batch runner guide and Telegram/VPS guide to list the new pipeline, its four required inputs, camera-aware behavior, additional image-generation cost, and strict failure policy. Include an example manifest and explain that it matches only a representative frame, not the driver's full camera movement.
