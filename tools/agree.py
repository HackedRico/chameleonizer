from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import statistics
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from engine.adapters.agent_config import load_agent_config
from engine.adapters.harness import build_advisor, warm_up
from engine.adapters.openai_http import opener as http_opener
from engine.core.advice import (ISSUE_PENALTIES, ISSUES, PROMPT_VERSION, parse_flags, parse_verdict, prompt,
                                replay_key, resolve, shows_original)
from engine.core.agent import call_with_deadline, drain_abandoned

# =============================================================================
# Module Overview
# =============================================================================
# Agreement evaluation for the optional model harness: does a local model
# agree with the frontier judge often enough to be allowed to pick finalists?
#   near-ties  lays out the engine's own near-tie finalists as a blind round
#              (`tools.blind` layout) for in-session frontier judging.
#   build      turns judged rounds, API pairwise files, candidate audits and
#              absolute rounds into a dataset: pairs.jsonl, flags.jsonl,
#              replay.jsonl, images/ and manifest.json.
#   run        asks the configured advisor every pair in both orders.
#   score      computes agreement, abstention, position bias, latency and the
#              gates, with Wilson intervals, into metrics.json and report.md.
#   mcnemar    an exact test between two runs on the same pairs.
#   doctor     one synthetic comparison, to check a model server works.
# Development only; never in the scoring image.

ROOT = Path(__file__).resolve().parents[1]
BUILDER_VERSION = "1"
BATCH = 5  # comparisons per judging agent, as in tools/blind.py
GRACE_S = 0.25
DECISIVE_TIERS = ("D1", "D2", "D3")
CONTROL_TIERS = ("C1", "C2")
# In-session judges against the API judge on 31 tasks it found decisive (docs/benchmark.md).
FRONTIER_VS_FRONTIER = {"agree": 24, "of": 31, "contradict": 0, "source": "docs/benchmark.md, calibration"}

# Gates, judged on the `hold` split. Passing all of them means "run the end-to-end benchmark", not "ship".
GATES: dict[str, Any] = {
    "min_decisive": 60,
    "min_d3": 30,
    "min_sources": 20,
    "families": ("strip", "banner", "square", "portrait", "tall", "print", "tiny"),
    "min_controls": 20,
    "max_invalid_or_error_rate": 0.01,
    "max_d1_contradictions": 0,
    "max_contradiction_rate": 0.05,
    "max_contradiction_upper": 0.12,
    "min_precision_lower": 0.70,
    "min_d3_override_precision": 0.70,
    "max_position_bias": 0.20,
    "first_slot_range": (0.40, 0.60),
    "max_c1_false_decisive": 0.30,
    "min_determinism": 0.98,
    "max_pair_p95_s": 8.0,
    "latency_env": "amd64-cpu4",
    "min_d3_coverage": 0.30,
}

# Words in an absolute round's issue text that name each issue code. A rough map: spot-check by hand.
ISSUE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "text_overlap": ("overlap", "collid", "collision", "on top of", "text over", "sits on", "covers", "covering",
                     "obscur", "runs into", "piled", "pile"),
    "tiny_text": ("tiny", "too small", "very small", "illegib", "unreadable", "hard to read", "run together",
                  "runs together", "squint"),
    "cut_off": ("cut off", "clipped", "cropped", "truncat", "runs off", "off the edge", "cut at", "cut through"),
    "weak_headline": ("does not dominate", "doesn't dominate", "no longer dominat", "weak headline",
                      "headline is only", "competes with the headline", "hierarchy"),
    "crowded": ("crowd", "cramped", "jammed", "tight", "no margin", "squeez", "against the edge", "hugs"),
    "empty_space": ("empty", "dead space", "white space", "whitespace", "unused", "off-centre", "off-center",
                    "lopsided", "unbalanced"),
    "busy_background": ("busy", "bubble", "dots behind", "cluttered background"),
    "lost_look": ("lost the original", "no longer looks", "unlike the original", "flipped", "mirrored",
                  "loses the original"),
    "distorted_image": ("stretched", "squashed", "distort", "squished"),
}


# =============================================================================
# Statistics
# =============================================================================


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    """Wilson 95% interval for k successes in n trials; (None, None) when n is 0."""
    if n <= 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def rate(k: int, n: int) -> dict[str, Any]:
    """k of n with its share and Wilson interval."""
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "lo": lo, "hi": hi}


def binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial test of k in n against one half."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def percentile(values: list[float], q: float) -> float | None:
    """The q-th percentile by linear interpolation, or None for no values."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 3)


def families(target: str) -> set[str]:
    """Format families of a "WxH" target, by aspect, area and print size."""
    try:
        w, h = (int(v) for v in target.lower().split("x"))
    except ValueError:
        return set()
    r = w / max(1, h)
    out = {"strip" if r >= 5 else "banner" if r >= 2.2 else "wide" if r >= 1.1 else "square" if r > 0.9
           else "portrait" if r > 0.6 else "tall"}
    if w * h < 40_000:
        out.add("tiny")
    if min(w, h) >= 2000:
        out.add("print")
    return out


# =============================================================================
# Small helpers
# =============================================================================


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _dataset_rows(data: Path, kind: str) -> list[dict[str, Any]]:
    """The pair or flag rows of a dataset; exits when the file is missing or empty, never returns []."""
    path = data / ("pairs.jsonl" if kind == "compare" else "flags.jsonl")
    rows = _read_jsonl(path)
    if not rows:
        raise SystemExit(f"{path} is missing or empty; pass --data with the dataset folder "
                         "(`tools.agree build` writes it)")
    return rows


def _stored_path(path: Path) -> str:
    """`path` relative to the repo root when inside it, so a run made in a container scores on the host."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _data_of(meta: dict[str, Any], override: Path | None = None) -> Path:
    """The dataset a run asked: `override`, or run.json's path, read against the repo root if relative."""
    data = override if override is not None else Path(meta["data"])
    return data if data.is_absolute() else ROOT / data


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _answers(path: Path) -> dict[str, dict[str, Any]]:
    """Verdicts from a workflow output file: a list of {id, winner, reason}, or {"result": list}."""
    data = json.loads(path.read_text())
    rows = data["result"] if isinstance(data, dict) else data
    return {str(v["id"]): v for v in rows if isinstance(v, dict) and "id" in v}


def _norm_winner(value: Any) -> str | None:
    """A frontier judge's letter as "A", "B" or "tie"; None when missing or unknown."""
    v = str(value or "").strip().upper()
    return "tie" if v in ("TIE", "SAME") else v if v in ("A", "B") else None


def _resolve_pair(first: str, second: str) -> str:
    """`tools.judge.resolve_pair`: "new", "base" or "tie", from order 1 (base as A) and order 2."""
    as_new = {"A": "base", "B": "new", "tie": "tie"}[first]
    swapped = {"A": "new", "B": "base", "tie": "tie"}[second]
    return as_new if as_new == swapped else "tie"


def _target_size(target: str, fallback: bytes | None = None) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in str(target).lower().split("x"))
        return w, h
    except ValueError:
        if fallback is None:
            return 0, 0
        from PIL import Image

        with Image.open(io.BytesIO(fallback)) as im:
            return im.size


# =============================================================================
# near-ties: the engine's own finalists as a blind round
# =============================================================================


def near_ties(suites: list[Path], out: Path, epsilon: float = 3.0, top_k: int = 4, force: bool = False,
              image_max: int = 1024, policy: str = "none") -> dict[str, Any]:
    """Lay out every eligible challenger against the engine default as a blind round in `out`.

    Order 1 shows the default as A. Renders are the raw bytes the engine would show a model, so
    verdicts on them replay through the engine (`replay.jsonl`)."""
    from engine.adapters.render import OfficialRenderer
    from engine.adapters.stage_one import default_ports
    from engine.core.agent import eligible
    from engine.core.pipeline import resize_with_report
    from tools.bench import load_suite

    loaded = []
    for suite in suites:
        folder = suite if suite.is_absolute() else ROOT / suite
        if not sorted(folder.glob("*.json")):
            raise SystemExit(f"no task files in {folder}")
        loaded.append((suite, load_suite(folder)))
    _clear_round(out, force)
    out.mkdir(parents=True, exist_ok=True)  # empty now, or new
    measurer, probe = default_ports(ROOT)
    renderer = OfficialRenderer(ROOT)
    key: dict[str, dict[str, Any]] = {}
    items: dict[int, list[dict[str, str]]] = {1: [], 2: []}
    n = tasks = fired = 0
    for suite, suite_tasks in loaded:
        for task in suite_tasks:
            tasks += 1
            tid, src, tgt = task["task_id"], task["source"], task["target_canvas"]
            outcome = resize_with_report(src, tgt, measurer, probe, policy, alternatives=top_k - 1)
            elig = eligible(outcome.finalists, src, tgt, epsilon)
            if len(elig) < 2:
                continue
            fired += 1
            fin = outcome.finalists
            target = f"{tgt['width']}x{tgt['height']}"
            orig, base = renderer.render(src, image_max), renderer.render(fin[0].design, image_max)
            for c in elig[1:]:
                new = renderer.render(fin[c].design, image_max)
                if new == base:
                    continue  # drawn identically: nothing for a judge to see
                n += 1
                group = f"c{n:03d}"
                for order, (a, b) in ((1, (base, new)), (2, (new, base))):
                    vid = f"{group}o{order}"
                    d = out / vid
                    d.mkdir()
                    (d / "original.png").write_bytes(orig)
                    (d / "A.png").write_bytes(a)
                    (d / "B.png").write_bytes(b)
                    key[vid] = {
                        "label": suite.name, "task": tid, "order": order, "tier": "D3", "target": target,
                        "base": "engine:finalist0", "new": f"engine:finalist{c}",
                        "original_png": f"{group}o1/original.png", "base_png": f"{group}o1/A.png",
                        "new_png": f"{group}o1/B.png",
                        "critic": {"base": fin[0].report.score, "new": fin[c].report.score,
                                   "margin": round(fin[0].report.score - fin[c].report.score, 2)},
                        "strategy": {"base": fin[0].strategy, "new": fin[c].strategy},
                        "sha": {"original": _sha(orig), "base": _sha(base), "new": _sha(new)},
                        "image_max": image_max, "epsilon": epsilon,
                    }
                    items[order].append({"id": vid, "dir": str(d.resolve()), "target": target})
    batches = [lst[i:i + BATCH] for order in (1, 2) for lst in [items[order]] for i in range(0, len(lst), BATCH)]
    (out / "key.json").write_text(json.dumps(key, indent=1))
    (out / "batches.json").write_text(json.dumps(batches, indent=1))
    return {"tasks": tasks, "fired": fired, "pairs": n, "batches": len(batches),
            "file": str((out / "batches.json").resolve())}


# What a near-ties round folder holds: the files `near_ties` writes, the judges' verdicts, and the
# results `tools.blind resolve` adds.
_ROUND_FILES = re.compile(r"^(key\.json|batches\.json|results\.json|c\d+o[12]|verdicts[^/]*\.json)$")


def _clear_round(out: Path, force: bool) -> None:
    """Empty `out` for a new round, but only when it is missing, empty, or a round this tool made.

    A folder with anything else in it, the repo root or a folder holding `.git` is never touched,
    and a round with verdicts is replaced only with `force`."""
    if not out.exists():
        return
    if not out.is_dir():
        raise SystemExit(f"{out} is not a folder")
    resolved = out.resolve()
    if resolved == ROOT or resolved in ROOT.parents or (resolved / ".git").exists():
        raise SystemExit(f"refusing to clear {out}: it is the repository or holds one; choose another --out")
    entries = list(out.iterdir())
    if not entries:
        return
    names = {e.name for e in entries}
    foreign = sorted(e.name for e in entries if not _ROUND_FILES.match(e.name)
                     or (e.name.startswith("c") and not e.is_dir()))
    if not {"key.json", "batches.json"} <= names or foreign:
        shown = ", ".join(foreign[:5]) or "no key.json and batches.json"
        raise SystemExit(f"{out} is not a near-ties round made by this tool ({shown}); choose another --out "
                         "or empty it yourself")
    if any(out.glob("verdicts*.json")) and not force:
        raise SystemExit(f"{out} holds verdicts; pass --force to replace the round, or choose another --out")
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


# =============================================================================
# build: judged rounds to a dataset
# =============================================================================


class _Dataset:
    """Pairs, flags and replay rows being collected, with images stored once by content hash."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.images = out / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.pairs: list[dict[str, Any]] = []
        self.flags: list[dict[str, Any]] = []
        self.replay: dict[str, str] = {}
        self.seen: set[tuple[str, frozenset[str]]] = set()
        self.report: Counter[str] = Counter()
        self.sources: list[str] = []

    def store(self, path: Path) -> tuple[str, str]:
        """(relative path, sha256) of an image copied into the dataset."""
        data = path.read_bytes()
        sha = _sha(data)
        dest = self.images / f"{sha}.png"
        if not dest.exists():
            dest.write_bytes(data)
        return f"images/{sha}.png", sha

    def add_pair(self, *, tier: str, round_: str, task: str, target: str, original: Path, base: Path, new: Path,
                 winner: str, judge: str, reps: int, orders: list[list[str | None]], reasons: list[str],
                 base_run: str, new_run: str, critic: dict[str, float] | None) -> dict[str, Any] | None:
        """One judged pair; `winner` is "base", "new" or "tie". Duplicates and identical images are skipped."""
        orig_rel, orig_sha = self.store(original)
        base_rel, base_sha = self.store(base)
        new_rel, new_sha = self.store(new)
        if base_sha == new_sha:
            self.report["skipped_identical"] += 1
            return None
        dedupe = (orig_sha, frozenset((base_sha, new_sha)))
        if dedupe in self.seen:
            self.report["skipped_duplicate"] += 1
            return None
        self.seen.add(dedupe)
        pid = "p_" + _sha(f"{orig_sha}|{base_sha}|{new_sha}".encode())[:12]
        base_is_a = int(_sha(pid.encode()), 16) % 2 == 0
        a_rel, b_rel = (base_rel, new_rel) if base_is_a else (new_rel, base_rel)
        side = {"base": "a" if base_is_a else "b", "new": "b" if base_is_a else "a", "tie": "tie"}
        row = {
            "id": pid, "tier": tier, "round": round_, "split": _split(orig_sha), "task": task, "target": target,
            "original": orig_rel, "a": a_rel, "b": b_rel, "frontier_winner": side[winner],
            "a_is": "base" if base_is_a else "new", "judge": judge, "reps": reps, "orders": orders,
            "reasons": reasons, "base_run": base_run, "new_run": new_run, "critic": critic,
            "sha256": {"original": orig_sha, "base": base_sha, "new": new_sha},
        }
        self.pairs.append(row)
        return row


def _split(original_sha: str) -> str:
    """`tune` or `hold` by the parity of the original's hash, so one source never straddles both."""
    return "tune" if int(original_sha, 16) % 2 == 0 else "hold"


def _round_pairs(ds: _Dataset, rnd: Path, tiers: dict[str, str]) -> None:
    """Decisive (D1 to D3) and all-tie (C1) pairs from one judged blind round; mixed ones (C3) are counted only."""
    key = json.loads((rnd / "key.json").read_text())
    files = sorted(rnd.glob("verdicts*.json"))
    if not files:
        ds.report["rounds_without_verdicts"] += 1
        return
    reps = [_answers(f) for f in files]
    # tools.blind keeps each comparison's target size in batches.json rather than key.json.
    targets: dict[str, str] = {}
    if (rnd / "batches.json").exists():
        targets = {str(it["id"]): str(it.get("target", "")) for batch in json.loads((rnd / "batches.json").read_text())
                   for it in batch}
    groups: dict[str, dict[int, str]] = defaultdict(dict)
    for vid, meta in key.items():
        groups[vid[:-2]][int(meta["order"])] = vid
    ds.sources.append(f"round:{rnd}")
    for group, orders in sorted(groups.items()):
        if 1 not in orders or 2 not in orders:
            continue
        v1, v2 = orders[1], orders[2]
        meta = key[v1]
        answered = []
        reasons = []
        for rep in reps:
            if v1 in rep and v2 in rep:
                answered.append([_norm_winner(rep[v1].get("winner")), _norm_winner(rep[v2].get("winner"))])
                reasons += [str(rep[v1].get("reason", "")), str(rep[v2].get("reason", ""))]
        answered = [o for o in answered if None not in o]
        if not answered:
            continue
        resolved = [_resolve_pair(o1, o2) for o1, o2 in answered]
        if all(r == "new" for r in resolved) or all(r == "base" for r in resolved):
            tier, winner = str(meta.get("tier") or tiers.get(rnd.name, "D2")).upper(), resolved[0]
        elif all(o1 == "tie" and o2 == "tie" for o1, o2 in answered):
            tier, winner = "C1", "tie"
        else:
            ds.report["C3"] += 1
            continue
        d = rnd / v1
        base_png = rnd / meta["base_png"] if "base_png" in meta else d / "A.png"
        new_png = rnd / meta["new_png"] if "new_png" in meta else d / "B.png"
        row = ds.add_pair(tier=tier, round_=rnd.name, task=str(meta.get("task", group)),
                          target=str(meta.get("target") or targets.get(v1, "")), original=d / "original.png",
                          base=base_png,
                          new=new_png, winner=winner, judge="in-session", reps=len(answered), orders=answered,
                          reasons=reasons, base_run=str(meta.get("base", "")), new_run=str(meta.get("new", "")),
                          critic=meta.get("critic"))
        if row is not None and "image_max" in meta:
            # Raw engine renders: verdicts on them replay through the engine unchanged.
            orig, base, new = ((rnd / v1 / "original.png").read_bytes(), base_png.read_bytes(), new_png.read_bytes())
            first = {"new": "B", "base": "A", "tie": "SAME"}[winner]
            second = {"new": "A", "base": "B", "tie": "SAME"}[winner]
            ds.replay[replay_key("compare", (orig, base, new))] = first
            ds.replay[replay_key("compare", (orig, new, base))] = second


def _target_of(run: Path, task: str) -> str:
    rows = {r["task"]: r for r in json.loads((run / "results.json").read_text())["rows"]}
    return str(rows.get(task, {}).get("target", ""))


def _resolve_run(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _pairwise_pairs(ds: _Dataset, path: Path, tiers: dict[str, str]) -> None:
    """Pairs from an API `tools.judge --pair` file: one rep, no per-order answers; ties are C3."""
    data = json.loads(path.read_text())
    summary = data["summary"]
    base_run, new_run = _resolve_run(summary["base_run"]), _resolve_run(summary["new_run"])
    tier = tiers.get(path.stem, tiers.get(path.parent.name, "D2")).upper()
    ds.sources.append(f"pairwise:{path}")
    for v in data.get("verdicts", []):
        if "error" in v or v.get("identical"):
            continue
        if v.get("result") not in ("new", "base"):
            ds.report["C3"] += 1
            continue
        t = v["task"]
        ds.add_pair(tier=tier, round_=path.stem, task=t, target=_target_of(new_run, t),
                    original=new_run / "renders" / f"{t}_source.png", base=base_run / "renders" / f"{t}_output.png",
                    new=new_run / "renders" / f"{t}_output.png", winner=v["result"],
                    judge=f"api:{summary.get('model', '')}", reps=1, orders=[], reasons=list(v.get("reasons", [])),
                    base_run=str(summary["base_run"]), new_run=str(summary["new_run"]), critic=None)


def _audit_pairs(ds: _Dataset, path: Path) -> None:
    """D3 pairs from a `tools.candidates` audit: the critic's pick against each alternative."""
    data = json.loads(path.read_text())
    ds.sources.append(f"audit:{path}")
    for r in data.get("results", []):
        if "error" in r:
            continue
        prefers = r.get("judge_prefers")
        if prefers not in ("alt", "pick"):
            ds.report["C3"] += 1
            continue
        t = r["task"]

        def png(strategy: str, task: str = t) -> Path:
            return path.parent / f"{task}__{strategy.replace('/', '-')}.png"

        base, new = png(r["pick"]), png(r["alt"])
        ds.add_pair(tier="D3", round_=path.parent.name, task=t, target=r.get("target", ""),
                    original=path.parent / f"{t}__source.png", base=base, new=new,
                    winner="new" if prefers == "alt" else "base", judge="api:audit", reps=1, orders=[],
                    reasons=[str(r.get("reason", ""))], base_run=r["pick"], new_run=r["alt"],
                    critic={"base": r["pick_critic"], "new": r["alt_critic"],
                            "margin": round(r["pick_critic"] - r["alt_critic"], 2)})


def issue_codes(texts: list[str]) -> list[str]:
    """Issue codes named in free-text judge issues, by `ISSUE_KEYWORDS`, in priority order."""
    found: set[str] = set()
    for text in texts:
        t = text.lower()
        if "logo" in t and any(w in t for w in ("small", "tiny", "illegib", "missing", "unreadable")):
            found.add("logo_small")
        if "logo" in t and any(w in t for w in ("oversized", "too large", "too big", "dominat", "out of proportion")):
            found.add("logo_large")
        found |= {code for code, words in ISSUE_KEYWORDS.items() if any(w in t for w in words)}
    return [c for c in ISSUES if c in found]


def critic_codes(penalties: dict[str, float]) -> list[str]:
    """Issue codes whose critic penalties (`ISSUE_PENALTIES`) fire on a design."""
    return [c for c in ISSUES if any(penalties.get(k, 0.0) > 0 for k in ISSUE_PENALTIES[c])]


def _absolute_flags(ds: _Dataset, spec: str) -> None:
    """Flag rows from an absolute round, `DIR` or `DIR=RUN` (RUN gives the critic penalties)."""
    folder, _, run = spec.partition("=")
    rnd = Path(folder)
    data = None
    for name in ("scores.json", "scores_raw.json"):
        if (rnd / name).exists():
            data = json.loads((rnd / name).read_text())
            break
    if data is None:
        ds.report["absolute_without_scores"] += 1
        return
    rows = data.get("rows") or data.get("result") or []
    targets = {it["id"]: it.get("target", "") for batch in json.loads((rnd / "batches.json").read_text())
               for it in batch}
    penalties = {}
    if run:
        rows_run = json.loads((Path(run) / "results.json").read_text())["rows"]
        penalties = {r["task"]: r.get("penalties", {}) for r in rows_run}
    ds.sources.append(f"absolute:{spec}")
    for r in rows:
        d = rnd / r["id"]
        if not (d / "output.png").exists():
            continue
        orig_rel, orig_sha = ds.store(d / "original.png")
        img_rel, img_sha = ds.store(d / "output.png")
        pen = penalties.get(r["id"])
        ds.flags.append({
            "id": "f_" + _sha(f"{orig_sha}|{img_sha}".encode())[:12], "round": rnd.name, "task": r["id"],
            "target": targets.get(r["id"], ""), "split": _split(orig_sha), "original": orig_rel, "image": img_rel,
            "overall": r.get("overall"), "issues": list(r.get("issues", [])),
            "codes": issue_codes(list(r.get("issues", []))),
            "critic_penalties": pen, "critic_codes": critic_codes(pen) if pen is not None else None,
        })


def _controls(ds: _Dataset, count: int) -> None:
    """C2: identical a and b, made from a hash-ordered sample of decisive pairs; any decisive answer is bias."""
    decisive = sorted((p for p in ds.pairs if p["tier"] in DECISIVE_TIERS), key=lambda p: _sha(p["id"].encode()))
    for p in decisive[:count]:
        image = p["a"]
        ds.pairs.append({**p, "id": "c2_" + p["id"][2:], "tier": "C2", "a": image, "b": image,
                         "frontier_winner": "tie", "a_is": "same", "orders": [], "reasons": [], "critic": None})


def build(out: Path, rounds: list[Path], tiers: dict[str, str], pairwise: list[Path], audits: list[Path],
          absolutes: list[str], controls: int = 20) -> dict[str, Any]:
    """Write the agreement dataset to `out` and return its manifest."""
    missing = [str(r) for r in rounds if not (r / "key.json").exists()]
    missing += [str(p) for p in [*pairwise, *audits] if not p.exists()]
    missing += [spec for spec in absolutes if not Path(spec.partition("=")[0]).is_dir()]
    if missing:
        raise SystemExit("these inputs do not exist here (judged rounds and bench runs live under the gitignored "
                         "output/ of the machine that made them; see docs/agentic-harness.md section 7):\n  "
                         + "\n  ".join(missing))
    out.mkdir(parents=True, exist_ok=True)
    ds = _Dataset(out)
    for rnd in rounds:
        _round_pairs(ds, rnd, tiers)
    for path in pairwise:
        _pairwise_pairs(ds, path, tiers)
    for path in audits:
        _audit_pairs(ds, path)
    for spec in absolutes:
        _absolute_flags(ds, spec)
    _controls(ds, controls)
    _write_jsonl(out / "pairs.jsonl", ds.pairs)
    _write_jsonl(out / "flags.jsonl", ds.flags)
    _write_jsonl(out / "replay.jsonl", [{"key": k, "raw": v} for k, v in sorted(ds.replay.items())])
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tune": 0, "hold": 0})
    for p in ds.pairs:
        counts[p["tier"]][p["split"]] += 1
    manifest = {
        "builder_version": BUILDER_VERSION, "prompt_version": PROMPT_VERSION,
        "date": dt.date.today().isoformat(), "pairs": len(ds.pairs), "flags": len(ds.flags),
        "replay": len(ds.replay), "counts": dict(sorted(counts.items())), "reported_only": dict(ds.report),
        "distinct_originals": len({p["sha256"]["original"] for p in ds.pairs}), "sources": ds.sources,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


# =============================================================================
# run: ask the configured advisor
# =============================================================================


def run(data: Path, out: Path, kind: str = "compare", prompt_id: str = "p1", split: str = "hold", reps: int = 1,
        limit: int | None = None, env_label: str = "", max_errors: int = 5, drain_s: float = 120.0) -> dict[str, Any]:
    """Ask every pair (both orders, every rep) or every flag row; write calls.jsonl and run.json.

    Each call is appended to calls.jsonl as it is made, and run.json is written first with
    `"stopped": "incomplete"`, so an interrupted run keeps every answer and still scores. After a
    timeout the abandoned call is waited for (up to `drain_s`) before the next request, and a
    "busy" answer is back-pressure: waited out, retried, and never counted as an error."""
    from tools.digests import recorded_env

    cfg = load_agent_config(os.environ)
    if cfg is None:
        raise SystemExit("set CHAMELEON_AGENT and friends to choose an advisor (docs/agentic-harness.md)")
    rows = _dataset_rows(data, kind)
    rows = [r for r in rows if split == "all" or r["split"] == split][:limit]
    advisor = build_advisor(cfg)
    warm_latency, warm_error = warm_up(advisor, cfg.warmup_s) if cfg.warmup_s > 0 else (0.0, None)
    if warm_error:
        raise SystemExit(f"the advisor failed its warmup: {warm_error}")
    timeout = cfg.settings.call_timeout_s
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "data": _stored_path(data), "kind": kind, "prompt": prompt_id if kind == "compare" else "p2a",
        "prompt_version": PROMPT_VERSION, "split": split, "reps": reps, "limit": limit, "env_label": env_label,
        "advisor": advisor.describe(), "backend": cfg.backend, "timeout_s": timeout, "warmup_s": warm_latency,
        "items": len(rows), "calls": 0, "stopped": "incomplete",
        "config": {k: v for k, v in recorded_env(os.environ).items() if k.startswith("CHAMELEON_AGENT")},
    }
    (out / "run.json").write_text(json.dumps(meta, indent=1))
    n_calls = 0
    streak = 0
    stopped = None
    with (out / "calls.jsonl").open("w", encoding="utf-8") as log:
        for row in rows:
            orig = (data / row["original"]).read_bytes()
            shown = [(data / row["a"]).read_bytes(), (data / row["b"]).read_bytes()] if kind == "compare" \
                else [(data / row["image"]).read_bytes()]
            w, h = _target_size(row.get("target", ""), shown[0])
            p = prompt(prompt_id if kind == "compare" else "p2a", w, h)
            orders = (1, 2) if kind == "compare" else (1,)
            for rep in range(reps):
                for order in orders:
                    ims = (orig, *shown) if order == 1 else (orig, shown[1], shown[0])
                    answer = _ask_patiently(advisor, p, ims, timeout, drain_s)
                    if answer.error == "busy":
                        stopped = f"the advisor stayed busy after waiting {drain_s:g} s for an abandoned call"
                        break
                    parsed = _parsed(kind, p, answer)
                    log.write(json.dumps({
                        "pair_id": row["id"], "order": order, "rep": rep, "raw": answer.raw, "parsed": parsed,
                        "probs": dict(answer.probs) if answer.probs else None, "latency_s": answer.latency_s,
                        "error": answer.error, "prompt_id": p.id, "prompt_version": PROMPT_VERSION,
                        "advisor": advisor.describe(), "ru_maxrss": _maxrss(),
                    }, sort_keys=True) + "\n")
                    log.flush()
                    n_calls += 1
                    streak = streak + 1 if answer.error else 0
                    if streak >= max_errors:
                        stopped = f"{streak} transport errors in a row ({answer.error})"
                        break
                if stopped:
                    break
            if stopped:
                break
    meta.update(calls=n_calls, stopped=stopped)
    (out / "run.json").write_text(json.dumps(meta, indent=1))
    return meta


def _ask_patiently(advisor: Any, p: Any, ims: tuple[bytes, ...], timeout: float, drain_s: float) -> Any:
    """One call. A call abandoned at its deadline is waited for before returning, so the next request
    never queues behind it; a "busy" answer is waited out and asked again once."""
    for _attempt in range(2):
        answer = call_with_deadline(lambda: advisor.ask(p, ims, timeout), timeout + GRACE_S)
        if answer.error in ("timeout", "busy"):
            drain_abandoned(drain_s)
        if answer.error != "busy":
            return answer
    return answer


def _parsed(kind: str, p: Any, answer: Any) -> Any:
    """The parsed answer as calls.jsonl stores it, or None; never raises."""
    if answer.error:
        return None
    try:
        if kind == "compare":
            return parse_verdict(answer.raw, p.labels, shows_original(p))
        flags = parse_flags(answer.raw)
        return None if flags is None else {"flags": list(flags[0]), "worst": flags[1]}
    except Exception:  # the parsers never raise; if one did, the answer is unusable
        return None


def _maxrss() -> int | None:
    """Peak resident memory of this client (KB on Linux, bytes on macOS)."""
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, OSError):
        return None


# =============================================================================
# score
# =============================================================================


def _model_winner(first: str | None, second: str | None) -> str:
    """"a", "b" or "tie" from order 1 (a shown as A) and order 2 (b shown as A)."""
    r = resolve(first, second)
    return "b" if r == "challenger" else "a" if r == "incumbent" else "tie"


def _outcomes(pairs: dict[str, dict[str, Any]], calls: list[dict[str, Any]], rep: int = 0) -> dict[str, dict[str, Any]]:
    """Per pair, for one rep: both orders' parsed answers, the model's winner and the pair latency."""
    by: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for c in calls:
        if c["rep"] == rep and c["pair_id"] in pairs:
            by[c["pair_id"]][c["order"]] = c
    out = {}
    for pid, orders in by.items():
        if 1 in orders and 2 in orders:
            v1, v2 = orders[1]["parsed"], orders[2]["parsed"]
            out[pid] = {"v1": v1, "v2": v2, "model": _model_winner(v1, v2),
                        "latency": (orders[1]["latency_s"] or 0) + (orders[2]["latency_s"] or 0),
                        "calls": (orders[1], orders[2])}
    return out


def _agreement(rows: list[dict[str, Any]], model: dict[str, str]) -> dict[str, Any]:
    """Agree, contradict and abstain counts, precision and coverage of `model` against the frontier."""
    n = len(rows)
    agree = sum(model[p["id"]] == p["frontier_winner"] for p in rows)
    abstain = sum(model[p["id"]] == "tie" for p in rows)
    contradict = n - agree - abstain
    return {"n": n, "agree": rate(agree, n), "contradict": rate(contradict, n), "abstain": rate(abstain, n),
            "precision": rate(agree, agree + contradict), "coverage": rate(agree + contradict, n)}


def _side(pair: dict[str, Any], letter: str) -> str:
    """"base", "new" or "tie" for a pair's "a", "b" or "tie"."""
    if letter == "tie":
        return "tie"
    return pair["a_is"] if letter == "a" else ("new" if pair["a_is"] == "base" else "base")


def _soft(pairs: dict[str, dict[str, Any]], outcomes: dict[str, dict[str, Any]], tau: str) -> dict[str, Any] | None:
    """Soft verdicts from first-token probabilities, with τ tuned on `tune` and applied to `hold`."""
    def margin(call: dict[str, Any]) -> float | None:
        p = call.get("probs") or {}
        if "A" not in p and "B" not in p:
            return None
        return math.log(max(p.get("A", 0.0), 1e-6)) - math.log(max(p.get("B", 0.0), 1e-6))

    c2 = [m for pid, o in outcomes.items() if pairs[pid]["tier"] == "C2" for c in o["calls"]
          if (m := margin(c)) is not None]
    if not any(margin(c) is not None for o in outcomes.values() for c in o["calls"]):
        return None
    mu = statistics.mean(c2) if c2 else 0.0
    scored = []
    for pid, o in outcomes.items():
        m1, m2 = margin(o["calls"][0]), margin(o["calls"][1])
        if m1 is None or m2 is None:
            continue
        scored.append((pairs[pid], ((m1 - mu) - (m2 - mu)) / 2))

    def at(t: float, split: str) -> dict[str, Any]:
        rows = [(p, m) for p, m in scored if p["split"] == split and p["tier"] in DECISIVE_TIERS]
        model = {p["id"]: ("a" if m > t else "b" if m < -t else "tie") for p, m in rows}
        return _agreement([p for p, _ in rows], model)

    if tau == "auto":
        chosen = None
        for t in sorted({abs(m) for _, m in scored} | {0.0}):
            res = at(t, "tune")
            prec = res["precision"]["rate"]
            if prec is not None and prec >= 0.8 and (chosen is None or res["coverage"]["k"] > chosen[1]):
                chosen = (t, res["coverage"]["k"])
        t_final = chosen[0] if chosen else None
    else:
        t_final = float(tau)
    return {"c2_mean_margin": round(mu, 4), "tau": t_final,
            "hold": at(t_final, "hold") if t_final is not None else None}


def score(run_dir: Path, rerun: Path | None = None, tau: str = "auto", data: Path | None = None) -> dict[str, Any]:
    """Agreement metrics and gates for one run, written to metrics.json and report.md.

    `data` overrides the dataset folder run.json names, e.g. for a run made in the amd64 container."""
    meta = json.loads((run_dir / "run.json").read_text())
    data = _data_of(meta, data)
    _dataset_rows(data, meta["kind"])  # exits on a missing dataset rather than scoring zero pairs
    calls = _read_jsonl(run_dir / "calls.jsonl")
    if meta["kind"] == "flags":
        metrics = _score_flags(data, calls, meta)
    else:
        metrics = _score_compare(data, calls, meta, rerun, tau)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
    (run_dir / "report.md").write_text(_report(metrics))
    return metrics


def _score_compare(data: Path, calls: list[dict[str, Any]], meta: dict[str, Any], rerun: Path | None,
                   tau: str) -> dict[str, Any]:
    pairs = {p["id"]: p for p in _read_jsonl(data / "pairs.jsonl")}
    outcomes = _outcomes(pairs, calls)
    model = {pid: o["model"] for pid, o in outcomes.items()}
    asked = [pairs[pid] for pid in outcomes]
    hold = [p for p in asked if p["split"] == "hold"]

    def tier_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        out = {t: _agreement([p for p in rows if p["tier"] == t], model) for t in DECISIVE_TIERS}
        out["pooled"] = _agreement([p for p in rows if p["tier"] in DECISIVE_TIERS], model)
        return out

    def overrides(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Of the D3 pairs where the model would replace the engine's default, how many the frontier backs."""
        picked = [p for p in rows if p["tier"] == "D3" and _side(p, model[p["id"]]) == "new"]
        return rate(sum(_side(p, p["frontier_winner"]) == "new" for p in picked), len(picked))

    def position(rows: list[dict[str, Any]]) -> dict[str, Any]:
        both = [outcomes[p["id"]] for p in rows if outcomes[p["id"]]["v1"] is not None
                and outcomes[p["id"]]["v2"] is not None]
        same = sum(o["v1"] == o["v2"] and o["v1"] in ("A", "B") for o in both)
        return rate(same, len(both))

    def first_slot(rows: list[dict[str, Any]]) -> dict[str, Any]:
        letters = [c["parsed"] for p in rows for c in outcomes[p["id"]]["calls"] if c["parsed"] in ("A", "B")]
        return rate(sum(v == "A" for v in letters), len(letters))

    def slot_shares(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """First-slot share of A/B answers, raw and debiased by the C2 letter prior (identical images)."""
        raw_first = first_slot([p for p in rows if p["tier"] != "C2"])
        prior = first_slot([p for p in rows if p["tier"] == "C2"])
        debiased = None
        if raw_first["rate"] is not None:
            debiased = raw_first["rate"] - ((prior["rate"] - 0.5) if prior["rate"] is not None else 0.0)
            debiased = round(min(1.0, max(0.0, debiased)), 4)
        return {"raw": raw_first, "c2_prior": prior, "debiased": debiased}

    def c1_false_decisive(rows: list[dict[str, Any]]) -> dict[str, Any]:
        c1 = [p for p in rows if p["tier"] == "C1"]
        return rate(sum(model[p["id"]] != "tie" for p in c1), len(c1))

    n_calls = len(calls)
    errors = sum(bool(c["error"]) for c in calls)
    invalid = sum(c["parsed"] is None and not c["error"] for c in calls)
    same = sum(c["parsed"] == "SAME" for c in calls)
    lat_call = [c["latency_s"] for c in calls if c["latency_s"] is not None]
    lat_pair = [o["latency"] for o in outcomes.values()]
    hold_pair_lat = [outcomes[p["id"]]["latency"] for p in hold]
    determinism = _determinism(calls, rerun)
    rng = random.Random(0)
    rand_model = {p["id"]: _model_winner(rng.choice(["A", "B", "SAME"]), rng.choice(["A", "B", "SAME"]))
                  for p in asked}
    d3 = [p for p in hold if p["tier"] == "D3" and p.get("critic")]
    critic_model = {p["id"]: ("a" if (p["critic"]["base"] >= p["critic"]["new"]) == (p["a_is"] == "base") else "b")
                    for p in d3}
    metrics: dict[str, Any] = {
        "run": meta, "pairs_scored": len(outcomes),
        "all": tier_metrics(asked), "hold": tier_metrics(hold),
        "d3_override_precision": {"all": overrides(asked), "hold": overrides(hold)},
        "position_bias": {"all": position(asked), "hold": position(hold)},
        "first_slot": slot_shares(hold), "first_slot_all": slot_shares(asked),
        "per_call": {"calls": n_calls, "same": rate(same, n_calls), "invalid": rate(invalid, n_calls),
                     "error": rate(errors, n_calls)},
        "c1_false_decisive": c1_false_decisive(hold), "c1_false_decisive_all": c1_false_decisive(asked),
        "determinism": determinism,
        "latency": {"call_p50": percentile(lat_call, 50), "call_p95": percentile(lat_call, 95),
                    "call_max": max(lat_call, default=None), "pair_p50": percentile(lat_pair, 50),
                    "pair_p95": percentile(lat_pair, 95), "pair_max": max(lat_pair, default=None),
                    "hold_pair_p95": percentile(hold_pair_lat, 95), "cold_load_s": meta.get("warmup_s")},
        "baselines": {
            "always_a": _agreement([p for p in hold if p["tier"] in DECISIVE_TIERS], {p["id"]: "tie" for p in hold}),
            "random_seed0": _agreement([p for p in hold if p["tier"] in DECISIVE_TIERS], rand_model),
            "critic_d3": _agreement(d3, critic_model),
            "frontier_vs_frontier": FRONTIER_VS_FRONTIER,
        },
        "soft": _soft(pairs, outcomes, tau),
    }
    metrics["gates"] = gates(metrics, hold, meta.get("env_label", ""))
    return metrics


def _determinism(calls: list[dict[str, Any]], rerun: Path | None) -> dict[str, Any] | None:
    """Share of calls answered the same across reps of this run, or against a rerun."""
    if rerun is not None:
        other = {(c["pair_id"], c["order"], c["rep"]): c["parsed"] for c in _read_jsonl(rerun / "calls.jsonl")}
        both = [(c["parsed"], other[(c["pair_id"], c["order"], c["rep"])]) for c in calls
                if (c["pair_id"], c["order"], c["rep"]) in other]
        return {"source": "rerun", **rate(sum(a == b for a, b in both), len(both))}
    by: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for c in calls:
        by[(c["pair_id"], c["order"])].append(json.dumps(c["parsed"], sort_keys=True))
    multi = [v for v in by.values() if len(v) > 1]
    if not multi:
        return None
    return {"source": "reps", **rate(sum(len(set(v)) == 1 for v in multi), len(multi))}


def gates(metrics: dict[str, Any], hold: list[dict[str, Any]], env_label: str) -> dict[str, Any]:
    """The data floor, then every no-go check, on the `hold` split."""
    g = GATES
    decisive = [p for p in hold if p["tier"] in DECISIVE_TIERS]
    covered = set().union(*(families(p.get("target", "")) for p in decisive)) if decisive else set()
    floor = {
        "decisive": len(decisive) >= g["min_decisive"],
        "d3": sum(p["tier"] == "D3" for p in decisive) >= g["min_d3"],
        "sources": len({p["sha256"]["original"] for p in decisive}) >= g["min_sources"],
        "families": all(f in covered for f in g["families"]),
        "controls": sum(p["tier"] in CONTROL_TIERS for p in hold) >= g["min_controls"],
    }
    h = metrics["hold"]
    pc = metrics["per_call"]
    lo, hi = g["first_slot_range"]
    bad_calls = (pc["invalid"]["k"] + pc["error"]["k"]) / pc["calls"] if pc["calls"] else 1.0
    override = metrics["d3_override_precision"]["hold"]["rate"]
    debiased = metrics["first_slot"]["debiased"]
    det = metrics["determinism"]
    p95 = metrics["latency"]["hold_pair_p95"]
    checks = {
        "invalid_or_error": bad_calls <= g["max_invalid_or_error_rate"],
        "d1_contradictions": h["D1"]["contradict"]["k"] <= g["max_d1_contradictions"],
        "contradiction_rate": (h["pooled"]["contradict"]["rate"] or 0.0) <= g["max_contradiction_rate"]
        and (h["pooled"]["contradict"]["hi"] or 0.0) <= g["max_contradiction_upper"],
        "precision_lower": (h["pooled"]["precision"]["lo"] or 0.0) >= g["min_precision_lower"],
        "d3_override_precision": override is not None and override >= g["min_d3_override_precision"],
        "position_bias": (metrics["position_bias"]["hold"]["rate"] or 0.0) <= g["max_position_bias"],
        "first_slot": debiased is None or lo <= debiased <= hi,
        "c1_false_decisive": (metrics["c1_false_decisive"]["rate"] or 0.0) <= g["max_c1_false_decisive"],
        "determinism": det is not None and (det["rate"] or 0.0) >= g["min_determinism"],
        "pair_p95": env_label != g["latency_env"] or (p95 is not None and p95 <= g["max_pair_p95_s"]),
        "d3_coverage": (h["D3"]["coverage"]["rate"] or 0.0) >= g["min_d3_coverage"],
    }
    if not all(floor.values()):
        verdict = "insufficient_data"
    elif not all(checks.values()):
        verdict = "no-go"
    else:
        verdict = "go-to-e2e"
    return {"verdict": verdict, "floor": floor, "checks": checks, "families_covered": sorted(covered),
            "latency_judged": env_label == g["latency_env"]}


def _score_flags(data: Path, calls: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Per-code agreement of the model's flags with the frontier's issue text and with the critic."""
    rows = {r["id"]: r for r in _read_jsonl(data / "flags.jsonl")}
    first = [c for c in calls if c["rep"] == 0 and c["pair_id"] in rows]
    per_code = {}
    for code in ISSUES:
        tp = fp = fn = 0
        for c in first:
            said = code in ((c["parsed"] or {}).get("flags", []))
            truth = code in rows[c["pair_id"]]["codes"]
            tp += said and truth
            fp += said and not truth
            fn += truth and not said
        per_code[code] = {"precision": rate(tp, tp + fp), "recall": rate(tp, tp + fn)}
    n = len(calls)
    lat = [c["latency_s"] for c in calls if c["latency_s"] is not None]
    return {
        "run": meta, "rows_scored": len(first), "per_code": per_code,
        "none_rate": rate(sum((c["parsed"] or {}).get("worst") == "none" for c in first), len(first)),
        "per_call": {"calls": n, "invalid": rate(sum(c["parsed"] is None and not c["error"] for c in calls), n),
                     "error": rate(sum(bool(c["error"]) for c in calls), n)},
        "latency": {"call_p50": percentile(lat, 50), "call_p95": percentile(lat, 95),
                    "call_max": max(lat, default=None)},
        "determinism": _determinism(calls, None),
    }


def _report(m: dict[str, Any]) -> str:
    """metrics.json as a short Markdown report."""
    run_meta = m["run"]
    lines = [f"# Agreement run: {run_meta['advisor']} ({run_meta['kind']}, prompt {run_meta['prompt']})", "",
             f"- data: `{run_meta['data']}`, split `{run_meta['split']}`, env `{run_meta.get('env_label') or 'unset'}`",
             f"- calls: {m['per_call']['calls']}, invalid {m['per_call']['invalid']['rate']}, "
             f"errors {m['per_call']['error']['rate']}"]
    if run_meta["kind"] == "flags":
        lines += ["", "| Code | Precision | Recall |", "|---|---|---|"]
        lines += [f"| {c} | {v['precision']['rate']} ({v['precision']['k']}/{v['precision']['n']}) | "
                  f"{v['recall']['rate']} ({v['recall']['k']}/{v['recall']['n']}) |" for c, v in m["per_code"].items()]
        return "\n".join(lines) + "\n"
    g = m["gates"]
    lines += [f"- verdict: **{g['verdict']}**", "",
              "| Split | Tier | n | Agree | Contradict | Abstain | Precision (95% CI) | Coverage |",
              "|---|---|---|---|---|---|---|---|"]
    for split in ("all", "hold"):
        for tier, r in m[split].items():
            if r["n"]:
                p = r["precision"]
                lines.append(f"| {split} | {tier} | {r['n']} | {r['agree']['k']} | {r['contradict']['k']} | "
                             f"{r['abstain']['k']} | {p['rate']} ({p['lo']} to {p['hi']}) | {r['coverage']['rate']} |")
    lat = m["latency"]
    lines += ["", f"- D3 override precision (hold): {m['d3_override_precision']['hold']}",
              f"- position bias (hold): {m['position_bias']['hold']['rate']}",
              f"- first slot (hold): raw {m['first_slot']['raw']['rate']}, "
              f"C2 prior {m['first_slot']['c2_prior']['rate']}, debiased {m['first_slot']['debiased']}",
              f"- C1 false-decisive (hold): {m['c1_false_decisive']['rate']}", f"- determinism: {m['determinism']}",
              f"- latency per call p50 {lat['call_p50']} s, p95 {lat['call_p95']} s; per pair p95 {lat['pair_p95']} s; "
              f"cold load {lat['cold_load_s']} s",
              f"- baselines: always-A coverage {m['baselines']['always_a']['coverage']['rate']}, random precision "
              f"{m['baselines']['random_seed0']['precision']['rate']}, critic on D3 precision "
              f"{m['baselines']['critic_d3']['precision']['rate']}",
              "", "## Gates", "", "| Gate | Pass |", "|---|---|"]
    lines += [f"| floor: {k} | {v} |" for k, v in g["floor"].items()]
    lines += [f"| {k} | {v} |" for k, v in g["checks"].items()]
    return "\n".join(lines) + "\n"


def mcnemar(run_a: Path, run_b: Path, data: Path | None = None) -> dict[str, Any]:
    """Exact McNemar test on decisive pairs both runs scored: does one agree with the frontier more often?"""
    def correct(run_dir: Path) -> dict[str, bool]:
        meta = json.loads((run_dir / "run.json").read_text())
        pairs = {p["id"]: p for p in _dataset_rows(_data_of(meta, data), "compare")}
        outs = _outcomes(pairs, _read_jsonl(run_dir / "calls.jsonl"))
        return {pid: o["model"] == pairs[pid]["frontier_winner"] for pid, o in outs.items()
                if pairs[pid]["tier"] in DECISIVE_TIERS}

    a, b = correct(run_a), correct(run_b)
    shared = sorted(set(a) & set(b))
    only_a = sum(a[p] and not b[p] for p in shared)
    only_b = sum(b[p] and not a[p] for p in shared)
    return {"pairs": len(shared), "a_right_b_wrong": only_a, "b_right_a_wrong": only_b,
            "p_two_sided": round(binom_two_sided(min(only_a, only_b), only_a + only_b), 4)}


# =============================================================================
# doctor
# =============================================================================


def _synthetic_designs() -> tuple[bytes, bytes, bytes]:
    """An original poster, a clean resize of it and one with text over text, drawn with PIL."""
    from PIL import Image, ImageDraw, ImageFont

    font_path = ROOT / "brand_kit" / "fonts" / "SolsticeSans-Bold.ttf"

    def font(size: int) -> Any:
        try:
            return ImageFont.truetype(str(font_path), size)
        except OSError:
            return ImageFont.load_default()

    def draw(size: tuple[int, int], head: tuple[int, int], body: tuple[int, int], scale: float) -> bytes:
        im = Image.new("RGB", size, (250, 236, 214))
        d = ImageDraw.Draw(im)
        d.rectangle([size[0] - int(150 * scale), int(30 * scale), size[0] - int(30 * scale), int(150 * scale)],
                    fill=(230, 120, 60))
        d.text(head, "SUMMER SALE", font=font(int(56 * scale)), fill=(40, 20, 50))
        d.text(body, "Fresh drinks, cold and bright", font=font(int(30 * scale)), fill=(40, 20, 50))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

    original = draw((800, 800), (60, 300), (60, 400), 1.0)
    clean = draw((1200, 400), (60, 120), (60, 220), 0.9)
    overlap = draw((1200, 400), (60, 150), (80, 165), 0.9)
    return original, clean, overlap


_THINKING_HINTS = {
    "ollama": "Ollama: CHAMELEON_AGENT_EXTRA='{\"reasoning_effort\":\"none\"}' (it ignores chat_template_kwargs)",
    "llama-server": "llama-server: start it with --reasoning off, or CHAMELEON_AGENT_EXTRA="
                    "'{\"chat_template_kwargs\":{\"enable_thinking\":false}}'",
    "lm-studio": "LM Studio: turn thinking off in the model's settings, or try CHAMELEON_AGENT_EXTRA="
                 "'{\"reasoning_effort\":\"none\"}' if your version accepts it",
    "mlx-vlm": "mlx-vlm: try CHAMELEON_AGENT_EXTRA='{\"chat_template_kwargs\":{\"enable_thinking\":false}}'",
}
# Each server's default port, to put its hint first.
_DEFAULT_PORTS = {11434: "ollama", 8080: "llama-server", 1234: "lm-studio", 8081: "mlx-vlm"}


def _thinking_hints(url: str) -> list[str]:
    """How each server turns thinking off, the one on `url`'s default port first."""
    try:
        port = urllib.parse.urlsplit(url).port if url else None
    except ValueError:
        port = None
    first = _DEFAULT_PORTS.get(port or -1)
    order = ([first] if first else []) + [k for k in _THINKING_HINTS if k != first]
    return [_THINKING_HINTS[k] for k in order]


def doctor() -> int:
    """One synthetic comparison in both orders, with what to check when it goes wrong."""
    cfg = load_agent_config(os.environ)
    if cfg is None:
        print("CHAMELEON_AGENT is off or invalid; set it (and friends) first. See docs/agentic-harness.md.")
        return 1
    print(f"backend {cfg.backend}, prompt {cfg.settings.prompt}, constrain {cfg.constrain}, "
          f"image_form {cfg.image_form}, timeout {cfg.settings.call_timeout_s} s")
    if cfg.backend == "http":
        try:
            with http_opener().open(cfg.url.rstrip("/") + "/models", timeout=10) as resp:
                ids = [m.get("id") for m in json.loads(resp.read()).get("data", [])]
            print(f"GET /models: {ids}")
            if cfg.model and cfg.model not in ids:
                print(f"  warning: CHAMELEON_AGENT_MODEL={cfg.model!r} is not listed")
        except Exception as exc:  # report and carry on: some servers do not list models
            print(f"GET /models failed: {exc!r}. Is the server running on {cfg.url}?")
    advisor = build_advisor(cfg)
    original, clean, overlap = _synthetic_designs()
    p = prompt(cfg.settings.prompt, 1200, 400)
    ok = True
    verdicts = []
    for order, ims in ((1, (original, clean, overlap)), (2, (original, overlap, clean))):
        t = max(cfg.settings.call_timeout_s, cfg.warmup_s)
        answer = call_with_deadline(lambda ims=ims, t=t: advisor.ask(p, ims, t), t + GRACE_S)
        parsed = _parsed("compare", p, answer)
        verdicts.append(parsed)
        print(f"order {order}: {answer.latency_s:.2f} s, error {answer.error}, raw {answer.raw!r}, parsed {parsed}")
        if answer.error:
            ok = False
            if answer.error.startswith("http_4"):
                print("  the server rejected the request: try CHAMELEON_AGENT_IMAGE_FORM=string, "
                      "or check the model has vision (a projector / mmproj)")
        if "<think>" in answer.raw or (not answer.raw.strip() and not answer.error):
            print("  the model is thinking or said nothing; turn thinking off the way this server accepts it:")
            for hint in _thinking_hints(cfg.url if cfg.backend == "http" else ""):
                print(f"    {hint}")
    expected = resolve(*verdicts)
    print(f"both orders: {expected} (the clean layout is A in order 1; 'incumbent' is the right answer)")
    if verdicts[0] is not None and verdicts[0] == verdicts[1]:
        print("  the same letter in both orders: position bias, or the model cannot see the images")
    return 0 if ok else 1


# =============================================================================
# CLI
# =============================================================================


def _pairs_arg(values: list[str] | None) -> dict[str, str]:
    out = {}
    for v in values or []:
        name, _, tier = v.partition("=")
        out[name] = tier.upper()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Agreement evaluation for the optional model harness.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    nt = sub.add_parser("near-ties", help="lay out the engine's near-tie finalists as a blind round")
    nt.add_argument("--suite", type=Path, action="append", required=True)
    nt.add_argument("--out", type=Path, required=True)
    nt.add_argument("--epsilon", type=float, default=3.0)
    nt.add_argument("--top-k", type=int, default=4)
    nt.add_argument("--image-max", type=int, default=1024)
    nt.add_argument("--force", action="store_true", help="replace a round that already holds verdicts")
    bd = sub.add_parser("build", help="build the agreement dataset from judged rounds")
    bd.add_argument("--out", type=Path, required=True)
    bd.add_argument("--round", type=Path, action="append", default=[], help="a judged blind round folder")
    bd.add_argument("--tier", action="append", help="ROUND=D1 or D2 (default D2); near-ties rounds are D3")
    bd.add_argument("--pairwise", type=Path, action="append", default=[], help="a tools.judge pairwise file")
    bd.add_argument("--audit", type=Path, action="append", default=[], help="a tools.candidates audit.json")
    bd.add_argument("--absolute", action="append", default=[], help="an absolute round, DIR or DIR=RUN")
    bd.add_argument("--controls", type=int, default=20)
    rn = sub.add_parser("run", help="ask the configured advisor every pair")
    rn.add_argument("--data", type=Path, required=True)
    rn.add_argument("--out", type=Path, required=True)
    rn.add_argument("--kind", choices=("compare", "flags"), default="compare")
    rn.add_argument("--prompt", default="p1")
    rn.add_argument("--split", choices=("hold", "tune", "all"), default="hold")
    rn.add_argument("--reps", type=int, default=1)
    rn.add_argument("--limit", type=int)
    rn.add_argument("--env-label", default="")
    rn.add_argument("--max-errors", type=int, default=5)
    rn.add_argument("--drain-s", type=float, default=120.0,
                    help="longest wait for a timed-out call to finish before the next request")
    sc = sub.add_parser("score", help="metrics and gates for one run")
    sc.add_argument("--run", type=Path, required=True)
    sc.add_argument("--rerun", type=Path)
    sc.add_argument("--tau", default="auto")
    sc.add_argument("--data", type=Path, help="the dataset folder, when run.json's path does not exist here")
    mc = sub.add_parser("mcnemar", help="exact McNemar test between two runs")
    mc.add_argument("run_a", type=Path)
    mc.add_argument("run_b", type=Path)
    mc.add_argument("--data", type=Path, help="the dataset folder, when run.json's path does not exist here")
    sub.add_parser("doctor", help="one synthetic comparison against the configured advisor")
    args = parser.parse_args()
    if args.cmd == "near-ties":
        result: Any = near_ties(args.suite, args.out, args.epsilon, args.top_k, args.force, args.image_max)
    elif args.cmd == "build":
        result = build(args.out, args.round, _pairs_arg(args.tier), args.pairwise, args.audit, args.absolute,
                       args.controls)
    elif args.cmd == "run":
        result = run(args.data, args.out, args.kind, args.prompt, args.split, args.reps, args.limit,
                     args.env_label, args.max_errors, args.drain_s)
    elif args.cmd == "score":
        m = score(args.run, args.rerun, args.tau, args.data)
        result = {"gates": m.get("gates"), "report": str(args.run / "report.md")}
    elif args.cmd == "mcnemar":
        result = mcnemar(args.run_a, args.run_b, args.data)
    else:
        return doctor()
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
