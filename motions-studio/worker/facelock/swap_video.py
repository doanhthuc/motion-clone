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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="ảnh mẫu (nguồn identity)")
    ap.add_argument("--inp", required=True, help="video Wan output")
    ap.add_argument("--out", required=True)
    ap.add_argument("--det", type=int, default=640, help="det_size SCRFD")
    ap.add_argument("--crf", type=int, default=15)
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
            frame = swapper.get(frame, tgt, src_face, paste_back=True)
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
