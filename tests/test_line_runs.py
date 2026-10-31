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
# Line runs: short lines the source stacks as one block, such as an event's time
# and place or a feature list, form one unit that keeps its order, alignment and
# shared size on every target, instead of splitting into scattered leftovers.

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "bench" / "suite" / "probe"
PORTS = default_ports(ROOT)
RUN_TASKS = sorted(PROBE.glob("event_story__*.json")) + sorted(PROBE.glob("feature_list__*.json"))


def _source(name: str) -> dict:
    return json.loads((PROBE / name).read_text())["source"]


def _runs(source: dict) -> list[list[str]]:
    scene = understand(source, PORTS[1])
    return [[scene.elements[c.member_ids[0]]["content"] for c in u.children]
            for u in scene.units if u.kind == "lines"]


def test_time_and_place_form_one_run_and_the_bolder_date_stays_apart():
    assert _runs(_source("event_story__leaderboard.json")) == [["6 to 9 pm", "Pier 39 Rooftop"]]


def test_feature_list_forms_one_run_carrying_its_bullets():
    source = _source("feature_list__billboard.json")
    scene = understand(source, PORTS[1])
    runs = [u for u in scene.units if u.kind == "lines"]
    assert len(runs) == 1
    shapes = [m for m in runs[0].member_ids if scene.elements[m]["type"] == "shape"]
    assert len(shapes) == 3


def test_runs_ignore_element_ids():
    source = _source("feature_list__billboard.json")
    renamed = json.loads(json.dumps(source))
    for i, el in enumerate(renamed["elements"]):
        el["id"] = f"z{97 - i}"
    assert _runs(renamed) == _runs(source)


def test_a_divider_between_lines_keeps_them_apart():
    source = _source("feature_list__billboard.json")
    els = {el["content"]: el for el in source["elements"] if el["type"] == "text"}
    upper, lower = els["Real fruit"], els["No added sugar"]
    y = upper["y"] + upper["height"] + 2
    source["elements"].append({"id": "rule", "type": "shape", "x": upper["x"], "y": y, "width": 200, "height": 3,
                               "z_index": 3, "shape_type": "rectangle", "fill_color": "rgba(240,178,66,1)"})
    assert _runs(source) == [["No added sugar", "Small batch"]]


@pytest.mark.parametrize("path", RUN_TASKS, ids=lambda p: p.stem)
def test_run_keeps_order_size_and_alignment(path: Path):
    task = json.loads(path.read_text())
    scene = understand(task["source"], PORTS[1])
    out = resize_with_report(task["source"], task["target_canvas"], *PORTS)
    els = {el["id"]: el for el in out.design["elements"]}
    for unit in (u for u in scene.units if u.kind == "lines"):
        lines = [els[c.member_ids[0]] for c in unit.children]
        boxes = [element_box(el) for el in lines]
        src = [scene.elements[c.member_ids[0]] for c in unit.children]
        # Lines the source set at one size share one size.
        for a, b, sa, sb in zip(lines, lines[1:], src, src[1:]):
            if sa["font_size"] == sb["font_size"]:
                assert a["font_size"] == b["font_size"]
        stacked = all(b.y >= a.y + 0.5 * a.h for a, b in zip(boxes, boxes[1:]))
        side_by_side = all(b.x >= a.x1 for a, b in zip(boxes, boxes[1:]))
        assert stacked or side_by_side, f"run out of order in {path.stem}"
        if stacked:
            align = src[0].get("text_align", "left")
            edge = {"center": lambda b: b.cx, "right": lambda b: b.x1}.get(align, lambda b: b.x)
            assert max(edge(b) for b in boxes) - min(edge(b) for b in boxes) <= 1.0
        else:
            assert len({round(b.y1, 1) for b in boxes}) == 1
