from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# =============================================================================
# Module Overview
# =============================================================================
# Development-time LLM judge, a stand-in for the hidden AI evaluator. `judge_run`
# scores one bench run against the brief's rubric (absolute 1 to 10 scores, for
# the leaderboard). `judge_pairs` compares two runs task by task, asking in both
# orders to cancel position bias, for accept-or-reject decisions. Development
# only: it needs Anthropic credentials and never ships in the Docker image.

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "claude-opus-5-5"
# USD per million tokens (input, output); fallback hops are priced as the requested model.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
# Running total across every judge run on this machine, so a session budget can be enforced.
# `JUDGE_LEDGER` and `CHAMELEON_ENV_FILE` let git worktrees share one ledger and one `.env`.
LEDGER = Path(os.environ.get("JUDGE_LEDGER", ROOT / "output" / "judge_spend.json"))
BUDGET_USD = float(os.environ.get("JUDGE_BUDGET_USD", "100"))
RUBRIC_KEYS = ("collisions", "readability", "brand", "intentional", "overall")

CONTEXT = """You are judging automatic design resizes for the Adobe Chameleon challenge.
A resize may change only element positions, sizes and font sizes; copy, fonts, colours and image
pixels are fixed, and inventing imagery is not allowed. The rubric:
- collisions: no unwanted overlaps. Intentional layering (decorative shapes, text on its own
  button) is fine; text over other text, images over text, or text on a busy area is not.
- readability: all text is large enough, has contrast, and nothing is cut off.
- brand: colours, fonts and the logo survive intact and in proportion; the logo is legible.
- intentional: things line up, spacing is even, the most important element still dominates,
  pictures keep their proportions. It should look like a designer made it."""

ABSOLUTE_PROMPT = CONTEXT + """

Image 1 is the original design. Image 2 is the same design re-laid-out for a new canvas size.
Score image 2 from 1 to 10 on each rubric row, using the original as the reference, plus
`overall`, your holistic score for how well image 2 serves as the design at this size.
List the concrete problems you see, most serious first. Be strict and consistent."""

PAIR_PROMPT = CONTEXT + """

Image 1 is the original design. Images 2 and 3 are two candidate resizes of it to the same new
canvas: candidate A is image 2, candidate B is image 3. Pick the candidate a professional
designer would ship, judging by the rubric. Answer "tie" only when they are genuinely equal."""

ABSOLUTE_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": "integer"} for k in RUBRIC_KEYS},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*RUBRIC_KEYS, "issues"],
    "additionalProperties": False,
}
PAIR_SCHEMA = {
    "type": "object",
    "properties": {"winner": {"type": "string", "enum": ["A", "B", "tie"]}, "reason": {"type": "string"}},
    "required": ["winner", "reason"],
    "additionalProperties": False,
}


# =============================================================================
# Shared plumbing
# =============================================================================


def load_local_env() -> None:
    """Load `ANTHROPIC_API_KEY` and friends from the repo's gitignored `.env`, if present."""
    from dotenv import load_dotenv  # dev-only dependency, see requirements-dev.txt

    # Variables already set in the shell win over the file.
    load_dotenv(os.environ.get("CHAMELEON_ENV_FILE", ROOT / ".env"), override=False)


def make_client() -> Any:
    """An Anthropic client from `.env`, failing early with a fix when credentials are missing."""
    import anthropic  # dev-only dependency; imported here so the bench runs without it

    load_local_env()
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("No Anthropic credentials: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.")
    if _spent() >= BUDGET_USD:
        raise SystemExit(f"Judge budget of ${BUDGET_USD:.0f} used up (see {LEDGER}); raise `JUDGE_BUDGET_USD`.")
    # Organization-level keys must name a workspace on every request; workspace-scoped keys need nothing.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _ask(client: Any, model: str, content: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    """One structured judge call: the parsed answer plus `cost_usd`, or an `error` entry."""
    import anthropic

    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            # Re-run a classifier decline on Anthropic's recommended model instead of losing the score.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.RateLimitError as exc:
        return {"error": f"rate limited: {exc.message}"}
    except anthropic.APIStatusError as exc:
        return {"error": f"API error {exc.status_code}: {exc.message}"}
    except anthropic.APIConnectionError:
        return {"error": "network error"}
    if response.stop_reason == "refusal":
        return {"error": "declined"}
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    u = response.usage
    cost = ((u.input_tokens + (u.cache_creation_input_tokens or 0)) * price_in
            + (u.cache_read_input_tokens or 0) * price_in * 0.1 + u.output_tokens * price_out) / 1e6
    text = next(b.text for b in response.content if b.type == "text")
    return {"model": response.model, "cost_usd": round(cost, 4), **json.loads(text)}


def _spent() -> float:
    return json.loads(LEDGER.read_text())["usd"] if LEDGER.exists() else 0.0


def _record_spend(usd: float) -> float:
    import fcntl

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    # Several judge processes may run at once; lock so concurrent runs never lose spend.
    with open(LEDGER.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        total = round(_spent() + usd, 4)
        LEDGER.write_text(json.dumps({"usd": total}))
    return total


def _rows(run: Path, limit: int | None) -> list[dict[str, Any]]:
    return json.loads((run / "results.json").read_text())["rows"][:limit]


def _parallel(fn, items: list[Any]) -> list[Any]:
    # Calls are independent and I/O bound; a small pool keeps a suite run to a few minutes.
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(fn, items))


# =============================================================================
# Absolute scores for one run
# =============================================================================


def judge_run(run: Path, model: str, limit: int | None) -> dict[str, Any]:
    """Score every task in `run` and write `judge.json` and `judge.md` beside it."""
    client = make_client()

    def one(row: dict[str, Any]) -> dict[str, Any]:
        renders = run / "renders"
        content = [
            _image_block(renders / f"{row['task']}_source.png"),
            _image_block(renders / f"{row['task']}_output.png"),
            {"type": "text", "text": f"{ABSOLUTE_PROMPT}\n\nTarget canvas: {row['target']} {row.get('target_name', '')}."},
        ]
        return {"task": row["task"], **_ask(client, model, content, ABSOLUTE_SCHEMA)}

    verdicts = _parallel(one, _rows(run, limit))
    scored = [v for v in verdicts if "error" not in v]
    means = {k: round(statistics.mean(v[k] for v in scored), 2) for k in RUBRIC_KEYS} if scored else {}
    run_cost = round(sum(v.get("cost_usd", 0.0) for v in verdicts), 4)
    summary = {"model": model, "judged": len(scored), "errors": len(verdicts) - len(scored), "means": means,
               "fails_le4": sum(v["overall"] <= 4 for v in scored), "cost_usd": run_cost,
               "total_spent_usd": _record_spend(run_cost)}
    (run / "judge.json").write_text(json.dumps({"summary": summary, "verdicts": verdicts}, indent=2))
    lines = [f"# LLM judge ({model})", "", f"- judged: {len(scored)}, errors: {summary['errors']}",
             f"- overall 4 or below: {summary['fails_le4']}", f"- cost: ${run_cost}"]
    lines += [f"- mean {k}: {v}" for k, v in means.items()]
    lines += ["", "| Task | Overall | Collisions | Readability | Brand | Intentional | Top issue |",
              "|---|---|---|---|---|---|---|"]
    for v in verdicts:
        if "error" in v:
            lines.append(f"| {v['task']} | error: {v['error']} | | | | | |")
            continue
        top = v["issues"][0] if v["issues"] else ""
        lines.append(f"| {v['task']} | {v['overall']} | {v['collisions']} | {v['readability']} | "
                     f"{v['brand']} | {v['intentional']} | {top} |")
    (run / "judge.md").write_text("\n".join(lines) + "\n")
    return summary


# =============================================================================
# Pairwise comparison of two runs
# =============================================================================


def resolve_pair(first: str, second: str) -> str:
    """Combine the two orderings: `first` names the winner with base shown as A, `second` with new as A.

    Returns "new", "base" or "tie". A verdict that flips with the order is position bias, so it is a tie."""
    as_new = {"A": "base", "B": "new", "tie": "tie"}[first]
    swapped = {"A": "new", "B": "base", "tie": "tie"}[second]
    return as_new if as_new == swapped else "tie"


def judge_pairs(base: Path, new: Path, model: str, limit: int | None) -> dict[str, Any]:
    """Compare `new` against `base` on every shared task; writes `pairwise.json` in `new`."""
    client = make_client()
    shared = {r["task"]: r for r in _rows(base, None)}
    rows = [r for r in _rows(new, limit) if r["task"] in shared]
    # Identical outputs are ties by definition; skipping them saves money and judge noise.
    same = [r["task"] for r in rows if (base / "renders" / f"{r['task']}_output.json").read_text()
            == (new / "renders" / f"{r['task']}_output.json").read_text()]
    rows = [r for r in rows if r["task"] not in same]

    def one(row: dict[str, Any]) -> dict[str, Any]:
        t = row["task"]
        src = _image_block(new / "renders" / f"{t}_source.png")
        a = _image_block(base / "renders" / f"{t}_output.png")
        b = _image_block(new / "renders" / f"{t}_output.png")
        text = {"type": "text", "text": f"{PAIR_PROMPT}\n\nTarget canvas: {row['target']} {row.get('target_name', '')}."}
        first = _ask(client, model, [src, a, b, text], PAIR_SCHEMA)
        second = _ask(client, model, [src, b, a, text], PAIR_SCHEMA)
        cost = first.get("cost_usd", 0.0) + second.get("cost_usd", 0.0)
        if "error" in first or "error" in second:
            return {"task": t, "error": first.get("error") or second.get("error"), "cost_usd": cost}
        return {"task": t, "result": resolve_pair(first["winner"], second["winner"]), "cost_usd": cost,
                "reasons": [first["reason"], second["reason"]]}

    verdicts = _parallel(one, rows) + [{"task": t, "result": "tie", "cost_usd": 0.0, "identical": True} for t in same]
    done = [v for v in verdicts if "error" not in v]
    tally = {k: sum(v["result"] == k for v in done) for k in ("new", "base", "tie")}
    run_cost = round(sum(v.get("cost_usd", 0.0) for v in verdicts), 4)
    summary = {"model": model, "base_run": str(base), "new_run": str(new), "compared": len(done),
               "errors": len(verdicts) - len(done), **tally, "cost_usd": run_cost,
               "total_spent_usd": _record_spend(run_cost)}
    (new / "pairwise.json").write_text(json.dumps({"summary": summary, "verdicts": verdicts}, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Score bench runs with Claude as the judge.")
    parser.add_argument("--run", type=Path, help="score one run with absolute rubric scores")
    parser.add_argument("--pair", nargs=2, type=Path, metavar=("BASE", "NEW"), help="compare two runs pairwise")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="judge only the first N tasks")
    args = parser.parse_args()
    if args.pair:
        print(json.dumps(judge_pairs(args.pair[0], args.pair[1], args.model, args.limit), indent=2))
    elif args.run:
        print(json.dumps(judge_run(args.run, args.model, args.limit), indent=2))
    else:
        parser.error("pass `--run RUN` or `--pair BASE NEW`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
