// Storage adapter — MinIO (S3-compatible), giống Supabase Storage: bucket + presigned URL.
import { S3Client, PutObjectCommand, GetObjectCommand, DeleteObjectCommand, HeadBucketCommand, CreateBucketCommand, ListObjectsV2Command } from "@aws-sdk/client-s3"
import { getSignedUrl } from "@aws-sdk/s3-request-presigner"
import { NodeHttpHandler } from "@smithy/node-http-handler"
import { Agent as HttpAgent } from "node:http"
import { Agent as HttpsAgent } from "node:https"
import crypto from "node:crypto"

const BUCKET = process.env.STORAGE_BUCKET || "motion"
const TTL = Number(process.env.SIGNED_URL_TTL_SEC || 604800)

// ALD 15/06/2026 - FIX /media TREO VÔ HẠN: S3 client mặc định giữ keep-alive socket; sau ~1h idle, MinIO/LB
// đóng socket nhưng client KHÔNG biết → getObject treo mãi (không timeout) → phải restart api. Khắc phục:
//   • httpAgent timeout 25s = ĐÓNG socket idle trước khi nó chết → request sau mở socket tươi (hết stale).
//   • connectTimeout 15s + requestTimeout 300s (đủ cho stream/upload lớn; data chảy là reset) = backstop, SDK retry.
// ALD 15/06/2026 - connectTimeout NỚI 5s→15s + cho chỉnh qua env: lúc box tải nặng (RAM/swap thrash) MinIO nhận
// kết nối chậm >5s → 5s chém OAN upload ("socket did not establish connection 5000ms"). Tunable không cần rebuild.
const _S3_EP = process.env.S3_ENDPOINT || "http://minio:9000"
const _s3RequestHandler = new NodeHttpHandler({
  connectionTimeout: Number(process.env.S3_CONNECT_TIMEOUT_MS || 15000),
  requestTimeout: Number(process.env.S3_REQUEST_TIMEOUT_MS || 300000),
  ...(_S3_EP.startsWith("https")
    ? { httpsAgent: new HttpsAgent({ keepAlive: true, timeout: 25000, maxSockets: Number(process.env.S3_MAX_SOCKETS || 256) }) }
    : { httpAgent: new HttpAgent({ keepAlive: true, timeout: 25000, maxSockets: Number(process.env.S3_MAX_SOCKETS || 256) }) }),
})

// Client nội bộ (API ↔ MinIO trong stack) — dùng put/get stream.
const s3 = new S3Client({
  endpoint: _S3_EP,
  region: process.env.S3_REGION || "us-east-1",
  credentials: { accessKeyId: process.env.S3_ACCESS_KEY, secretAccessKey: process.env.S3_SECRET_KEY },
  forcePathStyle: true,
  requestHandler: _s3RequestHandler,
  maxAttempts: 3,
})

// Client public (endpoint mà CLIENT bên ngoài truy cập) — dùng để PRESIGN URL tải trực tiếp.
const s3public = new S3Client({
  endpoint: process.env.S3_PUBLIC_ENDPOINT || process.env.S3_ENDPOINT || "http://localhost:9000",
  region: process.env.S3_REGION || "us-east-1",
  credentials: { accessKeyId: process.env.S3_ACCESS_KEY, secretAccessKey: process.env.S3_SECRET_KEY },
  forcePathStyle: true,
})

export async function ensureBucket() {
  try { await s3.send(new HeadBucketCommand({ Bucket: BUCKET })) }
  catch { try { await s3.send(new CreateBucketCommand({ Bucket: BUCKET })) } catch { /* race với createbuckets */ } }
}

export async function putObject(key, body, contentType) {
  await s3.send(new PutObjectCommand({ Bucket: BUCKET, Key: key, Body: body, ContentType: contentType }))
  return key
}

// Stream object (cho API serve cho worker tải input).
export async function getObjectStream(key) {
  const out = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }))
  return { stream: out.Body, contentType: out.ContentType, length: out.ContentLength }
}

// ALD 03/06/2026 - Stream kèm HTTP Range (vd "bytes=0-1023") → <video> seek được. Safari/nhiều player
// BẮT BUỘC server trả 206 Partial Content mới phát; trước đây /media trả 200 nguyên file → video không chạy.
export async function getObjectRange(key, rangeHeader) {
  const out = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key, Range: rangeHeader }))
  return { stream: out.Body, contentType: out.ContentType, length: out.ContentLength, contentRange: out.ContentRange }
}

// ALD 16/06/2026 - Pipe stream media + ĐÓNG upstream khi CLIENT NGẮT (đổi/đóng video, chuyển trang, video player
// dừng sau khi đủ buffer). Trước đây route /media + /media-file chỉ `stream.pipe(res)` KHÔNG cleanup → mỗi lần
// xem-dở rò 1 socket S3 → đầy maxSockets → connect timeout 15s → /media TREO sau vài phút. Dùng thay stream.pipe(res).
export function pipeObjectStream(stream, res) {
  const close = () => { try { stream.destroy() } catch { /* noop */ } }
  res.on("close", close)
  stream.on("error", () => { try { if (!res.headersSent) res.status(404); if (!res.writableEnded) res.end() } catch { /* noop */ } })
  stream.pipe(res)
}

// #region ALD 13/06/2026 - Backup & Restore: liệt kê MỌI object trong bucket (paginate) + tên bucket vật lý.
// Dùng cho /admin/backup (đóng gói toàn bộ MinIO, kể cả output job KHÔNG nằm trong bảng storage_files).
export function getBucketName() { return BUCKET }

// Trả danh sách { key, size } của TẤT CẢ object trong bucket (tự phân trang ContinuationToken).
export async function listAllObjects() {
  const keys = []
  let token
  do {
    const out = await s3.send(new ListObjectsV2Command({ Bucket: BUCKET, ContinuationToken: token }))
    for (const o of out.Contents || []) keys.push({ key: o.Key, size: o.Size })
    token = out.IsTruncated ? out.NextContinuationToken : undefined
  } while (token)
  return keys
}
// #endregion

export async function deleteObject(key) {
  if (!key) return
  try { await s3.send(new DeleteObjectCommand({ Bucket: BUCKET, Key: key })) } catch { /* đã xóa / không tồn tại */ }
}

// Presigned URL public (giống Supabase signed URL) — client tải trực tiếp từ MinIO.
export async function presignGet(key) {
  if (!key) return null
  return getSignedUrl(s3public, new GetObjectCommand({ Bucket: BUCKET, Key: key }), { expiresIn: TTL })
}

// #region ALD 31/05/2026 - Signed media URL (HMAC, stateless) qua API HTTPS — cho browser <img>/<video>
// (không cần header → dùng được trực tiếp). Thay presigned MinIO (vốn trỏ host nội bộ/HTTP).
const MEDIA_SECRET = process.env.SESSION_JWT_SECRET || "media-fallback-secret"
function _sig(key, exp) {
  return crypto.createHmac("sha256", MEDIA_SECRET).update(`${key}|${exp}`).digest("hex")
}
export function verifyMediaSig(key, exp, sig) {
  if (!key || !exp || !sig) return false
  if (Number(exp) < Math.floor(Date.now() / 1000)) return false
  try { return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(_sig(key, exp))) } catch { return false }
}
/**
 * URL media cho BROWSER: nếu có PUBLIC_BASE_URL → /media/<key>?exp&sig (HTTPS qua API, HMAC,
 * không cần auth header). Không có PUBLIC_BASE_URL → fallback presigned MinIO.
 */
export async function browserUrl(key) {
  if (!key) return null
  const base = process.env.PUBLIC_BASE_URL
  if (!base) return await presignGet(key)
  const exp = Math.floor(Date.now() / 1000) + TTL
  return `${base.replace(/\/$/, "")}/media/${key}?exp=${exp}&sig=${_sig(key, exp)}`
}
// #endregion
