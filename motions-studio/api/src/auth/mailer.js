// #region ALD 31/05/2026 - Gửi OTP qua SMTP (nodemailer) — port từ _shared/smtp.ts.
import nodemailer from "nodemailer"

const APP_NAME = process.env.APP_NAME || "Motions"

// ALD 01/06/2026 - Cờ IS_USED_GMAIL: bật → gửi qua Gmail (App Password), bỏ qua OTP_SMTP_*;
// tắt → SMTP thường (Mailgun/SendGrid/SES/nội bộ). Chấp nhận cả tên camelCase isUsedGmail.
const USE_GMAIL = /^(1|true|yes|on)$/i.test(String(process.env.IS_USED_GMAIL ?? process.env.isUsedGmail ?? ""))
// Gmail App Password thường được Google hiển thị dạng "abcd efgh ijkl mnop" → bỏ mọi space trước khi dùng.
const stripPass = (p) => String(p || "").replace(/\s+/g, "")

let _tx = null
function tx() {
  if (_tx) return _tx
  if (USE_GMAIL) {
    // nodemailer service:'gmail' tự set host smtp.gmail.com + port + TLS. PASS = App Password.
    const user = process.env.GMAIL_USER || process.env.OTP_SMTP_USER
    const pass = stripPass(process.env.GMAIL_APP_PASSWORD || process.env.OTP_SMTP_PASS)
    _tx = nodemailer.createTransport({ service: "gmail", auth: { user, pass } })
  } else {
    const port = Number(process.env.OTP_SMTP_PORT || 587)
    _tx = nodemailer.createTransport({
      host: process.env.OTP_SMTP_HOST,
      port,
      secure: port === 465, // 465 = TLS implicit; 587 = STARTTLS
      auth: { user: process.env.OTP_SMTP_USER, pass: stripPass(process.env.OTP_SMTP_PASS) },
    })
  }
  return _tx
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")

function html(otp) {
  const year = new Date().getFullYear()
  return `<!DOCTYPE html><html lang="vi"><body style="margin:0;padding:0;background:#f2f0e6;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <table width="100%" style="background:#f2f0e6;padding:40px 0;"><tr><td align="center">
  <table width="600" style="max-width:600px;width:100%;"><tr><td style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
  <table width="100%" style="background:linear-gradient(135deg,#2563eb,#1e3a8a);"><tr><td style="padding:28px 40px;">
  <span style="font-size:18px;font-weight:900;color:#fff;">${esc(APP_NAME)}</span>
  <div style="margin-top:4px;font-size:11px;color:rgba(255,255,255,.7);text-transform:uppercase;letter-spacing:.1em;">Mã xác thực OTP</div>
  </td></tr></table>
  <table width="100%"><tr><td style="padding:32px 40px;">
  <h2 style="font-size:24px;font-weight:900;color:#0f172a;margin:0 0 20px;text-transform:uppercase;">Mã xác thực</h2>
  <p style="font-size:14px;color:#64748b;margin:0 0 30px;line-height:1.6;">Nhập mã 6 chữ số sau để đăng nhập. Mã có hiệu lực 5 phút.</p>
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:16px;padding:30px;text-align:center;margin-bottom:30px;">
  <span style="font-family:Monaco,monospace;font-size:36px;font-weight:900;color:#2563eb;letter-spacing:12px;">${esc(otp)}</span></div>
  <p style="font-size:13px;color:#94a3b8;font-style:italic;margin:0;">Nếu bạn không yêu cầu mã này, hãy bỏ qua email.</p>
  </td></tr></table>
  <table width="100%" style="border-top:1px solid #e5e7eb;"><tr><td style="padding:16px 40px;text-align:center;color:#94a3b8;font-size:11px;">© ${year} ${esc(APP_NAME)}</td></tr></table>
  </td></tr></table></td></tr></table></body></html>`
}

export async function sendOtpEmail({ to, otp }) {
  // Gmail chặn From lạ → khi dùng Gmail ép From = GMAIL_USER.
  const from = USE_GMAIL
    ? (process.env.GMAIL_USER || process.env.OTP_SMTP_USER || "")
    : (process.env.OTP_SMTP_FROM || process.env.OTP_SMTP_USER || "")
  const fromName = process.env.OTP_SMTP_FROM_NAME || APP_NAME
  await tx().sendMail({
    from: `"${fromName}" <${from}>`,
    to,
    subject: `[${APP_NAME}] Mã xác thực: ${otp}`,
    html: html(otp),
  })
}
// #endregion
