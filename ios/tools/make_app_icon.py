"""Draw the app icon (light/dark/tinted) and the launch-screen mark into the asset catalog.

The mark was picked from Gemini concepts on 2026-09-25 and redrawn here so edges
are crisp and the lime is the exact `Theme.accent`. StackLoader.swift animates
the same geometry; change both together. Run: python3 ios/tools/make_app_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw
SS = 4
SHEAR = 0.307                       # cards rise to the right
W, H, R, GAP = 398, 460, 46, 20     # card geometry on a 1024 canvas at scale 1
STEP = (-145, -62)                  # offset back from each card to the one behind it
BG = (10, 10, 12)
LIME = [(0x5E, 0x7A, 0x2A), (0x8F, 0xA8, 0x52), (0xB9, 0xDD, 0x6B)]   # back, mid, front
GRAY = [(110,) * 3, (170,) * 3, (255,) * 3]

def card_mask(n, k, left, top, grow=0):
    s = lambda v: v * k * SS
    m = Image.new("L", (n, n), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (s(left - grow), s(top - grow), s(left + W + grow), s(top + H + grow)), radius=s(R + grow), fill=255)
    return m.transform((n, n), Image.AFFINE, (1, 0, 0, SHEAR, 1, -SHEAR * s(left)), resample=Image.BICUBIC)

def mark(colors, k=1.0):
    """The three cards on a transparent canvas, cropped tight. k scales the geometry."""
    n = int(1400 * k * SS)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    for i, col in zip((2, 1, 0), colors):
        l, t = 400 + STEP[0] * i, 400 + STEP[1] * i
        a = img.getchannel("A"); a.paste(0, (0, 0), card_mask(n, k, l, t, GAP)); img.putalpha(a)
        img.paste(col + (255,), (0, 0), card_mask(n, k, l, t))
    return img.crop(img.getchannel("A").getbbox())

def icon(colors, bg, fill=0.62):
    m = mark(colors)
    f = 1024 * SS * fill / max(m.size); m = m.resize((round(m.width * f), round(m.height * f)), Image.LANCZOS)
    N = 1024 * SS
    out = Image.new("RGBA", (N, N), bg + (255,) if bg else (0, 0, 0, 0))
    out.alpha_composite(m, ((N - m.width) // 2, (N - m.height) // 2))
    return out.resize((1024, 1024), Image.LANCZOS)

assets = Path(__file__).resolve().parent.parent / "MotionApp" / "Assets.xcassets"
o = assets / "AppIcon.appiconset"
icon(LIME, BG).convert("RGB").save(o / "AppIcon.png")
icon(LIME, None).save(o / "AppIcon-dark.png")
icon(GRAY, None).save(o / "AppIcon-tinted.png")
m = mark(LIME)
for scale in (1, 2, 3):            # launch mark: 128pt wide
    w = 128 * scale
    m.resize((w, round(m.height * w / m.width)), Image.LANCZOS).save(assets / "LaunchLogo.imageset" / f"LaunchLogo@{scale}x.png")
