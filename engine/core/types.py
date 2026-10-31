from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# =============================================================================
# Module Overview
# =============================================================================
# Value types shared by every stage of the engine core: `Box` geometry, the
# `Unit` an element or rigid group becomes after understanding, the `Scene`
# handed from understanding to layout, the `Container` shapes that hold copy,
# and the `Candidate` layouts compare.

Role = Literal[
    "background",
    "decoration",
    "missing",
    "logo",
    "hero",
    "image",
    "headline",
    "subhead",
    "body",
    "legal",
    "cta",
    "badge",
    "sticker",
]

# Lower number means more important; layout drops from the bottom of this list first.
ROLE_PRIORITY: dict[str, int] = {
    "headline": 0,
    "logo": 1,
    "hero": 2,
    "cta": 3,
    "subhead": 4,
    "image": 5,
    "body": 6,
    "badge": 7,
    "sticker": 8,
    "legal": 9,
    "decoration": 10,
    "missing": 11,
    "background": 12,
}

# Roles laid out as content; the rest are backdrop and never count as collisions.
CONTENT_ROLES = frozenset(
    {"logo", "hero", "image", "headline", "subhead", "body", "legal", "cta", "badge", "sticker"}
)


# =============================================================================
# Geometry
# =============================================================================


@dataclass(frozen=True)
class Box:
    """Axis-aligned rectangle in canvas pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @property
    def aspect(self) -> float:
        """Width over height, guarded against zero-height boxes."""
        return self.w / self.h if self.h > 0 else 1.0

    def intersection_area(self, other: Box) -> float:
        """Area shared with `other`, zero when they do not touch."""
        ix = min(self.x1, other.x1) - max(self.x, other.x)
        iy = min(self.y1, other.y1) - max(self.y, other.y)
        return ix * iy if ix > 0 and iy > 0 else 0.0

    def union(self, other: Box) -> Box:
        """Smallest box containing both boxes."""
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        return Box(x0, y0, max(self.x1, other.x1) - x0, max(self.y1, other.y1) - y0)

    def inset(self, dx: float, dy: float | None = None) -> Box:
        """Shrink by `dx` horizontally and `dy` vertically on every side."""
        dy = dx if dy is None else dy
        return Box(self.x + dx, self.y + dy, max(0.0, self.w - 2 * dx), max(0.0, self.h - 2 * dy))


def union_all(boxes: list[Box]) -> Box:
    """Bounding box of a non-empty list of boxes."""
    if not boxes:
        raise ValueError("boxes must be non-empty")
    out = boxes[0]
    for b in boxes[1:]:
        out = out.union(b)
    return out


def element_box(el: dict[str, Any]) -> Box:
    """Source box of a schema element."""
    return Box(float(el["x"]), float(el["y"]), float(el["width"]), float(el["height"]))


# =============================================================================
# Understanding output
# =============================================================================


@dataclass(frozen=True)
class Unit:
    """An element, or a rigid group of elements, that layout moves as one piece."""

    id: str
    role: Role
    kind: Literal["text", "image", "chip", "row", "shape", "lines"]
    # For `text` units: the text first, then any satellite marks that travel with it; `lines` units
    # list their texts' members in that shape, line by line.
    member_ids: tuple[str, ...]
    box: Box
    priority: int
    # For `row` units, the chip units it carries, left to right; for a matched image set, its pictures;
    # for `lines`, its text units top to bottom.
    children: tuple[Unit, ...] = ()


@dataclass(frozen=True)
class Container:
    """A panel or band shape that holds copy: layout fits it behind the units it held."""

    id: str
    # Ids of the units the shape enclosed in the source.
    member_ids: tuple[str, ...]
    box: Box
    # Canvas edges the shape touched or bled past in the source: left, top, right, bottom.
    edges: tuple[bool, bool, bool, bool]


@dataclass(frozen=True)
class Scene:
    """Everything layout needs to know about the source design."""

    source: dict[str, Any]
    canvas_w: float
    canvas_h: float
    elements: dict[str, dict[str, Any]]
    units: tuple[Unit, ...]
    background_ids: tuple[str, ...]
    decoration_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    centered: bool
    containers: tuple[Container, ...] = ()
    # Marks that sit between two texts, as (mark, upper text, lower text); the upper text carries them.
    separators: tuple[tuple[str, str, str], ...] = ()

    def units_by_role(self, *roles: str) -> list[Unit]:
        """Units whose role is one of `roles`, in source reading order."""
        return [u for u in self.units if u.role in roles]


# =============================================================================
# Layout output
# =============================================================================


@dataclass
class Candidate:
    """One proposed layout: per-element patches plus the ids it chose to hide."""

    strategy: str
    patches: dict[str, dict[str, Any]] = field(default_factory=dict)
    hidden: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
