from __future__ import annotations

import copy
import math
from typing import Any

from engine.core.types import Candidate, Scene, element_box

# =============================================================================
# Module Overview
# =============================================================================
# Turns a `Candidate` into a schema design and guarantees the hard rules:
# every source id exactly once, everything inside the canvas, non-background
# images within 5% of their source aspect, integer font sizes. `fallback_layout`
# is the always-valid letterbox used when anything upstream fails.

# The rules allow 5% aspect drift; correct anything past 2% so rounding never trips the check.
_ASPECT_TOLERANCE = 0.02
# Smallest side an image or shape may end up with. A zero-size box has no aspect at all, so a
# checker dividing by it would fail, and the element silently vanishes from the render.
_MIN_SIDE = 1.0


def stretches(el: dict[str, Any]) -> bool:
    """True when this image must keep its box shape: every image but a `cover` one.

    The starter code exempts only "a full-bleed / `fit: \"cover\"` element, which is expected to
    crop" from the 5% aspect rule, so a `contain` or `fill` backdrop keeps its own shape too."""
    return el["type"] == "image" and el.get("fit", "contain") != "cover"


def backdrop_box(el: dict[str, Any], W: float, H: float) -> dict[str, float]:
    """Box for a full-bleed backdrop on a `W` by `H` canvas: the whole canvas, unless that would
    stretch its pixels, in which case the largest box of its own shape, centred."""
    if not stretches(el):
        return {"x": 0, "y": 0, "width": W, "height": H}
    aspect = element_box(el).aspect or 1.0
    w = min(W, H * aspect)
    h = w / aspect
    return {"x": (W - w) / 2, "y": (H - h) / 2, "width": w, "height": h}


def apply_candidate(scene: Scene, target: dict[str, Any], cand: Candidate) -> dict[str, Any]:
    """Source design with the candidate's patches applied, rules enforced."""
    W, H = float(target["width"]), float(target["height"])
    letterbox = _letterbox_patches(scene.source, W, H)
    elements = []
    for el in scene.source["elements"]:
        out = copy.deepcopy(el)
        out.update(cand.patches.get(el["id"], letterbox[el["id"]]))
        elements.append(out)
    design = {"canvas": {"width": int(target["width"]), "height": int(target["height"])}, "elements": elements}
    return enforce_rules(design, scene)


def enforce_rules(design: dict[str, Any], scene: Scene) -> dict[str, Any]:
    """Clamp geometry so the design passes the structural checks, whatever layout produced."""
    W, H = float(design["canvas"]["width"]), float(design["canvas"]["height"])
    background = set(scene.background_ids)
    for el in design["elements"]:
        src = scene.elements[el["id"]]
        if el["type"] == "text":
            el["font_size"] = max(1, int(round(float(el["font_size"]))))
        # Every image keeps its shape except a backdrop the renderer crops or letterboxes: a
        # foreground `cover` image squeezed to a strip shows only a sliver of its pixels.
        keep_aspect = el["type"] == "image" and (el["id"] not in background or stretches(el))
        want = element_box(src).aspect
        if keep_aspect and src["width"] > 0 and src["height"] > 0:
            if abs(float(el["width"]) / max(float(el["height"]), 1e-6) / want - 1) > _ASPECT_TOLERANCE:
                el["height"] = float(el["width"]) / want
        if el["type"] != "text":
            _keep_visible(el, want if keep_aspect else None)
        _clamp_inside(el, W, H, keep_aspect)
    return design


def _keep_visible(el: dict[str, Any], aspect: float | None) -> None:
    """Grow a box below `_MIN_SIDE` on either side back to it, keeping `aspect` when given."""
    w, h = float(el["width"]), float(el["height"])
    if w >= _MIN_SIDE and h >= _MIN_SIDE:
        return
    if aspect:
        # The shortest side becomes `_MIN_SIDE` and the other follows the aspect.
        w, h = (_MIN_SIDE, _MIN_SIDE / aspect) if aspect <= 1 else (_MIN_SIDE * aspect, _MIN_SIDE)
    else:
        w, h = max(w, _MIN_SIDE), max(h, _MIN_SIDE)
    cx, cy = float(el["x"]) + float(el["width"]) / 2, float(el["y"]) + float(el["height"]) / 2
    el.update({"x": cx - w / 2, "y": cy - h / 2, "width": w, "height": h})


def _clamp_inside(el: dict[str, Any], W: float, H: float, keep_aspect: bool) -> None:
    """Shrink then shift `el` so its box lies within the canvas."""
    w, h = max(0.0, float(el["width"])), max(0.0, float(el["height"]))
    if w > W or h > H:
        if keep_aspect:
            k = min(W / w if w else 1.0, H / h if h else 1.0)
            w, h = w * k, h * k
        else:
            w, h = min(w, W), min(h, H)
    x = min(max(0.0, float(el["x"])), W - w)
    y = min(max(0.0, float(el["y"])), H - h)
    el.update({"x": round(x, 2), "y": round(y, 2), "width": round(w, 2), "height": round(h, 2)})
    # Rounding can push the far edge a hair past the canvas; pull it back.
    if el["x"] + el["width"] > W:
        el["width"] = round(W - el["x"], 2)
    if el["y"] + el["height"] > H:
        el["height"] = round(H - el["y"], 2)


def _letterbox_patches(source: dict[str, Any], W: float, H: float) -> dict[str, dict[str, Any]]:
    """Uniform scale-to-fit placement for every element, the shape-preserving baseline."""
    sw, sh = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    s = min(W / sw, H / sh)
    ox, oy = (W - sw * s) / 2, (H - sh * s) / 2
    out: dict[str, dict[str, Any]] = {}
    for el in source["elements"]:
        b = element_box(el)
        covers = b.x <= 0 and b.y <= 0 and b.x1 >= sw and b.y1 >= sh
        if covers:
            out[el["id"]] = backdrop_box(el, W, H)
            continue
        patch: dict[str, Any] = {"x": ox + b.x * s, "y": oy + b.y * s, "width": b.w * s, "height": b.h * s}
        if el["type"] == "text":
            patch["font_size"] = max(1, round(float(el["font_size"]) * s))
        out[el["id"]] = patch
    return out


def fallback_layout(source: dict[str, Any], target: dict[str, Any], scene: Scene | None = None) -> dict[str, Any]:
    """Letterboxed copy of the source that always passes the structural rules."""
    W, H = float(target["width"]), float(target["height"])
    patches = _letterbox_patches(source, W, H)
    elements = []
    for el in source["elements"]:
        out = copy.deepcopy(el)
        out.update(patches[el["id"]])
        if out["type"] == "text":
            out["font_size"] = max(1, int(out["font_size"]))
        keep = out["type"] == "image"
        if out["type"] != "text":
            src_box = element_box(el)
            _keep_visible(out, src_box.aspect if keep and src_box.w > 0 and src_box.h > 0 else None)
        _clamp_inside(out, W, H, keep)
        elements.append(out)
    return {"canvas": {"width": int(target["width"]), "height": int(target["height"])}, "elements": elements}


def emergency_layout(source: Any, target: Any) -> dict[str, Any]:
    """Every source element as a small box in the top-left corner: the last resort when even the
    letterbox fails on malformed input. It keeps the id contract, the canvas bounds and image
    shapes, and never raises."""

    def side(key: str) -> int:
        try:
            value = float(target.get(key, 1)) if isinstance(target, dict) else 1.0
        except (TypeError, ValueError):
            value = 1.0
        return max(1, int(value)) if math.isfinite(value) else 1

    W, H = side("width"), side("height")
    raw = source.get("elements", []) if isinstance(source, dict) else []
    elements = []
    for el in raw if isinstance(raw, list) else []:
        if not isinstance(el, dict):
            elements.append(el)
            continue
        out = copy.deepcopy(el)
        try:
            aspect = float(el.get("width", 1)) / float(el.get("height", 1))
        except (TypeError, ValueError, ZeroDivisionError):
            aspect = 1.0
        aspect = aspect if math.isfinite(aspect) and aspect > 0 else 1.0
        w, h = (aspect, 1.0) if aspect >= 1 else (1.0, 1 / aspect)
        k = min(1.0, W / w, H / h)  # one factor for both sides, so the shape survives
        w, h = w * k, h * k
        out.update({"x": 0, "y": 0, "width": w, "height": h})
        if out.get("type") == "text":
            try:
                out["font_size"] = max(1, int(float(out.get("font_size", 1))))
            except (TypeError, ValueError, OverflowError):
                out["font_size"] = 1
        elements.append(out)
    return {"canvas": {"width": W, "height": H}, "elements": elements}
