// #region ALD 28/06/2026 - Social/media URL import for workflow Input nodes.
// POST /media-imports/social { url, contentType: "video"|"audio" }
//   video → yt-dlp downloads MP4, stores as storage_files bucket motion-jobs.
//   audio → yt-dlp extracts MP3, stores as audio_files for Voice/Audio inputs.
import { Router } from "express"
import crypto from "node:crypto"
import { spawn } from "node:child_process"
import { constants as fsConstants, createWriteStream } from "node:fs"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { Readable, Transform } from "node:stream"
import { pipeline } from "node:stream/promises"
import { query } from "../db.js"
import { putObject, browserUrl } from "../storage.js"
import { sessionAuth } from "../auth/session.js"

const router = Router()
const MAX_VIDEO_BYTES = Number(process.env.SOCIAL_IMPORT_MAX_VIDEO_BYTES || 500 * 1024 * 1024)
const MAX_AUDIO_BYTES = Number(process.env.SOCIAL_IMPORT_MAX_AUDIO_BYTES || 60 * 1024 * 1024)
const YTDLP_BIN_CANDIDATES = [
  process.env.YTDLP_BIN,
  path.join(os.homedir(), ".local/bin/yt-dlp"),
  "yt-dlp",
].filter(Boolean)
const SOCIAL_USER_AGENT = process.env.SOCIAL_IMPORT_USER_AGENT
  || "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
const YTDLP_PROFILES = [
  { name: "direct", args: [] },
  // TikTok/Facebook đôi lúc trả HTML khác cho request Python. curl-cffi của
  // yt-dlp sẽ tạo TLS/browser fingerprint thật thay vì chỉ đổi User-Agent.
  { name: "chrome", args: ["--impersonate", "chrome"] },
]
// TikTok đôi lúc trả metadata có duration/videoID nhưng cố tình để trống
// playAddr. Resolver chỉ được gọi sau khi cả hai profile yt-dlp thất bại.
// Có thể thay endpoint bằng dịch vụ nội bộ mà không cần sửa code.
const TIKTOK_RESOLVER_URL = process.env.SOCIAL_TIKTOK_RESOLVER_URL
  || "https://www.tikwm.com/api/"

router.use("/media-imports", sessionAuth)

function isHostOrSubdomain(host, root) {
  return host === root || host.endsWith(`.${root}`)
}

function isTikTokUrl(url) {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "")
    return isHostOrSubdomain(host, "tiktok.com")
  } catch {
    return false
  }
}

function assertTrustedTikTokMediaUrl(raw) {
  let parsed
  try { parsed = new URL(String(raw || "")) } catch { throw new Error("Resolver trả URL media không hợp lệ") }
  if (parsed.protocol !== "https:") throw new Error("Resolver chỉ được trả media HTTPS")
  const host = parsed.hostname.toLowerCase()
  const allowed = /(^|\.)tiktokcdn(?:-[a-z0-9-]+)?\.com$/.test(host)
    || /(^|\.)tiktokv\.com$/.test(host)
    || /(^|\.)byteoversea\.com$/.test(host)
    || /(^|\.)ibytedtos\.com$/.test(host)
    || /(^|\.)muscdn\.com$/.test(host)
  if (!allowed) throw new Error(`Resolver trả media ngoài CDN TikTok (${host})`)
  return parsed.toString()
}

function assertSupportedUrl(raw) {
  // App TikTok/Facebook thường copy cả caption kèm URL. Lấy URL đầu tiên giống
  // các downloader web thay vì bắt người dùng tự xoá phần chữ thừa.
  const input = String(raw || "").trim()
  const firstUrl = input.match(/https?:\/\/[^\s<>"']+/i)?.[0]
    ?.replace(/[),.;!?]+$/g, "")
  let u
  try { u = new URL(firstUrl || input) } catch { throw new Error("Link không hợp lệ") }
  if (!/^https?:$/.test(u.protocol)) throw new Error("Chỉ hỗ trợ http/https")
  const host = u.hostname.toLowerCase().replace(/^www\./, "")
  const ok = host === "youtu.be"
    || isHostOrSubdomain(host, "youtube.com")
    || isHostOrSubdomain(host, "tiktok.com")
    || host === "fb.watch"
    || isHostOrSubdomain(host, "facebook.com")
  if (!ok) throw new Error("Chỉ hỗ trợ link Facebook, TikTok hoặc YouTube")
  return u.toString()
}

async function canonicalizeSocialUrl(raw) {
  const valid = assertSupportedUrl(raw)
  let u
  try { u = new URL(valid) } catch { return valid }
  const host = u.hostname.toLowerCase().replace(/^www\./, "")
  const isFbShare = isHostOrSubdomain(host, "facebook.com") && /^\/share\//.test(u.pathname)
  const isTikTokShort = host === "vm.tiktok.com"
    || host === "vt.tiktok.com"
    || host === "m.tiktok.com"
    || /^\/t\//.test(u.pathname)
  if (!isFbShare && !isTikTokShort) return valid
  try {
    let current = valid
    // Theo redirect từng bước và validate hostname trước khi request bước kế
    // tiếp để không biến endpoint canonicalize thành open-redirect SSRF.
    for (let i = 0; i < 6; i += 1) {
      const res = await fetch(current, {
        method: "GET",
        redirect: "manual",
        signal: AbortSignal.timeout(12_000),
        headers: {
          "user-agent": SOCIAL_USER_AGENT,
          accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
      })
      const location = res.headers.get("location")
      if (!location || res.status < 300 || res.status >= 400) return current
      current = assertSupportedUrl(new URL(location, current).toString())
    }
    return current
  } catch {
    return valid
  }
}

async function availableYtdlpBins() {
  const bins = []
  const seen = new Set()
  for (const candidate of YTDLP_BIN_CANDIDATES) {
    if (!candidate || seen.has(candidate)) continue
    seen.add(candidate)
    if (!path.isAbsolute(candidate)) {
      // PATH thường trỏ lại đúng ~/.local/bin. Chỉ dùng tên lệnh khi chưa có
      // executable tuyệt đối hợp lệ để không chạy lặp cùng một bản yt-dlp.
      if (!bins.length) bins.push(candidate)
      continue
    }
    try {
      await fs.access(candidate, fsConstants.X_OK)
      const real = await fs.realpath(candidate).catch(() => candidate)
      if (!seen.has(real)) seen.add(real)
      bins.push(candidate)
    } catch {
      // Bỏ executable tuyệt đối không tồn tại; không đưa ENOENT giả vào lỗi trả
      // cho client sau khi một extractor hợp lệ đã thất bại.
    }
  }
  return bins
}

function extOf(name, fallback) {
  const ext = path.extname(name || "").replace(/^\./, "").toLowerCase()
  return ext || fallback
}

function safeName(name, fallback) {
  return String(name || fallback || "media")
    .replace(/[^\p{L}\p{N}._ -]+/gu, "_")
    .replace(/\s+/g, "_")
    .slice(0, 90) || fallback
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { ...opts, stdio: ["ignore", "pipe", "pipe"] })
    let stdout = "", stderr = ""
    child.stdout.on("data", (d) => { stdout += d })
    child.stderr.on("data", (d) => { stderr += d })
    child.on("error", (error) => {
      // Giữ nguyên `code=ENOENT` của Node để phân biệt thật sự không có executable
      // với chữ ENOENT nằm trong stderr của extractor/ffmpeg.
      error.command = cmd
      reject(error)
    })
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr })
      else reject(new Error(`${cmd} exited ${code}: ${(stderr || stdout).slice(-1200)}`))
    })
  })
}

async function fetchJson(url, timeoutMs = 30_000) {
  const response = await fetch(url, {
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMs),
    headers: {
      "user-agent": SOCIAL_USER_AGENT,
      accept: "application/json",
    },
  })
  if (!response.ok) throw new Error(`Resolver HTTP ${response.status}`)
  const contentType = String(response.headers.get("content-type") || "").toLowerCase()
  if (!contentType.includes("json")) throw new Error("Resolver không trả JSON")
  return response.json()
}

async function downloadTrustedTikTokMedia(rawUrl, outputPath) {
  let current = assertTrustedTikTokMediaUrl(rawUrl)
  for (let redirect = 0; redirect < 6; redirect += 1) {
    const response = await fetch(current, {
      redirect: "manual",
      signal: AbortSignal.timeout(90_000),
      headers: {
        "user-agent": SOCIAL_USER_AGENT,
        accept: "video/mp4,video/*;q=0.9,application/octet-stream;q=0.8",
        referer: "https://www.tiktok.com/",
      },
    })
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location")
      if (!location) throw new Error(`TikTok CDN redirect ${response.status} nhưng thiếu Location`)
      current = assertTrustedTikTokMediaUrl(new URL(location, current).toString())
      continue
    }
    if (!response.ok || !response.body) throw new Error(`TikTok CDN HTTP ${response.status}`)

    const declared = Number(response.headers.get("content-length") || 0)
    if (declared > MAX_VIDEO_BYTES) throw new Error(`Video TikTok quá lớn (${Math.ceil(declared / 1024 / 1024)}MB)`)
    let received = 0
    const limiter = new Transform({
      transform(chunk, encoding, callback) {
        received += chunk.length
        if (received > MAX_VIDEO_BYTES) {
          callback(new Error(`Video TikTok vượt giới hạn ${Math.round(MAX_VIDEO_BYTES / 1024 / 1024)}MB`))
          return
        }
        callback(null, chunk)
      },
    })
    await pipeline(Readable.fromWeb(response.body), limiter, createWriteStream(outputPath, { flags: "wx" }))
    return received
  }
  throw new Error("TikTok CDN redirect quá nhiều lần")
}

async function downloadTikTokViaResolver(url, dir) {
  const endpoint = new URL(TIKTOK_RESOLVER_URL)
  if (endpoint.protocol !== "https:") throw new Error("SOCIAL_TIKTOK_RESOLVER_URL phải dùng HTTPS")
  endpoint.searchParams.set("url", url)
  const payload = await fetchJson(endpoint.toString())
  const data = payload?.data
  if (Number(payload?.code) !== 0 || !data) {
    throw new Error(String(payload?.msg || "Resolver không tìm thấy video TikTok"))
  }
  const mediaUrl = data.hdplay || data.play
  if (!mediaUrl) throw new Error("Resolver không trả luồng MP4")

  await resetDir(dir)
  const outputPath = path.join(dir, "tiktok-resolved.mp4")
  const size = await downloadTrustedTikTokMedia(mediaUrl, outputPath)
  const streams = await mediaStreams(outputPath)
  if (!streams.hasVideo) throw new Error("Resolver chỉ trả audio, không có video stream")
  const stat = await fs.stat(outputPath)
  return {
    path: outputPath,
    name: `tiktok-${String(data.id || crypto.randomUUID())}.mp4`,
    mtimeMs: stat.mtimeMs,
    size: size || stat.size,
    ...streams,
    sourceMode: "video:tiktok-resolver",
    synthetic: false,
  }
}

function isMissingBinaryError(err) {
  // Chỉ lỗi spawn của chính executable mới là "chưa cài yt-dlp". Trước đây
  // `.includes('ENOENT')` bắt nhầm cả lỗi file tạm/ffmpeg trong stderr, làm cùng
  // một server lúc báo có yt-dlp, lúc lại báo chưa cài.
  return err?.code === "ENOENT"
    || /^spawn(?:sync)?\s+.+\s+enoent$/i.test(String(err?.message || "").trim())
}

function isExtractorParseError(err) {
  const msg = String(err?.message || "").toLowerCase()
  return msg.includes("cannot parse data") || msg.includes("unable to extract") || msg.includes("unsupported url")
}

function formatYtdlpError(url, attempts) {
  const host = (() => {
    try { return new URL(url).hostname.toLowerCase() } catch { return "" }
  })()
  const hasParse = attempts.some((a) => isExtractorParseError(a.error))
  if (isHostOrSubdomain(host, "facebook.com") || host === "fb.watch") {
    if (hasParse) {
      return "Facebook chưa cung cấp được luồng video công khai cho link này. Hệ thống đã thử tải trực tiếp và giả lập Chrome; hãy kiểm tra bài còn công khai rồi thử lại."
    }
  }
  if (hasParse) {
    return "TikTok chưa cung cấp được luồng video công khai cho link này. Hệ thống đã thử tải trực tiếp và giả lập Chrome; hãy kiểm tra video còn công khai rồi thử lại."
  }
  return `Không tải được media từ link đã nhập sau ${attempts.length || 1} lần thử.`
}

async function probeDuration(filePath) {
  try {
    const { stdout } = await run("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filePath])
    const n = Number.parseFloat(stdout.trim())
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

async function detectShotCount(filePath, durationSec = null) {
  try {
    const threshold = String(process.env.SOCIAL_IMPORT_SCENE_THRESHOLD || 0.24)
    const minGap = Number.parseFloat(process.env.SOCIAL_IMPORT_SCENE_MIN_GAP_SEC || "0.45") || 0.45
    const { stderr } = await run("ffmpeg", [
      "-hide_banner",
      "-nostdin",
      "-v", "info",
      "-i", filePath,
      "-filter:v", `select='gt(scene,${threshold})',showinfo`,
      "-an",
      "-f", "null",
      "-"
    ])
    const pts = []
    for (const m of String(stderr || "").matchAll(/pts_time:([0-9.]+)/g)) {
      const t = Number.parseFloat(m[1])
      if (Number.isFinite(t)) pts.push(t)
    }
    let cuts = 0
    let last = -Infinity
    for (const t of pts) {
      if ((t - last) >= minGap) {
        cuts += 1
        last = t
      }
    }
    let shots = Math.max(1, cuts + 1)
    const dur = Number.isFinite(durationSec) ? durationSec : null
    if (dur) {
      shots = Math.min(shots, Math.max(1, Math.round(dur / 0.9)))
      shots = Math.max(shots, Math.max(1, Math.round(dur / 3.5)))
    }
    return Math.max(1, Math.min(12, shots))
  } catch {
    return null
  }
}

async function newestFile(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true })
  const files = []
  for (const e of entries) {
    if (!e.isFile()) continue
    const p = path.join(dir, e.name)
    const st = await fs.stat(p)
    files.push({ path: p, name: e.name, mtimeMs: st.mtimeMs, size: st.size })
  }
  files.sort((a, b) => b.mtimeMs - a.mtimeMs)
  return files[0] || null
}

async function mediaStreams(filePath) {
  try {
    const { stdout } = await run("ffprobe", [
      "-v", "error",
      "-show_entries", "stream=codec_type,codec_name,width,height",
      "-of", "json",
      filePath,
    ])
    const streams = JSON.parse(stdout || "{}").streams || []
    return {
      hasVideo: streams.some((stream) => stream.codec_type === "video"),
      hasAudio: streams.some((stream) => stream.codec_type === "audio"),
      streams,
    }
  } catch {
    return { hasVideo: false, hasAudio: false, streams: [] }
  }
}

async function filesByNewest(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    if (!entry.isFile() || /\.(part|ytdl)$/i.test(entry.name)) continue
    const filePath = path.join(dir, entry.name)
    const stat = await fs.stat(filePath)
    files.push({ path: filePath, name: entry.name, mtimeMs: stat.mtimeMs, size: stat.size })
  }
  return files.sort((a, b) => b.mtimeMs - a.mtimeMs)
}

async function newestMediaFile(dir, predicate) {
  for (const file of await filesByNewest(dir)) {
    const streams = await mediaStreams(file.path)
    if (predicate(streams, file)) return { ...file, ...streams }
  }
  return null
}

async function resetDir(dir) {
  await fs.rm(dir, { recursive: true, force: true }).catch(() => {})
  await fs.mkdir(dir, { recursive: true })
}

async function downloadSocial(url, contentType) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "social-import-"))
  const outTpl = path.join(dir, "%(title).90s.%(ext)s")
  try {
    const baseArgs = [
      "--ignore-config",
      "--no-playlist",
      "--restrict-filenames",
      "--newline",
      "--user-agent", SOCIAL_USER_AGENT,
      "--socket-timeout", "30",
      "--retries", "3",
      "--extractor-retries", "5",
      "--fragment-retries", "5",
      "--concurrent-fragments", "4",
    ]
    const attempts = []
    const bins = await availableYtdlpBins()
    for (const bin of bins) {
      for (const profile of YTDLP_PROFILES) {
        try {
          await resetDir(dir)
          const args = [...baseArgs, ...profile.args, "-o", outTpl]
          if (contentType === "audio") {
            args.push("-x", "--audio-format", "mp3", "--audio-quality", "0")
          } else {
            // Chỉ nhận file có video stream thật. Không biến ảnh bìa + MP3 thành
            // MP4 vì client cần video driver có chuyển động thực.
            args.push(
              "-f", "bestvideo*[vcodec!=none]+bestaudio/bestvideo*[vcodec!=none]",
              "--merge-output-format", "mp4",
              "--remux-video", "mp4",
              "-S", "vcodec:h264,acodec:aac,res,fps",
            )
          }
          args.push(url)
          await run(bin, args, { cwd: dir })

          const file = contentType === "audio"
            ? await newestFile(dir)
            : await newestMediaFile(dir, ({ hasVideo }, candidate) => hasVideo && !/\.(?:jpe?g|png|webp)$/i.test(candidate.name))
          if (!file) throw new Error(contentType === "audio" ? "Không tìm thấy audio sau khi tải" : "Extractor chỉ trả audio, không có video stream")
          return {
            ...file,
            sourceMode: `${contentType}:${profile.name}`,
            cleanup: () => fs.rm(dir, { recursive: true, force: true }).catch(() => {}),
          }
        } catch (e) {
          attempts.push({ bin: `${bin}:${profile.name}`, error: e })
          if (isMissingBinaryError(e)) break
        }
      }
    }
    if (contentType === "video" && isTikTokUrl(url)) {
      try {
        const resolved = await downloadTikTokViaResolver(url, dir)
        return {
          ...resolved,
          cleanup: () => fs.rm(dir, { recursive: true, force: true }).catch(() => {}),
        }
      } catch (error) {
        attempts.push({ bin: "tiktok-resolver", error })
      }
    }
    const lastErr = attempts[attempts.length - 1]?.error || null
    if (lastErr && attempts.every((a) => isMissingBinaryError(a.error))) throw lastErr
    console.warn("[social-import] yt-dlp failed", {
      host: (() => { try { return new URL(url).hostname } catch { return "unknown" } })(),
      contentType,
      attempts: attempts.map((attempt) => ({
        profile: attempt.bin,
        error: String(attempt.error?.message || attempt.error).slice(-500),
      })),
    })
    throw new Error(formatYtdlpError(url, attempts))
  } catch (e) {
    await fs.rm(dir, { recursive: true, force: true }).catch(() => {})
    if (isMissingBinaryError(e)) {
      throw new Error("Server chưa cài yt-dlp. Cài yt-dlp hoặc set YTDLP_BIN.")
    }
    throw e
  }
}

// #region ALD 16/07/2026 - Dùng chung importer cho API và Task Cloud Auto worker.
// Worker nền không có session trình duyệt nhưng vẫn phải đi đúng một pipeline tải/kiểm tra
// video social, tránh hai implementation lệch nhau hoặc lúc tải được lúc không.
export async function importSocialMedia({ url: rawUrl, contentType: requestedType = "video", userId }) {
  let downloaded
  try {
    const url = await canonicalizeSocialUrl(rawUrl)
    const contentType = requestedType === "audio" ? "audio" : "video"
    downloaded = await downloadSocial(url, contentType)
    const max = contentType === "audio" ? MAX_AUDIO_BYTES : MAX_VIDEO_BYTES
    if (downloaded.size > max) throw new Error(`File quá lớn (${Math.round(downloaded.size / 1024 / 1024)}MB)`)

    const buf = await fs.readFile(downloaded.path)
    const originalName = safeName(downloaded.name, contentType === "audio" ? "social.mp3" : "social.mp4")
    if (contentType === "audio") {
      const id = crypto.randomUUID()
      const key = `audio/${id}.mp3`
      await putObject(key, buf, "audio/mpeg")
      const duration = await probeDuration(downloaded.path)
      const { rows } = await query(
        `INSERT INTO audio_files (id, name, storage_key, size_bytes, duration_sec, mime, client_id)
         VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
        [id, originalName.replace(/\.[^.]+$/, ".mp3"), key, buf.length, duration, "audio/mpeg", userId],
      )
      const signedUrl = await browserUrl(key)
      return {
        kind: "audio",
        item: { ...rows[0], size_bytes: Number(rows[0].size_bytes || 0), duration_sec: rows[0].duration_sec == null ? null : Number(rows[0].duration_sec), signedUrl },
      }
    }

    const durationSec = await probeDuration(downloaded.path)
    const shotCount = await detectShotCount(downloaded.path, durationSec)
    const bucket = "motion-jobs"
    const pathName = `${safeName("social-video", "social-video")}/${crypto.randomUUID()}/${originalName.replace(/\.[^.]+$/, ".mp4")}`
    const key = `${bucket}/${pathName}`
    await putObject(key, buf, "video/mp4")
    const { rows } = await query(
      `INSERT INTO storage_files (bucket, path, storage_key, name, size_bytes, mime, user_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [bucket, pathName, key, originalName.replace(/\.[^.]+$/, ".mp4"), buf.length, "video/mp4", userId],
    )
    const signedUrl = await browserUrl(key)
    return {
      kind: "video",
      path: pathName,
      bucket,
      signedUrl,
      item: rows[0],
      meta: {
        durationSec: durationSec == null ? null : Number(durationSec),
        shotCount: shotCount == null ? null : Number(shotCount),
        sourceMode: downloaded.sourceMode || "video",
        synthetic: Boolean(downloaded.synthetic),
      },
    }
  } finally {
    if (downloaded?.cleanup) downloaded.cleanup()
  }
}

router.post("/media-imports/social", async (req, res) => {
  try {
    const result = await importSocialMedia({
      url: req.body?.url,
      contentType: req.body?.contentType,
      userId: req.session.userId,
    })
    res.status(201).json(result)
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})
// #endregion

export default router
// #endregion
