// #region ALD 19/07/2026 - Kết nối outbound Motion Backend -> Motion Task Cloud.
// API key chỉ lưu dạng AES-256-GCM trong PostgreSQL; trình duyệt không bao giờ đọc lại plaintext.
import crypto from "node:crypto"
import { query } from "../db.js"

const CIPHER_VERSION = "v1"
const CACHE_MS = 3_000
let cached = null
let cachedAt = 0

function encryptionKey() {
  const secret = String(process.env.TASK_CLOUD_CREDENTIAL_SECRET || process.env.SESSION_JWT_SECRET || "")
  if (secret.length < 24) throw new Error("Thiếu TASK_CLOUD_CREDENTIAL_SECRET hoặc SESSION_JWT_SECRET để mã hoá API key")
  return crypto.createHash("sha256").update(secret).digest()
}

function b64(value) {
  return Buffer.from(value).toString("base64url")
}

function unb64(value) {
  return Buffer.from(String(value || ""), "base64url")
}

export function encryptTaskCloudToken(token) {
  const iv = crypto.randomBytes(12)
  const cipher = crypto.createCipheriv("aes-256-gcm", encryptionKey(), iv)
  const encrypted = Buffer.concat([cipher.update(String(token), "utf8"), cipher.final()])
  return [CIPHER_VERSION, b64(iv), b64(cipher.getAuthTag()), b64(encrypted)].join(".")
}

export function decryptTaskCloudToken(payload) {
  const [version, iv, tag, encrypted] = String(payload || "").split(".")
  if (version !== CIPHER_VERSION || !iv || !tag || !encrypted) throw new Error("API key Task Cloud đã mã hoá không hợp lệ")
  const decipher = crypto.createDecipheriv("aes-256-gcm", encryptionKey(), unb64(iv))
  decipher.setAuthTag(unb64(tag))
  return Buffer.concat([decipher.update(unb64(encrypted)), decipher.final()]).toString("utf8")
}

export function normalizeTaskCloudUrl(value) {
  const raw = String(value || "").trim().replace(/\/+$/, "")
  if (!raw) throw new Error("Vui lòng nhập URL Motion Task Cloud")
  let url
  try { url = new URL(raw) } catch { throw new Error("URL Motion Task Cloud không hợp lệ") }
  const local = ["localhost", "127.0.0.1", "::1"].includes(url.hostname)
  if (url.protocol !== "https:" && !(process.env.NODE_ENV !== "production" && local)) {
    throw new Error("Motion Task Cloud phải dùng URL HTTPS")
  }
  if (url.username || url.password || url.search || url.hash) throw new Error("URL Task Cloud không được chứa tài khoản, query hoặc fragment")
  return `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, "")}`
}

function safeError(value, limit = 1000) {
  return String(value || "").replace(/[\u0000-\u001f]/g, " ").trim().slice(0, limit)
}

export async function validateTaskCloudConnection(url, token) {
  const taskCloudUrl = normalizeTaskCloudUrl(url)
  const apiKey = String(token || "").trim()
  if (apiKey.length < 24) throw new Error("API key Motion Task Cloud không hợp lệ")
  const response = await fetch(`${taskCloudUrl}/api/connector/ping`, {
    signal: AbortSignal.timeout(15_000),
    headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
  })
  const text = await response.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = null }
  if (!response.ok) throw new Error(safeError(data?.statusMessage || data?.message || data?.error || text || `Task Cloud HTTP ${response.status}`))
  if (!data?.ok || data?.service !== "motion-task-cloud" || data?.mode !== "worker" || !data?.providerId) {
    throw new Error("API key này không phải key worker do Motion Task Cloud cấp")
  }
  return { url: taskCloudUrl, token: apiKey, workerId: String(data.providerId), version: Number(data.version || 0) }
}

async function row() {
  const result = await query("SELECT * FROM task_cloud_auto_settings WHERE singleton_id=true")
  return result.rows[0] || null
}

export async function getTaskCloudConnection({ fresh = false } = {}) {
  if (!fresh && cached && Date.now() - cachedAt < CACHE_MS) return cached
  const setting = await row()
  let url = String(setting?.task_cloud_url || "").trim().replace(/\/+$/, "")
  let token = ""
  let source = "database"
  if (url && setting?.task_cloud_token_cipher) {
    try { token = decryptTaskCloudToken(setting.task_cloud_token_cipher) }
    catch (error) {
      cached = { configured: false, url, token: "", workerId: setting?.task_cloud_worker_id || "", source, error: safeError(error?.message || error) }
      cachedAt = Date.now()
      return cached
    }
  }
  // Tương thích Box .165 đang chạy: env chỉ là fallback cho tới khi lưu key qua giao diện mới.
  if (!url || !token) {
    url = String(process.env.TASK_CLOUD_URL || "").trim().replace(/\/+$/, "")
    token = String(process.env.TASK_CLOUD_TOKEN || "").trim()
    source = "environment"
  }
  cached = {
    configured: /^https:\/\//i.test(url) && token.length >= 24,
    url,
    token,
    workerId: setting?.task_cloud_worker_id || "",
    source,
    error: setting?.connection_error || "",
    checkedAt: setting?.connection_checked_at || null,
  }
  cachedAt = Date.now()
  return cached
}

export async function saveTaskCloudConnection({ url, token, userId }) {
  const checked = await validateTaskCloudConnection(url, token)
  const result = await query(
    `UPDATE task_cloud_auto_settings
     SET task_cloud_url=$1, task_cloud_token_cipher=$2, task_cloud_worker_id=$3,
         connection_checked_at=now(), connection_error=null, updated_by=$4, updated_at=now()
     WHERE singleton_id=true RETURNING *`,
    [checked.url, encryptTaskCloudToken(checked.token), checked.workerId, userId || null],
  )
  cached = null
  cachedAt = 0
  return publicTaskCloudConnection(result.rows[0], await getTaskCloudConnection({ fresh: true }))
}

export async function clearTaskCloudConnection(userId) {
  const result = await query(
    `UPDATE task_cloud_auto_settings
     SET enabled=false, state=CASE WHEN active_task_id IS NULL THEN 'off' ELSE state END,
         task_cloud_url='', task_cloud_token_cipher='', task_cloud_worker_id='',
         connection_checked_at=null, connection_error=null, updated_by=$1, updated_at=now()
     WHERE singleton_id=true RETURNING *`,
    [userId || null],
  )
  cached = null
  cachedAt = 0
  return publicTaskCloudConnection(result.rows[0])
}

export function publicTaskCloudConnection(setting, fallback = null) {
  const usingEnv = !setting?.task_cloud_url && Boolean(fallback?.configured)
  const url = setting?.task_cloud_url || (usingEnv ? fallback.url : "") || ""
  const configured = usingEnv
    ? Boolean(fallback?.configured)
    : Boolean(
        setting?.task_cloud_url
        && setting?.task_cloud_token_cipher
        && fallback?.source === "database"
        && fallback?.configured,
      )
  return {
    configured,
    url,
    workerId: setting?.task_cloud_worker_id || fallback?.workerId || "",
    source: usingEnv ? "environment" : "database",
    checkedAt: setting?.connection_checked_at || fallback?.checkedAt || null,
    lastError: setting?.connection_error || fallback?.error || "",
  }
}

export async function taskCloudConnectionStatus() {
  const setting = await row()
  const fallback = await getTaskCloudConnection({ fresh: true })
  return publicTaskCloudConnection(setting, fallback)
}
// #endregion
