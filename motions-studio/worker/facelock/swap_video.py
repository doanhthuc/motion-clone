#!/usr/bin/env python3
# ALD 03/07/2026 - faceLock: khóa IDENTITY mặt người mẫu sau render Wan Animate.
# Wan vẽ lại 100% khung hình nên mặt luôn drift về phía driver (nhất là faceSource=driver).
# Script này swap mặt từng frame về đúng mặt ảnh mẫu bằng insightface/inswapper_128:
# - GIỮ NGUYÊN biểu cảm/khẩu hình/hướng đầu của frame (Wan + driver tạo ra) — chỉ đắp identity mẫu lên.
# - Chạy TRƯỚC RIFE/mux audio (ít frame nhất, 16fps native; RIFE nội suy sau sẽ mượt hóa luôn phần swap).
# Chạy trong venv riêng ~/facelock (KHÔNG đụng venv ComfyUI — tránh vỡ dependency prod).
# Cài lại từ đầu: xem worker/facelock/setup.sh trong repo.
import argparse
import os
import subprocess
import sys


def _area(f):
    return (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])


# 19/09/2026 - inswapper_128 consistently renders fuller/bigger lips than the reference photo,
# confirmed on a real job's own native 544x960 output (before any enhance pass) so it's this
# model's own geometry bias, not an enhance artifact and not fixable by fidelity/restore knobs
# (already tried CodeFormer at 0.5/0.7 — made lips fuller, not less). paste_back=True has no
# blend knob upstream, so this reimplements insightface's own INSwapper.get(paste_back=True)
# (python-package/insightface/model_zoo/inswapper.py on deepinsight/insightface, read 19/09/2026)
# with one change: the merge mask is scaled by `blend` before compositing, so less of Wan's
# original face gets replaced — trading identity-lock strength for less geometry distortion.
# Drops upstream's fake_diff computation: read the source, that mask is built but never used in
# the final merge there (the line assigning it is commented out) — dead weight, not a behavior
# this needs to match. blend=1.0 (default) skips all of this and calls the unmodified upstream
# method, so today's output is bit-for-bit unchanged unless a caller opts into blend<1.
#
# 20/09/2026 - detail_keep (sigma in frame pixels, 0 = off): transfer only the LOW-frequency part
# of what the swap changed, so Wan's own high frequencies — skin grain, lashes, the hair strands
# falling over the forehead — survive the swap instead of being replaced by inswapper's 128x128
# render. Measured on .smoke/ab-face/, where original-02-camera-motion.mp4 and facelock-02.mp4 are
# the same 452 frames with and without the swap: today's merge costs the face region 39% of its
# Laplacian variance (150.7 -> 92.4, mean over frames 100-300).
# MEASURED 20/09/2026 on a 5090 (scripts/ab-facelock-detail.sh, three swaps of that same render,
# 33-35s each): sigma 2.0 -> 147.3, sigma 3.0 -> 148.6, i.e. 98% of Wan's own detail back. Frame-to
# -frame change in the crop stayed at Wan's level (5.19 -> 5.22 mean abs delta, plain swap 5.00), so
# it does not buy the texture back with shimmer. Splitting (arm - Wan) against (plain swap - Wan) by
# band: the low-frequency part — the identity shift — survives at 1.04, while the high-frequency
# overwrite drops to 0.71. That split is the whole point, and it is what `blend` cannot do: blend
# scales the change's amplitude (so identity and texture dilute together — that is why the 0.3
# default was reverted on 19/09), while this band-limits it and leaves the amplitude alone.
# Not fixed by this: inswapper's fuller/redder lips, which are low-frequency geometry and so ride
# through the lowpass — that needs a face-parser mask over the mouth, not a sigma.
def _swap_blended(swapper, frame, tgt, src_face, blend, detail_keep=0.0):
    if blend >= 0.999 and detail_keep <= 0:
        return swapper.get(frame, tgt, src_face, paste_back=True)
    import cv2
    import numpy as np

    bgr_fake, M = swapper.get(frame, tgt, src_face, paste_back=False)
    target_img = frame
    IM = cv2.invertAffineTransform(M)
    aimg_h, aimg_w = bgr_fake.shape[:2]
    img_white = np.full((aimg_h, aimg_w), 255, dtype=np.float32)
    bgr_fake_warp = cv2.warpAffine(bgr_fake, IM, (target_img.shape[1], target_img.shape[0]), borderValue=0.0)
    img_white = cv2.warpAffine(img_white, IM, (target_img.shape[1], target_img.shape[0]), borderValue=0.0)
    img_white[img_white > 20] = 255
    img_mask = img_white
    mask_h_inds, mask_w_inds = np.where(img_mask == 255)
    if mask_h_inds.size == 0:
        return target_img
    mask_h = np.max(mask_h_inds) - np.min(mask_h_inds)
    mask_w = np.max(mask_w_inds) - np.min(mask_w_inds)
    mask_size = int(np.sqrt(mask_h * mask_w))
    k = max(mask_size // 10, 10)
    img_mask = cv2.erode(img_mask, np.ones((k, k), np.uint8), iterations=1)
    k = max(mask_size // 20, 5)
    blur_size = (2 * k + 1, 2 * k + 1)
    img_mask = cv2.GaussianBlur(img_mask, blur_size, 0)
    img_mask = (img_mask / 255.0) * float(blend)
    img_mask = np.reshape(img_mask, [img_mask.shape[0], img_mask.shape[1], 1])
    target_f = target_img.astype(np.float32)
    if detail_keep > 0:
        # Same masked change as below (mask * (fake - target)), lowpassed before it is applied, so
        # everything finer than `detail_keep` stays exactly as Wan rendered it. Blurring the change
        # rather than the result is what keeps this from softening the frame.
        delta = cv2.GaussianBlur(img_mask * (bgr_fake_warp - target_f), (0, 0), float(detail_keep))
        return np.clip(target_f + delta, 0, 255).astype(np.uint8)
    fake_merged = img_mask * bgr_fake_warp + (1 - img_mask) * target_f
    return fake_merged.astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="ảnh mẫu (nguồn identity)")
    ap.add_argument("--inp", required=True, help="video Wan output")
    ap.add_argument("--out", required=True)
    ap.add_argument("--det", type=int, default=640, help="det_size SCRFD")
    ap.add_argument("--crf", type=int, default=15)
    ap.add_argument("--blend", type=float, default=1.0,
                     help="merge strength 0..1 (1.0 = today's behavior, lower keeps more of Wan's own face)")
    ap.add_argument("--detail-keep", type=float, default=0.0, dest="detail_keep",
                     help="lowpass sigma in px for the swap's change (0 = off; ~2-3 keeps Wan's skin/hair texture)")
    args = ap.parse_args()

    import cv2
    import onnxruntime as ort

    home = os.path.expanduser(os.environ.get("FACELOCK_DIR", "~/facelock"))
    # CUDA EP cần cuDNN/cuBLAS (pip nvidia-*-cu12 trong venv). LD_LIBRARY_PATH set sau khi process chạy thì
    # linker không thấy → preload tường minh. Không có GPU thì rơi về CPU (~1fps, 241f ≈ 4').
    try:
        ort.preload_dlls()
    except Exception:
        try:
            import ctypes
            import glob as _g
            _sp_dir = os.path.dirname(os.path.dirname(ort.__file__))
            for _pat in ("nvidia/cu*/lib/libcudart.so*", "nvidia/cublas/lib/libcublas.so*",
                         "nvidia/cudnn/lib/libcudnn.so*", "nvidia/cufft/lib/libcufft.so*"):
                for _so in sorted(_g.glob(os.path.join(_sp_dir, _pat))):
                    try:
                        ctypes.CDLL(_so, mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass
        except Exception:
            pass
    avail = ort.get_available_providers()
    providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                 if "CUDAExecutionProvider" in avail else ["CPUExecutionProvider"])
    print(f"[facelock] providers={providers}", flush=True)

    import insightface
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", root=home, providers=providers)
    app.prepare(ctx_id=0, det_size=(args.det, args.det))
    swapper = insightface.model_zoo.get_model(
        os.path.join(home, "models", "inswapper_128.onnx"), providers=providers)

    ref = cv2.imread(args.ref)
    if ref is None:
        sys.exit("[facelock] không đọc được ảnh ref")
    ref_faces = app.get(ref)
    if not ref_faces:
        sys.exit("[facelock] NO_FACE_IN_REF: không thấy mặt trong ảnh mẫu")
    src_face = max(ref_faces, key=_area)

    cap = cv2.VideoCapture(args.inp)
    if not cap.isOpened():
        sys.exit("[facelock] không mở được video input")
    fps = cap.get(cv2.CAP_PROP_FPS) or 16
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ff = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:.4f}", "-i", "-",
         "-an", "-c:v", "libx264", "-preset", "fast", "-crf", str(args.crf),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.out],
        stdin=subprocess.PIPE)
    n = swapped = 0
    last_tgt = None
    last_gap = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        faces = app.get(frame)
        if faces:
            tgt = max(faces, key=_area)
            last_tgt, last_gap = tgt, 0
        elif last_tgt is not None and last_gap < 6:
            # frame lẻ không detect được (blur/nghiêng) — dùng lại vị trí mặt frame trước để khỏi
            # flicker identity (mặt swap rồi lại mặt gốc chớp qua lại). Hụt >6 frame liên tiếp
            # (quay lưng hẳn) thì thôi, giữ nguyên.
            tgt, last_gap = last_tgt, last_gap + 1
        else:
            tgt = None
        if tgt is not None:
            frame = _swap_blended(swapper, frame, tgt, src_face, args.blend, args.detail_keep)
            swapped += 1
        ff.stdin.write(frame.tobytes())
        n += 1
        if n % 25 == 0:
            print(f"[facelock] {n}/{N} swapped={swapped}", flush=True)
    ff.stdin.close()
    ff.wait()
    cap.release()
    if ff.returncode != 0:
        sys.exit("[facelock] ffmpeg encode lỗi")
    # frame không detect được mặt (quay lưng/che) giữ nguyên — swapped < n là bình thường
    print(f"[facelock] DONE frames={n} swapped={swapped}", flush=True)


if __name__ == "__main__":
    main()
