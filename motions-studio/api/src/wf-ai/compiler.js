// #region ALD 09/07/2026 - wf-ai/compiler.js: tầng COMPILER — scenes (storyboard) → graph node, JS THUẦN deterministic.
// Thay tool-loop 48 vòng + guard "bắt buộc người+SP" của bản cũ. Recipe tra theo (usesProductHow/physicality ×
// needsPerson × assets có gì) — KHÔNG bao giờ chặn: thiếu người → b-roll; thiếu ảnh SP → create-image từ profile.
// role "compare" → 2 nhánh before/after → node reveal (đè lộ) — chuẩn review TRƯỚC/SAU.
import { createBuilder } from "./builder.js"
import { PHOTOREAL_TAGS, ANTI_CARTOON_NEGATIVE, DEFAULT_AD_NEGATIVE, BROLL_PATTERNS, IDENTITY_NEGATIVE, PRODUCT_FIDELITY_NEGATIVE, placementFor, composeCinematicPrompt, brollStyleFor, FILM_QUALITY_TAGS } from "./recipes.js"

// asset FE = object {kind,url,path,bucket,...} HOẶC chuỗi URL (contract cũ — giữ nguyên).
export function assetToInputConfig(asset) {
  if (typeof asset === "string" && /^https?:\/\//i.test(asset.trim())) return { source: "url", url: asset.trim() }
  if (!asset || typeof asset !== "object") return null
  const url = String(asset.url || asset.signedUrl || "").trim()
  const path = String(asset.path || asset.staticPath || "").trim()
  const bucket = String(asset.bucket || asset.staticBucket || "").trim()
  const name = String(asset.name || asset.staticName || "asset").slice(0, 120)
  const mime = String(asset.mime || asset.mimeType || asset.staticMime || "").slice(0, 80)
  if (path) return { source: "static", staticPath: path, staticBucket: bucket || "chat-attachments", staticName: name, staticMime: mime, staticUrl: url, staticData: "" }
  if (url) return { source: "url", url }
  return null
}

// Sinh diễn viên photoreal (tái dùng bản cũ; useModelStandard = tham chiếu kho model_refs cho mặt đẹp).
export function actorGenConfig(gender, ageGroup) {
  const g = gender === "male" ? "male" : "female"
  const a = ageGroup === "old" ? "mature" : ageGroup === "middle" ? "middle-aged" : "young"
  return {
    provider: "qwen", model: "qwen-edit", imageMode: "generate", promptMode: "text",
    prompt: `${a} ${g} Vietnamese model, ${PHOTOREAL_TAGS}, clean studio background, professional commercial portrait, confident natural pose, soft studio lighting`,
    negativePrompt: `${ANTI_CARTOON_NEGATIVE}, deformed, bad anatomy, low quality, text, watermark, logo`,
    inputCount: 0, outputCount: 1, aspectRatio: "auto", quality: "standard", useModelStandard: true, modelStandardPreset: g, gender: g, age_group: ageGroup, _role: "actor", _gen: "script",
  }
}

const clampDur = (n, d = 4) => Math.max(3, Math.min(10, Number(n) || d))

// Ép cảnh cuối đúng KIỂU KẾT user chọn (LLM chỉ viết lời — không được đổi kiểu kết).
function enforceEnding(scenes, ending, personOn) {
  if (!scenes.length) return
  const last = scenes[scenes.length - 1]
  if (ending === "cta-buy" || ending === "cta-follow") {
    last.role = "cta"
    if (personOn && last.needsPerson) last.talkToCamera = true
    if (!last.voiceoverVi) last.voiceoverVi = ending === "cta-buy" ? "Nếu bạn quan tâm, inbox mình để được tư vấn chi tiết nhé." : "Thấy hữu ích thì follow mình để xem thêm review nhé."
  } else if (last.role === "cta") {
    last.role = "verdict" // review khách quan: không để LLM lỡ chèn CTA
  }
}

export function compile({ scenes, meta = {}, profile = null, productAsset = null, modelAsset = null, modelStrategy = "library", personMode = "on", voice = "", subtitle = false, aspectRatio = "9:16", ending = "review-verdict", characterHint = "", log = [] }) {
  if (!Array.isArray(scenes) || !scenes.length) throw new Error("storyboard rỗng — không có cảnh để dựng")
  const builder = createBuilder({ aspectRatio })
  builder.setBrief({ look: meta.look, palette: meta.palette, mood: meta.mood, lens: meta.lens, lighting: meta.lighting })
  const brief = builder.getBrief()
  const voicePick = String(voice || "vixtts")
  const placement = placementFor(profile)
  const needsAnyPerson = personMode !== "off" && scenes.some((s) => s.needsPerson)

  // ── NGUỒN ──
  let productId = null
  if (assetToInputConfig(productAsset)) {
    productId = builder.addNode("input-image", { label: "Ảnh sản phẩm", field: "product_image" }, "Ảnh sản phẩm").node_id
  }
  let personId = null
  if (needsAnyPerson) {
    if (modelStrategy === "own") personId = builder.addNode("input-image", { label: "Ảnh người mẫu", field: "model_image" }, "Ảnh người mẫu").node_id
    else if (modelStrategy === "generate") {
      personId = builder.addNode("create-image", {}, "Sinh diễn viên").node_id
      // config actor đầy đủ (đè defaults create-image) — post-pass bên dưới gán qua graph.nodes.
    } else personId = builder.addNode("cast-model", { gender: meta.personaGender, ageGroup: meta.personaAge }, "Người mẫu (kho)").node_id
  }
  enforceEnding(scenes, ending, !!personId)

  // ── keyframe cho 1 cảnh (promptOverride cho compare before/after) ──
  function keyframeFor(scene, promptOverride) {
    const desc = String(promptOverride || scene.promptEn || "").trim()
    if (scene.needsPerson && personId) {
      if (productId) {
        // R1: quần áo + mặc → tryon (giữ identity tốt nhất). Còn lại R2-R7: edit-image GHÉP theo placement.
        if (profile?.garment && scene.usesProductHow === "wear") {
          const tid = builder.addNode("tryon", { garmentType: "auto" }, `Thử đồ · ${scene.titleVi}`.slice(0, 48)).node_id
          builder.mustConnect(personId, tid, "model")
          builder.mustConnect(productId, tid, "product")
          return tid
        }
        const fid = builder.addNode("edit-image", {
          combine: true,
          prompt: [desc, placement.prompt].filter(Boolean).join(". "),
          negativePrompt: placement.negativePrompt,
        }, `${placement.label} · ${scene.titleVi}`.slice(0, 48)).node_id
        builder.mustConnect(personId, fid, "image1")
        builder.mustConnect(productId, fid, "image2")
        return fid
      }
      // R9: có người, không ảnh SP → đặt người vào bối cảnh cảnh (giữ identity).
      const fid = builder.addNode("edit-image", {
        prompt: `Keep this exact person — same face, hairstyle, body and outfit — and place them naturally into the scene, full body, cinematic.${desc ? " " + desc : ""}${characterHint ? ` Character reference: ${characterHint}.` : ""}`.trim(),
        negativePrompt: `${IDENTITY_NEGATIVE}, restyled hair, different hair color, deformed, extra limbs, low quality, blurry`,
      }, `Cảnh · ${scene.titleVi}`.slice(0, 48)).node_id
      builder.mustConnect(personId, fid, "image1")
      return fid
    }
    // Không người:
    if (productId) {
      // R10: b-roll từ ẢNH SP thật (re-stage hero/macro/fresh/luxury) — giữ đúng bao bì.
      const style = BROLL_PATTERNS[scene.brollStyle] || BROLL_PATTERNS[brollStyleFor(scene.role, 0)] || BROLL_PATTERNS.hero
      const pName = profile?.nameEn || "the product"
      const fid = builder.addNode("edit-image", {
        combine: false,
        prompt: style.image(pName) + (profile?.details ? ` Key details to preserve exactly: ${profile.details}.` : "") + (promptOverride ? ` ${promptOverride}` : ""),
        negativePrompt: `${PRODUCT_FIDELITY_NEGATIVE}, hands, person, text, watermark, blurry, low quality`,
      }, `B-roll · ${scene.titleVi}`.slice(0, 48)).node_id
      builder.mustConnect(productId, fid, "image1")
      fidStyle.set(fid, style)
      return fid
    }
    // R11/R12: không người + không ảnh SP → create-image từ profile/promptEn (digital = UI mockup/lifestyle).
    const base = profile?.nameEn ? `${profile.nameEn}, ` : ""
    const digital = profile?.physicality === "digital"
    const fid = builder.addNode("create-image", {
      prompt: `${digital ? "clean modern UI mockup / lifestyle shot of " : "premium commercial product photography of "}${base}${desc || profile?.category || "the product"}. ${FILM_QUALITY_TAGS}`,
      negativePrompt: `${ANTI_CARTOON_NEGATIVE}, ${digital ? "garbled UI text, unreadable screen, " : ""}text artifacts, watermark, low quality`,
      aspectRatio,
    }, `Ảnh · ${scene.titleVi}`.slice(0, 48)).node_id
    return fid
  }
  const fidStyle = new Map() // keyframe b-roll → BROLL pattern (để prompt video khớp ảnh)

  // ── animate 1 keyframe → clip video ──
  function animate(scene, frameId, dur, promptOverride) {
    const style = fidStyle.get(frameId)
    const prompt = style
      ? `${style.video}. ${FILM_QUALITY_TAGS}`
      : composeCinematicPrompt({ brief, shot: { action: String(promptOverride || scene.promptEn || ""), shotType: scene.shotType, cameraMove: scene.cameraMove, lighting: scene.lighting }, placement: scene.needsPerson && personId && productId ? placement : null, keepIdentity: !!(scene.needsPerson && personId) })
    const clipId = builder.addNode("wan-i2v", { wanModel: "wan2.2", duration: dur, aspectRatio, prompt, negativePrompt: DEFAULT_AD_NEGATIVE }, scene.titleVi.slice(0, 48)).node_id
    builder.mustConnect(frameId, clipId, "start")
    return clipId
  }

  // ── dựng từng cảnh ──
  const tails = []
  scenes.forEach((scene, i) => {
    const dur = clampDur(scene.durationSec)
    const vdir = {}
    if (scene.emotion) vdir.emotion = scene.emotion
    if (scene.pace) vdir.pace = scene.pace

    // R13: compare → 2 nhánh before/after → reveal → voiceover.
    if (scene.role === "compare" && scene.compare) {
      const kfA = keyframeFor(scene, scene.compare.beforeEn)
      const kfB = keyframeFor(scene, scene.compare.afterEn)
      const half = clampDur(Math.round(dur / 2) || 3, 3)
      const clipA = animate(scene, kfA, half, scene.compare.beforeEn)
      const clipB = animate(scene, kfB, half, scene.compare.afterEn)
      const revId = builder.addNode("reveal", { revealMode: "slider", direction: "down", showLine: true, sweepDuration: 1 }, `Trước/Sau · ${scene.titleVi}`.slice(0, 44)).node_id
      builder.mustConnect(clipA, revId, "base")
      builder.mustConnect(clipB, revId, "reveal")
      let tail = revId
      if (scene.voiceoverVi) {
        const voId = builder.addNode("voiceover", { script: scene.voiceoverVi, voice: voicePick, mix: "replace", ...vdir }, `VO · ${scene.titleVi}`.slice(0, 48)).node_id
        builder.mustConnect(tail, voId)
        tail = voId
      }
      tails.push(tail)
      log.push(`cảnh ${i + 1} "${scene.titleVi}" → compare (2 nhánh + reveal)${scene.voiceoverVi ? "+voice" : ""}`)
      return
    }

    const frameId = keyframeFor(scene)
    // R8: talk (nhìn camera nói) — chỉ khi có keyframe NGƯỜI; thoại nằm trong talk, không voiceover.
    if (scene.talkToCamera && scene.needsPerson && personId && scene.voiceoverVi) {
      const talkId = builder.addNode("talk", {
        line: scene.voiceoverVi,
        action: [String(scene.promptEn || "").trim(), placement.talkPrompt].filter(Boolean).join(". "),
        voice: voicePick, fps: 25, ...vdir,
      }, `Nói · ${scene.titleVi}`.slice(0, 48)).node_id
      builder.mustConnect(frameId, talkId)
      tails.push(talkId)
      log.push(`cảnh ${i + 1} "${scene.titleVi}" → talk (lip-sync)`)
      return
    }
    const clipId = animate(scene, frameId, dur)
    let tail = clipId
    if (scene.voiceoverVi) {
      const voId = builder.addNode("voiceover", { script: scene.voiceoverVi, voice: voicePick, mix: "replace", ...vdir }, `VO · ${scene.titleVi}`.slice(0, 48)).node_id
      builder.mustConnect(tail, voId)
      tail = voId
    }
    tails.push(tail)
    log.push(`cảnh ${i + 1} "${scene.titleVi}" → ${scene.needsPerson && personId ? (productId ? (profile?.garment && scene.usesProductHow === "wear" ? "tryon" : "ghép SP") : "người+cảnh") : (productId ? "b-roll SP" : "create-image")} · wan-i2v ${dur}s${scene.voiceoverVi ? "+voice" : ""}`)
  })

  // ── concat (cap 8 clip / node — dư thì gộp 2 tầng, bản cũ chưa xử lý) ──
  function concatAll(list) {
    if (list.length <= 1) return list[0] || null
    if (list.length <= 8) {
      const cid = builder.addNode("concat", {}, "Ghép cảnh").node_id
      list.forEach((id) => builder.mustConnect(id, cid, "clip"))
      return cid
    }
    const groups = []
    for (let i = 0; i < list.length; i += 8) groups.push(list.slice(i, i + 8))
    const gids = groups.map((g, gi) => {
      const cid = builder.addNode("concat", {}, `Ghép phần ${gi + 1}`).node_id
      g.forEach((id) => builder.mustConnect(id, cid, "clip"))
      return cid
    })
    return concatAll(gids)
  }
  let finalTail = concatAll(tails)

  // Phụ đề: 1 node cho CẢ PHIM sau concat (opt-in) — hết cảnh nào cũng 1 node sub như bản cũ.
  if (subtitle && finalTail) {
    const subId = builder.addNode("subtitle", {}, "Phụ đề").node_id
    builder.mustConnect(finalTail, subId)
    finalTail = subId
  }

  const graph = builder.finalize()

  // ── post-pass: prefill asset + config actor (graph.nodes là canvas-shape, mutate trực tiếp) ──
  const findInput = (field) => graph.nodes.find((n) => n?.data?.type === "input" && String(n.data.config?.field || "") === field)
  const prodCfg = assetToInputConfig(productAsset)
  if (prodCfg) {
    const p = findInput("product_image")
    if (p) { Object.assign(p.data.config, prodCfg); log.push(`asset: nạp sẵn sản phẩm (${prodCfg.source})`) }
  }
  if (modelStrategy === "own") {
    const mCfg = assetToInputConfig(modelAsset)
    const m = findInput("model_image")
    if (mCfg && m) { Object.assign(m.data.config, mCfg); log.push(`asset: nạp sẵn người mẫu (${mCfg.source})`) }
  } else if (modelStrategy === "generate" && personId) {
    const actor = graph.nodes.find((n) => n.id === personId)
    if (actor) { actor.data.config = actorGenConfig(meta.personaGender, meta.personaAge); log.push("nguồn người: SINH diễn viên mới (create-image actor, photoreal)") }
  }
  log.push(`compiler: ${graph.nodes.length} node · ${graph.edges.length} cạnh · ${tails.length} cảnh`)
  return graph
}
// #endregion
