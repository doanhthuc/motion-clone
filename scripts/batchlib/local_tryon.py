"""Try-on chạy TRỰC TIẾP từ máy local, không qua pod — cho provider dùng API thuần
(Gemini bây giờ, "qwen-max" sau khi có endpoint/key thật). KHÔNG import linux.py: file
đó chạy TRONG worker process trên pod, phụ thuộc api_log/comfy_upload dù nhánh gemini
không gọi chúng — port phần logic thuần, cross-reference bằng file:line, đúng quy ước
scripts/batchlib/pipelines.py đã dùng cho tên field.

KHÔNG dùng `requests`: máy dev không có thư viện đó (batchlib/client.py:1-4 đã ghi lại
lý do này) — mọi HTTP call ở module này đi qua `urllib`, giống client.py.
"""
from __future__ import annotations

from pathlib import Path

LOCAL_PROVIDERS = {"gemini"}   # + "qwen-max" khi có endpoint/key thật (xem run_local_tryon)


def is_local_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in LOCAL_PROVIDERS


# linux.py:2688-2696 — Gemini image API tự đổi tỉ lệ output nếu KHÔNG truyền
# imageConfig.aspectRatio → cắt cụt ảnh dọc. Tính tỉ lệ gần nhất từ ảnh gốc để ép giữ khung.
_GEMINI_ARS = {"1:1": 1.0, "2:3": 2 / 3, "3:2": 1.5, "3:4": 0.75, "4:3": 4 / 3,
               "4:5": 0.8, "5:4": 1.25, "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9}


def gemini_aspect(dims: tuple[int, int] | None) -> str | None:
    if not dims or not dims[0] or not dims[1]:
        return None
    r = dims[0] / dims[1]
    return min(_GEMINI_ARS, key=lambda k: abs(_GEMINI_ARS[k] - r))


def valid_gemini_key(key: str) -> bool:
    # linux.py:2698-2702 — key Google AI Studio dạng 'AIza…' (~39 ký tự, không khoảng trắng).
    k = (key or "").strip()
    return k.startswith("AIza") and 30 <= len(k) <= 60 and not any(c.isspace() for c in k)


def mime_of(path: Path) -> str:
    # linux.py:3451-3453
    return {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "image/jpeg")


def img_size(path: Path) -> tuple[int, int] | None:
    # linux.py:3176-3185 — (W,H) qua ffprobe, None nếu lỗi (caller fallback an toàn:
    # gemini_aspect(None) trả None, gemini_edit khi đó không ép aspect ratio).
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        w, h = (r.stdout or "").strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None


# linux.py:3129-3146 — chỉ hai tập garment nhánh Gemini thật sự rẽ nhánh (SHOES, MULTI);
# GARMENT_DRESS/GARMENT_REVEAL chỉ dùng ở nhánh Qwen (_qwen_tryon_prompts), không cần ở đây.
GARMENT_LABEL = {
    "upper": "top or shirt", "lower": "pants", "skirt": "skirt", "dress": "dress",
    "gown": "gown", "dam": "dress", "đầm": "dress", "jumpsuit": "jumpsuit",
    "playsuit": "playsuit (one-piece outfit)", "romper": "romper (one-piece outfit)",
    "overall": "overall (one-piece outfit)", "bodysuit": "bodysuit",
    "ao-dai": "Vietnamese ao dai (long dress)", "aodai": "Vietnamese ao dai (long dress)",
    "ao_dai": "Vietnamese ao dai (long dress)", "maxi": "maxi dress",
    "onepiece": "one-piece dress", "one-piece": "one-piece dress",
    "bra": "bra (the single upper undergarment)", "accessory": "accessory",
    "shoes": "shoes/footwear", "giay": "shoes/footwear", "dep": "sandals/footwear",
    "set": "complete two-piece everyday outfit (top and bottom)",
    "full": "complete two-piece everyday outfit (top and bottom)",
    "bo": "complete two-piece everyday outfit (top and bottom)",
    "2pieces": "complete two-piece everyday outfit (top and bottom)",
    "two-piece": "complete two-piece everyday outfit (top and bottom)",
    "bikini": "complete bikini/swimwear set", "swimwear": "complete swimwear set",
    "swimsuit": "complete swimsuit set", "lingerie": "complete lingerie set",
    "underwear": "complete lingerie set", "do-lot": "complete lingerie set",
    "dolot": "complete lingerie set",
}
GARMENT_MULTI = {"set", "full", "bikini", "swimwear", "swimsuit", "lingerie", "underwear",
                 "do-lot", "dolot", "do_lot", "noi-y", "noiy", "bo", "2pieces", "two-piece"}
GARMENT_SHOES = {"shoes", "shoe", "footwear", "giay", "giày", "dep", "dép", "sandal",
                 "heels", "boot", "boots"}


def tryon_extra_clause(extra: str) -> str:
    # linux.py:3199-3204 — "Ghi chú thêm" của user, ưu tiên cao, chèn cuối prompt.
    e = (extra or "").strip()
    if not e:
        return ""
    return ("ADDITIONAL USER INSTRUCTION — high priority, follow this exactly and let it "
            "OVERRIDE the general rules where they conflict (e.g. keep or add the items the "
            f"user names): {e}. ")


def gemini_tryon_prompt_base(gt: str) -> str:
    # linux.py:3545-3570
    label = GARMENT_LABEL.get(gt, "garment")
    if gt in ("auto", "generic"):
        return ("Image 1 is a person; image 2 is a fashion product photo. Edit image 1: dress "
                "the person in EXACTLY the complete fashion look shown in image 2 — reproduce "
                "EVERY fashion item present: clothing (dress, two-piece set, top, bottom, "
                "swimwear, straps, belts, trims), hosiery (thigh-high stockings, tights, socks, "
                "garter belts), footwear (shoes/heels/boots on both feet), and accessories "
                "(jewelry, hat or headband worn without changing the hairstyle, gloves, scarf, "
                "glasses, handbag) — each with the exact color, pattern, fabric and cut. "
                "Do not invent items that are not in image 2. Fully remove the person's original "
                "clothing, hosiery, footwear and accessories wherever the new look covers or "
                "replaces them; keep nothing of the original showing through. Keep identity, "
                "face, hair, body, pose and background identical. Photorealistic; output only "
                "the edited photo.")
    if gt in GARMENT_SHOES:
        return ("Image 1 is a photo of a person; image 2 is a product photo of footwear. Edit "
                "image 1: completely replace the shoes on the person's feet with the EXACT "
                "footwear from image 2 (same color, material, sole, heel and design), on BOTH "
                "feet. Keep the person's face, hair, body, clothing, pose and the background "
                "EXACTLY the same. Photorealistic; output only the edited photo.")
    if gt in GARMENT_MULTI:
        return ("Image 1 is a person; image 2 is a clothing set. Edit image 1: dress the person "
                "in the COMPLETE set from image 2 (every piece), fully removing their original "
                "outfit. Keep identity, pose and background identical. Photorealistic; output "
                "only the edited photo.")
    return (f"Image 1 is a person; image 2 is a {label}. Edit image 1: replace the person's "
            f"{label} with the {label} from image 2 (exact color, pattern, fabric, cut). Keep "
            "other garments, identity, pose and background identical. Photorealistic; output "
            "only the edited photo.")


def gemini_tryon_prompt(gt: str, extra: str = "") -> str:
    # linux.py:3538-3543 — khoá mặt đứng ĐẦU prompt (chống "tryon đổi mặt").
    return ("CRITICAL: the person's face and facial identity must remain EXACTLY identical to "
            "image 1 — same facial structure, eyes, nose, lips, jawline, skin tone, makeup and "
            "expression; never beautify, reshape, swap or regenerate the face. "
            ) + gemini_tryon_prompt_base(gt) + tryon_extra_clause(extra)


# linux.py:4712-4721 — dùng NGUYÊN VĂN cho pass 2 ghép nền. Không cần _TRYON_BG_NEG:
# Gemini image-edit không nhận tham số negative riêng (xem cách gọi ở linux.py:4860).
TRYON_BG_POS = (
    "Take the person from image 1 and place them into the environment shown in image 2. "
    "CRITICAL — keep the person EXACTLY as in image 1: same face, same hair, same body "
    "proportions, same pose, and the SAME OUTFIT with its exact colors, patterns, fabrics and "
    "details; do not restyle, recolor or change any garment, shoe or accessory. "
    "The background must be the location from image 2: reproduce its setting, architecture, "
    "furniture, plants and depth faithfully — do NOT keep any part of the original background "
    "from image 1. Blend the person in naturally: match the lighting direction, color "
    "temperature, perspective and camera height of image 2, and add natural contact shadows "
    "under the person. Photorealistic, seamless composite, no cut-out edges.")
