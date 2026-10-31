from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from engine.adapters.stage_one import default_ports
from engine.core.critic import critique
from engine.core.finalize import apply_candidate
from engine.core.layout import propose
from engine.core.sizing import Ctx
from engine.core.understand import understand
from tools.bench import load_suite
from tools.checks import render
from tools.judge import PAIR_PROMPT, PAIR_SCHEMA, _ask, _image_block, _parallel, _record_spend, make_client, resolve_pair

# =============================================================================
# Module Overview
# =============================================================================
# Audits the engine's layout choice. For every suite task it renders every
# candidate the engine considered, then asks the judge, in both image orders,
# whether any alternative beats the candidate the critic picked. The output
# shows where the critic disagrees with the judge, which is where critic
# weights or strategy preferences should change.

ROOT = Path(__file__).resolve().parents[1]


def render_candidates(suite: Path, out: Path) -> list[dict[str, Any]]:
    """Render all candidates per task; return one record per task with the critic's pick first."""
    measurer, probe = default_ports(ROOT)
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for task in load_suite(suite):
        tid, src, tgt = task["task_id"], task["source"], task["target_canvas"]
        scene = understand(src, probe)
        ctx = Ctx(scene, float(tgt["width"]), float(tgt["height"]), measurer, probe)
        cands = []
        for cand in propose(ctx, "none"):
            design = apply_candidate(scene, tgt, cand)
            score = critique(ctx, design, cand.hidden).score
            path = out / f"{tid}__{cand.strategy.replace('/', '-')}.png"
            render(design).save(path)
            cands.append({"strategy": cand.strategy, "critic": score, "png": str(path)})
        if len(cands) < 2:
            continue
        render(src).save(out / f"{tid}__source.png")
        cands.sort(key=lambda c: -c["critic"])
        records.append({"task": tid, "target": f"{tgt['width']}x{tgt['height']}", "candidates": cands})
    return records


def audit(suite: Path, out: Path, model: str) -> dict[str, Any]:
    """Judge the critic's pick against each alternative and summarise the disagreements."""
    records = render_candidates(suite, out)
    client = make_client()
    jobs = [(r, alt) for r in records for alt in r["candidates"][1:]]

    def one(job: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        rec, alt = job
        pick = rec["candidates"][0]
        src = _image_block(out / f"{rec['task']}__source.png")
        a, b = _image_block(Path(pick["png"])), _image_block(Path(alt["png"]))
        text = {"type": "text", "text": f"{PAIR_PROMPT}\n\nTarget canvas: {rec['target']}."}
        first = _ask(client, model, [src, a, b, text], PAIR_SCHEMA)
        second = _ask(client, model, [src, b, a, text], PAIR_SCHEMA)
        cost = first.get("cost_usd", 0.0) + second.get("cost_usd", 0.0)
        if "error" in first or "error" in second:
            return {"task": rec["task"], "error": first.get("error") or second.get("error"), "cost_usd": cost}
        verdict = resolve_pair(first["winner"], second["winner"])
        return {"task": rec["task"], "pick": pick["strategy"], "alt": alt["strategy"],
                "pick_critic": pick["critic"], "alt_critic": alt["critic"],
                "judge_prefers": {"new": "alt", "base": "pick", "tie": "tie"}[verdict],
                "reason": first["reason"], "cost_usd": cost}

    results = _parallel(one, jobs)
    cost = round(sum(r.get("cost_usd", 0.0) for r in results), 4)
    flips = [r for r in results if r.get("judge_prefers") == "alt"]
    summary = {
        "pairs": len(results), "judge_prefers_alt": len(flips),
        "judge_prefers_pick": sum(r.get("judge_prefers") == "pick" for r in results),
        "ties": sum(r.get("judge_prefers") == "tie" for r in results),
        "preferred_alt_strategies": Counter(r["alt"] for r in flips).most_common(),
        "cost_usd": cost, "total_spent_usd": _record_spend(cost),
    }
    (out / "audit.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the critic's layout choice against the judge.")
    parser.add_argument("--suite", type=Path, default=ROOT / "bench" / "suite" / "dev")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "bench" / "candidates")
    parser.add_argument("--model", default="claude-opus-5-5")
    args = parser.parse_args()
    print(json.dumps(audit(args.suite, args.out, args.model), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
