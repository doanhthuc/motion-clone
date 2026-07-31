// #region ALD 24/07/2026 - Chốt hậu kỳ video Task Cloud ở tầng backend.
// Workflow cũ hoặc frontend chưa refresh vẫn có thể gửi FlashVSR. Mọi đường tạo job
// đều phải chuẩn hóa engine = Lanczos + FPS gốc cho video Task Cloud.
// ALD 25/07/2026 - Nấc chất lượng do user chọn (Gốc 540p / 720p / 1080p): giữ đúng targetRes
// khách yêu cầu trong danh sách cho phép thay vì ép cứng 1080p; ngoài danh sách → mặc định 1080p.
const TASK_CLOUD_ENHANCE_RES = new Set(["540p", "720p", "1080p"])
const RES_LABEL = { "540p": "540p", "720p": "HD 720p", "1080p": "Full HD" }
export function enforceTaskCloudEnhancePolicy(type, params) {
  if (type !== "enhance" || !params || typeof params !== "object") return params
  if (String(params._gen || "").trim().toLowerCase() !== "task-cloud") return params

  const mode = String(params.mode || "video").trim().toLowerCase()
  if (["image", "img", "photo", "picture"].includes(mode)) return params

  const requested = String(params.targetRes || params.target_res || "1080p").trim().toLowerCase()
  const targetRes = TASK_CLOUD_ENHANCE_RES.has(requested) ? requested : "1080p"

  const out = {
    ...params,
    label: `Lanczos ${RES_LABEL[targetRes]} · FPS gốc`,
    mode: "video",
    targetRes,
    fpsInterp: "",
    engine: "lanczos",
    allowFallback: false,
    faceRestore: false,
  }

  // Xóa alias cũ để worker luôn đọc các khóa chuẩn ở trên.
  delete out.target_res
  delete out.fps_interp
  delete out.fpsTarget
  delete out.allow_fallback
  delete out.face_restore
  delete out.casStrength
  delete out.cas_strength
  return out
}
// #endregion
