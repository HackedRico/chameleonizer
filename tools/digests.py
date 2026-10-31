from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any

# =============================================================================
# Module Overview
# =============================================================================
# The byte-identity gate. `run` calls `engine.resize.resize` on every task of
# the given suites, under whatever `CHAMELEON_*` environment the shell sets,
# and records the sha256 of each output exactly as `engine/main.py` writes it
# (`json.dumps(out, indent=2)`), plus one aggregate digest and whether the
# model harness was really on for each task: when `CHAMELEON_AGENT` names a
# backend but no harness was built, `run` fails, since identity would then
# prove nothing. `diff` compares two such files and exits 1 on any difference,
# so a refactor or an advisor that must not change outputs can be checked on
# every suite in one command. Secrets are never written: variables whose names
# hold KEY, TOKEN or SECRET are left out of the recorded environment.

ROOT = Path(__file__).resolve().parents[1]
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def recorded_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The `CHAMELEON_*` variables worth recording, without any that could hold a secret."""
    return {k: v for k, v in sorted(environ.items())
            if k.startswith("CHAMELEON_") and not any(word in k.upper() for word in _SECRET_WORDS)}


def _task_files(suites: list[Path]) -> list[tuple[str, Path]]:
    """(key, path) for every task file in `suites`; the key is `<suite name>/<file stem>`."""
    out = []
    for suite in suites:
        folder = suite if suite.is_absolute() else ROOT / suite
        files = sorted(folder.glob("*.json"))
        if not files:
            raise SystemExit(f"no task files in {folder}")
        out += [(f"{folder.name}/{path.stem}", path) for path in files]
    return out


def _digest_one(path: str) -> tuple[str, float, bool]:
    """Digest of `resize()`'s output for one task file, the seconds it took, and whether the model
    harness was on for it."""
    from engine.resize import _agent, resize  # imported in the worker, so each process builds its own harness

    task = json.loads(Path(path).read_text())
    t0 = time.perf_counter()
    out = resize(task["source"], task["target_canvas"])
    elapsed = time.perf_counter() - t0
    digest = hashlib.sha256(json.dumps(out, indent=2).encode("utf-8")).hexdigest()
    return digest, round(elapsed, 3), _agent() is not None


def aggregate(digests: dict[str, str]) -> str:
    """One digest over every task digest, in key order."""
    lines = "".join(f"{k}:{v}\n" for k, v in sorted(digests.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def run(suites: list[Path], out: Path, jobs: int = 1) -> dict[str, Any]:
    """Digest every task of `suites` into `out` and return the payload."""
    files = _task_files(suites)
    paths = [str(p) for _, p in files]
    if jobs > 1:
        # Spawned workers import the engine fresh, exactly as a scorer process would.
        with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
            results = list(pool.map(_digest_one, paths, chunksize=1))
    else:
        results = [_digest_one(p) for p in paths]
    digests = {key: digest for (key, _), (digest, _, _) in zip(files, results, strict=True)}
    seconds = {key: s for (key, _), (_, s, _) in zip(files, results, strict=True)}
    harness_off = sorted(key for (key, _), (_, _, on) in zip(files, results, strict=True) if not on)
    wanted = _agent_wanted(os.environ)
    payload = {"aggregate": aggregate(digests), "tasks": len(digests), "env": recorded_env(os.environ),
               "agent_wanted": wanted, "harness_on": len(digests) - len(harness_off),
               "suites": [str(s) for s in suites], "digests": digests, "seconds": seconds}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    if wanted and harness_off:
        raise SystemExit(f"CHAMELEON_AGENT={os.environ.get('CHAMELEON_AGENT')!r} names a backend, but the harness was "
                         f"off for {len(harness_off)} of {len(digests)} tasks (config, warmup or model load failed; "
                         f"see the log above), so these digests prove nothing about it. For a dead-model check, "
                         f"set CHAMELEON_AGENT_WARMUP_S=0. Digests written to {out}.")
    return payload


def _agent_wanted(environ: Mapping[str, str]) -> bool:
    """Whether the environment asks for a model harness at all."""
    from engine.adapters.agent_config import OFF_VALUES

    return environ.get("CHAMELEON_AGENT", "").strip().lower() not in OFF_VALUES


def diff(a: Path, b: Path) -> list[str]:
    """Task keys whose digest differs between two digest files, or that only one of them has."""
    da, db = (json.loads(p.read_text())["digests"] for p in (a, b))
    return sorted(k for k in set(da) | set(db) if da.get(k) != db.get(k))


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-task output digests, for byte-identity checks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="digest every task of the given suites")
    rn.add_argument("--suite", type=Path, action="append", required=True,
                    help="a folder of task files, e.g. input or bench/suite/dev; repeatable")
    rn.add_argument("--out", type=Path, required=True)
    rn.add_argument("--jobs", type=int, default=1, help="worker processes")
    df = sub.add_parser("diff", help="exit 1 if two digest files differ on any task")
    df.add_argument("a", type=Path)
    df.add_argument("b", type=Path)
    args = parser.parse_args()
    if args.cmd == "run":
        payload = run(args.suite, args.out, args.jobs)
        print(json.dumps({"aggregate": payload["aggregate"], "tasks": payload["tasks"], "out": str(args.out)}))
        return 0
    changed = diff(args.a, args.b)
    agg = [json.loads(p.read_text())["aggregate"] for p in (args.a, args.b)]
    print(json.dumps({"identical": not changed, "changed": changed, "aggregate": agg}, indent=1))
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
