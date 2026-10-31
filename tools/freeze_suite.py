from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from tools.catalog import FORMATS
from tools.gen_cases import generate

# =============================================================================
# Module Overview
# =============================================================================
# Writes the frozen benchmark suite under `bench/suite/`. Every catalogue
# format gets three generated designs: two go to `dev` (tune against these)
# and one to `test` (held out, report only). The official samples join `dev`.
# Freezing the files means scores stay comparable even if the generator changes.

ROOT = Path(__file__).resolve().parents[1]
DESIGNS_PER_FORMAT = 3
BASE_SEED = 50_000


def freeze(out: Path) -> dict[str, int]:
    """Write the dev and test task files under `out`, replacing any previous suite."""
    counts = {"dev": 0, "test": 0}
    for split in counts:
        folder = out / split
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    for path in sorted((ROOT / "input").glob("*.json")):
        task = json.loads(path.read_text())
        task["task_id"] = path.stem.replace("chameleon_sample_", "official_")
        (out / "dev" / f"{task['task_id']}.json").write_text(json.dumps(task, indent=2))
        counts["dev"] += 1
    for f, name in enumerate(FORMATS):
        slug = name.lower().replace(" ", "_")
        for k in range(DESIGNS_PER_FORMAT):
            task = generate(BASE_SEED + 10 * f + k, target=name)
            split = "test" if k == DESIGNS_PER_FORMAT - 1 else "dev"
            task["task_id"] = f"{slug}_{k}"
            (out / split / f"{task['task_id']}.json").write_text(json.dumps(task, indent=2))
            counts[split] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the Chameleonizer benchmark suite.")
    parser.add_argument("--out", type=Path, default=ROOT / "bench" / "suite")
    args = parser.parse_args()
    print(freeze(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
