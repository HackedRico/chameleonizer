from __future__ import annotations

import math
from dataclasses import dataclass, replace

from engine.core.layout import _STRIP_PAD, content_rect, finish_candidate
from engine.core.sizing import (
    Ctx, Patches, Sized, image_frame, is_placeholder, placeholder_height, size_image, size_unit,
)
from engine.core.types import CONTENT_ROLES, Box, Candidate, Unit, union_all

# =============================================================================
# Module Overview
# =============================================================================
# The packed fallback, for canvases where every archetype leaves copy on copy,
# such as eight pieces of copy on a 320x50 strip. `packed` gives each content
# unit a slot of its own in a few fixed arrangements of columns, and sets all
# copy at one shared scale, the largest at which every column fits. Nothing
# overlaps and the designer's type hierarchy survives; copy gets small but is
# never hidden. Judge result on these canvases: tiny but clean beat copy piled
# on copy, 10-0 against v2 on the formats split. The floorplan engine had won the
# small sizes 16-8 against v1.26 the same way.

STRATEGY = "packed"

# Smallest font each candidate sets. A higher floor keeps fine print legible and costs the headline
# size; the critic weighs the two.
_FLOORS = (5, 4, 3, 1)
# Most sizes a text steps down from the shared scale to one whose word spaces render.
_SPACE_DROP = 2
# How much of the lead boost each role takes, as a power of it: the headline and call to action all
# of it, the subhead half, so the copy under the headline keeps its place between the two tiers.
_LEAD_ROLES = {"headline": 1.0, "cta": 1.0, "subhead": 0.5}
# Scales of the lead copy against the rest; the critic picks. Judge: of 10 packed Level 3, story1 and
# Level 2 ads, 9 failed mostly on unreadable copy, every text at 1-5px; a readable headline and button
# over fine detail beat one shared scale. 2.5 is the most a lead tier may outgrow the next one.
_BOOSTS = (1.0, 1.6, 2.5)
# Largest canvas area, in px, that gets the lead tiers and word-space sizing: 336x280 and smaller ads.
_TIERED_MAX_AREA = 120_000
# Bisection steps of the shared scale.
_STEPS = 14
# Widest share of the content width one picture column may take on a banner.
_PICTURE_WIDTH = 0.25
# Shares of the content height pictures fill in their own column; the critic picks.
_PICTURE_FILLS = (0.85, 0.55)
# Least share of the canvas height a stacked logo and product keep, as the other layouts' floors do:
# scaled with copy that has to get tiny, they shrank to specks.
_STACK_LOGO = 0.16
_STACK_HERO = 0.22
# Narrowest share of the content width a column of copy may shrink to.
_MIN_COPY = 0.18
# Gap between stacked units, in ems of the largest copy at the chosen scale, never above the canvas gap.
_STACK_GAP_EM = 0.3


@dataclass(frozen=True)
class _Tier:
    """How the lead copy, the headline and call to action, sizes against the rest: `boost` times the
    shared scale, and with `lift` the full word-space rule, which may take it a few sizes up."""

    boost: float = 1.0
    lift: bool = False


@dataclass(frozen=True)
class _Column:
    """Units stacked top to bottom. A column with a `share` takes that part of the width the
    others leave; a `media` column fits its pictures to `fill` of the content height instead of
    scaling them with the copy."""

    units: tuple[Unit, ...]
    share: float = 0.0
    media: bool = False
    fill: float = 0.0


def packed(ctx: Ctx) -> list[Candidate]:
    """One candidate per arrangement and font floor that fits, every content unit in its own slot."""
    # The word-space rule only steps a size down, by at most `_SPACE_DROP`: searching up as well made the
    # shared scale uneven, a 6px headline over 1px pills, and left no floor above 1px that fit Level 3.
    # Without the rule at all, Level 3 at 300x250 drew 'SUNSETS ALE' and 'Shop the SummerLine'.
    # Tiers and the word-space rule apply only on small ad sizes, where they were judged: measuring word
    # spaces at print sizes took the pack from 0.2s to 69s on an A4 canvas. Larger canvases keep v1.27.
    small = ctx.W * ctx.H <= _TIERED_MAX_AREA
    sizing = replace(ctx, reshape_chips=True, chip_pad=_STRIP_PAD, tuck_marks=True, line_cap=0, balance=False,
                     **({"space_drop": _SPACE_DROP} if small else {"keep_spaces": False}))
    boosts = _BOOSTS if small else _BOOSTS[:1]
    out = []
    for name, columns in _arrangements(ctx):
        columns = [c for c in columns if c.units]
        # Headline lines at each floor without the boost.
        lines: dict[int, int] = {}
        for boost in boosts:
            if boost > 1 and not any(u.role in _LEAD_ROLES for c in columns for u in c.units if u.kind != "image"):
                continue
            for floor in _FLOORS:
                fitted = _search(sizing, columns, floor, _Tier(boost))
                if fitted is None:
                    continue
                patches, slots = _place(sizing, columns, *fitted)
                # A boost may not wrap the headline onto more lines: on story1 at 320x50 it broke the
                # source's one-line headline in two and the button outgrew it.
                count = _headline_lines(ctx, patches)
                if boost == 1:
                    lines[floor] = count
                elif count > lines.get(floor, count):
                    continue
                tag = "" if boost == 1 else f"/lead{round(boost * 10)}"
                cand = Candidate(strategy=f"{STRATEGY}/{name}/f{floor}{tag}", patches=patches)
                finish_candidate(ctx, cand, slots, [], "none")
                out.append(cand)
    return out


def _headline_lines(ctx: Ctx, patches: Patches) -> int:
    """How many lines the headline's text boxes hold in `patches`."""
    total = 0
    for u in ctx.scene.units:
        if u.role != "headline":
            continue
        for m in u.member_ids:
            el, patch = ctx.scene.elements[m], patches.get(m)
            if el["type"] == "text" and patch and patch.get("font_size"):
                step = patch["font_size"] * float(el.get("line_height") or 1.2)
                total += round(patch["height"] / step) if step > 0 else 1
    return total


def _arrangements(ctx: Ctx) -> list[tuple[str, list[_Column]]]:
    """Named column arrangements of the content units, in reading order within each column."""
    units = [u for u in ctx.scene.units if u.role in CONTENT_ROLES]
    # A missing asset's grey placeholder sits after the action at the copy's scale: in a column of
    # its own it was sized to the row height, larger than the product, and squeezed the copy.
    blanks = [u for u in units if is_placeholder(ctx, u)]
    pictures = [u for u in units if u.kind == "image" and u not in blanks]
    stickers = [u for u in units if u.role == "sticker"]
    ctas = [u for u in units if u.role == "cta"] + blanks
    legal = [u for u in units if u.role == "legal"]
    copy = [u for u in units if u not in pictures and u not in stickers and u not in ctas and u not in legal]
    if not units:
        return []
    out: list[tuple[str, list[_Column]]] = []
    if ctx.W >= 2.2 * ctx.H:
        # A row: each picture in its own column, the copy, then the action column. On a strip the
        # height is what runs out, so the copy may also split into two columns after the headline.
        heads = [i for i, u in enumerate(copy) if u.role == "headline"]
        cut = heads[0] + 1 if heads else 1
        action = tuple(stickers + ctas)
        for fill in _PICTURE_FILLS:
            lead = [_Column((p,), media=True, fill=fill) for p in pictures]
            tag = f"{round(fill * 100)}"
            out.append((f"row-{tag}", lead + [_Column(tuple(copy), 1.0), _Column(action + tuple(legal))]))
            if legal:
                out.append((f"row-legal-{tag}", lead + [_Column(tuple(copy + legal), 1.0), _Column(action)]))
            if 0 < cut < len(copy):
                out.append((f"row-two-{tag}", lead + [_Column(tuple(copy[:cut]), 0.55),
                                                      _Column(tuple(copy[cut:]), 0.45), _Column(action + tuple(legal))]))
    else:
        out.append(("stack", [_Column(tuple(stickers + pictures + copy + ctas + legal), 1.0)]))
        # The product beside the copy, on the side the designer put it. The logo heads the copy:
        # a lockup held to a picture column's width shrank to an unreadable wordmark.
        logos = [u for u in pictures if u.role == "logo"]
        products = [u for u in pictures if u.role != "logo"]
        if products and copy:
            right = products[0].box.cx > union_all([u.box for u in copy]).cx
            for fill in _PICTURE_FILLS:
                media = _Column(tuple(stickers + products), media=True, fill=fill)
                text = _Column(tuple(logos + copy + ctas + legal), 1.0)
                out.append((f"split-{round(fill * 100)}", [text, media] if right else [media, text]))
    return out


def _search(
    ctx: Ctx, columns: list[_Column], floor: int, tier: _Tier
) -> tuple[list[list[Sized]], list[float], float] | None:
    """The columns sized at the largest shared scale that fits the content area, or `None`. The lead
    copy then takes sizes whose word spaces render, where that still fits at the same scale."""
    rect = content_rect(ctx)
    top = max(1.0, 2.0 * max(ctx.W / ctx.scene.canvas_w, ctx.H / ctx.scene.canvas_h))
    lo, hi = 0.002, top
    best = _size(ctx, columns, lo, floor, rect, tier)
    if best is None:
        return None
    for _ in range(_STEPS):
        mid = math.sqrt(lo * hi)
        trial = _size(ctx, columns, mid, floor, rect, tier)
        if trial is None:
            hi = mid
        else:
            lo, best = mid, trial
    return _size(ctx, columns, lo, floor, rect, replace(tier, lift=True)) or best


def _size(
    ctx: Ctx, columns: list[_Column], scale: float, floor: int, rect: Box, tier: _Tier
) -> tuple[list[list[Sized]], list[float], float] | None:
    """Every column sized at `scale`, with its width and the stack gap, or `None` when one overflows."""
    gap = _stack_gap(ctx, columns, scale)
    sized: list[list[Sized] | None] = [None] * len(columns)
    widths = [0.0] * len(columns)
    for i, col in enumerate(columns):
        if col.share:
            continue
        parts = (_media(ctx, col, scale, floor, rect, gap, tier) if col.media
                 else _fitted(ctx, col, scale, floor, rect, tier))
        if parts is None:
            return None
        sized[i], widths[i] = parts, max(s.w for s in parts)
    room = rect.w - sum(widths) - ctx.gap * (len(columns) - 1)
    shares = sum(c.share for c in columns)
    for i, col in enumerate(columns):
        if not col.share:
            continue
        avail = room * col.share / shares
        if avail < _MIN_COPY * rect.w:
            return None
        parts = [_unit(ctx, u, scale, floor, avail, rect, tier) for u in col.units]
        sized[i], widths[i] = parts, max(s.w for s in parts)
    for parts in sized:
        if parts is None or any(s.overflow for s in parts) or _height(parts, gap) > rect.h + 0.01:
            return None
    if sum(widths) + ctx.gap * (len(columns) - 1) > rect.w + 0.01:
        return None
    return [p for p in sized if p is not None], widths, gap


def _stack_gap(ctx: Ctx, columns: list[_Column], scale: float) -> float:
    fonts = [float(ctx.scene.elements[m]["font_size"]) for c in columns for u in c.units for m in u.member_ids
             if ctx.scene.elements[m]["type"] == "text"]
    return max(1.0, min(ctx.gap, _STACK_GAP_EM * scale * max(fonts, default=0.0)))


def _height(parts: list[Sized], gap: float) -> float:
    return sum(s.h for s in parts) + gap * max(0, len(parts) - 1)


def _unit(ctx: Ctx, unit: Unit, scale: float, floor: int, avail_w: float, rect: Box, tier: _Tier) -> Sized:
    """`unit` at the shared scale; a stacked picture keeps its source size relative to the copy, down
    to its floor."""
    if unit.kind == "image":
        frame, frac = image_frame(ctx, unit)
        least = {"logo": _STACK_LOGO, "hero": _STACK_HERO}.get(unit.role, 0.0) * ctx.H
        if is_placeholder(ctx, unit):
            least = placeholder_height(unit)
        return size_image(ctx, unit, min(max(frac.h * frame.h * scale, least), rect.h), avail_w)
    if unit.role in _LEAD_ROLES:
        scale *= tier.boost ** _LEAD_ROLES[unit.role]
        if tier.lift and _LEAD_ROLES[unit.role] == 1.0:
            ctx = replace(ctx, space_drop=None)
    return _rotated(ctx, unit, size_unit(ctx, unit, scale, avail_w, floor))


def _fitted(ctx: Ctx, col: _Column, scale: float, floor: int, rect: Box, tier: _Tier) -> list[Sized]:
    """A column as wide as its widest piece that keeps one line, its text wrapped to that width."""
    rigid = [u for u in col.units if u.kind not in ("text", "lines")]
    cap = 0.4 * rect.w
    width = max((_unit(ctx, u, scale, floor, cap, rect, tier).w for u in rigid), default=0.0)
    width = max(width, _MIN_COPY * rect.w)
    return [_unit(ctx, u, scale, floor, width, rect, tier) for u in col.units]


def _media(
    ctx: Ctx, col: _Column, scale: float, floor: int, rect: Box, gap: float, tier: _Tier
) -> list[Sized] | None:
    """Pictures fitted to the column's share of the content height, any sticker in it set at the
    shared scale."""
    flowing = [_unit(ctx, u, scale, floor, 0.4 * rect.w, rect, tier) for u in col.units if u.kind != "image"]
    pics = [u for u in col.units if u.kind == "image"]
    room = rect.h * col.fill - _height(flowing, gap) - gap * len(pics)
    if pics and room <= 0:
        return None
    frames = [image_frame(ctx, u) for u in pics]
    total = sum(frac.h * frame.h for frame, frac in frames)
    k = room / max(total, 1e-6)
    sized = {u.id: size_image(ctx, u, frac.h * frame.h * k, _PICTURE_WIDTH * rect.w)
             for u, (frame, frac) in zip(pics, frames)}
    flow = iter(flowing)
    return [sized[u.id] if u.kind == "image" else next(flow) for u in col.units]


def _rotated(ctx: Ctx, unit: Unit, sized: Sized) -> Sized:
    """A rotated unit's slot grown to the box it draws, its pieces centred in that slot."""
    angle = max((float(ctx.scene.elements[m].get("rotation", 0) or 0) for m in unit.member_ids), key=abs)
    if abs(angle) < 0.5:
        return sized
    a = math.radians(angle)
    rw = sized.w * abs(math.cos(a)) + sized.h * abs(math.sin(a))
    rh = sized.w * abs(math.sin(a)) + sized.h * abs(math.cos(a))

    def emit(x: float, y: float) -> Patches:
        return sized.emit(x + (rw - sized.w) / 2, y + (rh - sized.h) / 2)

    return replace(sized, w=rw, h=rh, emit=emit)


def _place(
    ctx: Ctx, columns: list[_Column], sized: list[list[Sized]], widths: list[float], gap: float
) -> tuple[Patches, dict[str, Box]]:
    """Columns left to right, centred on the canvas, each stack centred in the content height."""
    rect = content_rect(ctx)
    spare = rect.w - sum(widths) - ctx.gap * (len(columns) - 1)
    col_gap = ctx.gap + (min(spare / (len(columns) - 1), 3 * ctx.gap) if len(columns) > 1 else 0.0)
    x = rect.x + (rect.w - sum(widths) - col_gap * (len(columns) - 1)) / 2
    left = not ctx.scene.centered
    patches: Patches = {}
    slots: dict[str, Box] = {}
    for col, parts, width in zip(columns, sized, widths):
        y = rect.y + (rect.h - _height(parts, gap)) / 2
        for s in parts:
            px = x if left and col.share else x + (width - s.w) / 2
            patches.update(s.emit(px, y))
            slots[s.unit.id] = Box(px, y, s.w, s.h)
            y += s.h + gap
        x += width + col_gap
    return patches, slots
