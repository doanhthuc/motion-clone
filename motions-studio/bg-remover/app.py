#!/usr/bin/env python3
# ALD 11/06/2026 - bg-remover: microservice xóa nền (rembg) cho motion-backend.
#   POST /remove?model=human|object&crop=0|1   body = bytes ảnh   → trả JPEG (nền xám 208,208,208)
#     model=human  (u2net_human_seg) → tách NGƯỜI: dùng cho ảnh model mẫu (/model-refs upload)
#     model=object (u2net)           → tách VẬT THỂ nổi: dùng cho ẢNH SẢN PHẨM tryon (đồ trên ma-nơ-canh/flat-lay)
#     crop=1 → cắt sát bounding-box foreground (+lề) → món đồ/người LẤP ĐẦY khung (fix tryon "sản phẩm quá nhỏ")
#   GET /health → {"status":"ok"}
# Model bake sẵn trong image (xem Dockerfile). NẠP LƯỜI + TỰ NHẢ khi rảnh (BG_IDLE_UNLOAD_SEC) → idle ~vài trăm MB
# thay vì ~1GB thường trú (box motion-backend RAM sát ngưỡng, ComfyUI offload model lên RAM khi render Wan). HTTP
# stdlib, đa luồng, chỉ chạy nội bộ.
import io, os, json, time, threading, gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from rembg import remove, new_session
from PIL import Image

BG = (208, 208, 208)
PORT = int(os.environ.get("PORT", "8000"))
CROP_MARGIN = float(os.environ.get("BG_CROP_MARGIN", "0.06"))          # lề quanh bbox = 6% cạnh
IDLE_UNLOAD_SEC = int(os.environ.get("BG_IDLE_UNLOAD_SEC", "600"))     # >0: nhả model không dùng sau N giây (0=giữ)
_MODEL_NAMES = {"human": "u2net_human_seg", "object": "u2net"}
_sessions, _last_used, _lock = {}, {}, threading.Lock()

def _get_session(model):
    name = _MODEL_NAMES.get(model, _MODEL_NAMES["human"])
    with _lock:
        if name not in _sessions:
            _sessions[name] = new_session(name)
        _last_used[name] = time.time()
        return _sessions[name]

def _idle_reaper():
    """Nhả model không dùng quá IDLE_UNLOAD_SEC → trả RAM về cho box (ComfyUI/Wan rất cần RAM)."""
    if IDLE_UNLOAD_SEC <= 0:
        return
    while True:
        time.sleep(60)
        now = time.time()
        with _lock:
            for name in [n for n in _sessions if now - _last_used.get(n, 0) > IDLE_UNLOAD_SEC]:
                _sessions.pop(name, None); _last_used.pop(name, None)
        gc.collect()

def process(raw, model="human", crop=False):
    sess = _get_session(model)
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    cut = remove(im, session=sess)                       # RGBA, nền trong suốt
    if crop:
        bbox = cut.split()[-1].getbbox()                 # bbox theo alpha (vùng foreground)
        if bbox:
            x0, y0, x1, y1 = bbox
            mx = int((x1 - x0) * CROP_MARGIN); my = int((y1 - y0) * CROP_MARGIN)
            x0 = max(0, x0 - mx); y0 = max(0, y0 - my)
            x1 = min(cut.width, x1 + mx); y1 = min(cut.height, y1 + my)
            area = (x1 - x0) * (y1 - y0)
            full = cut.width * cut.height
            # Chỉ crop khi bbox hợp lý (2%–97% diện tích) → tránh crop hỏng khi segmentation toàn-khung/quá-nhỏ.
            if 0.02 * full <= area <= 0.97 * full:
                cut = cut.crop((x0, y0, x1, y1))
    bg = Image.new("RGBA", cut.size, BG + (255,))
    comp = Image.alpha_composite(bg, cut).convert("RGB")
    out = io.BytesIO(); comp.save(out, "JPEG", quality=92)
    return out.getvalue(), comp.width, comp.height

class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._json(200, {"status": "ok", "loaded": list(_sessions)})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/remove":
            return self._json(404, {"error": "not found"})
        try:
            q = parse_qs(u.query)
            model = (q.get("model", ["human"])[0]).lower()
            crop = (q.get("crop", ["0"])[0]).lower() in ("1", "true", "yes")
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return self._json(400, {"error": "empty body"})
            data, w, h = process(raw, model=model, crop=crop)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("X-Image-Width", str(w)); self.send_header("X-Image-Height", str(h))
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, *a): pass   # tắt access log ồn ào

if __name__ == "__main__":
    threading.Thread(target=_idle_reaper, daemon=True).start()
    print(f"[bg-remover] listening :{PORT} (lazy-load, idle_unload={IDLE_UNLOAD_SEC}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
