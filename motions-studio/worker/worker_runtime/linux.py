#!/usr/bin/env python3
"""Motion Backend Worker — poll API → chạy ComfyUI → upload kết quả. Stateless, không dính Supabase.

Vòng lặp:
  POST {API_URL}/worker/claim  → nhận 1 job queued (hoặc 204 nếu trống)
  motion: tải ref+motion (GET /files/<key>) → upload sang ComfyUI input → build Wan workflow →
          submit /prompt → poll /history → fetch /view output.mp4 → POST /jobs/:id/output
  PATCH /jobs/:id liên tục để báo progress.

ComfyUI ở ngoài (COMFY_URL) — model files do container comfyui tự tải (entrypoint).
"""
import os, sys, time, json, tempfile, traceback, subprocess, shutil, base64, threading, random, re, math
from datetime import datetime, timezone
# ALD 22/06/2026 - HF_HOME → cache GHI ĐƯỢC. faster_whisper (subtitle ASR) + mọi tải HF mặc định ghi vào
# ~/.cache/huggingface/hub (root-owned → "[Errno 13] Permission denied"). Worker thiếu HF_HOME nên dính lỗi.
# Trỏ sang ~/.cache/hf (ubuntu-owned, ghi được; dir đã có sẵn). ĐẶT TRƯỚC mọi import HF (faster_whisper/
# huggingface_hub đều lazy-import phía dưới nên đặt ở đây là kịp). Fix 18/06 chỉ tạo dir trên box, KHÔNG vào code → tái phát.
os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/hf"))
try:
    os.makedirs(os.environ["HF_HOME"], exist_ok=True)
except Exception:
    pass
from concurrent.futures import ThreadPoolExecutor
import requests
from worker_runtime.runner import run_worker_loop
# ALD 10/08/2026 - RAM box đọc từ trần cgroup, KHÔNG từ /proc/meminfo của host (RunPod cho container
# thấy RAM host). Dùng cho trần frame preset drv-Ns bên dưới. Xem worker_runtime/box_ram.py.
from worker_runtime.box_ram import box_ram_gb, host_ram_bytes
# ALD 05/06/2026 - websocket-client để nghe progress sampling realtime từ ComfyUI (/ws). Guarded:
# nếu image chưa cài lib (chưa rebuild) → _ws=None → comfy_poll tự fallback về poll /history như cũ.
try:
    import websocket as _ws   # websocket-client
except Exception:
    _ws = None

API_URL   = os.environ.get("API_URL", "http://api:8080").rstrip("/")
COMFY_URL = os.environ.get("COMFY_URL", "http://comfyui:8188").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")
WORKER_ID = os.environ.get("WORKER_ID", "worker-1")
POLL = int(os.environ.get("POLL_INTERVAL_SEC", "3"))
# ALD 05/06/2026 - client_id cố định/process để gắn ws ComfyUI.
# ALD 07/06/2026 - "TREO" tính theo KHÔNG-TIẾN-TRIỂN, KHÔNG theo tổng thời gian: job đang chạy (ws còn nhả
# tín hiệu / tiến độ còn tăng) thì KHÔNG bao giờ bị hủy dù vượt 30' (job dài: BDS nhiều stage @720p, ESRGAN
# ảnh to). Chỉ khi IM/không tiến quá COMFY_HANG_SEC (mặc định 30') mới coi là treo (OOM/GPU kẹt) → fail.
# COMFY_MAX_SEC = trần tuyệt đối chống loop vô hạn. COMFY_STALL_SEC giữ lại cho tương thích (không còn dùng
# làm ngưỡng chính — trước đây nó + deadline tổng chém nhầm job vẫn đang render ở ~85%).
COMFY_CLIENT_ID = f"motion-worker-{os.getpid()}"
COMFY_STALL_SEC = float(os.environ.get("COMFY_STALL_SEC", "420"))
COMFY_HANG_SEC  = float(os.environ.get("COMFY_HANG_SEC", "1800"))   # KHÔNG tiến (ws im + frac đứng) ngần này giây = treo
COMFY_MAX_SEC   = float(os.environ.get("COMFY_MAX_SEC", "7200"))    # trần tuyệt đối (chống chạy vô hạn nếu mọi tín hiệu kẹt)
# ALD 11/06/2026 - ComfyUI CHẾT/treo/restart (unreachable hoặc mất prompt) quá ngần này giây → FAIL job NGAY (error),
# không treo tới COMFY_HANG_SEC=30'. Khác "render chậm còn sống" (history/queue còn prompt → reset liên tục).
COMFY_DOWN_SEC  = float(os.environ.get("COMFY_DOWN_SEC", "120"))
# ALD 20/06/2026 - (1) COMFY_READY_WAIT_SEC: khi upload/submit gặp ComfyUI đang xuống (restart/OOM → Connection
# refused), chờ nó dậy lại tối đa ngần này giây rồi thử LẠI thay vì fail job ngay (lỗi node "Motion Transfer"
# bắn nguội). GIỮ — chạy tốt.
# (2) COMFY_RECYCLE_GB: job xong, ComfyUI idle mà RSS ≥ mức này → recycle trả RAM về OS (leak ~50GB). 0 = tắt.
#   ⚠ LỊCH SỬ: 20/06 bật thử bằng `pm2 restart comfyui` → GÂY OUTAGE: SIGINT khiến torch teardown abort
#   ("Fatal Python error: Aborted") → PM2 kẹt "waiting restart" ~1h45'. ĐÃ SỬA: comfy_recycle giờ dùng
#   SIGKILL (pkill -9) → PM2 autorestart dựng fresh ~14s, KHÔNG qua shutdown graceful nên KHÔNG abort (đã test
#   thật: 41GB→1.2GB sạch). An toàn → bật lại default 22.
COMFY_READY_WAIT_SEC     = float(os.environ.get("COMFY_READY_WAIT_SEC", "120"))
COMFY_RECYCLE_GB         = float(os.environ.get("COMFY_RECYCLE_GB", "22"))
COMFY_RECYCLE_COOLDOWN_SEC = float(os.environ.get("COMFY_RECYCLE_COOLDOWN_SEC", "90"))
# ALD 30/06/2026 - MẶC ĐỊNH bao trùm MỌI node type có handler (trước đây chỉ "motion" → mọi node khác queue mãi nếu
# box chạy 1 `python worker.py` không set env). Sub-worker vẫn override qua env JOB_TYPES khi cần sharding.
_DEFAULT_JOB_TYPES = ("motion,tryon,create-image,edit-image,product-overlay,talk,face-motion,story-film,"
                      "wan-i2v,voiceover,subtitle,concat,enhance,teaser,video,bds,teen-flycam,trend-tiktok,reveal")
JOB_TYPES = [t.strip() for t in os.environ.get("JOB_TYPES", _DEFAULT_JOB_TYPES).split(",") if t.strip()]
# ALD 13/06/2026 - MẶC ĐỊNH 1 job/lúc (1 running + phần còn lại Ở QUEUE), theo yêu cầu user: tránh 2 job
# create-image chạy song song trên cùng GPU (render lâu + ảnh chập chờn). Muốn chạy song song lại thì set
# env WORKER_CONCURRENCY=2 (GPU vẫn an toàn vì ComfyUI tự xếp hàng nội bộ; song song chỉ lợi I/O). Guard RAM vẫn chặn.
WORKER_CONCURRENCY = max(1, int(os.environ.get("WORKER_CONCURRENCY", "1")))
# ALD 01/06/2026 - Vision auto-detect loại đồ cho Try-on (khi user để 'auto'). TRỐNG = tắt (fallback 'upper').
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "").rstrip("/")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen2.5vl:7b")
# ALD 12/06/2026 - dịch prompt VN→EN cho Qwen-Image. URL riêng (worker dùng host-network nên 127.0.0.1 reach
# Ollama local trên VPS) mặc định bật; model nhỏ instruct dịch nhanh. Tắt: CREATE_IMAGE_TRANSLATE=0.
TRANSLATE_URL   = (os.environ.get("CREATE_IMAGE_TRANSLATE_URL") or OLLAMA_URL or "http://127.0.0.1:11434").rstrip("/")
TRANSLATE_MODEL = os.environ.get("CREATE_IMAGE_TRANSLATE_MODEL", "qwen2.5:7b-instruct")
TRANSLATE_ON    = os.environ.get("CREATE_IMAGE_TRANSLATE", "1").strip().lower() not in ("0", "false", "no", "off", "")
# ALD 13/06/2026 - create-image: bối cảnh MẶC ĐỊNH (fallback) khi user KHÔNG nhập prompt → ảnh có nền/cảnh
# tử tế thay vì để model tự bịa lung tung. Override qua env CREATE_IMAGE_FALLBACK_BG.
CREATE_IMAGE_FALLBACK_BG = os.environ.get("CREATE_IMAGE_FALLBACK_BG",
    "in a clean professional photo studio with a soft light-gray seamless backdrop, even diffused softbox lighting, "
    "shallow depth of field, full-body fashion catalog photograph")

def _translate_prompt_en(text, job_id=None):
    """Dịch prompt người dùng (thường tiếng Việt) sang câu mô tả ảnh tiếng Anh cho Qwen-Image. Chỉ dịch khi
    phát hiện ký tự tiếng Việt. GIỮ NGUYÊN mọi chi tiết (bối cảnh/trang phục/tư thế/phụ kiện), KHÔNG thêm thắt.
    Fail-safe tuyệt đối: lỗi/tắt/không có dấu tiếng Việt → trả text gốc (không bao giờ raise)."""
    t = (text or "").strip()
    if not TRANSLATE_ON or not t:
        return text
    # Không có dấu tiếng Việt + không có chữ thường có dấu → coi như đã tiếng Anh, bỏ qua cho nhanh.
    if not any(c in t.lower() for c in "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"):
        return text
    try:
        sys_msg = ("You are a translator for an image-generation prompt. Translate the user's text into ONE "
                   "natural English image prompt. Preserve EVERY detail exactly — scene/location, lighting, "
                   "outfit, pose, camera angle, accessories, mood — and do NOT add, remove or invent anything. "
                   "Output ONLY the English prompt, no quotes, no explanation.")
        r = requests.post(f"{TRANSLATE_URL}/api/chat", timeout=60, json={
            "model": TRANSLATE_MODEL, "stream": False,
            "keep_alive": 0,
            "options": {"temperature": 0.1},
            "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": t}],
        })
        r.raise_for_status()
        out = (r.json().get("message", {}).get("content") or "").strip().strip('"').strip()
        return out or text
    except Exception as e:
        if job_id:
            api_log(job_id, f"Dịch prompt lỗi (giữ nguyên tiếng Việt): {e}", "warn")
        return text

# #region ALD 04/06/2026 - Guard RAM. Máy bridgellm-02 (62GB) gánh NHIỀU stack + ComfyUI/Ollama native.
# Trước khi nhận job mới, nếu RAM trống quá thấp HOẶC swap đang đầy → HOÃN claim (không ôm thêm job nặng)
# để tránh tràn swap → thrashing → sập cả máy (sự cố 04/06). Ngưỡng chỉnh qua env.
MIN_AVAIL_GB = float(os.environ.get("WORKER_MIN_AVAIL_GB", "12"))   # RAM trống tối thiểu (GB) mới dám nhận job
MAX_SWAP_PCT = float(os.environ.get("WORKER_MAX_SWAP_PCT", "70"))   # swap dùng quá % này → (kèm điều kiện RAM) hoãn
# ALD 05/06/2026 - swap cao CHỈ là vấn đề khi RAM cũng tụt. Swap đầy + RAM còn dư nhiều = pages CŨ/nguội,
# KHÔNG phải thrashing → đừng chặn nhầm (trước đây swap 75% tồn dư khiến worker từ chối job dù RAM trống 40GB).
SWAP_RAM_FLOOR_GB = float(os.environ.get("WORKER_SWAP_RAM_FLOOR_GB", "24"))  # swap cao + RAM < mức này mới hoãn
# #endregion

WORKER_HEADERS = {"X-Worker-Token": WORKER_TOKEN}

def log(*a): print("[worker]", *a, flush=True)

# ALD 04/06/2026 - Đọc áp lực RAM/swap từ /proc/meminfo (trong container vẫn thấy RAM HOST). Không cần psutil.
def _mem_status():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])   # kB
        avail_gb = info.get("MemAvailable", 0) / 1024 / 1024
        sw_tot, sw_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
        swap_pct = (100.0 * (sw_tot - sw_free) / sw_tot) if sw_tot else 0.0
        return avail_gb, swap_pct
    except Exception:
        return 999.0, 0.0   # đọc lỗi → KHÔNG chặn (fail-open, không làm worker đứng vì lỗi đọc)

# ───────────────────────── API client ─────────────────────────
def api_claim(active_ids=None):
    # ALD 12/06/2026 - gửi kèm active_job_ids (job đang chạy song song) để API chỉ reclaim job mồ côi
    # NGOÀI danh sách này — không fail oan khi WORKER_CONCURRENCY > 1.
    r = requests.post(f"{API_URL}/worker/claim", headers=WORKER_HEADERS,
                      json={"types": JOB_TYPES, "worker_id": WORKER_ID,
                            "active_job_ids": list(active_ids or [])}, timeout=15)
    if r.status_code == 204: return None
    r.raise_for_status()
    return r.json()

def api_patch(job_id, **fields):
    try: requests.patch(f"{API_URL}/jobs/{job_id}", headers=WORKER_HEADERS, json=fields, timeout=15)
    except Exception as e: log("patch fail:", e)

def api_log(job_id, message, level="info", progress=None):
    try: requests.post(f"{API_URL}/jobs/{job_id}/log", headers=WORKER_HEADERS,
                       json={"message": message, "level": level, "progress": progress}, timeout=10)
    except Exception as e: log("log fail:", e)

def api_progress(job_id, p, step=""):
    api_patch(job_id, progress=p, current_step=step)
    if step:
        log(f"  {job_id[:8]} {int(p*100)}% {step}")
        api_log(job_id, step, "info", p)   # mỗi bước → 1 dòng log (FE poll /logs)

def api_heartbeat(active_job_id=None):
    gpu_name, vram_mb = comfy_gpu_info()
    try: requests.post(f"{API_URL}/worker/heartbeat", headers=WORKER_HEADERS,
                       json={"worker_id": WORKER_ID, "active_job_id": active_job_id, "mode": "motion",
                             "gpu_name": gpu_name, "gpu_vram_total_mb": vram_mb}, timeout=10)
    except Exception as e: log("heartbeat fail:", e)

# ALD 05/06/2026 - Honor CANCEL giữa lúc poll ComfyUI: hỏi API job có bị hủy chưa (FE run-cancel cascade
# xuống jobs, hoặc DELETE /jobs/:id) → nếu có thì /interrupt ComfyUI ngay để nhả GPU, không chờ hết job.
def api_job_cancelled(job_id):
    if not job_id: return False
    try:
        r = requests.get(f"{API_URL}/jobs/{job_id}/cancelled", headers=WORKER_HEADERS, timeout=8)
        if r.status_code == 200: return bool(r.json().get("cancelled"))
    except Exception: pass
    return False
def comfy_interrupt(free=True):
    # Ngắt prompt đang chạy + (free=True) UNLOAD model khỏi VRAM. ALD 14/06/2026 - chỉ /interrupt KHÔNG nhả VRAM
    # (ComfyUI giữ model đã nạp) → cancel job vẫn kẹt VRAM. Thêm /free {unload_models,free_memory} để giải phóng.
    try: requests.post(f"{COMFY_URL}/interrupt", timeout=8)
    except Exception as e: log("comfy interrupt fail:", e)
    if free:
        try: requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=8)
        except Exception as e: log("comfy free (VRAM) fail:", e)

def _vram_status():
    """VRAM (used_gb, total_gb) LIVE từ ComfyUI /system_stats — (None, None) nếu không đọc được."""
    try:
        d = requests.get(f"{COMFY_URL}/system_stats", timeout=5).json()
        dev = (d.get("devices") or [{}])[0]
        tot = dev.get("vram_total"); free = dev.get("vram_free")
        if tot:
            used_gb = (tot - free) / 1073741824 if free is not None else None
            return used_gb, tot / 1073741824
    except Exception:
        pass
    return None, None

# ALD 15/06/2026 - Xả sạch VRAM trước job ComfyUI NẶNG (Wan/Motion) + kiểm đủ chưa. wf chạy ở worker host-net.
_MARKER_URL = os.environ.get("MARKER_URL", "http://127.0.0.1:8001").rstrip("/")
MOTION_MIN_VRAM_GB = float(os.environ.get("MOTION_MIN_VRAM_GB", "18") or "18")
# #region ALD 02/07/2026 - GIẢM RAM/thời-gian load mỗi job Wan: WanVideoTextEncodeCached cache embeds RA ĐĨA.
# Prompt các job Wan (motion/talk/bds/i2v) gần như TĨNH, nhưng ComfyUI bị recycle (SIGKILL, pre-Wan RSS≥22GB)
# trước hầu hết job → cache RAM chết theo → umt5-xxl bf16 (~11GB) phải load + encode LẠI mỗi job.
# use_disk_cache=True: cache sống qua restart → cache-hit BỎ QUA hẳn bước load T5 (đỡ ~11GB churn RAM/VRAM +
# job khởi động nhanh hơn). Prompt mới chỉ tốn thêm entry vài MB trên đĩa. Tắt khẩn cấp: MOTION_T5_DISK_CACHE=0.
MOTION_T5_DISK_CACHE = str(os.environ.get("MOTION_T5_DISK_CACHE", "1")).strip().lower() not in ("0", "false", "no", "off", "")
# ALD 13/07/2026 - Một nguồn attention chung cho TOÀN BỘ Wan worker. Trước đây chỉ motion-transfer đọc
# MOTION_ATTENTION, còn I2V/T2V/Talk/BDS hardcode sdpa nên RTX 5090 có SageAttention mà phần lớn pipeline không dùng.
WAN_ATTENTION = str(os.environ.get("MOTION_ATTENTION", "sdpa") or "sdpa").strip()
def _wan_attention(params=None):
    return str((params or {}).get("attention_mode") or WAN_ATTENTION).strip()
# #endregion
def _free_all_vram(job_id):
    """Giải phóng VRAM: ComfyUI unload models (job trước) + Ollama unload + Chandra stop/hold. KHÔNG /interrupt
    (tránh chém prompt đang chạy của job khác); /free chỉ nhả model RỖI."""
    try:
        requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=8)
    except Exception as e:
        api_log(job_id, f"ComfyUI free bỏ qua: {e}", "warn")
    if OLLAMA_URL:
        try:
            ps = (requests.get(f"{OLLAMA_URL}/api/ps", timeout=5).json() or {}).get("models", [])
            for mdl in ps:
                try: requests.post(f"{OLLAMA_URL}/api/generate", json={"model": mdl.get("name"), "keep_alive": 0, "prompt": ""}, timeout=15)
                except Exception: pass
            if ps: api_log(job_id, f"xả VRAM: unload {len(ps)} model Ollama", "info")
        except Exception as e:
            api_log(job_id, f"Ollama unload bỏ qua: {e}", "warn")
    try: requests.post(f"{_MARKER_URL}/chandra/stop", timeout=10)
    except Exception: pass
    try: requests.get(f"{_MARKER_URL}/gpu/hold", timeout=5)   # giữ Chandra KHÔNG tự start khi ta đang dùng GPU
    except Exception: pass

def _ensure_vram_for_motion(job_id, need_gb=None):
    """Xả sạch VRAM rồi KIỂM: thiếu → raise (STOP job ngay, tránh OOM giữa chừng)."""
    need = need_gb if need_gb is not None else MOTION_MIN_VRAM_GB
    api_progress(job_id, 0.02, "xả VRAM chuẩn bị Motion")
    _free_all_vram(job_id)
    time.sleep(2.5)   # chờ driver nhả VRAM
    used, tot = _vram_status()
    if tot is None:
        api_log(job_id, "Không đọc được VRAM (ComfyUI) — bỏ qua kiểm tra, vẫn chạy", "warn")
        return
    free = tot - (used or 0)
    api_log(job_id, f"VRAM sau khi xả: free {free:.1f}/{tot:.1f} GB (cần ≥{need:.0f} GB)", "info")
    if free < need:
        raise RuntimeError(f"Không đủ VRAM cho Motion: còn {free:.1f}GB < cần {need:.0f}GB sau khi đã xả. "
                           f"Service khác đang giữ VRAM → DỪNG workflow. Thử lại sau ít phút hoặc giảm preset.")

_GPU_CACHE = {"name": None, "vram_mb": None, "ts": 0}
def comfy_gpu_info():
    """Đọc GPU name + VRAM từ ComfyUI /system_stats (cache 60s)."""
    if time.time() - _GPU_CACHE["ts"] < 60:
        return _GPU_CACHE["name"], _GPU_CACHE["vram_mb"]
    try:
        d = requests.get(f"{COMFY_URL}/system_stats", timeout=5).json()
        dev = (d.get("devices") or [{}])[0]
        _GPU_CACHE["name"] = dev.get("name")
        vt = dev.get("vram_total")
        _GPU_CACHE["vram_mb"] = int(vt / 1024 / 1024) if vt else None
    except Exception:
        pass
    _GPU_CACHE["ts"] = time.time()
    return _GPU_CACHE["name"], _GPU_CACHE["vram_mb"]

def api_resolve_audio(audio_id):
    """audio_replacement_id → storage_key (rồi tải qua /files/<key>)."""
    r = requests.get(f"{API_URL}/audio/{audio_id}/key", headers=WORKER_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json().get("key")

# ALD 13/06/2026 - Thư viện "Giọng nói": voice string 'voicelib:<id>' → resolve storage_key qua API worker
# (GET /worker/voices/:id) → tải file ref về tmp LOCAL → trả path cho viXTTS làm 'ref' (clone giọng từ file
# mẫu lúc TTS). Cache theo id (tải 1 lần / process) để khỏi tải lại mỗi câu. None nếu không resolve được.
_VOICELIB_CACHE = {}
def api_resolve_voice_ref(voice_id):
    """'voicelib:<id>' → local path file ref audio (đã tải về tmp). None nếu lỗi/không tìm thấy."""
    vid = (voice_id or "").strip()
    if not vid:
        return None
    if vid in _VOICELIB_CACHE:
        p = _VOICELIB_CACHE[vid]
        if p and os.path.exists(p):
            return p
    r = requests.get(f"{API_URL}/worker/voices/{vid}", headers=WORKER_HEADERS, timeout=15)
    if r.status_code != 200:
        return None
    key = r.json().get("key")
    if not key:
        return None
    ext = os.path.splitext(key)[1] or ".wav"
    dest = os.path.join(tempfile.gettempdir(), f"voicelib-{vid}{ext}")
    api_download(key, dest)
    _VOICELIB_CACHE[vid] = dest
    return dest

def api_download(key, dest):
    key = str(key or "")
    dest = str(dest or "").split("?", 1)[0].split("#", 1)[0]
    url = key if key.startswith(("http://", "https://")) else f"{API_URL}/files/{key}"
    headers = WORKER_HEADERS if (not key.startswith(("http://", "https://")) or key.startswith(API_URL + "/files/")) else {}
    with requests.get(url, headers=headers, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk: f.write(chunk)
    return dest

def _cut_motion_driver_segment(src, tmp_dir, params, job_id, label="driver"):
    """Cắt driver theo frame để các segment liền nhau không mất/lặp frame ở mối nối."""
    try:
        start = max(0.0, float(params.get("driverStartSec") or 0))
    except Exception:
        start = 0.0
    try:
        dur = float(params.get("driverDurSec") or 0)
    except Exception:
        dur = 0.0
    try:
        target_frames = int(params.get("frames") or 0)
        target_fps = float(params.get("render_fps") or params.get("fps") or 16)
        target_dur = (target_frames / target_fps) if target_frames > 1 and target_fps > 0 else 0.0
        if dur > 0 and target_dur > 0 and dur > target_dur + 0.05:
            api_log(job_id, f"driverDurSec {dur:.3f}s > preset {target_dur:.3f}s → cắt theo preset để giữ timeline motion", "info")
            dur = target_dur
    except Exception:
        pass
    if start <= 0 and dur <= 0:
        return src
    dst = os.path.join(tmp_dir, f"{label}_segment.mp4")
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            check=True, capture_output=True, text=True, timeout=30,
        )
        rate = (probe.stdout or "").strip()
        num, den = (rate.split("/") + ["1"])[:2]
        source_fps = float(num) / max(1.0, float(den))
        start_frame = max(0, int(round(start * source_fps)))
        end_frame = (start_frame + max(1, int(round(dur * source_fps)))) if dur > 0 else None
        vf = f"trim=start_frame={start_frame}" + (f":end_frame={end_frame}" if end_frame else "") + ",setpts=PTS-STARTPTS"
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-vf", vf, "-map", "0:v:0"]
        if dur > 0:
            cmd += ["-map", "0:a?", "-af", f"atrim=start={start:.9f}:end={start + dur:.9f},asetpts=PTS-STARTPTS"]
        else:
            cmd += ["-map", "0:a?", "-af", f"atrim=start={start:.9f},asetpts=PTS-STARTPTS"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", dst]
    except Exception as e:
        api_log(job_id, f"không đọc được FPS driver để cắt theo frame (giữ nguyên video): {e}", "warn")
        return src
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            frame_range = f"{start_frame}..{end_frame - 1}" if end_frame else f"{start_frame}..end"
            api_log(job_id, f"Cắt driver motion theo frame {frame_range} ({source_fps:g}fps)", "info")
            return dst
    except Exception as e:
        api_log(job_id, f"cắt driver motion lỗi (giữ nguyên video gốc): {e}", "warn")
    return src

def _rgb24_frame_stats(path, tmp_dir, focus="all"):
    """Mean RGB của ảnh/frame đầu qua ffmpeg. focus='hair' ưu tiên vùng tối/trung có màu để bắt tone tóc."""
    raw = os.path.join(tmp_dir, f"rgbstats-{random.randint(100000, 999999)}.rgb")
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", path, "-frames:v", "1",
           "-vf", "scale=160:-1:flags=area,format=rgb24", "-f", "rawvideo", raw]
    subprocess.run(cmd, check=True, capture_output=True, timeout=45)
    data = b""
    try:
        with open(raw, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(raw)
        except Exception:
            pass
    n = len(data) // 3
    if n <= 0:
        return None
    step = max(3, (n // 12000) * 3)
    r = g = b = cnt = 0
    for i in range(0, len(data) - 2, step):
        rr = data[i] / 255.0
        gg = data[i + 1] / 255.0
        bb = data[i + 2] / 255.0
        if focus == "hair":
            lum = 0.2126 * rr + 0.7152 * gg + 0.0722 * bb
            sat = max(rr, gg, bb) - min(rr, gg, bb)
            # Tóc/nền tối nằm chủ yếu ở shadow/midtone. Bỏ vùng váy/da sáng để không làm loãng màu tóc.
            if not (0.08 <= lum <= 0.62 and sat >= 0.045):
                continue
        r += rr
        g += gg
        b += bb
        cnt += 1
    if cnt <= 0:
        return None
    return (r / cnt, g / cnt, b / cnt)

# #region ALD 03/07/2026 (re-add + fix audio 05/07) - faceLock: khóa IDENTITY mặt người mẫu sau render Wan.
# Wan vẽ lại 100% khung hình nên mặt luôn drift về driver. Fix = swap mặt TỪNG FRAME về đúng mặt ảnh mẫu
# (insightface/inswapper_128, venv riêng ~/facelock — worker/facelock/setup.sh để tái tạo): GIỮ biểu cảm/khẩu
# hình của frame, chỉ đắp identity mẫu. Chạy sau grade/audio, TRƯỚC RIFE/upload. Bật: param faceLock=1 hoặc env
# MOTION_FACELOCK_DEFAULT=1 (param đè env). MẶC ĐỊNH TẮT. Lỗi/chưa cài = warn + GIỮ output gốc, không fail job.
# ⚠ ALD 05/07/2026 - swap_video.py encode -an (video-only) nên PHẢI mux lại audio từ src_mp4 sau swap — đây là
#   bug "clip câm" khiến faceLock bị revert ở v8; nay fix bằng bước mux copy→aac bên dưới.
FACELOCK_DIR = os.path.expanduser(os.environ.get("FACELOCK_DIR", "~/facelock"))
def _apply_face_lock(src_mp4, ref_image, tmp_dir, params, job_id):
    _default = os.environ.get("MOTION_FACELOCK_DEFAULT", "0")
    on = str(params.get("faceLock", params.get("face_lock", _default))).strip().lower() in ("1", "true", "yes", "on")
    if not on:
        return src_mp4
    py = os.path.join(FACELOCK_DIR, "venv", "bin", "python")
    script = os.path.join(FACELOCK_DIR, "swap_video.py")
    if not (os.path.isfile(py) and os.path.isfile(script)):
        api_log(job_id, f"faceLock bật nhưng {FACELOCK_DIR} chưa cài (chạy worker/facelock/setup.sh) — bỏ qua", "warn")
        return src_mp4
    dst = os.path.join(tmp_dir, "facelock.mp4")
    api_progress(job_id, 0.91, "khóa mặt người mẫu (faceLock)")
    try:
        r = subprocess.run([py, script, "--ref", ref_image, "--inp", src_mp4, "--out", dst],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not (os.path.isfile(dst) and os.path.getsize(dst) > 1024):
            api_log(job_id, f"faceLock lỗi (giữ output gốc): {((r.stderr or '') + (r.stdout or ''))[-400:]}", "warn")
            return src_mp4
        _done = [l for l in (r.stdout or "").splitlines() if "DONE" in l]
        # FIX bug câm: dst chỉ có VIDEO (swap_video.py dùng -an). Mux lại audio từ src_mp4 (Wan passthrough / bản
        # đã ghép tiếng). Thử -c:a copy (lossless) trước, fail thì aac. src không audio → -map 1:a:0? tự bỏ qua.
        dst_av = os.path.join(tmp_dir, "facelock_av.mp4")
        # ALD 13/07/2026 - Không dùng -shortest: audio nguồn đôi khi ngắn hơn video 1–2 frame nên ffmpeg cắt mất
        # phần cuối dù swap_video đã xử lý đủ frame. Khóa duration theo chính video FaceLock để giữ nguyên hình;
        # audio dài hơn thì cắt đúng video, audio ngắn hơn vài ms không được phép cắt video.
        _video_dur = _probe_dur(dst)
        m = None
        for _ac in ("copy", "aac"):
            _mux_cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", dst, "-i", src_mp4,
                        "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", _ac]
            if _video_dur > 0:
                _mux_cmd += ["-t", f"{_video_dur:.6f}"]
            _mux_cmd += ["-movflags", "+faststart", dst_av]
            m = subprocess.run(_mux_cmd,
                               capture_output=True, text=True, timeout=300)
            if m.returncode == 0 and os.path.isfile(dst_av) and os.path.getsize(dst_av) > 1024:
                api_log(job_id, f"faceLock xong: {_done[-1] if _done else 'ok'} (+audio {_ac})", "info")
                return dst_av
        api_log(job_id, f"faceLock xong nhưng mux audio lỗi (giữ bản swap): {((m.stderr if m else '') or '')[-200:]}", "warn")
        return dst
    except Exception as e:
        api_log(job_id, f"faceLock lỗi (giữ output gốc): {e}", "warn")
    return src_mp4
# #endregion


# #region ALD 27/07/2026 - DRIFT-FIX: trị "màu ngả dần (tím/cam) theo thời lượng" của Motion Transfer.
# Gốc bệnh: autoregressive 81f/window (chốt 13/07) — window sau lấy frame window trước làm mồi nên sai màu
# nhỏ CỘNG DỒN. Sửa THUẦN HẬU KỲ bằng worker/tools/motion_drift_fix.py: đo chroma cr=R/G, cb=B/G từng frame,
# neo về median 1s đầu clip, áp gain đảo (đã làm mượt) qua sendcmd+colorchannelmixer — không đụng graph/sampler,
# không match histogram per-frame nên KHÔNG tái phát bệnh "flash/nhảy màu" từng khiến 27/06 phải gỡ sạch color-pass.
# ⚠ VỊ TRÍ: chạy TRƯỚC pass ESRGAN làm nét (_apply_motion_detail_upscale) — sửa cast trên master gốc, để ESRGAN
#   không khuếch đại cast lên bản 1080p.
# MẶC ĐỊNH BẬT (param driftFix=0 / env MOTION_DRIFT_FIX_DEFAULT=0 để tắt). Clip ngắn (<MOTION_DRIFT_FIX_MIN_SEC,
# mặc định 6s) hoặc drift dưới ngưỡng --min-drift → BỎ QUA, không re-encode (khỏi mất một thế hệ nén vô ích).
# Neo mặc định = 1s đầu clip (chỉ triệt drift, KHÔNG đổi tổng thể tông). driftFixRef=1 mới neo về ảnh mẫu.
# Fail-safe tuyệt đối: mọi lỗi (thiếu script/ffmpeg cũ không nhận sendcmd/timeout) = warn + GIỮ output gốc.
DRIFT_FIX_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "motion_drift_fix.py")
def _apply_motion_drift_fix(src_mp4, ref_image, tmp_dir, params, job_id):
    _default = os.environ.get("MOTION_DRIFT_FIX_DEFAULT", "1")
    on = str(params.get("driftFix", params.get("drift_fix", _default))).strip().lower() in ("1", "true", "yes", "on")
    if not on:
        return src_mp4
    if not os.path.isfile(DRIFT_FIX_SCRIPT):
        api_log(job_id, f"driftFix bật nhưng thiếu {DRIFT_FIX_SCRIPT} (git pull lại worker) — bỏ qua", "warn")
        return src_mp4
    try:
        _min_sec = float(os.environ.get("MOTION_DRIFT_FIX_MIN_SEC", "6") or 6)
    except Exception:
        _min_sec = 6.0
    _dur = _probe_dur(src_mp4)
    if _dur and _dur < _min_sec:
        return src_mp4  # clip ngắn gần như không kịp drift — khỏi tốn thêm 1 lần encode
    try:
        _min_drift = float(params.get("driftFixMinPct", os.environ.get("MOTION_DRIFT_FIX_MIN_PCT", "1.0")) or 1.0)
    except Exception:
        _min_drift = 1.0
    dst = os.path.join(tmp_dir, "driftfix.mp4")
    cmd = [sys.executable, DRIFT_FIX_SCRIPT, src_mp4, "-o", dst, "--quiet",
           "--min-drift", f"{_min_drift:.3f}", "--crf", "16", "--preset", "medium"]
    # Neo về ẢNH MẪU chỉ khi user chủ ý: ép cả clip về tông ảnh mẫu là thay đổi look, không chỉ triệt drift.
    if str(params.get("driftFixRef", params.get("drift_fix_ref", "0"))).strip().lower() in ("1", "true", "yes", "on") \
            and ref_image and os.path.isfile(str(ref_image)):
        cmd += ["--ref", str(ref_image)]
    api_progress(job_id, 0.93, "cân màu chống ngả tông (drift-fix)")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=max(900, int((_dur or 30) * 30)))
        info = {}
        for line in reversed((r.stdout or "").splitlines()):
            if line.startswith("DRIFTFIX_JSON "):
                try:
                    info = json.loads(line[len("DRIFTFIX_JSON "):])
                except Exception:
                    info = {}
                break
        if r.returncode != 0:
            api_log(job_id, f"drift-fix lỗi (giữ output gốc): {((r.stderr or '') + (r.stdout or ''))[-400:]}", "warn")
            return src_mp4
        _bef = info.get("before") or {}
        _aft = info.get("after") or {}
        if not info.get("applied"):
            api_log(job_id, f"Drift-fix: bỏ qua ({info.get('note') or 'không cần sửa'}; "
                            f"drift cr={_bef.get('cr', 0)}% cb={_bef.get('cb', 0)}%)", "info")
            return src_mp4
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 1024):
            api_log(job_id, "drift-fix báo xong nhưng file rỗng — giữ output gốc", "warn")
            return src_mp4
        api_log(job_id, f"Drift-fix xong: drift cr {_bef.get('cr', 0)}%→{_aft.get('cr', 0)}%, "
                        f"cb {_bef.get('cb', 0)}%→{_aft.get('cb', 0)}%", "info")
        return dst
    except subprocess.TimeoutExpired:
        api_log(job_id, "drift-fix quá thời gian (giữ output gốc)", "warn")
    except Exception as e:
        api_log(job_id, f"drift-fix lỗi (giữ output gốc): {e}", "warn")
    return src_mp4
# #endregion




def _apply_ref_grade_video(src_mp4, ref_image, tmp_dir, params, job_id):
    """Grade video sau Wan theo ảnh ref để sửa drift tông màu, nhất là tóc/nền, mà không ép histogram nặng."""
    enabled = str(params.get("motionRefGrade", params.get("motion_ref_grade",
                  os.environ.get("MOTION_REF_GRADE", "0")))).strip().lower() in ("1", "true", "yes", "on")
    match_ref_on = str(params.get("matchRef", params.get("match_ref", "0"))).strip().lower() in ("1", "true", "yes", "on")
    if not enabled or not match_ref_on:
        return src_mp4
    try:
        strength = float(params.get("motionRefGradeStrength", params.get("motion_ref_grade_strength",
                         os.environ.get("MOTION_REF_GRADE_STRENGTH", "0.95"))))
    except Exception:
        strength = 0.95
    strength = max(0.0, min(1.0, strength))
    if strength <= 0.01:
        return src_mp4
    try:
        ref_rgb = _rgb24_frame_stats(ref_image, tmp_dir)
        out_rgb = _rgb24_frame_stats(src_mp4, tmp_dir)
        if not ref_rgb or not out_rgb:
            return src_mp4
        ref_hair = _rgb24_frame_stats(ref_image, tmp_dir, focus="hair") or ref_rgb
        out_hair = _rgb24_frame_stats(src_mp4, tmp_dir, focus="hair") or out_rgb
        # Ref thường là ảnh toàn thân, output thường close-up. Overall kéo tone chung; hair kéo shadow/midtone
        # để sửa tóc nâu vàng về nâu đỏ sâu hơn mà không đánh quá mạnh lên da/highlight.
        dr_all = max(-0.16, min(0.16, (ref_rgb[0] - out_rgb[0]) * strength * 0.72))
        dg_all = max(-0.16, min(0.16, (ref_rgb[1] - out_rgb[1]) * strength * 0.72))
        db_all = max(-0.16, min(0.16, (ref_rgb[2] - out_rgb[2]) * strength * 0.72))
        dr_hair = max(-0.22, min(0.22, (ref_hair[0] - out_hair[0]) * strength * 1.05))
        dg_hair = max(-0.22, min(0.22, (ref_hair[1] - out_hair[1]) * strength * 1.05))
        db_hair = max(-0.22, min(0.22, (ref_hair[2] - out_hair[2]) * strength * 1.05))
        dr_mid = max(-0.18, min(0.18, dr_all * 0.35 + dr_hair * 0.65))
        dg_mid = max(-0.18, min(0.18, dg_all * 0.35 + dg_hair * 0.65))
        db_mid = max(-0.18, min(0.18, db_all * 0.35 + db_hair * 0.65))
        ref_luma = 0.2126 * ref_rgb[0] + 0.7152 * ref_rgb[1] + 0.0722 * ref_rgb[2]
        out_luma = 0.2126 * out_rgb[0] + 0.7152 * out_rgb[1] + 0.0722 * out_rgb[2]
        bright = max(-0.045, min(0.025, (ref_luma - out_luma) * strength * 0.22))
        if max(abs(dr_all), abs(dg_all), abs(db_all), abs(dr_hair), abs(dg_hair), abs(db_hair), abs(bright)) < 0.006:
            return src_mp4
        dst = os.path.join(tmp_dir, "out_refgrade.mp4")
        vf = (
            f"colorbalance=rs={dr_hair:.4f}:rm={dr_mid:.4f}:rh={dr_all:.4f}:"
            f"gs={dg_hair:.4f}:gm={dg_mid:.4f}:gh={dg_all:.4f}:"
            f"bs={db_hair:.4f}:bm={db_mid:.4f}:bh={db_all:.4f}:pl=1"
        )
        if abs(bright) >= 0.004:
            vf += f",eq=brightness={bright:.4f}"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src_mp4,
                        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "copy", "-movflags", "+faststart", dst],
                       check=True, capture_output=True, timeout=300)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            api_log(job_id, "post: ref-grade "
                    f"allΔ=({dr_all:+.3f},{dg_all:+.3f},{db_all:+.3f}) "
                    f"hairΔ=({dr_hair:+.3f},{dg_hair:+.3f},{db_hair:+.3f}) brightness={bright:+.3f} "
                    f"ref=({ref_rgb[0]:.2f},{ref_rgb[1]:.2f},{ref_rgb[2]:.2f}) "
                    f"out=({out_rgb[0]:.2f},{out_rgb[1]:.2f},{out_rgb[2]:.2f}) "
                    f"refHair=({ref_hair[0]:.2f},{ref_hair[1]:.2f},{ref_hair[2]:.2f}) "
                    f"outHair=({out_hair[0]:.2f},{out_hair[1]:.2f},{out_hair[2]:.2f})", "info")
            return dst
    except Exception as e:
        api_log(job_id, f"ref-grade lỗi (giữ nguyên): {e}", "warn")
    return src_mp4

def api_preview(job_id, path, label, content_type="image/png"):
    """ALD 03/06/2026 - Upload 1 ảnh PREVIEW từng bước (chân dung nhân vật / cảnh) — KHÔNG phải output cuối.
    API lưu vào jobs.previews; mediaViaJob emit thành event có thumbnail → FE hiện step-by-step trên timeline.
    Best-effort: lỗi KHÔNG làm hỏng job."""
    try:
        with open(path, "rb") as f:
            requests.post(f"{API_URL}/jobs/{job_id}/preview", headers=WORKER_HEADERS,
                          files={"preview": (os.path.basename(path), f, content_type)},
                          data={"label": label or ""}, timeout=120)
    except Exception as e:
        log("preview fail:", e)

# ───────────────── Làm sạch metadata riêng tư của output ─────────────────
# ALD 26/06/2026 - Sanitize metadata nhúng TRƯỚC KHI giao file cho người dùng:
#   • Ảnh PNG do ComfyUI sinh nhúng NGUYÊN workflow graph + prompt + tên model + cấu hình node vào tEXt/zTXt
#     ("prompt"/"workflow"/"parameters") → lộ TOÀN BỘ pipeline & prompt nội bộ.
#   • Video/ảnh có thể mang đường dẫn, tên handler hoặc timestamp không hợp lệ của môi trường dựng.
# → Chỉ bảo vệ dữ liệu riêng tư/IP và chuẩn hoá file giao. KHÔNG dùng bước này để né nhãn AI/AIGC; metadata
#   stripping không bảo đảm và không được coi là cơ chế vượt qua nhận diện của nền tảng.
_EXIFTOOL = shutil.which("exiftool")
# Chunk PNG GIỮ để render đúng pixel/màu, đồng thời giữ caBX (C2PA/JUMBF provenance) nếu nguồn có sẵn.
# Các chunk text/workflow riêng của ComfyUI vẫn được loại bỏ mà không đụng pixel (IDAT giữ nguyên).
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sRGB", b"gAMA", b"cHRM", b"pHYs", b"bKGD", b"sBIT", b"caBX"}
def _strip_png_chunks(path):
    """Lọc PNG chỉ giữ chunk render (_PNG_KEEP), bỏ mọi chunk metadata. True nếu là PNG hợp lệ đã xử lý."""
    try:
        d = open(path, "rb").read()
        if d[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        out = bytearray(d[:8]); p = 8; changed = False
        while p + 8 <= len(d):
            ln = int.from_bytes(d[p:p + 4], "big"); t = d[p + 4:p + 8]
            if t in _PNG_KEEP:
                out += d[p:p + 12 + ln]
            else:
                changed = True
            if t == b"IEND":
                break
            p += 12 + ln
        if changed and len(out) > 8:
            with open(path, "wb") as f:
                f.write(out)
        return True
    except Exception as _e:
        log(f"strip png chunks lỗi: {_e}")
        return False
def _qt_creation_time():
    """ISO-8601 UTC timestamp cho MP4/MOV creation_time."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def _strip_output_metadata(path, content_type=""):
    """Xoá metadata file output TẠI CHỖ. KHÔNG raise: strip lỗi thì giữ file gốc, không chặn upload."""
    tmp_out = None
    try:
        ext = os.path.splitext(path)[1].lower(); ct = (content_type or "").lower()
        is_video = ext in (".mp4", ".mov", ".m4v", ".webm", ".mkv") or ct.startswith("video/")
        is_image = ext in (".png", ".jpg", ".jpeg", ".webp") or ct.startswith("image/")
        if not (is_video or is_image) or not os.path.exists(path):
            return path
        # PNG (output chính của ComfyUI): lọc chunk text/workflow thuần Python trước, pixel nguyên vẹn;
        # caBX provenance được giữ. ffmpeg re-encode PNG để SÓT eXIf nên KHÔNG dùng.
        if is_image and ext == ".png" and _strip_png_chunks(path):
            return path
        # exiftool: xoá MỌI tag (EXIF/XMP/iTXt) tại chỗ, KHÔNG đụng pixel — dùng cho JPG/WebP (lossless).
        if _EXIFTOOL and is_image:
            r = subprocess.run([_EXIFTOOL, "-q", "-q", "-all=", "-overwrite_original", str(path)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                return path
            # exiftool lỗi → rơi xuống ffmpeg
        tmp_out = f"{path}.clean{ext or '.bin'}"
        if is_video:
            mp4ish = ext in (".mp4", ".mov", ".m4v") or ct == "video/mp4"
            created_at = _qt_creation_time()
            base = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path),
                    "-map_metadata", "-1", "-map_chapters", "-1",
                    "-metadata", f"creation_time={created_at}",
                    "-metadata:s:v:0", f"creation_time={created_at}",
                    "-metadata:s:a:0", f"creation_time={created_at}",
                    "-c", "copy"]
            tail = (["-movflags", "+faststart"] if mp4ish else []) + [tmp_out]
            # ALD 13/07/2026 - không xoá H.264 SEI/provenance để phục vụ né nhận diện. Chỉ remux metadata
            # container riêng tư và fast-start; nội dung bitstream giữ nguyên.
            subprocess.run(base + tail, check=True, capture_output=True, timeout=300)
        else:  # ── ẢNH (không có exiftool) ──
            # PNG: xoá chunk workflow/prompt riêng tư, giữ caBX provenance và pixel. KHÔNG dùng ffmpeg vì
            # ffmpeg re-encode PNG để sót eXIf.
            if ext == ".png" and _strip_png_chunks(path):
                return path
            if ext in (".jpg", ".jpeg"):   # JPG buộc re-encode (EXIF nằm trong APP1) → giữ chất lượng cao
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path), "-map_metadata", "-1", "-q:v", "2", tmp_out]
            elif ext == ".webp":
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path), "-map_metadata", "-1", "-lossless", "1", tmp_out]
            else:   # png hỏng chunk-filter / ảnh khác → ffmpeg re-encode lossless (vẫn rớt tEXt)
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path), "-map_metadata", "-1", "-frames:v", "1", tmp_out]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 512:
            os.replace(tmp_out, path)
        return path
    except Exception as _e:
        log(f"strip metadata output lỗi (giao file gốc): {_e}")
        return path
    finally:
        try:
            if tmp_out and os.path.exists(tmp_out):
                os.remove(tmp_out)
        except Exception:
            pass

def api_upload_output(job_id, path, content_type="video/mp4", variant=None, label=None, final=True):
    """Upload 1 output. variant/label → đa output/job (3 preset); final=False → CHƯA set done (worker tự
    patch done sau khi upload hết). Single-output (final=True, mặc định) → set done luôn (giữ tương thích)."""
    _strip_output_metadata(path, content_type)   # ALD 13/07 - bảo vệ prompt/workflow riêng tư; không né nhãn AI
    data = {"final": "true" if final else "false"}
    if variant is not None: data["variant"] = str(variant)
    if label: data["label"] = label
    with open(path, "rb") as f:
        r = requests.post(f"{API_URL}/jobs/{job_id}/output", headers=WORKER_HEADERS,
                          files={"output": (os.path.basename(path), f, content_type)}, data=data, timeout=600)
    r.raise_for_status()
    return r.json()

# #region ALD 13/07/2026 - TikTok Delivery: chuẩn hoá kỹ thuật phát hành, KHÔNG né nhãn AI/AIGC.
_DELIVERY_DISABLED = {"source", "original", "off", "none", "0", "false", "no"}

def _probe_tiktok_delivery(path):
    """Đọc thông số A/V cần thiết để xác minh file delivery; trả dict rỗng khi ffprobe lỗi."""
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
        ], capture_output=True, text=True, timeout=60, check=True)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}

def _motion_target_duration(params):
    try:
        value = float(params.get("_target_output_sec") or 0)
        return value if value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0

def _fit_motion_source_duration(src_mp4, tmp_dir, params, job_id):
    """Ép master không-delivery về đúng số frame yêu cầu; pad frame cuối thay vì làm hụt thời lượng."""
    target_sec = _motion_target_duration(params)
    if target_sec <= 0:
        return src_mp4
    fps = max(1, int(params.get("render_fps", params.get("fps", 16)) or 16))
    target_frames = max(1, int(round(target_sec * fps)))
    timeline_sec = target_frames / float(fps)
    out = os.path.join(tmp_dir, "motion_exact_duration.mp4")
    has_audio = _has_audio(src_mp4)
    vf = (f"fps={fps},tpad=stop_mode=clone:stop_duration={timeline_sec + 1.0:.6f},"
          f"trim=end_frame={target_frames},setpts=N/{fps}/TB,format=yuv420p")
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src_mp4),
           "-map", "0:v:0", "-vf", vf, "-fps_mode", "cfr",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af",
                f"aresample=async=1:first_pts=0,apad,atrim=end={timeline_sec:.9f},asetpts=PTS-STARTPTS",
                "-c:a", "aac"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=max(300, int(target_sec * 20)))
        actual_frames = _video_nframes(out)
        if actual_frames != target_frames:
            raise RuntimeError(f"frame output {actual_frames} != {target_frames}")
        api_log(job_id, f"Giữ đúng thời lượng: {target_frames} frame @ {fps}fps = {target_frames / fps:.3f}s", "info")
        return out
    except Exception as e:
        api_log(job_id, f"ép thời lượng chính xác lỗi (giữ output hiện tại): {e}", "warn")
        return src_mp4

# #region ALD 17/07/2026 - LÀM NÉT CHI TIẾT (ESRGAN) trước delivery — trị "output có 1 lớp blur".
# Gốc bệnh: Wan render 544×960 (trần VRAM 540p) rồi delivery phóng 2× lên 1080×1920 bằng lanczos —
# lanczos chỉ NỘI SUY, không sinh chi tiết → file 1080p nhưng độ nét thật 544p (user thấy "lớp blur").
# Fix: pass ESRGAN ×4 (4x-UltraSharp — thêm chi tiết thật, xem [[motion-enhance-upscale-quality]]) rồi co
# lanczos về cạnh ngắn 1080 TRƯỚC khi vào delivery (delivery chỉ còn scale ~1:1 = supersampling).
# ⚠ RAM: full-batch ×4 từng OOM (ghi chú 28/06 ở build_video_upscale_workflow) → pass này chạy CHUNKED
# qua VHS_BatchManager (mỗi chunk MOTION_DETAIL_UPSCALE_BATCH=16 frame ~1.6GB, VideoCombine append dần).
# Audio mux lại từ src bằng ffmpeg (không đi qua VHS meta-batch cho chắc). Fail-safe: lỗi → giữ src.
# Toggle: params.detailUpscale (FE motions + task-cloud) / env MOTION_DETAIL_UPSCALE_DEFAULT (mặc định TẮT).
# Chỉ áp path SELF-HOST (provider cloud trả video sẵn, không dùng GPU box). Bỏ qua nếu cạnh ngắn đã ≥1080.
def _video_wh(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=20)
        w, h = (r.stdout or "").strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0

def build_video_detail_upscale_workflow(video_name, target_w, target_h, fps, prefix="detail-up",
                                        upscale_model=None, frames_per_batch=16):
    _um = upscale_model or os.environ.get("MOTION_UPSCALE_MODEL", "4x-UltraSharp.pth")
    return {
        "05": {"class_type": "VHS_BatchManager", "inputs": {"frames_per_batch": int(frames_per_batch)}},
        "10": {"class_type": "VHS_LoadVideo", "inputs": {"video": video_name, "force_rate": 0, "custom_width": 0,
               "custom_height": 0, "frame_load_cap": 0, "skip_first_frames": 0, "select_every_nth": 1,
               "format": "AnimateDiff", "meta_batch": ["05", 0]}},
        "20": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": _um}},
        "30": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["20", 0], "image": ["10", 0]}},
        "40": {"class_type": "ImageScale", "inputs": {"image": ["30", 0], "width": int(target_w), "height": int(target_h),
               "upscale_method": "lanczos", "crop": "disabled"}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["40", 0], "frame_rate": int(fps), "loop_count": 0,
                "filename_prefix": prefix, "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 16,
                "pingpong": False, "save_output": True, "meta_batch": ["05", 0]}},
    }

def _apply_motion_detail_upscale(src_mp4, tmp_dir, params, job_id):
    _default = os.environ.get("MOTION_DETAIL_UPSCALE_DEFAULT", "0")
    on = str(params.get("detailUpscale", params.get("detail_upscale", _default))).strip().lower() \
        not in ("0", "false", "no", "off", "none", "")
    if not on:
        return src_mp4
    try:
        w, h = _video_wh(src_mp4)
        if not w or not h:
            raise RuntimeError("không đọc được kích thước video nguồn")
        if min(w, h) >= 1080:
            api_log(job_id, f"Làm nét chi tiết: bỏ qua (nguồn {w}×{h} đã đủ nét)", "info")
            return src_mp4
        scale = 1080.0 / min(w, h)
        tw, th = int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)
        fps = _video_fps(src_mp4) or int(params.get("render_fps", params.get("fps", 16)) or 16)
        frames = _video_nframes(src_mp4) or 0
        fpb = max(1, int(os.environ.get("MOTION_DETAIL_UPSCALE_BATCH", "16")))
        api_progress(job_id, 0.90, "làm nét chi tiết (ESRGAN)")
        name = comfy_upload(src_mp4)
        prefix = f"detailup-{job_id[:8]}-{int(time.time()) % 100000}"  # suffix chống nhặt file cũ khi retry cùng job
        comfy_submit(build_video_detail_upscale_workflow(name, tw, th, fps, prefix=prefix, frames_per_batch=fpb))
        # ⚠ KHÔNG comfy_poll được: VHS meta-batch TỰ REQUEUE prompt MỚI cho mỗi đoạn → history của prompt
        # gốc báo success ngay sau đoạn 1 (outputs rỗng). Thay bằng chờ MP4 FINALIZE: VideoCombine stream
        # qua ffmpeg, moov chỉ ghi khi ĐÓNG file sau đoạn cuối → _comfy_prefixed_output (check _mp4_has_moov)
        # trả file = chắc chắn xong. Deadline theo số frame (~1s/frame trần) + honor cancel như comfy_poll.
        _deadline = time.time() + max(900, frames * 1.0)
        _hb = 0.0
        up_out = None
        while time.time() < _deadline:
            up_out = _comfy_prefixed_output(prefix)
            if up_out:
                break
            now = time.time()
            if now - _hb > 15:
                api_heartbeat(job_id); _hb = now
                _elapsed = max(0.0, now - (_deadline - max(900, frames * 1.0)))
                _est = max(60.0, frames * 0.85)  # ~0.85s/frame đo thực tế trên 5090 (544×960 ×4, batch 16)
                api_progress(job_id, round(0.90 + 0.03 * min(1.0, _elapsed / _est), 3), "làm nét chi tiết (ESRGAN)")
            if api_job_cancelled(job_id):
                comfy_interrupt(); raise RuntimeError("Job đã bị hủy — đã dừng ComfyUI")
            time.sleep(5)
        if not up_out:
            comfy_interrupt()
            raise RuntimeError("pass ESRGAN không trả MP4 (quá deadline)")
        if _has_audio(src_mp4):  # audio KHÔNG đi qua VHS meta-batch → mux lại từ nguồn
            muxed = os.path.join(tmp_dir, "detail_up_audio.mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", up_out, "-i", str(src_mp4),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", muxed],
                           check=True, capture_output=True, timeout=600)
            up_out = muxed
        api_log(job_id, f"Làm nét chi tiết OK: {w}×{h} → ESRGAN ×4 → {tw}×{th}", "info")
        return up_out
    except Exception as e:
        api_log(job_id, f"Làm nét chi tiết lỗi (giữ output gốc): {e}", "warn")
        return src_mp4
# #endregion

def _apply_motion_delivery(src_mp4, tmp_dir, params, job_id):
    """Giữ master Motion nguồn; không upscale, crop hoặc ép CFR trong node Motion."""
    # ALD 19/07/2026 - Motion luôn trả master nguồn. TikTok delivery đã gỡ khỏi node và bị vô hiệu hoá
    # cả với workflow/job cũ còn lưu preset để không bake upscale 1080p/CFR30 trước Enhance.
    preset = "source"
    if preset in _DELIVERY_DISABLED:
        api_log(job_id, "Delivery: giữ nguyên thông số file nguồn", "info")
        return _fit_motion_source_duration(src_mp4, tmp_dir, params, job_id), False

    out = os.path.join(tmp_dir, "tiktok_delivery_1080x1920_cfr30.mp4")
    try:
        requested_duration = _motion_target_duration(params) or _probe_dur(src_mp4)
        if requested_duration <= 0:
            raise RuntimeError("không đọc được duration video nguồn")
        target_frames = max(1, int(round(requested_duration * 30)))
        duration = target_frames / 30.0
        has_audio = _has_audio(src_mp4)
        # #region ALD 17/07/2026 - CAS sharpen sau scale (trị cảm giác "lớp blur" khi nguồn < 1080p bị phóng to).
        # CAS (contrast-adaptive sharpening) gọn viền không halo — bù nội suy lanczos + encode, KHÔNG sinh chi tiết
        # (chi tiết thật do pass ESRGAN _apply_motion_detail_upscale lo). Toggle: params.deliverySharpen
        # (true/false hoặc số 0..1 = cường độ) / env MOTION_DELIVERY_CAS (mặc định tắt; "0"/"off" = tắt).
        _cas_raw = params.get("deliverySharpen", params.get("delivery_sharpen",
                              os.environ.get("MOTION_DELIVERY_CAS", "0")))
        _cas = 0.0
        _cas_s = str(_cas_raw).strip().lower()
        if _cas_s not in ("0", "false", "no", "off", "none", ""):
            try:
                _cas = min(max(float(_cas_s), 0.0), 1.0)
            except ValueError:
                _cas = 0.3  # true/on/bật → cường độ mặc định
        _cas_vf = f"cas={_cas:.2f}," if _cas > 0 else ""
        # #endregion
        # Fit theo chiều ngắn rồi crop tâm phần dư rất nhỏ của nguồn gần 9:16; tuyệt đối không kéo giãn hình.
        vf = ("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop=1080:1920:(iw-ow)/2:(ih-oh)/2,setsar=1,{_cas_vf}fps=30,"
              f"tpad=stop_mode=clone:stop_duration={duration + 1.0:.6f},"
              f"trim=end_frame={target_frames},setpts=N/30/TB,format=yuv420p")
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-fflags", "+genpts", "-i", str(src_mp4)]
        if not has_audio:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        cmd += [
            "-map", "0:v:0", "-map", ("0:a:0" if has_audio else "1:a:0"),
            "-vf", vf, "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
            "-maxrate", "12M", "-bufsize", "24M", "-g", "60", "-keyint_min", "30", "-sc_threshold", "0",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        ]
        cmd += ["-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=end={duration:.9f},asetpts=PTS-STARTPTS"]
        # Giữ presentation timestamp tự nhiên của MP4: `avoid_negative_ts=make_zero` dịch video +~1 AAC frame
        # (đã đo 13/07: video start_time=0.021s). Không dùng cờ đó để video bắt đầu đúng 0.000s.
        cmd += ["-t", f"{duration:.6f}", "-movflags", "+faststart", out]
        subprocess.run(cmd, check=True, capture_output=True, timeout=max(600, int(duration * 20)))

        probe = _probe_tiktok_delivery(out)
        streams = probe.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        fps_num, fps_den = (str(video.get("avg_frame_rate") or "0/1").split("/", 1) + ["1"])[:2]
        fps = float(fps_num) / max(float(fps_den), 1.0)
        actual_frames = int(video.get("nb_frames") or 0) or _video_nframes(out)
        valid = (
            video.get("codec_name") == "h264" and int(video.get("width") or 0) == 1080 and
            int(video.get("height") or 0) == 1920 and abs(fps - 30.0) < 0.01 and
            video.get("pix_fmt") == "yuv420p" and audio.get("codec_name") == "aac" and
            int(audio.get("sample_rate") or 0) == 48000 and actual_frames == target_frames and
            os.path.getsize(out) > 1024
        )
        if not valid:
            raise RuntimeError(f"ffprobe không đạt preset: video={video} audio={audio}")
        api_log(job_id,
                f"TikTok Delivery OK: {target_frames} frame/{target_frames / 30:.3f}s · 1080×1920 · CFR 30 · "
                "H.264 High/yuv420p · AAC 48kHz stereo · fast-start",
                "info")
        return out, True
    except Exception as e:
        api_log(job_id, f"TikTok Delivery lỗi (giữ output gốc): {e}", "warn")
        return _fit_motion_source_duration(src_mp4, tmp_dir, params, job_id), False

def _motion_delivery_label(params, applied):
    label = str(params.get("outputLabel") or params.get("output_label") or "").strip()
    if not applied:
        return label or None
    suffix = "TIKTOK_1080P_CFR30"
    return f"{label}_{suffix}" if label else suffix
# #endregion

# ───────────────────────── ComfyUI client ─────────────────────────
# ALD 20/06/2026 - ComfyUI trên box hay bị recycle (watchdog xả RAM) / OOM-restart. Job bắn vào lúc nó đang
# dậy → POST /upload/image hay /prompt ném ConnectionError (Connection refused :8188) → job CHẾT NGAY (đây là
# lý do node "Motion Transfer" bắn nguội hay lỗi, còn workflow chạy tryon trước thì ComfyUI đã ấm nên không).
# comfy_wait_ready chờ ComfyUI lên lại; _comfy_call thử lại 1 lần sau khi nó dậy.
def comfy_wait_ready(timeout=None, interval=2.0):
    """Chờ ComfyUI trả 200 ở /system_stats (đã sẵn sàng nhận prompt). True nếu lên kịp, False nếu hết timeout."""
    deadline = time.time() + (COMFY_READY_WAIT_SEC if timeout is None else timeout)
    first = True
    while time.time() < deadline:
        try:
            if requests.get(f"{COMFY_URL}/system_stats", timeout=5).ok:
                if not first: log("ComfyUI đã sẵn sàng lại")
                return True
        except Exception:
            pass
        if first: log(f"ComfyUI chưa sẵn sàng (đang restart?) — chờ tối đa {int(COMFY_READY_WAIT_SEC)}s"); first = False
        time.sleep(interval)
    return False

# ALD 22/06/2026 - check/chờ 1 custom node CỤ THỂ đã NẠP (object_info). wait_ready chỉ đảm bảo ComfyUI core lên,
# custom nodes (vd "RIFE VFI") nạp SAU → cần riêng để tránh race "node not found" khi submit ngay sau recycle.
def _comfy_has_node(name):
    try:
        import urllib.parse as _up
        r = requests.get(f"{COMFY_URL}/object_info/{_up.quote(name)}", timeout=8)
        return r.ok and (name in r.json())
    except Exception:
        return False

def comfy_wait_node(name, timeout=60, interval=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _comfy_has_node(name): return True
        time.sleep(interval)
    return False

def _comfy_call(do_req):
    """Gọi 1 request tới ComfyUI; nếu nó đang xuống (ConnectionError/refused) → chờ dậy rồi thử LẠI 1 lần."""
    try:
        return do_req()
    except requests.exceptions.ConnectionError:
        if comfy_wait_ready():
            return do_req()
        raise

def comfy_upload(path):
    """Upload file (ảnh/video) vào ComfyUI input/ → trả filename ComfyUI dùng trong workflow."""
    def _do():
        with open(path, "rb") as f:
            r = requests.post(f"{COMFY_URL}/upload/image",
                              files={"image": (os.path.basename(path), f)},
                              data={"overwrite": "true"}, timeout=300)
        r.raise_for_status()
        return r.json()
    d = _comfy_call(_do)
    name = d.get("name") or os.path.basename(path)
    return f"{d['subfolder']}/{name}" if d.get("subfolder") else name

def comfy_submit(workflow):
    # client_id → ComfyUI gửi event ws (progress/executing) cho prompt này (xem _comfy_ws_listen).
    def _do():
        r = requests.post(f"{COMFY_URL}/prompt", json={"prompt": workflow, "client_id": COMFY_CLIENT_ID}, timeout=30)
        if not r.ok:
            log(f"[comfy_submit] HTTP {r.status_code} — body: {r.text[:2000]}")
            # ALD 01/07/2026 - 400 = ComfyUI validate graph fail → trích node_errors/error CHI TIẾT để lộ lên job log/UI
            # (đỡ phải SSH grep). Chỉ đúng node + field sai (vd 'value not in list', 'required input missing').
            detail = r.text[:1500]
            try:
                j = r.json()
                if j.get("node_errors"):
                    parts = []
                    for nid, ne in j["node_errors"].items():
                        errs = "; ".join(e.get("message", str(e)) for e in (ne.get("errors") or []))
                        parts.append(f"node[{nid}] {ne.get('class_type', '')}: {errs}")
                    detail = " | ".join(parts)[:1500] or detail
                elif j.get("error"):
                    e = j["error"]
                    detail = (e.get("message") if isinstance(e, dict) else str(e))[:1500]
            except Exception:
                pass
            raise RuntimeError(f"ComfyUI {r.status_code}: {detail}")
        return r.json()
    d = _comfy_call(_do)
    if "error" in d: raise RuntimeError(f"ComfyUI prompt error: {json.dumps(d)[:400]}")
    return d["prompt_id"]

# ALD 20/06/2026 - Recycle ComfyUI để TRẢ RAM về OS. /free chỉ nhả VRAM; RAM tiến trình (RSS ~50GB do torch/CUDA
# leak) chỉ về OS khi RESTART. Gọi từ _resource_guard_delay khi worker IDLE + ComfyUI ôm RAM (job xong không tự xả).
_last_recycle_ts = 0.0
def _comfy_port():
    try: return COMFY_URL.rsplit(":", 1)[-1].split("/")[0].split("?")[0] or "8188"
    except Exception: return "8188"

def comfy_rss_gb():
    """RSS (GB) tiến trình python ComfyUI, khớp theo PORT ('main.py ... <COMFY_PORT>'). 0 nếu không chạy.
    ALD 20/06/2026 - KHÔNG match 'ComfyUI.*main.py': cmdline thực chỉ là 'python main.py --listen .. --port 8188',
    KHÔNG có chữ 'ComfyUI' → pattern cũ (watchdog) khớp RỖNG → đo trượt → RAM leo ~47GB mà không ai recycle.
    Match theo port mới đúng tiến trình thật."""
    port = _comfy_port()
    try:
        out = subprocess.run(
            ["bash", "-c", f"for p in $(pgrep -f 'main.py.*{port}'); do ps -o rss= -p \"$p\" 2>/dev/null; done | sort -rn | head -1"],
            capture_output=True, text=True, timeout=10)
        return int((out.stdout or "0").strip() or 0) / 1048576.0
    except Exception:
        return 0.0

def comfy_queue_idle():
    """ComfyUI KHÔNG đang chạy/chờ prompt nào. Đọc lỗi → coi BẬN (an toàn, không recycle nhầm lúc đang render)."""
    try:
        q = requests.get(f"{COMFY_URL}/queue", timeout=6).json()
        return not (q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False

def comfy_recycle(reason=""):
    """Best-effort, KHÔNG raise. Trả RAM về OS = KILL CỨNG python ComfyUI (SIGKILL) → PM2 autorestart dựng fresh.
    ⚠ KHÔNG dùng `pm2 restart comfyui`: nó gửi SIGINT → torch/CUDA teardown abort ("Fatal Python error: Aborted")
    → PM2 kẹt "waiting restart" → OUTAGE (đã dính 20/06). SIGKILL chết sạch, không qua shutdown graceful nên
    không abort (đã test thật: 41GB→1.2GB, ready ~14s). /free trước (nhả VRAM); wait_ready để chờ respawn xong."""
    global _last_recycle_ts
    _last_recycle_ts = time.time()
    try: requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=8)
    except Exception: pass
    port = _comfy_port()
    try:
        subprocess.run(["pkill", "-9", "-f", f"main.py.*{port}"], timeout=15, capture_output=True)
        log(f"recycle ComfyUI ({reason}) — SIGKILL python :{port}, PM2 autorestart dựng fresh")
    except Exception as e:
        log(f"recycle ComfyUI kill lỗi (bỏ qua): {e}"); return False
    comfy_wait_ready(90)   # chờ PM2 dựng lại xong rồi mới cho worker nhận job kế
    return True

# ALD 05/06/2026 - Nghe /ws ComfyUI trong 1 thread riêng để biết tiến độ sampling THẬT (KSampler value/max).
# Cập nhật `state` dùng chung với comfy_poll: frac (0..1 sampling), activity (mốc nhận tin cuối), connected, error.
# Mọi lỗi đều nuốt → fail-open: ws hỏng thì comfy_poll vẫn chạy poll /history như cũ.
def _comfy_ws_listen(prompt_id, state):
    if _ws is None:
        return
    url = COMFY_URL.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?clientId={COMFY_CLIENT_ID}"
    try:
        conn = _ws.create_connection(url, timeout=10)
    except Exception as e:
        log("comfy ws connect fail (fallback /history):", e); return
    state["connected"] = True
    state["activity"] = time.time()
    conn.settimeout(5)
    try:
        while not state["stop"]:
            try:
                raw = conn.recv()
            except _ws.WebSocketTimeoutException:
                continue   # timeout 5s → quay lại kiểm tra state["stop"]
            except Exception:
                state["connected"] = False; break   # ws rớt → comfy_poll quay về poll /history (tắt bắt-treo)
            state["activity"] = time.time()
            if not raw or isinstance(raw, (bytes, bytearray)):
                continue   # frame nhị phân = ảnh preview → chỉ tính là "có hoạt động"
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type"); d = msg.get("data") or {}
            pid = d.get("prompt_id")
            if pid and pid != prompt_id:
                continue   # event của prompt khác (chung client_id) → bỏ qua, không tính activity sai
            if t == "progress":
                mx = d.get("max") or 0
                if mx:
                    state["frac"] = max(0.0, min(1.0, float(d.get("value") or 0) / float(mx)))
            elif t == "execution_error" and pid == prompt_id:
                state["error"] = (d.get("exception_message") or "execution_error")[:400]
            elif t == "executing" and d.get("node") is None and pid == prompt_id:
                state["frac"] = 1.0   # node=null = prompt xong (history sẽ chốt success/output)
    finally:
        try: conn.close()
        except Exception: pass

def comfy_poll(prompt_id, job_id, deadline_sec=1800, prog_lo=None, prog_hi=None, prog_step="", windows=1,
               output_prefix=None, output_exts=(".mp4",)):
    """Poll /history tới khi success/error. Nếu truyền prog_lo/prog_hi → còn bơm tiến độ sampling realtime
    (map frac 0..1 của ws vào dải [prog_lo, prog_hi]) + phát hiện ComfyUI treo.
    windows>1: clip dài chạy NHIỀU animate-window, ComfyUI reset frac mỗi window → gộp thành tiến độ TỔNG
    monotonic ((đoạn-1+frac)/windows) + nhãn 'đoạn N' (tránh log nhảy 86%→25%→90% gây hiểu lầm).

    ALD 07/06/2026 - TIMEOUT theo KHÔNG-TIẾN-TRIỂN, KHÔNG theo tổng thời gian. Job nào còn nhả tín hiệu ws
    HOẶC tiến độ (frac) còn tăng thì KHÔNG bị hủy dù vượt deadline_sec (job dài: BDS nhiều stage @720p, ESRGAN
    ảnh to). last_progress = lần CUỐI thấy "còn tiến". Chỉ hủy khi đứng quá COMFY_HANG_SEC (30').
    - LIVE (có ws, đã từng kết nối): chém theo COMFY_HANG_SEC tính từ last_progress.
    - LIVE nhưng ws CHƯA bao giờ kết nối (ws hỏng): không đo được tiến → fallback trần deadline_sec (như cũ).
    - NON-live (ảnh ngắn, không truyền prog_lo/hi): giữ trần deadline_sec như cũ.
    COMFY_MAX_SEC = trần tuyệt đối cho mọi chế độ (chống loop vô hạn)."""
    start = time.time()
    deadline = start + deadline_sec          # chỉ dùng cho NON-live / ws-chưa-kết-nối
    last_progress = start                     # mốc "còn TIẾN" (ws nhả tín hiệu / frac tăng) — reset liên tục khi đang chạy
    last_alive = start                        # ALD 11/06/2026 - mốc ComfyUI CÒN SỐNG & CÒN BIẾT prompt (history/queue)
    last_hb = 0
    live = prog_lo is not None and prog_hi is not None and _ws is not None
    ever_connected = False
    state = {"stop": False, "connected": False, "activity": 0.0, "frac": 0.0, "error": None}
    th = None
    if live:
        th = threading.Thread(target=_comfy_ws_listen, args=(prompt_id, state), daemon=True)
        th.start()
    last_coarse = None
    win = 1; last_frac_seen = 0.0   # đếm animate-window (frac reset = sang đoạn mới) → tiến độ tổng monotonic
    # ALD 10/06/2026 - Chống tụt tiến độ: sampler xong các đoạn rồi nhưng node HẬU KỲ (RIFE nội suy/VAE decode)
    # reset frac về 0 → trước đây 90% tụt 70% gây hiểu lầm. Giờ: reset sau đoạn cuối = "hậu kỳ" (map frac vào
    # phần còn lại) + best_overall đảm bảo tiến độ tổng CHỈ TĂNG.
    best_overall = 0.0; post = False
    try:
        while True:
            now = time.time()
            # Trần tuyệt đối (mọi chế độ) — chống chạy vô hạn nếu ComfyUI nhả tín hiệu mãi mà không xong.
            if now - start > COMFY_MAX_SEC:
                comfy_interrupt(); raise RuntimeError(f"ComfyUI quá {int(COMFY_MAX_SEC/60)}' (trần tuyệt đối)")
            # ALD 02/06/2026 - Heartbeat TRONG lúc chờ ComfyUI (job dài) → worker không bị badge coi là
            # "offline" (healthy = có heartbeat trong 60s; trước đây job dài >60s không nhịp → tưởng chết).
            if now - last_hb > 15:
                api_heartbeat(job_id); last_hb = now
            # ALD 05/06/2026 - Honor cancel: user hủy → dừng + interrupt ComfyUI ngay (nhả GPU, không chờ hết).
            if api_job_cancelled(job_id):
                comfy_interrupt(); raise RuntimeError("Job đã bị hủy — đã dừng ComfyUI")
            # ALD 05/06/2026 - Tiến độ realtime (chỉ khi ws đã kết nối, tránh báo nhầm khi ws hỏng).
            if live and state["connected"]:
                ever_connected = True
                if state["error"]:
                    raise RuntimeError(f"ComfyUI exec error: {state['error']}")
                # ws còn nhả BẤT KỲ message nào = ComfyUI còn sống → còn tiến.
                if state["activity"] > last_progress:
                    last_progress = state["activity"]
                frac = state["frac"]
                if frac > 0:
                    # Đa-window: frac tụt mạnh = ComfyUI sang animate-window mới → đếm đoạn. Tiến độ TỔNG
                    # monotonic = (đoạn-1 + frac)/windows (không nhảy lùi); nhãn "· đoạn N" để log rõ ràng.
                    if frac < last_frac_seen - 0.25:
                        if windows > 1 and win < windows:
                            win += 1
                        else:
                            post = True   # reset SAU đoạn cuối = node hậu kỳ (RIFE nội suy / VAE decode)
                    if frac > last_frac_seen:        # tiến độ sampling TĂNG = chắc chắn còn tiến → reset mốc
                        last_progress = now
                    last_frac_seen = frac
                    if post:
                        # Hậu kỳ: map frac 0→1 vào phần còn lại (best→0.999) — không bao giờ tụt về 70%.
                        overall = best_overall + (0.999 - best_overall) * frac
                    else:
                        overall = min(0.999, ((win - 1) + frac) / windows) if windows > 1 else frac
                    best_overall = max(best_overall, overall)
                    overall = best_overall   # tiến độ tổng CHỈ TĂNG
                    step_label = (f"{prog_step} · hậu kỳ (giải mã + xuất video)" if post
                                  else f"{prog_step} · đoạn {win}/{windows}" if windows > 1 else prog_step)
                    p = round(prog_lo + (prog_hi - prog_lo) * overall, 3)
                    fields = {"progress": p}
                    if step_label: fields["current_step"] = step_label
                    api_patch(job_id, **fields)
                    coarse = int(overall * 10) * 10   # 0,10,...,100 theo tiến độ TỔNG
                    if coarse != last_coarse:
                        last_coarse = coarse
                        api_log(job_id, f"{step_label} {round(overall * 100)}%".strip(), "info", p)   # /logs: 1 dòng mỗi 10%
            try:
                r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=10)
                if r.status_code == 200 and prompt_id in r.json():
                    jd = r.json()[prompt_id]
                    st = jd.get("status", {}).get("status_str", "")
                    if st == "success": return jd.get("outputs", {})
                    if st == "error":
                        msg = "unknown"
                        for m in jd.get("status", {}).get("messages", []):
                            if m[0] == "execution_error": msg = m[1].get("exception_message", "")[:400]
                        raise RuntimeError(f"ComfyUI exec error: {msg}")
                    last_alive = now   # prompt đã vào history (đang/đã chạy) → ComfyUI còn sống & còn biết job
                else:
                    # Chưa vào history → còn render? kiểm /queue. Còn trong queue = còn sống. KHÔNG còn cả history
                    # lẫn queue = ComfyUI nghi đã restart/mất prompt (vd OOM-kill) → KHÔNG reset last_alive → fail nhanh.
                    q = requests.get(f"{COMFY_URL}/queue", timeout=10).json()
                    if prompt_id in {e[1] for e in (q.get("queue_running", []) + q.get("queue_pending", []))}:
                        last_alive = now
            except requests.RequestException as e:
                log("comfy poll:", e)   # ComfyUI không tới được (chết / treo D-state) → last_alive đứng yên
            # ALD 11/06/2026 - FAIL NHANH: ComfyUI unreachable HOẶC mất prompt (restart/OOM) quá COMFY_DOWN_SEC →
            # chuyển job sang error NGAY, không treo tới COMFY_HANG_SEC (30'). Render-chậm-còn-sống không dính (vì
            # history/queue còn prompt → last_alive reset liên tục).
            down = now - last_alive
            if down > COMFY_DOWN_SEC:
                # Sau sampling/hậu kỳ, một số workflow video đã bắt đầu ghi file ra output/ nhưng prompt biến mất
                # khỏi history/queue sớm hơn mong đợi. Nếu filename_prefix là deterministic, thử bắt file trước
                # khi kết luận ComfyUI chết/restart để tránh false-fail ở pha xuất video.
                if output_prefix and (post or best_overall >= 0.9):
                    cand = _comfy_prefixed_output(output_prefix, output_exts)
                    if cand:
                        api_log(job_id, "Prompt biến mất sau sampling; đã bắt output theo filename_prefix", "warn")
                        return {"__direct_file__": cand}
                comfy_interrupt()
                raise RuntimeError(f"ComfyUI không phản hồi/mất prompt {int(down)}s (>{int(COMFY_DOWN_SEC)}s) — nghi ComfyUI chết hoặc restart (OOM/treo)")
            # TREO = KHÔNG tiến triển quá lâu (KHÔNG phải tổng thời gian — job còn render thì để chạy).
            if live and ever_connected:
                idle = now - last_progress
                if idle > COMFY_HANG_SEC:
                    comfy_interrupt()
                    raise RuntimeError(f"ComfyUI đứng hình {int(idle/60)}' không tiến triển (>{int(COMFY_HANG_SEC/60)}') — nghi OOM/treo GPU")
            elif now >= deadline:
                # NON-live, hoặc LIVE mà ws chưa bao giờ kết nối (không có tín hiệu để đo tiến) → trần deadline_sec.
                raise RuntimeError("ComfyUI timeout" + ("" if not live else " (ws không kết nối)"))
            time.sleep(3)
    finally:
        state["stop"] = True

def comfy_fetch_output(outputs, exts=(".mp4",)):
    """Tìm file output trong history outputs → tải bytes qua /view.
    Hỗ trợ cả sentinel {"__direct_file__": "/tmp/..."} khi caller đã bắt được file theo prefix."""
    if isinstance(outputs, dict):
        direct = outputs.get("__direct_file__")
        if isinstance(direct, str) and os.path.exists(direct):
            return direct
    for _nid, nout in (outputs or {}).items():
        for it in (nout.get("gifs") or []) + (nout.get("videos") or []) + (nout.get("images") or []):
            fn = it.get("filename", "")
            if fn.lower().endswith(exts):
                params = {"filename": fn, "subfolder": it.get("subfolder", ""), "type": it.get("type", "output")}
                r = requests.get(f"{COMFY_URL}/view", params=params, timeout=300)
                r.raise_for_status()
                tmp = os.path.join(tempfile.gettempdir(), fn)
                with open(tmp, "wb") as f: f.write(r.content)
                return tmp
    return None

def comfy_view_file(filename, subfolder="", ftype="output"):
    """Tải 1 file output theo TÊN qua /view (không cần /history). Dùng làm fallback khi ComfyUI
    restart làm mất history nhưng FILE đã ghi ra output/. Trả path tmp hoặc None (404/lỗi)."""
    try:
        r = requests.get(f"{COMFY_URL}/view", params={"filename": filename, "subfolder": subfolder, "type": ftype}, timeout=120)
        if r.status_code == 200 and r.content and len(r.content) > 1024:
            tmp = os.path.join(tempfile.gettempdir(), filename)
            with open(tmp, "wb") as f: f.write(r.content)
            return tmp
    except requests.RequestException:
        pass
    return None

# ALD 03/06/2026 - Chống MP4 BỊ CẮT: file thiếu atom 'moov' (index) = không player nào phát được.
# Xảy ra khi fetch /view lúc ComfyUI ĐANG ghi file (VHS ghi mdat trước, moov ở cuối). Bắt buộc kiểm tra.
def _mp4_has_moov(path):
    """True nếu MP4 có box 'moov' ở top-level (đã ghi xong index). Thiếu = file ghi dở/bị cắt."""
    try:
        size = os.path.getsize(path)
        if size < 16: return False
        with open(path, "rb") as f:
            i = 0
            while i < size - 8:
                f.seek(i)
                hdr = f.read(8)
                if len(hdr) < 8: break
                bsize = int.from_bytes(hdr[:4], "big")
                if hdr[4:8] == b"moov": return True
                if bsize == 1: bsize = int.from_bytes(f.read(8), "big")
                elif bsize == 0: break   # box cuối, kéo tới EOF
                if bsize < 8: break
                i += bsize
        return False
    except OSError:
        return False

def _finalize_mp4(path):
    """Chuẩn hoá MP4 trước upload: (1) BẮT BUỘC có moov (raise nếu thiếu — chặn upload file cắt);
    (2) remux faststart (moov ra đầu) cho phát web/stream mượt. Không re-encode (-c copy)."""
    if not _mp4_has_moov(path):
        raise RuntimeError("MP4 thiếu atom 'moov' — file ghi chưa xong (bị cắt), không upload")
    fs = os.path.splitext(path)[0] + ".faststart.mp4"
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", path,
                        "-c", "copy", "-movflags", "+faststart", fs], check=True, timeout=180)
        if os.path.exists(fs) and os.path.getsize(fs) > 1024 and _mp4_has_moov(fs):
            return fs
    except Exception as e:
        log("finalize remux fail (dùng bản gốc, vẫn có moov):", e)
    return path

def _await_comfy_video(pid, job_id, prefix, timeout=1800):
    """Chờ ComfyUI render video. ROBUST: (a) history báo error → raise; (b) ƯU TIÊN file output theo TÊN
    (prefix cố định) khi đã có MOOV (ghi xong) — KHÔNG phụ thuộc history-by-pid nên không treo nếu history lệch/chậm
    (đây là bug treo cũ); (c) history success → fetch (cũng validate moov). moov validation chống đọc file ghi dở.
    Dùng chung cho talk + story-film. Trả path mp4 (đủ moov) hoặc raise."""
    out = None; deadline = time.time() + timeout; last_hb = 0
    while time.time() < deadline:
        if time.time() - last_hb > 15: api_heartbeat(job_id); last_hb = time.time()
        if api_job_cancelled(job_id):
            comfy_interrupt(); raise RuntimeError("Job đã bị hủy — đã dừng ComfyUI")
        try:
            r = requests.get(f"{COMFY_URL}/history/{pid}", timeout=10)
            if r.status_code == 200 and pid in r.json():
                jd = r.json()[pid]; st = jd.get("status", {}).get("status_str", "")
                if st == "error":
                    msg = next((m[1].get("exception_message", "")[:300] for m in jd.get("status", {}).get("messages", []) if m[0] == "execution_error"), "unknown")
                    raise RuntimeError(f"ComfyUI exec error: {msg}")
                if st == "success":
                    fo = comfy_fetch_output(jd.get("outputs", {}))
                    if fo and _mp4_has_moov(fo): out = fo; break
        except requests.RequestException:
            pass
        # File output theo TÊN (deterministic prefix) — bắt cả khi history-by-pid không khớp/chậm.
        # moov = đã ghi xong (chống file cắt). Đây là đường CHÍNH, không chờ history.
        cand = comfy_view_file(f"{prefix}_00001-audio.mp4") or comfy_view_file(f"{prefix}_00001.mp4")
        if cand and _mp4_has_moov(cand): out = cand; break
        time.sleep(3)
    if not out: raise RuntimeError("ComfyUI timeout/không trả MP4 (lip-sync)")
    return out

# ───────────────────────── Wan 2.2 Animate workflow ─────────────────────────

# #region ALD 13/07/2026 - Prompt trung tính: để Image Ref quyết định chất liệu/ánh sáng.
# Fast LightX2V CFG=1 bám positive rất trực tiếp. Các default cũ cố ép da matte, ánh sáng đều và
# "5 ngón rõ" khiến Wan tái sinh foreground tách khỏi nền, đặc biệt tay trông nhựa/CG. Giữ prompt
# nền tối thiểu; extraPositive chỉ còn tác dụng khi user thực sự tự nhập.
MOTION_BASE_POSITIVE = "natural body proportions, smooth natural motion, photorealistic video"
MOTION_BODY_PROPORTION_POSITIVE = (
    "preserve the reference person's exact body build, shoulder width, arm thickness, arm length, "
    "hand size and limb proportions, transfer motion only from the driver"
)
MOTION_BODY_PROPORTION_NEGATIVE = (
    "changed body proportions, body shape copied from the motion driver, thin or shrunken arms, "
    "tiny hands, elongated limbs, shortened limbs"
)
MOTION_LEGACY_AUTO_EXTRA_POSITIVES = frozenset({
    "soft even matte lighting with retained detail in bright areas, natural matte skin with visible pores and realistic texture, stable natural mouth and lips, steady well-formed bare hands with five clearly separated fingers, natural fingertips, clean short natural fingernails",
    "no overexposed or washed-out highlights, natural skin tone with visible pores, not plastic, not doll-like, stable natural mouth and lips, no warped or flickering mouth, no hallucinated teeth, steady well-formed fingers, no extra or fused fingers",
})
# #endregion

def build_wan_workflow(ref_name, motion_name, p, prefix="motion-out"):
    W = int(p.get("width", 720)); H = int(p.get("height", 1280))
    # #region ALD 22/06/2026 - GATE model-on-VRAM theo RES + SỐ FRAME (đo thật 22/06, Ollama đã tắt = full 32GB):
    #   • ≤540p (cạnh dài ≤968) + NGẮN (≤250 frame, ~10s/15s @16fps) → model THẲNG VRAM (main_device, block_swap=0):
    #     đo 540p/241f = đỉnh 29.9GB FIT (chạy xong OK) → RAM xuống ~22GB (hết ôm 14B ở CPU) + nhanh hơn.
    #   • Clip DÀI ("auto" 961f) / res cao (720p) / 30fps (≫250 frame) → OFFLOAD (block_swap=30): 14B ở CPU chừa
    #     VRAM cho decode dài, tránh OOM/treo (720p model-on-VRAM treo 31-32GB — đã chứng minh). Param/env override ưu tiên.
    # Knob: MOTION_VRAM_MAX_FRAMES (250), MOTION_VRAM_MAX_EDGE (968). Ép tay: MOTION_BLOCK_SWAP / MOTION_LOAD_DEVICE.
    # ALD 27/06/2026 - REVERT 480→540 (quay lại baseline đã ĐO an toàn VRAM: 544×960/241f = đỉnh 29.9/32GB).
    # ALD 19/07/2026 - 540p là mặc định thật cho mọi duration. Chỉ quality=720p mới mở trần 720;
    # nhờ đó Node Motion và Task Cloud có thể quyết định theo worker mà không bị duration tự nâng RAM.
    _quality = str(p.get("quality") or "540p").strip().lower()
    _short_cap = 720 if _quality == "720p" else int(os.environ.get("MOTION_SHORT", "540"))
    if min(W, H) > _short_cap:
        _sc = _short_cap / float(min(W, H)); W = _even16(W * _sc); H = _even16(H * _sc)
    _fr = int(p.get("frames", 81))
    # ALD 27/06/2026 - REVERT 1080→968 (đi kèm MOTION_SHORT 540): 968 = cạnh dài của 540p dọc 544×960 — đúng mức
    # đã ĐO an toàn (29.9/32GB). 1080 ở 540-short cho phép 540×1080 (>9:16) = 583k px/frame > 518k đo-an-toàn → rủi ro OOM.
    # Max clip 15s = 241f < 250 nên frame KHÔNG bao giờ là thứ đẩy sang RAM → khỏi cap frame.
    _vram_ok = (max(W, H) <= int(os.environ.get("MOTION_VRAM_MAX_EDGE", "968"))
                and _fr <= int(os.environ.get("MOTION_VRAM_MAX_FRAMES", "250")))
    _def_bs, _def_ld = (0, "main_device") if _vram_ok else (30, "offload_device")
    _block_swap = int(p.get("block_swap", int(os.environ.get("MOTION_BLOCK_SWAP", str(_def_bs)))))
    _load_device = str(p.get("load_device", os.environ.get("MOTION_LOAD_DEVICE", _def_ld)))
    # #endregion
    F = int(p.get("frames", 81))
    # ALD 13/07/2026 - Gỡ hẳn profile Natural/HQ khỏi backend vì 20 bước non-distill làm video tệ hơn.
    # Node Motion đã được khóa Fast 4 bước trong API + _normalize_motion_params. Hàm build này còn dùng chung
    # cho Trend TikTok/Teen Flycam/SS nên vẫn giữ tuning Fast riêng của các pipeline đó. Riêng alias Natural/HQ
    # legacy luôn bị hạ về baseline Fast, không thể bật lại non-distill.
    _legacy_profile = str(p.get("render_profile", p.get("renderProfile", ""))).strip().lower()
    _legacy_hq = str(p.get("hq", "")).strip().lower() in ("1", "true", "yes", "on")
    _retired_natural = _legacy_profile in ("natural", "quality", "official", "hq") or _legacy_hq
    if _retired_natural:
        S = 4
        cfg = 1.0
        lx2v = 1.0
        scheduler = "dpm++_sde"
    else:
        S = int(p.get("steps", 4))
        cfg = _motion_float(p, "cfg", default=0.0)
        lx2v = _motion_float(p, "lora_lightx2v", "loraLightx2v", default=1.0)
        if cfg < 0.5:
            cfg = 1.0
        scheduler = str(p.get("scheduler", "dpm++_sde"))
    shift = _motion_float(p, "shift", default=5.0)                 # ALD 21/06 - REVERT 05/06: shift 5.0
    # ALD 16/06/2026 - render_fps: fps RENDER NATIVE. Preset MAX dùng 30 (đúng doc Wan2.2-Animate: fps=30 →
    # motion mượt THẬT, khớp dữ liệu train) thay vì 16fps+RIFE (nội suy đoán). Mặc định 16 (preset nhanh).
    # render_fps>=30 → run_motion BỎ pass RIFE (đã native, tránh nội suy chồng). Driver cũng pre-convert về rfps.
    rfps = int(p.get("render_fps", 16) or 16)
    # faceSource=driver: lấy crop mặt từ video motion để giữ chuyển động/biểu cảm mặt theo driver.
    # faceSource=ref: khóa identity hơn bằng mặt ref lặp lại F frame, nhưng biểu cảm có thể kém bám motion.
    face_source = str(p.get("face_source", p.get("faceSource", "driver"))).lower().strip()
    # ALD 11/07/2026 - Pose = DWPose (node 20) THÔ của driver; face crop mặt driver (node 31). Path retarget
    # ViTPose (kijai WanAnimatePreprocess: node 25/26/27) đã GỠ HẲN theo chốt user ("vô dụng và thừa" — 3 sự
    # cố/1 ngày: vỡ khung, mặt phóng to, tay gập + chậm +30%). Node kijai + models trên box để nguyên (trơ).
    pose_src = ["20", 0]
    _face_driver_src = ["31", 0]
    # #region ALD 21/07/2026 - CROP MẶT CHUẨN ViTPose (verdict đối chiếu kijai 21/07, trị "chu mỏ"/"ngẩng đầu"):
    # chuỗi DWPose 20b→30→31 pad128 LỆCH CHUẨN — mặt chiếm ít pixel trong 512² (môi loãng tín hiệu → prior chu O),
    # và DWPose 68 điểm hay jitter/méo trên driver thật. Chuẩn kijai example_02 + official Wan-AI: bbox CHẶT từ
    # 68 landmark ×1.3, nở riêng phía TRÁN 3× (chống ngửa đầu đúng cách official), resize thẳng 512². Node
    # PoseAndFaceDetection (WanAnimatePreprocess, ĐÃ cài trên box + 2 onnx models/detection) xuất face_images
    # trực tiếp (output #1); retarget_image KHÔNG nối (path pose-retarget đã bác 11/07 — đây CHỈ là face crop).
    # Rollback: param faceCropMode=dwpose / env MOTION_FACE_CROP=dwpose; box thiếu node → submit fallback tự hạ.
    _face_crop_mode = str(p.get("faceCropMode", p.get("face_crop_mode",
                          os.environ.get("MOTION_FACE_CROP", "vitpose")))).strip().lower()
    _vitpose_face = _face_crop_mode not in ("dwpose", "legacy", "off", "0")
    if _vitpose_face:
        _face_driver_src = ["26", 1]
    # #region ALD 21/07/2026 - KHÔI PHỤC POSE RETARGET (opt-in) trị "tay ảo / ra gầy" (user nhớ custom cánh tay cũ).
    # Bản 09/07 (51fcdbf) nắn xương driver về tỉ lệ cơ thể ẢNH MẪU qua PoseAndFaceDetection(retarget_image=ref)
    # → DrawViTPose. Bị gỡ 11/07 vì "vỡ khung + mặt phóng to + tay gập + chậm +30%". NAY an toàn hơn: node 26
    # (PoseAndFaceDetection) đã chạy tốt cho face crop; bật retarget chỉ nối thêm retarget_image + node 27 vẽ pose.
    # MẶC ĐỊNH TẮT (giữ nguyên vóc dáng theo driver — hành vi hiện tại đã hết chu mỏ). Bật: param poseRetarget=1 /
    # env MOTION_POSE_RETARGET=1. Cần _vitpose_face (dùng chung node 26); face crop vẫn từ ["26",1] không đổi.
    _pose_retarget = (_vitpose_face
                      and str(p.get("poseRetarget", p.get("pose_retarget",
                              os.environ.get("MOTION_POSE_RETARGET", "0")))).strip().lower() in ("1", "true", "yes", "on"))
    if _pose_retarget:
        pose_src = ["27", 0]
    # #endregion
    # #endregion
    # #region ALD 11/07/2026 - TOGGLE BỎ KEYPOINT TAY DRIVER (trị "ngón tay kéo dài"). Gốc bệnh: driver chỉ tay
    # VÀO camera / frame mờ → DWPose lấy keypoint bàn tay SAI → Wan bám theo → duỗi ngón cho "rõ 5 ngón" → dài vô lý.
    # Bỏ detect_hand → stick-figure KHÔNG có ngón → Wan tự dựng bàn tay theo ẢNH MẪU (bớt bám cử chỉ ngón của driver,
    # đổi lấy HẾT dài/méo). KHÁC retargeting (không đụng xương/tỉ lệ → KHÔNG scale-explosion/vỡ khung). Pose THÂN vẫn giữ.
    # ALD 11/07/2026 (chiều) - REVERT default về "enable": thử default "disable" (bỏ tay) → Wan KHÔNG có keypoint tay
    # → dựng NẮM TAY / mất ngón (tệ hơn ngón dài). Toggle nhị phân không có mức giữa, nên GIỮ bám driver mặc định
    # (có tay, đôi lúc ngón dài). Trị ngón dài phải đi hướng khác (steps↑ / negative prompt / hand-detailer), KHÔNG bỏ tay.
    # Tắt tay thủ công (chấp nhận nắm tay) vẫn được: param driverHands=0 / env MOTION_DRIVER_HANDS=0 / toggle FE.
    _dh = str(p.get("driverHands", p.get("driver_hands", os.environ.get("MOTION_DRIVER_HANDS", "1")))).strip().lower()
    _detect_hand = "disable" if _dh in ("0", "false", "no", "off", "disable") else "enable"
    # #endregion
    # #region ALD 16/07/2026 - ÁP LẠI faceCropPadding=128 (trị "mặt ngước lên trời"). Fix gốc 02/07 (f2ce062,
    # A/B trên box: pad 0 = đầu ngửa 20-30° mọi frame; pad 128 = hết ngửa + GIỮ lipsync; 64 chưa đủ, 192 không hơn)
    # bị revert v8 (3b62133) cuốn mất → bệnh tái phát 16/07. Gốc bệnh: crop mặt driver sát mày→cằm (padding 0)
    # trên mặt nhỏ (~90px) rồi upscale 512 = mờ + mất ngữ cảnh đầu → nhánh face-conditioning của Wan rơi về
    # prior "ca sĩ hát ngửa đầu". Khuyến nghị Kijai WanAnimatePreprocess#10: face crop phải ôm trọn đầu.
    # Chỉ áp lại PADDING — không kèm auto-ColorMatch/faceLock của chuỗi 02-03/07 (user đã chốt Wan nguyên bản).
    _face_pad = _motion_int(p, "face_crop_padding", "faceCropPadding", default=128)
    # #endregion
    # #region ALD 21/07/2026 - TỰ NHIÊN HÓA nhánh mặt (user chốt sau 1 ngày A/B): driver vào + ref vào, đúng thiết
    # kế gốc Wan-Animate — face_images = crop mặt driver, không LivePortrait, không env override. Node cũ lỡ lưu
    # faceSource='liveportrait' (default FE 17-21/07) → tự lành về driver; chỉ 'ref' giữ nghĩa opt-in khóa identity.
    face_images_ref = ["33", 0] if face_source in ("ref", "reference", "identity", "lock") else _face_driver_src
    # #endregion
    # extraPositive là override chủ ý của user. Bỏ hai chuỗi tự điền cũ để workflow đã lưu
    # tự lành mà không tiếp tục cưỡng ép Wan vẽ lại tay/ánh sáng.
    _pos_extra = str(p.get("extraPositive") or p.get("extra_positive") or "").strip().strip(",").strip()
    if _pos_extra in MOTION_LEGACY_AUTO_EXTRA_POSITIVES:
        _pos_extra = ""
    _pos_extra = (", " + _pos_extra) if _pos_extra else ""
    # #region ALD 16/07/2026 - Driver chỉ cấp chuyển động, không cấp vóc dáng.
    # DWPose thô chứa cả khoảng cách khớp của driver. Khi driver tay nhỏ/gầy, pose_strength 0.8 kéo Wan
    # thu nhỏ cẳng tay/bàn tay của mẫu. Không bật lại pose-retarget (đã gây vỡ khung/mặt phóng to); thay vào đó
    # giảm vừa đủ lực pose, tăng neo CLIP của ảnh Ref và khóa hình thể bằng text conditioning tối thiểu.
    # build_wan_workflow còn được các pipeline khác dùng trực tiếp, nên chỉ áp dụng khi param được bật rõ;
    # riêng node Motion Transfer luôn được _normalize_motion_params tự bật mặc định.
    _body_lock = _motion_bool(p, "body_proportion_lock", "bodyProportionLock", default=False)
    _pose_strength = _motion_float(p, "pose_strength", "poseStrength", default=0.7 if _body_lock else 0.8)
    _clip_strength = _motion_float(p, "clip_strength", "clipStrength", default=1.35 if _body_lock else 1.2)
    if _body_lock:
        _pose_strength = min(_pose_strength, 0.7)
        _clip_strength = max(_clip_strength, 1.35)
    _positive_prompt = str(p.get("positive_prompt") or MOTION_BASE_POSITIVE).strip()
    _negative_prompt = str(p.get("negative_prompt") or "色调艳丽，过曝，皮肤油光，高光反射，手臂反光，静态，细节模糊不清，最差质量，低质量，畸形，多余的手指, shiny oily skin, glossy plastic skin, specular highlights on skin, blown-out highlights, overexposed arms, flash glare").strip()
    if _body_lock:
        _positive_prompt = f"{_positive_prompt}, {MOTION_BODY_PROPORTION_POSITIVE}"
        _negative_prompt = f"{_negative_prompt}, {MOTION_BODY_PROPORTION_NEGATIVE}"
    # #endregion
    # #region ALD 13/07/2026 - Chia window cho clip dài.
    # Loop gốc của WanAnimate lấy frame CUỐI vừa sinh làm temporal ref cho window sau. Một bàn tay lỗi ở window 2
    # vì thế bị truyền sang window 3/4 và identity/background trôi dần. Anchored-context tắt loop autoregressive
    # (frame_window_size của AnimateEmbeds = toàn clip), sau đó để WanVideoContextOptions chia context chồng lấn.
    # Sampler context đưa image_cond frame 0 (ảnh Ref gốc) vào đầu MỖI context rồi blend overlap, không chain
    # frame lỗi của context trước. Luồng cũ vẫn bật được bằng windowMode=autoregressive để A/B hoặc fallback.
    _window_plan = _wan_window_plan(p, F)
    _wsz_cm = _window_plan["context_frames"]
    _multi_window = _window_plan["multi_window"]
    _anchored_context = _window_plan["anchored"]
    if _anchored_context:
        # Context overlap đã blend trực tiếp noise prediction. ColorMatch hậu từng window vừa thừa vừa có thể
        # làm ánh sáng nhảy; luôn tắt trong anchored-context.
        _win_cm = "disabled"
    elif str(os.environ.get("MOTION_ENABLE_COLOR_ADJUST", "0")).strip().lower() in ("1", "true", "yes", "on"):
        _win_cm = p.get("colormatch", "mkl")            # đường cũ: bật color-adjust tổng → giữ nguyên hành vi
    elif _multi_window:
        # ALD 14/07/2026 - RAW là mặc định. Thử reinhard để neo drift cho thấy nó match
        # histogram TOÀN FRAME; khi bố cục output khác Ref, window cuối dồn saturation/contrast vào
        # riêng mặt. Drift được xử lý bằng window 81 cân đều bên normalize, không grade pixel.
        # Vẫn giữ param/env tường minh để A/B thủ công khi cần.
        _win_cm = str(p.get("windowColormatch")
                      or p.get("window_colormatch")
                      or os.environ.get("MOTION_WINDOW_COLORMATCH", "disabled")).strip() or "disabled"
    else:
        _win_cm = "disabled"                            # clip 1-window: không có mối nối → khỏi động vào màu
    # #endregion
    wf = {
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "11": {"class_type": "ImageResizeKJv2", "inputs": {
            "image": ["10", 0], "width": W, "height": H, "upscale_method": "lanczos",
            # ALD 05/06/2026 - "crop" (center) thay "pad_edge_pixel": pad lặp pixel mép gây DẢI LỖI bên phải khi
            # ảnh ref lệch tỉ lệ 9:16. crop cắt giữa, không để lại dải.
            "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center",
            "divisible_by": 16, "device": "cpu"}},
        "12": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": motion_name, "force_rate": rfps, "custom_width": W, "custom_height": H,
            "frame_load_cap": F, "skip_first_frames": _motion_int(p, "skip_first_frames", "skipFirstFrames", default=0),
            "select_every_nth": 1, "format": "AnimateDiff"}},
        # #region ALD 16/07/2026 - TÁCH ĐÔI DWPose: pose_images KHÔNG chứa điểm mặt (trị "mặt ngước lên trời").
        # A/B 16/07 (driver Douyin 576×1024, ref văn phòng): DWPose detect mặt driver SAI (68 điểm mặt vẽ rối/méo)
        # → điểm mặt hỏng nằm THẲNG trong pose_images đầu độc hướng đầu + miệng (prior "ca sĩ ngửa đầu há miệng").
        # Bằng chứng: tắt điểm mặt trong pose → đầu bám driver hoàn toàn (face crop giữ 0.5); đổi face crop sang
        # ref sạch mà GIỮ điểm mặt trong pose → VẪN ngửa. faceCropPadding 128 một mình không đủ.
        # Đúng thiết kế Wan-Animate: skeleton chỉ BODY(+tay), biểu cảm/hướng mặt do nhánh face_images (crop) lo.
        # Node 20 = pose_images (face TẮT); node 20b = DWPose phụ CÓ face, chỉ để FaceMaskFromPoseKeypoints crop mặt.
        "20": {"class_type": "DWPreprocessor", "inputs": {
            "image": ["12", 0], "detect_hand": _detect_hand, "detect_body": "enable", "detect_face": "disable",  # ALD 11/07 - _detect_hand: toggle bỏ tay; ALD 21/07 - detect_face TẮT cố định: skeleton gốc Wan-Animate chỉ body+tay, điểm mặt DWPose nhiễu → miệng chu/mắt lé (A/B 16/07 + 21/07)
            "resolution": max(W, H), "bbox_detector": "yolox_l.torchscript.pt",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}},
        "20b": {"class_type": "DWPreprocessor", "inputs": {
            "image": ["12", 0], "detect_hand": "disable", "detect_body": "enable", "detect_face": "enable",
            "resolution": max(W, H), "bbox_detector": "yolox_l.torchscript.pt",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}},
        "30": {"class_type": "FaceMaskFromPoseKeypoints", "inputs": {"pose_kps": ["20b", 1], "person_index": 0}},
        # #endregion
        # face_images mặc định dùng mặt trong driver để bám chuyển động/biểu cảm. Nếu user cần khóa identity chặt hơn
        # có thể set faceSource='ref', khi đó dùng crop mặt ref lặp lại F frame.
        "31": {"class_type": "ImageCropByMaskAndResize", "inputs": {
            "image": ["12", 0], "mask": ["30", 0], "base_resolution": 512, "padding": _face_pad,  # ALD 16/07 - pad 128 trị ngửa đầu (xem region faceCropPadding phía trên)
            "min_crop_resolution": 128, "max_crop_resolution": 512}},
        "21": {"class_type": "DWPreprocessor", "inputs": {
            "image": ["11", 0], "detect_hand": "disable", "detect_body": "enable", "detect_face": "enable",
            "resolution": max(W, H), "bbox_detector": "yolox_l.torchscript.pt",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}},
        "32": {"class_type": "FaceMaskFromPoseKeypoints", "inputs": {"pose_kps": ["21", 1], "person_index": 0}},
        "34": {"class_type": "ImageCropByMaskAndResize", "inputs": {
            "image": ["11", 0], "mask": ["32", 0], "base_resolution": 512, "padding": _face_pad,  # ALD 16/07 - đồng bộ pad với node 31
            "min_crop_resolution": 128, "max_crop_resolution": 512}},
        "33": {"class_type": "RepeatImageBatch", "inputs": {"image": ["34", 0], "amount": F}},
        "40": {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": "WanAnimate_relight_lora_fp16.safetensors", "strength_0": _motion_float(p, "lora_relight", "loraRelight", default=0.0),
            # ALD 17/08/2026 - MOTION_LX2V_FILE: đổi bản rank của LoRA distill (A/B bộ số Kijai: rank64;
            # example chính thức dùng rank64 @ 1.2). Default rank32 = baseline cũ, không đổi hành vi.
            "lora_1": os.environ.get("MOTION_LX2V_FILE", "lightx2v_I2V_14B_480p_cfg_step_distill_rank32_bf16.safetensors"),
            "strength_1": lx2v,
            "lora_2": "none", "strength_2": 1.0, "lora_3": "none", "strength_3": 1.0,
            "lora_4": "none", "strength_4": 1.0, "low_mem_load": False, "merge_loras": True}},
        "41": {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": _block_swap, "offload_img_emb": False, "offload_txt_emb": False,  # ALD 22/06 - LUÔN offload (=30) tính đầu hàm: model ở CPU, chừa VRAM cho decode → không OOM. (model-on-VRAM đã revert: OOM clip dài.)
            "use_non_blocking": True, "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}},
        "42": {"class_type": "WanVideoModelLoader", "inputs": {
            "model": "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors", "base_precision": "fp16_fast",
            "quantization": "disabled", "load_device": _load_device,  # ALD 22/06 - LUÔN offload_device (tính đầu hàm): model ở CPU. main_device 540p OOM ở clip dài → đã revert.
            # ALD 02/07/2026 - attention theo env MOTION_ATTENTION (default sdpa). sageattention (triton, INT8
            # attention gần-lossless) ĐÃ CÀI vào ~/ComfyUI/venv trên .165 → set MOTION_ATTENTION=sageattn để
            # sampling nhanh hơn ~15-25% + VRAM attention thấp hơn. LƯU Ý: ComfyUI phải RESTART sau khi cài
            # package thì wrapper mới import được (pm2 restart comfyui lúc idle). Param node vẫn override env.
            "attention_mode": _wan_attention(p),
            "lora": ["40", 0], "block_swap_args": ["41", 0]}},
        "50": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": "Wan2_1_VAE_bf16.safetensors", "precision": "bf16"}},
        "60": {"class_type": "WanVideoTextEncodeCached", "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16",
            # Không mô tả tay/da/ánh sáng trong default: các chi tiết này phải bám Image Ref thay vì
            # bị text guidance của LightX2V sáng tạo lại.
            "positive_prompt": _positive_prompt + _pos_extra,
            "negative_prompt": _negative_prompt,
            "quantization": "disabled", "use_disk_cache": MOTION_T5_DISK_CACHE, "device": "gpu"}},
        "70": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "71": {"class_type": "WanVideoClipVisionEncode", "inputs": {
            "clip_vision": ["70", 0], "image_1": ["11", 0], "strength_1": _clip_strength,
            "strength_2": 1.0, "crop": "center", "combine_embeds": "average", "force_offload": True, "tiles": 0, "ratio": 0.5}},
        "81": {"class_type": "WanVideoAnimateEmbeds", "inputs": {
            "vae": ["50", 0], "width": W, "height": H, "num_frames": F, "force_offload": False,
            # ALD 14/07/2026 - RAW/disabled mặc định; không grade histogram toàn frame.
            "frame_window_size": _window_plan["embed_window_frames"],
            "colormatch": _win_cm,
            # ALD 26/06/2026 - REVERT 0.9→0.8 (về baseline "tuyệt vời" 23/06). 0.9 không liên quan màu, revert chung batch.
            "pose_strength": _pose_strength,
            # ALD 21/07/2026 - face_strength default = 0.7 (user chốt "cho an toàn"): 0.6 đơ miệng, 1.0 bám nhịp nhưng lộ nét driver.
            # A/B 21/07: 0.6 = miệng đơ hoàn toàn; 1.0 = miệng bám driver (mở/đóng đúng nhịp). Lem identity → hạ per-node.
            "face_strength": _motion_float(p, "face_strength", "faceStrength", default=0.7), "clip_embeds": ["71", 0],
            "ref_images": ["11", 0], "pose_images": pose_src, "face_images": face_images_ref}},  # pose_src = DWPose thô (node 20)
        "90": {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["81", 0], "steps": S, "cfg": cfg, "shift": shift, "seed": 42,
            "force_offload": True, "scheduler": scheduler, "riflex_freq_index": 0,
            "text_embeds": ["60", 0], "rope_function": "comfy"}},
        # ALD 26/06/2026 - Tăng tile 272→400 + stride rộng hơn: ít tile hơn (3×4 thay 5×9 @ 720p), overlap giảm
        # từ 47%→28% → giảm "haze toàn frame" do pixel bị blend 4 lần ở tile junction. Chậm hơn ~10% nhưng sắc hơn.
        "100": {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["90", 0], "enable_vae_tiling": True,
            "tile_x": 400, "tile_y": 400, "tile_stride_x": 288, "tile_stride_y": 272, "normalization": "default"}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["100", 0], "frame_rate": rfps, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True, "audio": ["12", 2]}},
    }
    if _vitpose_face and face_images_ref == ["26", 1]:
        # ALD 21/07/2026 - face crop chuẩn kijai example_02: PoseAndFaceDetection (ViTPose wholebody onnx) xuất
        # face_images output #1 (bbox chặt ×1.3 + nở trán 3×, resize thẳng 512²). retarget_image CHỦ Ý không nối.
        # face_padding=0 cố định theo chuẩn (KHÔNG lấy faceCropPadding — param đó là semantics của chuỗi DWPose cũ).
        # Chỉ chèn node khi face_images THẬT SỰ tham chiếu ["26",1] (faceSource=ref thì khỏi — tránh validation đòi node thừa).
        wf["25"] = {"class_type": "OnnxDetectionModelLoader", "inputs": {
            "vitpose_model": "vitpose-l-wholebody.onnx", "yolo_model": "yolov10m.onnx",
            "onnx_device": "CUDAExecutionProvider"}}
        wf["26"] = {"class_type": "PoseAndFaceDetection", "inputs": {
            "model": ["25", 0], "images": ["12", 0], "width": W, "height": H, "face_padding": 0}}
        if _pose_retarget:
            # retarget_image = ẢNH MẪU đã resize khung (node 11) → nắn xương driver về tỉ lệ cơ thể mẫu.
            # node 27 DrawViTPose vẽ skeleton retarget thành pose_images (pose_src = ["27",0] đã set ở trên).
            wf["26"]["inputs"]["retarget_image"] = ["11", 0]
            wf["27"] = {"class_type": "DrawViTPose", "inputs": {
                "pose_data": ["26", 0], "width": W, "height": H, "retarget_padding": 16,
                "body_stick_width": -1, "hand_stick_width": -1, "draw_head": True}}
    if _anchored_context:
        wf["82"] = {"class_type": "WanVideoContextOptions", "inputs": {
            "context_schedule": _window_plan["schedule"],
            "context_frames": _window_plan["context_frames"],
            "context_stride": _window_plan["stride"],
            "context_overlap": _window_plan["overlap"],
            "freenoise": _window_plan["freenoise"],
            "verbose": False,
            "fuse_method": _window_plan["fuse_method"]}}
        wf["90"]["inputs"]["context_options"] = ["82", 0]
    # #region ALD 02/07/2026 - FETA (Enhance-A-Video, feta_args): tăng cường temporal attention → chi tiết
    # nét hơn, đỡ "ảo/mờ" khi chuyển động nhanh (tay). Chi phí ~vài % thời gian. OPT-IN (default 0 = tắt,
    # giữ nguyên baseline đã đo): bật bằng param node `feta`/`fetaWeight` (khuyến nghị thử 2.0 = default
    # của node) hoặc env MOTION_FETA_WEIGHT.
    _feta_w = _motion_float(p, "feta", "fetaWeight", "feta_weight",
                            default=float(os.environ.get("MOTION_FETA_WEIGHT", "0") or "0"))
    if _feta_w > 0:
        wf["45"] = {"class_type": "WanVideoEnhanceAVideo", "inputs": {
            "weight": _feta_w, "start_percent": 0.0, "end_percent": 1.0}}
        wf["90"]["inputs"]["feta_args"] = ["45", 0]
    # ALD 02/07/2026 - torch.compile (inductor, chỉ transformer blocks): nhanh hơn ~20-30% SAU lần compile
    # đầu (lần đầu tốn ~2-5' compile — chỉ đáng khi chạy nhiều job liên tiếp cùng preset/res). Cần triton
    # (đã có 3.7.1 trên box). OPT-IN qua env MOTION_TORCH_COMPILE=1; tắt ngay được nếu gây lỗi.
    if str(os.environ.get("MOTION_TORCH_COMPILE", "0")).strip().lower() in ("1", "true", "yes", "on"):
        wf["43"] = {"class_type": "WanVideoTorchCompileSettings", "inputs": {
            "backend": "inductor", "fullgraph": False, "mode": "default", "dynamic": False,
            "dynamo_cache_size_limit": 64, "compile_transformer_blocks_only": True}}
        wf["42"]["inputs"]["compile_args"] = ["43", 0]
    # #endregion
    # ALD 28/06/2026 - Baseline ổn định nhất là chỉ ref image + driver video.
    # ALD 27/06/2026 - RAW COLOR DEFAULT: tắt ColorMatch mặc định. Các pass chỉnh màu gây flash / đổi màu theo thời
    # gian ở Wan 2.2, nên giữ output nguyên thủy. Chỉ bật lại thủ công bằng env MOTION_ENABLE_COLOR_ADJUST=1.
    _mr = p.get("match_ref", p.get("matchRef", "0"))
    _allow_color_adjust = str(os.environ.get("MOTION_ENABLE_COLOR_ADJUST", "0")).strip().lower() in ("1", "true", "yes", "on")
    if _allow_color_adjust and str(_mr).lower() in ("1", "true", "yes", "on"):
        # ALD 22/06/2026 - mkl confirmed "hết cháy" + user OK.
        # ALD 26/06/2026 - strength 0.85 (GIỮ ĐỘ TƯƠI). Thử nghiệm cho thấy: strength 1.0 mkl ÉP phân bố output
        #   (ảnh rộng, nhiều nền) khớp ảnh gốc (close-up) → NÉN dải màu → BỆT/MẤT TƯƠI (user xác nhận "mất độ tươi").
        #   strength thấp (0.5) thì còn ÁM VÀNG. → KHÔNG dùng ColorMatch để trị nhiệt độ màu. Giữ 0.85 (cân bằng,
        #   tươi) cho phần "khớp tông chung"; còn ÁM VÀNG/CAM do nắng driver thì trị bằng knob `warmth` (post ffmpeg,
        #   = "ĐỘ ẤM" iPhone) — mát hóa white balance mà KHÔNG đụng saturation. method đổi được qua matchRefMethod.
        _mm = str(p.get("match_ref_method", p.get("matchRefMethod", "mkl")))
        try:
            _ms = float(p.get("match_ref_strength", p.get("matchRefStrength", 0.85)))
        except (TypeError, ValueError):
            _ms = 0.85
        wf["105"] = {"class_type": "ColorMatch", "inputs": {
            "image_ref": ["11", 0], "image_target": ["100", 0],
            "method": _mm, "strength": _ms, "multithread": True}}
        wf["110"]["inputs"]["images"] = ["105", 0]
    return wf


def _apply_swap_to_wan_workflow(wf, p):
    """Character-swap engine wananimate: chuyển graph animation → replacement (Mix) mode.

    Mix mode giữ background CỦA VIDEO: bg_images = frame driver tô đen vùng người, mask = vùng
    người. Chuỗi mask theo example kijai wanvideo_WanAnimate_example_01.json (Grow 10 → Blockify 32),
    thay SAM2+PointsEditor interactive bằng SAM3 core text-prompt để chạy headless.
    Chỉ thêm node 200-206 + 2 input của node 81; pose/face/sampler của motion giữ nguyên.
    """
    sam3_prompt = str(p.get("sam3Prompt") or p.get("sam3_prompt") or "person").strip() or "person"
    wf["200"] = {"class_type": "CheckpointLoaderSimple", "inputs": {
        "ckpt_name": "sam3.1_multiplex_fp16.safetensors"}}
    wf["201"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["200", 1], "text": sam3_prompt}}
    wf["202"] = {"class_type": "SAM3_VideoTrack", "inputs": {
        "images": ["12", 0], "model": ["200", 0], "conditioning": ["201", 0],
        "detection_threshold": _motion_float(p, "sam3Threshold", "sam3_threshold", default=0.5),
        "max_objects": _motion_int(p, "sam3MaxObjects", "sam3_max_objects", default=4),
        "detect_interval": _motion_int(p, "sam3DetectInterval", "sam3_detect_interval", default=1)}}
    wf["203"] = {"class_type": "SAM3_TrackToMask", "inputs": {
        "track_data": ["202", 0],
        # rỗng = union mọi người trong video; "0" = chỉ người đầu (chọn khi video đông người)
        "object_indices": str(p.get("maskIndices") or p.get("mask_indices") or "")}}
    wf["204"] = {"class_type": "GrowMaskWithBlur", "inputs": {
        "mask": ["203", 0],
        "expand": _motion_int(p, "maskGrow", "mask_grow", default=10),
        "incremental_expandrate": 0.0, "tapered_corners": True, "flip_input": False,
        "blur_radius": 0.0, "lerp_alpha": 1.0, "decay_factor": 1.0, "fill_holes": False}}
    wf["205"] = {"class_type": "BlockifyMask", "inputs": {
        "masks": ["204", 0],
        "block_size": _motion_int(p, "maskBlockify", "mask_blockify", default=32)}}
    wf["206"] = {"class_type": "DrawMaskOnImage", "inputs": {
        "image": ["12", 0], "mask": ["205", 0], "color": "0, 0, 0"}}
    wf["81"]["inputs"]["bg_images"] = ["206", 0]
    wf["81"]["inputs"]["mask"] = ["205", 0]
    return wf

def build_scail2_swap_workflow(ref_name, motion_name, p, prefix="swap-out"):
    """Character-swap engine scail2 — node CORE native (comfy_extras/nodes_scail.py + nodes_sam3.py).

    Graph bám subgraph Base của template chính thức Comfy-Org video_wan21_scail2_character_replacement
    (nhánh turbo: 6 bước, cfg 1, euler/simple, shift 5, LoRA DPO 1.0 + lightx2v rank64 0.8).
    Khác template: VHS_LoadVideo/VHS_VideoCombine (đồng bộ toolchain + mux audio driver), unet
    fp8_scaled (fp16 32.8GB không vừa 5090 32GB), 1 segment ≤81 frame (extend chaining làm sau).
    """
    W = (int(p.get("width", 544)) // 32) * 32       # WanSCAILToVideo đòi bội 32 (io.Int step=32)
    H = (int(p.get("height", 960)) // 32) * 32
    F = min(int(p.get("frames", 81) or 81), 81)     # SCAIL-2 train theo chunk 81 frame
    rfps = int(p.get("render_fps", 16) or 16)
    sam3_vid = str(p.get("sam3VideoPrompt") or p.get("sam3Prompt") or "human").strip() or "human"
    sam3_img = str(p.get("sam3ImagePrompt") or p.get("sam3Prompt") or "human").strip() or "human"
    pos = str(p.get("positive_prompt") or p.get("prompt") or
              "a person moving naturally, high quality, detailed clothing and face").strip()
    neg = str(p.get("negative_prompt") or "").strip()
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "11": {"class_type": "ImageResizeKJv2", "inputs": {
            "image": ["10", 0], "width": W, "height": H, "upscale_method": "lanczos",
            "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center",
            "divisible_by": 32, "device": "cpu"}},
        "12": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": motion_name, "force_rate": rfps, "custom_width": W, "custom_height": H,
            "frame_load_cap": F, "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff"}},
        # ── SAM3: track người trong driver + segment người trong ảnh ref, cùng 1 checkpoint ──
        "20": {"class_type": "CheckpointLoaderSimple", "inputs": {
            "ckpt_name": "sam3.1_multiplex_fp16.safetensors"}},
        "21": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["20", 1], "text": sam3_vid}},
        "22": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["20", 1], "text": sam3_img}},
        "23": {"class_type": "SAM3_VideoTrack", "inputs": {
            "images": ["12", 0], "model": ["20", 0], "conditioning": ["21", 0],
            "detection_threshold": _motion_float(p, "sam3Threshold", "sam3_threshold", default=0.5),
            "max_objects": _motion_int(p, "sam3MaxObjects", "sam3_max_objects", default=4),
            "detect_interval": 1}},
        "24": {"class_type": "SAM3_VideoTrack", "inputs": {
            "images": ["11", 0], "model": ["20", 0], "conditioning": ["22", 0],
            "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1}},
        "25": {"class_type": "SCAIL2ColoredMask", "inputs": {
            "driving_track_data": ["23", 0], "ref_track_data": ["24", 0],
            "object_indices": str(p.get("maskIndices") or p.get("mask_indices") or ""),
            "sort_by": "left_to_right", "replacement_mode": True}},
        # ── model + LoRA (thứ tự template: unet → DPO → lightx2v → shift) ──
        "30": {"class_type": "UNETLoader", "inputs": {
            "unet_name": os.environ.get("SCAIL2_UNET", "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors"),
            "weight_dtype": "default"}},
        "31": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["30", 0], "lora_name": "wan2.1_SCAIL_2_DPO_lora_bf16.safetensors",
            "strength_model": _motion_float(p, "dpoLora", "dpo_lora", default=1.0)}},
        "32": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["31", 0], "lora_name": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
            "strength_model": _motion_float(p, "distillLora", "distill_lora", default=0.8)}},
        "33": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["32", 0], "shift": _motion_float(p, "shift", default=5.0)}},
        "40": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "41": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["40", 0], "text": pos}},
        "42": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["40", 0], "text": neg}},
        "50": {"class_type": "VAELoader", "inputs": {"vae_name": "Wan2_1_VAE_bf16.safetensors"}},
        "60": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "61": {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["60", 0], "image": ["11", 0], "crop": "none"}},
        # ── conditioning + sampler ──
        "70": {"class_type": "WanSCAILToVideo", "inputs": {
            "positive": ["41", 0], "negative": ["42", 0], "vae": ["50", 0],
            "width": W, "height": H, "length": F, "batch_size": 1,
            "pose_video": ["12", 0], "pose_video_mask": ["25", 0], "replacement_mode": True,
            "pose_strength": _motion_float(p, "pose_strength", "poseStrength", default=1.0),
            "pose_start": 0.0, "pose_end": 1.0,
            "reference_image": ["11", 0], "reference_image_mask": ["25", 1],
            "clip_vision_output": ["61", 0],
            "video_frame_offset": 0, "previous_frame_count": 5}},
        "80": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "81": {"class_type": "BasicScheduler", "inputs": {
            "model": ["33", 0], "scheduler": "simple",
            "steps": int(p.get("steps", 6) or 6), "denoise": 1.0}},
        "90": {"class_type": "SamplerCustom", "inputs": {
            "model": ["33", 0], "add_noise": True,
            "noise_seed": _motion_int(p, "seed", default=42),
            "cfg": _motion_float(p, "cfg", default=1.0),
            "positive": ["70", 0], "negative": ["70", 1],
            "sampler": ["80", 0], "sigmas": ["81", 0], "latent_image": ["70", 2]}},
        "100": {"class_type": "VAEDecode", "inputs": {"samples": ["90", 1], "vae": ["50", 0]}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["100", 0], "frame_rate": rfps, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True, "audio": ["12", 2]}},
    }

# ───────────────────────── Motion: normalize params (workflow) + RIFE 60fps ─────────────────────────
# ALD 05/06/2026 - Luồng WORKFLOW gửi config THÔ (preset/camelCase/aspectRatio/quality) — KHÁC luồng tool
# standalone (server proxy đã expand preset→params). Nếu không normalize ở đây, run_motion rơi hết về default
# (frames=81 → ~9s dù chọn 15s/30s; face/pose/clip/lora/W-H về default). Bảng preset port từ
# motions/shared/utils/motionPresets.js — giữ ĐỒNG BỘ khi sửa 1 trong 2 nguồn.
# ALD 11/06/2026 - 5 preset CHÍNH (khớp dropdown FE motionPresets.js). 480p nhẹ RAM/nhanh
# (an toàn cho box RAM sát ngưỡng). Key cũ giữ lại để node đã lưu KHÔNG vỡ — không còn hiện trên UI.
MOTION_PRESETS = {
    # ALD 19/07/2026 - Preset driver-native mặc định 540p; quality=720p mới nâng cạnh ngắn.
    # Frame/fps vẫn do block ^drv-(N)s$ probe trực tiếp từ driver.
    "drv-5s":  {"steps": 4, "short": 544},
    "drv-10s": {"steps": 4, "short": 544},
    "drv-15s": {"steps": 4, "short": 544},
    "drv-20s": {"steps": 4, "short": 544},
    "drv-30s": {"steps": 4, "short": 544},
    # ── UI presets (FE dropdown) ── ALD 16/06/2026 - đã GỠ 1080p + 60fps khỏi UI.
    # ALD 21/06 - REVERT 05/06: steps 4 (steps 6 chậm +50%). 16fps native (RIFE off). 60fps đã GỠ (RIFE OOM/ảo ảo).
    "2s-720p":   {"frames": 33,  "steps": 4, "short": 544},
    "4s-720p":   {"frames": 65,  "steps": 4, "short": 544},
    "10s-480p":  {"frames": 161, "steps": 4, "short": 480},
    "10s-720p":  {"frames": 161, "steps": 4, "short": 544},
    "15s-720p":  {"frames": 241, "steps": 4, "short": 544},
    # ALD 30/06/2026 - 20s@16fps=321f (>250 frame VRAM-gate → Wan tự offload model xuống RAM: chậm hơn ~40% + nặng RAM).
    "20s-720p":  {"frames": 321, "steps": 4, "short": 544},
    # #region ALD 02/07/2026 - preset fixed-time legacy (tất cả đã chuẩn hóa Fast 4 bước):
    #   • 30fps NATIVE (mật độ frame ×2 → pose DWPose dày → tay/động tác NHANH bám mượt, hết alias 16fps):
    #     10s=301f / 15s=451f / 20s=601f đều >250 → VRAM-gate tự OFFLOAD (model ở RAM, block_swap 30, chậm hơn
    #     ~40% + RAM ~28GB) — đường offload đã chạy thật tới 961f (auto-720p) nên an toàn, chỉ là LÂU
    #     (10s ≈ 14-16', 15s ≈ 20-22', 20s ≈ 28-30'). Vẫn 544 (baseline 27/06; legacy 10s/15s-720p-30fps short
    #     720 bên dưới giữ nguyên cho node cũ). 8s@30fps=241f là mức 30fps duy nhất fit thẳng VRAM (~9') —
    #     đã đổi thành 10s theo yêu cầu 02/07.
    "10s-720p-sharp": {"frames": 161, "steps": 4, "short": 544},
    "10s-30fps": {"frames": 301, "steps": 4, "short": 544, "render_fps": 30},
    "15s-30fps": {"frames": 451, "steps": 4, "short": 544, "render_fps": 30},
    "20s-30fps": {"frames": 601, "steps": 4, "short": 544, "render_fps": 30},
    # #endregion
    # ALD 21/06/2026 - THEO ĐỘ DÀI VIDEO MOTION (basic 720p/16fps): cap 961f (~60s@16fps); run_motion tự RÚT frames về
    # đúng độ dài driver (block "TÍNH frames theo motion thực" → _F_new). Driver >60s thì cắt còn 60s. Timeout theo
    # no-progress (COMFY_HANG 30') + trần COMFY_MAX 2h → clip dài vẫn render xong.
    "auto-720p": {"frames": 961, "steps": 4, "short": 720},
    # ALD 21/06/2026 - 30fps NATIVE (render_fps=30 → Wan render thật 30fps, run_motion BỎ RIFE = KHÔNG "ảo ảo"/mờ).
    # 10s@30fps = 301 frame → chậm ~2× (~14-16'/clip) so 16fps. distill steps 4 (nhanh nhất có thể). opt-in.
    "10s-720p-30fps": {"frames": 301, "steps": 4, "short": 720, "render_fps": 30},
    "15s-720p-30fps": {"frames": 451, "steps": 4, "short": 720, "render_fps": 30},  # 15s@30fps=451f → ~21-24' (~3×)
    # Alias legacy: giữ ID để workflow cũ không vỡ, nhưng backend luôn hạ về Fast 4 bước + LightX2V.
    "8s-720p-max": {"frames": 241, "steps": 4, "short": 720, "render_fps": 30},
    # legacy (back-compat, ẩn khỏi UI). 1080p/30s/60fps gỡ khỏi UI nhưng giữ map để node CŨ đã lưu vẫn chạy.
    "10s-1080p": {"frames": 161, "steps": 4, "short": 1080}, "15s-1080p": {"frames": 241, "steps": 4, "short": 1080},
    # ALD 08/07/2026 - 30s@16fps=481f (>250 → VRAM-gate offload model xuống RAM: chậm hơn + nặng RAM). short 720→544
    # cho đồng bộ 15s/20s-720p (render 540p, enhance nâng sau) — 720p×481f dễ RAM-OOM lúc VAE decode.
    "30s-480p":  {"frames": 481, "steps": 4, "short": 480}, "30s-720p":  {"frames": 481, "steps": 4, "short": 544},
    "5s-480p": {"frames": 81, "steps": 4, "short": 480}, "5s-720p": {"frames": 81, "steps": 4, "short": 720},
    "10s-soft": {"frames": 161, "steps": 4, "short": 720}, "10s-natural": {"frames": 161, "steps": 4, "short": 720},
    "15s-hq": {"frames": 241, "steps": 4, "lora_relight": 0.0, "short": 1080},
    "15s-soft": {"frames": 241, "steps": 4, "short": 720}, "15s-natural": {"frames": 241, "steps": 4, "short": 720},
}
_ASPECT = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9)}
_QUALITY_SHORT = {"480p": 480, "540p": 544, "720p": 720, "1080p": 1080}  # ALD 30/06/2026 - 540p=544 cho Custom motion
def _even16(n): return max(16, int(round(float(n) / 16)) * 16)
def _motion_present(v):
    if v is None:
        return False
    if isinstance(v, str) and v.strip().lower() in ("", "null", "undefined", "nan"):
        return False
    return True

def _motion_float(p, *keys, default=0.0):
    for k in keys:
        v = p.get(k)
        if not _motion_present(v):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return float(default)

def _motion_int(p, *keys, default=0):
    for k in keys:
        v = p.get(k)
        if not _motion_present(v):
            continue
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return int(default)

def _motion_bool(p, *keys, default=False):
    for k in keys:
        v = p.get(k)
        if not _motion_present(v):
            continue
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")
    return bool(default)

def _wan_cover_frames(seconds, fps):
    """Số frame Wan nhỏ nhất theo lưới 4k+1 nhưng vẫn PHỦ ĐỦ thời lượng yêu cầu.

    Không được floor: 15s × 30fps = 450 mẫu phải render 453 frame rồi cắt về đúng 450 frame;
    floor về 449 frame làm MP4 chỉ còn 14.9667s và player hiển thị thành 14s.
    """
    try:
        samples = max(1, int(math.ceil(float(seconds) * float(fps) - 1e-7)))
    except (TypeError, ValueError):
        samples = 1
    return max(17, int(math.ceil((max(17, samples) - 1) / 4.0)) * 4 + 1)

# #region ALD 10/08/2026 - Ngân sách frame của preset drv-Ns, tách khỏi run_motion để test được.
# Đây là thứ làm preset drv-Ns "tự động": preset chỉ là TRẦN THỜI LƯỢNG, còn RAM do ngân sách frame
# lo. Vượt ngân sách thì HẠ FPS, KHÔNG cắt thời lượng — nên user chỉ cần upload ảnh + video là xong.
def _motion_frame_cap():
    """Trần frame cho preset drv-Ns, theo RAM THẬT của box (trần cgroup, không phải RAM host).

    os.sysconf đọc /proc/meminfo mà RunPod cho container thấy của HOST (123 GiB) chứ không phải trần
    thật của container (55,9 GiB) → nhánh 601f luôn trúng dù box không gánh nổi. Job motion 453 frame
    đã bị cgroup OOM-kill hai lần vì đúng nhóm nguyên nhân này (82c9e58). Xem worker_runtime/box_ram.py.
    """
    return int(os.environ.get("MOTION_DRV_MAX_FRAMES", "601" if box_ram_gb() >= 120 else "481"))


def _motion_ram_note():
    """Chuỗi log RAM — nói rõ đang dùng trần cgroup khi nó khác RAM host, để không ai phải đoán."""
    ram_gb = box_ram_gb()
    host_gb = host_ram_bytes() / (1024 ** 3)
    if host_gb - ram_gb > 1:
        return f"RAM box {ram_gb:.0f}GB — trần cgroup, RAM host {host_gb:.0f}GB KHÔNG dùng"
    return f"RAM box {ram_gb:.0f}GB"


def _motion_fit_frame_budget(target_sec, fps, frame_cap):
    """→ (fps, frames, drop|None). Ép số frame vào ngân sách bằng cách HẠ FPS, GIỮ NGUYÊN thời lượng.

    `drop` là None khi vừa ngân sách; ngược lại là {'from_fps', 'from_frames'} để caller log.
    """
    frames = _wan_cover_frames(target_sec, fps)
    if frames <= frame_cap:
        return fps, frames, None
    lowered = max(12, int((frame_cap - 1) // max(1.0, target_sec)))
    return lowered, _wan_cover_frames(target_sec, lowered), {"from_fps": fps, "from_frames": frames}
# #endregion

def _wan_static_final_overlap(latent_frames, context_latents, overlap_latents):
    """Overlap thật giữa 2 context cuối của static_standard (đơn vị latent frame)."""
    total = max(1, int(latent_frames))
    size = max(1, min(int(context_latents), total))
    overlap = max(1, min(int(overlap_latents), size - 1)) if size > 1 else 0
    if total <= size or overlap <= 0:
        return 0
    delta = size - overlap
    starts = []
    for start in range(0, total, delta):
        ending = start + size
        if ending >= total:
            starts.append(total - size)
            break
        starts.append(start)
    if len(starts) < 2:
        return 0
    return max(0, size - (starts[-1] - starts[-2]))

def _wan_auto_context(frames, overlap):
    """Chọn context 61..81f để phần overlap cuối bị shift ngược ngắn nhất có thể."""
    F = max(1, int(frames or 1))
    total_latents = (F - 1) // 4 + 1
    if total_latents <= 21:
        return F
    overlap_latents = max(1, int(overlap) // 4)
    candidates = range(16, 22)  # 61..81 pixel frames; quanh baseline cũ 77f (20 latent).
    context_latents = min(
        (c for c in candidates if c < total_latents),
        key=lambda c: (
            _wan_static_final_overlap(total_latents, c, min(overlap_latents, c - 1)),
            abs(c - 20),
            -c,
        ),
    )
    return (context_latents - 1) * 4 + 1

def _wan_window_plan(p, frames):
    """Chốt một nguồn sự thật cho wiring window của Wan Animate.

    `anchored` chỉ hoạt động khi clip dài hơn context. Embed node nhận toàn bộ số frame để KHÔNG kích hoạt
    loop autoregressive của wrapper; Context Options mới là nơi chia/blend. Default của helper vẫn là legacy
    để các pipeline dùng chung build_wan_workflow không tự đổi hành vi; riêng run_motion opt-in mặc định bên dưới.
    """
    F = max(1, int(frames or 1))
    raw_mode = str(p.get("window_mode", p.get("windowMode", "autoregressive")) or "autoregressive").strip().lower()
    wants_anchored = raw_mode in ("anchored", "anchor", "anchored-context", "context", "context-window")

    # 32 pixel frame = 8 latent bị linear-average qua hơn 1 giây ở 30fps nên tạo vùng nhòe dài. Ngược lại,
    # overlap 4/8 frame cắt cứng và làm lộ frame biên tối ở đúng 6 seam của output f06faa7d. Dùng mặc định chính
    # thức của WanVideoWrapper: 16 pixel frame = 4 latent, đủ ramp để che frame biên nhưng chỉ dài bằng một nửa.
    raw_overlap = _motion_int(p, "context_overlap", "contextOverlap", default=16)
    overlap = max(4, (raw_overlap // 4) * 4)

    explicit_context = _motion_present(p.get("context_frames")) or _motion_present(p.get("contextFrames"))
    if wants_anchored and not explicit_context:
        raw_context = _wan_auto_context(F, overlap)
    else:
        raw_context = max(17, _motion_int(p, "context_frames", "contextFrames", "frame_window_size", default=77))
    # Wan temporal frames phải theo lưới 4k+1.
    context_frames = ((raw_context - 1) // 4) * 4 + 1
    context_frames = min(context_frames, F) if F > 1 else 1
    multi_window = F > context_frames

    anchored = multi_window and wants_anchored

    max_overlap = max(4, context_frames - 4)
    overlap = max(4, min(overlap, max_overlap))
    overlap = max(4, (overlap // 4) * 4)
    stride = max(4, min(100, _motion_int(p, "context_stride", "contextStride", default=4)))

    schedule = str(p.get("context_schedule", p.get("contextSchedule", "static_standard")) or "static_standard").strip().lower()
    if schedule not in ("static_standard", "uniform_standard", "uniform_looped"):
        schedule = "static_standard"
    fuse_method = str(p.get("context_fuse_method", p.get("contextFuseMethod", "linear")) or "linear").strip().lower()
    if fuse_method not in ("linear", "pyramid"):
        fuse_method = "linear"

    latent_frames = (F - 1) // 4 + 1
    context_latents = (context_frames - 1) // 4 + 1
    final_overlap_latents = _wan_static_final_overlap(latent_frames, context_latents, overlap // 4)
    return {
        "mode": "anchored-context" if anchored else "autoregressive",
        "anchored": anchored,
        "multi_window": multi_window,
        "embed_window_frames": F if anchored else context_frames,
        "context_frames": context_frames,
        "overlap": overlap,
        "final_overlap_latents": final_overlap_latents,
        "stride": stride,
        "schedule": schedule,
        "freenoise": _motion_bool(p, "context_freenoise", "contextFreeNoise", default=True),
        "fuse_method": fuse_method,
    }

def _normalize_motion_params(p):
    p = dict(p or {})
    pre = MOTION_PRESETS.get(str(p.get("preset", "")))
    if pre:
        # ALD 16/06/2026 - Preset định nghĩa frame/fps/resolution; sampler được khóa Fast sau block này.
        # 'short' xử lý riêng (resolution); 'lora_relight' để user override.
        for _k, _v in pre.items():
            if _k == "short":
                continue
            if _k == "lora_relight":
                if p.get("loraRelight") is None and p.get("lora_relight") is None:
                    p["lora_relight"] = _v
                continue
            p[_k] = _v
    for _dead in ("bgAnchor", "bg_anchor", "bgAnchorMaskExpand", "bg_anchor_mask_expand", "bgAnchorMaskBlur", "bg_anchor_mask_blur"):
        p.pop(_dead, None)
    for cam, snake in {"faceStrength": "face_strength", "poseStrength": "pose_strength",
                       "clipStrength": "clip_strength", "loraRelight": "lora_relight",
                       "skipFirstFrames": "skip_first_frames", "blockSwap": "block_swap",
                       "faceSource": "face_source", "bodyProportionLock": "body_proportion_lock",
                       "windowMode": "window_mode",
                       "contextFrames": "context_frames", "contextOverlap": "context_overlap",
                       "contextStride": "context_stride", "contextSchedule": "context_schedule",
                       "contextFreeNoise": "context_freenoise", "contextFuseMethod": "context_fuse_method"}.items():
        if _motion_present(p.get(cam)) and not _motion_present(p.get(snake)):
            p[snake] = p[cam]
    # #region ALD 16/07/2026 - Khóa vóc dáng theo ảnh Ref cho Motion Transfer.
    # Workflow cũ chưa có field vẫn tự lành. User có thể tắt khóa để A/B; khi bật, các giá trị cũ 0.8/1.2
    # được chặn về 0.7/1.35 để pose chỉ dẫn chuyển động và CLIP giữ hình thể mẫu mạnh hơn.
    _body_lock = _motion_bool(p, "body_proportion_lock", "bodyProportionLock", default=True)
    p["body_proportion_lock"] = _body_lock
    p["bodyProportionLock"] = _body_lock
    if _body_lock:
        _pose = min(_motion_float(p, "pose_strength", "poseStrength", default=0.7), 0.7)
        _clip = max(_motion_float(p, "clip_strength", "clipStrength", default=1.35), 1.35)
        p["pose_strength"] = p["poseStrength"] = _pose
        p["clip_strength"] = p["clipStrength"] = _clip
    # #endregion
    # ALD 13/07/2026 - Quay về phương pháp Motion ban đầu theo chốt của user: AnimateEmbeds tự chạy từng
    # window 77f, KHÔNG WanVideoContextOptions, KHÔNG overlap/blend. Ép cứng để payload/node cũ còn lưu
    # anchored-context hoặc MOTION_WINDOW_MODE trên box cũng không bật lại đường gây nhòe/flash ở seam.
    p["window_mode"] = "autoregressive"
    p["windowMode"] = "autoregressive"
    # ALD 14/07/2026 - 81 frame = 1 ref + 80 frame mới/window. Các duration chuẩn 5/10/15/20/30s
    # ở 16fps có F=81/161/241/321/481 nên chia đều tuyệt đối, không còn tail window 4–12
    # frame (job 241f + window77 từng tạo window cuối 12f, đúng đoạn 14.25–15s bị đậm mặt).
    p["frame_window_size"] = 81
    # ALD 13/07/2026 - Natural/HQ đã gỡ khỏi backend. Chuẩn hóa cứng sau preset để cả preset/request
    # legacy 20 bước cũng chạy đúng baseline Fast và metadata job phản ánh cấu hình thật.
    # #region ALD 21/07/2026 - MỞ LẠI cửa NATURAL 20-step (user yêu cầu A/B trị "chu mỏ"): distill 4-step bám
    # conditioning mặt yếu → miệng rơi về prior. Opt-in: param renderProfile=natural/max HOẶC env
    # MOTION_FORCE_QUALITY=1 (A/B cả box không cần FE). Bộ số phục hồi NGUYÊN VĂN thời kỳ trước 34fb1e7
    # (13/07, không chu mỏ): steps≥20 · cfg 1.0 · unipc · shift 5.0 · lightx2v 0.0. Mặc định vẫn FAST.
    # ALD 10/08/2026 - CHỈ nhận tín hiệu CHỦ Ý. Trước đây list này gồm cả alias đã khai tử
    # ("natural"/"quality"/"official"/"hq") — đúng những chuỗi mà build_wan_workflow `_retired_natural`
    # (dưới đây, 13/07) vẫn đang hạ về Fast, và mà cả 6 layer FE/API scrub sạch trước khi tới worker.
    # Cùng một chuỗi mang hai nghĩa ngược nhau trong cùng file: workflow CŨ đã lưu renderProfile=natural
    # tự bật lại đường non-distill 20 bước đã gỡ 13/07, trái hẳn ý đồ "không thể kích hoạt lại" ghi ở
    # handlers.js:789 và routes/jobs.js:36. A/B vẫn nguyên: env MOTION_FORCE_QUALITY, hoặc gõ tay "max".
    _profile_req = str(p.get("renderProfile", p.get("render_profile", ""))).strip().lower()
    _want_natural = (_profile_req in ("max", "max20")
                     or str(os.environ.get("MOTION_FORCE_QUALITY", "")).strip().lower() in ("1", "true", "yes", "on"))
    if _want_natural:
        # Tên profile "max20" CHỦ Ý khác list alias legacy ("natural"/"quality"/...) — build_wan_workflow hạ cấp
        # các alias cũ về Fast (bảo vệ SS/trend legacy), còn "max20" rơi vào nhánh else đọc đúng steps/cfg/lora từ params.
        p["render_profile"] = "max20"
        p["renderProfile"] = "max20"
        p["hq"] = False
        p["steps"] = max(20, int(p.get("hq_steps", 20) or 20))
        p["cfg"] = 1.0
        p["scheduler"] = "unipc"
        p["lora_lightx2v"] = 0.0
        p["loraLightx2v"] = 0.0
    else:
        # #endregion
        # #region ALD 17/08/2026 - Knob env cho baseline Fast (A/B "bộ số Kijai" trị biểu cảm mặt hai cực):
        # example chính thức wanvideo_WanAnimate_example_01.json chạy 6 bước + distill rank64 @ 1.2 (repo: 4 bước
        # + rank32 @ 1.0). API scrub steps/loraLightx2v của payload nên đường A/B duy nhất là env worker — giống
        # MOTION_FORCE_QUALITY. Default giữ NGUYÊN baseline cũ; chỉ phiên A/B mới set env.
        p["render_profile"] = "fast"
        p["renderProfile"] = "fast"
        p["hq"] = False
        p.pop("hq_steps", None)
        p["steps"] = int(os.environ.get("MOTION_FAST_STEPS", "4") or "4")
        p["cfg"] = 1.0
        p["scheduler"] = "dpm++_sde"
        _lx2v_s = float(os.environ.get("MOTION_LX2V_STRENGTH", "1.0") or "1.0")
        p["lora_lightx2v"] = _lx2v_s
        p["loraLightx2v"] = _lx2v_s
        # #endregion
    # ALD 27/06/2026 - RAW COLOR DEFAULT: node cũ còn lưu bộ chỉnh màu thì tự lành về tắt, tránh flash/đổi màu.
    _legacy_face = str(p.get("face_source", p.get("faceSource", ""))).lower().strip()
    _legacy_method = str(p.get("match_ref_method", p.get("matchRefMethod", ""))).lower().strip()
    try:
        _legacy_strength = float(p.get("match_ref_strength", p.get("matchRefStrength", 0.85)))
    except (TypeError, ValueError):
        _legacy_strength = 0.85
    try:
        _legacy_bcap = float(p.get("brightCap", p.get("bright_cap", 0.88)))
    except (TypeError, ValueError):
        _legacy_bcap = 0.88
    _looks_legacy_motion_default = (
        _legacy_face in ("", "driver") and
        _legacy_method in ("", "mkl") and
        abs(_legacy_strength - 0.85) < 0.001 and
        abs(_legacy_bcap - 0.88) < 0.001
    )
    if _looks_legacy_motion_default:
        # ALD 09/07/2026 - user chốt MẶC ĐỊNH mặt theo DRIVER (bám biểu cảm/lipsync tốt hơn; 'ref' = opt-in tường minh
        # qua tile "Theo ảnh gốc"). Block heal chỉ còn lo TẮT màu — KHÔNG ép về ref nữa (ép ref làm chọn driver vô tác dụng).
        p["face_source"] = "driver"
        p["faceSource"] = "driver"
        p["match_ref"] = False
        p["matchRef"] = False
        p["warmth"] = 0
        p["brightCap"] = 1.0
        p["motionRefGrade"] = False
        p["motionDeflicker"] = False
    # ALD 27/06/2026 - RAW COLOR OVERRIDE: tránh node cũ/payload cũ bật lại bất kỳ chỉnh màu/sáng nào gây flash.
    # Muốn thử lại các filter màu phải set env MOTION_ENABLE_COLOR_ADJUST=1.
    if str(os.environ.get("MOTION_ENABLE_COLOR_ADJUST", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        p["match_ref"] = False
        p["matchRef"] = False
        p["colormatch"] = "disabled"
        p["driverGray"] = False
        p["driver_gray"] = False
        p["warmth"] = 0
        p["brightCap"] = 1.0
        p["motionRefGrade"] = False
        p["motion_ref_grade"] = False
        p["motionDeflicker"] = False
        p["motion_deflicker"] = False
        p["sharpen"] = 0
        p["contrast"] = 1.0
    # ALD 19/07/2026 - drv-* là preset Motion chính thức: resolution do quality + aspectRatio quyết định,
    # không nhận width/height cũ đã lưu trong node và không co theo tỷ lệ thật của driver.
    if str(p.get("preset") or "").startswith("drv-"):
        p.pop("width", None)
        p.pop("height", None)
        p["fitDriver"] = False
        p["fit_driver"] = False
        p["quality"] = "720p" if str(p.get("quality") or "").strip().lower() == "720p" else "540p"
        p["resolutionPolicy"] = "quality-v1"
        p["resolution_policy"] = "quality-v1"
    if not p.get("width") or not p.get("height"):
        rw, rh = _ASPECT.get(str(p.get("aspectRatio", "9:16")), (9, 16))
        # ALD 19/07/2026 - PRESET giữ baseline 540p; quality=720p là override có chủ đích từ Node/Task Cloud.
        ql = p.get("quality")
        q_short = _QUALITY_SHORT.get(str(ql), 0) if ql else 0
        short = max(int(pre.get("short", 544)), q_short) if pre else (q_short or 544)
        if rw <= rh: w, h = short, short * rh / rw
        else:        h, w = short, short * rw / rh
        p["width"], p["height"] = _even16(w), _even16(h)
    # ALD 25/06/2026 - Preview mode: FE gửi previewFrames để render nhanh (kiểm tra màu/sáng/pose mà không chạy
    # full workflow). Cap 17–49 frame (~1–3s tại 16fps). steps giữ nguyên (4 distill = nhanh đủ xem chất lượng).
    _pf = int(p.get("previewFrames") or 0)
    if _pf > 0:
        p["frames"] = max(17, min(_pf, 49))
    return p
def build_rife60_workflow(video_name, prefix="motion-60fps", multiplier=4, skip_first=0, frame_cap=0, with_audio=True, dtype="float16"):
    """Pass RIÊNG nội suy mp4 đã render → RIFE ×multiplier (16→16*mult fps, giữ đúng thời lượng) + giữ audio.
    multiplier=4 → 64fps (đích 60) · 3 → 48fps · 2 → 32fps (đích 30, nhẹ hơn ~nửa compute).
    ALD 23/06/2026 - skip_first/frame_cap: nội suy TỪNG ĐOẠN (VHS_LoadVideo skip/cap) cho enhance → mỗi lúc chỉ
    1 đoạn trong RAM → RAM PHẲNG bất kể video dài/2K (trước đây 1 pass gom CẢ video → 99% RAM). dtype float16 =
    NỬA RAM/frame so float32 (RIFE fp16 là chuẩn). with_audio=False = đoạn câm (worker ghép + mux audio gốc sau).
    clear_cache 10→4: xả VRAM cache dày hơn."""
    m = int(multiplier)
    return {
        "10": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": video_name, "force_rate": 16, "custom_width": 0, "custom_height": 0,
            "frame_load_cap": int(frame_cap), "skip_first_frames": int(skip_first), "select_every_nth": 1, "format": "AnimateDiff"}},
        "20": {"class_type": "RIFE VFI", "inputs": {
            "frames": ["10", 0], "ckpt_name": "rife47.pth", "clear_cache_after_n_frames": 4,
            "multiplier": m, "fast_mode": True, "ensemble": True, "scale_factor": 1.0,
            "dtype": str(dtype), "torch_compile": False, "batch_size": 1}},
        "30": {"class_type": "VHS_VideoCombine", "inputs": {**{
            "images": ["20", 0], "frame_rate": 16 * m, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 16,  # ALD 23/06 - crf 19→16 (enhance chất lượng cao)
            "pingpong": False, "save_output": True},
            **({"audio": ["10", 2]} if with_audio else {})}},
    }

# ───────────────────────── Wan 2.1 I2V (ảnh → video chuyển động, teaser) ─────────────────────────
# Image-to-video: 1 ảnh tĩnh → clip chuyển động THẬT (không cần video dẫn động như Animate).
# Tái dùng VAE + umt5 + clip_vision + LoRA lightx2v 4-step distill (đã có sẵn trên host ComfyUI).
WAN_I2V_MODEL = os.environ.get("WAN_I2V_MODEL", "Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors")
def build_wan_i2v_workflow(ref_name, p, frames, prefix="teaser-i2v", prompt=""):
    W = int(p.get("i2v_width", 480)); H = int(p.get("i2v_height", 832))   # 480p dọc 9:16, khớp LoRA 480p
    F = int(frames); S = int(p.get("i2v_steps", 4))
    pos = ((prompt or "").strip() or p.get("i2v_prompt") or "dynamic energetic camera movement, quick push-in, "
           "lively product reveal, strong parallax, vivid motion, punchy advertising, high detail, photorealistic")
    neg = p.get("negative_prompt", "色调艳丽，过曝，静态，细节模糊不清，最差质量，低质量，畸形，多余的手指，画面静止不动，static, no motion, frozen frame")
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "11": {"class_type": "ImageResizeKJv2", "inputs": {
            "image": ["10", 0], "width": W, "height": H, "upscale_method": "lanczos",
            "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center",
            "divisible_by": 16, "device": "cpu"}},
        "40": {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": "lightx2v_I2V_14B_480p_cfg_step_distill_rank32_bf16.safetensors", "strength_0": float(p.get("i2v_lora", 1.0)),
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0,
            "lora_3": "none", "strength_3": 1.0, "lora_4": "none", "strength_4": 1.0,
            "low_mem_load": False, "merge_loras": True}},
        "41": {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": int(p.get("i2v_block_swap", 20)), "offload_img_emb": False, "offload_txt_emb": False,
            "use_non_blocking": True, "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}},
        "42": {"class_type": "WanVideoModelLoader", "inputs": {
            "model": WAN_I2V_MODEL, "base_precision": "fp16_fast", "quantization": "disabled",
            "load_device": "offload_device", "attention_mode": _wan_attention(p),
            "lora": ["40", 0], "block_swap_args": ["41", 0]}},
        "50": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": "Wan2_1_VAE_bf16.safetensors", "precision": "bf16"}},
        "60": {"class_type": "WanVideoTextEncodeCached", "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16",
            "positive_prompt": pos, "negative_prompt": neg,
            "quantization": "disabled", "use_disk_cache": MOTION_T5_DISK_CACHE, "device": "gpu"}},
        "70": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "71": {"class_type": "WanVideoClipVisionEncode", "inputs": {
            "clip_vision": ["70", 0], "image_1": ["11", 0], "strength_1": 1.0, "strength_2": 1.0,
            "crop": "center", "combine_embeds": "average", "force_offload": True, "tiles": 0, "ratio": 0.5}},
        "80": {"class_type": "WanVideoImageToVideoEncode", "inputs": {
            "width": W, "height": H, "num_frames": F, "noise_aug_strength": float(p.get("i2v_noise_aug", 0.03)),
            "start_latent_strength": 1.0, "end_latent_strength": 1.0, "force_offload": True,
            "vae": ["50", 0], "clip_embeds": ["71", 0], "start_image": ["11", 0]}},
        "90": {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": S, "cfg": 1.0, "shift": float(p.get("i2v_shift", 5.0)),
            "seed": int(p.get("i2v_seed", 42)), "force_offload": True, "scheduler": "dpm++_sde",
            "riflex_freq_index": 0, "text_embeds": ["60", 0], "rope_function": "comfy"}},
        "100": {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["90", 0], "enable_vae_tiling": True,
            "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default"}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["100", 0], "frame_rate": 16, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }

# #region ALD 14/06/2026 - Wan 2.x TEXT→VIDEO (chỉ PROMPT, KHÔNG cần ảnh) — node "Text → Video".
# Graph CHÍNH XÁC lấy từ example WanVideoWrapper trên box prod:
#   ~/ai/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/example_workflows/wanvideo_2_1_14B_T2V_example_03.json
#   (đã verify node class_type qua /object_info :8188 ngày 14/06).
# Khác build_wan_i2v_workflow ở chỗ BỎ nhánh ảnh: BỎ LoadImage(10)/ImageResizeKJv2(11), BỎ CLIPVisionLoader(70)/
# WanVideoClipVisionEncode(71), THAY WanVideoImageToVideoEncode(80) bằng WanVideoEmptyEmbeds (latent rỗng từ
# W/H/num_frames) → đây CHÍNH là đường text-only (image_embeds của Sampler nhận embeds rỗng, conditioning chỉ từ
# text_embeds). Dùng lại WanVideoModelLoader/VAELoader/TextEncodeCached/Sampler/Decode/VHS y như I2V để nhất quán.
#
# Model: Wan2.1-T2V-14B (1 file fp8, KHỚP đúng single-loader của example đã verify) — fallback tương thích cũ.
# #region ALD 03/07/2026 - Wan2.2 T2V/I2V A14B: dual-model MoE (HIGH+LOW) + LoRA distill 4-step của
# lightx2v/Wan2.2-Distill-Loras (user đã test trên HF Inference Providers/WaveSpeed, kết quả rất ưng ý).
# denoising_step_list khuyến nghị [1000,750,500,250] = 4 bước, chia đôi HIGH (2 bước đầu) → LOW (2 bước cuối),
# cfg 1.0, shift 5.0, LoRA strength 1.0. Nhanh ~5x so với non-distill 20 bước cfg 6 (đường cũ) và đỡ cháy sáng
# (cfg cao là 1 nguồn over-exposure). Tên file model KHỚP repo Kijai/WanVideo_comfy_fp8_scaled (chú ý T2V HIGH
# dùng '_HIGH' gạch dưới, LOW dùng '-LOW' gạch ngang — repo Kijai đặt tên KHÔNG nhất quán, đừng "sửa" lại).
WAN_T2V_MODEL         = os.environ.get("WAN_T2V_MODEL", "Wan2_1-T2V-14B_fp8_e4m3fn_scaled_KJ.safetensors")   # wan2.1 fallback (single-loader, KHỚP example đã verify)
WAN22_T2V_MODEL_HIGH  = os.environ.get("WAN22_T2V_MODEL_HIGH", "Wan2_2-T2V-A14B_HIGH_fp8_e4m3fn_scaled_KJ.safetensors")
WAN22_T2V_MODEL_LOW   = os.environ.get("WAN22_T2V_MODEL_LOW",  "Wan2_2-T2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors")
WAN22_T2V_LORA_HIGH   = os.environ.get("WAN22_T2V_LORA_HIGH", "wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors")
WAN22_T2V_LORA_LOW    = os.environ.get("WAN22_T2V_LORA_LOW",  "wan2.2_t2v_A14b_low_noise_lora_rank64_lightx2v_4step_1217.safetensors")
WAN22_I2V_LORA_HIGH   = os.environ.get("WAN22_I2V_LORA_HIGH", "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors")
WAN22_I2V_LORA_LOW    = os.environ.get("WAN22_I2V_LORA_LOW",  "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors")
WAN22_LORA_STRENGTH   = float(os.environ.get("WAN22_LORA_STRENGTH", "1.0"))   # strength LoRA distill (khuyến nghị 1.0)
WAN22_T2V_STEPS       = int(os.environ.get("WAN22_T2V_STEPS", "4"))     # distill 4 bước (2 HIGH + 2 LOW)
WAN22_T2V_CFG         = float(os.environ.get("WAN22_T2V_CFG", "1.0"))   # distill BẮT BUỘC cfg 1.0 (cfg cao → cháy sáng)
# #endregion
WAN_T2V_FPS         = int(os.environ.get("WAN_T2V_FPS", "16"))          # frame_rate xuất VHS (khớp example Wan T2V)
WAN_T2V_STEPS       = int(os.environ.get("WAN_T2V_STEPS", "20"))        # non-distill 14B T2V ~20 bước (đường wan2.1 cũ)
WAN_T2V_CFG         = float(os.environ.get("WAN_T2V_CFG", "6.0"))       # cfg classifier-free guidance cho non-distill T2V
WAN_T2V_MAX_FRAMES  = int(os.environ.get("WAN_T2V_MAX_FRAMES", "241"))  # ALD 03/07/2026 - 201→241: trần 15s @16fps (distill 4 bước nhẹ VRAM hơn nhiều so với 20 bước cũ)
WAN_T2V_TIMEOUT     = int(os.environ.get("WAN_T2V_TIMEOUT_SEC", "1800"))  # 30' — trần an toàn khi GPU bận (distill thường xong <5')

# Bảng model dropdown FE (id ở config node) → mô tả engine. id KHỚP InspectorTextToVideo.vue; run_ss check membership.
T2V_MODELS = {
    "wan2.2": "dual-distill",     # Wan2.2 T2V-A14B MoE HIGH+LOW + LoRA distill 4-step (MẶC ĐỊNH MỚI 03/07/2026)
    "wan2.1": WAN_T2V_MODEL,      # Wan2.1 T2V-14B non-distill (fallback, KHỚP example đã verify)
    # "ltx" xử lý riêng trong run_text2video qua build_ltx_i2v_workflow (CHỈ khi có template I2V) — xem ghi chú.
}

def wan_t2v_frames(dur):
    """Số frame Wan T2V (≡ 1 mod 4 — VAE temporal stride 4) theo thời lượng + WAN_T2V_FPS."""
    F = max(17, min(WAN_T2V_MAX_FRAMES, int(round(float(dur) * WAN_T2V_FPS))))
    return F - ((F - 1) % 4)   # 4k+1: 17, 21, …, 81 (~5s), … hợp lệ cho WanVideoEmptyEmbeds

def _riflex_index(F):
    """ALD 03/07/2026 - RIFLEx cho clip DÀI: cửa sổ native của Wan là 81f (~5s @16fps); vượt 81f mà
    riflex_freq_index=0 thì RoPE tần số thấp lặp chu kỳ → video SLOW-MOTION/trôi lặp (bài học 28/06, commit
    1be09de — bị mất khi revert v8 03/07). >81f bật index 4 (khuyến nghị RIFLEx cho Wan) — 5s giữ nguyên 0."""
    return 0 if int(F) <= 81 else int(os.environ.get("WAN_RIFLEX_INDEX", "4"))

def _teen_wan_frames(dur, fps):
    """Frame count riêng Teen Flycam demo-driver.
    Dùng native fps cao hơn 16 để camera đỡ giật; vẫn giữ 4k+1 cho VAE temporal stride."""
    max_frames = int(os.environ.get("TEEN_FLYCAM_MAX_FRAMES", "289") or 289)
    F = max(17, min(max_frames, int(round(float(dur) * int(fps))) + 1))
    return F - ((F - 1) % 4)

def build_wan_t2v_workflow(prompt, prefix, width, height, frames, negative_prompt=None, model=None, steps=None, cfg=None,
                           wan_ver="wan2.1", params=None):
    """Graph Wan TEXT→VIDEO (text-only, KHÔNG ảnh) — xem #region ở trên. image_embeds của Sampler = WanVideoEmptyEmbeds.
    ALD 03/07/2026 - wan_ver='wan2.2': dual-model MoE HIGH→LOW + LoRA distill 4-step (lightx2v), cfg 1.0 —
    nhanh ~5x và đỡ CHÁY SÁNG hơn hẳn đường non-distill cfg 6 cũ. wan2.1 giữ nguyên single-loader (fallback).
    Clip >81f (>~5s) tự bật RIFLEx (chống slow-motion/trôi lặp) qua _riflex_index."""
    W = int(width); H = int(height); F = int(frames)
    is22 = (str(wan_ver).lower().strip() == "wan2.2")
    S = int(steps if steps is not None else (WAN22_T2V_STEPS if is22 else WAN_T2V_STEPS))
    C = float(cfg if cfg is not None else (WAN22_T2V_CFG if is22 else WAN_T2V_CFG))
    pos = (prompt or "").strip() or "cinematic establishing shot, smooth camera motion, vivid lighting, high detail, photorealistic"
    neg = negative_prompt or "色调艳丽，过曝，静态，细节模糊不清，最差质量，低质量，畸形，多余的手指，画面静止不动，static, no motion, frozen frame, blurry, lowres, watermark, text"
    g = {
        "41": {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": int(os.environ.get("WAN_T2V_BLOCK_SWAP", "20")), "offload_img_emb": False, "offload_txt_emb": False,
            "use_non_blocking": True, "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}},
        "50": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": "Wan2_1_VAE_bf16.safetensors", "precision": "bf16"}},
        "60": {"class_type": "WanVideoTextEncodeCached", "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16",
            "positive_prompt": pos, "negative_prompt": neg,
            "quantization": "disabled", "use_disk_cache": MOTION_T5_DISK_CACHE, "device": "gpu"}},
        # WanVideoEmptyEmbeds = đường TEXT-ONLY: latent rỗng theo W/H/F (KHÔNG có start_image/clip_vision).
        "80": {"class_type": "WanVideoEmptyEmbeds", "inputs": {"width": W, "height": H, "num_frames": F}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["100", 0], "frame_rate": WAN_T2V_FPS, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }
    seed = int(abs(hash(prefix)) % (2 ** 31))
    shift = float(os.environ.get("WAN_T2V_SHIFT", "5.0"))
    rfx = _riflex_index(F)
    if is22:
        # Dual-model MoE: HIGH lo nửa đầu denoise (bố cục/chuyển động), LOW lo nửa cuối (chi tiết).
        # Mỗi expert đeo ĐÚNG LoRA distill của nó (high_noise/low_noise KHÔNG hoán đổi được).
        S_high = max(int(S) // 2, 1)
        g["40"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": WAN22_T2V_LORA_HIGH, "strength_0": WAN22_LORA_STRENGTH,
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0,
            "lora_3": "none", "strength_3": 1.0, "lora_4": "none", "strength_4": 1.0,
            "low_mem_load": False, "merge_loras": True}}
        g["44"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": WAN22_T2V_LORA_LOW, "strength_0": WAN22_LORA_STRENGTH,
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0,
            "lora_3": "none", "strength_3": 1.0, "lora_4": "none", "strength_4": 1.0,
            "low_mem_load": False, "merge_loras": True}}
        g["42"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": WAN22_T2V_MODEL_HIGH, "base_precision": "fp16_fast", "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params),
            "lora": ["40", 0], "block_swap_args": ["41", 0]}}
        g["43"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": WAN22_T2V_MODEL_LOW, "base_precision": "fp16_fast", "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params),
            "lora": ["44", 0], "block_swap_args": ["41", 0]}}
        g["90"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": S, "cfg": C, "shift": shift,
            "seed": seed, "force_offload": True, "scheduler": "dpm++_sde",
            "riflex_freq_index": rfx, "text_embeds": ["60", 0], "rope_function": "comfy",
            "start_step": 0, "end_step": S_high}}
        g["91"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["43", 0], "image_embeds": ["80", 0], "steps": S, "cfg": C, "shift": shift,
            "seed": seed, "force_offload": True, "scheduler": "dpm++_sde",
            "riflex_freq_index": rfx, "text_embeds": ["60", 0], "rope_function": "comfy",
            "samples": ["90", 0], "start_step": S_high, "end_step": -1}}
        g["100"] = {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["91", 0], "enable_vae_tiling": True,
            "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default"}}
    else:
        g["40"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": "none", "strength_0": 1.0, "lora_1": "none", "strength_1": 1.0,
            "lora_2": "none", "strength_2": 1.0, "lora_3": "none", "strength_3": 1.0,
            "lora_4": "none", "strength_4": 1.0, "low_mem_load": False, "merge_loras": True}}
        g["42"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": model or WAN_T2V_MODEL, "base_precision": "fp16_fast", "quantization": "disabled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params),
            "lora": ["40", 0], "block_swap_args": ["41", 0]}}
        g["90"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": S, "cfg": C, "shift": shift,
            "seed": seed, "force_offload": True, "scheduler": "dpm++_sde",
            "riflex_freq_index": rfx, "text_embeds": ["60", 0], "rope_function": "comfy"}}
        g["100"] = {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["90", 0], "enable_vae_tiling": True,
            "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default"}}
    return g

# Kích thước Wan T2V theo aspectRatio (chia hết 16). 480p-class (nhẹ VRAM 5090 chia sẻ); chọn 16:9/9:16/1:1.
_T2V_DIMS = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (624, 624), "4:3": (768, 576), "3:4": (576, 768)}
def _wan_t2v_dims(params):
    ar = str(params.get("aspectRatio") or params.get("aspect_ratio") or "16:9")
    W, H = _T2V_DIMS.get(ar, _T2V_DIMS["16:9"])
    return int(params.get("width", W)), int(params.get("height", H))
# #endregion


# ───────────────────────── MultiTalk/InfiniteTalk — ảnh + audio → video NÓI + nhép miệng ─────────────────────────
# ALD 03/06/2026 - Lip-sync audio-driven qua ComfyUI-WanVideoWrapper (node MultiTalk có sẵn). Graph theo
# example InfiniteTalk đã verify: ảnh nhân vật + audio (TTS giọng nhân vật) → video nhân vật nói, miệng
# khớp khẩu hình + giọng mux sẵn. Dùng Wan2_1-I2V-14B (đã có) + InfiniteTalk Single fp8 + wav2vec2-chinese.
MULTITALK_MODEL = os.environ.get("MULTITALK_MODEL", "Wan2_1-InfiniteTalk-Single_fp8_e4m3fn_scaled_KJ.safetensors")
WAV2VEC_MODEL   = os.environ.get("WAV2VEC_MODEL", "wav2vec2-chinese-base_fp16.safetensors")
TALK_FPS        = int(os.environ.get("TALK_FPS", "25"))
TALK_NEG = ("bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, "
            "static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, "
            "extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, "
            "fused fingers, still picture, messy background, three legs, many people in the background, walking backwards")

def build_wan_multitalk_workflow(ref_name, audio_name, frames, p, prompt="", prefix="talk", fps=TALK_FPS):
    W = int(p.get("talk_width", 480)); H = int(p.get("talk_height", 832))   # 9:16 dọc cho nhân vật nói
    S = int(p.get("talk_steps", 6))
    pos = (prompt or "").strip() or "a person talking naturally to the camera, subtle head movement and facial expression, lip sync"
    neg = p.get("negative_prompt", TALK_NEG)
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "11": {"class_type": "ImageResizeKJv2", "inputs": {
            "image": ["10", 0], "width": W, "height": H, "upscale_method": "lanczos",
            "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center",
            "divisible_by": 16, "device": "cpu"}},
        "20": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "30": {"class_type": "Wav2VecModelLoader", "inputs": {
            "model": WAV2VEC_MODEL, "base_precision": "fp16", "load_device": "main_device"}},
        "31": {"class_type": "MultiTalkWav2VecEmbeds", "inputs": {
            "wav2vec_model": ["30", 0], "audio_1": ["20", 0], "normalize_loudness": True,
            "num_frames": int(frames), "fps": float(fps), "audio_scale": 1.0, "audio_cfg_scale": 1.0,
            "multi_audio_type": "para"}},
        "40": {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": "lightx2v_I2V_14B_480p_cfg_step_distill_rank32_bf16.safetensors", "strength_0": float(p.get("talk_lora", 1.0)),
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0,
            "lora_3": "none", "strength_3": 1.0, "lora_4": "none", "strength_4": 1.0,
            "low_mem_load": False, "merge_loras": True}},
        "41": {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": int(p.get("talk_block_swap", 20)), "offload_img_emb": False, "offload_txt_emb": False,
            "use_non_blocking": True, "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}},
        "45": {"class_type": "MultiTalkModelLoader", "inputs": {"model": MULTITALK_MODEL}},
        "42": {"class_type": "WanVideoModelLoader", "inputs": {
            "model": WAN_I2V_MODEL, "base_precision": "fp16_fast", "quantization": "disabled",
            # main_device: nạp THẲNG GPU (box 25.165 còn ~26GB trống; Ollama ở box 43.30 riêng). KHÔNG dùng
            # offload_device — nó đẩy 14B+InfiniteTalk lên CPU RAM → cạn RAM → ComfyUI treo. block_swap lo phần fit.
            "load_device": "main_device", "attention_mode": _wan_attention(p),
            "lora": ["40", 0], "block_swap_args": ["41", 0], "multitalk_model": ["45", 0]}},
        "50": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": "Wan2_1_VAE_bf16.safetensors", "precision": "bf16"}},
        "60": {"class_type": "WanVideoTextEncodeCached", "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16",
            "positive_prompt": pos, "negative_prompt": neg,
            "quantization": "disabled", "use_disk_cache": MOTION_T5_DISK_CACHE, "device": "gpu"}},
        "70": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "71": {"class_type": "WanVideoClipVisionEncode", "inputs": {
            "clip_vision": ["70", 0], "image_1": ["11", 0], "strength_1": 1.0, "strength_2": 1.0,
            "crop": "center", "combine_embeds": "average", "force_offload": True, "tiles": 0, "ratio": 0.5}},
        "80": {"class_type": "WanVideoImageToVideoMultiTalk", "inputs": {
            "vae": ["50", 0], "width": W, "height": H, "frame_window_size": int(p.get("talk_window", 81)),
            "motion_frame": int(p.get("talk_motion_frame", 9)), "force_offload": True, "colormatch": "disabled",
            "start_image": ["11", 0], "tiled_vae": True, "clip_embeds": ["71", 0], "mode": "infinitetalk", "output_path": ""}},
        "90": {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": S, "cfg": 1.0, "shift": float(p.get("talk_shift", 11.0)),
            "seed": int(p.get("talk_seed", 42)), "force_offload": True, "scheduler": "dpm++_sde",
            "riflex_freq_index": 0, "text_embeds": ["60", 0], "rope_function": "comfy", "multitalk_embeds": ["31", 0]}},
        "95": {"class_type": "WanVideoPassImagesFromSamples", "inputs": {"samples": ["90", 0]}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["95", 0], "audio": ["20", 0], "frame_rate": int(fps), "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }

def _audio_dur(path):
    """Thời lượng audio (giây) qua ffprobe — None nếu lỗi."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip())
    except Exception:
        return None

def _comfy_prefixed_output(prefix, exts=(".mp4",)):
    """Tìm output deterministic theo filename_prefix. MP4 ưu tiên bản có audio nếu có."""
    exts = tuple((e or "").lower() for e in (exts or ()))
    is_video = any(e in (".mp4", ".mov", ".mkv", ".webm") for e in exts)
    cand_names = []
    if is_video:
        cand_names.extend([
            f"{prefix}_00001-audio.mp4",
            f"{prefix}_00001.mp4",
            f"{prefix}.mp4",
        ])
    for ext in exts:
        if ext in (".mp4", ".mov", ".mkv", ".webm"):
            continue
        cand_names.extend([f"{prefix}_00001{ext}", f"{prefix}{ext}"])
    for name in cand_names:
        cand = comfy_view_file(name)
        if not cand:
            continue
        if is_video:
            if _mp4_has_moov(cand):
                return cand
        elif os.path.getsize(cand) > 0:
            return cand
    return None

# ───────────────────────── LTX-2.3 (Lightricks) — ảnh + prompt → video + audio ─────────────────────────
# ALD 02/06/2026 - Engine GENERATIVE cho Teaser (motionMode='ltx'): 1 ảnh + prompt → CẢNH QUAY có
# chuyển động + audio đồng bộ (KHÁC Wan Animate vốn pose-driven, cần video dẫn động).
#
# 🚨 VRAM: checkpoint chính thức Lightricks/LTX-2.3 (dev/distilled) là bf16 ~46GB > 32GB RTX 5090 → KHÔNG
#    chạy thẳng. Cần bản FP8/GGUF cộng đồng (~24GB, điền URL vào models.txt) HOẶC block-swap/offload (chậm).
#    Text-encoder = Gemma 3 (không phải umt5). Xem models.txt.
# ⚠️ Tên node ComfyUI LTXVideo không public sẵn → worker KHÔNG tự suy ra/đoán graph.
#    → Worker KHÔNG hardcode/đoán graph. Nó dựng workflow từ TEMPLATE ĐÃ-VERIFY do bạn cung cấp:
#       1. params.ltxWorkflow  (dict — đặt trong config node Teaser), HOẶC
#       2. env LTX_WORKFLOW_JSON = đường dẫn file .json | chuỗi JSON
#          → xuất "Save (API Format)" từ ComfyUI của workflow LTX-2.3 I2V CHÍNH THỨC.
#    Trong template, thay các giá-trị-động bằng TOKEN (worker tự điền):
#       __IMAGE__  tên ảnh đã upload vào ComfyUI (LoadImage.inputs.image)
#       __PROMPT__ / __NEG__       prompt + negative
#       __WIDTH__ / __HEIGHT__ / __FRAMES__ / __SEED__ / __STEPS__ / __FPS__   (số)
#       __MODEL__ / __VAE__ / __TEXT_ENCODER__   tên file model (= env LTX_MODEL/LTX_VAE/…)
#       __PREFIX__ filename_prefix node lưu video (đặt save_output=true để fetch ra /view)
#    KHÔNG có template → build_ltx_i2v_workflow raise → Teaser tự fallback Ken Burns (không crash job).
LTX_FPS          = int(os.environ.get("LTX_FPS", "24"))
LTX_STEPS        = int(os.environ.get("LTX_STEPS", "8"))             # distilled-1.1 = 8 bước cfg=1; dev cần ~20-30
LTX_MAX_FRAMES   = int(os.environ.get("LTX_MAX_FRAMES", "257"))
LTX_TIMEOUT      = int(os.environ.get("LTX_TIMEOUT_SEC", "1800"))
LTX_MODEL        = os.environ.get("LTX_MODEL", "LTX-2.3-22B-distilled-1.1-Q6_K.gguf")  # GGUF 21GB vừa 5090 (UnetLoaderGGUF), đã tải vào models/unet
LTX_VAE          = os.environ.get("LTX_VAE", "")                     # LTX-2.3 VAE bundled trong checkpoint → thường để trống
LTX_TEXT_ENCODER = os.environ.get("LTX_TEXT_ENCODER", "")           # Gemma 3 (điền nếu workflow tách riêng node text-encoder)
LTX_WORKFLOW_JSON = os.environ.get("LTX_WORKFLOW_JSON", "")

def ltx_frames(dur):
    """Số frame LTXV hợp lệ (≡ 1 mod 8) theo thời lượng shot + LTX_FPS."""
    F = max(25, min(LTX_MAX_FRAMES, int(round(float(dur) * LTX_FPS))))
    return F - ((F - 1) % 8)

def _ltx_template(p):
    """Template workflow LTX (dict) từ params.ltxWorkflow | env LTX_WORKFLOW_JSON (path|JSON). None = chưa có."""
    raw = p.get("ltxWorkflow") or LTX_WORKFLOW_JSON
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if not s.startswith("{") and os.path.exists(s):
            with open(s) as f: return json.load(f)
        return json.loads(s)
    return None

def _ltx_apply(tpl, subs):
    """Deep-copy template + thay token. Token nguyên-giá-trị (vd "__WIDTH__") → giá trị số;
    token nằm trong chuỗi (vd "...,__PROMPT__,...") → replace chuỗi."""
    def walk(v):
        if isinstance(v, dict):  return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):  return [walk(x) for x in v]
        if isinstance(v, str):
            if v in subs: return subs[v]
            out = v
            for tok, val in subs.items():
                if isinstance(val, str) and tok in out: out = out.replace(tok, val)
            return out
        return v
    return walk(tpl)

def build_ltx_i2v_workflow(image_name, p, frames, prompt="", prefix="ltx"):
    """Dựng workflow LTX-2.3 từ TEMPLATE đã-verify (params.ltxWorkflow | env LTX_WORKFLOW_JSON),
    thay token động. KHÔNG có template → raise (caller Teaser fallback Ken Burns)."""
    W = int(p.get("ltx_width", p.get("width", 768)))
    H = int(p.get("ltx_height", p.get("height", 1280)))
    pos = (prompt or p.get("ltxPrompt") or
           "cinematic camera motion, gentle parallax, subtle product movement, soft studio lighting, "
           "premium advertising, high detail, photorealistic").strip()
    neg = p.get("ltxNegative", p.get("negative_prompt",
           "static, no motion, frozen frame, blurry, lowres, deformed, watermark, text"))
    seed = int(p.get("ltx_seed", abs(hash(prefix)) % (2 ** 31)))
    steps = int(p.get("ltx_steps", LTX_STEPS))
    subs = {"__IMAGE__": image_name, "__PROMPT__": pos, "__NEG__": neg,
            "__MODEL__": LTX_MODEL, "__VAE__": LTX_VAE, "__TEXT_ENCODER__": LTX_TEXT_ENCODER, "__PREFIX__": prefix,
            "__WIDTH__": int(W), "__HEIGHT__": int(H), "__FRAMES__": int(frames),
            "__SEED__": int(seed), "__STEPS__": int(steps), "__FPS__": int(LTX_FPS)}
    tpl = _ltx_template(p)
    if tpl is None:
        raise RuntimeError(
            "LTX-2.3 chưa cấu hình workflow: set env LTX_WORKFLOW_JSON (đường dẫn/JSON 'Save (API Format)' "
            "của workflow LTX-2.3 I2V xuất từ ComfyUI) hoặc params.ltxWorkflow. "
            "Đặt token __IMAGE__/__PROMPT__/__WIDTH__/__HEIGHT__/__FRAMES__/__SEED__… trong template.")
    return _ltx_apply(tpl, subs)

# ───────────────────────── Qwen-Image-Edit (tryon / create-image) ─────────────────────────
QWEN_GGUF = os.environ.get("QWEN_EDIT_GGUF", "Qwen-Image-Edit-2509-Q8_0.gguf")
QWEN_CLIP = os.environ.get("QWEN_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors")
QWEN_VAE = os.environ.get("QWEN_VAE", "qwen_image_vae.safetensors")
QWEN_EMPTY_LATENT_NODE = os.environ.get("QWEN_EMPTY_LATENT_NODE", "EmptySD3LatentImage")

# #region ALD 13/06/2026 - Model self-host THAY THẾ Qwen-Image-Edit cho create-image (CHỈ text→image, KHÔNG
# sửa/ghép/face-swap). Dùng khi node chọn "Model (self-host)" ≠ qwen-edit VÀ KHÔNG gắn ảnh ref. Flux/SD3.5 tay
# đẹp + ảnh thật hơn Qwen cho ảnh sinh-mới. Có ref/edit/face-swap → tự rớt về Qwen-Edit (xem run_create_image).
#
# CHỌN BẢN ALL-IN-ONE FP8 (CheckpointLoaderSimple 1 file, gói sẵn CLIP/T5/VAE) → đơn giản + chắc, vừa 5090 32GB:
#   • Flux Dev/Schnell fp8: Comfy-Org/flux1-dev|flux1-schnell (KHÔNG gated).
#   • SD3.5 Large/Medium fp8: Comfy-Org/stable-diffusion-3.5-fp8 (KHÔNG gated).
#   • SD3.5 Large Turbo: bản all-in-one fp8 CHƯA có trên HF → dùng checkpoint stabilityai (GATED, cần HF_TOKEN)
#     + TripleCLIPLoader (clip_g+clip_l+t5xxl từ Comfy-Org, không gated). File chưa tải → Turbo lỗi, log rõ.
# Node lõi (FluxGuidance/EmptySD3LatentImage/ConditioningZeroOut/ModelSampling*) ĐÃ verify có trong ComfyUI 0.22.0.
FLUX_DEV_FP8 = os.environ.get("FLUX_DEV_FP8", "flux1-dev-fp8.safetensors")               # → models/checkpoints
FLUX_SCHNELL_FP8 = os.environ.get("FLUX_SCHNELL_FP8", "flux1-schnell-fp8.safetensors")   # → models/checkpoints
SD35_LARGE_FP8 = os.environ.get("SD35_LARGE_FP8", "sd3.5_large_fp8_scaled.safetensors")  # → models/checkpoints (gói CLIP/T5/VAE)
SD35_MEDIUM_FP8 = os.environ.get("SD35_MEDIUM_FP8", "sd3.5_medium_incl_clips_t5xxlfp8scaled.safetensors")  # → models/checkpoints
SD35_TURBO_CKPT = os.environ.get("SD35_TURBO_CKPT", "sd3.5_large_turbo.safetensors")     # → models/checkpoints (gated stabilityai; KHÔNG gói text-enc)
# Text-encoder rời cho Turbo (Turbo checkpoint không gói CLIP/T5 → cần TripleCLIPLoader). Tên khớp Comfy-Org.
SD35_CLIP_G = os.environ.get("SD35_CLIP_G", "clip_g.safetensors")
SD35_CLIP_L = os.environ.get("SD35_CLIP_L", "clip_l.safetensors")
SD35_T5XXL = os.environ.get("SD35_T5XXL", "t5xxl_fp8_e4m3fn_scaled.safetensors")
# Bước/guidance mặc định (override qua env). Flux/SD3.5 KHÔNG có negative thật (CFG=1) → ConditioningZeroOut.
FLUX_DEV_STEPS = int(os.environ.get("FLUX_DEV_STEPS", "24"))
FLUX_SCHNELL_STEPS = int(os.environ.get("FLUX_SCHNELL_STEPS", "4"))
FLUX_GUIDANCE = float(os.environ.get("FLUX_GUIDANCE", "3.5"))          # FluxGuidance node (distilled guidance, KHÁC CFG)
SD35_LARGE_STEPS = int(os.environ.get("SD35_LARGE_STEPS", "28"))
SD35_MEDIUM_STEPS = int(os.environ.get("SD35_MEDIUM_STEPS", "28"))
SD35_TURBO_STEPS = int(os.environ.get("SD35_TURBO_STEPS", "4"))
SD35_LARGE_CFG = float(os.environ.get("SD35_LARGE_CFG", "4.0"))
SD35_MEDIUM_CFG = float(os.environ.get("SD35_MEDIUM_CFG", "4.5"))
SD35_TURBO_CFG = float(os.environ.get("SD35_TURBO_CFG", "1.2"))        # Turbo distilled → CFG thấp
# Tập model self-host hợp lệ cho node (khớp value FE InspectorCreateImage.vue). qwen-edit = mặc định (Qwen path).
# ALD 14/06/2026 - Bỏ SD3.5 (Large/Medium/Turbo) theo yêu cầu user. Config cũ lỡ trỏ sd35-* → KHÔNG khớp set này
# → _selfhost=False → tự rớt về Qwen-Edit (KHÔNG còn submit graph SD3.5 thiếu model → hết lỗi 400 ComfyUI).
SELFHOST_T2I_MODELS = {"flux-dev", "flux-schnell"}
# #endregion

# Món NHỎ trong ảnh full-body (giày/dép) render ở megapixel cao hơn để model 'thấy' bàn chân rõ → áp được SP.
TRYON_SHOES_MP = float(os.environ.get("TRYON_SHOES_MP", "2.0"))
# Feet-detailer: giày quá nhỏ trong full-body → crop vùng chân, thay giày ở res cao, ghép lại ảnh gốc.
TRYON_FEET_DETAILER = os.environ.get("TRYON_FEET_DETAILER", "1").strip().lower() not in ("0", "false", "no", "off", "")
TRYON_FEET_CROP = float(os.environ.get("TRYON_FEET_CROP", "0.42"))   # tỉ lệ chiều cao tính từ ĐÁY (chứa chân)
# ALD 11/06/2026 - bg-remover service: xóa nền/crop ảnh. Tryon crop sát SẢN PHẨM (model=object) → món đồ lấp
# đầy khung → Qwen áp ĐÚNG tỉ lệ (fix "sản phẩm quá nhỏ, mẫu quá to").
BG_REMOVER_URL = os.environ.get("BG_REMOVER_URL", "http://bg-remover:8000").rstrip("/")
TRYON_PRODUCT_AUTOCROP = os.environ.get("TRYON_PRODUCT_AUTOCROP", "1").strip().lower() not in ("0", "false", "no", "off", "")

def _bg_remove_file(src, dst, model="human", crop=False):
    """Gọi service bg-remover xóa nền 1 file ảnh → ghi JPEG ra dst. model=human (người) | object (vật thể).
    crop=True → cắt sát bounding-box foreground. Trả dst nếu OK; raise nếu lỗi (caller tự fallback ảnh gốc)."""
    with open(src, "rb") as f:
        raw = f.read()
    r = requests.post(f"{BG_REMOVER_URL}/remove", params={"model": model, "crop": 1 if crop else 0},
                      data=raw, headers={"Content-Type": "application/octet-stream"}, timeout=120)
    r.raise_for_status()
    with open(dst, "wb") as f:
        f.write(r.content)
    return dst
# Gemini image-edit (Nano Banana) — try-on/edit chính xác hơn Qwen cho vật nhỏ (giày). provider='gemini' ở
# node → dùng path này (API call, KHÔNG cần GPU/ComfyUI). Key: node API Key (cổng nối) hoặc field apiKey trong node — KHÔNG còn env.
# ALD 02/06/2026 - Mặc định Nano Banana PRO (gemini-3-pro-image). 2.5-flash-image (cũ) KHÔNG sửa nổi
# vật nhỏ (giày trong ảnh full-body) + tự re-frame dọc→ngang cắt mất chân. Đã test pro: giữ khung + thay
# giày đúng (= kết quả web AI Studio). Đổi qua env GEMINI_IMAGE_MODEL (vd gemini-3.1-flash-image rẻ hơn).
# ALD 20/08/2026 - "-preview" bị Google khai tử 25/06/2026 (GA gemini-3-pro-image ra 28/05/2026, cùng tên
# comment trên đã ghi nhưng code cũ sót "-preview" chưa dọn) → nhánh Gemini tryon lỗi từ giữa tháng 6. Fix.
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
# ALD 05/06/2026 - BỎ key Gemini HỆ THỐNG (fallback): mỗi user/admin PHẢI nhập key Gemini RIÊNG ở node
# (quota/billing riêng). ALD 11/06/2026 - BỎ HẲN cơ chế env GEMINI_API_KEY (user yêu cầu): key CHỈ đến từ
# node API Key (nối cổng / tự phân bổ) hoặc field trong node. Chỉ self-host không cần key.
# ALD 03/06/2026 - Gemini TTS (giọng TỰ NHIÊN, đa ngôn ngữ) — thay edge-tts (bị MS chặn IP server này) +
# Piper (robotic). Dùng CHUNG key Gemini. Giọng prebuilt: Kore/Aoede/Puck/Charon/Leda/Zephyr… (~30 giọng).
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")
# ALD 03/06/2026 - viXTTS: clone GIỌNG từ file mẫu (service riêng trên GPU box, worker host-net gọi
# 127.0.0.1:8090). Ưu tiên CAO NHẤT khi có service (giống file mẫu user nhất). Lỗi → fallback Gemini/Piper.
VIXTTS_URL = os.environ.get("VIXTTS_URL", "").rstrip("/")
VIXTTS_REF = os.environ.get("VIXTTS_REF", "")
# ALD 15/06/2026 - thư mục ref clone giọng (node Phụ đề lồng tiếng "clone"): worker GHI vào đây, viXTTS (host) ĐỌC.
# Mount cùng đường dẫn host↔container trong compose để viXTTS đọc được file worker ghi (2 process khác fs).
# ALD 02/07/2026 - default theo expanduser("~") thay hardcode /home/ubuntu: VPS chạy user khác vẫn đúng home thật.
TTS_REF_DIR = os.environ.get("TTS_REF_DIR", os.path.expanduser("~/ai/tts-refs"))
# ALD 15/06/2026 - clone (XTTS fine-tune VN) đọc ngôn ngữ khác bị chậm ~1.35x → tăng tốc nền về pace tự nhiên.
CLONE_SPEED = float(os.environ.get("CLONE_TTS_SPEED", "1.3") or "1.3")
# ALD 07/06/2026 - Giọng viXTTS ĐẶT TÊN: FE/job chọn theo tên thân thiện, worker map sang file ref .wav trên box
# (service đọc path local cùng host). Thêm giọng = thêm 1 dòng + đặt .wav vào /home/ubuntu/ai/vixtts/voices/.
# 'voice-male-best' = giọng nam đã clone + LÀM SẠCH (dereverb/denoise/bandpass từ ref_main) — mặc định face-motion.
VIXTTS_VOICES = {
    # ref đã khử vang (WPE + arnndn) từ giọng mẫu user gửi — path RIÊNG (không trùng voice-male-best.wav cũ) để
    # service viXTTS tính latents MỚI, không dính cache cũ. Đổi giọng = thay file này + đổi tên path.
    "voice-male-best": os.environ.get("VIXTTS_REF_MALE_BEST", os.path.expanduser("~/ai/vixtts/voices/voice-male-best-ref.wav")),
}
FACE_MOTION_VOICE = os.environ.get("FACE_MOTION_VOICE", "voice-male-best")   # giọng mặc định "người mẫu đọc kịch bản"

# #region ALD 30/06/2026 - Trụ C: GIỌNG CẢM XÚC. viXTTS/OmniVoice clone cảm xúc TỪ FILE ref → để giọng "truyền cảm,
# hợp hoàn cảnh", đạo diễn (workflow-ai) gán emotion/pace cho từng câu → worker (a) chọn file .wav ref thu sẵn đúng
# sắc thái, (b) chỉnh temperature viXTTS, (c) nắn dấu câu + tốc độ. Gom 11 emotion về 5 "họ" để map ref gọn.
# Các file .wav ref CHƯA có trên box → os.path.exists=False → tự fallback giọng gốc (KHÔNG vỡ). Đặt file = bật cảm xúc.
VIXTTS_VOICE_DIR = os.environ.get("VIXTTS_VOICE_DIR", os.path.expanduser("~/ai/vixtts/voices"))
EMOTION_FAMILY = {
    "warm": "warm", "gentle": "warm", "tender": "warm", "calm": "warm",
    "excited": "excited", "cheerful": "excited", "playful": "excited",
    "confident": "confident", "authoritative": "confident",
    "urgent": "urgent", "neutral": "neutral",
}
# (gender, family) → path .wav ref. Env override: VIXTTS_REF_<GENDER>_<FAMILY> (vd VIXTTS_REF_FEMALE_WARM).
def _emo_ref(gender, family):
    return os.environ.get(f"VIXTTS_REF_{gender.upper()}_{family.upper()}",
                          os.path.join(VIXTTS_VOICE_DIR, f"voice-{gender}-{family}-ref.wav"))
EMOTION_VOICE_REFS = {(g, f): _emo_ref(g, f)
                      for g in ("female", "male")
                      for f in ("warm", "excited", "confident", "urgent")}
# Temperature viXTTS theo "độ kích thích" của cảm xúc (cao = biểu cảm/biến thiên hơn). Service default 0.7.
EMOTION_TEMPERATURE = {"warm": 0.65, "excited": 0.85, "confident": 0.72, "urgent": 0.8, "neutral": 0.7}
# atempo theo nhịp đọc (đạo diễn gán pace). slow = chậm rãi/cảm xúc; fast = dồn dập/CTA.
PACE_ATEMPO = {"slow": 0.92, "normal": 1.0, "fast": 1.1}

def _emotion_family(emotion):
    return EMOTION_FAMILY.get(str(emotion or "").lower().strip(), "neutral")

def _voice_gender_hint(voice, gender=None):
    """Đoán giới tính giọng để chọn ref cảm xúc đúng: param gender > tên voice ('male'/'nam'…) > default female."""
    g = str(gender or "").lower().strip()
    if g.startswith("m") or "nam" in g: return "male"
    if g.startswith("f") or g.startswith("w") or "nu" in g or "nữ" in g: return "female"
    v = str(voice or "").lower()
    if "male" in v or "nam" in v or v == FACE_MOTION_VOICE: return "male"
    return "female"

def _resolve_emotion_ref(voice, emotion, gender=None):
    """Trả path .wav ref đúng cảm xúc NẾU file tồn tại (an toàn deploy trước khi có file). None → giữ giọng gốc."""
    fam = _emotion_family(emotion)
    if fam == "neutral": return None
    ref = EMOTION_VOICE_REFS.get((_voice_gender_hint(voice, gender), fam))
    return ref if (ref and os.path.exists(ref)) else None

def _shape_tts_text(text, pace, emotion):
    """Nắn DẤU CÂU để gợi ngữ điệu (XTTS phản ứng theo dấu câu). KHÔNG đổi chữ → an toàn tiếng Việt có dấu."""
    t = str(text or "").strip()
    if not t: return t
    fam = _emotion_family(emotion)
    if fam in ("excited", "urgent") and t[-1] not in "!?…":
        t = t.rstrip(".") + "!"
    elif fam == "warm" and t.endswith("."):
        t = t[:-1] + "…"
    return t
# #endregion

# ALD 14/06/2026 - OmniVoice (k2-fsa, Apache-2.0): engine TTS CHÍNH MỚI — clone giọng từ ref .wav, bản fine-tune
# tiếng Việt 1000h (splendor1811/omnivoice-vietnamese). Service riêng trên GPU box (host-net 127.0.0.1:8091),
# contract /tts GIỐNG viXTTS. Ưu tiên CAO NHẤT khi có service; lỗi/không có → tự rớt viXTTS→Gemini→edge→Piper.
# TẮT = bỏ OMNIVOICE_URL (toàn bộ về y như cũ). ref dùng CHUNG pool với viXTTS (cùng file .wav trên box).
OMNIVOICE_URL = os.environ.get("OMNIVOICE_URL", "").rstrip("/")
OMNIVOICE_REF = os.environ.get("OMNIVOICE_REF", "")
OMNIVOICE_LANG = os.environ.get("OMNIVOICE_LANG", "vietnamese")

# Gemini image API tự đổi tỉ lệ output nếu KHÔNG truyền imageConfig.aspectRatio → cắt cụt ảnh dọc.
# → tính tỉ lệ gần nhất từ ảnh người để ÉP Gemini giữ khung (giữ bàn chân cho try-on giày).
_GEMINI_ARS = {"1:1": 1.0, "2:3": 2/3, "3:2": 1.5, "3:4": 0.75, "4:3": 4/3,
               "4:5": 0.8, "5:4": 1.25, "9:16": 9/16, "16:9": 16/9, "21:9": 21/9}
def _gemini_aspect(dims):
    if not dims or not dims[0] or not dims[1]:
        return None
    r = dims[0] / dims[1]
    return min(_GEMINI_ARS, key=lambda k: abs(_GEMINI_ARS[k] - r))

def _valid_gemini_key(k):
    """Key Google AI Studio dạng 'AIza…' (~39 ký tự, KHÔNG khoảng trắng). Chặn dán nhầm (vd dán cả
    dòng báo lỗi vào ô key) → báo rõ thay vì để Google trả 400 API_KEY_INVALID khó hiểu."""
    k = (k or "").strip()
    return k.startswith("AIza") and 30 <= len(k) <= 60 and not any(c.isspace() for c in k)

# ALD 11/06/2026 - BỎ HẲN cơ chế env GEMINI_API_KEY (user yêu cầu tách quota/billing từng user).
# ALD 20/08/2026 - KHÔI PHỤC làm fallback theo yêu cầu mới (dùng nhanh, khỏi nhập tay mỗi node) — node/param
# key vẫn ưu tiên tuyệt đối, env chỉ áp dụng khi node KHÔNG nối/không điền key. Một số nơi (teaser dòng
# ALD cũ) đã ghi comment "gem_key từ node hoặc env" từ trước dù code chưa từng đọc env — giờ khớp thật.
GEMINI_API_KEY_ENV = os.environ.get("GEMINI_API_KEY", "").strip()

def _gemini_key(d):
    """Lấy key Gemini: ưu tiên node (apiKey/geminiApiKey trong params), rỗng thì fallback env GEMINI_API_KEY."""
    return (d.get("apiKey") or d.get("geminiApiKey") or GEMINI_API_KEY_ENV or "").strip()

# #region ALD 11/06/2026 - HuggingFace Inference Providers (router → fal-ai). Cùng node media, đổi backend:
# provider 'huggingface' chạy model trên HF (token hf_… của user, billing theo credit HF của họ) thay vì GPU
# local — KHÔNG đụng ComfyUI. Protocol (verify từ huggingface_hub/_providers/fal_ai.py):
#  - sync : POST {HF_ROUTER}/{providerId}                  → JSON kết quả ngay (text-to-image ~5-20s)
#  - queue: POST {HF_ROUTER}/{providerId}?_subdomain=queue → {request_id, response_url}; poll
#           GET {HF_ROUTER}{path}/status?_subdomain=queue tới COMPLETED rồi GET {HF_ROUTER}{path}?_subdomain=queue
# Ảnh vào = data URI base64 (image_url + image_urls); ảnh/video ra = CDN url fal (GET không cần auth).
from urllib.parse import urlparse as _urlparse

HF_ROUTER = os.environ.get("HF_ROUTER_URL", "https://router.huggingface.co/fal-ai").rstrip("/")
# task → (model HF mặc định, fal providerId, queue?). Edit dùng 2511 — 2509 đã RỚT khỏi fal-ai (chỉ còn wavespeed).
HF_DEFAULT_MODELS = {
    "text-to-image":  ("Qwen/Qwen-Image",           "fal-ai/qwen-image",                   False),
    "image-to-image": ("Qwen/Qwen-Image-Edit-2511", "fal-ai/qwen-image-edit-plus",         True),
    "image-to-video": ("Wan-AI/Wan2.2-I2V-A14B",    "fal-ai/wan/v2.2-a14b/image-to-video", True),
}

def _valid_hf_token(k):
    """Token HF dạng 'hf_…' (KHÔNG khoảng trắng). Chặn dán nhầm key Gemini AIza… → báo rõ."""
    k = (k or "").strip()
    return k.startswith("hf_") and 20 <= len(k) <= 200 and not any(c.isspace() for c in k)

def _hf_key(params):
    """Token HF: hfToken (node-level/inject từ node API Key) → apiKey (nếu đúng dạng hf_). KHÔNG có env
    fallback (user yêu cầu: key chỉ từ node API Key — chỉ self-host không cần key). Raise message rõ nếu thiếu/sai."""
    k = str(params.get("hfToken") or "").strip()
    if not k:
        a = str(params.get("apiKey") or "").strip()
        if _valid_hf_token(a):
            k = a
    if not k:
        raise RuntimeError("Provider HuggingFace cần token hf_… — thêm node API Key (Type: HuggingFace) lên canvas hoặc nhập token trong Inspector của node.")
    if not _valid_hf_token(k):
        raise RuntimeError("Token HuggingFace không đúng định dạng (phải bắt đầu bằng hf_, không khoảng trắng). Tạo tại huggingface.co/settings/tokens (quyền Inference Providers).")
    return k

def _hf_resolve(model_id, task):
    """model_id ('' → mặc định theo task) → (hf_id, fal providerId, queue?). Model lạ → tra live
    inferenceProviderMapping của HF chọn entry fal-ai đúng task & status live (mapping hay drift — 2509 từng rớt).
    Raise rõ nếu model không chạy được qua fal-ai."""
    d = HF_DEFAULT_MODELS.get(task)
    mid = str(model_id or "").strip()
    if not mid or (d and mid == d[0]):
        if not d:
            raise RuntimeError(f"HF: task {task} chưa được hỗ trợ")
        return d
    try:
        r = requests.get(f"https://huggingface.co/api/models/{mid}", params={"expand[]": "inferenceProviderMapping"}, timeout=30)
        if r.status_code == 200:
            mp = r.json().get("inferenceProviderMapping") or []
            ents = ([{"provider": p, **(m or {})} for p, m in mp.items()] if isinstance(mp, dict)
                    else [m for m in mp if isinstance(m, dict)])
            for m in ents:
                if str(m.get("provider")) == "fal-ai" and str(m.get("status")) == "live" and str(m.get("task")) == task and m.get("providerId"):
                    return (mid, m["providerId"], True)  # model custom → luôn dùng queue (an toàn cho mọi endpoint fal)
    except Exception:
        pass
    raise RuntimeError(f"Model HF '{mid}' không có mapping fal-ai live cho task {task} — để trống ô Model để dùng mặc định ({(d or ('?',))[0]}) hoặc chọn model khác.")

def _hf_data_uri(path):
    """File ảnh → data URI base64. Payload base64 >~8MB → re-encode JPEG scale ≤2048 (ffmpeg) cho nhẹ request."""
    def _enc(p):
        with open(p, "rb") as f:
            return base64.b64encode(f.read()).decode()
    b64 = _enc(path)
    if len(b64) > 8 * 1024 * 1024:
        small = path + ".hf.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                        "-vf", "scale='min(2048,iw)':-2", "-q:v", "3", small], check=True, timeout=120)
        path, b64 = small, _enc(small)
    return f"data:{_mime(path)};base64,{b64}"

def _hf_raise(provider_id, r):
    """Dịch lỗi fal/HF thành message hành động được. content_policy_violation = bộ kiểm duyệt INPUT của fal
    (docs.fal.ai/errors) — không tắt được bằng tham số; nội dung nhạy cảm phải chạy Self-host."""
    if "content_policy_violation" in (r.text or ""):
        raise RuntimeError(
            "HF/fal TỪ CHỐI nội dung (content checker): prompt/ảnh bị gắn cờ nhạy cảm "
            "(vd đồ ngủ/lingerie/từ ngữ gợi cảm như 'intimate'). Giảm yếu tố nhạy cảm trong prompt, "
            f"hoặc đổi Provider = Self-host (không qua kiểm duyệt). [{provider_id} HTTP {r.status_code}]")
    raise RuntimeError(f"HF {provider_id} {r.status_code}: {r.text[:300]}")

def _hf_image_size(dims, max_side=1280):
    """dims (w,h) → image_size cho fal: GIỮ ĐÚNG tỉ lệ ảnh gốc, cap cạnh dài ≤max_side, làm tròn /8.
    Không gửi image_size = fal tự chọn (hay ra vuông) → người bị bóp méo/mặt to sai tỉ lệ."""
    if not dims:
        return None
    w, h = dims
    if not w or not h:
        return None
    sc = min(1.0, float(max_side) / max(w, h))
    return {"width": max(64, int(w * sc) // 8 * 8), "height": max(64, int(h * sc) // 8 * 8)}

def _hf_call(provider_id, payload, token, queue, job_id, deadline_sec, prog_lo, prog_hi, step):
    """Gọi HF router (fal-ai). sync → JSON ngay; queue → submit rồi poll status mỗi 2.5s tới COMPLETED
    (api_progress nhích dần prog_lo→prog_hi theo elapsed). Raise RuntimeError message rõ khi lỗi/timeout."""
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if not queue:
        r = requests.post(f"{HF_ROUTER}/{provider_id}", headers=hdr, json=payload, timeout=300)
        if r.status_code != 200:
            _hf_raise(provider_id, r)
        return r.json()
    r = requests.post(f"{HF_ROUTER}/{provider_id}?_subdomain=queue", headers=hdr, json=payload, timeout=120)
    if r.status_code not in (200, 201, 202):
        _hf_raise(provider_id, r)
    sub = r.json()
    qpath = _urlparse(str(sub.get("response_url") or "")).path
    if not qpath:
        raise RuntimeError(f"HF queue không trả response_url: {json.dumps(sub)[:200]}")
    start = time.time()
    bad = 0  # đếm status-check hỏng LIÊN TIẾP → fail nhanh kèm chẩn đoán thay vì treo im tới deadline
    while True:
        el = time.time() - start
        if el > deadline_sec:
            raise RuntimeError(f"HF {provider_id} quá {int(deadline_sec)}s chưa xong — hủy chờ")
        # ALD 11/06/2026 - tôn trọng nút Cancel: HF chạy ngoài ComfyUI nên phải tự check mỗi vòng poll
        if api_job_cancelled(job_id):
            raise RuntimeError("Job đã bị hủy (cancel)")
        st, _code, _body = "", 0, ""
        try:
            s = requests.get(f"{HF_ROUTER}{qpath}/status?_subdomain=queue", headers=hdr, timeout=30)
            _code, _body = s.status_code, (s.text or "")[:200]
            # ALD 11/06/2026 - FIX TREO "đang chờ": fal trả HTTP 202 khi IN_QUEUE/IN_PROGRESS (200 chỉ khi xong);
            # trước đây chỉ nhận ==200 nên st rỗng mãi → kẹt 87% tới deadline. Nhận mọi 2xx.
            if 200 <= s.status_code < 300:
                st = str((s.json() or {}).get("status") or "").upper()
        except Exception as _e:
            _body = str(_e)[:200]  # lỗi mạng thoáng qua → thử lại, nhưng có đếm bad bên dưới
        if st:
            bad = 0
        else:
            bad += 1
            if bad >= 20:  # ~50s hỏng liên tiếp = lỗi thật (URL/token/mạng) → báo rõ kèm HTTP code
                raise RuntimeError(f"HF {provider_id}: status check hỏng {bad} lần liên tiếp (HTTP {_code}: {_body})")  # lỗi mạng thoáng qua → thử lại vòng sau
        if "FAIL" in st or "ERROR" in st:
            raise RuntimeError(f"HF {provider_id} báo lỗi (status {st})")
        if st == "COMPLETED":
            res = requests.get(f"{HF_ROUTER}{qpath}?_subdomain=queue", headers=hdr, timeout=120)
            if res.status_code != 200:
                _hf_raise(provider_id, res)
            return res.json()
        frac = prog_lo + (prog_hi - prog_lo) * min(0.95, el / max(60.0, deadline_sec / 3.0))
        api_progress(job_id, frac, f"{step} (HF: {st or 'đang chờ'})")
        time.sleep(2.5)

def _hf_first_image(res):
    """URL ảnh đầu tiên từ JSON fal ({'images':[{'url':…}]} hoặc {'image':{'url':…}}). Raise nếu không có."""
    imgs = res.get("images") or ([res["image"]] if isinstance(res.get("image"), dict) else [])
    for im in imgs:
        u = (im or {}).get("url")
        if u:
            return u
    raise RuntimeError(f"HF không trả ảnh: {json.dumps(res)[:300]}")

def _hf_fetch(url, dest):
    """Tải file kết quả từ CDN fal (URL công khai, không cần auth)."""
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    return dest
# #endregion

# #region ALD 10/07/2026 - Provider DashScope (Alibaba Model Studio) cho node wan-i2v: happyhorse i2v + wan2.x i2v.
# Async submit+poll (header X-DashScope-Async: enable) giống _hf_call; ảnh gửi base64 data URI qua
# input.media[] {type:"first_frame"|"last_frame", url} (API này KHÔNG có img_url).
# Họ wan2.x (wan2.7-i2v...) thêm: driving_audio (video nhép theo audio — CHỈ nhận URL public wav/mp3 2-30s ≤15MB,
# KHÔNG nhận base64 → worker xin URL HMAC qua GET /worker/public-url), last_frame, prompt_extend, duration 2-15s.
# happyhorse: 3-15s, chỉ first_frame. resolution 720P/1080P.
# watermark TẮT chủ động — default happyhorse = CÓ chữ "Happy Horse" góc video. video_url sống 24h → tải về ngay.
# Key: params.apiKey (engine tự inject từ node API Key khi providerType == provider == "dashscope").
DASHSCOPE_BASE = os.environ.get("MOTION_DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")

def _api_public_url(key):
    """Đổi storage key → URL public (HMAC /media, không cần header) để dịch vụ cloud tự fetch."""
    r = requests.get(f"{API_URL}/worker/public-url", params={"key": key}, headers=WORKER_HEADERS, timeout=30)
    r.raise_for_status()
    url = (r.json() or {}).get("url") or ""
    if not url:
        raise RuntimeError(f"API không trả public-url cho key {key}")
    return url

def _api_upload_temp(key, local_path, content_type="video/mp4"):
    """Đẩy file trung gian lên storage (POST /worker/upload-temp) → trả key để public-url."""
    with open(local_path, "rb") as f:
        r = requests.post(f"{API_URL}/worker/upload-temp", headers=WORKER_HEADERS,
                          data={"key": key}, files={"file": (os.path.basename(local_path), f, content_type)},
                          timeout=300)
    r.raise_for_status()
    return (r.json() or {}).get("key") or key

def _dashscope_ready_video_url(job_id, motion_key, tmp):
    """DashScope decoder CHỈ ăn H.264 (driver AV1/HEVC/VP9 → 'InvalidVideo.OpenError') và driver phải 2-30s.
    Probe codec+duration: h264 và ≤30s → dùng thẳng URL gốc; ngược lại transcode H.264 (+cap 30s, giữ audio)
    → upload-temp → URL public của bản đã chuyển."""
    d_local = api_download(motion_key, os.path.join(tmp, "driver_src" + (os.path.splitext(motion_key)[1] or ".mp4")))
    codec, dur = "", 0.0
    try:
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=codec_name:format=duration",
                             "-of", "json", d_local], capture_output=True, text=True, timeout=60)
        pj = json.loads(pr.stdout or "{}")
        codec = ((pj.get("streams") or [{}])[0].get("codec_name") or "").lower()
        dur = float((pj.get("format") or {}).get("duration") or 0)
    except Exception as pe:
        api_log(job_id, f"ffprobe driver lỗi ({pe}) → transcode phòng hờ", "warn")
    if codec == "h264" and 0 < dur <= 30:
        return _api_public_url(motion_key)
    reason = f"codec={codec or '?'}" + (f", {dur:.0f}s>30s" if dur > 30 else "")
    api_log(job_id, f"Driver không đạt chuẩn cloud ({reason}) → transcode H.264" + (" + cắt 30s" if dur > 30 else ""), "info")
    h264 = os.path.join(tmp, "driver_h264.mp4")
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", d_local]
    if dur > 30:
        cmd += ["-t", "30"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", h264]
    subprocess.run(cmd, check=True, timeout=900)
    tkey = _api_upload_temp(f"{job_id}/driver_h264.mp4", h264)
    return _api_public_url(tkey)

def _dashscope_ready_image_url(job_id, ref_key, tmp):
    """Ảnh mẫu cloud cap 5MB — quá thì nén JPG (max 2048px) → upload-temp → URL public."""
    i_local = api_download(ref_key, os.path.join(tmp, "ref_src" + (os.path.splitext(ref_key)[1] or ".png")))
    if os.path.getsize(i_local) <= 5 * 1024 * 1024:
        return _api_public_url(ref_key)
    api_log(job_id, "Ảnh mẫu >5MB → nén JPG cho cloud", "info")
    jpg = os.path.join(tmp, "ref_5mb.jpg")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", i_local,
                    "-vf", "scale='min(2048,iw)':-2", "-q:v", "3", jpg], check=True, timeout=120)
    tkey = _api_upload_temp(f"{job_id}/ref_5mb.jpg", jpg, content_type="image/jpeg")
    return _api_public_url(tkey)

def _dashscope_i2v(job_id, params, prompt, start_local, tmp, end_key=None, audio_key=None):
    key = (params.get("apiKey") or params.get("dashscopeApiKey") or "").strip()
    if not key:
        raise RuntimeError("wan-i2v provider DashScope cần API key — nối node API Key (Type: dashscope) vào cổng API Key của node, hoặc đặt node API Key rời trong workflow.")
    model = str(params.get("dashscopeModel") or "happyhorse-1.0-i2v").strip()
    is_wan2x = model.startswith("wan2.")  # wan2.6/2.7: audio + last_frame + prompt_extend
    res = str(params.get("dashscopeResolution") or "720P").upper().strip()
    if res not in ("720P", "1080P"):
        res = "720P"
    try:
        dur = int(float(params.get("duration") or params.get("durationSec") or 5))
    except (TypeError, ValueError):
        dur = 5
    dur = max(2 if is_wan2x else 3, min(15, dur))
    media = [{"type": "first_frame", "url": _hf_data_uri(start_local)}]
    if end_key:
        if is_wan2x:
            end_local = api_download(end_key, os.path.join(tmp, "end" + (os.path.splitext(end_key)[1] or ".png")))
            media.append({"type": "last_frame", "url": _hf_data_uri(end_local)})
            api_log(job_id, "wan-i2v DashScope: có ẢNH CUỐI (last_frame) — morph đầu→cuối", "info")
        else:
            api_log(job_id, f"wan-i2v DashScope: model {model} chỉ nhận ẢNH ĐẦU → bỏ qua ảnh cuối (đổi wan2.7-i2v nếu cần)", "warn")
    if audio_key:
        if is_wan2x:
            aurl = _api_public_url(audio_key)
            media.append({"type": "driving_audio", "url": aurl})
            api_log(job_id, "wan-i2v DashScope: có DRIVING AUDIO — video nhép/diễn theo audio (wav/mp3 2-30s ≤15MB)", "info")
        else:
            api_log(job_id, f"wan-i2v DashScope: model {model} không hỗ trợ audio → bỏ qua (đổi wan2.7-i2v nếu cần)", "warn")
    if str(params.get("faceLock", params.get("face_lock", ""))).strip().lower() in ("1", "true", "yes", "on"):
        api_log(job_id, "wan-i2v DashScope: faceLock chưa hỗ trợ provider cloud → bỏ qua", "warn")
    body = {
        "model": model,
        "input": {"prompt": prompt, "media": media},
        "parameters": {"resolution": res, "duration": dur, "watermark": False},
    }
    if is_wan2x:
        body["parameters"]["prompt_extend"] = str(params.get("dashscopePromptExtend", "1")).strip().lower() not in ("0", "false", "no", "off")
    try:
        if str(params.get("seed") or "").strip() not in ("", "0"):
            body["parameters"]["seed"] = int(params.get("seed"))
    except (TypeError, ValueError):
        pass
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    api_progress(job_id, 0.12, f"DashScope {model} ({res} · {dur}s)")
    r = requests.post(f"{DASHSCOPE_BASE}/api/v1/services/aigc/video-generation/video-synthesis",
                      headers=hdr, json=body, timeout=120)
    if r.status_code >= 400:
        try:
            _em = (r.json().get("message") or r.text)[:300]
        except Exception:
            _em = r.text[:300]
        raise RuntimeError(f"DashScope submit {r.status_code}: {_em} — key sai/hết hạn? model {model} chưa mở ở region này?")
    task_id = str(((r.json() or {}).get("output") or {}).get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"DashScope không trả task_id: {r.text[:300]}")
    api_log(job_id, f"DashScope task {task_id} đã tạo — chờ render (~1-5 phút, poll 10s/lần)", "info")
    vurl = _dashscope_wait(job_id, key, task_id, timeout=int(params.get("dashscope_timeout") or 1800))
    api_progress(job_id, 0.88, "tải video từ DashScope")
    return _hf_fetch(vurl, os.path.join(tmp, "dashscope.mp4"))

def _ds_video_url(outp):
    """Bóc video_url từ output SUCCEEDED — vị trí khác nhau theo model (video_url / results{} / results[])."""
    if outp.get("video_url"):
        return outp["video_url"]
    r = outp.get("results")
    if isinstance(r, dict):
        return r.get("video_url") or r.get("url") or ""
    if isinstance(r, list) and r:
        return r[0].get("video_url") or r[0].get("url") or ""
    return ""

def _dashscope_wait(job_id, key, task_id, timeout=1800, prog_lo=0.15, prog_hi=0.85, eta_sec=300.0):
    """Poll GET /api/v1/tasks/{id} tới SUCCEEDED → trả video_url. Check hủy job mỗi vòng, fail-fast lỗi liên tục."""
    t0 = time.time()
    deadline = t0 + timeout
    errs = 0
    while True:
        if api_job_cancelled(job_id):
            raise RuntimeError("cancelled")
        if time.time() > deadline:
            raise RuntimeError(f"DashScope quá hạn chờ ({int(time.time() - t0)}s) — task {task_id}")
        time.sleep(10)
        try:
            pr = requests.get(f"{DASHSCOPE_BASE}/api/v1/tasks/{task_id}",
                              headers={"Authorization": f"Bearer {key}"}, timeout=60)
            pj = pr.json()
            errs = 0
        except Exception as pe:
            errs += 1
            if errs >= 5:
                raise RuntimeError(f"DashScope poll lỗi liên tục: {pe}")
            continue
        outp = pj.get("output") or {}
        st = str(outp.get("task_status") or "").upper()
        if st == "SUCCEEDED":
            vurl = _ds_video_url(outp)
            if not vurl:
                raise RuntimeError(f"DashScope SUCCEEDED nhưng thiếu video_url: {str(pj)[:300]}")
            return vurl
        if st in ("FAILED", "CANCELED", "CANCELLED"):
            raise RuntimeError(f"DashScope task {st}: {outp.get('code') or ''} {outp.get('message') or 'không rõ lý do'}".strip())
        frac = min(1.0, (time.time() - t0) / eta_sec)
        api_progress(job_id, prog_lo + (prog_hi - prog_lo) * frac, f"DashScope {st.lower() or 'running'}")

def _dashscope_animate(job_id, params, ref_key, motion_key, tmp):
    """MOTION TRANSFER cloud: wan2.2-animate-move (bê chuyển động driver → ảnh mẫu) / -mix (thay người vào video).
    Endpoint KHÁC i2v: /aigc/image2video/video-synthesis/ ; input = image_url + video_url — cả 2 PHẢI là URL
    public (ảnh ≤5MB, video 2-30s ≤200MB) → dùng _api_public_url. Kết quả: output.results.video_url."""
    key = (params.get("apiKey") or params.get("dashscopeApiKey") or "").strip()
    if not key:
        raise RuntimeError("Motion provider DashScope cần API key — nối node API Key (Type: dashscope) vào cổng API Key của node, hoặc đặt node API Key rời trong workflow.")
    model = str(params.get("dashscopeModel") or "wan2.2-animate-move").strip()
    quality = str(params.get("dashscopeQuality") or "wan-std").strip()
    if quality not in ("wan-std", "wan-pro"):
        quality = "wan-std"
    # ALD 10/07/2026 - sự cố job đầu tiên: driver AV1 (tải TikTok/YT) → DashScope "InvalidVideo.OpenError".
    # Chuẩn hoá input trước khi gửi: driver phải H.264 ≤30s, ảnh ≤5MB — không đạt thì transcode/nén + upload-temp.
    api_progress(job_id, 0.06, "chuẩn hoá input cho cloud (codec/size)")
    video_url = _dashscope_ready_video_url(job_id, motion_key, tmp)
    image_url = _dashscope_ready_image_url(job_id, ref_key, tmp)
    if model.endswith("-video-edit"):
        # happyhorse-1.0-video-edit: instruction-based edit — GIỮ motion driver, THAY người/đồ theo ảnh tham chiếu
        # (semantics như animate-mix nhưng CÓ trên region intl). Body khác hẳn: media [{video},{reference_image}]
        # + prompt chỉ dẫn; endpoint = video-generation (KHÔNG phải image2video). Resolution 720P/1080P.
        res = str(params.get("dashscopeResolution") or "720P").upper().strip()
        if res not in ("720P", "1080P"):
            res = "720P"
        _ins_raw = str(params.get("dashscopePrompt") or params.get("prompt") or "").strip()
        _ins = (_translate_prompt_en(_ins_raw, job_id) or _ins_raw) if _ins_raw else \
            "Replace the person in the video with the person from the reference image. Keep the original motion, camera movement and background exactly unchanged. Preserve the reference person's face and outfit accurately."
        body = {
            "model": model,
            "input": {"prompt": _ins, "media": [
                {"type": "video", "url": video_url},
                {"type": "reference_image", "url": image_url},
            ]},
            "parameters": {"resolution": res, "watermark": False},
        }
        _submit_path = "/api/v1/services/aigc/video-generation/video-synthesis"
        _step = f"DashScope {model} ({res})"
    else:
        body = {
            "model": model,
            "input": {"image_url": image_url, "video_url": video_url},
            "parameters": {"check_image": True, "mode": quality},
        }
        _submit_path = "/api/v1/services/aigc/image2video/video-synthesis/"
        _step = f"DashScope {model} ({quality})"
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    api_progress(job_id, 0.12, _step)
    r = requests.post(f"{DASHSCOPE_BASE}{_submit_path}", headers=hdr, json=body, timeout=120)
    if r.status_code >= 400:
        try:
            _em = (r.json().get("message") or r.text)[:300]
        except Exception:
            _em = r.text[:300]
        raise RuntimeError(f"DashScope submit {r.status_code}: {_em} — key sai/hết hạn? model {model} chưa mở ở region này? ảnh >5MB hoặc driver >30s/200MB?")
    task_id = str(((r.json() or {}).get("output") or {}).get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"DashScope không trả task_id: {r.text[:300]}")
    api_log(job_id, f"DashScope task {task_id} đã tạo — chờ render (poll 10s/lần)", "info")
    vurl = _dashscope_wait(job_id, key, task_id, timeout=int(params.get("dashscope_timeout") or 2400), eta_sec=420.0)
    api_progress(job_id, 0.88, "tải video từ DashScope")
    return _hf_fetch(vurl, os.path.join(tmp, "dashscope_motion.mp4"))
# #endregion

# ALD 02/06/2026 - Tách set/label ra module-level để build_qwen_tryon_workflow + _tryon_mp dùng chung.
# 10 loại. Bộ 2 mảnh (thay CẢ trên+dưới) + giày (chỉ thay chân) cần prompt riêng.
GARMENT_MULTI = {"set", "full", "bikini", "swimwear", "swimsuit", "lingerie", "underwear",
                 "do-lot", "dolot", "do_lot", "noi-y", "noiy", "bo", "2pieces", "two-piece"}
# ALD 21/06/2026 - TÁCH nhóm HỞ DA (bikini/đồ bơi/đồ lót) khỏi set/đồ-bộ THƯỜNG. Cả hai đều 2 mảnh, nhưng set
# = áo + quần/short BÌNH THƯỜNG (KHÔNG được render thành bikini/hở da). Chỉ REVEAL mới dùng prompt bra+panty+da trần.
GARMENT_REVEAL = {"bikini", "swimwear", "swimsuit", "lingerie", "underwear",
                  "do-lot", "dolot", "do_lot", "noi-y", "noiy"}
GARMENT_SHOES = {"shoes", "shoe", "footwear", "giay", "giày", "dep", "dép", "sandal", "heels", "boot", "boots"}
# ALD 21/06/2026 - ĐẦM/VÁY LIỀN 1 mảnh (phủ CẢ thân) — cần nhánh THAY TOÀN BỘ outfit, KHÁC set (2 mảnh tách top+bottom)
# và KHÁC skirt (chân váy lẻ → giữ áo). Thiếu nhánh này thì dress rơi vào else "keep other garments" → giữ quần/váy gốc
# của model → lòi ra dưới đầm = "lỗi phần váy". KHÔNG đưa "skirt"/"vay" vào đây (chân váy lẻ ≠ đầm liền).
GARMENT_DRESS = {"dress", "gown", "dam", "đầm", "jumpsuit", "playsuit", "romper", "overall", "bodysuit",
                 "ao-dai", "aodai", "ao_dai", "maxi", "onepiece", "one-piece"}
GARMENT_LABEL = {
    "upper": "top or shirt", "lower": "pants", "skirt": "skirt", "dress": "dress",
    "gown": "gown", "dam": "dress", "đầm": "dress", "jumpsuit": "jumpsuit", "playsuit": "playsuit (one-piece outfit)",
    "romper": "romper (one-piece outfit)", "overall": "overall (one-piece outfit)", "bodysuit": "bodysuit",
    "ao-dai": "Vietnamese ao dai (long dress)", "aodai": "Vietnamese ao dai (long dress)", "ao_dai": "Vietnamese ao dai (long dress)",
    "maxi": "maxi dress", "onepiece": "one-piece dress", "one-piece": "one-piece dress",
    "bra": "bra (the single upper undergarment)", "accessory": "accessory",
    "shoes": "shoes/footwear", "giay": "shoes/footwear", "dep": "sandals/footwear",
    "set": "complete two-piece everyday outfit (top and bottom)",
    "full": "complete two-piece everyday outfit (top and bottom)",
    "bo": "complete two-piece everyday outfit (top and bottom)",
    "2pieces": "complete two-piece everyday outfit (top and bottom)",
    "two-piece": "complete two-piece everyday outfit (top and bottom)",
    "bikini": "complete bikini/swimwear set",
    "swimwear": "complete swimwear set", "swimsuit": "complete swimsuit set",
    "lingerie": "complete lingerie set", "underwear": "complete lingerie set",
    "do-lot": "complete lingerie set", "dolot": "complete lingerie set",
}

# ALD 12/07/2026 - HOTFIX chống da-nhựa (theo phân tích user):
#  • TRYON_MP 1.0→1.6: 1MP làm mất vi-chi-tiết da → nâng MP giữ texture da thật.
#  • TRYON_CFG 4.0→2.4: CFG cao đốt màu + da bóng-render (chính file đã ghi 2.5 là mức hợp lý).
#  • denoise theo LOẠI ĐỒ (chỉ áp khi có VAEEncode ảnh gốc = protect_face): thấp = giữ da/người gốc nhiều hơn,
#    đủ để đổi đồ mà không vẽ lại toàn bộ. upper/lower nhẹ (0.82), dress/set/đồ-lộ đổi nhiều (0.92), giày (0.65).
TRYON_MP = float(os.environ.get("TRYON_MP", "1.6"))
TRYON_CFG = float(os.environ.get("TRYON_CFG", "2.4"))
# ALD 20/07/2026 - "Giữ tỉ lệ ảnh gốc" cho Try-On (mượn cơ chế bodyProportionLock của Motion): mặc định BẬT →
# tryon chạy IMG2IMG từ chính ảnh model (VAEEncode ảnh gốc + denoise<1.0 theo _tryon_denoise) thay vì
# denoise=1.0 + EmptyLatent (full-regen kéo gầy tay/đổi head-to-body ratio dù đã có _PROP_LOCK ở prompt).
# Đặt TRYON_IMG2IMG=0 để quay về hành vi cũ (full-regen) nếu regress.
TRYON_IMG2IMG = os.environ.get("TRYON_IMG2IMG", "1").strip().lower() not in ("0", "false", "no", "off", "")
def _tryon_mp(gt):
    """Megapixel làm việc cho tryon: giày/dép (RẤT nhỏ trong ảnh full-body) cần res cao → TRYON_SHOES_MP;
    còn lại TRYON_MP (1.6, giữ vi-chi-tiết da)."""
    return TRYON_SHOES_MP if str(gt or "").lower().strip() in GARMENT_SHOES else TRYON_MP

def _tryon_denoise(gt):
    """denoise img2img theo loại đồ (chỉ dùng khi base = VAEEncode ảnh gốc). Thấp = giữ người/da gốc nhiều hơn."""
    g = str(gt or "").lower().strip()
    if g in GARMENT_SHOES:
        return float(os.environ.get("TRYON_DENOISE_SHOES", "0.65"))
    if g in GARMENT_DRESS or g in GARMENT_MULTI or g in GARMENT_REVEAL or g in ("auto", "generic"):
        return float(os.environ.get("TRYON_DENOISE_HEAVY", "0.92"))
    # ALD 16/08/2026 - 0.82 → 0.90: đo trên cặp ảnh fail thật của user (áo trắng ↔ blouse trắng, seed 42) —
    # 0.82 neo ảnh gốc quá chặt, KHÔNG thay nổi món lẻ; 0.90 thay đúng mà vẫn giữ tỉ lệ/dáng (mục đích img2img).
    return float(os.environ.get("TRYON_DENOISE_LIGHT", "0.90"))

def _img_size(path):
    """(W,H) ảnh qua ffprobe (png/jpeg/webp) — None nếu lỗi (caller fallback an toàn)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
                           capture_output=True, text=True, timeout=30)
        w, h = (r.stdout or "").strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None

def _fit_aligned(w, h, mp=1.0, align=64):
    """Scale (w,h) về ~mp megapixel, GIỮ tỉ lệ, làm tròn BỘI SỐ `align` (≥align). Latent /64 tránh
    'band' lỗi ở đáy ảnh (do Qwen-Image-Edit pad phần dư khi H không chia hết cho grid)."""
    import math
    s = math.sqrt(mp * 1_000_000 / max(1, w * h))
    W = max(align, int(round(w * s / align)) * align)
    H = max(align, int(round(h * s / align)) * align)
    return W, H

# ALD 01/07/2026 - Ô "Ghi chú thêm" ở node TryOn: user dặn thêm điều muốn GIỮ/ĐỔI (vd "giữ nguyên nón và trang
# sức", "thêm kính râm") ngoài việc thay quần áo. Đặt mệnh lệnh ƯU TIÊN CAO lên đầu prompt (như _HAIR_LOCK) để
# image-conditioning của ảnh sản phẩm không đè. Rỗng → "" (không đổi hành vi cũ).
def _tryon_extra_clause(extra):
    e = str(extra or "").strip()
    if not e:
        return ""
    return ("ADDITIONAL USER INSTRUCTION — high priority, follow this exactly and let it OVERRIDE the general rules "
            f"where they conflict (e.g. keep or add the items the user names): {e}. ")

def _qwen_tryon_prompts(gt, extra=None):
    """(pos, neg) prompt try-on cho Qwen-Image-Edit (image1=model, image2(+3)=sản phẩm) — ALD 11/06/2026: tách
    từ build_qwen_tryon_workflow để nhánh HF (Qwen-Image-Edit-2511, CÙNG họ model) dùng chung, hết cảnh nhánh HF
    xài prompt viết cho Gemini → không áp sản phẩm. extra = ghi chú thêm của user (đã dịch EN ở run_tryon)."""
    MULTI, REVEAL, SHOES, DRESS = GARMENT_MULTI, GARMENT_REVEAL, GARMENT_SHOES, GARMENT_DRESS
    label = GARMENT_LABEL.get(gt, "garment")
    if gt in ("auto", "generic"):
        # ALD 28/06/2026 - AUTO (generic): KHÔNG cần chọn loại đồ. Bảo Qwen mặc NGUYÊN bộ trong ảnh 2 lên người ảnh 1,
        # kệ category — để ẢNH SẢN PHẨM tự quyết (1 món, 2 mảnh, đầm, đồ bơi... gì cũng được). Tránh bug chọn sai loại.
        # #region ALD 20/07/2026 - RÚT GỌN prompt AUTO (fix "lúc được lúc không"): bản 10/07 liệt kê từng thể loại
        # (hosiery/giày/trang sức/mũ/găng...) + negative missing-* dài → tổng prompt ~2.300 ký tự, Qwen bị LOÃNG LỆNH,
        # mỗi seed bám một phần khác nhau → output bất ổn định. Về lại MỘT mệnh lệnh cốt lõi: lấy outfit ảnh 2 →
        # mặc lên người ảnh 1 → gỡ hết đồ gốc → giữ mặt/người/nền. "every visible item of the look" đã bao trùm
        # tất/giày/phụ kiện mà không cần kê khai (kê khai chính là nguồn bịa đồ + loãng). Negative chỉ giữ nhóm
        # leftover-đồ-gốc + identity; bỏ hẳn missing-* (ép sinh đồ khi ảnh SP không có).
        pos = ("Dress the person in image 1 in the outfit shown in image 2. "
               "Copy the ENTIRE look exactly as shown — every visible item of the look in image 2, "
               "with its exact color, pattern, fabric and cut. Do not add, leave out, simplify or restyle anything. "
               "Remove ALL of the person's original clothing that the new outfit replaces or covers, "
               "so nothing of it remains visible or layers over the new outfit. "
               "Keep the person exactly as in image 1: same face, same hair, same body shape, same skin tone, "
               "same pose, same expression, same background. "
               "Photorealistic, natural draping and shadows.")
        neg = ("original outfit kept, original clothing visible, leftover original garment, layered over existing clothes, "
               "only partially replaced, missing pieces from the product, added garments not in the product, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
        # #endregion
    elif gt in REVEAL:
        # bikini / đồ bơi / đồ lót bộ — bộ 2 mảnh HỞ DA (bra + panty + dây/nơ/đai), da trần phần còn lại.
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE and discard ALL of their original clothing — including any dress, top, skirt and bottoms — and dress them EXACTLY in the {label} shown in image 2. "
               f"Reproduce EVERY piece and component of that set faithfully — the top/bra, the bottoms/panties, AND any waist band, garter belt, suspender straps, ties, bows and straps. Do NOT simplify it to just a bra and panty; nothing shown in image 2 may be left out. "
               f"The person must end up wearing only this set, bare skin everywhere else; none of the original garment (especially the original dress) may remain visible. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Match every piece's color, pattern, fabric, lace and cut from image 2 exactly. "
               f"Photorealistic, natural skin, natural draping and shadows, no style change.")
        neg = ("missing garter belt, missing suspender straps, missing waist band, simplified to bra and panty only, "
               "original dress still on, original outfit kept, dress underneath, bra over dress, layered over existing clothes, "
               "leftover original clothing, only top replaced, missing bottom piece, missing panties, mismatched set, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in MULTI:
        # ALD 21/06/2026 - set / đồ bộ THƯỜNG (áo + quần/short/chân váy): thay CẢ 2 mảnh, GIỮ là đồ thường — TUYỆT
        # ĐỐI KHÔNG biến thành bikini/đồ bơi/đồ lót, KHÔNG hở da thừa (fix bug 'set' ra bikini).
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE all of their original clothing and dress them EXACTLY in the {label} shown in image 2 — a COMPLETE TWO-PIECE EVERYDAY outfit. "
               f"Reproduce BOTH pieces faithfully: the TOP and the BOTTOM (pants, shorts or skirt) exactly as shown in image 2, with every color, pattern, fabric, trim and cut. "
               f"This is a NORMAL everyday outfit — do NOT turn it into a bikini, swimwear or lingerie, and do NOT expose any extra bare skin beyond what this outfit naturally covers. "
               f"None of the original clothing may remain visible. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = ("turned into bikini, turned into swimwear, turned into lingerie, bra and panty, exposed underwear, "
               "bare midriff not in product, added bare skin, missing bottom piece, only top replaced, bottom unchanged, "
               "original clothing kept, layered over existing clothes, mismatched set, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in DRESS:
        # ALD 21/06/2026 - FIX "lỗi phần váy": đầm/váy LIỀN 1 MẢNH phủ cả thân → PHẢI gỡ CẢ áo + chân váy/quần gốc của
        # model rồi mặc đầm vào (trước đây dress rơi vào else "keep other garments" → giữ quần/váy gốc → lòi dưới đầm).
        # KHÁC MULTI: đây là MỘT mảnh liền, KHÔNG tách top+bottom.
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE and discard ALL of their original clothing — "
               f"including their top, blouse, skirt and any bottoms — and dress them EXACTLY in the ONE-PIECE {label} shown in image 2. "
               f"Reproduce the ENTIRE dress faithfully: the bodice AND the full skirt with its exact length, tiers, ruffles, color, pattern, fabric and cut from image 2. "
               f"Below the hem of the dress, show the person's bare legs naturally; NONE of the original clothing (especially the original skirt or bottoms) may remain visible anywhere on the body. "
               f"This is ONE single dress, not a separate top and skirt. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = ("original skirt kept, original bottoms kept, original pants kept, beige skirt remaining, leftover clothing under the dress, "
               "dress layered over pants, dress over skirt, only top replaced, lower body unchanged, wrong dress length, truncated skirt, missing skirt, "
               "turned into two-piece, separated top and skirt, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in SHOES:
        # ALD 02/06/2026 - Bàn chân RẤT NHỎ trong ảnh full-body → Qwen hay bỏ qua, giữ giày cũ. Prompt CHỦ ĐỘNG
        # (ra lệnh GỠ giày cũ + ĐI giày mới lên cả 2 chân) + run_tryon nâng res (TRYON_SHOES_MP) cho rõ bàn chân.
        pos = (f"The person in image 1 is currently wearing shoes on their feet. You MUST completely REMOVE those existing shoes "
               f"and put the EXACT {label} from image 2 onto BOTH of their feet. The new shoes from image 2 have to clearly and "
               f"visibly replace the old ones — reproduce their exact color, material, sole, heel height and design faithfully. "
               f"Change NOTHING else: keep the person's clothing, body, legs, pose and the background completely identical. "
               f"Strictly preserve identity: face, hair, body shape, skin tone. "
               f"Photorealistic, correct foot perspective, feet firmly on the ground, natural shadows, no style change.")
        neg = ("original shoes kept, old shoes still on, unchanged footwear, same sneakers remaining, original sneakers, barefoot, missing shoes, "
               "changed clothing, altered outfit, different outfit, missing feet, deformed shoes, extra shoes, floating shoes, wrong foot shape, "
               "different person, distorted face, wrong color, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    else:
        # upper / lower / skirt (chân váy lẻ) / bra / accessory — thay đúng 1 món, GIỮ phần còn lại. (dress → nhánh DRESS riêng)
        pos = (f"Replace the {label} of the person in image 1 with the {label} from image 2. "
               f"Keep the person's other garments unchanged. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Match the new garment's color, pattern, fabric, and cut from image 2 exactly. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = "different person, distorted face, wrong color, wrong pattern, missing details, blurry, lowres, deformed, extra limbs, watermark, text, signature"
    # ALD 15/06/2026 - FIX "output đổi kiểu tóc": negative tóc (khối POS "HAIR LOCK" cũ đã gộp vào _COMPACT_LOCK
    # bên dưới — xem region 16/08/2026).
    _HAIR_NEG = ("different hairstyle, changed hair, restyled hair, new haircut, curled hair, wavy hair, "
                 "straightened hair, longer hair, shorter hair, added hair volume, different hair parting, "
                 "different hair color, hair extensions")
    # ALD 21/06/2026 - FIX "set/Bộ tỉ lệ không đồng nhất": negative tỉ lệ (khối POS "PROPORTION LOCK" cũ đã gộp
    # vào _COMPACT_LOCK). ALD 20/07/2026 - bổ sung negative đầu-nhỏ/chân-dài (PROP mạnh).
    _PROP_NEG = ("distorted body proportions, elongated body, stretched body, shrunken head, oversized head, "
                 "body copied from product image, pose copied from product image, different body scale, "
                 "mismatched proportions, warped anatomy, unnatural body length, "
                 "tiny head, small head, doll-like proportions, long legs, "
                 "elongated legs, stretched torso, lengthened body, fashion-illustration proportions, 9-head figure")
    # ALD 04/07/2026 - FIX "tryon (Auto) ĐỔI MẶT" + 12/07 "mặt bị mờ": negative mặt (khối POS "FACE LOCK" cũ đã
    # gộp vào _COMPACT_LOCK).
    _FACE_NEG = ("different face, changed face, new face, face swap, regenerated face, beautified face, slimmer face, "
                 "reshaped jawline, changed eyes, changed nose, changed lips, changed makeup, changed facial "
                 "expression, younger face, older face, altered identity, "
                 "blurry face, soft face, hazy face, out-of-focus face, low-detail face, smudged facial features, "
                 "unclear eyes, half-closed eyes not in image 1")
    # ALD 06/07/2026 - FIX "ĐỔI MÀU TÓC + sản phẩm KHÔNG GIỐNG" khi ảnh SP là người mặc: negative nguồn-sản-phẩm
    # (khối POS "PRODUCT-SOURCE" cũ đã gộp vào _COMPACT_LOCK).
    _SRC_NEG = ("hair from product image, hair color from image 2, wearer's hairstyle, product model's hair, "
                "face from image 2, wearer's face, skin tone from image 2, different floral pattern, invented pattern")
    # #region ALD 16/08/2026 - FIX "toàn không thay đồ được" (user báo, tái hiện được bằng ảnh thật + seed 42):
    # 4 khối CRITICAL LOCK dồn TRƯỚC lệnh thay đồ đẩy tổng prompt lên ~2.400 ký tự toàn mệnh lệnh "giữ nguyên
    # hệt ảnh 1" — lệnh thay đồ bị dí xuống cuối nên Qwen tái tạo lại gần nguyên ảnh 1 (đồ gốc giữ nguyên).
    # A/B trên pod (cùng ảnh + seed): locks-trước = KHÔNG thay; lệnh-trước = thay đúng sản phẩm, mặt/tóc/tỉ lệ
    # vẫn giữ. Đây cũng đúng bài học 20/07 "prompt dài → LOÃNG LỆNH": các bản vá cũ (FACE/HAIR/PROP/SRC 15/06→
    # 06/07) chữa identity bằng cách dồn lock lên đầu nhưng chính điều đó giết lệnh chính. Kiến trúc mới:
    # (1) LỆNH THAY ĐỒ theo loại đồ đứng ĐẦU, (2) ghi chú user ngay sau, (3) MỘT khối lock GỌN (mặt/tóc/tỉ lệ/
    # bỏ-người-mặc-trong-ảnh-SP) đứng CUỐI. Negative giữ NGUYÊN toàn bộ (vẫn mã hoá mọi failure mode cũ).
    _COMPACT_LOCK = ("CRITICAL: the output must show the SAME person as image 1 — identical face (rendered sharp "
                     "and in focus), identical hairstyle, hair length and hair color, identical body proportions, "
                     "height and scale within the frame, same skin tone, same pose and expression. "
                     "If a person, model or mannequin is wearing the product in image 2, ignore that wearer "
                     "entirely and take ONLY the garments — never copy their hair, face, body, pose or framing.")
    pos = pos + " " + _tryon_extra_clause(extra) + _COMPACT_LOCK
    # #endregion
    neg = neg + ", " + _FACE_NEG + ", " + _HAIR_NEG + ", " + _PROP_NEG + ", " + _SRC_NEG
    return pos, neg

def build_qwen_tryon_workflow(model_name, product_name, garment_type, prefix, target_wh=None, extra_prompt=None):
    # ALD 16/08/2026 - KHÔI PHỤC đa-góc (đảo "tối giản" 20/07 theo yêu cầu user): image1=model, image2=ảnh SP
    # chính, image3 (nếu có)=CÙNG sản phẩm góc khác (mặt sau/bên hông) — TextEncodeQwenImageEditPlus nhận tối đa 3 slot.
    product_names = [product_name] if isinstance(product_name, str) else list(product_name or [])[:2]
    if not product_names:
        raise RuntimeError("tryon: thiếu ảnh sản phẩm")
    gt = str(garment_type or "upper").lower().strip()
    pos, neg = _qwen_tryon_prompts(gt, extra=extra_prompt)
    if len(product_names) > 1:
        # Nói RÕ image3 là góc khác của CÙNG món đồ (thường ảnh 2 = mặt trước, ảnh 3 = mặt sau) —
        # không nói thì Qwen dễ hiểu nhầm là món THÊM và mặc chồng 2 lớp.
        pos += (" Image 2 and image 3 show the SAME single product from two angles — typically image 2 is the "
                "front and image 3 is the back of the product. Use both views only to render that ONE product "
                "accurately from the person's current viewpoint; image 3 is NOT an additional garment to add.")
    seed = abs(hash(prefix)) % (2 ** 31)
    model_mp = _tryon_mp(gt)   # giày → 2MP (rõ bàn chân); còn lại 1MP
    # #region ALD 20/07/2026 - GIỮ TỈ LỆ ẢNH GỐC (TRYON_IMG2IMG, mặc định BẬT). Thay vì denoise=1.0 + EmptyLatent
    # (Qwen VẼ LẠI cả người → gầy tay/lệch head-to-body ratio dù prompt đã có _PROP_LOCK), chạy IMG2IMG từ CHÍNH
    # ảnh model: VAEEncode ảnh gốc + denoise<1.0 theo loại đồ (_tryon_denoise: giày 0.65 / lẻ 0.82 / đầm-set-auto
    # 0.92). Denoise thấp = giữ pixel/tỉ lệ/dáng gốc, chỉ vẽ lại vùng đổi đồ. Base latent PHẢI /64-aligned
    # (target_wh) để không lòi band đáy → node 20 scale model = ImageScale đúng target_wh (thay ImageScaleToTotalPixels).
    # Không có target_wh (teaser) hoặc TRYON_IMG2IMG=0 → giữ nguyên hành vi cũ (full-regen denoise=1.0).
    # ALD 20/07/2026 - CHỈ img2img cho THAY MÓN LẺ (áo/quần/chân váy/giày/phụ kiện): giữ phần còn lại + tỉ lệ.
    # Với THAY NGUYÊN BỘ (auto/đầm/set/đồ bơi-lót) PHẢI full-regen denoise=1.0 + EmptyLatent — nếu img2img neo vào
    # ảnh gốc thì mảnh dưới (váy/quần gốc) KHÔNG bị vẽ lại → "thay được áo, váy vẫn của ảnh gốc". Full-regen cũng
    # cho outfit bám ĐÚNG thiết kế ảnh SP hơn (cổ/keyhole/emblem) — img2img nguyên bộ hay ra outfit chung chung.
    _full_outfit = gt in ("auto", "generic") or gt in GARMENT_DRESS or gt in GARMENT_MULTI or gt in GARMENT_REVEAL
    # ALD 20/07/2026 - Nguyên bộ (đầm/set/auto/reveal) GIỮ full-regen denoise=1.0 (outfit bám đúng thiết kế). Đã test
    # img2img-neo-tỉ-lệ trên .165: giữ tỉ lệ tốt NHƯNG neo ảnh gốc → không thay hết đồ (giữ chân váy gốc) → LOẠI.
    preserve = TRYON_IMG2IMG and bool(target_wh) and not _full_outfit
    denoise = _tryon_denoise(gt) if preserve else 1.0
    if preserve:
        model_scale = {"class_type": "ImageScale", "inputs": {"image": ["10", 0], "width": int(target_wh[0]),
                       "height": int(target_wh[1]), "upscale_method": "lanczos", "crop": "disabled"}}
        latent = {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["32", 0]}}
    else:
        model_scale = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "megapixels": model_mp, "resolution_steps": 1}}
        latent = ({"class_type": QWEN_EMPTY_LATENT_NODE,
                   "inputs": {"width": int(target_wh[0]), "height": int(target_wh[1]), "batch_size": 1}}
                  if target_wh else
                  {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["32", 0]}})
    # #endregion
    wf = {
        "10": {"class_type": "LoadImage", "inputs": {"image": model_name}},
        "11": {"class_type": "LoadImage", "inputs": {"image": product_names[0]}},
        "20": model_scale,
        "21": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["11", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}},
        "30": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": QWEN_GGUF}},
        # ALD 12/06/2026 - khớp template chuẩn Qwen-Image-Edit: thêm ModelSamplingAuraFlow + CFGNorm (chống cháy
        # màu/da nhựa, cùng fix như create-image). KHÔNG gắn LoRA realism ở try-on để giữ độ trung thực sản phẩm.
        "33": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["30", 0], "shift": CREATE_IMAGE_SHIFT}},
        "34": {"class_type": "CFGNorm", "inputs": {"model": ["33", 0], "strength": 1.0}},
        "31": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image"}},
        "32": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
        "40": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": pos, "vae": ["32", 0], "image1": ["20", 0], "image2": ["21", 0]}},
        "41": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": neg, "vae": ["32", 0], "image1": ["20", 0], "image2": ["21", 0]}},
        "42": latent,
        "50": {"class_type": "KSampler", "inputs": {"model": ["34", 0], "positive": ["40", 0], "negative": ["41", 0], "latent_image": ["42", 0], "seed": seed, "steps": 25, "cfg": TRYON_CFG, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "60": {"class_type": "VAEDecode", "inputs": {"samples": ["50", 0], "vae": ["32", 0]}},
        "100": {"class_type": "SaveImage", "inputs": {"images": ["60", 0], "filename_prefix": prefix}},
    }
    # ALD 16/08/2026 - Góc SP thứ 2 → image3 trên CẢ 2 encoder (pos/neg cùng nhận ref, như image1/2).
    if len(product_names) > 1:
        wf["12"] = {"class_type": "LoadImage", "inputs": {"image": product_names[1]}}
        wf["22"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["12", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}}
        wf["40"]["inputs"]["image3"] = ["22", 0]
        wf["41"]["inputs"]["image3"] = ["22", 0]
    return wf

# ───────────────────────── Feet detailer (giày/dép) ─────────────────────────
# ALD 02/06/2026 - Whole-image edit KHÔNG thay nổi giày vì bàn chân quá nhỏ trong ảnh full-body (đã test).
# Fix: CROP vùng chân (res gốc) → tryon CHỈ vùng đó (giày TO trong khung → Qwen thay được) → ghép (feather)
# trở lại ảnh model gốc. Dùng cho run_tryon + run_teaser khi loại đồ = giày. Lỗi → fallback tryon toàn ảnh.
def _ff_crop(src, dst, w, h, x, y):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y}", str(dst)],
                   check=True, timeout=120)

def _ff_feather_overlay(base, ov, dst, x, y, ow, oh, feather):
    """Ghép `ov` (vùng chân đã thay giày) lên `base` tại (x,y); resize ov→ow×oh; alpha ramp 0→255 trong
    `feather` px mép trên → blend mượt (tránh seam cứng). geq lỗi → fallback overlay cứng."""
    try:
        fc = (f"[1:v]scale={ow}:{oh},format=rgba,"
              f"geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':a='if(lt(Y\\,{feather})\\,Y/{feather}*255\\,255)'[ov];"
              f"[0:v][ov]overlay={x}:{y}")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(base), "-i", str(ov),
                        "-filter_complex", fc, str(dst)], check=True, timeout=180)
    except Exception:
        fc = f"[1:v]scale={ow}:{oh}[ov];[0:v][ov]overlay={x}:{y}"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(base), "-i", str(ov),
                        "-filter_complex", fc, str(dst)], check=True, timeout=180)

def _run_shoes_detailer(job_id, m_local, p_name, garment, prefix, tmp, frac=None):
    """Crop chân ảnh model → tryon vùng chân (giày to → thay được) → ghép lại. Trả path composite,
    None nếu không khả thi (caller fallback tryon toàn ảnh). frac = tỉ lệ chiều cao từ đáy (None→env)."""
    dims = _img_size(m_local)
    if not dims:
        return None
    W0, H0 = dims
    if H0 < 256 or W0 < 64:
        return None
    frac = max(0.12, min(0.7, float(frac) if frac else TRYON_FEET_CROP))
    ch = max(64, int(round(H0 * frac / 8)) * 8)
    cy = max(0, H0 - ch)
    crop_path = os.path.join(tmp, "feetcrop.png")
    _ff_crop(m_local, crop_path, W0, ch, 0, cy)
    crop_name = comfy_upload(crop_path)
    twh = _fit_aligned(W0, ch, mp=max(2.0, TRYON_SHOES_MP), align=64)
    api_log(job_id, f"feet-detailer: crop {W0}×{ch} @y{cy} → tryon {twh[0]}×{twh[1]}", "info")
    pid = comfy_submit(build_qwen_tryon_workflow(crop_name, p_name, garment, prefix + "-feet", target_wh=twh))
    edited = comfy_fetch_output(comfy_poll(pid, job_id, deadline_sec=600), exts=IMG_EXTS)
    if not edited:
        return None
    out = os.path.join(tmp, "feet_composite.png")
    _ff_feather_overlay(m_local, edited, out, 0, cy, W0, ch, max(8, int(ch * 0.12)))
    return out

# ───────────────────────── Gemini image-edit (Nano Banana) — provider='gemini' ─────────────────────────
def _mime(path):
    return {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"
            }.get(os.path.splitext(path)[1].lower(), "image/jpeg")

def _gemini_edit(images, prompt, key, out_path, aspect_ratio=None, model=None):
    """Gemini image-edit. images=[(bytes,mime),…] + prompt → ghi ảnh ra out_path. aspect_ratio (vd '9:16')
    ÉP Gemini giữ tỉ lệ/khung ảnh gốc (không truyền → Gemini tự re-frame, cắt cụt). model = id Gemini image
    (Nano Banana = gemini-2.5-flash-image-preview / Nano Banana Pro = gemini-3-pro-image-preview); None → default.
    Raise nếu lỗi/không trả ảnh."""
    parts = [{"text": prompt}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    gcfg = {"responseModalities": ["IMAGE"]}
    if aspect_ratio:
        gcfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model or GEMINI_IMAGE_MODEL}:generateContent"
    r = requests.post(url, params={"key": key}, headers={"Content-Type": "application/json"},
                      json={"contents": [{"parts": parts}], "generationConfig": gcfg},
                      timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini API {r.status_code}: {r.text[:300]}")
    for cand in (r.json().get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                with open(out_path, "wb") as f: f.write(base64.b64decode(blob["data"]))
                return out_path
    raise RuntimeError(f"Gemini không trả ảnh: {json.dumps(r.json())[:300]}")

def _gemini_tts(script, out_mp3, key, voice=None):
    """Gemini TTS (giọng tự nhiên). text → audio PCM L16 24kHz mono → wav → mp3. Raise nếu lỗi/không audio."""
    import wave
    body = {"contents": [{"parts": [{"text": script}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or GEMINI_TTS_VOICE}}}}}
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}:generateContent",
                      params={"key": key}, headers={"Content-Type": "application/json"}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini TTS {r.status_code}: {r.text[:200]}")
    for cand in (r.json().get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                pcm = base64.b64decode(blob["data"])
                wav = out_mp3 + ".wav"
                with wave.open(wav, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, out_mp3], check=True, timeout=60)
                return out_mp3
    raise RuntimeError("Gemini TTS không trả audio")

def _omnivoice_tts(script, out_mp3, ref=None):
    """OmniVoice clone: POST text → service trả wav (giọng clone từ file mẫu) → mp3. Raise nếu lỗi.
    Contract GIỐNG _vixtts_tts (field text/ref/language). ref = path .wav service ĐỌC ĐƯỢC (cùng box, như viXTTS)."""
    if not OMNIVOICE_URL:
        raise RuntimeError("OMNIVOICE_URL chưa cấu hình")
    payload = {"text": script, "language": OMNIVOICE_LANG}
    ref = ref or OMNIVOICE_REF
    if ref: payload["ref"] = ref
    r = requests.post(f"{OMNIVOICE_URL}/tts", json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"OmniVoice {r.status_code}: {r.text[:200]}")
    wav = out_mp3 + ".omni.wav"
    with open(wav, "wb") as f: f.write(r.content)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, out_mp3], check=True, timeout=60)
    return out_mp3

def _vixtts_tts(script, out_mp3, ref=None, language="vi", temperature=None):
    """viXTTS clone (XTTS-v2 ĐA NGỮ): POST text → service trả wav (giọng clone từ file mẫu) → mp3.
    language='en' + ref = giọng gốc video → CROSS-LINGUAL: đọc tiếng Anh BẰNG chất giọng người trong video.
    temperature (Trụ C): cao = biểu cảm/biến thiên hơn cho cảm xúc mạnh; None → để service tự dùng default 0.7."""
    if not VIXTTS_URL:
        raise RuntimeError("VIXTTS_URL chưa cấu hình")
    payload = {"text": script, "language": language or "vi"}
    if temperature is not None:
        try: payload["temperature"] = max(0.3, min(0.95, float(temperature)))
        except (TypeError, ValueError): pass
    ref = ref or VIXTTS_REF
    if ref: payload["ref"] = ref
    r = requests.post(f"{VIXTTS_URL}/tts", json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"viXTTS {r.status_code}: {r.text[:200]}")
    wav = out_mp3 + ".vix.wav"
    with open(wav, "wb") as f: f.write(r.content)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, out_mp3], check=True, timeout=60)
    return out_mp3

def _gemini_tryon_prompt(gt, extra=None):
    # ALD 01/07/2026 - chèn "Ghi chú thêm" của user (ưu tiên cao) trước câu chốt. Gemini hiểu tốt nên đặt cuối vẫn ăn.
    # ALD 04/07/2026 - KHÓA MẶT đặt đầu prompt (đồng bộ fix "tryon đổi mặt" bên _qwen_tryon_prompts).
    return ("CRITICAL: the person's face and facial identity must remain EXACTLY identical to image 1 — same facial "
            "structure, eyes, nose, lips, jawline, skin tone, makeup and expression; never beautify, reshape, swap "
            "or regenerate the face. ") + _gemini_tryon_prompt_base(gt) + _tryon_extra_clause(extra)

def _gemini_tryon_prompt_base(gt):
    label = GARMENT_LABEL.get(gt, "garment")
    if gt in ("auto", "generic"):
        # ALD 28/06/2026 - AUTO (generic): thay nguyên bộ từ ảnh 2, kệ loại đồ — ảnh sản phẩm tự quyết.
        # ALD 10/07/2026 - đồng bộ với _qwen_tryon_prompts: AUTO = TOÀN BỘ đồ thời trang (quần áo + tất/vớ đùi +
        # giày + phụ kiện trang sức/mũ/thắt lưng/túi/kính), không bịa món ảnh SP không có.
        return ("Image 1 is a person; image 2 is a fashion product photo. Edit image 1: dress the person in EXACTLY the "
                "complete fashion look shown in image 2 — reproduce EVERY fashion item present: clothing (dress, two-piece set, "
                "top, bottom, swimwear, straps, belts, trims), hosiery (thigh-high stockings, tights, socks, garter belts), "
                "footwear (shoes/heels/boots on both feet), and accessories (jewelry, hat or headband worn without changing the "
                "hairstyle, gloves, scarf, glasses, handbag) — each with the exact color, pattern, fabric and cut. "
                "Do not invent items that are not in image 2. Fully remove the person's original clothing, hosiery, footwear and "
                "accessories wherever the new look covers or replaces them; keep nothing of the original showing "
                "through. Keep identity, face, hair, body, pose and background identical. Photorealistic; output only the edited photo.")
    if gt in GARMENT_SHOES:
        return ("Image 1 is a photo of a person; image 2 is a product photo of footwear. Edit image 1: completely "
                "replace the shoes on the person's feet with the EXACT footwear from image 2 (same color, material, "
                "sole, heel and design), on BOTH feet. Keep the person's face, hair, body, clothing, pose and the "
                "background EXACTLY the same. Photorealistic; output only the edited photo.")
    if gt in GARMENT_MULTI:
        return ("Image 1 is a person; image 2 is a clothing set. Edit image 1: dress the person in the COMPLETE set "
                "from image 2 (every piece), fully removing their original outfit. Keep identity, pose and background "
                "identical. Photorealistic; output only the edited photo.")
    return (f"Image 1 is a person; image 2 is a {label}. Edit image 1: replace the person's {label} with the {label} "
            f"from image 2 (exact color, pattern, fabric, cut). Keep other garments, identity, pose and background "
            f"identical. Photorealistic; output only the edited photo.")

QWEN_EDIT_MAX_REFS = int(os.environ.get("QWEN_EDIT_MAX_REFS", "3"))
CREATE_IMAGE_CFG = float(os.environ.get("CREATE_IMAGE_CFG", "2.5"))  # ALD 11/06/2026 - 3.2→2.5: CFG cao đốt màu, da bóng kiểu render; 2.5 = mặc định template Qwen-Image(-Edit) của ComfyUI
# ALD 13/06/2026 - steps render chính. 25→30 (mặc định): thêm bước giúp chi tiết tay/mặt/vải ổn hơn (đổi lại ~+20% thời gian).
CREATE_IMAGE_STEPS = int(os.environ.get("CREATE_IMAGE_STEPS", "30"))
# #region ALD 12/06/2026 - chống "hoạt hình" tầng MODEL (prompt/CFG chưa đủ): LoRA realism flymy
# (file đã tải sẵn vào loras/ trên VPS; "" = tắt) + shift AuraFlow theo template chuẩn Qwen của ComfyUI.
CREATE_IMAGE_LORA = os.environ.get("CREATE_IMAGE_LORA", "flymy_realism.safetensors").strip()
# 12/06: 0.9 → 1.0 theo yêu cầu "thật hơn nữa" sau khi soi output; nếu mặt mẫu model-standard trôi nét thì hạ env về 0.7-0.9.
CREATE_IMAGE_LORA_STRENGTH = float(os.environ.get("CREATE_IMAGE_LORA_STRENGTH", "1.0"))
CREATE_IMAGE_SHIFT = float(os.environ.get("CREATE_IMAGE_SHIFT", "3.0"))
# 12/06: render thẳng ở độ phân giải native của Qwen-Image (1328²≈1.76MP): dưới mức này da/texture mất chi tiết
# (nguồn cảm giác "mịn AI"), trên mức này dễ vỡ hình. Dùng cho cả _qwen_dims lẫn latent nhánh ref.
CREATE_IMAGE_MP = float(os.environ.get("CREATE_IMAGE_MP", "1.75"))
# #endregion

# #region ALD 11/06/2026 - Nền hay bị "nhựa/cartoon": khối realism cũ chỉ tả NGƯỜI (da/vải/tóc), Qwen tự đổ
# ra phông studio mịn + bokeh CGI nên bối cảnh giả. Thêm câu ép realism RIÊNG cho NỀN (vật liệu thật có hao mòn,
# ánh sáng đồng nhất với chủ thể, DOF quang học chứ không "vẽ", bóng tiếp đất để người dính vào cảnh).
# SỬA LẦN 2 (cùng ngày): bản đầu ép "muted/desaturated/flat contrast" lên TOÀN ảnh + haze → da người tái, nhìn
# giả. Bỏ hẳn ép màu toàn ảnh + bỏ haze; thêm câu khóa CHỦ THỂ giữ màu da/độ nét tự nhiên, realism nền không
# được lan sang người.
# SỬA LẦN 3 (cùng ngày): vẫn ngả hoạt hình. Positive cũ đầy chữ phủ định ("not AI art / not illustration /
# never CGI / never painted…") — encoder vẫn KÍCH HOẠT các khái niệm bị phủ định trong POSITIVE (negation leak)
# → tự kéo ảnh về đúng thứ cần tránh. Viết lại THUẦN KHẲNG ĐỊNH (chỉ tả ảnh chụp thật); toàn bộ từ cấm đã nằm
# sẵn bên PHOTO_REALISM_NEGATIVE. Kèm hạ CREATE_IMAGE_CFG 3.2→2.5 (xem trên) — CFG cao đốt màu, da bóng nhựa.
# SỬA LẦN 4 (12/06/2026): prompt/CFG chưa đủ → đánh tầng MODEL: LoRA flymy realism (trigger bắt buộc
# "Realism" — câu mở đầu dưới đây) + ModelSamplingAuraFlow/CFGNorm trong build_qwen_create_workflow.
# SỬA LẦN 5 (12/06/2026): BỎ câu điều kiện "If the scene is street or night → Vietnamese alley/scooters/
# shutter doors": cùng lỗi leak như chữ phủ định — token "alley/plants/shutter doors" nằm trong positive thì
# kích hoạt LUÔN, user xin "bedroom window" vẫn ra hẻm phố (đã bắt tận tay 12/06). Việc chặn biển hiệu đã có
# đủ bên PHOTO_REALISM_NEGATIVE (shop signs, readable text, Chinese characters…), không cần câu này.
PHOTO_REALISM_POSITIVE = (
    "Super Realism. Authentic candid photograph taken with a real handheld camera, straight out of camera. "
    "Natural human skin with visible pores, tiny blemishes, slight facial asymmetry, fine stray hair strands, "
    "real shadows and natural subsurface skin tones; the subject's skin tone, facial detail, sharpness and colors "
    "stay natural, healthy and true to life, exactly like a real living person in a normal photograph. "
    "Real fabric with natural wrinkles, imperfect folds and visible stitching; natural body weight, real-world "
    "anatomy and natural lens perspective. Subtle ISO grain, mild sensor noise, slightly imperfect exposure and "
    "white balance, documentary smartphone or DSLR look. "
    "The environment is a real photographed place with the same camera realism as the subject: real materials "
    "with natural wear and small imperfections, one consistent light direction shared by subject and scene, true "
    "optical depth-of-field from a real lens, and grounded contact shadows so the person sits naturally inside the scene."
)
# #endregion

# #region ALD 11/06/2026 - thêm negative chống NỀN giả (phông gradient studio, matte painting, set CGI/game,
# diorama, cắt-dán) — đúng nguồn cảm giác "nền hoạt hình". SỬA LẦN 2: bỏ cụm màu TOÀN-ảnh (HDR/oversaturated/
# overprocessed/glossy render…) vì nó đè lên cả da người → người tái, nhìn giả; chỉ giữ từ khóa khoanh vùng nền.
PHOTO_REALISM_NEGATIVE = (
    "cartoon, anime, manga, illustration, drawing, painting, sketch, cel shading, comic, stylized, digital art, concept art, "
    "3d render, cgi, unreal engine, octane render, game character, plastic, plastic skin, wax skin, wax figure, porcelain skin, "
    "porcelain doll, doll, toy, figurine, mannequin, airbrushed skin, over-smoothed skin, poreless skin, perfect skin, beauty filter, "
    "faceapp, synthetic skin, fake skin, AI face, CGI face, glossy eyes, oversized eyes, fake hair, claymation, rubber skin, "
    "cyberpunk, neon commercial street, Hong Kong street, Chinatown, Taiwan street, shop signs, neon signs, signboard, banner, "
    "billboard, poster, menu board, street sign, logo, brand mark, watermark, signature, readable text, words, letters, numbers, "
    "Chinese characters, Hanzi, kanji, hangul, Vietnamese text, Vietnamese diacritics, blurry, lowres, deformed, distorted, "
    "extra limbs, bad hands, malformed hands, mutated hands, deformed hands, mangled hands, fused fingers, extra fingers, "
    "missing fingers, too many fingers, deformed fingers, twisted fingers, extra arms, deformed object, melted object, "
    "distorted object, mangled object, artifacts, child, teenager, teen, underage, baby face, childlike, flat chest, small bust, thin body, "
    "narrow hips, boyish body, generic cute face, "
    "smooth gradient backdrop, studio gradient background, seamless paper backdrop, plain studio backdrop, matte painting, "
    "painted background, painterly background, CGI environment, 3d set, video game scenery, diorama, miniature set, plastic "
    "environment, fake background, sticker cutout, pasted subject, floating subject, flat backdrop, smeared bokeh, "
    "oversaturated background"
)
# #endregion

def _force_photo_realism_prompt(prompt):
    base = (prompt or "").strip() or "A high quality photorealistic image."
    return f"{base}\n{PHOTO_REALISM_POSITIVE}"

# #region ALD 13/06/2026 - DÁNG người mẫu mặc định cho create-image (yêu cầu user): ngực to, eo thon, mông to,
# chân dài, da trắng — NHƯNG GIỮ chất thật (lỗ chân lông + ánh sáng tự nhiên ở PHOTO_REALISM_POSITIVE, KHÔNG
# bóng bẩy/airbrushed kiểu glamour). Chỉ áp khi prompt tả PHỤ NỮ (tránh phá ảnh sản phẩm/nam). Tắt: CREATE_IMAGE_BEAUTY_BODY=0.
CREATE_IMAGE_BEAUTY_BODY = os.environ.get("CREATE_IMAGE_BEAUTY_BODY", "1").strip().lower() not in ("0", "false", "no", "off")
BEAUTY_BODY_POSITIVE = (
    "She has a stunning hourglass figure: a large full bust, a slim toned waist, wide curvy hips, round full "
    "buttocks and long slender legs, with fair porcelain skin — kept natural and realistic with visible skin pores "
    "and natural lighting, not glossy or airbrushed."
)

def _describes_woman(text):
    import re as _re
    return bool(_re.search(r"\b(woman|women|girl|female|lady|she|her|bikini|lingerie|bust|breast|cleavage|model|girlfriend|waifu)\b", text or "", _re.I))

def _vcount(idx, n):
    # ALD 13/06/2026 - hậu tố " i/N" CHỈ khi nhiều biến thể; 1 ảnh thì bỏ (khỏi hiện "1/1" vô nghĩa).
    return f" {idx + 1}/{n}" if n > 1 else ""

def _prompt_template(text, **values):
    out = str(text or "")
    for k, v in values.items():
        val = "" if v is None else str(v)
        out = out.replace("{{ " + k + " }}", val).replace("{{" + k + "}}", val)
    return out.strip()

def build_qwen_create_workflow(image_names, prompt, prefix, width=1024, height=1024, force_size=False, seed=None, negative_prompt=None, realism=True, target_mp=None, denoise=1.0):
    # force_size=True (user chọn tỉ lệ) → render đúng W×H dù có ảnh ref (ảnh thành điều kiện
    # qua text-encoder, latent = empty W×H). force_size=False + có ảnh → giữ tỉ lệ ảnh gốc (VAEEncode).
    # ALD 01/07/2026 - realism=False (node edit-image): dùng prompt THÔ, KHÔNG ép câu photo-realism —
    # tránh ép ảnh thật lên edit kiểu cartoon/stylize/vẽ tay. create-image vẫn realism=True như cũ.
    imgs = [f for f in (image_names or []) if f][:QWEN_EDIT_MAX_REFS]
    pos = _force_photo_realism_prompt(prompt) if realism else (prompt or "")
    neg = (negative_prompt or PHOTO_REALISM_NEGATIVE).strip()
    seed = int(seed if seed is not None else abs(hash(prefix))) % (2 ** 31)
    wf, scale_ids = {}, []
    for i, fn in enumerate(imgs):
        lid, sid = str(10 + i), str(20 + i)
        wf[lid] = {"class_type": "LoadImage", "inputs": {"image": fn}}
        wf[sid] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [lid, 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}}
        scale_ids.append(sid)
    wf["30"] = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": QWEN_GGUF}}
    # #region ALD 12/06/2026 - khớp template chuẩn Qwen-Image(-Edit) của ComfyUI: thiếu ModelSamplingAuraFlow
    # (shift sigma) + CFGNorm (chống cháy màu) là một nguồn ra ảnh "render/hoạt hình"; kèm LoRA realism (tùy chọn).
    model_ref = ["30", 0]
    if CREATE_IMAGE_LORA:
        wf["33"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model_ref, "lora_name": CREATE_IMAGE_LORA, "strength_model": CREATE_IMAGE_LORA_STRENGTH}}
        model_ref = ["33", 0]
    wf["34"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": model_ref, "shift": CREATE_IMAGE_SHIFT}}
    wf["35"] = {"class_type": "CFGNorm", "inputs": {"model": ["34", 0], "strength": 1.0}}
    # #endregion
    wf["31"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image"}}
    wf["32"] = {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}}
    enc = {f"image{idx + 1}": [sid, 0] for idx, sid in enumerate(scale_ids)}
    wf["40"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": pos, "vae": ["32", 0], **enc}}
    wf["41"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": neg, "vae": ["32", 0], **enc}}
    if scale_ids and not force_size:
        # ALD 12/06/2026 - denoise=1.0 nên latent chỉ quyết định SIZE/tỉ lệ output (nội dung bị renoise sạch):
        # trước đây lấy thẳng ref đã scale 1MP → output kẹt 1MP dù muốn nét hơn. Scale RIÊNG một bản lên
        # CREATE_IMAGE_MP cho latent; ref conditioning (enc) vẫn giữ 1MP theo template chuẩn để đỡ VRAM.
        wf["43"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [scale_ids[0], 0], "upscale_method": "lanczos", "megapixels": (float(target_mp) if target_mp else CREATE_IMAGE_MP), "resolution_steps": 1}}
        wf["42"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["43", 0], "vae": ["32", 0]}}
    else:
        w = max(256, (int(width) // 16) * 16); h = max(256, (int(height) // 16) * 16)
        wf["42"] = {"class_type": QWEN_EMPTY_LATENT_NODE, "inputs": {"width": w, "height": h, "batch_size": 1}}
    # ALD 03/07/2026 - denoise tham số hoá (mặc định 1.0 như cũ): edit-image GHÉP có thể hạ (QWEN_COMBINE_DENOISE)
    # để neo bố cục ảnh gốc chặt hơn nếu prompt-EDIT vẫn chưa đủ giữ (bài học tryon clean denoise 0.30).
    wf["50"] = {"class_type": "KSampler", "inputs": {"model": ["35", 0], "positive": ["40", 0], "negative": ["41", 0], "latent_image": ["42", 0], "seed": seed, "steps": CREATE_IMAGE_STEPS, "cfg": CREATE_IMAGE_CFG, "sampler_name": "euler", "scheduler": "simple", "denoise": max(0.05, min(1.0, float(denoise)))}}
    wf["60"] = {"class_type": "VAEDecode", "inputs": {"samples": ["50", 0], "vae": ["32", 0]}}
    wf["100"] = {"class_type": "SaveImage", "inputs": {"images": ["60", 0], "filename_prefix": prefix}}
    return wf

# #region ALD 13/06/2026 - Flux.1 + SD3.5 (self-host, FP8) — CHỈ text→image (KHÔNG ref/edit). Graph LẤY ĐÚNG từ
# template chính thức ComfyUI 0.22.0 (gói comfyui_workflow_templates):
#   • Flux: flux_schnell.json + subgraph "Text to Image (Flux.1 Dev)" trong flux_dev_full_text_to_image.json →
#     CheckpointLoaderSimple (all-in-one fp8) → FluxGuidance(pos) + ConditioningZeroOut(neg) → EmptySD3LatentImage
#     → KSampler(euler/simple, cfg=1, denoise=1) → VAEDecode → SaveImage. (Flux KHÔNG có negative thật → CFG=1.)
#   • SD3.5: sd3.5_simple_example.json → CheckpointLoaderSimple (Large/Medium all-in-one) HOẶC ckpt+TripleCLIPLoader
#     (Turbo) → 2×CLIPTextEncode(pos/neg) → EmptySD3LatentImage → KSampler(euler/sgm_uniform) → VAEDecode → SaveImage.
# Cùng quy ước SaveImage filename_prefix như build_qwen_create_workflow → comfy_poll/comfy_fetch_output dùng chung.
def build_flux_create_workflow(prompt, prefix, width=1024, height=1024, seed=None, negative_prompt=None, schnell=False):
    """Graph Flux.1 (all-in-one fp8 checkpoint) text→image. schnell=True → bản 4 bước nhanh; mặc định Dev (~24 bước).
    negative_prompt KHÔNG dùng (Flux distilled CFG=1, không có negative thật) — nhận tham số cho đồng nhất chữ ký."""
    pos = _force_photo_realism_prompt(prompt)
    seed = int(seed if seed is not None else abs(hash(prefix))) % (2 ** 31)
    w = max(256, (int(width) // 16) * 16); h = max(256, (int(height) // 16) * 16)
    steps = FLUX_SCHNELL_STEPS if schnell else FLUX_DEV_STEPS
    ckpt = FLUX_SCHNELL_FP8 if schnell else FLUX_DEV_FP8
    wf = {}
    wf["10"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}
    wf["20"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": pos}}
    # FluxGuidance = guidance distilled (KHÁC CFG); positive đi qua node này. Negative = ConditioningZeroOut(positive).
    wf["21"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["20", 0], "guidance": FLUX_GUIDANCE}}
    wf["22"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["20", 0]}}
    wf["30"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf["40"] = {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["21", 0], "negative": ["22", 0], "latent_image": ["30", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
    wf["50"] = {"class_type": "VAEDecode", "inputs": {"samples": ["40", 0], "vae": ["10", 2]}}
    wf["100"] = {"class_type": "SaveImage", "inputs": {"images": ["50", 0], "filename_prefix": prefix}}
    return wf

def build_sd35_create_workflow(prompt, prefix, width=1024, height=1024, seed=None, negative_prompt=None, turbo=False, medium=False):
    """Graph SD3.5 text→image. medium=True → bản nhẹ; turbo=True → bản 4 bước (checkpoint stabilityai GATED + text-enc
    rời qua TripleCLIPLoader). Large/Medium = all-in-one fp8 (CheckpointLoaderSimple gói sẵn CLIP/T5). SD3.5 CÓ negative."""
    pos = _force_photo_realism_prompt(prompt)
    neg = (negative_prompt or PHOTO_REALISM_NEGATIVE).strip()
    seed = int(seed if seed is not None else abs(hash(prefix))) % (2 ** 31)
    w = max(256, (int(width) // 16) * 16); h = max(256, (int(height) // 16) * 16)
    if turbo:
        steps, cfg, ckpt = SD35_TURBO_STEPS, SD35_TURBO_CFG, SD35_TURBO_CKPT
    elif medium:
        steps, cfg, ckpt = SD35_MEDIUM_STEPS, SD35_MEDIUM_CFG, SD35_MEDIUM_FP8
    else:
        steps, cfg, ckpt = SD35_LARGE_STEPS, SD35_LARGE_CFG, SD35_LARGE_FP8
    wf = {}
    wf["10"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}
    # Turbo checkpoint KHÔNG gói text-encoder → load rời (clip_g + clip_l + t5xxl). Large/Medium đã gói → dùng CLIP của ckpt.
    if turbo:
        wf["11"] = {"class_type": "TripleCLIPLoader", "inputs": {"clip_name1": SD35_CLIP_G, "clip_name2": SD35_CLIP_L, "clip_name3": SD35_T5XXL}}
        clip_ref = ["11", 0]
    else:
        clip_ref = ["10", 1]
    wf["20"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": pos}}
    wf["21"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": neg}}
    wf["30"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf["40"] = {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["20", 0], "negative": ["21", 0], "latent_image": ["30", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "sgm_uniform", "denoise": 1.0}}
    wf["50"] = {"class_type": "VAEDecode", "inputs": {"samples": ["40", 0], "vae": ["10", 2]}}
    wf["100"] = {"class_type": "SaveImage", "inputs": {"images": ["50", 0], "filename_prefix": prefix}}
    return wf

def build_selfhost_create_workflow(model, prompt, prefix, width, height, seed=None, negative_prompt=None):
    """Dispatch model self-host (≠ qwen-edit) → build_flux/sd35. Trả None nếu model lạ (caller fallback Qwen)."""
    m = str(model or "").lower()
    if m == "flux-dev":
        return build_flux_create_workflow(prompt, prefix, width, height, seed, negative_prompt, schnell=False)
    if m == "flux-schnell":
        return build_flux_create_workflow(prompt, prefix, width, height, seed, negative_prompt, schnell=True)
    if m == "sd35-large":
        return build_sd35_create_workflow(prompt, prefix, width, height, seed, negative_prompt)
    if m == "sd35-medium":
        return build_sd35_create_workflow(prompt, prefix, width, height, seed, negative_prompt, medium=True)
    if m == "sd35-large-turbo":
        return build_sd35_create_workflow(prompt, prefix, width, height, seed, negative_prompt, turbo=True)
    return None
# #endregion

# ALD 14/06/2026 - GỠ "Xử lý hậu kỳ" (detailer vá mặt + refine hi-res toàn ảnh): theo yêu cầu bỏ hẳn chức năng
# postProcess. create-image giờ = render Qwen (+ refineSteps nếu user bật) → upload thẳng, KHÔNG còn detailer.

def _qwen_ref_window(image_names, offset=0):
    refs = [f for f in (image_names or []) if f]
    if len(refs) <= QWEN_EDIT_MAX_REFS:
        return refs
    primary = [f for f in refs if _is_primary_model_standard_ref(f)]
    if primary:
        pinned = primary[:1]
        rest = [f for f in refs if f not in pinned]
        offset = int(offset or 0) % len(rest) if rest else 0
        rest = rest[offset:] + rest[:offset]
        return pinned + rest[:max(0, QWEN_EDIT_MAX_REFS - len(pinned))]
    offset = int(offset or 0) % len(refs)
    refs = refs[offset:] + refs[:offset]
    return refs[:QWEN_EDIT_MAX_REFS]

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MODEL_STANDARD_DIR = os.environ.get(
    "CREATE_IMAGE_MODEL_STANDARD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "model-standards"),
)

def _model_standard_preset(params):
    if not _use_default_model_standard(params):
        return "off"
    v = params.get("modelStandardPreset", params.get("model_standard_preset", "female"))
    v = str(v or "female").strip().lower()
    return v if v in ("female", "male", "custom") else "female"

def _default_model_standard_paths(params=None):
    preset = _model_standard_preset(params or {})
    try:
        roots = []
        if preset != "off":
            roots.append(os.path.join(MODEL_STANDARD_DIR, preset))
        if preset == "female":
            roots.append(MODEL_STANDARD_DIR)
        files = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for fn in sorted(os.listdir(root)):
                if fn.lower().endswith(IMG_EXTS):
                    files.append(os.path.join(root, fn))
        files = [p for p in files if os.path.exists(p)]
        files.sort(key=lambda p: (0 if _is_primary_model_standard_ref(p) else 1, os.path.basename(p).lower()))
        return files[:6]
    except Exception:
        return []

def _is_primary_model_standard_ref(path):
    name = os.path.basename(str(path)).lower()
    return name.startswith("model-standard-00") or "reference-sheet" in name or "beauty-standard" in name

def _use_default_model_standard(params):
    v = params.get("useModelStandard", params.get("use_model_standard", None))
    if v is None:
        return True
    return str(v).strip().lower() not in ("0", "false", "no", "off", "none")

# ALD 14/06/2026 - GỠ chế độ "Ghép mặt 2 bước" (_use_face_swap + MS_FACE_SWAP_PROMPT): bước swap mặt gây lỗi mặt
# đôi/mặt ma do không blend được. Model-standard giờ chỉ chạy 1-step dùng NGUYÊN ảnh model làm ref danh tính.

def _ref_override_rules(base):
    """ALD 14/06/2026 - Trích NGUYÊN VĂN câu user tả TÓC / PHỤ KIỆN / BỐI CẢNH rồi dựng lệnh MỆNH LỆNH đặt TRÊN
    CÙNG, ép Qwen-Edit override image-conditioning của ảnh ref (tóc/nền/phụ kiện của ref hay đè chết mô tả + negative
    của user). Trước đây chỉ path Model mẫu (_model_standard_prompt) có; nay tách dùng CHUNG cho cả nhánh ref user
    đính kèm (user báo: đính ảnh + tả bối cảnh phòng ngủ → vẫn ra nền của ảnh ref). Trả chuỗi (rỗng nếu không tả)."""
    import re as _re
    _sents = [s.strip() for s in _re.split(r"[.\n]+", (base or "").strip()) if s.strip()]
    _hair_kw = ("hair", "haircut", "hairstyle", "bald", "bob", "pixie", "ponytail", "braid", "bangs", "fringe",
                "bun ", "updo", "tóc")
    _hair_txt = "; ".join(s for s in _sents if any(k in s.lower() for k in _hair_kw))[:300]
    hair_cmd = (
        "CRITICAL HAIR RULE — the hairstyle, hair length and hair color visible in the attached reference image are "
        "WRONG and must be COMPLETELY REPLACED. COMMAND: restyle this woman's hair to exactly this: \"" + _hair_txt +
        "\". Never keep or blend the reference hairstyle.\n\n"
        if _hair_txt else ""
    )
    _acc_re = _re.compile(
        r"\b(glasses|eyeglasses|sunglasses|spectacles|eyewear|hat|cap|beret|beanie|earrings?|necklace|choker|"
        r"headband|hairband|scarf|piercings?|jewell?ery|bracelets?|bangles?|anklets?)\b"
        r"|kính|mũ|nón|khuyên tai|bông tai|vòng cổ|dây chuyền|vòng tay|lắc tay|lắc chân|băng đô|khăn",
        _re.IGNORECASE)
    _acc_txt = "; ".join(s for s in _sents if _acc_re.search(s))[:300]
    acc_cmd = (
        "CRITICAL ACCESSORY RULE — the reference face wears no accessories; that is irrelevant. COMMAND: she MUST "
        "wear the accessories exactly as described here: \"" + _acc_txt + "\".\n\n"
        if _acc_txt else ""
    )
    _scene_re = _re.compile(
        r"\b(environment|background|backdrop|scene|location|setting|indoors?|outdoors?|rooftop|balcony|terrace|"
        r"beach|seaside|street|alley|bedroom|living room|room|kitchen|office|studio|park|garden|forest|mountain|"
        r"city|cafe|café|restaurant|bar|pool|window|sunset|sunrise|golden hour|night|daylight|lighting|light)\b"
        r"|bối cảnh|phông nền|ngoài trời|trong nhà|sân thượng|ban công|bãi biển|đường phố|con hẻm|phòng ngủ|"
        r"căn phòng|quán|công viên|hoàng hôn|bình minh|ban đêm|ánh sáng|ánh đèn", _re.IGNORECASE)
    _scene_txt = "; ".join(s for s in _sents if _scene_re.search(s))[:400]
    scene_cmd = (
        "CRITICAL LOCATION RULE — the scene, location, background and lighting MUST be exactly as described here: \""
        + _scene_txt + "\". Build the entire environment from this description only, never substitute a different "
        "kind of place, and NEVER keep the background, room or setting from the reference image.\n\n"
        if _scene_txt else ""
    )
    # ALD 14/06/2026 - CRITICAL OUTFIT: ảnh ref mặc đồ riêng (vd áo phông in chữ "STRANGER THINGS") → Qwen-Edit chép
    # NGUYÊN áo + logo + chữ, bỏ qua mô tả trang phục của user. Trích câu tả ĐỒ, ra lệnh thay HẲN + cấm giữ
    # print/logo/text/màu của áo ref (cùng dạng các rule trên — đã chứng minh ăn cho tóc/phụ kiện/bối cảnh).
    _outfit_re = _re.compile(
        r"\b(outfit|clothing|clothes|wearing|wears|dress|gown|top|shirt|t-?shirt|tee|blouse|tank|crop ?top|"
        r"off[- ]?the[- ]?shoulder|sleeveless|sleeve|neckline|collar|zipper|hoodie|jacket|coat|sweater|cardigan|"
        r"skirt|shorts|jeans|trousers|pants|leggings|lingerie|swimsuit|bikini|bra|costume|uniform|suit|ao dai|"
        r"áo|váy|đầm|quần)\b", _re.IGNORECASE)
    _outfit_txt = "; ".join(s for s in _sents if _outfit_re.search(s))[:400]
    outfit_cmd = (
        "CRITICAL OUTFIT RULE — the clothing worn in the attached reference image is WRONG and must be COMPLETELY "
        "REPLACED. COMMAND: dress her in exactly this: \"" + _outfit_txt + "\". Do not keep, reuse or blend any "
        "garment, print, logo, lettering, graphic or color from the reference clothing.\n\n"
        if _outfit_txt else ""
    )
    return scene_cmd + outfit_cmd + hair_cmd + acc_cmd

def _model_standard_prompt(prompt, params=None):
    base = (prompt or "").strip() or "A high quality, photorealistic, detailed image."
    params = params or {}
    custom = str(params.get("modelStandardPrompt") or params.get("model_standard_prompt") or "").strip()
    preset = _model_standard_preset(params)
    if preset == "custom" and custom:
        style = custom
    elif preset == "male":
        style = (
          "refined natural East Asian male facial beauty: a handsome face with a clean V-line jaw, expressive eyes, "
          "straight brows, refined nose, healthy lips, a confident subtle smile, clear fair healthy skin and clean modern grooming. "
        )
    else:
        # ALD 10/06/2026 - style = THAM KHẢO NÉT ĐẸP của KHUÔN MẶT thôi (KHÔNG copy identity, KHÔNG ép dáng người).
        # Body + tóc + trang phục + pose + bối cảnh đều theo PROMPT người dùng. Trước đây ép cả dáng hourglass + tóc
        # xõa + váy nên lấn át prompt; giờ chỉ mô tả tiêu chuẩn ĐẸP của gương mặt để nâng độ xinh, còn lại nghe prompt.
        style = (
          "refined natural East Asian female facial beauty: a very beautiful delicate face with balanced mature adult "
          "proportions, soft V-line jaw, bright almond eyes with subtle aegyo-sal, natural straight brows, small refined "
          "nose, soft glossy lips, clear fair skin with real texture and visible pores, a gentle charming expression and tasteful natural makeup. "
        )
    # ALD 14/06/2026 - rule MỆNH LỆNH tả TÓC/ĐỒ/PHỤ KIỆN/BỐI CẢNH (ép Qwen-Edit khỏi chép ref) giờ tách dùng CHUNG
    # ở _ref_override_rules (xài chung với path ref user đính kèm) + đã bổ sung CRITICAL OUTFIT RULE — xem helper trên.
    # #region ALD 10/06/2026 - Hướng A: ref ĐÍNH KÈM là 1 ảnh CHÂN DUNG gương mặt ĐẸP. Prompt này bảo Qwen: lấy
    # GƯƠNG MẶT (danh tính) từ ảnh đó cho xinh, còn pose/dáng/tóc/đồ/cảnh/nền đều theo mô tả end-user. Bỏ phần nền/
    # dáng/pose của ảnh ref (ảnh ref đã crop sát mặt nên ít gì để rỉ). `style` chỉ là hint mô tả vẻ đẹp gương mặt.
    # ALD 12/06/2026 - (1) câu thay-biển-hiệu cũ liệt kê "blank panels, walls, plants, curtains, windows" → LEAK:
    # user xin rooftop hoàng hôn vẫn ra phòng rèm trắng (cùng họ lỗi câu "Vietnamese alley"); viết lại không nêu
    # vật thể. (2) thêm camera angle + gaze direction vào PRIMARY INSTRUCTION — selfie góc cao/mắt nhìn lệch bị
    # ref nhìn-thẳng đè (bắt tận tay 12/06).
    return (
        _ref_override_rules(base)
        + "PRIMARY INSTRUCTION — take the pose, hairstyle, outfit, expression mood, framing, "
        "camera angle, gaze direction, scene, location and background strictly from the user's description below:\n"
        + base
        + "\n\nUse the attached reference image as the SAME PERSON — keep her face and facial identity "
          "(" + style + ") AND her body type, figure, proportions and skin tone, so the result is clearly the same "
          "woman. Adapt the head angle, gaze direction, expression and body pose so she naturally fits the described "
          "pose and viewpoint, but keep her the same attractive person with the same face and figure. "
          "Do NOT take anything else from the reference image — ignore its background, framing, pose, hairstyle "
          "and clothing; those all come entirely from the description above. "
          "Keep real camera realism, real skin pores, real fabric wrinkles, natural facial asymmetry, normal shadows and "
          "photographic depth of field. "
          "If the description above does not specify a location, use a simple clean sign-free setting. "
          "Do not render banners, billboards, shop signs, neon signs, posters, menus, logos, readable text, Chinese "
          "characters, Vietnamese text or Vietnamese diacritics anywhere in the image; any surface that would normally "
          "carry signs or text must stay blank or softly out of focus instead."
    )
    # #endregion

# ───────────────────────── pipelines ─────────────────────────
def run_motion(job):
    job_id = job["id"]; inputs = job.get("inputs", {})
    # ALD 03/07/2026 - chốt cờ "user ép width/height tay" TRƯỚC normalize (_normalize_motion_params mutate dict
    # tại chỗ rồi tự điền width/height từ preset → sau normalize hết phân biệt được). Dùng cho FIT DRIVER bên dưới.
    _raw_wh_forced = bool((job.get("params") or {}).get("width") or (job.get("params") or {}).get("height"))
    params = _normalize_motion_params(job.get("params", {}))
    _audio_mode = str(params.get("audioMode", params.get("audio_mode", "")) or "").strip().lower()
    _silent_audio = _audio_mode in ("silent", "mute", "muted", "none", "off")
    _replacement_audio = _audio_mode in ("replacement", "replace", "custom")
    if not _audio_mode:
        _replacement_audio = str(params.get("audioPassthrough", True)).lower() in ("0", "false", "no", "off")
        _silent_audio = False
    # debug logs
    api_log(job_id, f"DEBUG MOTION INPUTS: {json.dumps(inputs)} | PARAMS: {json.dumps(params)}", "info")
    api_log(job_id, f"Motion tune: profile={params.get('render_profile', 'fast')} face_strength={_motion_float(params, 'face_strength', 'faceStrength', default=0.7)} pose_strength={_motion_float(params, 'pose_strength', 'poseStrength', default=0.7)} clip_strength={_motion_float(params, 'clip_strength', 'clipStrength', default=1.35)} body_proportion_lock={_motion_bool(params, 'body_proportion_lock', 'bodyProportionLock', default=True)} face_source={params.get('face_source') or params.get('faceSource') or 'driver(default)'} face_crop={params.get('faceCropMode') or params.get('face_crop_mode') or os.environ.get('MOTION_FACE_CROP', 'vitpose')} pose_retarget={str(params.get('poseRetarget', params.get('pose_retarget', os.environ.get('MOTION_POSE_RETARGET', '0')))).strip().lower() in ('1','true','yes','on')}", "info")
    ref_key = inputs.get("ref") or inputs.get("image")
    motion_key = inputs.get("motion")
    # #region ALD 11/06/2026 - provider='huggingface' → IMAGE-TO-VIDEO (Wan 2.2 i2v qua fal-ai router), KHÔNG
    # phải motion-transfer: chỉ cần ảnh ref + prompt; cổng motion video (nếu nối) bị BỎ QUA — log rõ. Self-host
    # (mặc định) giữ nguyên Wan Animate (cần cả ref + motion).
    if str(params.get("provider") or "").lower().strip() == "huggingface":
        if not ref_key:
            raise RuntimeError("motion (HuggingFace) cần inputs.ref (ảnh nhân vật) — provider này chạy image-to-video, không dùng cổng motion video")
        if motion_key:
            api_log(job_id, "provider HuggingFace = image-to-video: cổng motion video bị bỏ qua", "warn")
        hf_key = _hf_key(params)
        hf_id, fal_id, q = _hf_resolve(params.get("hfModel"), "image-to-video")
        tmp = tempfile.mkdtemp(prefix=f"motion-{job_id[:8]}-")
        api_progress(job_id, 0.05, "tải input")
        ref_local = api_download(ref_key, os.path.join(tmp, "ref" + os.path.splitext(ref_key)[1]))
        payload = {
            "prompt": str(params.get("prompt") or params.get("motionPrompt") or
                          "subtle natural motion, realistic movement, cinematic camera"),
            "image_url": _hf_data_uri(ref_local),
            "resolution": "720p" if "720" in str(params.get("preset") or "") or "1080" in str(params.get("preset") or "") else "480p",
        }
        api_progress(job_id, 0.15, f"HF Wan i2v ({hf_id})")
        res = _hf_call(fal_id, payload, hf_key, q, job_id, 1200, 0.15, 0.9, f"HF Wan i2v")
        vurl = (res.get("video") or {}).get("url")
        if not vurl:
            raise RuntimeError(f"HF không trả video: {json.dumps(res)[:300]}")
        out_mp4 = os.path.join(tmp, "hf_motion.mp4")
        _hf_fetch(vurl, out_mp4)
        # Mux audio nếu workflow có nối (giữ parity self-host: audio đè lên video kết quả).
        # audioMode=silent ưu tiên cao nhất: bỏ mọi audio, kể cả audio input nối sẵn từ workflow cũ.
        audio_key = inputs.get("audio")
        if audio_key and not _silent_audio:
            try:
                a_local = api_download(audio_key, os.path.join(tmp, "audio" + (os.path.splitext(audio_key)[1] or ".mp3")))
                muxed = os.path.join(tmp, "hf_motion_audio.mp4")
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4, "-i", a_local,
                                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest", muxed],
                               check=True, timeout=300)
                if os.path.exists(muxed) and os.path.getsize(muxed) > 1024:
                    out_mp4 = muxed
            except Exception as _e:
                api_log(job_id, f"mux audio lỗi (giữ video không tiếng): {_e}", "warn")
        if _silent_audio:
            try:
                silent = os.path.join(tmp, "hf_motion_silent.mp4")
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4, "-an", "-c:v", "copy", silent],
                               check=True, timeout=300)
                if os.path.exists(silent) and os.path.getsize(silent) > 1024:
                    out_mp4 = silent
                    api_log(job_id, "Âm thanh: im lặng (bỏ mọi audio)", "info")
            except Exception as _e:
                api_log(job_id, f"bỏ tiếng lỗi (giữ output hiện tại): {_e}", "warn")
        api_progress(job_id, 0.94, "chuẩn hoá đầu ra phát hành")
        out_mp4, _delivery_applied = _apply_motion_delivery(out_mp4, tmp, params, job_id)
        api_progress(job_id, 0.98, "upload output")
        api_upload_output(job_id, out_mp4, label=_motion_delivery_label(params, _delivery_applied))
        return
    # #endregion
    # #region ALD 10/07/2026 - provider='dashscope' → MOTION TRANSFER CLOUD (wan2.2-animate-move/mix): đúng nghĩa
    # bê chuyển động driver → ảnh mẫu qua API Alibaba, KHÔNG cần GPU box (bỏ qua prepVram/recycle/preset/retarget
    # self-host — các knob đó chỉ áp dụng self-host). Ràng buộc cloud: ảnh ≤5MB, driver 2-30s ≤200MB, URL public.
    # Audio (DashScope trả video CÂM): original → mux audio driver; replacement → mux cổng audio; silent → giữ câm.
    if str(params.get("provider") or "").lower().strip() == "dashscope":
        if not ref_key or not motion_key:
            raise RuntimeError("motion (DashScope) cần inputs.ref (ảnh mẫu) + inputs.motion (video driver)")
        tmp = tempfile.mkdtemp(prefix=f"motion-{job_id[:8]}-")
        out_mp4 = _dashscope_animate(job_id, params, ref_key, motion_key, tmp)
        try:
            if _silent_audio:
                pass
            elif _replacement_audio and inputs.get("audio"):
                a_local = api_download(inputs["audio"], os.path.join(tmp, "audio" + (os.path.splitext(inputs["audio"])[1] or ".mp3")))
                muxed = os.path.join(tmp, "ds_motion_audio.mp4")
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4, "-i", a_local,
                                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest", muxed],
                               check=True, timeout=300)
                if os.path.exists(muxed) and os.path.getsize(muxed) > 1024:
                    out_mp4 = muxed
            elif not _replacement_audio:
                # audioMode original: bê audio driver sang output ("-map 1:a:0?" = driver câm cũng không fail)
                d_local = api_download(motion_key, os.path.join(tmp, "driver" + (os.path.splitext(motion_key)[1] or ".mp4")))
                muxed = os.path.join(tmp, "ds_motion_audio.mp4")
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4, "-i", d_local,
                                "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-shortest", muxed],
                               check=True, timeout=300)
                if os.path.exists(muxed) and os.path.getsize(muxed) > 1024:
                    out_mp4 = muxed
        except Exception as _e:
            api_log(job_id, f"mux audio lỗi (giữ video không tiếng): {_e}", "warn")
        out_mp4 = _finalize_mp4(out_mp4)
        api_progress(job_id, 0.94, "chuẩn hoá đầu ra phát hành")
        out_mp4, _delivery_applied = _apply_motion_delivery(out_mp4, tmp, params, job_id)
        api_progress(job_id, 0.98, "upload output")
        api_upload_output(job_id, out_mp4, label=_motion_delivery_label(params, _delivery_applied))
        return
    # #endregion
    if not ref_key or not motion_key:
        raise RuntimeError("motion cần inputs.ref (ảnh nhân vật) + inputs.motion (video)")
    # ALD 15/06/2026 - Wan Animate NẶNG VRAM: MẶC ĐỊNH xả sạch GPU trước (Ollama unload + Chandra stop/hold +
    # ComfyUI free); sau khi xả VẪN không đủ → STOP job NGAY (tránh OOM giữa render). Tắt bằng prepVram=false.
    if params.get("prepVram") is not False:
        _ensure_vram_for_motion(job_id)
    # #region ALD 21/06/2026 - MOTION LẺ PHẢI CHẠY TRÊN ComfyUI TƯƠI: motion-transfer đi một mình hay ra output
    # HỎNG (mặt xám/loang) khi ComfyUI còn ôm RAM rò (~50GB) từ job trước → Wan render dưới swap-thrash. Đường
    # _ensure_vram_for_motion bên trên chỉ xả VRAM (/free), KHÔNG nhả RAM → cần recycle (restart) mới trả RAM về OS. Gate theo RSS để
    # KHÔNG recycle thừa khi đã sạch (before_claim vừa recycle → RSS thấp → bỏ qua, hết cảnh recycle dồn).
    # comfy_recycle có wait_ready nên Wan không bắn vào ComfyUI lạnh. Tắt: MOTION_FRESH_COMFY=0.
    if str(os.environ.get("MOTION_FRESH_COMFY", "1")).lower() in ("1", "true", "yes", "on"):
        try:
            _rss = comfy_rss_gb()
            if _rss >= COMFY_RECYCLE_GB and comfy_queue_idle():
                log(f"motion: ComfyUI ôm {_rss:.0f}GB ≥ {COMFY_RECYCLE_GB:.0f}GB → recycle tươi trước Wan (chống output hỏng do swap-thrash)")
                comfy_recycle(f"motion pre-Wan RSS={_rss:.0f}GB")
        except Exception as _e:
            log(f"motion pre-Wan recycle bỏ qua (giữ chạy tiếp): {_e}")
    # #endregion
    tmp = tempfile.mkdtemp(prefix=f"motion-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải input")
    ref_local = api_download(ref_key, os.path.join(tmp, "ref" + os.path.splitext(ref_key)[1]))
    motion_local = api_download(motion_key, os.path.join(tmp, "motion" + (os.path.splitext(motion_key)[1] or ".mp4")))
    motion_local = _cut_motion_driver_segment(motion_local, tmp, params, job_id, "motion")
    # ALD 07/06/2026 - Pre-convert motion về ĐÚNG 16fps trước khi upload ComfyUI. Nếu bỏ qua và nguồn là
    # 30fps: VHS_LoadVideo với frame_load_cap=F chỉ load F frame gốc (8s@30fps) rồi đánh nhãn 16fps → render
    # ở 16fps = 15s → chuyển động CHẬM ~0.5× so với clip gốc. Convert trước: F frame = F/16s source ✓.
    import subprocess as _sp
    # ALD 16/06/2026 - render_fps NATIVE (preset MAX=30, đúng doc Wan2.2-Animate). Pre-convert driver + tính
    # frames + force_rate/frame_rate đều theo rfps. render_fps>=30 → BỎ pass RIFE phía sau (đã native).
    rfps = int(params.get("render_fps", 16) or 16)
    try:
        _motion_speed_factor = 1.0 + max(0.0, min(100.0, float(params.get("motionSpeedup", 0) or 0))) / 100.0
    except Exception:
        _motion_speed_factor = 1.0
    # #region ALD 09/07/2026 - CHẾ ĐỘ "THEO DRIVER" (preset drv-Ns, user chốt): user CHỈ chọn số giây; fps + số
    # frame + tỉ lệ khung THEO DRIVER 1:1 (hết decimation 30→16fps vứt nửa pose → tay ảo; khung fitDriver sẵn lo).
    # Trần frame theo RAM box: ≥120GB → 601f, dưới → 481f (env MOTION_DRV_MAX_FRAMES đè). Vượt trần → TỰ HẠ fps (log).
    # fps driver cap 30 (60fps → 30 — 1200f/20s không VRAM nào chịu). Pre-convert VFR (dưới) với rfps≈fps driver
    # → setpts GIỮ NGUYÊN từng frame: driver bao nhiêu frame, pose bấy nhiêu frame.
    import re as _re_drv
    _drv_m = _re_drv.match(r"^drv-(\d+)s$", str(params.get("preset") or "").strip().lower())
    try:
        _requested_source_sec = float(params.get("driverDurSec") or params.get("durationSec") or 0)
    except (TypeError, ValueError):
        _requested_source_sec = 0.0
    if _requested_source_sec <= 0:
        _fixed_m = _re_drv.match(r"^(\d+(?:\.\d+)?)s(?:-|$)", str(params.get("preset") or "").strip().lower())
        _requested_source_sec = float(_fixed_m.group(1)) if _fixed_m else 0.0
    _target_output_sec = (_requested_source_sec / _motion_speed_factor) if _requested_source_sec > 0 else 0.0
    if _drv_m:
        _want_sec = max(2.0, min(30.0, float(_drv_m.group(1))))
        _dfps = 0.0
        try:
            _p2 = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=avg_frame_rate", "-of", "csv=p=0", motion_local],
                          capture_output=True, text=True, timeout=20)
            _n2 = (_p2.stdout or "").strip().split("/")
            _dfps = (float(_n2[0]) / float(_n2[1])) if len(_n2) == 2 and float(_n2[1] or 0) else 0.0
        except Exception:
            _dfps = 0.0
        _rfps_drv = max(12, min(30, int(round(_dfps)) if _dfps > 0 else 30))
        _dur_avail = 0.0
        try:
            _dur_avail = float(_audio_dur(motion_local) or 0)
        except Exception:
            _dur_avail = 0.0
        # Container MP4 đúng 15s có thể báo 14.9667s (449/30). Chênh tối đa vài frame là lỗi biên encode,
        # không phải driver thật ngắn: vẫn giữ mốc user chọn và pad frame cuối cho đủ.
        _duration_slack = max(0.1, 4.0 / float(_rfps_drv))
        if _dur_avail > 0.5 and _dur_avail + _duration_slack < _want_sec:
            _eff = _dur_avail
        else:
            _eff = _want_sec
        _target_output_sec = _eff / _motion_speed_factor
        _fcap = _motion_frame_cap()
        _rfps_drv, _F_drv, _drop = _motion_fit_frame_budget(_target_output_sec, _rfps_drv, _fcap)
        if _drop:
            api_log(job_id, f"Theo driver: {_eff:.0f}s × {_drop['from_fps']}fps = {_drop['from_frames']}f "
                            f"VƯỢT trần {_fcap}f ({_motion_ram_note()}) → tự hạ {_rfps_drv}fps "
                            f"(GIỮ NGUYÊN {_target_output_sec:.1f}s, không cắt)", "warn")
        params["render_fps"] = _rfps_drv
        params["frames"] = _F_drv
        if not params.get("steps"):
            params["steps"] = 4
        if not (params.get("width") and params.get("height")):
            params["width"], params["height"] = 544, 960   # baseline; fitDriver chỉnh theo tỉ lệ thật driver
        rfps = _rfps_drv
        api_log(job_id, f"Theo driver: {_eff:.0f}s · fps driver ~{_dfps:.1f} → render {_rfps_drv}fps · "
                        f"{_F_drv} frame Wan (sẽ chốt output {_target_output_sec:.3f}s)", "info")
    # #endregion
    # #region ALD 22/06/2026 - fps THEO LỰA CHỌN (BỎ ép-30 blanket cũ — nó làm chậm gấp đôi + góp phần OOM 30fps).
    # render_fps đến từ PRESET: preset thường → 16fps (mặc định, nhanh); preset '-30fps' → 30fps (set ở MOTION_PRESETS,
    # _normalize áp vào params). frames do FE gửi khớp fps của preset nên KHÔNG quy đổi. RIÊNG preset AUTO + clip
    # <60s → ÉP 16fps (theo yêu cầu: auto dưới 1 phút luôn 16fps, nhẹ/nhanh) dù người dùng chọn gì.
    if "auto" in str(params.get("preset") or "").lower() and rfps != 16:
        try:
            _dur = _audio_dur(motion_local) or 0
        except Exception:
            _dur = 0
        if _dur and _dur < 60:
            rfps = 16; params["render_fps"] = 16
            api_log(job_id, f"auto + clip {_dur:.0f}s (<60s) → ép 16fps", "info")
    # #endregion
    # Duration là timeline phát hành; num_frames của Wan chỉ là lưới nội bộ 4k+1. Luôn ceil để phủ đủ rồi
    # trim chính xác ở delivery. Nếu vượt trần RAM thì hạ fps đều, không cắt ngắn timeline.
    if _target_output_sec > 0:
        _duration_frame_cap = _fcap if _drv_m else 481
        _duration_frames = _wan_cover_frames(_target_output_sec, rfps)
        if _duration_frames > _duration_frame_cap:
            _duration_fps = max(12, int((_duration_frame_cap - 1) // max(1.0, _target_output_sec)))
            api_log(job_id, f"Timeline {_target_output_sec:.3f}s vượt trần {_duration_frame_cap}f ở {rfps}fps "
                            f"→ hạ đều xuống {_duration_fps}fps (không cắt thời lượng)", "warn")
            rfps = _duration_fps
            params["render_fps"] = rfps
            _duration_frames = _wan_cover_frames(_target_output_sec, rfps)
        if int(params.get("frames", 0) or 0) != _duration_frames:
            api_log(job_id, f"Timeline {_target_output_sec:.3f}s × {rfps}fps → "
                            f"Wan {int(params.get('frames', 0) or 0)}→{_duration_frames} frame (ceil 4k+1)", "info")
        params["frames"] = _duration_frames
        params["_target_output_sec"] = _target_output_sec
    # #region ALD 12/06/2026 - motionSpeedup (%): tăng tốc chuyển động driver X% so với gốc (vd 5 = nhanh hơn
    # 5%). setpts=PTS/speed làm motion nhanh hơn + ngắn lại tương ứng; atempo giữ audio đồng bộ (chỉ khi video
    # CÓ audio — probe trước, tránh ffmpeg lỗi "filter requires audio" làm rớt cả bước pre-convert). 0 = giữ nguyên.
    _spd = _motion_speed_factor
    # ALD 08/07/2026 - VFR FIX (driver 15s→out 14s + tay giật/khựng): clip điện thoại/TikTok thường VFR (fps biến
    # thiên) dù header ghi 30. Filter fps={rfps} ép CFR bằng cách RỚT/NHÂN ĐÔI frame KHÔNG ĐỀU → giật + hụt frame.
    # Sửa: khi fps thật ≈ rfps (kể cả VFR) → GIỮ NGUYÊN mọi frame, đánh lại PTS đều ở rfps (setpts=N/rfps/TB) = CFR
    # mượt, không rớt frame, giữ độ dài. Chỉ resample bằng fps={rfps} khi fps thật lệch XA rfps (vd 60→30 downsample,
    # 15→30 dup — cả hai đều đều đặn nên không giật). fps thật đọc không được (0) → mặc định giữ-frame.
    _real_fps = 0.0
    try:
        _prb = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=avg_frame_rate", "-of", "csv=p=0", motion_local],
                       capture_output=True, text=True, timeout=20)
        _rn = (_prb.stdout or "").strip().split("/")
        _real_fps = (float(_rn[0]) / float(_rn[1])) if len(_rn) == 2 and float(_rn[1] or 0) else 0.0
    except Exception:
        _real_fps = 0.0
    # ALD 10/07/2026 - FIX "drv-20s bị SLOW-MOTION 1.25×": dải giữ-frame cũ (0.6..1.25×rfps) quá rộng — preset drv
    # tự HẠ fps vì trần RAM (vd driver 30fps → rfps 24, ratio 1.25 rơi ĐÚNG mép trong) → giữ nguyên 597 frame
    # re-time đều ở 24fps, graph chỉ nạp 477 frame ĐẦU = 16s nội dung giãn thành 20s → mọi động tác chậm 25%.
    # Giữ-frame CHỈ hợp lệ khi fps thật ≈ rfps (VFR cùng fps danh nghĩa) → siết còn ±10%; lệch hơn (hạ fps vì trần,
    # 60→30, 15→30) → fps={rfps} resample ĐỀU: rút/nhân frame cách quãng đều, GIỮ NHỊP THỜI GIAN THẬT 1:1.
    if _spd > 1.001:
        _vf = f"setpts=PTS/{_spd:.4f},fps={rfps}"                      # speedup: re-time chủ động, giữ như cũ
    elif _real_fps and not (rfps * 0.9 <= _real_fps <= rfps * 1.1):
        _vf = f"fps={rfps}"                                            # fps thật lệch >10% (hạ fps/60/15) → resample đều, giữ nhịp
    else:
        _vf = f"setpts=N/{rfps}/TB"                                    # fps thật ≈ rfps ±10% (kể cả VFR) → re-time đều = mượt
    if _real_fps:
        api_log(job_id, f"driver fps thật ~{_real_fps:.2f} (rfps={rfps}) → {'downsample fps' if _vf.startswith('fps=') else 'giữ-frame re-time đều (chống VFR giật)'}", "info")
    # #region ALD 27/06/2026 - RAW COLOR DEFAULT: không chuyển driver sang trắng-đen nữa; giữ nguyên màu input.
    # Nếu cần thử nghiệm lại, bật env MOTION_ENABLE_COLOR_ADJUST=1 và set driverGray=1.
    _allow_color_adjust = str(os.environ.get("MOTION_ENABLE_COLOR_ADJUST", "0")).strip().lower() in ("1", "true", "yes", "on")
    _driver_gray = _allow_color_adjust and str(params.get("driverGray", params.get("driver_gray", "0"))).strip().lower() in ("1", "true", "yes", "on")
    if _driver_gray:
        _vf += ",hue=s=0"
    # #endregion
    if _spd > 1.001:
        try:
            _ap = _sp.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                           "stream=index", "-of", "csv=p=0", motion_local], capture_output=True, text=True, timeout=20)
            _has_audio = bool((_ap.stdout or "").strip())
        except Exception:
            _has_audio = False
        _acodec = (["-af", f"atempo={_spd:.4f}", "-c:a", "aac"] if _has_audio else ["-an"])
    else:
        _acodec = ["-c:a", "copy"]
    try:
        motion_16fps = os.path.join(tmp, "motion_16fps.mp4")
        _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", motion_local,
                 "-vf", _vf, "-r", str(rfps), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",  # ALD 08/07 - -r ép CFR output (setpts đã đều PTS)
                 *_acodec, motion_16fps], check=True, timeout=120)
        if os.path.exists(motion_16fps) and os.path.getsize(motion_16fps) > 1024:
            motion_local = motion_16fps
            if _driver_gray:
                api_log(job_id, "Driver → trắng đen vào Wan (experimental color adjust)", "info")
            if _spd > 1.001:
                api_log(job_id, f"Tăng tốc chuyển động +{(_spd - 1) * 100:.0f}% so với motion gốc", "info")
    except Exception as _e:
        api_log(job_id, f"pre-convert motion 16fps lỗi (giữ nguyên): {_e}", "warn")
    # #endregion
    # Wan Animate chỉ nhận num_frames = 4k+1. Render số frame Wan nhỏ nhất PHỦ đủ duration rồi hậu kỳ cắt đúng
    # mốc user chọn. Khoảng thiếu tối đa 4 frame là sai số biên/container: clone frame cuối, tuyệt đối không floor.
    try:
        _target_frames = int(params.get("frames", 0) or 0)
        _actual_frames = _video_nframes(motion_local)
        _missing_frames = _target_frames - _actual_frames
        if _target_frames > 1 and 1 <= _missing_frames <= 4:
            _padded = os.path.join(tmp, "motion_frame_padded.mp4")
            _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", motion_local,
                     "-vf", (f"tpad=stop_mode=clone:stop_duration={_missing_frames / float(rfps):.9f},"
                             f"trim=end_frame={_target_frames},setpts=N/{rfps}/TB"),
                     "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                     "-c:a", "copy", "-movflags", "+faststart", _padded], check=True, timeout=120)
            if _video_nframes(_padded) == _target_frames:
                motion_local = _padded
                api_log(job_id, f"Pad frame biên cuối: {_actual_frames}f → {_target_frames}f để giữ preset Wan", "info")
    except Exception as _e:
        api_log(job_id, f"pad frame biên cho preset Wan lỗi (tiếp tục không pad): {_e}", "warn")
    # #region ALD 03/07/2026 - FIT DRIVER (fix "cằm bị nâng"): driver KHÔNG đúng tỷ lệ khung preset (vd 16:9 vào
    # 9:16 544x960) bị node 12 VHS_LoadVideo (custom_width+custom_height cùng set) KÉO DÃN không giữ tỷ lệ →
    # mặt méo/cằm nâng, DWPose lệch theo. Fix: render theo TỶ LỆ THẬT của driver — cạnh ngắn giữ nguyên hạng
    # chất lượng preset, cạnh dài theo tỷ lệ driver, CAP cạnh dài ≤ MOTION_VRAM_MAX_EDGE (968, ngân sách VRAM
    # đã đo 22/06 — driver 21:9 thu cả khung giữ tỷ lệ), cả hai bội 16. Probe đặt SAU cut/pre-convert-16fps/tpad
    # (ffmpeg re-encode đã autorotate → đúng chiều hiển thị; 3 bước đó không scale). Ref (node 11) vẫn
    # keep_proportion=crop theo khung mới. Node cũ ép width/height tay thì tôn trọng (_raw_wh_forced, skip).
    # Tắt: param fitDriver=0 hoặc env MOTION_FIT_DRIVER=0.
    _fit_driver = str(params.get("fitDriver", params.get("fit_driver",
                      os.environ.get("MOTION_FIT_DRIVER", "1")))).strip().lower() in ("1", "true", "yes", "on")
    if _fit_driver and not _raw_wh_forced:
        try:
            _dw, _dh = _img_dims(motion_local)
            if _dw and _dh:
                _short0 = float(min(int(params["width"]), int(params["height"])))
                _ratio = max(_dw, _dh) / float(min(_dw, _dh))
                # ALD 19/07/2026 - quality=720p được phép dùng cạnh dài 1280; mặc định 540p truyền
                # maxRenderEdge=968 để giữ baseline an toàn RAM, không phụ thuộc thời lượng.
                _max_edge = int(params.get("maxRenderEdge", params.get("max_render_edge",
                                os.environ.get("MOTION_VRAM_MAX_EDGE", "968"))))
                _max_edge = max(480, min(1280, _max_edge))
                _long0 = _short0 * _ratio
                if _long0 > _max_edge:
                    _short0, _long0 = _max_edge / _ratio, float(_max_edge)
                _w2, _h2 = ((_short0, _long0) if _dh >= _dw else (_long0, _short0))
                _w2, _h2 = _even16(_w2), _even16(_h2)
                if (_w2, _h2) != (int(params["width"]), int(params["height"])):
                    api_log(job_id, f"FIT DRIVER: driver {_dw}x{_dh} → render {_w2}x{_h2} "
                                    f"(bỏ ép khung {params['width']}x{params['height']} của preset)", "info")
                    params = {**params, "width": _w2, "height": _h2}
        except Exception as _e:
            api_log(job_id, f"fit driver lỗi (giữ khung preset): {_e}", "warn")
    # #endregion
    # Số frame cuối cùng còn có thể đổi theo duration/độ dài Driver thật. Window plan được tính SAU khi chốt _F.
    _F = int(params.get("frames", 81) or 81)
    # #region ALD 10/06/2026 - TÍNH frames theo độ dài motion THỰC (không tin preset mù quáng): driver chỉ có
    # N frame @16fps thì render đúng N — phần vượt quá Wan sẽ tự "bịa" chuyển động (lềnh bềnh) + tốn GPU vô ích.
    # Wan yêu cầu num_frames = 4k+1 → ceil và pad tối đa 3 frame biên để không vứt frame thật cuối clip.
    try:
        # Không suy từ duration/audio: video không audio hoặc timebase lẻ có thể báo duration sai,
        # khiến VHS trả batch ngắn hơn num_frames và Wan lỗi tensor size (vd 1 vs 227).
        # ALD 26/06/2026 - accurate=True: ĐẾM DECODE THẬT (count_frames) = đúng số frame VHS_LoadVideo nạp. Header
        # nb_frames đếm DƯ với video upload fps/timebase lẻ → reduction không kích hoạt → num_frames(241) > pose
        # thật(227) → lỗi "Expected size 1 but got size 227". Người dùng chỉ upload, mọi chuẩn hoá xử lý ở đây.
        _drv_frames_total = _video_nframes(motion_local, accurate=True)
        _drv_dur = (_drv_frames_total / float(rfps)) if _drv_frames_total > 0 else (_audio_dur(motion_local) or 0)
        _skip = _motion_int(params, "skip_first_frames", "skipFirstFrames", default=0)
        if _drv_frames_total > 0:
            if _skip > 0 and _drv_frames_total >= _F and (_drv_frames_total - _skip) < _F:
                api_log(job_id, f"Motion {_drv_dur:.1f}s chỉ vừa đủ preset {_F}f; bỏ skip_first_frames={_skip} để không hụt duration", "info")
                _skip = 0
                params = {**params, "skip_first_frames": 0, "skipFirstFrames": 0}
            _drv_frames = max(17, _drv_frames_total - _skip)
            if _drv_frames < _F:
                _available_sec = _drv_frames / float(rfps)
                if (_target_output_sec <= 0 or
                        _available_sec + (0.5 / float(rfps)) < _target_output_sec):
                    _target_output_sec = _available_sec
                    params["_target_output_sec"] = _target_output_sec
                _F_new = min(_F, _wan_cover_frames(_available_sec, rfps))
                _pad_needed = (_skip + _F_new) - _drv_frames_total
                if 1 <= _pad_needed <= 4:
                    _short_padded = os.path.join(tmp, "motion_short_padded.mp4")
                    _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", motion_local,
                             "-vf", (f"tpad=stop_mode=clone:stop_duration={_pad_needed / float(rfps):.9f},"
                                     f"trim=end_frame={_drv_frames_total + _pad_needed},setpts=N/{rfps}/TB"),
                             "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast",
                             "-crf", "23", "-c:a", "copy", "-movflags", "+faststart", _short_padded],
                            check=True, timeout=120)
                    if _video_nframes(_short_padded) == _drv_frames_total + _pad_needed:
                        motion_local = _short_padded
                api_log(job_id, f"Motion thực {_drv_dur:.1f}s (~{_drv_frames}f@{rfps}fps) NGẮN hơn preset {_F}f "
                                f"→ render {_F_new}f, pad {_pad_needed} frame biên (không bỏ frame thật)", "info")
                _F = _F_new
                params = {**params, "frames": _F}
    except Exception as _e:
        api_log(job_id, f"tính frames theo motion lỗi (dùng preset {_F}f): {_e}", "warn")
    # #endregion
    # ALD 21/07/2026 - TỰ NHIÊN HÓA (user chốt): gỡ hẳn LivePortrait + env MOTION_FACE_SOURCE_DEFAULT khỏi motion.
    # Mặt đi thẳng theo thiết kế gốc Wan-Animate: face_images = crop driver (default trong build_wan_workflow).
    api_progress(job_id, 0.15, "upload vào ComfyUI")
    ref_name = comfy_upload(ref_local)
    motion_name = comfy_upload(motion_local)
    api_progress(job_id, 0.25, "Wan 2.2 Animate (sampling)")
    window_plan = _wan_window_plan(params, _F)
    _wsz = window_plan["context_frames"]
    est_windows = max(1, int(math.ceil(max(1, _F - 1) / max(1, _wsz - 1))))
    if window_plan["anchored"]:
        api_log(job_id,
                f"Window mode: anchored-context · context={_wsz}f · overlap={window_plan['overlap']}f · "
                f"final-overlap={window_plan['final_overlap_latents']} latent · "
                f"schedule={window_plan['schedule']} · neo Ref ở mỗi context · seam không linear-average dài",
                "info")
    else:
        api_log(job_id, f"Window mode: autoregressive · {est_windows} window × {_wsz}f", "info")
    wan_prefix = f"motion-{job_id[:8]}"
    workflow = build_wan_workflow(ref_name, motion_name, params, prefix=wan_prefix)
    try:
        pid = comfy_submit(workflow)
    except Exception as _submit_error:
        # Box dùng WanVideoWrapper cũ có thể chưa có Context Options. Chỉ fallback khi validation nói đúng
        # node/input context không tồn tại; lỗi khác phải nổi lên để không che bug workflow.
        _msg = str(_submit_error)
        # ALD 21/07/2026 - Box thiếu node/model ViTPose face-crop (WanAnimatePreprocess chưa cài) → tự hạ về
        # chuỗi DWPose pad128 cũ thay vì chết job. Chỉ khớp đúng tên node để không che bug workflow khác.
        _vitpose_missing = any(x in _msg for x in ("PoseAndFaceDetection", "OnnxDetectionModelLoader", "DrawViTPose"))
        if _vitpose_missing and str(params.get("faceCropMode", "")).strip().lower() != "dwpose":
            api_log(job_id, f"Box thiếu ViTPose node → fallback DWPose pad128 (tắt cả face-crop lẫn pose-retarget): {_msg[:240]}", "warn")
            # faceCropMode=dwpose → _vitpose_face=False → _pose_retarget cũng tự tắt (pose về DWPose node 20).
            params = {**params, "faceCropMode": "dwpose", "face_crop_mode": "dwpose",
                      "poseRetarget": "0", "pose_retarget": "0"}
            pid = comfy_submit(build_wan_workflow(ref_name, motion_name, params, prefix=wan_prefix))
        else:
            _context_incompatible = any(x in _msg for x in ("WanVideoContextOptions", "WANVIDCONTEXT", "context_options"))
            if not window_plan["anchored"] or not _context_incompatible:
                raise
            api_log(job_id, f"Wan wrapper chưa hỗ trợ anchored-context; fallback autoregressive: {_msg[:240]}", "warn")
            params = {**params, "window_mode": "autoregressive", "windowMode": "autoregressive"}
            window_plan = _wan_window_plan(params, _F)
            pid = comfy_submit(build_wan_workflow(
                ref_name, motion_name, params, prefix=wan_prefix
            ))
    outputs = comfy_poll(pid, job_id, deadline_sec=1800,
                         prog_lo=0.25, prog_hi=0.9, prog_step="Wan 2.2 Animate",
                         windows=1 if window_plan["anchored"] else est_windows,
                         output_prefix=wan_prefix)
    api_progress(job_id, 0.9, "tải kết quả")
    out_mp4 = comfy_fetch_output(outputs)
    if not out_mp4:
        raise RuntimeError("ComfyUI không trả MP4")

    # #region ALD 10/06/2026 - Wan Animate render theo CỬA SỔ 77 frame → hay DƯ so với preset (161f → 3 cửa sổ
    # đầy = 228f). Phần dư KHÔNG còn motion dẫn → nhân vật trôi lềnh bềnh/chậm bất thường + video dài hơn yêu
    # cầu (preset 10s ra 14s). Cắt về đúng min(frames preset, độ dài motion thực - skip) ngay tại nguồn —
    # audio mux + RIFE phía sau đều hưởng đúng thời lượng.
    try:
        _mdur = _audio_dur(out_mp4) or 0
        _drv = _audio_dur(motion_local) or 0          # motion đã pre-convert 16fps ở trên
        _skip_sec = _motion_int(params, "skip_first_frames", "skipFirstFrames", default=0) / float(rfps)
        _exact_target = _motion_target_duration(params)
        _exp = (_exact_target if _exact_target > 0 else
                (min(_F / float(rfps), max(1.0, _drv - _skip_sec)) if _drv else _F / float(rfps)))
        if _mdur > _exp + 0.35:
            _trim = os.path.join(tmp, "wan_trim.mp4")
            _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4, "-t", f"{_exp:.3f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy",
                     "-movflags", "+faststart", _trim], check=True, capture_output=True, timeout=300)
            api_log(job_id, f"Cắt phần Wan render dư (hết motion dẫn, nhân vật tự trôi): {_mdur:.1f}s → {_exp:.1f}s", "info")
            out_mp4 = _trim
    except Exception as _e:
        api_log(job_id, f"trim phần render dư lỗi (giữ nguyên): {_e}", "warn")
    # #endregion

    # ALD 27/06/2026 - RAW COLOR DEFAULT: không chạy filter hậu kỳ màu/độ sáng vì có thể gây flash đổi màu.
    # Chỉ bật lại khi chủ động set MOTION_ENABLE_COLOR_ADJUST=1.
    _allow_color_adjust = str(os.environ.get("MOTION_ENABLE_COLOR_ADJUST", "0")).strip().lower() in ("1", "true", "yes", "on")
    _warmth = float(params.get("warmth", os.environ.get("MOTION_WARMTH", "0"))) if _allow_color_adjust else 0.0
    _bright_cap = float(params.get("brightCap", os.environ.get("MOTION_BRIGHT_CAP", "1.0"))) if _allow_color_adjust else 1.0
    _sharpen = float(params.get("sharpen", os.environ.get("MOTION_SHARPEN", "0"))) if _allow_color_adjust else 0.0
    _contrast = float(params.get("contrast", os.environ.get("MOTION_CONTRAST", "1.0"))) if _allow_color_adjust else 1.0
    _do_warmth = abs(_warmth) >= 1
    _do_bright = 0.5 < _bright_cap < 1.0
    _do_sharpen = _sharpen > 0.0
    _do_contrast = 1.0 < _contrast < 1.3
    if _do_warmth or _do_bright or _do_sharpen or _do_contrast:
        try:
            _bcap = os.path.join(tmp, "out_bcap.mp4")
            _vf_parts = []
            if _do_warmth:
                # warmth -47 → _wk ≈ -0.141 (đỏ giảm, xanh tăng). Scale 0.3: warmth ±100 ↔ ±0.3 colorbalance.
                _wk = max(-1.0, min(1.0, _warmth / 100.0)) * 0.3
                _vf_parts.append(f"colorbalance=rs={_wk:.3f}:rm={_wk:.3f}:rh={_wk:.3f}:bs={-_wk:.3f}:bm={-_wk:.3f}:bh={-_wk:.3f}:pl=1")
            if _do_bright:
                _vf_parts.append(f"colorlevels=romax={_bright_cap}:gomax={_bright_cap}:bomax={_bright_cap}")
            if _do_contrast:
                _vf_parts.append(f"eq=contrast={_contrast:.2f}")
            if _do_sharpen:
                _vf_parts.append(f"unsharp=5:5:{_sharpen:.2f}:3:3:0.0")
            _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4,
                     "-vf", ",".join(_vf_parts),
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy",
                     "-movflags", "+faststart", _bcap], check=True, capture_output=True, timeout=300)
            if os.path.exists(_bcap) and os.path.getsize(_bcap) > 1024:
                api_log(job_id, f"post: warmth={_warmth if _do_warmth else 'off'} cap={_bright_cap if _do_bright else 'off'} contrast={_contrast if _do_contrast else 'off'} sharpen={_sharpen if _do_sharpen else 'off'}", "info")
                out_mp4 = _bcap
        except Exception as _e:
            api_log(job_id, f"post-process lỗi (giữ nguyên): {_e}", "warn")

    if _allow_color_adjust:
        out_mp4 = _apply_ref_grade_video(out_mp4, ref_local, tmp, params, job_id)

    _do_deflicker = _allow_color_adjust and str(params.get("motionDeflicker", params.get("motion_deflicker",
                        os.environ.get("MOTION_DEFLICKER", "0")))).strip().lower() in ("1", "true", "yes", "on")
    try:
        _deflicker_window = int(float(params.get("motionDeflickerWindow", params.get("motion_deflicker_window",
                                os.environ.get("MOTION_DEFLICKER_WINDOW", "5")))))
    except Exception:
        _deflicker_window = 5
    _deflicker_window = max(2, min(15, _deflicker_window))
    if _do_deflicker:
        try:
            _df = os.path.join(tmp, "out_deflicker.mp4")
            _sp.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_mp4,
                     "-vf", f"deflicker=s={_deflicker_window}:m=am",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy",
                     "-movflags", "+faststart", _df], check=True, capture_output=True, timeout=300)
            if os.path.exists(_df) and os.path.getsize(_df) > 1024:
                api_log(job_id, f"post: deflicker={_deflicker_window}f", "info")
                out_mp4 = _df
        except Exception as _e:
            api_log(job_id, f"deflicker lỗi (giữ nguyên): {_e}", "warn")

    # ── Audio ────────────────────────────────────────────────────────────────────
    # ALD 05/06/2026 - Ưu tiên audio bro NỐI VÀO CỔNG input của node (inputs.audio) = "âm thanh thay thế",
    # mux đè full-length. Không có → giữ audio video dẫn động (node 110 đã passthrough). Tương thích cũ:
    # params.audio_replacement_id (chọn từ thư viện). audioMode=silent → bỏ mọi tiếng, kể cả audio input nối sẵn.
    # Legacy: audioPassthrough=false + không có nguồn → bỏ tiếng.
    import subprocess
    aud_local = None
    if _silent_audio:
        api_log(job_id, "Âm thanh: im lặng (bỏ audio gốc và audio thay thế nếu có)", "info")
    elif inputs.get("audio") and (_replacement_audio or not _audio_mode):
        try:
            ak = inputs["audio"]
            aud_local = api_download(ak, os.path.join(tmp, "in_audio" + (os.path.splitext(ak)[1] or ".mp3")))
        except Exception as e:
            api_log(job_id, f"tải audio cổng input lỗi (giữ audio gốc): {e}", "warn"); aud_local = None
    elif params.get("audio_replacement_id") and (_replacement_audio or not _audio_mode):
        try:
            ak = api_resolve_audio(params["audio_replacement_id"])
            aud_local = api_download(ak, os.path.join(tmp, "in_audio" + (os.path.splitext(ak)[1] or ".mp3")))
        except Exception as e:
            api_log(job_id, f"resolve audio thư viện lỗi (giữ audio gốc): {e}", "warn"); aud_local = None
    if aud_local:
        try:
            api_progress(job_id, 0.92, "ghép âm thanh thay thế")
            merged = os.path.join(tmp, "out_audio.mp4")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", out_mp4, "-i", aud_local,
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", merged],
                           check=True, capture_output=True, timeout=300)
            out_mp4 = merged
        except Exception as e:
            api_log(job_id, f"ghép audio lỗi (giữ audio gốc): {e}", "warn")
    elif _silent_audio or (not _audio_mode and str(params.get("audioPassthrough", True)).lower() in ("0", "false", "no", "off")):
        try:  # passthrough TẮT + không có nguồn thay thế → bỏ tiếng
            api_progress(job_id, 0.92, "bỏ âm thanh")
            silent = os.path.join(tmp, "out_silent.mp4")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", out_mp4, "-an", "-c:v", "copy", silent],
                           check=True, capture_output=True, timeout=300)
            out_mp4 = silent
        except Exception as e:
            api_log(job_id, f"bỏ tiếng lỗi (giữ audio gốc): {e}", "warn")

    # ALD 05/07/2026 - faceLock (opt-in faceLock=1 / env MOTION_FACELOCK_DEFAULT=1): khóa identity mặt về ảnh
    # mẫu bằng inswapper. Sau grade/audio, TRƯỚC RIFE/upload. Giữ audio (mux lại bên trong). Tắt/chưa cài = no-op.
    out_mp4 = _apply_face_lock(out_mp4, ref_local, tmp, params, job_id)

    # ── Nội suy fps (RIFE) — opt-in, pass RIÊNG (Wan đã offload) tránh OOM. Tối đa 30s (≈481 frame @16fps).
    # ALD 16/06/2026 - ĐÃ BỎ 60fps (chỉ còn: Gốc 16fps · 30fps RIFE ×2). Preset MAX render NATIVE 30fps
    # (rfps>=30) → KHÔNG chạy RIFE (tránh nội suy chồng lên bản đã 30fps thật). fps rõ ràng ưu tiên hơn legacy.
    # ALD 21/06/2026 - TẮT RIFE (revert về mặc định 05/06 = 16fps native). RIFE ×2/×4 = thêm pass + tạo "ảo ảo"
    # (mờ frame nội suy) khi chuyển động nhanh. 16fps native = nhanh hơn + nét (run "rất đẹp" của user vốn 16fps).
    # Giữ khối RIFE dưới (dead khi _fps_target=0) — muốn bật lại bỏ dòng ép 0. fps/fps60/interpolate vô hiệu.
    _fps_target = 0
    _pre_rife_mp4 = out_mp4  # giữ bản trước RIFE để mux audio đúng (RIFE VHS audio có thể ngắn hơn)
    if _fps_target:
        try:
            _mult = 4 if _fps_target == 60 else 2   # ALD 21/06/2026 - MỞ LẠI 60fps: ×4 (16→64) cho 60, ×2 (16→32) cho 30
            api_progress(job_id, 0.94, f"nội suy {_fps_target}fps (RIFE)")
            rife_name = comfy_upload(out_mp4)
            rife_out = comfy_fetch_output(
                comfy_poll(comfy_submit(build_rife60_workflow(rife_name, prefix=f"motion-{_fps_target}fps-{job_id[:8]}", multiplier=_mult)),
                           job_id, deadline_sec=1800))
            if rife_out:
                # ALD 07/06/2026 - Mux VIDEO từ RIFE + AUDIO từ _pre_rife_mp4 (audio đúng từ Wan passthrough).
                # -c:a copy từ rife_out (VHS VideoCombine) đôi khi ngắn hơn video → audio lệch.
                # ALD 10/06/2026 - BỎ "-vf fps=30/60": RIFE ra 32/64fps, ép xuống 30/60 = RỚT 2/4 frame mỗi
                # giây → giật micro-stutter đều đặn (user thấy "khựng"). Giữ nguyên 32/64fps — player nào
                # cũng phát được, mượt hơn hẳn vì không mất frame nào.
                exact = os.path.join(tmp, f"out_{_fps_target}fps.mp4")
                try:
                    _has_aud = _has_audio(str(_pre_rife_mp4))
                    if _has_aud:
                        subprocess.run(["ffmpeg", "-y", "-v", "error",
                                        "-i", rife_out, "-i", str(_pre_rife_mp4),
                                        "-map", "0:v:0", "-map", "1:a:0",
                                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                                        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest",
                                        "-movflags", "+faststart", exact],
                                       check=True, capture_output=True, timeout=600)
                    else:
                        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", rife_out,
                                        "-map", "0:v:0",
                                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                                        "-an", "-movflags", "+faststart", exact],
                                       check=True, capture_output=True, timeout=600)
                    out_mp4 = exact
                except Exception as e:
                    api_log(job_id, f"ép {_fps_target}fps lỗi (giữ bản RIFE): {e}", "warn"); out_mp4 = rife_out
            else:
                api_log(job_id, "RIFE không trả MP4 — giữ bản gốc fps", "warn")
        except Exception as e:
            api_log(job_id, f"{_fps_target}fps lỗi (giữ bản gốc fps): {e}", "warn")

    # ALD 27/07 - Cân màu chống ngả tông TRƯỚC ESRGAN: sửa cast trên master gốc để pass làm nét không khuếch đại nó
    out_mp4 = _apply_motion_drift_fix(out_mp4, ref_local, tmp, params, job_id)
    out_mp4 = _apply_motion_detail_upscale(out_mp4, tmp, params, job_id)  # ALD 17/07 - ESRGAN làm nét TRƯỚC delivery (trị lớp blur)
    api_progress(job_id, 0.94, "chuẩn hoá đầu ra phát hành")
    out_mp4, _delivery_applied = _apply_motion_delivery(out_mp4, tmp, params, job_id)
    api_progress(job_id, 0.98, "upload output")
    _output_label = _motion_delivery_label(params, _delivery_applied)
    api_upload_output(job_id, out_mp4, label=_output_label)  # API tự set status=done + Storage dùng label làm tên

# ALD 01/06/2026 - Vision auto-detect loại đồ từ ẢNH SẢN PHẨM (khi user để 'auto').
# Gọi Ollama vision (OLLAMA_URL + VISION_MODEL), trả 1 trong 10 loại. FAIL-SAFE: trả None khi
# chưa cấu hình / Ollama lỗi / JSON sai → caller fallback 'upper'. keep_alive=0 để nhả VRAM ngay
# (GPU dùng chung với Wan + Qwen-Image-Edit).
GARMENT_TYPES_VALID = {"upper", "lower", "skirt", "dress", "set", "bikini", "bra", "lingerie", "shoes", "accessory"}
_VISION_SYSTEM = (
    "Bạn là vision analyzer phân loại trang phục cho pipeline try-on. Xem ẢNH SẢN PHẨM và phân vào ĐÚNG 1 loại. "
    'Trả JSON THUẦN (không markdown): {"garment_type": "<một trong: upper|lower|skirt|dress|set|bikini|bra|lingerie|shoes|accessory>"}\n'
    "- upper: áo lẻ phần trên (sơ mi, thun, áo khoác) — KHÔNG phải bra/đồ lót\n"
    "- lower: quần (quần dài, short, jeans)\n"
    "- skirt: chân váy rời (chỉ phần dưới, không liền áo)\n"
    "- dress: váy/đầm LIỀN 1 mảnh (gồm jumpsuit)\n"
    "- set: bộ phối 2 mảnh mặc ngoài (áo + quần/chân váy cùng bộ)\n"
    "- bikini: đồ bơi/bikini 2 mảnh\n"
    "- bra: áo lót/bra LẺ (1 mảnh phần trên)\n"
    "- lingerie: đồ lót BỘ (áo lót + quần lót)\n"
    "- shoes: giày/dép/sandal/cao gót/boot\n"
    "- accessory: phụ kiện (mũ, túi, khăn, kính, thắt lưng, trang sức)\n"
    "Phân vân: 1 mảnh liền→dress; 2 mảnh tách rời→set; vải bơi→bikini; vải lót/ren mặc trong→lingerie."
)

def analyze_garment(image_path, job_id=None):
    if not OLLAMA_URL:
        return None
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=90, json={
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": "Phân loại sản phẩm trong ảnh.", "images": [b64]},
            ],
            "stream": False, "format": "json", "keep_alive": 0,
        })
        r.raise_for_status()
        txt = ((r.json().get("message") or {}).get("content") or "").strip()
        gt = str(json.loads(txt).get("garment_type", "")).lower().strip()
        if gt in GARMENT_TYPES_VALID:
            return gt
        if job_id: api_log(job_id, f"Vision trả loại lạ '{gt}' → bỏ qua", level="warn")
    except Exception as e:
        if job_id: api_log(job_id, f"Vision auto-detect lỗi: {e}", level="warn")
    return None

# ALD 14/06/2026 - Multi-step refine kiểu ChatGPT: vision (qwen-VL) SOI ảnh vừa render vs MÔ TẢ MONG MUỐN → tìm
# lỗi (sai màu tóc/tay hỏng/biểu cảm/ánh sáng/thiếu chi tiết) → trả 1 CÂU LỆNH EDIT tiếng Anh để Qwen-Edit img2img
# SỬA. FAIL-SAFE: Ollama lỗi/JSON sai → trả None (caller dừng tinh chỉnh, giữ ảnh hiện tại). "" = ảnh đã ĐẠT.
_REFINE_SYSTEM = (
    "Bạn là giám đốc nghệ thuật khó tính soi ảnh AI. So sánh ẢNH với MÔ TẢ MONG MUỐN của user. Tìm các điểm SAI / "
    "THIẾU / lỗi rõ nhất (vd: sai màu tóc, tay/ngón hỏng, biểu cảm gượng, ánh sáng phẳng, da nhựa, thiếu chi tiết, "
    "sai trang phục/bối cảnh). Viết DUY NHẤT 1 câu lệnh EDIT tiếng Anh NGẮN GỌN để Qwen-Image-Edit sửa ảnh cho khớp "
    "mô tả — CHỈ nêu thứ cần sửa, GIỮ NGUYÊN phần đã đúng (đừng vẽ lại từ đầu). Nếu ảnh đã tốt thì ok=true.\n"
    'Trả JSON THUẦN (không markdown): {"ok": <true|false>, "edit": "<câu lệnh edit tiếng Anh; rỗng nếu ok=true>"}'
)

def _vision_refine_prompt(image_path, intent_prompt, job_id=None):
    if not OLLAMA_URL:
        return None
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=120, json={
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": _REFINE_SYSTEM},
                {"role": "user", "content": f"MÔ TẢ MONG MUỐN:\n{(intent_prompt or '').strip()[:1500]}\n\nSoi ảnh rồi trả JSON.", "images": [b64]},
            ],
            "stream": False, "format": "json", "keep_alive": 0,
        })
        r.raise_for_status()
        d = json.loads(((r.json().get("message") or {}).get("content") or "{}").strip())
        if d.get("ok") is True:
            return ""   # ảnh đã ĐẠT → dừng
        edit = str(d.get("edit") or "").strip()
        return edit or None
    except Exception as e:
        if job_id: api_log(job_id, f"Vision refine lỗi: {e}", level="warn")
        return None

# ALD 15/06/2026 - Hậu kỳ tryon: chỉnh độ sáng (đồ trắng hay CHÁY SÁNG → kéo brightness xuống) + xuất độ phân
# giải FullHD/2K/4K (upscale long-edge lanczos). brightness: -0.5..0.5 (0=gốc). outputRes: ''|fullhd|2k|4k.
_RES_LONGEDGE = {"fullhd": 1920, "2k": 2560, "4k": 3840}
# ALD 12/07/2026 - ĐÃ GỠ HẲN 2 bước "đè ảnh" hậu kỳ tryon (user chốt: KHÔNG dán pixel đè output —
# gây mảng loang/mép ghép thấy rõ): _tryon_composite (dán da/mặt/nền ảnh gốc đè lên ảnh tryon qua
# SegFormer) + _tryon_facelock (dán cứng pixel khuôn mặt ảnh mẫu). Output tryon giờ là ảnh model
# sinh ra NGUYÊN VẸN; giữ mặt/da dựa vào prompt + denoise, không ghép pixel.

def _tryon_postprocess(out, params, job_id):
    try:
        bright = float(params.get("brightness") or 0)
    except (TypeError, ValueError):
        bright = 0.0
    bright = max(-0.5, min(bright, 0.5))
    # ALD 22/06/2026 - DEFAULT 1.0 (TẮT bơm bão hoà): bơm 1.15 khuếch đại tông ấm → ÁM VÀNG, mất tự nhiên. Việc
    # trả màu tự nhiên/độ tươi gốc giao cho ColorMatch (output→ảnh gốc) trong workflow. params.saturation >1 nếu vẫn muốn bơm tay.
    try:
        sat = float(params.get("saturation") or 1.0)
    except (TypeError, ValueError):
        sat = 1.0
    sat = max(0.5, min(sat, 2.0))
    res = str(params.get("outputRes") or params.get("resolution") or "").lower().strip()
    target = _RES_LONGEDGE.get(res)
    vf = []
    if abs(bright) > 0.005 or abs(sat - 1.0) > 0.01:
        vf.append(f"eq=brightness={bright:.3f}:saturation={sat:.3f}")
    if target:
        vf.append(f"scale='if(gte(iw,ih),{target},-2)':'if(gte(iw,ih),-2,{target})':flags=lanczos")
    if not vf:
        return out
    dst = os.path.splitext(out)[0] + ".pp.png"
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", out, "-vf", ",".join(vf), dst], check=True, timeout=300)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            api_log(job_id, f"hậu kỳ tryon: brightness={bright}, res={res or 'gốc'}", "info")
            return dst
    except Exception as e:
        api_log(job_id, f"hậu kỳ tryon lỗi → giữ ảnh gốc: {e}", "warn")
    return out

# #region ALD 16/08/2026 - GHÉP NỀN try-on (pass 2). FE toggle "Ghép nền" (InspectorTryon) mở cổng 'Nền' →
# engine gửi inputs.background, và FlowNode.vue hứa "worker ghép người vào bối cảnh (Qwen pass 2)" — nhưng
# run_tryon trước giờ KHÔNG đọc input này: user nối ảnh nền mà output vẫn giữ nguyên nền gốc (bug user báo
# 16/08). Pass 2 tái dùng build_qwen_create_workflow — đường ghép người-vào-cảnh ĐÃ chạy thật ở teaser
# TẦNG 2A (scene reference). image1 = người ĐÃ thay đồ, image2 = ảnh bối cảnh; realism=False để prompt
# compose không bị câu ép-realism đè (bài học prompt-loãng 20/07).
_TRYON_BG_POS = (
    "Take the person from image 1 and place them into the environment shown in image 2. "
    "CRITICAL — keep the person EXACTLY as in image 1: same face, same hair, same body proportions, same pose, "
    "and the SAME OUTFIT with its exact colors, patterns, fabrics and details; do not restyle, recolor or change "
    "any garment, shoe or accessory. "
    "The background must be the location from image 2: reproduce its setting, architecture, furniture, plants and "
    "depth faithfully — do NOT keep any part of the original background from image 1. "
    "Blend the person in naturally: match the lighting direction, color temperature, perspective and camera height "
    "of image 2, and add natural contact shadows under the person. "
    "Photorealistic, seamless composite, no cut-out edges.")
_TRYON_BG_NEG = (
    "changed outfit, different clothes, restyled garment, wrong garment color, missing accessories, "
    "changed face, different person, changed hair, changed pose, distorted body, "
    "original background kept, background from image 1, different location than image 2, invented background, "
    "pasted cutout, sticker edges, floating person, mismatched lighting, mismatched perspective, wrong scale, "
    "blurry, lowres, deformed, watermark, text, signature")

def _tryon_compose_background(job_id, person_path, bg_path, prefix):
    """Pass 2 'Ghép nền': đặt người (đã thay đồ, ảnh 1) vào bối cảnh (ảnh 2) bằng Qwen-Image-Edit.
    Trả path ảnh ghép. RAISE khi ComfyUI không trả ảnh — user đã chủ động nối cổng Nền, âm thầm giữ
    nền cũ chính là bug đang sửa; job lỗi rõ ràng còn hơn output sai."""
    pid = comfy_submit(build_qwen_create_workflow(
        [comfy_upload(person_path), comfy_upload(bg_path)], _TRYON_BG_POS, prefix + "-bg",
        realism=False, negative_prompt=_TRYON_BG_NEG, target_mp=TRYON_MP))
    outp = comfy_fetch_output(
        comfy_poll(pid, job_id, deadline_sec=600, prog_lo=0.82, prog_hi=0.93, prog_step="ghép nền (pass 2)"),
        exts=IMG_EXTS)
    if not outp:
        raise RuntimeError("Ghép nền: ComfyUI không trả ảnh (pass 2)")
    return outp
# #endregion

def run_tryon(job):
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    model_key = inputs.get("model") or inputs.get("ref") or inputs.get("image")
    product_key = inputs.get("product") or inputs.get("garment")
    # ALD 16/08/2026 - KHÔI PHỤC cleanOnly: 20/07 "Try-On tối giản" bỏ nhánh này ở worker nhưng FE vẫn có toggle
    # "Chỉ làm sạch ảnh" VÀ template Singer đa-outfit dựng node tryon cleanOnly làm bước làm sạch trước Motion —
    # các flow đó chết với "tryon cần inputs.product". cleanOnly chỉ cần ảnh model, không cần sản phẩm.
    clean_only = str(params.get("cleanOnly") or params.get("clean_only") or "").lower().strip() in ("1", "true", "yes", "on")
    if not model_key or (not product_key and not clean_only):
        raise RuntimeError("tryon cần inputs.model (người) + inputs.product (trang phục)")
    # ALD 16/08/2026 - KHÔI PHỤC đa-góc sản phẩm (theo yêu cầu user, đảo quyết định "tối giản" 20/07): FE vẫn có
    # UI "2 ảnh (đa góc)" + cổng Ảnh SP 2 → inputs.product2 = CÙNG sản phẩm chụp mặt sau/bên hông → Qwen image3.
    product2_key = inputs.get("product2") or inputs.get("garment2")
    product_keys = [k for k in (product_key, product2_key) if k]
    tmp = tempfile.mkdtemp(prefix=f"tryon-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải input")
    m_local = api_download(model_key, os.path.join(tmp, "model" + os.path.splitext(model_key)[1]))
    p_locals = [api_download(k, os.path.join(tmp, f"product{i}" + os.path.splitext(k)[1]))
                for i, k in enumerate(product_keys)]
    p_local = p_locals[0] if p_locals else None
    # ALD 11/06/2026 - Crop SÁT sản phẩm (bg-remover, model=object) → món đồ lấp đầy khung → Qwen/Gemini áp ĐÚNG
    # tỉ lệ lên người (fix "sản phẩm quá nhỏ, mẫu quá to" do đồ nằm bé giữa nền rộng). Lỗi/timeout → giữ ảnh gốc.
    if TRYON_PRODUCT_AUTOCROP:
        for i, p in enumerate(p_locals):
            try:
                cropped = os.path.join(tmp, f"product{i}_crop.jpg")
                _bg_remove_file(p, cropped, model="object", crop=True)
                p_locals[i] = cropped
            except Exception as e:
                api_log(job_id, f"crop sản phẩm {i} lỗi → dùng ảnh gốc: {e}", "warn")
        p_local = p_locals[0] if p_locals else None
    # ALD 16/08/2026 - GHÉP NỀN: cổng 'Nền' (FE useBackground) → inputs.background. Tải sớm để mọi nhánh
    # provider dùng chung; thiếu ảnh nền dù user bật toggle → không sao (toggle bật nhưng chưa nối dây).
    bg_key = inputs.get("background") or inputs.get("bg") or inputs.get("scene")
    bg_local = None
    if bg_key:
        bg_local = api_download(bg_key, os.path.join(tmp, "background" + (os.path.splitext(bg_key)[1] or ".png")))
        api_log(job_id, "Có ảnh Nền — sau khi thay đồ sẽ chạy pass 2 ghép người vào bối cảnh", "info")
    # ALD 11/06/2026 - đọc provider SỚM: gemini/huggingface là API call thuần — KHÔNG upload ComfyUI (box GPU
    # bận/chết vẫn chạy được; trước đây gemini vẫn upload phí và phụ thuộc ComfyUI vô cớ).
    provider = str(params.get("provider") or "qwen").lower().strip()
    m_name = p_name = None; p_names = []
    if provider not in ("gemini", "huggingface"):
        api_progress(job_id, 0.15, "upload vào ComfyUI")
        m_name = comfy_upload(m_local)
        p_names = [comfy_upload(p) for p in p_locals]
        p_name = p_names[0] if p_names else None
    # ALD 16/08/2026 - Nhánh cleanOnly (không thay đồ): img2img Qwen denoise THẤP trên chính ảnh model — neo bố
    # cục/người/đồ, chỉ làm sạch + nét (bài học "tryon clean denoise 0.30"). Luôn chạy ComfyUI self-host, đặt
    # TRƯỚC nhánh gemini/HF (các nhánh đó là prompt thay-đồ, không áp dụng cho làm sạch).
    if clean_only:
        api_progress(job_id, 0.3, "Làm sạch ảnh (Qwen img2img)")
        prompt_c = str(params.get("prompt") or "").strip() or (
            "Clean and refine this photo of the person. Keep the same person, outfit, face, hair, pose, "
            "full-body framing and background; remove artifacts and noise, even out the lighting, sharpen details. "
            "Photorealistic, natural skin texture.")
        den = float(os.environ.get("TRYON_CLEAN_DENOISE", "0.3"))
        pid = comfy_submit(build_qwen_create_workflow(
            [m_name or comfy_upload(m_local)], prompt_c, f"tryon-clean-{job_id[:8]}",
            realism=True, denoise=den, target_mp=TRYON_MP))
        out = comfy_fetch_output(comfy_poll(pid, job_id, deadline_sec=600), exts=IMG_EXTS)
        if not out:
            raise RuntimeError("ComfyUI không trả ảnh clean")
        if bg_local:
            api_progress(job_id, 0.82, "ghép nền (Qwen pass 2)")
            out = _tryon_compose_background(job_id, out, bg_local, f"tryon-{job_id[:8]}")
        out = _tryon_postprocess(out, params, job_id)
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out, content_type="image/png")
        return out
    # ALD 21/06/2026 - BỎ auto-analyze Ollama vision khỏi TryOn (theo yêu cầu): user TỰ CHỌN loại đồ ở node.
    # KHÔNG gọi Ollama nữa → tryon không nạp vision model, không tranh VRAM với ComfyUI, nhanh hơn (bỏ ~2-90s).
    # ALD 28/06/2026 - Rỗng/'auto' → nhánh AUTO (generic): thay NGUYÊN bộ từ ảnh sản phẩm, KHÔNG cần chọn loại
    # (xem _qwen_tryon_prompts nhánh 'auto'). Trước đây ép 'upper' → chỉ thay áo, giữ quần gốc → hay thay sai.
    garment = str(params.get("garment_type") or params.get("garmentType") or "").lower().strip()
    if not garment:
        garment = "auto"
        api_log(job_id, "Không chọn loại đồ → AUTO: thay nguyên bộ từ ảnh sản phẩm", level="info")
    # ALD 01/07/2026 - "Ghi chú thêm": user dặn thêm điều muốn GIỮ/ĐỔI ngoài thay đồ (vd "giữ nguyên nón và trang
    # sức"). Dịch VN→EN (Qwen không hiểu tiếng Việt) rồi chèn ưu tiên cao vào prompt của cả 3 provider.
    extra_raw = str(params.get("extraPrompt") or params.get("extra_prompt") or params.get("keepNote") or "").strip()
    extra_en = ""
    if extra_raw:
        extra_en = _translate_prompt_en(extra_raw, job_id) or extra_raw
        api_log(job_id, f"Ghi chú thêm try-on: {extra_en[:160]}", "info")
    # provider='gemini' → Gemini image-edit (Nano Banana): thay vật nhỏ (giày) chính xác hơn Qwen, API call
    # (không cần GPU). ALD 11/06/2026 - key CHỈ từ node API Key (nối cổng) / field node — env đã bỏ hẳn.
    if provider == "gemini":
        gem_key = _gemini_key(params)
        if not gem_key:
            raise RuntimeError("Gemini try-on cần API key — nối node API Key (Type: Gemini) vào cổng API key của node, hoặc đổi Provider = Self-host (không cần key).")
        if not _valid_gemini_key(gem_key):
            raise RuntimeError("Key Gemini không đúng định dạng (phải dạng 'AIza…', ~39 ký tự, KHÔNG khoảng trắng). "
                               "Có thể bạn dán nhầm text khác vào ô key. Lấy key ở https://aistudio.google.com/apikey, "
                               "hoặc đổi Provider = Qwen (không cần key).")
        api_progress(job_id, 0.3, f"Gemini image-edit ({GEMINI_IMAGE_MODEL})")
        with open(m_local, "rb") as f: mb = f.read()
        parts = [(mb, _mime(m_local))]
        for p in p_locals:
            with open(p, "rb") as f: parts.append((f.read(), _mime(p)))
        out = os.path.join(tmp, "gemini_tryon.png")
        prompt_g = _gemini_tryon_prompt(garment, extra=extra_en)
        # ALD 16/08/2026 - khôi phục đa-góc: p_locals có thể là 2 ảnh CÙNG sản phẩm (parts đã gửi hết bên trên).
        if len(p_locals) > 1:
            prompt_g += (" The two product images show the SAME single product from two angles — typically the "
                         "front and the back. Use both views only to render that ONE product accurately; "
                         "the second product image is NOT an additional garment to add.")
        _gemini_edit(parts, prompt_g, gem_key, out,
                     aspect_ratio=_gemini_aspect(_img_size(m_local)))
        # ALD 16/08/2026 - Ghép nền pass 2 (Gemini): ảnh 1 = kết quả thay đồ, ảnh 2 = bối cảnh. Tách pass riêng
        # (không nhét nền vào pass thay đồ) để mỗi lệnh 1 việc — cùng triết lý pass 2 của nhánh Qwen.
        if bg_local:
            api_progress(job_id, 0.8, "ghép nền (Gemini pass 2)")
            with open(out, "rb") as f: ob = f.read()
            with open(bg_local, "rb") as f: bb = f.read()
            out2 = os.path.join(tmp, "gemini_tryon_bg.png")
            _gemini_edit([(ob, "image/png"), (bb, _mime(bg_local))], _TRYON_BG_POS, gem_key, out2,
                         aspect_ratio=_gemini_aspect(_img_size(out)))
            out = out2
        out = _tryon_postprocess(out, params, job_id)
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out, content_type="image/png")
        return out
    # #region ALD 11/06/2026 - provider='huggingface' → image-to-image (Qwen-Image-Edit-2511 qua fal-ai router):
    # cùng prompt try-on như Gemini (provider-agnostic), model + tất cả góc độ sản phẩm qua image_urls.
    if provider == "huggingface":
        hf_key = _hf_key(params)
        hf_id, fal_id, q = _hf_resolve(params.get("hfModel"), "image-to-image")
        api_progress(job_id, 0.3, f"HF image-edit ({hf_id})")
        # ALD 11/06/2026 (fix theo test thật): prompt Gemini làm 2511 KHÔNG áp sản phẩm → dùng chung bộ prompt
        # Qwen self-host (_qwen_tryon_prompts, có negative); image_size cap /8 theo ảnh model → hết mặt to sai tỉ lệ.
        # ALD 16/08/2026 - khôi phục đa-góc: gửi tối đa 2 ảnh SP (image_urls[1]=chính, [2]=góc khác).
        uris = [_hf_data_uri(m_local)] + [_hf_data_uri(p) for p in p_locals[:2]]
        pos_t, neg_t = _qwen_tryon_prompts(garment, extra=extra_en)
        if len(p_locals) > 1:
            pos_t += (" Image 2 and image 3 show the SAME single product from two angles — typically image 2 is "
                      "the front and image 3 is the back. Use both views only to render that ONE product "
                      "accurately; image 3 is NOT an additional garment to add.")
        payload = {"prompt": pos_t, "negative_prompt": neg_t, "image_url": uris[0], "image_urls": uris}
        isz = _hf_image_size(_img_size(m_local))
        if isz:
            payload["image_size"] = isz
        res = _hf_call(fal_id, payload, hf_key, q, job_id, 600, 0.3, 0.9, "HF tryon")
        out = os.path.join(tmp, "hf_tryon.png")
        _hf_fetch(_hf_first_image(res), out)
        # ALD 16/08/2026 - Ghép nền pass 2 qua fal-ai (cùng họ Qwen-Edit nên dùng chung prompt _TRYON_BG_*).
        if bg_local:
            api_progress(job_id, 0.82, "ghép nền (HF pass 2)")
            uris2 = [_hf_data_uri(out), _hf_data_uri(bg_local)]
            payload2 = {"prompt": _TRYON_BG_POS, "negative_prompt": _TRYON_BG_NEG, "image_url": uris2[0], "image_urls": uris2}
            isz2 = _hf_image_size(_img_size(out))
            if isz2:
                payload2["image_size"] = isz2
            res2 = _hf_call(fal_id, payload2, hf_key, q, job_id, 600, 0.82, 0.93, "HF ghép nền")
            out = os.path.join(tmp, "hf_tryon_bg.png")
            _hf_fetch(_hf_first_image(res2), out)
        out = _tryon_postprocess(out, params, job_id)
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out, content_type="image/png")
        return out
    # #endregion
    api_progress(job_id, 0.3, "Qwen-Image-Edit (tryon)")
    # Giày/dép: bàn chân quá nhỏ trong full-body → FEET-DETAILER (crop chân → thay giày res cao → ghép lại).
    if garment in GARMENT_SHOES and TRYON_FEET_DETAILER:
        try:
            comp = _run_shoes_detailer(job_id, m_local, p_name, garment, f"tryon-{job_id[:8]}", tmp,
                                       frac=params.get("feetCrop") or params.get("feet_crop"))
            if comp:
                if bg_local:
                    comp = _tryon_compose_background(job_id, comp, bg_local, f"tryon-{job_id[:8]}")
                comp = _tryon_postprocess(comp, params, job_id)
                api_progress(job_id, 0.95, "upload output")
                api_upload_output(job_id, comp, content_type="image/png")
                return comp
            api_log(job_id, "feet-detailer trống → tryon toàn ảnh", "warn")
        except Exception as e:
            api_log(job_id, f"feet-detailer lỗi → tryon toàn ảnh: {e}", "warn")
    # Kích thước latent /64-aligned theo ảnh model → fix band đáy; giày dùng mp cao hơn (rõ bàn chân).
    mp = _tryon_mp(garment); twh = None
    dims = _img_size(m_local)
    if dims:
        twh = _fit_aligned(dims[0], dims[1], mp=mp, align=64)
        api_log(job_id, f"tryon {garment}: {twh[0]}×{twh[1]} /64-aligned (mp~{mp})", "info")
    pid = comfy_submit(build_qwen_tryon_workflow(m_name, p_names, garment, f"tryon-{job_id[:8]}", target_wh=twh, extra_prompt=extra_en))
    outputs = comfy_poll(pid, job_id, deadline_sec=600)
    api_progress(job_id, 0.9, "tải kết quả")
    out = comfy_fetch_output(outputs, exts=IMG_EXTS)
    if not out: raise RuntimeError("ComfyUI không trả ảnh tryon")
    if bg_local:
        api_progress(job_id, 0.82, "ghép nền (Qwen pass 2)")
        out = _tryon_compose_background(job_id, out, bg_local, f"tryon-{job_id[:8]}")
    out = _tryon_postprocess(out, params, job_id)
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out, content_type="image/png")
    return out

def _qwen_dims(params):
    """Từ aspectRatio + quality → (W, H, force_size). force_size=True khi user chọn tỉ lệ cụ thể."""
    import math
    ar = str(params.get("aspectRatio") or params.get("aspect_ratio") or "auto").lower()
    q = str(params.get("quality") or "standard").lower()
    # ALD 12/06/2026 - bỏ phân nhánh quality (1.0/1.6MP đều DƯỚI native 1.76MP của Qwen → da mịn AI);
    # render thẳng ở CREATE_IMAGE_MP cho mọi mức quality.
    mp = CREATE_IMAGE_MP                           # megapixels mục tiêu
    table = {"1:1": (1, 1), "4:5": (4, 5), "3:4": (3, 4), "2:3": (2, 3), "3:2": (3, 2), "9:16": (9, 16), "16:9": (16, 9)}
    if ar == "auto" or ar not in table:
        if params.get("width") and params.get("height"):
            return int(params["width"]), int(params["height"]), False
        rw, rh, force = 1, 1, False              # auto + không ảnh → vuông 1MP
    else:
        rw, rh, force = table[ar][0], table[ar][1], True
    k = math.sqrt(mp * 1_000_000 / (rw * rh))
    W = max(256, int(round(rw * k / 16)) * 16)
    H = max(256, int(round(rh * k / 16)) * 16)
    return W, H, force

# #region ALD 10/06/2026 - 20 GÓC MÁY nhiếp ảnh (create-image): id khớp FE InspectorCreateImage.vue.
# Phrase tiếng Anh (Qwen ăn prompt EN tốt nhất) mô tả cụ thể khung hình + vị trí camera.
CAMERA_ANGLES = {
    "can-mat":        "extreme close-up portrait framing the face, showing facial details and expression",
    "can-nhat":       "macro extreme close-up focusing on one small detail of the subject (eyes, lips)",
    "trung-canh":     "medium shot framing the subject from the waist up",
    "toan-than":      "full body shot showing the entire subject from head to toe",
    "toan-canh":      "long shot — the subject visible in full with plenty of surrounding scenery",
    "toan-canh-rong": "extreme wide shot — the subject appears small within a vast landscape",
    "qua-vai":        "over-the-shoulder shot, seen from behind another person's shoulder in the foreground",
    "goc-nhin":       "first-person POV shot showing exactly what the subject is seeing",
    "goc-cao":        "high-angle shot, camera looking down at the subject making them appear smaller",
    "goc-thap":       "low-angle shot, camera looking up at the subject making them appear taller and powerful",
    "tren-cao":       "bird's-eye aerial top-down shot looking straight down at the subject from above",
    "goc-nghieng":    "dutch angle shot with the camera visibly tilted for dramatic tension",
    "chi-tiet":       "insert detail shot clearly showing one small object or detail",
    "boi-canh":       "establishing shot showing the location and environment before the subject",
    "hai-nguoi":      "two-shot framing both subjects together in one frame",
    "doi-dien":       "reverse angle shot showing the opposite side of the conversation or scene",
    "chan-dung-nghieng": "side profile portrait of the subject in elegant lighting",
    "tu-sau":         "shot from directly behind the subject, showing their back and what lies ahead",
    "theo-doi":       "tracking shot feel — subject in motion with directional motion blur in the background",
    "chi-tiet-nho":   "extreme macro shot of one tiny but important detail, shallow depth of field",
}
# #endregion

# #region ALD 11/06/2026 - Pool "Model mẫu" (bảng model_refs, admin upload qua /settings). create-image bốc
# NGẪU NHIÊN 1 mẫu/output theo gender+age_group → render nhiều ảnh ra nhiều gương mặt khác nhau (hết "na ná").
def _fetch_model_ref_keys(params):
    """Danh sách storage_key model mẫu ACTIVE theo gender+age_group (gọi API worker). [] nếu rỗng/lỗi."""
    g = str(params.get("gender") or params.get("modelStandardPreset") or "female").strip().lower()
    if g not in ("female", "male"):
        g = "female"
    a = str(params.get("age_group") or params.get("ageGroup") or "").strip().lower()
    try:
        qp = {"gender": g}
        if a in ("young", "middle", "old"):
            qp["age_group"] = a
        r = requests.get(f"{API_URL}/worker/model-refs", params=qp, headers=WORKER_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        return [it["key"] for it in (r.json().get("items") or []) if it.get("key")]
    except Exception:
        return []

# ALD 11/06/2026 - tách phần "chọn + tải về local" khỏi phần upload ComfyUI để nhánh provider HuggingFace
# (cần local path làm data URI, KHÔNG đụng ComfyUI) dùng chung logic bốc mặt random.
def _model_face_local(pool_keys, disk_paths, cache, tmp):
    """Chọn 1 ảnh MODEL NGUYÊN (mặt + dáng) cho 1 output: ưu tiên bốc NGẪU NHIÊN trong pool model_refs (theo
    category), fallback ảnh disk cũ. Cache local path theo nguồn để khỏi tải lại. Trả local path hoặc None."""
    if pool_keys:
        src = ("key", random.choice(pool_keys))
    elif disk_paths:
        src = ("path", disk_paths[0])
    else:
        return None
    ck = "local:" + src[1]
    if ck in cache:
        return cache[ck]
    try:
        if src[0] == "key":
            loc = api_download(src[1], os.path.join(tmp, "model-" + os.path.basename(src[1]).replace("/", "_")))
        else:
            loc = src[1]
        # ALD 14/06/2026 - DÙNG NGUYÊN ẢNH MODEL làm ref (BỎ head-crop top-45% cũ). Yêu cầu user: ref lấy NGUYÊN người
        # mẫu (mặt + dáng), KHÔNG cần tách đâu mặt đâu thân; tóc/trang phục/bối cảnh/biểu cảm/pose theo prompt end-user
        # (_model_standard_prompt đã ra lệnh "ignore clothing/hair/pose/background"). Thiếu phần nào → model tự vẽ thêm.
        cache[ck] = loc
        return loc
    except Exception:
        return None

def _model_face_for_output(pool_keys, disk_paths, cache, tmp):
    """Như _model_face_local nhưng trả comfy name (upload ComfyUI, cache để khỏi upload lại) — cho nhánh Qwen local."""
    loc = _model_face_local(pool_keys, disk_paths, cache, tmp)
    if not loc:
        return None
    ck = "comfy:" + loc
    if ck in cache:
        return cache[ck]
    try:
        name = comfy_upload(loc)
        cache[ck] = name
        return name
    except Exception:
        return None
# #endregion

# #region ALD 13/06/2026 - JSON prompt mode (FE gạt Text/JSON): user dán JSON cấu trúc
# (meta/characters/scene/cinematic/negative_prompt) → ghép thành prompt tiếng Anh + tách negative + aspect_ratio.
# Tolerant: thiếu key thì bỏ qua; parse fail → trả (None,...) để caller fallback về text thường.
def _build_prompt_from_json(raw, job_id=None, exclude_subject=False):
    # exclude_subject=True (node SS I2V): BỎ mô tả chủ thể (characters/subject) vì ẢNH INPUT đã là chủ thể —
    # prompt chỉ còn cảnh/hành động/máy quay → ảnh đi CHUNG prompt, model giữ chủ thể từ ảnh thay vì vẽ lại theo text.
    import json as _json
    try:
        data = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        if job_id: api_log(job_id, f"promptMode=json: JSON lỗi cú pháp ({e}) — fallback dùng text", "warn")
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    parts = []
    def _add(v):
        if v is None: return
        if isinstance(v, (list, tuple)):
            for x in v: _add(x)
        elif isinstance(v, dict):
            for x in v.values(): _add(x)
        else:
            s = str(v).strip()
            if s: parts.append(s.rstrip("."))
    CH_ORDER = ("identity", "facial_expression", "hair", "makeup", "nails", "clothing",
                "pose_action", "skin_lighting", "aura")
    # 1) nhân vật (đối tượng chính) — BỎ khi exclude_subject (I2V: ảnh input là chủ thể)
    if not exclude_subject:
        for ch in (data.get("characters") or []):
            if isinstance(ch, dict):
                for key in CH_ORDER: _add(ch.get(key))
                for k, v in ch.items():
                    if k not in CH_ORDER and k != "id": _add(v)
            else:
                _add(ch)
    # 2) cảnh + 3) máy quay/ánh sáng/màu (+ field VIDEO cho node SS: camera/motion/lighting/action — chỉ thêm nếu có)
    _add(data.get("scene"))
    _add(data.get("cinematic"))
    _add(data.get("camera"))
    _add(data.get("motion"))
    _add(data.get("lighting"))
    _add(data.get("action"))
    # 4) phong cách/chất ảnh (đặt cuối để chốt realism) + fallback mô tả top-level
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    _add(meta.get("style"))
    if not exclude_subject:
        _add(data.get("description") or data.get("prompt") or data.get("subject"))
    prompt = ". ".join(p for p in parts if p)
    if prompt: prompt += "."
    neg = data.get("negative_prompt") or data.get("negative")
    if isinstance(neg, (list, tuple)): neg = ", ".join(str(x).strip() for x in neg if str(x).strip())
    aspect = meta.get("aspect_ratio") or data.get("aspect_ratio")
    return (prompt or None), (str(neg).strip() if neg else None), (str(aspect).strip() if aspect else None)
# #endregion

def run_create_image(job):
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    # #region ALD 14/07/2026 - Chế độ kiến trúc: không trộn prompt realism dành cho người mẫu.
    _content_domain = str(params.get("domain") or params.get("contentDomain") or "").strip().lower()
    _architecture_mode = _content_domain in ("architecture", "construction", "building")
    # #endregion
    # gom mọi input ảnh động: image1..imageN (sort theo số) + fallback image/ref/product/model
    import re as _re
    num_keys = sorted([k for k in inputs if _re.match(r"^image\d+$", k)], key=lambda k: int(k[5:]))
    img_keys, _seen = [], set()
    for k in num_keys + ["image", "ref", "product", "model"]:
        for x in (inputs.get(k) if isinstance(inputs.get(k), list) else ([inputs.get(k)] if inputs.get(k) else [])):
            if x and x not in _seen: _seen.add(x); img_keys.append(x)
    img_keys = img_keys[:QWEN_EDIT_MAX_REFS]
    # #region ALD 12/06/2026 - TẠM TẮT ảnh Ref user theo yêu cầu: dù đã tách nền + crop mặt, Qwen-Edit vẫn bê
    # trang phục/cảnh từ ref (cổ trang → output cổ trang, kệ prompt). Tắt = bỏ qua ref, chạy thuần theo prompt
    # (model-standard nếu đang bật vẫn hoạt động bình thường). Bật lại: CREATE_IMAGE_USER_REFS=1 + recreate worker.
    # ALD 12/06/2026 - BẬT LẠI mặc định (default "1"): thủ phạm "lệch prompt" là prompt tiếng Việt chưa dịch,
    # KHÔNG phải img ref. Giờ đã dịch VN→EN nên ref (chỉ lấy face+body, đã tách nền+crop) chạy đúng. Vẫn giữ
    # công tắc CREATE_IMAGE_USER_REFS=0 để tắt khẩn cấp nếu cần.
    if img_keys and os.environ.get("CREATE_IMAGE_USER_REFS", "1").strip().lower() in ("0", "false", "no", "off"):
        api_log(job_id, f"Ảnh Ref đang TẮT ({len(img_keys)} ảnh bị bỏ qua) — chạy theo prompt", "warn")
        img_keys = []
    if _architecture_mode and not img_keys:
        raise RuntimeError("create-image kiến trúc cần ít nhất 1 ảnh đầu vào; không được tự sinh người mẫu/cảnh mới")
    # #endregion
    # ALD 13/06/2026 - imageMode (FE toggle khi có ảnh ref): 'reference' (MẶC ĐỊNH — ref = identity, sinh ảnh
    # HOÀN TOÀN MỚI theo prompt, KHÔNG bê nền/pose/đồ từ ref) | 'edit' (SỬA ảnh gốc: giữ bố cục/nền/style, chỉ
    # đổi theo prompt — vd input 3D Iron Man + "biến thành Spider-Man" → 3D Spider-Man cùng tư thế/nền).
    # Edit mode: KHÔNG tách nền/crop mặt, giữ nguyên ảnh + tỉ lệ (latent từ ảnh).
    img_mode = (params.get("imageMode") or params.get("image_mode") or "reference").strip().lower()
    if img_mode not in ("reference", "edit"): img_mode = "reference"
    prompt = params.get("prompt") or params.get("positive_prompt") or ""
    # #region ALD 14/06/2026 - promptMode='json' (FE gạt Text/JSON): TÔN TRỌNG TOGGLE — JSON mode thì LUÔN dùng JSON,
    # TUYỆT ĐỐI KHÔNG bao giờ dùng ô Text (trước đây parse fail → rớt về Text = bug). Parse được → ghép prompt EN +
    # tách negative + aspect_ratio; JSON lỗi cú pháp → đưa NGUYÊN VĂN JSON cho model (vẫn liên quan ý đồ user, hơn là
    # lấy ô Text không liên quan). JSON đã tiếng Anh → bỏ dịch VN→EN.
    _json_neg = None
    _prompt_from_json = False
    if str(params.get("promptMode") or params.get("prompt_mode") or "text").strip().lower() == "json":
        _prompt_from_json = True
        _raw_json = str(params.get("promptJson") or params.get("prompt_json") or "").strip()
        _jp, _jn, _jar = _build_prompt_from_json(_raw_json, job_id)
        if _jp:
            prompt = _jp; _json_neg = _jn
            if _jar:
                params["aspectRatio"] = _jar   # meta.aspect_ratio → override tỉ lệ ảnh (xem _qwen_dims)
            api_log(job_id, f"promptMode=json → ghép prompt: {prompt[:160]}", "info")
        else:
            prompt = _raw_json   # JSON lỗi cú pháp/rỗng → dùng nguyên văn JSON, KHÔNG dùng ô Text
            api_log(job_id, "promptMode=json nhưng JSON LỖI CÚ PHÁP → dùng nguyên văn JSON làm prompt (KHÔNG dùng ô Text). "
                            "Hãy sửa JSON cho hợp lệ để chất lượng tốt nhất.", "warn")
    # #endregion
    # #region ALD 12/06/2026 - DỊCH PROMPT VN→EN (root-cause của loạt lỗi "lệch prompt"): text-encoder của
    # Qwen-Image (qwen_2.5_vl) hiểu tiếng Việt RẤT yếu → bỏ qua mô tả cảnh/trang phục, ra ảnh generic (vd
    # "phòng ngủ, váy body" → ra cô gái ngoài vườn váy dài). Dịch qua Ollama local trước khi dựng prompt.
    # Fail-safe: lỗi/không có Ollama → giữ nguyên prompt gốc (không chết job). JSON mode → bỏ dịch (đã EN).
    if not _prompt_from_json:
        _prompt_en = _translate_prompt_en(prompt, job_id)
        if _prompt_en and _prompt_en != prompt:
            api_log(job_id, f"Đã dịch prompt sang tiếng Anh cho Qwen: {_prompt_en[:160]}", "info")
            prompt = _prompt_en
    # #endregion
    # ALD 13/06/2026 - FALLBACK: user KHÔNG nhập prompt → dùng bối cảnh mặc định (studio nền xám trơn) để ảnh
    # có nền/cảnh tử tế thay vì để Qwen tự bịa. Sau dịch (fallback đã là tiếng Anh) + trước negative/góc máy.
    if not prompt.strip():
        prompt = CREATE_IMAGE_FALLBACK_BG
        api_log(job_id, "Không có prompt → dùng bối cảnh mặc định (studio nền xám).", "info")
    # #region ALD 11/06/2026 - Negative prompt RIÊNG từ FE (params.negativePrompt). Trước đây node không có
    # ô negative → user dán cả khối negative vào ô Prompt → bị nhét vào POSITIVE, model vẽ ĐÚNG những thứ cần
    # tránh (vd "braid, twin tails" → ra tóc tết). Merge negative user TRƯỚC + PHOTO_REALISM_NEGATIVE mặc định.
    user_neg = str(params.get("negativePrompt") or params.get("negative_prompt") or "").strip().strip(",").strip()
    # ALD 13/06/2026 - JSON mode: gộp negative_prompt từ JSON vào ô negative user (nếu có).
    if _json_neg:
        user_neg = ((user_neg + ", " + _json_neg) if user_neg else _json_neg).strip().strip(",").strip()
    neg_merged = (user_neg or None) if _architecture_mode else (
        (user_neg + ", " + PHOTO_REALISM_NEGATIVE) if user_neg else None
    )
    # #endregion
    # ALD 10/06/2026 - Góc máy (20 loại nhiếp ảnh, chọn ở Inspector FE) → ghép phrase EN vào prompt.
    # ALD 13/06/2026 - JSON mode: KHÔNG cộng góc máy/beauty-body của FE (JSON đã tả đủ camera + dáng → nguồn duy nhất).
    _cam = str(params.get("cameraAngle") or params.get("camera_angle") or "").strip()
    if _cam and _cam in CAMERA_ANGLES and not _prompt_from_json:
        prompt = (prompt.rstrip().rstrip(".,") + ". " if prompt.strip() else "") + \
                 f"Camera angle: {CAMERA_ANGLES[_cam]}."
    # ALD 13/06/2026 - Ép DÁNG đẹp (ngực to/eo thon/mông to/chân dài/da trắng) khi tả phụ nữ — giữ chất thật.
    if CREATE_IMAGE_BEAUTY_BODY and _describes_woman(prompt) and not _prompt_from_json:
        prompt = (prompt.rstrip().rstrip(".,") + ". ") + BEAUTY_BODY_POSITIVE
        api_log(job_id, "Thêm mô tả dáng người mẫu (curvy + da trắng, giữ realism).", "info")
    try:
        output_count = int(params.get("outputCount") or params.get("output_count") or params.get("num_outputs") or 1)
    except Exception:
        output_count = 1
    output_count = max(1, min(10, output_count))
    tmp = tempfile.mkdtemp(prefix=f"img-{job_id[:8]}-")
    api_progress(job_id, 0.1, "tải input")
    # ALD 11/06/2026 - provider: ''/'qwen' = self-host ComfyUI (mặc định); 'huggingface' = HF Inference Providers
    # (fal-ai router, token user). Nhánh HF chỉ cần local path (data URI) — KHÔNG upload ComfyUI (box bận vẫn chạy).
    provider = str(params.get("provider") or "").lower().strip()
    # ALD 13/06/2026 - Model self-host (dropdown "Model (self-host)" — chỉ áp khi provider qwen/self-host).
    # qwen-edit = mặc định (Qwen path, DUY NHẤT sửa/ghép/face-swap). flux-*/sd35-* = text→image (tay đẹp/ảnh thật),
    # CHỈ chạy khi KHÔNG có ảnh ref / model-standard (xem nhánh dispatch trước vòng lặp Qwen). Có ref/model-standard
    # → báo lỗi rõ ràng để người dùng không tưởng đang chạy Flux trong khi ComfyUI load Qwen.
    model = str(params.get("model") or "qwen-edit").lower().strip()
    names, locals_ = [], []
    for i, k in enumerate(img_keys):
        loc = api_download(k, os.path.join(tmp, f"img{i}" + os.path.splitext(k)[1]))
        if img_mode == "edit":
            # EDIT MODE: giữ NGUYÊN ảnh (không tách nền/crop) → Qwen sửa theo prompt, giữ bố cục/nền/style.
            locals_.append(loc)
            if provider != "huggingface":
                names.append(comfy_upload(loc))
            continue
        # #region ALD 12/06/2026 - Ref user = THAM KHẢO face+body, KHÔNG sao chép (user báo 12/06: output clone
        # nguyên ảnh ref cả nền xám lẫn khung). Chỉ dẫn bằng chữ không thắng nổi ảnh full-body có nền → TÁCH NỀN
        # + crop sát người ngay tại worker (bg-remover, model=human) — y như pool model-standard ("ảnh ref crop
        # sát nên ít gì để rỉ"). Lỗi tách nền → dùng ảnh gốc (không chết job).
        try:
            cut = os.path.join(tmp, f"img{i}-cut.jpg")
            _bg_remove_file(loc, cut, model="human", crop=True)
            loc = cut
        except Exception as e:
            log(f"bg-remove ref {i} lỗi (dùng ảnh gốc): {e}")
        # ALD 12/06/2026 (lần 2) - tách nền CHƯA đủ: trang phục nằm TRÊN người trong cutout nên Qwen vẫn bê
        # nguyên bộ đồ + tự vẽ cảnh hợp đồ (bắt tận tay: ref cổ trang mũ phượng → output y chang, kệ prompt
        # "váy body phòng ngủ"). Cutout CAO (h > w×1.25 = có thân) → cắt lấy vùng ĐẦU (top 45%) đúng công thức
        # pool model-standard "crop sát mặt nên ít gì để rỉ"; ảnh vuông-ish (đã là chân dung) giữ nguyên.
        try:
            dims = _img_size(loc)
            if dims and dims[1] > dims[0] * 1.1:  # ALD 12/06 - 1.25→1.1: ảnh pool 4:5 (1122×1402=1.249) lách ngưỡng cũ → bị chép nguyên
                head = os.path.join(tmp, f"img{i}-head.jpg")
                _ff_crop(loc, head, dims[0], max(64, int(dims[1] * 0.45)), 0, 0)
                loc = head
        except Exception as e:
            log(f"face-crop ref {i} lỗi (dùng cutout): {e}")
        # #endregion
        locals_.append(loc)
        if provider != "huggingface":
            names.append(comfy_upload(loc))
    using_model_standard = False
    ms_pool, ms_disk, ms_cache = [], [], {}
    # ALD 14/06/2026 - model-standard (POOL "Model mẫu") 1 BƯỚC: bốc NGẪU NHIÊN 1 ảnh model NGUYÊN làm ref danh tính
    # (mặt + dáng), prompt giữ danh tính + đổi tóc/đồ/cảnh/biểu cảm. Chạy cả TEXT lẫn JSON mode. Chỉ khi KHÔNG gắn
    # ảnh ref riêng (locals_ rỗng) + bật Model mẫu.
    if not locals_ and _use_default_model_standard(params):
        # ALD 11/06/2026 - model-standard = POOL "Model mẫu" (bảng model_refs, admin upload qua /settings) lọc theo
        # gender+age_group; BỐC NGẪU NHIÊN 1 mẫu cho TỪNG output (xem vòng lặp dưới) → render nhiều ảnh ra nhiều
        # gương mặt khác nhau (hết "10 tấm na ná"). Pool rỗng → fallback ảnh disk cũ ([:1]) → rỗng nữa → 0-ref
        # text-to-image. Ref là CHÂN DUNG đã tách nền → Qwen chỉ lấy gương mặt; pose/dáng/tóc/đồ/cảnh theo prompt.
        ms_pool = _fetch_model_ref_keys(params)
        ms_disk = [] if ms_pool else _default_model_standard_paths(params)[:1]
        using_model_standard = bool(ms_pool or ms_disk)
        if using_model_standard:
            # ALD 14/06/2026 - 1-step dùng NGUYÊN ảnh model làm ref danh tính; prompt giữ mặt+dáng, đổi tóc/đồ/cảnh.
            prompt = _model_standard_prompt(prompt, params)
    # ALD 12/06/2026 - User gắn ảnh Ref: trước đây prompt user đi thẳng → Qwen-Edit copy NGUYÊN ảnh ref (cả
    # quần/áo/váy lẫn nền/pose). Yêu cầu user 12/06: ref CHỈ lấy FACE + BODY; trang phục/pose/bối cảnh phải
    # theo mô tả. Áp cho cả nhánh self-host lẫn HF (prompt dùng chung).
    if locals_ and img_mode == "edit":
        # EDIT MODE: SỬA ảnh gốc theo prompt, GIỮ bố cục/nền/style — KHÔNG sinh ảnh mới.
        prompt = (
            "EDIT the attached image following this instruction. Keep the SAME composition, framing, subject "
            "placement, pose, camera angle, lighting, art style and background — change ONLY what the instruction "
            "says, and keep everything else identical to the original image:\n" + (prompt or "")
        )
    elif locals_ and not using_model_standard:
        # ALD 14/06/2026 - nhét rule MỆNH LỆNH tả TÓC/PHỤ KIỆN/BỐI CẢNH lên TRÊN CÙNG (như path Model mẫu) — user
        # báo: đính ảnh + tả bối cảnh phòng ngủ nhưng output vẫn ra nền của ảnh ref (image-conditioning đè mô tả).
        prompt = (
            _ref_override_rules(prompt)
            + "PRIMARY INSTRUCTION — generate a completely NEW photograph from the user's description below. This is "
            "NOT an image-editing task: never reproduce a reference image. The attached reference images are "
            "identity references only — keep the same face and the same body shape and proportions, and take "
            "NOTHING else from them: not the clothing or costume, not the headwear or hair accessories, not the "
            "background, not the pose, not the framing. Outfit, hairstyle, accessories, pose, scene, lighting and "
            "framing come only from the user's description below:\n" + prompt
        )
    W, H, force_size = _qwen_dims(params)
    # ALD 12/06/2026 - ref user cũng ép render đúng W×H yêu cầu (latent rỗng) như model-standard — trước đây
    # latent lấy từ ảnh ref → output copy luôn khung/tỉ lệ ảnh ref (một phần của bệnh "clone ref").
    if img_mode == "edit" and locals_:
        force_size = False   # edit: giữ bố cục/tỉ lệ ảnh gốc (latent từ ảnh ref), KHÔNG ép W×H
    elif using_model_standard or locals_:
        force_size = True
    ref_note = f"{len(locals_)} ảnh ref" + (" · EDIT mode" if img_mode == "edit" and locals_ else "")
    if len(locals_) > QWEN_EDIT_MAX_REFS:
        ref_note += f" (dùng tối đa {QWEN_EDIT_MAX_REFS}/lần)"
    if using_model_standard:
        ref_note = f"model-standard pool {len(ms_pool)} mẫu" if ms_pool else "model-standard disk fallback"
    api_log(job_id, f"create-image {W}×{H}{' (forced)' if force_size else ''} · {ref_note} · {output_count} output · provider {provider or 'qwen'}", "info")
    if _architecture_mode:
        api_log(job_id, "create-image: chế độ kiến trúc — tắt toàn bộ human/model realism", "info")
    try:
        _create_denoise = max(0.05, min(1.0, float(params.get("denoise") or 1.0)))
    except (TypeError, ValueError):
        _create_denoise = 1.0
    try:
        base_seed = int(params.get("seed") or abs(hash(job_id))) % (2 ** 31)
    except Exception:
        base_seed = abs(hash(job_id)) % (2 ** 31)
    # #region ALD 11/06/2026 - Provider 'huggingface': chạy model trên HF Inference Providers (fal-ai) thay vì GPU
    # local. 0 ref → text-to-image (Qwen/Qwen-Image); ≥1 ref (kể cả model-standard bốc mặt random) → image-to-image
    # (Qwen-Image-Edit-2511, multi image_urls). Negative gửi HF = user_neg THÔI — PHOTO_REALISM_* tune cho workflow
    # Qwen local, model HF xịn hơn không cần. params.hfModel override model (trống = mặc định registry).
    if provider == "huggingface":
        hf_key = _hf_key(params)
        for idx in range(output_count):
            if using_model_standard:
                face = _model_face_local(ms_pool, ms_disk, ms_cache, tmp)
                refs_local = [face] if face else []
            else:
                refs_local = _qwen_ref_window(locals_, idx)
            task = "image-to-image" if refs_local else "text-to-image"
            hf_id, fal_id, q = _hf_resolve(params.get("hfModel"), task)
            step_label = f"HF {hf_id}{_vcount(idx, output_count)}"
            api_progress(job_id, 0.3 + (0.6 * idx / output_count), step_label)
            payload = {"prompt": prompt, "seed": base_seed + idx * 9973}
            if user_neg:
                payload["negative_prompt"] = user_neg
            if refs_local:
                uris = [_hf_data_uri(p) for p in refs_local]
                payload["image_url"] = uris[0]
                payload["image_urls"] = uris
                # ALD 11/06/2026 - LUÔN chốt image_size (fal tự chọn hay ra vuông → méo người): force > canvas
                # model-standard (ref là mặt crop, không lấy tỉ lệ từ nó) > tỉ lệ ảnh ref đầu.
                isz = ({"width": W, "height": H} if (force_size or using_model_standard)
                       else _hf_image_size(_img_size(refs_local[0])))
                if isz:
                    payload["image_size"] = isz
            else:
                payload["image_size"] = {"width": W, "height": H}
            res = _hf_call(fal_id, payload, hf_key, q, job_id, 600,
                           0.3 + (0.6 * idx / output_count), 0.3 + (0.6 * (idx + 1) / output_count), step_label)
            out = os.path.join(tmp, f"hf_{idx + 1}.png")
            _hf_fetch(_hf_first_image(res), out)
            api_upload_output(
                job_id, out, content_type="image/png",
                variant=(idx + 1 if output_count > 1 else None),
                label=(f"Ảnh {idx + 1}" if output_count > 1 else None),
                final=(idx == output_count - 1),
            )
        return
    # #endregion
    # #region ALD 13/06/2026 - Provider 'gemini' → Gemini image API (Nano Banana / gemini-*-image). Trước đây
    # create-image THIẾU nhánh này (chỉ run_tryon có) → chọn Gemini vẫn rớt xuống Qwen local (bug: user trả tiền
    # Gemini mà chạy Qwen + lỗi tay). Chất lượng/tay/chi tiết tốt hơn Qwen, gọi API (không cần GPU). Key chỉ từ
    # node API Key / field (env đã bỏ). 0 ref = text-to-image; ≥1 ref = image-edit (ghép). aspect ép giữ khung.
    if provider == "gemini":
        gem_key = _gemini_key(params)
        if not gem_key:
            raise RuntimeError("create-image provider Gemini cần API key — nối node API Key (Type: Gemini) vào cổng "
                               "API key của node, hoặc đổi Provider = Qwen (self-host, không cần key).")
        if not _valid_gemini_key(gem_key):
            raise RuntimeError("Key Gemini không đúng định dạng (phải dạng 'AIza…', ~39 ký tự, KHÔNG khoảng trắng). "
                               "Lấy key ở https://aistudio.google.com/apikey, hoặc đổi Provider = Qwen.")
        # ALD 13/06/2026 - Dropdown model Gemini: Nano Banana (2.5 Flash Image, nhanh/rẻ) vs Nano Banana Pro
        # (3 Pro Image, đẹp nhất). FE gửi geminiModel; mặc định Pro (= GEMINI_IMAGE_MODEL).
        gem_model = {"nano-banana": "gemini-2.5-flash-image",
                     "nano-banana-pro": "gemini-3-pro-image-preview"}.get(
            str(params.get("geminiModel") or params.get("gemini_model") or "").lower().strip(), GEMINI_IMAGE_MODEL)
        for idx in range(output_count):
            refs_local = _qwen_ref_window(locals_, idx) if locals_ else []
            step_label = f"Gemini {gem_model}{_vcount(idx, output_count)}"
            api_progress(job_id, 0.3 + (0.6 * idx / output_count), step_label)
            parts = []
            for p in refs_local:
                with open(p, "rb") as f:
                    parts.append((f.read(), _mime(p)))
            gem_aspect = _gemini_aspect((W, H)) if (force_size or not refs_local) else _gemini_aspect(_img_size(refs_local[0]))
            out = os.path.join(tmp, f"gemini_{idx + 1}.png")
            # ALD 13/06/2026 - Gemini API KHÔNG có field negative riêng → nhét negative (JSON/field) vào prompt dạng
            # "AVOID" để model né (user báo "negative không vào model" khi chạy Gemini). Bỏ PHOTO_REALISM_NEGATIVE
            # (tuned cho Qwen) — chỉ dùng negative người dùng nhập + từ JSON cho gọn, tự nhiên với Gemini.
            gem_prompt = prompt
            if user_neg:
                gem_prompt = prompt.rstrip() + "\n\nIMPORTANT — strictly AVOID and do NOT render any of these: " + user_neg
            _gemini_edit(parts, gem_prompt, gem_key, out, aspect_ratio=gem_aspect, model=gem_model)
            api_upload_output(
                job_id, out, content_type="image/png",
                variant=(idx + 1 if output_count > 1 else None),
                label=(f"Ảnh {idx + 1}" if output_count > 1 else None),
                final=(idx == output_count - 1),
            )
        return
    # #endregion
    # #region ALD 13/06/2026 - Model self-host Flux.1/SD3.5 (FP8) — CHỈ text→image, dùng khi node chọn "Model
    # (self-host)" ≠ qwen-edit VÀ KHÔNG có ảnh ref/edit/model-standard (Flux/SD3.5 không sửa/ghép ảnh được). Có
    # ref/model-standard → báo lỗi rõ ràng, không fallback âm thầm sang Qwen.
    # Mirror nguyên vòng lặp Qwen: comfy_submit/poll/fetch + api_upload_output, nhãn _vcount, tiến độ 0.3→0.9.
    _selfhost = model in SELFHOST_T2I_MODELS
    # Chỉ khi KHÔNG ref + KHÔNG Model mẫu mới chạy Flux thuần text→image. Trước đây Model mẫu còn rớt xuống Qwen bên
    # dưới, gây hiểu nhầm "chọn Flux.Dev mà vẫn load QwenImage".
    if _selfhost and (locals_ or using_model_standard):
        reason = "ảnh tham chiếu" if locals_ else "Model mẫu/Tiêu chuẩn model mặc định"
        raise RuntimeError(
            f"Model '{model}' chỉ hỗ trợ text→image thuần, nhưng node đang bật {reason}. "
            "Hãy đặt Số cổng ảnh tham chiếu = 0 và Tiêu chuẩn model mặc định = Off, "
            "hoặc đổi model về Qwen-Edit nếu cần ảnh tham chiếu/sửa ảnh."
        )
    elif _selfhost and not using_model_standard:
        api_log(job_id, f"create-image dùng model self-host '{model}' (text→image, không ref).", "info")
        for idx in range(output_count):
            step_label = f"{model}{_vcount(idx, output_count)}"
            api_progress(job_id, 0.3 + (0.6 * idx / output_count), step_label)
            prefix = f"img-{job_id[:8]}-{idx + 1}"
            lo = 0.3 + (0.6 * idx / output_count); hi = 0.3 + (0.6 * (idx + 1) / output_count)
            wf = build_selfhost_create_workflow(model, prompt, prefix, W, H, seed=base_seed + idx * 9973, negative_prompt=neg_merged)
            if wf is None:  # model lạ (không nên xảy ra vì đã lọc qua SELFHOST_T2I_MODELS) → an toàn fallback Qwen
                api_log(job_id, f"Model '{model}' không dựng được workflow — dùng Qwen-Edit.", "warn")
                break
            pid = comfy_submit(wf)
            outputs = comfy_poll(pid, job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step=step_label)
            api_progress(job_id, 0.9 + (0.04 * idx / output_count), f"tải kết quả{_vcount(idx, output_count)}")
            out = comfy_fetch_output(outputs, exts=IMG_EXTS)
            if not out: raise RuntimeError("ComfyUI không trả ảnh")
            # Flux/SD3.5 KHÔNG chạy detailer Qwen (DWPose/TextEncodeQwen… của Qwen, model khác → vô nghĩa).
            api_progress(job_id, 0.95 + (0.04 * idx / output_count), f"upload output{_vcount(idx, output_count)}")
            api_upload_output(
                job_id, out, content_type="image/png",
                variant=(idx + 1 if output_count > 1 else None),
                label=(f"Ảnh {idx + 1}" if output_count > 1 else None),
                final=(idx == output_count - 1),
            )
        else:
            return  # for-else: vòng lặp xong KHÔNG break → đã upload đủ; KHÔNG rớt xuống Qwen
    # #endregion
    for idx in range(output_count):
        step_label = f"Qwen-Image-Edit{_vcount(idx, output_count)}"
        api_progress(job_id, 0.3 + (0.6 * idx / output_count), step_label)
        prefix = f"img-{job_id[:8]}-{idx + 1}"
        if using_model_standard:
            # ALD 14/06 - Bốc NGẪU NHIÊN 1 ảnh model NGUYÊN cho output này (mỗi ảnh một mẫu khác → đa dạng); dùng
            # nguyên ảnh (mặt + dáng) làm ref danh tính, KHÔNG crop mặt. None → 0-ref.
            model_ref = _model_face_for_output(ms_pool, ms_disk, ms_cache, tmp)
            refs_for_run = [model_ref] if model_ref else []
        else:
            refs_for_run = _qwen_ref_window(names, idx)
        lo = 0.3 + (0.6 * idx / output_count); hi = 0.3 + (0.6 * (idx + 1) / output_count)
        # ALD 14/06/2026 - 1 BƯỚC: render thẳng với ref (model-standard = NGUYÊN ảnh model giữ mặt+dáng; hoặc ref user).
        # Đã GỠ chế độ "ghép mặt 2 bước" (dựng cảnh → swap mặt) — gây lỗi mặt đôi/mặt ma do bước swap không blend.
        pid = comfy_submit(build_qwen_create_workflow(
            refs_for_run, prompt, prefix, width=W, height=H, force_size=force_size,
            seed=base_seed + idx * 9973, negative_prompt=neg_merged,
            realism=not _architecture_mode, denoise=_create_denoise,
        ))
        outputs = comfy_poll(pid, job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step=step_label)
        api_progress(job_id, 0.9 + (0.04 * idx / output_count), f"tải kết quả{_vcount(idx, output_count)}")
        out = comfy_fetch_output(outputs, exts=IMG_EXTS)
        if not out: raise RuntimeError("ComfyUI không trả ảnh")
        # ALD 14/06/2026 - MULTI-STEP REFINE kiểu ChatGPT (Ollama vision FREE, KHÔNG tốn phí): qwen-VL SOI ảnh vs prompt
        # → câu lệnh EDIT → Qwen-Edit img2img sửa, lặp refineSteps vòng (1-3). Vision lỗi/ảnh đã ĐẠT → dừng sớm, giữ
        # ảnh. refineSteps=0 → bỏ qua. Bám đúng ý đồ user, tự sửa "model bay prompt" (vd tóc hồng → đen).
        _refine_n = max(0, min(3, int(params.get("refineSteps") or params.get("refine_steps") or 0)))
        for _r in range(_refine_n):
            api_progress(job_id, 0.88, f"phân tích & tinh chỉnh {_r + 1}/{_refine_n}")
            _edit = _vision_refine_prompt(out, prompt, job_id)
            if _edit is None:
                break
            if not _edit:
                api_log(job_id, f"Tinh chỉnh vòng {_r + 1}: vision chấm ảnh đã ĐẠT → dừng.", "info")
                break
            api_log(job_id, f"Tinh chỉnh vòng {_r + 1}: {_edit[:160]}", "info")
            try:
                _rup = comfy_upload(out)
                _rpid = comfy_submit(build_qwen_create_workflow([_rup], _edit, f"{prefix}-rf{_r}", force_size=False, seed=base_seed + idx * 9973 + 101 + _r, negative_prompt=neg_merged))
                _routs = comfy_poll(_rpid, job_id, deadline_sec=600, prog_step=f"tinh chỉnh vòng {_r + 1}/{_refine_n}")
                _rloc = comfy_fetch_output(_routs, exts=IMG_EXTS)
                if _rloc:
                    out = _rloc
            except Exception as e:
                api_log(job_id, f"Tinh chỉnh vòng {_r + 1} lỗi render → giữ ảnh trước đó: {e}", "warn")
                break
        api_progress(job_id, 0.95 + (0.04 * idx / output_count), f"upload output{_vcount(idx, output_count)}")
        api_upload_output(
            job_id, out, content_type="image/png",
            variant=(idx + 1 if output_count > 1 else None),
            label=(f"Ảnh {idx + 1}" if output_count > 1 else None),
            final=(idx == output_count - 1),
        )

# #region ALD 14/07/2026 - Khoá pixel ngoài vùng thi công khi sửa ảnh kiến trúc.
def _preserve_outside_construction_roi(base_path, edited_path, params, output_path):
    enabled = str(params.get("preserveOutsideConstructionRoi") or
                  params.get("preserve_outside_construction_roi") or "").strip().lower()
    if enabled not in ("1", "true", "yes", "on"):
        return edited_path
    try:
        from PIL import Image, ImageDraw, ImageFilter
        roi = params.get("constructionRoi") or params.get("construction_roi") or {}
        if not isinstance(roi, dict):
            roi = {}

        def _ratio(key, default):
            try:
                return max(0.0, min(1.0, float(roi.get(key, default))))
            except (TypeError, ValueError):
                return default

        edited = Image.open(edited_path).convert("RGB")
        resampling = getattr(Image, "Resampling", Image)
        base = Image.open(base_path).convert("RGB").resize(edited.size, resampling.LANCZOS)
        width, height = edited.size
        top_y = _ratio("topY", 0.16)
        bottom_y = min(1.0, max(top_y + 0.05, _ratio("bottomY", 0.91)))
        points = [
            (round(width * _ratio("topLeft", 0.18)), round(height * top_y)),
            (round(width * _ratio("topRight", 0.82)), round(height * top_y)),
            (round(width * _ratio("bottomRight", 0.96)), round(height * bottom_y)),
            (round(width * _ratio("bottomLeft", 0.04)), round(height * bottom_y)),
        ]
        mask = Image.new("L", edited.size, 0)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        feather = max(2, round(min(width, height) * _ratio("feather", 0.025)))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
        Image.composite(edited, base, mask).save(output_path, format="PNG", optimize=True)
        return output_path
    except Exception as e:
        log(f"construction ROI pixel-lock lỗi (giữ ảnh edit): {e}")
        return edited_path
# #endregion

# #region ALD 01/07/2026 - Node "Sửa ảnh" (edit-image): SỬA ảnh CÓ SẴN theo mô tả, KHÔNG sinh ảnh mới.
# Khác create-image (bệnh edit-mode = regen sạch + nhét beauty-body/camera/model-standard vào prompt):
#   - Đường prompt SẠCH: chỉ instruction của user (dịch VN→EN), KHÔNG realism/beauty/camera/model-standard.
#   - GIỮ NGUYÊN pixel + tỉ lệ ảnh gốc (force_size=False → VAEEncode ảnh gốc), KHÔNG bg-remove/crop mặt.
#   - Input là LIST ảnh (image1..imageN). MỖI ảnh sinh outputCount (1-5) version.
#   - Xong version nào → api_preview (hiện NGAY trên node) + api_upload_output(final chỉ ở kết quả CUỐI) → progressive.
def run_edit_image(job):
    import re as _re
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    # Gom img_keys từ inputs: image1..imageN (theo thứ tự số) + fallback image/input/ref, khử trùng.
    num_keys = sorted([k for k in inputs if _re.match(r"^image\d+$", k)], key=lambda k: int(k[5:]))
    img_keys, _seen = [], set()
    for k in num_keys + ["image", "input", "ref"]:
        v = inputs.get(k)
        for x in (v if isinstance(v, list) else ([v] if v else [])):
            if x and x not in _seen: _seen.add(x); img_keys.append(x)
    if not img_keys:
        raise RuntimeError("edit-image: cần nối ít nhất 1 ảnh vào node")

    try:
        output_count = int(params.get("outputCount") or params.get("output_count") or 1)
    except Exception:
        output_count = 1
    output_count = max(1, min(5, output_count))

    # Chất lượng NATIVE → megapixel đích (1080 ~1.0MP · 2k ~1.75MP). KHÔNG ESRGAN.
    quality = str(params.get("quality") or "1080").lower().strip()
    target_mp = {"1080": 1.0, "2k": 1.75}.get(quality, 1.0)

    provider = str(params.get("provider") or "").lower().strip()
    try:
        edit_denoise = max(0.05, min(1.0, float(
            params.get("editDenoise") if params.get("editDenoise") is not None
            else params.get("edit_denoise", 1.0)
        )))
    except (TypeError, ValueError):
        edit_denoise = 1.0

    raw_prompt = str(params.get("prompt") or params.get("positive_prompt") or "").strip()
    if not raw_prompt:
        raise RuntimeError("edit-image: cần nhập MÔ TẢ cách sửa ảnh")
    instr = _translate_prompt_en(raw_prompt, job_id) or raw_prompt
    if instr != raw_prompt:
        api_log(job_id, f"Đã dịch mô tả sang tiếng Anh cho Qwen: {instr[:160]}", "info")
    # ALD 03/07/2026 - COMBINE: ≥2 ảnh + combine=1 → GHÉP tất cả ảnh thành 1 ảnh (1 lần Qwen multi-ref) thay vì
    # sửa từng ảnh. Dùng cho keyframe "người mẫu cầm sản phẩm" của đạo diễn AI (thay node Đặt sản phẩm đã bỏ):
    # ảnh 1 = người mẫu (giữ nhân dạng), ảnh 2+ = sản phẩm (giữ ĐÚNG bao bì/nhãn/logo).
    combine = len(img_keys) >= 2 and str(params.get("combine", "0")).strip().lower() in ("1", "true", "yes", "on")
    if combine:
        # ALD 03/07/2026 (chiều tối) - preamble cũ "Create ONE new photo" là SAI: mời Qwen DỰNG LẠI cả ảnh
        # (mẫu bị vẽ lại, đổi dáng/bối cảnh, giày to bất thường + nhân đôi — user bắt lỗi bằng ảnh thật).
        # Đổi thành lệnh EDIT đúng kiểu Qwen-Edit (như đường sửa-1-ảnh vốn chạy tốt): GIỮ NGUYÊN ảnh 1,
        # chỉ CHÈN sản phẩm 1 LẦN, đúng tỉ lệ thật so với cơ thể.
        edit_prompt = (
            "Edit image 1 following this instruction. Image 1 is the BASE photo: keep the same person "
            "(identity, face, hairstyle, outfit), the same pose, camera framing, background and lighting — "
            "do NOT redraw or restage the photo. Add the product from image 2 into image 1 exactly ONCE: "
            "same packaging, label, logo, colors and proportions, at its correct REAL-WORLD SIZE relative to "
            "the person's body (never oversized, never miniature). Blend contact, shadows and lighting "
            "realistically. Do not duplicate the product, do not invent a different product, do not add text:\n" + instr
        )
    else:
        edit_prompt = (
            "Edit the image following this instruction. Keep the same composition, framing, subject, pose, "
            "camera angle, lighting and everything else identical to the original — change ONLY what the "
            "instruction says:\n" + instr
        )
    user_neg = str(params.get("negativePrompt") or params.get("negative_prompt") or "").strip().strip(",").strip()
    if user_neg:  # Qwen cũng không hiểu negative tiếng Việt → dịch (chỉ dịch khi có dấu, fail-safe giữ nguyên).
        user_neg = _translate_prompt_en(user_neg, job_id) or user_neg
    neg_merged = user_neg or None   # edit: KHÔNG ép PHOTO_REALISM_NEGATIVE (giữ trung thực với instruction)

    gem_key = _gemini_key(params)
    gem_model = {"nano-banana": "gemini-2.5-flash-image-preview",
                 "nano-banana-pro": "gemini-3-pro-image-preview"}.get(
        str(params.get("geminiModel") or "").lower().strip(), GEMINI_IMAGE_MODEL)

    tmp = tempfile.mkdtemp(prefix=f"edit-{job_id[:8]}-")

    if combine:
        # GHÉP: 1 lần Qwen/Gemini với TẤT CẢ ảnh ref → output_count version của 1 ảnh ghép.
        total = output_count; done = 0
        api_log(job_id, f"edit-image: GHÉP {len(img_keys)} ảnh → 1 ảnh × {output_count} version · {quality} · provider {provider or 'qwen'}", "info")
        locs = [api_download(k, os.path.join(tmp, f"img{i}" + (os.path.splitext(k)[1] or ".png"))) for i, k in enumerate(img_keys)]
        cnames = None if provider == "gemini" else [comfy_upload(l) for l in locs]
        for v in range(output_count):
            step = f"Ghép ảnh{_vcount(v, output_count)}"
            lo = 0.05 + 0.9 * (v / total); hi = 0.05 + 0.9 * ((v + 1) / total)
            api_progress(job_id, lo, step)
            if provider == "gemini":
                if not gem_key:
                    raise RuntimeError("edit-image provider=gemini nhưng thiếu API key")
                parts = []
                for l in locs:
                    with open(l, "rb") as f:
                        parts.append((f.read(), _mime(l)))
                out = os.path.join(tmp, f"g-c-{v + 1}.png")
                _sz = _img_size(locs[0])
                _gemini_edit(parts, edit_prompt, gem_key, out,
                             aspect_ratio=(_gemini_aspect(_sz) if _sz else None), model=gem_model)
            else:
                prefix = f"edit-{job_id[:8]}-c-{v + 1}"
                seed = (abs(hash(img_keys[0])) & 0xffffff) + v * 9973
                try:
                    _dn_raw = params.get("combineDenoise")
                    if _dn_raw is None:
                        _dn_raw = (params.get("editDenoise") if params.get("editDenoise") is not None
                                   else os.environ.get("QWEN_COMBINE_DENOISE", "1.0"))
                    _dn = float(_dn_raw)
                except (TypeError, ValueError):
                    _dn = 1.0
                pid = comfy_submit(build_qwen_create_workflow(
                    cnames, edit_prompt, prefix, force_size=False, seed=seed,
                    negative_prompt=neg_merged, realism=False, target_mp=target_mp, denoise=_dn))
                outs = comfy_poll(pid, job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step=step)
                out = comfy_fetch_output(outs, exts=IMG_EXTS)
                if not out: raise RuntimeError("ComfyUI không trả ảnh")
            out = _preserve_outside_construction_roi(
                locs[0], out, params, os.path.join(tmp, f"locked-c-{v + 1}.png")
            )
            done += 1
            label = "Ảnh ghép" + (f" · v{v + 1}" if output_count > 1 else "")
            try: api_preview(job_id, out, label)
            except Exception as e: log(f"edit preview fail: {e}")
            api_upload_output(job_id, out, content_type="image/png",
                              variant=done, label=label, final=(done == total))
        return

    total = len(img_keys) * output_count
    done = 0
    api_log(job_id, f"edit-image: {len(img_keys)} ảnh × {output_count} version = {total} kết quả · {quality} · provider {provider or 'qwen'}", "info")

    for i, k in enumerate(img_keys):
        loc = api_download(k, os.path.join(tmp, f"img{i}" + (os.path.splitext(k)[1] or ".png")))
        cname = None if provider == "gemini" else comfy_upload(loc)
        for v in range(output_count):
            step = f"Sửa ảnh {i + 1}/{len(img_keys)}{_vcount(v, output_count)}"
            lo = 0.05 + 0.9 * (done / total); hi = 0.05 + 0.9 * ((done + 1) / total)
            api_progress(job_id, lo, step)
            if provider == "gemini":
                if not gem_key:
                    raise RuntimeError("edit-image provider=gemini nhưng thiếu API key")
                with open(loc, "rb") as f:
                    parts = [(f.read(), _mime(loc))]
                out = os.path.join(tmp, f"g-{i + 1}-{v + 1}.png")
                _sz = _img_size(loc)
                _gemini_edit(parts, edit_prompt, gem_key, out,
                             aspect_ratio=(_gemini_aspect(_sz) if _sz else None), model=gem_model)
            else:
                prefix = f"edit-{job_id[:8]}-{i + 1}-{v + 1}"
                seed = (abs(hash(k)) & 0xffffff) + v * 9973 + i * 101
                pid = comfy_submit(build_qwen_create_workflow(
                    [cname], edit_prompt, prefix, force_size=False, seed=seed,
                    negative_prompt=neg_merged, realism=False, target_mp=target_mp,
                    denoise=edit_denoise))
                outs = comfy_poll(pid, job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step=step)
                out = comfy_fetch_output(outs, exts=IMG_EXTS)
                if not out: raise RuntimeError("ComfyUI không trả ảnh")
            out = _preserve_outside_construction_roi(
                loc, out, params, os.path.join(tmp, f"locked-{i + 1}-{v + 1}.png")
            )
            done += 1
            label = f"Ảnh {i + 1}" + (f" · v{v + 1}" if output_count > 1 else "")
            # Progressive: preview hiện ngay trên node; output tích luỹ, final=True ở kết quả CUỐI → set done.
            try: api_preview(job_id, out, label)
            except Exception as e: log(f"edit preview fail: {e}")
            api_upload_output(job_id, out, content_type="image/png",
                              variant=done, label=label, final=(done == total))
# #endregion

def _open_rgba(path):
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError(f"Pillow chưa sẵn sàng cho product-overlay: {e}")
    return Image.open(path).convert("RGBA")

def _crop_near_white_alpha(img, threshold=246, pad=10):
    """Crop whitespace around packshot while preserving the original product pixels."""
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return img
    try:
        px = img.convert("RGB")
        w, h = px.size
        data = px.load()
        xs, ys = [], []
        step = 1 if max(w, h) < 1400 else 2
        for y in range(0, h, step):
            for x in range(0, w, step):
                r, g, b = data[x, y]
                if min(r, g, b) < threshold or (max(r, g, b) - min(r, g, b)) > 18:
                    xs.append(x); ys.append(y)
        if xs and ys:
            bbox = (
                max(0, min(xs) - pad),
                max(0, min(ys) - pad),
                min(w, max(xs) + pad),
                min(h, max(ys) + pad),
            )
    except Exception:
        pass
    return img.crop(bbox)

def _rounded_rect(size, radius, fill):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill)
    return im

def _product_hold_prompt(params):
    user_prompt = str(params.get("prompt") or "").strip()
    if user_prompt:
        return user_prompt
    placement = str(params.get("productPlacement") or params.get("placement") or "handheld").lower().strip()
    common = (
        "Edit image 1 only as the base photo. Keep the exact same person identity, face, hairstyle, outfit, "
        "body proportions, camera angle, background, lighting style and overall composition from image 1. "
        "Use image 2 as the only approved product reference. Preserve the product shape, label layout, logo, cap, "
        "colors, typography and proportions as much as possible. Add realistic contact, scale, shadows and reflections. "
        "The result must look like the product was physically present in the photo, not a sticker, not a floating overlay, "
        "not a separate card. "
    )
    if placement in ("auto", "", "natural"):
        return (
            common +
            "Decide the placement from image 2 product size and category. If it is a small hand-held product such as serum, "
            "cosmetic bottle, phone, perfume, book or small box, use a simple presentation grip near chest or face. If it is "
            "a tabletop product such as laptop, bag, shoes, speaker or small appliance, place it on a table/counter/shelf near "
            "the model. If it is a large appliance/furniture/TV/fridge/sofa/bed, place it at real-world scale beside or behind "
            "the model. If it is a vehicle, place it beside the model at real-world scale. Never force large products or vehicles "
            "into the model's hand."
        )
    if placement in ("vehicle", "car", "motorbike", "scooter"):
        return (
            common +
            "This product is a vehicle. Place it at real-world scale beside the model in a showroom or lifestyle ad setting. "
            "The model may stand beside it or naturally rest one simple hand on the handlebar, seat or door if appropriate. "
            "Never put the vehicle in the model's hand. Keep hands simple and anatomically normal."
        )
    if placement in ("large-display", "large", "floor-standing", "appliance", "furniture"):
        return (
            common +
            "This is a large product. Place it at real-world scale beside or behind the model in a showroom, living-room, "
            "kitchen, bedroom or appliance-store context that matches the product. The model presents it with an open palm "
            "or points from a short distance. Never put this large product in the model's hand."
        )
    if placement in ("tabletop", "counter", "shelf", "display"):
        return (
            common +
            "Place the product naturally on a table, counter, shelf, stand or display surface near the model. The model "
            "gestures toward it with an open palm or lightly touches the table. Do not force the model to hold the product. "
            "Keep hands simple and away from product edges."
        )
    return (
        common +
        "The product is small enough to be hand-held. Put the real product in a simple presentation grip "
        "near the model's chest like a premium skincare advertisement. The product should mostly cover the hand; "
        "show at most one thumb and a few partial fingertips. Do not create complex visible fingers, do not wrap "
        "fingers around the bottle/box."
    )

def _product_hold_negative(params):
    placement = str(params.get("productPlacement") or params.get("placement") or "handheld").lower().strip()
    base = (
        "different person, changed face, changed hairstyle, changed outfit, changed body, wrong product, "
        "changed product label, fake product packaging, different logo, distorted logo, unreadable label, "
        "floating sticker, pasted card, product poster, wrong scale, miniature product, toy-sized product, "
        "extra fingers, missing fingers, fused fingers, mutated fingers, "
        "deformed fingers, deformed hand, hand wrapped around product, bad anatomy, blurry, low quality, text artifacts"
    )
    if placement in ("auto", "", "natural"):
        base += ", holding large product, vehicle in hand, tiny vehicle, tiny appliance, wrong product placement"
    elif placement in ("vehicle", "car", "motorbike", "scooter"):
        base += ", vehicle in hand, holding vehicle, tiny vehicle, model carrying vehicle"
    elif placement in ("large-display", "large", "floor-standing", "appliance", "furniture"):
        base += ", product in hand, holding large product, tiny appliance, tiny furniture, toy-sized appliance"
    elif placement in ("tabletop", "counter", "shelf", "display"):
        base += ", awkward hand grip, hand holding product, product floating above table"
    extra = str(params.get("negativePrompt") or params.get("negative_prompt") or "").strip()
    return f"{base}, {extra}" if extra else base

def _run_product_natural_hold(job, base_path, product_path, tmp):
    """Qwen-Edit: image1=model/base, image2=real product. More natural than pixel overlay, but less exact."""
    job_id = job["id"]; params = job.get("params", {})
    api_progress(job_id, 0.16, "upload ảnh mẫu + sản phẩm vào Qwen-Edit")
    base_name = comfy_upload(base_path)
    product_name = comfy_upload(product_path)
    prompt = _product_hold_prompt(params)
    negative = _product_hold_negative(params)
    try:
        seed = int(params.get("seed") or abs(hash(f"{job_id}:product-hold"))) % (2 ** 31)
    except Exception:
        seed = abs(hash(f"{job_id}:product-hold")) % (2 ** 31)
    placement = str(params.get("productPlacement") or params.get("placement") or "handheld").lower().strip()
    api_log(job_id, f"product-overlay mode=natural-hold · Qwen-Edit đặt sản phẩm ({placement})", "info")
    api_progress(job_id, 0.26, "Qwen-Edit đặt sản phẩm vào cảnh")
    pid = comfy_submit(build_qwen_create_workflow(
        [base_name, product_name],
        prompt,
        f"product-hold-{job_id[:8]}",
        force_size=False,
        seed=seed,
        negative_prompt=negative,
    ))
    outputs = comfy_poll(pid, job_id, deadline_sec=900, prog_lo=0.28, prog_hi=0.9, prog_step="Qwen-Edit đặt sản phẩm")
    out = comfy_fetch_output(outputs, exts=IMG_EXTS)
    if not out:
        raise RuntimeError("Qwen-Edit không trả ảnh đặt sản phẩm")
    final_path = os.path.join(tmp, "product-hold.png")
    shutil.copyfile(out, final_path)
    return final_path

def run_product_overlay(job):
    """image1=scene/model frame, image2=real product packshot.

    mode=natural-hold uses Qwen-Edit so the model presents/holds the product.
    mode=safe-packshot uses deterministic pixel overlay for exact package fidelity.
    """
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    base_key = inputs.get("image1") or inputs.get("image") or inputs.get("start") or inputs.get("model")
    product_key = inputs.get("image2") or inputs.get("product") or inputs.get("sp")
    if not base_key or not product_key:
        raise RuntimeError("product-overlay cần image1=Ảnh mẫu và image2=Ảnh sản phẩm")
    api_progress(job_id, 0.08, "tải ảnh mẫu + sản phẩm")
    with tempfile.TemporaryDirectory(prefix=f"overlay-{job_id[:8]}-") as tmp:
        base_path = os.path.join(tmp, "base.png")
        product_path = os.path.join(tmp, "product.png")
        api_download(base_key, base_path)
        api_download(product_key, product_path)
        mode = str(params.get("mode") or "natural-hold").lower().strip()
        if mode not in ("safe-packshot", "packshot", "overlay", "safe"):
            try:
                out_path = _run_product_natural_hold(job, base_path, product_path, tmp)
                api_preview(job_id, out_path, params.get("label") or "Mẫu cầm sản phẩm")
                api_progress(job_id, 0.95, "upload output")
                api_upload_output(job_id, out_path, content_type="image/png")
                return out_path
            except Exception as e:
                if str(params.get("fallbackSafe") or "1").lower() in ("0", "false", "no", "off"):
                    raise
                api_log(job_id, f"natural-hold lỗi → fallback safe-packshot: {e}", "warn")
        base = _open_rgba(base_path)
        product = _crop_near_white_alpha(_open_rgba(product_path))
        W, H = base.size
        scale = max(0.12, min(0.55, float(params.get("scale") or 0.34)))
        pad = max(8, int(min(W, H) * max(0.015, min(0.08, float(params.get("padding") or 0.035)))))
        target_w = max(64, int(W * scale))
        target_h = int(product.height * (target_w / max(1, product.width)))
        max_h = int(H * 0.45)
        if target_h > max_h:
            target_h = max(64, max_h)
            target_w = int(product.width * (target_h / max(1, product.height)))
        product = product.resize((target_w, target_h), resample=1)
        position = str(params.get("position") or "bottom-right").lower()
        card_on = str(params.get("card", True)).lower() not in ("0", "false", "no", "off")
        card_pad = max(8, int(target_w * 0.08))
        card_w, card_h = target_w + card_pad * 2, target_h + card_pad * 2
        if "left" in position:
            x = pad
        elif "center" in position:
            x = max(pad, (W - card_w) // 2)
        else:
            x = max(pad, W - card_w - pad)
        if "top" in position:
            y = pad
        elif "middle" in position or "center" in position:
            y = max(pad, (H - card_h) // 2)
        else:
            y = max(pad, H - card_h - pad)
        api_progress(job_id, 0.45, "overlay packshot thật")
        out = base.copy()
        if card_on:
            from PIL import ImageFilter
            shadow = _rounded_rect((card_w, card_h), max(10, card_pad), (0, 0, 0, 62)).filter(ImageFilter.GaussianBlur(max(4, card_pad // 3)))
            out.alpha_composite(shadow, (min(W - card_w, x + max(2, card_pad // 4)), min(H - card_h, y + max(2, card_pad // 4))))
            card = _rounded_rect((card_w, card_h), max(10, card_pad), (255, 255, 255, 238))
            out.alpha_composite(card, (x, y))
            out.alpha_composite(product, (x + card_pad, y + card_pad))
        else:
            out.alpha_composite(product, (x, y))
        out_path = os.path.join(tmp, "product-overlay.png")
        out.convert("RGB").save(out_path, "PNG", optimize=True)
        api_preview(job_id, out_path, params.get("label") or "Product overlay")
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out_path, content_type="image/png")
        return out_path

def run_video(job):
    """LTX-2.3 (Lightricks) ảnh + prompt → video + audio. Engine GENERATIVE (KHÁC Wan Animate pose-driven).
    Cần inputs.image (+ params.prompt). Workflow từ params.ltxWorkflow / env LTX_WORKFLOW_JSON — xem
    build_ltx_i2v_workflow. Teaser gọi LTX qua motionMode='ltx'; type 'video' này để dùng/lests test LTX lẻ."""
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    img_key = inputs.get("image") or inputs.get("ref") or inputs.get("product") or inputs.get("model")
    if not img_key:
        raise RuntimeError("video (LTX-2.3) cần inputs.image (ảnh khởi đầu) + params.prompt")
    tmp = tempfile.mkdtemp(prefix=f"video-{job_id[:8]}-")
    api_progress(job_id, 0.1, "tải input")
    loc = api_download(img_key, os.path.join(tmp, "img" + os.path.splitext(img_key)[1]))
    api_progress(job_id, 0.2, "upload vào ComfyUI")
    name = comfy_upload(loc)
    dur = float(params.get("durationSec") or params.get("duration") or 5)
    F = ltx_frames(dur)
    prompt = (params.get("prompt") or params.get("ltxPrompt") or params.get("positive_prompt") or "").strip()
    api_progress(job_id, 0.3, f"LTX-2.3 ({F}f ~{F/LTX_FPS:.1f}s)")
    pid = comfy_submit(build_ltx_i2v_workflow(name, params, F, prompt=prompt, prefix=f"ltx-{job_id[:8]}"))
    outputs = comfy_poll(pid, job_id, deadline_sec=int(params.get("ltx_timeout", LTX_TIMEOUT)))
    api_progress(job_id, 0.9, "tải kết quả")
    out = comfy_fetch_output(outputs)
    if not out: raise RuntimeError("LTX-2.3 không trả MP4")
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out)

# #region ALD 14/06/2026 - text-to-video: CHỈ prompt (KHÔNG ảnh) → video ngắn. Dropdown chọn model (Wan2.x / LTX).
def run_text2video(job):
    """Text → Video (general-purpose, content-neutral): params.prompt + model (dropdown) → clip ngắn.
    KHÔNG nhận ảnh đầu vào. model ∈ T2V_MODELS ('wan2.1' default | 'wan2.2') hoặc 'ltx' (chỉ chạy nếu có
    template LTX I2V — LTX dùng cho I2V; T2V thuần thì ưu tiên Wan). Mirror run_video: submit/poll/fetch/upload.
    KHÔNG throw uncaught — lỗi → api_log + RuntimeError thông điệp tiếng Việt rõ ràng."""
    job_id = job["id"]; params = job.get("params", {}) or {}
    prompt = (params.get("prompt") or params.get("positive_prompt") or "").strip()
    if not prompt:
        api_log(job_id, "text-to-video: thiếu prompt", "error")
        raise RuntimeError("text-to-video cần params.prompt (mô tả cảnh quay) — không có ảnh đầu vào")
    neg = (params.get("negativePrompt") or params.get("negative_prompt") or "").strip() or None
    # ALD 03/07/2026 - MẶC ĐỊNH MỚI = wan2.2 (dual-model + LoRA distill 4-step lightx2v). Node cũ đã lưu
    # config vẫn gửi model tường minh nên không đổi hành vi workflow cũ.
    model = str(params.get("model") or "wan2.2").lower().strip()
    dur = float(params.get("duration") or params.get("durationSec") or 5)
    prefix = f"t2v-{job_id[:8]}"


    # LTX text→video: build_ltx_i2v_workflow CẦN ảnh (image_name) + template I2V → KHÔNG phải T2V thuần.
    # Box prod chỉ có template/model LTX I2V (LTX_MODEL=…distilled GGUF, graph theo template I2V). Vì vậy với
    # node text-to-video THUẦN (không ảnh) ta KHÔNG chạy LTX — nếu user chọn 'ltx' thì hạ về Wan + cảnh báo.
    if model == "ltx":
        api_log(job_id, "LTX là engine ảnh→video (cần ảnh đầu vào); node Text→Video không có ảnh → dùng Wan2.2 T2V distill", "warn")
        model = "wan2.2"

    if model not in T2V_MODELS:
        api_log(job_id, f"text-to-video: model '{model}' không hỗ trợ → dùng wan2.2", "warn")
        model = "wan2.2"

    W, H = _wan_t2v_dims(params)
    F = wan_t2v_frames(dur)
    is22 = (model == "wan2.2")
    steps = int(params.get("steps") or (WAN22_T2V_STEPS if is22 else WAN_T2V_STEPS))
    try:
        cfg = float(params.get("cfg")) if params.get("cfg") not in (None, "") else (WAN22_T2V_CFG if is22 else WAN_T2V_CFG)
    except (TypeError, ValueError):
        cfg = WAN22_T2V_CFG if is22 else WAN_T2V_CFG
    api_progress(job_id, 0.15, f"Wan T2V {model}{' distill' if is22 else ''} ({F}f ~{F/WAN_T2V_FPS:.1f}s · {W}x{H})")
    api_log(job_id, f"text-to-video: model={model} {W}x{H} {F}f @ {WAN_T2V_FPS}fps · {steps} bước · cfg {cfg}"
                    f"{' · RIFLEx idx ' + str(_riflex_index(F)) if F > 81 else ''}", "info")
    try:
        wf = build_wan_t2v_workflow(prompt, prefix, W, H, F, negative_prompt=neg, steps=steps, cfg=cfg,
                                    wan_ver=model, params=params)
        pid = comfy_submit(wf)
        outputs = comfy_poll(pid, job_id, deadline_sec=int(params.get("t2v_timeout", WAN_T2V_TIMEOUT)))
    except Exception as e:
        # ComfyUI 400 "value ... not in list" cho model/lora_0 = file CHƯA ĐƯỢC TẢI (bài học wav2vec2 18/06).
        _hint = " — Model/LoRA Wan2.2 chưa được tải: vào Settings → Models AI, cài nhóm 'Wan T2V' + 'Wan 2.2 · Distill LoRA 4 bước (lightx2v)'" \
            if ("not in" in str(e) and is22) else ""
        api_log(job_id, f"text-to-video lỗi khi render: {e}{_hint}", "error")
        raise RuntimeError(f"Text→Video (Wan {model}) lỗi: {e}{_hint}")
    api_progress(job_id, 0.9, "tải kết quả")
    out = comfy_fetch_output(outputs)
    if not out:
        api_log(job_id, "text-to-video: ComfyUI không trả MP4", "error")
        raise RuntimeError("Text→Video không trả video (ComfyUI timeout hoặc lỗi graph)")
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out)
# #endregion

def run_wan_i2v(job):
    """Node wan-i2v: ảnh đầu (+ ảnh cuối optional) + prompt → Wan I2V/FLF. Đọc đúng params.wanModel (wan2.1/wan2.2).
    ALD 03/07/2026 - MẶC ĐỊNH MỚI wan2.2 + LoRA distill 4-step (lightx2v/Wan2.2-Distill-Loras): 4 bước cfg 1.0
    (nhanh + đỡ cháy sáng), thời lượng 2–10s (>5s tự bật RIFLEx), và matchRef mặc định BẬT — ColorMatch mkl kéo
    màu/sáng output về ẢNH GỐC để trị model bị CHÁY SÁNG (học từ Motion node). Tắt: matchRef=0 / rawColor=1."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    start_key = (inputs.get("start") or inputs.get("input") or inputs.get("image") or inputs.get("product") or inputs.get("model"))
    end_key = inputs.get("end")
    # ALD 03/07/2026 - toggle "Ảnh cuối" trên FE (endEnabled): tắt → bỏ qua input end kể cả khi edge cũ còn
    # sót trong graph đã lưu. Thiếu key (node cũ) = bật như trước.
    if end_key and str(params.get("endEnabled", "1")).strip().lower() in ("0", "false", "no", "off"):
        api_log(job_id, "wan-i2v: toggle Ảnh cuối đang TẮT → bỏ qua ảnh cuối (chạy i2v 1 ảnh)", "info")
        end_key = None
    if not start_key:
        raise RuntimeError("wan-i2v cần 1 ảnh đầu vào (cổng Ảnh đầu)")
    prompt = (params.get("prompt") or params.get("positive_prompt") or "").strip()
    if not prompt:
        raise RuntimeError("wan-i2v cần prompt chuyển động (tiếng Anh)")
    neg = (params.get("negativePrompt") or params.get("negative_prompt") or "").strip() or None
    wan_ver = str(params.get("wanModel") or params.get("wan_model") or "wan2.2").lower().strip()
    if wan_ver not in {"wan2.1", "wan2.2"}:
        api_log(job_id, f"wan-i2v: wanModel={wan_ver!r} không hỗ trợ → dùng wan2.2", "warn")
        wan_ver = "wan2.2"
    dur = max(2.0, min(float(os.environ.get("WAN_I2V_MAX_SEC", "10")), float(params.get("duration") or params.get("durationSec") or 5)))
    F = wan_t2v_frames(dur)
    S = int(params.get("steps") or (4 if wan_ver == "wan2.2" else 6))
    # Giữ màu ảnh gốc (chống cháy sáng): mặc định BẬT; tắt bằng matchRef=0/false hoặc rawColor=1.
    _raw = str(params.get("rawColor", params.get("raw_color", "0"))).strip().lower() in ("1", "true", "yes", "on")
    match_ref = (not _raw) and str(params.get("matchRef", params.get("match_ref", "1"))).strip().lower() not in ("0", "false", "no", "off")
    mr_method = str(params.get("matchRefMethod") or params.get("match_ref_method") or "mkl")
    try:
        mr_strength = float(params.get("matchRefStrength", params.get("match_ref_strength", 0.85)))
    except (TypeError, ValueError):
        mr_strength = 0.85
    tmp = tempfile.mkdtemp(prefix=f"wan-i2v-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải ảnh đầu")
    start_local = api_download(start_key, os.path.join(tmp, "start" + (os.path.splitext(start_key)[1] or ".png")))
    # ALD 10/07/2026 - PROVIDER DASHSCOPE (Alibaba happyhorse/wan2.x i2v, cloud): render qua API thay vì ComfyUI.
    # wan2.x nhận thêm ảnh cuối (last_frame) + audio (driving_audio, cổng 'audio'); matchRef/faceLock không áp dụng
    # cho cloud — helper tự log warn. Xong tải video_url về, upload.
    if str(params.get("provider") or "self-host").lower().strip() == "dashscope":
        out = _dashscope_i2v(job_id, params, prompt, start_local, tmp,
                             end_key=end_key, audio_key=(inputs.get("audio") or None))
        out = _finalize_mp4(out)
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out)
        return
    start_name = comfy_upload(start_local)
    end_name = None
    if end_key:
        api_progress(job_id, 0.08, "tải ảnh cuối")
        end_local = api_download(end_key, os.path.join(tmp, "end" + (os.path.splitext(end_key)[1] or ".png")))
        end_name = comfy_upload(end_local)
        # ALD 03/07/2026 - FLF (có ảnh CUỐI): tắt matchRef — ColorMatch kéo mọi frame về màu ảnh ĐẦU sẽ phá
        # transition sang ảnh cuối (vd ngày→đêm bị "kéo sáng" lại). Chỉ match khi i2v 1 ảnh.
        if match_ref:
            api_log(job_id, "wan-i2v: có ảnh cuối (FLF) → tắt 'giữ màu ảnh gốc' để không phá transition", "info")
            match_ref = False
    W, H = _bds_resolve_wh(params, start_local, short=int(params.get("shortSide") or 480), cap_long=int(params.get("capLong") or 832))
    prefix = f"wan-i2v-{job_id[:8]}"
    api_progress(job_id, 0.15, f"Wan I2V {wan_ver}{' distill' if wan_ver == 'wan2.2' else ''} ({F}f ~{F/16:.1f}s · {W}x{H})")
    api_log(job_id, f"wan-i2v: model={wan_ver} end={'yes' if end_name else 'no'} {W}x{H} {F}f steps={S}"
                    f" · matchRef={'on (' + mr_method + ' ' + str(mr_strength) + ')' if match_ref else 'off'}"
                    f"{' · RIFLEx idx ' + str(_riflex_index(F)) if F > 81 else ''}", "info")
    try:
        wf = build_bds_segment_workflow(start_name, end_name, prompt, W, H, F, S, prefix, seed=int(params.get("seed") or 42),
                                        neg=neg, rife_mult=int(params.get("rifeMult") or 1), wan_ver=wan_ver,
                                        noise_aug=float(params.get("noiseAug") or params.get("noise_aug") or 0.025),
                                        match_ref=match_ref, match_ref_method=mr_method, match_ref_strength=mr_strength,
                                        params=params)
        out = comfy_fetch_output(comfy_poll(comfy_submit(wf), job_id, deadline_sec=int(params.get("wan_i2v_timeout", 2400))))
    except Exception as e:
        # ComfyUI 400 "value ... not in list" cho model/lora_0 = file CHƯA ĐƯỢC TẢI (bài học wav2vec2 18/06).
        _hint = " — Model/LoRA Wan2.2 chưa được tải: vào Settings → Models AI, cài nhóm 'Wan I2V' + 'Wan 2.2 · Distill LoRA 4 bước (lightx2v)'" \
            if ("not in" in str(e) and wan_ver == "wan2.2") else ""
        api_log(job_id, f"wan-i2v lỗi khi render: {e}{_hint}", "error")
        raise RuntimeError(f"Wan I2V ({wan_ver}) lỗi: {e}{_hint}")
    if not out:
        raise RuntimeError("Wan I2V không trả video")
    # ALD 07/07/2026 - faceLock cho wan-i2v: create-image/Wan làm DRIFT mặt → đắp ĐÚNG mặt người mẫu GỐC lên video
    # (inswapper, giữ biểu cảm). Ref ưu tiên: cạnh nối faceRef → config faceRefKey/faceRefUrl → ảnh đầu (fallback).
    # Self-gate theo faceLock=1 (hoặc MOTION_FACELOCK_DEFAULT). Lỗi/chưa cài = warn + giữ output gốc, không fail.
    _fl_on = str(params.get("faceLock", params.get("face_lock", os.environ.get("MOTION_FACELOCK_DEFAULT", "0")))).strip().lower() in ("1", "true", "yes", "on")
    if _fl_on:
        try:
            _fl_src = (inputs.get("faceRef") or inputs.get("face_ref")
                       or params.get("faceRefKey") or params.get("face_ref_key")
                       or params.get("faceRefUrl") or params.get("face_ref_url") or start_key)
            if str(_fl_src).startswith("http"):
                _fl_ref = os.path.join(tmp, "fl_ref.png")
                _rr = requests.get(_fl_src, timeout=40); _rr.raise_for_status()
                with open(_fl_ref, "wb") as _f:
                    _f.write(_rr.content)
            else:
                _fl_ref = api_download(_fl_src, os.path.join(tmp, "fl_ref" + (os.path.splitext(str(_fl_src))[1] or ".png")))
            out = _apply_face_lock(out, _fl_ref, tmp, params, job_id)
        except Exception as _fle:
            api_log(job_id, f"wan-i2v faceLock bỏ qua (giữ output gốc): {_fle}", "warn")
    out = _finalize_mp4(out)
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out)

TEEN_FLYCAM_PRESETS = {
    "street-fashion-5shot": {
        "label": "Street fashion flycam",
        "driver_url": "https://motion-server.datools.info/media/motion-jobs/social-video/1c6c4e66-ed7e-4fe5-abe3-026b09b117b0/396K_views_1.mp4?exp=1783258745&sig=1664bb877e77512fcb389968e16e545806ce4f9fb9367a4ea631059a19d1b4bb",
        "scene": "modern street fashion editorial, realistic outdoor or lifestyle location, natural daylight, premium social video",
        "camera": [
            "slow orbit from front-left to front-right at chest height, keeping the model centered",
            "smooth push-in from full body toward a medium fashion portrait, gentle parallax",
            "side dolly glide with subtle flycam movement, preserving the outfit shape",
            "slow pullback revealing the full outfit, stable vertical composition",
            "camera moves backward facing the model while the model walks forward naturally",
        ],
        "actions": [
            "standing naturally with a relaxed confident posture, subtle weight shift, hands relaxed",
            "small natural pose change, gently turning shoulders, looking slightly away then back to camera",
            "fashion editorial pose, one hand loosely near waist or side, no exaggerated gesture",
            "subtle step in place, natural breathing, calm expression, clothes moving naturally",
            "briefly adjusts sleeve, waist, hair, or outfit edge only if it fits the clothing, otherwise relaxed pose",
        ],
        "shots": [
            {"name": "intro orbit", "camera_pose": "Anti Clockwise (ACW)", "camera_speed": 1.15,
             "camera": "slow flycam orbit from front-left toward center, subtle parallax, full outfit visible",
             "action": "standing naturally with a relaxed confident posture and tiny weight shift"},
            {"name": "full outfit drift", "camera_pose": "Pan Left", "camera_speed": 0.90,
             "camera": "smooth pullback from medium-full shot to full-body fashion framing, stable vertical composition",
             "action": "small natural pose change, shoulders turn slightly, hands relaxed"},
            {"name": "portrait orbit", "camera_pose": "ClockWise (CW)", "camera_speed": 1.10,
             "camera": "smooth push-in to a medium portrait, gentle orbit around the face and upper outfit",
             "action": "calm expression, natural breathing, tiny head turn toward camera"},
            {"name": "side dolly", "camera_pose": "Pan Right", "camera_speed": 1.25,
             "camera": "side dolly glide with visible parallax, keeping the model centered and outfit shape preserved",
             "action": "fashion editorial pose, one hand loosely near waist or side only if natural"},
            {"name": "walk front", "engine": "wan-i2v", "camera_pose": "Static", "camera_speed": 1.0,
             "camera": "camera moves backward facing the model, smooth stable front tracking shot",
             "action": "the model walks forward naturally toward the camera with a calm fashion runway walk"},
        ],
        "final_action": "the model walks forward naturally toward the camera with a calm fashion runway walk",
    }
}

TREND_TIKTOK_PRESETS = {
    "paper-rip": {
        "label": "Xé giấy",
        "driver_url": "https://motion-server.datools.info/media/motion-jobs/social-video/77eb33a3-3fe0-48a4-9370-96cac32bfa15/-_Trend_xe_gi_y_bi_n_hinh_-_P5_douyin_trungquoc_fyp_viraltiktok_....mp4?exp=1783285434&sig=0c52935157d94fee89fb8611a8420c67ec4877831a7610b3c6c2499adeafc740",
        "duration": 6.0,
        "segments": [
            {
                "name": "before-rip",
                "source": "before",
                "start": 0.0,
                "duration": 2.8,
                "prompt": (
                    "holding or presenting a large paper sheet or poster in front of the body, "
                    "playful tiktok setup before the reveal, subtle preparation gesture, keep the paper as a prop only"
                ),
            },
            {
                "name": "after-rip",
                "source": "after",
                "start": 2.8,
                "duration": 3.2,
                "prompt": (
                    "paper rip reveal completed, confidently showing the new outfit, playful tiktok transformation energy, "
                    "natural satisfied pose after the reveal, tiny celebratory gesture, no exaggerated acting"
                ),
            },
        ],
    },
}

def _teen_fetch_template_driver(preset, tmp, duration, fps=16, params=None):
    """Chuẩn hoá clip demo thành motion-driver ẩn cho preset Teen Flycam.
    Đây là cách bám đúng choreography/camera của sample thay vì để Wan tự đoán từ prompt."""
    params = params or {}
    # ALD 29/06/2026 - POOL driver theo thứ tự ưu tiên:
    # 1) params.driverUrls/driverUrl từ FE "Clone preset mẫu"; 2) env; 3) preset.driver_urls; 4) preset.driver_url.
    # Nhiều URL thì bốc ngẫu nhiên 1 clip/job để mỗi model có choreography khác nhau.
    _driver_mode = str(params.get("driverMode") or params.get("driver_mode") or "").lower().strip()
    _raw_param_urls = params.get("driverUrls") or params.get("driver_urls") or params.get("driverUrl") or params.get("driver_url") or params.get("templateUrl") or params.get("template_url") or ""
    if _driver_mode and _driver_mode not in ("custom", "url", "clone", "template"):
        _raw_param_urls = ""
    if isinstance(_raw_param_urls, (list, tuple)):
        _pool = [str(u).strip() for u in _raw_param_urls if str(u).strip()]
    else:
        _pool = [u.strip() for u in re.split(r"[\n,]+", str(_raw_param_urls or "")) if u.strip()]
    if not _pool:
        _pool = [u.strip() for u in (os.environ.get("TEEN_FLYCAM_DEMO_DRIVERS") or "").split(",") if u.strip()]
    if not _pool and isinstance(preset.get("driver_urls"), (list, tuple)):
        _pool = [str(u).strip() for u in preset["driver_urls"] if str(u).strip()]
    src = random.choice(_pool) if _pool else (os.environ.get("TEEN_FLYCAM_DEMO_DRIVER") or preset.get("driver_url") or "").strip()
    if not src:
        raise RuntimeError("Thiếu driver demo Teen Flycam")
    raw = os.path.join(tmp, "teen-demo-driver-src.mp4")
    if src.startswith(("http://", "https://")):
        with requests.get(src, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(raw, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    elif os.path.exists(src):
        shutil.copy(src, raw)
    else:
        raise RuntimeError(f"Không tìm thấy driver demo Teen Flycam: {src}")
    out = os.path.join(tmp, "teen-demo-driver.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", raw,
                    "-t", f"{float(duration):.3f}",
                    "-vf", f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps={int(fps)}",
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", out], check=True, timeout=240)
    return {"video": out, "raw": raw, "source": src}

def _teen_flycam_seed(params):
    mode = str(params.get("seedMode") or params.get("seed_mode") or "random").lower().strip()
    if mode == "fixed":
        return int(params.get("seed") or 42)
    return random.SystemRandom().randint(1, 2_147_483_000)

def _wan_align_driver_frames(video_path, tmp_dir, job_id, label, target_frames, fps):
    """Căn số frame driver thật với num_frames Wan để tránh tensor mismatch."""
    path = str(video_path)
    F = int(target_frames or 0)
    try:
        actual = _video_nframes(path, accurate=True)
        if F > 1 and actual == F - 1:
            padded = os.path.join(tmp_dir, f"{label}-pad.mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", path,
                            "-vf", f"tpad=stop_mode=clone:stop_duration={1 / float(fps):.9f}",
                            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                            "-c:a", "copy", "-movflags", "+faststart", padded], check=True, timeout=120)
            padded_frames = _video_nframes(padded, accurate=True)
            if padded_frames >= F:
                api_log(job_id, f"{label}: pad frame biên cuối {actual}f → {F}f để khớp Wan", "info")
                path = padded
                actual = padded_frames
        if actual > 0 and actual < F:
            new_f = max(17, ((actual - 1) // 4) * 4 + 1)
            if new_f < F:
                api_log(job_id, f"{label}: driver thực {actual}f < target {F}f → hạ còn {new_f}f để tránh lệch tensor", "info")
                F = new_f
    except Exception as e:
        api_log(job_id, f"{label}: align driver frames lỗi, giữ target {F}f ({e})", "warn")
    return path, F

def run_trend_tiktok(job):
    """Node Trend TikTok: 2 ảnh before/after + preset driver → clip reveal theo trend."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    before_key = inputs.get("before") or inputs.get("image") or inputs.get("input") or inputs.get("look1")
    after_key = inputs.get("after") or inputs.get("image2") or inputs.get("target") or inputs.get("look2")
    audio_key = inputs.get("audio") or inputs.get("music") or inputs.get("sound")
    if not before_key or not after_key:
        raise RuntimeError("trend-tiktok cần 2 ảnh: look đầu và look sau")

    preset_id = str(params.get("preset") or "paper-rip").strip() or "paper-rip"
    preset = TREND_TIKTOK_PRESETS.get(preset_id)
    if not preset:
        raise RuntimeError(f"trend-tiktok chưa hỗ trợ preset '{preset_id}'")

    duration = max(3.0, min(30.0, float(params.get("duration") or preset.get("duration") or 6.0)))
    fps = max(16, min(24, int(params.get("fps") or params.get("renderFps") or 20)))
    steps = int(params.get("steps") or 20)
    seed0 = _teen_flycam_seed(params)
    tmp = tempfile.mkdtemp(prefix=f"trend-tiktok-{job_id[:8]}-")

    api_progress(job_id, 0.04, "tải ảnh before/after")
    before_local = api_download(before_key, os.path.join(tmp, "before" + (os.path.splitext(str(before_key))[1] or ".png")))
    after_local = api_download(after_key, os.path.join(tmp, "after" + (os.path.splitext(str(after_key))[1] or ".png")))
    W, H = _bds_resolve_wh({ **params, "aspectRatio": "9:16" }, before_local, short=int(params.get("shortSide") or 480), cap_long=int(params.get("capLong") or 832))
    out_W, out_H = 720, 1280

    skin_guard_positive = (
        "realistic matte skin texture, natural human skin tone with visible texture, "
        "soft even diffuse daylight on face, arms and hands, balanced exposure on skin, "
        "no shiny or oily skin, no harsh specular highlights, no flash glare"
    )
    skin_guard_negative = (
        "shiny oily skin, glossy plastic skin, specular highlights on skin, blown-out highlights, "
        "overexposed arms, overexposed hands, white glowing skin, flash glare, harsh relighting, plastic doll skin"
    )
    neg = (params.get("negativePrompt") or params.get("negative_prompt") or "").strip() or (
        "low quality, blurry, distorted face, identity drift, copied driver face, different person, deformed body, "
        "extra people, extra limbs, extra fingers, missing fingers, warped hands, broken hands, broken paper, "
        "multiple paper layers, duplicated hands, duplicated face, outfit mixing, outfit morphing, bad transition, "
        "logo, watermark, text, oversmoothed skin, beauty filter, exaggerated makeup, wrong body proportions, "
        f"{skin_guard_negative}"
    )
    gender = str(params.get("modelGender") or params.get("gender") or "auto").lower().strip()
    audio_mode = str(params.get("audioMode") or params.get("audio_mode") or "preset").lower().strip()
    gender_prompt = {
        "female": "female fashion model, elegant playful tiktok gesture language, soft controlled movement, natural feminine posing",
        "male": "male fashion model, clean confident tiktok reveal gesture, controlled natural posing, no exaggerated acting",
    }.get(gender, "fashion model, controlled playful tiktok reveal gesture, natural pose language")

    api_progress(job_id, 0.10, "tải preset trend driver")
    driver_asset = _teen_fetch_template_driver(preset, tmp, duration, fps=fps, params={})

    clips = []
    segments = list(preset.get("segments") or [])
    if not segments:
        raise RuntimeError("Preset trend-tiktok thiếu segments")

    for i, seg in enumerate(segments):
        seg_name = str(seg.get("name") or f"segment-{i + 1}")
        seg_dur = max(1.2, float(seg.get("duration") or (duration / max(1, len(segments)))))
        seg_start = max(0.0, float(seg.get("start") or 0.0))
        seg_image = after_local if str(seg.get("source") or "before").lower().strip() == "after" else before_local
        seg_driver = _cut_motion_driver_segment(
            driver_asset["video"], tmp,
            {"driverStartSec": seg_start, "driverDurSec": seg_dur, "frames": _teen_wan_frames(seg_dur, fps), "render_fps": fps},
            job_id, label=f"trend-{i + 1}"
        )
        F = _teen_wan_frames(seg_dur, fps)
        ref_name = comfy_upload(seg_image)
        seg_driver, F = _wan_align_driver_frames(seg_driver, tmp, job_id, f"trend-{i + 1}", F, fps)
        motion_name = comfy_upload(seg_driver)
        prefix = f"trend-tiktok-{job_id[:8]}-{i + 1}"
        positive = (
            "same person from the input image, preserve exact face identity, facial structure, eyes, nose, lips, jawline, hairstyle, "
            "skin tone, body proportions, and the exact outfit from the input image, do not copy the driver's face, "
            "do not borrow facial features from the driver video, natural real human face, no beauty retouch, "
            f"{skin_guard_positive}, {gender_prompt}, "
            f"{seg.get('prompt') or 'natural tiktok transition gesture'}, "
            "stable anatomy, realistic hands, realistic fingers, no hand duplication, premium tiktok vertical video"
        )
        mp = {
            **params,
            "width": W, "height": H, "frames": F, "steps": int(params.get("animateSteps") or 6),
            "render_fps": fps, "frame_window_size": 77, "faceSource": "ref",
            "loraRelight": 0.0, "lora_relight": 0.0,
            "pose_strength": float(params.get("poseStrength") or 0.56),
            "face_strength": float(params.get("faceStrength") or 0.58),
            "clip_strength": float(params.get("clipStrength") or 1.28),
            "positive_prompt": positive,
            "negative_prompt": neg,
        }
        prog_lo = 0.16 + 0.34 * i
        prog_hi = 0.16 + 0.34 * (i + 1)
        api_log(job_id, f"trend-tiktok: render {seg_name} ({seg_dur:.2f}s @ {fps}fps)", "info")
        api_progress(job_id, prog_lo, f"Trend TikTok · {seg_name}")
        pid = comfy_submit(build_wan_workflow(ref_name, motion_name, mp, prefix=prefix))
        outputs = comfy_poll(
            pid, job_id,
            deadline_sec=int(params.get("driverTimeoutSec") or 2400),
            prog_lo=prog_lo, prog_hi=prog_hi, prog_step=f"Trend TikTok · {seg_name}",
            windows=max(1, (F + 76) // 77), output_prefix=prefix
        )
        raw = comfy_fetch_output(outputs)
        if not raw:
            raise RuntimeError(f"Trend TikTok {seg_name} không trả MP4")
        norm = os.path.join(tmp, f"{seg_name}.mp4")
        _ff_norm_clip_cover(raw, norm, out_W, out_H, seg_dur, fps=fps, crf=18)
        clips.append(norm)

    if not clips:
        raise RuntimeError("trend-tiktok không tạo được segment nào")

    api_progress(job_id, 0.86, f"ghép {len(clips)} segment")
    final = os.path.join(tmp, "trend-tiktok.mp4")
    _concat_video_cut(clips, final, fps=fps, trim_sec=duration)
    if audio_mode in ("preset", "original", "driver", "source"):
        try:
            api_progress(job_id, 0.91, "ghép audio gốc preset")
            final = _mux_loop_audio(final, driver_asset["raw"], os.path.join(tmp, "trend-tiktok-preset-audio.mp4"), duration)
        except Exception as e:
            api_log(job_id, f"Trend TikTok ghép audio preset lỗi, giữ video im lặng: {e}", "warn")
    elif audio_mode in ("input", "replacement", "custom"):
        if audio_key:
            try:
                api_progress(job_id, 0.91, "ghép audio input")
                audio_local = api_download(audio_key, os.path.join(tmp, "audio" + (os.path.splitext(str(audio_key))[1] or ".mp3")))
                final = _mux_loop_audio(final, audio_local, os.path.join(tmp, "trend-tiktok-audio.mp4"), duration)
            except Exception as e:
                api_log(job_id, f"Trend TikTok ghép audio input lỗi, giữ video im lặng: {e}", "warn")
        else:
            api_log(job_id, "Trend TikTok chọn audio input nhưng không có cổng audio nối vào → xuất im lặng", "warn")

    final = _finalize_mp4(final)
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, final)

def _teen_crop_keyframe(src, dst, zoom=1.0, cx=0.5, cy=0.5, aspect=(9, 16)):
    """Crop ảnh ref thành keyframe khác nhau cho từng shot để Wan có mục tiêu camera/framing rõ hơn."""
    w0, h0 = _img_dims(src)
    if not w0 or not h0:
        shutil.copy(src, dst)
        return dst
    ar = float(aspect[0]) / max(1.0, float(aspect[1]))
    if w0 / h0 > ar:
        base_h = h0
        base_w = int(round(base_h * ar))
    else:
        base_w = w0
        base_h = int(round(base_w / ar))
    zoom = max(1.0, min(2.2, float(zoom or 1.0)))
    cw = max(64, min(w0, int(round(base_w / zoom))))
    ch = max(64, min(h0, int(round(base_h / zoom))))
    cw -= cw % 2; ch -= ch % 2
    cx = max(0.0, min(1.0, float(cx)))
    cy = max(0.0, min(1.0, float(cy)))
    x = int(round((w0 - cw) * cx)); y = int(round((h0 - ch) * cy))
    x = max(0, min(w0 - cw, x)); y = max(0, min(h0 - ch, y))
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                    "-vf", f"crop={cw}:{ch}:{x}:{y},setsar=1", "-frames:v", "1", str(dst)],
                   check=True, timeout=60)
    return dst

def _concat_video_cut(clips, out, fps=30, trim_sec=None):
    listf = os.path.join(os.path.dirname(str(out)), "concat-list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(str(c)).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")
    merged = os.path.join(os.path.dirname(str(out)), "merged.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c", "copy", "-an", merged], check=True, timeout=600)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", merged]
    if trim_sec:
        cmd += ["-t", f"{float(trim_sec):.3f}"]
    cmd += ["-r", str(int(fps)), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, timeout=600)
    return out

def _mux_loop_audio(video, audio, out, dur):
    """Ghép audio tuỳ chọn cho social clip: audio ngắn thì loop, audio dài thì cắt theo duration."""
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-stream_loop", "-1", "-i", str(audio),
                    "-map", "0:v:0", "-map", "1:a:0", "-t", f"{float(dur):.3f}",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", str(out)],
                   check=True, timeout=300)
    return out

def build_wan_fun_camera_workflow(start_name, prompt, W, H, F, S, prefix, seed=42, neg=None,
                                  camera_pose="Zoom In", camera_speed=1.0):
    """Wan Fun Camera native: ảnh ref + camera embedding → video.
    Dùng graph core của ComfyUI để camera là conditioning thật, không crop/zoom hậu kỳ."""
    valid_pose = {
        "Static", "Pan Up", "Pan Down", "Pan Left", "Pan Right", "Zoom In", "Zoom Out",
        "Anti Clockwise (ACW)", "ClockWise (CW)"
    }
    pose = camera_pose if camera_pose in valid_pose else "Zoom In"
    speed = max(0.1, min(3.0, float(camera_speed or 1.0)))
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "11": {"class_type": "ImageScale", "inputs": {
            "image": ["10", 0], "upscale_method": "lanczos", "width": int(W), "height": int(H), "crop": "center"}},
        "20": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "21": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["20", 0], "image": ["11", 0], "crop": "none"}},
        "30": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "31": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["30", 0], "text": prompt}},
        "32": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["30", 0], "text": (neg or "")}},
        "40": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "50": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "wan2.1_fun_camera_v1.1_1.3B_bf16.safetensors", "weight_dtype": "default"}},
        "51": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["50", 0], "shift": 8.0}},
        "60": {"class_type": "WanCameraEmbedding", "inputs": {
            "camera_pose": pose, "width": int(W), "height": int(H), "length": int(F),
            "speed": speed, "fx": 0.5, "fy": 0.5, "cx": 0.5, "cy": 0.5}},
        "70": {"class_type": "WanCameraImageToVideo", "inputs": {
            "positive": ["31", 0], "negative": ["32", 0], "vae": ["40", 0],
            "width": int(W), "height": int(H), "length": int(F), "batch_size": 1,
            "clip_vision_output": ["21", 0], "start_image": ["11", 0], "camera_conditions": ["60", 0]}},
        "80": {"class_type": "KSampler", "inputs": {
            "model": ["51", 0], "positive": ["70", 0], "negative": ["70", 1], "latent_image": ["70", 2],
            "seed": int(seed), "steps": int(S), "cfg": 6.0, "sampler_name": "uni_pc",
            "scheduler": "simple", "denoise": 1.0}},
        "90": {"class_type": "VAEDecode", "inputs": {"samples": ["80", 0], "vae": ["40", 0]}},
        "100": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["90", 0], "frame_rate": 16, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }

def run_teen_flycam(job):
    """Node Teen Flycam: 1 ảnh người mẫu → social clip bám demo driver bằng Wan Animate."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    image_key = inputs.get("image") or inputs.get("input") or inputs.get("model") or inputs.get("start") or inputs.get("ref")
    audio_key = inputs.get("audio") or inputs.get("music") or inputs.get("sound")
    if not image_key:
        raise RuntimeError("teen-flycam cần 1 ảnh người mẫu")
    preset_id = str(params.get("preset") or "").strip()
    driver_urls = str(params.get("driverUrls") or params.get("driver_urls") or params.get("driverUrl") or params.get("driver_url") or "").strip()
    preset = TEEN_FLYCAM_PRESETS.get(preset_id)
    if not preset:
        if not driver_urls:
            raise RuntimeError("teen-flycam chưa có preset. Hãy clone preset mẫu trước khi chạy")
        preset = {
            "label": "Custom preset",
            "driver_url": "",
            "scene": "fashion editorial social video, realistic lifestyle location, natural daylight, premium vertical video",
            "camera": [],
            "actions": [],
            "shots": [],
            "final_action": "the model walks forward naturally toward the camera with a calm fashion runway walk",
        }
    duration = max(2.0, min(30.0, float(params.get("duration") or 10)))
    shot_count = max(3, min(6, int(params.get("shotCount") or params.get("shot_count") or 5)))
    per_shot = duration / shot_count
    default_fps = 24 if duration <= 10.2 else 20
    fps = max(16, min(24, int(params.get("fps") or params.get("renderFps") or default_fps)))
    engine = "wan-animate-demo-driver"
    steps = int(params.get("steps") or 20)
    seed0 = _teen_flycam_seed(params)
    tmp = tempfile.mkdtemp(prefix=f"teen-flycam-{job_id[:8]}-")
    api_progress(job_id, 0.04, "tải ảnh người mẫu")
    image_local = api_download(image_key, os.path.join(tmp, "model" + (os.path.splitext(str(image_key))[1] or ".png")))
    W, H = _bds_resolve_wh({ **params, "aspectRatio": "9:16" }, image_local, short=int(params.get("shortSide") or 480), cap_long=int(params.get("capLong") or 832))
    out_W, out_H = 720, 1280
    skin_guard_positive = (
        "realistic matte skin texture, natural human skin tone with visible texture, "
        "soft even diffuse daylight on face, arms and hands, balanced exposure on skin, "
        "no shiny or oily skin, no harsh specular highlights, no flash glare, no blown-out hands or arms"
    )
    skin_guard_negative = (
        "色调艳丽，过曝，皮肤油光，高光反射，手臂反光, shiny oily skin, glossy plastic skin, "
        "specular highlights on skin, blown-out highlights, overexposed arms, overexposed hands, "
        "white glowing skin, flash glare, harsh relighting, plastic doll skin, wax skin"
    )
    neg = (params.get("negativePrompt") or params.get("negative_prompt") or "").strip() or (
        "low quality, blurry, distorted face, deformed body, extra people, extra limbs, extra fingers, missing fingers, "
        "warped hands, broken hands, elongated legs, stretched legs, stretched body, unnatural long legs, "
        "wrong body proportions, text, logo, watermark, outfit change, identity change, flicker, jump cut, "
        "changing shoes, mismatched footwear, shoe color changing between shots, outfit color changing between shots, "
        "different person across cuts, soldier march, stomping walk, stiff marching gait, exaggerated stride, wide aggressive steps, "
        "short legs, stumpy legs, shrunken legs, bent legs, broken knees, twisted ankles, deformed feet, warped shoes, "
        "melted shoes, barefoot, feet sinking into ground, shoes sinking into ground, floating feet, bad foot contact, "
        f"{skin_guard_negative}"
    )
    gender = str(params.get("modelGender") or params.get("gender") or "auto").lower().strip()
    audio_mode = str(params.get("audioMode") or params.get("audio_mode") or "preset").lower().strip()
    gender_prompt = {
        "female": "female fashion model, soft relaxed fashion walk, small elegant steps, gentle shoulders, natural feminine pose language",
        "male": "male fashion model, clean relaxed menswear walk, stable shoulders, natural confident stride, no exaggerated posing",
    }.get(gender, "fashion model, relaxed natural runway walk, small controlled steps, gender-neutral natural pose language")
    driver_mode = str(params.get("driverMode") or params.get("driver_mode") or "demo").lower().strip()
    use_template_driver = driver_mode not in ("shots", "legacy-fun-camera", "fun-camera")
    if use_template_driver:
        try:
            api_progress(job_id, 0.10, "tải preset demo driver")
            # ALD 28/06/2026 - SMOOTH: render NATIVE @fps (mặc định 24/20) thay vì 16fps rồi _ff_norm ép 30fps (duplicate
            # frame KHÔNG đều = giật). Driver + render_fps + frames + output ĐỀU cùng fps → CFR thật, không RIFE
            # (tránh ghosting/mờ). Đổi lại render lâu hơn ~(fps/16)×. Hạ params.fps=16 nếu cần nhanh.
            driver_asset = _teen_fetch_template_driver(preset, tmp, duration, fps=fps, params=params)
            driver_local = driver_asset["video"]
            max_frames = int(os.environ.get("TEEN_FLYCAM_MAX_FRAMES", "289") or 289)
            max_single_pass_dur = max(2.0, float(max_frames - 1) / max(1.0, float(fps)))
            seg_count = max(1, int(math.ceil(float(duration) / max_single_pass_dur)))
            ref_name = comfy_upload(image_local)
            base_mp = {
                **params,
                "width": W, "height": H, "steps": int(params.get("animateSteps") or 6),
                "render_fps": fps, "frame_window_size": 77, "faceSource": "ref",
                # Teen Flycam không dùng relight LORA của motion. Khóa cứng = 0 để tránh da/mặt bị sáng giả.
                "loraRelight": 0.0,
                "lora_relight": 0.0,
                # ALD 29/06/2026 - Fix "mặt nhựa/khác ref":
                # ảnh ref full-body có face crop nhỏ. Nếu face_strength quá cao, Wan sẽ tự "beautify" khuôn mặt
                # thay vì giữ đúng người thật. Cân lại gần baseline motion:
                # - face_strength vừa phải để khóa identity nhưng không ép hallucinate chi tiết mặt,
                # - tăng clip_strength từ toàn ảnh ref để giữ đúng tổng thể khuôn mặt/người mẫu,
                # - pose_strength vừa phải để chỉ mượn nhịp body/camera.
                "pose_strength": float(params.get("poseStrength") or 0.60),
                "face_strength": float(params.get("faceStrength") or 0.52),
                "clip_strength": float(params.get("clipStrength") or 1.24),
                "positive_prompt": (
                    "same person from the input image, preserve face identity, facial structure, eyes, nose, lips, jawline, hairstyle, outfit, footwear and natural body proportions, "
                    "keep the exact same face from the reference image across the whole clip, do not copy the driver's face, "
                    "do not borrow facial expression or facial features from the driver video, facial identity stays locked to the reference image, "
                    "preserve the original facial proportions and natural likeness from the reference image, real human face, natural eyes, natural nose, natural lips, no beauty retouch, no glamorized makeover, "
                    f"{skin_guard_positive}, "
                    "high-fashion model standard body proportions, elegant tall runway silhouette, balanced long legs, "
                    "straight healthy legs, natural knees and ankles, clean well-shaped shoes, accurate footwear details, "
                    "feet and shoes stay firmly on the floor with realistic ground contact, no sinking into the ground, "
                    "the same outfit and the same footwear are kept in every frame and across all cuts, "
                    "shoes keep one consistent color and shape based on the reference image, "
                    f"{gender_prompt}, street fashion social video, natural confident poses, "
                    "continuous stabilized flycam motion, smooth camera orbit and tracking with no jitter, smooth camera cuts, "
                    "final runway walk toward camera, realistic clothing motion, natural leg length, "
                    "stable anatomy, cinematic realistic lighting"
                ),
                "negative_prompt": (
                    f"{neg}, different face, identity drift, copied driver face, driver facial expression, "
                    "face shape changing, eye shape changing, nose changing, lips changing, hairstyle changing, "
                    "beautified face, glam makeup face, doll face, plastic beauty face, overretouched portrait, exaggerated eyelashes, oversized eyes, v-line jaw, tiny nose, porcelain skin, ai beauty filter"
                ),
            }
            api_log(job_id, f"teen-flycam: dùng demo motion driver dài {duration:.1f}s @{fps}fps → chia {seg_count} segment (max {max_single_pass_dur:.1f}s/segment) | face=ref-locked pose=0.60 face=0.52 clip=1.24", "info")
            clips = []
            seg_start = 0.0
            for seg_idx in range(seg_count):
                seg_dur = min(max_single_pass_dur, max(0.25, float(duration) - seg_start))
                F = _teen_wan_frames(seg_dur, fps)
                wan_prefix = f"teen-flycam-demo-{job_id[:8]}-{seg_idx + 1}"
                driver_seg = _cut_motion_driver_segment(
                    driver_local, tmp,
                    {"driverStartSec": seg_start, "driverDurSec": seg_dur, "frames": F, "render_fps": fps},
                    job_id, label=f"teen-demo-{seg_idx + 1}"
                )
                driver_seg, F = _wan_align_driver_frames(driver_seg, tmp, job_id, f"teen-demo-{seg_idx + 1}", F, fps)
                motion_name = comfy_upload(driver_seg)
                mp = { **base_mp, "frames": F }
                prog_lo = 0.18 + (0.70 * seg_idx / seg_count)
                prog_hi = 0.18 + (0.70 * (seg_idx + 1) / seg_count)
                api_progress(job_id, prog_lo, f"Teen Flycam demo driver · segment {seg_idx + 1}/{seg_count}")
                pid = comfy_submit(build_wan_workflow(ref_name, motion_name, mp, prefix=wan_prefix))
                outputs = comfy_poll(pid, job_id, deadline_sec=int(params.get("driverTimeoutSec") or 2400),
                                     prog_lo=prog_lo, prog_hi=prog_hi, prog_step=f"Teen Flycam demo driver · segment {seg_idx + 1}/{seg_count}",
                                     windows=max(1, (F + 76) // 77), output_prefix=wan_prefix)
                raw = comfy_fetch_output(outputs)
                if not raw:
                    raise RuntimeError(f"ComfyUI không trả MP4 cho segment {seg_idx + 1}/{seg_count}")
                seg_out = os.path.join(tmp, f"teen-flycam-demo-seg-{seg_idx + 1:02d}.mp4")
                _ff_norm_clip_cover(raw, seg_out, out_W, out_H, seg_dur, fps=fps, crf=18)
                clips.append(seg_out)
                seg_start += seg_dur
            final = os.path.join(tmp, "teen-flycam-demo.mp4")
            if len(clips) == 1:
                shutil.copy(clips[0], final)
            else:
                _concat_video_cut(clips, final, fps=fps, trim_sec=duration)
            if audio_mode in ("preset", "original", "driver", "source"):
                try:
                    api_progress(job_id, 0.92, "ghép audio gốc preset")
                    final = _mux_loop_audio(final, driver_asset["raw"], os.path.join(tmp, "teen-flycam-demo-preset-audio.mp4"), duration)
                except Exception as e:
                    api_log(job_id, f"Teen Flycam ghép audio gốc preset lỗi, giữ video im lặng: {e}", "warn")
            elif audio_mode in ("input", "replacement", "custom"):
                if audio_key:
                    try:
                        api_progress(job_id, 0.92, "ghép audio input")
                        audio_local = api_download(audio_key, os.path.join(tmp, "audio" + (os.path.splitext(str(audio_key))[1] or ".mp3")))
                        final = _mux_loop_audio(final, audio_local, os.path.join(tmp, "teen-flycam-demo-audio.mp4"), duration)
                    except Exception as e:
                        api_log(job_id, f"Teen Flycam ghép audio input lỗi, giữ video im lặng: {e}", "warn")
                else:
                    api_log(job_id, "Teen Flycam chọn audio input nhưng không có cổng audio nối vào → xuất im lặng", "warn")
            final = _finalize_mp4(final)
            api_progress(job_id, 0.95, "upload output")
            api_upload_output(job_id, final)
            return
        except Exception as e:
            raise RuntimeError(f"Teen Flycam demo-driver lỗi: {e}")
    actions = list(preset["actions"])
    cameras = list(preset["camera"])
    shots = list(preset.get("shots") or [])
    rnd = random.Random(seed0)
    rnd.shuffle(actions)
    rnd.shuffle(cameras)
    clips = []
    api_log(job_id, f"teen-flycam: preset={preset_id} shots={shot_count} duration={duration:.1f}s engine={engine} seed={seed0}", "info")
    for i in range(shot_count):
        is_final = i == shot_count - 1
        shot = shots[i % len(shots)] if shots else {}
        if is_final and shots:
            shot = shots[-1]
        action = shot.get("action") or (preset["final_action"] if is_final else actions[i % len(actions)])
        camera = shot.get("camera") or cameras[i % len(cameras)]
        shot_engine = str(shot.get("engine") or "fun-camera").lower().strip()
        if is_final:
            shot_engine = "wan-i2v"
        prompt = (
            f"{preset['scene']}, vertical 9:16 video, the same person from the input image, preserve face identity, "
            f"hair, outfit, body proportions and styling, photorealistic natural motion, {action}, camera: {camera}, "
            f"shot style: {shot.get('name') or f'shot {i + 1}'}, cinematic but realistic movement, stable anatomy, "
            "natural hands, natural leg length, preserve original body proportions, no text, no logo, no watermark, no extra people"
        )
        F = wan_t2v_frames(per_shot)
        prefix = f"teen-flycam-{job_id[:8]}-{i + 1}"
        api_progress(job_id, 0.08 + 0.76 * (i / shot_count), f"Teen Flycam shot {i + 1}/{shot_count} · {shot_engine}")
        try:
            start_name = comfy_upload(image_local)
            if shot_engine == "wan-i2v":
                walk_prompt = (
                    f"{preset['scene']}, vertical 9:16 social fashion video, same person from the input image, "
                    "preserve face identity, hair, outfit and natural body proportions, the model walks forward "
                    "naturally toward the camera, relaxed runway walk, camera tracks backward facing the model, "
                    "subtle arm swing, realistic clothing motion, stable anatomy, natural leg length, no text, no logo"
                )
                wf = build_bds_segment_workflow(start_name, None, walk_prompt, W, H, F, max(8, min(12, steps)), prefix,
                                                seed=seed0 + i * 9973, neg=neg, rife_mult=1,
                                                wan_ver="wan2.1", noise_aug=float(params.get("noiseAug") or 0.02),
                                                params=params)
            else:
                camera_pose = shot.get("camera_pose") or "Pan Right"
                camera_speed = float(params.get("cameraSpeed") or shot.get("camera_speed") or 1.0)
                wf = build_wan_fun_camera_workflow(start_name, prompt, W, H, F, steps, prefix,
                                                   seed=seed0 + i * 9973, neg=neg,
                                                   camera_pose=camera_pose, camera_speed=camera_speed)
            raw = comfy_fetch_output(comfy_poll(comfy_submit(wf), job_id,
                                                deadline_sec=int(params.get("shotTimeoutSec") or 2400),
                                                output_prefix=prefix))
            if not raw:
                raise RuntimeError("ComfyUI không trả MP4")
            norm = os.path.join(tmp, f"shot-{i + 1:02d}.mp4")
            _ff_norm_clip_cover(raw, norm, out_W, out_H, per_shot, fps=fps, crf=18)
            clips.append(norm)
        except Exception as e:
            api_log(job_id, f"Teen Flycam shot {i + 1}/{shot_count} lỗi: {e}", "error")
            raise RuntimeError(f"Teen Flycam shot {i + 1}/{shot_count} lỗi: {e}")
    if not clips:
        raise RuntimeError("Teen Flycam không tạo được shot nào")
    api_progress(job_id, 0.88, f"ghép {len(clips)} shot")
    final = os.path.join(tmp, "teen-flycam.mp4")
    _concat_video_cut(clips, final, fps=fps, trim_sec=duration)
    if audio_key:
        try:
            api_progress(job_id, 0.92, "ghép audio")
            audio_local = api_download(audio_key, os.path.join(tmp, "audio" + (os.path.splitext(str(audio_key))[1] or ".mp3")))
            muxed = os.path.join(tmp, "teen-flycam-audio.mp4")
            final = _mux_loop_audio(final, audio_local, muxed, duration)
        except Exception as e:
            api_log(job_id, f"Teen Flycam ghép audio lỗi, giữ video im lặng: {e}", "warn")
    final = _finalize_mp4(final)
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, final)

def run_voiceover(job):
    """Node voiceover: 1 video + script → TTS, rồi thay/trộn audio. Copy video stream để giữ nguyên hình."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    video_key = inputs.get("input") or inputs.get("video") or inputs.get("clip") or inputs.get("motion")
    if not video_key:
        raise RuntimeError("voiceover cần 1 video đầu vào")
    script = (params.get("script") or params.get("text") or params.get("line") or "").strip()
    if not script:
        raise RuntimeError("voiceover cần lời thuyết minh (script)")
    tmp = tempfile.mkdtemp(prefix=f"voiceover-{job_id[:8]}-")
    api_progress(job_id, 0.08, "tải video")
    vid = api_download(video_key, os.path.join(tmp, "input" + (os.path.splitext(video_key)[1] or ".mp4")))
    voice = os.path.join(tmp, "voice.mp3")
    api_progress(job_id, 0.25, "TTS voiceover")
    _tts(script, voice, params.get("voice") or params.get("voiceId") or DEFAULT_VOICE,
         gem_key=_gemini_key(params),
         emotion=params.get("emotion"), pace=params.get("pace"), gender=params.get("voiceGender") or params.get("gender"))
    out = os.path.join(tmp, "voiceover.mp4")
    mix = str(params.get("mix") or "replace").lower().strip()
    dur = _ffprobe_dur(vid) or _audio_dur(vid) or 0
    if dur > 0:
        vo = f"[1:a]volume=1.0,apad,atrim=0:{dur:.3f}[vo]"
        if mix == "under" and _has_audio(vid):
            filt = f"[0:a]volume=0.18[bg];{vo};[bg][vo]amix=inputs=2:duration=first:dropout_transition=0[a]"
        else:
            filt = f"{vo};[vo]anull[a]"
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", vid, "-i", voice,
               "-filter_complex", filt, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
               "-t", f"{dur:.3f}", "-movflags", "+faststart", out]
    else:
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", vid, "-i", voice,
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest",
               "-movflags", "+faststart", out]
    api_progress(job_id, 0.75, "ghép voiceover")
    subprocess.run(cmd, check=True, timeout=600)
    out = _finalize_mp4(out)
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out)

# #region ALD 14/06/2026 - node "SS": ẢNH + prompt → VIDEO bằng LTX-2.3 (base GGUF) + LoRA CUSTOM user tự train
# (upload qua Settings → Model AI → ComfyUI/models/uploads/loras). Engine I2V GENERATIVE (giống run_video) NHƯNG
# nạp THÊM LoRA của user qua LoraLoaderModelOnly trong graph. KHÔNG hardcode graph LTX-2.3 (node ra sau cutoff,
# wiring phức tạp — xem ghi chú run_video/build_ltx_i2v_workflow): dựng từ TEMPLATE ĐÃ-VERIFY (params.ssWorkflow |
# env SS_WORKFLOW_JSON = 'Save (API Format)' graph LTX-2.3 I2V trên box) rồi thay token động. Template nên có 1 node
# LoraLoaderModelOnly với lora_name=__LORA__, strength_model=__LORA_STRENGTH__ (worker GỠ node này nếu user không
# chọn LoRA → chạy base thuần). Text-encoder=Gemma(SS_GEMMA), VAE=SS_VAE, model=SS_MODEL(GGUF có sẵn trên box).
# Dùng `or default` (KHÔNG phải get(k, default)) để env RỖNG ("") cũng rơi về default — docker-compose hay set
# `${SS_VAE:-}` rỗng sẽ ghi đè default nếu dùng get(); `or` tránh bug "vae_name rỗng" (ALD 14/06/2026).
SS_MODEL         = os.environ.get("SS_MODEL") or LTX_MODEL                          # GGUF base LTX-2.3 (UnetLoaderGGUF) = __MODEL__
SS_GEMMA         = os.environ.get("SS_GEMMA") or "comfy_gemma_3_12B_it.safetensors" # text-encoder Gemma-3 (LTXAVTextEncoderLoader) = __GEMMA__
SS_VAE           = os.environ.get("SS_VAE") or "ltx-2.3-vae.safetensors"            # video VAE LTX-2.3 tách rời (kèm config) = __VAE__
SS_CKPT          = os.environ.get("SS_CKPT") or "ltx-2.3-22b-distilled-1.1.safetensors"  # checkpoint 22b: audio-vae + projection = __CKPT__
SS_FPS           = int(os.environ.get("SS_FPS", str(LTX_FPS)))
SS_STEPS         = int(os.environ.get("SS_STEPS", str(LTX_STEPS)))                # distilled ~8 bước (ManualSigmas trong template)
SS_TIMEOUT       = int(os.environ.get("SS_TIMEOUT_SEC", str(LTX_TIMEOUT)))
SS_WORKFLOW_JSON = os.environ.get("SS_WORKFLOW_JSON", "")
# Template AV đã-verify (render thật trên box 14/06/2026) đóng gói kèm worker — fallback khi không set env/param.
SS_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), "..", "assets", "ss_workflow_av.json")

# Kích thước theo aspectRatio. MẶC ĐỊNH theo bản ĐÃ VERIFY (960x544, ~16:9) — đổi tỉ lệ giữ ~0.5MP, chia hết 32.
_SS_DIMS = {"16:9": (960, 544), "9:16": (544, 960), "1:1": (704, 704), "4:3": (896, 672), "3:4": (672, 896)}
def _ss_dims(params):
    ar = str(params.get("aspectRatio") or params.get("aspect_ratio") or "9:16")
    W, H = _SS_DIMS.get(ar, _SS_DIMS["9:16"])
    return int(params.get("width", W)), int(params.get("height", H))

def _ss_template(p):
    """Template workflow SS (dict): params.ssWorkflow | env SS_WORKFLOW_JSON (path|JSON) | asset đóng gói (đã-verify)."""
    raw = p.get("ssWorkflow") or SS_WORKFLOW_JSON
    if isinstance(raw, dict): return raw
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if not s.startswith("{") and os.path.exists(s):
            with open(s) as f: return json.load(f)
        return json.loads(s)
    # Fallback: template AV đã-verify đóng gói kèm worker (worker/assets/ss_workflow_av.json).
    if os.path.exists(SS_TEMPLATE_FILE):
        with open(SS_TEMPLATE_FILE) as f: return json.load(f)
    return None

def _strip_lora_nodes(wf):
    """Gỡ mọi node LoraLoaderModelOnly (khi user KHÔNG chọn LoRA) → nối thẳng 'model' nguồn qua, bỏ qua lora.
    Xử lý cả chuỗi nhiều lora nối tiếp. KHÔNG đụng node khác."""
    lora_src = {nid: (n.get("inputs") or {}).get("model")
                for nid, n in wf.items() if isinstance(n, dict) and n.get("class_type") == "LoraLoaderModelOnly"}
    if not lora_src:
        return wf
    def resolve(ref):
        seen = set()
        while isinstance(ref, list) and len(ref) == 2 and str(ref[0]) in lora_src and str(ref[0]) not in seen:
            seen.add(str(ref[0])); ref = lora_src[str(ref[0])]
        return ref
    out = {}
    for nid, n in wf.items():
        if nid in lora_src:
            continue
        if isinstance(n, dict):
            ins = {k: (resolve(v) if isinstance(v, list) and len(v) == 2 else v) for k, v in (n.get("inputs") or {}).items()}
            out[nid] = {**n, "inputs": ins}
        else:
            out[nid] = n
    return out

SS_SEG_SEC = int(os.environ.get("SS_SEG_SEC", "5"))   # mỗi đoạn render ~5s (vừa VRAM 1 lượt). Dài hơn → chia đoạn + ghép.

def _ss_last_frame(clip, out_png):
    """Trích frame CUỐI clip → png, để I2V-chaining đoạn kế (nối tiếp liên tục)."""
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-sseof", "-1", "-i", str(clip),
                    "-update", "1", "-frames:v", "1", str(out_png)], check=True)
    return out_png

def _ss_blank_image(out_png, w=64, h=64):
    """Ảnh xám blank cho đoạn T2V đầu (không có ảnh): LoadImage cần 1 ảnh, nhưng node I2V bypass nên ảnh bị bỏ qua."""
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}",
                    "-frames:v", "1", str(out_png)], check=True)
    return out_png

def build_ss_workflow(image_name, p, frames, W, H, prompt="", neg="", prefix="ss", lora="", lora_strength=1.0,
                      steps=None, bypass=False, seed=None, extra_images=None):
    """Dựng workflow LTX-2.3 node SS từ template đã-verify, thay token. bypass=True → T2V (node I2V bỏ điều kiện ảnh).
    extra_images: ảnh phụ (cổng động image2/image3) → token __IMAGE2__/__IMAGE3__ (model động tự dùng nếu cần).
    Token thiếu ảnh → fallback ảnh chính để node vẫn hợp lệ; template không có token → bỏ qua (an toàn)."""
    pos = (prompt or p.get("ssPrompt") or
           "cinematic camera motion, smooth gentle movement, soft lighting, high detail, photorealistic").strip()
    sd = int(seed if seed is not None else p.get("ss_seed", abs(hash(prefix)) % (2 ** 31)))
    S = int(steps if steps is not None else SS_STEPS)
    _ex = list(extra_images or [])
    subs = {"__IMAGE__": image_name,
            "__IMAGE2__": (_ex[0] if len(_ex) > 0 else image_name),
            "__IMAGE3__": (_ex[1] if len(_ex) > 1 else image_name),
            "__PROMPT__": pos, "__NEG__": neg,
            "__MODEL__": SS_MODEL, "__GEMMA__": SS_GEMMA, "__VAE__": SS_VAE, "__CKPT__": SS_CKPT, "__PREFIX__": prefix,
            "__LORA__": lora or "", "__LORA_STRENGTH__": float(lora_strength), "__BYPASS__": bool(bypass),
            "__WIDTH__": int(W), "__HEIGHT__": int(H), "__FRAMES__": int(frames),
            "__SEED__": int(sd), "__STEPS__": int(S), "__FPS__": int(SS_FPS)}
    tpl = _ss_template(p)
    if tpl is None:
        raise RuntimeError(
            "SS chưa cấu hình workflow: set env SS_WORKFLOW_JSON (đường dẫn file .json / chuỗi JSON 'Save (API "
            "Format)' của graph LTX-2.3 I2V xuất từ ComfyUI trên box) hoặc params.ssWorkflow. Đặt token "
            "__IMAGE__/__PROMPT__/__NEG__/__LORA__/__LORA_STRENGTH__/__WIDTH__/__HEIGHT__/__FRAMES__/__SEED__/"
            "__STEPS__/__FPS__/__MODEL__/__GEMMA__/__VAE__/__PREFIX__ trong template.")
    wf = _ltx_apply(tpl, subs)        # tái dùng bộ thay token của LTX (đã có)
    if not lora:
        wf = _strip_lora_nodes(wf)    # không chọn LoRA → chạy base LTX-2.3 thuần
    return wf

def _ss_storyboard(raw, job_id=None, exclude_subject=False):
    """JSON prompt có 'action' = LIST cảnh (mỗi cảnh '0-5s: mô tả…') → storyboard [{dur, prompt}] để render TỪNG
    CẢNH theo prompt (KHÔNG chia cứng 5s). Mỗi cảnh = base (scene/cinematic/style[+characters nếu T2V]) + action riêng.
    None nếu không có action list ≥2 → run_ss rơi về chia-đều SS_SEG_SEC. ALD 14/06/2026."""
    import re, json as _json
    try:
        data = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    actions = data.get("action")
    if not (isinstance(actions, list) and len(actions) >= 2):
        return None
    base, _, _ = _build_prompt_from_json({k: v for k, v in data.items() if k != "action"}, job_id, exclude_subject=exclude_subject)
    base = (base or "").rstrip(". ")
    out = []
    for a in actions:
        txt = str(a).strip()
        m = re.match(r'^\s*(\d+)\s*[-–~]\s*(\d+)\s*s?\b\s*[:.\-]?\s*(.*)$', txt, re.I | re.S)
        if m:
            d = max(2, int(m.group(2)) - int(m.group(1))); desc = (m.group(3).strip() or txt)
        else:
            d = SS_SEG_SEC; desc = txt
        d = min(d, SS_SEG_SEC)   # cap an toàn VRAM (mỗi cảnh ≤ SS_SEG_SEC, đã verify ~5s vừa 32GB)
        out.append({"dur": float(d), "prompt": ((base + ". " + desc) if base else desc).strip()})
    return out or None

def run_ss(job):
    """Node SS (Video AI): I2V (LTX-2.3 + LoRA), T2V (Wan), V2V (Wan restyle từng frame giữ motion).
    V2V: nhận video qua cổng 'video' → extract frame đầu làm ref → build_wan_workflow. KHÔNG throw uncaught."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    model = str(params.get("model") or "ltx").lower().strip()
    # #region ALD 26/06/2026 - V2V mode: video nguồn → extract frame đầu làm ref → Wan Animate restyle giữ motion.
    if model == "wan-v2v":
        vid_key = inputs.get("video")
        if not vid_key:
            raise RuntimeError("SS V2V cần node Input (video) nối vào cổng 'video'.")
        tmp = tempfile.mkdtemp(prefix=f"ss-v2v-{job_id[:8]}-")
        api_progress(job_id, 0.05, "tải video nguồn")
        vid_ext = os.path.splitext(vid_key)[1] or ".mp4"
        vid_local = api_download(vid_key, os.path.join(tmp, f"src{vid_ext}"))
        # Extract frame đầu làm ref image
        ref_local = os.path.join(tmp, "ref_frame.png")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", vid_local, "-vframes", "1", "-q:v", "2", ref_local],
            check=True
        )
        api_progress(job_id, 0.10, "upload ref frame + video vào Wan Animate")
        ref_name = comfy_upload(ref_local)
        motion_name = comfy_upload(vid_local)
        # Reuse _normalize_motion_params để hợp nhất camelCase → snake_case, sau đó delegate sang build_wan_workflow
        mp = _normalize_motion_params(params)
        mp.setdefault("preset", "5s-720p"); mp.setdefault("aspect_ratio", "9:16")
        wan_prefix = f"ss-v2v-{job_id[:8]}"
        wf = build_wan_workflow(ref_name, motion_name, mp, prefix=wan_prefix)
        api_progress(job_id, 0.15, "queue ComfyUI (V2V Wan Animate)")
        pid = comfy_submit(wf)
        outputs = comfy_poll(pid, job_id, deadline_sec=1800, prog_lo=0.15, prog_hi=0.90,
                             output_prefix=wan_prefix)
        out_mp4 = comfy_fetch_output(outputs)
        if not out_mp4:
            raise RuntimeError("SS V2V: ComfyUI không trả kết quả video")
        api_upload_output(job_id, out_mp4)
        api_log(job_id, "SS V2V hoàn thành", "info")
        return
    # #endregion
    if model in T2V_MODELS:
        api_log(job_id, f"SS chọn {model} → chạy Wan T2V, bỏ qua ảnh input/LoRA của nhánh LTX", "info")
        t2v_job = dict(job)
        t2v_job["inputs"] = {}
        run_text2video(t2v_job)
        return
    if model and model != "ltx":
        api_log(job_id, f"SS model '{model}' không hỗ trợ → dùng LTX-2.3", "warn")
    # ALD 15/06/2026 - cổng vào ĐỘNG (model động 1–3 ảnh): ảnh CHÍNH (handle 'input'/legacy) + ảnh phụ image2/image3.
    # Template SS dùng token __IMAGE__ (+ __IMAGE2__/__IMAGE3__ nếu model cần) → user tự quyết số ảnh dùng.
    img_key = (inputs.get("image") or inputs.get("input") or inputs.get("ref")
               or inputs.get("product") or inputs.get("model"))
    extra_keys = [k for k in (inputs.get("image2"), inputs.get("image3")) if k]
    lora = (params.get("loraName") or params.get("lora") or "").strip()
    lora_str = float(params.get("loraStrength") or params.get("lora_strength") or 1.0)
    # ALD 14/06/2026 - prompt giống Create Image: toggle Text/JSON. JSON mode → _build_prompt_from_json (ghép prompt
    # tiếng Anh + tách negative + aspect_ratio); Text mode → dùng NGUYÊN ô prompt (user viết tiếng Anh, KHÔNG dịch).
    prompt = (params.get("prompt") or params.get("positive_prompt") or "").strip()
    _json_neg = None
    storyboard = None    # [{dur,prompt}] từ 'action' array → render TỪNG CẢNH theo prompt (không chia cứng 5s)
    if str(params.get("promptMode") or params.get("prompt_mode") or "text").strip().lower() == "json":
        _raw = str(params.get("promptJson") or params.get("prompt_json") or "").strip()
        _jp, _jn, _jar = _build_prompt_from_json(_raw, job_id, exclude_subject=bool(img_key))
        if _jp:
            prompt = _jp; _json_neg = _jn
            if _jar: params["aspectRatio"] = _jar    # meta.aspect_ratio → override tỉ lệ khung (xem _ss_dims)
            api_log(job_id, f"SS promptMode=json → ghép prompt: {prompt[:140]}", "info")
        else:
            prompt = _raw    # JSON lỗi cú pháp/rỗng → dùng nguyên văn JSON (KHÔNG rớt về ô Text)
            api_log(job_id, "SS promptMode=json nhưng JSON lỗi cú pháp → dùng nguyên văn JSON làm prompt.", "warn")
        storyboard = _ss_storyboard(_raw, job_id, exclude_subject=bool(img_key))   # 'action' array → cảnh theo prompt
    user_neg = str(params.get("negativePrompt") or params.get("negative_prompt") or "").strip().strip(",").strip()
    if _json_neg:
        user_neg = ((user_neg + ", " + _json_neg) if user_neg else _json_neg).strip().strip(",").strip()
    neg = user_neg or "static, no motion, frozen frame, blurry, lowres, deformed, watermark, text"
    dur = float(params.get("duration") or params.get("durationSec") or 5)
    W, H = _ss_dims(params)
    base_seed = int(params.get("ss_seed") or (abs(hash(job_id)) % (2 ** 31)))
    tmp = tempfile.mkdtemp(prefix=f"ss-{job_id[:8]}-")
    mode = "I2V" if img_key else "T2V"
    # ALD 14/06/2026 - nối đoạn: 'anchor' = mỗi cảnh DÙNG LẠI ẢNH INPUT (giữ chủ thể, "key theo ảnh mẫu") |
    #   'chain' = nối frame cuối (mượt nhưng trôi dần). MẶC ĐỊNH: I2V → anchor (giữ ảnh mẫu), T2V → chain.
    link_mode = str(params.get("linkMode") or params.get("link_mode") or "").strip().lower()
    if link_mode not in ("chain", "anchor"):
        link_mode = "anchor" if img_key else "chain"
    # seg_list: STORYBOARD (action array → mỗi cảnh dur+prompt riêng) HOẶC chia đều SS_SEG_SEC (cùng prompt)
    if storyboard:
        seg_list = storyboard
    else:
        _n = max(1, int(-(-dur // max(1, SS_SEG_SEC)))); seg_list = []; _rem = dur
        for _ in range(_n):
            _sd = min(SS_SEG_SEC, _rem) if _rem > 0 else SS_SEG_SEC; _rem -= _sd
            seg_list.append({"dur": _sd, "prompt": prompt})
    n_segs = len(seg_list)
    api_log(job_id, f"SS {mode} ({link_mode}{'·storyboard' if storyboard else ''}): {n_segs} cảnh · {W}x{H} · "
            f"LoRA={lora or '(none)'}@{lora_str}", "info")
    input_image = None   # ảnh INPUT gốc (None nếu T2V)
    if img_key:
        api_progress(job_id, 0.06, "tải ảnh đầu vào")
        loc = api_download(img_key, os.path.join(tmp, "in" + (os.path.splitext(img_key)[1] or ".png")))
        input_image = comfy_upload(loc)
    # ALD 15/06/2026 - ảnh phụ (cổng động image2/image3): upload 1 lần, dùng chung mọi cảnh qua token __IMAGE2__/__IMAGE3__.
    extra_images = []
    for j, ek in enumerate(extra_keys):
        eloc = api_download(ek, os.path.join(tmp, f"in{j + 2}" + (os.path.splitext(ek)[1] or ".png")))
        extra_images.append(comfy_upload(eloc))
    if extra_images:
        api_log(job_id, f"SS nhận thêm {len(extra_images)} ảnh phụ (cổng động) → token __IMAGE2__/__IMAGE3__ trong template", "info")
    cur_image = input_image
    clips = []
    try:
        for i, seg in enumerate(seg_list):
            frames = ltx_frames(seg["dur"])
            seg_prompt = seg["prompt"] or prompt
            # cảnh 1 = ảnh input; cảnh sau = ẢNH INPUT GỐC (anchor, giữ chủ thể) HOẶC frame cuối đoạn trước (chain)
            seg_img = cur_image if i == 0 else (input_image if (link_mode == "anchor" and input_image) else cur_image)
            if seg_img:
                image = seg_img; bypass = False                  # I2V: ảnh đi CHUNG với prompt (ảnh=chủ thể, prompt=hành động)
            else:   # T2V cảnh không ảnh: blank + bypass điều kiện ảnh
                image = comfy_upload(_ss_blank_image(os.path.join(tmp, "blank.png"))); bypass = True
            prefix = f"ss-{job_id[:8]}-{i}"
            api_progress(job_id, 0.1 + 0.75 * i / n_segs,
                         f"cảnh {i + 1}/{n_segs} ({'T2V' if bypass else 'I2V'} · {frames}f ~{frames / SS_FPS:.1f}s): {seg_prompt[:60]}")
            wf = build_ss_workflow(image, params, frames, W, H, prompt=seg_prompt, neg=neg, prefix=prefix,
                                   lora=lora, lora_strength=lora_str, bypass=bypass, seed=base_seed + i,
                                   extra_images=extra_images)
            pid = comfy_submit(wf)
            outs = comfy_poll(pid, job_id, deadline_sec=int(params.get("ss_timeout", SS_TIMEOUT)))
            clip = comfy_fetch_output(outs)
            if not clip:
                raise RuntimeError(f"cảnh {i + 1}/{n_segs} không ra video (ComfyUI timeout/lỗi graph)")
            clips.append(clip)
            # chain: snapshot frame CUỐI → ảnh cảnh kế (BỎ QUA ở anchor — anchor luôn dùng ảnh input gốc)
            if i < n_segs - 1 and not (link_mode == "anchor" and input_image):
                cur_image = comfy_upload(_ss_last_frame(clip, os.path.join(tmp, f"lf{i}.png")))
    except Exception as e:
        api_log(job_id, f"SS lỗi khi render: {e}", "error")
        raise RuntimeError(f"SS (LTX-2.3 {mode}) lỗi: {e}")
    api_progress(job_id, 0.9, "ghép đoạn" if len(clips) > 1 else "tải kết quả")
    out = clips[0] if len(clips) == 1 else os.path.join(tmp, "ss_final.mp4")
    if len(clips) > 1:
        _concat_av(clips, out, fps=int(SS_FPS), xfade=0)   # cut thẳng giữ liên tục (chain đã nối frame)
    out = _finalize_mp4(out)   # validate moov + remux faststart cho phát web mượt
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out)
# #endregion

# ───────────────────────── Dịch phụ đề video (node subtitle) ─────────────────────────
# ALD 15/06/2026 - input 1 VIDEO → ASR (OmniVoice /asr) → dịch (Ollama) → CHÁY phụ đề (ffmpeg), GIỮ tiếng gốc.
# Realtime SSE: emit từng câu khi dịch xong (current_step đổi mỗi câu → mediaViaJob emit → run stream). KHÔNG dub.
_LANG_NAME = {"vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
              "fr": "French", "es": "Spanish", "th": "Thai", "de": "German", "ru": "Russian"}
# ALD 15/06/2026 - lồng tiếng ĐA NGỮ. vi → giọng VN (OmniVoice/viXTTS/Piper). EN → Piper EN OFFLINE (đáng tin;
# edge-tts hay bị MS chặn IP server → rớt về Piper VN đọc tiếng Anh = bậy). Ngôn ngữ khác → edge (best-effort).
_AUTO_DUB_VOICE = {"en": "en_US-amy-medium", "zh": "zh-CN-XiaoxiaoNeural", "ja": "ja-JP-NanamiNeural",
                   "ko": "ko-KR-SunHiNeural", "fr": "fr-FR-DeniseNeural", "es": "es-ES-ElviraNeural",
                   "th": "th-TH-PremwadeeNeural", "de": "de-DE-KatjaNeural", "ru": "ru-RU-SvetlanaNeural"}

def _omnivoice_asr(audio_path, model=None, language=None):
    """POST audio → OmniVoice /asr → {language, duration, segments:[{start,end,text}]}."""
    if not OMNIVOICE_URL:
        raise RuntimeError("OMNIVOICE_URL chưa cấu hình (cần service OmniVoice có /asr)")
    with open(audio_path, "rb") as f:
        data = {}
        if model: data["model"] = model
        if language: data["language"] = language
        r = requests.post(f"{OMNIVOICE_URL}/asr", files={"file": (os.path.basename(audio_path), f, "application/octet-stream")},
                          data=data, timeout=1800)
    if r.status_code != 200:
        raise RuntimeError(f"OmniVoice /asr {r.status_code}: {r.text[:200]}")
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"ASR lỗi: {j['error']}")
    return j

# ALD 15/06/2026 - ASR LOCAL trong worker (fallback khi không có OmniVoice /asr): faster-whisper CPU (CTranslate2).
# Model tự tải lần đầu vào HF cache (volume persist). large-v3 trên CPU rất chậm → hạ về medium.
_LOCAL_WHISPER = {}
def _local_whisper_asr(audio_path, model_name, job_id=None, language=None):
    from faster_whisper import WhisperModel
    name = model_name or os.environ.get("WHISPER_MODEL", "small")
    if name == "large-v3":
        name = "medium"
        if job_id:
            api_log(job_id, "ASR local (CPU): large-v3 quá nặng → dùng medium", "warn")
    if _LOCAL_WHISPER.get("name") != name:
        if job_id:
            api_log(job_id, f"Nạp faster-whisper '{name}' (CPU, lần đầu tải model)…", "info")
        _LOCAL_WHISPER["m"] = WhisperModel(name, device="cpu", compute_type="int8")
        _LOCAL_WHISPER["name"] = name
    # ALD 22/06/2026 - language=None → tự nhận diện (tiếng Trung/… ok); truyền 'zh'/'en'/… nếu muốn ép nguồn.
    # VAD-RETRY: vad_filter=True hay LỌC SẠCH lời thoại (nhạc nền/giọng nhỏ) → báo "không nhận diện" OAN. Nếu rỗng
    # → thử lại KHÔNG VAD trước khi chịu thua (đây là nguyên nhân chính của lỗi "không nhận diện" với video TQ).
    def _run(vad):
        segs, info = _LOCAL_WHISPER["m"].transcribe(audio_path, vad_filter=vad, beam_size=5, language=language)
        return [{"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()}
                for s in segs if (s.text or "").strip()], info
    out, info = _run(True)
    if not out:
        if job_id:
            api_log(job_id, "ASR: VAD lọc hết câu → thử lại KHÔNG VAD", "warn")
        out, info = _run(False)
    return {"language": getattr(info, "language", None),
            "duration": float(getattr(info, "duration", 0) or 0), "segments": out}

def _asr_transcribe(audio_path, model_name, job_id=None, language=None):
    """Ưu tiên OmniVoice /asr (GPU, nhanh) nếu có OMNIVOICE_URL; lỗi/RỖNG/không có → faster-whisper LOCAL (CPU)."""
    if OMNIVOICE_URL:
        try:
            r = _omnivoice_asr(audio_path, model=model_name, language=language)
            if (r.get("segments") or []):   # ALD 22/06 - chỉ dùng nếu CÓ câu; rỗng → fallback whisper (VAD-retry)
                return r
            if job_id:
                api_log(job_id, "OmniVoice /asr trả RỖNG → faster-whisper local (VAD-retry)", "warn")
        except Exception as e:
            if job_id:
                api_log(job_id, f"OmniVoice /asr không dùng được ({e}) → faster-whisper local", "warn")
    return _local_whisper_asr(audio_path, model_name, job_id, language=language)

def _translate_text(text, target_lang, job_id=None):
    """Dịch 1 câu phụ đề sang target_lang (mã 'vi'/'en'/…). Fail-safe: lỗi/tắt → trả nguyên gốc."""
    t = (text or "").strip()
    if not t or not TRANSLATE_ON:
        return t
    tgt = _LANG_NAME.get(str(target_lang or "vi").lower(), "Vietnamese")
    try:
        sys_msg = (f"Translate the user's subtitle line into natural {tgt}. Keep the meaning faithful and concise "
                   f"for an on-screen subtitle. Output ONLY the translation — no quotes, no notes.")
        r = requests.post(f"{TRANSLATE_URL}/api/chat", timeout=60, json={
            "model": TRANSLATE_MODEL, "stream": False, "keep_alive": 0, "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": t}]})
        r.raise_for_status()
        out = (r.json().get("message", {}).get("content") or "").strip().strip('"').strip()
        return out or t
    except Exception as e:
        if job_id:
            api_log(job_id, f"Dịch câu lỗi (giữ gốc): {e}", "warn")
        return t

def _srt_ts(sec):
    sec = max(0.0, float(sec)); h = int(sec // 3600); m = int((sec % 3600) // 60)
    s = int(sec % 60); ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000: s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _segs_to_srt(segments):
    out = []
    for i, s in enumerate([x for x in segments if (x.get("text") or "").strip()], 1):
        out.append(f"{i}\n{_srt_ts(s['start'])} --> {_srt_ts(s['end'])}\n{s['text'].strip()}\n")
    return "\n".join(out)

def _ffprobe_dur(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30)
        return float((r.stdout or "").strip())
    except Exception:
        return None

def _sil_wav(ms, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{max(1, int(ms)) / 1000.0:.3f}",
                    "-i", "anullsrc=r=24000:cl=mono", "-c:a", "pcm_s16le", path], check=True, timeout=120)
    return path

def _build_dub_track(clips, out_path, total_dur=None):
    """Dựng track lồng tiếng TUẦN TỰ — KHÔNG chồng tiếng (fix 'nhiều người nói cùng lúc').
    Mỗi câu đặt tại mốc start; nếu clip DÀI hơn khoảng tới câu kế → tăng tốc atempo (CAP 1.5x, tránh nói nhanh
    chói) cho vừa; phần dư nối tiếp + chèn lặng để bám timestamp. total_dur (giây) cắt đuôi."""
    if not clips:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", str(total_dur or 1),
                        "-i", "anullsrc=r=24000:cl=mono", "-c:a", "aac", out_path], check=True, timeout=120)
        return out_path
    clips = sorted(clips, key=lambda c: int(c[0]))
    work = os.path.dirname(out_path)
    pieces = []
    cur_ms = 0
    for i, (start_ms, mp3) in enumerate(clips):
        start_ms = max(0, int(start_ms))
        if start_ms > cur_ms + 20:                       # chèn lặng để câu bám đúng mốc thời gian (chỉ tiến)
            pieces.append(_sil_wav(start_ms - cur_ms, os.path.join(work, f"sil{i}.wav")))
            cur_ms = start_ms
        d_ms = int((_ffprobe_dur(mp3) or 0) * 1000)
        if d_ms <= 0:
            continue
        nxt = int(clips[i + 1][0]) if i + 1 < len(clips) else (int(total_dur * 1000) if total_dur else None)
        af = "aresample=24000"
        if nxt is not None:                              # fit vào khe [start → câu kế] để KHỎI tràn (chồng tiếng)
            slot = nxt - cur_ms
            if slot > 200 and d_ms > slot:
                factor = min(d_ms / slot, 1.5)           # cap 1.5x: vừa đỡ chồng, vừa không quá nhanh
                af = f"atempo={factor:.3f},aresample=24000"
                d_ms = int(d_ms / factor)
        seg = os.path.join(work, f"seg{i}.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp3, "-af", af, "-ac", "1", "-ar", "24000", seg],
                       check=True, timeout=300)
        pieces.append(seg); cur_ms += d_ms
    lst = os.path.join(work, "dublist.txt")
    with open(lst, "w") as f:
        for p in pieces:
            f.write("file '%s'\n" % p)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-ar", "24000"]
    if total_dur:
        cmd += ["-t", str(total_dur)]
    cmd += ["-c:a", "aac", "-b:a", "160k", out_path]
    subprocess.run(cmd, check=True, timeout=1800)
    return out_path

def _extract_voice_ref(vid, segments, job_id):
    """Trích ~12s giọng SẠCH từ video (câu ASR dài nhất) → ref clone, GHI vào TTS_REF_DIR (host đọc được).
    Trả PATH (đồng nhất host↔container nhờ mount cùng đường dẫn) hoặc None."""
    if not segments:
        return None
    best = max(segments, key=lambda s: float(s.get("end", 0)) - float(s.get("start", 0)))
    start = max(0.0, float(best["start"]))
    dur = min(14.0, max(4.0, float(best["end"]) - start + 1.5))
    try:
        os.makedirs(TTS_REF_DIR, exist_ok=True)
    except Exception:
        pass
    ref = os.path.join(TTS_REF_DIR, f"vref-{job_id[:12]}.wav")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
                        "-i", vid, "-vn", "-ac", "1", "-ar", "22050", ref], check=True, timeout=120)
        return ref if (os.path.exists(ref) and os.path.getsize(ref) > 4000) else None
    except Exception as e:
        api_log(job_id, f"trích giọng ref clone lỗi: {e}", "warn")
        return None

def run_subtitle(job):
    """input 1 video → ASR + dịch + (cháy phụ đề / lồng tiếng / cả hai).
    params: mode(subtitle|dub|dub-sub), targetLang, bilingual, asrModel, fontSize, position, voice.
    Dub CHỈ → Tiếng Việt (giọng VN: OmniVoice/viXTTS/…). Tiến trình cập nhật THƯA (không emit per-câu)."""
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    vid_key = inputs.get("input") or inputs.get("video") or inputs.get("image") or inputs.get("ref")
    if not vid_key:
        raise RuntimeError("subtitle: cần 1 video đầu vào (nối node Input Video)")
    mode = str(params.get("mode") or "subtitle").lower()
    do_dub = mode in ("dub", "dub-sub")
    do_sub = mode in ("subtitle", "dub-sub")
    target = str(params.get("targetLang") or "vi").lower()  # dub theo NGÔN NGỮ ĐÍCH (vi→giọng VN; khác→edge-tts)
    bilingual = bool(params.get("bilingual")) and do_sub
    asr_model = params.get("asrModel") or None
    # ALD 22/06/2026 - hint ngôn ngữ NGUỒN (vd 'zh' cho video tiếng Trung) → ASR ép đúng tiếng, khỏi auto-detect
    # trượt. "" / "auto" = tự nhận. Đích dịch vẫn là targetLang (mặc định 'vi') → Trung→Việt chạy bình thường.
    src_hint = str(params.get("sourceLang") or params.get("srcLang") or "auto").lower().strip()
    src_hint = None if src_hint in ("", "auto") else src_hint
    font_size = int(params.get("fontSize") or 18)
    position = str(params.get("position") or "bottom")
    voice = (params.get("voice") or "").strip()  # "" = Tự động: chọn giọng theo target
    gem_key = _gemini_key(params) or None
    tmp = tempfile.mkdtemp(prefix=f"sub-{job_id[:8]}-")
    try:
        api_progress(job_id, 0.04, "tải video")
        vid = api_download(vid_key, os.path.join(tmp, "in" + (os.path.splitext(vid_key)[1] or ".mp4")))
        vdur = _ffprobe_dur(vid)
        api_progress(job_id, 0.10, "tách âm thanh")
        wav = os.path.join(tmp, "audio.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-vn", "-ac", "1", "-ar", "16000", wav],
                       check=True, timeout=900)
        api_progress(job_id, 0.16, "nhận diện lời thoại (ASR)")
        asr = _asr_transcribe(wav, asr_model, job_id, language=src_hint)
        segs = asr.get("segments") or []
        src_lang = (asr.get("language") or "").lower()
        if not segs:
            raise RuntimeError("Không nhận diện được lời thoại trong video")
        api_log(job_id, f"ASR {len(segs)} câu · nguồn={src_lang or '?'} → dịch {target} · mode={mode}", "info")
        same = bool(src_lang and src_lang.startswith(target))
        out_segs = []
        n = len(segs)
        trans_top = 0.45 if do_dub else 0.78  # chừa quota cho TTS nếu dub
        # ALD 15/06/2026 - BỎ emit realtime PER-CÂU. Trước đây mỗi câu gọi api_progress = 2 HTTP→api (PATCH +
        # POST log) = 2 DB write → video dài (hàng trăm câu) dồn ghi vào api/pool PG → query /media (list file)
        # phải chờ → /media TREO. Giờ chỉ cập nhật tiến trình THƯA (~8 mốc); kết quả trả khi job XONG.
        step_every = max(1, n // 8)
        for idx, s in enumerate(segs):
            orig = (s.get("text") or "").strip()
            tr = orig if same else _translate_text(orig, target, job_id)
            out_segs.append({"start": s["start"], "end": s["end"], "orig": orig, "tr": tr})
            if idx == n - 1 or idx % step_every == 0:
                api_progress(job_id, 0.16 + (trans_top - 0.16) * ((idx + 1) / n), f"dịch {idx + 1}/{n} câu")

        # ── phụ đề (.srt) nếu mode có phụ đề ──
        if do_sub:
            srt_segs = [{"start": s["start"], "end": s["end"],
                         "text": (s["orig"] + "\n" + s["tr"]) if (bilingual and not same) else s["tr"]} for s in out_segs]
            with open(os.path.join(tmp, "sub.srt"), "w", encoding="utf-8") as f:
                f.write(_segs_to_srt(srt_segs))

        # ── lồng tiếng (TTS từng câu → track theo timestamp) nếu mode có dub ──
        dub_audio = None
        ref_wav = None
        if do_dub:
            # voice == "clone": CLONE giọng người trong video (XTTS-v2 cross-lingual) → đọc target bằng chất giọng đó.
            clone = (voice == "clone")
            if clone:
                ref_wav = _extract_voice_ref(vid, out_segs, job_id) if VIXTTS_URL else None
                if ref_wav:
                    api_log(job_id, f"CLONE giọng gốc trong video → đọc {target} (XTTS cross-lingual)", "info")
                else:
                    clone = False
                    api_log(job_id, "Không clone được (viXTTS tắt / không trích được giọng) → giọng tự động", "warn")
            if not clone and (not voice or voice in ("auto", "clone")):
                voice = DEFAULT_VOICE if target == "vi" else _AUTO_DUB_VOICE.get(target, "en_US-amy-medium")
            # Tốc độ giọng clone: lấy từ props (voiceSpeed), fallback env CLONE_SPEED. Clamp 0.5–2.0.
            try:
                clone_speed = float(params.get("voiceSpeed") or CLONE_SPEED)
            except (TypeError, ValueError):
                clone_speed = CLONE_SPEED
            clone_speed = max(0.5, min(clone_speed, 2.0))
            api_log(job_id, f"lồng tiếng → {target} · {'clone giọng gốc x' + str(clone_speed) if clone else voice}", "info")
            api_progress(job_id, trans_top, "lồng tiếng (TTS)")
            fb_voice = DEFAULT_VOICE if target == "vi" else _AUTO_DUB_VOICE.get(target, "en_US-amy-medium")
            clips = []
            for i, s in enumerate(out_segs):
                txt = (s["tr"] or "").strip()
                if not txt:
                    continue
                c = os.path.join(tmp, f"v{i}.mp3")
                try:
                    if clone:
                        _vixtts_tts(txt, c, ref=ref_wav, language=target)  # cross-lingual clone
                        # XTTS fine-tune VN đọc ngôn ngữ khác (vd English) bị CHẬM → chỉnh tốc độ theo props
                        # voiceSpeed (giữ pitch). _build_dub_track sau đó vẫn fit thêm theo timestamp nếu cần.
                        if abs(clone_speed - 1.0) > 0.01:
                            sp = os.path.join(tmp, f"v{i}.sp.mp3")
                            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", c,
                                            "-af", f"atempo={clone_speed:.3f}", sp], check=True, timeout=120)
                            c = sp
                    else:
                        _tts(txt, c, voice, gem_key)
                    clips.append((int(float(s["start"]) * 1000), c))
                except Exception as e:
                    api_log(job_id, f"TTS câu {i + 1} lỗi → thử giọng dự phòng: {e}", "warn")
                    try:
                        _tts(txt, c, fb_voice, gem_key); clips.append((int(float(s["start"]) * 1000), c))
                    except Exception as e2:
                        api_log(job_id, f"TTS câu {i + 1} bỏ qua: {e2}", "warn")
                # ALD 15/06/2026 - cập nhật THƯA (~8 mốc), không emit per-câu (xem ghi chú vòng dịch ở trên).
                if i == n - 1 or i % step_every == 0:
                    api_progress(job_id, trans_top + (0.82 - trans_top) * ((i + 1) / n), f"đọc {i + 1}/{n} câu")
            dub_audio = _build_dub_track(clips, os.path.join(tmp, "dub.m4a"), total_dur=vdur)
            if ref_wav:
                try:
                    os.remove(ref_wav)
                except Exception:
                    pass

        # ── ghép xuất video ──
        api_progress(job_id, 0.85, "ghép video")
        out = os.path.join(tmp, "output.mp4")
        align = {"bottom": 2, "top": 8, "center": 5}.get(position, 2)
        style = (f"FontSize={font_size},Alignment={align},Outline=2,Shadow=0,MarginV=28,"
                 "PrimaryColour=&H00FFFFFF&,BorderStyle=1")
        if do_dub and do_sub:        # thay audio = giọng lồng + cháy phụ đề (re-encode video)
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", vid, "-i", dub_audio,
                   "-vf", f"subtitles=sub.srt:force_style='{style}'", "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac"]
        elif do_dub:                 # chỉ thay audio = giọng lồng (copy video, nhanh)
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", vid, "-i", dub_audio,
                   "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac"]
        else:                        # chỉ phụ đề, giữ tiếng gốc
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", vid,
                   "-vf", f"subtitles=sub.srt:force_style='{style}'",
                   "-c:a", "copy", "-c:v", "libx264", "-preset", "medium", "-crf", "20"]
        if vdur:
            cmd += ["-t", str(vdur)]  # giữ đúng độ dài video (cắt đuôi audio dub thừa)
        cmd.append(out)
        subprocess.run(cmd, check=True, timeout=2400, cwd=tmp)
        out = _finalize_mp4(out)
        api_progress(job_id, 0.95, "upload output")
        api_upload_output(job_id, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def run_talk(job):
    """MultiTalk/InfiniteTalk: ảnh nhân vật + câu thoại (TTS giọng riêng) → video NÓI + nhép miệng.
    inputs.image (ảnh nhân vật); params.line (lời thoại) + params.voice ('vixtts:<ref>'|'gemini:Puck'|…)."""
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    # node 1-input: engine truyền qua prev → mediaViaJob đặt inputs.input. Cũng nhận image/ref/char/model.
    img_key = inputs.get("image") or inputs.get("input") or inputs.get("ref") or inputs.get("char") or inputs.get("model") or inputs.get("product")
    line = (params.get("line") or params.get("text") or params.get("script") or "").strip()
    if not img_key: raise RuntimeError("talk cần inputs.image (ảnh nhân vật)")
    if not line: raise RuntimeError("talk cần params.line (câu thoại)")
    voice = params.get("voice") or None
    gem_key = _gemini_key(params)
    tmp = tempfile.mkdtemp(prefix=f"talk-{job_id[:8]}-")
    api_progress(job_id, 0.08, "tải ảnh nhân vật")
    loc = api_download(img_key, os.path.join(tmp, "char" + (os.path.splitext(img_key)[1] or ".png")))
    img_name = comfy_upload(loc)
    api_progress(job_id, 0.2, "TTS (giọng nhân vật)")
    wav = os.path.join(tmp, "voice.mp3")
    # Trụ C: đạo diễn gán emotion/pace cho câu thoại → giọng truyền cảm, hợp ngữ cảnh.
    _tts(line, wav, voice, gem_key, emotion=params.get("emotion"), pace=params.get("pace"),
         gender=params.get("voiceGender") or params.get("gender"))
    fps = int(params.get("fps") or TALK_FPS)
    dur = _audio_dur(wav) or max(2.0, len(line) / 12.0)
    F = max(25, min(int(round(dur * fps)) + 8, int(params.get("max_frames", 257))))  # cap ~10s/clip cho nhẹ
    aud_name = comfy_upload(wav)
    api_log(job_id, f"talk: {dur:.1f}s audio → {F}f @ {fps}fps · voice={voice or 'default'}", "info")
    api_progress(job_id, 0.35, f"MultiTalk lip-sync ({F}f ~{F/fps:.1f}s)")
    prefix = f"talk-{job_id[:8]}"
    pid = comfy_submit(build_wan_multitalk_workflow(img_name, aud_name, F, params,
                       prompt=params.get("prompt", ""), prefix=prefix, fps=fps))
    out = _await_comfy_video(pid, job_id, prefix, timeout=int(params.get("talk_timeout", 1800)))
    api_progress(job_id, 0.92, "tải kết quả")
    out = _finalize_mp4(out)  # validate moov (chặn file cắt) + remux faststart cho phát web mượt
    api_upload_output(job_id, out)

# #region ALD 06/06/2026 - face-motion: "Người mẫu đọc kịch bản" = talk (lip-sync) có HÀNH ĐỘNG theo phong thái.
# Tái dùng nguyên run_talk (MultiTalk/InfiniteTalk). Khác talk ở chỗ motion-prompt được sinh từ PHONG THÁI
# (+ AI director ở API đọc kịch bản → params.prompt). Đây là fallback TĨNH: nếu params.prompt rỗng (API director
# lỗi, hoặc gọi job thẳng) → fill mô tả hành động/biểu cảm theo phong thái để người mẫu KHÔNG chỉ nhép miệng.
# Mô tả bằng tiếng Anh (Wan/umt5 ăn prompt tiếng Anh, khớp default prompt của build_wan_multitalk_workflow).
DEMEANOR_PROMPTS = {
    "hon-nhien":  "cheerful and playful, light bouncy energy, bright genuine smile, gentle head tilts, lively hand gestures, talking naturally to the camera, lip sync",
    "trong-sang": "soft and gentle, warm sincere smile, calm graceful posture, soft eye contact, subtle natural head movement, talking naturally to the camera, lip sync",
    "manh-me":    "confident and assertive, firm decisive gestures, strong direct eye contact, energetic upper-body movement, talking naturally to the camera, lip sync",
    "ca-tinh":    "expressive and stylish, bold dynamic gestures, attitude and charisma, varied poses and head movement, talking naturally to the camera, lip sync",
}

def _clean_voice_wav(src, dst):
    """ALD 07/06/2026 - Dọn NHẸ giọng TTS NGAY TRÊN WAV, TRƯỚC lip-sync: highpass (bỏ ù trầm) → cắt im
    ĐẦU+CUỐI → loudnorm (chuẩn âm lượng). Trả wav 24k mono dùng CHUNG cho cả wav2vec (lip-sync) lẫn mux (VHS).
    Cắt im ở đây (không phải sau-render) để F = round(dur*fps) KHỚP đúng độ dài → miệng không mấp máy lúc hết tiếng.
    CHỦ Ý dọn NHẸ: chống "ong ong/vọng" phải làm ở FILE MẪU SẠCH (viXTTS bê y nguyên nền của ref), KHÔNG ở output —
    afftdn/agate mạnh trên output còn TẠO musical-noise (nghe như ong ong) nên đã BỎ. Lỗi → trả src (không chặn job)."""
    af = ("highpass=f=60,"   # 60Hz: bỏ rumble/ù trầm NHƯNG giữ độ trầm (chest ~100-250Hz) của giọng gốc
          "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.06:detection=peak,"
          "areverse,"
          "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.10:detection=peak,areverse,"
          "loudnorm=I=-16:TP=-1.5:LRA=11")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                        "-af", af, "-ar", "24000", "-ac", "1", str(dst)], check=True, timeout=300)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            return dst
    except Exception as e:
        log("clean voice wav fail:", e)
    return src

def _clean_talk_audio(src, dst):
    """Dọn audio TTS 1 đoạn: highpass bỏ ù/buzz tần số thấp + cắt im thừa CUỐI (giữ ~0.12s) + fade mép → hết
    'ong ong'/click ở cuối câu khi ghép. Video copy nguyên (concat -shortest tự căn video theo audio đã cắt)."""
    af = ("highpass=f=90,areverse,"
          "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.12:detection=peak,"
          "afade=t=in:d=0.05,areverse,afade=t=in:st=0:d=0.02")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                    "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k", str(dst)],
                   check=True, timeout=300)
    return dst

# ALD 06/06/2026 - Độ phân giải OUTPUT (720p/1080p). MultiTalk render ~480×832 cho nhẹ VRAM (box mong manh —
# render native 1080p sẽ OOM/treo như vụ 836f) → upscale bằng ffmpeg lanczos + unsharp. 480p = giữ render gốc.
_TALK_RES = {"480p": None, "720p": (720, 1280), "1080p": (1080, 1920)}
def _upscale_talk(src, quality, tmp):
    dims = _TALK_RES.get(str(quality or "720p"), (720, 1280))
    if not dims:
        return src
    W, H = dims
    dst = os.path.join(tmp, f"up_{W}x{H}.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                    "-vf", f"scale={W}:{H}:flags=lanczos,unsharp=5:5:0.6:5:5:0.0",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(dst)], check=True, timeout=600)
    return dst

def run_face_motion(job):
    """Người mẫu đọc kịch bản: ảnh người mẫu + kịch bản → video NÓI + nhép miệng + HÀNH ĐỘNG ĐA DẠNG theo phong thái.

    params.segments [{line, action}] (AI chia đoạn ở API) → render TỪNG đoạn với 1 HÀNH ĐỘNG KHÁC nhau → ghép
    (cut thẳng giữ trọn lời) → hết cảnh lặp đi lặp lại 1 động tác. InfiniteTalk 1-clip-1-prompt vốn hay lặp, nên
    chia đoạn là cách cho động tác đa dạng bám nội dung từng câu (giống story-film: render từng cảnh rồi concat).
    Không có segments (AI lỗi / gọi job thẳng) → fallback 1 clip như talk (đọc full, 1 hành động theo phong thái)."""
    job_id = job["id"]; inputs = job.get("inputs", {}); p = job.get("params", {}) or {}
    img_key = inputs.get("image") or inputs.get("input") or inputs.get("ref") or inputs.get("char") or inputs.get("model") or inputs.get("product")
    if not img_key: raise RuntimeError("face-motion cần inputs.image (ảnh người mẫu)")
    dem = str(p.get("demeanor") or "hon-nhien")
    segs = p.get("segments")
    segs = [s for s in segs if isinstance(s, dict) and (s.get("line") or "").strip()] if isinstance(segs, list) else []

    # Fallback: không chia được đoạn → 1 clip (đọc HẾT kịch bản, 1 hành động theo phong thái). Cap 900f (~36s) để
    # khít poll 30' của FE engine (mediaViaJob 900×2s) — talk mặc định cap 257f (~10s) làm cắt kịch bản.
    if not segs:
        if not (p.get("prompt") or "").strip():
            p["prompt"] = DEMEANOR_PROMPTS.get(dem, DEMEANOR_PROMPTS["hon-nhien"])
        p.setdefault("max_frames", 900)
        p.setdefault("voice", FACE_MOTION_VOICE)   # giọng nam clone đã làm sạch (mặc định) cho cả nhánh 1-clip
        job["params"] = p
        return run_talk(job)

    # Đa đoạn: mỗi đoạn 1 HÀNH ĐỘNG riêng → render clip ngắn → ghép. Model ComfyUI nạp 1 lần rồi giữ ấm qua các đoạn.
    voice = p.get("voice") or FACE_MOTION_VOICE
    gem_key = _gemini_key(p)
    fps = int(p.get("fps") or TALK_FPS)
    tmp = tempfile.mkdtemp(prefix=f"facemo-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải ảnh người mẫu")
    loc = api_download(img_key, os.path.join(tmp, "char" + (os.path.splitext(img_key)[1] or ".png")))
    img_name = comfy_upload(loc)
    n = len(segs); clips = []
    api_log(job_id, f"face-motion: {n} đoạn — mỗi đoạn 1 hành động riêng (phong thái {dem})", "info")
    for i, seg in enumerate(segs):
        if api_job_cancelled(job_id):
            comfy_interrupt(); raise RuntimeError("face-motion bị hủy")
        line = (seg.get("line") or "").strip()
        action = (seg.get("action") or "").strip() or DEMEANOR_PROMPTS.get(dem, DEMEANOR_PROMPTS["hon-nhien"])
        base = 0.08 + 0.82 * i / n
        api_progress(job_id, base, f"đoạn {i+1}/{n}: TTS")
        wav = os.path.join(tmp, f"seg{i:02d}.mp3")
        _tts(line, wav, voice, gem_key)
        # ALD 07/06/2026 - Dọn MẠNH + cắt im NGAY trên wav (vọng/ong ong/tạp âm) → dùng chung cho wav2vec lẫn mux.
        wav = _clean_voice_wav(wav, os.path.join(tmp, f"seg{i:02d}.clean.wav"))
        dur = _audio_dur(wav) or max(1.5, len(line) / 12.0)
        # +2 frame đệm mỏng (audio ĐÃ cắt im) → video ≈ độ dài tiếng → miệng KHÔNG mấp máy lúc đã hết tiếng.
        F = max(25, min(int(round(dur * fps)) + 2, int(p.get("seg_max_frames", 400))))  # mỗi đoạn ≤ ~16s
        aud_name = comfy_upload(wav)
        api_log(job_id, f"  đoạn {i+1}/{n}: {dur:.1f}s · {F}f · {action[:48]}", "info")
        api_progress(job_id, base + 0.02, f"đoạn {i+1}/{n}: render lip-sync ({F}f)")
        prefix = f"facemo-{job_id[:8]}-{i:02d}"
        pid = comfy_submit(build_wan_multitalk_workflow(img_name, aud_name, F, p, prompt=action, prefix=prefix, fps=fps))
        out = _await_comfy_video(pid, job_id, prefix, timeout=int(p.get("talk_timeout", 1800)))
        clips.append(_finalize_mp4(out))
    if not clips: raise RuntimeError("face-motion: không render được đoạn nào")
    # ALD 07/06/2026 - KHÔNG dọn audio SAU-render nữa: đã _clean_voice_wav() NGAY trên wav TRƯỚC lip-sync (sạch
    # hơn + cắt im để F khớp độ dài → miệng không mấp máy lúc hết tiếng), VHS mux thẳng audio sạch. Cách cũ dọn
    # bằng -c:v copy sau render làm audio NGẮN hơn video → lệch nhép cuối câu.
    q = str(p.get("quality") or "720p")
    if len(clips) == 1:
        final = clips[0]
    else:
        api_progress(job_id, 0.90, f"ghép {len(clips)} đoạn (giữ trọn lời từng đoạn)")
        final = os.path.join(tmp, "facemo.mp4")
        _concat_av(clips, final, fps=fps, xfade=0)   # cut thẳng: KHÔNG acrossfade → không nuốt chữ đầu/cuối mỗi câu
        final = _finalize_mp4(final)
    api_progress(job_id, 0.94, f"nâng chất lượng {q}")
    final = _upscale_talk(final, q, tmp)
    api_progress(job_id, 0.96, "tải kết quả")
    api_upload_output(job_id, final)
# #endregion

# ALD 03/06/2026 - concat: ghép NHIỀU clip (vd 2+ phân cảnh nhân vật NÓI) → 1 video, GIỮ tiếng từng cảnh.
def run_concat(job):
    """Ghép tuần tự clip video. Mặc định giữ audio từng clip; có thể mux 1 audio nguồn liên tục để tránh seam."""
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    reverse_output = str(params.get("reverseOutput") or params.get("reverse_output") or "").strip().lower() \
        in ("1", "true", "yes", "on")
    def _hnum(h):
        ds = "".join(c for c in h if c.isdigit()); return int(ds) if ds else 999
    keys = [(h, k) for h, k in inputs.items() if h not in ("music", "audio", "sourceAudio") and k]
    keys.sort(key=lambda x: (_hnum(x[0]), x[0]))   # clip1, clip2, … đúng thứ tự
    if len(keys) < 2:
        raise RuntimeError(f"concat cần ≥2 clip (nhận {len(keys)}: {[h for h, _ in keys]})")
    tmp = tempfile.mkdtemp(prefix=f"concat-{job_id[:8]}-")
    clips = []
    for i, (h, k) in enumerate(keys):
        api_progress(job_id, 0.1 + 0.25 * i / len(keys), f"tải clip {h}")
        clips.append(api_download(k, os.path.join(tmp, f"{i:02d}.mp4")))
    source_audio = None
    audio_mode = str(params.get("audioMode") or "clips").strip().lower()
    audio_key = inputs.get("audio") or inputs.get("sourceAudio") or inputs.get("music")
    if audio_mode in ("source", "original", "continuous") and audio_key:
        try:
            source_audio = api_download(audio_key, os.path.join(tmp, "source-audio" + (os.path.splitext(audio_key)[1] or ".mp4")))
            api_log(job_id, "concat audio: dùng 1 track nguồn liên tục, không nối audio từng đoạn", "info")
        except Exception as e:
            api_log(job_id, f"concat tải audio nguồn lỗi (fallback audio từng clip): {e}", "warn")
            source_audio = None
    # #region ALD 14/07/2026 - Dựng tháo dỡ rồi đảo ngược thành xây dựng.
    # Đảo từng clip 5 giây và đảo thứ tự clip, thay vì buffer toàn bộ video 30 giây trong RAM.
    if reverse_output:
        api_log(job_id, "concat: đảo ngược từng cảnh và thứ tự cảnh (tháo dỡ → xây dựng)", "info")
        reversed_clips = []
        for i, src in enumerate(reversed(clips)):
            api_progress(job_id, 0.36 + 0.16 * i / len(clips), f"đảo ngược cảnh {i + 1}/{len(clips)}")
            dst = os.path.join(tmp, f"reverse-{i:02d}.mp4")
            cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src,
                   "-vf", "reverse,setpts=PTS-STARTPTS", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "18", "-pix_fmt", "yuv420p"]
            if _has_audio(src):
                cmd += ["-af", "areverse,asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "192k"]
            else:
                cmd += ["-an"]
            cmd += [dst]
            subprocess.run(cmd, check=True, timeout=600)
            reversed_clips.append(dst)
        clips = reversed_clips
        if source_audio and _has_audio(source_audio):
            reversed_audio = os.path.join(tmp, "source-audio-reversed.m4a")
            subprocess.run([
                "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source_audio,
                "-vn", "-af", "areverse,asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "192k",
                reversed_audio,
            ], check=True, timeout=300)
            source_audio = reversed_audio
    # #endregion
    api_log(job_id, f"concat {len(clips)} clip ({', '.join(h for h, _ in keys)})", "info")
    out = os.path.join(tmp, "concat.mp4")
    api_progress(job_id, 0.55, f"ghép {len(clips)} phân cảnh")
    transition = str(params.get("transition") or "fade").strip().lower()
    softcut = transition in ("softcut", "soft-cut", "microfade", "micro-fade")
    if transition == "cut":
        xfade = 0.0
    elif softcut:
        try:
            soft_frames = max(1, min(12, int(float(params.get("softCutFrames") or params.get("soft_cut_frames") or 3))))
        except Exception:
            soft_frames = 3
        try:
            base_fps = float(params.get("fps") or 0) or (_video_fps(clips[0]) or 25)
        except Exception:
            base_fps = 25
        xfade = max(1.0 / max(1.0, base_fps), min(0.25, soft_frames / max(1.0, base_fps)))
        transition = "fade"
        api_log(job_id, f"softcut: blend {soft_frames} frame @ {base_fps:g}fps ≈ {xfade:.3f}s", "info")
    else:
        try:
            xfade = float(params.get("transitionDuration") or params.get("xfade") or 0.35)
        except Exception:
            xfade = 0.35
        xfade = max(0.0, min(2.0, xfade))
    try:
        first_clip_fps = float(_video_fps(clips[0]) or 25)
    except Exception:
        first_clip_fps = 25.0
    try:
        requested_fps = float(params.get("fps") if params.get("fps") is not None else 0)
    except Exception:
        requested_fps = 0.0
    # Legacy FE defaulted concat.fps to 25. Treat that as "preserve input" when the incoming
    # clips are already higher-FPS (e.g. Enhance 1080p60), otherwise 60fps jobs get downsampled.
    preserve_fps = requested_fps <= 0 or (abs(requested_fps - 25.0) < 0.01 and first_clip_fps > 30.0)
    concat_fps = 0 if (transition == "cut" or softcut or preserve_fps) else int(round(requested_fps))
    api_log(job_id, f"transition={transition}, duration={xfade:.3f}s, fps={'source' if concat_fps <= 0 else concat_fps}", "info")
    _concat_av(clips, out, fps=concat_fps, xfade=xfade, transition=transition, keep_audio=not bool(source_audio))
    if source_audio:
        muxed = os.path.join(tmp, "concat.source-audio.mp4")
        _mux_source_audio(out, source_audio, muxed)
        out = muxed
    api_progress(job_id, 0.92, "tải kết quả")
    out = _finalize_mp4(out)
    api_upload_output(job_id, out)

# #region ALD 08/07/2026 - Node "ĐÈ LỘ" (reveal-overlay): 2 video CÙNG người/động tác (khác bộ đồ) → wipe lộ dần
# video B (đồ mới) qua 1 dải viền-mềm quét top→bottom (mặc định). Pure ffmpeg (geq mask → alphamerge → overlay),
# RAM phẳng, KHÔNG GPU. Dùng cho ý tưởng: tryon×2 (cùng seed) → motion×2 (cùng driver+seed) → đè lộ.
# ⚠ 2 video PHẢI cùng người/pose để dải lộ khớp; đồ dáng khác hẳn → lệch mép ở ranh dải (feather bandPct che bớt).
# inputs: base(A,nền) + reveal(B,lộ) — hoặc 2 video-input đầu (sort theo handle). params: revealMode(wipe|scan),
# bandPct(0.25 = cao dải/feather), direction(down|up), sweepDuration(0=cả clip), swapBase(1=đảo A/B).
def run_reveal(job):
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    def _hnum(h):
        ds = "".join(c for c in h if c.isdigit()); return int(ds) if ds else 999
    a_key = inputs.get("base") or inputs.get("video1")
    b_key = inputs.get("reveal") or inputs.get("video2")
    if not (a_key and b_key):
        _ks = sorted([(h, k) for h, k in inputs.items() if h not in ("music", "audio", "sourceAudio") and k],
                     key=lambda x: (_hnum(x[0]), x[0]))
        if len(_ks) < 2:
            raise RuntimeError(f"đè lộ cần 2 video (nhận {len(_ks)})")
        a_key, b_key = _ks[0][1], _ks[1][1]
    if str(params.get("swapBase") or params.get("swap_base") or "").strip().lower() in ("1", "true", "yes", "on"):
        a_key, b_key = b_key, a_key
    tmp = tempfile.mkdtemp(prefix=f"reveal-{job_id[:8]}-")
    api_progress(job_id, 0.1, "tải 2 video")
    a = api_download(a_key, os.path.join(tmp, "a.mp4"))
    b = api_download(b_key, os.path.join(tmp, "b.mp4"))

    def _probe(v):
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height,avg_frame_rate:format=duration", "-of", "default=nw=1:nk=1", v],
                               capture_output=True, text=True, timeout=30)
            vals = [x for x in (r.stdout or "").split("\n") if x.strip()]
            W, H = int(vals[0]), int(vals[1])
            _num, _den = (vals[2].split("/") + ["1"])[:2]
            fps = (float(_num) / float(_den)) if float(_den or 0) else 25.0
            dur = float(vals[3]) if len(vals) > 3 else 0.0
            return W, H, (fps or 25.0), dur
        except Exception:
            return 0, 0, 25.0, 0.0
    W, H, fps, durA = _probe(a)
    _, _, _, durB = _probe(b)
    if W <= 0 or H <= 0:
        raise RuntimeError("đè lộ: không đọc được kích thước video A")
    _durs = [d for d in (durA, durB) if d > 0]
    dur = min(_durs) if _durs else 1.0
    fps_i = int(round(fps)) or 25
    try:
        bandPct = float(params.get("bandPct") or params.get("bandHeight") or 0.25)
    except Exception:
        bandPct = 0.25
    bandPct = max(0.05, min(0.9, bandPct)); BH = max(2, int(round(bandPct * H)))
    try:
        tsw = float(params.get("sweepDuration") or params.get("sweep_duration") or 0)
    except Exception:
        tsw = 0.0
    tsw = dur if tsw <= 0 else max(0.2, min(dur, tsw))
    mode = str(params.get("revealMode") or params.get("reveal_mode") or "wipe").strip().lower()
    direction = str(params.get("direction") or "down").strip().lower()
    loop = str(params.get("loop") or params.get("repeat") or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        twists = float(params.get("vortexTwists") or params.get("vortex_twists") or 2.0)
    except Exception:
        twists = 2.0
    twists = max(0.5, min(6.0, twists))

    # ALD 09/07/2026 - SLIDER: đường LINE CỨNG (không feather) quét NHANH qua GIỮA clip = thanh trượt so sánh
    # before/after. Trên/trước line = Nền(A), dưới/sau = Đồ mới(B). sweepAt (0.5=giữa clip → clip 10s quét ở giây 5),
    # sweepDuration = tốc độ quét (mặc định 1s), showLine = kẻ vạch trắng nhìn thấy lúc quét.
    try:
        _slider_at = float(params.get("sweepAt") or params.get("sweep_at") or 0.5)
    except Exception:
        _slider_at = 0.5
    _slider_at = max(0.0, min(1.0, _slider_at))
    try:
        _slider_sweep = float(params.get("sweepDuration") or params.get("sweep_duration") or 0) or 1.0
    except Exception:
        _slider_sweep = 1.0
    _slider_sweep = max(0.1, min(dur, _slider_sweep))
    # ALD 09/07 - sweepAtSec: nhập THẲNG giây quét (vd 5 = quét ở giây 5). Để trống/0 → dùng sweepAt frac (giữa clip).
    try:
        _at_sec = float(params.get("sweepAtSec") or params.get("sweep_at_sec") or 0)
    except Exception:
        _at_sec = 0.0
    _center = _at_sec if _at_sec > 0 else _slider_at * dur
    _slider_t0 = max(0.0, min(dur, _center) - _slider_sweep / 2.0)
    _slider_t1 = min(dur, _slider_t0 + _slider_sweep)
    _show_line = str(params.get("showLine", params.get("show_line", "1"))).strip().lower() in ("1", "true", "yes", "on")
    _line_w = max(1, int(params.get("lineWidth") or params.get("line_width") or 3))
    # ALD 09/07 - điểm BẮT ĐẦU/KẾT THÚC của line (frac trục, 0=đầu 1=cuối). vd startPos=0.6 → line bắt đầu ở 60% chiều cao.
    # Line di từ start→end; trên/đã-qua = Đồ mới(B), dưới = Nền(A). loop=dao động qua-lại start↔end suốt clip.
    try:
        _slider_start = float(params.get("startPos") if params.get("startPos") is not None else (params.get("start_pos") if params.get("start_pos") is not None else 0.0))
    except Exception:
        _slider_start = 0.0
    try:
        _slider_end = float(params.get("endPos") if params.get("endPos") is not None else (params.get("end_pos") if params.get("end_pos") is not None else 1.0))
    except Exception:
        _slider_end = 1.0
    _slider_start = max(0.0, min(1.0, _slider_start))
    _slider_end = max(0.0, min(1.0, _slider_end))
    _slider_span = _slider_end - _slider_start   # âm = quét ngược (end < start)
    # ALD 09/07 - toggle customSlider = "MỐC THỜI GIAN ACTION" cho MỌI mode (wipe/scan/vortex/slider):
    # TẮT = mặc định đơn giản (slider quét GIỮA clip full khung; wipe/scan/vortex chạy từ ĐẦU clip theo tsw).
    # BẬT = action quanh sweepAtSec (cửa sổ dài _slider_sweep) + điểm bắt đầu/kết thúc startPos/endPos (vortex bỏ vị trí).
    _custom = str(params.get("customSlider", params.get("custom_slider", "0"))).strip().lower() in ("1", "true", "yes", "on")
    if not _custom:
        _slider_start, _slider_end, _slider_span = 0.0, 1.0, 1.0
        _center = 0.5 * dur
        _slider_t0 = max(0.0, _center - _slider_sweep / 2.0)
        _slider_t1 = min(dur, _slider_t0 + _slider_sweep)

    # prog(T) ∈ [0,1]: 1 LƯỢT (min) hoặc LOOP (ping-pong cho scan = lên-xuống liên tục mượt; wrap cho wipe/vortex).
    # tsw = giây cho 1 lượt/1 chu kỳ → ĐIỀU CHỈNH TỐC ĐỘ (nhỏ = nhanh).
    if loop:
        prog = f"(1-abs(2*mod(T/{tsw},1)-1))" if mode in ("scan", "window") else f"mod(T/{tsw},1)"
    elif _custom:
        # ALD 09/07 - MỐC THỜI GIAN ACTION (custom bật, mọi mode): hiệu ứng chạy trong cửa sổ [_slider_t0, _slider_t1]
        # quanh sweepAtSec (trống = giữa clip), dài _slider_sweep giây. Trước đó đứng ở start, sau đó giữ ở end.
        prog = f"clip((T-{_slider_t0:.3f})/{_slider_sweep:.3f},0,1)"
    else:
        prog = f"min(1,T/{tsw})"

    # Canvas mask (geq): DỌC→8×H, NGANG→W×8 (mask 1 chiều, rẻ); CHÉO/VORTEX→(W/2)×(H/2) (2 chiều, atan2/hypot đắt
    # → hạ res cho nhanh, scale-up mượt). Mọi công thức theo TỌA ĐỘ MASK (mw,mh) rồi scale lên W×H.
    _diag = direction in ("diagtl", "diag-l", "diagleft", "45l", "diagtr", "diag-r", "diagright", "45r")
    if mode == "vortex" or _diag:
        mw, mh = max(64, W // 2), max(64, H // 2)
    elif direction in ("left", "right"):
        mw, mh = W, 8
    else:
        mw, mh = 8, H

    if mode == "vortex":                                       # LỐC XOÁY: reveal xoắn ốc từ tâm (quay + toả ra)
        cx, cy = mw / 2.0, mh / 2.0
        maxr = (mw * mw + mh * mh) ** 0.5 / 2.0
        theta = f"(atan2(Y-{cy:.1f},X-{cx:.1f})/(2*PI)+0.5)"    # 0..1 góc quanh tâm
        rr = f"(hypot(X-{cx:.1f},Y-{cy:.1f})/{maxr:.2f})"       # 0..1 bán kính
        span = 1.0 + twists
        fea = max(0.03, bandPct) * span
        lum = f"255*clip(({span:.3f}*{prog}-({theta}+{twists:.2f}*{rr}))/{fea:.4f},0,1)"
    else:                                                      # WIPE/SCAN theo trục P (hướng)
        if direction in ("left", "right"):
            P, Pmax = ("X", float(mw)) if direction == "right" else (f"({mw}-X)", float(mw))
        elif direction in ("diagtl", "diag-l", "diagleft", "45l"):
            P, Pmax = "(X+Y)", float(mw + mh)                  # 45° từ góc trên-TRÁI
        elif direction in ("diagtr", "diag-r", "diagright", "45r"):
            P, Pmax = f"(({mw}-X)+Y)", float(mw + mh)          # 45° từ góc trên-PHẢI
        elif direction == "up":
            P, Pmax = f"({mh}-Y)", float(mh)
        else:                                                  # down (mặc định)
            P, Pmax = "Y", float(mh)
        BW = max(2.0, bandPct * Pmax)                          # dải mềm = bandPct × độ dài quét
        if mode == "slider":                                   # LINE CỨNG (thanh chia): trên/đã-qua=B, dưới=A. KHÔNG feather
            if loop:
                _prog_s = f"(1-abs(2*mod(T/{_slider_sweep:.3f},1)-1))"    # dao động qua-lại start↔end suốt clip
            else:
                _prog_s = f"clip((T-{_slider_t0:.3f})/{_slider_sweep:.3f},0,1)"   # 1 lượt start→end, căn @sweepAt
            _edge = f"({_slider_start:.4f}+({_slider_span:.4f})*{_prog_s})*{Pmax:.1f}"   # vị trí line = start→end (frac trục)
            lum = f"255*lt({P},{_edge})"
        elif mode in ("scan", "window"):
            # ALD 09/07 - custom bật: cửa sổ chạy startPos→endPos (vd 60%→100%); tắt = 25–85% như cũ.
            _sl, _sh = (_slider_start, _slider_end) if _custom else (0.25, 0.85)
            if _sh < _sl:
                _sl, _sh = _sh, _sl
            p0, p1 = _sl * Pmax + BW / 2, _sh * Pmax - BW / 2
            c = f"({p0:.1f}+({p1 - p0:.1f})*{prog})"
            lum = f"255*(1-clip((abs({P}-{c})-{BW / 2:.1f})/{max(1.0, BW / 3.0):.2f},0,1))"
        else:                                                  # wipe: lộ dần theo edge startPos→endPos (mặc định 0→1 = full)
            _em = f"({_slider_start:.4f}+({_slider_span:.4f})*{prog})*({Pmax:.1f}+{BW:.1f})"
            lum = f"255*clip(({_em}-{P})/{BW:.1f},0,1)"

    # ALD 09/07 - vạch line trắng nhìn thấy lúc slider quét (trục thẳng down/up/left/right; chéo bỏ). drawbox dùng `t` giây.
    # Expr bọc trong '...' nên comma của clip()/between() literal, KHÔNG cần escape. enable= chỉ vẽ trong cửa sổ quét.
    _line_fc = ""
    if mode == "slider" and _show_line and not _diag:
        if loop:
            _pbox = f"(1-abs(2*mod(t/{_slider_sweep:.3f},1)-1))"    # loop: line dao động → vẽ suốt clip
            _enp = ""
        else:
            _pbox = f"clip((t-{_slider_t0:.3f})/{_slider_sweep:.3f},0,1)"
            _enp = f":enable='between(t,{_slider_t0:.3f},{_slider_t1:.3f})'"   # chỉ vẽ trong cửa sổ quét
        _efr = f"({_slider_start:.4f}+({_slider_span:.4f})*{_pbox})"           # vị trí line = start→end (frac trục)
        if direction in ("left", "right"):
            _lx = f"{W}*{_efr}" if direction == "right" else f"{W}-{W}*{_efr}"
            _line_fc = f",drawbox=x='{_lx}-{_line_w}/2':y=0:w={_line_w}:h=ih:color=white@0.9:t=fill{_enp}"
        else:
            _ly = f"{H}-{H}*{_efr}" if direction == "up" else f"{H}*{_efr}"
            _line_fc = f",drawbox=x=0:y='{_ly}-{_line_w}/2':w=iw:h={_line_w}:color=white@0.9:t=fill{_enp}"
    fc = (
        f"[1:v]scale={W}:{H}:flags=lanczos,fps={fps_i},setsar=1,format=yuva420p[b];"
        f"color=c=black:s={mw}x{mh}:r={fps_i}:d={dur:.3f},format=gray,geq=lum='{lum}',"
        f"scale={W}:{H}:flags=bilinear,format=gray[m];"
        f"[b][m]alphamerge[bA];"
        f"[0:v]fps={fps_i},setsar=1,format=yuv420p[a];"
        f"[a][bA]overlay=0:0:format=auto,format=yuv420p{_line_fc}[v]"
    )
    out = os.path.join(tmp, "reveal.mp4")
    _dbg_timing = (f"line cứng · quét {_slider_t0:.1f}→{_slider_t1:.1f}s (giữa @{_slider_at:.0%})" if mode == "slider"
                   else f"dải {int(bandPct*100)}% · {tsw:.1f}s/{'loop' if loop else 'lượt'}")
    api_log(job_id, f"đè lộ: {W}×{H}@{fps_i}fps · mode={mode}{' xoáy×'+str(twists) if mode=='vortex' else ''} · {_dbg_timing} · {direction} · mask {mw}×{mh}", "info")
    api_progress(job_id, 0.4, f"đè lộ ({mode}, dải {int(bandPct*100)}%)")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", a, "-i", b,
                    "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", out], check=True, timeout=1800)
    if not (os.path.exists(out) and os.path.getsize(out) > 1024):
        raise RuntimeError("đè lộ: ffmpeg không tạo được MP4")
    api_progress(job_id, 0.92, "tải kết quả")
    out = _finalize_mp4(out)
    api_upload_output(job_id, out)
# #endregion

def _still_clip(img, dur, out, W=720, H=1280, fps=25):
    """Ảnh tĩnh → clip ngắn (audio im lặng) cho cảnh KHÔNG thoại, để concat đồng nhất."""
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps},format=yuv420p"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.2f}", "-i", str(img),
                    "-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-shortest", str(out)], check=True, timeout=180)
    return out

# ALD 03/06/2026 - story-film: KỊCH BẢN → phim nhân vật. AI director (handler) phân tích kịch bản → characters+shots.
def run_story_film(job):
    """Phim nhân vật từ kịch bản. params.characters (AI tự nhận diện chính/phụ/quần chúng + giọng) + params.shots.
    (1) sinh ảnh tham chiếu MỖI nhân vật 1 lần (nhất quán); (2) mỗi shot compose nhân vật vào bối cảnh → có thoại
    → talk (lip-sync giọng riêng), không thoại → clip tĩnh; (3) concat giữ tiếng → phim. Config-driven (engine cấm fan-out)."""
    job_id = job["id"]; params = job.get("params", {})
    chars = params.get("characters") or []
    shots = params.get("shots") or []
    if not shots: raise RuntimeError("story-film: AI director chưa trả cảnh nào (kịch bản rỗng/không phân tích được)")
    gem_key = _gemini_key(params)
    fps = int(params.get("fps") or TALK_FPS)
    tmp = tempfile.mkdtemp(prefix=f"film-{job_id[:8]}-"); pid8 = job_id[:8]

    def _voice_of(ch):
        v = (ch.get("voice") or "").strip()
        if v: return v
        return "gemini:Puck" if str(ch.get("gender") or "").lower().startswith("m") else "gemini:Aoede"

    # 1) ảnh tham chiếu mỗi nhân vật chính/phụ (quần chúng bỏ qua cho nhẹ)
    char_ref, char_voice = {}, {}
    mains = [c for c in chars if str(c.get("role") or "").lower() not in ("extra", "quần chúng", "quan chung", "background")] or chars[:3]
    def _role_vi(r): return "chính" if str(r or "").lower() in ("main", "chính", "chinh", "lead") else "phụ"
    def _voice_vi(v): return "giọng nam" if any(x in (v or "").lower() for x in ("puck", "charon")) else "giọng nữ"
    # STORYBOARD: in NGAY đầu run cho user thấy AI HIỂU kịch bản thế nào (nhân vật chính/phụ + từng cảnh).
    api_log(job_id, f"🎬 Đạo diễn phân tích: {len(chars)} nhân vật ({len(mains)} chính/phụ) · {len(shots)} cảnh", "info")
    for c in chars:
        api_log(job_id, f"  👤 {c.get('name') or c.get('id')} — diễn viên {_role_vi(c.get('role'))}, {_voice_vi(_voice_of(c))}", "info")
    for si, sh in enumerate(shots):
        sp = str(sh.get('speakerId') or sh.get('speaker') or ''); ln = (sh.get('line') or '').strip()
        api_log(job_id, f"  🎞 Cảnh {si+1} [{sh.get('framing') or 'medium shot'}]: {(sh.get('setting') or '')[:48]}"
                + (f" — {sp}: “{ln[:38]}”" if ln else " — (hành động)"), "info")
    for ci, ch in enumerate(mains):
        cid = str(ch.get("id") or ch.get("name") or ci)
        appearance = (ch.get("appearance") or ch.get("desc") or ch.get("name") or "a Vietnamese person").strip()
        api_progress(job_id, 0.04 + 0.22 * ci / max(1, len(mains)), f"vẽ nhân vật {ch.get('name') or cid} ({_role_vi(ch.get('role'))})")
        pfx = f"film-{pid8}-char{ci}"
        wf = build_qwen_create_workflow([], f"Full-body character reference portrait of {appearance}, plain light-gray studio background, even soft lighting, neutral standing pose, clear sharp face, photorealistic, high detail", pfx, width=768, height=1024, force_size=True)
        img = comfy_fetch_output(comfy_poll(comfy_submit(wf), job_id, deadline_sec=600), exts=IMG_EXTS)
        if not img: raise RuntimeError(f"không tạo được ảnh nhân vật {cid}")
        api_preview(job_id, img, f"Nhân vật {_role_vi(ch.get('role'))}: {ch.get('name') or cid} · {_voice_vi(_voice_of(ch))}")  # thumbnail trên canvas
        char_ref[cid] = comfy_upload(img); char_voice[cid] = _voice_of(ch)
    first_ref = next(iter(char_ref.values()), None)

    # 2) mỗi shot: compose nhân vật vào bối cảnh → talk (thoại) / clip tĩnh (không thoại)
    clips = []
    for si, sh in enumerate(shots):
        api_progress(job_id, 0.28 + 0.58 * si / max(1, len(shots)), f"cảnh {si + 1}/{len(shots)}")
        setting = (sh.get("setting") or sh.get("scenePrompt") or sh.get("scene") or "a simple interior").strip()
        framing = (sh.get("framing") or sh.get("shot") or "medium shot").strip()
        speaker = str(sh.get("speakerId") or sh.get("speaker") or sh.get("characterId") or "")
        line = (sh.get("line") or "").strip()
        if isinstance(sh.get("dialogue"), dict):
            speaker = str(sh["dialogue"].get("speaker") or speaker); line = str(sh["dialogue"].get("line") or line).strip()
        elif not line and isinstance(sh.get("dialogue"), str):
            line = sh["dialogue"].strip()
        ref = char_ref.get(speaker) or first_ref
        pfx = f"film-{pid8}-shot{si}"
        comp = f"{framing}, a person standing in this scene: {setting}. KEEP the person's face, hairstyle and outfit unchanged from the reference. Cinematic warm lighting, photorealistic, sharp focus."
        wf = build_qwen_create_workflow([ref] if ref else [], comp, pfx, width=720, height=1280, force_size=True)
        scene_img = comfy_fetch_output(comfy_poll(comfy_submit(wf), job_id, deadline_sec=600), exts=IMG_EXTS)
        if not scene_img: raise RuntimeError(f"không dựng được cảnh {si + 1}")
        api_preview(job_id, scene_img, f"Cảnh {si + 1} [{framing}]" + (" — có thoại (lip-sync)" if line else " — hành động"))  # thumbnail
        scene_name = comfy_upload(scene_img)
        if line:
            voice = char_voice.get(speaker) or "gemini:Aoede"
            wav = os.path.join(tmp, f"v{si}.mp3")
            _tts(line, wav, voice, gem_key)
            dur = _audio_dur(wav) or max(2.0, len(line) / 12.0)
            F = max(25, min(int(round(dur * fps)) + 8, int(params.get("max_frames", 257))))
            tpfx = f"film-{pid8}-talk{si}"
            wf2 = build_wan_multitalk_workflow(scene_name, comfy_upload(wav), F, params, prompt=(sh.get("action") or ""), prefix=tpfx, fps=fps)
            clip = _await_comfy_video(comfy_submit(wf2), job_id, tpfx, timeout=int(params.get("talk_timeout", 1800)))
            clips.append(_finalize_mp4(clip))
        else:
            still = os.path.join(tmp, f"shot{si}.mp4")
            _still_clip(scene_img, max(2.0, min(8.0, float(sh.get("durationSec") or 3.0))), still, fps=fps)
            clips.append(still)
        api_log(job_id, f"✓ cảnh {si + 1}/{len(shots)} xong ({'thoại' if line else 'tĩnh'})", "info")
    if not clips: raise RuntimeError("story-film: không tạo được clip nào")

    # 3) ghép tất cả cảnh
    api_progress(job_id, 0.9, f"ghép {len(clips)} cảnh")
    out = os.path.join(tmp, "film.mp4")
    if len(clips) >= 2:
        _concat_av(clips, out, fps=fps); out = _finalize_mp4(out)
    else:
        out = _finalize_mp4(clips[0])
    api_progress(job_id, 0.95, "upload phim")
    api_upload_output(job_id, out)

# ALD 31/05/2026 - Caption overlay (chữ trên màn hình) qua drawtext. DejaVu Sans hỗ trợ tiếng Việt.
# Dùng textfile= tránh escape ký tự đặc biệt (dấu :, ', xuống dòng) trong text VN.
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
def _drawtext_vf(caption, dst, H):
    cap = (caption or "").strip()
    if not cap:
        return ""
    capf = str(dst) + ".cap.txt"
    with open(capf, "w") as f:
        f.write(cap)
    fs = max(24, int(H * 0.058))
    p = capf.replace("\\", "/").replace(":", "\\:")
    fp = FONT_BOLD.replace(":", "\\:")
    bw = max(4, fs // 12)
    # ALD 04/06/2026 - Caption kiểu TikTok: chữ TO, trắng, VIỀN ĐEN dày + bóng (bỏ box), đặt giữa-dưới (~64% cao).
    # expansion=none → %, {…} hiển thị nguyên văn (caption "65%" không bị strftime expand).
    return (f",drawtext=fontfile='{fp}':textfile='{p}':expansion=none:fontcolor=white:fontsize={fs}"
            f":borderw={bw}:bordercolor=black:shadowcolor=black@0.6:shadowx=3:shadowy=3:line_spacing=10"
            f":x=(w-text_w)/2:y=h*0.64-text_h/2")

# ALD 04/06/2026 - _ff_still_clip (Ken Burns) ĐÃ GỠ theo yêu cầu (pan/zoom ảnh tĩnh = vô dụng).
# Mọi shot dùng chuyển động AI thật (Wan I2V / LTX). Motion lỗi → bỏ shot, KHÔNG rơi về ảnh tĩnh.

def _ff_norm_clip(src, dst, W, H, dur, caption=None):
    # Cover-fill (I2V 480p → 720p sạch, không letterbox) + caption.
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=25,format=yuv420p"
          + _drawtext_vf(caption, dst, H))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", str(dur), "-vf", vf, "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(dst)], check=True, timeout=300)

def _ff_norm_clip_cover(src, dst, W, H, dur, fps=30, crf=19):
    """Cover-fill chuẩn hoá social clip: không letterbox, không audio, CFR ổn định để concat."""
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={int(fps)},format=yuv420p"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-t", f"{float(dur):.3f}",
                    "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf)),
                    "-movflags", "+faststart", str(dst)], check=True, timeout=300)
    return dst

def _teen_apply_camera_move(src, dst, W, H, dur, fps=30, move="orbit-left"):
    """Ép flycam/camera move bằng hậu kỳ thay vì trông chờ Wan hiểu prompt camera."""
    z0, z1, x0, x1, y0, y1 = {
        "orbit-left": (1.16, 1.24, 0.18, 0.58, 0.48, 0.46),
        "dolly-right": (1.20, 1.20, 0.16, 0.68, 0.50, 0.50),
        "pushin": (1.06, 1.34, 0.50, 0.50, 0.36, 0.32),
        "pullback": (1.34, 1.06, 0.50, 0.50, 0.48, 0.53),
        "walk-push": (1.10, 1.28, 0.50, 0.50, 0.52, 0.45),
    }.get(str(move or ""), (1.12, 1.22, 0.45, 0.55, 0.50, 0.50))
    frames = max(2, int(round(float(dur) * int(fps))))
    prog = f"(n/{frames - 1})"
    zw = f"({z0}+({z1}-{z0})*{prog})"
    xexpr = f"((iw-{W})*({x0}+({x1}-{x0})*{prog}))"
    yexpr = f"((ih-{H})*({y0}+({y1}-{y0})*{prog}))"
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"scale=w='iw*{zw}':h='ih*{zw}':eval=frame,"
        f"crop={W}:{H}:x='{xexpr}':y='{yexpr}',"
        f"setsar=1,fps={int(fps)},format=yuv420p"
    )
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-t", f"{float(dur):.3f}",
                    "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-movflags", "+faststart", str(dst)], check=True, timeout=300)
    return dst

def _shot_reframe(src, idx, tmp):
    """ALD 03/06/2026 - Đa dạng KHUNG mỗi shot từ CÙNG 1 ảnh (lookbook: ảnh mẫu) → full/thân trên/váy/cận
    3-4… cho đỡ nhàm. Crop vùng khác nhau theo idx (ảnh dọc). full → khỏi crop; lỗi → trả ảnh gốc."""
    dims = _img_size(src)
    if not dims:
        return src
    W0, H0 = dims
    boxes = [(0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 0.58), (0.0, 0.42, 1.0, 0.58),
             (0.14, 0.06, 0.72, 0.86), (0.0, 0.0, 1.0, 1.0), (0.16, 0.0, 0.68, 0.52)]
    fx, fy, fw, fh = boxes[idx % len(boxes)]
    if fw >= 0.999 and fh >= 0.999:
        return src
    x = int(W0 * fx); y = int(H0 * fy)
    w = max(64, min(W0 - x, int(W0 * fw))); h = max(64, min(H0 - y, int(H0 * fh)))
    out = os.path.join(tmp, f"reframe{idx}.png")
    try:
        _ff_crop(src, out, w, h, x, y)
        return out
    except Exception:
        return src

def _probe_dur(p):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True, timeout=30)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0

# ALD 03/06/2026 - Ghép clip GIỮ TIẾNG từng clip (cho concat nhiều phân cảnh NÓI, mỗi cảnh giọng riêng).
# Khác _concat_xfade (vốn -an bỏ tiếng rồi mux 1 VO chung) → đây nối tuần tự cả video LẪN audio mỗi clip.
def _has_audio(p):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=30)
        return bool((r.stdout or "").strip())
    except Exception:
        return False

def _norm_av(src, dst, W, H, fps):
    """Chuẩn hoá 1 clip về W×H×fps + audio aac 44.1k stereo (thêm im lặng nếu clip không tiếng) để concat đồng nhất.
    Audio ngắn hơn video thì pad im lặng để không rơi frame; audio dài hơn thì `-shortest` cắt theo video."""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p")
    if _has_audio(src):
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-af", "aresample=async=1:first_pts=0,apad",
                        "-shortest", str(dst)], check=True, timeout=300)
    else:
        dur = _probe_dur(src) or 3.0
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                        "-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "1:a",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-shortest",
                        str(dst)], check=True, timeout=300)
    return dst

def _norm_video_only(src, dst, W, H, fps):
    """Chuẩn hoá hình, bỏ audio. Dùng khi audio cuối lấy từ một nguồn liên tục riêng."""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
                    "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    str(dst)], check=True, timeout=300)
    return dst

def _mux_source_audio(video, audio, out):
    """Mux video đã ghép với audio nguồn liên tục, cắt audio theo độ dài video."""
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                    "-af", "aresample=async=1:first_pts=0", "-shortest", "-movflags", "+faststart",
                    str(out)], check=True, timeout=300)
    return out

def _concat_av(clips, out, fps=25, xfade=0.35, transition="fade", keep_audio=True):
    """Ghép clips GIỮ tiếng từng clip + CHUYỂN CẢNH MƯỢT (xfade video + acrossfade audio) → hết khựng/đứng hình.
    Chuẩn hoá đồng nhất (W×H clip đầu) trước. xfade<=0 / 1 clip / clip quá ngắn → nối thẳng (cut)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "csv=s=x:p=0", str(clips[0])],
                           capture_output=True, text=True, timeout=30)
        parts = (r.stdout or "720x1280x25/1").strip().split("x")
        W, H = (int(x) for x in parts[:2])
        if int(fps or 0) <= 0:
            n, d = (parts[2].split("/") + ["1"])[:2]
            fps = float(n) / max(1.0, float(d))
    except Exception:
        W, H = 720, 1280
        fps = 25
    W -= W % 2; H -= H % 2
    norm, durs = [], []
    for i, c in enumerate(clips):
        n = os.path.splitext(out)[0] + f".n{i}.mp4"
        if keep_audio:
            _norm_av(c, n, W, H, fps)
        else:
            _norm_video_only(c, n, W, H, fps)
        norm.append(n); durs.append(_probe_dur(n) or 0.0)
    args = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    for n in norm: args += ["-i", str(n)]
    T = float(xfade)
    allowed_transitions = {
        "fade", "smoothleft", "smoothright", "smoothup", "smoothdown",
        "circleopen", "circleclose", "radial", "dissolve", "pixelize",
        "wipeleft", "wiperight", "wipeup", "wipedown",
    }
    transition = str(transition or "fade").strip().lower()
    if transition not in allowed_transitions:
        transition = "fade"
    if T > 0 and fps:
        # ffmpeg xfade can collapse very short (1-frame) transitions to the first clip's
        # duration. Keep micro transitions at least 2 frames so N clips remain N clips.
        T = max(T, 2.0 / max(1.0, float(fps)))
    if len(norm) < 2 or T <= 0 or (durs and min(durs) <= T * 2):
        # clip quá ngắn để xfade an toàn → nối thẳng (concat filter)
        if keep_audio:
            fc = "".join(f"[{i}:v][{i}:a]" for i in range(len(norm))) + f"concat=n={len(norm)}:v=1:a=1[v][a]"
            args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
        else:
            fc = "".join(f"[{i}:v]" for i in range(len(norm))) + f"concat=n={len(norm)}:v=1:a=0[v]"
            args += ["-filter_complex", fc, "-map", "[v]"]
    else:
        # xfade chain (video) + acrossfade chain (audio): mỗi mối ghép hoà tan T giây → mượt, tự nhiên
        chains = []
        vlabel, alabel, total = "0:v", "0:a", durs[0]
        for i in range(1, len(norm)):
            off = max(0.0, total - T)
            vo, ao = f"vx{i}", f"ax{i}"
            chains.append(f"[{vlabel}][{i}:v]xfade=transition={transition}:duration={T}:offset={off:.3f}[{vo}]")
            if keep_audio:
                chains.append(f"[{alabel}][{i}:a]acrossfade=d={T}[{ao}]")
            vlabel, alabel = vo, ao
            total = total + durs[i] - T
        args += ["-filter_complex", ";".join(chains), "-map", f"[{vlabel}]", *(["-map", f"[{alabel}]"] if keep_audio else [])]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if keep_audio:
        args += ["-c:a", "aac"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True, timeout=900)
    return out

# ALD 03/06/2026 - Chuyển cảnh ĐA DẠNG (đổi mỗi cắt) → giật, cuốn kiểu CapCut thay vì chỉ hòa tan.
_XFADES = ["slideleft", "circleopen", "wiperight", "slideup", "fade", "wipeleft",
           "circleclose", "slideright", "dissolve", "smoothup", "fadeblack", "smoothleft",
           # ALD 04/06/2026 - thêm transition punchy kiểu CapCut/TikTok (xfade ffmpeg hỗ trợ)
           "zoomin", "squeezev", "squeezeh", "pixelize", "wipeup", "wipedown",
           "slidedown", "hlslice", "vuslice", "coverup", "revealup", "diagtl"]
# ALD 04/06/2026 - Bộ transition cho style TikTok: dồn dập, biến đổi liên tục (zoom/slide/squeeze/slice).
_XFADES_TIKTOK = ["zoomin", "slideup", "squeezev", "circleopen", "slideleft", "wipeup",
                  "pixelize", "slideright", "hlslice", "circleclose", "squeezeh", "coverup",
                  "diagtl", "revealup", "wipedown"]
# ALD 03/06/2026 - 3 STYLE ghép: dynamic (TikTok, cắt nhanh đa dạng) / cinematic (chậm, sang, hòa tan)
# / transform (biến hình, flash trắng + circle reveal). Cùng bộ clip → 3 cảm giác khác nhau.
# ALD 03/06/2026 - Mỗi style có (transitions, T chuyển-cảnh, SPEED nhân tốc video). Wan I2V vốn êm/trôi
# (16fps + LoRA distill) → cảm giác "slow-motion". SPEED>1 nén time bằng setpts → chuyển động giựt,
# clip ngắn lại, cắt dồn dập kiểu TikTok/CapCut. cinematic giữ 1.0 (chậm sang, dissolve dài, đúng chất).
# Tinh chỉnh nhanh không cần sửa code: env TEASER_SPEED_MUL nhân thêm cho cả 3 (vd 1.2 = giựt hơn nữa).
_SPEED_MUL = float(os.environ.get("TEASER_SPEED_MUL", "1.0") or "1.0")
_STYLE_XFADES = {
    "dynamic":   (_XFADES, 0.25, 1.35),
    "cinematic": (["dissolve", "fade", "dissolve", "fade", "dissolve"], 0.7, 1.0),
    "transform": (["fadewhite", "circleopen", "fadewhite", "circleclose", "fadewhite"], 0.28, 1.45),
    # ALD 04/06/2026 - TikTok affiliate: cắt cực nhanh (T ngắn), tốc 1.5x, transition dồn dập đa dạng.
    "tiktok":    (_XFADES_TIKTOK, 0.18, 1.5),
}
def _concat_xfade(clips, out_video, style="dynamic"):
    """Ghép clips với CHUYỂN CẢNH + TĂNG TỐC theo style (xem _STYLE_XFADES). speed nén time qua setpts
    → chống slow-mo, cắt nhanh hơn. VO/nhạc mux SAU nên KHÔNG bị tăng tốc (giọng không bị méo)."""
    trans, T, speed = _STYLE_XFADES.get(style, _STYLE_XFADES["dynamic"])
    speed = max(0.5, min(3.0, speed * _SPEED_MUL))
    if len(clips) == 1:
        if abs(speed - 1.0) < 0.01:
            shutil.copy(clips[0], out_video); return
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clips[0]), "-vf",
                        f"setpts=PTS/{speed:.3f},format=yuv420p", "-an", "-c:v", "libx264", "-preset",
                        "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out_video)], check=True, timeout=300)
        return
    durs = [max(0.1, _probe_dur(c) / speed) for c in clips]   # độ dài SAU khi tăng tốc → tính offset xfade
    inputs = []
    for c in clips: inputs += ["-i", str(c)]
    filt = [f"[{i}:v]setpts=PTS/{speed:.3f},format=yuv420p[s{i}]" for i in range(len(clips))]  # nén tốc từng clip
    prev, offset = "s0", 0.0
    for i in range(1, len(clips)):
        offset += max(0.1, durs[i - 1] - T)
        tr = trans[(i - 1) % len(trans)]
        lbl = f"vx{i}"
        filt.append(f"[{prev}][s{i}]xfade=transition={tr}:duration={T:.2f}:offset={offset:.3f}[{lbl}]")
        prev = lbl
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filt), "-map", f"[{prev}]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out_video)],
                   check=True, timeout=600)

def _teaser_montage(clips, voice, music, out_file, style="dynamic"):
    work = os.path.dirname(clips[0])
    montage = os.path.join(work, f"montage-{style}.mp4")
    try:
        _concat_xfade(clips, montage, style=style)
    except Exception:
        # Fallback hard-cut concat nếu xfade lỗi (clip khác size/fps).
        listf = os.path.join(work, "concat.txt")
        with open(listf, "w") as f: f.write("\n".join(f"file '{c}'" for c in clips))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf, "-c", "copy", montage], check=True, timeout=300)
    hv = bool(voice and os.path.exists(voice)); hm = bool(music and os.path.exists(music))
    # ALD 02/06/2026 - QUAN TRỌNG: audio (VO/nhạc) thường NGẮN hơn video → KHÔNG được để -shortest cắt
    # cụt VIDEO theo audio (bug cũ: VO 8s cắt video 20s còn 8s). Pad/loop audio cho ≥ video rồi -shortest
    # cắt theo VIDEO (montage là input đầu, hữu hạn) → output = trọn độ dài video.
    if hv and hm:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", montage, "-i", voice, "-i", music, "-filter_complex",
                        "[1:a]volume=1.0[vo];[2:a]volume=0.22,aloop=loop=-1:size=200000000[mu];[vo][mu]amix=inputs=2:duration=longest:dropout_transition=2,apad[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", out_file], check=True, timeout=300)
    elif hv:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", montage, "-i", voice, "-filter_complex", "[1:a]apad[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", out_file], check=True, timeout=300)
    elif hm:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", montage, "-stream_loop", "-1", "-i", music, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-shortest", out_file], check=True, timeout=300)
    else:
        shutil.copy(montage, out_file)

PIPER_DIR = os.environ.get("PIPER_DIR", "/app/piper-voice")
# ALD 03/06/2026 - Mặc định GIỌNG GEMINI TTS (tự nhiên, dùng key Gemini) — edge-tts bị MS chặn IP server,
# Piper thì robotic. 'gemini:<VoiceName>'. Không có key → fallback Piper (offline, robotic).
DEFAULT_VOICE = os.environ.get("DEFAULT_TTS_VOICE", "") or ("omnivoice" if OMNIVOICE_URL else ("vixtts" if VIXTTS_URL else "gemini:Kore"))
PIPER_DEFAULT = "vi_VN-vais1000-medium"
# Registry giọng Piper (offline). voiceId = tên model Piper. Label cho FE picker.
PIPER_VOICES = {
    "vi_VN-vais1000-medium":              "Tiếng Việt · Nữ",
    "en_US-amy-medium":                   "English (US) · Nữ — trẻ",
    "en_US-kristin-medium":               "English (US) · Nữ — thanh niên",
    "en_US-hfc_female-medium":            "English (US) · Nữ — trung niên",
    "en_US-joe-medium":                   "English (US) · Nam — trẻ",
    "en_US-ryan-high":                    "English (US) · Nam — tự nhiên (HQ)",
    "en_US-hfc_male-medium":              "English (US) · Nam — trung niên",
    "en_GB-alba-medium":                  "English (UK) · Nữ",
    "en_GB-alan-medium":                  "English (UK) · Nam",
    "en_GB-northern_english_male-medium": "English (UK) · Nam — miền Bắc",
}

def _piper_path(name):
    # name = <locale>-<speaker>-<quality> → HF path <lang>/<locale>/<speaker>/<quality>/<name>
    locale, speaker, quality = name.split("-")
    return f"{locale.split('_')[0]}/{locale}/{speaker}/{quality}/{name}"

def _ensure_voice(voice):
    """Trả path .onnx; lazy-download từ HuggingFace nếu chưa có (cache PIPER_DIR)."""
    if voice not in PIPER_VOICES:
        voice = PIPER_DEFAULT
    onnx = os.path.join(PIPER_DIR, voice + ".onnx")
    if not (os.path.exists(onnx) and os.path.getsize(onnx) > 0):
        os.makedirs(PIPER_DIR, exist_ok=True)
        base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/" + _piper_path(voice)
        for ext in (".onnx", ".onnx.json"):
            r = requests.get(base + ext, timeout=600); r.raise_for_status()
            with open(os.path.join(PIPER_DIR, voice + ext), "wb") as f: f.write(r.content)
        log(f"piper voice tải về: {voice}")
    return onnx

# Giọng NEURAL (edge-tts, online) — tự nhiên hơn Piper NHIỀU. id kết thúc 'Neural'. Label cho FE picker.
EDGE_VOICES = {
    "vi-VN-HoaiMyNeural":  "Tiếng Việt · Nữ (tự nhiên)",
    "vi-VN-NamMinhNeural": "Tiếng Việt · Nam (tự nhiên)",
    "en-US-AriaNeural":    "English (US) · Nữ (tự nhiên)",
    "en-US-GuyNeural":     "English (US) · Nam (tự nhiên)",
    "en-GB-SoniaNeural":   "English (UK) · Nữ (tự nhiên)",
    "en-GB-RyanNeural":    "English (UK) · Nam (tự nhiên)",
}
# Fallback locale → giọng neural (khi voice id lạ / Piper lỗi).
EDGE_FALLBACK = {"vi_VN": "vi-VN-HoaiMyNeural", "en_US": "en-US-AriaNeural", "en_GB": "en-GB-SoniaNeural"}
def _edge_voice(voice):
    return EDGE_FALLBACK.get(str(voice or "").split("-")[0], "vi-VN-HoaiMyNeural")

def _edge_tts(script, out_mp3, voice):
    import asyncio
    from edge_tts import Communicate
    asyncio.run(Communicate(script, voice=voice).save(out_mp3))

def _tts_engine(script, out_mp3, voice=None, gem_key=None, emotion=None, gender=None):
    """TTS đa tầng: (1) Gemini TTS (tự nhiên nhất, cần key) — voice 'gemini:<Name>' HOẶC giọng đang là
    default/Piper-robotic + có key → tự dùng Gemini. (2) edge-tts neural (server này thường bị MS chặn).
    (3) Piper offline (robotic, luôn chạy — fallback cuối). emotion/gender (Trụ C): chọn file ref .wav đúng sắc thái
    + temperature viXTTS theo cảm xúc (chỉ tác động nhánh clone OmniVoice/viXTTS)."""
    voice = (voice or DEFAULT_VOICE).strip()
    gem_key = (gem_key or "").strip()
    # Trụ C: ref cảm xúc (None nếu chưa có file → giữ giọng gốc) + temperature theo "họ" cảm xúc.
    emo_ref = _resolve_emotion_ref(voice, emotion, gender)
    vix_temp = EMOTION_TEMPERATURE.get(_emotion_family(emotion)) if emotion else None
    if emo_ref: log(f"tts emotion={emotion} → ref {os.path.basename(emo_ref)}")
    # ALD 14/06/2026 - 0) OmniVoice clone — engine CHÍNH (Apache-2.0, bản VN). Ưu tiên TRÊN viXTTS. Cùng cơ chế chọn
    #    ref như viXTTS: giọng đặt-tên / 'omnivoice' / 'omni:<ref>' / 'voicelib:<id>' / default → clone. Lỗi → rớt viXTTS↓.
    if OMNIVOICE_URL and (voice in VIXTTS_VOICES or voice.startswith("omni") or voice.startswith("voicelib:")
                          or voice in ("vi_VN-vais1000-medium", DEFAULT_VOICE, "vixtts", "")):
        try:
            if voice in VIXTTS_VOICES:           # giọng ĐẶT-TÊN → file ref .wav trên box (chung pool với viXTTS)
                ref = VIXTTS_VOICES[voice]
            elif voice.startswith("voicelib:"):  # thư viện giọng 'voicelib:<id>' → tải ref về tmp (service đọc path)
                ref = api_resolve_voice_ref(voice.split(":", 1)[1]) or None
            elif voice.startswith("omni:"):      # 'omni:<path>' → ref tuỳ ý
                ref = voice.split(":", 1)[1]
            else:                                 # 'omnivoice'/'vixtts'/default → ref mặc định của service
                ref = None
            if emo_ref: ref = emo_ref             # Trụ C: ref cảm xúc đè ref giọng gốc (nếu có file)
            _omnivoice_tts(script, out_mp3, ref); return
        except Exception as e:
            log(f"omnivoice fail → fallback viXTTS/Gemini: {e}")
    # 0.5) viXTTS clone (giọng file mẫu) — dự phòng khi OmniVoice tắt/lỗi. Giọng đặt-tên ('voice-male-best') /
    #    'vixtts' / 'vixtts:<ref>' / 'voicelib:<id>' (thư viện giọng) / giọng default/Piper-cũ + service chạy →
    #    dùng giọng clone. Lỗi → fallback.
    if VIXTTS_URL and (voice in VIXTTS_VOICES or voice.startswith("vixtts") or voice.startswith("voicelib:")
                       or voice in ("vi_VN-vais1000-medium", DEFAULT_VOICE, "")):
        try:
            if voice in VIXTTS_VOICES:           # giọng ĐẶT-TÊN → file ref .wav tương ứng trên box
                ref = VIXTTS_VOICES[voice]
            elif voice.startswith("voicelib:"):  # ALD 13/06/2026 - thư viện giọng: 'voicelib:<id>' → tải ref về tmp
                ref = api_resolve_voice_ref(voice.split(":", 1)[1])
                if not ref:                       # không resolve được → fallback ref mặc định service, KHÔNG vỡ job
                    log(f"voicelib không resolve được ({voice}) → dùng giọng viXTTS mặc định")
                    ref = None
            elif voice.startswith("vixtts:"):    # 'vixtts:<path>' → ref tuỳ ý
                ref = voice.split(":", 1)[1]
            else:                                 # 'vixtts' trơn / default → ref mặc định của service
                ref = None
            if emo_ref: ref = emo_ref             # Trụ C: ref cảm xúc đè ref giọng gốc (nếu có file)
            _vixtts_tts(script, out_mp3, ref, temperature=vix_temp); return
        except Exception as e:
            log(f"vixtts fail → fallback:", e)
    # 1) Gemini TTS
    gv = None
    if voice.startswith("gemini:"):
        gv = voice.split(":", 1)[1] or GEMINI_TTS_VOICE
    elif gem_key and voice in ("vi_VN-vais1000-medium", DEFAULT_VOICE, ""):
        gv = GEMINI_TTS_VOICE   # giọng cũ/default + có key → nâng cấp Gemini TTS tự nhiên
    if gv and _valid_gemini_key(gem_key):
        try:
            _gemini_tts(script, out_mp3, gem_key, gv); return
        except Exception as e:
            log(f"gemini-tts ({gv}) fail → fallback:", e)
    # 2) edge-tts neural (id kết thúc 'Neural') — có thể fail do MS chặn IP server
    if voice.endswith("Neural"):
        try:
            _edge_tts(script, out_mp3, voice); return
        except Exception as e:
            log(f"edge-tts ({voice}) fail → piper:", e)
    # 3) Piper offline (fallback cuối — luôn chạy được)
    pv = voice if voice in PIPER_VOICES else PIPER_DEFAULT
    try:
        model = _ensure_voice(pv)
        wav = out_mp3 + ".wav"
        subprocess.run(["piper", "-m", model, "-f", wav],
                       input=script.encode("utf-8"), check=True, capture_output=True, timeout=180)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, out_mp3], check=True, timeout=60)
        return
    except Exception as e:
        log(f"piper TTS ({pv}) fail → edge locale:", e)
    _edge_tts(script, out_mp3, _edge_voice(pv))

# #region ALD 30/06/2026 - Trụ C: funnel TTS có CHỈ ĐẠO CẢM XÚC. Bọc _tts_engine: (1) nắn dấu câu theo emotion,
# (2) engine chọn ref/temperature theo emotion, (3) chỉnh tốc độ đọc theo pace (atempo, giữ cao độ). emotion/pace do
# đạo diễn gán mỗi câu; None → hành vi y như cũ (mọi call-site cũ KHÔNG cần đổi). Tên _tts giữ nguyên cho call-site cũ.
def _tts(script, out_mp3, voice=None, gem_key=None, emotion=None, pace=None, gender=None):
    shaped = _shape_tts_text(script, pace, emotion)
    factor = PACE_ATEMPO.get(str(pace or "").lower().strip(), 1.0)
    if abs(factor - 1.0) < 0.01:
        _tts_engine(shaped, out_mp3, voice, gem_key, emotion=emotion, gender=gender); return
    tmp = out_mp3 + ".pace.mp3"
    _tts_engine(shaped, tmp, voice, gem_key, emotion=emotion, gender=gender)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-filter:a", f"atempo={factor:.3f}", out_mp3], check=True, timeout=120)
        os.remove(tmp)
    except Exception as e:
        log(f"pace atempo fail → giữ tốc độ gốc: {e}")
        os.replace(tmp, out_mp3)
# #endregion

def run_teaser(job):
    """Teaser montage: shotlist → mỗi shot tryon (Qwen) → motion (Wan, nếu có) hoặc still →
    ghép montage + voiceover (edge-tts) + nhạc nền. inputs.model[]+product[] (+motion?+music?)."""
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    import re
    aslist = lambda v: v if isinstance(v, list) else ([v] if v else [])
    # Gom mọi handle model*/product* ĐỘNG (node teaser: cổng product, product2..N + model, model2..N).
    # Sort theo số thứ tự để giữ đúng thứ tự cổng (product < product2 < product3…).
    def _gather(prefix):
        ks = sorted((k for k in inputs if re.fullmatch(prefix + r"\d*", k)),
                    key=lambda k: int(re.sub(r"\D", "", k) or "1"))
        return sum((aslist(inputs.get(k)) for k in ks), [])
    models = _gather("model")
    products = _gather("product")
    scenes = _gather("scene")
    if not products:
        raise RuntimeError("teaser cần ít nhất inputs.product[] (ảnh sản phẩm)")
    motion_key = inputs.get("motion")            # optional — teaser thường KHÔNG có motion
    music_key = inputs.get("music") or inputs.get("audio")
    # voiceScript = VO sạch do AI bóc từ storyboard; fallback scriptText thô.
    script = (params.get("voiceScript") or params.get("scriptText") or params.get("script_text") or params.get("script") or "").strip()
    garment = str(params.get("garmentType") or params.get("garment_type") or "auto").lower().strip()
    # provider try-on cho teaser: 'qwen' (mặc định) | 'gemini' (giỏi giày). gem_key từ node hoặc env.
    provider = str(params.get("provider") or "qwen").lower().strip()
    gem_key = _gemini_key(params)
    # Key Gemini sai định dạng (vd dán nhầm) → bỏ qua Gemini, dùng Qwen (KHÔNG crash cả teaser).
    if provider == "gemini" and gem_key and not _valid_gemini_key(gem_key):
        api_log(job_id, "Key Gemini không đúng định dạng → bỏ Gemini, dùng Qwen.", "warn"); gem_key = ""
    target = int(params.get("targetDurationSec") or params.get("target_duration_sec") or params.get("durationSec") or 30)
    W = int(params.get("width", 720)); H = int(params.get("height", 1280))
    # ALD 03/06/2026 - Phụ đề (chữ trên màn hình) MẶC ĐỊNH TẮT (user thấy kì cục). Bật lại: params.captions=true.
    # ALD 04/06/2026 - Caption mặc định TẮT (user không cần); chỉ bật khi đặt captions=true.
    show_captions = bool(params.get("captions") or params.get("showCaptions") or params.get("showCaption"))
    tmp = tempfile.mkdtemp(prefix=f"teaser-{job_id[:8]}-")
    api_progress(job_id, 0.03, "tải input")
    m_local = [api_download(k, os.path.join(tmp, f"m{i}" + os.path.splitext(k)[1])) for i, k in enumerate(models)]
    # p_local = NHIỀU ẢNH/GÓC của CÙNG 1 sản phẩm (KHÔNG phải nhiều sản phẩm khác nhau).
    p_local = [api_download(k, os.path.join(tmp, f"p{i}" + os.path.splitext(k)[1])) for i, k in enumerate(products)]
    # s_local = ảnh bối cảnh THẬT cho lookbook sceneMode='reference' (scene, scene2..N).
    s_local = [api_download(k, os.path.join(tmp, f"s{i}" + os.path.splitext(k)[1])) for i, k in enumerate(scenes)]
    motion_name = None
    if motion_key:
        ml = api_download(motion_key, os.path.join(tmp, "motion" + (os.path.splitext(motion_key)[1] or ".mp4")))
        motion_name = comfy_upload(ml)
    nimg = len(p_local)   # số ảnh của sản phẩm
    # shotlist: cảnh đã tách từ kịch bản người dùng (product_idx = ảnh nào, caption, durationSec). Fallback: mỗi ảnh 1 shot.
    shotlist = params.get("shotlist") or [{"product_idx": i} for i in range(nimg)]
    n = max(1, len(shotlist))
    default_per_shot = max(2.0, min(8.0, target / n))
    has_model = len(m_local) > 0
    # ALD 02/06/2026 - Auto-detect loại đồ từ ẢNH SẢN PHẨM (giống run_tryon) — tránh teaser luôn mặc định
    # 'upper' (vd sản phẩm là GIÀY mà lại đi thay áo cho mẫu). Chỉ cần khi CÓ người mẫu (mới try-on).
    if has_model:
        auto_analyze = params.get("autoAnalyze", params.get("auto_analyze", True))
        if garment in ("", "auto") and auto_analyze is not False and p_local:
            detected = analyze_garment(p_local[0], job_id)
            if detected:
                garment = detected
                api_log(job_id, f"Teaser auto-detect loại đồ = {garment}", "info")
        if not garment or garment == "auto":
            garment = "upper"
            api_log(job_id, "Teaser: không detect được loại đồ → 'upper' (chọn loại thủ công nếu sai)", "warn")
    # motionMode: 'ltx' = LTX-2.3 ảnh+prompt → cảnh quay+audio (chất lượng cao, chậm); 'i2v' = Wan I2V
    # sinh chuyển động THẬT (chậm); 'kenburns' = camera zoom/pan (nhanh, không AI).
    # ALD 04/06/2026 - BỎ Ken Burns (user: pan/zoom ảnh tĩnh vô dụng). Default = Wan I2V (chuyển động AI thật).
    motion_mode = str(params.get("motionMode") or params.get("motion_mode") or "i2v").lower()
    if motion_mode == "kenburns": motion_mode = "i2v"   # ép cũ → i2v (Ken Burns đã gỡ)
    # sceneMode: 'auto' = sinh BỐI CẢNH điện ảnh cho từng shot khi KHÔNG có người mẫu (product-hero,
    # đúng cho teaser kiểu ASICS); 'on' = sinh cả khi có mẫu; 'off' = tắt (chỉ ảnh gốc / try-on).
    scene_mode = str(params.get("sceneMode") or params.get("scene_mode") or "auto").lower().strip()
    if params.get("negativePrompt") and not params.get("negative_prompt"):
        params["negative_prompt"] = str(params.get("negativePrompt") or "").strip()
    scene_ref_template = str(params.get("sceneReferencePrompt") or params.get("scene_reference_prompt") or "").strip()
    i2v_guard_template = str(params.get("i2vGuardPrompt") or params.get("i2v_guard_prompt") or "").strip()
    mlabel = "Wan Animate" if motion_name else {
        "ltx": "LTX-2.3 (AI cảnh quay + audio)", "i2v": "Wan I2V (AI motion)"}.get(motion_mode, "Ken Burns")
    scene_lbl = f" · cảnh ref({len(s_local)})" if scene_mode == "reference" and s_local else (f" · sinh cảnh AI({scene_mode})" if scene_mode != "off" else "")
    api_log(job_id, f"{n} shot · {nimg} ảnh SP · {'try-on (mẫu mặc SP)' if has_model else 'showcase SP'} · chuyển động: {mlabel}{scene_lbl}", "info")
    clips = []
    for i, shot in enumerate(shotlist):
        api_progress(job_id, 0.05 + 0.7 * i / n, f"shot {i+1}/{n}")
        pi = int(shot.get("product_idx", shot.get("productIdx", shot.get("imageIdx", i)))) % nimg
        cap = ((shot.get("caption") or shot.get("text") or "").strip() if show_captions else "")
        sd = shot.get("durationSec", shot.get("duration"))
        dur = max(2.0, min(10.0, float(sd))) if sd else default_per_shot
        scene_prompt = (shot.get("scenePrompt") or shot.get("scene_prompt") or "").strip()
        motion_prompt = (shot.get("motionPrompt") or shot.get("i2vPrompt") or "").strip()
        camera = (shot.get("camera") or shot.get("cameraAngle") or shot.get("camera_angle") or "").strip()
        framing = (shot.get("framing") or shot.get("frame") or shot.get("shotSize") or shot.get("shot_size") or "").strip()
        camera_note = ". ".join([x for x in [camera, framing] if x])
        if camera_note:
            if scene_prompt:
                scene_prompt = f"{scene_prompt}. Camera and framing: {camera_note}."
            motion_prompt = f"{motion_prompt}. Camera and framing: {camera_note}.".strip()
        try:
            if has_model:
                # Có người mẫu → try-on (mẫu mặc sản phẩm), dùng ảnh SP góc pi làm trang phục.
                mi = int(shot.get("model_idx", shot.get("modelIdx", i))) % len(m_local)
                p_up = comfy_upload(p_local[pi]); src = None
                if provider == "gemini" and gem_key:
                    # Gemini try-on cho shot (giỏi giày/vật nhỏ). Lỗi → fallback Qwen.
                    try:
                        with open(m_local[mi], "rb") as f: _mb = f.read()
                        with open(p_local[pi], "rb") as f: _pb = f.read()
                        gout = os.path.join(tmp, f"gtry{i}.png")
                        _gemini_edit([(_mb, _mime(m_local[mi])), (_pb, _mime(p_local[pi]))], _gemini_tryon_prompt(garment), gem_key, gout,
                                     aspect_ratio=_gemini_aspect(_img_size(m_local[mi])))
                        src = gout
                    except Exception as e:
                        api_log(job_id, f"shot {i} Gemini try-on lỗi → Qwen: {e}", "warn")
                if not src and garment in GARMENT_SHOES and TRYON_FEET_DETAILER:
                    # Giày (Qwen) → feet-detailer (crop chân) cho mỗi shot; lỗi → tryon thường.
                    try: src = _run_shoes_detailer(job_id, m_local[mi], p_up, garment, f"teaser-{job_id[:8]}-{i}", tmp)
                    except Exception as e: api_log(job_id, f"shot {i} feet-detailer lỗi → tryon thường: {e}", "warn")
                if not src:
                    pid = comfy_submit(build_qwen_tryon_workflow(comfy_upload(m_local[mi]), p_up, garment, f"teaser-{job_id[:8]}-tryon{i}"))
                    src = comfy_fetch_output(comfy_poll(pid, job_id, deadline_sec=600), exts=IMG_EXTS)
                if not src: continue
            else:
                # Không người mẫu → dùng thẳng ảnh sản phẩm (góc pi) làm shot.
                src = p_local[pi]
            # TẦNG 2A — ẢNH CẢNH THẬT: dùng scene/scene2..N làm reference background để giảm cảm giác AI.
            # image1 = người mẫu đã try-on; image2 = cảnh thật. Prompt chỉ hướng dẫn composite, KHÔNG tự bịa cảnh.
            if s_local and scene_mode in ("reference", "ref", "scene-ref", "scene_reference"):
                try:
                    si = int(shot.get("scene_idx", shot.get("sceneIdx", i))) % len(s_local)
                    ref_prompt = _prompt_template(
                        scene_ref_template,
                        scenePrompt=scene_prompt,
                        camera=camera,
                        framing=framing,
                        motionPrompt=motion_prompt,
                        shotIndex=i + 1,
                        sceneIndex=si + 1,
                    )
                    if not ref_prompt:
                        ref_prompt = scene_prompt or "Composite image 1 person into image 2 environment as a photorealistic fashion frame."
                    api_log(job_id, f"shot {i+1}: ghép vào ảnh cảnh thật {si+1}/{len(s_local)}", "info")
                    sc_pid = comfy_submit(build_qwen_create_workflow(
                        [comfy_upload(src), comfy_upload(s_local[si])], ref_prompt, f"teaser-{job_id[:8]}-scene-ref{i}",
                        width=W, height=H, force_size=True, negative_prompt=params.get("negative_prompt")))
                    scene_img = comfy_fetch_output(comfy_poll(sc_pid, job_id, deadline_sec=600), exts=IMG_EXTS)
                    if scene_img:
                        src = scene_img
                except Exception as e:
                    api_log(job_id, f"shot {i} ghép ảnh cảnh lỗi → giữ ảnh gốc: {e}", "warn")
            # TẦNG 2 — SINH BỐI CẢNH điện ảnh: ảnh nguồn + scenePrompt → cảnh MỚI (Qwen create-image),
            # rồi mới tạo chuyển động. 'auto' chỉ áp cho product-hero (không người mẫu) để giữ nhận diện
            # mẫu khi try-on; 'on' áp cả khi có mẫu. Lỗi → giữ ảnh gốc (KHÔNG crash shot).
            if scene_prompt and scene_mode != "off" and scene_mode not in ("reference", "ref", "scene-ref", "scene_reference") and (scene_mode == "on" or not has_model):
                try:
                    api_log(job_id, f"shot {i+1}: sinh bối cảnh — {scene_prompt[:90]}", "info")
                    sc_pid = comfy_submit(build_qwen_create_workflow(
                        [comfy_upload(src)], scene_prompt, f"teaser-{job_id[:8]}-scene{i}",
                        width=W, height=H, force_size=True, negative_prompt=params.get("negative_prompt")))
                    scene_img = comfy_fetch_output(comfy_poll(sc_pid, job_id, deadline_sec=600), exts=IMG_EXTS)
                    if scene_img:
                        src = scene_img
                except Exception as e:
                    api_log(job_id, f"shot {i} sinh bối cảnh lỗi → ảnh gốc: {e}", "warn")
            # Lookbook (cùng 1 ảnh mẫu) → đa dạng KHUNG mỗi shot cho đỡ nhàm (mặt/váy/toàn thân/cận 3-4).
            if has_model:
                src = _shot_reframe(src, i, tmp)
            clip = os.path.join(tmp, f"clip{i}.mp4")
            made = False
            if motion_name:
                # Có video dẫn động (Wan Animate) — hiếm khi teaser dùng.
                wan_prefix = f"teaser-wan-{job_id[:8]}-{i}"
                wan_mp4 = comfy_fetch_output(comfy_poll(
                    comfy_submit(build_wan_workflow(comfy_upload(src), motion_name, params, prefix=wan_prefix)),
                    job_id, deadline_sec=1800, output_prefix=wan_prefix))
                if wan_mp4:
                    _ff_norm_clip(wan_mp4, clip, W, H, dur, caption=cap); made = True
            elif motion_mode == "ltx":
                # LTX-2.3: ảnh (góc pi / tryon) + prompt → CẢNH QUAY có chuyển động + audio. Prompt mỗi
                # shot: motionPrompt riêng → caption → params.ltxPrompt mặc định. Lỗi (vd chưa cấu hình
                # template) → fallback Ken Burns (KHÔNG crash teaser). Xem build_ltx_i2v_workflow.
                try:
                    F = ltx_frames(dur)
                    sp = (motion_prompt or cap or "").strip()
                    api_log(job_id, f"shot {i+1}: LTX-2.3 {F}f (~{F/LTX_FPS:.1f}s) — sinh cảnh quay", "info")
                    mp4 = comfy_fetch_output(comfy_poll(comfy_submit(build_ltx_i2v_workflow(comfy_upload(src), params, F, prompt=sp, prefix=f"teaser-{job_id[:8]}-ltx{i}")), job_id, deadline_sec=LTX_TIMEOUT))
                    if mp4:
                        _ff_norm_clip(mp4, clip, W, H, dur, caption=cap); made = True
                except Exception as e:
                    api_log(job_id, f"shot {i} LTX lỗi → bỏ shot: {e}", "warn")
            elif motion_mode == "i2v":
                # Sinh CHUYỂN ĐỘNG THẬT từ ảnh (Wan I2V); frames theo thời lượng shot (chuẩn 4n+1).
                try:
                    F = int(round(dur * 16)); F = max(25, min(81, F - (F % 4) + 1))
                    i2v_prompt = (motion_prompt or scene_prompt)
                    guard_prompt = _prompt_template(
                        i2v_guard_template,
                        scenePrompt=scene_prompt,
                        camera=camera,
                        framing=framing,
                        motionPrompt=motion_prompt,
                        shotIndex=i + 1,
                    )
                    if guard_prompt:
                        i2v_prompt = (i2v_prompt + ". " + guard_prompt).strip(". ")
                    api_log(job_id, f"shot {i+1}: Wan I2V {F}f (~{F/16:.1f}s) — sinh chuyển động", "info")
                    mp4 = comfy_fetch_output(comfy_poll(comfy_submit(build_wan_i2v_workflow(comfy_upload(src), params, F, f"teaser-{job_id[:8]}-i2v{i}", prompt=i2v_prompt)), job_id, deadline_sec=1200))
                    if mp4:
                        _ff_norm_clip(mp4, clip, W, H, dur, caption=cap); made = True
                except Exception as e:
                    api_log(job_id, f"shot {i} I2V lỗi → bỏ shot: {e}", "warn")
            if not made:
                # ALD 04/06/2026 - Ken Burns đã gỡ → motion lỗi thì BỎ shot (không rơi về ảnh tĩnh).
                api_log(job_id, f"shot {i+1}: motion lỗi → bỏ shot (Ken Burns đã gỡ)", "warn")
                continue
            if os.path.exists(clip): clips.append(clip)
        except Exception as e:
            api_log(job_id, f"shot {i} lỗi: {e}", "warn")
    if not clips:
        raise RuntimeError("teaser: không tạo được clip nào")
    api_progress(job_id, 0.8, "voiceover (TTS đọc kịch bản)")
    voice = None
    if script:
        try:
            voice = os.path.join(tmp, "voice.mp3")
            _tts(script, voice, params.get("voice") or params.get("voiceId") or DEFAULT_VOICE, gem_key=gem_key)
        except Exception as e:
            api_log(job_id, f"TTS lỗi (bỏ voiceover): {e}", "warn"); voice = None
    music = None
    if music_key:
        try: music = api_download(music_key, os.path.join(tmp, "music" + (os.path.splitext(music_key)[1] or ".mp3")))
        except Exception: music = None
    # ALD 03/06/2026 - 3 PRESET: cùng bộ clip → ghép 3 style → 3 output (FE chọn). 'dynamic' đầu tiên →
    # output_key (FE cũ hiện cái này). multiStyle=false → chỉ 1 video. final=False rồi tự patch done.
    multi = params.get("multiStyle", params.get("multi_style", True)) is not False
    # ALD 04/06/2026 - TikTok làm style CHÍNH (cắt nhanh, transition dồn dập). Giữ thêm dynamic/biến hình.
    presets = ([("tiktok", "TikTok"), ("dynamic", "Năng động"), ("transform", "Biến hình")]
               if multi else [("tiktok", "Video")])
    uploaded = 0
    for vi, (st, label) in enumerate(presets):
        api_progress(job_id, 0.9 + 0.08 * vi / len(presets), f"montage [{label}]")
        out_file = os.path.join(tmp, f"output_{st}.mp4")
        try:
            _teaser_montage(clips, voice, music, out_file, style=st)
            api_upload_output(job_id, out_file, content_type="video/mp4", variant=vi, label=label, final=False)
            uploaded += 1
            api_log(job_id, f"✓ style {label}", "info")
        except Exception as e:
            api_log(job_id, f"montage [{label}] lỗi: {e}", "warn")
    if not uploaded:
        raise RuntimeError("teaser: không ghép được video nào")
    api_patch(job_id, status="done", progress=1, current_step="done")

# #region ALD 05/06/2026 - BDS time-lapse xây nhà (port prototype ~/test/bds_stage2.py + bds_vertical2.py — đã duyệt 04/06).
# Input = 1 ẢNH NHÀ HOÀN THIỆN → output = video time-lapse xây nhà (đất→móng→khung→hoàn thiện) + flycam, dọc 9:16 @60fps.
# Pipeline (8 lần render ComfyUI, ~15-25', tất cả LOCAL/FREE): ESRGAN làm nét ảnh → 3 reverse-edit stage (Qwen-Image-Edit
# "lùi" ảnh nhà về khung/móng/đất, CHẶN máy móc — chỉ công nhân xây tay) → 3 đoạn FLF (Wan 2.1 I2V 480p, start+end image,
# fun_or_fl2v) nối liên tục đất→móng→khung→nhà → flycam i2v ảnh nhà → ffmpeg tua nhanh + concat dọc.
# Models KHÁC node motion: bds dùng Wan 2.1 I2V-14B-480P (default); Wan 2.2 I2V cần download model riêng.
# ALD 09/06/2026 - thêm wan_ver để UI chọn Wan 2.1 / Wan 2.2 I2V.
_BDS_WAN_MODELS = {
    "wan2.1": {
        "model": os.environ.get("WAN_BDS_V21_MODEL", "Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors"),
        "vae":   os.environ.get("WAN_BDS_V21_VAE",   "Wan2_1_VAE_bf16.safetensors"),
        "lora":  os.environ.get("WAN_BDS_V21_LORA",  "lightx2v_I2V_14B_480p_cfg_step_distill_rank32_bf16.safetensors"),
    },
    "wan2.2": {
        # ALD 09/06/2026 - Wan 2.2 I2V A14B dùng kiến trúc dual-model (HIGH noise + LOW noise).
        # ALD 03/07/2026 - thêm cặp LoRA distill 4-step lightx2v (mỗi expert 1 LoRA riêng, xem catalog.json).
        "model_high": os.environ.get("WAN_BDS_V22_MODEL_HIGH", "Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors"),
        "model_low":  os.environ.get("WAN_BDS_V22_MODEL_LOW",  "Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors"),
        "vae":        os.environ.get("WAN_BDS_V22_VAE",        "Wan2_1_VAE_bf16.safetensors"),
        "lora_high":  os.environ.get("WAN_BDS_V22_LORA_HIGH",  WAN22_I2V_LORA_HIGH),
        "lora_low":   os.environ.get("WAN_BDS_V22_LORA_LOW",   WAN22_I2V_LORA_LOW),
    },
}
# ALD 05/06/2026 - tách time-of-day để dùng lại cho cảnh đêm.
# ALD 06/06/2026 - CHÂN THẬT HƠN (#7): bỏ "Photorealistic 3D architectural render style" — chính câu này làm
# output trông như RENDER/CGI chứ không giống nhà thật. Đổi sang phong cách ẢNH CHỤP THẬT (real photograph).
# ALD 06/06/2026 - GIỮ NGUYÊN BỐI CẢNH ẢNH GỐC (đất trống không bị "vẽ lại" thành generic) + ép ẢNH THẬT.
# ALD 09/06/2026 - Bỏ "aerial bird's-eye" khỏi BDS_KEEP: ảnh input là front-view, Qwen bị mâu thuẫn
# → stage 'empty' ra aerial (theo prompt), các stage khác ra front-view (theo ảnh gốc) → camera nhảy góc.
# Fix: dùng "same camera angle as input photo" để Qwen luôn giữ góc của ảnh gốc dù là front/aerial/bất kỳ.
BDS_KEEP_BASE = ("Keep the EXACT same camera viewpoint, angle and distance as the INPUT PHOTO — "
                 "the SAME plot boundary, fence and walls, the SAME neighbouring houses, road, trees, vegetation and "
                 "ground texture, the SAME layout and position; ONLY the house structure itself changes, everything around "
                 "it stays identical to the original. RAW photo on a real DSLR camera, photorealistic, hyper-realistic, "
                 "true-to-life natural daylight, realistic materials, real people, documentary construction photography, "
                 "looks like a real photograph, NOT a 3D render, NOT cartoon")
BDS_KEEP = BDS_KEEP_BASE + ", BRIGHT CLEAR SUNNY DAYTIME, blue sky, strong natural daylight, well-lit, bright exposure."
BDS_KEEP_NIGHT = BDS_KEEP_BASE + ", early evening dusk / blue hour, sky still has soft blue light, warm lights on, clearly visible, NOT pitch black."
# ALD 06/06/2026 - tách tối/ngày: stage ngày CẤM tối (anh Đức báo build ra "hoàng hôn tối"); stage đêm CẤM sáng ngày.
BDS_NEG_DARK = "night, dark, darkness, dusk, sunset, evening, nighttime, low light, gloomy, underexposed, dim, moody lighting, shadowy"
BDS_NEG_DAYLIGHT = "bright daylight, sunny day, blue sky, sunshine, sunlight, overexposed, daytime, morning light, harsh sunlight, midday"
# ALD 06/06/2026 - chặn mạnh render/CGI/hoạt hình (anh Đức báo "như hoạt hình") → đẩy ảnh thật.
BDS_NEG_CGI = ("3d render, cgi, computer graphics, render, unreal engine, video game, cartoon, anime, comic, drawing, painting, "
               "sketch, illustration, cel shading, plastic look, toy, doll, claymation, smooth plastic, oversaturated, fake, artificial, generic")
BDS_NEG_EDIT = ("finished painted house, walls, different angle, blurry, lowres, deformed, watermark, text, " + BDS_NEG_CGI)
# ALD 09/06/2026 - bỏ cấm máy móc: công trình thực tế có xe trộn, cần cẩu nhỏ, máy đào mini → thực tế hơn.
BDS_NEG_VID = ("static, frozen, jump cut, hard cut, scene change, teleport, deformed building, melting, blurry, low quality, watermark, text, " + BDS_NEG_CGI)

# ALD 06/06/2026 - VẬT LIỆU kết cấu — MULTI-SELECT (#4): material là LIST (vd ['steel','concrete']) hoặc str
# ('steel' / 'steel,concrete' — tương thích cũ). Ghép nhiều vật liệu thành 1 mệnh đề khung; chặn GỖ khi KHÔNG chọn gỗ.
BDS_MATERIALS = {
    "steel":    {"label": "thép",             "en": "galvanized steel and iron",
                 "detail": "steel I-beams, steel columns and steel roof trusses bolted with metal brackets, exposed steel framework"},
    "concrete": {"label": "bê tông cốt thép", "en": "reinforced concrete",
                 "detail": "cast concrete columns and beams with steel rebar, a concrete roof slab and grey concrete formwork"},
    "wood":     {"label": "gỗ",               "en": "timber",
                 "detail": "wooden columns, wooden beams and wooden roof trusses"},
}
BDS_WOOD_NEG = "wooden frame, timber columns, wooden beams, wooden roof trusses, log cabin, wood structure, bamboo scaffolding"

def _bds_mats(material):
    """Chuẩn hoá material (str 'steel' / 'steel,concrete' / list) → list id hợp lệ, mặc định ['steel']."""
    if isinstance(material, (list, tuple)):
        ids = [str(m).strip() for m in material]
    else:
        ids = [m.strip() for m in str(material or "").split(",")]
    out = []
    for m in ids:
        if m in BDS_MATERIALS and m not in out: out.append(m)
    return out or ["steel"]
def _bds_mat_label(material):
    return " + ".join(BDS_MATERIALS[m]["label"] for m in _bds_mats(material))
def bds_frame_phrase(material):
    """Mệnh đề mô tả KHUNG kết cấu cho 1 hoặc nhiều vật liệu (ghép '... and ...')."""
    ms = _bds_mats(material)
    if ms == ["wood"]:
        return "a TIMBER structural frame — " + BDS_MATERIALS["wood"]["detail"]
    ens = " and ".join(BDS_MATERIALS[m]["en"] for m in ms)
    details = "; ".join(BDS_MATERIALS[m]["detail"] for m in ms)
    no_wood = "" if "wood" in ms else ", NO wood and NO timber"
    return f"a structural frame of {ens} — {details}{no_wood}"
def bds_neg_edit(material):
    return BDS_NEG_EDIT + ("" if "wood" in _bds_mats(material) else ", " + BDS_WOOD_NEG)
def bds_neg_vid(material):
    return BDS_NEG_VID + ("" if "wood" in _bds_mats(material) else ", " + BDS_WOOD_NEG)

BDS_WK = ("real photorealistic construction workers in hi-vis vests and hard hats actively building by hand, many workers, "
          "scaffolding, concrete mixer truck, bundled steel rebar, stacked bricks and material piles, "
          "wheelbarrows, power tools, construction dust drifting in the air, organized busy activity on site")
# 4 reverse stage: từ ảnh nhà hoàn thiện "lùi" về khung → móng (đổ) → ĐÀO móng → đất trống (chỉ công nhân tay).
# ALD 06/06/2026 - thêm stage 'excavation' (đào móng) — anh Đức hỏi "đào móng, xây móng đâu".
def bds_stages(material):
    f = bds_frame_phrase(material)
    return [
        ("frame",      "CONSTRUCTION SITE photo from the SAME viewpoint as the input: only the bare structural skeleton of the house — " + f + ", NO walls and NO finishes — scaffolding, building materials and MANY CONSTRUCTION WORKERS actively building by hand, construction dust and activity. No machinery. " + BDS_KEEP),
        ("foundation", "EARLY CONSTRUCTION photo from the SAME viewpoint: CONSTRUCTION WORKERS building the concrete foundation — timber formwork, steel rebar grid and freshly poured concrete slab on the bare dirt plot, NO walls yet, NO frame yet, workers and wheelbarrows. No machinery. " + BDS_KEEP),
        ("excavation", "FOUNDATION EXCAVATION photo from the SAME viewpoint: freshly dug foundation trenches and footing pits in the brown soil, steel rebar cages set into the open trenches, mounds of excavated dirt, CONSTRUCTION WORKERS digging by hand with shovels, NO concrete slab yet. No machinery. " + BDS_KEEP),
        ("empty",      "EMPTY cleared dirt construction plot from the SAME viewpoint: bare brown leveled soil, NO house structure at all, just cleared land inside the same boundary walls, a FEW CONSTRUCTION WORKERS surveying and marking the ground with string lines. No machinery. " + BDS_KEEP),
    ]
# 3 đoạn build liên tục (start_key, end_key, prompt). end_key 'finished' = ảnh nhà đã ESRGAN (house_hi).
# ALD 06/06/2026 - CAMERA CHẬM (#6) + GIỮ XA/RỘNG: drone bay CHẬM & ở XA (anh Đức báo cam xây "qua gần") —
# ALD 09/06/2026 - bỏ "aerial" khỏi BDS_WIDE: camera theo góc của ảnh input, không cứng là aerial.
BDS_WIDE = ("wide shot keeping the whole house and the entire plot fully in frame, "
            "generous distance, plenty of space around the house, never a close-up, same viewpoint as the input photo")
def bds_build(material):
    f = bds_frame_phrase(material)
    return [
        ("empty", "excavation",
         f"Construction time-lapse, stable locked camera same angle as the reference house: {BDS_WK} — "
         f"they dig foundation trenches and footing pits in the bare earth, shovels and excavator working, "
         f"mounds of excavated soil pile up steadily, steel rebar cages lowered into the open trenches, "
         f"soil darkens near the edges, sunlight shifts naturally as the work progresses, "
         f"smooth continuous motion, {BDS_WIDE}, no cuts, photorealistic"),
        ("excavation", "foundation",
         f"Construction time-lapse, stable locked camera: {BDS_WK} — "
         f"timber formwork assembled in the trenches, rebar tied and positioned, "
         f"concrete mixer truck arrives and pours fresh wet concrete that slowly fills and levels the slab, "
         f"concrete surface brightens as it cures under the sun, footings solidify into grey concrete, "
         f"smooth steady progress, {BDS_WIDE}, same viewpoint, no cuts, photorealistic"),
        ("foundation", "frame",
         f"Construction time-lapse, stable locked camera: {BDS_WK} — "
         f"they erect {f} rising from the grey concrete base, vertical columns go up first bolted to footings, "
         f"horizontal beams span across, roof trusses lifted and welded into place, "
         f"scaffolding climbs alongside the growing skeleton, cast shadows shift as the structure rises, "
         f"smooth continuous progress, {BDS_WIDE}, same viewpoint, no cuts, photorealistic"),
        ("frame", "finished",
         f"Construction time-lapse, stable locked camera: {BDS_WK} — "
         f"brick walls and render coat fill the structural frame, roof tiles laid and ridge capped, "
         f"window frames and glass fitted, facade smoothed and painted bright white, "
         f"garden soil turned and planted, driveway paved, final details polished — "
         f"gradual transformation from bare skeleton to gleaming finished modern villa, "
         f"smooth continuous progress, very slow gentle pullback, {BDS_WIDE}, same viewpoint, no cuts, photorealistic"),
    ]

# ALD 06/06/2026 - góc quay flycam — thêm nhiều góc khó hơn (#5). auto = dọc cho 9:16, ngang cho 16:9.
BDS_FLY_MOVES = {
    # ALD 10/06/2026 - Mọi move mặc định GIỮ ĐỘ CAO THẤP (eye-level → ngang mái). Wan hay tự drift bay lên cao
    # → frame cuối thành nhìn từ trên xuống rất xấu. Chỉ topdown/front-tilt/doc/fpv/corner được phép lên cao (chủ ý).
    "ngang":    "smoothly orbiting horizontally around the house at a constant ROOFTOP height, the camera stays level the entire time",
    "doc":      "craning straight up in a smooth vertical rise, revealing the house from ground level up to the rooftop",
    "left":     "sweeping in from a 45-degree angle on the LEFT side and gliding leftward around the house at a constant rooftop height",
    "right":    "sweeping in from a 45-degree angle on the RIGHT side and gliding rightward around the house at a constant rooftop height",
    "orbit360": "performing a full smooth 360-degree orbit all the way around the house at a constant rooftop height, camera level the whole time",
    "topdown":  "starting lower then craning high up into a top-down bird's-eye reveal looking straight down at the house and the whole plot",
    "pushin":   "a slow cinematic push-in at eye level, flying forward through the front gate into the garden toward the house, ending close on the beautiful front entrance",
    "fpv":      "a dynamic FPV drone shot diving down and sweeping smoothly around and past the house with energetic motion",
    "corner":   "gliding diagonally from a high front corner down across the house to the opposite far corner",
    "pullback":   "slowly pulling straight back at a CONSTANT low altitude between eye level and rooftop height, the camera stays perfectly level — NOT rising — to reveal the whole house, garden and surroundings in a wide establishing shot",
    # ALD 09/06/2026 - front-tilt: 2 shots liên tiếp — mặt tiền eye-level → tilt 0→45°
    "front-eye":  "gliding slowly straight back directly in front of the house at eye level, beautifully revealing the complete front facade and entrance in a smooth steady pullback, ending settled on a perfect level hero view of the front facade",
    "front-tilt": "starting from eye level directly facing the front facade, slowly craning and tilting upward from 0 to 45 degrees elevation, rising above the roofline to reveal the full house and front yard from a dramatic high angle",
}
# Các góc CHỦ Ý bay cao/dốc — không áp ràng buộc giữ thấp.
BDS_FLY_HIGH = {"topdown", "front-tilt", "doc", "fpv", "corner"}
def bds_fly_prompt(angle, vertical, night=False):
    a = str(angle or "auto")
    if a == "auto":
        a = "pullback"  # auto 1-shot fallback; run_bds auto dùng 2-shot (ngang + front-eye) — xem vid_jobs
    move = BDS_FLY_MOVES.get(a, BDS_FLY_MOVES["ngang"])
    tod = ("warm golden interior lights glowing, deep blue twilight sky, cinematic evening light, consistent dark night, NO daylight"
           if night else "bright natural sunlight, vivid accurate colors, perfect real-estate golden-hour lighting")
    # ALD 10/06/2026 - Chống Wan drift lên cao: ràng buộc độ cao + frame cuối phải là hero view ngang tầm.
    low = ("" if a in BDS_FLY_HIGH else
           " The camera must stay between eye level and rooftop height for the ENTIRE shot — it must NEVER climb into "
           "a high aerial, bird's-eye or top-down view. The FINAL frame ends settled and level, with the house facade "
           "clearly and beautifully visible straight-on.")
    return ("Cinematic 4K aerial drone footage " + move + " — a beautifully finished modern villa, "
            "ultra-sharp detail, perfectly smooth professional DJI drone movement, "
            "luxury real estate showcase, no camera shake, no distortion, photorealistic, "
            "looks like a professional real-estate photograph, " + tod + "." + low)

# ALD 05/06/2026 - cảnh đêm: Qwen đổi ảnh nhà ngày → đêm bật đèn (denoise thấp giữ kết cấu) + Wan FLF ngày→đêm.
BDS_NIGHT_EDIT = ("The SAME finished modern villa, now at DUSK in the EVENING: warm golden interior lights glowing through "
                  "all the windows, garden and facade landscape lighting turned on, deep blue twilight sky, cinematic "
                  "real-estate evening shot. " + BDS_KEEP_NIGHT)
BDS_NIGHT_SEG = ("Time-lapse from the same viewpoint as the input photo: the finished villa transitions from bright daytime to evening dusk, "
                 "warm interior lights and garden lights gradually turn on, the sky darkens to deep blue twilight, "
                 "and STAYS fully dark night at the end, ending at night, no return to daylight, "
                 "smooth continuous transition, stable camera same viewpoint, no cuts, photorealistic")

# ALD 05/06/2026 - chất lượng: tier → (Qwen image steps, Wan video steps). Cao = nét hơn nhưng chậm.
# ALD 09/06/2026 - tăng vid_steps: 4/6/8 quá ít cho lightx2v distilled → blur/fake motion.
# 8/12/16 cho quality rõ rệt hơn; Qwen img_steps giữ nguyên (đủ rồi).
BDS_QUALITY = {"nhanh": (18, 8), "chuan": (25, 12), "cao": (32, 16)}

def build_bds_upscale_workflow(house_name, prefix="bds_house_hi"):
    """ESRGAN RealESRGAN_x4plus làm nét ảnh nhà (flycam mờ nếu không upscale). Output = ảnh.
    ALD 06/06/2026 - FIX 'ComfyUI timeout' ở bước đầu: HẠ ảnh ~0.5MP TRƯỚC khi ×4. Ảnh nhà to (vd 1-scaled.jpg)
    đi thẳng vào ×4 không tiling → tensor khổng lồ → CUDA OOM/treo GPU → ComfyUI không trả → timeout. house_hi
    sau cùng cũng chỉ resize về ~480×832 nên 0.5MP×4=2MP là quá đủ nét. (Bước Qwen ngay sau đã cap 1MP, ESRGAN thì chưa.)"""
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": house_name}},
        "15": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "megapixels": 0.5, "resolution_steps": 1}},
        "20": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.pth"}},
        "30": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["20", 0], "image": ["15", 0]}},
        "100": {"class_type": "SaveImage", "inputs": {"images": ["30", 0], "filename_prefix": prefix}},
    }

# ALD 22/06/2026 - Node ENHANCE/UPSCALE (hậu Wan): upscale VIDEO output 540p → 1080p/2K/4K bằng ESRGAN ×4
# (RealESRGAN_x4plus đã có trên box, bds đang dùng). Chạy như JOB RIÊNG sau khi Wan xả GPU/RAM (comfy_recycle).
# Input đã 540p (~0.46MP/frame) → ×4 trực tiếp ra ~2160p, ImageUpscaleWithModel tự tile 512px → an toàn 32GB
# (KHÁC bds ảnh tĩnh phải pre-downscale 0.5MP vì ảnh nhà to). Giữ audio + fps gốc.
def _video_fps(path):
    """fps video qua ffprobe (r_frame_rate) — None nếu lỗi."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=r_frame_rate", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=20)
        s = (r.stdout or "").strip()
        if "/" in s:
            a, b = s.split("/"); return int(round(float(a) / float(b))) if float(b) else None
        return int(round(float(s))) if s else None
    except Exception:
        return None

def build_video_upscale_workflow(video_name, target_w, target_h, fps, frame_cap=0, prefix="enhance", upscale_model=None):
    # ⚠ ALD 28/06/2026 - KHÔNG CÒN DÙNG cho node enhance (đã chuyển sang ffmpeg lanczos vì pass này
    #   bung CẢ batch ×4 thành 1 tensor CPU → OOM RAM trên video dài/≥720p). Giữ lại cho tham khảo/nếu cần ESRGAN.
    # ALD 22/06/2026 - model upscale tunable. Default 4x-UltraSharp (sắc, đỡ "cartoon/nhựa" so với RealESRGAN_x4plus).
    # Đổi qua env MOTION_UPSCALE_MODEL hoặc param node (4x-UltraSharp / 4x_foolhardy_Remacri / RealESRGAN_x4plus).
    _um = upscale_model or os.environ.get("MOTION_UPSCALE_MODEL", "4x-UltraSharp.pth")
    return {
        "10": {"class_type": "VHS_LoadVideo", "inputs": {"video": video_name, "force_rate": 0, "custom_width": 0,
               "custom_height": 0, "frame_load_cap": int(frame_cap), "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff"}},
        "20": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": _um}},
        "30": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["20", 0], "image": ["10", 0]}},  # ×4 (540→~2160 cạnh ngắn), tự tile 512
        "40": {"class_type": "ImageScale", "inputs": {"image": ["30", 0], "width": int(target_w), "height": int(target_h),
               "upscale_method": "lanczos", "crop": "disabled"}},  # ×4 → co lanczos về đúng đích (giữ AR)
        "110": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["40", 0], "frame_rate": int(fps), "loop_count": 0,
                "filename_prefix": prefix, "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 16,  # ALD 23/06 - crf 19→16: chất lượng cao ở encode GỐC (enhance)
                "pingpong": False, "save_output": True, "audio": ["10", 2]}},  # giữ audio gốc
    }

# #region ALD 06/07/2026 - Node ENHANCE engine SeedVR2 (restoration diffusion 1-STEP, ByteDance-Seed 3B)
# Khác ffmpeg lanczos (chỉ nội suy) — SeedVR2 dựng lại CHI TIẾT thật + nhất quán thời gian (temporal).
# TÙY CHỌN HQ: mặc định Enhance video dùng FlashVSR; bật per-job params.engine='seedvr2' để dùng SeedVR2.
# ⚠ CHỈ hợp clip THẬT MỜ/ĐỘ-PHÂN-GIẢI-THẤP (vd 480p→1080p). Card SeedVR2 cảnh báo: input đã nét → OVERSHARPEN/giả.
# Model+VAE ở models/SEEDVR2 (catalog nhóm 'SeedVR2'); custom node numz/ComfyUI-SeedVR2_VideoUpscaler.
# class_type xác nhận qua /object_info trên box 06/07: SeedVR2LoadDiTModel / SeedVR2LoadVAEModel / SeedVR2VideoUpscaler.
# Knob env: MOTION_SEEDVR2_MODEL (fp16 mặc định — chất lượng tối đa), _BLOCKS_SWAP (0-32, VRAM), _BATCH (phải 4n+1),
#   _TOVERLAP (temporal_overlap chống giật giữa batch), _COLORFIX (wavelet = giữ màu, chống ám), _ATTENTION (sdpa an toàn).
def _gpu_free_gb():
    """VRAM TRỐNG toàn GPU (GB) từ nvidia-smi — phản ánh cả co-tenant (OCR/Ollama), khác ComfyUI /system_stats
    (chỉ thấy phần torch của ComfyUI). None nếu không đọc được."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8).stdout.strip().splitlines()
        return int(out[0].strip()) / 1024.0
    except Exception:
        return None

def _seedvr2_tier(low_vram):
    """Tham số SeedVR2 theo tầng (1 NGUỒN dùng chung cho builder + tính windows tiến độ).
    low_vram = sống-sót khi GPU bận (Chandra-vLLM OCR chiếm VRAM): swap HẾT block, batch 1, VAE tiled, res≤720."""
    if low_vram:
        return {"bswap": 32, "batch": 1, "tover": 0, "swap_io": True, "dec_tiled": True}
    batch = int(os.environ.get("MOTION_SEEDVR2_BATCH", "5"))          # BẮT BUỘC 4n+1 (1,5,9,13…)
    if batch % 4 != 1:
        batch = max(1, (batch // 4) * 4 + 1)
    return {"bswap": int(os.environ.get("MOTION_SEEDVR2_BLOCKS_SWAP", "16")), "batch": batch,
            "tover": int(os.environ.get("MOTION_SEEDVR2_TOVERLAP", "2")), "swap_io": False, "dec_tiled": False}

def _seedvr2_windows(frames, low_vram):
    """Số 'batch' SeedVR2 sẽ in ('Upscaling batch X/N') = số WINDOW cho comfy_poll gộp tiến độ monotonic.
    MỖI batch là 1 mini-sampler reset frac → nếu windows=1, comfy_poll hiểu nhầm reset đầu là 'hậu kỳ' → GHIM 90%.
    SeedVR2 chia frames cuốn chiếu: stride = batch - temporal_overlap → num_batches = ceil((frames-overlap)/stride)."""
    if not frames or frames < 1:
        return 1
    t = _seedvr2_tier(low_vram); stride = max(1, t["batch"] - t["tover"])
    return max(1, math.ceil((frames - t["tover"]) / stride))

def _video_nb_frames(path):
    """Số frame video (nhanh: nb_frames metadata → fallback fps×duration). 0 nếu không đọc được."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=nb_frames,r_frame_rate,duration",
                              "-of", "default=nw=1", path], capture_output=True, text=True, timeout=20).stdout
        nb = 0; fr = 0.0; dur = 0.0
        for ln in out.splitlines():
            k, _, v = ln.partition("="); v = v.strip()
            if k == "nb_frames" and v.isdigit():
                nb = int(v)
            elif k == "r_frame_rate" and "/" in v:
                a, b = v.split("/"); fr = (float(a) / float(b)) if float(b) else 0.0
            elif k == "duration":
                try: dur = float(v)
                except Exception: pass
        if nb > 0:
            return nb
        return int(round(fr * dur)) if fr > 0 and dur > 0 else 0
    except Exception:
        return 0

def build_seedvr2_upscale_workflow(video_name, short_side, fps, frame_cap=0, prefix="enh-sr", low_vram=False):
    # low_vram=True: chế độ sống-sót khi GPU bị co-tenant (Chandra-vLLM OCR) chiếm VRAM — swap TẤT CẢ block (32),
    #   batch 1, VAE decode tiled, res kẹp ≤720. Chậm hơn + ít mượt temporal nhưng KHÔNG OOM (đã kiểm 06/07 ~5GB trống).
    model = os.environ.get("MOTION_SEEDVR2_MODEL", "seedvr2_ema_3b_fp16.safetensors")
    attn = os.environ.get("MOTION_SEEDVR2_ATTENTION", "sdpa")         # sdpa an toàn; sageattn_2/flash_attn tùy build
    cfix = os.environ.get("MOTION_SEEDVR2_COLORFIX", "wavelet")       # wavelet giữ phân bố màu gốc (chống drift/ám)
    t = _seedvr2_tier(low_vram)
    bswap, batch, tover, swap_io, dec_tiled = t["bswap"], t["batch"], t["tover"], t["swap_io"], t["dec_tiled"]
    if low_vram:
        short_side = min(int(short_side), 720)
    vae_in = {"model": "ema_vae_fp16.safetensors", "device": "cuda:0"}
    if dec_tiled:
        vae_in.update({"decode_tiled": True, "decode_tile_size": 512})
    return {
        "10": {"class_type": "VHS_LoadVideo", "inputs": {"video": video_name, "force_rate": 0, "custom_width": 0,
               "custom_height": 0, "frame_load_cap": int(frame_cap), "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff"}},
        # BlockSwap>0 BẮT BUỘC offload_device='cpu' (khác device) — node ValueError nếu để 'none'.
        "20": {"class_type": "SeedVR2LoadDiTModel", "inputs": {"model": model, "device": "cuda:0",
               "blocks_to_swap": bswap, "swap_io_components": swap_io, "attention_mode": attn,
               "offload_device": ("cpu" if bswap > 0 else "none")}},
        "21": {"class_type": "SeedVR2LoadVAEModel", "inputs": vae_in},
        "30": {"class_type": "SeedVR2VideoUpscaler", "inputs": {
               "image": ["10", 0], "dit": ["20", 0], "vae": ["21", 0], "seed": 42,
               "resolution": int(short_side), "max_resolution": 0, "batch_size": int(batch),
               "uniform_batch_size": False, "color_correction": cfix, "temporal_overlap": int(tover)}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["30", 0], "frame_rate": int(fps), "loop_count": 0,
                "filename_prefix": prefix, "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 16,
                "pingpong": False, "save_output": True, "audio": ["10", 2]}},  # giữ audio gốc
    }
# #endregion

# #region ALD 08/07/2026 - ENGINE FlashVSR (video super-res AI 1-step, Wan2.1-1.3B; nhanh ~10× SeedVR2 ở res cao,
# hợp box share-GPU hơn). Node: naxci1/ComfyUI-FlashVSR_Stable (bọc lihaoyun6, có OOM-fallback + tiling).
# class_type XÁC NHẬN từ nodes.py: FlashVSRInitPipe (→PIPE) → FlashVSRNodeAdv (frames IMAGE → image).
# Out = input × resize_factor × scale (vd 544 × 0.7 × 4 = 1523 → ffmpeg conform về đúng target sau).
# ⚠ Model v1.1 ở ComfyUI/models/FlashVSR-v1.1/ (LQ_proj_in.ckpt, TCDecoder.ckpt,
#   diffusion_..._streaming_dmd.safetensors, Wan2.2_VAE.pth). Catalog cho phép cài đủ cả nhóm.
#   attention mặc định sparse_sage_attention; ép MOTION_FLASHVSR_ATTENTION=sdpa nếu box chưa có SageAttention.
def build_flashvsr_upscale_workflow(video_name, scale, resize_factor, fps, frame_cap=0, prefix="enh-fv", low_vram=False):
    model = os.environ.get("MOTION_FLASHVSR_MODEL", "FlashVSR-v1.1")
    mode = os.environ.get("MOTION_FLASHVSR_MODE", "tiny-long" if low_vram else "tiny")   # tiny-long: stream, VRAM thấp
    vae_model = os.environ.get("MOTION_FLASHVSR_VAE", "Wan2.2")
    # ALD 24/07/2026 - locality-constrained sparse attention là đường chuẩn của FlashVSR Stable:
    # nhanh hơn dense SDPA và giữ chi tiết temporal tốt hơn; có thể ép lại sdpa bằng env khi box chưa có SageAttention.
    attn = os.environ.get("MOTION_FLASHVSR_ATTENTION", "sparse_sage_attention")
    precision = os.environ.get("MOTION_FLASHVSR_PRECISION", "bf16")     # bf16 khuyên cho RTX 3000/4000/5000
    device = os.environ.get("MOTION_FLASHVSR_DEVICE", "cuda:0")
    # Không tile ở tầng thường vì tile VAE/DiT có thể tạo dải mảnh trên nền/da. Nhánh retry low_vram
    # vẫn tự bật tile để cứu OOM.
    _tiled = low_vram or str(os.environ.get("MOTION_FLASHVSR_TILED", "0")).strip().lower() in ("1", "true", "yes", "on")
    _tsize = int(os.environ.get("MOTION_FLASHVSR_TILE_SIZE", "256"))
    _tover = int(os.environ.get("MOTION_FLASHVSR_TILE_OVERLAP", "24"))
    # 100 frame giữ peak VRAM ổn định cho video 10–20s; clip ngắn hơn vẫn đi nguyên khối.
    _chunk = int(os.environ.get("MOTION_FLASHVSR_CHUNK", "100"))
    return {
        "10": {"class_type": "VHS_LoadVideo", "inputs": {"video": video_name, "force_rate": 0, "custom_width": 0,
               "custom_height": 0, "frame_load_cap": int(frame_cap), "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff"}},
        "20": {"class_type": "FlashVSRInitPipe", "inputs": {"model": model, "mode": mode, "vae_model": vae_model,
               "force_offload": True, "precision": precision, "device": device, "attention_mode": attn}},
        "30": {"class_type": "FlashVSRNodeAdv", "inputs": {
               "pipe": ["20", 0], "frames": ["10", 0], "scale": int(scale), "color_fix": True,
               "tiled_vae": bool(_tiled), "tiled_dit": bool(_tiled), "tile_size": _tsize, "tile_overlap": _tover,
               "unload_dit": True, "sparse_ratio": 2.0, "kv_ratio": 3.0, "local_range": 11, "seed": 42,
               "frame_chunk_size": _chunk, "enable_debug": False, "keep_models_on_cpu": True, "resize_factor": float(resize_factor)}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["30", 0], "frame_rate": int(fps), "loop_count": 0,
                "filename_prefix": prefix, "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 16,
                "pingpong": False, "save_output": True, "audio": ["10", 2]}},  # giữ audio gốc
    }
# #endregion

def build_bds_reverse_workflow(house_name, prompt, prefix, seed=7, steps=25, neg=None, denoise=1.0):
    """Qwen-Image-Edit-2509 'lùi' ảnh nhà về 1 giai đoạn thi công. denoise=1.0 (đổi hẳn) / thấp (giữ kết cấu, vd cảnh đêm). Output = ảnh."""
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": house_name}},
        "20": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}},
        "30": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "Qwen-Image-Edit-2509-Q8_0.gguf"}},
        "31": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image"}},
        "32": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "40": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": prompt, "vae": ["32", 0], "image1": ["20", 0]}},
        "41": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["31", 0], "prompt": (neg or BDS_NEG_EDIT), "vae": ["32", 0], "image1": ["20", 0]}},
        "42": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["32", 0]}},
        "50": {"class_type": "KSampler", "inputs": {"model": ["30", 0], "positive": ["40", 0], "negative": ["41", 0], "latent_image": ["42", 0], "seed": int(seed), "steps": int(steps), "cfg": 3.0, "sampler_name": "euler", "scheduler": "simple", "denoise": float(denoise)}},
        "60": {"class_type": "VAEDecode", "inputs": {"samples": ["50", 0], "vae": ["32", 0]}},
        "100": {"class_type": "SaveImage", "inputs": {"images": ["60", 0], "filename_prefix": prefix}},
    }

def build_bds_segment_workflow(start_name, end_name, prompt, W, H, F, S, prefix, seed=42, neg=None, rife_mult=4, wan_ver="wan2.1", noise_aug=0.025,
                               match_ref=False, match_ref_method="mkl", match_ref_strength=0.85, params=None):
    """Wan I2V FLF segment: start_image→end_image (hoặc i2v 1 ảnh nếu end_name=None).
    wan2.1: single-model + lightx2v LoRA.
    wan2.2: dual-model HIGH→LOW (MoE) + LoRA distill 4-step lightx2v/Wan2.2-Distill-Loras (ALD 03/07/2026 —
      trước đây chạy KHÔNG LoRA nên cần 10+ bước mà cfg lại để 1.0 → vừa chậm vừa dễ cháy sáng; giờ 4 bước chuẩn distill).
    match_ref=True → ColorMatch (mkl) kéo màu/sáng output VỀ ẢNH GỐC — chống CHÁY SÁNG kiểu Motion node
      (bài học 21-22/06: mkl trị cháy da/ám màu, strength 0.85 giữ độ tươi; 1.0 gây bệt). Với I2V thì ảnh ref
      CHÍNH LÀ frame đầu nên match rất an toàn (khác motion driver-lệch-màu).
    Clip >81f tự bật RIFLEx (chống slow-motion — bài học 28/06). RIFE nội suy → VHS combine. Output = mp4."""
    rife_mult = int(rife_mult)
    _wm = _BDS_WAN_MODELS.get(wan_ver, _BDS_WAN_MODELS["wan2.1"])
    RK = lambda src: {"class_type": "ImageResizeKJv2", "inputs": {
        "image": [src, 0], "width": W, "height": H, "upscale_method": "lanczos", "keep_proportion": "crop",
        "pad_color": "0, 0, 0", "crop_position": "center", "divisible_by": 16, "device": "cpu"}}
    # Shared: image load, CLIP vision, text encode, VAE, RIFE, VHS
    g = {
        "10": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "11": RK("10"),
        "50": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": _wm["vae"], "precision": "bf16"}},
        "60": {"class_type": "WanVideoTextEncodeCached", "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16", "positive_prompt": prompt,
            "negative_prompt": (neg or BDS_NEG_VID), "quantization": "disabled", "use_disk_cache": MOTION_T5_DISK_CACHE, "device": "gpu"}},
        "70": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "71": {"class_type": "WanVideoClipVisionEncode", "inputs": {
            "clip_vision": ["70", 0], "image_1": ["11", 0], "strength_1": 1.0, "strength_2": 1.0,
            "crop": "center", "combine_embeds": "average", "force_offload": True, "tiles": 0, "ratio": 0.5}},
        "105": {"class_type": "RIFE VFI", "inputs": {
            "frames": ["100", 0], "ckpt_name": "rife47.pth", "clear_cache_after_n_frames": 10, "multiplier": rife_mult,
            "fast_mode": True, "ensemble": True, "scale_factor": 1.0, "dtype": "float32", "torch_compile": False, "batch_size": 1}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["105", 0], "frame_rate": 16 * rife_mult, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }
    _rfx = _riflex_index(F)   # >81f (~5s @16fps) → RIFLEx idx 4 chống slow-motion
    if wan_ver == "wan2.2":
        # ALD 09/06/2026 - Wan 2.2 I2V A14B: dual-model MoE — HIGH xử lý half đầu, LOW tiếp half cuối.
        # quantization fp8_e4m3fn_scaled (bắt buộc cho A14B scaled weights).
        # ALD 03/07/2026 - đeo LoRA distill 4-step (lightx2v) cho TỪNG expert (high_noise/low_noise riêng,
        # KHÔNG hoán đổi) → 4 bước, cfg 1.0 đúng chuẩn distill. Strength qua env WAN22_LORA_STRENGTH (1.0).
        S_high = min(max(int(S) // 2, 1), max(int(S) - 1, 1))  # bước HIGH model (nửa đầu denoising)
        g["40"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": _wm.get("lora_high", WAN22_I2V_LORA_HIGH), "strength_0": WAN22_LORA_STRENGTH,
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0, "lora_3": "none", "strength_3": 1.0,
            "lora_4": "none", "strength_4": 1.0, "low_mem_load": False, "merge_loras": True}}
        g["44"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": _wm.get("lora_low", WAN22_I2V_LORA_LOW), "strength_0": WAN22_LORA_STRENGTH,
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0, "lora_3": "none", "strength_3": 1.0,
            "lora_4": "none", "strength_4": 1.0, "low_mem_load": False, "merge_loras": True}}
        g["41"] = {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": 20, "offload_img_emb": False, "offload_txt_emb": False, "use_non_blocking": True,
            "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}}
        g["42"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": _wm["model_high"], "base_precision": "fp16_fast", "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params), "lora": ["40", 0], "block_swap_args": ["41", 0]}}
        g["43"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": _wm["model_low"], "base_precision": "fp16_fast", "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params), "lora": ["44", 0], "block_swap_args": ["41", 0]}}
        g["90"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": int(S), "cfg": 1.0, "shift": 5.0, "seed": int(seed),
            "force_offload": True, "scheduler": "dpm++_sde", "riflex_freq_index": _rfx, "text_embeds": ["60", 0],
            "rope_function": "comfy", "start_step": 0, "end_step": S_high}}
        g["91"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["43", 0], "image_embeds": ["80", 0], "steps": int(S), "cfg": 1.0, "shift": 5.0, "seed": int(seed),
            "force_offload": True, "scheduler": "dpm++_sde", "riflex_freq_index": _rfx, "text_embeds": ["60", 0],
            "rope_function": "comfy", "samples": ["90", 0], "start_step": S_high, "end_step": -1}}
        g["100"] = {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["91", 0], "enable_vae_tiling": True, "tile_x": 272, "tile_y": 272,
            "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default"}}
    else:
        # wan2.1: single-model + lightx2v LoRA distilled
        g["40"] = {"class_type": "WanVideoLoraSelectMulti", "inputs": {
            "lora_0": _wm["lora"], "strength_0": 1.0,
            "lora_1": "none", "strength_1": 1.0, "lora_2": "none", "strength_2": 1.0, "lora_3": "none", "strength_3": 1.0,
            "lora_4": "none", "strength_4": 1.0, "low_mem_load": False, "merge_loras": True}}
        g["41"] = {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": 20, "offload_img_emb": False, "offload_txt_emb": False, "use_non_blocking": True,
            "vace_blocks_to_swap": 0, "prefetch_blocks": 2, "block_swap_debug": False}}
        g["42"] = {"class_type": "WanVideoModelLoader", "inputs": {
            "model": _wm["model"], "base_precision": "fp16_fast", "quantization": "disabled",
            "load_device": "offload_device", "attention_mode": _wan_attention(params), "lora": ["40", 0], "block_swap_args": ["41", 0]}}
        g["90"] = {"class_type": "WanVideoSampler", "inputs": {
            "model": ["42", 0], "image_embeds": ["80", 0], "steps": int(S), "cfg": 1.0, "shift": 5.0, "seed": int(seed),
            "force_offload": True, "scheduler": "dpm++_sde", "riflex_freq_index": _rfx, "text_embeds": ["60", 0], "rope_function": "comfy"}}
        g["100"] = {"class_type": "WanVideoDecode", "inputs": {
            "vae": ["50", 0], "samples": ["90", 0], "enable_vae_tiling": True, "tile_x": 272, "tile_y": 272,
            "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default"}}
    # ALD 03/07/2026 - CHỐNG CHÁY SÁNG: ColorMatch kéo màu + độ sáng của output về ẢNH GỐC (node "11" =
    # ảnh start đã resize — với I2V đây chính là frame đầu nên khớp tuyệt đối). Học từ Motion node (mkl,
    # strength 0.85 giữ độ tươi). Chèn giữa decode(100) và RIFE(105)/VHS(110).
    frames_src = "100"
    if match_ref:
        g["106"] = {"class_type": "ColorMatch", "inputs": {
            "image_ref": ["11", 0], "image_target": ["100", 0],
            "method": str(match_ref_method or "mkl"), "strength": float(match_ref_strength), "multithread": True}}
        frames_src = "106"
        g["105"]["inputs"]["frames"] = ["106", 0]
    if rife_mult <= 1:
        g.pop("105", None)
        g["110"]["inputs"]["images"] = [frames_src, 0]
        g["110"]["inputs"]["frame_rate"] = 16
    enc = {"width": W, "height": H, "num_frames": int(F), "noise_aug_strength": float(noise_aug), "start_latent_strength": 1.0,
           "end_latent_strength": 1.0, "force_offload": True, "vae": ["50", 0], "clip_embeds": ["71", 0],
           "start_image": ["11", 0], "fun_or_fl2v_model": True}
    if end_name:
        g["12"] = {"class_type": "LoadImage", "inputs": {"image": end_name}}
        g["13"] = RK("12")
        enc["end_image"] = ["13", 0]
    g["80"] = {"class_type": "WanVideoImageToVideoEncode", "inputs": enc}
    return g

# ALD 06/06/2026 - TỈ LỆ video #9: nhiều tỉ lệ + 'auto' khớp ảnh nhà upload. Short side 480 (native Wan 480p),
# long side scale theo tỉ lệ, CAP ≤ 832 (giữ VRAM an toàn như 16:9 cũ). 'auto' đọc kích thước ảnh thật bằng ffprobe.
_BDS_ASPECT = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5), "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9)}
def _img_dims(path):
    """(width, height) của ảnh qua ffprobe — (None, None) nếu lỗi."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                           capture_output=True, text=True, timeout=20)
        w, h = r.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None, None
def _bds_resolve_wh(params, house_local, short=480, cap_long=832):
    ar = str(params.get("aspectRatio", "auto") or "auto")
    if ar == "auto":
        w, h = _img_dims(house_local)
        rw, rh = (w, h) if (w and h) else (9, 16)
    else:
        rw, rh = _BDS_ASPECT.get(ar, (9, 16))
    if rw <= rh:   # dọc/vuông: short = bề ngang
        W, H = short, min(cap_long, int(round(short * rh / rw)))
    else:          # ngang: short = chiều cao
        H, W = short, min(cap_long, int(round(short * rw / rh)))
    return _even16(W), _even16(H)


# ALD 06/06/2026 - CHUYỂN CẢNH MƯỢT #2: thay concat cứng bằng xfade crossfade giữa các đoạn (+ intro→build),
# hết "flash". Clip đã chuẩn hoá cùng W×H/fps/yuv420p (no audio). Lỗi → fallback concat cứng. Kèm grade nhẹ (#7).
def _xfade_concat(clips, out_path, fps, dur=1.2, grade=True, transition="fade", transitions=None):
    # transitions[j] = transition từ clip j sang clip j+1 (len = len(clips)-1); fallback về `transition`.
    clips = [c for c in clips if c and os.path.exists(c)]
    if not clips:
        return None
    # Grade tốt hơn: contrast nhẹ + saturation rõ màu + unsharp tăng nét nhẹ
    eq = ",eq=contrast=1.05:saturation=1.10:gamma=0.97,unsharp=3:3:0.4" if grade else ""
    if transitions is None:
        transitions = [transition] * max(0, len(clips) - 1)
    else:
        transitions = list(transitions)
        if len(transitions) < len(clips) - 1:
            transitions += [transition] * (len(clips) - 1 - len(transitions))
    if len(clips) == 1:
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", clips[0], "-vf",
                            f"format=yuv420p{eq}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
                           check=True, capture_output=True, timeout=600)
            return out_path
        except Exception as e:
            log("xfade single-clip fail:", e)
    durs = [_audio_dur(c) for c in clips]
    if all(d and d > 0 for d in durs) and len(clips) >= 2:
        D = max(0.15, min(dur, min(durs) / 2.0))
        parts = [f"[{i}:v]fps={fps},format=yuv420p,setsar=1,settb=AVTB[c{i}]" for i in range(len(clips))]
        prev = "c0"
        for j in range(1, len(clips)):
            off = sum(durs[:j]) - j * D
            out = f"x{j}" if j < len(clips) - 1 else "xf"
            tr_j = transitions[j - 1] if j - 1 < len(transitions) else "fade"
            parts.append(f"[{prev}][c{j}]xfade=transition={tr_j}:duration={D:.3f}:offset={max(0.0, off):.3f}[{out}]")
            prev = out
        last = "xf" if len(clips) >= 2 else "c0"
        parts.append(f"[{last}]format=yuv420p{eq}[vout]")
        fc = ";".join(parts)
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
        for c in clips: cmd += ["-i", c]
        cmd += ["-filter_complex", fc, "-map", "[vout]", "-r", str(fps), "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=900)
            return out_path
        except Exception as e:
            log("xfade concat fail → fallback hard concat:", e)
    # Fallback: concat cứng (vẫn grade) khi thiếu duration hoặc xfade lỗi.
    listf = os.path.splitext(out_path)[0] + ".list.txt"
    with open(listf, "w") as f:
        f.write("\n".join(f"file '{c}'" for c in clips))
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf,
                        "-vf", f"format=yuv420p{eq}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
                       check=True, capture_output=True, timeout=900)
        return out_path
    except Exception as e:
        log("hard concat fail:", e); return None

def run_bds(job):
    import subprocess
    job_id = job["id"]; inputs = job.get("inputs", {}); params = dict(job.get("params", {}) or {})
    house_key = inputs.get("house") or inputs.get("input") or inputs.get("image") or inputs.get("ref") or inputs.get("default")
    if not house_key:
        raise RuntimeError("bds cần 1 ẢNH NHÀ HOÀN THIỆN (nối Input Image vào input 'Ảnh nhà')")
    # ALD 06/06/2026 - params Inspector (material giờ là LIST #4; aspectRatio có 'auto'+nhiều tỉ lệ #9).
    F = int(params.get("frames", 113) or 113)
    seed = int(params.get("seed", 42) or 42)
    build_speed = float(params.get("buildSpeed", 1.0) or 1.0)   # ALD 09/06/2026 - 1.3→1.0: clip dài hơn, ít looping
    fly_speed = float(params.get("flySpeed", 1.0) or 1.0)
    material = params.get("material") or "steel"
    fly_angle = str(params.get("flyAngle", "auto") or "auto")
    # ALD 10/06/2026 - shotMode: "1-shot" = dissolve 3s (1 cảnh liên tục trực diện) + tự dùng front-tilt flycam.
    shot_mode = str(params.get("shotMode", "multi") or "multi")
    if shot_mode == "1-shot":
        xfade_dur   = 3.0
        xfade_tr    = "fade"
        if fly_angle == "auto":
            fly_angle = "front-tilt"   # ép flycam trực diện khi 1-shot
    else:
        xfade_dur   = 1.5
        xfade_tr    = "fade"
    quality = str(params.get("quality", "chuan") or "chuan")
    img_steps, vid_steps = BDS_QUALITY.get(quality, BDS_QUALITY["chuan"])
    night = str(params.get("nightScene", "1")).lower() in ("1", "true", "yes", "on")
    wan_ver = str(params.get("wanModel", "wan2.1") or "wan2.1")
    # ALD 09/06/2026 - runtime check: tự động mở khóa wan2.2 khi cả 2 model file đã có trên disk.
    # ALD 02/07/2026 - đọc từ COMFY_MODELS_DIR (.env do setup ghi) thay hardcode /home/ubuntu/ai/... — path cũ
    # sai cả trên box PM2-native (ComfyUI ở ~/ComfyUI) khiến check wan2.2 luôn False; fallback expanduser.
    _MODELS_ROOT = os.environ.get("COMFY_MODELS_DIR", os.path.expanduser("~/ComfyUI/models"))
    _DIFFUSION_DIR = os.path.join(_MODELS_ROOT, "diffusion_models")
    _wm22 = _BDS_WAN_MODELS["wan2.2"]
    # ALD 03/07/2026 - graph wan2.2 giờ ĐEO LoRA distill (lora_high/lora_low) → check sẵn-sàng phải gồm cả
    # 2 file LoRA trong loras/ (thiếu → ComfyUI 400 "lora_0 not in list" và fallback wan2.1 không bao giờ chạy).
    _LORAS_DIR = os.path.join(_MODELS_ROOT, "loras")
    _wan22_ready = (
        os.path.isfile(os.path.join(_DIFFUSION_DIR, _wm22["model_high"])) and
        os.path.isfile(os.path.join(_DIFFUSION_DIR, _wm22["model_low"])) and
        os.path.isfile(os.path.join(_LORAS_DIR, _wm22["lora_high"])) and
        os.path.isfile(os.path.join(_LORAS_DIR, _wm22["lora_low"]))
    )
    _WAN_BDS_AVAILABLE = {"wan2.1", "wan2.2"} if _wan22_ready else {"wan2.1"}
    if wan_ver not in _WAN_BDS_AVAILABLE:
        log(f"[bds] wanModel={wan_ver!r} chưa sẵn sàng (model/LoRA distill chưa download — Settings → Models AI), fallback wan2.1")
        wan_ver = "wan2.1"
    fps_target = int(params.get("fps", 60) or 60)
    rife_mult = 4 if fps_target >= 60 else 2
    out_fps = 60 if fps_target >= 60 else 30
    neg_edit = bds_neg_edit(material); neg_vid = bds_neg_vid(material)
    # ALD 06/06/2026 - tách negative NGÀY/ĐÊM: stage ngày CẤM tối (build ra "hoàng hôn tối" = xấu); stage đêm CẤM
    # sáng ban ngày (chống flash về day ở shot cuối).
    neg_edit_day = neg_edit + ", " + BDS_NEG_DARK
    neg_edit_night = neg_edit + ", " + BDS_NEG_DAYLIGHT
    neg_vid_day = neg_vid + ", " + BDS_NEG_DARK
    neg_night = neg_vid + ", " + BDS_NEG_DAYLIGHT
    stages = bds_stages(material); builds = bds_build(material)

    tmp = tempfile.mkdtemp(prefix=f"bds-{job_id[:8]}-")
    api_progress(job_id, 0.02, "tải ảnh nhà")
    house_local = api_download(house_key, os.path.join(tmp, "house" + (os.path.splitext(house_key)[1] or ".jpg")))
    house_name = comfy_upload(house_local)
    W, H = _bds_resolve_wh(params, house_local)   # #9 'auto' khớp tỉ lệ ảnh nhà thật
    vertical = H >= W
    api_log(job_id, f"bds: vật liệu={_bds_mat_label(material)} · {W}x{H} · {F}f · {out_fps}fps · chất lượng={quality} · đêm={'có' if night else 'không'} · flycam={fly_angle}", "info")

    # 1) ESRGAN làm nét → house_hi (dùng cho stage 'finished', cảnh đêm + flycam)
    api_progress(job_id, 0.04, "ESRGAN làm nét ảnh nhà")
    up_out = comfy_poll(comfy_submit(build_bds_upscale_workflow(house_name)), job_id,
                        deadline_sec=600, prog_lo=0.04, prog_hi=0.08, prog_step="ESRGAN làm nét")
    house_hi_local = comfy_fetch_output(up_out, exts=(".png", ".jpg", ".jpeg"))
    if not house_hi_local:
        api_log(job_id, "ESRGAN không trả ảnh — dùng ảnh gốc", "warn"); house_hi_local = house_local
    house_hi_name = comfy_upload(house_hi_local)

    # 2) Ảnh giai đoạn (Qwen) — CHAIN từng bước: ảnh nhà gốc → khung → móng → đất. ALD 06/06/2026: trước đây
    # mỗi stage "vẽ lại từ ảnh gốc" độc lập (denoise 1.0) nên đất trống bị BỊA generic, mất bối cảnh ảnh gốc.
    # Giờ mỗi bước chỉ GỠ 1 lớp từ ảnh bước trước (denoise 0.9 giữ thêm kết cấu) → giữ nguyên đất/hàng xóm/cây,
    # nhất quán giữa các stage. + cảnh đêm (từ ảnh nhà hoàn thiện, denoise 0.6).
    names = {"finished": house_hi_name}
    IMG_LO, IMG_HI = 0.08, 0.28
    n_img = len(stages) + (1 if night else 0)
    istep = (IMG_HI - IMG_LO) / max(1, n_img)
    prev_src = house_name   # chain bắt đầu từ ảnh nhà gốc
    for i, (key, prompt) in enumerate(stages):
        lo = IMG_LO + i * istep; hi = lo + istep
        label = f"giai đoạn: {key}"
        api_progress(job_id, lo, f"dựng {label}")
        # ALD 09/06/2026 - stage "empty": dùng ảnh gốc (không chain) + denoise=0.95 → giữ góc camera
        # nhưng xóa toàn bộ công trình. Các stage khác 0.93 (tăng từ 0.9) để thay đổi rõ hơn mỗi stage.
        if key == "empty":
            stage_src, stage_denoise = house_name, 0.97   # chain-break: đất không có nhà, cần deviation lớn nhưng giữ góc
        else:
            stage_src, stage_denoise = prev_src, 0.96     # ALD 09/06/2026: 0.93→0.96 → các stage trung gian khác biệt rõ hơn
        out = comfy_poll(comfy_submit(build_bds_reverse_workflow(stage_src, prompt, f"bds_{key}", seed, steps=img_steps, neg=neg_edit_day, denoise=stage_denoise)),
                         job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step=label)
        png = comfy_fetch_output(out, exts=(".png", ".jpg", ".jpeg"))
        if not png:
            raise RuntimeError(f"bds: giai đoạn '{key}' không trả ảnh")
        names[key] = comfy_upload(png)
        prev_src = names[key]
        api_preview(job_id, png, f"Giai đoạn: {label}")
    if night:
        lo = IMG_LO + len(stages) * istep; hi = lo + istep
        api_progress(job_id, lo, "dựng cảnh đêm bật đèn")
        out = comfy_poll(comfy_submit(build_bds_reverse_workflow(house_hi_name, BDS_NIGHT_EDIT, "bds_night", seed, steps=img_steps, neg=neg_edit_night, denoise=0.6)),
                         job_id, deadline_sec=600, prog_lo=lo, prog_hi=hi, prog_step="cảnh đêm bật đèn")
        png = comfy_fetch_output(out, exts=(".png", ".jpg", ".jpeg"))
        if png:
            names["night"] = comfy_upload(png); api_preview(job_id, png, "Giai đoạn: cảnh đêm bật đèn")
        else:
            api_log(job_id, "cảnh đêm lỗi — bỏ qua, dùng ảnh ngày", "warn")
    has_night = night and ("night" in names)

    # 3) Video #3: build×3 (đất→móng→khung→nhà) → flycam NGÀY → (ngày→đêm → flycam ĐÊM là shot CUỐI).
    # ALD 10/06/2026 - Per-segment settings: build dùng RIFE×2 + F_build (nhanh hơn ~30-40%); flycam/night dùng RIFE×4 đầy đủ.
    build_rife_mult = 2
    fly_rife_mult   = rife_mult
    F_build         = min(F, 81)   # cap frames build-segments (~28% nhanh hơn Wan sampler, motion chậm không cần thêm)

    # neg riêng: đoạn đêm dùng neg_night (cấm daylight) để KHÔNG flash về sáng.
    # Tuple: (sn, en, prompt, prefix, speed, label, neg, tr, seg_rife, seg_F, seg_noise)
    # tr = transition TỪ clip này SANG clip tiếp theo (clip cuối bỏ qua).
    n_builds = len(builds)
    vid_jobs = []
    for i, (start_key, end_key, prompt) in enumerate(builds):
        tr = "fadewhite" if i == n_builds - 1 else "fade"   # reveal ngôi nhà hoàn thiện = fadewhite
        vid_jobs.append((names[start_key], names[end_key], prompt, f"bds_seg{i}",
                         build_speed, f"build {start_key}→{end_key}", neg_vid_day,
                         tr, build_rife_mult, F_build, 0.025))
    # ALD 09/06/2026 - "front-tilt": 2 shots liên tiếp (eye-level → tilt 0→45°).
    # ALD 10/06/2026 - "auto": bố cục 2 shot chuẩn video BĐS — (1) orbit ngang quanh nhà ở độ cao ngang mái khoe
    # toàn cảnh + sân vườn, (2) KẾT hero shot: lùi chậm trực diện mặt tiền eye-level → frame CUỐI là mặt tiền nhà
    # hoàn chỉnh ngang tầm mắt, KHÔNG bao giờ kết bằng góc nhìn từ trên cao xuống (xấu).
    if fly_angle == "front-tilt":
        vid_jobs.append((house_hi_name, None, bds_fly_prompt("front-eye",  vertical, night=False), "bds_flyday",
                         fly_speed, "flycam trực diện", neg_vid_day, "fade", fly_rife_mult, F, 0.01))
        vid_jobs.append((house_hi_name, None, bds_fly_prompt("front-tilt", vertical, night=False), "bds_flyday2",
                         fly_speed, "flycam tilt lên",  neg_vid_day, "fade", fly_rife_mult, F, 0.01))
    elif fly_angle == "auto":
        vid_jobs.append((house_hi_name, None, bds_fly_prompt("ngang", vertical, night=False), "bds_flyday",
                         fly_speed, "flycam quanh nhà", neg_vid_day, "fade", fly_rife_mult, F, 0.01))
        vid_jobs.append((house_hi_name, None, bds_fly_prompt("front-eye", vertical, night=False), "bds_flyday2",
                         fly_speed, "kết mặt tiền", neg_vid_day, "fade", fly_rife_mult, F, 0.01))
    else:
        vid_jobs.append((house_hi_name, None, bds_fly_prompt(fly_angle, vertical, night=False), "bds_flyday",
                         fly_speed, "flycam ngày", neg_vid_day, "fade", fly_rife_mult, F, 0.01))
    if has_night:
        vid_jobs.append((house_hi_name, names["night"], BDS_NIGHT_SEG, "bds_night",
                         build_speed, "ngày→đêm bật đèn", neg_night, "fade", fly_rife_mult, F, 0.01))
        # ALD 09/06/2026 - FLF với start=end=night image: Wan không drift về ban ngày cuối clip.
        # Đêm kết bằng front-eye (hero mặt tiền đèn vàng) cho auto/front-tilt; góc khác giữ lựa chọn user.
        night_fly = "front-eye" if fly_angle in ("front-tilt", "auto") else fly_angle
        vid_jobs.append((names["night"], names["night"], bds_fly_prompt(night_fly, vertical, night=True), "bds_flynight",
                         fly_speed, "flycam đêm", neg_night, "fade", fly_rife_mult, F, 0.01))

    # ALD 10/06/2026 - Background ffmpeg normalization: mỗi clip hoàn thành → submit ffmpeg ngay (overlap với render tiếp).
    segs      = []   # (mp4_local, speed, tr)
    norm_futs = []   # Future[str] tương ứng 1-1 với segs
    _norm_ex  = ThreadPoolExecutor(max_workers=1)   # 1 ffmpeg tại 1 thời điểm, không tranh GPU
    VID_LO, VID_HI = 0.30, 0.88
    vstep = (VID_HI - VID_LO) / max(1, len(vid_jobs))
    for i, (sn, en, prompt, prefix, sp, label, neg, tr, seg_rife, seg_F, seg_noise) in enumerate(vid_jobs):
        lo = VID_LO + i * vstep; hi = lo + vstep
        api_progress(job_id, lo, f"dựng {label}")
        out = comfy_poll(comfy_submit(build_bds_segment_workflow(
                sn, en, prompt, W, H, seg_F, vid_steps, prefix, seed,
                neg=neg, rife_mult=seg_rife, wan_ver=wan_ver, noise_aug=seg_noise, params=params)),
            job_id, deadline_sec=1800, prog_lo=lo, prog_hi=hi, prog_step=label)
        mp4 = comfy_fetch_output(out)
        if mp4:
            dst = os.path.join(tmp, f"bn{len(segs)}.mp4")
            def _do_norm(src=mp4, speed=sp, d=dst):
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                                f"setpts=PTS/{speed:.3f},scale={W}:{H},fps={out_fps},format=yuv420p", "-an",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", d],
                               check=True, capture_output=True, timeout=600)
                return d
            norm_futs.append(_norm_ex.submit(_do_norm))
            segs.append((mp4, sp, tr))
        else:
            api_log(job_id, f"đoạn '{label}' không trả MP4 — bỏ qua", "warn")

    if len(segs) < 2:
        _norm_ex.shutdown(wait=False)
        raise RuntimeError(f"bds: không đủ đoạn video ({len(segs)}) — render lỗi")

    # 4) Chờ background ffmpeg normalize (đã chạy xen kẽ với ComfyUI renders)
    api_progress(job_id, 0.90, "ghép video time-lapse")
    _norm_ex.shutdown(wait=True)
    norm      = []
    clip_trs  = []   # transitions[j] = từ clip j sang j+1
    for i, (fut, (_, _, tr)) in enumerate(zip(norm_futs, segs)):
        try:
            n = fut.result()
            if n and os.path.exists(n):
                norm.append(n)
                clip_trs.append(tr)
        except Exception as e:
            api_log(job_id, f"normalize clip {i} lỗi: {e} — bỏ qua", "warn")

    out_mp4 = os.path.join(tmp, "bds_final.mp4")
    if shot_mode == "1-shot":
        ok = _xfade_concat(norm, out_mp4, out_fps, dur=xfade_dur, transition=xfade_tr)
    else:
        ok = _xfade_concat(norm, out_mp4, out_fps, dur=xfade_dur, transitions=clip_trs)
    if not ok:
        raise RuntimeError("bds: ghép video lỗi")
    api_progress(job_id, 0.96, "upload kết quả")
    api_upload_output(job_id, out_mp4)
# #endregion

# ALD 22/06/2026 - Node ENHANCE/UPSCALE (hậu Wan, JOB RIÊNG 1-input video). Wan render nhẹ 540p/16fps → node này
# nâng nét lên 1080p/2K. ALD 28/06/2026: upscale chuyển ComfyUI ESRGAN ×4 → FFMPEG lanczos (RAM phẳng,
# không OOM, không đụng GPU); BỎ 4K. comfy_recycle vẫn XẢ RAM của Wan trước khi encode. RIFE fps giữ nguyên (đã chunk).
def _video_nframes(path, accurate=False):
    """Số frame video. accurate=True: ĐẾM DECODE THẬT (nb_read_frames qua -count_frames) — khớp ĐÚNG số frame mà
    ComfyUI VHS_LoadVideo nạp; dùng cho driver Wan Animate để num_frames không bao giờ lệch (header nb_frames /
    duration×fps đếm DƯ với video người dùng upload fps/timebase lẻ/VFR → reduction không kích hoạt → Wan lỗi
    'Sizes of tensors must match ... Expected size 1 but got size N'). Decode nên chậm hơn, nhưng driver ≤60s nên
    không đáng kể. accurate=False (mặc định): nb_frames header (nhanh) → fallback duration×fps. 0 nếu không đọc được."""
    if accurate:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                                "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                               capture_output=True, text=True, timeout=180)
            nrf = int(json.loads(r.stdout)["streams"][0].get("nb_read_frames") or 0)
            if nrf > 0:
                return nrf
        except Exception:
            pass
        # count_frames lỗi → rơi xuống đọc header bên dưới (vẫn tốt hơn 0)
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_frames,r_frame_rate,duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=20)
        st = json.loads(r.stdout)["streams"][0]
        nbf = int(st.get("nb_frames") or 0)
        if nbf > 0:
            return nbf
        dur = float(st.get("duration") or 0)
        rfr = str(st.get("r_frame_rate") or "16/1"); num, den = (rfr.split("/") + ["1"])[:2]
        fps = (float(num) / float(den)) if float(den or 1) else float(num or 16)
        if dur > 0 and fps > 0:
            return int(round(dur * fps))
    except Exception:
        pass
    return 0


def _concat_chunks(parts, out):
    """Ghép nhiều mp4 (cùng res/fps) → 1 video câm. -c copy (nhanh) → fallback re-encode nếu param lệch.
    ffmpeg streaming = KHÔNG ôm frame trong RAM (giữ RAM phẳng cả ở bước ghép)."""
    lst = str(out) + ".lst"
    with open(lst, "w") as f:
        for p in parts:
            f.write("file '%s'\n" % os.path.abspath(str(p)))
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-an", str(out)],
                       check=True, timeout=600)
    except Exception:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-an", str(out)],
                       check=True, timeout=1800)


# ALD 05/07/2026 - Node ENHANCE cho ẢNH tĩnh: ESRGAN upscale ×4 (1 ảnh → ImageUpscaleWithModel tự tile 512px,
# an toàn VRAM 32GB, KHÁC video batch bị OOM nên video mới xài ffmpeg). Ảnh QUÁ to (>6MP) pre-cap ~6MP trước ×4
# để output không phình quá cỡ (6MP×4=24MP đã rất lớn). Model mặc định 4x-UltraSharp (thêm chi tiết, không "nhựa").
def build_image_upscale_workflow(image_name, model_name="4x-UltraSharp.pth", prefix="enh-img", in_mp=0, face_restore=False, face_fidelity=0.5):
    # ALD 09/07/2026 - face_restore: ESRGAN là GAN upscaler THUẦN, không biết "mặt người" → mặt ít pixel ra
    # sáp/nhựa/méo (user than). Nối CodeFormer (custom node mav-rik/facerestore_cf) SAU upscale: detect mặt →
    # dựng lại CHỈ vùng mặt, phần còn lại giữ nguyên. codeformer_fidelity: 0=đẹp/bịa nhiều ↔ 1=bám ảnh gốc (0.5 cân bằng).
    # class_type FaceRestoreModelLoader/FaceRestoreCFWithModel — VERIFY /object_info sau khi cài node trên box.
    src = ["10", 0]
    wf = {"10": {"class_type": "LoadImage", "inputs": {"image": image_name}}}
    if in_mp and in_mp > 6.0:
        wf["15"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": ["10", 0], "upscale_method": "lanczos", "megapixels": 6.0, "resolution_steps": 1}}
        src = ["15", 0]
    wf["20"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model_name}}
    wf["30"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["20", 0], "image": src}}
    out = ["30", 0]
    if face_restore:
        _frm = os.environ.get("MOTION_FACE_RESTORE_MODEL", "codeformer-v0.1.0.pth")
        _fd = os.environ.get("MOTION_FACE_DETECT", "retinaface_resnet50")
        wf["40"] = {"class_type": "FaceRestoreModelLoader", "inputs": {"model_name": _frm}}
        wf["50"] = {"class_type": "FaceRestoreCFWithModel", "inputs": {
            "facerestore_model": ["40", 0], "image": ["30", 0], "facedetection": _fd,
            "codeformer_fidelity": max(0.0, min(1.0, float(face_fidelity)))}}
        out = ["50", 0]
    wf["100"] = {"class_type": "SaveImage", "inputs": {"images": out, "filename_prefix": prefix}}
    return wf


def _run_enhance_image(job_id, img_key, params):
    """enhance mode=image: upscale 1 ẢNH tĩnh bằng ESRGAN ×4 (ComfyUI). Bỏ qua fps/RIFE. targetRes tùy chọn =
    ép long-edge SAU ×4 (rỗng = giữ nguyên ×4). upscaleModel: 4x-UltraSharp (default) / RealESRGAN_x4plus / 4x_foolhardy_Remacri."""
    tmp = tempfile.mkdtemp(prefix=f"enh-img-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải ảnh")
    ext = os.path.splitext(str(img_key))[1] or ".png"
    in_local = api_download(img_key, os.path.join(tmp, "in" + ext))
    model = str(params.get("upscaleModel") or params.get("upscale_model") or "4x-UltraSharp").strip()
    if not model.lower().endswith(".pth"):
        model += ".pth"
    target = str(params.get("targetRes") or params.get("target_res") or "").lower().strip()
    _LONG = {"1080p": 1920, "2k": 2560, "1440p": 2560, "4k": 3840, "2160p": 3840}
    long_cap = _LONG.get(target, 0)
    iw, ih = _img_size(in_local) or (0, 0)
    in_mp = (iw * ih / 1e6) if (iw and ih) else 0
    # ALD 09/07/2026 - PHỤC HỒI MẶT (CodeFormer) sau ESRGAN — mặc định BẬT (ESRGAN không biết mặt người → sáp/méo).
    # Tắt per-node: faceRestore=false. faceFidelity 0..1 (0=đẹp/bịa nhiều ↔ 1=bám gốc, default 0.5).
    # Thiếu custom node (facerestore_cf) → tự BỎ QUA, vẫn upscale bình thường (không vỡ job).
    _fr_want = str(params.get("faceRestore", params.get("face_restore", "1"))).strip().lower() not in ("0", "false", "no", "off")
    try:
        _fr_fid = float(params.get("faceFidelity", params.get("face_fidelity", 0.5)))
    except Exception:
        _fr_fid = 0.5
    _fr_on = _fr_want and _comfy_has_node("FaceRestoreCFWithModel")
    if _fr_want and not _fr_on:
        api_log(job_id, "enhance(ảnh): THIẾU node FaceRestoreCFWithModel (cài mav-rik/facerestore_cf + codeformer.pth) → bỏ qua phục hồi mặt, chỉ ESRGAN", "warn")
    api_log(job_id, f"enhance(ảnh): {iw}×{ih} → ×4 ESRGAN [{model}]" + (f" + CodeFormer mặt (fidelity {_fr_fid:.2f})" if _fr_on else "") + (f" rồi ép ≤{long_cap}px ({target})" if long_cap else " (giữ ×4)"), "info")
    api_progress(job_id, 0.2, "Upscale ảnh ×4 (ESRGAN)")
    up_name = comfy_upload(in_local)   # _comfy_call tự chờ/thử lại nếu ComfyUI đang dậy
    out_img = comfy_fetch_output(
        comfy_poll(comfy_submit(build_image_upscale_workflow(up_name, model_name=model, prefix=f"enh-img-{job_id[:8]}", in_mp=in_mp, face_restore=_fr_on, face_fidelity=_fr_fid)),
                   job_id, deadline_sec=1200, prog_lo=0.3, prog_hi=0.85, prog_step="Upscale ảnh (ESRGAN)"),
        exts=(".png", ".jpg", ".jpeg", ".webp"))
    if not out_img:
        raise RuntimeError("enhance(ảnh): ComfyUI không trả ảnh upscale")
    final = out_img
    if long_cap:
        ow, oh = _img_size(out_img) or (0, 0)
        if ow and oh and max(ow, oh) > long_cap:
            if ow >= oh:
                nw, nh = long_cap, max(2, int(round(long_cap * oh / ow)))
            else:
                nh, nw = long_cap, max(2, int(round(long_cap * ow / oh)))
            nw -= nw % 2; nh -= nh % 2
            final = os.path.join(tmp, "enh_img_out.png")
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", out_img,
                            "-vf", f"scale={nw}:{nh}:flags=lanczos", "-frames:v", "1", final], check=True, timeout=180)
            api_log(job_id, f"enhance(ảnh): ép {ow}×{oh} → {nw}×{nh}", "info")
    api_progress(job_id, 0.95, "upload ảnh")
    api_upload_output(job_id, final, content_type=("image/png" if final.lower().endswith(".png") else "image/jpeg"))
    return final


def run_enhance(job):
    job_id = job["id"]; inputs = job.get("inputs", {}) or {}; params = job.get("params", {}) or {}
    video_key = inputs.get("input") or inputs.get("video") or inputs.get("motion") or inputs.get("image")
    if not video_key:
        raise RuntimeError("enhance cần 1 đầu vào (nối node trước đó vào)")
    # ALD 05/07/2026 - ẢNH hay VIDEO? mode=image/video ép tay; mode=auto tự nhận theo ĐUÔI FILE của key
    # (mediaViaJob giữ nguyên đuôi node trước: create-image/edit-image/tryon → .png/.jpg → nhánh ảnh ESRGAN ×4).
    _mode = str(params.get("mode") or params.get("inputType") or "auto").lower().strip()
    _ext = (os.path.splitext(str(video_key))[1] or "").lower().lstrip(".")
    _is_image = (_mode in ("image", "img", "photo", "picture")) or (_mode == "auto" and _ext in ("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"))
    if _mode in ("video", "clip"):
        _is_image = False
    if _is_image:
        return _run_enhance_image(job_id, video_key, params)
    target = str(params.get("targetRes") or params.get("target_res") or "1080p").lower()
    # ALD 28/06/2026 - BỎ 4K: enhance chỉ còn 1080p / 2K (chuẩn 1080-60 & 2K-60). 4K/2160p cũ → kẹp về 2K.
    _LONG = {"1080p": 1920, "2k": 2560, "1440p": 2560}
    if target in ("4k", "2160p"):
        api_log(job_id, "enhance: 4K đã bỏ → dùng 2K (1440p). Chuẩn hỗ trợ: 1080p / 2K, đều 30–60fps.", "warn")
        target = "2k"
    long_edge = _LONG.get(target, 1920)
    # ALD 23/06/2026 - KHÔI PHỤC ×4 (60fps): nội suy giờ CHUNK → RAM phẳng nên ×4 OK lại (lý do bỏ ×4 là RAM, nay hết).
    # fpsInterp chọn FPS ĐÍCH thật. RIFE vẫn chạy theo bội số 16fps (×4=64 raw, ×2=32 raw), sau đó normalize CFR
    # về đúng target_fps để mọi đoạn 1080p-60fps có cùng fps/timebase trước concat.
    # ALD 24/07/2026 - fpsInterp="" là lựa chọn "Gốc" thật sự. Trước đây toán tử `or` nuốt chuỗi rỗng
    # rồi lấy env mặc định 30, khiến UI ghi FPS gốc nhưng video <30fps vẫn bị RIFE.
    # Env MOTION_ENHANCE_FPS đổi mặc định toàn cục (30 / 48 / 60 / off).
    _fps_key = next((k for k in ("fpsInterp", "fps_interp", "fpsTarget") if k in params), None)
    if _fps_key is not None:
        _fps_value = params.get(_fps_key)
        _fps_raw = "native" if _fps_value is None or str(_fps_value).strip() == "" else str(_fps_value).strip().lower()
    else:
        _fps_raw = str(os.environ.get("MOTION_ENHANCE_FPS", "30")).strip().lower()
    fps_mult = 4 if _fps_raw in ("60", "60fps") else (3 if _fps_raw in ("48", "48fps") else (2 if _fps_raw in ("30", "30fps", "1", "true", "yes", "on") else 0))
    fps_target = 60 if _fps_raw in ("60", "60fps") else (48 if _fps_raw in ("48", "48fps") else (30 if fps_mult == 2 else 0))
    do_fps = fps_mult > 0
    tmp = tempfile.mkdtemp(prefix=f"enh-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải video")
    video_local = api_download(video_key, os.path.join(tmp, "in" + (os.path.splitext(str(video_key))[1] or ".mp4")))
    # ALD 28/06/2026 - UPSCALE BẰNG FFMPEG (thay ComfyUI ESRGAN ×4 đang OOM).
    #   Bug cũ: ImageUpscaleWithModel bung CẢ batch ×4 thành 1 tensor CPU → video dài/≥720p = OOM RAM
    #   (vd alloc 357GB = frames·W·H·192). ffmpeg scale=lanczos STREAM từng frame → RAM phẳng tuyệt đối, KHÔNG đụng
    #   GPU (giải phóng 5090 cho Wan/Ollama). Không sharpen hậu kỳ để tránh rung viền/halo theo thời gian.
    # XẢ Wan/ComfyUI trước (vừa ôm ~50GB RAM) → trả RAM về OS cho ffmpeg encode.
    comfy_recycle("enhance: xả Wan/ComfyUI trước ffmpeg upscale")
    vw, vh = _img_size(video_local) or (960, 540)
    fps = _video_fps(video_local) or 16
    # ALD 09/07/2026 - fps_mult ĐỘNG theo fps THẬT video vào (trước nhân CỨNG theo giả định gốc 16fps — motion
    # "Theo driver" giờ ra 20/24/30fps): mult = ceil(target/fps_vào) kẹp 2..4 (RIFE chỉ nhân nguyên), rồi normalize
    # CFR về target như cũ. Video vào ĐÃ ≥ target → BỎ RIFE (trước đây input 30fps chọn "30fps" vẫn RIFE ×2 vô ích).
    if do_fps:
        if float(fps) >= fps_target - 1:
            api_log(job_id, f"enhance: video vào đã {float(fps):.0f}fps ≥ đích {fps_target}fps → bỏ nội suy RIFE, giữ nguyên", "info")
            do_fps = False
            fps_mult = 0
        else:
            fps_mult = max(2, min(4, int((fps_target + float(fps) - 1) // max(1.0, float(fps)))))
    if vw >= vh:
        tw, th = long_edge, int(round(long_edge * vh / max(1, vw)))
    else:
        th, tw = long_edge, int(round(long_edge * vw / max(1, vh)))
    tw = max(16, (tw // 2) * 2); th = max(16, (th // 2) * 2)
    _engine = str(params.get("engine") or os.environ.get("MOTION_ENHANCE_ENGINE", "flashvsr")).lower().strip()
    api_log(job_id, f"enhance: {vw}×{vh}@{fps}fps → {tw}×{th} ({target}), engine={_engine}, fps×{fps_mult or 1}", "info")
    api_progress(job_id, 0.2, f"Upscale → {target}")
    out_mp4 = os.path.join(tmp, "up.mp4")
    _aud_in = _has_audio(video_local)
    def _ff_upscale():
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", video_local,
               "-vf", f"scale={tw}:{th}:flags=lanczos,format=yuv420p",
               "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p"]
        cmd += (["-c:a", "aac", "-b:a", "192k"] if _aud_in else ["-an"])
        cmd += ["-movflags", "+faststart", out_mp4]
        subprocess.run(cmd, check=True, timeout=3600)
    # ALD 24/07/2026 - Engine AI không còn âm thầm trả Lanczos rồi gắn nhãn FlashVSR/SeedVR2.
    # Chỉ luồng legacy gửi allowFallback=true mới được phép hạ xuống Lanczos.
    _af_raw = params.get("allowFallback", params.get("allow_fallback", False))
    _allow_fallback = _af_raw is True or str(_af_raw).strip().lower() in ("1", "true", "yes", "on")
    _ai_failure = None
    # ALD 19/07/2026 - Tắt hoàn toàn CAS mặc định: FlashVSR đã tự phục hồi chi tiết;
    # sharpen hậu kỳ làm viền giả bị rung/halo theo thời gian.
    _seedvr2_ok = False
    if _engine in ("seedvr2", "seedvr", "sr"):
        if _comfy_has_node("SeedVR2VideoUpscaler"):
            try:
                _short = 1440 if target == "2k" else 1080          # SeedVR2 dùng cạnh NGẮN làm resolution
                _srcap = int(os.environ.get("MOTION_SEEDVR2_MAX_FRAMES", "0"))  # 0 = cả clip (clip enhance thường ngắn)
                # Chọn TẦNG theo VRAM trống THẬT: GPU .165 share với Chandra-vLLM (OCR) — khi OCR giữ ~24GB thì
                # bản HQ (1080p/batch5) OOM → tự hạ low-vram (720p/batch1/tiled, đã kiểm chạy được ~5GB trống 06/07).
                _hq_min = float(os.environ.get("MOTION_SEEDVR2_HQ_MIN_VRAM_GB", "12"))
                _free = _gpu_free_gb()
                _low = (_free is not None and _free < _hq_min)
                api_log(job_id, f"enhance: SeedVR2 — VRAM trống {('%.1f' % _free) if _free is not None else '?'}GB → chế độ {'VRAM-thấp 720p' if _low else 'HQ ' + str(_short) + 'p'}", "info")
                _frames = _video_nb_frames(video_local)             # để tính windows tiến độ (mỗi batch = 1 window)
                def _sr_run(_lv):
                    comfy_wait_node("SeedVR2VideoUpscaler", 60)     # comfy có thể vừa recycle → chờ node nạp lại
                    _nm = comfy_upload(video_local)
                    # windows = số batch SeedVR2 → comfy_poll gộp tiến độ monotonic (khỏi ghim 90%), FE thấy "đoạn X/N" chạy.
                    _win = _seedvr2_windows(_frames, _lv)
                    api_log(job_id, f"enhance: SeedVR2 {'low-vram' if _lv else 'HQ'} — {_frames or '?'} frame ≈ {_win} batch", "info")
                    return comfy_fetch_output(comfy_poll(comfy_submit(
                        build_seedvr2_upscale_workflow(_nm, _short, fps, frame_cap=_srcap, prefix=f"enh-sr-{job_id[:8]}", low_vram=_lv)),
                        job_id, deadline_sec=3600, prog_lo=0.25, prog_hi=0.9, prog_step="SeedVR2 restoration", windows=_win))
                try:
                    _srout = _sr_run(_low)
                except Exception as _e1:
                    if not _low:                                    # HQ OOM/lỗi → xả VRAM rồi hạ low-vram 1 lần
                        api_log(job_id, f"enhance: SeedVR2 HQ lỗi ({_e1}) → xả VRAM, hạ chế độ VRAM-thấp", "warn")
                        comfy_recycle("enhance: xả trước SeedVR2 low-vram")
                        _srout = _sr_run(True)
                    else:
                        raise
                if _srout and os.path.exists(_srout) and os.path.getsize(_srout) > 1024:
                    shutil.move(_srout, out_mp4); _seedvr2_ok = True
                    api_log(job_id, "enhance: SeedVR2 3B restoration xong", "info")
                else:
                    _ai_failure = "SeedVR2 không tạo được video đầu ra"
                    api_log(job_id, "enhance: SeedVR2 không tạo được bản AI", "warn")
            except Exception as _sre:
                _ai_failure = f"SeedVR2 lỗi: {_sre}"
                api_log(job_id, f"enhance: SeedVR2 lỗi ({_sre})", "warn")
        else:
            _ai_failure = "Thiếu node SeedVR2VideoUpscaler hoặc model SeedVR2"
            api_log(job_id, "enhance: engine=seedvr2 nhưng THIẾU node SeedVR2VideoUpscaler hoặc model SeedVR2", "warn")
    # #region ALD 08/07/2026 - ENGINE FlashVSR (VSR AI 1-step, nhanh ~10× SeedVR2, hợp box share-GPU). Bật engine='flashvsr'
    # hoặc env MOTION_ENHANCE_ENGINE=flashvsr. Out = input × resize_factor × scale → ffmpeg conform về đúng tw×th sau.
    _flashvsr_ok = False
    if _engine in ("flashvsr", "flash", "fvsr"):
        if _comfy_has_node("FlashVSRNodeAdv"):
            try:
                _fv_scale = int(os.environ.get("MOTION_FLASHVSR_SCALE", "4"))          # FlashVSR tối ưu 4×
                _net = max(1.0, tw / max(1, vw))                                       # tỉ lệ cần đạt target
                _fv_rf = min(1.0, max(0.1, round(_net / max(1, _fv_scale), 1)))        # resize_factor bước 0.1
                _fvcap = int(os.environ.get("MOTION_FLASHVSR_MAX_FRAMES", "0"))
                _hq_min = float(os.environ.get("MOTION_FLASHVSR_HQ_MIN_VRAM_GB", "10"))
                _free = _gpu_free_gb()
                _low = (_free is not None and _free < _hq_min)
                api_log(job_id, f"enhance: FlashVSR — VRAM trống {('%.1f' % _free) if _free is not None else '?'}GB → {'tiled/low-vram' if _low else 'thường'}, net×{_net:.2f} (scale {_fv_scale}×resize {_fv_rf})", "info")
                def _fv_run(_lv):
                    comfy_wait_node("FlashVSRNodeAdv", 60)                             # comfy có thể vừa recycle
                    _nm = comfy_upload(video_local)
                    return comfy_fetch_output(comfy_poll(comfy_submit(
                        build_flashvsr_upscale_workflow(_nm, _fv_scale, _fv_rf, fps, frame_cap=_fvcap, prefix=f"enh-fv-{job_id[:8]}", low_vram=_lv)),
                        job_id, deadline_sec=3600, prog_lo=0.25, prog_hi=0.85, prog_step="FlashVSR upscale"))
                try:
                    _fvout = _fv_run(_low)
                except Exception as _e1:
                    if not _low:                                                      # lỗi/OOM → xả VRAM rồi hạ low-vram 1 lần
                        api_log(job_id, f"enhance: FlashVSR lỗi ({_e1}) → xả VRAM, hạ chế độ tiled/low-vram", "warn")
                        comfy_recycle("enhance: xả trước FlashVSR low-vram")
                        _fvout = _fv_run(True)
                    else:
                        raise
                if _fvout and os.path.exists(_fvout) and os.path.getsize(_fvout) > 1024:
                    # Đúng kích thước/fps thì giữ thẳng encode của Comfy, tránh H.264 lần hai làm mất chi tiết.
                    _fv_wh = _img_size(_fvout)
                    _fv_fps = _video_fps(_fvout) or fps
                    if _fv_wh == (tw, th) and abs(float(_fv_fps) - float(fps)) < 0.1:
                        shutil.move(_fvout, out_mp4)
                    else:
                        _fv_final = os.path.join(tmp, f"enh-fv-final-{job_id[:8]}.mp4")
                        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", _fvout,
                                        "-vf", f"scale={tw}:{th}:flags=lanczos,format=yuv420p", "-r", str(fps),
                                        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                                        "-c:a", "copy", "-movflags", "+faststart", _fv_final], check=True, timeout=600)
                        shutil.move(_fv_final, out_mp4)
                    _flashvsr_ok = True
                    api_log(job_id, "enhance: FlashVSR upscale xong", "info")
                else:
                    _ai_failure = "FlashVSR không tạo được video đầu ra"
                    api_log(job_id, "enhance: FlashVSR không tạo được bản AI", "warn")
            except Exception as _fve:
                _ai_failure = f"FlashVSR lỗi: {_fve}"
                api_log(job_id, f"enhance: FlashVSR lỗi ({_fve})", "warn")
        else:
            _ai_failure = "Thiếu node FlashVSRNodeAdv hoặc model FlashVSR-v1.1"
            api_log(job_id, "enhance: engine=flashvsr nhưng THIẾU node FlashVSRNodeAdv hoặc model FlashVSR-v1.1", "warn")
    # #endregion
    if not (_seedvr2_ok or _flashvsr_ok):
        if _engine in ("seedvr2", "seedvr", "sr", "flashvsr", "flash", "fvsr") and not _allow_fallback:
            raise RuntimeError((_ai_failure or f"Engine AI {_engine} không chạy được") + ". Không xuất Lanczos thay thế để tránh giao nhầm bản chất lượng thấp.")
        if _engine in ("seedvr2", "seedvr", "sr", "flashvsr", "flash", "fvsr"):
            api_log(job_id, f"enhance: {_ai_failure or 'engine AI không chạy được'} → allowFallback=true, dùng Lanczos", "warn")
        _ff_upscale()
    if not (os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1024):
        raise RuntimeError("ffmpeg upscale không tạo được MP4")
    if not do_fps:
        api_progress(job_id, 0.9, f"Upscale {target} xong")
    if do_fps:
        # ALD 22/06/2026 - RIFE MỀM: node "RIFE VFI" (custom node ComfyUI-Frame-Interpolation) + rife47.pth có thể THIẾU
        # trên box → trước đây submit thẳng → 400 làm HỎNG cả job (dù bản upscale đã xong). Giờ: check node trước +
        # bọc try/except → thiếu/lỗi thì BỎ QUA nội suy, GIỮ output upscale (không 400). Cài node + rife47.pth → 60fps tự bật.
        # ⚠ Lưu ý: 4K + nội suy = quá nhiều frame 4K trong RAM → dễ OOM; 60fps nên để ≤2K.
        _has_rife = _comfy_has_node("RIFE VFI")
        if not _has_rife:
            api_log(job_id, f"enhance: THIẾU node 'RIFE VFI' (ComfyUI-Frame-Interpolation) → bỏ nội suy, giữ {fps}fps. Cài node + rife47.pth để bật 60fps.", "warn")
        else:
            try:
                comfy_recycle("enhance: xả trước RIFE")
                rife_name = comfy_upload(out_mp4)
                # ALD 23/06/2026 - CHUNK nội suy: 1 pass giữ CẢ video trong RAM (VHS gom hết frame + RIFE trả CPU tensor)
                # → 2K×3/×4 đụng 99% RAM. Giờ nội suy TỪNG ĐOẠN (VHS skip/cap) + recycle giữa đoạn → RAM PHẲNG bất kể
                # video dài/2K (nhờ vậy ×4/60fps sống lại). fp16 = nửa RAM. Mỗi đoạn câm → ghép + mux audio gốc.
                # Đánh đổi: nhiều comfy-restart hơn → chậm hơn. Mối nối thiếu 1 frame nội suy (micro-hitch nhẹ, hiếm).
                # số frame VHS sẽ load = duration×16 (build_rife60_workflow force_rate=16) → đếm theo duration cho khớp
                # window (nb_frames native sẽ LỆCH nếu bản upscale không phải 16fps). Fallback nb_frames nếu thiếu duration.
                _dur = _audio_dur(out_mp4) or 0
                _nf = int(round(_dur * 16)) if _dur > 0 else _video_nframes(out_mp4)
                _CHUNK = max(24, int(os.environ.get("ENHANCE_RIFE_CHUNK", "120")))
                if _nf <= 0 or _nf <= _CHUNK:
                    comfy_wait_node("RIFE VFI", 60)  # chờ custom node nạp lại sau recycle
                    rife_out = comfy_fetch_output(comfy_poll(comfy_submit(build_rife60_workflow(rife_name, prefix=f"enh-rife-{job_id[:8]}", multiplier=fps_mult)),
                                                             job_id, deadline_sec=1800, prog_lo=0.6, prog_hi=0.92, prog_step="Nội suy fps (RIFE)"))
                else:
                    _nch = (_nf + _CHUNK - 1) // _CHUNK
                    api_log(job_id, f"enhance: nội suy CHUNK {_nf}f → {_nch} đoạn ×{_CHUNK}f (RAM phẳng, recycle giữa đoạn)", "info")
                    _parts, _off, _idx = [], 0, 0
                    while _off < _nf:
                        comfy_wait_node("RIFE VFI", 60)
                        _cap = min(_CHUNK, _nf - _off)
                        _lo = 0.6 + 0.30 * (_idx / _nch); _hi = 0.6 + 0.30 * ((_idx + 1) / _nch)
                        _cout = comfy_fetch_output(comfy_poll(comfy_submit(build_rife60_workflow(
                                    rife_name, prefix=f"enh-rife-{job_id[:8]}-{_idx:03d}", multiplier=fps_mult,
                                    skip_first=_off, frame_cap=_cap, with_audio=False)),
                                    job_id, deadline_sec=1800, prog_lo=_lo, prog_hi=_hi, prog_step=f"Nội suy đoạn {_idx + 1}/{_nch}"))
                        if not _cout:
                            raise RuntimeError(f"RIFE đoạn {_idx + 1}/{_nch} không ra video")
                        _parts.append(_cout)
                        _off += _cap; _idx += 1
                        if _off < _nf:
                            comfy_recycle(f"enhance: xả sau đoạn {_idx}")
                    rife_out = os.path.join(tmp, "enh_rife_concat.mp4")
                    _concat_chunks(_parts, rife_out)
                if rife_out:
                    muxed = os.path.join(tmp, "enh_final.mp4")
                    try:
                        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", rife_out, "-i", out_mp4,
                               "-map", "0:v:0"]
                        _aud = _has_audio(out_mp4)
                        if _aud:
                            cmd += ["-map", "1:a:0", "-af", "aresample=async=1:first_pts=0"]
                        else:
                            cmd += ["-an"]
                        if fps_target > 0:
                            # ALD 28/06/2026 - chất lượng output cao hơn: preset medium + crf 17 (cũ veryfast/18)
                            cmd += ["-vf", f"fps={fps_target},format=yuv420p",
                                    "-r", str(fps_target), "-c:v", "libx264", "-preset", "medium", "-crf", "17"]
                        else:
                            cmd += ["-c:v", "copy"]
                        if _aud:
                            cmd += ["-c:a", "aac"]
                        cmd += ["-shortest", "-movflags", "+faststart", muxed]
                        subprocess.run(cmd, check=True, timeout=600)
                        if fps_target > 0:
                            api_log(job_id, f"enhance: normalize RIFE output về CFR {fps_target}fps để concat đồng nhất", "info")
                        out_mp4 = muxed
                    except Exception as _e:
                        api_log(job_id, f"mux audio sau RIFE lỗi (giữ bản RIFE): {_e}", "warn")
                        out_mp4 = rife_out
            except Exception as _re:
                api_log(job_id, f"enhance: RIFE lỗi ({_re}) → giữ {fps}fps (output upscale)", "warn")
    api_progress(job_id, 0.95, "upload output")
    api_upload_output(job_id, out_mp4)
    return out_mp4


# #region ALD 20/07/2026 - wan-dancer: "Vũ đạo theo nhạc" (Wan-Dancer-14B, music-to-dance). Ảnh nhân vật + nhạc +
# style → video nhảy dài. Model CHƯA có node ComfyUI → chạy raw qua DiffSynth-Studio (script worker/wan_dancer/infer.py).
# CHỈ chạy trên VPS thuê GPU ≥ 90GB (2×34.5GB bf16); API + worker + script đều self-check VRAM. .165 KHÔNG claim job
# này (không có 'wan-dancer' trong JOB_TYPES mặc định — VPS đích set env JOB_TYPES=...,wan-dancer để bật claim).
def run_wan_dancer(job):
    job_id = job["id"]; inputs = job.get("inputs", {}); params = job.get("params", {})
    # Self-check VRAM (belt-and-suspenders — API đã hard-block, infer.py cũng check).
    try:
        import torch
        total_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        if total_gb < 90:
            raise RuntimeError(f"Wan-Dancer cần GPU ≥ 90GB VRAM — worker này chỉ có {total_gb:.0f}GB")
        api_log(job_id, f"wan-dancer: GPU {total_gb:.0f}GB ≥ 90GB — OK", "info")
    except RuntimeError:
        raise
    except Exception as _e:
        api_log(job_id, f"wan-dancer: không kiểm tra được VRAM ({_e}) — vẫn thử chạy", "warn")

    img_key = inputs.get("image") or inputs.get("input")
    music_key = inputs.get("music") or inputs.get("audio")
    if not img_key:
        raise RuntimeError("wan-dancer: thiếu ảnh nhân vật (cổng 'image')")
    if not music_key:
        raise RuntimeError("wan-dancer: thiếu nhạc (cổng 'music')")

    style = str(params.get("danceStyle") or params.get("style") or "street").lower()
    if style not in ("classical", "kpop", "street", "tap", "latin"):
        style = "street"
    try: duration = max(2.0, min(60.0, float(params.get("durationSec") or 15)))
    except Exception: duration = 15.0
    resolution = "720p" if str(params.get("resolution") or "720p").lower() == "720p" else "540p"
    try: seed = max(0, int(params.get("seed") or 0))
    except Exception: seed = 0
    extra_prompt = str(params.get("prompt") or "").strip()

    tmp = tempfile.mkdtemp(prefix=f"wandancer-{job_id[:8]}-")
    api_progress(job_id, 0.05, "tải ảnh + nhạc")
    img_path = api_download(img_key, os.path.join(tmp, "ref.jpg"))
    music_path = api_download(music_key, os.path.join(tmp, "music.wav"))
    out = os.path.join(tmp, "dance.mp4")

    infer_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wan_dancer", "infer.py")
    if not os.path.exists(infer_py):
        raise RuntimeError(f"wan-dancer: không tìm thấy infer.py ({infer_py})")
    py = os.environ.get("WAN_DANCER_PY") or sys.executable
    cmd = [py, infer_py, "--image", img_path, "--music", music_path, "--style", style,
           "--duration", f"{duration:.2f}", "--resolution", resolution, "--seed", str(seed), "--out", out]
    if extra_prompt:
        cmd += ["--prompt", extra_prompt]

    api_progress(job_id, 0.15, f"render vũ đạo ({style}, {duration:.0f}s, {resolution})")
    api_log(job_id, f"wan-dancer: {style} · {duration:.0f}s · {resolution} · seed={seed} → DiffSynth", "info")
    # Stream log DiffSynth (global→local render lâu, minute-scale). Không có % chi tiết → giữ progress coarse.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip()
        if line:
            api_log(job_id, line[:400], "info")
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"wan-dancer: infer.py thoát mã {rc} (xem log). Kiểm tra DiffSynth + weights + env trên VPS.")
    if not (os.path.exists(out) and os.path.getsize(out) > 1024):
        raise RuntimeError("wan-dancer: không tạo được MP4")
    api_progress(job_id, 0.95, "upload output")
    out = _finalize_mp4(out)
    api_upload_output(job_id, out)
    return out
# #endregion


PIPELINES = {
    "motion": run_motion,
    "bds": run_bds,       # time-lapse xây nhà từ 1 ảnh (ESRGAN + Qwen reverse-stage + Wan 2.1 FLF + flycam + concat dọc 60fps)
    "tryon": run_tryon,
    "create-image": run_create_image,
    "edit-image": run_edit_image,  # ALD 01/07/2026 - SỬA ảnh có sẵn theo mô tả (list ảnh × 1-5 version, progressive); Qwen-Edit/Gemini
    "product-overlay": run_product_overlay,
    "teaser": run_teaser,
    "video": run_video,   # LTX-2.3 ảnh+prompt → video+audio (lẻ; teaser dùng LTX qua motionMode='ltx')
    "text-to-video": run_text2video,  # ALD 14/06/2026 - CHỈ prompt (không ảnh) → video ngắn; dropdown model Wan2.x T2V (LTX = I2V → hạ về Wan)
    "wan-i2v": run_wan_i2v,  # ALD 26/06/2026 - Ảnh đầu (+ ảnh cuối opt) + prompt → Wan I2V/FLF, đọc wanModel
    "wan-dancer": run_wan_dancer,  # ALD 20/07/2026 - Wan-Dancer-14B music-to-dance (ảnh + nhạc → nhảy). VPS ≥90GB; VPS đích tự set JOB_TYPES=...,wan-dancer để claim (KHÔNG bật trên .165)
    "teen-flycam": run_teen_flycam,  # 1 ảnh người mẫu → preset social video 10s/5 shot flycam
    "trend-tiktok": run_trend_tiktok,  # 2 ảnh before/after → clip trend TikTok bám driver preset
    "ss": run_ss,         # ALD 14/06/2026 - Ảnh + prompt → video LTX-2.3 I2V + LoRA custom user tự train (template SS_WORKFLOW_JSON)
    "talk": run_talk,     # MultiTalk/InfiniteTalk: ảnh + thoại (TTS) → video nói + nhép miệng
    "face-motion": run_face_motion,  # talk có HÀNH ĐỘNG theo phong thái: ảnh người mẫu + kịch bản → đọc + nhép miệng + cử động
    "concat": run_concat, # ghép ≥2 clip (phân cảnh) → 1 video, GIỮ tiếng từng cảnh
    "reveal": run_reveal, # ALD 08/07 - ĐÈ LỘ: 2 video cùng người khác đồ → wipe lộ dần đồ mới (ffmpeg, RAM phẳng)
    "voiceover": run_voiceover,  # 1 clip + lời thuyết minh → TTS rồi thay/trộn audio, giữ nguyên hình
    "story-film": run_story_film,  # KỊCH BẢN → AI director tách cảnh/nhân vật/thoại → phim nhân vật (lip-sync) ghép sẵn
    "subtitle": run_subtitle,  # ALD 15/06/2026 - 1 video → ASR (OmniVoice /asr) + dịch (Ollama) → CHÁY phụ đề (hardsub), giữ tiếng gốc
    "enhance": run_enhance,  # ALD 28/06/2026 - upscale video hậu Wan bằng ffmpeg lanczos → 1080p/2K (RAM phẳng, bỏ 4K) + RIFE fps tùy chọn
}

def _startup():
    log(f"start worker_id={WORKER_ID} types={JOB_TYPES} api={API_URL} comfy={COMFY_URL}")
    # #region ALD 10/06/2026 - Dọn prompt MA trong ComfyUI khi worker khởi động. Worker là client DUY NHẤT
    # submit prompt vào ComfyUI box này, nên sau restart (deploy/crash/OOM) mọi prompt còn trong queue đều là
    # tàn dư của job đã bị API reclaim (running→error) — nếu không hủy, prompt cũ chiếm GPU 100% khiến job
    # mới xếp hàng chờ vô ích (đã gặp 10/06: Wan của job chết render tiếp, tryon mới đợi 4+ phút).
    try:
        q = requests.get(f"{COMFY_URL}/queue", timeout=5).json()
        n_run, n_pend = len(q.get("queue_running", [])), len(q.get("queue_pending", []))
        if n_run or n_pend:
            requests.post(f"{COMFY_URL}/queue", json={"clear": True}, timeout=5)       # xóa pending
            requests.post(f"{COMFY_URL}/interrupt", timeout=5)                          # ngắt prompt đang chạy
            log(f"dọn ComfyUI queue mồ côi: interrupt {n_run} running + clear {n_pend} pending")
    except Exception as e:
        log(f"dọn ComfyUI queue lỗi (bỏ qua): {e}")
    # #endregion


def _resource_guard_delay():
    # ALD 04/06/2026 - Guard RAM/swap: RAM sắp cạn hoặc swap đầy → HOÃN nhận job (chống OOM sập máy).
    avail_gb, swap_pct = _mem_status()
    # ALD 21/06/2026 - XẢ TRƯỚC, HOÃN SAU (sửa thứ tự — đây là gốc của 2 bệnh). before_claim chạy MỖI poll khi còn
    # slot rảnh. JOB XONG → XẢ: nếu ComfyUI đang IDLE (queue rỗng) mà còn ôm RAM (RSS ≥ COMFY_RECYCLE_GB do leak
    # ~50GB) → RESTART để trả RAM về OS. Khối này PHẢI xét TRƯỚC nhánh "hoãn" bên dưới: khi RAM tụt thấp vì CHÍNH
    # ComfyUI rò, nếu để nhánh "hoãn" return trước thì recycle (thứ DUY NHẤT giải phóng RAM) không bao giờ chạy →
    # kẹt vĩnh viễn idle ~93% RAM; job kế render dưới swap-thrash → output Wan HỎNG (mặt xám/loang). Đảo lên đây
    # cũng cho mọi job (kể cả motion-transfer chạy lẻ) luôn khởi động trên ComfyUI tươi. Cooldown chống recycle dồn.
    if COMFY_RECYCLE_GB > 0 and (time.time() - _last_recycle_ts) > COMFY_RECYCLE_COOLDOWN_SEC and comfy_queue_idle():
        rss = comfy_rss_gb()
        if rss >= COMFY_RECYCLE_GB:
            log(f"ComfyUI idle ôm RAM {rss:.0f}GB ≥ {COMFY_RECYCLE_GB:.0f}GB → recycle (trả RAM về OS)")
            comfy_recycle(f"idle RSS={rss:.0f}GB")
            return max(POLL, 8)   # cho ComfyUI dựng lại fresh trước khi nhận job kế
    # Sau khi đã thử recycle mà RAM vẫn thấp (do tiến trình khác, hoặc ComfyUI đang BẬN nên không recycle được) → hoãn.
    if avail_gb < MIN_AVAIL_GB or (swap_pct > MAX_SWAP_PCT and avail_gb < SWAP_RAM_FLOOR_GB):
        log(f"hoãn nhận job — RAM trống {avail_gb:.1f}GB (min {MIN_AVAIL_GB}) | swap {swap_pct:.0f}% (max {MAX_SWAP_PCT:.0f}%, sàn RAM {SWAP_RAM_FLOOR_GB}GB)")
        return max(POLL, 10)
    return None


def _log_pre_run_resources(job):
    # ALD 05/06/2026 - Warn RAM + VRAM TRƯỚC khi chạy job (dễ soi OOM) — hiện cả ở log job trên FE.
    _ra, _sw = _mem_status()
    _vu, _vt = _vram_status()
    _vram_s = f"{_vu:.1f}/{_vt:.1f}GB" if (_vu is not None and _vt) else "n/a"
    _tight = (_ra < MIN_AVAIL_GB + 4) or (_vt and _vu is not None and _vu > _vt * 0.9)
    api_log(job["id"], f"Tài nguyên trước khi chạy: RAM trống {_ra:.1f}GB · swap {_sw:.0f}% · VRAM {_vram_s}",
            "warn" if _tight else "info")
    log(f"  {job['id'][:8]} pre-run: RAM={_ra:.1f}GB swap={_sw:.0f}% VRAM={_vram_s}")


def _unsupported_message(jt, pipelines):
    return f"Chưa hỗ trợ type '{jt}' (worker có: {list(pipelines)})"


def main():
    run_worker_loop(
        log=log,
        poll=POLL,
        api_claim=api_claim,
        api_patch=api_patch,
        api_heartbeat=api_heartbeat,
        pipelines=PIPELINES,
        before_claim=_resource_guard_delay,
        before_run=_log_pre_run_resources,
        startup=_startup,
        unsupported_message=_unsupported_message,
        concurrency=WORKER_CONCURRENCY,
    )

if __name__ == "__main__":
    main()
