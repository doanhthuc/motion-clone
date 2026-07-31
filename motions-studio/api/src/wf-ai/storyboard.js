// #region ALD 09/07/2026 - wf-ai/storyboard.js: tầng STORYBOARD — 1 LLM call (format:json) sinh beats REVIEW.
// Thay 5 parser chồng nhau + tool-loop 48 vòng của bản cũ bằng: normalizeExternalStoryboard (script đã là JSON
// scenes → khỏi gọi LLM) → buildStoryboard (1 call) → templateStoryboard (fallback deterministic, KHÔNG cần LLM).
// Ending (review-verdict|cta-buy|cta-follow) do interview quyết — LLM chỉ viết lời, compiler ép cảnh cuối đúng role.
import { chatJson } from "./ollama.js"

export const ROLES = ["hook", "unbox", "detail", "experience", "compare", "proof", "verdict", "cta"]
const USES = ["wear", "hold", "apply", "operate", "place", "eat-drink", "show-screen", "beside", "none"]
const SHOT_TYPES_ENUM = ["extreme-close-up", "close-up", "medium-close-up", "medium", "wide", "hero-low-angle"]
const CAM_MOVES_ENUM = ["dolly-in", "pull-back", "orbit", "tracking", "pan-left", "pan-right", "crane-up", "static", "handheld"]
const LIGHTS_ENUM = ["studio-premium", "golden-hour", "neon", "high-key", "dark-luxury", "window-natural"]

// physicality → usesProductHow mặc định (khi LLM bỏ trống/bịa).
const HOW_BY_PHYS = { wearable: "wear", handheld: "hold", tabletop: "place", large: "beside", consumable: "eat-drink", digital: "show-screen" }

// ── Thời lượng (tái dùng bản cũ, isTalk đọc theo schema mới) ──
export function parseTargetSeconds(script) {
  const m = String(script || "").match(/(?:~\s*)?(\d{1,3})\s*(?:giây|giay(?![a-zà-ỹ])|s\b|sec(?:onds?)?)/i)
  const n = m ? Number(m[1]) : 0
  return n >= 8 && n <= 180 ? n : 0
}
export function estimateTalkSeconds(line) {
  const words = String(line || "").trim().split(/\s+/).filter(Boolean).length
  return Math.max(3, Math.min(12, Math.round(words / 3)))
}
export function distributeSceneDurations(scenes, targetSec, log) {
  if (!targetSec || !Array.isArray(scenes) || !scenes.length) return
  let talkSec = 0
  const anims = []
  for (const s of scenes) {
    if (s.talkToCamera) talkSec += estimateTalkSeconds(s.voiceoverVi)
    else anims.push(s)
  }
  if (!anims.length) return
  const per = Math.round(Math.max(3, Math.min(10, (targetSec - talkSec) / anims.length)))
  anims.forEach((s) => { s.durationSec = per })
  log?.push?.(`thời lượng: mục tiêu ~${targetSec}s ≈ talk ~${Math.round(talkSec)}s + ${anims.length} cảnh video × ${per}s`)
}

// ── Chuẩn hoá 1 scene thô (LLM/JSON ngoài) → schema đóng. Enum sai → default, KHÔNG throw. ──
export function normalizeScene(raw, i, { profile, personMode } = {}) {
  const s = raw && typeof raw === "object" ? raw : {}
  const role = ROLES.includes(String(s.role || "").toLowerCase()) ? String(s.role).toLowerCase() : (i === 0 ? "hook" : "experience")
  const defaultHow = HOW_BY_PHYS[String(profile?.physicality || "")] || "none"
  const how = USES.includes(String(s.usesProductHow || "").toLowerCase()) ? String(s.usesProductHow).toLowerCase() : defaultHow
  const needsPerson = personMode === "off" ? false : s.needsPerson !== false
  const compareRaw = s.compare && typeof s.compare === "object" ? s.compare : null
  const compare = compareRaw && (compareRaw.beforeEn || compareRaw.afterEn)
    ? { beforeEn: String(compareRaw.beforeEn || "").slice(0, 400), afterEn: String(compareRaw.afterEn || "").slice(0, 400) }
    : null
  const out = {
    role,
    needsPerson,
    usesProductHow: how,
    titleVi: String(s.titleVi || s.title || `Cảnh ${i + 1}`).slice(0, 60),
    promptEn: String(s.promptEn || s.visual || s.action || "").slice(0, 500),
    voiceoverVi: String(s.voiceoverVi || s.voiceLine || s.line || s.voice || "").slice(0, 500),
    shotType: SHOT_TYPES_ENUM.includes(String(s.shotType || "").toLowerCase()) ? String(s.shotType).toLowerCase() : "medium",
    cameraMove: CAM_MOVES_ENUM.includes(String(s.cameraMove || "").toLowerCase()) ? String(s.cameraMove).toLowerCase() : "dolly-in",
    lighting: LIGHTS_ENUM.includes(String(s.lighting || "").toLowerCase()) ? String(s.lighting).toLowerCase() : "",
    emotion: String(s.emotion || "").toLowerCase().trim(),
    pace: String(s.pace || "").toLowerCase().trim(),
    talkToCamera: s.talkToCamera === true,
    durationSec: Math.max(3, Math.min(10, Number(s.durationSec || s.duration) || 4)),
    compare: role === "compare" ? compare : null,
    brollStyle: String(s.brollStyle || "").toLowerCase().trim(),
  }
  // compare mà thiếu before/after → hạ về experience (compiler không đủ dữ liệu dựng reveal).
  if (out.role === "compare" && !out.compare) out.role = "experience"
  return (out.promptEn || out.voiceoverVi) ? out : null
}

function normalizeMeta(raw) {
  const m = raw && typeof raw === "object" ? raw : {}
  return {
    title: String(m.title || "").slice(0, 100),
    look: String(m.look || "").slice(0, 240),
    palette: String(m.palette || "").slice(0, 160),
    mood: String(m.mood || "").slice(0, 120),
    lens: String(m.lens || "").slice(0, 60),
    lighting: String(m.lighting || "").slice(0, 160),
    personaGender: ["male", "female"].includes(String(m.personaGender || m.modelGender || "").toLowerCase()) ? String(m.personaGender || m.modelGender).toLowerCase() : "female",
    personaAge: ["young", "middle", "old"].includes(String(m.personaAge || m.modelAge || "").toLowerCase()) ? String(m.personaAge || m.modelAge).toLowerCase() : "young",
  }
}

// ── Script đã LÀ JSON storyboard (scenes/segments/shots) → map thẳng, khỏi gọi LLM ──
export function normalizeExternalStoryboard(script, { profile, personMode, maxScenes } = {}) {
  const raw = String(script || "").trim()
  if (!raw || !/^\s*[\[{]/.test(raw)) return null
  let obj
  try { obj = JSON.parse(raw) } catch { return null }
  const list = [obj?.scenes, obj?.segments, obj?.shots, obj?.storyboard?.scenes, obj?.storyboard?.segments].find((v) => Array.isArray(v))
  if (!list || !list.length) return null
  const scenes = list.slice(0, maxScenes || 10).map((s, i) => normalizeScene(s, i, { profile, personMode })).filter(Boolean)
  if (!scenes.length) return null
  return { meta: normalizeMeta(obj.meta || obj.brief), scenes, source: "external-json" }
}

const ENDING_RULES = {
  "review-verdict": 'Cảnh CUỐI role "verdict": chốt nhận xét REVIEW khách quan (ưu điểm chính + 1 lưu ý nếu có), KHÔNG kêu gọi mua hàng, KHÔNG "inbox ngay".',
  "cta-buy": 'Cảnh CUỐI role "cta": kêu gọi mua/đặt hàng/nhắn tin tự nhiên, không lố.',
  "cta-follow": 'Cảnh CUỐI role "cta": kêu gọi follow/lưu video/chia sẻ để xem thêm review, KHÔNG kêu mua hàng.',
}

// ── 1 LLM call chính: profile + script + answers → storyboard REVIEW ──
export async function buildStoryboard({ script, answers, profile, characterHint, maxScenes, targetSec, ending, personMode, aspectRatio, model, think, log }) {
  const angle = String(answers?.review_angle || "").trim()
  const tone = String(answers?.tone || answers?.style || "khach-quan").trim()
  const prodCtx = profile
    ? `SẢN PHẨM (SỰ THẬT TUYỆT ĐỐI — ghi đè mọi mô tả khác): ${profile.name} — ${profile.category} (physicality: ${profile.physicality}${profile.garment ? ", quần áo" : ""}).${profile.usage ? ` Cách dùng: ${profile.usage}.` : ""}${profile.usageEn ? ` Hành động dùng (EN): ${profile.usageEn}.` : ""}${profile.sellingPoints?.length ? ` Điểm đáng review: ${profile.sellingPoints.join("; ")}.` : ""}${profile.details ? ` Chi tiết nhận diện (EN): ${profile.details}.` : ""}`
    : `CHƯA rõ sản phẩm từ ảnh — TUYỆT ĐỐI KHÔNG BỊA loại sản phẩm: chỉ nói đúng như kịch bản mô tả; kịch bản không nêu loại thì gọi trung tính "sản phẩm", hành động dùng lấy từ kịch bản.`
  const personCtx = personMode === "off"
    ? "KHÔNG có người trong hình — mọi cảnh needsPerson=false, chỉ cận cảnh sản phẩm (b-roll) + thuyết minh review."
    : `CÓ người trải nghiệm sản phẩm.${characterHint ? ` Nhân vật (GIỮ NGUYÊN mọi cảnh): ${characterHint}.` : ""} Xen kẽ cảnh người dùng thật sản phẩm và ≥1 cảnh needsPerson=false cận sản phẩm nếu phim ≥15s.`
  const sys = `Bạn là ĐẠO DIỄN video REVIEW SẢN PHẨM chuyên nghiệp (mọi ngành hàng — thời trang, đồ ăn, gia dụng, xe, app…). Đọc yêu cầu + hồ sơ sản phẩm rồi trả STORYBOARD. CHỈ trả JSON thuần, KHÔNG giải thích.
${prodCtx}
${personCtx}
${angle ? `GÓC REVIEW khách chọn: ${angle} (trai-nghiem = dùng thử thực tế; mo-hop = unbox rồi đánh giá; so-sanh = TRƯỚC/SAU — phải có 1 cảnh role "compare" điền compare.beforeEn/afterEn cùng bố cục khung; tinh-nang = đi từng điểm nổi bật).` : ""}
TONE lời thoại: ${tone === "than-thien" ? "thân thiện đời thường, như bạn bè kể chuyện" : tone === "nang-dong" ? "trẻ trung năng động, bắt trend" : "khách quan như chuyên gia review, đáng tin"}. Lời thoại tiếng Việt CÓ DẤU, tự nhiên, KHÔNG văn mẫu quảng cáo lố.
${ENDING_RULES[ending] || ENDING_RULES["review-verdict"]}
Tỉ lệ khung ${aspectRatio}. Tối đa ${maxScenes} cảnh.${targetSec ? ` TỔNG thời lượng ≈ ${targetSec}s — cộng durationSec các cảnh xấp xỉ số này.` : ""}
Schema:
{ "meta": { "title": "<tên video tiếng Việt>", "look": "<phong cách hình ảnh TIẾNG ANH, kèm 'photorealistic real human'>", "palette": "<bảng màu EN>", "mood": "<EN>", "lens": "<vd 50mm>", "lighting": "<EN>", "personaGender": "female|male", "personaAge": "young|middle|old" },
  "scenes": [ { "role": "hook|unbox|detail|experience|compare|proof|verdict|cta",
      "needsPerson": true|false,
      "usesProductHow": "wear|hold|apply|operate|place|eat-drink|show-screen|beside|none",
      "titleVi": "<tên cảnh tiếng Việt>",
      "promptEn": "<chủ thể + bối cảnh + HÀNH ĐỘNG tối đa 2 nhịp, TIẾNG ANH, kèm từ chỉ tốc độ (briskly/gently), KHÔNG slow motion>",
      "voiceoverVi": "<lời review tiếng Việt có dấu>",
      "shotType": "extreme-close-up|close-up|medium-close-up|medium|wide|hero-low-angle",
      "cameraMove": "dolly-in|pull-back|orbit|tracking|pan-left|pan-right|crane-up|static|handheld",
      "lighting": "studio-premium|golden-hour|neon|high-key|dark-luxury|window-natural",
      "emotion": "warm|excited|gentle|confident|urgent|neutral", "pace": "slow|normal|fast",
      "talkToCamera": true|false, "durationSec": 4,
      "compare": { "beforeEn": "<khung TRƯỚC, EN>", "afterEn": "<khung SAU, CÙNG bố cục, EN>" } } ] }
Quy tắc: cảnh 1 = HOOK mở giữa hành động hoặc hero reveal — sản phẩm xuất hiện ≤3s đầu, KHÔNG mở tĩnh/chào dài. role "experience" phải cho DÙNG THẬT sản phẩm đúng cách dùng ở trên. compare CHỈ dùng khi hiệu quả nhìn thấy được. talkToCamera=true chỉ khi người nhìn thẳng camera NÓI. Mỗi cảnh 1-2 nhịp hành động + đúng 1 cameraMove. durationSec 3-10.`
  const { obj, model: usedModel } = await chatJson({ model, sys, user: String(script || "").slice(0, 10000), think, log })
  if (!obj || !Array.isArray(obj.scenes) || !obj.scenes.length) {
    log?.push?.("storyboard LLM rỗng/JSON xấu")
    return { meta: normalizeMeta(obj?.meta), scenes: [], model: usedModel }
  }
  const personModeEff = personMode
  const scenes = obj.scenes.slice(0, maxScenes).map((s, i) => normalizeScene(s, i, { profile, personMode: personModeEff })).filter(Boolean)
  log?.push?.(`storyboard: ${scenes.length} cảnh (${usedModel})`)
  return { meta: normalizeMeta(obj.meta), scenes, model: usedModel }
}

// ── Fallback deterministic — KHÔNG cần LLM: dựng review chuẩn từ profile (hết câu fallback bias serum của bản cũ) ──
export function templateStoryboard({ profile, ending, maxScenes, personMode, answers }) {
  const name = profile?.name || "sản phẩm"
  const points = profile?.sellingPoints?.length ? profile.sellingPoints : []
  const usage = profile?.usage || ""
  const person = personMode !== "off"
  const how = HOW_BY_PHYS[String(profile?.physicality || "")] || "none"
  const wantCompare = String(answers?.review_angle || "") === "so-sanh"
  const scenes = []
  scenes.push({
    role: "hook", needsPerson: false, usesProductHow: "none", titleVi: "Mở đầu",
    promptEn: "hero reveal of the product, premium commercial opening", voiceoverVi: `Trên tay mình là ${name} — cùng xem nó có đáng tiền không nhé.`,
    shotType: "hero-low-angle", cameraMove: "orbit", lighting: "studio-premium", emotion: "excited", pace: "normal",
    talkToCamera: false, durationSec: 4, compare: null, brollStyle: "hero",
  })
  scenes.push({
    role: "detail", needsPerson: false, usesProductHow: "none", titleVi: "Cận chi tiết",
    promptEn: "extreme macro close-up of the product's most distinctive detail", voiceoverVi: points[0] ? `Điểm đầu tiên mình thích: ${points[0]}.` : `Nhìn gần mới thấy phần hoàn thiện của ${name}.`,
    shotType: "extreme-close-up", cameraMove: "dolly-in", lighting: "dark-luxury", emotion: "confident", pace: "normal",
    talkToCamera: false, durationSec: 4, compare: null, brollStyle: "macro",
  })
  scenes.push({
    role: "experience", needsPerson: person, usesProductHow: how, titleVi: "Trải nghiệm thật",
    promptEn: profile?.usageEn || "using the product naturally in a matching real-life setting", voiceoverVi: usage ? `Dùng thử thực tế: ${usage}.` : `Trải nghiệm thực tế với ${name}.`,
    shotType: "medium", cameraMove: "tracking", lighting: "window-natural", emotion: "warm", pace: "normal",
    talkToCamera: false, durationSec: 5, compare: null, brollStyle: "fresh",
  })
  if (wantCompare) {
    scenes.push({
      role: "compare", needsPerson: false, usesProductHow: "none", titleVi: "Trước / Sau",
      promptEn: "before and after comparison, same framing", voiceoverVi: "Trước và sau khi dùng — khác biệt thấy rõ.",
      shotType: "medium", cameraMove: "static", lighting: "high-key", emotion: "confident", pace: "normal",
      talkToCamera: false, durationSec: 6,
      compare: { beforeEn: "the subject BEFORE using the product, same framing, neutral lighting", afterEn: "the subject AFTER using the product, same framing, improved clean result" },
      brollStyle: "",
    })
  }
  const endingScene = ending === "cta-buy"
    ? { voiceoverVi: `Nếu bạn đang tìm ${profile?.category || "một sản phẩm như này"}, inbox mình để được tư vấn nhé.`, role: "cta" }
    : ending === "cta-follow"
      ? { voiceoverVi: "Thấy hữu ích thì follow mình để xem thêm nhiều review thật nữa nhé.", role: "cta" }
      : { voiceoverVi: points[1] ? `Chốt lại: ${points[1]} — với mình là đáng tiền.` : `Chốt lại, ${name} làm tốt đúng những gì nó hứa.`, role: "verdict" }
  scenes.push({
    role: endingScene.role, needsPerson: person, usesProductHow: person ? how : "none", titleVi: "Chốt review",
    promptEn: "confident closing shot presenting the product", voiceoverVi: endingScene.voiceoverVi,
    shotType: "medium-close-up", cameraMove: "static", lighting: "studio-premium", emotion: "confident", pace: "normal",
    talkToCamera: person, durationSec: 4, compare: null, brollStyle: "hero",
  })
  return { meta: normalizeMeta({ title: `Review ${name}`, look: "clean modern review video, photorealistic real human" }), scenes: scenes.slice(0, Math.max(3, maxScenes)), source: "template" }
}
// #endregion
