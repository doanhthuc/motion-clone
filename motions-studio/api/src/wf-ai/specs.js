// #region ALD 09/07/2026 - wf-ai/specs.js: NODE_SPECS — bộ node AI đạo diễn ĐƯỢC PHÉP sinh (curated).
// Tách từ routes/workflow-ai.js cũ + BỔ SUNG 2 node: reveal (đè lộ — hợp review before/after), ss (LTX + LoRA).
// GỠ PRODUCT_AD_LOCKS/PRODUCT_TERMS_RE (bias mỹ phẩm) — negative giờ generic ở recipes.js.
// defaults() PHẢI khớp FE defaultConfig (motions app/pages/workflows/[id]/index.vue) + handle FlowNode.vue.
export const slugField = (s, f) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/đ/g, "d").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32) || f

// Chỉ đạo GIỌNG + CAMERA (giữ từ bản cũ): emotion → worker map file ref .wav; pace → nắn tốc độ đọc.
export const EMOTIONS = new Set(["neutral", "warm", "excited", "cheerful", "gentle", "calm", "confident", "authoritative", "urgent", "tender", "playful"])
export const PACES = new Set(["slow", "normal", "fast"])
export function voiceDir(p) {
  const out = {}
  const e = String(p.emotion || "").toLowerCase().trim()
  const pc = String(p.pace || "").toLowerCase().trim()
  const em = String(p.emphasis || "").trim()
  if (EMOTIONS.has(e)) out.emotion = e
  if (PACES.has(pc)) out.pace = pc
  if (em) out.emphasis = em.slice(0, 160)
  return out
}
export function cameraDir(p) {
  const cam = String(p.camera || p.shot || "").trim()
  return cam ? { camera: cam.slice(0, 200) } : {}
}

export const NODE_SPECS = {
  "input-image": { type: "input", contentType: "image", output: "image", multiInput: false, desc: "Ảnh nguồn (người mẫu / sản phẩm) — người dùng nạp khi chạy",
    defaults: (p) => ({ contentType: "image", source: "session", field: slugField(p.field || p.label, "anh"), label: String(p.label || "Ảnh"), staticData: "", staticMime: "", staticName: "" }) },
  "input-video": { type: "input", contentType: "video", output: "video", multiInput: false, desc: "Video nguồn (clip motion gốc / driver)",
    defaults: (p) => ({ contentType: "video", source: "session", field: slugField(p.field || p.label, "video"), label: String(p.label || "Video"), staticData: "", staticMime: "", staticName: "" }) },
  "input-audio": { type: "input", contentType: "audio", output: "audio", multiInput: false, desc: "Audio nguồn (nhạc nền / giọng)",
    defaults: (p) => ({ contentType: "audio", source: "session", field: slugField(p.field || p.label, "audio"), label: String(p.label || "Audio") }) },
  "input-text": { type: "input", contentType: "text", output: "text", multiInput: false, desc: "Text nguồn",
    defaults: (p) => ({ contentType: "text", source: "session", field: slugField(p.field || p.label, "text"), label: String(p.label || "Text") }) },
  "cast-model": { type: "cast-model", output: "image", multiInput: false, desc: "Tuyển người mẫu từ kho theo giới tính + độ tuổi — 1 người CỐ ĐỊNH cho cả phim",
    defaults: (p) => ({ gender: ["male", "female"].includes(String(p.gender || "").toLowerCase()) ? String(p.gender).toLowerCase() : "female", ageGroup: ["young", "middle", "old"].includes(String(p.ageGroup || p.age_group || "").toLowerCase()) ? String(p.ageGroup || p.age_group).toLowerCase() : "young", seed: Math.max(0, Number(p.seed) || 0), label: String(p.label || "Người mẫu (kho)") }) },
  "create-image": { type: "create-image", output: "image", multiInput: true, dyn: "image", desc: "Tạo/biến ảnh bằng Qwen-Edit (prompt + 0–6 ảnh tham chiếu) → ảnh mới",
    defaults: (p) => ({ provider: "qwen", model: "qwen-edit", prompt: String(p.prompt || ""), promptMode: "text", negativePrompt: String(p.negativePrompt || ""), apiKey: "", inputCount: 0, outputCount: 1, aspectRatio: p.aspectRatio || "auto", quality: "standard", imageMode: p.imageMode || "reference" }) },
  "compose": { type: "compose", output: "image", multiInput: true, dyn: "image", desc: "Ghép người/sản phẩm vào ảnh mẫu, giữ bố cục + identity",
    defaults: (p) => ({ provider: "qwen", personCount: Math.max(1, Math.min(2, Number(p.personCount) || 1)), keepFace: p.keepFace !== false, subjectKind: p.subjectKind || "person", sceneNote: String(p.sceneNote || ""), prompt: String(p.prompt || ""), negativePrompt: String(p.negativePrompt || ""), autoPrompt: p.autoPrompt !== false }) },
  "edit-image": { type: "edit-image", output: "image", multiInput: true, dyn: "image", desc: "Sửa/ghép ảnh Qwen-Edit: 1 ảnh → sửa theo mô tả; ≥2 ảnh + combine → GHÉP (giữ nhân dạng + đúng bao bì/nhãn)",
    defaults: (p) => ({ provider: "qwen", model: "qwen-edit", geminiModel: "nano-banana-pro", prompt: String(p.prompt || ""), negativePrompt: String(p.negativePrompt || ""), apiKey: "", inputCount: Math.max(1, Math.min(6, Number(p.inputCount) || 1)), combine: p.combine === true, outputCount: 1, quality: "1080" }) },
  "text-to-video": { type: "text-to-video", output: "video", multiInput: false, prevPort: "input", prevKinds: ["text"], desc: "Prompt → video ngắn (Wan2.2 / Wan2.1)",
    defaults: (p, ctx) => ({ model: p.model || "wan2.2", duration: Number(p.duration) || 5, aspectRatio: p.aspectRatio || ctx.aspectRatio || "16:9", prompt: String(p.prompt || ""), negativePrompt: String(p.negativePrompt || ""), ...cameraDir(p) }) },
  "wan-i2v": { type: "wan-i2v", output: "video", multiInput: true, desc: "1 ảnh đầu (+ ảnh cuối tùy chọn) + prompt → video Wan I2V",
    defaults: (p, ctx) => ({ prompt: String(p.prompt || ""), negativePrompt: String(p.negativePrompt || ""), duration: Number(p.duration) || 5, aspectRatio: p.aspectRatio || ctx.aspectRatio || "9:16", wanModel: p.wanModel || "wan2.2", matchRef: p.matchRef !== false, endEnabled: p.endEnabled === true, ...cameraDir(p) }) },
  "tryon": { type: "tryon", output: "image", multiInput: true, desc: "Thử đồ: model (1 ảnh) + product (1 ảnh) → ảnh mặc đồ mới",
    defaults: (p) => ({ provider: "qwen", garmentType: p.garmentType || "auto", autoAnalyze: true, brightness: 0, outputRes: "" }) },
  "motion": { type: "motion", output: "video", multiInput: true, desc: "Wan 2.2 Animate: ảnh (cổng image) + video driver (cổng motion) → video theo driver",
    defaults: (p, ctx) => ({ preset: p.preset || "5s-720p", mode: "transfer", aspectRatio: ctx.aspectRatio, quality: "480p", refImageSource: "prev", motionVideoSource: "prev", audioMode: "original", audioPassthrough: true, fps60: true, loraRelight: 0, skipFirstFrames: 0, matchRef: false, matchRefMethod: "mkl", matchRefStrength: 0, brightCap: 1.0, faceStrength: 0.7, faceSource: "driver", bodyProportionLock: true, poseStrength: 0.7, clipStrength: 1.35, detailUpscale: false, deliverySharpen: false,
      ...(p.faceLock ? { faceLock: 1 } : {}),
      ...(Number(p.driverDurSec) > 0 ? { driverStartSec: Number(p.driverStartSec) || 0, driverDurSec: Number(p.driverDurSec) } : {}) }) },
  "talk": { type: "talk", output: "video", multiInput: false, prevPort: undefined, prevKinds: ["image"], desc: "Người NÓI nhép miệng (MultiTalk): 1 ẢNH + câu thoại + hành động → video nói",
    defaults: (p) => ({ line: String(p.line || p.script || ""), voice: String(p.voice || "vixtts"), prompt: String(p.action || p.prompt || ""), fps: Number(p.fps) || 25, ...voiceDir(p) }) },
  "voiceover": { type: "voiceover", output: "video", multiInput: false, prevPort: "input", prevKinds: ["video"], desc: "Lồng tiếng: 1 CLIP + lời thuyết minh → giọng đọc ghép lên clip",
    defaults: (p) => ({ script: String(p.script || p.line || ""), voice: String(p.voice || "vixtts"), mix: p.mix === "overlay" ? "overlay" : "replace", ...voiceDir(p) }) },
  "concat": { type: "concat", output: "video", multiInput: true, dyn: "clip", desc: "Ghép ≥2 clip video thành 1 (cổng clip1, clip2…), giữ tiếng từng cảnh",
    defaults: (p) => ({ clipCount: 2, transition: p.transition || "cut", transitionDuration: p.transition && p.transition !== "cut" ? 0.35 : 0, softCutFrames: 3, fps: 0, audioMode: "clips" }) },
  "subtitle": { type: "subtitle", output: "video", multiInput: false, prevPort: undefined, prevKinds: ["video"], desc: "Phụ đề/dịch: 1 video → ASR + cháy phụ đề",
    defaults: (p) => ({ mode: p.mode || "subtitle", targetLang: p.targetLang || "vi", bilingual: false, asrModel: "medium", fontSize: 18, position: "bottom", voice: "", voiceSpeed: 1.15 }) },
  "enhance": { type: "enhance", output: "video", multiInput: false, prevPort: undefined, prevKinds: ["video", "image"], desc: "Nâng chất lượng VIDEO (upscale + fps) hoặc ẢNH (ESRGAN ×4)",
    defaults: (p) => ({ mode: p.mode || "auto", targetRes: p.targetRes || "1080p", fpsInterp: String(p.fpsInterp || "60"), upscaleModel: p.upscaleModel || "4x-UltraSharp" }) },
  // ALD 09/07/2026 - MỚI: reveal (Đè lộ) — 2 video CÙNG bố cục → dải/line quét lộ dần video B. Hợp review
  // BEFORE/AFTER (so sánh trước-sau). Handle base + reveal (FlowNode REVEAL_TARGETS; engine MULTI_INPUT có sẵn).
  "reveal": { type: "reveal", output: "video", multiInput: true, desc: "Đè lộ 2 video cùng bố cục: line/dải quét lộ dần video B trên video A — chuẩn so sánh BEFORE/AFTER trong review",
    defaults: (p) => ({ revealMode: p.revealMode || "slider", direction: p.direction || "down", customSlider: false, sweepAtSec: null, startPos: 0, endPos: 1, showLine: p.showLine !== false, sweepDuration: Number(p.sweepDuration) || 1, loop: p.loop === true, bandPct: 0.25, vortexTwists: 2, swapBase: false }) },
  // ALD 09/07/2026 - MỚI: ss (Ảnh→Video LTX + LoRA custom). Compiler mặc định KHÔNG dùng (ưu tiên wan-i2v);
  // expose để storyboard JSON ngoài / thể loại sau này gọi được khi user có LoRA riêng.
  "ss": { type: "ss", output: "video", multiInput: true, desc: "Ảnh → video LTX-2.3 với LoRA custom của user (chỉ dùng khi user có LoRA riêng; mặc định ưu tiên wan-i2v)",
    defaults: (p, ctx) => ({ model: "ltx", prompt: String(p.prompt || ""), negativePrompt: String(p.negativePrompt || ""), promptMode: "text", promptJson: "", duration: Number(p.duration) || 5, aspectRatio: p.aspectRatio || ctx.aspectRatio || "9:16", linkMode: "anchor", loraName: String(p.loraName || ""), loraStrength: Number(p.loraStrength) || 1.0, inputCount: 1 }) },
  "output": { type: "output", output: null, multiInput: false, prevPort: undefined, prevKinds: ["video", "image", "audio", "text"], desc: "Kết quả cuối — luôn 1 node ở cuối workflow",
    defaults: (p) => ({ format: p.format || "video", cleanup: true }) },
}

export const TYPE_ALIASES = { input: "input-text", inputtext: "input-text", inputimage: "input-image", inputvideo: "input-video", inputaudio: "input-audio", image: "create-image", "create_image": "create-image", "createimage": "create-image", "try-on": "tryon", "motion-transfer": "motion", "voice-over": "voiceover", subtitles: "subtitle",
  "product-overlay": "edit-image", productoverlay: "edit-image", "edit_image": "edit-image", editimage: "edit-image" }

export function resolveSpecKey(raw) {
  const k = String(raw || "").toLowerCase().trim()
  if (NODE_SPECS[k]) return k
  if (TYPE_ALIASES[k]) return TYPE_ALIASES[k]
  return null
}
export const MULTI_INPUT_TYPES = new Set(Object.values(NODE_SPECS).filter((s) => s.multiInput).map((s) => s.type))
export const ENUM_TYPES = Object.keys(NODE_SPECS)
// #endregion
