export function isCameraAwareMotion(params) {
  const value = params?.cameraAwareMotion ?? params?.camera_aware_motion
  return value === true || (typeof value === "string" && ["1", "true", "yes", "on"].includes(
    value.trim().toLowerCase(),
  ))
}

export function enforceMotionFitPolicy(params) {
  if (!params || typeof params !== "object") return params
  const out = { ...params }
  const cameraAware = isCameraAwareMotion(out)
  out.fitDriver = cameraAware
  out.fit_driver = cameraAware
  return out
}
