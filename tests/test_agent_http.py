from __future__ import annotations

import base64
import io
import socket
import time

import pytest
from PIL import Image

from engine.adapters.openai_http import OpenAIHTTPAdvisor
from engine.adapters.vision import prepare_png
from engine.core.advice import parse_verdict, prompt
from tools.agent_stub_server import serve

# =============================================================================
# Module Overview
# =============================================================================
# The OpenAI-compatible HTTP advisor against the stub server, started inside
# the test on a loopback port, with no external network: the request body,
# proxy bypass, each failure mapped to its error code, logprobs and auth.


def _png(w: int, h: int, color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, format="PNG")
    return out.getvalue()


IMAGES = (_png(400, 400), _png(320, 50, (0, 0, 200)), _png(320, 50, (0, 200, 0)))


@pytest.fixture
def stub():
    servers = []

    def start(mode: str):
        server, url = serve(mode)
        servers.append(server)
        return server, url

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_request_body_shape(stub):
    server, url = stub("answer:B")
    adv = OpenAIHTTPAdvisor(url, model="qwen3.5:0.8b", seed=7)
    p = prompt("p1", 320, 50)
    answer = adv.ask(p, IMAGES, 5.0)
    assert (answer.raw, answer.error, answer.probs) == ("B", None, None) and answer.latency_s >= 0
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    body = req["body"]
    assert (body["model"], body["temperature"], body["seed"], body["max_tokens"], body["stream"]) == (
        "qwen3.5:0.8b", 0, 7, p.max_tokens, False)
    system, user = body["messages"]
    assert system == {"role": "system", "content": p.system}
    images = [part for part in user["content"] if part["type"] == "image_url"]
    texts = [part["text"] for part in user["content"] if part["type"] == "text"]
    assert len(images) == 3 and texts[0] == "ORIGINAL design:"
    for part, raw in zip(images, IMAGES):
        uri = part["image_url"]["url"]
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == prepare_png(raw)
    assert "grammar" not in body and "response_format" not in body and "logprobs" not in body
    assert "Authorization" not in req["headers"]


def test_constraints_extra_and_image_form(stub):
    server, url = stub("answer:SAME")
    OpenAIHTTPAdvisor(url, constrain="grammar").ask(prompt("p1", 320, 50), IMAGES, 5.0)
    OpenAIHTTPAdvisor(url, constrain="json_schema").ask(prompt("p1-json", 320, 50), IMAGES, 5.0)
    OpenAIHTTPAdvisor(url, constrain="json_schema").ask(prompt("p1", 320, 50), IMAGES, 5.0)
    OpenAIHTTPAdvisor(url, image_form="string", extra={"chat_template_kwargs": {"enable_thinking": False}},
                      api_key="secret").ask(prompt("p1-noorig", 320, 50), IMAGES, 5.0)
    g, js, plain, last = (r["body"] for r in server.requests)
    assert g["grammar"] == prompt("p1", 320, 50).grammar and "response_format" not in g
    assert js["response_format"]["type"] == "json_schema"
    assert js["response_format"]["json_schema"]["schema"]["properties"]["winner"]["enum"] == ["A", "B", "SAME"]
    assert "response_format" not in plain and "grammar" not in plain
    assert last["chat_template_kwargs"] == {"enable_thinking": False}
    images = [part for part in last["messages"][1]["content"] if part["type"] == "image_url"]
    assert len(images) == 2 and all(isinstance(part["image_url"], str) for part in images)  # no original
    assert server.requests[-1]["headers"]["Authorization"] == "Bearer secret"


def test_proxy_variables_are_bypassed(stub, monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    server, url = stub("answer:A")
    answer = OpenAIHTTPAdvisor(url).ask(prompt("p1", 320, 50), IMAGES, 5.0)
    assert (answer.raw, answer.error) == ("A", None) and len(server.requests) == 1


def test_slow_server_times_out_within_bound(stub):
    _server, url = stub("sleep:3")
    t0 = time.monotonic()
    answer = OpenAIHTTPAdvisor(url).ask(prompt("p1", 320, 50), IMAGES, 0.3)
    assert answer.error == "timeout" and time.monotonic() - t0 < 2.0


@pytest.mark.parametrize("mode, error", [("status:500", "http_500"), ("status:404", "http_404"),
                                         ("badjson", "bad_response")])
def test_server_failures_map_to_error_codes(stub, mode, error):
    _server, url = stub(mode)
    assert OpenAIHTTPAdvisor(url).ask(prompt("p1", 320, 50), IMAGES, 5.0).error == error


def test_refused_connection():
    answer = OpenAIHTTPAdvisor(f"http://127.0.0.1:{_free_port()}/v1").ask(prompt("p1", 320, 50), IMAGES, 2.0)
    assert answer.error == "connection"


def test_garbage_is_returned_raw_and_parses_to_nothing(stub):
    _server, url = stub("garbage")
    answer = OpenAIHTTPAdvisor(url).ask(prompt("p1", 320, 50), IMAGES, 5.0)
    assert answer.error is None and answer.raw and parse_verdict(answer.raw) is None


def test_logprobs_come_back_as_probs(stub):
    server, url = stub("logprobs:B")
    answer = OpenAIHTTPAdvisor(url, logprobs=True).ask(prompt("p1", 320, 50), IMAGES, 5.0)
    assert server.requests[0]["body"]["logprobs"] is True and server.requests[0]["body"]["top_logprobs"] == 5
    assert answer.raw == "B" and set(answer.probs) == {"A", "B", "SAME"}
    assert answer.probs["B"] > 0.9 and abs(sum(answer.probs.values()) - 1) < 1e-4


def test_only_loopback_without_allow_remote():
    with pytest.raises(ValueError):
        OpenAIHTTPAdvisor("http://10.0.0.5:8080/v1")
    with pytest.raises(ValueError):
        OpenAIHTTPAdvisor("file:///etc/passwd")
    assert OpenAIHTTPAdvisor("http://10.0.0.5:8080/v1", allow_remote=True).describe() == "http:local@10.0.0.5:8080"
    assert OpenAIHTTPAdvisor("http://127.0.0.1:11434/v1", model="m").describe() == "http:m@127.0.0.1:11434"


def test_redirects_are_refused_not_followed(stub):
    target, target_url = stub("answer:B")
    _server, url = stub(f"redirect:{target_url}/elsewhere")
    answer = OpenAIHTTPAdvisor(url).ask(prompt("p1", 320, 50), IMAGES, 5.0)
    assert (answer.error, answer.raw) == ("http_302", "")
    assert target.requests == [] and target.gets == []  # the redirect target was never contacted
