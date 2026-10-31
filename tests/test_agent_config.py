from __future__ import annotations

import json

import pytest

from engine.adapters.agent_config import AgentConfig, load_agent_config
from engine.core.agent import AgentSettings

# =============================================================================
# Module Overview
# =============================================================================
# Configuration from `CHAMELEON_AGENT*` variables: off values and unset mean
# no harness, defaults match the documented table, and every invalid value
# turns the harness off instead of changing what the engine ships.


@pytest.mark.parametrize("value", [None, "", "0", "off", "OFF", "false", "no", "none", " None "])
def test_off_values(value):
    env = {} if value is None else {"CHAMELEON_AGENT": value}
    assert load_agent_config(env) is None


def test_defaults():
    cfg = load_agent_config({"CHAMELEON_AGENT": "http"})
    assert cfg == AgentConfig(backend="http")
    assert cfg.settings == AgentSettings()
    s = cfg.settings
    assert (s.slots, s.top_k, s.epsilon, s.flag_delta) == ("tiebreak", 2, 3.0, 0.25)
    assert (s.call_timeout_s, s.budget_s, s.image_max, s.prompt) == (5.0, 10.0, 1024, "p1")
    assert (cfg.warmup_s, cfg.url, cfg.constrain, cfg.image_form, cfg.image_min) == (
        30.0, "http://127.0.0.1:11434/v1", "none", "object", 224)
    assert cfg.extra == {} and not cfg.logprobs and not cfg.allow_remote


def test_every_value_is_read():
    env = {
        "CHAMELEON_AGENT": "HTTP", "CHAMELEON_AGENT_SLOTS": "flags+tiebreak", "CHAMELEON_AGENT_TOP_K": "4",
        "CHAMELEON_AGENT_EPSILON": "1", "CHAMELEON_AGENT_FLAG_DELTA": "0.5", "CHAMELEON_AGENT_TIMEOUT_S": "7.5",
        "CHAMELEON_AGENT_BUDGET_S": "20", "CHAMELEON_AGENT_WARMUP_S": "0", "CHAMELEON_AGENT_PROMPT": "p1-json",
        "CHAMELEON_AGENT_DRAIN_S": "12",
        "CHAMELEON_AGENT_IMAGE_MAX": "768", "CHAMELEON_AGENT_IMAGE_MIN": "0",
        "CHAMELEON_AGENT_URL": "http://localhost:8080/v1", "CHAMELEON_AGENT_MODEL": "qwen3.5:0.8b",
        "CHAMELEON_AGENT_API_KEY": "k", "CHAMELEON_AGENT_CONSTRAIN": "json_schema", "CHAMELEON_AGENT_LOGPROBS": "1",
        "CHAMELEON_AGENT_IMAGE_FORM": "string", "CHAMELEON_AGENT_SEED": "42",
        "CHAMELEON_AGENT_EXTRA": '{"chat_template_kwargs": {"enable_thinking": false}}',
        "CHAMELEON_AGENT_TRACE": "/tmp/t.jsonl",
    }
    cfg = load_agent_config(env)
    assert cfg.backend == "http" and cfg.settings == AgentSettings(
        slots="flags+tiebreak", top_k=4, epsilon=1.0, flag_delta=0.5, call_timeout_s=7.5, budget_s=20.0,
        drain_s=12.0, image_max=768, prompt="p1-json")
    assert (cfg.warmup_s, cfg.model, cfg.api_key, cfg.constrain, cfg.logprobs, cfg.image_form, cfg.seed) == (
        0.0, "qwen3.5:0.8b", "k", "json_schema", True, "string", 42)
    assert cfg.extra == {"chat_template_kwargs": {"enable_thinking": False}} and cfg.trace == "/tmp/t.jsonl"
    assert cfg.image_min == 0


@pytest.mark.parametrize("name, value", [
    ("CHAMELEON_AGENT_TOP_K", "1"), ("CHAMELEON_AGENT_TOP_K", "5"), ("CHAMELEON_AGENT_TOP_K", "2.5"),
    ("CHAMELEON_AGENT_EPSILON", "-1"), ("CHAMELEON_AGENT_EPSILON", "21"), ("CHAMELEON_AGENT_EPSILON", "nan"),
    ("CHAMELEON_AGENT_FLAG_DELTA", "11"), ("CHAMELEON_AGENT_TIMEOUT_S", "0.05"), ("CHAMELEON_AGENT_TIMEOUT_S", "121"),
    ("CHAMELEON_AGENT_BUDGET_S", "0.1"), ("CHAMELEON_AGENT_BUDGET_S", "601"), ("CHAMELEON_AGENT_WARMUP_S", "-1"),
    ("CHAMELEON_AGENT_DRAIN_S", "-1"), ("CHAMELEON_AGENT_DRAIN_S", "601"),
    ("CHAMELEON_AGENT_WARMUP_S", "inf"), ("CHAMELEON_AGENT_IMAGE_MAX", "128"), ("CHAMELEON_AGENT_IMAGE_MAX", "4096"),
    ("CHAMELEON_AGENT_IMAGE_MIN", "513"), ("CHAMELEON_AGENT_SLOTS", "roles"), ("CHAMELEON_AGENT_PROMPT", "p2a"),
    ("CHAMELEON_AGENT_CONSTRAIN", "regex"), ("CHAMELEON_AGENT_IMAGE_FORM", "bytes"),
    ("CHAMELEON_AGENT_LOGPROBS", "maybe"), ("CHAMELEON_AGENT_SEED", "x"), ("CHAMELEON_AGENT_TIMEOUT_S", "fast"),
    ("CHAMELEON_AGENT_EXTRA", "{bad json"), ("CHAMELEON_AGENT_EXTRA", "[1, 2]"),
    ("CHAMELEON_AGENT_URL", "ftp://127.0.0.1/v1"), ("CHAMELEON_AGENT_URL", "http://127.0.0.1:notaport/v1"),
])
def test_invalid_values_turn_the_harness_off(name, value, caplog):
    assert load_agent_config({"CHAMELEON_AGENT": "http", name: value}) is None
    assert any("invalid" in r.getMessage() for r in caplog.records)


def test_unknown_backend_is_off():
    assert load_agent_config({"CHAMELEON_AGENT": "gpt"}) is None


def test_remote_urls_need_allow_remote():
    remote = {"CHAMELEON_AGENT": "http", "CHAMELEON_AGENT_URL": "http://192.168.1.20:8080/v1"}
    assert load_agent_config(remote) is None
    assert load_agent_config({**remote, "CHAMELEON_AGENT_ALLOW_REMOTE": "1"}).allow_remote
    for host in ("127.0.0.1", "localhost", "[::1]"):
        assert load_agent_config({"CHAMELEON_AGENT": "http", "CHAMELEON_AGENT_URL": f"http://{host}:1234/v1"})


def test_required_files(tmp_path):
    assert load_agent_config({"CHAMELEON_AGENT": "llamacpp"}) is None
    assert load_agent_config({"CHAMELEON_AGENT": "llamacpp", "CHAMELEON_AGENT_GGUF": str(tmp_path / "missing.gguf"),
                              "CHAMELEON_AGENT_MMPROJ": str(tmp_path / "missing.gguf")}) is None
    gguf, mmproj = tmp_path / "m.gguf", tmp_path / "p.gguf"
    gguf.write_bytes(b"x")
    mmproj.write_bytes(b"x")
    cfg = load_agent_config({"CHAMELEON_AGENT": "llamacpp", "CHAMELEON_AGENT_GGUF": str(gguf),
                             "CHAMELEON_AGENT_MMPROJ": str(mmproj), "CHAMELEON_AGENT_THREADS": "8"})
    assert (cfg.gguf, cfg.mmproj, cfg.threads, cfg.chat_handler) == (str(gguf), str(mmproj), 8, "MTMDChatHandler")
    assert load_agent_config({"CHAMELEON_AGENT": "script"}) is None
    script = tmp_path / "s.json"
    script.write_text(json.dumps(["A"]))
    assert load_agent_config({"CHAMELEON_AGENT": "script", "CHAMELEON_AGENT_SCRIPT": str(script)}).script == str(script)
    missing = str(tmp_path / "no.jsonl")
    assert load_agent_config({"CHAMELEON_AGENT": "replay", "CHAMELEON_AGENT_REPLAY": missing}) is None
    assert load_agent_config({"CHAMELEON_AGENT": "null"}).backend == "null"
    assert load_agent_config({"CHAMELEON_AGENT": "random", "CHAMELEON_AGENT_SEED": "7"}).seed == 7


def test_a_budget_below_one_timeout_warns(caplog):
    cfg = load_agent_config({"CHAMELEON_AGENT": "null", "CHAMELEON_AGENT_TIMEOUT_S": "8", "CHAMELEON_AGENT_BUDGET_S": "5"})
    assert cfg is not None and "no model call can start" in caplog.text
