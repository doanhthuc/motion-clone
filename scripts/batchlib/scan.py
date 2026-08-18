"""Quét bốn thư mục vai trò → danh sách run. Không đặt tên file, chỉ thả đúng ngăn.

  ~/materials/characters/  outfits/  backgrounds/  drivers/

Ghép:
  pair   số run = min(character, driver, và outfit nếu có outfit)
  cross  tích Descartes character × outfit × driver

background KHÔNG tham gia giới hạn hay nhân — nó XOAY VÒNG. Nền thường chỉ có
một cái dùng chung; nếu để nó giới hạn thì một file nền biến lô 12 run thành 1
run, và người dùng sẽ không hiểu vì sao.

Kết quả phải ỔN ĐỊNH giữa hai lần quét cùng một thư mục (sorted ở mọi chỗ) — nếu
không, sinh lại manifest sẽ đẻ diff giả và không ai biết cái nào đã chạy.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

from .manifest import Run

ROLE_DIRS = {
    "character": "characters",
    "outfit": "outfits",
    "background": "backgrounds",
    "driver": "drivers",
}
MODES = ("pair", "cross")


class ScanError(Exception):
    """Thư mục material không dùng được."""


def slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", _strip_accents(text).lower()).strip("-")
    return out or "x"


def _strip_accents(text: str) -> str:
    import unicodedata
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


def collect(materials_dir: Path) -> dict[str, list[Path]]:
    root = Path(materials_dir).expanduser().resolve()
    found: dict[str, list[Path]] = {}
    for role, dirname in ROLE_DIRS.items():
        d = root / dirname
        if not d.is_dir():
            found[role] = []
            continue
        found[role] = sorted(
            (p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
    for role in ("character", "driver"):
        if not found[role]:
            raise ScanError(
                f"Không có file nào trong {root / ROLE_DIRS[role]}\n"
                f"  Cần tối thiểu: {ROLE_DIRS['character']}/ và {ROLE_DIRS['driver']}/\n"
                f"  Bốn ngăn: {', '.join(ROLE_DIRS.values())}"
            )
    return found


def build_runs(found: dict[str, list[Path]], mode: str) -> list[Run]:
    if mode not in MODES:
        raise ScanError(f"MODE không có thật: {mode!r} — chỉ nhận {' | '.join(MODES)}")

    chars, outfits = found["character"], found["outfit"]
    bgs, drivers = found["background"], found["driver"]
    pipeline = "tryon-motion-enhance" if outfits else "motion-enhance"

    if mode == "cross":
        combos = list(itertools.product(chars, outfits or [None], drivers))
    else:
        n = min(len(chars), len(drivers), len(outfits) if outfits else len(chars))
        combos = [(chars[i], outfits[i] if outfits else None, drivers[i]) for i in range(n)]

    runs: list[Run] = []
    used: dict[str, int] = {}
    for index, (character, outfit, driver) in enumerate(combos):
        parts = [slugify(character.stem)]
        if outfit is not None:
            parts.append(slugify(outfit.stem))
        parts.append(slugify(driver.stem))
        base = "__".join(parts)
        used[base] = used.get(base, 0) + 1
        run_id = base if used[base] == 1 else f"{base}-{used[base]}"

        inputs = {"character": character, "driver": driver}
        if outfit is not None:
            inputs["outfit"] = outfit
        if bgs:
            inputs["background"] = bgs[index % len(bgs)]
        runs.append(Run(id=run_id, pipeline=pipeline, inputs=inputs, stage_params={}))
    return runs
