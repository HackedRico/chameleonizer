from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report

# =============================================================================
# Module Overview
# =============================================================================
# Regression tests for inputs the schema allows that once raised inside the
# engine and fell back to the letterbox: a text without its optional font
# fields, a font size under the minimum, a group of one element and a group with
# no label. They call the core directly, so a raise fails the test instead of
# hiding behind the fallback. Also pins fine print after the copy it follows.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, PROBE = default_ports(ROOT)
LEVEL1 = ROOT / "input" / "chameleon_sample_level1_warmup.json"
TARGETS = [{"width": 1600, "height": 400}, {"width": 728, "height": 90}, {"width": 1080, "height": 1350},
           {"width": 160, "height": 600}]


def _source(path: Path = LEVEL1) -> dict:
    return copy.deepcopy(json.loads(path.read_text())["source"])


def _element(source: dict, eid: str) -> dict:
    return next(el for el in source["elements"] if el["id"] == eid)


def _layout(source: dict, target: dict) -> list[dict]:
    outcome = resize_with_report(source, target, MEASURER, PROBE)
    assert not outcome.strategy.startswith("fallback")
    return outcome.design["elements"]


@pytest.mark.parametrize("target", TARGETS, ids=lambda t: f"{t['width']}x{t['height']}")
@pytest.mark.parametrize("missing", [("font_family",), ("font_size",), ("font_family", "font_size")])
def test_text_without_its_optional_font_fields(missing, target):
    """Only id, type and geometry are required; the engine never names a font the source did not."""
    source = _source()
    for key in missing:
        del _element(source, "headline")[key]
    out = _element({"elements": _layout(source, target)}, "headline")
    assert ("font_family" in out) == ("font_family" not in missing)
    assert out["font_size"] >= 1 and float(out["font_size"]).is_integer()


@pytest.mark.parametrize("size", [0, 0.5])
def test_font_size_under_the_schema_minimum(size):
    source = _source()
    _element(source, "headline")["font_size"] = size
    out = _element({"elements": _layout(source, TARGETS[2])}, "headline")
    assert out["font_size"] >= 1


@pytest.mark.parametrize("target", TARGETS, ids=lambda t: f"{t['width']}x{t['height']}")
@pytest.mark.parametrize("eid", ["bg", "logo", "headline", "cta"])
def test_a_group_of_one_lays_out_like_the_element(eid, target):
    """A lone group id once turned the logo into the call to action, and raised on strips."""
    plain = _layout(_source(), target)
    source = _source()
    _element(source, eid)["group_id"] = "only-me"
    grouped = _layout(source, target)
    assert _element({"elements": grouped}, eid).pop("group_id") == "only-me"
    assert grouped == plain


@pytest.mark.parametrize("target", TARGETS[:2], ids=lambda t: f"{t['width']}x{t['height']}")
def test_a_group_without_a_label_on_strips(target):
    source = _source()
    for eid in ("bg", "logo"):
        _element(source, eid)["group_id"] = "pictures"
    ids = sorted(el["id"] for el in _layout(source, target))
    assert ids == sorted(el["id"] for el in source["elements"])


def test_fine_print_follows_the_copy_it_belongs_after():
    """Fine print parked between a headline and its big number read as a broken layout."""
    task = json.loads((ROOT / "bench" / "suite" / "probe" / "big_number__linkedin_banner.json").read_text())
    texts = [el for el in task["source"]["elements"] if el["type"] == "text"]
    legal = min(texts, key=lambda el: el["font_size"])
    out = {el["id"]: el for el in _layout(task["source"], task["target_canvas"])}
    fine = out[legal["id"]]
    for el in texts:
        if el is legal or el.get("group_id"):
            continue
        copy_box = out[el["id"]]
        shares_column = min(fine["x"] + fine["width"], copy_box["x"] + copy_box["width"]) > max(fine["x"], copy_box["x"])
        if shares_column:
            assert fine["y"] >= copy_box["y"], el["id"]
