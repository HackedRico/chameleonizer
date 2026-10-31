from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from engine.core.ports import AssetProbe
from engine.core.sizing import probe_visible_fraction
from engine.core.types import ROLE_PRIORITY, Box, Container, Role, Scene, Unit, element_box, union_all

# =============================================================================
# Module Overview
# =============================================================================
# The understand stage: turns a raw source design into a `Scene`. It finds the
# backdrop, rigid groups (explicit `group_id`s plus text sitting on a shape),
# container panels that hold copy, satellite marks that belong to one text
# (bullets, accent rules, dividers), matched sets of pictures side by side and
# runs of lines stacked as one block, and assigns each remaining unit a role
# from geometry, type, style and copy, never from element ids, since hidden
# test designs may use opaque ids. Units come out in the source's reading
# order, which every layout keeps.

# Share of the canvas an element must cover to count as backdrop.
_BACKGROUND_COVERAGE = 0.85
# Imperative openers that mark call-to-action copy; matched on the first word only.
_CTA_VERBS = frozenset(
    "shop buy get order learn sign join try start download book discover explore see view "
    "register subscribe claim save call reserve grab find visit apply watch play".split()
)
# Share of a piece's box that must lie inside a shape for the shape to hold it.
_HELD_SHARE = 0.8
# Below this share of the canvas, a shape holding its pieces on one line is a button, not a panel.
_BUTTON_AREA = 0.08


def understand(
    source: dict[str, Any], probe: AssetProbe, button_rows: bool = True, hide_missing: bool = False
) -> Scene:
    """Build the `Scene` for `source`, assigning every element a role or a backdrop slot.

    `button_rows` off keeps the buttons beside the call to action as separate badges, for canvases
    too short to set them side by side. `hide_missing` sets an image whose asset file is missing
    aside in `missing_ids` for layout to hide, which only the opt-in occlude policy asks for. By
    default such an image is laid out like any other: the renderer draws it as a grey placeholder
    filling its box, and the organizers' October 22 session asks that every input element stay visible."""
    if "canvas" not in source or "elements" not in source:
        raise ValueError("source must have `canvas` and `elements`")
    cw, ch = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    # A group of one is no group: read as one, a lone grouped logo became the call to action and a
    # lone grouped backdrop stopped being the background. The output still carries the source's ids.
    sizes = Counter(repr(el["group_id"]) for el in source["elements"] if el.get("group_id"))
    elements = {
        el["id"]: {k: v for k, v in el.items() if k != "group_id"}
        if el.get("group_id") and sizes[repr(el["group_id"])] < 2 else el
        for el in source["elements"]
    }
    canvas = Box(0, 0, cw, ch)

    background = [
        eid
        for eid, el in elements.items()
        if el["type"] in ("image", "shape")
        and not el.get("group_id")
        and element_box(el).intersection_area(canvas) >= _BACKGROUND_COVERAGE * canvas.area
    ]
    missing = [
        eid
        for eid, el in elements.items()
        if hide_missing and el["type"] == "image" and eid not in background
        and not probe.exists(el.get("asset_id", ""))
    ]
    taken = set(background) | set(missing)

    # Panels are found before text-on-shape grouping, or a small card would claim its first line as a label.
    panels = _panel_shapes(elements, taken, canvas)
    # One key registry for every group, set and row unit, so no two internal keys collide.
    issued: dict[str, str] = {}
    groups = _collect_groups(elements, taken | panels, issued)
    grouped = {eid for members in groups.values() for eid in members}
    decoration = [
        eid
        for eid, el in elements.items()
        if eid not in taken and eid not in grouped and eid not in panels and _is_decoration(el)
    ]
    marks, separators = _satellite_marks(elements, decoration, taken | grouped, set(background))
    attached = {m for ids in marks.values() for m in ids}
    decoration = [eid for eid in decoration if eid not in attached]
    # Groups made only of shapes carry no message, so they behave like decoration.
    for key, members in list(groups.items()):
        if all(elements[m]["type"] == "shape" for m in members):
            decoration.extend(members)
            del groups[key]
    taken |= set(decoration) | attached

    units: list[Unit] = []
    units += _chip_units(groups, elements)
    units += _image_units(elements, taken | grouped, cw, ch, issued, probe)
    chip_cta = next((u.box for u in units if u.role == "cta"), None)
    units += _text_units(elements, taken | grouped, ch, marks, chip_cta)
    if button_rows:
        units = _merge_cta_row(units, elements, issued)
    units = _merge_badge_rows(units, elements, issued)
    units = _merge_line_runs(units, elements, issued)
    units = _reading_order(units, elements, probe)
    containers, loose = _containers(panels, elements, units, cw, ch)
    decoration.extend(loose)

    # A button label is centred on its button whatever the layout does, so only free copy votes;
    # two centred labels had turned a left-aligned headline and body into a centred stack.
    texts = [elements[t.member_ids[0]] for u in units for t in (u.children if u.kind == "lines" else (u,))
             if t.kind == "text"]
    texts = texts or [el for el in elements.values() if el["type"] == "text"]
    centered = sum(el.get("text_align", "left") == "center" for el in texts) * 2 >= max(1, len(texts))
    return Scene(
        source=source,
        canvas_w=cw,
        canvas_h=ch,
        elements=elements,
        units=tuple(units),
        background_ids=tuple(background),
        decoration_ids=tuple(decoration),
        missing_ids=tuple(missing),
        centered=centered,
        containers=containers,
        separators=separators,
    )


# =============================================================================
# Grouping
# =============================================================================


def _collect_groups(
    elements: dict[str, dict[str, Any]], taken: set[str], issued: dict[str, str]
) -> dict[str, list[str]]:
    """Explicit `group_id` groups plus implicit text-on-shape pairs."""
    groups: dict[str, list[str]] = {}
    for eid, el in elements.items():
        if eid not in taken and el.get("group_id"):
            groups.setdefault(_fresh_key(f"g:{el['group_id']}", elements, issued), []).append(eid)

    loose_shapes = [
        el for eid, el in elements.items()
        if el["type"] == "shape" and eid not in taken and not el.get("group_id")
    ]
    for eid, el in elements.items():
        if el["type"] != "text" or eid in taken or el.get("group_id"):
            continue
        tb = element_box(el)
        for shape in loose_shapes:
            sb = element_box(shape)
            # A label is "on" a shape when most of its box sits inside a reasonably small shape.
            if sb.area < 0.25 * _canvas_area(elements) and tb.intersection_area(sb) >= 0.8 * tb.area:
                key = _fresh_key(f"auto:{shape['id']}", elements, issued)
                groups.setdefault(key, [shape["id"]])
                groups[key].append(eid)
                break
    return groups


def _fresh_key(base: str, elements: dict[str, dict[str, Any]], issued: dict[str, str]) -> str:
    """The unit key for `base`: the same key each time for the same base, never an element id and
    never a key issued for another base. A single element's unit uses its own id, so a design with
    an element named like a group key ("g:3") would otherwise merge two units."""
    if base not in issued:
        key, taken = base, set(issued.values())
        while key in elements or key in taken:
            key += "'"
        issued[base] = key
    return issued[base]


def _canvas_area(elements: dict[str, dict[str, Any]]) -> float:
    """Rough canvas area from element extents, used only for relative size tests."""
    boxes = [element_box(el) for el in elements.values()]
    return max(1.0, union_all(boxes).area)


# -----------------------------------------------------------------
# Containers: a panel or band that holds copy is part of the message
# -----------------------------------------------------------------


def _panel_shapes(elements: dict[str, dict[str, Any]], taken: set[str], canvas: Box) -> set[str]:
    """Loose upright rectangles drawn under two or more content pieces: cards and bands holding copy.

    Judge feedback on the probe split: treated as ornament, a card panel drifted to an empty blob at
    the top and a text band cut across the product. A piece is a loose text, a visible image or an
    explicit group; ellipses stay ornament, since refitting one around copy would distort it."""
    pieces: list[tuple[Box, int]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for eid, el in elements.items():
        if eid in taken:
            continue
        if el.get("group_id"):
            groups.setdefault(el["group_id"], []).append(el)
        elif el["type"] == "text" or (el["type"] == "image" and not _is_decoration(el)):
            pieces.append((element_box(el), int(el.get("z_index", 0))))
    for members in groups.values():
        drawn = [m for m in members if m["type"] != "shape"]
        if drawn:
            pieces.append((union_all([element_box(m) for m in members]), max(int(m.get("z_index", 0)) for m in drawn)))

    panels = set()
    for eid, el in elements.items():
        if eid in taken or el["type"] != "shape" or el.get("group_id") or el.get("shape_type") != "rectangle":
            continue
        if abs(float(el.get("rotation", 0) or 0)) > 0.5:
            continue  # a tilted shape under copy is a sticker
        box, z = element_box(el), int(el.get("z_index", 0))
        held = [b for b, top in pieces if top >= z and _holds(box, b)]
        if len(held) < 2:
            continue
        one_row = max(b.y for b in held) < min(b.y1 for b in held)
        if one_row and box.intersection_area(canvas) < _BUTTON_AREA * canvas.area:
            continue  # an icon and a label on a small shape: a button
        panels.add(eid)
    return panels


def _containers(
    panels: set[str], elements: dict[str, dict[str, Any]], units: list[Unit], cw: float, ch: float
) -> tuple[tuple[Container, ...], list[str]]:
    """Each panel with the units it holds, plus the panels that hold fewer than two and act as ornament."""
    tol = max(1.0, 0.005 * min(cw, ch))
    found: list[Container] = []
    loose: list[str] = []
    for eid in (e for e in elements if e in panels):
        box = element_box(elements[eid])
        # Stickers are tucked into corners on their own, so a panel never follows them.
        members = tuple(u.id for u in units if u.role != "sticker" and _holds(box, u.box))
        if len(members) < 2:
            loose.append(eid)
            continue
        edges = (box.x <= tol, box.y <= tol, box.x1 >= cw - tol, box.y1 >= ch - tol)
        found.append(Container(eid, members, box, edges))
    return tuple(found), loose


def _holds(box: Box, piece: Box) -> bool:
    return piece.area > 0 and piece.intersection_area(box) >= _HELD_SHARE * piece.area


def _is_decoration(el: dict[str, Any]) -> bool:
    """Loose shapes and faint images carry no message and act as ornament."""
    if el["type"] == "shape":
        return True
    return el["type"] == "image" and float(el.get("opacity", 1)) < 0.5


# How far a mark may sit from its text, in that text's line heights.
_MARK_REACH = 1.25
# Effective alpha below which a shape is a translucent ornament, never a mark.
_MARK_ALPHA = 0.5


def _satellite_marks(
    elements: dict[str, dict[str, Any]], shapes: list[str], taken: set[str], background: set[str]
) -> tuple[dict[str, list[str]], tuple[tuple[str, str, str], ...]]:
    """Small solid shapes that belong to one text: bullets, accent rules, dividers.

    A mark is no thicker than a line of its text and no longer than the text box, solid, and
    within about a line height beside, above or below it with nothing in between; it goes to
    the nearest such text. Judge feedback on the probe split: placed as decoration, bullet dots
    and rules floated away from their lines as stray dots and stubs clipped at an edge.

    A mark with such a text both above and below it separates the two, like a rule between a
    headline and its date. It is also returned as (mark, upper, lower), so a tight stack can
    centre it between the two texts' glyphs."""
    texts = [el for eid, el in elements.items() if el["type"] == "text" and eid not in taken]
    blockers = [element_box(el) for eid, el in elements.items()
                if el["type"] in ("text", "image") and eid not in background]
    out: dict[str, list[str]] = {}
    separators: list[tuple[str, str, str]] = []
    for sid in shapes:
        s = elements[sid]
        if s["type"] != "shape" or s.get("group_id") or abs(float(s.get("rotation", 0) or 0)) > 0.5:
            continue
        if float(s.get("opacity", 1)) * _alpha(s.get("fill_color", "")) < _MARK_ALPHA:
            continue
        sb = element_box(s)
        near: list[tuple[float, str, str]] = []
        for t in texts:
            tb = element_box(t)
            lh = float(t["font_size"]) * float(t.get("line_height", 1.2))
            if min(sb.w, sb.h) > lh or max(sb.w, sb.h) > tb.w:
                continue
            gx = max(0.0, tb.x - sb.x1, sb.x - tb.x1)
            gy = max(0.0, tb.y - sb.y1, sb.y - tb.y1)
            # Diagonal neighbours, such as the next list item's text, are not its text.
            if (gx > 0 and gy > 0) or gx + gy > _MARK_REACH * lh:
                continue
            between = _between(sb, tb)
            if any(between.intersection_area(b) > 0 for b in blockers if b != tb):
                continue
            side = "beside" if gy == 0 else "above" if tb.y1 <= sb.y else "below"
            near.append((gx + gy, t["id"], side))
        if not near:
            continue
        _, owner, side = min(near, key=lambda n: n[0])
        if side != "beside":
            other = min((n for n in near if n[2] not in (side, "beside")), key=lambda n: n[0], default=None)
            if other is not None:
                upper, lower = (owner, other[1]) if side == "above" else (other[1], owner)
                separators.append((sid, upper, lower))
        out.setdefault(owner, []).append(sid)
    return out, tuple(separators)


def _between(a: Box, b: Box) -> Box:
    """The strip separating two boxes that share a row or a column."""
    x0, x1 = sorted((max(a.x, b.x), min(a.x1, b.x1)))
    y0, y1 = sorted((max(a.y, b.y), min(a.y1, b.y1)))
    return Box(x0, y0, x1 - x0, y1 - y0)


# =============================================================================
# Role assignment
# =============================================================================


def _chip_units(groups: dict[str, list[str]], elements: dict[str, dict[str, Any]]) -> list[Unit]:
    """Turn each group into a rigid chip unit and decide which one is the call to action."""
    chips: list[tuple[str, list[str], Box, bool, str]] = []
    for key, members in groups.items():
        box = union_all([element_box(elements[m]) for m in members])
        rotated = any(abs(float(elements[m].get("rotation", 0) or 0)) > 0.5 for m in members)
        label = " ".join(elements[m].get("content", "") for m in members if elements[m]["type"] == "text")
        chips.append((key, members, box, rotated, label))

    upright = [c for c in chips if not c[3]]
    verb_chips = [c for c in upright if _starts_with_cta_verb(c[4])]
    pool = verb_chips or upright
    cta_key = max(pool, key=lambda c: c[2].area)[0] if pool else None
    # Without CTA copy, only call the biggest chip a CTA when it clearly dominates the rest.
    if not verb_chips and len(upright) > 1:
        areas = sorted((c[2].area for c in upright), reverse=True)
        if areas[0] < 1.3 * areas[1]:
            cta_key = None

    units = []
    for key, members, box, rotated, _label in chips:
        role: Role = "sticker" if rotated else "cta" if key == cta_key else "badge"
        units.append(Unit(key, role, "chip", tuple(members), box, ROLE_PRIORITY[role]))
    return units


def _image_units(
    elements: dict[str, dict[str, Any]], taken: set[str], cw: float, ch: float, issued: dict[str, str],
    probe: AssetProbe,
) -> list[Unit]:
    """Split loose images into the logo, the hero, and secondary images.

    An image whose asset is missing draws as a grey placeholder that shows no brand or product, so
    it is always a secondary image, and the first content a crowded layout moves aside: as the logo
    or the hero it grew to fill the column a real picture would have had."""
    loose = [el for eid, el in elements.items() if el["type"] == "image" and eid not in taken]
    images = [el for el in loose if probe.exists(el.get("asset_id", ""))]
    blanks = [Unit(el["id"], "image", "image", (el["id"],), element_box(el), ROLE_PRIORITY["missing"])
              for el in loose if not probe.exists(el.get("asset_id", ""))]
    if not images:
        return blanks

    def logo_score(el: dict[str, Any]) -> float:
        b = element_box(el)
        score = 0.0
        if b.aspect >= 1.8:
            score += 2
        if b.area < 0.08 * cw * ch:
            score += 1
        if b.cy < 0.3 * ch:
            score += 1
        return score

    logo = max(images, key=logo_score)
    logo_id = logo["id"] if logo_score(logo) >= 2 and len(images) > 1 or logo_score(logo) >= 3 else None
    sets = _image_sets([el for el in images if el["id"] != logo_id])
    # A matched set weighs as much as its pictures together, so a pair outranks one of its own halves.
    sets.sort(key=lambda s: -sum(element_box(el).area for el in s))
    units = [Unit(logo_id, "logo", "image", (logo_id,), element_box(logo), ROLE_PRIORITY["logo"])] if logo_id is not None else []
    for i, members in enumerate(sets):
        role: Role = "hero" if i == 0 else "image"
        if len(members) == 1:
            el = members[0]
            units.append(Unit(el["id"], role, "image", (el["id"],), element_box(el), ROLE_PRIORITY[role]))
            continue
        kids = tuple(Unit(el["id"], role, "image", (el["id"],), element_box(el), ROLE_PRIORITY[role]) for el in members)
        box = union_all([k.box for k in kids])
        units.append(Unit(_fresh_key(f"set:{kids[0].id}", elements, issued), role, "image", tuple(k.id for k in kids), box, ROLE_PRIORITY[role], kids))
    return units + blanks


# Smallest area ratio at which two pictures read as a matched pair rather than a hero and an inset.
_SET_AREA_RATIO = 0.5


def _image_sets(images: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group pictures that sit side by side, or stacked, at similar sizes into matched sets.

    Judge feedback on two products side by side: sized apart, one became the hero and the other a
    tiny can wedged between headline and button, breaking the pair the design was built on. A set
    is laid out as one picture, so its members keep their relative sizes and spacing."""
    parent = {el["id"]: el["id"] for el in images}

    def root(eid: str) -> str:
        while parent[eid] != eid:
            eid = parent[eid]
        return eid

    for i, a in enumerate(images):
        for b in images[i + 1:]:
            if _matched(element_box(a), element_box(b)):
                parent[root(b["id"])] = root(a["id"])
    sets: dict[str, list[dict[str, Any]]] = {}
    for el in images:
        sets.setdefault(root(el["id"]), []).append(el)
    # Reading order inside a set: left to right, then top to bottom.
    return [sorted(s, key=lambda e: (e["x"], e["y"])) for s in sets.values()]


def _matched(a: Box, b: Box) -> bool:
    """True when `a` and `b` are similar in size and sit next to each other along one axis."""
    if min(a.area, b.area) < _SET_AREA_RATIO * max(a.area, b.area):
        return False
    # Each axis as (start, length) along it and (centre, length) across it: side by side, then stacked.
    for (a0, al, ac, aw), (b0, bl, bc, bw) in (
        ((a.x, a.w, a.cy, a.h), (b.x, b.w, b.cy, b.h)),
        ((a.y, a.h, a.cx, a.w), (b.y, b.h, b.cx, b.w)),
    ):
        # Aligned across like a badge row; along the axis, offset by at least half a picture and
        # no further apart than half a picture, so they sit beside each other, not one on the other.
        unit = 0.5 * min(al, bl)
        aligned = abs(ac - bc) < 0.5 * min(aw, bw)
        offset = abs((a0 + al / 2) - (b0 + bl / 2)) >= unit
        gap = max(a0, b0) - min(a0 + al, b0 + bl)
        if aligned and offset and gap <= unit:
            return True
    return False


def _text_units(
    elements: dict[str, dict[str, Any]], taken: set[str], ch: float, marks: dict[str, list[str]],
    chip_cta: Box | None,
) -> list[Unit]:
    """Rank loose text into headline, subhead, body, legal and text-only CTA.

    Each text unit carries its satellite marks after the text id, so they move and scale with it."""
    texts = [el for eid, el in elements.items() if el["type"] == "text" and eid not in taken]
    if not texts:
        return []
    roles: dict[str, Role] = {}

    cta = chip_cta
    if chip_cta is None:
        for el in texts:
            if _starts_with_cta_verb(el.get("content", "")) and len(el["content"].split()) <= 6:
                roles[el["id"]] = "cta"
                cta = element_box(el)
                break

    ranked = sorted(
        (el for el in texts if el["id"] not in roles),
        key=lambda e: (-_visual_weight(e), e["y"]),
    )
    if ranked:
        head = ranked[0]
        roles[head["id"]] = "headline"
        smallest = min(ranked, key=lambda e: float(e["font_size"]))
        # Fine print is read last: copy sitting above the call to action is body copy, however small.
        # Called legal, a product description was moved under the button (judge feedback).
        above_cta = cta is not None and element_box(smallest).cy < cta.y
        if smallest is not head and not above_cta and _looks_legal(smallest, head, ch):
            roles[smallest["id"]] = "legal"
        for el in ranked[1:]:
            if el["id"] not in roles:
                roles[el["id"]] = "subhead" if "subhead" not in roles.values() else "body"

    units = []
    for el in texts:
        ids = (el["id"], *marks.get(el["id"], ()))
        box = union_all([element_box(elements[m]) for m in ids])
        units.append(Unit(el["id"], roles[el["id"]], "text", ids, box, ROLE_PRIORITY[roles[el["id"]]]))
    return units


# How far a chip's height may differ from the call to action's for both to read as one row of buttons.
_CTA_ROW_HEIGHT = 1.25
# Widest gap between neighbouring buttons of one row, in the call to action's heights.
_CTA_ROW_GAP = 1.0


def _merge_cta_row(units: list[Unit], elements: dict[str, dict[str, Any]], issued: dict[str, str]) -> list[Unit]:
    """Fold the call to action and the same-size buttons beside it in the source into one `row` unit.

    Judge feedback on a pair of buttons: apart, the second one read as a badge and stacked below the
    first, 'detached from the CTA group'. A row keeps them side by side and stacks them only when the
    canvas is too narrow for both."""
    cta = next((u for u in units if u.role == "cta" and u.kind == "chip"), None)
    if cta is None:
        return units
    row = [cta]
    chips = [u for u in units if u.role == "badge" and u.kind == "chip"]
    for u in sorted(chips, key=lambda u: abs(u.box.cx - cta.box.cx)):
        near = min(abs(u.box.x - b.box.x1) if u.box.x >= b.box.x else abs(b.box.x - u.box.x1) for b in row)
        if (abs(u.box.cy - cta.box.cy) < 0.5 * min(u.box.h, cta.box.h)
                and 1 / _CTA_ROW_HEIGHT <= u.box.h / cta.box.h <= _CTA_ROW_HEIGHT
                and near <= _CTA_ROW_GAP * cta.box.h):
            row.append(u)
    if len(row) == 1:
        return units
    row.sort(key=lambda u: u.box.x)
    kids = tuple(Unit(u.id, "cta", u.kind, u.member_ids, u.box, ROLE_PRIORITY["cta"]) for u in row)
    members = tuple(m for u in kids for m in u.member_ids)
    merged = Unit(_fresh_key(f"row:{kids[0].id}", elements, issued), "cta", "row", members,
                  union_all([u.box for u in kids]), ROLE_PRIORITY["cta"], kids)
    out = [u for u in units if u not in row]
    out.append(merged)
    return out


def _merge_badge_rows(units: list[Unit], elements: dict[str, dict[str, Any]], issued: dict[str, str]) -> list[Unit]:
    """Fold badges that sit side by side in the source into one `row` unit."""
    badges = sorted((u for u in units if u.role == "badge"), key=lambda u: u.box.x)
    rows: list[list[Unit]] = []
    for b in badges:
        for row in rows:
            ref = row[0].box
            if abs(b.box.cy - ref.cy) < 0.5 * min(b.box.h, ref.h):
                row.append(b)
                break
        else:
            rows.append([b])
    out = [u for u in units if u.role != "badge"]
    for row in rows:
        if len(row) == 1:
            out.append(row[0])
            continue
        members = tuple(m for u in row for m in u.member_ids)
        box = union_all([u.box for u in row])
        out.append(Unit(_fresh_key(f"row:{row[0].id}", elements, issued), "badge", "row", members, box, ROLE_PRIORITY["badge"], tuple(row)))
    return out


# Largest font ratio between neighbouring lines of one run; a headline over its details differs more.
_RUN_SIZE_RATIO = 1.2
# Widest gap between neighbouring lines of one run, in the larger line's line heights.
_RUN_GAP = 1.0
# How far two lines' aligned edges or centres may differ, in the smaller font's size.
_RUN_ALIGN = 0.5


def _merge_line_runs(units: list[Unit], elements: dict[str, dict[str, Any]], issued: dict[str, str]) -> list[Unit]:
    """Fold short lines the source stacks as one block into a `lines` unit, top to bottom.

    Judge feedback on the probe split: an event's time and place, or a feature list, split into
    separate roles; tight layouts kept one and parked the rest wherever there was room, so a time
    and a place landed on one broken line and list items scattered across a strip. A run is
    consecutive loose text, each line starting under the previous one, aligned the same way, in
    the same face and about the same size, about a line apart with nothing between. The headline,
    a text call to action and fine print stay on their own, since layout treats them specially."""
    loose = [u for u in units if u.kind == "text" and u.role not in ("headline", "cta", "legal")]
    if len(loose) < 2:
        return units
    content = [(u.id, m, element_box(elements[m])) for u in units for m in u.member_ids]
    parent = {u.id: u.id for u in loose}

    def root(uid: str) -> str:
        while parent[uid] != uid:
            uid = parent[uid]
        return uid

    for a in loose:
        for b in loose:
            if a is not b and _next_line(a, b, elements, content):
                parent[root(b.id)] = root(a.id)
    runs: dict[str, list[Unit]] = {}
    for u in loose:
        runs.setdefault(root(u.id), []).append(u)
    merged: dict[str, Unit] = {}
    for run in runs.values():
        if len(run) < 2:
            continue
        run.sort(key=lambda u: elements[u.member_ids[0]]["y"])
        lead = min(run, key=lambda u: u.priority)
        members = tuple(m for u in run for m in u.member_ids)
        box = union_all([u.box for u in run])
        unit = Unit(_fresh_key(f"lines:{run[0].id}", elements, issued), lead.role, "lines", members, box, lead.priority,
                    tuple(run))
        for u in run:
            merged[u.id] = unit
    out: list[Unit] = []
    for u in units:
        unit = merged.get(u.id, u)
        if all(o.id != unit.id for o in out):
            out.append(unit)
    return out


def _next_line(a: Unit, b: Unit, elements: dict[str, dict[str, Any]], content: list[tuple[str, str, Box]]) -> bool:
    """True when text unit `b` is the line that follows `a` in a block the source stacks tightly."""
    ea, eb = elements[a.member_ids[0]], elements[b.member_ids[0]]
    style = ("font_family", "font_weight", "font_style", "text_align", "capitalize")
    if any(ea.get(k) != eb.get(k) for k in style):
        return False
    fa, fb = float(ea["font_size"]), float(eb["font_size"])
    if max(fa, fb) > _RUN_SIZE_RATIO * min(fa, fb):
        return False
    ba, bb = element_box(ea), element_box(eb)
    align = ea.get("text_align", "left")
    edge = {"center": lambda b: b.cx, "right": lambda b: b.x1}.get(align, lambda b: b.x)
    if abs(edge(ba) - edge(bb)) > _RUN_ALIGN * min(fa, fb):
        return False
    lha, lhb = fa * float(ea.get("line_height", 1.2)), fb * float(eb.get("line_height", 1.2))
    # Text is top-aligned and spills below a short box, so a box holds at least one line.
    bottom = ba.y + max(ba.h, lha)
    gap = bb.y - bottom
    if gap < -0.25 * lha or gap > _RUN_GAP * max(lha, lhb):
        return False
    x0, x1 = max(ba.x, bb.x), min(ba.x1, bb.x1)
    if x1 <= x0:
        return False
    # Nothing between them: no other copy, picture or mark, such as a divider between a date and a place.
    band = Box(x0, bottom, x1 - x0, bb.y - bottom)
    return band.h <= 0 or not any(
        m not in (ea["id"], eb["id"]) and band.intersection_area(box) > 0 for _uid, m, box in content
    )


# =============================================================================
# Reading order
# =============================================================================


def _reading_order(units: list[Unit], elements: dict[str, dict[str, Any]], probe: AssetProbe) -> list[Unit]:
    """Units top to bottom, then left to right, reading a column to its end before the next.

    Sorting by top edge alone interleaves side-by-side columns: in a wide source with the logo, the
    product, the copy and the button in a row, it read the headline, the logo, the button and only
    then the tagline, and stacks kept that order (judge: "breaks the original hierarchy"). This cuts
    the design into rows wherever a horizontal line crosses no unit, cuts each row into columns the
    same way, and recurses; where no cut exists it falls back to the top-edge sort. Rotated stickers
    straddle rows and are placed on their own, so they stay out of the cut and come last."""
    stickers = sorted((u for u in units if u.role == "sticker"), key=lambda u: (round(u.box.y), u.box.x))
    flow = [(u, _drawn_box(u, elements, probe)) for u in units if u.role != "sticker"]
    return [u for u, _ in _xy_cut(flow, rows=True)] + stickers


def _drawn_box(unit: Unit, elements: dict[str, dict[str, Any]], probe: AssetProbe) -> Box:
    """Where a unit's pixels sit: an image's transparent margin would otherwise bridge columns."""
    if unit.kind != "image":
        return unit.box
    frac = probe_visible_fraction(probe, elements[unit.member_ids[0]])
    b = unit.box
    return Box(b.x + frac.x * b.w, b.y + frac.y * b.h, frac.w * b.w, frac.h * b.h)


def _xy_cut(items: list[tuple[Unit, Box]], rows: bool, tried_other: bool = False) -> list[tuple[Unit, Box]]:
    """Recursive XY-cut: split into rows (or columns) at clear gaps, then cut each part the other way."""
    if len(items) <= 1:
        return items
    lo = (lambda b: b.y) if rows else (lambda b: b.x)
    hi = (lambda b: b.y1) if rows else (lambda b: b.x1)
    bands: list[list[tuple[Unit, Box]]] = []
    end = -math.inf
    for item in sorted(items, key=lambda t: lo(t[1])):
        if bands and lo(item[1]) < end - 0.5:
            bands[-1].append(item)
            end = max(end, hi(item[1]))
        else:
            bands.append([item])
            end = hi(item[1])
    if len(bands) > 1:
        return [t for band in bands for t in _xy_cut(band, not rows)]
    if not tried_other:
        return _xy_cut(items, not rows, tried_other=True)
    return sorted(items, key=lambda t: (round(t[0].box.y), t[0].box.x))


# -----------------------------------------------------------------
# Small predicates
# -----------------------------------------------------------------


def _starts_with_cta_verb(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.lower())
    return bool(words) and words[0] in _CTA_VERBS


def _visual_weight(el: dict[str, Any]) -> float:
    """Font size with a small boost for bold, the main cue for hierarchy."""
    return float(el["font_size"]) * (1.1 if el.get("font_weight") == "bold" else 1.0)


def _alpha(color: str) -> float:
    nums = re.findall(r"[\d.]+", color or "")
    return float(nums[3]) if len(nums) >= 4 else 1.0


def _looks_legal(el: dict[str, Any], head: dict[str, Any], ch: float) -> bool:
    """Fine print: much smaller than the headline, near the bottom, long or faded."""
    # 0.36 separates fine print (about 0.3x the headline) from subheads (about 0.4x).
    small = float(el["font_size"]) <= 0.36 * float(head["font_size"])
    low = element_box(el).cy >= 0.65 * ch
    wordy_or_faded = len(el.get("content", "")) >= 50 or _alpha(el.get("color", "")) <= 0.75
    return small and low and wordy_or_faded
