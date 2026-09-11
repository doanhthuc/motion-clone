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
