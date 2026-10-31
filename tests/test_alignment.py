from __future__ import annotations

import json
from pathlib import Path

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_design
from engine.core.sizing import probe_visible_fraction
from engine.core.understand import understand

# =============================================================================
# Module Overview
# =============================================================================
# Left-aligned copy shares one drawn left edge. These cases pin the rules behind
# it: button labels never make a layout centred, and a logo lines up with the
# copy by its visible pixels.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)
PROBE = ROOT / "bench" / "suite" / "probe"


def _task(name: str) -> dict:
    return json.loads((PROBE / f"{name}.json").read_text())


def _resize(name: str) -> dict:
    task = _task(name)
    return resize_design(task["source"], task["target_canvas"], *PORTS)


def test_centred_button_labels_do_not_centre_left_aligned_copy():
    scene = understand(_task("two_ctas__instagram_post")["source"], PORTS[1])
    assert not scene.centered


def test_logo_pixels_line_up_with_left_aligned_copy():
    for name in ("editorial_left__instagram_story", "two_ctas__instagram_story"):
        task = _task(name)
        out = resize_design(task["source"], task["target_canvas"], *PORTS)
        logo = next(e for e in out["elements"] if e.get("asset_id") == "solstice_logo_lockup.png")
        frac = probe_visible_fraction(PORTS[1], logo)
        logo_left = logo["x"] + frac.x * logo["width"]
        copy_left = min(e["x"] for e in out["elements"] if e["type"] == "text" and e.get("text_align") == "left")
        assert abs(logo_left - copy_left) <= 2, name
