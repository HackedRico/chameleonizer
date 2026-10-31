from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core import finalize, pipeline
from engine.resize import resize
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# Regression tests for the v1.16 rules audit: findings that broke no rule on the
# suite but could on unseen designs. Element ids must not change a layout, no
# image may vanish or stretch, letter-spaced text must stay inside its slot,
# and malformed input must degrade to a valid design instead of raising or hanging.

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = sorted((ROOT / "input").glob("*.json"))
PROBES = sorted((ROOT / "bench" / "suite" / "probe").glob("*__instagram_story.json"))
GEOMETRY = ("x", "y", "width", "height", "font_size")


def _task(path: Path) -> tuple[dict, dict]:
    task = json.loads(path.read_text())
    return task["source"], task["target_canvas"]


def _renamed(source: dict, scheme: str) -> tuple[dict, dict[str, str]]:
    """`source` with every element id and group id replaced; returns the new-to-old id map."""
    out = copy.deepcopy(source)
    rng = random.Random(7)
    groups = sorted({el["group_id"] for el in out["elements"] if el.get("group_id")})
    group_map = {g: str(i) for i, g in enumerate(groups)}
    back = {}
    for i, el in enumerate(out["elements"]):
        new = f"g:{i % max(1, len(groups))}" if scheme == "collide" else f"{rng.getrandbits(48):012x}"
        new = new if new not in back else f"{new}_{i}"
        back[new] = el["id"]
        el["id"] = new
        if el.get("group_id"):
            el["group_id"] = group_map[el["group_id"]]
    return out, back


@pytest.mark.parametrize("scheme", ["random", "collide"])
@pytest.mark.parametrize("path", SAMPLES + PROBES, ids=lambda p: p.stem)
def test_layout_ignores_element_ids(path, scheme):
    """Opaque or adversarial ids, even ones shaped like internal group keys, give the same layout."""
    source, target = _task(path)
    base = {el["id"]: el for el in resize(source, target)["elements"]}
    renamed, back = _renamed(source, scheme)
    for el in resize(renamed, target)["elements"]:
        orig = base[back[el["id"]]]
        assert all(el.get(k) == orig.get(k) for k in GEOMETRY), (back[el["id"]], el, orig)


@pytest.mark.parametrize("size", [(88, 31), (120, 60), (120, 90), (125, 125), (180, 150)])
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_no_image_vanishes_on_tiny_canvases(path, size):
    source, _ = _task(path)
    target = {"width": size[0], "height": size[1]}
    out = resize(source, target)
    assert structural(source, target, out) == []
    for el in out["elements"]:
        if el["type"] in ("image", "shape"):
            assert el["width"] >= 0.99 and el["height"] >= 0.99, el


def test_fill_background_is_never_stretched():
    source, _ = _task(ROOT / "input" / "chameleon_sample_level2_core.json")
    source = copy.deepcopy(source)
    bg = next(el for el in source["elements"] if el["type"] == "image" and el["width"] >= source["canvas"]["width"])
    bg["fit"] = "fill"
    target = {"width": 1600, "height": 400}
    out = {el["id"]: el for el in resize(source, target)["elements"]}[bg["id"]]
    assert abs(out["width"] / out["height"] / (bg["width"] / bg["height"]) - 1) < 0.02
    assert 0 <= out["x"] and out["x"] + out["width"] <= 1600.5


def test_letter_spaced_text_stays_inside_the_canvas():
    measurer, _ = default_ports(ROOT)
    for path in sorted((ROOT / "bench" / "suite" / "probe").glob("*.json")):
        source, target = _task(path)
        spaced = {el["id"] for el in source["elements"] if el.get("letter_spacing")}
        for el in resize(source, target)["elements"]:
            if el["id"] not in spaced:
                continue
            lay = measurer.layout(el["content"], el["font_family"], el.get("font_weight") == "bold",
                                  el.get("font_style") == "italic", el["font_size"], el["width"])
            drawn = max(w + el["letter_spacing"] * (len(line) - 1) for line, w in zip(lay.lines, lay.widths))
            spill = max(0.0, drawn - el["width"])
            left = el["x"] - (spill / 2 if el.get("text_align") == "center" else spill if el.get("text_align") == "right" else 0)
            assert left >= -0.5 and left + max(drawn, el["width"]) <= target["width"] + 0.5, (path.stem, el["id"])


def _sample() -> tuple[dict, dict]:
    return _task(ROOT / "input" / "chameleon_sample_level3_advanced.json")


def test_negative_sizes_do_not_hang():
    source, target = _sample()
    source = copy.deepcopy(source)
    for el in source["elements"]:
        if el["type"] == "image":
            el["width"] = -50
    start = time.monotonic()
    out = resize(source, target)
    assert time.monotonic() - start < 30
    assert sorted(el["id"] for el in out["elements"]) == sorted(el["id"] for el in source["elements"])


def test_zero_width_image_does_not_fall_back():
    source, target = _sample()
    source = copy.deepcopy(source)
    next(el for el in source["elements"] if el["type"] == "image" and el["width"] < 500)["width"] = 0
    measurer, probe = default_ports(ROOT)
    outcome = pipeline.resize_with_report(source, target, measurer, probe)
    assert not outcome.strategy.startswith("fallback")


def test_resize_never_raises_even_when_the_letterbox_fails(monkeypatch):
    source, target = _sample()

    def boom(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr("engine.resize.resize_design", boom)
    monkeypatch.setattr("engine.resize.fallback_layout", boom)
    out = resize(source, target)
    assert sorted(el["id"] for el in out["elements"]) == sorted(el["id"] for el in source["elements"])
    W, H = target["width"], target["height"]
    assert all(0 <= el["x"] and el["x"] + el["width"] <= W and 0 <= el["y"] and el["y"] + el["height"] <= H
               for el in out["elements"])
    assert finalize.emergency_layout({"elements": [{"id": "a", "type": "text", "font_size": "x"}]},
                                     {"width": 10, "height": 10})["elements"][0]["font_size"] == 1


# =============================================================================
# v1.28 final review
# =============================================================================
# Findings from the release review: hard line breaks, a zero-width cover image,
# rigid labels that shrink near the canvas bottom, and the last-resort layout.

LEVEL1 = ROOT / "input" / "chameleon_sample_level1_warmup.json"


def test_longest_word_stops_at_a_hard_line_break():
    measurer, _ = default_ports(ROOT)
    args = ("Solstice Sans", True, False, 40, 10_000.0)
    broken = measurer.layout("Summer\nSale", *args).longest_word
    assert broken == pytest.approx(measurer.layout("Summer", *args).longest_word)


@pytest.mark.parametrize("target", [{"width": 1080, "height": 1350}, {"width": 160, "height": 600},
                                    {"width": 300, "height": 250}])
def test_hard_line_breaks_keep_the_headline_above_the_cta(target):
    source, _ = _task(LEVEL1)
    for el in source["elements"]:
        if el["id"] == "headline":
            el["content"] = "Chase\nthe\nLight"
    out = {el["id"]: el for el in resize(source, target)["elements"]}
    assert out["headline"]["font_size"] > out["cta"]["font_size"]


def test_zero_width_cover_image_keeps_the_engine_layout():
    source, _ = _task(LEVEL1)
    for el in source["elements"]:
        if el["id"] == "logo":
            el.update(width=0, fit="cover")
    measurer, probe = default_ports(ROOT)
    report = pipeline.resize_with_report(source, {"width": 728, "height": 90}, measurer, probe)
    assert not report.strategy.startswith("fallback")


def test_a_shrunk_button_label_stays_above_the_canvas_bottom():
    y = 83.5
    source = {"canvas": {"width": 400, "height": 100}, "elements": [
        {"id": "s", "type": "shape", "x": 300, "y": y - 2, "width": 90, "height": 100 - (y - 2),
         "shape_type": "rectangle", "fill_color": "rgba(0,0,0,1)", "group_id": "g"},
        {"id": "t", "type": "text", "x": 300, "y": y, "width": 90, "height": 100 - y,
         "content": "Shop the new summer collection today", "font_family": "Solstice Sans", "font_size": 12,
         "color": "rgba(255,255,255,1)", "group_id": "g"},
        {"id": "h", "type": "text", "x": 10, "y": 10, "width": 200, "height": 30, "content": "Headline",
         "font_family": "Solstice Sans", "font_size": 24, "color": "rgba(0,0,0,1)"}]}
    measurer, probe = default_ports(ROOT)
    clean = pipeline._sanitized(source)
    ctx = pipeline.Ctx(pipeline.understand(clean, probe), 400.0, 100.0, measurer, probe)
    design = {"canvas": dict(clean["canvas"]), "elements": copy.deepcopy(clean["elements"])}
    pipeline.contain_text(ctx, design)
    label = next(el for el in design["elements"] if el["id"] == "t")
    assert label["font_size"] < 12 and label["y"] == y
    assert label["y"] + label["height"] <= 100


def test_emergency_layout_keeps_an_extreme_image_shape():
    out = finalize.emergency_layout({"elements": [{"id": "a", "type": "image", "width": 1000, "height": 10}]},
                                    {"width": 88, "height": 31})["elements"][0]
    assert out["width"] <= 88 and out["height"] <= 31
    assert out["width"] / out["height"] == pytest.approx(100, rel=0.01)
