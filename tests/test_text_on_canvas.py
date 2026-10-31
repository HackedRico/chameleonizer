from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.resize import resize

# =============================================================================
# Module Overview
# =============================================================================
# The renderer draws every wrapped line of a text, top-aligned, whatever its box
# height, so a box that fits the canvas can still draw lines past it. The v1.25
# rules audit found headlines cut off on small ad sizes. Every text must draw
# inside the canvas, measured with the renderer's own wrap.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, _ = default_ports(ROOT)
TASKS = sorted((ROOT / "bench" / "suite" / "formats").glob("*.json")) + sorted((ROOT / "input").glob("*.json"))


@pytest.mark.parametrize("path", TASKS, ids=lambda p: p.stem)
def test_every_text_draws_inside_the_canvas(path):
    task = json.loads(path.read_text())
    out = resize(task["source"], task["target_canvas"])
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    for el in out["elements"]:
        if el["type"] != "text" or not el.get("content"):
            continue
        lay = MEASURER.layout(el["content"], el["font_family"], el.get("font_weight") == "bold",
                              el.get("font_style") == "italic", el["font_size"], el["width"])
        drawn = len(lay.lines) * el["font_size"] * float(el.get("line_height", 1.2))
        assert el["y"] + drawn <= H + 0.5, (el["id"], el["y"], drawn, H)
        assert lay.longest_word <= el["width"] + 0.5 and el["x"] + el["width"] <= W + 0.5, el["id"]
