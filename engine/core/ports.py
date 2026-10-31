from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from engine.core.types import Box

# =============================================================================
# Module Overview
# =============================================================================
# The interfaces the engine core uses to reach the outside world. Stage one
# implements `TextMeasurer` and `AssetProbe` with the official renderer's fonts
# and the brand-kit files; the Express add-on will implement them from Express.
# `Renderer` and `Advisor` serve only the optional model harness
# (`engine/core/agent.py`): a renderer draws finished designs, and an advisor
# puts one enumerated question to a model and returns its raw answer.


@dataclass(frozen=True)
class TextLayout:
    """How a string wraps at a given size and width."""

    lines: tuple[str, ...]
    widths: tuple[float, ...]
    longest_word: float

    @property
    def max_width(self) -> float:
        return max(self.widths, default=0.0)


class TextMeasurer(Protocol):
    """Wraps and measures text exactly the way the target renderer draws it."""

    def layout(
        self, text: str, family: str, bold: bool, italic: bool, size: int, max_width: float
    ) -> TextLayout: ...

    def ink(self, line: str, family: str, bold: bool, italic: bool, size: int) -> tuple[float, float]:
        """Top and bottom of the glyphs drawn for one line, measured down from the line's top.

        The renderer puts each baseline one ascent below the line top, so descenders can hang
        below the line box; a mark placed by the box alone runs into them."""
        ...

    def word_gaps(
        self, text: str, family: str, bold: bool, italic: bool, size: int
    ) -> tuple[tuple[float, float], ...]:
        """How each word space in `text` draws on one line, in pixels: the clear gap it leaves
        between ink, and the widest gap between letters in the words either side of it.

        A renderer that hints glyphs to whole pixels can leave a word space no wider than the gaps
        inside words; one that does not can answer from advances and side bearings."""
        ...


class AssetProbe(Protocol):
    """Answers questions about image assets without the core touching files."""

    def exists(self, asset_id: str) -> bool: ...

    def visible_box(self, asset_id: str) -> Box | None:
        """Visible-pixel bounds as fractions of the image, or `None` if unknown."""
        ...

    def aspect(self, asset_id: str) -> float | None:
        """Pixel width over height of the image, or `None` if unknown."""
        ...

    def busyness(self, asset_id: str, region: Box) -> float | None:
        """Share of `region`, in image fractions, covered by fine detail such as baked-in dots, or `None`."""
        ...


class Renderer(Protocol):
    """Draws a finished design exactly as the target renderer will. PNG bytes are opaque to the core.

    Stage one: the official renderer. No Express implementation exists: the finale runs the engine
    as a web service the add-on calls, and only the add-on can draw a page in Express. `choose`
    renders synchronously, mid-resize, so in the add-on the harness stays off unless a protocol is
    built in which the service returns finalists and the add-on renders them
    (`docs/agentic-harness.md`, section 9)."""

    def render(self, design: dict[str, Any], max_size: int) -> bytes: ...


@dataclass(frozen=True)
class Prompt:
    """One enumerated question. `parts` interleaves text with indexes into the images tuple."""

    id: str  # "p1" | "p1-json" | "p1-noorig" | "p1-num" | "p2a" | "warmup"
    kind: str  # "compare" | "flags" | "warmup"
    system: str
    parts: tuple[str | int, ...]
    max_tokens: int
    labels: tuple[str, str] = ("A", "B")  # what the model is shown for the two candidates
    json_schema: Mapping[str, Any] | None = None  # sent only when CHAMELEON_AGENT_CONSTRAIN=json_schema
    grammar: str | None = None  # GBNF, sent only when CHAMELEON_AGENT_CONSTRAIN=grammar

    @property
    def images_used(self) -> tuple[int, ...]:
        """Indexes of the images the parts refer to, in order of first use; adapters send only these."""
        seen: list[int] = []
        for part in self.parts:
            if isinstance(part, int) and part not in seen:
                seen.append(part)
        return tuple(seen)


@dataclass(frozen=True)
class Answer:
    """A model's raw reply to one prompt; the core parses it, never the adapter."""

    raw: str = ""  # model text, truncated to 2000 characters
    probs: Mapping[str, float] | None = None  # first-token label probabilities, when the backend has logprobs
    latency_s: float = 0.0
    error: str | None = None  # "timeout" | "connection" | "http_<code>" | "bad_response" | "busy" | "exception:<Type>"
    note: str | None = None  # adapter remark kept in the trace, e.g. "replay_hit" or "replay_miss"


class Advisor(Protocol):
    """A model behind an enumerated question. Adapters serialize; the core parses and decides."""

    def describe(self) -> str:
        """A short label for logs and traces, e.g. "http:qwen3.5:0.8b@127.0.0.1:11434"."""
        ...

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer: ...
