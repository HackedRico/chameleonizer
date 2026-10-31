from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from engine.adapters.scripted import Fail, NullAdvisor, RandomAdvisor, ScriptedAdvisor, Sleep
from engine.adapters.stage_one import default_ports
from engine.core import agent as agent_mod
from engine.core.advice import PROMPT_VERSION
from engine.core.agent import AgentSettings, Breaker, call_with_deadline, choose, eligible
from engine.core.critic import Report
from engine.core.pipeline import Finalist, Outcome, resize_with_report
from engine.core.ports import Answer
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# The harness's decision loop on synthetic finalists, with a fake renderer and
# the scripted advisor driving each enumerated action: a switch needs a
# decisive answer in both image orders; abstentions, garbage, slow, failing
# and raising advisors all keep the engine's choice; the breaker, the budget,
# eligibility and the final validator hold; and a seeded random advisor over
# real finalists only ever ships one of them.

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {"canvas": {"width": 100, "height": 100}, "elements": [
    {"id": "t", "type": "text", "x": 10, "y": 10, "width": 50, "height": 20, "content": "Hello",
     "font_family": "Montserrat", "font_size": 12, "color": "#000000"},
    {"id": "i", "type": "image", "x": 60, "y": 60, "width": 20, "height": 20, "asset_id": "x.png", "fit": "contain"},
]}
TARGET = {"width": 200, "height": 100}
SETTINGS = AgentSettings(call_timeout_s=2.0, budget_s=30.0)
OVERLAP = '{"flags": ["text_overlap"], "worst": "text_overlap"}'


class FakeRenderer:
    """PNG-like bytes unique to each design, and a count of renders."""

    def __init__(self) -> None:
        self.renders = 0

    def render(self, design, max_size):
        self.renders += 1
        return b"png" + hashlib.sha256(json.dumps(design, sort_keys=True).encode()).digest()


class BrokenRenderer:
    def render(self, design, max_size):
        raise RuntimeError("no fonts")


def _design(x: float) -> dict:
    return {"canvas": dict(TARGET), "elements": [
        {**SOURCE["elements"][0], "x": x, "y": 10},
        {**SOURCE["elements"][1], "x": 150, "y": 40},
    ]}


def _outcome(*specs: tuple[float, dict], alt_seconds: float = 0.0) -> Outcome:
    """Finalists with these (score, penalties); finalist i puts the text at x = 10 + 5 i."""
    fins = tuple(Finalist(_design(10 + 5 * i), f"s{i}", f"s{i}", Report(score, pen), Report(score, pen))
                 for i, (score, pen) in enumerate(specs))
    return Outcome(fins[0].design, fins[0].strategy, fins[0].report, (), fins, alt_seconds)


def _run(outcome, advisor, settings=SETTINGS, breaker=None, renderer=None, **kw):
    renderer = renderer or FakeRenderer()
    breaker = breaker or Breaker()
    result = choose(outcome, SOURCE, TARGET, advisor, renderer, settings, breaker, **kw)
    return result, renderer, breaker


TIE2 = ((95.0, {}), (94.0, {}))


def test_b_then_a_switches():
    out = _outcome(*TIE2)
    adv = ScriptedAdvisor(["B", "A"])
    result, _, _ = _run(out, adv)
    assert (result.chosen, result.reason, result.calls) == (1, "switched:tiebreak", 2)
    assert result.design is out.finalists[1].design and result.strategy == "s1"
    first, second = adv.calls
    assert first[1][1:] == second[1][:0:-1]  # the second call swaps the two candidates


@pytest.mark.parametrize("script, calls", [(["A"], 1), (["B", "B"], 2), (["B", "SAME"], 2), (["SAME"], 1)])
def test_anything_short_of_both_orders_keeps(script, calls):
    out = _outcome(*TIE2)
    result, _, breaker = _run(out, ScriptedAdvisor(script))
    assert (result.chosen, result.reason, result.calls) == (0, "kept", calls)
    assert result.design is out.design and not breaker.is_open


def test_garbage_keeps_and_three_in_a_row_open_the_breaker():
    breaker = Breaker()
    adv = ScriptedAdvisor(["Well, maybe C?", "", "<think>unclosed"])
    for _ in range(3):
        result, _, _ = _run(_outcome(*TIE2), adv, breaker=breaker)
        assert (result.chosen, result.reason) == (0, "invalid")
    assert breaker.is_open and breaker.reason == "invalid"
    result, renderer, _ = _run(_outcome(*TIE2), adv, breaker=breaker)
    assert (result.reason, result.calls, renderer.renders) == ("breaker_open", 0, 0)


def test_a_usable_answer_resets_the_invalid_streak():
    breaker = Breaker()
    adv = ScriptedAdvisor(["junk", "junk", "SAME", "junk", "junk"])
    for _ in range(5):
        _run(_outcome(*TIE2), adv, breaker=breaker)
    assert not breaker.is_open


def test_timeout_returns_within_its_bound_and_the_next_task_makes_no_calls():
    settings = AgentSettings(call_timeout_s=0.2, budget_s=30.0)
    breaker = Breaker()
    t0 = time.monotonic()
    result, _, _ = _run(_outcome(*TIE2), ScriptedAdvisor([Sleep(2.0, then="B")]), settings, breaker)
    elapsed = time.monotonic() - t0
    assert elapsed <= settings.call_timeout_s + settings.grace_s + 0.1
    assert (result.chosen, result.reason) == (0, "advisor_error")
    assert result.trace["steps"][0]["error"] == "timeout" and breaker.is_open
    nxt, renderer, _ = _run(_outcome(*TIE2), ScriptedAdvisor(["B", "A"]), settings, breaker)
    assert (nxt.reason, nxt.calls, renderer.renders, nxt.chosen) == ("breaker_open", 0, 0, 0)


@pytest.mark.parametrize("step, error", [(Fail("connection"), "connection"), (Fail("http_500"), "http_500"),
                                         (RuntimeError("boom"), "exception:RuntimeError")])
def test_failing_or_raising_advisors_keep_and_open_the_breaker(step, error):
    result, _, breaker = _run(_outcome(*TIE2), ScriptedAdvisor([step]))
    assert (result.chosen, result.reason) == (0, "advisor_error")
    assert result.trace["steps"][0]["error"] == error and breaker.is_open


def test_a_render_failure_keeps():
    result, _, _ = _run(_outcome(*TIE2), ScriptedAdvisor(["B", "A"]), renderer=BrokenRenderer())
    assert (result.chosen, result.reason, result.calls) == (0, "render_error", 0)


def test_an_exhausted_budget_makes_no_calls():
    out = _outcome(*TIE2, alt_seconds=10.0)
    adv = ScriptedAdvisor(["B", "A"])
    result, renderer, _ = _run(out, adv, AgentSettings(budget_s=10.0))
    assert (result.reason, result.calls, len(adv.calls), renderer.renders) == ("budget", 0, 0, 0)


def test_the_budget_runs_out_between_calls():
    # Start, three renders and the first call see time 0; the second call finds 0.2 s left.
    ticks = iter([0.0] * 5)

    def clock():
        return next(ticks, 9.8)

    adv = ScriptedAdvisor(["B", "A"])
    result, _, _ = _run(_outcome(*TIE2), adv, AgentSettings(budget_s=10.0), clock=clock)
    assert result.reason == "budget" and result.chosen == 0 and len(adv.calls) == 1


def test_only_eligible_challengers_are_shown():
    out = _outcome((90.0, {}), (80.0, {}), (89.5, {"collision": 3.0}), (88.0, {"small_text": 2.0}))
    assert eligible(out.finalists, SOURCE, TARGET, 3.0) == [0, 3]
    adv = ScriptedAdvisor(["SAME"])
    result, renderer, _ = _run(out, adv)
    shown = {sha for _, shas in adv.calls for sha in shas}
    fake = FakeRenderer()
    assert {agent_mod.short_sha(fake.render(out.finalists[i].design, 0)) for i in (1, 2)}.isdisjoint(shown)
    assert renderer.renders == 3 and result.reason == "kept"
    assert [f["eligible"] for f in result.trace["finalists"]] == [True, False, False, True]


def test_no_eligible_challenger_makes_no_calls():
    out = _outcome((90.0, {}), (80.0, {}))
    adv = ScriptedAdvisor(["B", "A"])
    result, renderer, _ = _run(out, adv)
    assert (result.reason, len(adv.calls), renderer.renders) == ("no_eligible", 0, 0)
    single = _outcome((90.0, {}))
    assert _run(single, adv)[0].reason == "single"


def test_a_challenger_that_breaks_a_hard_rule_is_never_chosen(monkeypatch):
    out = _outcome(*TIE2)
    out.finalists[1].design["elements"][0]["x"] = 500  # off the canvas
    result, _, _ = _run(out, ScriptedAdvisor(["B", "A"]))
    assert (result.chosen, result.reason) == (0, "no_eligible")
    # Even past eligibility, the final validator keeps the default.
    monkeypatch.setattr(agent_mod, "eligible", lambda fins, *a: list(range(len(fins))))
    result, _, _ = _run(out, ScriptedAdvisor(["B", "A"]))
    assert (result.chosen, result.reason, result.calls) == (0, "invalid_design", 2)


def test_king_of_the_hill_with_three_finalists():
    out = _outcome((95.0, {}), (94.0, {}), (93.0, {}))
    adv = ScriptedAdvisor(["B", "A", "B", "A"])
    result, _, _ = _run(out, adv, AgentSettings(top_k=3))
    assert (result.chosen, result.reason, result.calls) == (2, "switched:tiebreak", 4)
    fake = FakeRenderer()
    sha = [agent_mod.short_sha(fake.render(f.design, 0)) for f in out.finalists]
    assert adv.calls[2][1][1:] == (sha[1], sha[2])  # the new incumbent defends as A


def test_a_confirmed_switch_survives_a_later_failure():
    out = _outcome((95.0, {}), (94.0, {}), (93.0, {}))
    result, _, breaker = _run(out, ScriptedAdvisor(["B", "A", Fail("timeout")]))
    assert (result.chosen, result.reason) == (1, "switched:tiebreak") and breaker.is_open


FLAG_CASES = [
    (OVERLAP, ((95.0, {"collision": 4.0}), (94.0, {})), 1, "switched:flags"),
    (OVERLAP, ((95.0, {"collision": 4.0}), (94.0, {"collision": 3.9})), 0, "kept"),  # gain below flag_delta
    ('{"flags": [], "worst": "none"}', ((95.0, {"collision": 4.0}), (94.0, {})), 0, "kept"),
    ('{"flags": ["distorted_image"], "worst": "distorted_image"}', ((95.0, {}), (94.0, {})), 0, "kept"),
    ('{"flags": ["made_up"], "worst": "made_up"}', ((95.0, {"collision": 4.0}), (94.0, {})), 0, "invalid"),
    ('{"flags": ["crowded", "tiny_text"], "worst": "none"}',
     ((95.0, {"small_text": 3.0}), (94.0, {"small_text": 1.0, "edge_crowding": 2.0})), 1, "switched:flags"),
]


@pytest.mark.parametrize("answer, specs, chosen, reason", FLAG_CASES)
def test_flags_act_only_through_critic_evidence(answer, specs, chosen, reason):
    result, _, _ = _run(_outcome(*specs), ScriptedAdvisor([answer]), AgentSettings(slots="flags"))
    assert (result.chosen, result.reason) == (chosen, reason)
    assert result.calls == 1


def test_flags_pick_the_lowest_flagged_penalty():
    out = _outcome((95.0, {"collision": 4.0}), (94.0, {"collision": 1.0}), (94.5, {}))
    result, _, _ = _run(out, ScriptedAdvisor([OVERLAP]), AgentSettings(slots="flags", top_k=3))
    assert result.chosen == 2
    assert result.trace["steps"][0]["acted_issue"] == "text_overlap" and result.trace["steps"][0]["proposal"] == 2


def test_flags_and_tiebreak_need_both():
    specs = ((95.0, {"collision": 4.0}), (94.0, {}))
    settings = AgentSettings(slots="flags+tiebreak")
    yes, _, _ = _run(_outcome(*specs), ScriptedAdvisor([OVERLAP, "B", "A"]), settings)
    assert (yes.chosen, yes.reason, yes.calls) == (1, "switched:flags+tiebreak", 3)
    no, _, _ = _run(_outcome(*specs), ScriptedAdvisor([OVERLAP, "A"]), settings)
    assert (no.chosen, no.reason, no.calls) == (0, "kept", 2)
    none, _, _ = _run(_outcome(*specs), ScriptedAdvisor(['{"flags": [], "worst": "none"}', "B", "A"]), settings)
    assert (none.chosen, none.reason, none.calls) == (0, "kept", 1)


def test_trace_records_what_was_asked_and_answered():
    result, _, _ = _run(_outcome(*TIE2), ScriptedAdvisor(["B", "A"]))
    t = result.trace
    assert t["v"] == 1 and t["prompt_version"] == PROMPT_VERSION and t["prompt"] == "p1"
    assert t["canvas"] == [200, 100] and len(t["source_sha"]) == 16
    assert (t["chosen"], t["reason"], t["calls"]) == (1, "switched:tiebreak", 2)
    s1, s2 = t["steps"]
    assert (s1["order"], s1["shown"], s1["raw"], s1["parsed"]) == (1, ["orig", 0, 1], "B", "B")
    assert (s2["order"], s2["shown"], s2["parsed"]) == (2, ["orig", 1, 0], "A")
    assert all(len(sha) == 16 for sha in s1["sha"]) and s1["sha"][1:] == s2["sha"][:0:-1]
    assert s1["latency_s"] >= 0 and s1["error"] is None
    json.dumps(t)  # serialisable, no bytes


def test_choose_never_raises():
    class Weird:
        def describe(self):
            raise ValueError("no name")

        def ask(self, prompt, images, timeout_s):
            return "not an Answer"

    result, _, _ = _run(_outcome(*TIE2), Weird())
    assert result.chosen == 0 and result.reason == "advisor_error"
    broken = Outcome({"canvas": {}}, "x", Report(1.0), (), (None, None))  # type: ignore[arg-type]
    result = choose(broken, SOURCE, TARGET, NullAdvisor(), FakeRenderer(), SETTINGS, Breaker())
    assert (result.chosen, result.reason) == (0, "error") and result.design is broken.design


def test_call_with_deadline_reports_instead_of_raising():
    assert call_with_deadline(lambda: Answer(raw="A"), 1.0).raw == "A"
    assert call_with_deadline(lambda: 1 / 0, 1.0).error == "exception:ZeroDivisionError"
    t0 = time.monotonic()
    assert call_with_deadline(lambda: time.sleep(1.0) or Answer(raw="A"), 0.1).error == "timeout"
    assert time.monotonic() - t0 < 0.5
    assert len(call_with_deadline(lambda: Answer(raw="x" * 5000), 1.0).raw) == 2000


@pytest.fixture(scope="module")
def sample_outcomes():
    ports = default_ports(ROOT)
    out = []
    for path in sorted((ROOT / "input").glob("*.json")):
        task = json.loads(path.read_text())
        out.append((task, resize_with_report(task["source"], task["target_canvas"], *ports, alternatives=3)))
    return out


@pytest.mark.parametrize("slots", ["tiebreak", "flags", "flags+tiebreak"])
def test_random_advisor_only_ever_ships_a_finalist(sample_outcomes, slots):
    settings = AgentSettings(slots=slots, top_k=4, epsilon=20.0)
    for task, outcome in sample_outcomes:
        src, tgt = task["source"], task["target_canvas"]
        for seed in range(6):
            result = choose(outcome, src, tgt, RandomAdvisor(seed), FakeRenderer(), settings, Breaker())
            assert any(result.design is f.design for f in outcome.finalists)
            assert result.design is outcome.finalists[result.chosen].design
            assert structural(src, tgt, result.design) == []
            assert result.reason in agent_mod.REASONS


def test_a_challenger_may_not_trade_one_unreadable_penalty_for_another():
    out = _outcome((90.0, {"collision": 5.0}), (89.0, {"cut_off": 4.9}), (89.0, {"collision": 4.0}))
    assert eligible(out.finalists, SOURCE, TARGET, 3.0) == [0, 2]


def test_a_challenger_changing_anything_but_boxes_and_sizes_is_never_chosen(monkeypatch):
    out = _outcome(*TIE2)
    out.finalists[1].design["elements"][1]["opacity"] = 0.1  # within the hard rules, but not a layout change
    result, _, _ = _run(out, ScriptedAdvisor(["B", "A"]))
    assert (result.chosen, result.reason, result.calls) == (0, "no_eligible", 0)
    monkeypatch.setattr(agent_mod, "eligible", lambda fins, *a: list(range(len(fins))))
    result, _, _ = _run(out, ScriptedAdvisor(["B", "A"]))
    assert (result.chosen, result.reason, result.calls) == (0, "invalid_design", 2)


class FakeClock:
    """Time that moves only when a scripted answer says it took a while."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def takes(self, seconds: float, answer: str):
        def step(prompt, images):
            self.now += seconds
            return answer
        return step


def test_a_call_starts_only_with_its_whole_timeout_left():
    # Each answer takes 1.4 s against a 2 s timeout; after the first, 1.6 s of a 3 s budget is left.
    clock = FakeClock()
    settings = AgentSettings(call_timeout_s=2.0, budget_s=3.0)
    breaker = Breaker()
    adv = ScriptedAdvisor([clock.takes(1.4, "B"), clock.takes(1.4, "A"), clock.takes(1.4, "B")])
    result, _, _ = _run(_outcome(*TIE2), adv, settings, breaker, clock=clock)
    assert (result.chosen, result.reason, result.calls) == (0, "budget", 1)
    assert not breaker.is_open  # running short of budget is not a transport failure
    nxt, _, _ = _run(_outcome(*TIE2), adv, settings, breaker, clock=clock)
    assert (nxt.calls, nxt.reason) == (1, "kept")  # the next task still asks; its first answer is A


def test_a_budget_below_one_timeout_makes_no_calls():
    adv = ScriptedAdvisor(["B", "A"])
    result, renderer, breaker = _run(_outcome(*TIE2), adv, AgentSettings(call_timeout_s=5.0, budget_s=4.0))
    assert (result.reason, result.calls, len(adv.calls)) == ("budget", 0, 0) and not breaker.is_open


def test_a_pathological_answer_is_counted_traced_and_kept():
    deep = ('{"winner":' + "[" * 997 + "]" * 997 + "}")[:2000]
    result, _, breaker = _run(_outcome(*TIE2), ScriptedAdvisor([deep]))
    assert (result.chosen, result.reason, result.calls) == (0, "invalid", 1)
    assert result.trace["steps"][0]["parsed"] is None and breaker._invalid == 1


def test_p1_noorig_reads_image_positions_among_the_two_images_sent():
    settings = AgentSettings(prompt="p1-noorig")
    result, _, _ = _run(_outcome(*TIE2), ScriptedAdvisor(["Image 2", "Image 1"]), settings)
    assert (result.chosen, result.reason) == (1, "switched:tiebreak")
    assert [s["shown"] for s in result.trace["steps"]] == [["orig", 0, 1], ["orig", 1, 0]]


def test_a_breaker_open_task_asks_for_no_alternatives(monkeypatch):
    from engine.adapters import harness as harness_mod

    seen = []
    real = harness_mod.resize_with_report

    def spy(*args, **kwargs):
        seen.append(kwargs.get("alternatives"))
        return real(*args, **kwargs)

    monkeypatch.setattr(harness_mod, "resize_with_report", spy)
    breaker = Breaker()
    breaker._trip("timeout")
    h = harness_mod.Harness(AgentSettings(top_k=4), ScriptedAdvisor(["B", "A"]), FakeRenderer(), breaker)
    task = json.loads((ROOT / "bench" / "suite" / "dev" / "x_post_0.json").read_text())
    design, outcome, result = harness_mod.agent_resize(task["source"], task["target_canvas"], *default_ports(ROOT),
                                                       "none", h)
    assert seen == [0] and outcome.finalists == ()
    assert (result.reason, result.calls, result.chosen) == ("breaker_open", 0, 0) and design is outcome.design


def _burn(seconds: float):
    """A scripted answer that keeps a CPU busy in pure Python, as an in-process model call would."""
    def step(prompt, images):
        end = time.monotonic() + seconds
        x = 0
        while time.monotonic() < end:
            x += sum(i * i for i in range(500))
        return "B"
    return step


def test_drain_waits_for_an_abandoned_call():
    answer = call_with_deadline(lambda: Answer(raw=_burn(0.8)(None, None)), 0.1)
    assert answer.error == "timeout" and agent_mod.abandoned() == 1
    assert agent_mod.drain_abandoned(0.0) is False
    assert agent_mod.drain_abandoned(5.0) is True and agent_mod.abandoned() == 0
