from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

# =============================================================================
# Module Overview
# =============================================================================
# Compares two bench runs task by task. `compare` lines up critic scores (and
# judge scores when both runs were judged) so a change is accepted on evidence,
# not on a mean that hides a regression in one format.


def _load(run: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    rows = {r["task"]: r for r in json.loads((run / "results.json").read_text())["rows"]}
    judged = {}
    if (run / "judge.json").exists():
        judged = {v["task"]: v for v in json.loads((run / "judge.json").read_text())["verdicts"] if "error" not in v}
    return rows, judged


def compare(base: Path, new: Path, show: int) -> None:
    """Print mean deltas and the tasks that moved most between `base` and `new`."""
    b_rows, b_j = _load(base)
    n_rows, n_j = _load(new)
    common = sorted(set(b_rows) & set(n_rows))
    deltas = sorted(((n_rows[t]["critic"] - b_rows[t]["critic"], t) for t in common))
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    print(f"tasks compared: {len(common)}")
    print(f"critic mean {mean([b_rows[t]['critic'] for t in common]):.2f} -> {mean([n_rows[t]['critic'] for t in common]):.2f}")
    both = [t for t in common if t in b_j and t in n_j]
    if both:
        print(f"judge overall mean {mean([b_j[t]['overall'] for t in both]):.2f} -> "
              f"{mean([n_j[t]['overall'] for t in both]):.2f} over {len(both)} tasks")
        print(f"judge fails (<=4) {sum(b_j[t]['overall'] <= 4 for t in both)} -> {sum(n_j[t]['overall'] <= 4 for t in both)}")
    print("biggest critic drops:")
    for d, t in deltas[:show]:
        print(f"  {d:+7.2f}  {t}  {b_rows[t]['strategy']} -> {n_rows[t]['strategy']}  {n_rows[t]['target_name']}")
    print("biggest critic gains:")
    for d, t in deltas[::-1][:show]:
        print(f"  {d:+7.2f}  {t}  {b_rows[t]['strategy']} -> {n_rows[t]['strategy']}  {n_rows[t]['target_name']}")


def sheet(base: Path, new: Path, tasks: list[str], out: Path) -> None:
    """Write one image pairing each task's base output (left) with its new output (right)."""
    tiles = []
    for t in tasks:
        a = Image.open(base / "renders" / f"{t}_output.png")
        b = Image.open(new / "renders" / f"{t}_output.png")
        # Small canvases are shown at 2x so text problems are visible.
        k = 2 if max(a.size) < 700 else 1
        a, b = a.resize((a.width * k, a.height * k)), b.resize((b.width * k, b.height * k))
        for im in (a, b):
            im.thumbnail((700, 700))
        tile = Image.new("RGB", (1430, max(a.height, b.height) + 28), (246, 242, 236))
        ImageDraw.Draw(tile).text((6, 6), f"{t}   base (left) vs new (right){'  x2' if k == 2 else ''}", fill=(0, 0, 0))
        tile.paste(a, (6, 24))
        tile.paste(b, (722, 24))
        tiles.append(tile)
    if not tiles:
        return
    canvas = Image.new("RGB", (1430, sum(t.height + 8 for t in tiles)), (220, 214, 206))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y))
        y += t.height + 8
    canvas.save(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two bench runs task by task.")
    parser.add_argument("base", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--show", type=int, default=5)
    parser.add_argument("--sheet", type=Path, help="write a side-by-side image of these --tasks")
    parser.add_argument("--tasks", nargs="*", default=[])
    args = parser.parse_args()
    compare(args.base, args.new, args.show)
    if args.sheet:
        sheet(args.base, args.new, args.tasks, args.sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
