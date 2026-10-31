from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.judge import resolve_pair

# =============================================================================
# Module Overview
# =============================================================================
# Judging without the API, by Claude agents inside a session. `make` lays out a
# round of pairwise comparisons on disk, and `resolve` turns the agents' verdicts
# into per-task results with `tools.judge.resolve_pair`. `absolute` and `scores`
# do the same for 1 to 10 rubric scores of one run. The agents themselves run
# from `tools/workflows/blind_pairwise_judge.js` and `absolute_judge.js`.
# Order 1 shows the base as A and the candidate as B; order 2 swaps them, and the
# two orders of a task go to different batches, so no agent sees both.

BATCH = 5


def changed_tasks(base: Path, new: Path) -> list[str]:
    """Tasks whose output JSON differs between two bench runs."""
    out = []
    for f in sorted((new / "renders").glob("*_output.json")):
        b = base / "renders" / f.name
        if not b.exists() or b.read_bytes() != f.read_bytes():
            out.append(f.name.removesuffix("_output.json"))
    return out


def make_round(out: Path, pairs: list[tuple[str, Path, Path]], tasks: list[str] | None = None) -> dict[str, Any]:
    """Write `<out>/<id>/{original,A,B}.png`, `key.json` and `batches.json` for `pairs` of runs.

    Each pair is a label, a base run and a candidate run. Without `tasks`, every changed task is
    compared."""
    shutil.rmtree(out, ignore_errors=True)
    key: dict[str, dict[str, Any]] = {}
    items: dict[int, list[dict[str, str]]] = {1: [], 2: []}
    n = 0
    for label, base, new in pairs:
        rows = {r["task"]: r for r in json.loads((new / "results.json").read_text())["rows"]}
        for task in tasks if tasks is not None else changed_tasks(base, new):
            n += 1
            target = rows.get(task, {}).get("target", "")
            for order, (a, b) in ((1, (base, new)), (2, (new, base))):
                vid = f"c{n:03d}o{order}"
                d = out / vid
                d.mkdir(parents=True)
                shutil.copy(base / "renders" / f"{task}_source.png", d / "original.png")
                shutil.copy(a / "renders" / f"{task}_output.png", d / "A.png")
                shutil.copy(b / "renders" / f"{task}_output.png", d / "B.png")
                key[vid] = {"label": label, "task": task, "order": order, "base": str(base), "new": str(new)}
                items[order].append({"id": vid, "dir": str(d.resolve()), "target": str(target)})
    batches = [lst[i:i + BATCH] for order in (1, 2) for lst in [items[order]] for i in range(0, len(lst), BATCH)]
    (out / "key.json").write_text(json.dumps(key, indent=1))
    (out / "batches.json").write_text(json.dumps(batches, indent=1))
    return {"tasks": n, "batches": len(batches), "file": str((out / "batches.json").resolve())}


def resolve_round(out: Path, verdicts_file: Path) -> dict[str, Any]:
    """Per-task verdicts and per-label tallies from the agents' answers, written to `results.json`.

    `verdicts_file` holds a list of {id, winner, reason}, or a workflow output with that list as `result`."""
    data = json.loads(verdicts_file.read_text())
    answers = {v["id"]: v for v in (data["result"] if isinstance(data, dict) else data)}
    key = json.loads((out / "key.json").read_text())
    tasks: dict[str, dict[str, Any]] = defaultdict(dict)
    for vid, meta in key.items():
        if vid in answers:
            tasks[vid[:-2]][meta["order"]] = answers[vid]["winner"]
    rows, tally = [], defaultdict(Counter)
    for group, orders in sorted(tasks.items()):
        if 1 not in orders or 2 not in orders:
            continue
        meta = key[group + "o1"]
        verdict = resolve_pair(orders[1], orders[2])
        rows.append({"label": meta["label"], "task": meta["task"], "verdict": verdict})
        tally[meta["label"]][verdict] += 1
    summary = {label: {"new": t["new"], "base": t["base"], "tie": t["tie"]} for label, t in tally.items()}
    (out / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    return summary


RUBRIC = ("collisions", "readability", "brand", "intentional", "overall")


def make_absolute(out: Path, run: Path) -> dict[str, Any]:
    """Write `<out>/<task>/{original,output}.png` and `batches.json` to score every task in `run`."""
    shutil.rmtree(out, ignore_errors=True)
    items = []
    for row in json.loads((run / "results.json").read_text())["rows"]:
        d = out / row["task"]
        d.mkdir(parents=True)
        shutil.copy(run / "renders" / f"{row['task']}_source.png", d / "original.png")
        shutil.copy(run / "renders" / f"{row['task']}_output.png", d / "output.png")
        items.append({"id": row["task"], "dir": str(d.resolve()), "target": row["target"]})
    batches = [items[i:i + BATCH - 1] for i in range(0, len(items), BATCH - 1)]
    (out / "batches.json").write_text(json.dumps(batches, indent=1))
    return {"tasks": len(items), "batches": len(batches), "file": str((out / "batches.json").resolve())}


def summarize_scores(out: Path, scores_file: Path) -> dict[str, Any]:
    """Rubric means and fails at 4 or below from the agents' scores, written to `scores.json`."""
    data = json.loads(scores_file.read_text())
    rows = data["result"] if isinstance(data, dict) else data
    means = {k: round(sum(r[k] for r in rows) / len(rows), 2) for k in RUBRIC} if rows else {}
    summary = {"judged": len(rows), "means": means, "fails_le4": sum(r["overall"] <= 4 for r in rows),
               "worst": [(r["id"], r["overall"]) for r in sorted(rows, key=lambda r: r["overall"])[:8]]}
    (out / "scores.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind pairwise judging by in-session agents.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="lay out a round of comparisons")
    mk.add_argument("out", type=Path)
    mk.add_argument("--pair", nargs=3, action="append", metavar=("LABEL", "BASE", "NEW"), required=True)
    mk.add_argument("--tasks", nargs="*", help="compare these tasks instead of every changed one")
    rs = sub.add_parser("resolve", help="tally the agents' verdicts")
    rs.add_argument("out", type=Path)
    rs.add_argument("verdicts", type=Path)
    ab = sub.add_parser("absolute", help="lay out one run for rubric scores")
    ab.add_argument("out", type=Path)
    ab.add_argument("run", type=Path)
    sc = sub.add_parser("scores", help="summarize the agents' rubric scores")
    sc.add_argument("out", type=Path)
    sc.add_argument("scores", type=Path)
    args = parser.parse_args()
    if args.cmd == "make":
        pairs = [(label, Path(base), Path(new)) for label, base, new in args.pair]
        result = make_round(args.out, pairs, args.tasks)
    elif args.cmd == "resolve":
        result = resolve_round(args.out, args.verdicts)
    elif args.cmd == "absolute":
        result = make_absolute(args.out, args.run)
    else:
        result = summarize_scores(args.out, args.scores)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
