// #region ALD 09/07/2026 - wf-ai/recipes.js: vocab prompt điện ảnh + bảng PLACEMENT theo physicality + negative locks.
// Tái dùng nguyên khối prompt đã tinh chỉnh qua lỗi thật của bản cũ (vehicle/large/worn/tabletop/handheld,
// BROLL_PATTERNS, composeCinematicPrompt); VIẾT MỚI consumable + digital; negative lock generic 1 NƠI DUY NHẤT
// (bỏ bias "serum bottle/cosmetic" của bản cũ).

// ── Vocab chất lượng phim (giữ nguyên bản cũ) ──
export const PHOTOREAL_TAGS = "photorealistic, real human, lifelike natural skin texture with visible pores, realistic subsurface skin, natural imperfections, shot on real camera, documentary realism"
export const FILM_QUALITY_TAGS = `${PHOTOREAL_TAGS}, cinematic commercial film look, sharp focus, high detail, professional color grading, shallow depth of field, 35mm photography`
export const ANTI_CARTOON_NEGATIVE = "cartoon, anime, illustration, drawing, painting, 3d render, cgi, render, video game, plastic skin, plastic look, waxy skin, doll, mannequin, figurine, toy, airbrushed, oversmoothed skin, beauty filter, fake looking, uncanny, stylized, cel shaded"

export const SHOT_CAMERA_MOVES = {
  "dolly-in":  "The camera slowly dollies in toward the subject",
  "pull-back": "The camera slowly pulls back, revealing the wider scene",
  "orbit":     "The camera slowly orbits around the subject in a smooth arc, keeping it centered",
  "tracking":  "Tracking shot following the subject as they move",
  "pan-left":  "Slow pan left across the scene",
  "pan-right": "Slow pan right across the scene",
  "crane-up":  "The camera cranes up from a close-up detail to a wider view",
  "static":    "Static camera, locked shot",
  "handheld":  "Handheld camera with subtle shake, documentary style",
}
export const SHOT_TYPES = {
  "extreme-close-up": "extreme close-up, macro detail",
  "close-up":         "close-up, shallow depth of field",
  "medium-close-up":  "medium close-up",
  "medium":           "medium shot, eye level",
  "wide":             "wide establishing shot",
  "hero-low-angle":   "low angle hero shot",
}
export const LIGHTING_MOODS = {
  "studio-premium": "premium studio key lighting with soft rim light, seamless backdrop",
  "golden-hour":    "golden-hour side lighting, warm amber tones, volumetric light",
  "neon":           "soft neon rim light, dark background, 35mm cinematic look",
  "high-key":       "bright high-key daylight, clean fresh commercial look",
  "dark-luxury":    "dark luxury studio, single warm rim light carving the silhouette, deep shadows",
  "window-natural": "soft natural window light, cozy lifestyle tones",
}

// B-ROLL sản phẩm thuần (không người): cặp [ảnh hero re-stage] + [motion 5s Wan I2V]. {P} = nameEn sản phẩm.
export const BROLL_PATTERNS = {
  hero: {
    label: "Hero pedestal · orbit",
    image: (P) => `Restage this product photo as premium commercial product photography: ${P} centered on a matte stone pedestal against a smooth neutral gradient studio background, softbox key light from upper left, soft rim light tracing the edges, subtle floor reflection, 100mm lens look, ultra-sharp, clean negative space above. Keep the product itself EXACTLY as in the photo: same shape, label, logo, colors, proportions.`,
    video: "Low angle hero shot. The camera slowly orbits around the product in a smooth arc, keeping it centered and in crisp focus; glossy reflections glide across the surface; lighting and background remain exactly as shown; slow, elegant, constant speed; no flicker, no warping, logo stays readable",
  },
  macro: {
    label: "Macro texture · push-in",
    image: (P) => `Restage this product photo as an extreme macro close-up of the most distinctive detail of ${P}: fine surface texture, sharp focus, shallow depth of field, 100mm macro look, soft diffused key light with a single hard rim light, dark seamless background, luxurious detail shot. Keep the product's real texture, label, logo and colors EXACTLY as in the photo.`,
    video: "Extreme close-up, macro detail. Slow macro dolly push-in toward the product detail, shallow depth of field with focus locked on the product, subtle parallax only, everything else still, smooth and precise, constant speed, no jitter, no warping",
  },
  fresh: {
    label: "Fresh set · rack focus",
    image: (P) => `Restage this product photo as a fresh commercial set: ${P} on wet dark slate with fine water droplets and condensation, soft backlit haze, crisp reflections, bright high-key studio lighting, clean fresh advertising look. Keep the product EXACTLY as in the photo: same shape, label, logo, colors.`,
    video: "Close-up, shallow depth of field. Gentle rack focus lands on the product; the camera holds still; condensation beads slowly roll down catching the rim light; fine mist drifts through the backlight; only micro-motion, composition unchanged, crisp focus when it lands",
  },
  luxury: {
    label: "Dark luxury · light sweep",
    image: (P) => `Restage this product photo as a dark luxury commercial: ${P} resting on black satin in a dark studio, single warm rim light carving the silhouette, deep shadows, glossy reflections, jewelry-store atmosphere, moody premium grade. Keep the product EXACTLY as in the photo: same shape, label, logo, colors.`,
    video: "Close-up. Camera holds nearly still as a soft beam of warm light slowly sweeps across the product and traces the logo; specular highlights glide over the glossy surface; background stays dark and unchanged; slow hypnotic motion, crisp focus throughout, no flicker, no text, no hands",
  },
}
export const _vocab = (table, key, fallback = "") => table[String(key || "").toLowerCase().trim()] || (String(key || "").trim() || fallback)

// ── Negative locks — 1 NƠI DUY NHẤT, generic mọi ngành hàng (bỏ bias serum/cosmetic của bản cũ) ──
export const IDENTITY_NEGATIVE = "different person, changed face, changed hairstyle, changed outfit"
export const PRODUCT_FIDELITY_NEGATIVE = "wrong product, changed product label, changed logo, fake product packaging, distorted packaging, distorted logo, unreadable label, redesigned product, invented product, duplicated product, multiple copies of the product, extra product, oversized product, giant product, miniature product, toy-sized product, floating sticker, pasted card, wrong scale"
export const DEFAULT_AD_NEGATIVE = `${ANTI_CARTOON_NEGATIVE}, slow motion, slow-mo, slowmo, time freeze, frozen pose, stop motion, stutter, stuttering, jerky motion, motion stall, static frozen subject, robotic movement, finger motion, hand mutation, extra fingers, fused fingers, ${IDENTITY_NEGATIVE}, ${PRODUCT_FIDELITY_NEGATIVE}, blurry, deformed face, text artifacts, low quality, oversaturated, overexposed, washed out, flat lighting, walking backwards, warping, flicker, amateur`

// ── PLACEMENT[physicality]: cách đặt sản phẩm vào khung cùng người — tra TRỰC TIẾP theo profile.physicality
// (hết đoán bằng regex như inferProductPlacement cũ; các khối prompt giữ nguyên vì đã tinh chỉnh qua lỗi thật). ──
const COMMON_PROMPT = [
  "Edit image 1 only as the base photo. Keep the exact same model identity, face, hairstyle, outfit, body proportions, camera angle, background and lighting mood.",
  "Use image 2 as the only approved product reference. Preserve product shape, label layout, logo, color, typography and proportions as much as possible. Do not invent a different product.",
  "Make product contact, scale, shadows and lighting realistic. The result must look physically present, not a sticker and not a separate card.",
]
const COMMON_NEGATIVE = [
  IDENTITY_NEGATIVE, PRODUCT_FIDELITY_NEGATIVE,
  "changed background", "different room", "restaged photo", "blurry", "low quality",
]

function withProductFacts(parts, profile) {
  const out = [...parts]
  if (profile?.nameEn) {
    out.push(`The product in image 2 is: ${profile.nameEn}.`
      + (profile.details ? ` Key details to preserve exactly: ${profile.details}.` : "")
      + (profile.sizeHint ? ` Its real-world size: ${profile.sizeHint} — render it at exactly this scale relative to the model's body, never larger.` : ""))
  }
  return out
}

export function placementFor(profile) {
  const phys = String(profile?.physicality || "tabletop")
  const P = (parts, extraNeg, motion, talkPrompt, label) => ({
    kind: phys, label,
    prompt: withProductFacts(parts, profile).join(" "),
    negativePrompt: [...COMMON_NEGATIVE, ...extraNeg].join(", "),
    motion, talkPrompt,
  })
  if (phys === "large") {
    return P([
      ...COMMON_PROMPT,
      "This is a large product (vehicle, appliance or furniture). Place it at real-world scale beside or behind the model in a showroom, living-room, kitchen, bedroom, street or store context that matches the product.",
      "The model presents it with an open palm, points from a short distance, or naturally rests one hand on a handlebar/seat/door/edge if appropriate. Never put this large product in the model's hand.",
    ], ["product in hand", "holding large product", "tiny appliance", "tiny furniture", "tiny vehicle", "toy-sized appliance", "model carrying product", "deformed hand", "extra fingers"],
    "real-time showroom presentation: model stands beside the large product at correct scale, open-palm gesture toward it or one simple hand resting on it, natural blink and slight body shift, product remains stable and full-size",
    "talking to camera while standing beside the large product, open-palm presentation gesture, natural Vietnamese lip-sync, no holding the product",
    "SP lớn cạnh mẫu")
  }
  if (phys === "wearable") {
    return P([
      ...COMMON_PROMPT,
      "The product is wearable. Show the model actually WEARING/USING it in its correct position on the body: shoes on the feet, hat on the head, glasses on the face, bag on the shoulder, watch/jewelry on the wrist or neck, clothes fitted on the body.",
      "Adjust the pose minimally so wearing looks physically natural at real-world scale, with correct contact and weight. Never paste the product floating on the body, never put worn products in the hand.",
    ], ["floating product", "product pasted on body", "product on shoulder", "product in hand", "wrong body position", "detached product", "deformed feet", "deformed hand", "extra fingers"],
    "real-time lifestyle motion: the model moves naturally while wearing the product (confident walk, light turn, natural arm swing), the worn product stays fixed in its correct position on the body, natural blink and smile, no slow motion",
    "talking to camera while wearing the product, natural Vietnamese lip-sync, confident smile, the worn product clearly visible in its correct position",
    "Mẫu đang dùng SP")
  }
  if (phys === "consumable") {
    // ALD 09/07 - MỚI: đồ ăn/uống — cầm tự nhiên, có cảnh nếm/uống; chống biến dạng đồ ăn + lỗi tay/miệng.
    return P([
      ...COMMON_PROMPT,
      "The product is food or a drink. Show the model holding it naturally (cup, bowl, package) at a cafe table, kitchen counter or picnic setting that matches the product; or mid-tasting with a relaxed, genuine expression.",
      "Keep the food/drink appetizing and physically plausible: correct portion size, natural steam or condensation if hot/cold. Hands simple, at most a light natural grip.",
    ], ["deformed food", "melted food", "unappetizing", "wrong portion size", "extra fingers", "fused fingers", "deformed hand", "distorted mouth", "food floating"],
    "real-time cafe-lifestyle motion: the model lifts the product slightly, smells or tastes it with a genuine delighted expression, natural blink and smile, the product stays intact and appetizing, no slow motion",
    "talking to camera while holding the food or drink naturally, genuine relaxed smile, natural Vietnamese lip-sync, product stays intact and clearly visible",
    "Mẫu thưởng thức SP")
  }
  if (phys === "digital") {
    // ALD 09/07 - MỚI: app/phần mềm/dịch vụ — hiển thị trên màn hình điện thoại/laptop; chống chữ UI vỡ.
    return P([
      "Edit image 1 only as the base photo. Keep the exact same model identity, face, hairstyle, outfit, body proportions, camera angle, background and lighting mood.",
      "Use image 2 as the only approved reference for the app/service interface. The model holds a modern smartphone (or sits at a laptop) with this interface clearly visible on the screen, at a natural viewing angle.",
      "Screen content should look clean and plausible; do not invent detailed UI text. The device is at real-world scale in the model's hand or on the desk.",
    ], ["garbled UI text", "unreadable screen", "broken interface", "wrong app", "extra fingers", "fused fingers", "deformed hand", "floating phone"],
    "real-time tech-lifestyle motion: the model glances at the screen then back to camera with a satisfied nod, light thumb scroll, natural blink, screen stays bright and stable, no slow motion",
    "talking to camera while holding the phone with the app visible, natural Vietnamese lip-sync, confident smile, screen stays bright and readable",
    "Mẫu dùng app/dịch vụ")
  }
  if (phys === "tabletop") {
    return P([
      ...COMMON_PROMPT,
      "Place the product naturally on a table, counter, shelf, stand or display surface near the model. The model gestures toward it with an open palm or lightly touches the table.",
      "Do not force the model to hold the product unless it is clearly small and hand-held. Keep hands simple and away from product edges.",
    ], ["awkward hand grip", "hand wrapped around product", "extra fingers", "missing fingers", "fused fingers", "deformed hand"],
    "real-time tabletop product presentation: product sits on a counter or display stand near the model, model gestures open palm toward it, natural blink, slight head turn, no slow motion",
    "talking to camera next to tabletop product, open-palm gesture toward product, natural Vietnamese lip-sync, product stays on table or display stand",
    "SP trên bàn/kệ")
  }
  // handheld (mặc định cuối)
  return P([
    ...COMMON_PROMPT,
    "The product is small enough to be hand-held. Put the real product in a simple presentation grip near the model's chest or face like a premium ad.",
    "The product should mostly cover the hand. Show at most one thumb and a few partial fingertips. Do not create complex visible fingers and do not wrap fingers around the bottle/box.",
  ], ["extra fingers", "missing fingers", "fused fingers", "mutated fingers", "deformed fingers", "deformed hand", "hand wrapped around product", "bad anatomy"],
  "real-time natural product presentation: model holds the small product steady near chest or face with a simple hidden-finger grip, blinks naturally, relaxed smile, product remains stable and readable, no complex finger motion",
  "natural Vietnamese lip-sync at normal speaking speed, confident smile, direct-to-camera delivery, small product held steady with simple hidden-finger grip, no slow motion",
  "Mẫu cầm SP thật")
}

// ── composeCinematicPrompt (giữ nguyên công thức Veo-3-style của bản cũ) ──
export function composeCinematicPrompt({ brief = {}, shot = {}, placement = null, extraBeats = "", keepIdentity = true }) {
  const parts = []
  const shotAnchor = _vocab(SHOT_TYPES, shot.shotType, "") || String(shot.camera || "").trim() || "medium shot, eye level"
  const lens = String(shot.lens || brief.lens || "").trim()
  parts.push([shotAnchor, lens].filter(Boolean).join(", "))
  const action = String(shot.action || "").trim()
  const beats = String(extraBeats || placement?.motion || "").trim()
  const actionLine = [action, beats].filter(Boolean).join("; ")
  if (actionLine) parts.push(actionLine)
  if (keepIdentity) parts.push("keep the exact same person identity, face, hairstyle, outfit, and the exact same product packaging and label as the provided keyframe")
  const camMove = _vocab(SHOT_CAMERA_MOVES, shot.cameraMove, "") || String(shot.camera || "").trim()
  parts.push((camMove || "The camera slowly dollies in toward the subject") + ", constant speed")
  const lightLine = [_vocab(LIGHTING_MOODS, shot.lighting, "") || String(brief.lighting || "").trim(), String(shot.mood || brief.mood || "").trim()].filter(Boolean).join(", ")
  parts.push([lightLine, "lighting and composition remain exactly as shown"].filter(Boolean).join("; "))
  const look = String(brief.look || "").trim()
  const palette = String(brief.palette || "").trim()
  const lookLine = [look, palette ? `color palette: ${palette}` : ""].filter(Boolean).join(", ")
  if (lookLine) parts.push(lookLine)
  parts.push(FILM_QUALITY_TAGS)
  parts.push("natural real-time speed, brisk decisive lively movement, full natural body motion, normal playback speed, crisp focus throughout, no flicker, no warping, NO slow motion, NO slow-mo, no frozen pose, no time freeze, no stutter, no stop motion")
  return parts.filter(Boolean).join(". ")
}

// Xoay vòng phong cách b-roll theo role cảnh (hook=hero, detail=macro…) — deterministic.
export function brollStyleFor(role, index) {
  const map = { hook: "hero", unbox: "fresh", detail: "macro", proof: "macro", verdict: "luxury", cta: "hero" }
  if (map[role]) return map[role]
  const cycle = ["hero", "macro", "fresh", "luxury"]
  return cycle[Math.max(0, index) % cycle.length]
}
// #endregion
