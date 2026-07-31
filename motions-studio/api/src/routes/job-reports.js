// #region ALD 31/05/2026 - job-reports (thay /functions/v1/job-reports).
// motions KHÔNG có analyze/OCR jobs → reports rỗng. Trả shape hợp lệ để ReportsManager không lỗi.
//   GET /job-reports?... → { items:[], total:0 } · GET /job-reports/stats → {…0}
//   GET /job-reports/:id → { item:null } · DELETE /job-reports/:id → { success:true }
import { Router } from "express"
import { sessionAuth } from "../auth/session.js"

const router = Router()
router.use("/job-reports", sessionAuth)
router.get("/job-reports/stats", (_req, res) => res.json({ total: 0, byStatus: {}, byType: {} }))
router.get("/job-reports", (_req, res) => res.json({ items: [], total: 0, page: 1, limit: 20 }))
router.get("/job-reports/:id", (_req, res) => res.status(404).json({ item: null, error: "Không có report" }))
router.delete("/job-reports/:id", (_req, res) => res.json({ success: true }))

export default router
// #endregion
