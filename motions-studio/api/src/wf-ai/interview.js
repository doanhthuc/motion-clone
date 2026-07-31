// #region ALD 09/07/2026 - wf-ai/interview.js: phỏng vấn ngược GENERIC cho review MỌI loại sản phẩm.
// Thay INTERVIEW_DEFAULTS cũ (bias default "Mỹ phẩm" — thủ phạm "giày → phim nước hoa"). Bộ BASE cố định +
// LLM đề xuất TỐI ĐA 2 câu đặc thù theo kịch bản. Giữ id "shot_count"/"aspect" (route cũ/FE đọc 2 id này).
import { chatJson } from "./ollama.js"

export const INTERVIEW_BASE = [
  { id: "review_angle", header: "Góc review", question: "Bạn muốn review theo góc nào?", multiSelect: false, allowOther: true, suggested: "trai-nghiem",
    options: [
      { value: "trai-nghiem", label: "Trải nghiệm thực tế", description: "Dùng thử thật, cảm nhận thật" },
      { value: "mo-hop", label: "Mở hộp (unbox)", description: "Đập hộp + đánh giá nhanh" },
      { value: "so-sanh", label: "So sánh trước/sau", description: "Hiệu quả nhìn thấy được (có cảnh before/after)" },
      { value: "tinh-nang", label: "Điểm nổi bật", description: "Đi từng tính năng đáng tiền" },
    ] },
  { id: "tone", header: "Tone", question: "Giọng điệu video?", multiSelect: false, allowOther: true, suggested: "khach-quan",
    options: [
      { value: "khach-quan", label: "Khách quan / chuyên gia", description: "Đáng tin, phân tích rõ" },
      { value: "than-thien", label: "Thân thiện / đời thường", description: "Như bạn bè kể chuyện" },
      { value: "nang-dong", label: "Trẻ trung / bắt trend", description: "Nhịp nhanh, năng lượng cao" },
    ] },
  { id: "shot_count", header: "Số cảnh", question: "Video có khoảng bao nhiêu cảnh?", multiSelect: false, allowOther: true, suggested: "5",
    options: [
      { value: "3", label: "3 cảnh (~15s)", description: "Gọn, 1 thông điệp" },
      { value: "5", label: "5 cảnh (~25-30s)", description: "Đủ kể câu chuyện review" },
      { value: "7", label: "7 cảnh (~40s)", description: "Nhiều điểm nhấn" },
    ] },
  // ALD 09/07 - quyết định user: KIỂU KẾT hỏi trong phỏng vấn — mặc định review khách quan, CTA opt-in.
  { id: "ending", header: "Kiểu kết", question: "Kết video kiểu gì?", multiSelect: false, allowOther: true, suggested: "review",
    options: [
      { value: "review", label: "Chốt nhận xét review", description: "Ưu/nhược khách quan — KHÔNG kêu gọi mua (mặc định)" },
      { value: "cta-mua", label: "CTA mua hàng", description: "Kêu gọi đặt hàng / inbox tư vấn" },
      { value: "cta-follow", label: "CTA follow", description: "Kêu gọi follow / lưu video" },
    ] },
  { id: "person_presence", header: "Người mẫu", question: "Video có người trải nghiệm sản phẩm không?", multiSelect: false, allowOther: false, suggested: "co",
    options: [
      { value: "co", label: "Có người trải nghiệm", description: "Người cầm/mặc/dùng sản phẩm trong hình" },
      { value: "khong", label: "Không — chỉ sản phẩm", description: "Cận cảnh sản phẩm + thuyết minh review" },
    ] },
  { id: "aspect", header: "Tỉ lệ", question: "Tỉ lệ khung hình đăng ở đâu?", multiSelect: false, allowOther: true, suggested: "9:16",
    options: [
      { value: "9:16", label: "Dọc 9:16 (TikTok/Reels)", description: "Video ngắn mạng xã hội" },
      { value: "16:9", label: "Ngang 16:9 (YouTube/web)", description: "Trình chiếu ngang" },
      { value: "1:1", label: "Vuông 1:1 (Feed)", description: "Bài đăng vuông" },
    ] },
]
// Hỏi mô tả sản phẩm CHỈ khi chưa có ảnh SP (không vision được thì ít nhất có chữ cho profileFromText).
export const PRODUCT_DESC_Q = { id: "product_desc", header: "Sản phẩm", question: "Sản phẩm bạn muốn review là gì? (tên/loại càng rõ càng tốt)", multiSelect: false, allowOther: true, suggested: "",
  options: [
    { value: "thoi-trang", label: "Đồ mặc được", description: "Quần áo, giày, túi, phụ kiện" },
    { value: "do-dung", label: "Đồ dùng / gia dụng", description: "Đặt bàn, thiết bị, đồ lớn" },
    { value: "an-uong", label: "Đồ ăn / uống", description: "F&B" },
    { value: "app-dich-vu", label: "App / dịch vụ", description: "Phần mềm, khoá học, dịch vụ" },
  ] }

// Tái dùng nguyên normalizeQuestions bản cũ (dupe-filter giọng/phụ đề — FE đã hỏi riêng 2 thứ đó).
export function normalizeQuestions(raw) {
  const arr = Array.isArray(raw) ? raw : []
  const out = []
  for (const q of arr.slice(0, 6)) {
    if (!q || typeof q !== "object") continue
    const question = String(q.question || q.q || "").trim()
    if (!question) continue
    const dupeKey = `${q.id || ""} ${q.header || ""} ${question}`.toLowerCase()
    if (/giọng|giong\b|voice|lồng\s*tiếng|long\s*tieng|phụ\s*đề|phu\s*de\b|subtitle/i.test(dupeKey)) continue
    const options = (Array.isArray(q.options) ? q.options : []).map((o, i) => {
      if (o && typeof o === "object") return { value: String(o.value || o.id || o.label || `opt${i}`).slice(0, 60), label: String(o.label || o.value || `Lựa chọn ${i + 1}`).slice(0, 80), description: String(o.description || o.desc || "").slice(0, 160) }
      return { value: String(o).slice(0, 60), label: String(o).slice(0, 80), description: "" }
    }).filter((o) => o.label).slice(0, 5)
    if (options.length < 2) continue
    out.push({
      id: String(q.id || q.key || question).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || `q${out.length}`,
      header: String(q.header || "").slice(0, 16) || "Chọn",
      question, multiSelect: q.multiSelect === true, allowOther: q.allowOther !== false,
      suggested: q.suggested != null ? String(q.suggested).slice(0, 60) : (options[0]?.value || ""),
      options,
    })
  }
  return out.slice(0, 5)
}

export function baseQuestions({ hasProductImage } = {}) {
  const qs = [...INTERVIEW_BASE]
  if (!hasProductImage) qs.splice(0, 0, PRODUCT_DESC_Q)
  return qs.slice(0, 6)
}

// LLM đề xuất TỐI ĐA 2 câu bổ sung đặc thù kịch bản (không trùng BASE) — lỗi → BASE thôi, KHÔNG bao giờ throw.
export async function interviewQuestions({ script, model, think, hasProductImage, hasModelImage, log }) {
  const base = baseQuestions({ hasProductImage })
  const baseIds = new Set(base.map((q) => q.id))
  const sys = `Bạn là ĐẠO DIỄN video REVIEW SẢN PHẨM. Người dùng sắp dựng video review từ kịch bản dưới đây.
Hệ thống ĐÃ hỏi sẵn: góc review, tone, số cảnh, kiểu kết, có người mẫu không, tỉ lệ khung${hasProductImage ? "" : ", mô tả sản phẩm"}. KHÔNG hỏi lại các điều đó, KHÔNG hỏi về giọng đọc/phụ đề/nguồn ảnh.
Nếu kịch bản có điểm ĐẶC THÙ cần làm rõ (vd bối cảnh quay, đối tượng xem, điểm nhấn muốn ưu tiên), đề xuất TỐI ĐA 2 câu hỏi; không có gì đáng hỏi → trả mảng rỗng.
CHỈ trả JSON thuần: { "questions": [ { "id": "<slug>", "header": "<≤14 ký tự>", "question": "<tiếng Việt>", "multiSelect": false, "allowOther": true, "suggested": "<value>", "options": [ { "value": "<slug>", "label": "<nhãn>", "description": "<ngắn>" } ] } ] }`
  try {
    const { obj } = await chatJson({ model, sys, user: String(script || "").slice(0, 8000), think, log })
    const extra = normalizeQuestions(obj?.questions).filter((q) => !baseIds.has(q.id)).slice(0, 2)
    if (extra.length) log?.push?.(`interview: +${extra.length} câu đặc thù từ LLM`)
    return [...base, ...extra].slice(0, 6)
  } catch (e) {
    log?.push?.(`interview LLM lỗi → dùng bộ câu hỏi chuẩn: ${e?.message || e}`)
    return base
  }
}
// #endregion
