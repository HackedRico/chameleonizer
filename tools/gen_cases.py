from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from engine.adapters.stage_one import SkiaTextMeasurer
from tools.catalog import FORMATS

# =============================================================================
# Module Overview
# =============================================================================
# Generates synthetic task files that look like the official samples: a
# poster-style source design built from the Solstice brand kit, plus a target
# format from the catalogue. `generate` is deterministic per seed, varies element
# count, copy length, groups, rotation and missing assets, and uses opaque ids
# half the time so the engine can't lean on names like `headline`.

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [(1080, 1080), (1080, 1350), (1080, 1920), (1200, 628)]

HEADLINES = [
    "Chase the Light", "Golden Hour, Bottled", "Taste the Sunset", "Summer Starts Here",
    "Sip Something Brighter", "New: Blood Orange and Rosemary",
    "Introducing the All-New Limited-Batch Solstice Botanical Sparkling Collection",
    "The Brightest Flavor of the Season Is Finally Here, for a Limited Time Only",
]
SUBHEADS = [
    "Botanical sparkling infusions, crafted for golden hour.",
    "Small batch. Low sugar. All flavor.",
    "Made with real fruit and nothing artificial.",
    "Wild honey, blood orange zest and a whisper of smoked rosemary, bottled at the peak of "
    "golden hour for a taste that lingers long after sunset.",
]
CTAS = ["Shop the Summer Line", "Shop Now", "Get Yours Today", "Order Online", "Discover the Collection"]
BADGES = ["NEW FLAVOR", "LIMITED EDITION", "AWARD WINNING 2026", "SMALL BATCH", "ZERO SUGAR", "VEGAN"]
RIBBONS = ["SUNSET SALE", "20% OFF", "JUST DROPPED"]
LEGAL = [
    "Please drink responsibly. Botanical Sparkling Co., 2026. Void where prohibited. "
    "See solstice.com/terms for full details.",
    "Offer valid while supplies last. Terms apply.",
]
WHITE, GOLD, ORANGE, PLUM = "rgba(255,255,255,1)", "rgba(240,178,66,1)", "rgba(232,106,58,1)", "rgba(43,27,46,1)"


class _Builder:
    """Accumulates elements for one source design, top to bottom."""

    def __init__(self, rng: random.Random, w: int, h: int, opaque_ids: bool) -> None:
        self.rng, self.w, self.h = rng, w, h
        self.opaque = opaque_ids
        self.elements: list[dict[str, Any]] = []
        self._n = 0
        self.measure = SkiaTextMeasurer(ROOT / "brand_kit" / "fonts")

    def new_id(self, name: str) -> str:
        self._n += 1
        return f"e{self._n}" if self.opaque else f"{name}_{self._n}"

    def text(self, name: str, content: str, family: str, size: int, x: float, y: float, w: float,
             color: str = WHITE, bold: bool = False, z: int = 5, group: str | None = None,
             align: str = "center") -> dict[str, Any]:
        lay = self.measure.layout(content, family, bold, False, size, w)
        el = {
            "id": self.new_id(name), "type": "text", "x": x, "y": y, "width": w,
            "height": round(len(lay.lines) * size * 1.2, 1), "z_index": z, "content": content,
            "font_family": family, "font_size": size, "font_weight": "bold" if bold else "regular",
            "color": color, "text_align": align,
        }
        if group:
            el["group_id"] = group
        self.elements.append(el)
        return el

    def chip(self, name: str, label: str, x: float, y: float, w: float, h: float, fill: str,
             font: int, rotation: float = 0.0, z: int = 3) -> None:
        group = self.new_id(f"{name}_group")
        shape = {
            "id": self.new_id(f"{name}_bg"), "type": "shape", "x": x, "y": y, "width": w, "height": h,
            "z_index": z, "shape_type": "rectangle", "fill_color": fill,
            "corner_radius": 0 if rotation else h / 2, "group_id": group,
        }
        if rotation:
            shape["rotation"] = rotation
        self.elements.append(shape)
        label_el = self.text(f"{name}_label", label, "Solstice Sans", font, x, y + (h - font * 1.2) / 2, w,
                             bold=True, z=z + 2, group=group)
        if rotation:
            label_el["rotation"] = rotation

    def image(self, name: str, asset: str, x: float, y: float, w: float, h: float,
              fit: str = "contain", z: int = 3) -> None:
        self.elements.append({
            "id": self.new_id(name), "type": "image", "x": x, "y": y, "width": w, "height": h,
            "z_index": z, "asset_id": asset, "fit": fit,
        })


def _differs(source: tuple[int, int], target: tuple[int, int]) -> bool:
    """True when the target's aspect ratio is more than 10% away from the source's."""
    return abs(target[0] / target[1] - source[0] / source[1]) > 0.1 * source[0] / source[1]


def generate(seed: int, target: str | None = None) -> dict[str, Any]:
    """One synthetic task: a poster-like source design and a differently shaped target.

    With `target` set, the task uses that catalogue format and a source of a different shape;
    without it, the target is drawn at random, which keeps older seeds reproducible."""
    rng = random.Random(seed)
    if target is not None and target not in FORMATS:
        raise ValueError(f"unknown target format {target!r}; see `tools/catalog.py`")
    pool = SOURCES if target is None else [s for s in SOURCES if _differs(s, FORMATS[target])]
    sw, sh = rng.choice(pool)
    b = _Builder(rng, sw, sh, opaque_ids=rng.random() < 0.5)
    landscape = sw > sh
    b.image("bg", "solstice_bg_gradient.png", 0, 0, sw, sh, fit="cover", z=0)

    for _ in range(rng.randint(0, 3)):
        d = rng.uniform(0.15, 0.45) * sw
        b.elements.append({
            "id": b.new_id("deco"), "type": "shape", "x": rng.uniform(-0.1, 0.9) * sw,
            "y": rng.uniform(-0.1, 0.9) * sh, "width": d, "height": d, "z_index": 1,
            "shape_type": "ellipse", "fill_color": WHITE, "opacity": round(rng.uniform(0.08, 0.18), 2),
        })

    margin = 0.06 * min(sw, sh)
    col_x, col_w = (0.48 * sw, 0.46 * sw) if landscape else (0.08 * sw, 0.84 * sw)
    y = margin
    if rng.random() < 0.85:
        lw = 0.28 * sw if not landscape else 0.2 * sw
        b.image("logo", "solstice_logo_lockup.png", col_x + (col_w - lw) / 2, y, lw, lw / 3.2, z=4)
        y += lw / 3.2 + margin * 0.6
    if rng.random() < 0.7:
        if landscape:
            ps = 0.75 * sh
            b.image("product", "solstice_bottle.png", 0.06 * sw, (sh - ps) / 2, ps, ps)
        else:
            ps = rng.uniform(0.32, 0.45) * sw
            b.image("product", "solstice_bottle.png", (sw - ps) / 2, y, ps, ps)
            y += ps + margin * 0.4
    head = b.text("headline", rng.choice(HEADLINES), "Solstice Serif",
                  round(rng.uniform(0.045, 0.065) * sw * (0.7 if landscape else 1)), col_x, y, col_w, bold=True)
    if rng.random() < 0.3:
        words = head["content"].split()
        start = len(head["content"]) - len(words[-1])
        head["style_runs"] = [{"start": start, "end": len(head["content"]), "color": GOLD}]
    y += head["height"] + margin * 0.4
    if rng.random() < 0.65:
        sub = b.text("subhead", rng.choice(SUBHEADS), "Solstice Sans",
                     round(rng.uniform(0.02, 0.026) * sw * (0.8 if landscape else 1)), col_x, y, col_w,
                     color="rgba(255,255,255,0.88)")
        y += sub["height"] + margin * 0.5
    if rng.random() < 0.35:
        labels = rng.sample(BADGES, rng.randint(1, 3))
        bw, bh, font = col_w / len(labels) - 0.02 * sw, 0.045 * sw, round(0.014 * sw)
        for i, label in enumerate(labels):
            b.chip("badge", label, col_x + i * (bw + 0.02 * sw), y, bw, bh, rng.choice([ORANGE, PLUM]), font)
        y += bh + margin * 0.5
    roll = rng.random()
    if roll < 0.75:
        cw, ch = 0.38 * sw * (0.8 if landscape else 1), 0.08 * sw * (0.8 if landscape else 1)
        b.chip("cta", rng.choice(CTAS), col_x + (col_w - cw) / 2, y, cw, ch, ORANGE, round(ch * 0.26))
        y += ch + margin * 0.5
    elif roll < 0.9:
        cta = b.text("cta", rng.choice(CTAS), "Solstice Sans", round(0.028 * sw), col_x, y, col_w,
                     color=ORANGE, bold=True)
        y += cta["height"] + margin * 0.5
    if rng.random() < 0.2:
        b.chip("ribbon", rng.choice(RIBBONS), 0.68 * sw, 0.03 * sh, 0.28 * sw, 0.05 * sw, "rgba(220,60,50,1)",
               round(0.018 * sw), rotation=-12, z=6)
    if rng.random() < 0.35:
        b.text("legal", rng.choice(LEGAL), "Solstice Sans", max(10, round(0.011 * sw)), 0.06 * sw,
               sh - margin - 0.03 * sw, 0.88 * sw, color="rgba(255,255,255,0.6)", z=6)
    if rng.random() < 0.12:
        b.image("watermark", "solstice_holiday_badge_2025.png", 0.78 * sw, 0.88 * sh, 0.16 * sw, 0.07 * sw, z=2)

    if target is None:
        name, (tw, th) = rng.choice([(n, s) for n, s in FORMATS.items() if _differs((sw, sh), s)])
    else:
        name, (tw, th) = target, FORMATS[target]
    return {
        "task_id": f"synthetic_{seed:03d}",
        "difficulty": "synthetic",
        "notes": f"{sw}x{sh} to {name}",
        "target_name": name,
        "source": {"canvas": {"width": sw, "height": sh}, "elements": b.elements},
        "target_canvas": {"width": tw, "height": th},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write synthetic Chameleon task files.")
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "cases")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(args.count):
        task = generate(args.seed * 1000 + i)
        (args.out / f"{task['task_id']}.json").write_text(json.dumps(task, indent=2))
    print(f"wrote {args.count} tasks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
