#!/usr/bin/env node
import { execFileSync } from "node:child_process"
import { existsSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const DEFAULT_SEED = join(ROOT, "db", "seeds", "workflow_templates.sql")
const sqlFile = process.argv[2] ? join(process.cwd(), process.argv[2]) : DEFAULT_SEED

if (!existsSync(sqlFile)) {
  console.error(`[workflow-seed] Không thấy file SQL: ${sqlFile}`)
  process.exit(1)
}

const sql = readFileSync(sqlFile, "utf8")
if (!sql.trim()) {
  console.error(`[workflow-seed] File SQL rỗng: ${sqlFile}`)
  process.exit(1)
}

const databaseUrl = process.env.DATABASE_URL || ""
if (databaseUrl) {
  run("psql", ["-v", "ON_ERROR_STOP=1", databaseUrl], sql)
  console.log(`[workflow-seed] Applied ${sqlFile} qua DATABASE_URL`)
  process.exit(0)
}

const pgService = process.env.WORKFLOW_SEED_DB_SERVICE || "postgres"
const pgUser = process.env.POSTGRES_USER || "motion"
const pgDb = process.env.POSTGRES_DB || "motion"

run("docker", ["compose", "exec", "-T", pgService, "psql", "-v", "ON_ERROR_STOP=1", "-U", pgUser, "-d", pgDb], sql, ROOT)
console.log(`[workflow-seed] Applied ${sqlFile} qua docker compose service '${pgService}'`)

function run(cmd, args, input, cwd = process.cwd()) {
  try {
    execFileSync(cmd, args, {
      cwd,
      input,
      stdio: ["pipe", "inherit", "inherit"],
      maxBuffer: 50 * 1024 * 1024,
    })
  } catch (e) {
    console.error(`[workflow-seed] Lỗi khi chạy ${cmd}: ${e?.message || e}`)
    process.exit(e?.status || 1)
  }
}
