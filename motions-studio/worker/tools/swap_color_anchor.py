#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# #region ALD 23/08/2026 - NEO MÀU THEO DRIVER cho character-swap.
#
# BỆNH: ở chế độ Mix (replacement), nền KHÔNG phải bản sao pixel của driver — nó là frame driver bị
# đục lỗ vùng người rồi chạy lại qua VAE, nên ăn nguyên phần nén chroma của model. Đo 23/08 trên
# out/2026-08-23-1732 + out/2026-08-23-2242: clip nền phức tạp (dandong8, dynamic range 112) chỉ giữ
# 53% biên độ cr và mất 29% SAT; ba clip nền đơn giản (dynrange 23–61) bám driver trong ±2/255.
# Tức bệnh CÓ THẬT nhưng KHÔNG PHẢI clip nào cũng dính → tool này phải tự bỏ qua clip sạch.
#
# VÌ SAO KHÔNG SỬA Ở TẦNG RENDER: lô A/B 5 ô ngày 23/08 (~40 phút GPU) đã bác bỏ cả ba đường
# render-side. negative prompt vô tác dụng vì sampler chạy cfg=1 (build_wan_workflow:1432) nên nhánh
# unconditional không được đánh giá — ba ô đổi negative cho ra khung hình TRÙNG BYTE. Neo dương bằng
# positive prompt được +2%, hạ lora_relight 1.0→0.5 được +3%: đều là mức nhiễu.
#
# KHÁC GÌ motion_drift_fix.py: file đó neo về 1s ĐẦU CỦA CHÍNH CLIP để triệt drift theo thời gian —
# nó không biết màu ĐÚNG là màu gì. Ở swap thì driver LÀ ground truth cho nền, nên ở đây neo theo
# CHÍNH DRIVER, từng frame một. Toàn bộ phần smooth/clamp/bảo toàn luma/sendcmd dùng lại của file đó,
# một nguồn sự thật.
#
# ĐO Ở ĐÂU — hai lớp, mỗi lớp trị một cái bẫy khác nhau:
#   Cần đo chroma trên đúng phần khung hình mà driver là chân lý (nền), bỏ vùng người (đến từ ảnh mẫu,
#   khác driver là ĐÚNG chứ không phải lỗi).
#   Lớp 1 — lọc thô theo |ΔY| (phân vị, --keep). KHÔNG lọc theo "độ lệch màu" được: làm thế là tự bắn
#     vào chân, vì đúng những pixel không lệch mới được giữ nên gain luôn ≈ 1, sửa được số 0. |ΔY|
#     thoát bẫy đó vì lỗi cần sửa là lỗi SẮC (luma gần như giữ nguyên) nên nền dù sai màu vẫn có |ΔY|
#     nhỏ, còn vùng thay người thì lớn. Dùng phân vị để khỏi chỉnh ngưỡng tay cho từng clip sáng/tối.
#   Lớp 2 — hàng rào MAD trên log tỉ lệ chroma từng pixel, RỒI mới lấy trung bình gộp. Lớp 1 một
#     mình KHÔNG ĐỦ: bản chỉ có lớp 1 làm HỎNG clip vốn lành (nhanvat1__dandong-3, nền đang bám driver
#     +28,1 vs +27,7 bị đẩy xuống +24,1) vì clip đó người chiếm khung to và áo khác hẳn driver — vài
#     pixel người lọt lưới là đủ kéo lệch trung bình. MAD loại hẳn chúng.
#     Đã thử và LOẠI: dùng trung vị làm ước lượng luôn (thay vì chỉ làm hàng rào). Chống outlier tốt
#     nhưng sai bản chất — nén chroma là nén biên độ lệch khỏi xám, mà phần đông pixel vốn gần xám nên
#     tỉ lệ của chúng ≈ 1 và kéo trung vị về 1: báo "sạch" cả trên clip đang bệnh rõ. Trung bình gộp
#     mới là thứ khớp với cái mắt nhìn thấy.
#
# Dùng:
#   python3 swap_color_anchor.py out.mp4 --driver driver.mp4              # sửa → out.coloranchor.mp4
#   python3 swap_color_anchor.py out.mp4 --driver driver.mp4 --report-only  # chỉ đo, không encode
#   python3 swap_color_anchor.py out.mp4 --driver driver.mp4 --compare      # thêm file trái/phải
# Worker gọi: _apply_swap_color_anchor() trong worker_runtime/linux.py (--quiet --min-drift).
# #endregion

from __future__ import annotations

import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from motion_drift_fix import (  # noqa: E402  - cùng thư mục, import sau khi chỉnh sys.path
    EPS, LUMA, _median, _movavg, apply_fix, build_sendcmd, make_compare, probe,
)

PROBE_W = PROBE_H = 64          # như motion_drift_fix: đủ cho cast toàn cục, decode nhanh
PX = PROBE_W * PROBE_H


def paired_frames(out_path, drv_path, fps):
    """Giải mã CẢ HAI về cùng fps + 64×64 rgb24, trả list frame (array 'B') đã cắt về độ dài chung.

    Ép cùng fps vì driver có thể dài hơn output (preset drv-Ns chỉ lấy N giây đầu) và đôi khi lệch
    fps. Cắt về min() thay vì báo lỗi: thiếu vài frame cuối không ảnh hưởng gain đã được làm mượt.
    """
    def _decode(p):
        raw = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", p,
             "-vf", f"fps={fps},scale={PROBE_W}:{PROBE_H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        fsz = PX * 3
        return [array.array("B", raw[i * fsz:(i + 1) * fsz]) for i in range(len(raw) // fsz)]

    a, b = _decode(out_path), _decode(drv_path)
    n = min(len(a), len(b))
    return a[:n], b[:n]


G_FLOOR, G_CEIL = 16, 245       # bỏ pixel quá tối (tỉ lệ R/G nổ) và pixel cháy sáng (đã clip, mất tin)
FENCE_K = 3.0                   # 3σ
SCALE_MIN = 0.03                # sàn σ trên log tỉ lệ — xem chú thích trong frame_ratio


def _mad(v, med):
    return _median([abs(x - med) for x in v]) or EPS


def frame_ratio(out_f, drv_f, keep):
    """Gain chroma driver/output của MỘT frame. Kèm mean RGB output (để bù luma sau).

    Ước lượng = TRUNG BÌNH GỘP trên tập pixel nền: kr = (ΣRd/ΣGd) / (ΣRo/ΣGo).

    ⚠ Hai cách làm sai đã thử và loại, ghi lại để khỏi ai quay lại:
      • Trung bình gộp trên toàn bộ pixel qua lọc |ΔY| (keep 0.6): LÀM HỎNG clip vốn lành —
        nhanvat1__dandong-3 nền đang bám driver (R-B +28,1 vs +27,7) bị đẩy xuống +24,1. Clip đó
        người chiếm khung to, áo kem vs áo bạc hà của driver, nên vài pixel người lọt lưới là đủ
        kéo lệch trung bình.
      • Trung vị tỉ lệ chroma từng pixel: chống outlier tốt nhưng SAI ƯỚC LƯỢNG — nén chroma là nén
        biên độ lệch khỏi xám, mà phần đông pixel vốn gần xám nên tỉ lệ của chúng ≈ 1 và kéo trung vị
        về 1. Kết quả: báo "sạch" cả trên clip đang bệnh rõ (dandong8 tụt còn cr 0,46%).
    Nên giữ trung bình gộp (đúng thứ mắt thấy) và loại pixel người bằng một bước riêng: cắt outlier
    theo MAD trên log tỉ lệ chroma từng pixel. Pixel người có màu lệch hẳn → rơi ra ngoài hàng rào;
    pixel nền dù bị nén vẫn nằm sát nhau.
    """
    dy = []
    for i in range(PX):
        j = i * 3
        yo = LUMA[0] * out_f[j] + LUMA[1] * out_f[j + 1] + LUMA[2] * out_f[j + 2]
        yd = LUMA[0] * drv_f[j] + LUMA[1] * drv_f[j + 1] + LUMA[2] * drv_f[j + 2]
        dy.append((abs(yo - yd), i))
    dy.sort()
    n_keep = max(16, int(PX * keep))          # sàn 16 px: frame nào người chiếm gần hết vẫn có mẫu

    cand = []                                  # (i, log tỉ lệ cr, log tỉ lệ cb)
    so = [0.0, 0.0, 0.0]
    for _, i in dy[:n_keep]:
        j = i * 3
        ro, go, bo = out_f[j], out_f[j + 1], out_f[j + 2]
        rd, gd, bd = drv_f[j], drv_f[j + 1], drv_f[j + 2]
        so[0] += ro; so[1] += go; so[2] += bo
        if not (G_FLOOR <= go <= G_CEIL and G_FLOOR <= gd <= G_CEIL):
            continue
        if min(ro, rd, bo, bd) < 1:
            continue
        cand.append((i,
                     math.log((rd / gd) / (ro / go)),
                     math.log((bd / gd) / (bo / go))))
    mean_out = (so[0] / n_keep, so[1] / n_keep, so[2] / n_keep)
    if len(cand) < 32:                        # frame gần như toàn đen/cháy → không kết luận, để yên
        return 1.0, 1.0, mean_out

    mr = _median([c[1] for c in cand])
    mb = _median([c[2] for c in cand])
    # σ từ MAD, nhưng CÓ SÀN. Không có sàn thì khung nào có mảng phẳng lớn (tường trơn, trời) sẽ đẩy
    # MAD về ~0, hàng rào siết lại gần bằng 0 và cắt sạch đúng những pixel MANG tín hiệu → gain trả
    # về 1, pass tưởng clip sạch. Sàn 0.03 (→ hàng rào ±9%) an toàn vì lệch thật đo được chỉ 2–5%,
    # còn màu vùng người lệch xa hơn nhiều nên vẫn bị loại.
    sr = max(1.4826 * _mad([c[1] for c in cand], mr), SCALE_MIN)
    sb = max(1.4826 * _mad([c[2] for c in cand], mb), SCALE_MIN)
    keep_i = [c[0] for c in cand
              if abs(c[1] - mr) <= FENCE_K * sr and abs(c[2] - mb) <= FENCE_K * sb]
    if len(keep_i) < 32:
        return 1.0, 1.0, mean_out

    ao = [0.0, 0.0, 0.0]
    ad = [0.0, 0.0, 0.0]
    for i in keep_i:
        j = i * 3
        for c in range(3):
            ao[c] += out_f[j + c]
            ad[c] += drv_f[j + c]
    cr_o, cb_o = ao[0] / max(ao[1], EPS), ao[2] / max(ao[1], EPS)
    cr_d, cb_d = ad[0] / max(ad[1], EPS), ad[2] / max(ad[1], EPS)
    return cr_d / max(cr_o, EPS), cb_d / max(cb_o, EPS), mean_out


def measure(out_path, drv_path, fps, keep):
    """Trả (mean_out[], rr[], rb[], dcr[], dcb[]).

    rr/rb = gain CẦN áp (driver/output). dcr/dcb = % lệch hiện tại của output so với driver, tức
    nghịch đảo của rr/rb — in ra cho người đọc, không dùng để tính gain.
    """
    fa, fb = paired_frames(out_path, drv_path, fps)
    mo, rr, rb, dcr, dcb = [], [], [], [], []
    for x, y in zip(fa, fb):
        kr, kb, m = frame_ratio(x, y, keep)
        mo.append(m)
        rr.append(kr)
        rb.append(kb)
        dcr.append((1.0 / max(kr, EPS) - 1.0) * 100.0)
        dcb.append((1.0 / max(kb, EPS) - 1.0) * 100.0)
    return mo, rr, rb, dcr, dcb


def compute_gains(means_out, rr, rb, fps, args):
    """Gain per frame đưa chroma output về chroma driver CÙNG FRAME.

    Làm trên log-gain để smooth/clamp đối xứng, y hệt motion_drift_fix.compute_gains — khác đúng chỗ
    mốc neo là chuỗi driver theo thời gian thay vì một hằng số.
    """
    lmax = math.log(max(1.01, args.max_gain))
    lr = [max(-lmax, min(lmax, math.log(max(EPS, k)))) for k in rr]
    lb = [max(-lmax, min(lmax, math.log(max(EPS, k)))) for k in rb]
    win = max(1, int(round(args.smooth_sec * fps)) | 1)
    lr, lb = _movavg(lr, win), _movavg(lb, win)

    gains = []
    for i, (r, g, b) in enumerate(means_out):
        kr = math.exp(lr[i] * args.strength)
        kb = math.exp(lb[i] * args.strength)
        kr = max(1.0 / args.max_gain, min(args.max_gain, kr))
        kb = max(1.0 / args.max_gain, min(args.max_gain, kb))
        # Bảo toàn luma: chỉ sửa SẮC. Không kéo độ sáng về driver — người trong khung là người KHÁC,
        # ép luma là ép cả vùng nhân vật về độ sáng của người trong driver.
        luma = LUMA[0] * r + LUMA[1] * g + LUMA[2] * b
        luma_fix = LUMA[0] * kr * r + LUMA[1] * g + LUMA[2] * kb * b
        l = max(0.9, min(1.1, luma / max(luma_fix, EPS)))
        gains.append((kr * l, l, kb * l))
    return gains


def report(dcr, dcb, fps, label, quiet=False):
    """Bảng lệch chroma so với driver theo giây. Trả (median|Δcr|, median|Δcb|) — dùng MEDIAN chứ
    không dùng max: một frame người che gần hết khung là mask hỏng, max sẽ bị frame đó lái."""
    if not quiet and dcr:
        dur = len(dcr) / fps
        step = 1 if dur <= 24 else 2 if dur <= 48 else 5
        print(f"\n  {label} — lệch chroma so với DRIVER (cr=R/G, cb=B/G):")
        print("    giây |   Δcr%  |   Δcb%")
        t = 0
        while t < dur:
            i = min(len(dcr) - 1, int(t * fps))
            print(f"    {t:4d} | {dcr[i]:+6.2f} | {dcb[i]:+6.2f}")
            t += step
    mcr = _median([abs(x) for x in dcr]) if dcr else 0.0
    mcb = _median([abs(x) for x in dcb]) if dcb else 0.0
    if not quiet:
        print(f"    → median |Δcr| = {mcr:.2f}%   median |Δcb| = {mcb:.2f}%")
    return mcr, mcb


def main():
    ap = argparse.ArgumentParser(
        description="Neo màu output character-swap về đúng driver (hậu kỳ thuần, không đụng render). "
                    "Tự đo lại sau khi sửa.")
    ap.add_argument("input", help="output character-swap cần neo màu")
    ap.add_argument("--driver", required=True, help="video driver — ground truth màu cho vùng nền")
    ap.add_argument("-o", "--output", default=None, help="file ra (mặc định <input>.coloranchor.mp4)")
    ap.add_argument("--keep", type=float, default=0.95,
                    help="tỉ lệ pixel qua lọc thô |ΔY|. CỐ Ý để cao: lọc này chỉ vứt lõi vùng người, "
                         "việc loại người là của hàng rào MAD. Quét 23/08 trên dandong8: keep 0.60 khép "
                         "được 23%% khoảng cách, 0.85 được 51%%, 0.95 được 77%% — vì keep thấp nghĩa là "
                         "chỉ lấy mẫu đúng những pixel model tái tạo TỐT nhất, tự làm loãng ước lượng")
    ap.add_argument("--smooth-sec", type=float, default=2.0, help="cửa sổ làm mượt gain (giây)")
    ap.add_argument("--strength", type=float, default=1.0, help="cường độ (1.0 = kéo hết về driver)")
    ap.add_argument("--max-gain", type=float, default=1.20,
                    help="trần gain mỗi kênh. Chặt hơn drift-fix (1.33) vì ở đây sai số mask có thể "
                         "đẩy gain đi xa, mà lệch thật đo được chỉ cỡ 2–5%%")
    ap.add_argument("--step", type=float, default=0.25, help="bước cập nhật gain (giây)")
    ap.add_argument("--min-drift", type=float, default=1.2,
                    help="lệch dưới ngưỡng này (%%) thì BỎ QUA encode — clip nền đơn giản vốn đã đúng màu. "
                         "1.2 chọn từ đo thật 23/08: clip bệnh 2.21%%, ba clip lành 0.77/0.55/0.21%% (biên 2.9×)")
    ap.add_argument("--report-only", action="store_true", help="chỉ đo, không encode")
    ap.add_argument("--compare", action="store_true", help="xuất thêm file trái=gốc/phải=đã neo")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--quiet", action="store_true", help="chỉ in dòng JSON cho worker")
    args = ap.parse_args()

    src = args.input
    for p in (src, args.driver):
        if not os.path.isfile(p):
            sys.exit(f"Không thấy file: {p}")
    dst = args.output or (os.path.splitext(src)[0] + ".coloranchor.mp4")

    def _summary(applied, out_path, before, after, note=""):
        print("COLORANCHOR_JSON " + json.dumps({
            "applied": bool(applied), "output": out_path, "note": note,
            "before": {"cr": round(before[0], 3), "cb": round(before[1], 3)},
            "after": ({"cr": round(after[0], 3), "cb": round(after[1], 3)} if after else None),
        }, ensure_ascii=False))

    fps, dur = probe(src)
    if not args.quiet:
        print(f"Đo màu vs driver: {os.path.basename(src)} (fps={fps:.2f}, ~{dur:.1f}s), "
              f"giữ {args.keep:.0%} pixel nền…")
    mo, rr, rb, dcr, dcb = measure(src, args.driver, fps, args.keep)
    if len(mo) < 8:
        _summary(False, src, (0.0, 0.0), None, "clip quá ngắn (<8 frame)")
        sys.exit("Clip quá ngắn (<8 frame).")

    before = report(dcr, dcb, fps, "TRƯỚC", args.quiet)
    if args.report_only:
        _summary(False, src, before, None, "report-only")
        return
    if args.min_drift > 0 and max(before) < args.min_drift:
        _summary(False, src, before, None,
                 f"lệch {max(before):.2f}% < ngưỡng {args.min_drift:.2f}% — clip vốn đã đúng màu")
        return

    gains = compute_gains(mo, rr, rb, fps, args)
    with tempfile.TemporaryDirectory() as td:
        cmdfile = os.path.join(td, "anchor.cmd")
        build_sendcmd(gains, fps, args.step, cmdfile)
        apply_fix(src, dst, cmdfile, crf=args.crf, preset=args.preset)

    # Tự kiểm chứng: đo lại chính file vừa xuất. Mỗi lần chạy là một lần chứng minh nó có tác dụng.
    _, _, _, dcr2, dcb2 = measure(dst, args.driver, fps, args.keep)
    after = report(dcr2, dcb2, fps, "SAU", args.quiet)
    if args.compare:
        cmp_path = os.path.splitext(dst)[0] + ".compare.mp4"
        make_compare(src, dst, cmp_path)
        if not args.quiet:
            print(f"  file so sánh: {cmp_path}")
    _summary(True, dst, before, after)


if __name__ == "__main__":
    raise SystemExit(main())
