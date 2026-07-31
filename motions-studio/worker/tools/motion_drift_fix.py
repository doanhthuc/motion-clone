#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# #region ALD 24/07/2026 - DRIFT-FIX HẬU KỲ cho Motion Transfer: trị "kéo màu ngả dần sang tím".
# Gốc bệnh: chế độ autoregressive 81f/window (chốt 13/07) — window sau lấy frame window trước làm mồi,
# sai màu nhỏ của mỗi window CỘNG DỒN → clip tím dần theo thời lượng. Tool này sửa THUẦN HẬU KỲ trên
# file mp4 đã render (không đụng graph/sampler/thuật toán render): đo chroma-drift theo thời gian rồi
# áp gain per-channel đảo lại bằng sendcmd + colorchannelmixer (một lần re-encode, giữ fps/size/audio).
#
# Nguyên lý (chỉ sửa CAST màu, không đụng exposure/nội dung):
#   1. Decode toàn clip về 64×64 rgb24 → mean R,G,B từng frame (ffmpeg pipe, thuần python, không numpy).
#   2. Drift mô hình hoá bằng tỉ lệ chroma cr=R/G, cb=B/G — tím hoá = cr,cb tăng dần. Dùng tỉ lệ nên
#      miễn nhiễm thay đổi sáng/tối toàn khung (đổi đều 3 kênh không làm cr/cb nhúc nhích).
#   3. Neo về median <anchor-sec> giây đầu clip (đầu clip chưa kịp drift; hoặc --ref ảnh mẫu nếu muốn).
#      Gain sửa: k_r = cr0/cr(t), k_b = cb0/cb(t), kênh G giữ nguyên.
#   4. LÀM MƯỢT (moving average ±smooth/2) + clamp — KHÔNG match histogram per-frame, nên không tái
#      phát bệnh "flash/nhảy màu" từng khiến 27/06 phải tắt sạch mọi color-pass (RAW COLOR).
#   5. Bảo toàn luma từng frame (bù gain đồng nhất l(t) trên cả 3 kênh) → chỉ đổi sắc, không đổi sáng.
#   6. Áp bằng -vf sendcmd,colorchannelmixer bước 0.25s — drift vài % trải trên cả clip nên mỗi bước
#      chỉ lệch ~0.05%, mắt không thấy nấc. colorchannelmixer nhận command từ ffmpeg ≥ 4.4
#      (box .165 Ubuntu 22.04 = 4.4 → dự kiến OK, verify lại khi port vào worker).
#   7. Sau encode tự đo lại output và in bảng residual → mỗi lần chạy là một lần tự kiểm chứng.
#
# Dùng:
#   python3 motion_drift_fix.py input.mp4                  # sửa → input.driftfix.mp4 + bảng đo trước/sau
#   python3 motion_drift_fix.py input.mp4 --report-only    # chỉ đo drift, không encode (soi bệnh nhanh)
#   python3 motion_drift_fix.py input.mp4 --compare        # xuất thêm file trái=gốc | phải=đã-fix
#   python3 motion_drift_fix.py input.mp4 --ref anh_goc.jpg  # neo màu về ảnh mẫu thay vì 1s đầu clip
# Knob: --anchor-sec 1.0  --smooth-sec 2.0  --strength 1.0  --max-gain 1.33  --step 0.25  -o out.mp4
#
# ALD 27/07/2026 - ĐÃ NỐI VÀO WORKER: _apply_motion_drift_fix() trong worker_runtime/linux.py gọi file này
# (subprocess, --quiet --min-drift) NGAY TRƯỚC pass ESRGAN làm nét, nên ESRGAN không khuếch đại cast màu.
# Worker parse dòng cuối `DRIFTFIX_JSON {...}`; --min-drift cho phép bỏ qua encode khi clip vốn đã sạch.
# #endregion

import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile

PROBE_W = PROBE_H = 64          # đo mean màu trên khung nhỏ — đủ chính xác cho global cast, decode nhanh
LUMA = (0.2126, 0.7152, 0.0722)  # Rec.709
EPS = 1e-6


def probe(path):
    """fps + duration của stream video đầu tiên. VFR không mong đợi (pipeline xuất CFR)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate,r_frame_rate:format=duration",
         "-of", "json", path],
        capture_output=True, check=True).stdout
    j = json.loads(out or b"{}")
    st = (j.get("streams") or [{}])[0]

    def _fr(s):
        try:
            n, d = (s or "0/1").split("/")
            return float(n) / float(d) if float(d or 0) else 0.0
        except Exception:
            return 0.0

    fps = _fr(st.get("avg_frame_rate")) or _fr(st.get("r_frame_rate")) or 16.0
    if not (1.0 <= fps <= 120.0):
        fps = 16.0
    try:
        dur = float((j.get("format") or {}).get("duration") or 0.0)
    except Exception:
        dur = 0.0
    return fps, dur


def frame_means(path):
    """Mean R,G,B (0..255) từng frame. Ảnh tĩnh cũng dùng được (trả 1 phần tử)."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path,
         "-vf", f"scale={PROBE_W}:{PROBE_H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    fsz = PROBE_W * PROBE_H * 3
    px = PROBE_W * PROBE_H
    means = []
    for i in range(len(raw) // fsz):
        a = array.array("B", raw[i * fsz:(i + 1) * fsz])
        means.append((sum(a[0::3]) / px, sum(a[1::3]) / px, sum(a[2::3]) / px))
    return means


def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _movavg(v, win):
    """Moving average có tâm (cửa sổ co tự nhiên ở biên) — prefix sum, O(n)."""
    if win <= 1 or len(v) <= 2:
        return list(v)
    half = win // 2
    pre = [0.0]
    for x in v:
        pre.append(pre[-1] + x)
    out = []
    for i in range(len(v)):
        a = max(0, i - half)
        b = min(len(v), i + half + 1)
        out.append((pre[b] - pre[a]) / (b - a))
    return out


def chroma_series(means):
    cr = [r / max(g, EPS) for r, g, b in means]
    cb = [b / max(g, EPS) for r, g, b in means]
    return cr, cb


def compute_gains(means, fps, args, ref_mean=None):
    """Trả (gains per frame [(rr,gg,bb)], cr0, cb0). Toàn bộ trên log-gain để smooth/clamp đối xứng."""
    cr, cb = chroma_series(means)
    if ref_mean:
        r0, g0, b0 = ref_mean
        cr0 = r0 / max(g0, EPS)
        cb0 = b0 / max(g0, EPS)
    else:
        n_anchor = max(1, int(round(args.anchor_sec * fps)))
        cr0 = _median(cr[:n_anchor])
        cb0 = _median(cb[:n_anchor])

    lmax = math.log(max(1.01, args.max_gain))
    lr = [max(-lmax, min(lmax, math.log(max(EPS, cr0 / max(c, EPS))))) for c in cr]
    lb = [max(-lmax, min(lmax, math.log(max(EPS, cb0 / max(c, EPS))))) for c in cb]
    win = max(1, int(round(args.smooth_sec * fps)) | 1)  # lẻ hoá để có tâm
    lr = _movavg(lr, win)
    lb = _movavg(lb, win)

    gains = []
    for i, (r, g, b) in enumerate(means):
        kr = math.exp(lr[i] * args.strength)
        kb = math.exp(lb[i] * args.strength)
        # clamp lần cuối (strength > 1 có thể đẩy vượt trần)
        kr = max(1.0 / args.max_gain, min(args.max_gain, kr))
        kb = max(1.0 / args.max_gain, min(args.max_gain, kb))
        # Bảo toàn luma của frame: chỉnh R/B làm luma xê dịch nhẹ → bù gain đồng nhất l
        luma = LUMA[0] * r + LUMA[1] * g + LUMA[2] * b
        luma_fix = LUMA[0] * kr * r + LUMA[1] * g + LUMA[2] * kb * b
        l = max(0.9, min(1.1, luma / max(luma_fix, EPS)))
        gains.append((kr * l, l, kb * l))
    return gains, cr0, cb0


def build_sendcmd(gains, fps, step, path):
    """File lệnh cho -vf sendcmd: mỗi <step> giây chốt lại rr/gg/bb theo gain của frame tương ứng."""
    n = len(gains)
    dur = n / fps
    lines = []
    t = 0.0
    while t < dur:
        i = min(n - 1, int(round(t * fps)))
        rr, gg, bb = gains[i]
        ts = f"{t:.3f}"
        lines.append(f"{ts} colorchannelmixer rr {rr:.5f};")
        lines.append(f"{ts} colorchannelmixer gg {gg:.5f};")
        lines.append(f"{ts} colorchannelmixer bb {bb:.5f};")
        t += step
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def apply_fix(src, dst, cmdfile, crf=17, preset="veryfast"):
    vf = f"sendcmd=f='{cmdfile}',colorchannelmixer,format=yuv420p"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-stats", "-i", src,
         "-vf", vf, "-c:v", "libx264", "-preset", str(preset), "-crf", str(crf),
         "-c:a", "copy", "-movflags", "+faststart", dst],
        check=True)


def make_compare(src, fixed, dst):
    """Trái = nửa trái file gốc, phải = nửa phải file đã fix — seam ở giữa để soi bằng mắt."""
    fc = ("[0:v]crop=floor(iw/4)*2:ih:0:0[l];"
          "[1:v]crop=floor(iw/4)*2:ih:iw-floor(iw/4)*2:0[r];"
          "[l][r]hstack=2,format=yuv420p")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-i", fixed,
         "-filter_complex", fc, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-an", "-movflags", "+faststart", dst],
        check=True)


def drift_report(means, fps, cr0, cb0, label, quiet=False):
    """Bảng drift theo giây: Δcr/Δcb (%) so với mốc neo. Trả (max|Δcr|, max|Δcb|) %."""
    cr, cb = chroma_series(means)
    dcr = [(c / max(cr0, EPS) - 1.0) * 100.0 for c in cr]
    dcb = [(c / max(cb0, EPS) - 1.0) * 100.0 for c in cb]
    dur = len(means) / fps
    step = 1 if dur <= 24 else 2 if dur <= 48 else 5
    if not quiet:
        print(f"\n  {label} — drift chroma so với mốc neo (cr=R/G, cb=B/G):")
        print("    giây |   Δcr%  |   Δcb%")
        t = 0
        while t < dur:
            i = min(len(means) - 1, int(t * fps))
            print(f"    {t:4d} | {dcr[i]:+6.2f} | {dcb[i]:+6.2f}")
            t += step
        i = len(means) - 1
        print(f"    cuối | {dcr[i]:+6.2f} | {dcb[i]:+6.2f}")
    mcr = max(abs(x) for x in dcr)
    mcb = max(abs(x) for x in dcb)
    if not quiet:
        print(f"    → max |Δcr| = {mcr:.2f}%   max |Δcb| = {mcb:.2f}%")
    return mcr, mcb


def main():
    ap = argparse.ArgumentParser(
        description="Sửa drift màu (ngả tím dần) của video Motion Transfer bằng hậu kỳ thuần — "
                    "không đụng thuật toán render. Tự đo lại output sau khi sửa.")
    ap.add_argument("input", help="file mp4 cần sửa")
    ap.add_argument("-o", "--output", default=None, help="file ra (mặc định: <input>.driftfix.mp4)")
    ap.add_argument("--ref", default=None, help="ảnh mẫu để neo màu (mặc định: neo 1s đầu clip)")
    ap.add_argument("--anchor-sec", type=float, default=1.0, help="số giây đầu clip dùng làm mốc neo (median)")
    ap.add_argument("--smooth-sec", type=float, default=2.0, help="cửa sổ làm mượt gain (giây) — to hơn = lì hơn với biến động nội dung")
    ap.add_argument("--strength", type=float, default=1.0, help="cường độ sửa (1.0 = đảo đủ drift đo được)")
    ap.add_argument("--max-gain", type=float, default=1.33, help="trần gain mỗi kênh (an toàn)")
    ap.add_argument("--step", type=float, default=0.25, help="bước cập nhật gain theo thời gian (giây)")
    ap.add_argument("--report-only", action="store_true", help="chỉ đo và in bảng drift, không encode")
    ap.add_argument("--compare", action="store_true", help="xuất thêm file so sánh trái=gốc/phải=fix")
    # #region ALD 27/07/2026 - knob cho worker gọi tự động (xem _apply_motion_drift_fix trong worker_runtime/linux.py)
    ap.add_argument("--min-drift", type=float, default=0.0,
                    help="drift đo được (max|Δcr|,|Δcb| %%) dưới ngưỡng này thì BỎ QUA encode (0 = luôn encode)")
    ap.add_argument("--crf", type=int, default=17, help="chất lượng x264 khi encode bản sửa")
    ap.add_argument("--preset", default="veryfast", help="preset x264 khi encode bản sửa")
    ap.add_argument("--quiet", action="store_true", help="không in bảng drift theo giây (worker chỉ cần dòng JSON)")
    # #endregion
    args = ap.parse_args()

    src = args.input
    if not os.path.isfile(src):
        sys.exit(f"Không thấy file: {src}")
    dst = args.output or (os.path.splitext(src)[0] + ".driftfix.mp4")

    def _summary(applied, out_path, before, after, note=""):
        """Dòng cuối máy đọc — worker parse dòng này (xem linux.py). Luôn in, kể cả khi bỏ qua."""
        print("DRIFTFIX_JSON " + json.dumps({
            "applied": bool(applied), "output": out_path, "note": note,
            "before": {"cr": round(before[0], 3), "cb": round(before[1], 3)},
            "after": ({"cr": round(after[0], 3), "cb": round(after[1], 3)} if after else None),
        }, ensure_ascii=False))

    fps, dur = probe(src)
    if not args.quiet:
        print(f"Đo màu từng frame: {os.path.basename(src)} (fps={fps:.2f}, ~{dur:.1f}s)…")
    means = frame_means(src)
    if len(means) < 8:
        _summary(False, src, (0.0, 0.0), None, "clip quá ngắn (<8 frame)")
        sys.exit("Clip quá ngắn (<8 frame) — không có gì để sửa.")

    ref_mean = None
    if args.ref:
        ref_frames = frame_means(args.ref)
        if not ref_frames:
            _summary(False, src, (0.0, 0.0), None, f"không đọc được ảnh ref: {args.ref}")
            sys.exit(f"Không đọc được ảnh ref: {args.ref}")
        ref_mean = ref_frames[0]
        if not args.quiet:
            print(f"Neo màu theo ảnh ref: {args.ref}")

    gains, cr0, cb0 = compute_gains(means, fps, args, ref_mean)
    before = drift_report(means, fps, cr0, cb0, "TRƯỚC (input)", quiet=args.quiet)

    if args.report_only:
        _summary(False, src, before, None, "report-only")
        if not args.quiet:
            print("\n--report-only: dừng ở bước đo.")
        return

    # Clip sạch (drift dưới ngưỡng) → KHÔNG re-encode: giữ nguyên bản gốc, tránh mất một thế hệ nén vô ích.
    if args.min_drift > 0 and max(before) < args.min_drift:
        _summary(False, src, before, None, f"drift {max(before):.2f}% < ngưỡng {args.min_drift:.2f}% — giữ nguyên")
        if not args.quiet:
            print(f"\nDrift {max(before):.2f}% dưới ngưỡng {args.min_drift:.2f}% — bỏ qua, giữ file gốc.")
        return

    fd, cmdfile = tempfile.mkstemp(suffix=".cmd", prefix="driftfix_")
    os.close(fd)
    try:
        build_sendcmd(gains, fps, args.step, cmdfile)
        if not args.quiet:
            print(f"\nEncode bản sửa → {dst}")
        apply_fix(src, dst, cmdfile, crf=args.crf, preset=args.preset)
    finally:
        try:
            os.remove(cmdfile)
        except OSError:
            pass

    # Tự kiểm chứng: đo lại output với CÙNG mốc neo — drift phải phẳng về ~0
    means_out = frame_means(dst)
    if ref_mean:
        cr1, cb1 = cr0, cb0
    else:
        n_anchor = max(1, int(round(args.anchor_sec * fps)))
        cr_o, cb_o = chroma_series(means_out)
        cr1 = _median(cr_o[:n_anchor])
        cb1 = _median(cb_o[:n_anchor])
    mcr, mcb = drift_report(means_out, fps, cr1, cb1, "SAU (output)", quiet=args.quiet)
    verdict = "PHẲNG (drift đã triệt)" if max(mcr, mcb) < 1.5 else \
        "còn residual — thử --smooth-sec nhỏ hơn hoặc --strength 1.1-1.3"
    if not args.quiet:
        print(f"\n  Kết luận tự đo: {verdict}")

    if args.compare:
        cmp_path = os.path.splitext(dst)[0] + ".compare.mp4"
        print(f"Xuất file so sánh (trái=gốc | phải=fix) → {cmp_path}")
        make_compare(src, dst, cmp_path)

    _summary(True, dst, before, (mcr, mcb), verdict)
    if not args.quiet:
        print(f"\nXong: {dst}")


if __name__ == "__main__":
    main()
