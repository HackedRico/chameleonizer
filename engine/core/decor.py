from __future__ import annotations

import math
from dataclasses import dataclass

from engine.core.sizing import Ctx, drawn_glyph_bounds, visible_fraction
from engine.core.types import Box, Candidate, Unit, element_box

# =============================================================================
# Module Overview
# =============================================================================
# Decoration placement as a small optimisation. Each ornament (a loose shape,
# a faint image, an empty panel) picks a position and a scale from a candidate
# set: its source-relative spot, spots along every canvas edge, and, for a
# shape that sat behind the product, a halo spot that follows the product.
# A weighted cost scores each candidate the way the judge reads a layout:
# ornaments clear of copy, buttons and the logo first; then clear of the
# product unless they framed it; no new pile-ups between ornaments; whole
# unless the source let them bleed off that edge; and the source's
# arrangement around the content, mirrored when the layout mirrored.

# Scales an ornament may take to reach a clean spot, as shares of its carried size.
_SCALES = (1.0, 0.85, 0.72, 0.6)
# Positions along each edge, as shares of the free run.
_ALONG = (0.0, 0.25, 0.5, 0.75, 1.0)
# Shares of the source offset between a halo and its product that a halo may keep.
_HALO_OFFSETS = (1.0, 0.6, 0.3, 0.0)

# Cost weights. Copy, buttons and the logo dominate: v1.8 won 17-2 by clearing them alone.
W_COPY_HIT = 3.0       # per copy piece the ornament touches
W_COPY_COVER = 6.0     # times the share of that piece it covers
W_COPY_NEAR = 1.5      # per copy piece within the clearance but not touched
W_PROD_HIT = 1.5       # per product the ornament touches, unless it framed that product
W_PROD_COVER = 4.0     # times the share of the product it covers
W_PROD_NEAR = 0.5      # per product within half the clearance but not touched
W_STACK = 5.0          # times the overlap with another ornament beyond the source's
W_STACK_HIT = 1.5      # two ornaments that were apart in the source now touch
W_STACK_NEAR = 0.5     # two ornaments that were apart in the source now nearly touch
W_WEDGED = 1.0         # squeezed into a gap with content close on two opposite sides
W_EDGE_NEW = 1.0       # per canvas edge touched that the source kept clear of
W_EDGE_LOST = 0.4      # per edge the source bled off that the ornament no longer reaches
W_MOVE = 2.0           # times the distance from the source-relative spot, over the diagonal
W_SIDE = 1.5           # turning away from the source direction relative to the content
W_SHRINK = 2.5         # times the share of size given up
W_INWARD = 4.0         # times the depth gained from the nearest edge, over the short side
W_HALO = 2.0           # times a framing halo's offset from its source offset, over its size
W_HALO_LOST = 3.0      # a shape that framed the product no longer does
W_FRAME_NEAR = 0.5     # share of the crowding cost a framing halo pays
W_HIDDEN = 6.0         # an ornament its framed product covers by more than half, up to all of it

# A shape behind the product at this share of the smaller area or more frames it.
_HALO_OVERLAP = 0.4
# A halo is at least this large against the product's visible pixels; smaller shapes are dots.
_HALO_SIZE = 0.3


@dataclass(frozen=True)
class _Piece:
    box: Box
    area: float


@dataclass(frozen=True)
class _Source:
    """What the source said about one ornament."""

    box: Box
    ellipse: bool
    edges: tuple[bool, bool, bool, bool]  # left, top, right, bottom touched or bled
    depth: float                          # distance to the nearest edge, over the short side
    rel: tuple[float, float]              # direction from the content centre, over the canvas


def place(ctx: Ctx, cand: Candidate, ids: list[str] | tuple[str, ...]) -> None:
    """Patch every ornament in `ids` into its cheapest spot, halos first, then largest first."""
    if not ids:
        return
    copy, products = _obstacles(ctx, cand)
    halos = halo_owners(ctx)
    content = _content_centre(ctx, cand)
    flip = _flips(ctx, cand)
    scene = ctx.scene

    def order(eid: str) -> tuple[int, float]:
        return (0 if eid in halos else 1, -element_box(scene.elements[eid]).area)

    placed: list[tuple[str, Box]] = [
        (d, _patch_box(cand, d)) for d in scene.decoration_ids if d not in ids and d in cand.patches
    ]
    for eid in sorted(ids, key=order):
        box = _choose(ctx, cand, eid, copy, products, halos, content, flip, placed)
        cand.patches[eid] = {"x": box.x, "y": box.y, "width": box.w, "height": box.h}
        placed.append((eid, box))


def halo_owners(ctx: Ctx) -> dict[str, str]:
    """Ornaments that sat behind a product in the source, mapped to that product's unit id.

    Judge feedback: a circle that framed the can and was carried elsewhere left the can without
    its backdrop, while a circle that followed the product kept the composition."""
    out: dict[str, str] = {}
    products = [u for u in ctx.scene.units if u.kind == "image" and u.role != "logo"]
    if not products:
        return out
    for d in ctx.scene.decoration_ids:
        el = ctx.scene.elements[d]
        if el["type"] != "shape":
            continue
        b = element_box(el)
        ellipse = el.get("shape_type") == "ellipse"
        best, best_share = None, 0.0
        for u in products:
            z = min(int(ctx.scene.elements[m].get("z_index", 0)) for m in u.member_ids)
            if int(el.get("z_index", 0)) >= z:
                continue
            p = _source_visible(ctx, u)
            area = _area(b, ellipse)
            if p.area <= 0 or area < _HALO_SIZE * p.area:
                continue
            share = _cover(b, ellipse, p) / min(area, p.area)
            inside = _inside(b, ellipse, p.cx, p.cy)
            if (share >= _HALO_OVERLAP or inside) and share > best_share:
                best, best_share = u.id, share
        if best is not None:
            out[d] = best
    return out


# =============================================================================
# Candidates and cost
# =============================================================================


def _choose(ctx: Ctx, cand: Candidate, eid: str, copy: list[_Piece], products: dict[str, _Piece],
            halos: dict[str, str], content: tuple[float, float] | None, flip: tuple[bool, bool],
            placed: list[tuple[str, Box]]) -> Box:
    """The cheapest box for ornament `eid` among its candidate spots and scales."""
    scene = ctx.scene
    src = _describe(ctx, eid, _content_centre_src(ctx))
    W, H = ctx.W, ctx.H
    k = _carry_scale(ctx, src)
    bw, bh = src.box.w * k, src.box.h * k
    fit = min(1.0, W / bw if bw else 1.0, H / bh if bh else 1.0)
    bw, bh = max(1.0, bw * fit), max(1.0, bh * fit)

    # Source-relative spot, mirrored on any axis the layout mirrored.
    cx = src.box.cx / scene.canvas_w * W
    cy = src.box.cy / scene.canvas_h * H
    if flip[0]:
        cx = W - cx
    if flip[1]:
        cy = H - cy
    edges = _flip_edges(src.edges, flip)
    rel = (-src.rel[0] if flip[0] else src.rel[0], -src.rel[1] if flip[1] else src.rel[1])

    owner = halos.get(eid)
    halo = _halo_target(ctx, owner, src, products[owner]) if owner in products else None

    hidden_ok, frame_reach = 0.5, 0.0
    if halo is not None:
        # As much of the halo may hide behind the product as did in the source, and a little more.
        unit = next(u for u in scene.units if u.id == owner)
        seen = _source_visible(ctx, unit)
        was = _cover(src.box, src.ellipse, seen) / max(_area(src.box, src.ellipse), 1.0)
        hidden_ok = max(0.5, was + 0.15)
        # It frames the product while the product sits about as centred on it as in the source.
        frame_reach = max(0.45, _reach(src.box, seen.cx, seen.cy) + 0.2)
    pc = (products[owner].box.cx, products[owner].box.cy) if halo is not None else (cx, cy)
    inset = ctx.margin / 2
    spots: list[tuple[Box, float]] = []
    for s in _SCALES:
        w, h = bw * s, bh * s
        pts = {(_clamp(cx - w / 2, 0, W - w), _clamp(cy - h / 2, 0, H - h))}
        xs = {_clamp(cx - w / 2, 0, W - w)} | {t * max(0.0, W - w) for t in _ALONG}
        ys = {_clamp(cy - h / 2, 0, H - h)} | {t * max(0.0, H - h) for t in _ALONG}
        rows = {0.0, _clamp(inset, 0, H - h), max(0.0, H - h), max(0.0, H - h - inset)}
        cols = {0.0, _clamp(inset, 0, W - w), max(0.0, W - w), max(0.0, W - w - inset)}
        pts |= {(x, y) for x in xs for y in rows} | {(x, y) for y in ys for x in cols}
        spots += [(Box(x, y, w, h), s) for x, y in pts]
        if halo is not None:
            hfit = min(1.0, W / (halo.w * s), H / (halo.h * s))
            hw, hh = halo.w * s * hfit, halo.h * s * hfit
            # The source offset from the product, or less of it, so a halo can stay clear of the copy.
            for t in _HALO_OFFSETS:
                hx = pc[0] + (halo.cx - pc[0]) * t
                hy = pc[1] + (halo.cy - pc[1]) * t
                hb = Box(_clamp(hx - hw / 2, 0, W - hw), _clamp(hy - hh / 2, 0, H - hh), hw, hh)
                spots.append((hb, s * hfit))

    diag = math.hypot(W, H)
    short = min(W, H)

    # Each obstacle with its clearance ring, worked out once for every spot.
    rings = [(p, p.box.inset(-ctx.gap), W_COPY_HIT, W_COPY_COVER, W_COPY_NEAR) for p in copy]
    rings += [(p, p.box.inset(-ctx.gap / 2), W_PROD_HIT, W_PROD_COVER, W_PROD_NEAR)
              for uid, p in products.items() if uid != owner]

    solids = [p.box for p in copy] + [p.box for uid, p in products.items() if uid != owner]

    def cost(box: Box, s: float) -> float:
        e = src.ellipse
        framing = halo is not None and _reach(box, pc[0], pc[1]) <= frame_reach
        # A halo sits where its product sits, so crowding what the product crowds counts for less.
        crowd = W_FRAME_NEAR if framing else 1.0
        c = 0.0
        bx0, by0, bx1, by1 = box.x, box.y, box.x + box.w, box.y + box.h
        for p, g, hit, cover, near in rings:
            if bx0 < g.x + g.w and bx1 > g.x and by0 < g.y + g.h and by1 > g.y:
                c += _hit_cost(box, e, p, g, hit, cover, near * crowd)
        c += sum(_stack_cost(ctx, eid, box, other_id, other) for other_id, other in placed)
        if halo is not None and hidden_ok < 1.0:
            # A halo hidden almost whole behind its product reads as a stray sliver (judge feedback).
            hidden = _cover(box, e, products[owner].box) / max(_area(box, e), 1.0)
            c += W_HIDDEN * max(0.0, hidden - hidden_ok) / (1.0 - hidden_ok)
        touch = (box.x <= 0.5, box.y <= 0.5, box.x1 >= W - 0.5, box.y1 >= H - 0.5)
        for was, now in zip(edges, touch):
            if now and not was:
                c += W_EDGE_NEW
            elif was and not now and not framing:
                c += W_EDGE_LOST
        c += W_SHRINK * max(0.0, 1.0 - s)
        if not framing and _wedged(box, solids, max(2 * ctx.gap, 0.5 * min(box.w, box.h))):
            c += W_WEDGED
        if framing:
            # Framing its product again: the source offset matters, the source spot does not.
            c += W_HALO * math.hypot(box.cx - halo.cx, box.cy - halo.cy) / max(halo.w, 1.0)
        else:
            if halo is not None:
                c += W_HALO_LOST
            c += W_MOVE * math.hypot(box.cx - cx, box.cy - cy) / diag
            if content is not None:
                c += _side_cost(rel, ((box.cx - content[0]) / W, (box.cy - content[1]) / H))
            depth = min(box.x, box.y, W - box.x1, H - box.y1) / short
            c += W_INWARD * max(0.0, depth - src.depth - 0.02)
        return c

    return min(spots, key=lambda sp: (round(cost(*sp), 6), -sp[1], sp[0].y, sp[0].x))[0]


def _hit_cost(box: Box, ellipse: bool, piece: _Piece, grown: Box, hit: float, cover: float, near: float) -> float:
    """Cost of `box` against one obstacle: touching it, covering a share of it, or crowding its ring."""
    if _touches(box, ellipse, piece.box):
        ov = _cover(box, ellipse, piece.box)
        if ov > 0.5:
            return hit + cover * min(1.0, ov / max(piece.area, 1.0))
    return near if _touches(box, ellipse, grown) else 0.0


def _stack_cost(ctx: Ctx, eid: str, box: Box, other_id: str, other: Box) -> float:
    a, b = element_box(ctx.scene.elements[eid]), element_box(ctx.scene.elements[other_id])
    now = _pair_share(box, other)
    if now <= 0:
        # Two ornaments that were apart and now nearly kiss read as one lumpy shape.
        if _pair_share(box.inset(-ctx.gap / 2), other) > 0 and _pair_share(a.inset(-ctx.gap / 2), b) <= 0:
            return W_STACK_NEAR
        return 0.0
    was = _pair_share(a, b)
    excess = max(0.0, now - was)
    c = W_STACK * excess
    if was <= 0 and now > 0.01:
        c += W_STACK_HIT
    return c


def _wedged(box: Box, solids: list[Box], reach: float) -> bool:
    """True when content sits within `reach` on two opposite sides of `box`.

    Judge feedback on the rejected free grid search: circles dropped into the gaps between the
    logo, the product and the copy read as clutter, even clear of every piece."""
    left = right = above = below = False
    for b in solids:
        if b.y < box.y + box.h and b.y + b.h > box.y:
            left = left or 0 <= box.x - (b.x + b.w) <= reach
            right = right or 0 <= b.x - (box.x + box.w) <= reach
        if b.x < box.x + box.w and b.x + b.w > box.x:
            above = above or 0 <= box.y - (b.y + b.h) <= reach
            below = below or 0 <= b.y - (box.y + box.h) <= reach
    return (left and right) or (above and below)


def _side_cost(src: tuple[float, float], now: tuple[float, float]) -> float:
    n0 = math.hypot(*src)
    n1 = math.hypot(*now)
    if n0 < 0.05 or n1 < 1e-6:
        return 0.0
    cos = (src[0] * now[0] + src[1] * now[1]) / (n0 * n1)
    return W_SIDE * (1 - cos) / 2 * min(1.0, n0 / 0.2)


def _halo_target(ctx: Ctx, owner: str, src: _Source, drawn: _Piece) -> Box | None:
    """Where a halo goes around its product: the source offset and size, scaled with the product."""
    unit = next(u for u in ctx.scene.units if u.id == owner)
    p = _source_visible(ctx, unit)
    if p.area <= 0 or drawn.box.area <= 0:
        return None
    k = math.sqrt(drawn.box.area / p.area)
    w, h = src.box.w * k, src.box.h * k
    if w < 1 or h < 1:
        return None
    cx = drawn.box.cx + (src.box.cx - p.cx) * k
    cy = drawn.box.cy + (src.box.cy - p.cy) * k
    return Box(cx - w / 2, cy - h / 2, w, h)


# =============================================================================
# Scene facts
# =============================================================================


def _describe(ctx: Ctx, eid: str, content_src: tuple[float, float] | None) -> _Source:
    el = ctx.scene.elements[eid]
    b = element_box(el)
    cw, ch = ctx.scene.canvas_w, ctx.scene.canvas_h
    tol = 0.005 * min(cw, ch)
    edges = (b.x <= tol, b.y <= tol, b.x1 >= cw - tol, b.y1 >= ch - tol)
    depth = max(0.0, min(b.x, b.y, cw - b.x1, ch - b.y1)) / min(cw, ch)
    rel = (0.0, 0.0)
    if content_src is not None:
        rel = ((b.cx - content_src[0]) / cw, (b.cy - content_src[1]) / ch)
    return _Source(b, el.get("shape_type") == "ellipse", edges, depth, rel)


def _carry_scale(ctx: Ctx, src: _Source) -> float:
    """Scale an ornament carries to the new canvas.

    The short-side ratio alone kept circles at full size on a smaller canvas of the same width,
    where they dominated (judge feedback); the area ratio caps that. An ornament the source showed
    only partly, bled off an edge, carries a little less, since the whole of it now shows."""
    cw, ch = ctx.scene.canvas_w, ctx.scene.canvas_h
    k = min(ctx.short_side / min(cw, ch), math.sqrt(ctx.W * ctx.H / (cw * ch)))
    total = _area(src.box, src.ellipse)
    if total > 0:
        shown = _cover(src.box, src.ellipse, Box(0, 0, cw, ch)) / total
        k *= max(0.75, min(1.0, shown ** 0.35))
    return k


def _content_centre_src(ctx: Ctx) -> tuple[float, float] | None:
    boxes = [u.box for u in ctx.scene.units]
    if not boxes:
        return None
    return _mass_centre(boxes)


def _content_centre(ctx: Ctx, cand: Candidate) -> tuple[float, float] | None:
    boxes = [b for u in ctx.scene.units if (b := _unit_box(ctx, cand, u)) is not None]
    if not boxes:
        return None
    return _mass_centre(boxes)


def _mass_centre(boxes: list[Box]) -> tuple[float, float]:
    total = sum(max(b.area, 1.0) for b in boxes)
    return (sum(b.cx * max(b.area, 1.0) for b in boxes) / total,
            sum(b.cy * max(b.area, 1.0) for b in boxes) / total)


def _flips(ctx: Ctx, cand: Candidate) -> tuple[bool, bool]:
    """Axes on which the layout put the product on the other side of the copy from the source."""
    units = ctx.scene.units
    hero = next((u for u in units if u.role == "hero"), None) or next(
        (u for u in units if u.kind == "image" and u.role != "logo"), None)
    copy = [u for u in units if u.kind != "image"]
    if hero is None or not copy:
        return False, False
    hb = _unit_box(ctx, cand, hero)
    cb = [b for u in copy if (b := _unit_box(ctx, cand, u)) is not None]
    if hb is None or not cb:
        return False, False
    sc = _union(u.box for u in copy)
    oc = _union(cb)
    out = []
    for src, now, s_ext, o_ext in ((hero.box.cx - sc.cx, hb.cx - oc.cx, ctx.scene.canvas_w, ctx.W),
                                   (hero.box.cy - sc.cy, hb.cy - oc.cy, ctx.scene.canvas_h, ctx.H)):
        a, b = src / s_ext, now / o_ext
        out.append(abs(a) > 0.1 and abs(b) > 0.1 and a * b < 0)
    return out[0], out[1]


def _flip_edges(edges: tuple[bool, bool, bool, bool], flip: tuple[bool, bool]) -> tuple[bool, bool, bool, bool]:
    left, top, right, bottom = edges
    if flip[0]:
        left, right = right, left
    if flip[1]:
        top, bottom = bottom, top
    return left, top, right, bottom


def _obstacles(ctx: Ctx, cand: Candidate) -> tuple[list[_Piece], dict[str, _Piece]]:
    """Drawn copy pieces (glyphs, buttons, the logo) and product boxes in the candidate."""
    copy: list[_Piece] = []
    products: dict[str, _Piece] = {}
    for unit in ctx.scene.units:
        if unit.kind == "image" and unit.role != "logo":
            b = _unit_box(ctx, cand, unit)
            if b is not None:
                products[unit.id] = _Piece(b, b.area)
            continue
        for m in unit.member_ids:
            patch = cand.patches.get(m)
            if not patch or m in cand.hidden:
                continue
            el = {**ctx.scene.elements[m], **patch}
            b = Box(float(patch["x"]), float(patch["y"]), float(patch["width"]), float(patch["height"]))
            if el["type"] == "text":
                g = drawn_glyph_bounds(ctx.measurer, el)
                if g is None:
                    continue
                b = g
            elif el["type"] == "image":
                f = visible_fraction(ctx, el)
                b = Box(b.x + f.x * b.w, b.y + f.y * b.h, f.w * b.w, f.h * b.h)
            copy.append(_Piece(b, b.area))
    return copy, products


def _unit_box(ctx: Ctx, cand: Candidate, unit: Unit) -> Box | None:
    parts = []
    for m in unit.member_ids:
        patch = cand.patches.get(m)
        if not patch or m in cand.hidden:
            continue
        b = Box(float(patch["x"]), float(patch["y"]), float(patch["width"]), float(patch["height"]))
        el = ctx.scene.elements[m]
        if el["type"] == "image":
            f = visible_fraction(ctx, el)
            b = Box(b.x + f.x * b.w, b.y + f.y * b.h, f.w * b.w, f.h * b.h)
        parts.append(b)
    return _union(parts) if parts else None


def _source_visible(ctx: Ctx, unit: Unit) -> Box:
    parts = []
    for m in unit.member_ids:
        el = ctx.scene.elements[m]
        b = element_box(el)
        if el["type"] == "image":
            f = visible_fraction(ctx, el)
            b = Box(b.x + f.x * b.w, b.y + f.y * b.h, f.w * b.w, f.h * b.h)
        parts.append(b)
    return _union(parts)


def _patch_box(cand: Candidate, eid: str) -> Box:
    p = cand.patches[eid]
    return Box(float(p["x"]), float(p["y"]), float(p["width"]), float(p["height"]))


# =============================================================================
# Geometry
# =============================================================================


def _union(boxes) -> Box:
    boxes = list(boxes)
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x1 for b in boxes), max(b.y1 for b in boxes)
    return Box(x0, y0, x1 - x0, y1 - y0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(lo, v), max(lo, hi))


def _area(b: Box, ellipse: bool) -> float:
    return b.area * (math.pi / 4 if ellipse else 1.0)


def _inside(b: Box, ellipse: bool, x: float, y: float) -> bool:
    if not ellipse:
        return b.x <= x <= b.x1 and b.y <= y <= b.y1
    a, c = b.w / 2, b.h / 2
    if a <= 0 or c <= 0:
        return False
    return ((x - b.cx) / a) ** 2 + ((y - b.cy) / c) ** 2 <= 1


def _reach(b: Box, x: float, y: float) -> float:
    """How far point (x, y) sits from the centre of the ellipse in `b`, 1 on its rim."""
    a, c = max(b.w / 2, 1e-6), max(b.h / 2, 1e-6)
    return math.hypot((x - b.cx) / a, (y - b.cy) / c)


def _touches(b: Box, ellipse: bool, r: Box) -> bool:
    """True when the shape drawn in `b` overlaps rectangle `r`.

    Scaling the axes turns the ellipse into a circle and keeps `r` a rectangle, so the nearest
    point of `r` to the centre decides it exactly."""
    if not (b.x < r.x + r.w and r.x < b.x + b.w and b.y < r.y + r.h and r.y < b.y + b.h):
        return False
    if not ellipse:
        return True
    cx, cy = b.x + b.w / 2, b.y + b.h / 2
    dx = max(r.x - cx, 0.0, cx - r.x - r.w) / max(b.w / 2, 1e-6)
    dy = max(r.y - cy, 0.0, cy - r.y - r.h) / max(b.h / 2, 1e-6)
    return dx * dx + dy * dy < 1.0


def _cover(b: Box, ellipse: bool, r: Box, slices: int = 12) -> float:
    """Area of rectangle `r` under the shape drawn in `b`, an ellipse or the box itself."""
    if b.intersection_area(r) <= 0:
        return 0.0
    if not ellipse:
        return b.intersection_area(r)
    a, c = b.w / 2, b.h / 2
    x0, x1 = max(b.x, r.x), min(b.x1, r.x1)
    dx = (x1 - x0) / slices
    total = 0.0
    for i in range(slices):
        u = (x0 + (i + 0.5) * dx - b.cx) / a
        if abs(u) >= 1:
            continue
        half = c * math.sqrt(1 - u * u)
        span = min(b.cy + half, r.y1) - max(b.cy - half, r.y)
        if span > 0:
            total += span * dx
    return total


def _pair_share(a: Box, b: Box) -> float:
    """Overlap of two ornaments, treated as circles, over the smaller one's area."""
    ra, rb = (a.w + a.h) / 4, (b.w + b.h) / 4
    if ra <= 0 or rb <= 0:
        return 0.0
    d = math.hypot(a.cx - b.cx, a.cy - b.cy)
    if d >= ra + rb:
        return 0.0
    small = min(ra, rb)
    if d <= abs(ra - rb):
        return 1.0
    r1, r2 = ra, rb
    p1 = r1 * r1 * math.acos(_clamp((d * d + r1 * r1 - r2 * r2) / (2 * d * r1), -1, 1))
    p2 = r2 * r2 * math.acos(_clamp((d * d + r2 * r2 - r1 * r1) / (2 * d * r2), -1, 1))
    p3 = 0.5 * math.sqrt(max(0.0, (-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2)))
    return (p1 + p2 - p3) / (math.pi * small * small)
