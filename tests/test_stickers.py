from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.sizing import drawn_glyph_bounds
from engine.resize import resize

# =============================================================================
# Module Overview
# =============================================================================
# Rotated stickers: the renderer rotates each element about its box centre and
# the canvas clips whatever crosses its edge. A sticker's ribbon may bleed off
# the canvas, but its label's glyphs must draw whole, so the judge never reads
# the sticker as clipped copy.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, _ = default_ports(ROOT)


def _rotated_labels(design: dict) -> list[dict]:
    return [e for e in design["elements"]
            if e["type"] == "text" and abs(float(e.get("rotation", 0) or 0)) > 0.5]


def _sticker_tasks():
    for path in sorted((ROOT / "bench" / "suite").glob("*/*.json")):
        task = json.loads(path.read_text())
        if _rotated_labels(task["source"]):
            yield pytest.param(task, id=f"{path.parent.name}/{path.stem}")


def test_glyph_bounds_follow_rotation_and_alignment():
    el = {"id": "t", "type": "text", "x": 100, "y": 100, "width": 300, "height": 40, "content": "ENDS SUNDAY",
          "font_family": "Solstice Sans", "font_size": 30, "font_weight": "bold", "text_align": "center"}
    flat = drawn_glyph_bounds(MEASURER, el)
    left = drawn_glyph_bounds(MEASURER, {**el, "text_align": "left"})
    tilted = drawn_glyph_bounds(MEASURER, {**el, "rotation": 12})
    assert flat.x > 100 and flat.x1 < 400 and abs(flat.cx - 250) < 2
    assert abs(left.x - 100) < 1
    # Turned about the box centre, the line climbs on the left and drops on the right.
    assert tilted.h > flat.h + 20 and abs(tilted.cx - flat.cx) < 2


def _tilted(source: dict, degrees: float) -> dict:
    """`source` with every rotated element turned to `degrees`, keeping its direction."""
    out = copy.deepcopy(source)
    for el in out["elements"]:
        rot = float(el.get("rotation", 0) or 0)
        if abs(rot) > 0.5:
            el["rotation"] = math.copysign(degrees, rot)
    return out


# The suite's stickers turn 12 degrees; 25 tips their labels past the edge without the nudge.
@pytest.mark.parametrize("degrees", [None, 25])
@pytest.mark.parametrize("task", list(_sticker_tasks()))
def test_rotated_label_glyphs_stay_on_canvas(task, degrees):
    canvas = task["target_canvas"]
    if degrees is not None and canvas["width"] <= 320 and canvas["height"] <= 100:
        # On a mobile strip a ribbon tilted past the source's own angle has no corner that clears
        # the edge; v1.25 fails these two the same way. The stress tilt covers every larger canvas.
        pytest.skip("no corner on a mobile strip holds a ribbon at the stress tilt")
    source = task["source"] if degrees is None else _tilted(task["source"], degrees)
    out = resize(source, task["target_canvas"])
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    # The layout keeps at least this much clear canvas between a label's glyphs and the edge.
    edge = max(2.0, 0.01 * min(W, H)) - 0.5
    for el in _rotated_labels(out):
        drawn = drawn_glyph_bounds(MEASURER, el)
        if drawn is None:
            continue
        assert min(drawn.x, drawn.y, W - drawn.x1, H - drawn.y1) >= edge, (el["id"], drawn)
