#!/usr/bin/env python3
# #region ALD 20/07/2026 - Wan-Dancer-14B (music-to-dance) inference standalone.
# Model: https://huggingface.co/Wan-AI/Wan-Dancer-14B — "A Hierarchical Framework for Minute-scale Coherent
# Music-to-Dance Generation". CHƯA có node ComfyUI → chạy raw qua DiffSynth-Studio. Two-stage: global keyframe
# → local refine. Mỗi model (global/local) ~34.5GB bf16 ⇒ CẦN GPU ≥ 90GB VRAM (vd RTX 6000 Blackwell 96GB).
#
# Script CHẠY TRONG ENV GPU CỦA VPS THUÊ (không phải box .165). worker run_wan_dancer gọi qua subprocess:
#   python infer.py --image ref.jpg --music song.wav --style street --duration 15 --resolution 720p --out out.mp4
#
# SETUP TRÊN VPS (làm 1 lần, người dùng tự nghiệm thu):
#   1) git clone DiffSynth-Studio + cài theo hướng dẫn Wan-Dancer (torch cu128 cho Blackwell sm_120 — xem
#      bài học [[vps-simplepod-v100]]: card phải CC ≥ 8.0, đúng kernel).
#   2) Tải weights Wan-Dancer-14B (global_model + local_model + umt5-xxl + clip + VAE).
#   3) Đặt ENV cho worker:
#        WAN_DANCER_DIR=/path/to/DiffSynth-Studio      (repo có pipeline Wan-Dancer)
#        WAN_DANCER_MODEL=/path/to/Wan-Dancer-14B      (thư mục weights)
#        WAN_DANCER_PY=/path/to/venv/bin/python        (env riêng, nếu khác python worker)
#
# ⚠ TODO (nghiệm thu trên VPS): tên module/hàm pipeline DiffSynth cho Wan-Dancer có thể khác — chỉnh phần
#    `_load_pipeline()` + `_run()` bên dưới cho khớp API DiffSynth thực tế sau khi cài. Phần khung (argparse,
#    map style→prompt, ghép nhạc, ghi mp4) đã đúng và tái dùng được.
# #endregion
import argparse
import os
import subprocess
import sys

# Style vũ đạo (khớp model card) → prompt gợi ý. Giữ tiếng Anh cho model.
STYLE_PROMPTS = {
    "classical": "Chinese classical dance, graceful flowing movements, elegant posture",
    "kpop":      "energetic K-Pop choreography, sharp synchronized idol dance moves",
    "street":    "street dance, hip-hop groove, dynamic urban freestyle",
    "tap":       "rhythmic tap dance, precise footwork, lively percussive steps",
    "latin":     "Latin dance, passionate hip movement, salsa/cha-cha rhythm",
}

# Độ phân giải hỗ trợ (short-edge). Wan-Dancer refine ra HD; giữ khung dọc social mặc định.
RES_MAP = {"540p": (544, 960), "720p": (720, 1280)}


def _log(msg):
    print(f"[wan-dancer] {msg}", flush=True)


def _load_pipeline(model_dir, device="cuda"):
    """Nạp pipeline Wan-Dancer từ DiffSynth-Studio.
    ⚠ TODO VPS: khớp đúng API DiffSynth sau khi cài. Placeholder theo cấu trúc DiffSynth phổ biến."""
    from diffsynth import ModelManager, WanVideoPipeline  # noqa: E402  (chỉ có trên VPS đã cài)

    mm = ModelManager(device=device)
    # global + local + text encoder + clip + vae — đường dẫn theo layout repo Wan-AI/Wan-Dancer-14B.
    mm.load_models([
        os.path.join(model_dir, "global_model.safetensors"),
        os.path.join(model_dir, "local_model.safetensors"),
        os.path.join(model_dir, "models_t5_umt5-xxl-enc-bf16.pth"),
        os.path.join(model_dir, "models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth"),
        os.path.join(model_dir, "Wan2.1_VAE.pth"),
    ], torch_dtype="bf16")
    pipe = WanVideoPipeline.from_model_manager(mm)
    return pipe


def _run(pipe, image_path, prompt, num_frames, width, height, seed):
    """Chạy 2-stage global→local. ⚠ TODO VPS: khớp tham số hàm pipeline thực tế của Wan-Dancer."""
    from PIL import Image  # noqa: E402
    import torch  # noqa: E402

    ref = Image.open(image_path).convert("RGB")
    with torch.no_grad():
        frames = pipe(
            prompt=prompt,
            input_image=ref,
            num_frames=num_frames,
            width=width,
            height=height,
            seed=seed,
        )
    return frames  # list[PIL.Image]


def _frames_to_video(frames, music_path, fps, out_path, tmp_dir):
    """Ghi frames → mp4 (libx264) rồi mux nhạc gốc, cắt theo độ dài video."""
    import imageio  # noqa: E402

    silent = os.path.join(tmp_dir, "silent.mp4")
    writer = imageio.get_writer(silent, fps=fps, codec="libx264", quality=8,
                                pixelformat="yuv420p", macro_block_size=16)
    for fr in frames:
        writer.append_data(_np(fr))
    writer.close()

    # Mux nhạc: cắt audio theo đúng độ dài video (-shortest), giữ chất lượng.
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-i", silent, "-i", music_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_path,
    ], check=True, timeout=1800)
    return out_path


def _np(img):
    import numpy as np  # noqa: E402
    return np.asarray(img)


def main():
    ap = argparse.ArgumentParser(description="Wan-Dancer-14B music-to-dance inference")
    ap.add_argument("--image", required=True, help="ảnh nhân vật tham chiếu")
    ap.add_argument("--music", required=True, help="file nhạc (wav/mp3)")
    ap.add_argument("--style", default="street", choices=list(STYLE_PROMPTS.keys()))
    ap.add_argument("--duration", type=float, default=15.0, help="giây")
    ap.add_argument("--resolution", default="720p", choices=list(RES_MAP.keys()))
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prompt", default="", help="prompt bổ sung (nối vào style prompt)")
    ap.add_argument("--out", required=True, help="đường dẫn mp4 xuất")
    args = ap.parse_args()

    model_dir = os.environ.get("WAN_DANCER_MODEL", "")
    if not model_dir or not os.path.isdir(model_dir):
        _log(f"LỖI: WAN_DANCER_MODEL không hợp lệ ({model_dir!r}). Set env trỏ tới thư mục weights.")
        sys.exit(2)

    # Self-check VRAM (belt-and-suspenders — worker cũng đã check + API đã gate).
    try:
        import torch
        total = torch.cuda.get_device_properties(0).total_memory
        if total < 90 * 1024 ** 3:
            _log(f"LỖI: GPU {total / 1024**3:.0f}GB < 90GB — Wan-Dancer cần ≥ 90GB VRAM.")
            sys.exit(3)
    except Exception as e:
        _log(f"CẢNH BÁO: không kiểm tra được VRAM ({e}) — vẫn thử chạy.")

    prompt = STYLE_PROMPTS[args.style]
    if args.prompt.strip():
        prompt = f"{prompt}, {args.prompt.strip()}"
    width, height = RES_MAP[args.resolution]
    num_frames = max(16, int(round(args.duration * args.fps)))
    tmp_dir = os.path.dirname(os.path.abspath(args.out)) or "."

    _log(f"style={args.style} · {width}x{height} · {num_frames}f@{args.fps}fps · seed={args.seed}")
    pipe = _load_pipeline(model_dir)
    _log("pipeline loaded → global→local render")
    frames = _run(pipe, args.image, prompt, num_frames, width, height, args.seed)
    _log(f"render xong {len(frames)} frames → ghép nhạc")
    _frames_to_video(frames, args.music, args.fps, args.out, tmp_dir)
    _log(f"DONE → {args.out}")


if __name__ == "__main__":
    main()
