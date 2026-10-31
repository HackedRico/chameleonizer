from __future__ import annotations

import math
from collections import Counter
from typing import Any

# =============================================================================
# Module Overview
# =============================================================================
# The hard rules as one pure check the core can run on any finished design:
# the canvas is the target, every source id appears exactly once, every box is
# inside the canvas, required fields are present, copy, fonts, assets and
# shapes are unchanged, font sizes are integers, and every image keeps its
# aspect within 5% unless it is a `cover` backdrop that bleeds off all four
# edges of the source. Ported from `tools/checks.structural`, which the image
# does not ship, and stricter than it on that exemption. Given a reference
# design, such as the engine's own pick, it also refuses any change beyond
# geometry and text size that the reference does not already make. The model
# harness runs it before it lets a model's choice ship.

# Fields a resize may never change.
FROZEN = ("type", "content", "font_family", "asset_id", "shape_type")
# Fields each type needs, as in `tools/checks.structural`; one the source leaves out may stay out.
REQUIRED = {"text": ("content", "font_family", "font_size", "color"), "image": ("asset_id",),
            "shape": ("shape_type", "fill_color")}
# Boxes may reach this far past the canvas edge, as in the official self-check.
_BOUNDS_SLACK = 0.5
# Largest aspect drift allowed for an image that is not a full-bleed `cover` backdrop.
_ASPECT_DRIFT = 0.05
_GEOMETRY = ("x", "y", "width", "height")
# What a finalist may change against the reference: its box, and a text element's size.
_MOVABLE = frozenset(_GEOMETRY)
_MOVABLE_TEXT = _MOVABLE | {"font_size"}


def hard_rule_problems(
    source: dict[str, Any], target: dict[str, Any], design: dict[str, Any], reference: dict[str, Any] | None = None
) -> list[str]:
    """Every hard-rule violation in `design`; [] when valid.

    With `reference`, a design built for the same target, any field outside each element's box
    (and a text element's `font_size`) that differs from the reference's is a problem too, as is
    any top-level field other than `elements`. Never raises: an internal error is reported as the
    problem "validator_error:<Type>"."""
    try:
        problems = _problems(source, target, design)
        if reference is not None:
            problems += _changes(reference, design)
        return problems
    except Exception as exc:  # a validator that raises would let the caller skip it
        return [f"validator_error:{type(exc).__name__}"]


def _problems(source: dict[str, Any], target: dict[str, Any], design: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if design.get("canvas") != {"width": target["width"], "height": target["height"]}:
        problems.append("canvas does not match target")
    src = {el["id"]: el for el in source["elements"]}
    elements = design.get("elements")
    if not isinstance(elements, list):
        return problems + ["elements missing"]
    if Counter(el.get("id") for el in elements) != Counter(list(src)):
        problems.append("element ids differ from source")
    W, H = float(target["width"]), float(target["height"])
    src_w, src_h = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    for el in elements:
        s = src.get(el.get("id"))
        if s is None:
            continue
        eid = el["id"]
        missing = [k for k in _GEOMETRY if k not in el]
        missing += [k for k in REQUIRED.get(str(s.get("type")), ()) if k in s and k not in el]
        if missing:
            problems.append(f"{eid}: missing {', '.join(missing)}")
        if any(k not in el for k in _GEOMETRY):
            continue
        x, y, w, h = (float(el[k]) for k in _GEOMETRY)
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            problems.append(f"{eid}: non-finite geometry")
            continue
        for field in FROZEN:
            # A field the source leaves out, such as `font_family`, must stay out or stay equal.
            if s.get(field) != el.get(field):
                problems.append(f"{eid}: changed {field}")
        if x < -_BOUNDS_SLACK or y < -_BOUNDS_SLACK or x + w > W + _BOUNDS_SLACK or y + h > H + _BOUNDS_SLACK:
            problems.append(f"{eid}: off-canvas")
        if el.get("type") == "text" and "font_size" in el:
            size = float(el["font_size"])
            if not math.isfinite(size) or size != int(size):
                problems.append(f"{eid}: fractional font_size")
        if el.get("type") == "image" and h > 0:
            sx, sy, sw, sh = (float(s.get(k, 0) or 0) for k in _GEOMETRY)
            # The only exemption: a `cover` backdrop the renderer crops, full-bleed on both axes.
            backdrop = (el.get("fit") == "cover" and sx <= _BOUNDS_SLACK and sy <= _BOUNDS_SLACK
                        and sx + sw >= src_w - _BOUNDS_SLACK and sy + sh >= src_h - _BOUNDS_SLACK)
            if not backdrop and sw > 0 and sh > 0:
                ratio = (w / h) / (sw / sh)
                if abs(ratio - 1) > _ASPECT_DRIFT:
                    problems.append(f"{eid}: aspect drift {ratio:.3f}")
    return problems


def _changes(reference: dict[str, Any], design: dict[str, Any]) -> list[str]:
    """Fields `design` changes against `reference` beyond boxes and text sizes."""
    top = sorted((set(reference) | set(design)) - {"elements"}, key=str)
    problems = [f"{key}: changed against the reference" for key in top if reference.get(key) != design.get(key)]
    ref = {el.get("id"): el for el in reference.get("elements") or []}
    for el in design.get("elements") or []:
        r = ref.get(el.get("id"))
        if r is None:
            continue  # ids are checked against the source
        movable = _MOVABLE_TEXT if r.get("type") == "text" else _MOVABLE
        changed = sorted(k for k in (set(r) | set(el)) - movable if r.get(k) != el.get(k))
        if changed:
            problems.append(f"{el['id']}: changed {', '.join(changed)} against the reference")
    return problems
