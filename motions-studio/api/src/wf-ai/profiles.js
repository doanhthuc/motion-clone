// #region ALD 09/07/2026 - wf-ai/profiles.js: tầng PROFILE — hiểu input trước khi đạo diễn.
// analyzeProduct: ảnh sản phẩm → HỒ SƠ SẢN PHẨM (category TỰ DO — hết enum 12 loại; physicality enum ĐÓNG cho
// compiler tra recipe). analyzeStoryboard: ảnh storyboard/nhân vật → brief + scenes + character sheet (tái dùng
// nguyên bản cũ — đã chạy tốt). profileFromText: fallback không vision (default tabletop — KHÔNG handheld như cũ).
import { VISION_MODEL, DEFAULT_MODEL, ollamaChat, ollamaHasVision, ollamaUnload, jsonFromLLM } from "./ollama.js"

export const PHYSICALITIES = ["wearable", "handheld", "tabletop", "large", "consumable", "digital"]

async function fetchImageB64(asset) {
  const url = String(asset?.url || asset?.signedUrl || asset?.staticUrl || "").trim()
    || (typeof asset === "string" ? String(asset).trim() : "")
  if (!/^https?:\/\//i.test(url)) return null
  const r0 = await fetch(url, { signal: AbortSignal.timeout(20000) })
  if (!r0.ok) throw new Error(`tải ảnh HTTP ${r0.status}`)
  return Buffer.from(await r0.arrayBuffer()).toString("base64")
}

// ── PRODUCT PROFILE (vision) ──
export async function analyzeProduct(asset, log) {
  let b64
  try {
    b64 = await fetchImageB64(asset)
  } catch (e) { log.push(`vision: tải ảnh sản phẩm lỗi (${e?.message || e}) → bỏ qua`); return null }
  if (!b64) { log.push("vision: ảnh sản phẩm không có URL http(s) → bỏ qua phân tích"); return null }
  try {
    if (!(await ollamaHasVision(VISION_MODEL, log))) {
      log.push(`vision: model ${VISION_MODEL} KHÔNG hỗ trợ ảnh (Ollama sẽ âm thầm bỏ ảnh → dễ bịa sản phẩm) → bỏ qua. Set WORKFLOW_AI_VISION_MODEL sang model VL (vd qwen2.5vl:7b).`)
      return null
    }
    const sys = `Bạn là chuyên gia phân tích sản phẩm cho video REVIEW. Nhìn KỸ ẢNH và CHỈ trả JSON thuần:
{ "name": "<tên sản phẩm ngắn tiếng Việt>",
  "nameEn": "<mô tả TIẾNG ANH ngắn để dựng ảnh, vd 'white espresso machine with steel portafilter'>",
  "category": "<loại sản phẩm, TỰ DO tiếng Việt, vd 'máy pha cà phê espresso', 'đầm dạ hội', 'app đặt lịch'>",
  "physicality": "wearable|handheld|tabletop|large|consumable|digital",
  "garment": true|false,
  "usage": "<1 câu tiếng Việt: người ta DÙNG sản phẩm này thế nào>",
  "usageEn": "<hành động dùng, TIẾNG ANH ngắn, vd 'slips the sneakers on and walks briskly'>",
  "sellingPoints": ["<2-4 điểm đáng nói khi review, tiếng Việt ngắn>"],
  "details": "<TIẾNG ANH: chi tiết nhận diện PHẢI GIỮ ĐÚNG khi ghép ảnh — màu, chất liệu, logo, hoạ tiết, nắp/đế/nhãn>",
  "sizeHint": "<TIẾNG ANH: kích thước THẬT so với cơ thể/bàn tay, vd 'fits in one hand (~15cm tall)'>" }
Định nghĩa physicality: wearable = mặc/mang/đeo LÊN NGƯỜI (giày, áo, kính, túi, đồng hồ); garment=true CHỈ khi là QUẦN ÁO
mặc lên thân (áo/váy/đầm/quần — giày/kính/túi thì garment=false); handheld = cầm gọn 1 tay (chai, hộp nhỏ, điện thoại);
tabletop = đặt bàn/kệ (nồi chiên, loa, máy pha cà phê, laptop); large = đồ lớn đứng cạnh (xe, tủ lạnh, sofa, TV);
consumable = đồ ăn/uống; digital = app/phần mềm/dịch vụ (ảnh là màn hình/logo/poster).`
    const r = await ollamaChat({ model: VISION_MODEL, messages: [
      { role: "system", content: sys },
      { role: "user", content: "Phân tích ảnh sản phẩm này.", images: [b64] },
    ], format: "json", think: false })
    const obj = jsonFromLLM(r.message?.content)
    if (!obj?.name) throw new Error("JSON thiếu name")
    const profile = {
      name: String(obj.name).slice(0, 120),
      nameEn: String(obj.nameEn || "").slice(0, 160),
      category: String(obj.category || "sản phẩm").slice(0, 80),
      physicality: PHYSICALITIES.includes(String(obj.physicality)) ? String(obj.physicality) : "tabletop",
      garment: obj.garment === true,
      usage: String(obj.usage || "").slice(0, 200),
      usageEn: String(obj.usageEn || "").slice(0, 200),
      sellingPoints: (Array.isArray(obj.sellingPoints) ? obj.sellingPoints : []).map((s) => String(s).slice(0, 120)).filter(Boolean).slice(0, 4),
      details: String(obj.details || "").slice(0, 300),
      sizeHint: String(obj.sizeHint || "").slice(0, 160),
      fromVision: true,
    }
    log.push(`vision: sản phẩm = ${profile.name} · ${profile.category} · ${profile.physicality}${profile.garment ? " (quần áo)" : ""}${profile.sizeHint ? ` · size: ${profile.sizeHint}` : ""}`)
    return profile
  } catch (e) {
    log.push(`vision bỏ qua: ${e?.modelMissing ? `model ${VISION_MODEL} chưa pull (ollama pull ${VISION_MODEL})` : (e?.message || e)}`)
    return null
  } finally {
    // Vision là model RIÊNG mới unload; mặc định vision = model đạo diễn → giữ trên VRAM cho bước storyboard.
    if (VISION_MODEL !== DEFAULT_MODEL) { try { await ollamaUnload(VISION_MODEL) } catch { /* best-effort */ } }
  }
}

// ── PROFILE fallback từ CHỮ (không vision): phân loại physicality bằng regex — CHỈ để tra recipe, không sinh prompt. ──
const GARMENT_RE = /(áo|ao\s|váy|vay\b|đầm|dam\b|quần|quan\s|jean|hoodie|sơ\s*mi|so\s*mi|dress|shirt|pants|skirt|jacket|coat)/i
const WEARABLE_RE = /(giày|giay\b|dép|dep\b|sneaker|shoe|sandal|boot|mũ|nón|hat\b|kính|glasses|túi\s*(?:xách|đeo)|balo|backpack|đồng\s*hồ|dong\s*ho|watch|vòng\s*tay|nhẫn|dây\s*chuyền|trang\s*sức|tai\s*nghe|headphone|earbuds?)/i
const VEHICLE_LARGE_RE = /(xe\s*máy|motorbike|scooter|ô\s*tô|oto|car\b|xe\s*hơi|xe\s*đạp|bicycle|tivi|tv\b|television|tủ\s*lạnh|fridge|refrigerator|máy\s*giặt|washing\s*machine|sofa|giường|nệm|đệm|tủ\s*quần\s*áo|wardrobe|điều\s*hòa|air\s*conditioner|máy\s*lạnh)/i
const CONSUMABLE_RE = /(đồ\s*ăn|do\s*an|thức\s*ăn|món\b|bánh|banh\b|kẹo|keo\b|trà\b|tra\s*sữa|cà\s*phê(?!\s*máy)|ca\s*phe|nước\s*(?:ép|ngọt|uống)|snack|food|drink|beverage|matcha|sinh\s*tố|mì\b|phở\b)/i
const DIGITAL_RE = /(app\b|ứng\s*dụng|ung\s*dung|phần\s*mềm|phan\s*mem|software|website|dịch\s*vụ|dich\s*vu|service|khoá\s*học|khoa\s*hoc|course|game\b|nền\s*tảng|platform|saas)/i
const HANDHELD_RE = /(serum|mỹ\s*phẩm|cosmetic|skincare|chai|lọ|lo\b|bottle|kem\b|cream\b|son\b|lipstick|điện\s*thoại|dien\s*thoai|phone|smartphone|nước\s*hoa|perfume|sách|sach\b|book\b|ly\b|cốc\b|cup\b|hộp\s*nhỏ)/i

export function profileFromText(script, answers) {
  const hay = `${String(answers?.product_desc || "")}\n${String(script || "")}`.slice(0, 4000)
  if (!hay.trim()) return null
  let physicality = "tabletop" // trung tính — KHÔNG default handheld "cầm trước ngực" như bản cũ
  let garment = false
  if (GARMENT_RE.test(hay)) { physicality = "wearable"; garment = true }
  else if (WEARABLE_RE.test(hay)) physicality = "wearable"
  else if (VEHICLE_LARGE_RE.test(hay)) physicality = "large"
  else if (DIGITAL_RE.test(hay)) physicality = "digital"
  else if (CONSUMABLE_RE.test(hay)) physicality = "consumable"
  else if (HANDHELD_RE.test(hay)) physicality = "handheld"
  const desc = String(answers?.product_desc || "").trim().slice(0, 120)
  return {
    name: desc || "sản phẩm", nameEn: "", category: desc || "sản phẩm",
    physicality, garment, usage: "", usageEn: "", sellingPoints: [], details: "", sizeHint: "",
    fromVision: false,
  }
}

// ── STORYBOARD / NHÂN VẬT (vision) — tái dùng nguyên bản cũ (đã LIVE 07/07, chạy tốt) ──
export async function analyzeStoryboard(asset, { maxScenes = 6, log = [] } = {}) {
  let b64
  try {
    b64 = await fetchImageB64(asset)
  } catch (e) { log.push(`vision(storyboard): tải ảnh lỗi (${e?.message || e}) → bỏ qua`); return null }
  if (!b64) { log.push("vision(storyboard): ảnh không có URL http(s) → bỏ qua"); return null }
  try {
    if (!(await ollamaHasVision(VISION_MODEL, log))) {
      log.push(`vision(storyboard): model ${VISION_MODEL} KHÔNG hỗ trợ ảnh → bỏ qua. Set WORKFLOW_AI_VISION_MODEL sang VL (vd qwen2.5vl:7b).`)
      return null
    }
    const sys = `Bạn là AI ĐẠO DIỄN phim. Nhìn KỸ ẢNH và tách thành kịch bản video. Ảnh có thể là:
- STORYBOARD (nhiều khung/phân cảnh kèm chữ mô tả) → đọc ĐÚNG từng khung theo thứ tự.
- 1 ẢNH NHÂN VẬT hoặc 1 CẢNH đơn → suy ra nhân vật + đề xuất các cảnh hợp lý.
CHỈ trả JSON thuần (không giải thích):
{ "kind": "storyboard" | "character" | "scene",
  "title": "<tên video ngắn tiếng Việt>",
  "character": { "present": true|false, "appearance": "<mô tả ngoại hình + TRANG PHỤC chi tiết, TIẾNG ANH — để GIỮ NGUYÊN nhân vật này ở mọi cảnh>" },
  "brief": "<1-2 câu concept/tông màu/ánh sáng dùng CHUNG cho cả phim, TIẾNG ANH>",
  "scenes": [ { "title": "<tên cảnh tiếng Việt>", "setting": "<bối cảnh TIẾNG ANH>", "action": "<hành động/chuyển động camera TIẾNG ANH>", "framing": "wide|medium|close", "durationSec": <số giây> } ],
  "tone": "<vd serene, elegant>", "pacing": "slow|normal|fast" }
Tối đa ${maxScenes} cảnh. Mọi mô tả TIẾNG ANH (trừ title/tên cảnh). Nếu storyboard đã ghi rõ số phân cảnh → GIỮ ĐÚNG số đó và đúng nội dung từng khung.`
    const r = await ollamaChat({ model: VISION_MODEL, messages: [
      { role: "system", content: sys },
      { role: "user", content: "Phân tích ảnh này thành kịch bản video.", images: [b64] },
    ], format: "json", think: false })
    const obj = jsonFromLLM(r.message?.content)
    if (!obj || !Array.isArray(obj.scenes) || !obj.scenes.length) throw new Error("JSON thiếu scenes")
    const scenes = obj.scenes.slice(0, maxScenes).map((s, i) => ({
      title: String(s?.title || `Cảnh ${i + 1}`).slice(0, 80),
      setting: String(s?.setting || "").slice(0, 240),
      action: String(s?.action || "").slice(0, 240),
      framing: ["wide", "medium", "close"].includes(String(s?.framing)) ? String(s.framing) : "medium",
      durationSec: Math.max(2, Math.min(15, Number(s?.durationSec) || 8)),
    }))
    const info = {
      kind: ["storyboard", "character", "scene"].includes(String(obj.kind)) ? String(obj.kind) : "storyboard",
      title: String(obj.title || "").slice(0, 100),
      character: { present: obj?.character?.present !== false, appearance: String(obj?.character?.appearance || "").slice(0, 400) },
      brief: String(obj.brief || "").slice(0, 300),
      scenes,
      tone: String(obj.tone || "").slice(0, 60),
      pacing: ["slow", "normal", "fast"].includes(String(obj.pacing)) ? String(obj.pacing) : "normal",
    }
    log.push(`vision(storyboard): ${info.kind} · ${scenes.length} cảnh · nhân vật ${info.character.present ? "CÓ" : "không"}${info.character.appearance ? ` (${info.character.appearance.slice(0, 60)}…)` : ""}`)
    return info
  } catch (e) {
    log.push(`vision(storyboard) bỏ qua: ${e?.modelMissing ? `model ${VISION_MODEL} chưa pull (ollama pull ${VISION_MODEL})` : (e?.message || e)}`)
    return null
  } finally {
    if (VISION_MODEL !== DEFAULT_MODEL) { try { await ollamaUnload(VISION_MODEL) } catch { /* best-effort */ } }
  }
}

// Ghép kết quả vision storyboard → 1 "script" chữ nạp vào tầng STORYBOARD.
export function storyboardInfoToScript(info) {
  if (!info || !Array.isArray(info.scenes)) return ""
  const lines = []
  if (info.title) lines.push(`Video: ${info.title}.`)
  if (info.brief) lines.push(info.brief)
  if (info.tone || info.pacing) lines.push(`Tông: ${info.tone || ""}${info.pacing ? `, nhịp ${info.pacing}` : ""}.`)
  if (info.character?.present && info.character.appearance) {
    lines.push(`Nhân vật chính (GIỮ NGUYÊN mặt, tóc, trang phục ở MỌI cảnh): ${info.character.appearance}.`)
  }
  info.scenes.forEach((s, i) => {
    lines.push(`Cảnh ${i + 1}${s.title ? ` — ${s.title}` : ""} (${s.durationSec}s, ${s.framing}): ${s.setting}. ${s.action}.`)
  })
  const total = info.scenes.reduce((a, s) => a + (Number(s.durationSec) || 0), 0)
  if (total) lines.push(`Tổng thời lượng khoảng ${total}s (${info.scenes.length} cảnh).`)
  return lines.join("\n")
}
// #endregion
