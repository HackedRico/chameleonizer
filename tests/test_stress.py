from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.resize import resize
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# Stress tests for inputs the generator never makes but hidden designs might:
# degenerate element sets, odd fields, extreme canvases. Each case must come
# back valid from the real engine path, not from the letterbox fallback.

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "input" / "chameleon_sample_level2_core.json").read_text())["source"]
PORTS = default_ports(ROOT)


def _text(eid: str, content: str, size: int = 40, **extra) -> dict:
    el = {"id": eid, "type": "text", "x": 100, "y": 100, "width": 600, "height": size * 1.3,
          "z_index": 3, "content": content, "font_family": "Solstice Serif", "font_size": size,
          "color": "rgba(255,255,255,1)"}
    el.update(extra)
    return el


def _design(elements: list[dict], w: int = 1080, h: int = 1080) -> dict:
    return {"canvas": {"width": w, "height": h}, "elements": elements}


def _cases():
    bg = copy.deepcopy(BASE["elements"][0])
    yield "text_only", _design([_text("a", "Just a headline")])
    yield "image_only", _design([copy.deepcopy(BASE["elements"][4])])
    yield "no_background", _design([e for e in copy.deepcopy(BASE["elements"]) if e["id"] != "bg"])
    yield "empty_text", _design([bg, _text("a", ""), _text("b", "Visible")])
    yield "very_long_text", _design([bg, _text("a", "word " * 300, 30)])
    yield "unbreakable_word", _design([bg, _text("a", "Supercalifragilisticexpialidocious" * 3, 60)])
    yield "unknown_font", _design([bg, _text("a", "Mystery font", font_family="Comic Neue")])
    yield "zero_size_element", _design([bg, _text("a", "Hello"), {**_text("b", "tiny"), "width": 0, "height": 0}])
    yield "rotated_image", _design([bg, {**copy.deepcopy(BASE["elements"][4]), "rotation": 33}])
    many = [bg] + [_text(f"t{i}", f"Line number {i}", 20 + i % 10, y=20 * i) for i in range(40)]
    yield "forty_elements", _design(many)
    yield "fully_offcanvas_source", _design([bg, {**_text("a", "Gone"), "x": 2000, "y": 2000}])
    yield "shape_background", _design([{"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1080,
                                        "height": 1080, "z_index": 0, "shape_type": "rectangle",
                                        "fill_color": "rgba(43,27,46,1)"}, _text("a", "On a shape")])
    yield "group_of_images", _design([bg, {**copy.deepcopy(BASE["elements"][3]), "group_id": "g"},
                                      {**copy.deepcopy(BASE["elements"][4]), "group_id": "g"}])
    yield "unicode_text", _design([bg, _text("a", "Café déjà vu, naïve façade")])
    yield "product_pair", _design([e for e in copy.deepcopy(BASE["elements"]) if e["id"] != "product"] + _pair())
    grid = _pair() + [{**p, "id": f"{p['id']}_low", "y": p["y"] + 260} for p in _pair()]
    yield "product_grid", _design([bg, _text("a", "Four flavours", y=20)] + grid)


def _pair(gap: float = 40) -> list[dict]:
    """Two products side by side at slightly different sizes, a matched pair."""
    product = copy.deepcopy(BASE["elements"][4])
    left = {**product, "id": "p_left", "x": 140, "y": 260, "width": 380, "height": 380}
    right = {**product, "id": "p_right", "x": 520 + gap, "y": 290, "width": 340, "height": 340}
    return [left, right]


TARGETS = [(50, 50), (4000, 4000), (1080, 1920), (728, 90), (320, 50), (160, 600)]


@pytest.mark.parametrize("target", TARGETS, ids=lambda t: f"{t[0]}x{t[1]}")
@pytest.mark.parametrize(("name", "design"), list(_cases()), ids=[c[0] for c in _cases()])
def test_engine_handles_odd_inputs(name, design, target):
    tgt = {"width": target[0], "height": target[1]}
    outcome = resize_with_report(copy.deepcopy(design), tgt, *PORTS)
    assert outcome.strategy != "fallback/no-candidate" or len(design["elements"]) <= 1, outcome.strategy
    assert structural(design, tgt, outcome.design) == []


@pytest.mark.parametrize(("name", "design"), list(_cases()), ids=[c[0] for c in _cases()])
def test_public_entry_never_raises(name, design):
    out = resize(copy.deepcopy(design), {"width": 728, "height": 90})
    assert sorted(e["id"] for e in out["elements"]) == sorted(e["id"] for e in design["elements"])


@pytest.mark.parametrize("target", [(1080, 1920), (1584, 396), (300, 250), (728, 90), (160, 600)],
                         ids=lambda t: f"{t[0]}x{t[1]}")
def test_matched_pair_scales_together(target):
    """Two products side by side keep their size ratio, alignment and order as one unit."""
    design = _design([e for e in copy.deepcopy(BASE["elements"]) if e["id"] != "product"] + _pair())
    out = {e["id"]: e for e in resize_with_report(design, {"width": target[0], "height": target[1]},
                                                   *PORTS).design["elements"]}
    left, right = out["p_left"], out["p_right"]
    assert right["width"] / left["width"] == pytest.approx(340 / 380, rel=0.02)
    assert left["x"] < right["x"]
    # Bottom edges stay as aligned as in the source, relative to the pair's scale.
    k = left["width"] / 380
    assert (right["y"] + right["height"]) - (left["y"] + left["height"]) == pytest.approx(-10 * k, abs=2)
