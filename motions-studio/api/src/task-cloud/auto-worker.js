// #region ALD 16/07/2026 - Task Cloud Auto worker chạy nền trên VPS.
// Không dùng localStorage, window timer hoặc iframe. Tiến trình PM2 tự nhận task Ưu tiên,
// tải đủ input vào MinIO, tạo workflow riêng, chờ wf-worker render và gửi kết quả về khách.
import crypto from "node:crypto"
import { query, waitForDb } from "../db.js"
import { runMigrations } from "../migrate.js"
import { bootstrapSuperAdmin } from "../auth/bootstrap.js"
import { ensureBucket, putObject, deleteObject } from "../storage.js"
import { importSocialMedia } from "../routes/social-imports.js"
import { runResourceActions } from "../admin-resource-actions.js"
import { getTaskCloudConnection } from "./connection.js"

const POLL_MS = Math.max(2000, Number(process.env.TASK_CLOUD_AUTO_POLL_SEC || 5) * 1000)
const MAX_INPUT_BYTES = Math.max(10 * 1024 * 1024, Number(process.env.TASK_CLOUD_MAX_INPUT_BYTES || 195 * 1024 * 1024))
const MAX_OUTPUT_BYTES = Math.max(10 * 1024 * 1024, Number(process.env.TASK_CLOUD_MAX_OUTPUT_BYTES || 500 * 1024 * 1024))
const COMFY_URL = String(process.env.ADMIN_COMFY_URL || process.env.COMFY_URL || "")
const log = (...args) => console.log("[task-cloud-auto]", ...args)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// #region ALD 19/07/2026 - Chỉ nhận task khi ComfyUI đủ node + model của ĐÚNG capability box.
// Vast/RunPod có thể trả HTTP 200 ngay sau boot nhưng vẫn chỉ là ComfyUI stock. Nếu ping Task
// Cloud trước lúc kiểm tra capability, máy bị xem là primary online và claim task dù graph
// chắc chắn lỗi. Kiểm tra này phải chạy TRƯỚC cloudHeartbeat/claim.
//
// ALD 26/07/2026 - Tách theo capability. Trước đây hàm này hardcode bộ Motion nên box chuyên
// Try-On (chỉ có Qwen-Image-Edit, không có WanVideoWrapper) fail gate VĨNH VIỄN và không bao
// giờ claim được task nào. Box khai báo mình làm gì qua JOB_TYPES (lib-feature.sh khoá cứng).
const CAPABILITY_REQUIREMENTS = {
  // Task Cloud type `motion-control` → auto-worker dựng node `motion` (Wan 2.2 Animate).
  "motion-control": {
    jobTypes: ["motion", "teen-flycam", "trend-tiktok"],
    nodes: [
      "ImageResizeKJv2", "VHS_LoadVideo", "DWPreprocessor", "FaceMaskFromPoseKeypoints",
      "ImageCropByMaskAndResize", "WanVideoLoraSelectMulti", "WanVideoBlockSwap",
      "WanVideoModelLoader", "WanVideoVAELoader", "WanVideoTextEncodeCached",
      "CLIPVisionLoader", "WanVideoClipVisionEncode", "WanVideoAnimateEmbeds",
      "WanVideoSampler", "WanVideoDecode", "VHS_VideoCombine",
    ],
    models: [
      ["WanVideoModelLoader", "model", "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"],
      ["WanVideoVAELoader", "model_name", "Wan2_1_VAE_bf16.safetensors"],
      ["WanVideoTextEncodeCached", "model_name", "umt5-xxl-enc-bf16.safetensors"],
      ["CLIPVisionLoader", "clip_name", "clip_vision_h.safetensors"],
      ["WanVideoLoraSelectMulti", "lora_0", "WanAnimate_relight_lora_fp16.safetensors"],
      ["WanVideoLoraSelectMulti", "lora_1", "lightx2v_I2V_14B_480p_cfg_step_distill_rank32_bf16.safetensors"],
    ],
  },
  // Task Cloud type `edit-image` → auto-worker dựng node `tryon` (Qwen-Image-Edit 2509).
  // Node dựng graph: linux.py:3217-3270 (UnetLoaderGGUF + CLIPLoader qwen_image + VAELoader).
  // CFGNorm/ModelSamplingAuraFlow/KSampler là node CORE của ComfyUI, vẫn kiểm để bắt
  // trường hợp ComfyUI quá cũ so với graph.
  "edit-image": {
    jobTypes: ["tryon", "create-image", "edit-image", "product-overlay"],
    nodes: [
      "UnetLoaderGGUF", "CLIPLoader", "VAELoader", "KSampler",
      "CFGNorm", "ModelSamplingAuraFlow", "TextEncodeQwenImageEditPlus",
    ],
    models: [
      ["UnetLoaderGGUF", "unet_name", "Qwen-Image-Edit-2509-Q8_0.gguf"],
      ["CLIPLoader", "clip_name", "qwen_2.5_vl_7b_fp8_scaled.safetensors"],
      ["VAELoader", "vae_name", "qwen_image_vae.safetensors"],
    ],
  },
}

const _DEFAULT_AUTO_JOB_TYPES = "motion,teen-flycam,trend-tiktok,tryon,create-image,edit-image"
const BOX_JOB_TYPES = new Set(
  String(process.env.JOB_TYPES || _DEFAULT_AUTO_JOB_TYPES)
    .split(",").map((value) => value.trim()).filter(Boolean),
)

function resolveActiveCapabilities() {
  // Box thuê trên cloud được bootstrap ghi thẳng WORKER_CAPABILITIES vào .env → tin tuyệt đối.
  // Dạng danh sách phân cách bằng dấu phẩy (không JSON) cho khỏi vướng nháy khi đi qua
  // shell → .env → loadEnv → PM2.
  const declared = String(process.env.WORKER_CAPABILITIES || "")
    .split(",").map((value) => value.trim()).filter((capability) => CAPABILITY_REQUIREMENTS[capability])
  if (declared.length) return declared

  const matched = Object.entries(CAPABILITY_REQUIREMENTS)
    .filter(([, spec]) => spec.jobTypes.some((type) => BOX_JOB_TYPES.has(type)))
    .map(([capability]) => capability)
  // Box đa năng cũ (.165 chạy đủ JOB_TYPES, không khai WORKER_CAPABILITIES): GIỮ NGUYÊN hành vi
  // trước 26/07 — chỉ kiểm bộ Motion. Siết thêm bộ Qwen ở đây sẽ có nguy cơ chặn cả motion trên
  // máy đang chạy production chỉ vì thiếu một model tryon.
  return matched.length > 1 ? ["motion-control"] : matched
}
const ACTIVE_CAPABILITIES = resolveActiveCapabilities()

// object_info không đổi khi ComfyUI đang chạy; cache để không bắn ~20 request mỗi vòng poll.
// Chỉ cache kết quả THÀNH CÔNG — fail phải kiểm lại vì model có thể vừa tải xong.
let renderReadyOkUntil = 0

function comfyInputChoices(nodeInfo, inputName) {
  const spec = nodeInfo?.input?.required?.[inputName] || nodeInfo?.input?.optional?.[inputName]
  return Array.isArray(spec?.[0]) ? spec[0].map(String) : []
}

async function assertComfyRenderReady() {
  if (!COMFY_URL) throw new Error("COMFY_URL chưa cấu hình; worker chưa thể render")
  if (!ACTIVE_CAPABILITIES.length) {
    throw new Error(`JOB_TYPES="${[...BOX_JOB_TYPES].join(",")}" không khớp capability nào của Task Cloud (motion-control / edit-image)`)
  }
  if (Date.now() < renderReadyOkUntil) return true

  const base = COMFY_URL.replace(/\/+$/, "")
  const wanted = [...new Set(ACTIVE_CAPABILITIES.flatMap((capability) => CAPABILITY_REQUIREMENTS[capability].nodes))]
  const responses = await Promise.all(wanted.map(async (nodeName) => {
    const response = await fetch(`${base}/object_info/${encodeURIComponent(nodeName)}`, {
      signal: AbortSignal.timeout(15_000),
    })
    if (!response.ok) throw new Error(`ComfyUI object_info HTTP ${response.status}`)
    const payload = await response.json()
    return [nodeName, payload?.[nodeName] || null]
  }))
  const nodeInfo = Object.fromEntries(responses)

  for (const capability of ACTIVE_CAPABILITIES) {
    const spec = CAPABILITY_REQUIREMENTS[capability]
    const missingNodes = spec.nodes.filter((nodeName) => !nodeInfo[nodeName])
    if (missingNodes.length) {
      throw new Error(`ComfyUI chưa render-ready (${capability}); thiếu node: ${missingNodes.join(", ")}`)
    }
    const missingModels = spec.models.filter(([nodeName, inputName, modelName]) => {
      const choices = comfyInputChoices(nodeInfo[nodeName], inputName)
      return choices.length > 0 && !choices.some((choice) => choice === modelName || choice.endsWith(`/${modelName}`))
    }).map(([, , modelName]) => modelName)
    if (missingModels.length) {
      throw new Error(`ComfyUI chưa render-ready (${capability}); thiếu model: ${missingModels.join(", ")}`)
    }
  }
  renderReadyOkUntil = Date.now() + 60_000
  return true
}
// #endregion

function safeText(value, limit = 1500) {
  return String(value || "").trim().slice(0, limit)
}

function safeName(value, fallback, mime = "") {
  let name = String(value || fallback || "task-input")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-")
    .replace(/\s+/g, "-")
    .slice(0, 140)
  if (!/\.[a-z0-9]{2,5}$/i.test(name)) {
    name += mime.startsWith("image/") ? (mime === "image/png" ? ".png" : ".jpg") : ".mp4"
  }
  return name
}

function taskSlug(task) {
  return `task-${String(task?.id || "").replace(/[^a-z0-9]/gi, "").slice(0, 28).toLowerCase()}`
}

function isSocialUrl(value) {
  try {
    const host = new URL(value).hostname.toLowerCase().replace(/^www\./, "")
    return host === "youtu.be" || host.endsWith("youtube.com") || host.endsWith("tiktok.com")
      || host === "fb.watch" || host.endsWith("facebook.com")
  } catch {
    return false
  }
}

async function cloud(pathname, options = {}) {
  const connection = await getTaskCloudConnection()
  if (!connection.configured) throw new Error(connection.error || "Motion Task Cloud URL/API key chưa được cấu hình")
  const response = await fetch(`${connection.url}${pathname}`, {
    ...options,
    signal: options.signal || AbortSignal.timeout(options.timeoutMs || 120_000),
    headers: {
      Authorization: `Bearer ${connection.token}`,
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  })
  const text = await response.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = null }
  if (!response.ok) {
    throw new Error(safeText(data?.statusMessage || data?.message || data?.error || text || `Task Cloud HTTP ${response.status}`, 1000))
  }
  return data
}

async function patchCloudStatus(taskId, status, note) {
  return cloud(`/api/connector/tasks/${encodeURIComponent(taskId)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status, note: safeText(note, 1900) }),
  })
}

async function setRunnerState(state, task = null, error = null) {
  await query(
    `UPDATE task_cloud_auto_settings
       SET state=$1, active_task_id=$2, active_task_code=$3, last_error=$4,
           heartbeat_at=now(), updated_at=now()
     WHERE singleton_id=true`,
    [state, task?.id || null, task?.code || null, error ? safeText(error) : null],
  )
}

async function heartbeat() {
  await query("UPDATE task_cloud_auto_settings SET heartbeat_at=now() WHERE singleton_id=true")
}

async function cloudHeartbeat() {
  const data = await cloud("/api/connector/ping", { timeoutMs: 15_000 })
  if (!data?.ok || data?.mode !== "worker") throw new Error("Task Cloud không xác nhận kết nối worker")
  await heartbeat()
  return data
}

async function runnerSetting() {
  const { rows } = await query("SELECT * FROM task_cloud_auto_settings WHERE singleton_id=true")
  return rows[0] || { enabled: false, state: "off" }
}

async function adminUser() {
  const email = String(process.env.SUPER_ADMIN || process.env.SUPPER_ADMIN || "")
    .split(",")[0].trim().toLowerCase()
  if (!email) throw new Error("SUPER_ADMIN chưa cấu hình")
  const { rows } = await query("SELECT id, email FROM users WHERE lower(email)=lower($1) AND is_active=true", [email])
  if (!rows[0]) throw new Error(`Không tìm thấy Super Admin ${email}`)
  return rows[0]
}

function inputNode(id, contentType, field, label, x, y) {
  return {
    id, type: "input", position: { x, y },
    data: { config: { contentType, source: "session", field, label, staticData: "", staticMime: "", staticName: "", _gen: "task-cloud" } },
  }
}

function taskMotionDimensions(quality = "540p", aspectRatio = "9:16") {
  const ratios = { "9:16": [9, 16], "16:9": [16, 9], "1:1": [1, 1], "3:4": [3, 4], "4:3": [4, 3], "21:9": [21, 9] }
  const [rw, rh] = ratios[aspectRatio] || ratios["9:16"]
  const is720 = quality === "720p"
  const shortEdge = is720 ? 720 : 544
  const maxEdge = is720 ? 1280 : 968
  let width = rw <= rh ? shortEdge : shortEdge * rw / rh
  let height = rw <= rh ? shortEdge * rh / rw : shortEdge
  const scale = Math.min(1, maxEdge / Math.max(width, height))
  width = Math.max(16, Math.round(width * scale / 16) * 16)
  height = Math.max(16, Math.round(height * scale / 16) * 16)
  return { width, height }
}

function buildTaskDefinition(task) {
  const cfg = task.jobConfig || {}
  const priority = task.queueTier === "priority"
  if (task.type === "motion-control") {
    const seconds = [10, 15, 20, 30].includes(Number(task.durationSeconds)) ? Number(task.durationSeconds) : 10
    // #region ALD 25/07/2026 - Nấc chất lượng do user chọn. cfg.quality = nấc RENDER (đã cap theo trần máy ở claim);
    // cfg.deliveredQuality = nấc GIAO khách chọn (Gốc 540p / 720p / 1080p). Render < giao → chèn node enhance
    // Lanczos phóng lên nấc giao. Client cũ không gửi deliveredQuality → suy theo quality (không enhance thêm).
    const RES_ORDER = { "540p": 1, "720p": 2, "1080p": 3 }
    const motionQuality = cfg.quality === "720p" ? "720p" : "540p"
    const deliveredQuality = RES_ORDER[cfg.deliveredQuality] ? cfg.deliveredQuality : motionQuality
    const doEnhance = RES_ORDER[deliveredQuality] > RES_ORDER[motionQuality]
    const maxRenderEdge = motionQuality === "720p" ? 1280 : 968
    const dimensions = taskMotionDimensions(motionQuality, cfg.aspectRatio || "9:16")
    // #endregion
    return {
      taskCloudVersion: 13,
      taskCloudTaskId: task.id,
      taskCloudCode: task.code || "",
      taskCloudQueueTier: task.queueTier || "standard",
      nodes: [
        inputNode("task-image", "image", "image_ref", "Ảnh người mẫu", 80, 90),
        inputNode("task-driver", "video", "driver_video", "Video Driver", 80, 430),
        { id: "task-motion", type: "motion", position: { x: 480, y: 245 }, data: { config: {
          preset: `drv-${seconds}s`, quality: motionQuality, maxRenderEdge, ...dimensions,
          fitDriver: false, fit_driver: false,
          mode: "transfer", renderProfile: "fast", provider: "qwen",
          aspectRatio: cfg.aspectRatio || "9:16", audioMode: cfg.audioMode || "original",
          audioPassthrough: (cfg.audioMode || "original") === "original",
          deliveryPreset: "source",
          // ALD 21/07/2026 - default GỐC kijai 1.0 (tự nhiên); tự lành default cũ 0.4/0.6 về 1.0.
          // ALD 27/07/2026 - BỎ ép 0.4/0.6 → 0.7. Luật đó (21/07, từ A/B "0.6 đơ miệng") khiến khách
          // chọn "Cân bằng" trên Task Cloud mà job chạy 0.7 — số hiện trên đơn không phải số thật sự chạy.
          // Thang mới của Task Cloud: Mềm 0.3 · Cân bằng 0.5 · Giữ chặt 0.7 · Rất chặt 1; tôn trọng đúng
          // lựa chọn của khách. Mặc định vẫn 0.7 khi task không gửi giá trị nào.
          faceStrength: Number(cfg.faceStrength ?? 0.7), faceSource: "driver",
          bodyProportionLock: cfg.bodyProportionLock !== false,
          poseStrength: cfg.bodyProportionLock === false ? Number(cfg.poseStrength || 0.8) : Math.min(Number(cfg.poseStrength || 0.7), 0.7),
          clipStrength: cfg.bodyProportionLock === false ? Number(cfg.clipStrength || 1.2) : Math.max(Number(cfg.clipStrength || 1.35), 1.35),
          loraRelight: 0, matchRef: false, skipFirstFrames: 0,
          // ALD 17/07/2026 - mặc định tắt; Task Cloud BE chỉ cho gói Ưu tiên gửi true.
          // ALD 17/07/2026 - detailUpscale ép false (răng bể pixel — xem feature/face-restore-motion-delivery.md).
          detailUpscale: false, deliverySharpen: false,
          extraPositive: task.brief || "", _gen: "task-cloud",
        } } },
        ...(doEnhance ? [{ id: "task-enhance", type: "enhance", position: { x: 820, y: 245 }, data: { config: {
          label: `Lanczos ${deliveredQuality} · FPS gốc`, mode: "video", targetRes: deliveredQuality, fpsInterp: "",
          engine: "lanczos", allowFallback: false, faceRestore: false, _gen: "task-cloud",
        } } }] : []),
        { id: "task-output", type: "output", position: { x: doEnhance ? 1160 : 820, y: 245 }, data: { config: { format: "video", cleanup: false, _gen: "task-cloud" } } },
      ],
      edges: [
        { id: "task-e-image", source: "task-image", target: "task-motion", targetHandle: "image" },
        { id: "task-e-driver", source: "task-driver", target: "task-motion", targetHandle: "motion" },
        ...(doEnhance
          ? [
              { id: "task-e-enhance", source: "task-motion", target: "task-enhance" },
              { id: "task-e-output", source: "task-enhance", target: "task-output" },
            ]
          : [{ id: "task-e-output", source: "task-motion", target: "task-output" }]),
      ],
    }
  }

  // ALD 20/07/2026 - Try-On tối giản: 1 Image Ref (model) + 1 Product Ref. Bỏ clean-only, đa-góc, ghép nền.
  const garmentTypes = new Set(["auto", "upper", "lower", "skirt", "dress", "set", "bikini", "bra", "lingerie", "shoes", "accessory"])
  const garmentType = garmentTypes.has(cfg.garmentType) ? cfg.garmentType : "auto"
  const outputRes = ["", "fullhd", "2k", "4k"].includes(cfg.outputRes) ? cfg.outputRes : "2k"
  const enhanceRes = outputRes === "fullhd" ? "1080p" : (outputRes || "2k")
  return {
    taskCloudVersion: 5,
    taskCloudTaskId: task.id,
    taskCloudCode: task.code || "",
    taskCloudQueueTier: task.queueTier || "standard",
    nodes: [
      inputNode("task-model", "image", "image_ref", "Ảnh người mẫu", 80, 90),
      inputNode("task-product", "image", "product_ref", "Ảnh sản phẩm", 80, 430),
      { id: "task-tryon", type: "tryon", position: { x: 620, y: 310 }, data: { config: {
        provider: "qwen", garmentType, autoAnalyze: garmentType === "auto",
        brightness: Number.isFinite(Number(cfg.brightness)) ? Math.max(-0.3, Math.min(0.2, Number(cfg.brightness))) : 0,
        outputRes, keepFace: true, extraPrompt: cfg.extraPrompt || task.brief || "",
        prompt: task.brief || "Giữ nguyên khuôn mặt, vóc dáng và bối cảnh. Mặc chính xác sản phẩm trong ảnh tham chiếu.",
        _gen: "task-cloud",
      } } },
      ...(priority ? [{ id: "task-enhance", type: "enhance", position: { x: 960, y: 310 }, data: { config: {
        label: `Nâng nét ảnh · ${enhanceRes === "1080p" ? "Full HD" : enhanceRes.toUpperCase()}`,
        mode: "image", targetRes: enhanceRes, fpsInterp: "", engine: "lanczos",
        upscaleModel: "4x-UltraSharp", faceRestore: true, faceFidelity: 0.5, _gen: "task-cloud",
      } } }] : []),
      { id: "task-output", type: "output", position: { x: priority ? 1300 : 960, y: 310 }, data: { config: { format: "image", cleanup: false, _gen: "task-cloud" } } },
    ],
    edges: [
      { id: "task-e-model", source: "task-model", target: "task-tryon", targetHandle: "model" },
      { id: "task-e-product-1", source: "task-product", target: "task-tryon", targetHandle: "product" },
      ...(priority
        ? [
            { id: "task-e-enhance", source: "task-tryon", target: "task-enhance" },
            { id: "task-e-output", source: "task-enhance", target: "task-output" },
          ]
        : [{ id: "task-e-output", source: "task-tryon", target: "task-output" }]),
    ],
  }
}

function roleInputs(task, role, kind) {
  return (task.inputs || []).filter((input) => input.role === role || (!input.role && String(input.mime || "").startsWith(`${kind}/`)))
}

async function downloadInput(task, input, expectedKind, label, userId) {
  if (!input?.url) throw new Error(`Task thiếu ${label}`)
  if (expectedKind === "video" && input.source === "url" && isSocialUrl(input.url)) {
    const imported = await importSocialMedia({ url: input.url, contentType: "video", userId })
    if (!String(imported?.item?.mime || "").startsWith("video/") || !Number(imported?.item?.size_bytes || 0)) {
      throw new Error(`${label} social không trả video hợp lệ`)
    }
    return {
      bucket: imported.bucket || "motion-jobs", path: imported.path,
      name: imported.item.name, mime: imported.item.mime, size: Number(imported.item.size_bytes),
    }
  }

  const response = await fetch(input.url, { redirect: "follow", signal: AbortSignal.timeout(15 * 60 * 1000) })
  if (!response.ok) throw new Error(`Không tải được ${label} (HTTP ${response.status})`)
  const declared = Number(response.headers.get("content-length") || input.size || 0)
  if (declared > MAX_INPUT_BYTES) throw new Error(`${label} vượt giới hạn ${Math.round(MAX_INPUT_BYTES / 1024 / 1024)}MB`)
  const bytes = Buffer.from(await response.arrayBuffer())
  if (!bytes.length) throw new Error(`${label} là file rỗng`)
  if (bytes.length > MAX_INPUT_BYTES) throw new Error(`${label} vượt giới hạn ${Math.round(MAX_INPUT_BYTES / 1024 / 1024)}MB`)
  const mime = String(response.headers.get("content-type") || input.mime || "").split(";")[0].trim().toLowerCase()
  if (!mime.startsWith(`${expectedKind}/`)) throw new Error(`${label} sai định dạng (${mime || "không xác định"})`)
  const name = safeName(input.name, label, mime)
  const bucket = "motion-jobs"
  const folder = String(task.code || task.id).replace(/[^a-z0-9_-]/gi, "").slice(0, 48) || "task"
  const objectPath = `task-cloud/${folder}/${crypto.randomUUID()}/${name}`
  const key = `${bucket}/${objectPath}`
  await putObject(key, bytes, mime)
  await query(
    `INSERT INTO storage_files (bucket, path, storage_key, name, size_bytes, mime, user_id)
     VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (bucket, path) DO NOTHING`,
    [bucket, objectPath, key, name, bytes.length, mime, userId],
  )
  return { bucket, path: objectPath, name, mime, size: bytes.length }
}

function applyAsset(node, input, asset, label) {
  node.data.config = {
    ...(node.data.config || {}), source: "static", url: "", staticData: "", staticUrl: "",
    staticPath: asset.path, staticBucket: asset.bucket, staticName: asset.name,
    staticMime: asset.mime, staticSize: asset.size, label,
    _taskCloudInputKey: [input.index, input.role || "", input.size || 0, input.name || ""].join(":"),
  }
}

async function materializeDefinition(task, userId) {
  const definition = buildTaskDefinition(task)
  const imageNodes = definition.nodes.filter((node) => node.type === "input" && node.data?.config?.contentType === "image")
  const videoNodes = definition.nodes.filter((node) => node.type === "input" && node.data?.config?.contentType === "video")
  const byField = (field) => imageNodes.find((node) => node.data?.config?.field === field)
  const images = roleInputs(task, "image-ref", "image")
  const products = (task.inputs || []).filter((input) => input.role === "product-reference")

  if (task.type === "motion-control") {
    const drivers = roleInputs(task, "driver-video", "video")
    const imageNode = byField("image_ref") || imageNodes[0]
    const videoNode = videoNodes.find((node) => node.data?.config?.field === "driver_video") || videoNodes[0]
    if (!imageNode || !images[0]) throw new Error("Task thiếu Ảnh người mẫu")
    if (!videoNode || !drivers[0]) throw new Error("Task thiếu Video Driver")
    applyAsset(imageNode, images[0], await downloadInput(task, images[0], "image", "Ảnh người mẫu", userId), "Ảnh người mẫu")
    applyAsset(videoNode, drivers[0], await downloadInput(task, drivers[0], "video", "Video Driver", userId), "Video Driver")
    return definition
  }

  // ALD 20/07/2026 - Try-On tối giản: 1 Ảnh người mẫu + 1 Ảnh sản phẩm.
  const modelNode = byField("image_ref") || imageNodes[0]
  if (!modelNode || !images[0]) throw new Error("Task thiếu Ảnh người mẫu")
  applyAsset(modelNode, images[0], await downloadInput(task, images[0], "image", "Ảnh người mẫu", userId), "Ảnh người mẫu")
  const productNode = byField("product_ref") || imageNodes[1]
  if (!productNode || !products[0]) throw new Error("Task thiếu Ảnh sản phẩm")
  applyAsset(productNode, products[0], await downloadInput(task, products[0], "image", "Ảnh sản phẩm", userId), "Ảnh sản phẩm")
  return definition
}

async function createWorkflowRun(task, userId, definition) {
  const slug = taskSlug(task)
  const { rows: workflowRows } = await query(
    `INSERT INTO workflows (user_id, slug, name, description, definition, is_public, is_active)
     VALUES ($1,$2,$3,$4,$5,false,true)
     ON CONFLICT (user_id, slug) DO UPDATE
       SET name=EXCLUDED.name, description=EXCLUDED.description, definition=EXCLUDED.definition,
           is_active=true, updated_at=now()
     RETURNING id`,
    [userId, slug, `${task.code} · ${task.type === "edit-image" ? "Edit Image / Try-On" : "Motion Control"}`,
      "Flow riêng do Task Cloud Auto worker tạo.", JSON.stringify(definition)],
  )
  const workflowId = workflowRows[0].id
  const { rows: runRows } = await query(
    `INSERT INTO workflow_runs (workflow_id, user_id, auth_method, status, input, definition)
     VALUES ($1,$2,'task-cloud-auto','queued','{}'::jsonb,$3) RETURNING id`,
    [workflowId, userId, JSON.stringify(definition)],
  )
  return { workflowId, runId: runRows[0].id }
}

// #region ALD 27/07/2026 - Chấp nhận http:// khi output trỏ về CHÍNH BOX.
// Box .165 phát URL https qua Cloudflare Tunnel nên luật cũ "chỉ https" chạy được. Pod RunPod
// KHÔNG mở port nào (bảo mật) → PUBLIC_BASE_URL=http://127.0.0.1:8080 → MỌI output bị loại và
// task báo "Workflow hoàn tất nhưng không tìm thấy URL output" dù render đã xong
// (gặp 27/07, task MT-202607-925E5B: render 595s thành công nhưng gửi 0 file).
// An toàn: URL chỉ được fetch NGAY TRÊN BOX rồi đẩy lên Task Cloud dạng multipart — không có byte
// nào đi qua mạng công cộng dưới dạng http. Địa chỉ ngoài vẫn BẮT BUỘC https như cũ.
function acceptableOutputUrl(url) {
  let parsed
  try { parsed = new URL(String(url || "")) } catch { return false }
  if (parsed.protocol === "https:") return true
  if (parsed.protocol !== "http:") return false
  const host = parsed.hostname
  return host === "localhost" || host === "127.0.0.1" || host === "::1"
    || /^10\./.test(host) || /^192\.168\./.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host)
}
// #endregion

function outputFiles(run) {
  const output = run?.output || {}
  const metadata = output.metadata || {}
  const textUrls = (String(output.text || "").match(/https?:\/\/[^\s"'<>]+/gi) || []).filter(acceptableOutputUrl)
  const candidates = [
    { value: metadata.video, kind: "video" }, { value: metadata.image, kind: "image" },
    { value: output.video, kind: "video" }, { value: output.image, kind: "image" },
    ...(Array.isArray(metadata.images) ? metadata.images.map((value) => ({ value, kind: "image" })) : []),
    ...(Array.isArray(output.images) ? output.images.map((value) => ({ value, kind: "image" })) : []),
    ...(Array.isArray(metadata.outputs) ? metadata.outputs.map((value) => ({ value, kind: "" })) : []),
    ...(Array.isArray(output.files) ? output.files.map((value) => ({ value, kind: "" })) : []),
    ...textUrls.map((value) => ({ value, kind: "" })),
  ]
  const seen = new Set()
  return candidates.flatMap(({ value, kind }) => {
    const url = typeof value === "string" ? value : value?.url
    if (!acceptableOutputUrl(url) || seen.has(url)) return []
    seen.add(url)
    const image = kind === "image" || String(value?.mime || "").startsWith("image/") || /\.(png|jpe?g|webp|avif)(\?|$)/i.test(url)
    return [{ url, name: value?.name || value?.label || `result.${image ? "png" : "mp4"}`, mime: value?.mime || (image ? "image/png" : "video/mp4") }]
  })
}

async function waitForRun(runId, task) {
  for (;;) {
    const { rows } = await query("SELECT id, status, output, error_msg FROM workflow_runs WHERE id=$1", [runId])
    const run = rows[0]
    if (!run) throw new Error("Workflow run không còn tồn tại")
    if (run.status === "success") return run
    if (["error", "cancelled"].includes(run.status)) throw new Error(run.error_msg || `Workflow ${run.status}`)
    await setRunnerState(run.status === "queued" ? "preparing" : "running", task)
    await sleep(POLL_MS)
  }
}

async function sendResults(task, run) {
  const outputs = outputFiles(run)
  if (!outputs.length) throw new Error("Workflow hoàn tất nhưng không tìm thấy URL output")
  const form = new FormData()
  for (let index = 0; index < outputs.length; index += 1) {
    const output = outputs[index]
    const response = await fetch(output.url, { redirect: "follow", signal: AbortSignal.timeout(15 * 60 * 1000) })
    if (!response.ok) throw new Error(`Không tải được output ${index + 1} (HTTP ${response.status})`)
    const declared = Number(response.headers.get("content-length") || 0)
    if (declared > MAX_OUTPUT_BYTES) throw new Error(`Output ${index + 1} vượt giới hạn ${Math.round(MAX_OUTPUT_BYTES / 1024 / 1024)}MB`)
    const bytes = await response.arrayBuffer()
    if (!bytes.byteLength || bytes.byteLength > MAX_OUTPUT_BYTES) throw new Error(`Output ${index + 1} rỗng hoặc quá lớn`)
    const mime = String(response.headers.get("content-type") || output.mime || "application/octet-stream").split(";")[0]
    if (!mime.startsWith("image/") && !mime.startsWith("video/")) throw new Error(`Output ${index + 1} sai định dạng (${mime})`)
    form.append("files", new Blob([bytes], { type: mime }), safeName(output.name, `result-${index + 1}`, mime))
  }
  form.append("note", `Auto đã hoàn tất workflow /${taskSlug(task)} và gửi ${outputs.length} file kết quả.`)
  return cloud(`/api/connector/tasks/${encodeURIComponent(task.id)}/results`, {
    method: "POST", body: form, timeoutMs: 30 * 60 * 1000,
  })
}

// #region ALD 17/07/2026 - Task Ưu tiên giành GPU từ task Free đang render.
// Chỉ từ chối task Free khi tìm thấy workflow_run queued/running tương ứng trên chính
// VPS này. Sau khi hủy DB, ngắt ComfyUI + unload model để task Ưu tiên không kế thừa VRAM.
async function patchCloudStatusWithRetry(taskId, status, note, attempts = 3) {
  let lastError
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try { return await patchCloudStatus(taskId, status, note) }
    catch (error) {
      lastError = error
      if (attempt < attempts) await sleep(attempt * 1000)
    }
  }
  throw lastError
}

async function preemptRunningFreeTasks(priorityTask, userId) {
  if (priorityTask?.queueTier !== "priority") return 0
  const responses = await Promise.all([
    cloud("/api/connector/tasks?status=processing&limit=100"),
    cloud("/api/connector/tasks?status=accepted&limit=100"),
  ])
  const freeTasks = responses
    .flatMap((response) => response?.items || [])
    .filter((task, index, all) => task.queueTier === "standard"
      && all.findIndex((item) => item.id === task.id) === index)
  let preempted = 0
  for (const task of freeTasks) {
    const { rows: activeRuns } = await query(
      `SELECT wr.id, wr.status
         FROM workflow_runs wr
         JOIN workflows w ON w.id=wr.workflow_id
        WHERE w.user_id=$1 AND w.slug=$2 AND wr.status IN ('queued','running')
        ORDER BY wr.started_at DESC`,
      [userId, taskSlug(task)],
    )
    if (!activeRuns.length) continue
    const runIds = activeRuns.map((run) => String(run.id))
    const reason = `Task Free ${task.code || task.id} đã dừng để nhường GPU cho task Ưu tiên ${priorityTask.code || priorityTask.id}.`
    await query(
      `UPDATE workflow_runs
          SET status='cancelled', error_msg=$2, finished_at=COALESCE(finished_at, now())
        WHERE id = ANY($1::uuid[]) AND status IN ('queued','running')`,
      [runIds, reason],
    )
    await query(
      `UPDATE jobs
          SET status='cancelled', error=$2, current_step='Nhường GPU cho task Ưu tiên',
              finished_at=COALESCE(finished_at, now())
        WHERE params->>'_wfRunId' = ANY($1::text[]) AND status IN ('queued','running')`,
      [runIds, reason],
    ).catch((error) => log(`không hủy được media job của ${task.code}:`, error?.message || error))
    await patchCloudStatusWithRetry(
      task.id,
      "rejected",
      "Task Free đang chạy đã được dừng vì hệ thống vừa nhận job Ưu tiên. Tài nguyên GPU được chuyển sang hàng ưu tiên; vui lòng gửi lại sau hoặc nâng cấp gói để được ưu tiên xử lý.",
    )
    preempted += 1
    log(`đã preempt Free ${task.code || task.id}: ${runIds.length} workflow run`)
  }
  if (preempted) {
    const released = await runResourceActions("gpu", { comfyUrl: COMFY_URL })
    log(`đã ngắt render Free và xả VRAM: success=${released.success}`)
    await sleep(1500)
  }
  return preempted
}
// #endregion

// #region ALD 17/07/2026 - Không lưu bền tài nguyên Task Cloud trên .165: sau khi kết quả đã nằm an toàn
// bên Task Cloud (103.142.24.69, /var/lib/motion-task-cloud), dọn TOÀN BỘ object MinIO của run:
// input tĩnh (staticBucket/staticPath trong definition, gồm cả social-import) + output/preview/trung gian
// của mọi job thuộc run (truy qua params._wfRunId do wf-worker/handlers.js gắn lúc tạo job).
// Row DB (jobs/workflow_runs/task_cloud_auto_jobs) giữ lại làm lịch sử — chỉ file media bị xóa.
// Tắt bằng env TASK_CLOUD_CLEANUP_LOCAL=0. Lỗi dọn chỉ warn, không fail task (khách đã nhận hàng).
const CLEANUP_LOCAL = String(process.env.TASK_CLOUD_CLEANUP_LOCAL ?? "1") !== "0"

async function cleanupLocalArtifacts(task, runId) {
  if (!CLEANUP_LOCAL || !runId) return
  const keys = new Set()
  const { rows: runRows } = await query("SELECT definition FROM workflow_runs WHERE id=$1", [runId])
  for (const node of (runRows[0]?.definition?.nodes || [])) {
    const c = node?.data?.config
    if (c?.staticBucket && c?.staticPath) keys.add(`${c.staticBucket}/${c.staticPath}`)
  }
  const { rows: jobRows } = await query(
    "SELECT output_key, outputs, previews FROM jobs WHERE params->>'_wfRunId'=$1", [String(runId)])
  for (const row of jobRows) {
    if (row.output_key) keys.add(row.output_key)
    for (const o of (Array.isArray(row.outputs) ? row.outputs : [])) if (o?.key) keys.add(o.key)
    for (const p of (Array.isArray(row.previews) ? row.previews : [])) if (p?.key) keys.add(p.key)
  }
  for (const key of keys) await deleteObject(key)
  if (keys.size) {
    await query("DELETE FROM storage_files WHERE storage_key = ANY($1::text[])", [[...keys]]).catch(() => {})
  }
  log(`${task.code || task.id}: đã dọn ${keys.size} object MinIO trên .165 (bản lưu duy nhất giờ ở Task Cloud)`)
}
// #endregion

// #region ALD 22/07/2026 - Tự phân loại lỗi DO INPUT (chất lượng file khách gửi), không phải lỗi hệ thống.
// Các lỗi này retry cũng vô ích (driver/ảnh mẫu không có mặt rõ → NaN face bbox). Báo thẳng input_error
// để Task Cloud email khách gửi lại input đạt chuẩn, khỏi cần admin triage tay như needs_review.
const INPUT_ERROR_SIGNATURES = [
  {
    re: /cannot convert float NaN to integer|keypoints_face|get_face_bboxes/i,
    reason: "Video nguồn (driver) không phát hiện được khuôn mặt rõ ở một hoặc nhiều khung hình. Hãy dùng video có khuôn mặt rõ, chính diện, đủ sáng, không bị che khuất hay quay lưng.",
  },
  {
    re: /NO_FACE_IN_REF|không thấy mặt trong ảnh mẫu/i,
    reason: "Ảnh mẫu không phát hiện được khuôn mặt. Hãy dùng ảnh chân dung có khuôn mặt rõ, chính diện, đủ sáng.",
  },
]

function classifyInputError(message) {
  const text = String(message || "")
  for (const sig of INPUT_ERROR_SIGNATURES) {
    if (sig.re.test(text)) return sig.reason
  }
  return null
}
// #endregion

async function failTask(task, error) {
  const message = safeText(error?.message || error || "Lỗi không xác định")
  log(`${task?.code || task?.id || "task"} lỗi:`, message)
  if (task?.id) {
    // #region ALD 22/07/2026 - Lỗi do input → input_error (báo khách gửi lại); còn lại → needs_review (admin xử lý).
    const inputReason = classifyInputError(message)
    if (inputReason) {
      log(`${task?.code || task?.id}: phân loại LỖI INPUT → input_error`)
      await patchCloudStatus(task.id, "input_error", `LỖI INPUT: ${inputReason}`)
        .catch((statusError) => log("không cập nhật được input_error:", statusError?.message || statusError))
    } else {
      await patchCloudStatus(task.id, "needs_review", `Auto worker đã bỏ qua task lỗi và tiếp tục task Ưu tiên kế tiếp. Lỗi: ${message}`)
        .catch((statusError) => log("không cập nhật được needs_review:", statusError?.message || statusError))
    }
    // #endregion
    await query(
      `UPDATE task_cloud_auto_jobs SET status='failed', last_error=$1, finished_at=now(), updated_at=now() WHERE task_id=$2`,
      [message, task.id],
    ).catch(() => {})
  }
  const setting = await runnerSetting().catch(() => ({ enabled: true }))
  await setRunnerState(setting.enabled ? "waiting" : "off", null, message)
}

async function processTask(task, existingJob = null) {
  const admin = await adminUser()
  let workflowId = existingJob?.workflow_id || null
  let runId = existingJob?.run_id || null
  try {
    await setRunnerState("preparing", task)
    await query(
      `INSERT INTO task_cloud_auto_jobs (task_id, task_code, task_type, task_payload, status, attempts, started_at)
       VALUES ($1,$2,$3,$4,'preparing',1,now())
       ON CONFLICT (task_id) DO UPDATE SET task_payload=EXCLUDED.task_payload, status='preparing',
         attempts=task_cloud_auto_jobs.attempts+1, last_error=null, updated_at=now()`,
      [task.id, task.code || null, task.type || null, JSON.stringify(task)],
    )
    if (!runId) {
      const definition = await materializeDefinition(task, admin.id)
      const created = await createWorkflowRun(task, admin.id, definition)
      workflowId = created.workflowId
      runId = created.runId
      await query(
        `UPDATE task_cloud_auto_jobs SET workflow_id=$1, run_id=$2, status='queued', updated_at=now() WHERE task_id=$3`,
        [workflowId, runId, task.id],
      )
      await patchCloudStatus(task.id, "processing", `Auto VPS đã nạp đủ input và bắt đầu workflow /${taskSlug(task)}.`)
    }
    const run = await waitForRun(runId, task)
    await query("UPDATE task_cloud_auto_jobs SET status='sending', updated_at=now() WHERE task_id=$1", [task.id])
    await setRunnerState("sending", task)
    await patchCloudStatus(task.id, "review", "Workflow đã render xong; Auto đang lưu và gửi kết quả cho khách.")
    const result = await sendResults(task, run)
    // ALD 17/07/2026 - kết quả đã nằm bên Task Cloud → dọn sạch media của run trên .165 (lỗi chỉ warn).
    await cleanupLocalArtifacts(task, runId).catch((cleanupError) => log("dọn artifact .165 lỗi (bỏ qua):", cleanupError?.message || cleanupError))
    await query(
      `UPDATE task_cloud_auto_jobs SET status='completed', last_error=null, finished_at=now(), updated_at=now() WHERE task_id=$1`,
      [task.id],
    )
    log(`${task.code || task.id} hoàn tất${result?.mailSent === false ? " (email lỗi)" : ""}`)
    const setting = await runnerSetting()
    await setRunnerState(setting.enabled ? "waiting" : "off")
  } catch (error) {
    await failTask(task, error)
  }
}

async function recoverJob() {
  const { rows } = await query(
    `SELECT * FROM task_cloud_auto_jobs
     WHERE status IN ('claimed','preparing','queued','running','sending')
     ORDER BY updated_at ASC LIMIT 1`,
  )
  const job = rows[0]
  if (!job?.task_payload?.id) return false
  log(`khôi phục ${job.task_code || job.task_id} (${job.status})`)
  await processTask(job.task_payload, job)
  return true
}

async function main() {
  await waitForDb()
  await runMigrations()
  await bootstrapSuperAdmin()
  await ensureBucket()
  const initialConnection = await getTaskCloudConnection({ fresh: true })
  log(`start · poll ${POLL_MS}ms · configured=${initialConnection.configured} · source=${initialConnection.source}`)
  if (!initialConnection.configured) await setRunnerState("error", null, "Motion Task Cloud URL/API key chưa cấu hình")
  await recoverJob()

  for (;;) {
    try {
      const setting = await runnerSetting()
      if (!setting.enabled) {
        if (!setting.active_task_id && setting.state !== "off") await setRunnerState("off")
        // Runner tạm dừng chỉ có nghĩa là không claim job. Backend vẫn phải gửi
        // heartbeat ra Task Cloud để bảng máy chủ phân biệt được "Online · tạm
        // dừng nhận job" với một worker thật sự mất kết nối.
        const connection = await getTaskCloudConnection({ fresh: true })
        if (connection.configured) {
          try {
            await cloudHeartbeat()
          } catch (error) {
            log("heartbeat Task Cloud lỗi khi runner tạm dừng:", safeText(error?.message || error))
            await heartbeat()
          }
        } else {
          await heartbeat()
        }
        await sleep(POLL_MS)
        continue
      }
      const connection = await getTaskCloudConnection({ fresh: true })
      if (!connection.configured) {
        await setRunnerState("error", null, connection.error || "Motion Task Cloud URL/API key chưa cấu hình")
        await sleep(Math.max(POLL_MS, 15_000))
        continue
      }
      await setRunnerState("waiting")
      await assertComfyRenderReady()
      await cloudHeartbeat()
      const claimed = await cloud("/api/connector/tasks/claim", { method: "POST", body: "{}" })
      const task = claimed?.item
      if (!task) {
        await sleep(POLL_MS)
        continue
      }
      log(`nhận ${task.code || task.id} (${task.type})`)
      const admin = await adminUser()
      // Preempt là ưu tiên vận hành nhưng lỗi đồng bộ một task Free không được phép
      // làm task Ưu tiên vừa claim kẹt vĩnh viễn ở processing.
      await preemptRunningFreeTasks(task, admin.id)
        .catch((error) => log("preempt Free lỗi, vẫn tiếp tục task Ưu tiên:", error?.message || error))
      await processTask(task)
    } catch (error) {
      const message = safeText(error?.message || error)
      log("loop lỗi, sẽ thử lại:", message)
      await setRunnerState("error", null, message).catch(() => {})
      await sleep(Math.max(POLL_MS, 10_000))
    }
  }
}

main().catch((error) => {
  console.error("[task-cloud-auto] fatal:", error)
  process.exit(1)
})
// #endregion
