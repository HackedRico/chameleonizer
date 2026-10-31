from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_design

# =============================================================================
# Module Overview
# =============================================================================
# Two buttons the source sets side by side stay side by side when the canvas has
# the room: stacked, the second read as an orphan below the first (judge).

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)
PROBE = ROOT / "bench" / "suite" / "probe"


@pytest.mark.parametrize("target", ["instagram_post", "instagram_story", "half_page", "billboard"])
def test_side_by_side_buttons_share_a_row(target: str):
    task = json.loads((PROBE / f"two_ctas__{target}.json").read_text())
    out = resize_design(task["source"], task["target_canvas"], *PORTS)
    groups = {e["id"]: e["group_id"] for e in task["source"]["elements"] if e.get("group_id")}
    buttons = [e for e in out["elements"] if e["type"] == "shape" and e["id"] in groups]
    assert len(buttons) == 2
    a, b = sorted(buttons, key=lambda e: e["x"])
    assert abs((a["y"] + a["height"] / 2) - (b["y"] + b["height"] / 2)) < 0.5 * min(a["height"], b["height"])
    assert a["x"] + a["width"] <= b["x"]
