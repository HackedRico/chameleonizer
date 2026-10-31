from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.critic import critique
from engine.core.finalize import apply_candidate
from engine.core.packed import packed
from engine.core.pipeline import _sanitized, contain_text
from engine.core.sizing import Ctx
from engine.core.understand import understand
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# The packed fallback replaces a layout that piles content on copy, so every
# layout it offers must keep content apart and hide nothing. Checked on every
# suite task smaller than 336x280, where the fallback can run, through the
# critic's own collision measure and the structural checks.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, PROBE = default_ports(ROOT)
TASKS = [p for p in sorted((ROOT / "bench" / "suite").glob("*/*.json"))
         if (lambda t: t["width"] * t["height"] < 336 * 280)(json.loads(p.read_text())["target_canvas"])]


@pytest.mark.parametrize("path", TASKS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_packed_layout_keeps_content_apart_and_visible(path):
    task = json.loads(path.read_text())
    source, target = task["source"], task["target_canvas"]
    scene = understand(_sanitized(source), PROBE)
    ctx = Ctx(scene, float(target["width"]), float(target["height"]), MEASURER, PROBE)
    candidates = packed(ctx)
    assert candidates
    by_id = {el["id"]: el for el in source["elements"]}
    for cand in candidates:
        design = apply_candidate(scene, target, cand)
        contain_text(ctx, design)
        report = critique(ctx, design, cand.hidden)
        assert report.penalties.get("collision", 0.0) == 0, cand.strategy
        assert report.penalties.get("cut_off", 0.0) == 0, cand.strategy
        assert not structural(source, target, design), cand.strategy
        # Nothing is tucked away, a missing asset included: every element keeps its layer and opacity.
        assert not cand.hidden, cand.strategy
        for el in design["elements"]:
            src = by_id[el["id"]]
            assert el.get("z_index") == src.get("z_index"), (cand.strategy, el["id"])
            assert el.get("opacity", 1) == src.get("opacity", 1), (cand.strategy, el["id"])
