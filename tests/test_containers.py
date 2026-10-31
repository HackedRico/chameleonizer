from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.core.types import element_box
from engine.core.understand import understand
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# Container panels: a card or band that holds copy in the source must still sit
# behind that copy after resizing, and a band that bled to canvas edges must
# keep reaching them. Buttons and chips must not be mistaken for panels.

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "bench" / "suite" / "probe"
PORTS = default_ports(ROOT)
PANEL_TASKS = sorted(PROBE.glob("card_panel__*.json")) + sorted(PROBE.glob("photo_band__*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", PANEL_TASKS, ids=lambda p: p.stem)
def test_panel_stays_behind_its_copy(path):
    task = _load(path)
    scene = understand(task["source"], PORTS[1])
    assert len(scene.containers) == 1
    container = scene.containers[0]
    design = resize_with_report(task["source"], task["target_canvas"], *PORTS).design
    assert structural(task["source"], task["target_canvas"], design) == []
    out = {el["id"]: el for el in design["elements"]}
    panel = element_box(out[container.id])
    by_id = {u.id: u for u in scene.units}
    copy = [m for k in container.member_ids if by_id[k].kind != "image" for m in by_id[k].member_ids]
    outside = [m for m in copy if element_box(out[m]).intersection_area(panel) < 0.95 * element_box(out[m]).area]
    assert outside == []


def test_band_keeps_reaching_the_edges_it_bled_to():
    task = _load(PROBE / "photo_band__instagram_story.json")
    scene = understand(task["source"], PORTS[1])
    container = scene.containers[0]
    assert container.edges == (True, False, True, True)
    design = resize_with_report(task["source"], task["target_canvas"], *PORTS).design
    band = element_box(next(el for el in design["elements"] if el["id"] == container.id))
    W, H = task["target_canvas"]["width"], task["target_canvas"]["height"]
    assert band.x <= 0.5 and band.x1 >= W - 0.5 and band.y1 >= H - 0.5


@pytest.mark.parametrize("name", ["two_ctas__instagram_post", "feature_list__instagram_post", "quote_card__medium_rectangle"])
def test_buttons_and_ornaments_are_not_panels(name):
    task = _load(PROBE / f"{name}.json")
    assert understand(task["source"], PORTS[1]).containers == ()
