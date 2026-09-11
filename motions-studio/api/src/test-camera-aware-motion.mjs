import assert from "node:assert/strict"
import { enforceMotionFitPolicy, isCameraAwareMotion } from "./motion-camera-policy.js"
import { enforceMotionResolution } from "./motion-resolution.js"
import { normalizeMotionDriverSegment as normalizeDirectMotion } from "./routes/jobs.js"
import { normalizeMotionDriverSegment as normalizeWorkflowMotion } from "./wf-worker/handlers.js"

assert.equal(isCameraAwareMotion({ cameraAwareMotion: true }), true)
assert.equal(isCameraAwareMotion({ cameraAwareMotion: "true" }), true)
assert.equal(isCameraAwareMotion({ camera_aware_motion: "yes" }), true)
assert.equal(isCameraAwareMotion({ cameraAwareMotion: "0" }), false)
assert.equal(isCameraAwareMotion({}), false)

for (const unsupported of [null, undefined, 0, "motion", true]) {
  assert.equal(enforceMotionFitPolicy(unsupported), unsupported)
}

for (const [name, normalize] of [
  ["direct jobs", normalizeDirectMotion],
  ["workflow jobs", normalizeWorkflowMotion],
]) {
  const camera = enforceMotionResolution("motion", normalize("motion", {
    preset: "drv-15s", quality: "720p", cameraAwareMotion: true,
    bodyProportionLock: false, poseStrength: 0.9, clipStrength: 1.2, fitDriver: true,
  }))
  assert.equal(camera.cameraAwareMotion, true, `${name} preserves the explicit camera flag`)
  assert.equal(camera.bodyProportionLock, false, `${name} preserves camera body proportions`)
  assert.equal(camera.poseStrength, 0.9, `${name} preserves camera pose strength`)
  assert.equal(camera.clipStrength, 1.2, `${name} preserves camera clip strength`)
  assert.equal(camera.fitDriver, true, `${name} preserves camera driver fitting`)
  assert.equal(camera.fit_driver, true, `${name} preserves snake-case camera driver fitting`)

  const ordinary = enforceMotionResolution("motion", normalize("motion", {
    preset: "drv-15s", quality: "720p", fitDriver: true,
  }))
  assert.equal(ordinary.fitDriver, false, `${name} disables ordinary driver fitting`)
  assert.equal(ordinary.fit_driver, false, `${name} disables ordinary snake-case driver fitting`)

  const characterSwap = { preset: "drv-15s", fitDriver: true }
  assert.equal(normalize("character-swap", characterSwap), characterSwap, `${name} leaves Character Swap untouched`)
}

const directCamera = enforceMotionResolution("motion", enforceMotionFitPolicy({
  preset: "drv-15s", quality: "720p", cameraAwareMotion: true, fitDriver: true,
}))
assert.equal(directCamera.fitDriver, true)
assert.equal(directCamera.fit_driver, true)

const directOrdinary = enforceMotionResolution("motion", enforceMotionFitPolicy({
  preset: "drv-15s", quality: "720p", fitDriver: true,
}))
assert.equal(directOrdinary.fitDriver, false)
assert.equal(directOrdinary.fit_driver, false)
