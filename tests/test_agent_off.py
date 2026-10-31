from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import engine.resize
from engine.adapters import harness as harness_mod
from engine.adapters.harness import Harness
from engine.adapters.render import OfficialRenderer
from engine.adapters.scripted import NullAdvisor, ScriptedAdvisor
from engine.core import agent as agent_mod
from engine.core.agent import AgentSettings, Breaker
from engine.resize import resize
from tools.agent_stub_server import serve

# =============================================================================
# Module Overview
# =============================================================================
# The harness is off by default and changes nothing when it cannot help.
# Unset, `resize()` never imports it. Configured with an abstaining, dead,
# garbage-speaking, slow, CPU-burning or raising advisor, or a renderer that
# fails, every output is byte-identical to the default engine's, as
# `engine/main.py` writes it, and a call abandoned at its deadline has finished
# before the next task's engine starts.

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "bench" / "suite" / "dev"
# The official samples, then one dev task per aspect bucket plus a strip; most of them fire.
TASKS = [*sorted((ROOT / "input").glob("*.json")),
         *[DEV / f"{t}.json" for t in ("mobile_banner_0", "billboard_1", "x_post_0", "instagram_post_0",
                                        "instagram_portrait_1", "instagram_story_1")]]
HARNESS_MODULES = ("engine.core.agent", "engine.core.advice", "engine.adapters.harness",
                   "engine.adapters.openai_http", "engine.adapters.agent_config", "engine.adapters.render")


def _bytes(out: dict) -> str:
    return json.dumps(out, indent=2)


def _run_all() -> dict[str, str]:
    out = {}
    for path in TASKS:
        task = json.loads(path.read_text())
        out[path.stem] = _bytes(resize(task["source"], task["target_canvas"]))
    return out


@pytest.fixture(scope="module")
def baseline():
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("CHAMELEON_AGENT"):
                mp.delenv(name)
        engine.resize._agent.cache_clear()
        out = _run_all()
        assert engine.resize._agent() is None
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("value", [None, "off", "0", "none"])
def test_default_path_imports_no_harness_module(value):
    code = ("import json, sys\nfrom engine.resize import resize\n"
            "t = json.load(open('input/chameleon_sample_level1_warmup.json'))\n"
            "resize(t['source'], t['target_canvas'])\n"
            f"print(json.dumps([m for m in {HARNESS_MODULES!r} if m in sys.modules]))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHAMELEON_AGENT")}
    if value is not None:
        env["CHAMELEON_AGENT"] = value
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


def _script(tmp_path: Path, steps: list) -> str:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(steps))
    return str(path)


# Configs whose advisor must really be asked get a task budget that no machine load can use up
# before the first call; the default 10 s failed this test at load average 200.
_ROOMY = {"CHAMELEON_AGENT_BUDGET_S": "600"}


def _configs(tmp_path: Path, stub_url: str) -> dict[str, dict[str, str]]:
    return {
        "null": {"CHAMELEON_AGENT": "null", **_ROOMY},
        "script-garbage": {"CHAMELEON_AGENT": "script", **_ROOMY,
                           "CHAMELEON_AGENT_SCRIPT": _script(tmp_path, ["C", "maybe", "", "?", "D"])},
        "script-slow": {"CHAMELEON_AGENT": "script", "CHAMELEON_AGENT_TIMEOUT_S": "0.2",
                        "CHAMELEON_AGENT_SCRIPT": _script(tmp_path, [{"sleep": 2, "then": "B"}, "A"])},
        "script-raise": {"CHAMELEON_AGENT": "script", **_ROOMY, "CHAMELEON_AGENT_SCRIPT": _script(tmp_path, [{"raise": "boom"}])},
        "http-closed": {"CHAMELEON_AGENT": "http", **_ROOMY, "CHAMELEON_AGENT_URL": f"http://127.0.0.1:{_free_port()}/v1",
                        "CHAMELEON_AGENT_WARMUP_S": "0"},
        "http-stub-sleep": {"CHAMELEON_AGENT": "http", "CHAMELEON_AGENT_URL": stub_url,
                            "CHAMELEON_AGENT_WARMUP_S": "0", "CHAMELEON_AGENT_TIMEOUT_S": "0.5"},
    }


@pytest.mark.parametrize("name", ["null", "script-garbage", "script-slow", "script-raise", "http-closed",
                                  "http-stub-sleep"])
def test_outputs_are_byte_identical_with_a_useless_advisor(baseline, monkeypatch, tmp_path, name):
    server, url = serve("sleep:5")
    try:
        env = _configs(tmp_path, url)[name]
        trace = tmp_path / "trace.jsonl"
        for key, value in {**env, "CHAMELEON_AGENT_TRACE": str(trace)}.items():
            monkeypatch.setenv(key, value)
        engine.resize._agent.cache_clear()
        assert engine.resize._agent() is not None, "the harness must really be on for this test to mean anything"
        assert _run_all() == baseline
    finally:
        server.shutdown()
        server.server_close()
    traces = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(traces) == len(TASKS)
    reasons = {t["reason"] for t in traces}
    assert all(t["chosen"] == 0 for t in traces)
    assert reasons & {"kept", "invalid", "advisor_error"}, reasons  # the advisor was really asked
    if name != "null":
        assert "kept" not in reasons  # every answer was unusable or failed


def test_outputs_are_byte_identical_when_rendering_fails(baseline, monkeypatch):
    class BrokenRenderer:
        def render(self, design, max_size):
            raise RuntimeError("renderer down")

    harness = Harness(AgentSettings(), NullAdvisor(), BrokenRenderer(), Breaker())
    monkeypatch.setattr(engine.resize, "_agent", lambda: harness)
    assert _run_all() == baseline


def _burn(seconds: float):
    """A scripted answer that keeps a CPU busy in pure Python, as an in-process model call would."""
    def step(prompt, images):
        end = time.monotonic() + seconds
        x = 0
        while time.monotonic() < end:
            x += sum(i * i for i in range(500))
        return "B"
    return step


def test_a_cpu_burning_timeout_is_drained_before_the_next_engine_run(baseline, monkeypatch):
    still_running = []
    real = harness_mod.resize_with_report

    def spy(*args, **kwargs):
        still_running.append(agent_mod.abandoned())
        return real(*args, **kwargs)

    monkeypatch.setattr(harness_mod, "resize_with_report", spy)
    harness = Harness(AgentSettings(call_timeout_s=0.2), ScriptedAdvisor([_burn(3.0)]), OfficialRenderer(ROOT),
                      Breaker())
    monkeypatch.setattr(engine.resize, "_agent", lambda: harness)
    assert _run_all() == baseline
    assert harness.breaker.is_open and harness.breaker.reason == "timeout"
    assert still_running == [0] * len(TASKS)  # the burning call was waited for before each engine run
