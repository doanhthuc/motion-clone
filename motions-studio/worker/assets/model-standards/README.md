Create-image model-standard assets can be split by preset:

- `female/` — female model references.
- `male/` — male model references.
- `custom/` — optional custom references.

Legacy images in this directory are treated as the female fallback.

Put the primary female beauty reference sheet here as:

`female/model-standard-00-reference-sheet.jpg`

or keep the legacy fallback name:

`model-standard-00-reference-sheet.jpg`

The worker pins `model-standard-00*`, `*reference-sheet*`, or `*beauty-standard*` into every create-image render for that preset when no user image reference is provided.

## IMPORTANT — reference images must be background-free (ALD 10/06/2026)

These photos are an **identity reference only** (face, hair, skin, body shape). Qwen-Image-Edit treats
whatever is in the reference as something to *preserve*, so any background here leaks into the output —
the model ended up "copying" the alley/room/pool behind the model instead of obeying the user's prompt.

Rule: **cut out the model and place her on a flat neutral light-gray background** before adding a photo here.
Quick way (rembg, human segmentation):

```bash
python -m venv venv && ./venv/bin/pip install "rembg[cpu]" pillow
./venv/bin/python - <<'PY'
from rembg import remove, new_session
from PIL import Image
s = new_session("u2net_human_seg")
im = Image.open("input.jpg").convert("RGBA")
cut = remove(im, session=s)
bg = Image.new("RGBA", cut.size, (208,208,208,255))
Image.alpha_composite(bg, cut).convert("RGB").save("output.jpg", quality=92)
PY
```

The existing `model-standard-03..06` were already processed this way. The scene/pose/outfit of the final
image come entirely from the user's prompt (see `_model_standard_prompt` in `worker/worker_runtime/linux.py`).
