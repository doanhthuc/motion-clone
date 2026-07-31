#!/usr/bin/env node
import { execFileSync } from "node:child_process"
import { mkdirSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const OUT = join(ROOT, "db", "seeds", "workflow_templates.sql")
const SSH_HOST = process.env.WORKFLOW_SEED_SSH || "ubuntu@101.99.25.165"
const PG_CONTAINER = process.env.WORKFLOW_SEED_PG_CONTAINER || "motion-backend-postgres-1"
const PG_USER = process.env.WORKFLOW_SEED_PG_USER || "motion"
const PG_DB = process.env.WORKFLOW_SEED_PG_DB || "motion"

const SQL = `
copy (
  select replace(
    encode(
      convert_to(
        (
          select jsonb_agg(
            jsonb_build_object(
              'slug', slug,
              'name', name,
              'description', description,
              'definition', definition,
              'is_active', is_active
            )
            order by slug
          )::text
          from workflows
        ),
        'utf8'
      ),
      'base64'
    ),
    E'\\n',
    ''
  )
  from (
    select 1
  ) s
) to stdout;
`.trim()

const rawB64 = execFileSync("ssh", [
  "-o", "BatchMode=yes",
  "-o", "ConnectTimeout=10",
  SSH_HOST,
  `docker exec ${PG_CONTAINER} psql -U ${PG_USER} -d ${PG_DB} -Atc ${shellQuote(SQL)}`,
], { encoding: "utf8", maxBuffer: 20 * 1024 * 1024 })

// #region ALD 13/07/2026 - Không cho workflow legacy quay lại qua seed export.
const workflows = JSON.parse(Buffer.from(rawB64.trim(), "base64").toString("utf8"))
  .filter((workflow) => !usesRemovedNode(workflow))
// #endregion
const linuxSlugs = new Set(workflows.map((w) => w.slug))
const macosSlugs = new Set([
  "create-image",
  "lookbook",
  "motion-teaser",
  "thay-do-mau",
])

const exported = [
  ...workflows.filter((w) => linuxSlugs.has(w.slug)).map((w) => templateFor("linux", w)),
  ...workflows.filter((w) => macosSlugs.has(w.slug)).map((w) => templateFor("macos", w)),
]

mkdirSync(dirname(OUT), { recursive: true })
writeFileSync(OUT, renderSql(exported), "utf8")
console.log(`wrote ${OUT} (${exported.length} templates)`)

function usesRemovedNode(workflow) {
  if (["fashion-motion", "linux-fashion-motion", "macos-fashion-motion"].includes(workflow?.slug)) return true
  return (workflow?.definition?.nodes || []).some((node) => node?.type === "fashion-motion" || node?.data?.type === "fashion-motion")
}

function shellQuote(s) {
  return `'${String(s).replaceAll("'", "'\\''")}'`
}

function templateFor(platform, workflow) {
  const def = sanitizeDefinition(workflow.definition, platform)
  return {
    slug: `${platform}-${workflow.slug}`,
    name: `[${platform === "linux" ? "Linux" : "macOS"}] ${workflow.name}`,
    description: platformDescription(platform, workflow),
    definition: def,
    is_active: workflow.is_active !== false,
  }
}

function platformDescription(platform, workflow) {
  const suffix = platform === "linux"
    ? "Template seed cho worker Linux/CUDA."
    : "Template seed cho worker macOS/MPS; cần bật sub-worker/job type phù hợp nếu pipeline nặng."
  return `${workflow.description || ""}\n\n${suffix}`.trim()
}

function sanitizeDefinition(definition, platform) {
  const def = JSON.parse(JSON.stringify(definition || { nodes: [], edges: [] }))
  for (const node of def.nodes || []) {
    const cfg = node?.data?.config
    if (!cfg || typeof cfg !== "object") continue

    stripSecrets(cfg)
    normalizeProviders(cfg)
    normalizeStaticInput(node, cfg)
    normalizeUnsafeExampleText(node, cfg)

    if (platform === "macos") normalizeForMacos(node, cfg)
  }
  return def
}

function stripSecrets(obj) {
  for (const key of Object.keys(obj)) {
    if (key === "apiKey" || key === "geminiApiKey" || key.startsWith("__")) {
      obj[key] = ""
    }
  }
}

function normalizeProviders(cfg) {
  if (String(cfg.provider || "").toLowerCase() === "gemini") cfg.provider = "qwen"
  if (String(cfg.voice || "").toLowerCase().startsWith("gemini:")) cfg.voice = "vixtts"
}

function normalizeStaticInput(node, cfg) {
  if (node.type !== "input") return
  const contentType = String(cfg.contentType || "")
  if (!["image", "video", "file"].includes(contentType)) return

  cfg.source = "session"
  cfg.staticUrl = ""
  cfg.staticData = ""
  cfg.staticPath = ""
  cfg.staticBucket = ""
  cfg.staticSize = 0
  cfg.staticMime = ""
  cfg.staticName = ""
}

function normalizeUnsafeExampleText(node, cfg) {
  if (node.type === "create-image" && typeof cfg.prompt === "string") {
    cfg.prompt = "A clean cinematic product concept image, premium studio lighting, high detail, photorealistic"
  }
}

function normalizeForMacos(node, cfg) {
  if (node.type === "create-image") {
    cfg.provider = "qwen"
    cfg.outputCount = 1
    cfg.quality = cfg.quality || "standard"
  }
  if (node.type === "motion") {
    cfg.preset = cfg.preset || "5s-480p"
    cfg.quality = "480p"
    cfg.fps60 = false
  }
  if (node.type === "teaser" || node.type === "lookbook") {
    cfg.preset = cfg.preset || "5s-480p"
    cfg.targetDurationSec = Math.min(Number(cfg.targetDurationSec || 8), 8)
  }
}

function renderSql(items) {
  const values = items.map((w) => `  (
    ${sqlString(w.slug)},
    ${sqlString(w.name)},
    ${sqlString(w.description)},
    ${sqlJson(w.definition)},
    ${w.is_active ? "true" : "false"}
  )`).join(",\n")

  return `-- Seed public workflow templates exported from production.
-- Generated by scripts/export-workflow-seed.mjs.
-- Manual only: run scripts/apply-workflow-seed.mjs when you intentionally want to import/update templates.
-- Secrets and expiring VPS static asset URLs are stripped; input nodes use session uploads.

INSERT INTO users (email, full_name, role, is_active)
VALUES ('seed-workflows@local.dev', 'Seed Workflow Templates', 'staff', true)
ON CONFLICT (email) DO UPDATE SET
  full_name = EXCLUDED.full_name,
  is_active = true;

WITH seed_user AS (
  SELECT id FROM users WHERE email = 'seed-workflows@local.dev'
),
seed_workflows(slug, name, description, definition, is_active) AS (
  VALUES
${values}
)
INSERT INTO workflows (user_id, slug, name, description, definition, is_public, is_active)
SELECT seed_user.id, seed_workflows.slug, seed_workflows.name, seed_workflows.description,
       seed_workflows.definition, true, seed_workflows.is_active
FROM seed_user, seed_workflows
ON CONFLICT (user_id, slug) DO UPDATE SET
  name = EXCLUDED.name,
  description = EXCLUDED.description,
  definition = EXCLUDED.definition,
  is_public = true,
  is_active = EXCLUDED.is_active,
  updated_at = now();
`
}

function sqlString(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value))}::jsonb`
}
