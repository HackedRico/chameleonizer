from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from engine.core.advice import (COMPARE_PROMPTS, ISSUE_PENALTIES, ISSUES, PROMPT_VERSION, VERDICTS, parse_flags,
                                parse_verdict, prompt, replay_key, resolve, short_sha, shows_original)

# =============================================================================
# Module Overview
# =============================================================================
# The harness's vocabularies and parsers: raw model text in, an enumerated
# verdict or issue list out, or None (an abstention). Also the both-orders
# rule, replay keys and the prompt registry.

ROOT = Path(__file__).resolve().parents[1]
NUM = ("1", "2")


@pytest.mark.parametrize("raw, labels, want", [
    ("A", ("A", "B"), "A"),
    (" b.", ("A", "B"), "B"),
    ("SAME", ("A", "B"), "SAME"),
    ("tie", ("A", "B"), "SAME"),
    ("Neither", ("A", "B"), "SAME"),
    ("**B**", ("A", "B"), "B"),
    ("Candidate A.", ("A", "B"), "A"),
    ("Image 3", ("A", "B"), "B"),
    ("<think>A has overlaps, so B</think>B", ("A", "B"), "B"),
    ("<think>A looks better because", ("A", "B"), None),
    ("reasoning without an opening tag</think> A", ("A", "B"), "A"),
    ('```json {"winner":"A"}```', ("A", "B"), "A"),
    ('{"winner": "SAME"}', ("A", "B"), "SAME"),
    ('{"answer": "b"}', ("A", "B"), "B"),
    ('Sure! {"winner": "B", "reason": "A has text over text"}', ("A", "B"), "B"),
    ("Answer: B", ("A", "B"), "B"),
    ("I compared both. Verdict = A", ("A", "B"), "A"),
    ("A or B", ("A", "B"), None),
    ("Both A and B have problems, but B is cleaner", ("A", "B"), None),
    ("", ("A", "B"), None),
    ("C", ("A", "B"), None),
    ("1", NUM, "A"),
    ("2.", NUM, "B"),
    ("Candidate 2", NUM, "B"),
    ("SAME", NUM, "SAME"),
    ("1", ("A", "B"), None),
    ("A", NUM, None),
])
def test_parse_verdict(raw, labels, want):
    assert parse_verdict(raw, labels) == want


@pytest.mark.parametrize("raw, want", [
    ('{"flags": ["text_overlap", "tiny_text"], "worst": "text_overlap"}',
     (("text_overlap", "tiny_text"), "text_overlap")),
    ('{"flags": ["crowded", "made_up", "crowded"], "worst": "crowded"}', (("crowded",), "crowded")),
    ('{"flags": ["empty_space", "cut_off"], "worst": "none"}', (("empty_space", "cut_off"), "cut_off")),
    ('{"flags": ["tiny_text"]}', (("tiny_text",), "tiny_text")),
    ('{"flags": ["logo_small"], "worst": "bogus"}', (("logo_small",), "logo_small")),
    ('{"flags": [], "worst": "none"}', ((), "none")),
    ('{"flags": [], "worst": "lost_look"}', ((), "lost_look")),
    ('```json\n{"flags": ["busy_background"], "worst": "busy_background"}\n```',
     (("busy_background",), "busy_background")),
    ('<think>hmm</think>{"flags": ["cut_off"], "worst": "cut_off"}', (("cut_off",), "cut_off")),
    ('{"flags": ["made_up"], "worst": "also_made_up"}', None),
    ('{"verdict": "A"}', None),
    ("crowded, tiny_text", (("crowded", "tiny_text"), "tiny_text")),
    ("none", ((), "none")),
    ('["cut_off"]', (("cut_off",), "cut_off")),
    ("the text overlaps", None),
    ("{not json", None),
    ("", None),
])
def test_parse_flags(raw, want):
    assert parse_flags(raw) == want


@pytest.mark.parametrize("first, second, want", [
    ("B", "A", "challenger"),
    ("A", "B", "incumbent"),
    ("B", "B", "tie"),
    ("A", "A", "tie"),
    ("SAME", "A", "tie"),
    ("B", "SAME", "tie"),
    ("B", None, "tie"),
    (None, None, "tie"),
])
def test_resolve_truth_table(first, second, want):
    assert resolve(first, second) == want


def test_replay_key_is_stable_and_order_sensitive():
    a, b, c = b"orig", b"one", b"two"
    assert replay_key("compare", (a, b, c)) == replay_key("compare", (a, b, c))
    assert replay_key("compare", (a, b, c)) != replay_key("compare", (a, c, b))
    assert replay_key("compare", (a, b)) != replay_key("flags", (a, b))
    assert re.fullmatch(r"[0-9a-f]{64}", replay_key("compare", (a, b, c)))
    # The documented scheme, pinned, so recorded replay files stay valid unless it changes on purpose.
    scheme = hashlib.sha256(b"compare" + b"".join(hashlib.sha256(x).hexdigest().encode() for x in (a, b, c)))
    assert replay_key("compare", (a, b, c)) == scheme.hexdigest() == (
        "c1196bbb3e6694cc9473408e00a3d54d69464d6bbe5eff649aed9bfcaebc46e0")
    assert len(short_sha(a)) == 16


@pytest.mark.parametrize("variant", [*COMPARE_PROMPTS, "p2a", "warmup"])
def test_prompt_parts_reference_valid_images(variant):
    p = prompt(variant, 728, 90)
    n_images = {"compare": 3, "flags": 2, "warmup": 1}[p.kind]
    refs = [x for x in p.parts if isinstance(x, int)]
    assert refs and all(0 <= i < n_images for i in refs)
    assert len(set(refs)) == len(refs)
    assert all(isinstance(x, str) and x for x in p.parts if not isinstance(x, int))
    assert p.system and p.max_tokens > 0
    if p.kind != "warmup":
        assert any("728x90" in x for x in p.parts if isinstance(x, str))


def test_prompt_registry_shapes():
    p1 = prompt("p1", 300, 250)
    assert p1.parts[:2] == ("ORIGINAL design:", 0) and p1.parts[-1].endswith("Answer with one word: A, B or SAME.")
    assert p1.grammar == 'root ::= "A" | "B" | "SAME"' and p1.max_tokens == 4
    pj = prompt("p1-json", 300, 250)
    assert pj.json_schema["properties"]["winner"]["enum"] == list(VERDICTS) and pj.max_tokens == 16
    assert 0 not in prompt("p1-noorig", 300, 250).parts
    assert prompt("p1-noorig", 300, 250).grammar == p1.grammar  # the ablation drops the original, not the grammar
    assert prompt("p1-num", 300, 250).labels == ("1", "2")
    p2 = prompt("p2a", 300, 250)
    assert p2.kind == "flags" and p2.json_schema["properties"]["flags"]["maxItems"] == 4
    assert all(code in p2.parts[-1] for code in ISSUES)
    with pytest.raises(KeyError):
        prompt("p9", 300, 250)
    assert PROMPT_VERSION


def test_issue_penalties_are_real_critic_keys():
    critic = (ROOT / "engine" / "core" / "critic.py").read_text()
    keys = set(re.findall(r'add\("([a-z_]+)"', critic))
    assert set(ISSUE_PENALTIES) == set(ISSUES)
    for code, penalty_keys in ISSUE_PENALTIES.items():
        assert set(penalty_keys) <= keys, code
    assert ISSUE_PENALTIES["distorted_image"] == ()


@pytest.mark.parametrize("raw, shown, want", [
    ("Image 2", True, "A"), ("Image 3", True, "B"), ("Image 1", True, None),
    ("Image 1", False, "A"), ("Image 2", False, "B"), ("Image 3", False, None),
])
def test_positions_count_among_the_images_sent(raw, shown, want):
    assert parse_verdict(raw, ("A", "B"), original_shown=shown) == want


def test_which_prompts_show_the_original():
    assert [shows_original(prompt(v, 320, 50)) for v in COMPARE_PROMPTS] == [True, True, False, True]
    assert shows_original(prompt("p2a", 320, 50))


@pytest.mark.parametrize("raw", [
    ('{"a":' + "[" * 997 + "]" * 997 + "}")[:2000],
    '{"winner": ' + "{" * 40 + "}" * 40 + "}",
    "{" * 1999,
])
def test_parsers_are_total_on_pathological_json(raw):
    assert parse_verdict(raw) is None
    assert parse_flags(raw) is None


def test_nesting_within_the_limit_still_parses():
    assert parse_verdict('{"meta": {"a": [[1]]}, "winner": "B"}') == "B"
    assert parse_flags('{"flags": [["crowded"]], "worst": "crowded"}') == ((), "crowded")
