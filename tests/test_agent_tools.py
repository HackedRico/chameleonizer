from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

import engine.resize
from engine.adapters.agent_config import load_agent_config
from engine.adapters.harness import agent_resize, build_harness
from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from tools import agree, bench, digests

# =============================================================================
# Module Overview
# =============================================================================
# The instruments the evaluation plan depends on, each on one real task with
# no model: `tools.bench --agent` (rows, summary, trace file), the replay path
# from a near-ties round through `build` to a switch in the engine, with
# misses reported rather than silent, and `tools.digests` (run, diff, exit
# codes, a harness that never came up, and secrets kept out of the file).

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "bench" / "suite" / "dev" / "x_post_0.json"  # fires with top_k 2
CALM = ROOT / "input" / "chameleon_sample_level1_warmup.json"


def _suite(tmp_path: Path, *tasks: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(exist_ok=True)
    for task in tasks:
        shutil.copy(task, suite)
    return suite


def _env(monkeypatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    engine.resize._agent.cache_clear()


def test_bench_agent_rows_summary_and_trace(tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(json.dumps(["B", "A"]))
    _env(monkeypatch, CHAMELEON_AGENT="script", CHAMELEON_AGENT_SCRIPT=str(script))
    out = tmp_path / "bench"
    summary = bench.run_bench(out, 0, 1, "none", _suite(tmp_path, TASK), agent=True)
    rows = json.loads((out / "results.json").read_text())["rows"]
    agent = rows[0]["agent"]
    assert (agent["chosen"], agent["reason"], agent["calls"]) == (1, "switched:tiebreak", 2)
    assert rows[0]["strategy"] == agent["finalists"][1]["strategy"]
    assert rows[0]["critic"] == agent["finalists"][1]["critic"] and rows[0]["violations"] == []
    assert (summary["agent_fired"], summary["agent_switched"], summary["agent_calls"]) == (1, 1, 2)
    assert summary["agent_reasons"] == {"switched:tiebreak": 1} and "agent_replay_hits" not in summary
    traces = [json.loads(line) for line in (out / "agent_trace.jsonl").read_text().splitlines()]
    assert len(traces) == 1 and traces[0]["chosen"] == 1
    task = json.loads(TASK.read_text())
    outcome = resize_with_report(task["source"], task["target_canvas"], *default_ports(ROOT), alternatives=1)
    written = json.loads((out / "renders" / "x_post_0_output.json").read_text())
    assert written == outcome.finalists[1].design != outcome.design  # the chosen finalist, not the default


def _replay_dataset(tmp_path: Path, image_max: int) -> Path:
    """A near-ties round on TASK in which the challenger wins in both orders, built into a dataset."""
    rnd = tmp_path / f"round{image_max}"
    agree.near_ties([_suite(tmp_path, TASK)], rnd, top_k=2, image_max=image_max)
    key = json.loads((rnd / "key.json").read_text())
    rows = [{"id": vid, "winner": "B" if meta["order"] == 1 else "A", "reason": "synthetic"}
            for vid, meta in key.items()]
    for rep in (0, 1):
        (rnd / f"verdicts_rep{rep}.json").write_text(json.dumps(rows))
    data = tmp_path / f"data{image_max}"
    agree.build(data, [rnd], {}, [], [], [], controls=0)
    return data / "replay.jsonl"


def test_replay_from_near_ties_switches_in_the_engine(tmp_path, monkeypatch):
    replay = _replay_dataset(tmp_path, 1024)
    _env(monkeypatch, CHAMELEON_AGENT="replay", CHAMELEON_AGENT_REPLAY=str(replay), CHAMELEON_AGENT_WARMUP_S="0")
    harness = build_harness(load_agent_config(__import__("os").environ), ROOT)
    task = json.loads(TASK.read_text())
    _design, _outcome, result = agent_resize(task["source"], task["target_canvas"], *default_ports(ROOT), "none",
                                             harness)
    assert (result.chosen, result.reason) == (1, "switched:tiebreak")
    assert [s["note"] for s in result.trace["steps"]] == ["replay_hit", "replay_hit"]


def test_replay_misses_are_reported_not_silent(tmp_path, monkeypatch, capsys):
    replay = _replay_dataset(tmp_path, 512)  # drawn at another size than the engine's 1024: nothing matches
    _env(monkeypatch, CHAMELEON_AGENT="replay", CHAMELEON_AGENT_REPLAY=str(replay))
    out = tmp_path / "bench"
    monkeypatch.setattr(sys, "argv", ["tools.bench", "--agent", "--suite", str(_suite(tmp_path, TASK)),
                                      "--out", str(out)])
    assert bench.main() == 1
    summary = json.loads((out / "results.json").read_text())["summary"]
    assert (summary["agent_replay_hits"], summary["agent_replay_misses"]) == (0, 1)
    assert summary["agent_replay_unmatched_tasks"] == 1 and summary["agent_reasons"] == {"kept": 1}
    err = capsys.readouterr().err
    assert "0 hits, 1 misses" in err and "matched no question" in err


def test_digests_run_diff_and_exit_codes(tmp_path, monkeypatch):
    suite = _suite(tmp_path, CALM)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    digests.run([suite], a)
    digests.run([suite], b)
    assert digests.diff(a, b) == []
    payload = json.loads(b.read_text())
    assert (payload["tasks"], payload["agent_wanted"], payload["harness_on"]) == (1, False, 0)
    payload["digests"]["suite/chameleon_sample_level1_warmup"] = "0" * 64
    b.write_text(json.dumps(payload))
    assert digests.diff(a, b) == ["suite/chameleon_sample_level1_warmup"]
    monkeypatch.setattr(sys, "argv", ["tools.digests", "diff", str(a), str(b)])
    assert digests.main() == 1
    monkeypatch.setattr(sys, "argv", ["tools.digests", "diff", str(a), str(a)])
    assert digests.main() == 0


def test_digests_fail_when_the_named_harness_never_came_up(tmp_path, monkeypatch):
    suite = _suite(tmp_path, CALM)
    closed = "http://127.0.0.1:9/v1"
    _env(monkeypatch, CHAMELEON_AGENT="http", CHAMELEON_AGENT_URL=closed, CHAMELEON_AGENT_WARMUP_S="1",
         CHAMELEON_AGENT_API_KEY="sk-secret-value", CHAMELEON_AGENT_TIMEOUT_S="1")
    with pytest.raises(SystemExit, match="harness was off for 1 of 1"):
        digests.run([suite], tmp_path / "dead.json")
    _env(monkeypatch, CHAMELEON_AGENT_WARMUP_S="0")
    payload = digests.run([suite], tmp_path / "on.json")
    assert payload["harness_on"] == 1
    text = (tmp_path / "on.json").read_text() + (tmp_path / "dead.json").read_text()
    assert "sk-secret-value" not in text and "CHAMELEON_AGENT_API_KEY" not in text
    assert payload["env"]["CHAMELEON_AGENT_URL"] == closed
