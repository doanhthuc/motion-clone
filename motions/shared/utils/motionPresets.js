// #region ALD 31/05/2026 - Preset motion dùng chung FE (dropdown) + server proxy (đổi preset → params).
// motion-backend worker đọc params cụ thể (width/height/frames/steps/lora_relight…) chứ KHÔNG hiểu
// "preset", nên proxy phải expand preset → params trước khi POST /jobs.
// ALD 11/07/2026 - CHỐT: end user CHỈ chọn SỐ GIÂY. fps + số frame + tỉ lệ khung LUÔN theo DRIVER 1:1
// (worker probe fps driver, cap 30; trần frame theo RAM box, vượt → tự hạ fps — logic ở linux.py `^drv-(\d+)s$`).
// KHÔNG còn cho end user chọn mức fps (16/30) → hết "tay ảo" do decimation 30→16fps + hết OOM do 30fps·601f.
// Vẫn render 540p (baseline nhẹ, không OOM); tỉ lệ khung do fitDriver worker chỉnh theo driver thật. CHẤT LƯỢNG
// cao (1080p/2K/4K) do node "Enhance/Upscale" RIÊNG xử lý SAU: Wan render nhẹ → xả GPU/RAM → upscale dồn tài nguyên.
export const MOTION_PRESETS = [
  // driverNative: presetToParams KHÔNG gửi frames/render_fps — worker tự probe & tính. resolution 544x960 = baseline
  // 540p (fitDriver worker nắn theo tỉ lệ driver thật). ETA/độ nặng phụ thuộc fps thật của driver nên ghi "theo driver".
  { id: 'drv-5s',  label: '5s',  resolution: '544x960', steps: 4, driverNative: true, eta: 'theo fps driver', fps: 0, note: 'Test nhanh · fps & khung theo driver' },
  { id: 'drv-10s', label: '10s', resolution: '544x960', steps: 4, driverNative: true, eta: 'theo fps driver', fps: 0, note: 'fps & khung 1:1 theo driver' },
  { id: 'drv-15s', label: '15s', resolution: '544x960', steps: 4, driverNative: true, eta: 'theo fps driver', fps: 0, note: 'Mặc định · fps & khung 1:1 theo driver' },
  { id: 'drv-20s', label: '20s', resolution: '544x960', steps: 4, driverNative: true, eta: 'theo fps driver', fps: 0, note: 'Dài — worker có thể tự hạ fps theo RAM box' },
  // ALD 10/08/2026 - MỞ LẠI drv-30s và BỎ minRamGb:128. Gate theo RAM chỉ là proxy cho "601 frame nặng
  // quá"; từ khi trần frame đọc đúng trần cgroup (worker_runtime/box_ram.py) thì chính trần đó đã chặn
  // trực tiếp — vượt ngân sách là worker hạ fps chứ KHÔNG cắt thời lượng, nên preset dài không còn tự nó
  // gây OOM. Giữ gate cũ chỉ khiến preset bị ẩn vĩnh viễn (pod báo 56GB sau khi sửa, không bao giờ ≥128).
  // 30s = "lấy trọn clip": user chỉ upload ảnh + video rồi Chạy, không phải đoán số giây.
  { id: 'drv-30s', label: '30s', resolution: '544x960', steps: 4, driverNative: true, eta: 'theo fps driver', fps: 0, note: 'Lấy trọn clip (tối đa 30s) · clip dài thì tự hạ fps cho vừa RAM box' },
]

// preset id → params cụ thể cho worker (width/height/frames/steps + lora_relight nếu preset HQ).
// ALD 11/07/2026 - preset id lạ / legacy đã gỡ khỏi UI (vd "15s-720p", "10s-30fps") → fallback 'drv-15s'
// (driver-native 15s). Node cũ đã lưu preset fixed vẫn chạy qua bảng MOTION_PRESETS ở worker (linux.py) để
// back-compat; ở FE chỉ cần trả params an toàn (worker tự probe khi thấy id 'drv-*').
export function presetToParams(presetId) {
  const p = MOTION_PRESETS.find((x) => x.id === presetId) || MOTION_PRESETS.find((x) => x.id === 'drv-15s') || MOTION_PRESETS[0]
  const [w, h] = String(p.resolution || '544x960').split('x').map((n) => parseInt(n, 10) || 0)
  // ALD 09/07/2026 - driverNative (drv-Ns): KHÔNG gửi frames/render_fps — worker probe fps driver rồi tự tính.
  if (p.driverNative) return { width: w || 544, height: h || 960, steps: p.steps || 4 }
  const out = { width: w || 544, height: h || 960, frames: p.frames, steps: p.steps }
  if (p.lora_relight != null) out.lora_relight = p.lora_relight
  if (p.renderFps) out.render_fps = p.renderFps // ALD 02/07/2026 - preset 30fps native: proxy expand phải giữ render_fps
  return out
}
// #endregion
