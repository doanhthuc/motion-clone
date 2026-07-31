// #region ALD 31/05/2026 - Workflow engine (port từ _shared/workflow-engine.ts).
// Chạy graph Vue-Flow: walk từ root theo edges, branching condition/error, fan-in multi-input.
// Persist events/output vào workflow_runs (pg) thay vì Supabase client.
import { query } from "../db.js"
import { NODE_HANDLERS } from "./handlers.js"

// ALD 03/06/2026 - thêm 'lookbook' (model+product) + 'compose' (ảnh mẫu + người) — node đa-input.
// ALD 03/06/2026 - 'concat' (ghép ≥2 phân cảnh/clip) là node đa-input (nhận clip1, clip2, …).
// ALD 15/06/2026 - "ss" thêm vào: node SS cổng vào động 1–3 ảnh (model động) → cho phép >1 input.
// ALD 22/06/2026 - "wan-i2v" thêm vào: node Ảnh→Video có thể nhận start + end frame (FLF), VÀ cho phép 1 input-image
// làm root của clip trong workflow nhiều root (vd "singer": input-image→wan-i2v + text-to-video→concat + wan-i2v→concat).
// ALD 03/07/2026 - "edit-image" thêm vào: node Sửa ảnh có cổng động image1..6 (inputCount, FE 01/07) + chế độ GHÉP
// (combine) nhận mẫu + sản phẩm — đạo diễn dựng keyframe kiểu này (thay product-overlay) nên input-image phải được
// fan-out tới N edit-image và mỗi edit-image nhận ≥2 input (trước đây thiếu → "gen-input-1 có 4 output" fail).
const MULTI_INPUT_TYPES = new Set(["motion", "tryon", "create-image", "edit-image", "teaser", "lookbook", "compose", "product-overlay", "concat", "ss", "wan-i2v", "teen-flycam", "trend-tiktok", "reveal"])  // ALD 08/07 - reveal (đè lộ) nhận 2 video: base + reveal

// #region ALD 14/07/2026 - Kiểm tra chiều dây + loại media trước khi chạy job tốn GPU.
const IMAGE_OUTPUT_TYPES = new Set(["create-image", "edit-image", "tryon", "lookbook", "compose", "product-overlay"])
const VIDEO_OUTPUT_TYPES = new Set(["motion", "wan-i2v", "text-to-video", "ss", "teen-flycam", "trend-tiktok", "concat", "reveal", "enhance", "voiceover", "subtitle", "story-film", "face-motion"])

function outputKind(node) {
  const type = String(node?.type || node?.data?.type || "")
  if (type === "input") return String(node?.data?.config?.contentType || "").toLowerCase().trim()
  if (IMAGE_OUTPUT_TYPES.has(type)) return "image"
  if (VIDEO_OUTPUT_TYPES.has(type)) return "video"
  return ""
}

function edgeDirectionError(edge, sourceNode, targetNode) {
  const sourceType = String(sourceNode?.type || "")
  const onError = sourceNode?.data?.config?.onError
  const validNamedSource = sourceType === "condition"
    ? ["true", "false"].includes(String(edge.sourceHandle || ""))
    : onError === "route" && ["success", "error"].includes(String(edge.sourceHandle || ""))
  if (edge.sourceHandle && !validNamedSource) {
    return `Dây ${edge.id} đang bắt đầu từ cổng vào "${edge.sourceHandle}" của node ${edge.source}. `
      + "Hãy kéo dây từ chấm output bên phải node nguồn sang cổng input bên trái node đích."
  }
  if (String(targetNode?.type) === "wan-i2v" && ["start", "end"].includes(String(edge.targetHandle || ""))) {
    const kind = outputKind(sourceNode)
    if (kind && kind !== "image") {
      return `Node ${edge.target} (Ảnh → Video): cổng ${edge.targetHandle === "start" ? "Ảnh đầu" : "Ảnh cuối"} cần ẢNH, `
        + `nhưng node ${edge.source} xuất ${kind === "video" ? "VIDEO" : kind.toUpperCase()}. `
        + "Hãy dùng Input Image/Tạo ảnh/Sửa ảnh; output Ảnh → Video chỉ nối vào Ghép cảnh."
    }
  }
  return ""
}
// #endregion

export function validateWorkflowDefinition(def) {
  const incoming = new Map(), outgoing = new Map()
  const nodeById = new Map((def.nodes || []).map((n) => [n.id, n]))
  for (const n of def.nodes || []) { incoming.set(n.id, 0); outgoing.set(n.id, []) }
  for (const e of def.edges || []) {
    if (!nodeById.has(e.target) || !nodeById.has(e.source)) throw new Error(`Edge ${e.id} reference node không tồn tại`)
    const directionError = edgeDirectionError(e, nodeById.get(e.source), nodeById.get(e.target))
    if (directionError) throw new Error(directionError)
    incoming.set(e.target, (incoming.get(e.target) || 0) + 1)
    outgoing.get(e.source).push(e)
  }
  for (const [id, edges] of outgoing) {
    const node = nodeById.get(id)
    const onError = node?.data?.config?.onError
    // ALD 23/06/2026 - cho FAN-OUT dữ liệu: node ra NHIỀU cạnh được phép NẾU mọi target là node multi-input
    // (phân phối output tới nhiều consumer, vd 1 ảnh → N create-image). condition/onError=route = rẽ control-flow.
    const allowMulti = node?.type === "condition" || onError === "route"
      || edges.every((e) => MULTI_INPUT_TYPES.has(String(nodeById.get(e.target)?.type)))
    if (edges.length > 1 && !allowMulti) throw new Error(`Node ${id} (${node?.type}) có ${edges.length} output — cần condition, onError=route, hoặc mọi nhánh tới node multi-input`)
  }
  for (const [id, count] of incoming) {
    if (count > 1 && !MULTI_INPUT_TYPES.has(String(nodeById.get(id)?.type)))
      throw new Error(`Node ${id} có ${count} input — chỉ 1 input/node (trừ ${[...MULTI_INPUT_TYPES].join(", ")})`)
  }
  const orphan = new Set()
  for (const n of def.nodes || []) if ((incoming.get(n.id) || 0) === 0 && (outgoing.get(n.id) || []).length === 0) orphan.add(n.id)
  const roots = (def.nodes || []).filter((n) => !orphan.has(n.id) && incoming.get(n.id) === 0)
  if (roots.length === 0) throw new Error("Workflow không có node bắt đầu (orphan hết hoặc có cycle?)")
  if (roots.length > 1) {
    const allFeedMulti = roots.every((r) => {
      const targets = (outgoing.get(r.id) || []).map((e) => nodeById.get(e.target))
      return targets.length > 0 && targets.every((t) => MULTI_INPUT_TYPES.has(String(t?.type)))
    })
    if (!allFeedMulti) throw new Error(`Workflow có ${roots.length} node bắt đầu — chỉ cho phép 1 root`)
  }
  const incomingEdges = new Map()
  for (const n of def.nodes || []) incomingEdges.set(n.id, [])
  for (const e of def.edges || []) incomingEdges.get(e.target).push(e)
  // Wan I2V luôn cần một Ảnh đầu thật. Ảnh cuối chỉ là keyframe tuỳ chọn, không thay thế Ảnh đầu.
  for (const n of def.nodes || []) {
    if (String(n.type) !== "wan-i2v") continue
    const edges = incomingEdges.get(n.id) || []
    const starts = edges.filter((e) => e.targetHandle === "start")
    if (starts.length === 0) {
      throw new Error(`Node ${n.id} (Ảnh → Video) thiếu Ảnh đầu. Nối một node ảnh vào cổng "Ảnh đầu"; "Ảnh cuối" chỉ là tuỳ chọn.`)
    }
    if (starts.length > 1) throw new Error(`Node ${n.id} có ${starts.length} dây vào cổng Ảnh đầu — chỉ được nối 1 ảnh.`)
  }
  return { root: roots[0], roots, outgoing, incomingEdges, nodeById }
}

// runId BẮT BUỘC tồn tại (worker đã tạo row queued). Cập nhật running → success/error.
export async function runWorkflow({ runId, workflowId, userId, authMethod, definition, input, _stack = [] }) {
  const events = []
  let flushAt = 0
  const persistEvents = async (force = false) => {
    const now = Date.now()
    if (!force && now - flushAt < 400) return
    flushAt = now
    await query("UPDATE workflow_runs SET events = $1 WHERE id = $2", [JSON.stringify(events), runId]).catch(() => {})
  }
  const emit = (level, msg, extra, nodeId) => {
    events.push({ ts: Date.now(), level, msg, ...(nodeId ? { node_id: nodeId } : {}), ...(extra ? { extra } : {}) })
    persistEvents()
  }

  let lastOutput = null
  const outputsById = new Map()
  try {
    await query("UPDATE workflow_runs SET status='running', started_at=now() WHERE id=$1", [runId])
    // #region ALD 11/06/2026 - Node "api-key" (config-only, KHÔNG execute). Hai cách dùng:
    // 1) NỐI CỔNG (ưu tiên cao nhất): cạnh từ node api-key → node đích = "config edge", gán key cho ĐÚNG node đó
    //    rồi LOẠI node+cạnh khỏi graph chạy (làm TRƯỚC validate — không tính input data, không dính luật
    //    1-input/multi-output; 1 node api-key nối được nhiều node).
    // 2) Đặt rời trên canvas: tự phân bổ key theo providerType cho mọi node cùng provider (fallback).
    // Key CHỈ đến từ node API Key / field node — env đã bỏ. KHÔNG log giá trị key.
    const providerKeys = {}
    const wiredKeyByNode = {}
    const keyCfgById = new Map()
    for (const n of definition?.nodes || []) {
      if (String(n?.type) !== "api-key") continue
      const c = n?.data?.config || {}
      const t = String(c.providerType || "").toLowerCase().trim()
      const k = String(c.apiKey || "").trim()
      keyCfgById.set(n.id, t && k ? { providerType: t, apiKey: k } : null)
      if (t && k) {
        if (providerKeys[t]) emit("warn", `Nhiều node API Key cùng provider "${t}" — node sau cùng thắng (nối cổng trực tiếp để chỉ định rõ)`)
        providerKeys[t] = k
      }
    }
    let defRun = definition
    if (keyCfgById.size) {
      for (const e of definition?.edges || []) {
        if (!keyCfgById.has(e.source)) continue
        const pk = keyCfgById.get(e.source)
        if (pk) wiredKeyByNode[e.target] = pk
        else emit("warn", "Node API Key được nối dây nhưng chưa nhập Type/key — bỏ qua")
      }
      defRun = {
        ...definition,
        nodes: (definition.nodes || []).filter((n) => !keyCfgById.has(n.id)),
        edges: (definition.edges || []).filter((e) => !keyCfgById.has(e.source) && !keyCfgById.has(e.target)),
      }
    }
    // #endregion
    const { root, roots, outgoing, incomingEdges, nodeById } = validateWorkflowDefinition(defRun)
    emit("info", `Workflow bắt đầu (root: ${root.type}${roots.length > 1 ? `, +${roots.length - 1} root khác` : ""})`)

    let current = root
    const visited = new Set()
    const pendingRoots = roots.filter((r) => r.id !== root.id)
    const MAX = 100
    let step = 0
    while (current && step < MAX) {
      step++
      if (visited.has(current.id)) throw new Error(`Cycle tại node ${current.id}`)
      if (MULTI_INPUT_TYPES.has(current.type)) {
        const missing = (incomingEdges.get(current.id) || []).map((e) => e.source).filter((s) => !visited.has(s))
        if (missing.length > 0) {
          // ALD 08/07/2026 - BỎ QUA node đã xử lý khi lấy từ pendingRoots: fan-out (line ~187) đẩy target vào
          // pendingRoots lúc chưa visited, nhưng target đó có thể được xử lý qua đường khác → entry STALE. Không
          // skip thì shift trúng stale → dòng visited-check tưởng là "Cycle" (vd tr-tryonB: model fan-out + prodB hội tụ).
          let next = pendingRoots.shift()
          while (next && visited.has(next.id)) next = pendingRoots.shift()
          if (next) { current = next; continue }
          throw new Error(`Node ${current.id} (${current.type}) thiếu input từ ${missing.join(", ")}`)
        }
      }
      visited.add(current.id)
      const handler = NODE_HANDLERS[current.type]
      if (!handler) throw new Error(`Node type "${current.type}" không có handler`)
      const config = current.data?.config || {}
      const inputsByHandle = {}, inputSourcesByHandle = {}
      if (MULTI_INPUT_TYPES.has(current.type)) {
        for (const e of (incomingEdges.get(current.id) || [])) {
          const up = outputsById.get(e.source)
          if (up) { const h = e.targetHandle || "default"; inputsByHandle[h] = up; inputSourcesByHandle[h] = e.source }
        }
      }
      const cur = current
      const ctx = {
        userId, workflowRunId: runId, workflowInput: input || {}, inputsByHandle, inputSourcesByHandle,
        usesHandleInputs: MULTI_INPUT_TYPES.has(current.type),
        workflowStack: _stack,
        providerKeys, // ALD 11/06/2026 - key từ node api-key (tự phân bổ theo providerType)
        wiredKey: wiredKeyByNode[cur.id] || null, // key NỐI CỔNG thẳng vào node này (ưu tiên nhất)
        emit: (lvl, msg, extra) => emit(lvl, msg, extra, cur.id),
      }
      emit("info", `- ${current.id} (${current.type})`, undefined, current.id)
      const onError = config.onError || "stop"
      let nodeFailed = false
      try {
        lastOutput = await handler({ type: current.type, config }, lastOutput, ctx)
        outputsById.set(current.id, lastOutput)
        const meta = lastOutput?.metadata || {}
        const preview = meta.image || meta.video || meta.tryon_url || meta.url
        emit("success", "done", preview ? { previewUrl: preview, previewKind: meta.video ? "video" : "image", outputMeta: meta } : undefined, current.id)
      } catch (e) {
        const errMsg = e?.message || String(e)
        const label = config.label ? ` "${config.label}"` : ""
        const prefix = `[node ${current.id} (${current.type})${label}]`
        if (onError === "stop") throw new Error(`${prefix} ${errMsg}`)
        else if (onError === "continue") emit("warn", `Node lỗi (continue): ${errMsg}`, undefined, current.id)
        else if (onError === "route") {
          emit("warn", `Node lỗi (route error): ${errMsg}`, undefined, current.id)
          nodeFailed = true
          lastOutput = { text: errMsg, metadata: { _branch: "error", _error: true, errorMessage: errMsg, nodeId: current.id } }
        }
      }
      const outEdges = outgoing.get(current.id) || []
      if (outEdges.length === 0) break
      let nextEdge
      if (current.type === "condition") {
        const branch = lastOutput?.metadata?._branch || "false"
        nextEdge = outEdges.find((e) => e.sourceHandle === branch) || outEdges.find((e) => e.data?.label === branch) || outEdges[0]
        emit("info", `Condition → nhánh "${branch}"`, undefined, current.id)
      } else if (nodeFailed || lastOutput?.metadata?._branch === "error") {
        nextEdge = outEdges.find((e) => e.sourceHandle === "error") || outEdges.find((e) => e.sourceHandle !== "error") || outEdges[0]
      } else {
        nextEdge = outEdges.find((e) => e.sourceHandle !== "error") || outEdges[0]
      }
      // ALD 23/06/2026 - FAN-OUT: node thường ra NHIỀU cạnh → đi 1 nhánh (nextEdge), đẩy các nhánh CÒN LẠI vào
      // pendingRoots để duyệt sau (dùng lại cơ chế hội tụ multi-input). Cho 1 ảnh TỎA vào N create-image (singer).
      if (current.type !== "condition" && outEdges.length > 1) {
        for (const e of outEdges) {
          if (e === nextEdge) continue
          const t = nodeById.get(e.target)
          if (t && !visited.has(t.id) && !pendingRoots.includes(t)) pendingRoots.push(t)
        }
      }
      current = nodeById.get(nextEdge.target)
    }
    if (step >= MAX) throw new Error(`Workflow vượt ${MAX} step (loop?)`)
    emit("success", "Workflow hoàn tất")
    await persistEvents(true)
    const saved = await query(
      "UPDATE workflow_runs SET status='success', output=$1, events=$2, finished_at=now() WHERE id=$3 AND status <> 'cancelled' RETURNING id",
      [JSON.stringify(lastOutput), JSON.stringify(events), runId],
    )
    if (!saved.rows[0]) return { status: "cancelled", output: null, events }
    return { status: "success", output: lastOutput, events }
  } catch (e) {
    const errMsg = e?.message || String(e)
    emit("error", errMsg)
    await persistEvents(true)
    const failed = await query(
      "UPDATE workflow_runs SET status='error', error_msg=$1, events=$2, finished_at=now() WHERE id=$3 AND status <> 'cancelled' RETURNING id",
      [errMsg, JSON.stringify(events), runId],
    ).catch(() => ({ rows: [] }))
    if (!failed.rows[0]) return { status: "cancelled", output: null, events, errorMsg: errMsg }
    return { status: "error", output: null, events, errorMsg: errMsg }
  }
}
// #endregion
