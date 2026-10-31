from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest
from PIL import Image

from engine.adapters.stage_one import default_ports
from engine.resize import resize

# =============================================================================
# Module Overview
# =============================================================================
# The brief's first rubric row: no unwanted collisions, and text never on a busy
# image. On every suite task, small ad sizes included, no two texts may overlap
# where they draw, and no text may cover the visible pixels of a picture. Drawn
# text is the renderer's own wrap; a picture is its asset's visible alpha box placed
# as `contain` draws it, so transparent margins never count. Rotated stickers and
# full-canvas backdrops are left out. Below 300x250 the packed fallback is what
# keeps dense designs, such as Level 3 at 320x50, clear.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, _ = default_ports(ROOT)
SUITE = ROOT / "bench" / "suite"
TASKS = [p for split in ("dev", "test", "probe", "formats") for p in sorted((SUITE / split).glob("*.json"))]
SLACK = 3.0


@lru_cache(maxsize=None)
def _visible(asset: str) -> tuple[float, float, float, float, float] | None:
    """The asset's visible box as shares of its size, plus its aspect, or None when it is missing."""
    path = ROOT / "brand_kit" / "assets" / asset
    if not path.exists():
        return None
    im = Image.open(path).convert("RGBA")
    box = im.split()[-1].point(lambda v: 255 if v > 40 else 0).getbbox()
    if box is None:
        return None
    return box[0] / im.width, box[1] / im.height, box[2] / im.width, box[3] / im.height, im.width / im.height


def _texts(design: dict) -> list[tuple[str, float, float, float, float]]:
    out = []
    for el in design["elements"]:
        if el["type"] != "text" or not el.get("content") or el.get("z_index", 0) < 0:
            continue
        if abs(float(el.get("rotation", 0) or 0)) > 1:
            continue
        lay = MEASURER.layout(el["content"], el.get("font_family", "Montserrat"), el.get("font_weight") == "bold",
                              el.get("font_style") == "italic", int(el["font_size"]), el["width"])
        h = len(lay.lines) * el["font_size"] * float(el.get("line_height", 1.2))
        align = el.get("text_align")
        x = (el["x"] + (el["width"] - lay.max_width) / 2 if align == "center"
             else el["x"] + el["width"] - lay.max_width if align == "right" else el["x"])
        out.append((el["id"], x, el["y"], x + lay.max_width, el["y"] + h))
    return out


def _pictures(design: dict) -> list[tuple[str, float, float, float, float]]:
    W, H = design["canvas"]["width"], design["canvas"]["height"]
    out = []
    for el in design["elements"]:
        if el["type"] != "image" or float(el.get("opacity", 1)) < 0.5 or el.get("z_index", 0) < 0:
            continue
        if el["width"] * el["height"] >= 0.8 * W * H:
            continue  # a backdrop
        frac = _visible(el.get("asset_id", ""))
        if frac is None:
            continue
        bw, bh, aspect = el["width"], el["height"], frac[4]
        dw, dh = (bw, bw / aspect) if bw / max(bh, 1e-6) < aspect else (bh * aspect, bh)
        ox, oy = el["x"] + (bw - dw) / 2, el["y"] + (bh - dh) / 2
        out.append((el["id"], ox + frac[0] * dw, oy + frac[1] * dh, ox + frac[2] * dw, oy + frac[3] * dh))
    return out


def _overlap(a, b) -> bool:
    return min(a[3], b[3]) - max(a[1], b[1]) > SLACK and min(a[4], b[4]) - max(a[2], b[2]) > SLACK


@pytest.mark.parametrize("path", TASKS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_no_text_on_text_or_on_a_picture(path):
    task = json.loads(path.read_text())
    design = resize(task["source"], task["target_canvas"])
    texts, pictures = _texts(design), _pictures(design)
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            assert not _overlap(a, b), (a[0], b[0])
        for p in pictures:
            assert not _overlap(a, p), (a[0], p[0])


def test_the_check_finds_planted_overlaps():
    """The check itself: a headline moved onto the product, and onto the subhead, must both fail it."""
    task = json.loads((SUITE / "dev" / "instagram_story_0.json").read_text())
    design = resize(task["source"], task["target_canvas"])
    els = {el["id"]: el for el in design["elements"]}
    texts = sorted((el for el in design["elements"] if el["type"] == "text"), key=lambda el: -el["font_size"])
    head, other = texts[0], texts[1]
    product = next(el for el in design["elements"] if el.get("asset_id") == "solstice_bottle.png")
    head["x"], head["y"] = product["x"], product["y"] + 0.4 * product["height"]
    assert any(_overlap(t, p) for t in _texts(design) if t[0] == head["id"] for p in _pictures(design))
    other["x"], other["y"] = els[head["id"]]["x"], els[head["id"]]["y"]
    found = _texts(design)
    assert any(_overlap(a, b) for i, a in enumerate(found) for b in found[i + 1:])
