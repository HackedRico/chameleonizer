from __future__ import annotations

import copy
import json
import logging
import math
import time
from dataclasses import dataclass, replace
from typing import Any

from engine.core.critic import Report, critique
from engine.core.finalize import apply_candidate, fallback_layout
from engine.core.layout import UNSPACED, aspect_bucket, propose
from engine.core.packed import packed
from engine.core.ports import AssetProbe, TextMeasurer
from engine.core.sizing import Ctx
from engine.core.types import Scene
from engine.core.understand import understand

logger = logging.getLogger(__name__)

# =============================================================================
# Module Overview
# =============================================================================
# The engine core's entry point. `resize_design` runs understand, propose,
# finalize and critique, and returns the best-scoring valid design, rebuilt
# with its outer margins matched. `resize_with_report` also returns the
# scores, for the test bench, and on request a few more finished designs, the
# finalists an optional model harness may choose between (`engine/core/agent.py`).
# The engine's own pick is built first and exactly as without them.


# Seconds after which no further candidate is tried once one has scored. Scoring runs tasks one
# after another, so a pathological design must not hold up every task behind it.
_TIME_BUDGET_S = 20.0
_GEOMETRY = ("x", "y", "width", "height")
# What the official converter draws a text element with when the source leaves these out
# (`renderer/converter/convert.py`).
_RENDERER_FONT = "Montserrat"
_RENDERER_SIZE = 24.0
# Critic penalties for content that cannot be read at all. A candidate that holds text at its
# spaced floor may not carry more of them than its twin without the floor: word spacing is never
# worth copy over copy or lines past the canvas.
_UNREADABLE = ("collision", "cut_off", "text_overflow", "hidden_content")
# Collision penalty from which the packed fallback replaces the chosen layout: one piece over more than
# a quarter of a piece of copy, or several smaller overlaps. A ribbon grazing fine print at 728x90 or
# 300x250 is not worth giving up the archetype's hierarchy for small, evenly packed type.
_CLASH = 10.0


def _sanitized(source: dict[str, Any]) -> dict[str, Any]:
    """`source` with geometry the layout code can trust: finite numbers, no negative sizes.

    The schema forbids negative sizes, but a width of -50 once sent the free-space search
    through billions of spots; malformed input should degrade, not hang."""
    out = copy.deepcopy(source)
    for el in out.get("elements", []):
        for key in _GEOMETRY:
            try:
                value = float(el.get(key, 0.0))
            except (TypeError, ValueError):
                value = 0.0
            if not math.isfinite(value):
                value = 0.0
            el[key] = max(0.0, value) if key in ("width", "height") else value
        if el.get("type") == "text":
            # The schema leaves both optional, and a missing one raised and fell back to the letterbox;
            # the renderer's own defaults keep what the engine measures equal to what is drawn.
            el.setdefault("font_family", _RENDERER_FONT)
            el.setdefault("font_size", _RENDERER_SIZE)
            # The schema's minimum is 1; a font size of 0 divided by zero and fell back to the letterbox.
            try:
                size = float(el["font_size"])
            except (TypeError, ValueError):
                size = 1.0
            el["font_size"] = size if math.isfinite(size) and size >= 1.0 else 1.0
    return out


def _without_added_fonts(design: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """`design` minus any `font_family` the source did not set, so the renderer resolves those
    elements' type exactly as it would have: the font family is the designer's, never the engine's."""
    absent = {el.get("id") for el in source.get("elements", []) if el.get("type") == "text" and "font_family" not in el}
    for el in design["elements"]:
        if el.get("id") in absent:
            el.pop("font_family", None)
    return design


@dataclass(frozen=True)
class Finalist:
    """A finished, shippable design the harness may choose instead of the engine's pick."""

    design: dict[str, Any]  # after `_balanced`, the `_CLASH`/`_packed` check and `_without_added_fonts`
    strategy: str  # what ships: the candidate's strategy, or the packed one that replaced it
    picked_from: str  # candidate strategy it was finished from
    report: Report  # critic report of the finished design
    raw: Report  # critic report of the candidate as it competed in the loop


@dataclass(frozen=True)
class Outcome:
    """The chosen design plus why it won, for debugging and the scoreboard."""

    design: dict[str, Any]
    strategy: str
    report: Report
    considered: tuple[tuple[str, float], ...]
    # Filled only when alternatives are asked for; `finalists[0].design is design`.
    finalists: tuple[Finalist, ...] = ()
    # Seconds spent finishing the alternatives, which the harness takes out of its own budget.
    alt_seconds: float = 0.0


# Public names for the harness: the penalties that make content unreadable, and their sum.
UNREADABLE = _UNREADABLE


def unreadable(report: Report) -> float:
    """Sum of the critic penalties for content that cannot be read at all."""
    return _unreadable(report)


def resize_with_report(
    source: dict[str, Any],
    target: dict[str, Any],
    measurer: TextMeasurer,
    probe: AssetProbe,
    policy: str = "none",
    alternatives: int = 0,
    alt_budget_s: float | None = None,
) -> Outcome:
    """Best-scoring design for `target`, with the score of every candidate considered.

    With `alternatives` above 0, also finishes up to that many runners-up the same way the pick is
    finished, distinct from each other and from the pick, within `alt_budget_s` seconds."""
    if int(target.get("width", 0)) < 1 or int(target.get("height", 0)) < 1:
        raise ValueError("target_canvas needs positive `width` and `height`")
    given, source = source, _sanitized(source)
    # A strip's action column is one button tall, so a row of buttons would stack inside one unit
    # with no stack gap between them: at 320x50 the pair ran from the top edge to the bottom edge.
    strip = aspect_bucket(float(target["width"]), float(target["height"])) == "strip"
    scene = understand(source, probe, button_rows=not strip, hide_missing=policy == "occlude")
    ctx = Ctx(scene, float(target["width"]), float(target["height"]), measurer, probe)
    scored: list[tuple[dict[str, Any], str, Report]] = []
    start = time.monotonic()
    # Candidates compete as built before margins were matched: matched margins changed which
    # archetype the critic preferred, toward banner columns the judge rates below the split.
    for cand in propose(replace(ctx, balance=False), policy):
        if scored and time.monotonic() - start > _TIME_BUDGET_S:
            break
        design = apply_candidate(scene, target, cand)
        contain_text(ctx, design)
        scored.append((design, cand.strategy, critique(ctx, design, cand.hidden)))
    considered = [(strategy, report.score) for _design, strategy, report in scored]
    ranked = _ranked(scored)
    if not ranked:
        design = fallback_layout(source, target)
        report = critique(ctx, design, set())
        return Outcome(_without_added_fonts(design, given), "fallback/no-candidate", report, tuple(considered))
    packs = _PackedOnce(ctx, scene, target)
    design, strategy, report, repacked = _finish(ctx, scene, target, policy, ranked[0], given, packs)
    if repacked:
        considered.append((strategy, report.score))
    if alternatives <= 0:
        return Outcome(design, strategy, report, tuple(considered))
    t0 = time.monotonic()
    finalists = [Finalist(design, strategy, ranked[0][1], report, ranked[0][2])]
    seen = {_canonical(design)}
    for entry in ranked[1:]:
        if len(finalists) > alternatives or (alt_budget_s is not None and time.monotonic() - t0 > alt_budget_s):
            break
        try:
            alt, alt_strategy, alt_report, _ = _finish(ctx, scene, target, policy, entry, given, packs)
        except Exception:  # an alternative is optional; the engine's pick is already built
            logger.warning("[pipeline] Alternative %s failed; skipped.", entry[1], exc_info=True)
            continue
        key = _canonical(alt)
        if key not in seen:
            seen.add(key)
            finalists.append(Finalist(alt, alt_strategy, entry[1], alt_report, entry[2]))
    return Outcome(design, strategy, report, tuple(considered), tuple(finalists), round(time.monotonic() - t0, 3))


_Entry = tuple[dict[str, Any], str, Report]


def _ranked(scored: list[_Entry]) -> list[_Entry]:
    """Candidates that survive the twin filter, best first.

    `ranked[0]` is exactly the pick of the original strict `>` loop, the earliest of equal top
    scores; the rest follow in a stable sort by score."""
    unreadable_by = {strategy: _unreadable(report) for _design, strategy, report in scored}
    kept: list[_Entry] = []
    best: _Entry | None = None
    for entry in scored:
        _design, strategy, report = entry
        twin = unreadable_by.get(strategy + UNSPACED)
        if twin is not None and unreadable_by[strategy] > twin + 0.5:
            continue
        kept.append(entry)
        if best is None or report.score > best[2].score:
            best = entry
    if best is None:
        return []
    rest = sorted((e for e in kept if e is not best), key=lambda e: -e[2].score)
    return [best, *rest]


def _finish(
    ctx: Ctx,
    scene: Scene,
    target: dict[str, Any],
    policy: str,
    entry: _Entry,
    given: dict[str, Any],
    packs: _PackedOnce,
) -> tuple[dict[str, Any], str, Report, bool]:
    """`entry` as it would ship: rebuilt with matched margins, replaced by the packed layout when it
    still overlaps copy, and stripped of fonts the source left out. Returns the design, the strategy
    that ships, its report, and whether the packed layout replaced it."""
    design, strategy, report = _balanced(ctx, scene, target, policy, entry)
    repacked = False
    if report.penalties.get("collision", 0.0) >= _CLASH:
        clean = packs.get()
        if clean is not None:
            design, strategy, report = clean
            repacked = True
    return _without_added_fonts(design, given), strategy, report, repacked


class _PackedOnce:
    """`_packed(ctx, scene, target)`, computed at most once per call, with one warning on failure.

    Each caller gets its own deep copy, so finalists never alias each other's dicts."""

    def __init__(self, ctx: Ctx, scene: Scene, target: dict[str, Any]) -> None:
        self._args = (ctx, scene, target)
        self._done = False
        self._best: _Entry | None = None

    def get(self) -> _Entry | None:
        if not self._done:
            self._best = _packed(*self._args)
            self._done = True
        if self._best is None:
            return None
        design, strategy, report = self._best
        return copy.deepcopy(design), strategy, report


def _canonical(design: dict[str, Any]) -> str:
    """A design as one comparable string, so equal finalists count once."""
    return json.dumps(design, sort_keys=True)


def _packed(ctx: Ctx, scene: Scene, target: dict[str, Any]) -> tuple[dict[str, Any], str, Report] | None:
    """The best-scoring packed layout that the critic finds wholly readable, or `None`.

    Runs only when the chosen layout still overlaps content. The judge preferred small copy with
    nothing overlapping to copy piled on copy, and the critic, which charges every pixel of small
    type, would never pick that trade itself, so any clean packed layout replaces an overlapping one."""
    best: tuple[dict[str, Any], str, Report] | None = None
    try:
        cands = packed(ctx)
    except Exception:  # the fallback is optional; the overlapping layout is still valid output
        logger.warning("[pipeline] Packed fallback failed; keeping the chosen layout.", exc_info=True)
        return None
    for cand in cands:
        design = apply_candidate(scene, target, cand)
        contain_text(ctx, design)
        report = critique(ctx, design, cand.hidden)
        if _unreadable(report) > 0:
            continue
        if best is None or report.score > best[2].score:
            best = (design, cand.strategy, report)
    return best


def _balanced(
    ctx: Ctx, scene: Scene, target: dict[str, Any], policy: str, best: tuple[dict[str, Any], str, Report]
) -> tuple[dict[str, Any], str, Report]:
    """The chosen candidate rebuilt with matched outer margins, unless that costs readable content."""
    for cand in propose(ctx, policy, only=best[1]):
        design = apply_candidate(scene, target, cand)
        contain_text(ctx, design)
        report = critique(ctx, design, cand.hidden)
        if _unreadable(report) <= _unreadable(best[2]) + 0.5:
            return design, best[1], report
    return best


def contain_text(ctx: Ctx, design: dict[str, Any]) -> None:
    """Shrink, in place, any text whose wrapped lines would draw past the canvas.

    The renderer draws every wrapped line, top-aligned, whatever the box height, and the final
    clamp only shortens the box: a headline wrapped to eight lines on a 320x50 strip ran 46 px off
    the bottom (rules audit). Wrapping here is the renderer's own, at the box width. Text that
    belongs to a button, badge or line group only shrinks, because moving it would pull it off its
    shape. Loose text shrinks to 60% before it is lifted; lifting first moved a label under a sticker."""
    unit_kind = {m: u.kind for u in ctx.scene.units for m in u.member_ids}
    for el in design["elements"]:
        if el.get("type") != "text" or not el.get("content"):
            continue
        bold, italic = el.get("font_weight") == "bold", el.get("font_style") == "italic"
        lh = float(el.get("line_height", 1.2))
        size = start = int(el["font_size"])
        width = max(1.0, float(el["width"]))
        rigid = unit_kind.get(el["id"], "text") != "text"
        floor = 1 if rigid else max(1, int(0.6 * start))
        y = float(el["y"])
        while True:
            lay = ctx.measurer.layout(el["content"], el["font_family"], bold, italic, size, width)
            drawn = len(lay.lines) * size * lh
            fits = lay.longest_word <= width + 0.5 and y + drawn <= ctx.H + 0.5
            if fits or size <= 1:
                break
            if size <= floor and lay.longest_word <= width + 0.5 and drawn <= ctx.H + 0.5:
                break  # loose text at its floor: lift it below instead of shrinking further
            size -= 1
        if size != start:
            el["font_size"] = size
            room = ctx.H - y if rigid else ctx.H  # rigid text keeps its y, so cap the box there
            el["height"] = round(drawn, 2) if drawn <= room else math.floor(room * 100) / 100
        if y + drawn > ctx.H + 0.5 and not rigid:
            el["y"] = round(max(0.0, ctx.H - drawn), 2)


def _unreadable(report: Report) -> float:
    return sum(report.penalties.get(key, 0.0) for key in _UNREADABLE)


def resize_design(
    source: dict[str, Any], target: dict[str, Any], measurer: TextMeasurer, probe: AssetProbe, policy: str = "none"
) -> dict[str, Any]:
    """Best-scoring design for `target`."""
    return resize_with_report(source, target, measurer, probe, policy).design
