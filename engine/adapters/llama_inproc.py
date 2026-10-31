from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any

from engine.adapters.vision import prepare_png
from engine.core.ports import Answer, Prompt

# =============================================================================
# Module Overview
# =============================================================================
# An `Advisor` that runs a GGUF vision model inside this process through the
# optional `llama-cpp-python` package (`requirements-agent.txt`), imported
# lazily so the engine never needs it. A call in process cannot be
# interrupted: on timeout its thread is abandoned, the lock it holds makes the
# next call answer "busy", and the core's breaker stops asking. The HTTP
# adapter with a llama-server is the recommended path; this one is a laptop
# convenience. `MTMDChatHandler` exists in the pinned 0.3.36; any other
# handler in `llama_cpp.llama_chat_format` can be chosen by name.

_RAW_LIMIT = 2000


class LlamaCppAdvisor:
    """A local GGUF model plus its multimodal projector, asked one question at a time."""

    def __init__(self, gguf: str, mmproj: str, chat_handler: str = "MTMDChatHandler", threads: int = 4,
                 n_ctx: int = 8192, seed: int = 0, constrain: str = "none", image_max: int = 1024,
                 image_min: int = 224) -> None:
        import llama_cpp  # optional dependency; ImportError turns the harness off
        from llama_cpp import llama_chat_format

        handler_cls = getattr(llama_chat_format, chat_handler, None)
        if handler_cls is None:
            raise ValueError(f"llama_cpp.llama_chat_format has no chat handler {chat_handler!r}")
        handler = handler_cls(clip_model_path=mmproj, verbose=False)
        self._llama_cpp = llama_cpp
        self._llm = llama_cpp.Llama(model_path=gguf, chat_handler=handler, n_ctx=n_ctx, n_threads=threads,
                                    seed=seed, verbose=False)
        self._name = os.path.basename(gguf)
        self._seed, self._constrain = int(seed), constrain
        self._image_max, self._image_min = int(image_max), int(image_min)
        self._lock = threading.Lock()

    def describe(self) -> str:
        return f"llamacpp:{self._name}"

    def messages(self, prompt: Prompt, images: tuple[bytes, ...]) -> list[dict[str, Any]]:
        """The chat messages for one question, in the OpenAI shape the chat handlers read."""
        content: list[dict[str, Any]] = []
        for part in prompt.parts:
            if isinstance(part, int):
                prepared = prepare_png(images[part], self._image_max, self._image_min)
                url = "data:image/png;base64," + base64.b64encode(prepared).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": url}})
            else:
                content.append({"type": "text", "text": part})
        return [{"role": "system", "content": prompt.system}, {"role": "user", "content": content}]

    def ask(self, prompt: Prompt, images: tuple[bytes, ...], timeout_s: float) -> Answer:
        # A timed-out call still holds the lock; asking again would queue behind it.
        if not self._lock.acquire(blocking=False):
            return Answer(error="busy")
        t0 = time.monotonic()
        try:
            kwargs: dict[str, Any] = {"messages": self.messages(prompt, images), "temperature": 0.0,
                                      "max_tokens": prompt.max_tokens, "seed": self._seed}
            if self._constrain == "grammar" and prompt.grammar:
                kwargs["grammar"] = self._llama_cpp.LlamaGrammar.from_string(prompt.grammar, verbose=False)
            if self._constrain == "json_schema" and prompt.json_schema:
                kwargs["response_format"] = {"type": "json_object", "schema": dict(prompt.json_schema)}
            reply = self._llm.create_chat_completion(**kwargs)
            content = reply["choices"][0]["message"].get("content") or ""
            return Answer(raw=str(content)[:_RAW_LIMIT], latency_s=round(time.monotonic() - t0, 3))
        except (KeyError, IndexError, TypeError):
            return Answer(error="bad_response", latency_s=round(time.monotonic() - t0, 3))
        finally:
            self._lock.release()
