from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from engine.adapters.stage_one import default_ports
from engine.core.pipeline import resize_with_report
from tools.checks import pixel_metrics, render, structural
from tools.gen_cases import generate

# =============================================================================
# Module Overview
# =============================================================================
# The scoreboard. `run_bench` resizes the official samples plus generated
# tasks, checks hard rules, renders each result with the official renderer,
# measures contrast and busyness, and writes `summary.md`, `results.json`, the
# renders, and contact sheets that pair each source with its output for judging.
# With `--agent`, the optional model harness configured by the `CHAMELEON_AGENT*`
# environment variables chooses among the engine's finalists, and each row says
# what it did (`docs/agentic-harness.md`).

ROOT = Path(__file__).resolve().parents[1]
FONT = ImageFont.truetype(str(ROOT / "brand_kit" / "fonts" / "SolsticeSans-Bold.ttf"), 18)
SMALL = ImageFont.truetype(str(ROOT / "brand_kit" / "fonts" / "SolsticeSans-Regular.ttf"), 15)


def load_tasks(count: int, seed: int) -> list[dict[str, Any]]:
    """The three official samples followed by `count` generated tasks."""
    tasks = []
    for path in sorted((ROOT / "input").glob("*.json")):
        task = json.loads(path.read_text())
        task["task_id"] = path.stem.replace("chameleon_sample_", "official_")
        tasks.append(task)
    tasks += [generate(seed * 1000 + i) for i in range(count)]
    return tasks


def load_suite(folder: Path) -> list[dict[str, Any]]:
    """Every task file in a frozen suite folder, in name order."""
    tasks = []
    for path in sorted(folder.glob("*.json")):
        task = json.loads(path.read_text())
        task.setdefault("task_id", path.stem)
        tasks.append(task)
    if not tasks:
        raise SystemExit(f"no task files in {folder}")
    return tasks


def run_bench(out: Path, count: int, seed: int, policy: str, suite: Path | None = None,
              agent: bool = False) -> dict[str, Any]:
    """Run every task, write artifacts under `out`, and return the aggregate scores."""
    measurer, probe = default_ports(ROOT)
    harness = _harness() if agent else None
    renders = out / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in load_suite(suite) if suite else load_tasks(count, seed):
        tid, src, tgt = task["task_id"], task["source"], task["target_canvas"]
        t0 = time.perf_counter()
        if harness is None:
            outcome = resize_with_report(src, tgt, measurer, probe, policy)
            design, strategy, report, extra = outcome.design, outcome.strategy, outcome.report, {}
        else:
            from engine.adapters.harness import agent_resize

            design, outcome, result = agent_resize(src, tgt, measurer, probe, policy, harness)
            chosen = outcome.finalists[result.chosen] if outcome.finalists else None
            strategy = result.strategy
            report = chosen.report if chosen is not None else outcome.report
            extra = {"agent": {
                "chosen": result.chosen, "reason": result.reason, "calls": result.calls, "seconds": result.seconds,
                "finalists": [{"strategy": f.strategy, "critic": f.report.score} for f in outcome.finalists],
                "trace": result.trace,
            }}
        elapsed = time.perf_counter() - t0
        violations = structural(src, tgt, design)
        render(src).save(renders / f"{tid}_source.png")
        render(design).save(renders / f"{tid}_output.png")
        (renders / f"{tid}_output.json").write_text(json.dumps(design, indent=2))
        rows.append({
            "task": tid,
            "source": f"{src['canvas']['width']}x{src['canvas']['height']}",
            "target": f"{tgt['width']}x{tgt['height']}",
            "target_name": task.get("target_name", ""),
            "elements": len(src["elements"]),
            "strategy": strategy,
            "critic": report.score,
            "penalties": report.penalties,
            "violations": violations,
            "seconds": round(elapsed, 3),
            **pixel_metrics(design),
            **extra,
        })
    summary = _summarize(rows)
    if harness is not None:
        summary.update(_agent_summary(rows))
        with (out / "agent_trace.jsonl").open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r["agent"]["trace"], default=str) + "\n")
    # numpy scalars from the pixel metrics are not JSON-native; `.item()` converts them.
    payload = json.dumps({"summary": summary, "rows": rows}, indent=2, default=lambda o: o.item())
    (out / "results.json").write_text(payload)
    (out / "summary.md").write_text(_markdown(summary, rows, policy))
    _contact_sheets(out, rows)
    return summary


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    critic = [r["critic"] for r in rows]
    return {
        "tasks": len(rows),
        "structural_pass": int(sum(not r["violations"] for r in rows)),
        "critic_mean": round(statistics.mean(critic), 2),
        "critic_min": round(min(critic), 2),
        "low_contrast_tasks": int(sum(r["min_contrast"] < 3.0 for r in rows)),
        "busy_text_tasks": int(sum(r["max_busy"] > 0.03 for r in rows)),
        "seconds_mean": round(statistics.mean(r["seconds"] for r in rows), 3),
        "seconds_max": round(max(r["seconds"] for r in rows), 3),
    }


def _harness() -> Any:
    """The model harness the `CHAMELEON_AGENT*` environment configures; exits when there is none."""
    from engine.adapters.agent_config import load_agent_config
    from engine.adapters.harness import build_harness

    cfg = load_agent_config(os.environ)
    harness = build_harness(cfg, ROOT) if cfg is not None else None
    if harness is None:
        raise SystemExit("--agent needs a working model harness: set CHAMELEON_AGENT and friends "
                         "(docs/agentic-harness.md); the log above says what failed.")
    return harness


def _agent_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the harness asked, switched and waited, and for a replay, how often it found a
    recorded verdict."""
    agent = [r["agent"] for r in rows]
    asked = [a for a in agent if a["calls"] > 0]
    notes = [[step.get("note") for step in a["trace"].get("steps", [])] for a in agent]
    summary = {
        "agent_fired": len(asked),
        "agent_switched": sum(a["chosen"] != 0 for a in agent),
        "agent_calls": sum(a["calls"] for a in agent),
        "agent_seconds_mean": round(statistics.mean(a["seconds"] for a in agent), 3),
        "agent_seconds_max": round(max(a["seconds"] for a in agent), 3),
        "agent_reasons": dict(sorted(Counter(a["reason"] for a in agent).items())),
    }
    if any(n in ("replay_hit", "replay_miss") for ns in notes for n in ns):
        summary["agent_replay_hits"] = sum(n == "replay_hit" for ns in notes for n in ns)
        summary["agent_replay_misses"] = sum(n == "replay_miss" for ns in notes for n in ns)
        # A firing task whose first question missed was never really asked: its "kept" means nothing.
        summary["agent_replay_unmatched_tasks"] = sum(bool(ns) and ns[0] == "replay_miss" for ns in notes)
    return summary


def replay_problem(summary: dict[str, Any]) -> str | None:
    """A warning when a replay run's verdicts did not match the engine's renders, else None."""
    misses = summary.get("agent_replay_misses", 0)
    if not misses:
        return None
    text = (f"replay: {summary.get('agent_replay_hits', 0)} hits, {misses} misses, "
            f"{summary.get('agent_replay_unmatched_tasks', 0)} firing tasks whose first question had no recorded "
            "verdict. Misses after a switch are expected with top_k above 2; a miss on a first question means "
            "the dataset was built from different renders (another machine or renderer build, --image-max, "
            "drop policy, epsilon or top_k), and those tasks count as kept without being asked.")
    return text


def _markdown(summary: dict[str, Any], rows: list[dict[str, Any]], policy: str) -> str:
    lines = [f"# Bench summary (policy `{policy}`)", ""]
    lines += [f"- {k}: {v}" for k, v in summary.items()]
    lines += ["", "| Task | Source | Target | Strategy | Critic | Contrast | Busy | Violations | Top penalties |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        top = ", ".join(f"{k} {v}" for k, v in sorted(r["penalties"].items(), key=lambda kv: -kv[1])[:3])
        lines.append(
            f"| {r['task']} | {r['source']} | {r['target']} {r['target_name']} | {r['strategy']} | {r['critic']} "
            f"| {r['min_contrast']} | {r['max_busy']} | {len(r['violations'])} | {top} |"
        )
    return "\n".join(lines) + "\n"


def _contact_sheets(out: Path, rows: list[dict[str, Any]], per_sheet: int = 5) -> None:
    """Pair each source thumbnail with its output, five tasks per sheet."""
    renders = out / "renders"
    for start in range(0, len(rows), per_sheet):
        chunk = rows[start:start + per_sheet]
        tiles = []
        for r in chunk:
            src = Image.open(renders / f"{r['task']}_source.png")
            dst = Image.open(renders / f"{r['task']}_output.png")
            src.thumbnail((260, 260))
            # Show outputs at native size when they fit, so small banners read as the judge sees them.
            dst.thumbnail((1180, 360))
            h = max(src.height, dst.height) + 34
            tile = Image.new("RGB", (1480, h), (246, 242, 236))
            d = ImageDraw.Draw(tile)
            label = f"{r['task']}  {r['source']} to {r['target']} {r['target_name']}  [{r['strategy']}]"
            d.text((10, 6), label, font=FONT, fill=(30, 20, 35))
            d.text((1020, 9), f"critic {r['critic']}", font=SMALL, fill=(120, 60, 40))
            tile.paste(src, (10, 30))
            tile.paste(dst, (290, 30))
            tiles.append(tile)
        sheet = Image.new("RGB", (1480, sum(t.height for t in tiles) + 10 * len(tiles)), (220, 214, 206))
        y = 0
        for t in tiles:
            sheet.paste(t, (0, y))
            y += t.height + 10
        sheet.save(out / f"sheet_{start // per_sheet + 1:02d}.png")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Chameleonizer test bench.")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "bench" / "latest")
    parser.add_argument("--count", type=int, default=22)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--policy", choices=("none", "occlude"), default="none")
    parser.add_argument("--suite", type=Path, help="run a frozen suite folder, e.g. bench/suite/dev")
    parser.add_argument("--agent", action="store_true",
                        help="let the model harness configured by CHAMELEON_AGENT* choose among finalists")
    args = parser.parse_args()
    summary = run_bench(args.out, args.count, args.seed, args.policy, args.suite, args.agent)
    print(json.dumps(summary, indent=2))
    print(f"artifacts in {args.out}")
    problem = replay_problem(summary)
    if problem:
        print(f"warning: {problem}", file=sys.stderr)
        if summary.get("agent_replay_hits", 0) == 0:
            print("error: the replay matched no question at all", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
