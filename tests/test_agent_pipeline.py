from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core import pipeline
from engine.core.pipeline import resize_with_report
from engine.core.validate import hard_rule_problems
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# The pipeline refactor that exposes finished finalists to the harness. Asking
# for alternatives must not change the engine's own pick in any way; finalist
# 0 is that pick; finalists are distinct, independent and valid; and the
# alternatives' time budget and the no-candidate fallback behave.

ROOT = Path(__file__).resolve().parents[1]
PORTS = default_ports(ROOT)
TASKS = [*sorted((ROOT / "input").glob("*.json")),
         *[ROOT / "bench" / "suite" / "dev" / f"{t}.json" for t in
           ("billboard_1", "facebook_post_0", "mobile_banner_1", "linkedin_banner_1", "x_header_0")]]


def _task(path: Path) -> tuple[dict, dict]:
    task = json.loads(path.read_text())
    return task["source"], task["target_canvas"]


@pytest.fixture(scope="module")
def runs():
    out = {}
    for path in TASKS:
        src, tgt = _task(path)
        out[path.stem] = (src, tgt, resize_with_report(src, tgt, *PORTS),
                          resize_with_report(src, tgt, *PORTS, alternatives=3))
    return out


@pytest.mark.parametrize("name", [p.stem for p in TASKS])
def test_alternatives_leave_the_pick_unchanged(runs, name):
    _src, _tgt, plain, alt = runs[name]
    assert json.dumps(alt.design, indent=2) == json.dumps(plain.design, indent=2)
    assert (alt.strategy, alt.report, alt.considered) == (plain.strategy, plain.report, plain.considered)
    assert plain.finalists == () and plain.alt_seconds == 0.0


@pytest.mark.parametrize("name", [p.stem for p in TASKS])
def test_finalists_are_distinct_valid_and_led_by_the_pick(runs, name):
    src, tgt, _plain, alt = runs[name]
    assert alt.finalists[0].design is alt.design
    assert alt.finalists[0].strategy == alt.strategy and alt.finalists[0].report == alt.report
    assert 1 <= len(alt.finalists) <= 4
    keys = [json.dumps(f.design, sort_keys=True) for f in alt.finalists]
    assert len(set(keys)) == len(keys)
    for fin in alt.finalists:
        assert structural(src, tgt, fin.design) == []
        assert hard_rule_problems(src, tgt, fin.design) == []


def test_some_task_has_several_finalists(runs):
    assert any(len(r[3].finalists) > 1 for r in runs.values())


def test_finalists_do_not_alias(runs):
    name = next(k for k, r in runs.items() if len(r[3].finalists) > 1)
    fins = runs[name][3].finalists
    before = [json.dumps(f.design, sort_keys=True) for f in fins]
    fins[1].design["elements"][0]["x"] = -12345.0
    try:
        assert json.dumps(fins[0].design, sort_keys=True) == before[0]
        assert all(json.dumps(f.design, sort_keys=True) == b for f, b in zip(fins[2:], before[2:]))
    finally:
        fins[1].design["elements"][0]["x"] = json.loads(before[1])["elements"][0]["x"]


def test_packed_layout_is_shared_by_copy():
    """Two colliding candidates both replaced by the packed layout give one finalist, and copies never alias."""
    src, tgt = _task(ROOT / "input" / "chameleon_sample_level3_advanced.json")
    tgt = {"width": 320, "height": 50}
    out = resize_with_report(src, tgt, *PORTS, alternatives=3)
    strategies = [f.strategy for f in out.finalists]
    assert len([s for s in strategies if s.startswith("packed")]) <= 1
    assert all(hard_rule_problems(src, tgt, f.design) == [] for f in out.finalists)


def test_expired_alternatives_budget_leaves_only_the_pick():
    src, tgt = _task(TASKS[1])
    out = resize_with_report(src, tgt, *PORTS, alternatives=3, alt_budget_s=-1.0)
    assert len(out.finalists) == 1 and out.finalists[0].design is out.design


def test_no_candidate_fallback_has_no_finalists(monkeypatch):
    monkeypatch.setattr(pipeline, "propose", lambda *a, **k: iter(()))
    src, tgt = _task(TASKS[0])
    out = resize_with_report(src, tgt, *PORTS, alternatives=3)
    assert out.strategy == "fallback/no-candidate" and out.finalists == ()


def test_a_failing_alternative_is_skipped(monkeypatch):
    src, tgt = _task(TASKS[1])
    real = pipeline._finish
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("forced")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_finish", flaky)
    out = resize_with_report(src, tgt, *PORTS, alternatives=3)
    assert len(out.finalists) == 1 and len(calls) > 1
