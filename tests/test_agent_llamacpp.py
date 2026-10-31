from __future__ import annotations

import io
import os
import sys
import threading
import types
from pathlib import Path

import pytest
from PIL import Image

from engine.adapters.agent_config import AgentConfig
from engine.adapters.harness import build_harness
from engine.core.advice import parse_verdict, prompt

# =============================================================================
# Module Overview
# =============================================================================
# The in-process llama.cpp advisor. A fake `llama_cpp` module checks message
# building, the grammar and the "busy" lock; without the package the harness
# stays off; a real smoke test runs only where the package and model files
# exist (never in the cloud).

ROOT = Path(__file__).resolve().parents[1]


def _png(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(out, format="PNG")
    return out.getvalue()


IMAGES = (_png(300, 300), _png(320, 50), _png(320, 50))


def _fake_llama_cpp(reply: str = "B", gate: threading.Event | None = None) -> types.ModuleType:
    mod = types.ModuleType("llama_cpp")
    fmt = types.ModuleType("llama_cpp.llama_chat_format")
    seen: dict = {"calls": []}

    class MTMDChatHandler:
        def __init__(self, clip_model_path, verbose=True):
            seen["clip"] = clip_model_path

    class LlamaGrammar:
        def __init__(self, text):
            self.text = text

        @classmethod
        def from_string(cls, grammar, verbose=True):
            return cls(grammar)

    class Llama:
        def __init__(self, **kwargs):
            seen["init"] = kwargs

        def create_chat_completion(self, **kwargs):
            seen["calls"].append(kwargs)
            if gate is not None:
                gate.wait(5)
            return {"choices": [{"message": {"role": "assistant", "content": reply}}]}

    fmt.MTMDChatHandler = MTMDChatHandler
    mod.llama_chat_format = fmt
    mod.Llama = Llama
    mod.LlamaGrammar = LlamaGrammar
    mod.seen = seen
    return mod


def _install(monkeypatch, mod):
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setitem(sys.modules, "llama_cpp.llama_chat_format", mod.llama_chat_format)


def test_messages_grammar_and_kwargs(monkeypatch):
    from engine.adapters.llama_inproc import LlamaCppAdvisor

    mod = _fake_llama_cpp("B")
    _install(monkeypatch, mod)
    adv = LlamaCppAdvisor("/models/q.gguf", "/models/mm.gguf", threads=3, n_ctx=4096, seed=5, constrain="grammar")
    assert adv.describe() == "llamacpp:q.gguf"
    assert mod.seen["clip"] == "/models/mm.gguf"
    assert {k: mod.seen["init"][k] for k in ("model_path", "n_ctx", "n_threads", "seed")} == {
        "model_path": "/models/q.gguf", "n_ctx": 4096, "n_threads": 3, "seed": 5}
    p = prompt("p1", 320, 50)
    answer = adv.ask(p, IMAGES, 5.0)
    assert parse_verdict(answer.raw) == "B" and answer.error is None
    call = mod.seen["calls"][0]
    assert call["temperature"] == 0.0 and call["max_tokens"] == p.max_tokens and call["seed"] == 5
    assert call["grammar"].text == p.grammar and "response_format" not in call
    system, user = call["messages"]
    assert system == {"role": "system", "content": p.system}
    urls = [c["image_url"]["url"] for c in user["content"] if c["type"] == "image_url"]
    assert len(urls) == 3 and all(u.startswith("data:image/png;base64,") for u in urls)


def test_json_schema_mode(monkeypatch):
    from engine.adapters.llama_inproc import LlamaCppAdvisor

    mod = _fake_llama_cpp('{"winner": "A"}')
    _install(monkeypatch, mod)
    adv = LlamaCppAdvisor("q.gguf", "mm.gguf", constrain="json_schema")
    answer = adv.ask(prompt("p1-json", 320, 50), IMAGES, 5.0)
    call = mod.seen["calls"][0]
    assert call["response_format"]["type"] == "json_object" and "grammar" not in call
    assert parse_verdict(answer.raw) == "A"


def test_a_call_still_running_makes_the_next_one_busy(monkeypatch):
    from engine.adapters.llama_inproc import LlamaCppAdvisor
    from engine.core.agent import call_with_deadline

    gate = threading.Event()
    mod = _fake_llama_cpp("A", gate)
    _install(monkeypatch, mod)
    adv = LlamaCppAdvisor("q.gguf", "mm.gguf")
    p = prompt("p1", 320, 50)
    first = call_with_deadline(lambda: adv.ask(p, IMAGES, 0.1), 0.1)
    assert first.error == "timeout"
    assert adv.ask(p, IMAGES, 0.1).error == "busy"
    gate.set()


def test_unknown_handler_raises(monkeypatch):
    from engine.adapters.llama_inproc import LlamaCppAdvisor

    _install(monkeypatch, _fake_llama_cpp())
    with pytest.raises(ValueError):
        LlamaCppAdvisor("q.gguf", "mm.gguf", chat_handler="NoSuchHandler")


def test_without_the_package_the_harness_stays_off(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)  # import llama_cpp raises ImportError
    gguf, mmproj = tmp_path / "m.gguf", tmp_path / "p.gguf"
    gguf.write_bytes(b"x")
    mmproj.write_bytes(b"x")
    cfg = AgentConfig(backend="llamacpp", gguf=str(gguf), mmproj=str(mmproj), warmup_s=0)
    assert build_harness(cfg, ROOT) is None


def test_real_model_smoke():
    pytest.importorskip("llama_cpp")
    gguf, mmproj = os.environ.get("CHAMELEON_TEST_GGUF"), os.environ.get("CHAMELEON_TEST_MMPROJ")
    if not gguf or not mmproj:
        pytest.skip("set CHAMELEON_TEST_GGUF and CHAMELEON_TEST_MMPROJ to run against a real model")
    from engine.adapters.llama_inproc import LlamaCppAdvisor

    adv = LlamaCppAdvisor(gguf, mmproj, constrain="grammar")
    answer = adv.ask(prompt("p1", 320, 50), IMAGES, 120.0)
    assert answer.error is None and parse_verdict(answer.raw) in ("A", "B", "SAME")


def test_the_scorer_entry_point_refuses_the_in_process_backend(monkeypatch, tmp_path):
    import engine.resize

    mod = _fake_llama_cpp()
    _install(monkeypatch, mod)
    gguf, mmproj = tmp_path / "m.gguf", tmp_path / "p.gguf"
    gguf.write_bytes(b"x")
    mmproj.write_bytes(b"x")
    for key, value in {"CHAMELEON_AGENT": "llamacpp", "CHAMELEON_AGENT_GGUF": str(gguf),
                       "CHAMELEON_AGENT_MMPROJ": str(mmproj), "CHAMELEON_AGENT_WARMUP_S": "0"}.items():
        monkeypatch.setenv(key, value)
    engine.resize._agent.cache_clear()
    assert engine.resize._agent() is None
    assert "init" not in mod.seen  # no model was loaded in the scorer's process
