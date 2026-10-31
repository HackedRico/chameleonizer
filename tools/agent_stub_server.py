from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# =============================================================================
# Module Overview
# =============================================================================
# A stand-in for a local OpenAI-compatible model server, for tests and for
# dead-model runs of the harness. `serve(mode)` starts it on a loopback port
# in a daemon thread and records every request body and header. Modes:
#   answer:<text>        every completion says <text>
#   script:<file>        answers from a JSON list (strings, {"sleep": s, "then": t}
#                        or {"status": code}), then SAME
#   sleep:<s>[:<text>]   waits <s> seconds, then says <text> (SAME by default)
#   status:<code>        answers with that HTTP status
#   garbage              says something that is not a verdict
#   badjson              sends a body that is not JSON
#   logprobs:<label>     says <label> and returns first-token logprobs favouring it
#   redirect:<url>       answers 302 with Location <url>
# It also answers GET /v1/models and GET /health, and records every GET path
# in `gets`. Development only; stdlib only.

_GARBAGE = "Well, both have merits. Perhaps option C, or maybe neither?"


class StubServer(ThreadingHTTPServer):
    """The HTTP server plus its mode and what it was sent."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], mode: str) -> None:
        super().__init__(address, _Handler)
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._script: list[Any] | None = None
        self._lock = threading.Lock()

    def next_scripted(self) -> Any:
        with self._lock:
            if self._script is None:
                self._script = list(json.loads(Path(self.mode.split(":", 1)[1]).read_text()))
            return self._script.pop(0) if self._script else "SAME"


class _Handler(BaseHTTPRequestHandler):
    server: StubServer

    def log_message(self, fmt: str, *args: Any) -> None:  # keep test output clean
        return

    def do_GET(self) -> None:
        self.server.gets.append(self.path)
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "stub", "object": "model"}]})
        elif self.path.rstrip("/").endswith("/health"):
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        self.server.requests.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
        mode = self.server.mode
        kind, _, arg = mode.partition(":")
        if kind == "script":
            step = self.server.next_scripted()
            if isinstance(step, dict) and "sleep" in step:
                time.sleep(float(step["sleep"]))
                return self._completion(str(step.get("then", "SAME")))
            if isinstance(step, dict) and "status" in step:
                return self._json(int(step["status"]), {"error": {"message": "scripted failure"}})
            return self._completion(str(step))
        if kind == "answer":
            return self._completion(arg)
        if kind == "sleep":
            seconds, _, text = arg.partition(":")
            time.sleep(float(seconds))
            return self._completion(text or "SAME")
        if kind == "status":
            return self._json(int(arg), {"error": {"message": f"stub status {arg}"}})
        if kind == "redirect":
            self.send_response(302)
            self.send_header("Location", arg)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if kind == "garbage":
            return self._completion(_GARBAGE)
        if kind == "badjson":
            return self._send(200, b"<html>not json</html>", "text/html")
        if kind == "logprobs":
            label = arg or "A"
            other = "B" if label != "B" else "A"
            top = [{"token": label, "logprob": -0.05}, {"token": other, "logprob": -3.2},
                   {"token": "SAME", "logprob": -4.0}, {"token": "The", "logprob": -6.0}]
            return self._completion(label, {"content": [{"token": label, "logprob": -0.05, "top_logprobs": top}]})
        return self._json(500, {"error": {"message": f"unknown stub mode {mode!r}"}})

    def _completion(self, text: str, logprobs: dict[str, Any] | None = None) -> None:
        choice: dict[str, Any] = {"index": 0, "message": {"role": "assistant", "content": text},
                                  "finish_reason": "stop"}
        if logprobs is not None:
            choice["logprobs"] = logprobs
        self._json(200, {"id": "stub", "object": "chat.completion", "model": "stub", "choices": [choice]})

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up after its timeout
            pass


def serve(mode: str, port: int = 0, host: str = "127.0.0.1") -> tuple[StubServer, str]:
    """Start a stub on `host:port` (0 picks a free port) in a daemon thread: (server, base URL)."""
    server = StubServer((host, port), mode)
    threading.Thread(target=server.serve_forever, name="agent-stub", daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="An OpenAI-compatible stub model server for harness tests.")
    parser.add_argument("--mode", default="answer:SAME", help="see the module overview")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server, url = serve(args.mode, args.port, args.host)
    print(f"stub model server ({args.mode}) at {url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
