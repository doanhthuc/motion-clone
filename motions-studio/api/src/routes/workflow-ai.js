// #region ALD 09/07/2026 - VIẾT LẠI TOÀN BỘ AI đạo diễn: "REVIEW MỌI LOẠI SẢN PHẨM" (lệnh user — bản cũ 1916 dòng
// đóng khung "quảng cáo người-mẫu-cầm-sản-phẩm + CTA", fail với sản phẩm ngoài khuôn: đồ lớn, đồ ăn, app/dịch vụ,
// review không người, before/after…).
//
// Kiến trúc 3 tầng (module ../wf-ai/): PROFILE (vision → hồ sơ sản phẩm, category TỰ DO + physicality enum)
// → STORYBOARD (1 LLM call → beats review: hook/detail/experience/compare/verdict…) → COMPILER (JS thuần,
// deterministic — tra SHOT_RECIPES theo physicality × người × asset; KHÔNG guard chặn: thiếu người → b-roll).
// Thay tool-loop 48 vòng; thêm node reveal (before/after) + ss vào catalog. CONTRACT FE GIỮ NGUYÊN 100%:
// 2 endpoint, canvas-shape {nodes, edges}, needImage round-trip, asset object|string, _gen:'script'.
import { Router } from "express"
import { sessionAuth } from "../auth/session.js"
import { query } from "../db.js"
import { DEFAULT_MODEL, FALLBACK_MODEL, ollamaUnload } from "../wf-ai/ollama.js"
import { analyzeProduct, analyzeStoryboard, storyboardInfoToScript, profileFromText } from "../wf-ai/profiles.js"
import { buildStoryboard, normalizeExternalStoryboard, templateStoryboard, parseTargetSeconds, distributeSceneDurations } from "../wf-ai/storyboard.js"
import { compile, assetToInputConfig } from "../wf-ai/compiler.js"
import { interviewQuestions, baseQuestions } from "../wf-ai/interview.js"

const router = Router()

const ENDINGS = { review: "review-verdict", "review-verdict": "review-verdict", "cta-mua": "cta-buy", "cta-buy": "cta-buy", "cta-follow": "cta-follow" }

router.post("/workflows/generate-from-script", sessionAuth, async (req, res) => {
  let script = String(req.body?.script || "").trim() // có thể rỗng nếu có storyboardAsset (suy từ ẢNH)
  const answers = (req.body?.answers && typeof req.body.answers === "object") ? req.body.answers : null
  const ansShots = Number(String(answers?.shot_count ?? answers?.shots ?? "").match(/\d+/)?.[0])
  const aspectRatio = ["9:16", "16:9", "1:1", "4:5", "3:4"].includes(String(answers?.aspect || "")) ? String(answers.aspect) : (["9:16", "16:9", "1:1", "4:5", "3:4"].includes(String(req.body?.aspectRatio || "")) ? String(req.body.aspectRatio) : "9:16")
  const maxScenes = Math.max(1, Math.min(10, (Number.isFinite(ansShots) && ansShots > 0 ? ansShots : Number(req.body?.maxScenes)) || 6))
  const think = req.body?.think !== false
  const productAsset = req.body?.product || req.body?.productAsset || null
  const modelAsset = req.body?.model || req.body?.modelAsset || null
  const storyboardAsset = req.body?.storyboard || req.body?.storyboardAsset || null
  let modelAssetEff = modelAsset
  const voice = String(req.body?.voice || "").trim()
  const wantSub = req.body?.subtitle === true
  const ending = ENDINGS[String(answers?.ending || "").toLowerCase()] || "review-verdict"
  // personMode: user trả lời "không người" → toàn phim b-roll (quyết định user: KHÔNG guard chặn).
  let personMode = String(answers?.person_presence || "").toLowerCase() === "khong" ? "off" : "on"
  const rawStrategy = String(req.body?.modelStrategy || answers?.model_source || "").toLowerCase()
  let modelStrategy = ["own", "upload", "provide"].includes(rawStrategy) ? "own"
    : ["generate", "ai", "sinh"].includes(rawStrategy) ? "generate"
    : ["library", "kho"].includes(rawStrategy) ? "library"
    : (assetToInputConfig(modelAsset) ? "own" : "library")
  const log = []
  // "Từ kho" mà kho model_refs TRỐNG → tự chuyển sang SINH diễn viên (không bao giờ thiếu nguồn người).
  if (modelStrategy === "library" && personMode !== "off") {
    try {
      const { rows } = await query("SELECT count(*)::int AS n FROM model_refs WHERE is_active = true")
      if (!Number(rows?.[0]?.n)) { modelStrategy = "generate"; log.push("kho người mẫu (model_refs) trống → tự chuyển 'từ kho' → 'sinh diễn viên mới'") }
      else log.push(`kho người mẫu: ${rows[0].n} ảnh active`)
    } catch (e) { log.push(`đếm model_refs lỗi (giữ 'từ kho'): ${e?.message || e}`) }
  }
  let model = DEFAULT_MODEL
  let characterHint = ""
  try {
    // 1) Ảnh storyboard/nhân vật (nếu có) → suy kịch bản + character sheet (giữ nhất quán).
    if (storyboardAsset) {
      const sbInfo = await analyzeStoryboard(storyboardAsset, { maxScenes, log })
      if (sbInfo) {
        if (!script) { script = storyboardInfoToScript(sbInfo); log.push(`đạo diễn: suy kịch bản từ ẢNH → ${sbInfo.scenes.length} cảnh`) }
        if (sbInfo.character?.present && sbInfo.character.appearance) characterHint = sbInfo.character.appearance
        if (sbInfo.kind === "character" && !assetToInputConfig(modelAssetEff)) {
          modelAssetEff = storyboardAsset; modelStrategy = "own"; personMode = "on"
          log.push("ảnh upload là NHÂN VẬT → dùng làm người mẫu (own) cho mọi cảnh")
        } else if (sbInfo.character?.present && personMode !== "off" && modelStrategy === "own" && !assetToInputConfig(modelAssetEff)) {
          return res.json({
            needImage: "character",
            message: `Đạo diễn đọc được ${sbInfo.scenes.length} cảnh, nhưng CẦN 1 ảnh nhân vật gốc (${sbInfo.character.appearance ? sbInfo.character.appearance.slice(0, 90) : "nhân vật chính"}) để giữ NHẤT QUÁN mọi cảnh. Hãy upload ảnh nhân vật, hoặc chọn "Sinh diễn viên mới".`,
            title: sbInfo.title, scenes: sbInfo.scenes, character: sbInfo.character, log,
          })
        }
      } else if (!script) {
        return res.status(422).json({ error: "Không đọc được ảnh (model vision chưa sẵn sàng) và chưa có kịch bản. Hãy gõ kịch bản, hoặc bật WORKFLOW_AI_VISION_MODEL (vd qwen2.5vl:7b).", log })
      }
    }
    if (!script) return res.status(400).json({ error: "Thiếu kịch bản hoặc ảnh storyboard" })
    // needImage: cần người + user chọn TỰ đưa ảnh nhưng chưa đưa → round-trip hỏi ảnh (FE có sẵn flow needModel).
    if (personMode !== "off" && modelStrategy === "own" && !assetToInputConfig(modelAssetEff)) {
      return res.json({ needImage: "character", message: "Bạn chọn dùng ảnh người mẫu của mình nhưng chưa upload. Hãy upload 1 ảnh người mẫu rõ mặt, hoặc chọn \"Sinh diễn viên mới\".", title: "", scenes: [], character: { present: true, appearance: "" }, log })
    }

    // 2) PROFILE sản phẩm: vision → fallback chữ. Vision GHI ĐÈ lựa chọn phỏng vấn (chống "giày → phim nước hoa").
    let profile = productAsset ? await analyzeProduct(productAsset, log) : null
    if (!profile) {
      profile = profileFromText(script, answers)
      if (profile) log.push(`profile từ CHỮ: ${profile.category} · ${profile.physicality}${profile.garment ? " (quần áo)" : ""}`)
    }
    const warning = (productAsset && !profile?.fromVision)
      ? "⚠️ Chưa phân tích được ẢNH SẢN PHẨM (model vision chưa sẵn sàng) — đạo diễn chỉ dựa vào chữ trong kịch bản. Hãy ghi RÕ tên/loại sản phẩm, hoặc nhờ admin bật model vision (WORKFLOW_AI_VISION_MODEL, vd qwen2.5vl:7b)."
      : ""

    // 3) STORYBOARD: script là JSON scenes sẵn → dùng luôn; không → 1 LLM call; fail → template deterministic.
    const targetSec = parseTargetSeconds(script)
    let sb = normalizeExternalStoryboard(script, { profile, personMode, maxScenes })
    if (sb) log.push(`storyboard: dùng JSON có sẵn trong kịch bản (${sb.scenes.length} cảnh, bỏ qua LLM)`)
    if (!sb) {
      try {
        const r = await buildStoryboard({ script, answers, profile, characterHint, maxScenes, targetSec, ending, personMode, aspectRatio, model, think, log })
        model = r.model || model
        if (r.scenes.length) sb = { meta: r.meta, scenes: r.scenes }
      } catch (e) { log.push(`storyboard LLM lỗi: ${e?.message || e}`) }
    }
    if (!sb || !sb.scenes.length) {
      log.push("→ fallback template deterministic (không cần LLM)")
      sb = templateStoryboard({ profile, ending, maxScenes, personMode, answers })
    }
    distributeSceneDurations(sb.scenes, targetSec, log)

    // 4) COMPILE (deterministic — không guard chặn).
    const graph = compile({
      scenes: sb.scenes, meta: sb.meta, profile,
      productAsset, modelAsset: modelAssetEff, modelStrategy, personMode,
      voice, subtitle: wantSub, aspectRatio, ending, characterHint, log,
    })
    log.push("Ollama unload trước khi trả graph cho frontend")
    await ollamaUnload(model)
    return res.json({ nodes: graph.nodes, edges: graph.edges, model, log, warning: warning || undefined })
  } catch (e) {
    return res.status(422).json({ error: e?.message || String(e), log })
  } finally {
    await ollamaUnload(model) // luôn nhả VRAM dù thành công hay lỗi
  }
})

router.post("/workflows/interview-script", sessionAuth, async (req, res) => {
  const script = String(req.body?.script || "").trim()
  if (!script) return res.status(400).json({ error: "Thiếu kịch bản" })
  const think = req.body?.think !== false
  const hasProductImage = req.body?.hasProductImage === true
  const hasModelImage = req.body?.hasModelImage === true
  let model = DEFAULT_MODEL
  const log = []
  try {
    let questions
    try {
      questions = await interviewQuestions({ script, model, think, hasProductImage, hasModelImage, log })
    } catch (e) {
      if (e?.modelMissing && model !== FALLBACK_MODEL) { model = FALLBACK_MODEL; questions = await interviewQuestions({ script, model, think, hasProductImage, hasModelImage, log }) }
      else throw e
    }
    return res.json({ questions, model, log })
  } catch (e) {
    // Lỗi vẫn trả bộ chuẩn để FE không kẹt.
    return res.json({ questions: baseQuestions({ hasProductImage }), model, log: [...log, `interview lỗi: ${e?.message || e}`] })
  } finally {
    await ollamaUnload(model)
  }
})

export default router
// #endregion
