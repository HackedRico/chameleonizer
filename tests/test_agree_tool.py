from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from engine.core.advice import replay_key
from tools import agree

# =============================================================================
# Module Overview
# =============================================================================
# The agreement-evaluation tool end to end without a model: the dataset
# builder on a synthetic judged round, a scripted advisor run scored against
# hand-computed metrics, the statistics behind the gates, and the near-ties
# layout on one real task.

ROOT = Path(__file__).resolve().parents[1]


def _png(color: tuple[int, int, int], size=(64, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def _round(folder: Path, groups: dict[str, tuple[bytes, bytes, bytes]], verdicts: list[dict[str, tuple[str, str]]],
           extra_meta: dict | None = None) -> Path:
    """A blind round as tools.blind lays it out: order 1 shows base as A."""
    key = {}
    for group, (orig, base, new) in groups.items():
        for order, (a, b) in ((1, (base, new)), (2, (new, base))):
            d = folder / f"{group}o{order}"
            d.mkdir(parents=True)
            (d / "original.png").write_bytes(orig)
            (d / "A.png").write_bytes(a)
            (d / "B.png").write_bytes(b)
            key[f"{group}o{order}"] = {"label": "dev", "task": f"task_{group}", "order": order, "base": "runs/base",
                                       "new": "runs/new", "target": "728x90", **(extra_meta or {})}
    (folder / "key.json").write_text(json.dumps(key))
    for i, rep in enumerate(verdicts):
        rows = [{"id": f"{g}o{o}", "winner": w[o - 1], "reason": f"r{g}{o}"} for g, w in rep.items() for o in (1, 2)]
        (folder / f"verdicts_rep{i}.json").write_text(json.dumps({"result": rows} if i else rows))
    return folder


def _images(n: int) -> list[tuple[bytes, bytes, bytes]]:
    return [(_png((10 * i, 0, 0)), _png((0, 10 * i, 0)), _png((0, 0, 10 * i + 5))) for i in range(1, n + 1)]


def test_builder_classifies_dedupes_and_assigns(tmp_path):
    imgs = _images(3)
    groups = {"c001": imgs[0], "c002": imgs[1], "c003": imgs[2]}
    reps = [{"c001": ("B", "A"), "c002": ("tie", "tie"), "c003": ("B", "A")},
            {"c001": ("B", "A"), "c002": ("tie", "tie"), "c003": ("A", "A")}]
    r1 = _round(tmp_path / "blind" / "r1", groups, reps)
    r2 = _round(tmp_path / "blind" / "r2", {"c001": imgs[0]}, [{"c001": ("B", "A")}])  # the same pair again
    out = tmp_path / "data"
    manifest = agree.build(out, [r1, r2], {"r1": "D1"}, [], [], [], controls=1)

    pairs = [json.loads(line) for line in (out / "pairs.jsonl").read_text().splitlines()]
    by_tier = {p["tier"]: p for p in pairs}
    assert sorted(p["tier"] for p in pairs) == ["C1", "C2", "D1"]
    assert manifest["reported_only"] == {"C3": 1, "skipped_duplicate": 1}
    assert manifest["pairs"] == 3 and (out / "manifest.json").exists()
    d1 = by_tier["D1"]
    assert d1["reps"] == 2 and d1["orders"] == [["B", "A"], ["B", "A"]] and d1["round"] == "r1"
    assert d1["a_is"] == ("base" if int(hashlib.sha256(d1["id"].encode()).hexdigest(), 16) % 2 == 0 else "new")
    assert d1["frontier_winner"] == ("b" if d1["a_is"] == "base" else "a")  # the new output won
    orig_sha = hashlib.sha256(imgs[0][0]).hexdigest()
    assert d1["split"] == ("tune" if int(orig_sha, 16) % 2 == 0 else "hold")
    for p in pairs:
        for key in ("original", "a", "b"):
            assert not Path(p[key]).is_absolute() and p[key].startswith("images/") and (out / p[key]).exists()
    assert (out / d1["a"]).read_bytes() == (imgs[0][1] if d1["a_is"] == "base" else imgs[0][2])
    assert by_tier["C1"]["frontier_winner"] == "tie"
    c2 = by_tier["C2"]
    assert c2["a"] == c2["b"] and c2["frontier_winner"] == "tie"
    assert (out / "replay.jsonl").read_text() == ""  # bench renders do not replay through the engine


def test_builder_writes_replay_keys_for_engine_renders(tmp_path):
    orig, base, new = _images(1)[0]
    rnd = _round(tmp_path / "nt", {"c001": (orig, base, new)}, [{"c001": ("B", "A")}, {"c001": ("B", "A")}],
                 {"tier": "D3", "image_max": 1024, "critic": {"base": 95.0, "new": 94.0, "margin": 1.0}})
    agree.build(tmp_path / "data", [rnd], {}, [], [], [], controls=0)
    pair = json.loads((tmp_path / "data" / "pairs.jsonl").read_text())
    assert pair["tier"] == "D3" and pair["critic"]["margin"] == 1.0
    lines = (tmp_path / "data" / "replay.jsonl").read_text().splitlines()
    replay = {r["key"]: r["raw"] for r in map(json.loads, lines)}
    assert replay == {replay_key("compare", (orig, base, new)): "B", replay_key("compare", (orig, new, base)): "A"}


def test_wilson_matches_published_values():
    assert agree.wilson(0, 30)[1] == pytest.approx(0.1135, abs=5e-4)
    assert agree.wilson(30, 30)[0] == pytest.approx(0.8865, abs=5e-4)
    lo, hi = agree.wilson(15, 30)
    assert (lo, hi) == (pytest.approx(0.3315, abs=5e-4), pytest.approx(0.6685, abs=5e-4))
    assert agree.wilson(0, 0) == (None, None)
    assert agree.binom_two_sided(0, 6) == pytest.approx(0.03125)
    assert agree.binom_two_sided(3, 6) == 1.0


def test_gate_table():
    g = agree.GATES
    assert (g["min_decisive"], g["min_d3"], g["min_sources"], g["min_controls"]) == (60, 30, 20, 20)
    assert set(g["families"]) == {"strip", "banner", "square", "portrait", "tall", "print", "tiny"}
    assert (g["max_invalid_or_error_rate"], g["max_d1_contradictions"], g["max_contradiction_rate"],
            g["max_contradiction_upper"]) == (0.01, 0, 0.05, 0.12)
    assert (g["min_precision_lower"], g["min_d3_override_precision"], g["max_position_bias"]) == (0.70, 0.70, 0.20)
    assert (g["first_slot_range"], g["max_c1_false_decisive"], g["min_determinism"]) == ((0.40, 0.60), 0.30, 0.98)
    assert (g["max_pair_p95_s"], g["latency_env"], g["min_d3_coverage"]) == (8.0, "amd64-cpu4", 0.30)
    assert agree.families("320x50") == {"strip", "tiny"} and agree.families("2480x3508") == {"portrait", "print"}
    assert agree.families("1080x1920") == {"tall"} and agree.families("1080x1080") == {"square"}
    assert agree.families("970x250") == {"banner"}
    docs = (ROOT / "docs" / "agentic-harness.md").read_text()
    for text in ("60 decisive", "30 of them D3", "Wilson upper bound exceeds 12%", "0.40 to 0.60", "8 s"):
        assert text in docs, text


def _dataset(folder: Path) -> list[dict]:
    """Six hand-made pairs, all on `hold`, with a known frontier verdict each."""
    (folder / "images").mkdir(parents=True)
    rows = []
    specs = [("P1", "D3", "a", "new"), ("P2", "D3", "b", "new"), ("P3", "D2", "a", "base"),
             ("P4", "D1", "b", "base"), ("P5", "C1", "tie", "base"), ("P6", "C2", "tie", "same")]
    for i, (pid, tier, winner, a_is) in enumerate(specs, start=1):
        names = []
        for color in ((i * 20, 0, 0), (0, i * 20, 0), (0, 0, i * 20)):
            data = _png(color)
            name = f"images/{hashlib.sha256(data).hexdigest()}.png"
            (folder / name).write_bytes(data)
            names.append(name)
        rows.append({"id": pid, "tier": tier, "round": "r", "split": "hold", "task": pid, "target": "728x90",
                     "original": names[0], "a": names[1], "b": names[2] if tier != "C2" else names[1],
                     "frontier_winner": winner, "a_is": a_is, "judge": "in-session", "reps": 2, "orders": [],
                     "reasons": [], "base_run": "", "new_run": "", "critic": {"base": 95.0, "new": 94.0, "margin": 1.0}
                     if tier == "D3" else None,
                     "sha256": {"original": hashlib.sha256(f"{pid}o".encode()).hexdigest(), "base": "x", "new": "y"}})
    (folder / "pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


# Order 1 then order 2 for each pair in file order.
SCRIPT = ["A", "B",  # P1: a, agrees (a is the new output: an override the frontier backs)
          "B", "A",  # P2: b, agrees (b is the base output)
          "B", "A",  # P3: b, contradicts
          "A", "A",  # P4: tie, abstains; the same letter in both orders
          "SAME", "no idea",  # P5: tie on an all-tie control; the second answer is invalid
          "A", "A"]  # P6: identical images, both A: the letter prior


def test_scripted_run_scores_as_computed_by_hand(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _dataset(data)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(SCRIPT))
    monkeypatch.setenv("CHAMELEON_AGENT", "script")
    monkeypatch.setenv("CHAMELEON_AGENT_SCRIPT", str(script))
    meta = agree.run(data, tmp_path / "run", split="hold", env_label="test")
    assert meta["calls"] == 12 and meta["stopped"] is None
    calls = [json.loads(line) for line in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()]
    assert [c["raw"] for c in calls] == SCRIPT and all(c["prompt_version"] for c in calls)
    agree.run(data, tmp_path / "rerun", split="hold")
    m = agree.score(tmp_path / "run", rerun=tmp_path / "rerun")

    pooled = m["hold"]["pooled"]
    assert (pooled["n"], pooled["agree"]["k"], pooled["contradict"]["k"], pooled["abstain"]["k"]) == (4, 2, 1, 1)
    assert pooled["precision"]["rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert pooled["coverage"]["rate"] == 0.75
    assert m["hold"]["D3"]["agree"]["k"] == 2 and m["hold"]["D2"]["contradict"]["k"] == 1
    assert m["hold"]["D1"]["abstain"]["k"] == 1 and m["hold"]["D1"]["contradict"]["k"] == 0
    assert m["d3_override_precision"]["hold"]["k"] == 1 and m["d3_override_precision"]["hold"]["n"] == 1
    assert (m["position_bias"]["hold"]["k"], m["position_bias"]["hold"]["n"]) == (2, 5)
    assert m["first_slot"]["raw"]["rate"] == 0.625 and m["first_slot"]["c2_prior"]["rate"] == 1.0
    assert m["first_slot"]["debiased"] == 0.125
    pc = m["per_call"]
    assert (pc["calls"], pc["same"]["k"], pc["invalid"]["k"], pc["error"]["k"]) == (12, 1, 1, 0)
    assert m["c1_false_decisive"]["k"] == 0 and m["c1_false_decisive"]["n"] == 1
    assert m["determinism"]["source"] == "rerun" and m["determinism"]["rate"] == 1.0
    assert m["baselines"]["always_a"]["coverage"]["rate"] == 0.0
    assert m["baselines"]["critic_d3"]["n"] == 2
    assert m["baselines"]["frontier_vs_frontier"]["agree"] == 24
    assert m["latency"]["call_p50"] is not None and m["soft"] is None
    assert m["gates"]["verdict"] == "insufficient_data" and not m["gates"]["floor"]["decisive"]
    assert m["gates"]["checks"]["d1_contradictions"] is True  # P4 abstained instead of contradicting
    assert m["gates"]["checks"]["position_bias"] is False and m["gates"]["checks"]["first_slot"] is False
    assert (tmp_path / "run" / "report.md").read_text().startswith("# Agreement run")
    assert agree.mcnemar(tmp_path / "run", tmp_path / "rerun")["pairs"] == 4


def test_runs_stop_after_repeated_transport_errors(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _dataset(data)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"fail": "connection"}] * 20))
    monkeypatch.setenv("CHAMELEON_AGENT", "script")
    monkeypatch.setenv("CHAMELEON_AGENT_SCRIPT", str(script))
    meta = agree.run(data, tmp_path / "run", split="all", max_errors=3)
    assert meta["calls"] == 3 and "transport errors" in meta["stopped"]


def test_soft_verdicts_from_probabilities(tmp_path):
    data = tmp_path / "data"
    rows = _dataset(data)
    for r in rows:
        r["split"] = "tune" if r["id"] in ("P1", "P3") else "hold"
    (data / "pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    probs = {"a": {"A": 0.9, "B": 0.1}, "b": {"A": 0.1, "B": 0.9}, "tie": {"A": 0.5, "B": 0.5}}
    calls = []
    for r in rows:
        w = r["frontier_winner"]
        first = probs[w]
        second = probs["b" if w == "a" else "a" if w == "b" else "tie"]
        for order, p in ((1, first), (2, second)):
            calls.append({"pair_id": r["id"], "order": order, "rep": 0, "raw": "", "parsed": None, "probs": p,
                          "latency_s": 0.1, "error": None})
    (run_dir / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    (run_dir / "run.json").write_text(json.dumps({"data": str(data), "kind": "compare", "prompt": "p1", "split": "all",
                                                  "advisor": "fake", "env_label": ""}))
    soft = agree.score(run_dir)["soft"]
    assert soft["tau"] is not None
    hold = soft["hold"]
    assert hold["agree"]["k"] == hold["n"] == 2 and hold["contradict"]["k"] == 0


def test_near_ties_lays_out_a_round_and_guards_verdicts(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copy(ROOT / "bench" / "suite" / "dev" / "x_post_0.json", suite)
    out = tmp_path / "round"
    summary = agree.near_ties([suite], out, epsilon=3.0, top_k=2, image_max=512)
    assert summary["fired"] == 1 and summary["pairs"] == 1
    key = json.loads((out / "key.json").read_text())
    assert set(key) == {"c001o1", "c001o2"} and key["c001o1"]["tier"] == "D3"
    assert key["c001o1"]["critic"]["margin"] >= 0 and key["c001o1"]["image_max"] == 512
    assert (out / "c001o1" / "A.png").read_bytes() == (out / "c001o2" / "B.png").read_bytes()
    batches = json.loads((out / "batches.json").read_text())
    assert [[i["id"] for i in b] for b in batches] == [["c001o1"], ["c001o2"]]
    (out / "verdicts_rep0.json").write_text("[]")
    with pytest.raises(SystemExit):
        agree.near_ties([suite], out)


def _one_task_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copy(ROOT / "bench" / "suite" / "dev" / "x_post_0.json", suite)
    return suite


def test_near_ties_never_clears_a_folder_it_did_not_make(tmp_path):
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True)
    (victim / "important.txt").write_text("keep me")
    (victim / "sub" / "notes.md").write_text("keep me too")
    with pytest.raises(SystemExit, match="no task files"):  # the suites are checked before anything is touched
        agree.near_ties([tmp_path / "no_such_suite"], victim)
    suite = _one_task_suite(tmp_path)
    for force in (False, True):
        with pytest.raises(SystemExit, match="not a near-ties round"):
            agree.near_ties([suite], victim, force=force)
    assert (victim / "important.txt").read_text() == "keep me" and (victim / "sub" / "notes.md").exists()
    for out in (ROOT, ROOT / "engine" / ".."):
        with pytest.raises(SystemExit, match="refusing to clear"):
            agree.near_ties([suite], out, force=True)
    assert (ROOT / "README.md").exists()


def test_near_ties_replaces_its_own_round_only_with_force(tmp_path):
    suite = _one_task_suite(tmp_path)
    out = tmp_path / "round"
    agree.near_ties([suite], out, top_k=2, image_max=512)
    (out / "verdicts_rep0.json").write_text("[]")
    with pytest.raises(SystemExit, match="holds verdicts"):
        agree.near_ties([suite], out, top_k=2, image_max=512)
    assert (out / "verdicts_rep0.json").exists()
    agree.near_ties([suite], out, top_k=2, image_max=512, force=True)
    assert not (out / "verdicts_rep0.json").exists() and (out / "key.json").exists()


def test_build_names_missing_inputs(tmp_path):
    with pytest.raises(SystemExit, match="no_such_round"):
        agree.build(tmp_path / "data", [tmp_path / "no_such_round"], {}, [], [], [tmp_path.as_posix() + "/abs=x"])
    assert not (tmp_path / "data").exists()


def _run_with_script(tmp_path, monkeypatch, steps, data=None, **kw):
    data = data or tmp_path / "data"
    if not (data / "pairs.jsonl").exists():
        _dataset(data)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(steps))
    monkeypatch.setenv("CHAMELEON_AGENT", "script")
    monkeypatch.setenv("CHAMELEON_AGENT_SCRIPT", str(script))
    return agree.run(data, tmp_path / "run", split="all", **kw)


def test_score_and_run_refuse_a_missing_dataset(tmp_path, monkeypatch):
    meta = _run_with_script(tmp_path, monkeypatch, ["A", "B"] * 6)
    assert meta["data"] == str((tmp_path / "data").resolve())  # outside the repo: absolute
    run_json = tmp_path / "run" / "run.json"
    moved = json.loads(run_json.read_text())
    moved["data"] = "/app/bench/agree/v1"  # a run made in the container
    run_json.write_text(json.dumps(moved))
    with pytest.raises(SystemExit, match="missing or empty"):
        agree.score(tmp_path / "run")
    with pytest.raises(SystemExit, match="missing or empty"):
        agree.mcnemar(tmp_path / "run", tmp_path / "run")
    assert agree.score(tmp_path / "run", data=tmp_path / "data")["pairs_scored"] == 6
    assert agree.mcnemar(tmp_path / "run", tmp_path / "run", data=tmp_path / "data")["pairs"] == 4
    with pytest.raises(SystemExit, match="missing or empty"):
        agree.run(tmp_path / "empty", tmp_path / "run2")


def test_run_stores_a_dataset_inside_the_repo_relative_to_it(tmp_path):
    assert agree._stored_path(ROOT / "bench" / "agree" / "v1") == "bench/agree/v1"
    assert agree._data_of({"data": "bench/agree/v1"}) == ROOT / "bench" / "agree" / "v1"
    assert agree._stored_path(tmp_path) == str(tmp_path.resolve())


def test_run_keeps_every_answer_when_interrupted(tmp_path, monkeypatch):
    real = agree._maxrss
    count = {"n": 0}

    def crash_on_third():
        count["n"] += 1
        if count["n"] == 3:
            raise KeyboardInterrupt
        return real()

    monkeypatch.setattr(agree, "_maxrss", crash_on_third)
    with pytest.raises(KeyboardInterrupt):
        _run_with_script(tmp_path, monkeypatch, ["B", "A", "A", "A"])
    calls = [json.loads(line) for line in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()]
    assert [c["raw"] for c in calls] == ["B", "A"]
    assert json.loads((tmp_path / "run" / "run.json").read_text())["stopped"] == "incomplete"


def test_run_survives_a_pathological_answer(tmp_path, monkeypatch):
    deep = ('{"winner":' + "[" * 997 + "]" * 997 + "}")[:2000]
    meta = _run_with_script(tmp_path, monkeypatch, [deep, "A"])
    calls = [json.loads(line) for line in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()]
    assert meta["calls"] == 12 and calls[0]["parsed"] is None and calls[1]["parsed"] == "A"


def _slow_first_llama(seconds: float):
    """A fake `llama_cpp` whose first completion outlasts the timeout, then answers at once."""
    import types

    mod = types.ModuleType("llama_cpp")
    fmt = types.ModuleType("llama_cpp.llama_chat_format")
    state = {"calls": 0}

    class MTMDChatHandler:
        def __init__(self, clip_model_path, verbose=True):
            pass

    class Llama:
        def __init__(self, **kwargs):
            pass

        def create_chat_completion(self, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                import time

                time.sleep(seconds)
            return {"choices": [{"message": {"content": "A"}}]}

    fmt.MTMDChatHandler = MTMDChatHandler
    mod.llama_chat_format = fmt
    mod.Llama = Llama
    mod.state = state
    return mod


def test_a_slow_in_process_call_is_waited_out_not_counted_as_busy(tmp_path, monkeypatch):
    mod = _slow_first_llama(1.5)
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp", mod)
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp.llama_chat_format", mod.llama_chat_format)
    data = tmp_path / "data"
    _dataset(data)
    gguf, mmproj = tmp_path / "m.gguf", tmp_path / "p.gguf"
    gguf.write_bytes(b"x")
    mmproj.write_bytes(b"x")
    for key, value in {"CHAMELEON_AGENT": "llamacpp", "CHAMELEON_AGENT_GGUF": str(gguf),
                       "CHAMELEON_AGENT_MMPROJ": str(mmproj), "CHAMELEON_AGENT_WARMUP_S": "0",
                       "CHAMELEON_AGENT_TIMEOUT_S": "1.0"}.items():
        monkeypatch.setenv(key, value)
    meta = agree.run(data, tmp_path / "run", split="all", max_errors=2)
    calls = [json.loads(line) for line in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()]
    assert meta["stopped"] is None and meta["calls"] == 12
    assert [c["error"] for c in calls] == ["timeout"] + [None] * 11
    assert mod.state["calls"] == 12


def test_doctor_suggests_the_thinking_switch_each_server_accepts():
    assert agree._thinking_hints("http://127.0.0.1:11434/v1")[0].startswith("Ollama")
    assert "reasoning_effort" in agree._thinking_hints("http://127.0.0.1:11434/v1")[0]
    assert agree._thinking_hints("http://127.0.0.1:8080/v1")[0].startswith("llama-server")
    assert len(agree._thinking_hints("")) == 4
