from __future__ import annotations

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
# An image whose asset file is missing stays visible. The organizers' October
# 22 session asks that every input element remain visible, and FAQ Q1 lets the
# engine change only position, size and a text's font size, so the renderer's
# grey placeholder is laid out like any other picture. Only the opt-in occlude
# policy still tucks it under the backdrop.

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = sorted((ROOT / "input").glob("*.json"))
LEVEL3 = ROOT / "input" / "chameleon_sample_level3_advanced.json"
PORTS = default_ports(ROOT)
GEOMETRY = {"x", "y", "width", "height"}
# Shortest side, in pixels, below which a placeholder reads as a speck rather than a picture.
MIN_SIDE = 8.0


def _synthetic() -> dict:
    """A product ad whose product photo is missing from the brand kit."""
    return {
        "canvas": {"width": 1080, "height": 1080},
        "elements": [
            {"id": "a", "type": "image", "x": 0, "y": 0, "width": 1080, "height": 1080, "z_index": 0,
             "asset_id": "solstice_bg_gradient.png", "fit": "cover"},
            {"id": "b", "type": "image", "x": 390, "y": 60, "width": 300, "height": 94, "z_index": 4,
             "asset_id": "solstice_logo_lockup.png", "fit": "contain"},
            {"id": "c", "type": "image", "x": 290, "y": 220, "width": 500, "height": 400, "z_index": 2,
             "opacity": 0.9, "asset_id": "no_such_product_photo.png", "fit": "contain"},
            {"id": "d", "type": "text", "x": 90, "y": 680, "width": 900, "height": 120, "z_index": 5,
             "content": "Bright botanicals for long summer evenings", "font_family": "Solstice Serif",
             "font_size": 48, "font_weight": "bold", "color": "rgba(255,255,255,1)", "text_align": "center"},
            {"id": "e", "type": "shape", "x": 360, "y": 880, "width": 360, "height": 80, "z_index": 3,
             "shape_type": "rectangle", "fill_color": "rgba(232,106,58,1)", "corner_radius": 40, "group_id": "cta"},
            {"id": "f", "type": "text", "x": 360, "y": 902, "width": 360, "height": 36, "z_index": 5,
             "content": "Shop now", "font_family": "Solstice Sans", "font_size": 26, "font_weight": "bold",
             "color": "rgba(255,255,255,1)", "text_align": "center", "group_id": "cta"},
        ],
    }


def _missing_cases():
    level3 = json.loads(LEVEL3.read_text())
    yield pytest.param(level3["source"], level3["target_canvas"], "holiday_watermark", id="level3")
    # At 320x50 the packed row once set the placeholder at 10x4.
    yield pytest.param(level3["source"], {"width": 320, "height": 50}, "holiday_watermark", id="level3-320x50")
    for w, h in ((1080, 1920), (1200, 628), (728, 90), (300, 250), (320, 50)):
        yield pytest.param(_synthetic(), {"width": w, "height": h}, "c", id=f"synthetic-{w}x{h}")


def _assert_placed_and_visible(src: dict, out: dict, canvas: dict) -> None:
    for key in set(src) | set(out):
        if key not in GEOMETRY:
            assert out.get(key) == src.get(key), key
    assert ("opacity" in out) == ("opacity" in src)
    w, h = float(out["width"]), float(out["height"])
    assert min(w, h) >= MIN_SIDE, (w, h)
    assert out["x"] >= 0 and out["y"] >= 0
    assert out["x"] + w <= canvas["width"] + 1e-6 and out["y"] + h <= canvas["height"] + 1e-6
    assert abs((w / h) / (src["width"] / src["height"]) - 1) <= 0.02


@pytest.mark.parametrize("source,target,eid", list(_missing_cases()))
def test_missing_asset_stays_visible_in_place_of_its_picture(source, target, eid):
    out = resize(source, target)
    assert structural(source, target, out) == []
    src = next(el for el in source["elements"] if el["id"] == eid)
    _assert_placed_and_visible(src, next(el for el in out["elements"] if el["id"] == eid), target)


@pytest.mark.parametrize("path", OFFICIAL, ids=lambda p: p.stem)
def test_official_samples_change_only_geometry_and_text_size(path):
    task = json.loads(path.read_text())
    out = resize(task["source"], task["target_canvas"])
    by_id = {el["id"]: el for el in out["elements"]}
    for src in task["source"]["elements"]:
        el = by_id[src["id"]]
        free = GEOMETRY | ({"font_size"} if src["type"] == "text" else set())
        changed = {k for k in set(src) | set(el) if k not in free and el.get(k) != src.get(k)}
        assert not changed, (src["id"], changed)
        assert set(el) - set(src) <= free, src["id"]


def test_occlude_policy_still_tucks_a_missing_asset_away():
    task = json.loads(LEVEL3.read_text())
    out = resize_with_report(task["source"], task["target_canvas"], *PORTS, "occlude").design
    el = next(e for e in out["elements"] if e["id"] == "holiday_watermark")
    assert el["opacity"] == 0.0
