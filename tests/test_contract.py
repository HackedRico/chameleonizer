from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from engine.resize import resize
from tools.checks import structural
from tools.gen_cases import generate

# =============================================================================
# Module Overview
# =============================================================================
# Contract tests: every output from `resize` must pass the hard rules for the
# official samples and a spread of generated tasks, under both drop policies,
# and `resize` must never raise, even on a design the engine cannot parse.

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = sorted((ROOT / "input").glob("*.json"))
GENERATED = [generate(9000 + i) for i in range(16)]
PORTS = default_ports(ROOT)


def _tasks():
    for path in OFFICIAL:
        yield pytest.param(json.loads(path.read_text()), id=path.stem)
    for task in GENERATED:
        yield pytest.param(task, id=task["task_id"])


@pytest.mark.parametrize("policy", ["none", "occlude"])
@pytest.mark.parametrize("task", list(_tasks()))
def test_output_passes_hard_rules(task, policy):
    outcome = resize_with_report(task["source"], task["target_canvas"], *PORTS, policy)
    assert structural(task["source"], task["target_canvas"], outcome.design) == []


@pytest.mark.parametrize("task", list(_tasks())[:6])
def test_resize_does_not_mutate_input(task):
    before = copy.deepcopy(task)
    resize(task["source"], task["target_canvas"])
    assert task == before


def test_resize_falls_back_instead_of_raising():
    task = json.loads(OFFICIAL[0].read_text())
    broken = copy.deepcopy(task["source"])
    broken["elements"][1]["font_size"] = "not a number"
    out = resize(broken, task["target_canvas"])
    assert sorted(e["id"] for e in out["elements"]) == sorted(e["id"] for e in broken["elements"])
