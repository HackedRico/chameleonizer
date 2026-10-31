from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.core.advice import ISSUES, replay_key, short_sha
from engine.core.ports import Answer, Prompt

# =============================================================================
# Module Overview
# =============================================================================
# Advisors that need no model, for tests, dead-model runs and evaluation
# baselines. `NullAdvisor` abstains on every question, so the whole harness
# path runs, renders included, with outputs byte-identical to the engine's.
# `ScriptedAdvisor` plays a fixed list of answers, sleeps, failures and
# exceptions. `RandomAdvisor` is the seeded noise floor, and `ReplayAdvisor`
# answers from recorded verdicts keyed on the exact render bytes, which is how
# frontier-model verdicts are replayed through the engine. Stdlib only.

# What every offline advisor answers to the one warmup call; warmups never use up a script.
_WARMUP_REPLY = "A"
_NO_FLAGS = '{"flags": [], "worst": "none"}'


def _abstain(prompt: Prompt) -> str:
    """A valid answer that changes nothing: SAME to a comparison, no issue to a review."""
    return _NO_FLAGS if prompt.kind == "flags" else "SAME"


class NullAdvisor:
    """Abstains on everything: the default backend, and proof the harness path changes nothing."""

    def describe(self) -> str:
        return "null"

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        return Answer(raw=_WARMUP_REPLY if prompt.kind == "warmup" else _abstain(prompt))


@dataclass(frozen=True)
class Sleep:
    """A script step that really sleeps for `seconds`, then answers `then`."""

    seconds: float
    then: str = "SAME"


@dataclass(frozen=True)
class Fail:
    """A script step that returns a transport error, such as "connection" or "http_500"."""

    error: str


Step = str | Sleep | Fail | BaseException | Callable[[Prompt, tuple[bytes, ...]], str]


class ScriptedAdvisor:
    """Plays `steps` in order, one per question; answers SAME (or no issue) once they run out.

    A step is a raw answer string, `Sleep`, `Fail`, an exception instance to raise, or a callable
    `(prompt, images) -> str`. `calls` records each question as (prompt id, image short shas)."""

    def __init__(self, steps: list[Step], cycle: bool = False) -> None:
        self._steps = list(steps)
        self._cycle = cycle
        self._next = 0
        self._lock = threading.Lock()
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @classmethod
    def from_file(cls, path: str | Path, cycle: bool = False) -> ScriptedAdvisor:
        """Steps from a JSON list of strings, {"sleep": s, "then": "B"}, {"fail": "connection"} or
        {"raise": "message"}."""
        data = json.loads(Path(path).read_text())
        if not isinstance(data, list):
            raise ValueError("a script is a JSON list of steps")
        steps: list[Step] = []
        for item in data:
            if isinstance(item, str):
                steps.append(item)
            elif isinstance(item, dict) and "sleep" in item:
                steps.append(Sleep(float(item["sleep"]), str(item.get("then", "SAME"))))
            elif isinstance(item, dict) and "fail" in item:
                steps.append(Fail(str(item["fail"])))
            elif isinstance(item, dict) and "raise" in item:
                steps.append(RuntimeError(str(item["raise"])))
            else:
                raise ValueError(f"unknown script step: {item!r}")
        return cls(steps, cycle=cycle or False)

    def describe(self) -> str:
        return f"script:{len(self._steps)} steps"

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        if prompt.kind == "warmup":
            return Answer(raw=_WARMUP_REPLY)
        with self._lock:
            self.calls.append((prompt.id, tuple(short_sha(im) for im in images)))
            if self._next < len(self._steps):
                step: Step | None = self._steps[self._next]
                self._next += 1
                if self._cycle and self._next >= len(self._steps):
                    self._next = 0
            else:
                step = None
        t0 = time.monotonic()
        if step is None:
            return Answer(raw=_abstain(prompt))
        if isinstance(step, Sleep):
            time.sleep(step.seconds)
            return Answer(raw=step.then, latency_s=round(time.monotonic() - t0, 3))
        if isinstance(step, Fail):
            return Answer(error=step.error)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return Answer(raw=str(step(prompt, images)))
        return Answer(raw=str(step))


class RandomAdvisor:
    """Seeded noise: the same answer for the same (seed, prompt id, images), uniform over the vocabulary.

    It goes through the same both-orders rule as a real model, so it is the floor a model must beat."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = int(seed)

    def describe(self) -> str:
        return f"random:{self.seed}"

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        if prompt.kind == "warmup":
            return Answer(raw=_WARMUP_REPLY)
        key = "|".join([str(self.seed), prompt.id, *(hashlib.sha256(im).hexdigest() for im in images)])
        rng = random.Random(hashlib.sha256(key.encode("utf-8")).hexdigest())
        if prompt.kind == "flags":
            code = rng.choice([*ISSUES, "none"])
            flags = [] if code == "none" else [code]
            return Answer(raw=json.dumps({"flags": flags, "worst": code}))
        return Answer(raw=rng.choice([prompt.labels[0], prompt.labels[1], "SAME"]))


class ReplayAdvisor:
    """Answers from recorded verdicts: JSONL lines {"key": replay_key(kind, images), "raw": "A"}.

    Keys are the raw render bytes, so a verdict recorded on the exact finalists the engine renders
    replays through the engine unchanged, but only on the machine, renderer build and settings
    (`--image-max`, drop policy, epsilon and `top_k`) that produced those renders. A question with
    no record gets an abstention marked "replay_miss" in its answer's note, which traces and
    `tools.bench --agent` report, so a dataset that matches nothing is never mistaken for a
    model that keeps everything."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._answers: dict[str, str] = {}
        for line in self.path.read_text().splitlines():
            if line.strip():
                row: dict[str, Any] = json.loads(line)
                self._answers[str(row["key"])] = str(row["raw"])
        self.hits = 0
        self.misses = 0

    def describe(self) -> str:
        return f"replay:{self.path.name}:{len(self._answers)}"

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        if prompt.kind == "warmup":
            return Answer(raw=_WARMUP_REPLY)
        raw = self._answers.get(replay_key(prompt.kind, images))
        if raw is None:
            self.misses += 1
            return Answer(raw=_abstain(prompt), note="replay_miss")
        self.hits += 1
        return Answer(raw=raw, note="replay_hit")
