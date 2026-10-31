from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core import decor
from engine.core.pipeline import resize_with_report
from engine.core.sizing import Ctx, drawn_glyph_bounds
from engine.core.types import Box, element_box
from engine.core.understand import understand

# =============================================================================
# Module Overview
# =============================================================================
# Decoration placement on suite designs: ornaments stay clear of copy where
# the canvas has room, never pile onto each other beyond the source, and a
# circle that framed the product in the source frames it again.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)
SUITE = ROOT / "bench" / "suite"


def _run(split: str, task: str):
    data = json.loads((SUITE / split / f"{task}.json").read_text())
    out = resize_with_report(data["source"], data["target_canvas"], *PORTS)
    scene = understand(data["source"], PORTS[1])
    design = out.design
    ctx = Ctx(scene, design["canvas"]["width"], design["canvas"]["height"], *PORTS)
    return scene, ctx, {e["id"]: e for e in design["elements"]}


@pytest.mark.parametrize("split,task", [("probe", "event_story__instagram_post"),
                                        ("dev", "instagram_portrait_0"), ("probe", "quote_card__linkedin_banner")])
def test_ornaments_clear_copy_and_each_other(split: str, task: str) -> None:
    scene, _ctx, els = _run(split, task)
    copy = []
    for unit in scene.units:
        if unit.kind == "image" and unit.role != "logo":
            continue
        for m in unit.member_ids:
            el = els[m]
            b = drawn_glyph_bounds(PORTS[0], el) if el["type"] == "text" else element_box(el)
            if b is not None:
                copy.append(b)
    ids = list(scene.decoration_ids)
    for i, d in enumerate(ids):
        b = element_box(els[d])
        ellipse = els[d].get("shape_type") == "ellipse"
        assert not any(decor._touches(b, ellipse, c) for c in copy), d
        for d2 in ids[i + 1:]:
            was = decor._pair_share(element_box(scene.elements[d]), element_box(scene.elements[d2]))
            assert decor._pair_share(b, element_box(els[d2])) <= was + 0.05, (d, d2)


@pytest.mark.parametrize("split,task", [("probe", "photo_band__linkedin_banner"),
                                        ("probe", "two_ctas__instagram_post"), ("probe", "wide_source__half_page")])
def test_halo_follows_its_product(split: str, task: str) -> None:
    scene, ctx, els = _run(split, task)
    halos = decor.halo_owners(ctx)
    assert halos
    units = {u.id: u for u in scene.units}
    for d, owner in halos.items():
        m = units[owner].member_ids[0]
        p = element_box(els[m])
        assert decor._inside(element_box(els[d]), True, p.cx, p.cy), d


def test_cover_matches_circle_area() -> None:
    b = Box(0, 0, 100, 100)
    assert decor._cover(b, True, Box(-10, -10, 200, 200)) == pytest.approx(7854, rel=0.01)
    assert decor._cover(b, True, Box(50, 0, 100, 100)) == pytest.approx(3927, rel=0.01)
    assert not decor._touches(b, True, Box(90, 90, 20, 20))
    assert decor._touches(b, True, Box(80, 80, 20, 20))
