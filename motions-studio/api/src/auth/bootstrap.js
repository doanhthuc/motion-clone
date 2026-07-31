// #region ALD 01/06/2026 - Bootstrap super-admin từ env (SUPER_ADMIN).
// Vì sao bắt buộc: login là OTP passwordless và user PHẢI tồn tại sẵn (auth.js từ chối email lạ
// với "Email chưa được đăng ký"). Một deploy mới có bảng users RỖNG → không ai đăng nhập được
// → không tạo nổi admin đầu tiên (route /admin/users đòi session admin). Đây là vòng lặp chết.
// Hàm này chạy mỗi lần API khởi động: đọc SUPER_ADMIN (1 hoặc nhiều email, ngăn cách bằng dấu
// phẩy) rồi upsert thành role='admin', is_active=true. Idempotent — restart luôn khôi phục quyền
// admin cho email này (cố ý: "ghim" tài khoản gốc, nên KHÔNG hạ quyền nó qua UI được).
import { query } from "../db.js"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export async function bootstrapSuperAdmin() {
  // Hỗ trợ cả tên gõ nhầm cũ (SUPPER_ADMIN) để .env lỡ copy vẫn chạy.
  const raw = process.env.SUPER_ADMIN || process.env.SUPPER_ADMIN || ""
  const emails = raw.split(",").map((e) => e.trim().toLowerCase()).filter(Boolean)

  if (emails.length === 0) {
    console.warn(
      "[bootstrap] ⚠ SUPER_ADMIN chưa đặt → chưa có admin gốc. Login OTP đòi user tồn tại sẵn, " +
      "nên hãy đặt SUPER_ADMIN=<email> trong .env rồi khởi động lại, nếu không sẽ KHÔNG ai đăng nhập/quản trị được.",
    )
    return
  }

  for (const email of emails) {
    if (!EMAIL_RE.test(email)) {
      console.warn(`[bootstrap] ⚠ Bỏ qua SUPER_ADMIN không hợp lệ: "${email}"`)
      continue
    }
    try {
      // full_name chỉ set khi tạo mới; nếu user đã có thì giữ tên thật, chỉ nâng quyền + mở khoá.
      await query(
        `INSERT INTO users (email, role, is_active, full_name)
         VALUES ($1, 'admin', true, 'Super Admin')
         ON CONFLICT (email) DO UPDATE SET role = 'admin', is_active = true`,
        [email],
      )
      console.log(`[bootstrap] ✓ Super-admin sẵn sàng: ${email} (role=admin, active)`)
    } catch (e) {
      console.error(`[bootstrap] ✗ Không upsert được admin ${email}:`, e?.message || e)
    }
  }
}
// #endregion
