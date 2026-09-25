"""Image Studio generation: model catalog, request validation, provider calls.

Runs inside motion-bot on a small thread pool (spec approach A): the phone
gets 202 at once and polls, because Nano Banana Pro takes tens of seconds
per image and Cloudflare cuts a request near 100 s.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from batchlib.client import JobError
from batchlib.config import env_get
from batchlib.local_tryon import (gemini_image_bytes, img_size, mime_of, qwen_image_generate,
                                  qwen_max_configured, qwen_setting)
from control.studio import StudioError, StudioStore

MAX_REF_SIDE = 2048
MAX_COUNT = 4
MAX_PROMPT = 4000
REF_KINDS = ("material", "tryon", "run_tryon", "studio")
# Qwen `size` is "W*H" with total pixels <= 2048*2048 (Bailian Qwen Image 3.0 reference,
# read 2026-09-26). Gemini takes the ratio string itself.
ASPECTS = {"16:9": "2048*1152", "4:3": "2048*1536", "1:1": "2048*2048",
           "3:4": "1536*2048", "9:16": "1152*2048"}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    provider: str            # "gemini" | "qwen"
    api_model: str | None    # None = read QWEN_IMAGE_MODEL live
    image_size: str | None
    max_refs: int
    price_usd: float


# USD per output image. Gemini: https://ai.google.dev/gemini-api/docs/pricing, read 2026-09-26
# (Pro 1K/2K $0.134; Nano Banana 2 2K $0.101; Lite lists 1K only, $0.0336 — so no imageSize).
# Qwen: ~$0.07 converted from the Bailian card's ¥0.5 per 2K output + ¥0.02 per input image;
# replace with the Singapore-workspace billed figure after the first live run.
MODELS = {m.key: m for m in (
    ModelSpec("nano-banana-pro", "Nano Banana Pro", "gemini", "gemini-3-pro-image", "2K", 14, 0.134),
    ModelSpec("nano-banana-2", "Nano Banana 2", "gemini", "gemini-3.1-flash-image", "2K", 14, 0.101),
    ModelSpec("nano-banana-2-lite", "Nano Banana 2 Lite", "gemini", "gemini-3.1-flash-lite-image",
              None, 14, 0.0336),
    ModelSpec("qwen-image-3", "Qwen Image 3.0 Pro", "qwen", None, None, 3, 0.07),
)}
DEFAULT_MODEL = "nano-banana-2"


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    model: ModelSpec
    aspect: str
    count: int
    refs: list


def _available(spec: ModelSpec, root: Path) -> bool:
    if spec.provider == "qwen":
        return qwen_max_configured(root)
    return bool(env_get(root / ".env", "GEMINI_API_KEY"))


def _key(provider: str, root: Path) -> str:
    return env_get(root / ".env", "GEMINI_API_KEY" if provider == "gemini" else "DASHSCOPE_API_KEY")


def catalog(root: Path) -> dict:
    return {"models": [{"key": m.key, "label": m.label, "provider": m.provider,
                        "max_refs": m.max_refs, "price_usd": m.price_usd,
                        "available": _available(m, root), "default": m.key == DEFAULT_MODEL}
                       for m in MODELS.values()],
            "aspects": list(ASPECTS), "max_count": MAX_COUNT}


def parse_request(body: dict, root: Path) -> GenerationRequest:
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise StudioError("bad_request", "prompt is required")
    if len(prompt) > MAX_PROMPT:
        raise StudioError("bad_request", f"prompt is longer than {MAX_PROMPT} characters")
    spec = MODELS.get(body.get("model"))
    if spec is None:
        raise StudioError("unknown_model", f"unknown model: {body.get('model')!r}")
    if not _available(spec, root):
        raise StudioError("model_unavailable", f"{spec.label} is not configured on the server")
    aspect = body.get("aspect")
    if aspect not in ASPECTS:
        raise StudioError("bad_request", f"aspect must be one of {', '.join(ASPECTS)}")
    count = body.get("count")
    if type(count) is not int or not 1 <= count <= MAX_COUNT:
        raise StudioError("bad_request", f"count must be an integer 1-{MAX_COUNT}")
    refs = body.get("refs") or []
    if not isinstance(refs, list) or not all(
            isinstance(r, dict) and r.get("kind") in REF_KINDS and isinstance(r.get("id"), str)
            and r["id"] for r in refs):
        raise StudioError("bad_request", f"each ref needs kind in {REF_KINDS} and an id")
    if len(refs) > spec.max_refs:
        raise StudioError("too_many_refs",
                          f"{spec.label} takes at most {spec.max_refs} reference images, got {len(refs)}")
    return GenerationRequest(prompt, spec, aspect, count, [{"kind": r["kind"], "id": r["id"]} for r in refs])


def fit_image(src: Path, dest: Path) -> None:
    """Write `src` to `dest` (a .png or .jpg name), downscaled to <= MAX_REF_SIDE px per side.

    Qwen refuses inputs over 10 MB and Gemini's inline payload grows with size; a 2048 px side keeps
    the detail both models use. A same-format image that already fits is copied byte for byte;
    anything else (a .webp, an oversize photo) goes through ffmpeg. Unreadable → ref_not_image.
    """
    size = img_size(src)
    a, b = src.suffix.lower(), dest.suffix.lower()
    same_format = a == b or {a, b} <= {".jpg", ".jpeg"}
    # Unprobeable (size None) but already PNG/JPEG: pass it through and let the provider judge,
    # rather than refusing a file ffprobe merely failed to read.
    if same_format and (size is None or max(size) <= MAX_REF_SIDE):
        shutil.copyfile(src, dest)
        return
    vf = []
    if size is not None and max(size) > MAX_REF_SIDE:
        w, h = size
        # -1 (not -2): -2 forces an even result (2048x682 for a 3000x1000 source), but the
        # provider APIs take odd dimensions fine and the test fixture expects the unrounded
        # 682.67 -> 683 (measured 2026-09-26).
        vf = ["-vf", f"scale={MAX_REF_SIDE}:-1" if w >= h else f"scale=-1:{MAX_REF_SIDE}"]
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *vf, "-frames:v", "1", str(dest)],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        raise StudioError("ref_not_image", f"could not read reference image {src.name}")


def gemini_call(spec: ModelSpec, prompt: str, images: list, aspect: str, *, key: str) -> bytes:
    return gemini_image_bytes(images, prompt, key, model=spec.api_model, aspect_ratio=aspect,
                              image_size=spec.image_size)


def qwen_call(spec: ModelSpec, prompt: str, images: list, aspect: str, count: int, *, key: str,
              root: Path) -> list[bytes]:
    model = qwen_setting("QWEN_IMAGE_MODEL", root, default="qwen-image-3.0-pro")
    return qwen_image_generate(images, prompt, key, n=count, size=ASPECTS[aspect], model=model)


class StudioRunner:
    def __init__(self, store: StudioStore, root: Path, *, gemini=None, qwen=None,
                 workers: int = 4, log=print):
        self.store, self.root, self.log = store, root, log
        self._gemini = gemini or (lambda spec, p, imgs, a: gemini_call(
            spec, p, imgs, a, key=_key("gemini", root)))
        self._qwen = qwen or (lambda spec, p, imgs, a, n: qwen_call(
            spec, p, imgs, a, n, key=_key("qwen", root), root=root))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="studio")
        self._pending = 0
        self._idle = threading.Condition()

    def submit(self, pid: str, req: GenerationRequest, ref_paths: list[Path]) -> dict:
        gen = self.store.add_generation(
            pid, prompt=req.prompt, model=req.model.key, aspect=req.aspect, count=req.count,
            refs=list(zip(req.refs, ref_paths)), unit_price_usd=req.model.price_usd, copy=fit_image)
        images = []
        for ref in gen["refs"]:
            path = self.store.resolve_ref(pid, ref["file"])
            images.append((path.read_bytes(), mime_of(path)))
        if req.model.provider == "qwen":
            self._spawn(self._run_qwen, pid, gen["id"], req, images)
        else:
            for slot in range(req.count):
                self._spawn(self._run_gemini_slot, pid, gen["id"], slot, req, images)
        return gen

    def _spawn(self, fn, *args) -> None:
        with self._idle:
            self._pending += 1
        self._pool.submit(self._guard, fn, *args)

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        finally:
            with self._idle:
                self._pending -= 1
                self._idle.notify_all()

    def _mark(self, pid, gid, slot, **kw) -> bool:
        try:
            self.store.set_slot(pid, gid, slot, **kw)
            return True
        except StudioError:            # project deleted while the call ran — drop the result
            return False

    def _error_text(self, exc: Exception) -> str:
        if isinstance(exc, JobError):
            return str(exc)
        self.log("studio: generation failed\n" + traceback.format_exc())
        return "internal error"

    def _run_gemini_slot(self, pid, gid, slot, req, images) -> None:
        if not self._mark(pid, gid, slot, status="running"):
            return
        try:
            data = self._gemini(req.model, req.prompt, images, req.aspect)
        except Exception as exc:
            self._mark(pid, gid, slot, status="error", error=self._error_text(exc))
            return
        self._mark(pid, gid, slot, status="done", image=data)

    def _run_qwen(self, pid, gid, req, images) -> None:
        for slot in range(req.count):
            if not self._mark(pid, gid, slot, status="running"):
                return
        try:
            results = self._qwen(req.model, req.prompt, images, req.aspect, req.count)
        except Exception as exc:
            text = self._error_text(exc)
            for slot in range(req.count):
                self._mark(pid, gid, slot, status="error", error=text)
            return
        for slot in range(req.count):
            if slot < len(results):
                self._mark(pid, gid, slot, status="done", image=results[slot])
            else:
                self._mark(pid, gid, slot, status="error", error="Qwen returned fewer images than asked")

    def wait_idle(self, timeout: float) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
