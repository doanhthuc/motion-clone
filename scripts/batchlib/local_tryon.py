"""Try-on chạy TRỰC TIẾP từ máy local, không qua pod — cho provider dùng API thuần
(Gemini bây giờ, "qwen-max" sau khi có endpoint/key thật). KHÔNG import linux.py: file
đó chạy TRONG worker process trên pod, phụ thuộc api_log/comfy_upload dù nhánh gemini
không gọi chúng — port phần logic thuần, cross-reference bằng file:line, đúng quy ước
scripts/batchlib/pipelines.py đã dùng cho tên field.

KHÔNG dùng `requests`: máy dev không có thư viện đó (batchlib/client.py:1-4 đã ghi lại
lý do này) — mọi HTTP call ở module này đi qua `urllib`, giống client.py.

KHÁC BIỆT ĐÃ BIẾT so với pod — KHÔNG có autocrop ảnh sản phẩm: trên pod, run_tryon chạy
`_bg_remove_file(..., model="object", crop=True)` cho ảnh sản phẩm/outfit trước khi gọi
Gemini (linux.py:4766-4774), và cờ TRYON_PRODUCT_AUTOCROP mặc định BẬT (linux.py:2578) —
nó cắt sát món đồ để sửa đúng ca "sản phẩm quá nhỏ, mẫu quá to" trong ảnh nguồn. Đường
local ở đây gửi NGUYÊN byte ảnh outfit, không tiền xử lý gì: `_bg_remove_file` gọi dịch
vụ tách nền BG_REMOVER_URL chỉ tồn tại trên pod, không port được về máy local. Hệ quả:
CÙNG một manifest có thể ra ảnh try-on khác nhau (bố cục có thể xấu hơn) tuỳ chạy local
hay chạy trên pod — rõ nhất với ảnh outfit mà món đồ nhỏ so với khung.
"""
from __future__ import annotations

from pathlib import Path

LOCAL_PROVIDERS = {"gemini", "qwen-max"}


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
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        w, h = (r.stdout or "").strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None


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
# linux.py:3678-3679 — tách khỏi MULTI: bikini/đồ bơi/đồ lót (2 mảnh HỞ DA) dùng prompt bra+panty+da trần
# riêng, MULTI còn lại (set/full/bo…) là đồ thường 2 mảnh, không được lộ da thừa.
GARMENT_REVEAL = {"bikini", "swimwear", "swimsuit", "lingerie", "underwear",
                  "do-lot", "dolot", "do_lot", "noi-y", "noiy"}
GARMENT_SHOES = {"shoes", "shoe", "footwear", "giay", "giày", "dep", "dép", "sandal",
                 "heels", "boot", "boots"}
# linux.py:3684-3685 — đầm/váy liền 1 mảnh phủ cả thân (khác MULTI = 2 mảnh tách top+bottom).
GARMENT_DRESS = {"dress", "gown", "dam", "đầm", "jumpsuit", "playsuit", "romper", "overall",
                 "bodysuit", "ao-dai", "aodai", "ao_dai", "maxi", "onepiece", "one-piece"}


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


# Đo 28/08/2026: outfit photo thường là ảnh người mẫu mặc đồ, không phải flat-lay — o8.jpeg
# trong batch/2026-08-28-lanczos-6cap.yaml có mặt và tóc đen của người mẫu khác. Gemini không
# có negative prompt (khác Qwen) nên phải chặn bằng câu dương ở CUỐI prompt: run c1-o8-m2-b3
# ra tóc đen + mặt lệch về người mẫu trong ảnh outfit thay vì giữ đúng c1 — nhánh Qwen
# (qwen_tryon_prompts._compact_lock) đã có câu này từ đầu, nhánh Gemini thì thiếu.
_GEMINI_WEARER_GUARD = (
    " If image 2 shows a person, model or mannequin wearing the product, ignore that wearer "
    "entirely and take ONLY the garment itself — never copy their face, hairstyle, hair color, "
    "body or pose onto the result.")


def gemini_tryon_prompt(gt: str, extra: str = "") -> str:
    # linux.py:3538-3543 — khoá mặt đứng ĐẦU prompt (chống "tryon đổi mặt").
    return ("CRITICAL: the person's face and facial identity must remain EXACTLY identical to "
            "image 1 — same facial structure, eyes, nose, lips, jawline, skin tone, makeup and "
            "expression; never beautify, reshape, swap or regenerate the face. "
            ) + gemini_tryon_prompt_base(gt) + tryon_extra_clause(extra) + _GEMINI_WEARER_GUARD


def qwen_tryon_prompts(gt: str, extra: str = "") -> tuple[str, str]:
    """Cổng linux.py:_qwen_tryon_prompts (3763-3889). Prompt Qwen-Image KHÁC hẳn Gemini (không dùng chung
    được — nhánh HF trên pod từng dùng nhầm prompt Gemini cho model họ Qwen và sản phẩm không áp lên người,
    xem linux.py:5521-5522); provider='qwen-max' ở local PHẢI dùng bộ prompt riêng này."""
    label = GARMENT_LABEL.get(gt, "garment")
    if gt in ("auto", "generic"):
        pos = ("Dress the person in image 1 in the outfit shown in image 2. "
               "Copy the ENTIRE look exactly as shown — every visible item of the look in image 2, "
               "with its exact color, pattern, fabric and cut. Do not add, leave out, simplify or restyle anything. "
               "Remove ALL of the person's original clothing that the new outfit replaces or covers, "
               "so nothing of it remains visible or layers over the new outfit. "
               "Keep the person exactly as in image 1: same face, same hair, same body shape, same skin tone, "
               "same pose, same expression, same background. "
               "Photorealistic, natural draping and shadows.")
        neg = ("original outfit kept, original clothing visible, leftover original garment, layered over existing clothes, "
               "only partially replaced, missing pieces from the product, added garments not in the product, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in GARMENT_REVEAL:
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE and discard ALL of their original clothing — including any dress, top, skirt and bottoms — and dress them EXACTLY in the {label} shown in image 2. "
               f"Reproduce EVERY piece and component of that set faithfully — the top/bra, the bottoms/panties, AND any waist band, garter belt, suspender straps, ties, bows and straps. Do NOT simplify it to just a bra and panty; nothing shown in image 2 may be left out. "
               f"The person must end up wearing only this set, bare skin everywhere else; none of the original garment (especially the original dress) may remain visible. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Match every piece's color, pattern, fabric, lace and cut from image 2 exactly. "
               f"Photorealistic, natural skin, natural draping and shadows, no style change.")
        neg = ("missing garter belt, missing suspender straps, missing waist band, simplified to bra and panty only, "
               "original dress still on, original outfit kept, dress underneath, bra over dress, layered over existing clothes, "
               "leftover original clothing, only top replaced, missing bottom piece, missing panties, mismatched set, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in GARMENT_MULTI:
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE all of their original clothing and dress them EXACTLY in the {label} shown in image 2 — a COMPLETE TWO-PIECE EVERYDAY outfit. "
               f"Reproduce BOTH pieces faithfully: the TOP and the BOTTOM (pants, shorts or skirt) exactly as shown in image 2, with every color, pattern, fabric, trim and cut. "
               f"This is a NORMAL everyday outfit — do NOT turn it into a bikini, swimwear or lingerie, and do NOT expose any extra bare skin beyond what this outfit naturally covers. "
               f"None of the original clothing may remain visible. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = ("turned into bikini, turned into swimwear, turned into lingerie, bra and panty, exposed underwear, "
               "bare midriff not in product, added bare skin, missing bottom piece, only top replaced, bottom unchanged, "
               "original clothing kept, layered over existing clothes, mismatched set, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in GARMENT_DRESS:
        pos = (f"Completely change the outfit of the person in image 1: fully REMOVE and discard ALL of their original clothing — "
               f"including their top, blouse, skirt and any bottoms — and dress them EXACTLY in the ONE-PIECE {label} shown in image 2. "
               f"Reproduce the ENTIRE dress faithfully: the bodice AND the full skirt with its exact length, tiers, ruffles, color, pattern, fabric and cut from image 2. "
               f"Below the hem of the dress, show the person's bare legs naturally; NONE of the original clothing (especially the original skirt or bottoms) may remain visible anywhere on the body. "
               f"This is ONE single dress, not a separate top and skirt. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = ("original skirt kept, original bottoms kept, original pants kept, beige skirt remaining, leftover clothing under the dress, "
               "dress layered over pants, dress over skirt, only top replaced, lower body unchanged, wrong dress length, truncated skirt, missing skirt, "
               "turned into two-piece, separated top and skirt, "
               "different person, distorted face, wrong color, wrong pattern, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    elif gt in GARMENT_SHOES:
        pos = (f"The person in image 1 is currently wearing shoes on their feet. You MUST completely REMOVE those existing shoes "
               f"and put the EXACT {label} from image 2 onto BOTH of their feet. The new shoes from image 2 have to clearly and "
               f"visibly replace the old ones — reproduce their exact color, material, sole, heel height and design faithfully. "
               f"Change NOTHING else: keep the person's clothing, body, legs, pose and the background completely identical. "
               f"Strictly preserve identity: face, hair, body shape, skin tone. "
               f"Photorealistic, correct foot perspective, feet firmly on the ground, natural shadows, no style change.")
        neg = ("original shoes kept, old shoes still on, unchanged footwear, same sneakers remaining, original sneakers, barefoot, missing shoes, "
               "changed clothing, altered outfit, different outfit, missing feet, deformed shoes, extra shoes, floating shoes, wrong foot shape, "
               "different person, distorted face, wrong color, blurry, lowres, deformed, extra limbs, watermark, text, signature")
    else:
        pos = (f"Replace the {label} of the person in image 1 with the {label} from image 2. "
               f"Keep the person's other garments unchanged. "
               f"Strictly preserve the person's identity: face, hair, body shape, skin tone, pose, expression, and background. "
               f"Match the new garment's color, pattern, fabric, and cut from image 2 exactly. "
               f"Photorealistic, natural draping and shadows, no style change.")
        neg = "different person, distorted face, wrong color, wrong pattern, missing details, blurry, lowres, deformed, extra limbs, watermark, text, signature"
    _hair_neg = ("different hairstyle, changed hair, restyled hair, new haircut, curled hair, wavy hair, "
                 "straightened hair, longer hair, shorter hair, added hair volume, different hair parting, "
                 "different hair color, hair extensions")
    _prop_neg = ("distorted body proportions, elongated body, stretched body, shrunken head, oversized head, "
                 "body copied from product image, pose copied from product image, different body scale, "
                 "mismatched proportions, warped anatomy, unnatural body length, "
                 "tiny head, small head, doll-like proportions, long legs, "
                 "elongated legs, stretched torso, lengthened body, fashion-illustration proportions, 9-head figure")
    _face_neg = ("different face, changed face, new face, face swap, regenerated face, beautified face, slimmer face, "
                 "reshaped jawline, changed eyes, changed nose, changed lips, changed makeup, changed facial "
                 "expression, younger face, older face, altered identity, "
                 "blurry face, soft face, hazy face, out-of-focus face, low-detail face, smudged facial features, "
                 "unclear eyes, half-closed eyes not in image 1")
    _src_neg = ("hair from product image, hair color from image 2, wearer's hairstyle, product model's hair, "
                "face from image 2, wearer's face, skin tone from image 2, different floral pattern, invented pattern")
    _compact_lock = ("CRITICAL: the output must show the SAME person as image 1 — identical face (rendered sharp "
                     "and in focus), identical hairstyle, hair length and hair color, identical body proportions, "
                     "height and scale within the frame, same skin tone, same pose and expression. "
                     "If a person, model or mannequin is wearing the product in image 2, ignore that wearer "
                     "entirely and take ONLY the garments — never copy their hair, face, body, pose or framing.")
    pos = pos + " " + tryon_extra_clause(extra) + _compact_lock
    neg = neg + ", " + _face_neg + ", " + _hair_neg + ", " + _prop_neg + ", " + _src_neg
    return pos, neg


# linux.py:5433-5448 — dùng NGUYÊN VĂN cho pass 2 ghép nền. Gemini image-edit không nhận tham số
# negative riêng (xem cách gọi ở linux.py:5504-5509) nên TRYON_BG_NEG chỉ dùng bởi qwen_max_edit.
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
TRYON_BG_NEG = (
    "changed outfit, different clothes, restyled garment, wrong garment color, missing accessories, "
    "changed face, different person, changed hair, changed pose, distorted body, "
    "original background kept, background from image 1, different location than image 2, invented background, "
    "pasted cutout, sticker edges, floating person, mismatched lighting, mismatched perspective, wrong scale, "
    "blurry, lowres, deformed, watermark, text, signature")


import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .client import JobError

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
# linux.py:2598 — cùng default. Đổi qua env nếu cần model rẻ hơn.
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
# Không có model text-only nào có sẵn trong linux.py (bản gốc dùng Ollama, xem
# translate_vn_to_en) — chọn một model Gemini text rẻ, ổn định làm mặc định.
GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")


def _post_json(url: str, query: dict, payload: dict, timeout: int) -> dict:
    full_url = f"{url}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        full_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # PHẢI đứng TRƯỚC nhánh OSError: HTTPError là con của URLError, mà URLError là con
        # của OSError — để sau thì mọi lỗi HTTP đều rơi vào nhánh dưới và mất mã 4xx/5xx.
        body = exc.read()[:300].decode("utf-8", "replace")
        raise JobError(f"Gemini API {exc.code}: {body}") from exc
    except OSError as exc:
        # OSError chứ không chỉ URLError: urllib CHỈ bọc lỗi socket thành URLError ở khâu
        # gửi request. Hết giờ đọc response (TimeoutError) hay rớt kết nối giữa chừng
        # (ConnectionError) bay thẳng ra ngoài — không bắt ở đây thì nó không phải JobError,
        # nên run_local_phase._one() (chỉ bắt JobError) để nó nổ ra tận main(): CẢ Pha A
        # chết vì một run hết giờ, và run đó kẹt "pending" trong journal thay vì "error".
        raise JobError(f"Gemini API không phản hồi: {exc}") from exc


def gemini_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path,
                aspect_ratio: str | None = None, model: str | None = None,
                base_url: str = GEMINI_API_BASE) -> Path:
    """Cổng urllib của linux.py:_gemini_edit (3455-3478, bản gốc dùng requests.post)."""
    parts = [{"text": prompt}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    gcfg = {"responseModalities": ["IMAGE"]}
    if aspect_ratio:
        gcfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    url = f"{base_url}/v1beta/models/{model or GEMINI_IMAGE_MODEL}:generateContent"
    data = _post_json(url, {"key": key},
                      {"contents": [{"parts": parts}], "generationConfig": gcfg}, 300)
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                out_path.write_bytes(base64.b64decode(blob["data"]))
                return out_path
    raise JobError(f"Gemini không trả ảnh: {json.dumps(data)[:300]}")


# linux.py: khối "Qwen-Image (DashScope Model Studio) — provider='qwen-max' & fallback Gemini" (thêm
# 25/08/2026, ngay sau QWEN_EDIT_MAX_REFS). qwen-image-3.0-pro đang limited preview (Alibaba yêu cầu apply
# access qua Model Gallery trước khi key gọi được — key user 25/08 CHƯA được duyệt) → mặc định về
# qwen-image-edit-plus (bản GA). Được duyệt 3.0 → set env QWEN_IMAGE_MODEL=qwen-image-3.0-pro, không cần sửa code.
QWEN_IMAGE_MODEL = os.environ.get("QWEN_IMAGE_MODEL", "qwen-image-edit-plus")
# Endpoint multimodal-generation/generation gắn Workspace ID vào HOST — khác dashscope-intl.aliyuncs.com
# (dùng cho video-generation ở linux.py:_dashscope_i2v). QWEN_IMAGE_BASE ghi đè toàn bộ URL nếu cần.
QWEN_IMAGE_WORKSPACE = os.environ.get("QWEN_IMAGE_WORKSPACE", "").strip()
QWEN_IMAGE_REGION = os.environ.get("QWEN_IMAGE_REGION", "ap-southeast-1").strip()
QWEN_IMAGE_BASE = os.environ.get("QWEN_IMAGE_BASE", "").strip()
# Tự rớt Gemini→Qwen-Max khi Gemini lỗi/hết quota — mặc định TẮT (giữ hành vi cũ). Cùng cờ tên với worker
# (linux.py TRYON_GEMINI_FALLBACK), set trong .env để bật cho cả pod lẫn batch local.
TRYON_GEMINI_FALLBACK = str(os.environ.get("TRYON_GEMINI_FALLBACK", "")).strip().lower() in ("1", "true", "yes", "on")


def _qwen_image_url() -> str:
    if QWEN_IMAGE_BASE:
        return f"{QWEN_IMAGE_BASE.rstrip('/')}/api/v1/services/aigc/multimodal-generation/generation"
    if not QWEN_IMAGE_WORKSPACE:
        raise JobError(
            "Qwen-Max try-on cần QWEN_IMAGE_WORKSPACE (Workspace ID Alibaba Model Studio) trong .env, "
            "hoặc QWEN_IMAGE_BASE để tự ghi đè URL.")
    return (f"https://{QWEN_IMAGE_WORKSPACE}.{QWEN_IMAGE_REGION}.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation")


def qwen_max_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path,
                  negative_prompt: str | None = None, model: str | None = None) -> Path:
    """Cổng linux.py:_qwen_max_edit — bản urllib (cùng lý do KHÔNG dùng requests đã ghi đầu file). Call ĐỒNG
    BỘ (multimodal-generation trả ảnh ngay, không async submit+poll); ảnh trả về là URL OSS sống 24h → tải
    ngay bằng urllib."""
    content = [{"image": f"data:{mime};base64,{base64.b64encode(data).decode()}"} for data, mime in images[:3]]
    content.append({"text": prompt})
    body = {"model": model or QWEN_IMAGE_MODEL,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"watermark": False}}
    if negative_prompt:
        body["parameters"]["negative_prompt"] = negative_prompt
    req = urllib.request.Request(
        _qwen_image_url(), data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise JobError(f"Qwen-Max API {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}") from exc
    except OSError as exc:
        raise JobError(f"Qwen-Max API không phản hồi: {exc}") from exc
    try:
        img_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
    except (KeyError, IndexError, TypeError):
        raise JobError(f"Qwen-Max không trả ảnh: {json.dumps(data)[:300]}")
    with urllib.request.urlopen(img_url, timeout=180) as resp:
        out_path.write_bytes(resp.read())
    return out_path


_VN_CHARS = "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"


def translate_vn_to_en(text: str, key: str, base_url: str = GEMINI_API_BASE) -> str:
    """Thay linux.py:_translate_prompt_en (88-115): bản gốc gọi Ollama TRÊN POD
    (qwen2.5:7b-instruct qua TRANSLATE_URL) — không gọi được từ máy local. Dùng Gemini
    text-generation (đã cần key cho chính bước try-on), cùng system prompt, cùng
    fail-safe: lỗi / không có dấu tiếng Việt → trả nguyên văn, KHÔNG BAO GIỜ raise."""
    t = (text or "").strip()
    if not t or not any(c in t.lower() for c in _VN_CHARS):
        return text
    sys_msg = ("You are a translator for an image-generation prompt. Translate the user's text "
               "into ONE natural English image prompt. Preserve EVERY detail exactly — "
               "scene/location, lighting, outfit, pose, camera angle, accessories, mood — and do "
               "NOT add, remove or invent anything. Output ONLY the English prompt, no quotes, "
               "no explanation.")
    url = f"{base_url}/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent"
    try:
        data = _post_json(url, {"key": key}, {
            "contents": [{"parts": [{"text": t}]}],
            "systemInstruction": {"parts": [{"text": sys_msg}]},
            "generationConfig": {"temperature": 0.1},
        }, 60)
    except Exception:
        # Never raise: ANY error (JobError, json.JSONDecodeError, AttributeError on malformed
        # response, etc.) returns original text. Matches fail-safe contract of linux.py:88-115.
        return text
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            out = (part.get("text") or "").strip().strip('"').strip()
            if out:
                return out
    return text


import subprocess
import tempfile
import time

from .config import Settings
from .manifest import Run

_RES_LONGEDGE = {"1080p": 1080, "2k": 1440}


def postprocess(out_path: Path, params: dict) -> Path:
    """Thay linux.py:_tryon_postprocess (4674-4704): brightness/saturation/resize qua
    ffmpeg. KHÔNG dùng api_log — lỗi bị nuốt về ảnh gốc, giống hệt hành vi worker gốc."""
    try:
        bright = max(-0.5, min(float(params.get("brightness") or 0), 0.5))
    except (TypeError, ValueError):
        bright = 0.0
    try:
        sat = max(0.5, min(float(params.get("saturation") or 1.0), 2.0))
    except (TypeError, ValueError):
        sat = 1.0
    res = str(params.get("outputRes") or params.get("resolution") or "").lower().strip()
    target = _RES_LONGEDGE.get(res)
    vf = []
    if abs(bright) > 0.005 or abs(sat - 1.0) > 0.01:
        vf.append(f"eq=brightness={bright:.3f}:saturation={sat:.3f}")
    if target:
        vf.append(f"scale='if(gte(iw,ih),{target},-2)':'if(gte(iw,ih),-2,{target})':flags=lanczos")
    if not vf:
        return out_path
    dst = out_path.with_suffix(".pp.png")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_path), "-vf", ",".join(vf),
                        str(dst)], check=True, timeout=300)
        if dst.is_file() and dst.stat().st_size > 1024:
            return dst
    except Exception:
        pass
    return out_path


def _gemini_or_qwen_max(gem_key, qwen_key, images, gem_prompt, qwen_prompt, out_path,
                        aspect_ratio, qwen_negative=None):
    """Cổng linux.py:_tryon_gemini_or_fallback. Gọi Gemini; lỗi + TRYON_GEMINI_FALLBACK bật + có key
    Qwen-Max → tự rớt sang Qwen-Max thay vì fail run. Mặc định TẮT (raise thẳng lỗi Gemini)."""
    try:
        return gemini_edit(images, gem_prompt, gem_key, out_path,
                           aspect_ratio=aspect_ratio, base_url=GEMINI_API_BASE)
    except Exception:
        if not TRYON_GEMINI_FALLBACK or not qwen_key:
            raise
        return qwen_max_edit(images, qwen_prompt, qwen_key, out_path, negative_prompt=qwen_negative)


def run_local_tryon(run: Run, params: dict, settings: Settings, out_path: Path) -> tuple[int, int]:
    """Điểm vào Pha A cho MỘT run. Trả (elapsed_sec, bytes). Ném JobError khi hỏng cấu hình/mạng,
    NotImplementedError khi provider không nằm trong LOCAL_PROVIDERS."""
    started = time.time()
    provider = str(params.get("provider") or "").lower().strip()
    if not is_local_provider(provider):
        raise NotImplementedError(
            f"provider {provider!r}: chưa có endpoint/key thật cho try-on local — điền vào "
            "local_tryon.py khi có chi tiết API")

    gem_key = str(params.get("apiKey") or params.get("geminiApiKey")
                 or settings.gemini_api_key or "").strip()
    qwen_key = str(params.get("apiKey") or params.get("dashscopeApiKey")
                  or settings.dashscope_api_key or "").strip()
    # CHỈ nhắc .env: apiKey/geminiApiKey/dashscopeApiKey KHÔNG nằm trong bảng param hợp lệ của chặng
    # tryon (params.py đọc từ linux.py), nên manifest có các key đó bị validate_manifest chặn TRƯỚC
    # khi Pha A chạy — khuyên dùng chúng là khuyên một đường không đi được.
    if provider == "qwen-max":
        if not qwen_key:
            raise JobError(
                f"run {run.id!r}: try-on local (provider qwen-max) cần API key — thêm "
                "DASHSCOPE_API_KEY vào .env")
    else:
        if not gem_key:
            raise JobError(
                f"run {run.id!r}: try-on local (provider gemini) cần API key — thêm "
                "GEMINI_API_KEY vào .env")
        if not valid_gemini_key(gem_key):
            raise JobError(
                f"run {run.id!r}: GEMINI_API_KEY không đúng định dạng (phải 'AIza…', ~39 ký tự, "
                "không khoảng trắng)")
        if TRYON_GEMINI_FALLBACK and not qwen_key:
            raise JobError(
                f"run {run.id!r}: TRYON_GEMINI_FALLBACK bật nhưng thiếu DASHSCOPE_API_KEY trong .env")

    model_path = run.inputs.get("character")
    product_path = run.inputs.get("outfit")
    background_path = run.inputs.get("background")
    if model_path is None or product_path is None:
        raise JobError(f"run {run.id!r}: try-on local cần inputs.character và inputs.outfit")

    garment = (str(params.get("garment_type") or params.get("garmentType") or "").lower().strip()
              or "auto")
    extra_raw = str(params.get("extraPrompt") or params.get("extra_prompt")
                    or params.get("keepNote") or "").strip()
    # base_url=GEMINI_API_BASE truyền TƯỜNG MINH ở mọi lệnh gọi dưới đây: đây là biến
    # global, đọc lại giá trị hiện tại mỗi lần hàm chạy — nếu để gemini_edit/
    # translate_vn_to_en tự dùng default parameter của chúng thì giá trị đó bị chốt
    # CỐ ĐỊNH lúc định nghĩa hàm (module load), nên test patch module-level
    # GEMINI_API_BASE (mock.patch.object(lt, "GEMINI_API_BASE", ...)) sẽ không có tác
    # dụng và code gọi thẳng ra Google thật thay vì fake HTTP server của test.
    extra_en = translate_vn_to_en(extra_raw, gem_key, base_url=GEMINI_API_BASE) if extra_raw else ""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        images = [(model_path.read_bytes(), mime_of(model_path)),
                  (product_path.read_bytes(), mime_of(product_path))]

        if provider == "qwen-max":
            pos_q, neg_q = qwen_tryon_prompts(garment, extra=extra_en)
            edited = qwen_max_edit(images, pos_q, qwen_key, tmp_dir / "pass1.png", negative_prompt=neg_q)
            if background_path is not None:
                edited = qwen_max_edit(
                    [(edited.read_bytes(), "image/png"),
                     (background_path.read_bytes(), mime_of(background_path))],
                    TRYON_BG_POS, qwen_key, tmp_dir / "pass2.png", negative_prompt=TRYON_BG_NEG)
        else:
            prompt = gemini_tryon_prompt(garment, extra=extra_en)
            pos_q, neg_q = qwen_tryon_prompts(garment, extra=extra_en)
            edited = _gemini_or_qwen_max(gem_key, qwen_key, images, prompt, pos_q, tmp_dir / "pass1.png",
                                        gemini_aspect(img_size(model_path)), qwen_negative=neg_q)

            if background_path is not None:
                edited = _gemini_or_qwen_max(
                    gem_key, qwen_key,
                    [(edited.read_bytes(), "image/png"),
                     (background_path.read_bytes(), mime_of(background_path))],
                    TRYON_BG_POS, TRYON_BG_POS, tmp_dir / "pass2.png",
                    gemini_aspect(img_size(edited)), qwen_negative=TRYON_BG_NEG)

        final = postprocess(edited, params)
        out_path.write_bytes(final.read_bytes())

    return int(time.time() - started), out_path.stat().st_size
