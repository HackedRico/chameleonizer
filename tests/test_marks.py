from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.core.types import element_box
from engine.core.understand import understand

# =============================================================================
# Module Overview
# =============================================================================
# Satellite marks: a rule or dot above or below a text must stay clear of the
# glyphs drawn on both sides of it, descenders included, on every target.

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "bench" / "suite" / "probe"
PORTS = default_ports(ROOT)
MARK_TASKS = sorted(PROBE.glob("event_story__*.json")) + sorted(PROBE.glob("quote_card__*.json"))


def _ink(el: dict) -> tuple[float, float]:
    """Drawn top and bottom of a text element, in canvas pixels."""
    measurer = PORTS[0]
    bold, italic = el.get("font_weight") == "bold", el.get("font_style") == "italic"
    size = int(el["font_size"])
    lay = measurer.layout(el["content"], el["font_family"], bold, italic, size, float(el["width"]))
    top = measurer.ink(lay.lines[0], el["font_family"], bold, italic, size)[0]
    bottom = measurer.ink(lay.lines[-1], el["font_family"], bold, italic, size)[1]
    return el["y"] + top, el["y"] + (len(lay.lines) - 1) * size * float(el.get("line_height", 1.2)) + bottom


def test_rule_between_headline_and_date_is_a_separator():
    task = json.loads((PROBE / "event_story__linkedin_banner.json").read_text())
    scene = understand(task["source"], PORTS[1])
    assert len(scene.separators) == 1
    mark, upper, lower = scene.separators[0]
    owner = next(u for u in scene.units if mark in u.member_ids)
    assert owner.member_ids[0] in (upper, lower)
    assert scene.elements[upper]["y"] < scene.elements[lower]["y"]


@pytest.mark.parametrize("path", MARK_TASKS, ids=lambda p: p.stem)
def test_marks_clear_the_glyphs_beside_them(path):
    task = json.loads(path.read_text())
    scene = understand(task["source"], PORTS[1])
    design = resize_with_report(task["source"], task["target_canvas"], *PORTS).design
    out = {el["id"]: el for el in design["elements"]}
    for mark_id, *texts in scene.separators:
        mark = element_box(out[mark_id])
        for tid in texts:
            text = element_box(out[tid])
            if mark.x1 <= text.x or mark.x >= text.x1:
                continue  # placed side by side, so the vertical gap does not apply
            top, bottom = _ink(out[tid])
            gap = top - mark.y1 if mark.cy < top else mark.y - bottom
            assert gap >= 2.0, f"{mark_id} is {gap:.1f}px from the glyphs of {tid}"
