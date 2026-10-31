from __future__ import annotations

from pathlib import Path

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.core.understand import understand

# =============================================================================
# Module Overview
# =============================================================================
# A call to action with no button shape is a text link. In banner columns it
# stacks under the copy at the source's proportions; given its own column, it
# was sized like a button and wrapped into a block bigger than the headline.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)


def _text(eid: str, content: str, y: float, size: int, color: str = "rgba(255,255,255,1)") -> dict:
    return {"id": eid, "type": "text", "x": 90, "y": y, "width": 900, "height": size * 1.2, "z_index": 3,
            "content": content, "font_family": "Solstice Sans", "font_size": size, "color": color,
            "text_align": "center"}


SOURCE = {
    "canvas": {"width": 1080, "height": 1350},
    "elements": [
        {"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1080, "height": 1350, "z_index": 0,
         "fill_color": "rgba(40,20,40,1)"},
        _text("a", "Summer Starts Here", 200, 48),
        _text("b", "Wild honey, blood orange zest and a whisper of smoked rosemary, bottled at the peak "
                   "of golden hour.", 280, 26),
        _text("c", "Discover the Collection", 380, 30, "rgba(230,100,60,1)"),
    ],
}


def test_text_link_stacks_under_the_copy_in_banner_columns():
    scene = understand(SOURCE, PORTS)
    assert {u.id: u.role for u in scene.units}["c"] == "cta"
    outcome = resize_with_report(SOURCE, {"width": 820, "height": 312}, *PORTS)
    els = {el["id"]: el for el in outcome.design["elements"]}
    head, link = els["a"], els["c"]
    assert outcome.strategy.startswith("banner/columns")
    assert link["font_size"] < head["font_size"]
    # Below the headline, in the same column rather than beside it.
    assert link["y"] > head["y"] + head["height"]
    assert link["x"] < head["x"] + head["width"] and head["x"] < link["x"] + link["width"]
