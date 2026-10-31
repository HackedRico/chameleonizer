from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from engine.adapters.vision import prepare_png
from engine.core.ports import Answer, Prompt

# =============================================================================
# Module Overview
# =============================================================================
# An `Advisor` for any local server with an OpenAI-compatible chat endpoint:
# llama.cpp's llama-server, Ollama, LM Studio or mlx-vlm. Stdlib only. Images
# go through `prepare_png` and travel as data URIs; the reply's text comes
# back unparsed, because the core parses. Every failure becomes an `Answer`
# error code, never an exception. Requests never go through HTTP(S)_PROXY,
# never follow a redirect (a 3xx is the error "http_3xx"), and only loopback
# hosts are allowed unless remote use is switched on.

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
# Largest response body read; a verdict is a few bytes, so anything near this is a broken server.
_MAX_BODY = 2 * 1024 * 1024
_RAW_LIMIT = 2000
# Prepared images kept per advisor: both image orders of a comparison reuse the same three.
_IMAGE_CACHE = 16


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect, so urllib raises the 3xx as an HTTPError.

    A redirect could send the request, and take the answer, from a host the loopback check never saw."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def opener() -> urllib.request.OpenerDirector:
    """A urllib opener that ignores proxy variables and follows no redirect."""
    # An empty ProxyHandler: a loopback model server must never be reached through a proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class OpenAIHTTPAdvisor:
    """`POST {url}/chat/completions` with temperature 0, one question per call."""

    def __init__(self, url: str, model: str = "", api_key: str = "", constrain: str = "none",
                 logprobs: bool = False, image_form: str = "object", extra: Mapping[str, Any] | None = None,
                 seed: int = 0, image_max: int = 1024, image_min: int = 224, allow_remote: bool = False) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"advisor URL must be http(s)://host[:port]/path, got {url!r}")
        if not allow_remote and parts.hostname not in LOOPBACK:
            raise ValueError(f"advisor host {parts.hostname!r} is not loopback; set CHAMELEON_AGENT_ALLOW_REMOTE=1")
        if constrain not in ("none", "grammar", "json_schema"):
            raise ValueError(f"unknown constrain mode {constrain!r}")
        if image_form not in ("object", "string"):
            raise ValueError(f"unknown image form {image_form!r}")
        self._endpoint = url.rstrip("/") + "/chat/completions"
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._model, self._api_key = model, api_key
        self._constrain, self._logprobs, self._image_form = constrain, bool(logprobs), image_form
        self._extra = dict(extra or {})
        self._seed, self._image_max, self._image_min = int(seed), int(image_max), int(image_min)
        self._prepared: dict[str, str] = {}
        self._opener = opener()

    def describe(self) -> str:
        return f"http:{self._model or 'local'}@{self._host}:{self._port}"

    def body(self, prompt: Prompt, images: tuple[bytes, ...]) -> dict[str, Any]:
        """The request body for one question; only the images the prompt refers to are sent."""
        content: list[Any] = []
        for part in prompt.parts:
            if isinstance(part, int):
                url = self._data_uri(images[part])
                image = url if self._image_form == "string" else {"url": url}
                content.append({"type": "image_url", "image_url": image})
            else:
                content.append({"type": "text", "text": part})
        body: dict[str, Any] = {
            "model": self._model or "local",
            "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": content}],
            "temperature": 0,
            "seed": self._seed,
            "max_tokens": prompt.max_tokens,
            "stream": False,
        }
        if self._constrain == "grammar" and prompt.grammar:
            body["grammar"] = prompt.grammar
        if self._constrain == "json_schema" and prompt.json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": prompt.id, "schema": dict(prompt.json_schema), "strict": True}}
        if self._logprobs:
            body["logprobs"] = True
            body["top_logprobs"] = 5
        body.update(self._extra)
        return body

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        t0 = time.monotonic()

        def took() -> float:
            return round(time.monotonic() - t0, 3)

        try:
            data = json.dumps(self.body(prompt, images)).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            request = urllib.request.Request(self._endpoint, data=data, headers=headers, method="POST")
            with self._opener.open(request, timeout=timeout_s) as response:
                payload = response.read(_MAX_BODY + 1)
            if len(payload) > _MAX_BODY:
                return Answer(error="bad_response", latency_s=took())
            reply = json.loads(payload)
            text = _content(reply)
            probs = _probs(reply, prompt.labels) if self._logprobs else None
            return Answer(raw=text[:_RAW_LIMIT], probs=probs, latency_s=took())
        except urllib.error.HTTPError as exc:
            return Answer(error=f"http_{exc.code}", latency_s=took())
        except (TimeoutError, socket.timeout):
            return Answer(error="timeout", latency_s=took())
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, (TimeoutError, socket.timeout))
            return Answer(error="timeout" if timed_out else "connection", latency_s=took())
        except (ValueError, KeyError, IndexError, TypeError, http.client.HTTPException):
            return Answer(error="bad_response", latency_s=took())
        except OSError:
            return Answer(error="connection", latency_s=took())

    def _data_uri(self, png: bytes) -> str:
        key = hashlib.sha256(png).hexdigest()
        uri = self._prepared.get(key)
        if uri is None:
            prepared = prepare_png(png, self._image_max, self._image_min)
            uri = "data:image/png;base64," + base64.b64encode(prepared).decode("ascii")
            if len(self._prepared) >= _IMAGE_CACHE:
                self._prepared.clear()
            self._prepared[key] = uri
        return uri


def _content(reply: Mapping[str, Any]) -> str:
    """The answer text of a chat completion; reasoning fields are ignored."""
    message = reply["choices"][0]["message"]
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str):
        raise TypeError("message content is not text")
    return content


def _probs(reply: Mapping[str, Any], labels: tuple[str, str]) -> dict[str, float] | None:
    """First-token probabilities of A, B and SAME, normalised over those three, or None."""
    try:
        top = reply["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return None
    names = {labels[0].upper(): "A", labels[1].upper(): "B", "SAME": "SAME", "S": "SAME", "SAM": "SAME"}
    mass: dict[str, float] = {}
    for entry in top or []:
        if not isinstance(entry, dict):
            continue
        name = names.get(str(entry.get("token", "")).strip().upper())
        logprob = entry.get("logprob")
        if name and isinstance(logprob, (int, float)) and math.isfinite(logprob):
            mass[name] = mass.get(name, 0.0) + math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        return None
    return {k: round(v / total, 6) for k, v in sorted(mass.items())}
