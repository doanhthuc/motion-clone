#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# region ALD 21/06/2026 - Wan2.2-Animate runner CHÍNH THỨC (Diffusers, per-job) — thay đường ComfyUI cho motion.
# Theo ĐÚNG tài liệu Wan2.2 (huggingface.co/docs/diffusers .../wan + github Wan-Video/Wan2.2):
#   pipe(image, pose_video, face_video, segment_frame_length=77, guidance_scale=1.0, mode="animate") → export fps=30.
# CHẠY PER-JOB (1 process/clip): xong → process thoát → OS thu hồi TOÀN BỘ RAM. KHÔNG leak, KHÔNG cần recycle,
# KHÔNG --disable-smart-memory như ComfyUI. Đây là điểm khác cốt lõi so với runner cũ.
#
# ⚠️ INPUT pose_video/face_video PHẢI preprocess sẵn (Diffusers chưa tự preprocess) — dùng preprocess_data.py của
#    repo Wan2.2 gốc (xem worker/wan_animate/README — bước riêng, có checkpoint riêng).
#
# VRAM (1×32GB): 540p (max_area ~540×960) → model nạp thẳng VRAM (offload_model=False của docs) → RAM nhẹ.
#   Nếu OOM (vd 720p) → WAN_OFFLOAD=1 bật enable_model_cpu_offload() (đẩy layer xuống CPU như block_swap, RAM nặng
#   lại NHƯNG vẫn per-job nên thoát là sạch). VAE tiling bật sẵn để decode đỡ tốn VRAM.
#
# Dùng: python infer.py --image ref.png --pose pose.mp4 --face face.mp4 --out out.mp4 [--mode animate|replace
#        --bg bg.mp4 --mask mask.mp4 --short 540 --steps 20 --seed 42 --prompt "..."]
# endregion
import argparse, os, sys
import numpy as np
import torch
from diffusers import AutoencoderKLWan, WanAnimatePipeline
from diffusers.utils import export_to_video, load_image, load_video

MODEL_DIR = os.environ.get("WAN_ANIMATE_MODEL_DIR", "Wan-AI/Wan2.2-Animate-14B-Diffusers")


def aspect_ratio_resize(image, pipe, max_area):
    """Resize ảnh ref khớp ràng buộc VAE/patch (theo đúng helper trong docs Diffusers)."""
    ar = image.height / image.width
    mod = pipe.vae_scale_factor_spatial * pipe.transformer.config.patch_size[1]
    h = round(np.sqrt(max_area * ar)) // mod * mod
    w = round(np.sqrt(max_area / ar)) // mod * mod
    return image.resize((w, h)), h, w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="ảnh nhân vật (ref)")
    ap.add_argument("--pose", required=True, help="pose_video.mp4 (ĐÃ preprocess)")
    ap.add_argument("--face", required=True, help="face_video.mp4 (ĐÃ preprocess)")
    ap.add_argument("--out", required=True, help="đường dẫn mp4 xuất")
    ap.add_argument("--mode", default="animate", choices=["animate", "replace"])
    ap.add_argument("--bg", default=None, help="background_video.mp4 (mode=replace)")
    ap.add_argument("--mask", default=None, help="mask_video.mp4 (mode=replace)")
    ap.add_argument("--short", type=int, default=int(os.environ.get("WAN_SHORT", "540")), help="cạnh ngắn px (540)")
    ap.add_argument("--steps", type=int, default=int(os.environ.get("WAN_STEPS", "20")), help="num_inference_steps (docs=20)")
    ap.add_argument("--seg", type=int, default=77, help="segment_frame_length (docs=77)")
    ap.add_argument("--refert", type=int, default=int(os.environ.get("WAN_REFERT", "1")), help="prev_segment_conditioning_frames (1 hoặc 5)")
    ap.add_argument("--guidance", type=float, default=1.0, help="CFG (docs=1.0 = tắt)")
    ap.add_argument("--fps", type=int, default=30, help="export fps (docs=30 native)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prompt", default=os.environ.get("WAN_PROMPT", "A person performing natural movements, sharp focus, high quality"))
    ap.add_argument("--negative", default="blurry, low quality, distorted, deformed, static, poorly drawn")
    args = ap.parse_args()

    print(f"[wan-animate] model={MODEL_DIR} mode={args.mode} short={args.short} steps={args.steps} seg={args.seg}", flush=True)

    vae = AutoencoderKLWan.from_pretrained(MODEL_DIR, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanAnimatePipeline.from_pretrained(MODEL_DIR, vae=vae, torch_dtype=torch.bfloat16)

    # VRAM: mặc định nạp thẳng GPU (offload_model=False). WAN_OFFLOAD=1 → đẩy CPU khi OOM (vd 720p).
    if os.environ.get("WAN_OFFLOAD", "0").lower() in ("1", "true", "yes", "on"):
        print("[wan-animate] enable_model_cpu_offload (RAM nặng nhưng per-job thoát là sạch)", flush=True)
        pipe.enable_model_cpu_offload()
    else:
        pipe.to("cuda")
    try:
        pipe.vae.enable_tiling()   # decode đỡ tốn VRAM
    except Exception:
        pass

    image = load_image(args.image)
    max_area = args.short * round(args.short * 16 / 9)        # 540 → ~540×960
    image, h, w = aspect_ratio_resize(image, pipe, max_area)
    print(f"[wan-animate] render {w}×{h}", flush=True)

    pose_video = load_video(args.pose)
    face_video = load_video(args.face)
    gen = torch.Generator(device="cuda").manual_seed(args.seed)

    kw = dict(
        image=image, pose_video=pose_video, face_video=face_video,
        prompt=args.prompt, negative_prompt=args.negative,
        height=h, width=w, segment_frame_length=args.seg,
        prev_segment_conditioning_frames=args.refert,
        guidance_scale=args.guidance, num_inference_steps=args.steps,
        mode=args.mode, generator=gen,
    )
    if args.mode == "replace":
        if not (args.bg and args.mask):
            sys.exit("mode=replace cần --bg và --mask (background_video + mask_video)")
        kw["background_video"] = load_video(args.bg)
        kw["mask_video"] = load_video(args.mask)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    output = pipe(**kw).frames[0]
    export_to_video(output, args.out, fps=args.fps)
    _vram = (torch.cuda.max_memory_allocated() / 1e9) if torch.cuda.is_available() else 0.0
    # VRAM đỉnh = câu trả lời cho "540p có vừa 32GB không" (không OOM nếu < ~31). >31 → giảm --short hoặc WAN_OFFLOAD=1.
    print(f"[wan-animate] DONE → {args.out} ({args.fps}fps) | VRAM đỉnh {_vram:.1f}GB", flush=True)


if __name__ == "__main__":
    main()
