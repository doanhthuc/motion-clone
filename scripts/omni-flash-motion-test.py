#!/usr/bin/env python3
"""
Test thăm dò Gemini Omni Flash (Interactions API) cho nhu cầu MOTION TRANSFER.

Mục tiêu: xác minh THỰC TẾ (không đoán qua doc) xem có thể "lấy chuyển động từ
1 video mẫu áp lên người trong 1 ảnh" qua API hay không. Chạy 3 đường:

  1) sanity          : image_to_video   — ảnh người → video. CHỈ để xác nhận key +
                       endpoint + model chạy được. Nếu cái này lỗi → lỗi hạ tầng,
                       không phải lỗi motion-transfer.
  2) reference_to_video : ảnh người + video mẫu (làm reference) + prompt bám motion.
  3) edit               : video mẫu (input) + ảnh người + prompt "swap người".

LƯU Ý: Interactions API đang PREVIEW, schema chưa công bố đầy đủ. Script IN RA
toàn bộ request/response — chính thông báo lỗi của Google sẽ cho biết schema thật
và liệu video-reference có được xử lý hay chỉ bị bỏ qua (như model card cảnh báo).

Cách dùng:
  export GEMINI_API_KEY=AIza...
  python3 scripts/omni-flash-motion-test.py \
      --person path/to/nguoi.png \
      --motion path/to/video_mau.mp4 \
      --mode both --trim

  # chỉ chạy 1 đường:
  python3 scripts/omni-flash-motion-test.py --person a.png --motion b.mp4 --mode reference
"""

import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-omni-flash-preview"

# Prompt motion-transfer (rút gọn từ prompt bạn dùng trên Flow web).
MOTION_PROMPT = (
    "Use the reference video strictly as the motion source. Reproduce the same body "
    "movement, hand gestures, pose transitions, head movement, facial expression, "
    "timing and camera motion. Preserve the exact character identity, face, hairstyle, "
    "outfit and body proportions from the reference image. Keep the environment, "
    "lighting and composition unchanged. Photorealistic, natural cinematic motion, "
    "stable hands, stable face, high detail."
)
SWAP_PROMPT = (
    "Replace the person in this video with the person from the reference image. "
    "Keep every movement, pose, timing, camera motion, background and lighting exactly "
    "the same. Preserve the reference person's face, hairstyle, outfit and body "
    "proportions. Photorealistic, stable face, seamless motion."
)


def log(msg):
    print(msg, flush=True)


def die(msg, code=1):
    log(f"\n[FATAL] {msg}")
    sys.exit(code)


def http(method, url, headers=None, body=None, timeout=600):
    """Gọi HTTP bằng stdlib urllib (không cần cài 'requests'). Trả (status_code, bytes)."""
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()  # body lỗi của API — chứa message hữu ích


def http_json(method, url, headers=None, body=None, timeout=600):
    """Như http() nhưng parse JSON. Trả (status_code, json|str)."""
    code, raw = http(method, url, headers, body, timeout)
    try:
        return code, json.loads(raw.decode())
    except Exception:
        return code, raw.decode(errors="replace")


def guess_mime(path, fallback):
    m, _ = mimetypes.guess_type(path)
    return m or fallback


def b64_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def maybe_trim_video(path, out_dir):
    """Cắt video ≤10s + scale 720p bằng ffmpeg để payload base64 không quá to.
    Omni Flash chỉ nhận clip ngắn 720p; video mẫu dài/nặng sẽ làm request thất bại."""
    trimmed = os.path.join(out_dir, "_motion_trimmed.mp4")
    log(f"[trim] ffmpeg cắt ≤10s + scale 720p → {trimmed}")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", path, "-t", "10",
         "-vf", "scale='min(1280,iw)':-2", "-an", trimmed],
        check=True, timeout=180,
    )
    return trimmed


def size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)


def post_interaction(key, model, input_items, task, previous_id=None):
    """Gửi 1 lượt Interactions API. Trả (status_code, json|text). In request tóm tắt + response đầy đủ."""
    body = {
        "model": model,
        "input": input_items,
        "generation_config": {"video_config": {"task": task}},
    }
    if previous_id:
        body["previous_interaction_id"] = previous_id

    # In request nhưng che base64 cho dễ đọc.
    def _mask(it):
        if isinstance(it, dict) and "data" in it:
            return {**it, "data": f"<base64 {len(it['data'])} chars>"}
        return it
    log("[request] POST " + f"{BASE}/interactions")
    log("[request] body = " + json.dumps(
        {**body, "input": [_mask(i) for i in input_items]}, ensure_ascii=False, indent=2))

    code, payload = http_json(
        "POST", f"{BASE}/interactions",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        body=body, timeout=600,
    )
    log(f"[response] HTTP {code}")
    log("[response] " + (json.dumps(payload, ensure_ascii=False, indent=2)
                          if isinstance(payload, (dict, list)) else str(payload)[:2000]))
    return code, payload


def poll_if_needed(key, payload, max_wait=600):
    """Nếu API trả về dạng long-running (có id/name mà chưa có video), poll tới khi xong.
    Schema preview chưa chắc — hàm này thăm dò: thử GET /interactions/{id} và in kết quả."""
    if not isinstance(payload, dict):
        return payload
    iid = payload.get("id") or payload.get("name")
    done = payload.get("done") or payload.get("status") in ("SUCCEEDED", "COMPLETED", "completed")
    if not iid or done:
        return payload
    log(f"[poll] interaction đang xử lý (id={iid}) — poll tối đa {max_wait}s")
    waited = 0
    while waited < max_wait:
        time.sleep(10)
        waited += 10
        gid = iid.split("/")[-1]
        code, p = http_json("GET", f"{BASE}/interactions/{gid}",
                            headers={"x-goog-api-key": key}, timeout=60)
        if not isinstance(p, dict):
            log(f"[poll] HTTP {code} (non-JSON) — dừng poll")
            return payload
        st = p.get("status") or ("done" if p.get("done") else "…")
        log(f"[poll] +{waited}s HTTP {code} status={st}")
        if code != 200:
            log("[poll] endpoint poll không dùng được (schema khác) — xem response gốc ở trên.")
            return payload
        if p.get("done") or p.get("status") in ("SUCCEEDED", "COMPLETED", "completed", "FAILED"):
            log("[poll] xong:\n" + json.dumps(p, ensure_ascii=False, indent=2))
            return p
    log("[poll] hết thời gian chờ.")
    return payload


def save_video(payload, out_path):
    """Dò trong response bất kỳ chỗ nào chứa video (inline base64 hoặc URL) và lưu ra file."""
    found = {"n": 0}

    def walk(node):
        if isinstance(node, dict):
            # inline base64 video
            blob = node.get("inlineData") or node.get("inline_data")
            if isinstance(blob, dict) and blob.get("data") and "video" in str(blob.get("mimeType") or blob.get("mime_type") or ""):
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(blob["data"]))
                found["n"] += 1
                log(f"[save] video inline → {out_path}")
                return
            # URL video
            for k, v in node.items():
                if isinstance(v, str) and v.startswith("http") and any(
                        v.lower().endswith(ext) for ext in (".mp4", ".webm", ".mov")):
                    try:
                        vc, vbytes = http("GET", v, timeout=300)
                        if vc == 200:
                            with open(out_path, "wb") as f:
                                f.write(vbytes)
                            found["n"] += 1
                            log(f"[save] tải video từ URL ({k}) → {out_path}")
                            return
                        log(f"[save] tải {v} lỗi HTTP {vc}")
                    except Exception as e:
                        log(f"[save] lỗi tải {v}: {e}")
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    if not found["n"]:
        log("[save] KHÔNG tìm thấy video trong response (có thể chỉ trả text/id, hoặc đường này chưa cho ra video).")


def run_case(name, key, model, input_items, task, out_dir, prev_id=None):
    log("\n" + "=" * 70)
    log(f">>> CASE: {name}  (task={task})")
    log("=" * 70)
    try:
        code, payload = post_interaction(key, model, input_items, task, prev_id)
    except Exception as e:
        log(f"[ERROR] request thất bại: {e}")
        return None
    if code != 200:
        log(f"[KẾT LUẬN] '{name}' bị API từ chối ở HTTP {code} — đọc message lỗi ở trên "
            f"để biết schema/tham số sai hoặc tính năng chưa bật.")
        return payload if isinstance(payload, dict) else None
    payload = poll_if_needed(key, payload)
    save_video(payload, os.path.join(out_dir, f"out_{name}.mp4"))
    return payload


def main():
    ap = argparse.ArgumentParser(description="Test Omni Flash motion-transfer (2 đường + sanity).")
    ap.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""),
                    help="Gemini API key (AIza…). Mặc định lấy từ env GEMINI_API_KEY.")
    ap.add_argument("--person", required=True, help="Ảnh nhân vật (người cần cho chuyển động).")
    ap.add_argument("--motion", help="Video mẫu chuyển động (bắt buộc cho mode reference/edit/both).")
    ap.add_argument("--mode", default="both",
                    choices=["both", "reference", "edit", "sanity"],
                    help="Đường muốn thử. 'both' = sanity + reference + edit.")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out-dir", default="scripts/omni-out")
    ap.add_argument("--trim", action="store_true",
                    help="Cắt video mẫu ≤10s + 720p bằng ffmpeg trước khi gửi (khuyến nghị).")
    ap.add_argument("--prompt", default="", help="Ghi đè prompt motion (tuỳ chọn).")
    args = ap.parse_args()

    key = args.key.strip()
    if not key:
        die("Thiếu API key. Chạy: export GEMINI_API_KEY=AIza...  hoặc dùng --key")
    if not key.startswith("AIza"):
        log(f"[warn] key không bắt đầu bằng 'AIza' (dạng Gemini thường thấy) — vẫn thử.")
    if not os.path.isfile(args.person):
        die(f"Không thấy ảnh person: {args.person}")

    need_motion = args.mode in ("both", "reference", "edit")
    if need_motion:
        if not args.motion:
            die("mode này cần --motion (video mẫu).")
        if not os.path.isfile(args.motion):
            die(f"Không thấy video motion: {args.motion}")

    os.makedirs(args.out_dir, exist_ok=True)

    motion_path = args.motion
    if need_motion and args.trim:
        motion_path = maybe_trim_video(args.motion, args.out_dir)

    # Chuẩn bị input dùng chung.
    person_mime = guess_mime(args.person, "image/png")
    person_item = {"type": "image", "data": b64_file(args.person), "mime_type": person_mime}

    motion_item = None
    if need_motion:
        mb = size_mb(motion_path)
        log(f"[info] video mẫu: {motion_path} ({mb:.1f} MB){'  ⚠ >15MB, nên dùng --trim' if mb > 15 else ''}")
        motion_mime = guess_mime(motion_path, "video/mp4")
        motion_item = {"type": "video", "data": b64_file(motion_path), "mime_type": motion_mime}

    prompt_motion = args.prompt or MOTION_PROMPT
    results = {}

    if args.mode in ("both", "sanity"):
        results["sanity"] = run_case(
            "sanity", key, args.model,
            [person_item, {"type": "text", "text": "The person in the image comes to life, "
                                                   "subtle natural movement, cinematic, 720p."}],
            "image_to_video", args.out_dir,
        )

    if args.mode in ("both", "reference"):
        results["reference"] = run_case(
            "reference", key, args.model,
            [person_item, motion_item, {"type": "text", "text": prompt_motion}],
            "reference_to_video", args.out_dir,
        )

    if args.mode in ("both", "edit"):
        results["edit"] = run_case(
            "edit", key, args.model,
            [motion_item, person_item, {"type": "text", "text": SWAP_PROMPT}],
            "edit", args.out_dir,
        )

    # Tóm tắt.
    log("\n" + "#" * 70)
    log("# TÓM TẮT")
    log("#" * 70)
    for name, payload in results.items():
        out = os.path.join(args.out_dir, f"out_{name}.mp4")
        has_video = os.path.isfile(out) and os.path.getsize(out) > 0
        log(f"  - {name:10s}: {'✅ có video: ' + out if has_video else '❌ không có video (xem log ở trên)'}")
    log("\nĐọc kỹ block [response] của từng case: nếu 'reference'/'edit' trả HTTP 200 "
        "nhưng video KHÔNG bám chuyển động video mẫu → đúng như model card: video-reference "
        "bị model bỏ qua. Nếu HTTP 4xx → message lỗi cho biết tham số/tính năng chưa bật.")


if __name__ == "__main__":
    main()
