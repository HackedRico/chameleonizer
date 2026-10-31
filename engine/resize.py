"""
THIS IS THE FILE YOU IMPLEMENT.

Everything else in this repo (input loading, output writing, rendering,
the Dockerfile) is plumbing so you can focus on exactly one function:
`resize()`. See RULES.md and the design schema for the full contract.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from engine.adapters.stage_one import default_ports
from engine.core.finalize import emergency_layout, fallback_layout
from engine.core.pipeline import resize_design

# =============================================================================
# Module Overview
# =============================================================================
# Stage-one entry point the scorer calls. `resize` wires the engine core to
# the brand-kit fonts and assets, and falls back to a letterboxed layout if
# anything raises, because a crash scores nothing and a valid layout scores something.
# An optional model harness (`docs/agentic-harness.md`) runs only when
# `CHAMELEON_AGENT` names a backend; unset, nothing of it is even imported.

logger = logging.getLogger(__name__)

# Repo root, so brand-kit paths resolve the same under `python -m engine.main` and in Docker.
_ROOT = Path(__file__).resolve().parents[1]
# "none" keeps every element visible, as the organizers' October 22 recap asks. "occlude" hides
# missing assets and, on strips, low-priority units, and stays opt-in for experiments.
_POLICY = os.environ.get("CHAMELEON_DROP_POLICY", "none")
# Values of `CHAMELEON_AGENT` that leave the model harness off, as does leaving it unset.
_AGENT_OFF = frozenset({"", "0", "off", "false", "no", "none"})


@lru_cache(maxsize=1)
def _ports():
    return default_ports(_ROOT)


@lru_cache(maxsize=1)
def _agent():
    """The configured model harness, or None (the default). Never raises."""
    if os.environ.get("CHAMELEON_AGENT", "").strip().lower() in _AGENT_OFF:
        return None  # nothing below is imported on the default path
    try:
        from engine.adapters.agent_config import load_agent_config
        from engine.adapters.harness import build_harness

        cfg = load_agent_config(os.environ)
        if cfg is not None and cfg.backend == "llamacpp":
            # Loading a model in this process has no deadline, and running out of memory would kill
            # the scorer's process; the in-process backend is for tools only. Use `http`.
            logger.warning("[resize] CHAMELEON_AGENT=llamacpp is for tools only; running without a model.")
            return None
        return build_harness(cfg, _ROOT) if cfg is not None else None
    except Exception as exc:  # a broken optional helper must never cost the layout
        logger.warning("[resize] Model harness unavailable; running without it. Reason: %r", exc)
        return None


def resize(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    """Return `source` re-laid-out for `target_canvas`, never raising."""
    try:
        measurer, probe = _ports()
        harness = _agent()
        if harness is None:
            return resize_design(source, target_canvas, measurer, probe, _POLICY)
        from engine.adapters.harness import agent_resize

        return agent_resize(source, target_canvas, measurer, probe, _POLICY, harness)[0]
    except Exception as exc:  # integration boundary: any failure degrades to the letterbox
        logger.warning("[resize] Engine failed; falling back to letterbox. Reason: %r", exc)
        try:
            return fallback_layout(source, target_canvas)
        except Exception as exc2:  # malformed input can break the letterbox too; never raise
            logger.warning("[resize] Letterbox failed too; using the emergency layout. Reason: %r", exc2)
            return emergency_layout(source, target_canvas)
