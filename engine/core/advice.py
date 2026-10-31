from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from engine.core.ports import Prompt

# =============================================================================
# Module Overview
# =============================================================================
# What the optional model harness may ask and how it reads the answers. The
# vocabularies are fixed: a comparison is answered with A, B or SAME, and a
# review with issue codes from `ISSUES`. `prompt` builds each registered
# question, `parse_verdict` and `parse_flags` turn raw model text into those
# vocabularies or None (an abstention), and `resolve` combines the two image
# orders of one comparison. Pure: no files, no network, no randomness.

# Bumped whenever a prompt's wording changes, so traces and evaluation runs say which text was asked.
PROMPT_VERSION = "2026-10-30.1"
VERDICTS = ("A", "B", "SAME")
# Priority order: when a model flags several issues without naming the worst, the first one acts.
ISSUES = ("text_overlap", "tiny_text", "cut_off", "logo_small", "logo_large", "weak_headline",
          "crowded", "empty_space", "busy_background", "lost_look", "distorted_image")
# What each code means, as shown to the model.
ISSUE_MEANINGS = {
    "text_overlap": "text over other text, or over a picture or logo",
    "tiny_text": "text too small to read, or words run together",
    "cut_off": "text or picture cut off by the edge",
    "logo_small": "logo missing, tiny or unreadable",
    "logo_large": "logo out of proportion",
    "weak_headline": "the headline no longer stands out",
    "crowded": "elements jammed together or against the edges",
    "empty_space": "large empty areas, or content off-centre",
    "busy_background": "text on a busy part of a picture",
    "lost_look": "no longer looks like the original",
    "distorted_image": "a picture stretched or squashed",
}
# The critic penalties (`engine/core/critic.py`) that measure each issue. A flag acts only through
# them: the harness proposes the finalist whose penalties for the flagged issue are lowest. An issue
# with no keys is an alarm only; the hard rules already guarantee image aspect.
ISSUE_PENALTIES: dict[str, tuple[str, ...]] = {
    "text_overlap": ("collision", "cut_off", "text_overflow"),
    "tiny_text": ("small_text", "collapsed_spaces"),
    "cut_off": ("cut_off", "text_overflow"),
    "logo_small": ("logo_small",),
    "logo_large": ("logo_inflated",),
    "weak_headline": ("hierarchy", "weak_headline", "headline_lines", "headline_rewrap"),
    "crowded": ("edge_crowding", "hero_clutter"),
    "empty_space": ("empty", "off_center", "hero_small"),
    "busy_background": ("busy_backdrop", "hero_clutter"),
    "lost_look": ("flipped_sides", "hierarchy"),
    "distorted_image": (),
}

# =============================================================================
# Prompt registry
# =============================================================================

SYSTEM = "You are a strict graphic design reviewer."
_RUBRIC = ("Which resize would a designer ship? Worst problems first: 1. text over text, or text over a "
           "picture or logo; 2. text too small or cut off; 3. logo or product missing, tiny, squashed or cut; "
           "4. messy alignment, uneven spacing, big empty areas; 5. lost the original's look or hierarchy.")
_WINNER_SCHEMA = {"type": "object", "properties": {"winner": {"enum": list(VERDICTS)}},
                  "required": ["winner"], "additionalProperties": False}
_FLAGS_SCHEMA = {
    "type": "object",
    "properties": {"flags": {"type": "array", "items": {"enum": list(ISSUES)}, "maxItems": 4},
                   "worst": {"enum": [*ISSUES, "none"]}},
    "required": ["flags", "worst"],
    "additionalProperties": False,
}
_CODE_RULE = "code ::= " + " | ".join(f'"\\"{c}\\""' for c in ISSUES)
_GRAMMARS = {
    "p1": 'root ::= "A" | "B" | "SAME"',
    "p1-json": 'root ::= "{\\"winner\\": \\"" ("A" | "B" | "SAME") "\\"}"',
    "p1-noorig": 'root ::= "A" | "B" | "SAME"',
    "p1-num": 'root ::= "1" | "2" | "SAME"',
    # At most four flags, as the JSON schema allows.
    "p2a": ('root ::= "{\\"flags\\": [" (code ("," code)? ("," code)? ("," code)?)? "], \\"worst\\": " '
            '(code | "\\"none\\"") "}"\n' + _CODE_RULE),
}
COMPARE_PROMPTS = ("p1", "p1-json", "p1-noorig", "p1-num")


def prompt(variant: str, width: int, height: int) -> Prompt:
    """The registered question `variant` for a `width` by `height` target; KeyError for unknown ids.

    The system text, "ORIGINAL design:" and the original image come first in every prompt that shows
    the original, so a server can reuse the cached prefix across both image orders."""
    size = f"{int(width)}x{int(height)}"
    if variant in ("p1", "p1-json", "p1-noorig", "p1-num"):
        labels = ("1", "2") if variant == "p1-num" else ("A", "B")
        if variant == "p1-json":
            ask = 'Reply with JSON only: {"winner": "A" | "B" | "SAME"}'
        else:
            ask = f"Answer with one word: {labels[0]}, {labels[1]} or SAME."
        head: tuple[str | int, ...] = () if variant == "p1-noorig" else ("ORIGINAL design:", 0)
        resized = f"Resized to {size}. Candidate {labels[0]}:"
        parts = (*head, resized, 1, f"Candidate {labels[1]}:", 2, f"{_RUBRIC} {ask}")
        json_variant = variant == "p1-json"
        return Prompt(variant, "compare", SYSTEM, parts, 16 if json_variant else 4, labels,
                      json_schema=_WINNER_SCHEMA if json_variant else None, grammar=_GRAMMARS.get(variant))
    if variant == "p2a":
        codes = "\n".join(f"- {code}: {ISSUE_MEANINGS[code]}" for code in ISSUES)
        text = (f"List the problems this resize has, using only these codes:\n{codes}\n"
                'Reply with JSON only: {"flags": [codes], "worst": code or "none"}')
        return Prompt("p2a", "flags", SYSTEM, ("ORIGINAL design:", 0, f"Resized to {size}:", 1, text), 64,
                      json_schema=_FLAGS_SCHEMA, grammar=_GRAMMARS["p2a"])
    if variant == "warmup":
        return Prompt("warmup", "warmup", SYSTEM, (0, "Reply with the single letter A."), 2, grammar=_GRAMMARS["p1"])
    raise KeyError(variant)


# =============================================================================
# Parsers
# =============================================================================

_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"
_FENCE = re.compile(r"```[A-Za-z]*")
_MARKER = re.compile(r"(?i)\b(answer|winner|verdict)\s*[:=]?\s*\"?(A|B|SAME|TIE|1|2)\b")
_SAME_WORDS = {"SAME", "TIE", "EQUAL", "NEITHER", "BOTH"}
# Answers that name a candidate by its position among the images the model was sent: after the
# original, candidate A is image 2; in a prompt without the original (`p1-noorig`) it is image 1.
_POSITIONAL = {True: {"IMAGE 2": "A", "IMAGE 3": "B"}, False: {"IMAGE 1": "A", "IMAGE 2": "B"}}
# Deepest JSON nesting read from an answer; a deeper object is not an answer and would exhaust the
# recursion limit in `json.loads`.
_MAX_DEPTH = 32


def _strip(raw: str) -> str:
    """`raw` without reasoning blocks, code fences and surrounding whitespace.

    Text after the last closing think tag is the answer; an unclosed think tag leaves nothing."""
    text = raw or ""
    if _THINK_CLOSE in text:
        text = text.rsplit(_THINK_CLOSE, 1)[1]
    if _THINK_OPEN in text:
        return ""
    return _FENCE.sub("", text).strip()


def _first_object(text: str) -> Any:
    """The first balanced `{...}` in `text`, parsed as JSON, or None."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
            if depth > _MAX_DEPTH:
                return None
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (ValueError, RecursionError):
                    return None
    return None


def _verdict_word(word: str, labels: tuple[str, str], original_shown: bool = True) -> str | None:
    """A, B or SAME for one answer word, or None."""
    w = " ".join(str(word).upper().split())
    if w in _SAME_WORDS:
        return "SAME"
    if w.startswith("CANDIDATE "):
        w = w[len("CANDIDATE "):]
    positional = _POSITIONAL[bool(original_shown)]
    if w in positional:
        return positional[w]
    if w == labels[0].upper():
        return "A"
    if w == labels[1].upper():
        return "B"
    return None


def parse_verdict(raw: str, labels: tuple[str, str] = ("A", "B"), original_shown: bool = True) -> str | None:
    """"A", "B" or "SAME" from a comparison answer, or None when it does not say one clearly.

    `original_shown` says whether the original was sent as the first image, which decides what
    "Image 2" means. Never raises: an answer that cannot be read is None."""
    try:
        return _parse_verdict(raw, labels, original_shown)
    except Exception:  # a parser that raises would skip the caller's bookkeeping
        return None


def _parse_verdict(raw: str, labels: tuple[str, str], original_shown: bool) -> str | None:
    text = _strip(raw)
    if not text:
        return None
    obj = _first_object(text)
    if isinstance(obj, dict):
        for key in ("winner", "answer"):
            if isinstance(obj.get(key), (str, int)):
                verdict = _verdict_word(str(obj[key]), labels, original_shown)
                if verdict is not None:
                    return verdict
                break
    marked = _MARKER.findall(text)
    if marked:
        verdict = _verdict_word(marked[-1][1], labels, original_shown)
        if verdict is not None:
            return verdict
    if text.startswith("{"):
        return None  # JSON without a usable winner; its keys and brackets are not a bare answer
    # The whole answer on its own: "B", " b.", "**SAME**", "Candidate A".
    bare = " ".join(re.sub(r"[^\w\s]", " ", text).split())
    return _verdict_word(bare, labels, original_shown) if bare else None


def shows_original(p: Prompt) -> bool:
    """Whether a prompt sends the original design as its first image."""
    return bool(p.images_used) and p.images_used[0] == 0


def parse_flags(raw: str) -> tuple[tuple[str, ...], str] | None:
    """(flags, worst) from a review answer, where worst is a code or "none"; None when unusable.

    Unknown codes are dropped. When `worst` is missing or "none" but flags were given, the
    highest-priority flag is the worst. Never raises."""
    try:
        return _parse_flags(raw)
    except Exception:  # a parser that raises would skip the caller's bookkeeping
        return None


def _parse_flags(raw: str) -> tuple[tuple[str, ...], str] | None:
    text = _strip(raw)
    if not text:
        return None
    obj = _first_object(text)
    if isinstance(obj, dict):
        if "flags" not in obj and "worst" not in obj:
            return None
        given = obj.get("flags", [])
        given = [given] if isinstance(given, str) else given if isinstance(given, list) else []
        flags = _known(given)
        worst = obj.get("worst")
        worst = worst if isinstance(worst, str) and (worst in ISSUES or worst == "none") else None
        if worst is None and not flags and (given or obj.get("worst") not in (None, "")):
            return None  # only unknown codes: nothing usable was said
        return _settled(flags, worst)
    if text.startswith("{"):
        return None
    tokens = [t for t in re.split(r"[,\s]+", re.sub(r"[\[\]\"']", " ", text)) if t]
    if tokens and all(t in ISSUES or t == "none" for t in tokens):
        return _settled(_known(tokens), None)
    return None


def _known(codes: list[Any]) -> tuple[str, ...]:
    """Known issue codes from `codes`, in order and without duplicates."""
    out: list[str] = []
    for code in codes:
        if isinstance(code, str) and code in ISSUES and code not in out:
            out.append(code)
    return tuple(out)


def _settled(flags: tuple[str, ...], worst: str | None) -> tuple[tuple[str, ...], str]:
    if (worst is None or worst == "none") and flags:
        worst = min(flags, key=ISSUES.index)
    return flags, worst or "none"


def resolve(first: str | None, second: str | None) -> str:
    """Combine the two image orders of one comparison.

    `first` is the answer with the incumbent shown as A, `second` with the challenger shown as A.
    A verdict that flips with the order is position bias, so only a consistent one counts. This is
    `tools/judge.resolve_pair`, ported because `tools/` is not in the image."""
    if first == "B" and second == "A":
        return "challenger"
    if first == "A" and second == "B":
        return "incumbent"
    return "tie"


def replay_key(kind: str, images: tuple[bytes, ...]) -> str:
    """A stable key for one question about these exact images, for replaying recorded answers."""
    h = hashlib.sha256(kind.encode("utf-8"))
    for image in images:
        h.update(hashlib.sha256(image).hexdigest().encode("ascii"))
    return h.hexdigest()


def short_sha(png: bytes) -> str:
    """First 16 hex characters of an image's sha256, as traces show it."""
    return hashlib.sha256(png).hexdigest()[:16]
