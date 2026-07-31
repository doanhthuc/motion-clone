import express from "express"
import { waitForDb } from "./db.js"
import { runMigrations } from "./migrate.js"
import { ensureBucket } from "./storage.js"
import { bootstrapSuperAdmin } from "./auth/bootstrap.js"
import jobsRouter from "./routes/jobs.js"
import audioRouter from "./routes/audio.js"
import authRouter from "./routes/auth.js"
import usersRouter from "./routes/users.js"
import storageRouter from "./routes/storage.js"
import workflowsRouter from "./routes/workflows.js"
import workflowAiRouter from "./routes/workflow-ai.js"
import aiProvidersRouter from "./routes/ai-providers.js"
import mediaJobsRouter from "./routes/media-jobs.js"
import jobReportsRouter from "./routes/job-reports.js"
import adminVpsRouter from "./routes/admin-vps.js"
import modelRefsRouter from "./routes/model-refs.js"
import voicesRouter from "./routes/voices.js"
import modelsRouter from "./routes/models.js"
import adminBackupRouter from "./routes/admin-backup.js"
import socialImportsRouter from "./routes/social-imports.js"
import socialAccountsRouter from "./routes/social-accounts.js"
import socialPostsRouter from "./routes/social-posts.js"
import contentPlansRouter from "./routes/content-plans.js"
import socialAppConfigRouter from "./routes/social-app-config.js"
import taskCloudAutoRouter from "./routes/task-cloud-auto.js"

const app = express()

// CORS — FE motions gọi trực tiếp với session Bearer (endpoint session KHÔNG có secret).
// CORS_ORIGINS = csv origin được phép (mặc định '*'). Header Authorization/X-API-Key/X-Client-Id.
const CORS_ORIGINS = (process.env.CORS_ORIGINS || "*").split(",").map((s) => s.trim()).filter(Boolean)
app.use((req, res, next) => {
  const origin = req.get("origin")
  // #region ALD 12/06/2026 - CORS: PHẢN CHIẾU origin thay vì trả '*' literal. Lý do: khi FE gửi credentials
  // (cookie/Authorization với mode include) thì Access-Control-Allow-Origin='*' bị browser CHẶN — phải echo
  // đúng origin + Allow-Credentials:true. '*' = cho mọi origin (phản chiếu); hoặc whitelist qua CORS_ORIGINS.
  let allow = ""
  if (CORS_ORIGINS.includes("*")) allow = origin || "*"
  else if (origin && CORS_ORIGINS.includes(origin)) allow = origin
  if (allow) {
    res.set("Access-Control-Allow-Origin", allow)
    if (allow !== "*") { res.set("Vary", "Origin"); res.set("Access-Control-Allow-Credentials", "true") }
  }
  // #endregion
  res.set("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
  res.set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key, X-Worker-Token, X-Session-Token, X-Client-Id")
  res.set("Access-Control-Max-Age", "86400")
  if (req.method === "OPTIONS") return res.status(204).end()
  next()
})

app.use(express.json({ limit: "2mb" }))

app.get("/health", (_req, res) => res.json({ status: "ok", service: "motion-backend-api", version: "1.0.0" }))
app.use("/", authRouter)
app.use("/", usersRouter)
app.use("/", jobsRouter)
app.use("/", audioRouter)
app.use("/", storageRouter)
app.use("/", workflowsRouter)
app.use("/", workflowAiRouter)
app.use("/", aiProvidersRouter)
app.use("/", mediaJobsRouter)
app.use("/", jobReportsRouter)
app.use("/", adminVpsRouter)
app.use("/", modelRefsRouter)
app.use("/", voicesRouter)
app.use("/", modelsRouter)
app.use("/", adminBackupRouter)
app.use("/", socialImportsRouter)
app.use("/", socialAccountsRouter)
app.use("/", socialPostsRouter)
app.use("/", contentPlansRouter)
app.use("/", socialAppConfigRouter)
app.use("/", taskCloudAutoRouter)

const PORT = Number(process.env.PORT || 8080)

async function main() {
  await waitForDb()
  await runMigrations()         // ALD 04/06/2026 - tự áp db/init/*.sql idempotent → docker up (volume cũ) không thiếu cột
  await bootstrapSuperAdmin()   // tạo/nâng admin gốc từ SUPER_ADMIN (idempotent) — tránh khoá ra ngoài
  await ensureBucket()
  app.listen(PORT, () => console.log(`[api] listening on :${PORT}`))
}
main().catch((e) => { console.error("[api] fatal:", e); process.exit(1) })
