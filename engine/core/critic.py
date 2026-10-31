from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from engine.core.decor import halo_owners
from engine.core.sizing import Ctx, spaces_read, visible_fraction
from engine.core.types import CONTENT_ROLES, ROLE_PRIORITY, Box, Unit, element_box, union_all

# =============================================================================
# Module Overview
# =============================================================================
# The deterministic critic: scores a finished design against coded stand-ins
# for the rubric rows (collisions, readability, overflow, brand, hierarchy,
# use of space, clutter, faithfulness to the source composition). `critique`
# returns a `Report`; the pipeline keeps the candidate with the highest score.
# Backdrop detail comes through the asset probe; full pixel checks live in the
# test bench.


@dataclass(frozen=True)
class Report:
    """Score out of 100 plus the penalty behind each deduction."""

    score: float
    penalties: dict[str, float] = field(default_factory=dict)


def critique(ctx: Ctx, design: dict[str, Any], hidden: set[str]) -> Report:
    """Score `design`; higher is better, 100 means no coded check fired."""
    W, H = float(design["canvas"]["width"]), float(design["canvas"]["height"])
    els = {el["id"]: el for el in design["elements"]}
    pen: dict[str, float] = {}

    def add(key: str, value: float) -> None:
        if value > 0:
            pen[key] = pen.get(key, 0.0) + value

    units = [u for u in ctx.scene.units if u.role in CONTENT_ROLES and not set(u.member_ids) <= hidden]
    boxes: dict[str, Box] = {}
    # Each unit without its separator marks: a separator sits between its two texts by design.
    bare: dict[str, Box] = {}
    separators = {mid for mid, _, _ in ctx.scene.separators}
    for u in units:
        parts = []
        for m in u.member_ids:
            vb, overflow = _visual_box(ctx, els[m])
            parts.append(vb)
            add("text_overflow", 6.0 * overflow)
            if els[m]["type"] == "text" and vb.area > 0:
                # Lines that spill past the canvas are cut off in the render, whatever the box says.
                lost = 1.0 - vb.intersection_area(Box(0, 0, W, H)) / vb.area
                add("cut_off", (6.0 + 20.0 * lost) if lost > 0.01 else 0.0)
        boxes[u.id] = union_all(parts)
        bare[u.id] = union_all([b for m, b in zip(u.member_ids, parts) if m not in separators] or parts)

    _collisions(ctx, units, boxes, bare, add)
    _readability(ctx, units, els, add)
    _brand_and_hierarchy(ctx, units, els, boxes, W, H, add)
    _space(ctx, units, boxes, W, H, add)
    _clutter(ctx, units, els, boxes, add)
    _source_side(ctx, units, boxes, W, add)
    for u in ctx.scene.units:
        if set(u.member_ids) & hidden:
            # Hiding costs more the more the unit matters to the message.
            add("hidden_content", max(0.5, (10 - ROLE_PRIORITY[u.role]) * 1.2))
    return Report(round(100.0 - sum(pen.values()), 2), {k: round(v, 2) for k, v in pen.items()})


# =============================================================================
# Visual extents
# =============================================================================


def _visual_box(ctx: Ctx, el: dict[str, Any]) -> tuple[Box, float]:
    """Where `el` actually paints, plus how badly its text spills (0 means it fits)."""
    b = element_box(el)
    overflow = 0.0
    if el["type"] == "text":
        bold, italic = el.get("font_weight") == "bold", el.get("font_style") == "italic"
        size = int(el["font_size"])
        lay = ctx.measurer.layout(el.get("content", ""), el["font_family"], bold, italic, size, max(1.0, b.w))
        h = len(lay.lines) * size * float(el.get("line_height", 1.2))
        w = min(max(lay.max_width, lay.longest_word), max(b.w, lay.longest_word))
        align = el.get("text_align", "left")
        x = b.x + (b.w - w) / 2 if align == "center" else b.x + b.w - w if align == "right" else b.x
        if h > b.h + 1:
            overflow += (h - b.h) / max(b.h, 1.0)
        if lay.longest_word > b.w + 1:
            overflow += 1.0
        b = Box(x, b.y, w, h)
    elif el["type"] == "image":
        frac = visible_fraction(ctx, el)
        b = Box(b.x + frac.x * b.w, b.y + frac.y * b.h, frac.w * b.w, frac.h * b.h)
    rot = math.radians(float(el.get("rotation", 0) or 0))
    if abs(rot) > 1e-3:
        cw, sw = abs(math.cos(rot)), abs(math.sin(rot))
        rw, rh = b.w * cw + b.h * sw, b.w * sw + b.h * cw
        b = Box(b.cx - rw / 2, b.cy - rh / 2, rw, rh)
    return b, overflow


# =============================================================================
# Rubric stand-ins
# =============================================================================

# Unit kinds that carry copy.
_COPY_KINDS = ("text", "lines", "chip", "row")


def _collisions(ctx: Ctx, units: list[Unit], boxes: dict[str, Box], bare: dict[str, Box], add) -> None:
    """New overlaps between content units; overlaps the source already had are intended.

    A separator mark may reach into the box of the other text it separates, whose top is blank
    above the glyphs; layout keeps it clear of the glyphs themselves."""
    pairs = {frozenset((upper, lower)) for _, upper, lower in ctx.scene.separators}
    for i, a in enumerate(units):
        for b in units[i + 1:]:
            if frozenset((a.id, b.id)) in pairs:
                inter = bare[a.id].intersection_area(bare[b.id])
            else:
                inter = boxes[a.id].intersection_area(boxes[b.id])
            if inter <= 0 or a.box.intersection_area(b.box) > 0:
                continue
            share = inter / max(1.0, min(boxes[a.id].area, boxes[b.id].area))
            text_involved = a.kind in _COPY_KINDS or b.kind in _COPY_KINDS
            add("collision", (25.0 if text_involved else 12.0) * min(1.0, share) + 3.0)


def _readability(ctx: Ctx, units: list[Unit], els: dict[str, dict[str, Any]], add) -> None:
    """Text below the canvas's readable size, weighted by how much the text matters.

    Text whose word spaces the renderer closes up costs more again: the judge read it as
    'AWARDWINNING' on chips, buttons and badges."""
    for u in units:
        for m in u.member_ids:
            el = els[m]
            if el["type"] != "text":
                continue
            weight = 3.0 if ROLE_PRIORITY[u.role] <= 4 else 1.5
            short = ctx.min_font - int(el["font_size"])
            if short > 0:
                add("small_text", weight * short)
            if not spaces_read(ctx, [el], int(el["font_size"])):
                add("collapsed_spaces", _COLLAPSE_WEIGHT * weight)


# Collapsed word spaces cost as much as text this many pixels under the readable size.
_COLLAPSE_WEIGHT = 2.0


def _brand_and_hierarchy(ctx: Ctx, units: list[Unit], els, boxes: dict[str, Box], W, H, add) -> None:
    """Logo big enough to read, and the headline still the dominant text."""
    for u in units:
        if u.role == "logo":
            share = boxes[u.id].h / H
            add("logo_small", max(0.0, 0.05 - share) * 200)
        if u.role == "hero":
            # The product is usually the reason the design exists; a thumbnail-sized hero reads as an afterthought.
            area = boxes[u.id].area / (W * H)
            add("hero_small", max(0.0, 0.06 - area) * 100)
    heads = [u for u in units if u.role == "headline"]
    if heads:
        head_font = int(els[heads[0].member_ids[0]]["font_size"])
        others = [
            int(els[m]["font_size"]) for u in units if u.role not in ("headline",)
            for m in u.member_ids if els[m]["type"] == "text"
        ]
        if others and max(others) > head_font:
            add("hierarchy", 8.0)
        add("weak_headline", max(0.0, 0.06 * ctx.short_side - head_font) * 0.8)
        logo = next((u for u in units if u.role == "logo"), None)
        if logo is not None:
            add("logo_inflated", _LOGO_GROWTH_WEIGHT * max(0.0, _logo_growth(ctx, logo, heads[0], boxes, els)
                                                         - _LOGO_GROWTH_LIMIT))
        hb = boxes[heads[0].id]
        lines = round(hb.h / (head_font * float(els[heads[0].member_ids[0]].get("line_height", 1.2))))
        if W > 1.3 * H:
            # Judge feedback: a headline stacked into many short lines on a wide canvas looks squeezed.
            add("headline_lines", max(0, lines - 3) * 4.0)
        # Judge feedback: a narrow column that doubles the designer's line count reads as ragged.
        add("headline_rewrap", max(0, lines - _source_lines(ctx, heads[0]) - 2) * 0.5)


# A logo may grow this many times more than the headline before it starts to outweigh the copy,
# and each further step costs this many points. Banner logo columns typically grow 1.4 to 2.7 times.
_LOGO_GROWTH_LIMIT = 2.5
_LOGO_GROWTH_WEIGHT = 3.0


def _logo_growth(ctx: Ctx, logo: Unit, head: Unit, boxes: dict[str, Box], els) -> float:
    """How much more the logo grew than the headline type, relative to the source design.

    The judge rewards the designer's proportions; a logo column several times the headline's
    scale reads as the message, and the critic had no other check against it."""
    src_el = ctx.scene.elements[head.member_ids[0]]
    src_logo = union_all([_visual_box(ctx, ctx.scene.elements[m])[0] for m in logo.member_ids])
    src = src_logo.h / max(1.0, float(src_el["font_size"]))
    out = boxes[logo.id].h / max(1.0, float(els[head.member_ids[0]]["font_size"]))
    return out / src if src > 0 else 0.0


def _space(ctx: Ctx, units: list[Unit], boxes: dict[str, Box], W: float, H: float, add) -> None:
    """Penalise empty canvases, crowded edges and lopsided content."""
    if not units:
        return
    content = union_all([boxes[u.id] for u in units])
    fill = content.area / (W * H)
    add("empty", max(0.0, 0.45 - fill) * 30)
    edge = ctx.margin * 0.5
    for u in units:
        b = boxes[u.id]
        if b.x < edge or b.y < edge or b.x1 > W - edge or b.y1 > H - edge:
            add("edge_crowding", 2.0)
    off_x = abs(content.cx - W / 2) / W
    off_y = abs(content.cy - H / 2) / H
    add("off_center", max(0.0, off_x - 0.08) * 20 + max(0.0, off_y - 0.08) * 20)


def _source_lines(ctx: Ctx, unit: Unit) -> int:
    """How many lines the unit's text wrapped to in the source design."""
    el = ctx.scene.elements[unit.member_ids[0]]
    bold, italic = el.get("font_weight") == "bold", el.get("font_style") == "italic"
    size = max(1, int(float(el["font_size"])))
    return len(ctx.measurer.layout(el.get("content", ""), el["font_family"], bold, italic, size,
                                   max(1.0, float(el["width"]))).lines)


# =============================================================================
# Clutter and composition
# =============================================================================


def _clutter(ctx: Ctx, units: list[Unit], els: dict[str, dict[str, Any]], boxes: dict[str, Box], add) -> None:
    """Copy sitting on busy backdrop detail, and decorations crowding the product.

    Judge critiques of otherwise equal layouts kept naming a baked-in dot touching a word or a
    bubble behind the product; mirrored splits tied on every other check."""
    pad = 0.01 * ctx.short_side
    busy = [
        max((_backdrop_busyness(ctx, els[b], boxes[u.id].inset(-pad)) for b in ctx.scene.background_ids),
            default=0.0)
        for u in units if u.kind != "image" or u.role == "logo"
    ]
    if busy:
        # Capped so backdrop detail only settles close calls; dodging it outright lost to balance.
        add("busy_backdrop", min(2.0, 24.0 * sum(busy) / len(busy)))
    # A shape that framed the product in the source frames it again; only the others clutter it.
    halos = halo_owners(ctx)
    for u in units:
        if u.kind == "image" and u.role != "logo":
            covered = sum(_shape_overlap(els[d], boxes[u.id]) for d in ctx.scene.decoration_ids
                          if halos.get(d) != u.id)
            add("hero_clutter", covered / max(1.0, boxes[u.id].area))


def _backdrop_busyness(ctx: Ctx, el: dict[str, Any], box: Box) -> float:
    """Share of `box` over fine detail in backdrop image `el`, mapped through its fit."""
    if el["type"] != "image":
        return 0.0
    asset = el.get("asset_id", "")
    bx, by, bw, bh = (float(el[k]) for k in ("x", "y", "width", "height"))
    dw, dh = bw, bh
    aspect, fit = ctx.probe.aspect(asset), el.get("fit", "contain")
    if aspect and fit in ("cover", "contain"):
        # Mirror the renderer: cover center-crops, contain letterboxes, anything else stretches.
        dw = max(bw, bh * aspect) if fit == "cover" else min(bw, bh * aspect)
        dh = dw / aspect
    if dw <= 0 or dh <= 0:
        return 0.0
    ox, oy = bx + (bw - dw) / 2, by + (bh - dh) / 2
    region = Box((box.x - ox) / dw, (box.y - oy) / dh, box.w / dw, box.h / dh)
    return ctx.probe.busyness(asset, region) or 0.0


def _shape_overlap(el: dict[str, Any], box: Box) -> float:
    """Area of `box` under decoration `el`; an ellipse fills about pi/4 of its bounds."""
    share = math.pi / 4 if el.get("shape_type") == "ellipse" else 1.0
    return share * element_box(el).intersection_area(box)


def _source_side(ctx: Ctx, units: list[Unit], boxes: dict[str, Box], W: float, add) -> None:
    """Product moved to the other side of the copy from where the designer put it."""
    hero = next((u for u in units if u.role == "hero"), None) or next((u for u in units if u.role == "image"), None)
    copy = [u for u in units if u.kind in _COPY_KINDS]
    if hero is None or not copy:
        return
    src = (hero.box.cx - union_all([u.box for u in copy]).cx) / ctx.scene.canvas_w
    out = (boxes[hero.id].cx - union_all([boxes[u.id] for u in copy]).cx) / W
    # Judge feedback: a mirrored split reads as unmotivated when the source had a clear side.
    if abs(src) > 0.1 and abs(out) > 0.1 and src * out < 0:
        add("flipped_sides", 1.0)
