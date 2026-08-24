#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đo lệch MÀU của output so với driver — công cụ chấm lô A/B "màu nhạt / ngả vàng".

VÌ SAO CẦN: chấm màu bằng mắt trên 5 ô A/B là cách chắc chắn nhất để cãi nhau mà không kết luận
được gì. Script này in ra đúng bộ số đã dùng để TÌM RA bệnh (23/08/2026), nên lô A/B được chấm
bằng cùng một cây thước với lúc chẩn đoán.

ĐO Ở HAI VÙNG, vì bệnh chỉ nằm ở một trong hai:
  • NỀN (mặc định dải 12% trên cùng) — với character-swap thì nền đến THẲNG từ driver qua mask,
    nên driver là ground truth. Nền lệch = model làm hỏng cả khung.
  • MẶT (box do người gõ vào) — vùng model vẽ lại. Đây là chỗ bệnh thật sự nằm: đo 23/08 cho thấy
    da mất 18–44% biên độ màu so với cả ảnh mẫu lẫn driver, trong khi nền vẫn sạch.

ĐỌC SỐ THẾ NÀO:
  R-B = biên độ ấm. R-G = mức đỏ so với lục; da người khoẻ thì cả hai đều dương và khá lớn.
  Bệnh "nhạt + ngả vàng" = R-B và R-G CÙNG tụt, R-G tụt nhanh hơn (mất đỏ trước) → da ngả kem.
  Y = độ sáng. Y khớp driver mà R-B/R-G tụt → KHÔNG phải lỗi phơi sáng, mà là mất sắc độ.
  SAT/dynrange lấy từ ffmpeg signalstats, dùng để bắt ca "nền bị bệt" (dandong8 mất 27% SAT nền).

⚠ Box mặt là thủ công và KHÔNG bám theo người khi họ di chuyển. Nó chỉ dùng để so cùng một mốc
  thời gian giữa các ô A/B (mọi ô cùng seed, cùng driver → người đứng gần như cùng chỗ). Đừng
  đem con số tuyệt đối của một box đi so với clip khác.

Dùng:
  # so cả lô A/B với driver, chỉ vùng nền
  python3 scripts/color_probe.py --driver .smoke/dandong8.mp4 out/*/runs/c*/01-character-swap.mp4

  # thêm vùng mặt: --face x,y,w,h theo TỈ LỆ khung (0..1), + mốc thời gian
  python3 scripts/color_probe.py --driver .smoke/dandong8.mp4 --face 0.30,0.10,0.34,0.20 --at 6 \\
      out/2026-08-23-.../runs/c0-control/01-character-swap.mp4
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

# Dải nền mặc định: 12% trên cùng. Chọn vì với clip dọc quay người thì đầu khung gần như luôn là
# trần/tường/hậu cảnh — phần character-swap KHÔNG vẽ lại. Clip nào có người chạm mép trên thì phải
# tự đổi bằng --bg.
BG_DEFAULT = "iw:ih*0.12:0:0"
LUMA = (0.2126, 0.7152, 0.0722)


def _mean_rgb(path: str, crop: str | None, seconds: float, at: float | None) -> tuple[float, float, float] | None:
    """Trung bình R,G,B. Thủ thuật: scale=1:1:flags=area ép ffmpeg tự tính trung bình cả khung,
    nên không cần numpy và không phải kéo pixel về python."""
    cmd = ["ffmpeg", "-v", "error"]
    if at is not None:
        cmd += ["-ss", str(at)]
    else:
        cmd += ["-t", str(seconds)]
    cmd += ["-i", path]
    if at is not None:
        cmd += ["-frames:v", "1"]
    vf = (crop + ",") if crop else ""
    cmd += ["-vf", vf + "scale=1:1:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    b = subprocess.run(cmd, capture_output=True).stdout
    n = len(b) // 3
    if not n:
        return None
    return sum(b[0::3]) / n, sum(b[1::3]) / n, sum(b[2::3]) / n


def _signalstats(path: str, crop: str, seconds: float) -> dict | None:
    """SAT/dynrange qua lavfi. Lấy mẫu 1/10 frame — đủ cho thống kê toàn cục, nhanh hơn 10 lần.
    ⚠ frame_tags trả về theo THỨ TỰ NỘI BỘ của signalstats (YMIN,YLOW,YAVG,...) chứ không theo
    thứ tự mình xin, nên phải đọc bằng TÊN (json) — đọc theo cột csv là lệch, đã dính một lần."""
    cmd = ["ffprobe", "-v", "error", "-f", "lavfi", "-i",
           f"movie={path},trim=0:{seconds},{crop},select='not(mod(n\\,10))',signalstats",
           "-show_entries", "frame_tags", "-of", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    frames = [f["tags"] for f in json.loads(out or '{"frames":[]}').get("frames", []) if "tags" in f]
    if not frames:
        return None
    g = lambda k: statistics.mean(float(t["lavfi.signalstats." + k]) for t in frames)
    return {"SAT": g("SATAVG"), "dyn": g("YHIGH") - g("YLOW")}


def _fmt(label: str, rgb, stats=None, base=None) -> str:
    R, G, B = rgb
    y = LUMA[0] * R + LUMA[1] * G + LUMA[2] * B
    line = (f"  {label:22s} R={R:6.1f} G={G:6.1f} B={B:6.1f} | "
            f"R-B={R - B:+6.1f} R-G={R - G:+6.1f} | Y={y:6.1f}")
    if stats:
        line += f" | SAT={stats['SAT']:5.2f} dyn={stats['dyn']:6.1f}"
    if base:  # so với driver: mất bao nhiêu % biên độ ấm
        bR, bG, bB = base
        b_amp = bR - bB
        if abs(b_amp) > 1e-6:
            line += f"  ⇒ R-B còn {100 * (R - B) / b_amp:5.1f}% của driver"
    return line


def main() -> int:
    ap = argparse.ArgumentParser(description="Đo lệch màu output vs driver")
    ap.add_argument("outputs", nargs="+", help="các file output cần chấm")
    ap.add_argument("--driver", required=True, help="video driver = ground truth cho vùng nền")
    ap.add_argument("--bg", default=BG_DEFAULT, help=f"crop vùng nền (mặc định {BG_DEFAULT})")
    ap.add_argument("--face", default="", help="box mặt theo tỉ lệ khung: x,y,w,h (vd 0.30,0.10,0.34,0.20)")
    ap.add_argument("--at", type=float, default=None, help="mốc giây để đo vùng mặt (bắt buộc nếu có --face)")
    ap.add_argument("--seconds", type=float, default=15.0, help="đo vùng nền trong bao nhiêu giây đầu")
    a = ap.parse_args()

    face_crop = None
    if a.face:
        try:
            x, y, w, h = (float(v) for v in a.face.split(","))
        except ValueError:
            print(f"--face phải là 4 số cách nhau bởi dấu phẩy, nhận được: {a.face!r}", file=sys.stderr)
            return 2
        if a.at is None:
            print("--face cần đi kèm --at <giây>: box thủ công không bám theo người, "
                  "so hai clip ở hai mốc thời gian khác nhau là vô nghĩa.", file=sys.stderr)
            return 2
        face_crop = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}"

    print(f"NỀN (ground truth = driver) · {a.seconds:.0f}s đầu · crop={a.bg}")
    drv_bg = _mean_rgb(a.driver, f"crop={a.bg}", a.seconds, None)
    if drv_bg is None:
        print(f"không đọc được driver: {a.driver}", file=sys.stderr)
        return 1
    print(_fmt("driver", drv_bg, _signalstats(a.driver, f"crop={a.bg}", a.seconds)))
    for f in a.outputs:
        rgb = _mean_rgb(f, f"crop={a.bg}", a.seconds, None)
        if rgb is None:
            print(f"  {Path(f).parent.name:22s} KHÔNG ĐỌC ĐƯỢC")
            continue
        print(_fmt(Path(f).parent.name or Path(f).stem, rgb,
                   _signalstats(f, f"crop={a.bg}", a.seconds)))

    if face_crop:
        print(f"\nMẶT (vùng model vẽ lại) · t={a.at}s · {face_crop}")
        drv_face = _mean_rgb(a.driver, face_crop, a.seconds, a.at)
        if drv_face:
            print(_fmt("driver", drv_face))
            for f in a.outputs:
                rgb = _mean_rgb(f, face_crop, a.seconds, a.at)
                if rgb:
                    print(_fmt(Path(f).parent.name or Path(f).stem, rgb, base=drv_face))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
