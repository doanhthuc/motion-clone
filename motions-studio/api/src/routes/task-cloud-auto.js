// #region ALD 16/07/2026 - Điều khiển Auto worker phía server.
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import {
  clearTaskCloudConnection,
  saveTaskCloudConnection,
  taskCloudConnectionStatus,
} from "../task-cloud/connection.js"

const router = Router()
router.use("/task-cloud-auto", sessionAuth, requireAdmin)

function item(row, connection) {
  const heartbeat = row?.heartbeat_at ? new Date(row.heartbeat_at) : null
  const online = Boolean(heartbeat && Date.now() - heartbeat.getTime() < 45_000)
  return {
    configured: Boolean(connection?.configured),
    connection: connection || { configured: false, url: "", workerId: "" },
    online,
    enabled: Boolean(row?.enabled),
    state: row?.state || "off",
    activeTaskId: row?.active_task_id || "",
    activeTaskCode: row?.active_task_code || "",
    lastError: row?.last_error || "",
    heartbeatAt: row?.heartbeat_at || null,
    updatedAt: row?.updated_at || null,
  }
}

async function current() {
  const { rows } = await query("SELECT * FROM task_cloud_auto_settings WHERE singleton_id=true")
  return rows[0]
}

router.get("/task-cloud-auto/status", async (_req, res) => {
  try {
    res.json({ item: item(await current(), await taskCloudConnectionStatus()) })
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) })
  }
})

router.patch("/task-cloud-auto/status", async (req, res) => {
  try {
    const enabled = req.body?.enabled === true
    const connection = await taskCloudConnectionStatus()
    if (enabled && !connection.configured) {
      return res.status(503).json({ error: "Auto VPS chưa được cấu hình kết nối service-to-service với Task Cloud." })
    }
    const before = await current()
    const state = enabled
      ? (before?.active_task_id ? before.state : "waiting")
      : (before?.active_task_id ? before.state : "off")
    const { rows } = await query(
      `UPDATE task_cloud_auto_settings
       SET enabled=$1, state=$2, updated_by=$3, updated_at=now(),
           last_error=CASE WHEN $1 THEN NULL ELSE last_error END
       WHERE singleton_id=true RETURNING *`,
      [enabled, state, req.session.userId],
    )
    res.json({ item: item(rows[0], connection) })
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) })
  }
})

router.get("/task-cloud-auto/connection", async (_req, res) => {
  try {
    res.set("Cache-Control", "private, no-store, max-age=0")
    res.json({ item: await taskCloudConnectionStatus() })
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) })
  }
})

router.put("/task-cloud-auto/connection", async (req, res) => {
  try {
    const url = req.body?.url || req.body?.taskCloudUrl
    const token = req.body?.token || req.body?.apiKey
    const saved = await saveTaskCloudConnection({ url, token, userId: req.session.userId })
    res.set("Cache-Control", "private, no-store, max-age=0")
    res.json({ item: saved })
  } catch (error) {
    res.status(400).json({ error: String(error?.message || error) })
  }
})

router.delete("/task-cloud-auto/connection", async (req, res) => {
  try {
    res.json({ item: await clearTaskCloudConnection(req.session.userId) })
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) })
  }
})

export default router
// #endregion
