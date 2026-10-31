from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.adapters.agent_config import AgentConfig
from engine.adapters.render import OfficialRenderer
from engine.adapters.vision import blank_png
from engine.core.advice import prompt
from engine.core.agent import AgentResult, AgentSettings, Breaker, call_with_deadline, choose, drain_abandoned
from engine.core.pipeline import Outcome, resize_with_report
from engine.core.ports import Advisor, AssetProbe, Renderer, TextMeasurer

logger = logging.getLogger(__name__)

# =============================================================================
# Module Overview
# =============================================================================
# Stage-one assembly of the optional model harness. `build_advisor` picks the
# backend named in the configuration, `build_harness` adds the official
# renderer, a circuit breaker and one warmup call, and `agent_resize` runs
# the pipeline with finalists and lets `engine/core/agent.choose` pick among
# them. Before the engine runs, any model call abandoned at its deadline is
# waited for, so a slow model never competes with the engine for the CPU.
# `engine/resize.py` imports this module only when a model is configured;
# nothing here runs on the default path.

# Join slack for the warmup call beyond its own timeout, as for every other call.
_WARMUP_GRACE_S = 0.25


@dataclass
class Harness:
    """A ready advisor plus everything `choose` needs, shared by every task in the process."""

    settings: AgentSettings
    advisor: Advisor
    renderer: Renderer
    breaker: Breaker
    trace_path: Path | None = None
    warmup_latency_s: float = 0.0

    def record(self, result: AgentResult) -> None:
        """Log one task's result and append its trace when a trace file is set; never raises."""
        try:
            logger.info("[agent] %s: finalist %d of %d, %d calls, %.2f s", result.reason, result.chosen,
                        len(result.trace.get("finalists", [])), result.calls, result.seconds)
            if self.trace_path is not None:
                self.trace_path.parent.mkdir(parents=True, exist_ok=True)
                with self.trace_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(result.trace, default=str) + "\n")
        except Exception as exc:  # a trace is a convenience; losing one must not lose the task
            logger.debug("[agent] Trace not written: %r", exc)


def build_advisor(cfg: AgentConfig) -> Advisor:
    """The advisor `cfg` names. Raises on failure; tools call it directly."""
    if cfg.backend == "null":
        from engine.adapters.scripted import NullAdvisor

        return NullAdvisor()
    if cfg.backend == "script":
        from engine.adapters.scripted import ScriptedAdvisor

        return ScriptedAdvisor.from_file(cfg.script)
    if cfg.backend == "random":
        from engine.adapters.scripted import RandomAdvisor

        return RandomAdvisor(cfg.seed)
    if cfg.backend == "replay":
        from engine.adapters.scripted import ReplayAdvisor

        return ReplayAdvisor(cfg.replay)
    if cfg.backend == "http":
        from engine.adapters.openai_http import OpenAIHTTPAdvisor

        return OpenAIHTTPAdvisor(cfg.url, cfg.model, cfg.api_key, cfg.constrain, cfg.logprobs, cfg.image_form,
                                 cfg.extra, cfg.seed, cfg.settings.image_max, cfg.image_min, cfg.allow_remote)
    if cfg.backend == "llamacpp":
        from engine.adapters.llama_inproc import LlamaCppAdvisor

        return LlamaCppAdvisor(cfg.gguf, cfg.mmproj, cfg.chat_handler, cfg.threads, cfg.n_ctx, cfg.seed,
                               cfg.constrain, cfg.settings.image_max, cfg.image_min)
    raise ValueError(f"unknown advisor backend {cfg.backend!r}")


def warm_up(advisor: Advisor, seconds: float) -> tuple[float, str | None]:
    """One tiny call that loads the model and its vision projector: (latency, error or None)."""
    warmup = prompt("warmup", 64, 64)
    image = (blank_png(),)
    answer = call_with_deadline(lambda: advisor.ask(warmup, image, seconds), seconds + _WARMUP_GRACE_S)
    return answer.latency_s, answer.error


def build_harness(cfg: AgentConfig, root: Path) -> Harness | None:
    """A ready harness, or None (the harness stays off for this process) on any failure."""
    try:
        advisor = build_advisor(cfg)
        renderer = OfficialRenderer(root)
        latency = 0.0
        if cfg.warmup_s > 0:
            latency, error = warm_up(advisor, cfg.warmup_s)
            if error:
                logger.warning("[agent] Advisor %s failed its warmup (%s); running without it.",
                               advisor.describe(), error)
                _drain(cfg.settings)
                return None
        logger.info("[agent] Model harness on: %s, warmup %.2f s.", advisor.describe(), latency)
        return Harness(cfg.settings, advisor, renderer, Breaker(), Path(cfg.trace) if cfg.trace else None, latency)
    except Exception as exc:  # a missing package, model file or server leaves the engine as it is
        logger.warning("[agent] Model harness unavailable; running without it. Reason: %r", exc)
        return None


def _drain(settings: AgentSettings) -> None:
    """Wait for model calls abandoned at their deadline, so they do not slow the engine down."""
    if not drain_abandoned(settings.drain_s):
        logger.warning("[agent] A timed-out model call is still running after %.0f s; the engine's own "
                       "time budget may now cut its candidate search short.", settings.drain_s)


def agent_resize(
    source: dict[str, Any],
    target: dict[str, Any],
    measurer: TextMeasurer,
    probe: AssetProbe,
    policy: str,
    harness: Harness,
) -> tuple[dict[str, Any], Outcome, AgentResult]:
    """The engine's outcome with finalists, and the finalist the harness chose to ship.

    Once the breaker is open no alternatives are finished. `resize_with_report` raises exactly as on
    the default path; `choose` never raises."""
    s = harness.settings
    _drain(s)
    alternatives = 0 if harness.breaker.is_open else s.top_k - 1
    outcome = resize_with_report(source, target, measurer, probe, policy, alternatives=alternatives,
                                 alt_budget_s=s.budget_s)
    result = choose(outcome, source, target, harness.advisor, harness.renderer, s, harness.breaker)
    harness.record(result)
    return result.design, outcome, result
