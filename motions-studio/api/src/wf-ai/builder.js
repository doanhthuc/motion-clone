// #region ALD 09/07/2026 - wf-ai/builder.js: dựng graph an toàn (port-aware, validate mirror engine, BFS layout).
// Tái dùng gần nguyên createBuilder từ routes/workflow-ai.js cũ (đã chạy ổn) + THÊM port cho reveal (base|reveal)
// và ss (input→image2→image3). Node shape = CANVAS-shape FE addNodes thẳng: {id, type:'step', position, data:{type, config}}.
// Mọi config tự gắn _gen:'script' (FE "Dựng lại từ kịch bản" xoá node cũ theo marker này).
import { NODE_SPECS, MULTI_INPUT_TYPES, ENUM_TYPES, resolveSpecKey } from "./specs.js"

// Layout canvas (khớp FE buildMultiOutfitMotion).
const COL = 320, ROW = 180, X0 = 80, Y0 = 80

export function createBuilder({ aspectRatio }) {
  const items = new Map() // id → { node, key, spec, incoming, outgoing:[], clips, images, products }
  let nseq = 0, eseq = 0
  const nodes = [], edges = []
  let brief = {}
  const sources = {} // role ('model'|'product') → node_id

  function addNode(rawType, params = {}, label) {
    const key = resolveSpecKey(rawType)
    if (!key) throw new Error(`type "${rawType}" không hợp lệ (chỉ: ${ENUM_TYPES.join(", ")})`)
    const spec = NODE_SPECS[key]
    const id = `gen-${spec.type}-${++nseq}`
    const config = { ...spec.defaults(params || {}, { aspectRatio }), _gen: "script" }
    if (label && spec.type !== "input") config.label = String(label).slice(0, 60)
    const node = { id, type: "step", position: { x: 0, y: 0 }, data: { type: spec.type, config } }
    nodes.push(node)
    items.set(id, { id, node, key, spec, incoming: 0, outgoing: [], clips: 0, images: 0, products: 0 })
    if (spec.type === "cast-model") sources.model = id
    else if (spec.type === "input" && config.field === "model_image") sources.model = id
    else if (spec.type === "input" && config.field === "product_image") sources.product = id
    return { node_id: id }
  }

  // Trả targetHandle hợp lệ cho cạnh from→to (logic port khớp FlowNode.vue *_TARGETS).
  function resolvePort(t, port) {
    const spec = t.spec, p = String(port || "").toLowerCase().trim()
    if (!spec.multiInput) return { targetHandle: spec.prevPort }
    switch (spec.type) {
      case "motion":
        if (p === "image" || p === "ref" || p === "anh") return { targetHandle: "image" }
        if (p === "motion" || p === "driver" || p === "video") return { targetHandle: "motion" }
        if (p === "audio") return { targetHandle: "audio" }
        return { error: "motion cần port 'image' (ảnh người mẫu) hoặc 'motion' (video driver)" }
      case "tryon":
        // ALD 20/07/2026 - Try-On tối giản: đúng 1 model + 1 product (bỏ đa-góc, background).
        if (p === "model" || p === "" || p === "image") return { targetHandle: "model" }
        if (p.startsWith("product")) { if (t.products >= 1) return { error: "tryon chỉ nhận 1 ảnh sản phẩm" }; t.products++; return { targetHandle: "product" } }
        return { error: "tryon cần port 'model' (người mẫu) hoặc 'product' (sản phẩm)" }
      case "create-image":
        if (t.images >= 6) return { error: "create-image tối đa 6 ảnh tham chiếu" }
        t.images++; t.node.data.config.inputCount = t.images; return { targetHandle: `image${t.images}` }
      case "compose":
        if (t.images >= 3) return { error: "compose tối đa 3 ảnh (1 mẫu + 2 đối tượng)" }
        t.images++
        t.node.data.config.personCount = Math.max(1, t.images - 1)
        return { targetHandle: `image${t.images}` }
      case "edit-image":
        if (t.images >= 6) return { error: "edit-image tối đa 6 ảnh" }
        t.images++; t.node.data.config.inputCount = t.images
        if (t.images >= 2) t.node.data.config.combine = true
        return { targetHandle: `image${t.images}` }
      case "wan-i2v":
        if (p === "end" || p === "image2" || p === "last") { t.node.data.config.endEnabled = true; return { targetHandle: "end" } }
        return { targetHandle: "start" }
      // ALD 09/07/2026 - reveal (Đè lộ): 2 cổng cố định base (video nền) + reveal (video lộ dần). Khớp REVEAL_TARGETS.
      case "reveal":
        if (p === "reveal" || p === "b" || p === "after") return { targetHandle: "reveal" }
        if (p === "base" || p === "a" || p === "before" || p === "") return { targetHandle: "base" }
        return { error: "reveal cần port 'base' (nền/before) hoặc 'reveal' (lộ dần/after)" }
      // ALD 09/07/2026 - ss (LTX I2V): cổng tuần tự input → image2 → image3 (khớp SS_TARGETS I2V 1-3 ảnh).
      case "ss":
        if (t.images >= 3) return { error: "ss tối đa 3 ảnh" }
        t.images++; t.node.data.config.inputCount = t.images
        return { targetHandle: t.images === 1 ? "input" : `image${t.images}` }
      case "concat":
        if (p === "audio") { t.node.data.config.audioMode = "source"; return { targetHandle: "audio" } }
        if (t.clips >= 8) return { error: "concat tối đa 8 clip" }
        t.clips++; t.node.data.config.clipCount = Math.max(2, t.clips); return { targetHandle: `clip${t.clips}` }
    }
    return { error: `node ${spec.type} không nhận input` }
  }

  function connect(from, to, port) {
    const f = items.get(from), t = items.get(to)
    if (!f) return { error: `node "${from}" chưa tồn tại` }
    if (!t) return { error: `node "${to}" chưa tồn tại` }
    if (from === to) return { error: "không thể nối node vào chính nó" }
    if (t.spec.type === "input" || t.spec.type === "cast-model") return { error: `node "${to}" (${t.spec.type}) là nguồn — không nhận input` }
    if (!t.spec.multiInput && t.incoming >= 1) return { error: `node "${to}" (${t.spec.type}) chỉ nhận 1 input` }
    const { targetHandle, error } = resolvePort(t, port)
    if (error) return { error }
    edges.push({ id: `gen-e-${++eseq}`, source: from, target: to, sourceHandle: undefined, targetHandle, data: {} })
    t.incoming++; f.outgoing.push(to)
    return { ok: true, port: targetHandle ?? "default" }
  }

  // Nối "bắt buộc thành công" — compiler deterministic dùng thay connect (lỗi = bug compiler, throw luôn).
  function mustConnect(from, to, port) {
    const r = connect(from, to, port)
    if (r.error) throw new Error(`compiler nối ${from} → ${to}${port ? ` (${port})` : ""} lỗi: ${r.error}`)
    return r
  }

  // Hoàn thiện: bảo đảm có output nối vào sink + validate mirror engine + BFS layout.
  function finalize() {
    if (!nodes.length) throw new Error("AI chưa dựng được node nào")
    const all = [...items.values()]
    let outItem = all.find((i) => i.spec.type === "output")
    if (!outItem) outItem = items.get(addNode("output", {}, "Kết quả").node_id)
    if (outItem.incoming === 0) {
      const concat = all.find((i) => i.spec.type === "concat" && i.outgoing.length === 0)
      const videoTails = all.filter((i) => i.spec.output === "video" && i.outgoing.length === 0 && i.id !== outItem.id)
      let sinkId
      if (concat) sinkId = concat.id
      else if (videoTails.length === 1) sinkId = videoTails[0].id
      else if (videoTails.length > 1) {
        sinkId = addNode("concat", {}, "Ghép cảnh").node_id
        for (const v of videoTails) { const r = connect(v.id, sinkId, "clip"); if (r.error) throw new Error(`Tự ghép concat lỗi: ${r.error}`) }
      } else { const anyTail = all.find((i) => i.outgoing.length === 0 && i.id !== outItem.id); sinkId = anyTail?.id }
      if (sinkId) { const r = connect(sinkId, outItem.id); if (r.error) throw new Error(`Nối output lỗi: ${r.error}`) }
    }
    // validate mirror engine: không cạnh treo, 1-input rule, root rule.
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const incoming = new Map(nodes.map((n) => [n.id, 0])), outgoing = new Map(nodes.map((n) => [n.id, []]))
    for (const e of edges) {
      if (!byId.has(e.source) || !byId.has(e.target)) throw new Error(`Cạnh ${e.id} trỏ node không tồn tại`)
      incoming.set(e.target, incoming.get(e.target) + 1)
      outgoing.get(e.source).push(e)
    }
    for (const [id, c] of incoming) if (c > 1 && !MULTI_INPUT_TYPES.has(byId.get(id).data.type)) throw new Error(`Node ${byId.get(id).data.type} nhận ${c} input — chỉ node đa-input mới được`)
    const roots = nodes.filter((n) => incoming.get(n.id) === 0 && outgoing.get(n.id).length > 0)
    if (!roots.length) throw new Error("Graph không có node bắt đầu (cycle?)")
    if (roots.length > 1) {
      const bad = roots.find((r) => outgoing.get(r.id).some((e) => !MULTI_INPUT_TYPES.has(byId.get(e.target).data.type)))
      if (bad) throw new Error(`Có nhiều node bắt đầu nhưng "${bad.data.config?.label || bad.data.type}" nối tới node 1-input — hãy cho input đi qua node đa-input trước`)
    }
    // BFS layering → position.
    const depth = new Map(nodes.map((n) => [n.id, 0]))
    for (let pass = 0; pass < nodes.length; pass++) { let ch = false; for (const e of edges) { const d = depth.get(e.source) + 1; if (d > depth.get(e.target)) { depth.set(e.target, d); ch = true } } if (!ch) break }
    const rowByCol = new Map()
    for (const n of nodes) { const d = depth.get(n.id); const r = rowByCol.get(d) || 0; rowByCol.set(d, r + 1); n.position = { x: X0 + d * COL, y: Y0 + r * ROW } }
    return { nodes, edges }
  }

  const setBrief = (b) => { brief = { ...brief, ...(b && typeof b === "object" ? b : {}) } }
  const getBrief = () => brief
  const setSource = (role, id) => { if (role && id && items.has(id)) sources[role] = id }
  const getSource = (role) => sources[role] || null
  return { addNode, connect, mustConnect, finalize, count: () => nodes.length, setBrief, getBrief, setSource, getSource, has: (id) => items.has(id), typeOf: (id) => items.get(id)?.spec?.type || null }
}
// #endregion
