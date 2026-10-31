from __future__ import annotations

from pathlib import Path

from engine.adapters.stage_one import default_ports
from engine.core.understand import understand

# =============================================================================
# Module Overview
# =============================================================================
# The understand stage hands every layout the source's reading order. These
# cases pin the two rules behind it: a row of columns reads column by column,
# and small copy above the call to action is body copy, never fine print.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)


def _text(eid: str, content: str, x: float, y: float, w: float, size: int) -> dict:
    return {"id": eid, "type": "text", "x": x, "y": y, "width": w, "height": size * 1.2, "z_index": 3,
            "content": content, "font_family": "Solstice Sans", "font_size": size,
            "color": "rgba(255,255,255,1)", "text_align": "center"}


def _button(x: float, y: float) -> list[dict]:
    return [
        {"id": "s", "type": "shape", "x": x, "y": y, "width": 300, "height": 80, "z_index": 2,
         "fill_color": "rgba(230,100,60,1)", "group_id": "g"},
        {**_text("b", "Shop now", x, y + 25, 300, 26), "group_id": "g"},
    ]


def test_side_by_side_columns_read_one_column_at_a_time():
    # Headline and tagline in the middle column, the button in a right column starting higher than
    # the tagline: sorting by top edge alone read the button before the tagline.
    els = [_text("h", "Chase the Light", 600, 180, 480, 60),
           _text("t", "Botanical sparkling infusions.", 600, 266, 480, 22)] + _button(1170, 204)
    scene = understand({"canvas": {"width": 1500, "height": 500}, "elements": els}, PORTS)
    assert [u.role for u in scene.units] == ["headline", "subhead", "cta"]


def test_small_copy_above_the_button_is_not_fine_print():
    long = "Botanical sparkling infusions with blood orange and wild honey, crafted for golden hour."
    els = [_text("h", "Chase the Light", 140, 900, 800, 76),
           _text("d", long, 210, 1008, 660, 26)] + _button(390, 1125)
    scene = understand({"canvas": {"width": 1080, "height": 1350}, "elements": els}, PORTS)
    roles = {u.id: u.role for u in scene.units}
    assert roles["d"] != "legal"
    assert [u.role for u in scene.units] == ["headline", "subhead", "cta"]
