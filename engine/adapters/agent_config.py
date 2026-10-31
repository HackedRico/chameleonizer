from __future__ import annotations

import json
import logging
import math
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.core.advice import COMPARE_PROMPTS
from engine.core.agent import SLOTS, AgentSettings

logger = logging.getLogger(__name__)

# =============================================================================
# Module Overview
# =============================================================================
# Reads the optional model harness's configuration from `CHAMELEON_AGENT*`
# environment variables, which the shell or `docker run -e` sets, so neither
# `engine/main.py` nor the Dockerfile changes. Unset or an off value means no
# harness. Anything invalid also means no harness, with one warning: a typo
# must never change what the engine ships. `docs/agentic-harness.md` has the
# full table.

PREFIX = "CHAMELEON_AGENT"
OFF_VALUES = frozenset({"", "0", "off", "false", "no", "none"})
BACKENDS = ("null", "script", "random", "replay", "http", "llamacpp")
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUE, _FALSE = frozenset({"1", "true", "yes", "on"}), frozenset({"0", "false", "no", "off", ""})


@dataclass(frozen=True)
class AgentConfig:
    """Everything needed to build one harness: the backend, its options and the loop settings."""

    backend: str  # one of BACKENDS
    settings: AgentSettings = field(default_factory=AgentSettings)
    warmup_s: float = 30.0
    url: str = "http://127.0.0.1:11434/v1"
    model: str = ""
    api_key: str = ""
    constrain: str = "none"
    logprobs: bool = False
    image_form: str = "object"
    extra_json: str = "{}"
    seed: int = 0
    allow_remote: bool = False
    image_min: int = 224
    gguf: str = ""
    mmproj: str = ""
    chat_handler: str = "MTMDChatHandler"
    threads: int = 4
    n_ctx: int = 8192
    script: str = ""
    replay: str = ""
    trace: str = ""

    @property
    def extra(self) -> dict[str, Any]:
        return json.loads(self.extra_json)


class _Invalid(ValueError):
    """One bad configuration value."""


def load_agent_config(env: Mapping[str, str]) -> AgentConfig | None:
    """The configured harness, or None when it is off.

    Any invalid value (unknown backend, bad number, out-of-range, malformed EXTRA JSON, non-loopback
    URL without ALLOW_REMOTE, missing required file) logs one warning and returns None."""
    backend = env.get(PREFIX, "").strip().lower()
    if backend in OFF_VALUES:
        return None
    try:
        return _load(backend, env)
    except _Invalid as exc:
        logger.warning("[agent] Model harness configuration invalid; running without it. %s", exc)
        return None


def _load(backend: str, env: Mapping[str, str]) -> AgentConfig:
    if backend not in BACKENDS:
        raise _Invalid(f"{PREFIX}={backend!r} is not one of {', '.join(BACKENDS)}")

    def get(name: str, default: str) -> str:
        return env.get(f"{PREFIX}_{name}", default).strip()

    def num(name: str, default: float, lo: float, hi: float, cast: Callable[[str], Any] = float) -> Any:
        raw = get(name, "")
        if raw == "":
            return default
        try:
            value = cast(raw)
        except ValueError:
            raise _Invalid(f"{PREFIX}_{name}={raw!r} is not a number") from None
        if not (isinstance(value, int) or math.isfinite(value)) or not lo <= value <= hi:
            raise _Invalid(f"{PREFIX}_{name}={raw!r} is outside {lo:g} to {hi:g}")
        return value

    def choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
        value = get(name, default).lower() or default
        if value not in allowed:
            raise _Invalid(f"{PREFIX}_{name}={value!r} is not one of {', '.join(allowed)}")
        return value

    def flag(name: str) -> bool:
        value = get(name, "0").lower()
        if value not in _TRUE | _FALSE:
            raise _Invalid(f"{PREFIX}_{name}={value!r} is not 0 or 1")
        return value in _TRUE

    def existing(name: str, required: bool) -> str:
        value = get(name, "")
        if required and not value:
            raise _Invalid(f"{PREFIX}={backend} needs {PREFIX}_{name}")
        if value and not Path(value).is_file():
            raise _Invalid(f"{PREFIX}_{name}={value!r} is not a file")
        return value

    settings = AgentSettings(
        slots=choice("SLOTS", "tiebreak", SLOTS),
        top_k=num("TOP_K", 2, 2, 4, int),
        epsilon=num("EPSILON", 3.0, 0.0, 20.0),
        flag_delta=num("FLAG_DELTA", 0.25, 0.0, 10.0),
        call_timeout_s=num("TIMEOUT_S", 5.0, 0.1, 120.0),
        budget_s=num("BUDGET_S", 10.0, 0.5, 600.0),
        drain_s=num("DRAIN_S", 30.0, 0.0, 600.0),
        image_max=num("IMAGE_MAX", 1024, 256, 2048, int),
        prompt=choice("PROMPT", "p1", COMPARE_PROMPTS),
    )
    if settings.budget_s < settings.call_timeout_s:
        # Not invalid, but a call starts only with its whole timeout left, so none ever would.
        logger.warning("[agent] %s_BUDGET_S (%g) is below %s_TIMEOUT_S (%g): no model call can start. "
                       "Allow two calls plus renders, e.g. a budget of 2 x timeout + 2 s.",
                       PREFIX, settings.budget_s, PREFIX, settings.call_timeout_s)
    extra_json = get("EXTRA", "{}") or "{}"
    try:
        extra = json.loads(extra_json)
    except ValueError:
        raise _Invalid(f"{PREFIX}_EXTRA is not valid JSON") from None
    if not isinstance(extra, dict):
        raise _Invalid(f"{PREFIX}_EXTRA must be a JSON object")
    url = get("URL", "http://127.0.0.1:11434/v1")
    allow_remote = flag("ALLOW_REMOTE")
    if backend == "http":
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise _Invalid(f"{PREFIX}_URL={url!r} is not an http(s) URL")
        if parts.hostname not in LOOPBACK and not allow_remote:
            raise _Invalid(f"{PREFIX}_URL host {parts.hostname!r} is not loopback; set {PREFIX}_ALLOW_REMOTE=1")
        try:
            parts.port  # noqa: B018 - raises on a malformed port
        except ValueError:
            raise _Invalid(f"{PREFIX}_URL={url!r} has a bad port") from None
    return AgentConfig(
        backend=backend,
        settings=settings,
        warmup_s=num("WARMUP_S", 30.0, 0.0, 600.0),
        url=url,
        model=get("MODEL", ""),
        api_key=get("API_KEY", ""),
        constrain=choice("CONSTRAIN", "none", ("none", "grammar", "json_schema")),
        logprobs=flag("LOGPROBS"),
        image_form=choice("IMAGE_FORM", "object", ("object", "string")),
        extra_json=json.dumps(extra),
        seed=num("SEED", 0, -(2**31), 2**31 - 1, int),
        allow_remote=allow_remote,
        image_min=num("IMAGE_MIN", 224, 0, 512, int),
        gguf=existing("GGUF", backend == "llamacpp"),
        mmproj=existing("MMPROJ", backend == "llamacpp"),
        chat_handler=get("CHAT_HANDLER", "MTMDChatHandler") or "MTMDChatHandler",
        threads=num("THREADS", 4, 1, 256, int),
        n_ctx=num("CTX", 8192, 512, 262144, int),
        script=existing("SCRIPT", backend == "script"),
        replay=existing("REPLAY", backend == "replay"),
        trace=get("TRACE", ""),
    )
