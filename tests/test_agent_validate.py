from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.core.validate import hard_rule_problems
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# The core validator the harness runs before a model's choice ships. It must
# agree with the bench's `tools.checks.structural` on real outputs, catch each
# kind of injected violation, and never raise.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)
DEV = ["leaderboard_0", "billboard_1", "facebook_post_0", "instagram_post_0", "instagram_portrait_1",
       "instagram_story_0"]
TASKS = sorted((ROOT / "input").glob("*.json")) + [ROOT / "bench" / "suite" / "dev" / f"{t}.json" for t in DEV]


@pytest.fixture(scope="module")
def outputs():
    out = []
    for path in TASKS:
        task = json.loads(path.read_text())
        outcome = resize_with_report(task["source"], task["target_canvas"], *PORTS, alternatives=3)
        out.append((path.stem, task, outcome))
    return out


def test_agrees_with_structural_on_outputs(outputs):
    checked = 0
    for name, task, outcome in outputs:
        for fin in outcome.finalists:
            src, tgt = task["source"], task["target_canvas"]
            assert hard_rule_problems(src, tgt, fin.design) == structural(src, tgt, fin.design) == [], name
            checked += 1
    assert checked >= len(TASKS)


def _injections(source: dict, design: dict) -> list[tuple[str, dict]]:
    text = next(el["id"] for el in design["elements"] if el["type"] == "text")
    src_w = source["canvas"]["width"]
    image = next((s["id"] for s in source["elements"] if s["type"] == "image" and s.get("fit") != "cover"
                  and not (s["x"] <= 0 and s["y"] <= 0 and s["x"] + s["width"] >= src_w)), None)
    cases = []

    def case(label: str, fn) -> None:
        d = copy.deepcopy(design)
        fn(d, {el["id"]: el for el in d["elements"]})
        cases.append((label, d))

    case("canvas", lambda d, els: d.__setitem__("canvas", {"width": 1, "height": 1}))
    case("missing id", lambda d, els: d["elements"].pop())
    case("duplicate id", lambda d, els: d["elements"].append(copy.deepcopy(d["elements"][0])))
    case("off-canvas", lambda d, els: els[text].__setitem__("x", d["canvas"]["width"] + 5))
    case("negative", lambda d, els: els[text].__setitem__("y", -3))
    case("content", lambda d, els: els[text].__setitem__("content", "changed"))
    case("font", lambda d, els: els[text].__setitem__("font_family", "Comic Sans"))
    case("fractional size", lambda d, els: els[text].__setitem__("font_size", 12.5))
    case("type", lambda d, els: els[text].__setitem__("type", "shape"))
    if image is not None:
        case("aspect", lambda d, els: els[image].__setitem__("width", els[image]["width"] * 1.3))
    return cases


def test_catches_injected_violations(outputs):
    for name, task, outcome in outputs[:3]:
        src, tgt = task["source"], task["target_canvas"]
        for label, bad in _injections(src, outcome.design):
            problems = hard_rule_problems(src, tgt, bad)
            assert problems, (name, label)
            assert structural(src, tgt, bad), (name, label)


def test_font_family_may_stay_absent_when_the_source_omits_it(outputs):
    _, task, outcome = outputs[0]
    src, design = copy.deepcopy(task["source"]), copy.deepcopy(outcome.design)
    text = next(el for el in src["elements"] if el["type"] == "text")
    text.pop("font_family")
    next(el for el in design["elements"] if el["id"] == text["id"]).pop("font_family")
    assert hard_rule_problems(src, task["target_canvas"], design) == []


def test_never_raises():
    src = {"canvas": {"width": 100, "height": 0}, "elements": [
        {"id": "a", "type": "image", "x": 5, "y": 5, "width": 10, "height": 0, "asset_id": "x.png"}]}
    out = {"canvas": {"width": 50, "height": 50}, "elements": [
        {"id": "a", "type": "image", "x": 5, "y": 5, "width": 10, "height": 10, "asset_id": "x.png"}]}
    assert hard_rule_problems(src, {"width": 50, "height": 50}, out) == []
    assert hard_rule_problems(src, {"width": 50, "height": 50}, {"elements": None}) != []
    assert hard_rule_problems({}, {}, {})[0].startswith("validator_error:")
    nan = copy.deepcopy(out)
    nan["elements"][0]["x"] = float("nan")
    assert hard_rule_problems(src, {"width": 50, "height": 50}, nan) == ["a: non-finite geometry"]


def _image_design(fit: str, src_box: tuple[float, float, float, float], out_box: tuple[float, float, float, float],
                  src_canvas=(1080, 1080)) -> tuple[dict, dict, dict]:
    sx, sy, sw, sh = src_box
    x, y, w, h = out_box
    base = {"id": "img", "type": "image", "asset_id": "a.png", "fit": fit}
    src = {"canvas": {"width": src_canvas[0], "height": src_canvas[1]},
           "elements": [{**base, "x": sx, "y": sy, "width": sw, "height": sh}]}
    tgt = {"width": 1200, "height": 600}
    out = {"canvas": dict(tgt), "elements": [{**base, "x": x, "y": y, "width": w, "height": h}]}
    return src, tgt, out


@pytest.mark.parametrize("fit, src_box, out_box, ok", [
    # A full-bleed `cover` backdrop is cropped by the renderer, so its box may change shape.
    ("cover", (0, 0, 1080, 1080), (0, 0, 1200, 600), True),
    ("cover", (-10, -5, 1100, 1090), (0, 0, 1200, 600), True),
    # A `cover` image that does not bleed off every edge keeps its aspect.
    ("cover", (100, 100, 400, 400), (0, 0, 800, 400), False),
    ("cover", (0, 0, 1080, 900), (0, 0, 1200, 600), False),
    # A header strip at the top-left spanning the width is not a backdrop, whatever its fit.
    ("contain", (0, 0, 1080, 200), (0, 0, 600, 600), False),
    ("contain", (0, 0, 1080, 1080), (0, 0, 1200, 600), False),
    ("contain", (100, 100, 400, 400), (10, 10, 300, 300), True),
])
def test_aspect_exemption_is_only_a_full_bleed_cover_backdrop(fit, src_box, out_box, ok):
    src, tgt, out = _image_design(fit, src_box, out_box)
    problems = hard_rule_problems(src, tgt, out)
    assert (problems == []) == ok, problems
    if not ok:
        assert problems[0].startswith("img: aspect drift")


@pytest.mark.parametrize("kind, field", [("text", "font_size"), ("text", "color"), ("shape", "fill_color"),
                                         ("image", "asset_id"), ("text", "content")])
def test_required_fields_must_be_present(outputs, kind, field):
    for name, task, outcome in outputs:
        src, tgt = task["source"], task["target_canvas"]
        target = next((s for s in src["elements"] if s["type"] == kind and field in s), None)
        if target is None:
            continue
        design = copy.deepcopy(outcome.design)
        next(el for el in design["elements"] if el["id"] == target["id"]).pop(field)
        assert any(p.startswith(f"{target['id']}: missing") and field in p
                   for p in hard_rule_problems(src, tgt, design)), (name, field)
        return
    pytest.skip(f"no {kind} element with {field}")


def test_reference_allows_only_boxes_and_text_sizes(outputs):
    def plain_image(design: dict) -> str | None:
        """An image that is not a `cover` backdrop, so changing its fit leaves the hard rules alone."""
        return next((el["id"] for el in design["elements"] if el["type"] == "image" and el.get("fit") != "cover"),
                    None)

    name, task, outcome = next(o for o in outputs if plain_image(o[2].design))
    src, tgt, ref = task["source"], task["target_canvas"], outcome.design
    els = {el["id"]: el for el in ref["elements"]}
    text = next(el["id"] for el in ref["elements"] if el["type"] == "text")
    image = plain_image(ref)
    assert hard_rule_problems(src, tgt, ref, ref) == []

    moved = copy.deepcopy(ref)
    m = {el["id"]: el for el in moved["elements"]}
    m[text].update(x=1, y=1, font_size=int(els[text]["font_size"]) + 1)
    assert hard_rule_problems(src, tgt, moved, ref) == []

    for el_id, field, value in ((image, "fit", "fill"), (image, "opacity", 0.1), (image, "z_index", 99),
                                (text, "color", "rgba(255,0,0,0.05)"), (text, "text_align", "right")):
        changed = copy.deepcopy(ref)
        next(el for el in changed["elements"] if el["id"] == el_id)[field] = value
        assert hard_rule_problems(src, tgt, changed) == [], (name, field)  # no hard rule names these fields
        problems = hard_rule_problems(src, tgt, changed, ref)
        assert problems == [f"{el_id}: changed {field} against the reference"], (field, problems)
    extra = copy.deepcopy(ref)
    extra["note"] = "x"
    assert hard_rule_problems(src, tgt, extra, ref) == ["note: changed against the reference"]


def test_review_gap_case_is_caught():
    """A stretched header image with its fit, opacity and stacking changed and near-invisible text."""
    src = {"canvas": {"width": 1080, "height": 1080}, "elements": [
        {"id": "h", "type": "image", "x": 0, "y": 0, "width": 1080, "height": 200, "asset_id": "a.png",
         "fit": "contain", "opacity": 1, "z_index": 1},
        {"id": "t", "type": "text", "x": 100, "y": 400, "width": 800, "height": 100, "content": "Hi",
         "font_family": "F", "font_size": 40, "color": "rgba(0,0,0,1)"}]}
    tgt = {"width": 1080, "height": 1080}
    ref = copy.deepcopy(src)
    bad = copy.deepcopy(src)
    bad["elements"][0].update(width=1080, height=600, fit="fill", opacity=0.1, z_index=99)
    bad["elements"][1]["color"] = "rgba(255,0,0,0.05)"
    problems = hard_rule_problems(src, tgt, bad, ref)
    assert any(p.startswith("h: aspect drift") for p in problems)
    assert "h: changed fit, opacity, z_index against the reference" in problems
    assert "t: changed color against the reference" in problems
