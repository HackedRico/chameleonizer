from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from engine.core.advice import (ISSUE_PENALTIES, ISSUES, PROMPT_VERSION, parse_flags, parse_verdict, prompt,
                                resolve, short_sha, shows_original)
from engine.core.pipeline import UNREADABLE, Finalist, Outcome, unreadable
from engine.core.ports import Advisor, Answer, Prompt, Renderer
from engine.core.validate import hard_rule_problems

logger = logging.getLogger(__name__)

# =============================================================================
# Module Overview
# =============================================================================
# The optional model harness's decision loop, pure and off by default. Given
# the pipeline's finished finalists (finalist 0 is the engine's own pick),
# `choose` keeps the near-ties that are no less readable and pass the hard
# rules, renders them through the `Renderer` port, asks the `Advisor` port
# enumerated questions, and maps each parsed answer to a coded action. The
# model never picks an action and never emits geometry: the only thing it can
# change is which finalist ships, and anything short of a decisive, valid
# answer confirmed in both image orders, or a flag the critic's penalties
# back, keeps finalist 0. Every call has its
# own deadline and starts only with that whole deadline left in the task's
# budget, and a circuit breaker turns the advisor off for the rest of the
# process after a transport failure. A call abandoned at its deadline may keep
# running on its thread; `drain_abandoned` lets the caller wait for it before
# the engine runs again, so it cannot steal the engine's CPU.


@dataclass(frozen=True)
class AgentSettings:
    """How the harness asks; every default is the conservative one."""

    slots: str = "tiebreak"  # "tiebreak" | "flags" | "flags+tiebreak"
    top_k: int = 2  # finalists including the engine default, 2..4
    epsilon: float = 3.0  # critic points a challenger may trail the default by
    flag_delta: float = 0.25  # least drop in the flagged penalties for a flag to act
    call_timeout_s: float = 5.0  # per call; a call starts only with this much budget left
    budget_s: float = 10.0  # per task: finishing alternatives, renders and calls
    min_call_s: float = 0.5  # no render starts with less time left
    grace_s: float = 0.25  # thread join slack beyond the adapter's own timeout
    drain_s: float = 30.0  # longest wait for an abandoned call to finish before the engine runs again
    image_max: int = 1024  # render max_size; the renderer only downscales
    prompt: str = "p1"  # compare variant; the flags slot always uses "p2a"


SLOTS = ("tiebreak", "flags", "flags+tiebreak")
REASONS = ("single", "no_eligible", "breaker_open", "budget", "advisor_error", "invalid", "kept",
           "switched:tiebreak", "switched:flags", "switched:flags+tiebreak", "invalid_design",
           "render_error", "error")
# Longest model text kept in an answer and its trace.
_RAW_LIMIT = 2000
# Unreadable content a challenger may carry beyond the default's: none, up to float noise.
_READABLE_SLACK = 1e-6


class Breaker:
    """Turns the advisor off for the rest of the process after the first transport failure (timeout,
    connection, HTTP, adapter exception, busy) or `invalid_limit` unusable answers in a row.

    A server keeps generating after a client timeout, and the next request would queue behind it."""

    def __init__(self, invalid_limit: int = 3) -> None:
        self.invalid_limit = invalid_limit
        self.is_open = False
        self.reason: str | None = None
        self._invalid = 0
        self._lock = threading.Lock()

    def record(self, answer: Answer, usable: bool) -> None:
        with self._lock:
            if answer.error:
                self._trip(answer.error)
            elif usable:
                self._invalid = 0
            else:
                self._invalid += 1
                if self._invalid >= self.invalid_limit:
                    self._trip("invalid")

    def _trip(self, reason: str) -> None:
        if not self.is_open:
            self.is_open, self.reason = True, reason
            logger.warning("[agent] Advisor switched off for this process: %s.", reason)


# Advisor threads still running after their deadline; `drain_abandoned` waits for them.
_ABANDONED: list[threading.Thread] = []
_ABANDONED_LOCK = threading.Lock()


def call_with_deadline(fn: Callable[[], Answer], timeout_s: float) -> Answer:
    """`fn()` on a daemon thread, waited for at most `timeout_s` seconds.

    Returns Answer(error="timeout") if it is still running and Answer(error="exception:<Type>") if it
    raised. A daemon thread rather than a ThreadPoolExecutor, because executor workers are joined at
    interpreter exit, and an uninterruptible in-process model call would hold up exit. A thread
    still running at the deadline is remembered for `drain_abandoned`."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["answer"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported as an answer, never re-raised
            box["exc"] = exc

    t0 = time.monotonic()
    thread = threading.Thread(target=run, name="chameleon-advisor", daemon=True)
    thread.start()
    thread.join(max(0.0, timeout_s))
    elapsed = round(time.monotonic() - t0, 3)
    if thread.is_alive():
        with _ABANDONED_LOCK:
            _ABANDONED.append(thread)
        return Answer(error="timeout", latency_s=elapsed)
    if "exc" in box:
        return Answer(error=f"exception:{type(box['exc']).__name__}", latency_s=elapsed)
    answer = box.get("answer")
    if not isinstance(answer, Answer):
        return Answer(error="bad_response", latency_s=elapsed)
    raw = answer.raw if isinstance(answer.raw, str) else ""
    return replace(answer, raw=raw[:_RAW_LIMIT], latency_s=answer.latency_s or elapsed)


def abandoned() -> int:
    """How many advisor calls abandoned at their deadline are still running."""
    with _ABANDONED_LOCK:
        _ABANDONED[:] = [t for t in _ABANDONED if t.is_alive()]
        return len(_ABANDONED)


def drain_abandoned(limit_s: float) -> bool:
    """Wait up to `limit_s` seconds for abandoned advisor calls to finish; True when none still runs.

    An in-process model call cannot be interrupted, and while it runs it competes with the engine
    for the CPU; the engine's candidate loop has a wall-clock budget, so that contention could
    change what the engine itself picks. Call this before the engine runs again."""
    end = time.monotonic() + max(0.0, limit_s)
    with _ABANDONED_LOCK:
        threads = list(_ABANDONED)
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    return abandoned() == 0


def eligible(
    finalists: Sequence[Finalist], source: dict[str, Any], target: dict[str, Any], epsilon: float
) -> list[int]:
    """[0] plus every finalist within `epsilon` critic points of the default, no worse than it on any
    unreadable-content penalty, free of hard-rule problems, and changing nothing but boxes and text
    sizes against it."""
    if not finalists:
        return []
    base = finalists[0]
    floor = base.report.score - epsilon
    out = [0]
    for i, fin in enumerate(finalists[1:], start=1):
        if fin.report.score >= floor and _no_less_readable(fin.report.penalties, base.report.penalties) \
                and not hard_rule_problems(source, target, fin.design, base.design):
            out.append(i)
    return out


def _no_less_readable(penalties: Mapping[str, float], base: Mapping[str, float]) -> bool:
    """Per key, so a challenger can never trade a collision for text drawn off the canvas."""
    return all(penalties.get(k, 0.0) <= base.get(k, 0.0) + _READABLE_SLACK for k in UNREADABLE)


@dataclass(frozen=True)
class AgentResult:
    """What the harness chose and why."""

    design: dict[str, Any]  # finalists[chosen].design, or outcome.design (the same object) when kept
    chosen: int  # 0 is the engine default
    strategy: str
    reason: str  # one of REASONS
    calls: int
    seconds: float
    trace: dict[str, Any]  # JSON-serialisable, no image bytes


class _Stop(Exception):
    """Ends the loop early; a switch already confirmed in both orders survives it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def choose(
    outcome: Outcome,
    source: dict[str, Any],
    target: dict[str, Any],
    advisor: Advisor,
    renderer: Renderer,
    settings: AgentSettings,
    breaker: Breaker,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """The finalist to ship: the engine default unless the advisor's answers, read by code, say a
    near-tie is better. Never raises; any exception yields the default with reason "error"."""
    try:
        run = _Run(outcome, source, target, advisor, renderer, settings, breaker, clock)
        return run.go()
    except Exception as exc:  # the harness is optional; the default is always shippable
        logger.warning("[agent] Harness failed; keeping the engine's choice. Reason: %r", exc)
        try:
            return run.keep("error")
        except Exception:  # `run` was never built, or its clock fails too
            return AgentResult(outcome.design, 0, outcome.strategy, "error", 0, 0.0, {"v": 1, "reason": "error"})


class _Run:
    """One task's pass through the fixed policy: RENDER, ASK_FLAGS, ASK_COMPARE, SWITCH or KEEP."""

    def __init__(self, outcome: Outcome, source: dict[str, Any], target: dict[str, Any], advisor: Advisor,
                 renderer: Renderer, settings: AgentSettings, breaker: Breaker, clock: Callable[[], float]) -> None:
        self.outcome, self.source, self.target = outcome, source, target
        self.advisor, self.renderer, self.settings = advisor, renderer, settings
        self.breaker, self.clock = breaker, clock
        self.start = clock()
        self.deadline = self.start + max(0.0, settings.budget_s - outcome.alt_seconds)
        self.calls = 0
        self.usable = 0
        self.images: dict[Any, bytes] = {}
        self.trace: dict[str, Any] = {
            "v": 1, "prompt_version": PROMPT_VERSION, "advisor": _describe(advisor), "prompt": settings.prompt,
            "slots": settings.slots, "canvas": [_int(target.get("width")), _int(target.get("height"))],
            "source_sha": _json_sha(source), "finalists": [], "steps": [],
        }

    # -- results ---------------------------------------------------------------------------------

    def keep(self, reason: str) -> AgentResult:
        return self._result(0, reason)

    def _result(self, chosen: int, reason: str) -> AgentResult:
        seconds = round(max(0.0, self.clock() - self.start), 3)
        self.trace.update(chosen=chosen, reason=reason, calls=self.calls, seconds=seconds)
        if chosen == 0:
            design, strategy = self.outcome.design, self.outcome.strategy
        else:
            fin = self.outcome.finalists[chosen]
            design, strategy = fin.design, fin.strategy
        return AgentResult(design, chosen, strategy, reason, self.calls, seconds, self.trace)

    # -- the policy ------------------------------------------------------------------------------

    def go(self) -> AgentResult:
        fin = self.outcome.finalists
        if self.breaker.is_open:
            return self.keep("breaker_open")
        if len(fin) < 2:
            return self.keep("single")
        elig = eligible(fin, self.source, self.target, self.settings.epsilon)
        self.trace["finalists"] = [
            {"strategy": f.strategy, "critic": f.report.score, "unreadable": round(unreadable(f.report), 3),
             "eligible": i in elig}
            for i, f in enumerate(fin)
        ]
        if len(elig) < 2:
            return self.keep("no_eligible")
        s = self.settings
        W, H = _int(self.target["width"]), _int(self.target["height"])
        incumbent, via = 0, ""
        challengers = sorted(elig[1:], key=lambda i: (-fin[i].report.score, i))
        try:
            if s.slots in ("flags", "flags+tiebreak"):
                parsed = self.ask_flags(prompt("p2a", W, H), ("orig", 0))
                proposal = self.flag_proposal(parsed, 0, challengers)
                if proposal is None:
                    return self.keep("invalid" if parsed is None else "kept")
                if s.slots == "flags":
                    incumbent, via = proposal, "flags"
                else:
                    challengers = [proposal]
            if s.slots in ("tiebreak", "flags+tiebreak"):
                compare = prompt(s.prompt, W, H)
                for c in challengers:  # king of the hill
                    first = self.ask_compare(compare, ("orig", incumbent, c), 1)
                    if first != "B":
                        continue  # A, SAME or unusable: the second order cannot make the pair decisive
                    second = self.ask_compare(compare, ("orig", c, incumbent), 2)
                    if resolve(first, second) == "challenger":
                        incumbent, via = c, s.slots
        except _Stop as stop:
            if incumbent == 0:
                return self.keep(stop.reason)
        if incumbent == 0:
            return self.keep("kept" if self.usable else "invalid")
        if hard_rule_problems(self.source, self.target, fin[incumbent].design, fin[0].design):
            return self.keep("invalid_design")  # the final validator
        return self._result(incumbent, f"switched:{via}")

    def flag_proposal(self, parsed: tuple[tuple[str, ...], str] | None, incumbent: int,
                      challengers: list[int]) -> int | None:
        """The challenger whose critic penalties for the flagged issue are lowest, if they beat the
        incumbent's by at least `flag_delta`; None for no flag, an alarm-only flag or no coded gain."""
        step = self.trace["steps"][-1] if self.trace["steps"] else {}
        if parsed is None:
            return None
        flags, worst = parsed
        issue = worst if worst in ISSUES else (min(flags, key=ISSUES.index) if flags else None)
        step["acted_issue"] = issue
        step["proposal"] = None
        keys = ISSUE_PENALTIES.get(issue or "", ())
        if not keys:
            if issue == "distorted_image":
                logger.warning("[agent] Advisor flagged a distorted image; hard rules guarantee aspect, keeping.")
            return None
        fin = self.outcome.finalists

        def cost(i: int) -> float:
            return sum(fin[i].report.penalties.get(k, 0.0) for k in keys)

        best = min(challengers, key=lambda i: (cost(i), -fin[i].report.score, i), default=None)
        if best is None or cost(best) > cost(incumbent) - self.settings.flag_delta:
            return None
        step["proposal"] = best
        return best

    # -- actions ---------------------------------------------------------------------------------

    def image(self, which: Any) -> bytes:
        """The render of the source ("orig") or of a finalist, drawn once per task on this thread."""
        if which not in self.images:
            if self.deadline - self.clock() < self.settings.min_call_s:
                raise _Stop("budget")
            design = self.source if which == "orig" else self.outcome.finalists[which].design
            try:
                png = self.renderer.render(design, self.settings.image_max)
            except Exception as exc:
                logger.warning("[agent] Render failed; keeping the engine's choice. Reason: %r", exc)
                raise _Stop("render_error") from exc
            if not isinstance(png, (bytes, bytearray)) or not png:
                raise _Stop("render_error")
            self.images[which] = bytes(png)
        return self.images[which]

    def ask_compare(self, p: Prompt, shown: tuple[Any, ...], order: int) -> str | None:
        original = shows_original(p)
        return self._ask(p, shown, lambda raw: parse_verdict(raw, p.labels, original), {"order": order})

    def ask_flags(self, p: Prompt, shown: tuple[Any, ...]) -> tuple[tuple[str, ...], str] | None:
        parsed = self._ask(p, shown, parse_flags, {})
        if parsed is not None:
            self.trace["steps"][-1]["parsed"] = {"flags": list(parsed[0]), "worst": parsed[1]}
        return parsed

    def _ask(self, p: Prompt, shown: tuple[Any, ...], parse: Callable[[str], Any], extra: dict[str, Any]) -> Any:
        images = tuple(self.image(w) for w in shown)
        if self.breaker.is_open:
            raise _Stop("breaker_open")
        # A call starts only with its whole timeout left, so a timeout always means the model was
        # slower than `call_timeout_s`, never that the task's budget cut a normal answer short:
        # only the first trips the breaker for the rest of the process.
        t = self.settings.call_timeout_s
        if self.deadline - self.clock() < max(t, self.settings.min_call_s):
            raise _Stop("budget")
        answer = call_with_deadline(lambda: self.advisor.ask(p, images, t), t + self.settings.grace_s)
        parsed = None
        if not answer.error:
            try:
                parsed = parse(answer.raw)
            except Exception:  # the parsers never raise; if one did, its answer is an abstention
                parsed = None
        self.breaker.record(answer, parsed is not None)
        self.calls += 1
        self.usable += parsed is not None
        self.trace["steps"].append({
            "kind": p.kind, "prompt": p.id, **extra, "shown": list(shown), "sha": [short_sha(im) for im in images],
            "raw": answer.raw, "parsed": parsed, "probs": dict(answer.probs) if answer.probs else None,
            "latency_s": answer.latency_s, "error": answer.error, "note": answer.note,
        })
        if answer.error:
            raise _Stop("advisor_error")  # the breaker is open now; abandon the task
        return parsed


def _describe(advisor: Advisor) -> str:
    try:
        return str(advisor.describe())
    except Exception as exc:  # a label is not worth failing a task over
        return f"unknown ({type(exc).__name__})"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_sha(design: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(design, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
