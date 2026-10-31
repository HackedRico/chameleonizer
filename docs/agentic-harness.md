# Model harness

An optional layer around the deterministic engine that lets a small local vision model choose between finished layouts the engine has already built. It is **off by default**: with `CHAMELEON_AGENT` unset, `engine/resize.py` never imports it and every output is byte-identical to the engine's.

**Status:** one real model has run the harness: Qwen3.5-0.8B, on 2026-10-30, logged in `docs/experiments.md`. It named whichever layout it saw first in all 80 answers across both image orders, so it never changed an output. In the scorer-shaped linux/amd64 container one call took 29 to 95 s against the 5 s timeout. The submission ships the harness off, and no model ships with the engine until the organizers publish their approved model list and size cap.

This page covers what the harness does, its vocabularies, configuration, fallbacks, how to run it on a MacBook against a local model, and how to decide whether a model is good enough to keep.

## 1. What it is and is not

- **It is a selector.** The pipeline builds a few finished, shippable designs, the finalists. Finalist 0 is the engine's own pick. The harness may replace it with another finalist, and that index is the only thing a model can change.
- **It never touches coordinates.** Every answer is parsed in the core to `A`, `B` or `SAME`, or to issue codes from a fixed list. No code path runs from an answer to geometry, and the chosen finalist ships unmodified.
- **It is conservative.** A switch needs a decisive answer in both image orders, or a flag the critic's own penalties back. Anything else keeps finalist 0.
- **It can only ship what the engine would.** A challenger must pass the hard rules, and it may differ from finalist 0 only in element boxes and text sizes. Each of `collision`, `cut_off`, `text_overflow` and `hidden_content` must be no higher than finalist 0's, so a challenger can never trade a collision for text drawn off the canvas. Images keep their aspect within 5% unless they are a `cover` backdrop that bleeds off all four edges of the source.
- **It is not a judge of the layout.** `docs/research.md` section 5 explains why: zero-shot open judges score near chance against human preference (DesignSense about 0.54, TASTE 0.50 to 0.54), small models are unreliable with coordinates, and model output must be treated as untrusted. The harness follows those findings: enumerated choices, a timeout and a coded default for every call, and a final validator.

## 2. How a task flows

```
resize_with_report(..., alternatives = top_k - 1)
  -> finalists: [engine pick, runner-up, ...], each finished like the pick
  -> eligible: within epsilon critic points of the pick, no worse on any unreadable-content penalty,
     no hard-rule problem, and no change but boxes and text sizes against the pick
  -> renders through the Renderer port (official renderer, max_size image_max)
  -> enumerated questions through the Advisor port (both image orders)
  -> coded action: switch only on a decisive, consistent answer
  -> final validator (engine/core/validate.py)
  -> design
```

Code map:

| Module | Role |
|---|---|
| `engine/core/pipeline.py` | `Finalist`, `Outcome.finalists`; the default path is unchanged |
| `engine/core/ports.py` | `Renderer`, `Prompt`, `Answer`, `Advisor` |
| `engine/core/advice.py` | Vocabularies, prompt registry, parsers, `resolve`, `replay_key` (pure) |
| `engine/core/validate.py` | `hard_rule_problems()`, the core port of `tools/checks.structural`, stricter on the aspect exemption (pure) |
| `engine/core/agent.py` | `AgentSettings`, `Breaker`, `call_with_deadline`, `eligible`, `choose` (pure) |
| `engine/adapters/render.py` | `OfficialRenderer`, the only harness module that imports `renderer/` |
| `engine/adapters/vision.py` | `prepare_png()`: the one image preprocessing step for every model |
| `engine/adapters/agent_config.py` | `load_agent_config(env)` |
| `engine/adapters/harness.py` | `build_advisor`, `build_harness`, `agent_resize` |
| `engine/adapters/scripted.py` | `NullAdvisor`, `ScriptedAdvisor`, `RandomAdvisor`, `ReplayAdvisor` |
| `engine/adapters/openai_http.py` | `OpenAIHTTPAdvisor`, stdlib only |
| `engine/adapters/llama_inproc.py` | `LlamaCppAdvisor`, optional `llama-cpp-python` |

**Firing rate.** The harness asks a model only when at least one challenger is eligible.
- With the defaults (`top_k` 2, epsilon 3) and the `null` advisor, it fired on 63 of the 172 tasks in `input/` and the four suites, 16 of them on `dev`. On 81 tasks the runner-up finished into the same design as the pick or there was none (`single`), and on 28 it was not eligible.
- Measured on raw candidate scores before finishing and dedupe, epsilon 3 fires on 23 of 39 dev, 27 of 60 probe and 24 of 52 formats tasks, and epsilon 1 on 17, 19 and 20.
- Each firing task costs one extra finished alternative (a `propose(only=...)` and a critique, usually under 0.1 s, up to about 1.2 s when the packed layout is built), three renders, and one or two model calls. On a quiet machine the renders took 0.7 to 1.2 s per firing task. On a loaded one (load average about 10 on 4 CPUs) a firing task took 3.9 s on average and 9.2 s at most on top of the engine with the `null` advisor, and 3 tasks ran out of the 10 s budget before asking.
- Epsilon and `top_k` are the latency knobs; renders, not the alternatives, dominate the cost before any model call.

## 3. Vocabularies

### Compare answers

The tie-break slot asks "which resize would a designer ship" with the incumbent shown as A first.

| Order 1 (incumbent = A, challenger = B) | Order 2 (challenger = A, incumbent = B) | Coded action |
|---|---|---|
| `B` | `A` | The challenger becomes the incumbent. |
| `B` | `B`, `SAME` or unusable | Keep. The answer flipped with position, which is position bias, so it counts as a tie. |
| `A`, `SAME` or unusable | not asked | Keep. The second call is skipped because it cannot make the pair decisive. |

With `top_k` above 2 the challengers are asked in critic order, king of the hill: a challenger that wins in both orders becomes the incumbent the next one must beat.

### Issue codes

The flags slot (`CHAMELEON_AGENT_SLOTS=flags` or `flags+tiebreak`) asks the model which problems the engine's pick has.
1. The model returns flags and a worst issue.
2. The acting issue is `worst` if valid, otherwise the highest-priority flag (the table's order).
3. The coded action finds the eligible challenger with the lowest sum of the issue's critic penalties. Ties go to the higher critic score, then the lower index.
4. That challenger is proposed only if its sum is at least `flag_delta` (default 0.25) below the incumbent's. With `flags+tiebreak`, the proposal must then also win the comparison in both orders.

| Code | Meaning shown to the model | Critic penalty keys | Action |
|---|---|---|---|
| `text_overlap` | text over other text, or over a picture or logo | `collision`, `cut_off`, `text_overflow` | re-rank |
| `tiny_text` | text too small to read, or words run together | `small_text`, `collapsed_spaces` | re-rank |
| `cut_off` | text or picture cut off by the edge | `cut_off`, `text_overflow` | re-rank |
| `logo_small` | logo missing, tiny or unreadable | `logo_small` | re-rank |
| `logo_large` | logo out of proportion | `logo_inflated` | re-rank |
| `weak_headline` | the headline no longer stands out | `hierarchy`, `weak_headline`, `headline_lines`, `headline_rewrap` | re-rank |
| `crowded` | elements jammed together or against the edges | `edge_crowding`, `hero_clutter` | re-rank |
| `empty_space` | large empty areas, or content off-centre | `empty`, `off_center`, `hero_small` | re-rank |
| `busy_background` | text on a busy part of a picture | `busy_backdrop`, `hero_clutter` | re-rank |
| `lost_look` | no longer looks like the original | `flipped_sides`, `hierarchy` | re-rank |
| `distorted_image` | a picture stretched or squashed | none | Keep; logged as an alarm, since the hard rules guarantee aspect |
| `none` | no problem | none | Keep |

**Known limit:** `logo_inflated`, `empty`, `weak_headline`, `hero_small`, `logo_small`, `headline_lines` and `off_center` are thresholded. Finalists below a threshold all read 0 on that key, so those flags often act as Keep. A `Report.metrics` field with raw measures would fix that; it is not built. Measured with `top_k` 4 and epsilon 3, a flag had a coded gain of at least 0.25 on any eligible challenger for 7 issue-and-task combinations on `dev` (17 firing tasks) and 7 on `probe` (22 firing tasks). So the flags slot can change the outcome on only a handful of tasks; the tie-break slot is the one that matters.

### Prompt registry

Every prompt uses the system text "You are a strict graphic design reviewer." The system text, "ORIGINAL design:" and the original image come first, so llama.cpp can reuse the cached prefix across both orders. `PROMPT_VERSION` is `2026-10-30.1`; change it whenever wording changes.

| Id | Kind | Shows | Answer | Constraint | `max_tokens` |
|---|---|---|---|---|---|
| `p1` (default) | compare | original, A, B, rubric | one word: A, B or SAME | grammar `root ::= "A" \| "B" \| "SAME"` | 4 |
| `p1-json` | compare | the same | `{"winner": "A" \| "B" \| "SAME"}` | JSON schema, or a grammar | 16 |
| `p1-noorig` | compare | A, B only (ablation) | as `p1` | as `p1` | 4 |
| `p1-num` | compare | candidates labelled 1 and 2 | 1, 2 or SAME | `"1" \| "2" \| "SAME"` | 4 |
| `p2a` | flags | original, the engine's pick, the code list | `{"flags": [codes], "worst": code or "none"}` | JSON schema (`maxItems` 4), or a grammar | 64 |
| `warmup` | warmup | one 64x64 white image | "A" | as `p1` | 2 |

The rubric text is: "Which resize would a designer ship? Worst problems first: 1. text over text, or text over a picture or logo; 2. text too small or cut off; 3. logo or product missing, tiny, squashed or cut; 4. messy alignment, uneven spacing, big empty areas; 5. lost the original's look or hierarchy."

### Parser rules

Deterministic, with no retries; the raw text is always kept in the trace.

`parse_verdict`:
1. Strip `<think>…</think>`. Text after the last `</think>` is the answer; an unclosed `<think>` leaves nothing. Strip code fences and whitespace.
2. If the text holds a balanced `{…}`, read `winner` from the first one if that key holds a string or number, otherwise `answer`. A `winner` that holds an unknown value gives no answer.
3. Otherwise take the last match of `(?i)\b(answer|winner|verdict)\s*[:=]?\s*"?(A|B|SAME|TIE|1|2)\b`.
4. Otherwise, unless the reply is a JSON object, strip punctuation from the whole reply and match it on its own.
5. `TIE`, `SAME`, `EQUAL`, `NEITHER` and `BOTH` mean SAME. `CANDIDATE A` means A and `CANDIDATE B` means B. A position counts among the images the model was sent: with the original first, `IMAGE 2` is A and `IMAGE 3` is B; for `p1-noorig`, `IMAGE 1` is A and `IMAGE 2` is B. The prompt's label pair maps to A and B.
6. Anything else, including a reply that names both labels without a marker ("A or B"), is None: an abstention.
7. The parsers never raise. JSON nested deeper than 32 levels is not read, so a pathological answer is an abstention, not a crash.

`parse_flags`: strip as above, read the first JSON object, keep known codes in order without duplicates, drop unknown ones; `worst` must be a known code or `none`, and when it is missing or `none` while flags were given, the highest-priority flag becomes the worst. A bare list of codes, such as `crowded, tiny_text`, is accepted too. Anything else is None.

## 4. Configuration

All variables start with `CHAMELEON_AGENT`. They come from the shell or `docker run -e`, so `engine/main.py` and the Dockerfile do not change. **Anything invalid turns the harness off** with one warning.

| Variable | Default | Values | Notes |
|---|---|---|---|
| `CHAMELEON_AGENT` | unset (off) | `off`, `0`, `false`, `no`, `none`; `null`, `script`, `random`, `replay`, `http`, `llamacpp` | Unknown values mean off, with a warning. |
| `_SLOTS` | `tiebreak` | `tiebreak`, `flags`, `flags+tiebreak` | With `flags+tiebreak`, flags propose and the comparison must confirm. |
| `_TOP_K` | 2 | 2 to 4 | Finalists including the default. Each extra one costs a `propose(only=…)` and a render. |
| `_EPSILON` | 3.0 | 0 to 20 | Critic points a challenger may trail the default by. |
| `_FLAG_DELTA` | 0.25 | 0 to 10 | |
| `_TIMEOUT_S` | 5.0 | 0.1 to 120 | Per call. A call starts only while at least this much of the task's budget is left, so a timeout always means the model was too slow, never that the budget cut a normal answer short. |
| `_BUDGET_S` | 10.0 | 0.5 to 600 | Per task, after the default is built: finishing alternatives, renders and calls. A two-call comparison needs about 2 x `_TIMEOUT_S` plus 1 to 2 s of renders; below one `_TIMEOUT_S` no call can start, and the config logs a warning. |
| `_DRAIN_S` | 30.0 | 0 to 600 | Longest wait, before the engine runs again, for a call abandoned at its deadline to finish. |
| `_WARMUP_S` | 30.0 | 0 to 600 | One warmup call per process. 0 skips it. A failed warmup means off. |
| `_PROMPT` | `p1` | `p1`, `p1-json`, `p1-noorig`, `p1-num` | |
| `_IMAGE_MAX` | 1024 | 256 to 2048 | Render `max_size` and preprocessing cap. |
| `_IMAGE_MIN` | 224 | 0 to 512 | Short-side floor; tiny banners are scaled up, then padded grey. |
| `_URL` | `http://127.0.0.1:11434/v1` | http(s) URL | Must be loopback unless `_ALLOW_REMOTE=1`. Use the IP literal. |
| `_MODEL` | "" | | Ollama and LM Studio need it; llama-server ignores it. |
| `_API_KEY` | "" | | Bearer token. |
| `_CONSTRAIN` | `none` | `none`, `grammar` (llama-server), `json_schema` | Use `json_schema` with `p1-json` or `p2a`. |
| `_LOGPROBS` | 0 | 0 or 1 | Logged only; the evaluation tool uses them. |
| `_IMAGE_FORM` | `object` | `object`, `string` | Shape of `image_url`. |
| `_EXTRA` | `{}` | JSON object | Merged into the request body, e.g. `{"chat_template_kwargs":{"enable_thinking":false}}` for llama-server or `{"reasoning_effort":"none"}` for Ollama. |
| `_SEED` | 0 | int | |
| `_ALLOW_REMOTE` | 0 | 0 or 1 | |
| `_GGUF`, `_MMPROJ` | "" | existing files | Required by `llamacpp`, which only the tools accept (see below). |
| `_CHAT_HANDLER` | `MTMDChatHandler` | class name in `llama_cpp.llama_chat_format` | `llamacpp` only. |
| `_THREADS`, `_CTX` | 4, 8192 | 1 to 256, 512 to 262144 | `llamacpp` only. |
| `_SCRIPT` | "" | JSON file | Required by `script`. |
| `_REPLAY` | "" | JSONL file | Required by `replay`. |
| `_TRACE` | "" | path | Appends one JSONL trace per task. |

Backends:
- `null` abstains on everything. It runs the whole path, renders included, and must stay byte-identical to off.
- `script` plays a JSON list of answers: strings, `{"sleep": 2, "then": "B"}`, `{"fail": "connection"}` or `{"raise": "message"}`.
- `random` answers uniformly at random, seeded per question: the noise floor.
- `replay` answers from recorded verdicts keyed on the exact render bytes (`tools/agree.py build` writes `replay.jsonl`).
- `http` talks to any OpenAI-compatible server. It never follows a redirect: a 3xx reply is the error `http_3xx`, so a server cannot send the request to a host the loopback check never saw.
- `llamacpp` runs a GGUF in process, for `tools.bench --agent` and `tools.agree` only. `engine.resize` refuses it with a warning and runs without a model, because loading a model in the scorer's process has no deadline and running out of memory would kill the process.

Presets:

| Server | Settings |
|---|---|
| llama-server | `CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:8080/v1 CHAMELEON_AGENT_CONSTRAIN=grammar CHAMELEON_AGENT_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'` |
| Ollama | `CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:11434/v1 CHAMELEON_AGENT_MODEL=qwen3.5:0.8b CHAMELEON_AGENT_PROMPT=p1-json CHAMELEON_AGENT_CONSTRAIN=json_schema CHAMELEON_AGENT_EXTRA='{"reasoning_effort":"none"}'` |
| LM Studio | `CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:1234/v1 CHAMELEON_AGENT_MODEL=<id> CHAMELEON_AGENT_PROMPT=p1-json CHAMELEON_AGENT_CONSTRAIN=json_schema`; turn thinking off in the model's settings |
| mlx-vlm | `CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:8081/v1` |

## 5. Fallbacks

| Situation | Behaviour | Reason or log |
|---|---|---|
| `CHAMELEON_AGENT` unset or off | Old call; no harness module imported | none |
| Invalid config, failed lazy import, missing model files, failed warmup, or `build_harness` raising | Harness off for the whole process; a timed-out warmup call is waited for, up to `_DRAIN_S`, first | one warning |
| `CHAMELEON_AGENT=llamacpp` reaching `engine.resize` | Harness off for the whole process | one warning |
| `resize_with_report` raises | As today: letterbox, then emergency layout | warning |
| Finishing an alternative raises | That alternative is skipped | warning |
| Fewer than 2 finalists, or none eligible | Default | `single`, `no_eligible` |
| Breaker opened by an earlier task | Default, with no alternatives finished, no renders and no calls | `breaker_open` |
| A render raises | Default | `render_error` |
| Less than `min_call_s` left for a render, or less than a whole `_TIMEOUT_S` left for a call | Default, or a switch already confirmed in both orders; the breaker stays closed | `budget` |
| Timeout, refused connection, HTTP error (3xx included), bad response, adapter exception, or `busy` | Default; breaker opens | `advisor_error` |
| A call abandoned at its deadline is still running when the next task starts | The next task waits for it, up to `_DRAIN_S`, before the engine runs | a warning if it is still running after that |
| Unparseable answer | An abstention; 3 in a row open the breaker | `invalid` |
| A, SAME, a position-inconsistent pair, a flag with no coded gain, `none`, `distorted_image` | Default | `kept` |
| Chosen finalist fails `hard_rule_problems` | Default | `invalid_design` |
| Any other exception in `choose` | Default | `error` |
| Trace write fails | Ignored | debug log |

**The breaker.** After the first transport failure the advisor is off for the rest of the process. A server keeps generating after a client gives up, and the next request would queue behind it.

**Byte identity under a failing model.** A timed-out call cannot be interrupted, so it may keep using the CPU. The engine's candidate loop stops after 20 s of wall time (`_TIME_BUDGET_S`), so CPU stolen from it could change finalist 0 itself. `agent_resize` therefore waits for abandoned calls, up to `_DRAIN_S`, before the engine runs again, and so does a failed warmup before the harness turns off. That covers the in-process backend and the HTTP client, but not a model server that keeps generating after the client gave up: identity under a failing real model holds only while the engine's own time budget is not reached.

**Reading a trace.** With `CHAMELEON_AGENT_TRACE=output/agent_trace.jsonl`, each task appends one line:

```json
{"v":1,"prompt_version":"2026-10-30.1","advisor":"http:qwen3.5:0.8b@127.0.0.1:11434","prompt":"p1",
 "slots":"tiebreak","canvas":[728,90],"source_sha":"…16 hex…",
 "finalists":[{"strategy":"banner/columns","critic":91.2,"unreadable":0.0,"eligible":true}, …],
 "steps":[{"kind":"compare","prompt":"p1","order":1,"shown":["orig",0,1],"sha":["…","…","…"],"raw":"B",
           "parsed":"B","probs":null,"latency_s":1.84,"error":null}, …],
 "chosen":1,"reason":"switched:tiebreak","calls":2,"seconds":4.1}
```

`shown` names the images in order: `orig` is the source, numbers are finalist indexes. Flags steps also carry `acted_issue` and `proposal`. `note` carries an adapter's remark: the `replay` backend marks every answer `replay_hit` or `replay_miss`.

## 6. Deploying on a MacBook

Run everything natively from the repo `.venv`, not in Docker: a container started with `--network none` cannot reach a model server on the host.

### llama.cpp (recommended: the same GGUF that would ship)

1. `brew install llama.cpp`, or build it with `-DGGML_METAL=ON`.
2. Start the server:
   ```bash
   llama-server -hf unsloth/Qwen3.5-0.8B-GGUF:Q4_K_M --host 127.0.0.1 --port 8080 -np 1 -c 8192 \
     --image-max-tokens 256 --no-webui --jinja --seed 42 --temp 0 --reasoning off
   ```
   On older builds use `--reasoning-budget 0`; check `llama-server --help`, since flags change between builds.
3. Point the engine at it:
   ```bash
   export CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:8080/v1 CHAMELEON_AGENT_CONSTRAIN=grammar
   export CHAMELEON_AGENT_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'
   ```

### Ollama

1. `OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_KEEP_ALIVE=1h ollama serve`, then `ollama pull qwen3.5:0.8b`.
2. Confirm `ollama show qwen3.5:0.8b` lists vision; otherwise use `qwen3.5:2b` or `qwen3-vl:2b`.
3. `export CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:11434/v1 CHAMELEON_AGENT_MODEL=qwen3.5:0.8b CHAMELEON_AGENT_PROMPT=p1-json CHAMELEON_AGENT_CONSTRAIN=json_schema`
4. `export CHAMELEON_AGENT_EXTRA='{"reasoning_effort":"none"}'`. Ollama's `/v1` turns thinking off this way ("none" requests no thinking); it ignores llama-server's `chat_template_kwargs`. A thinking model otherwise spends the 4 or 16 answer tokens thinking, every answer parses as invalid, and three in a row open the breaker.
5. The first request loads the model; the warmup absorbs that. If images are rejected, try `CHAMELEON_AGENT_IMAGE_FORM=string`.

### LM Studio

1. `lms get` a GGUF that shows the vision badge, such as `lmstudio-community/Qwen3.5-0.8B-GGUF`.
2. `lms server start --port 1234` and `lms load <key> --context-length 8192`.
3. Read the model id from `curl -s http://127.0.0.1:1234/v1/models`.
4. `export CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:1234/v1 CHAMELEON_AGENT_MODEL=<id> CHAMELEON_AGENT_PROMPT=p1-json CHAMELEON_AGENT_CONSTRAIN=json_schema`
5. Turn thinking off in the model's settings in LM Studio. Whether its `/v1` accepts `reasoning_effort` depends on the version; `tools.agree doctor` shows whether answers come back empty.

### MLX (prompt iteration only; different quantization from what would ship)

Install `mlx-vlm` in a separate venv and run `python -m mlx_vlm.server --model mlx-community/Qwen3.5-0.8B-4bit --port 8081` (verify the flags), with `CHAMELEON_AGENT_URL=http://127.0.0.1:8081/v1`.

### In process

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv pip install --python .venv/bin/python -r requirements-agent.txt
export CHAMELEON_AGENT=llamacpp CHAMELEON_AGENT_GGUF=/path/model.gguf CHAMELEON_AGENT_MMPROJ=/path/mmproj.gguf
```

The repo's venv comes from `uv venv`, which has no `pip`, hence `uv pip`. The in-process backend works in `tools.bench --agent` and `tools.agree` only; `engine.resize`, and so `engine.main` and `tools.digests`, refuse it. An in-process call cannot be interrupted: on a timeout its thread is abandoned, the next call answers `busy`, and the breaker turns the advisor off; the next task waits for the abandoned call before the engine runs. Prefer the HTTP path. `tests/test_agent_llamacpp.py::test_real_model_smoke` runs against a real model when `llama_cpp` is installed and `CHAMELEON_TEST_GGUF` and `CHAMELEON_TEST_MMPROJ` are set.

### Smoke test

```bash
.venv/bin/python -m tools.agree doctor      # one synthetic comparison in both orders
CHAMELEON_AGENT_TRACE=output/agent_trace.jsonl .venv/bin/python -m engine.main
```

`doctor` prints the latency, the raw reply and the parsed verdict for each order, and says what to change when the server rejects images or the model is thinking, with the switch each server accepts, the one on the configured port first.

### Model shortlist

- **Primary:** Qwen3.5-0.8B Q4_K_M plus its mmproj.
- **Backup under the cap:** SmolVLM2-500M-Video-Instruct.
- **Laptop-only arms:** Qwen3.5-2B and gemma-4-E2B-it.
- **Optional arm:** the d1-omni-600M decision model, after a licence check. It needs a `/v1/decisions` adapter, which is not built.
- **Licences:** Qwen3.5 and SmolVLM2 are Apache-2.0. LFM models are under the LFM licence; check it.

## 7. Evaluating

A model earns a place in two stages. Neither has been run with a real model yet, because the agreement dataset `bench/agree/v1/` has not been built.

### Stage 1: agreement with the frontier judge (`tools/agree.py`)

1. Lay out the engine's own near-ties as blind rounds:
   ```bash
   .venv/bin/python -m tools.agree near-ties --suite bench/suite/dev --suite bench/suite/probe --suite bench/suite/formats \
     --out output/blind/nt1 --epsilon 3 --top-k 4
   ```
   Renders are the raw bytes the engine would show a model, at `--image-max`, so verdicts on them replay through the engine, on this machine and renderer build with the same `--image-max`, drop policy, epsilon and `--top-k`. `--out` must be missing, empty or an earlier near-ties round: the command refuses to clear any other folder, and a round with verdicts only with `--force`.
2. Judge them with in-session Claude agents through `tools/workflows/blind_pairwise_judge.js`, twice, saving `verdicts_rep0.json` and `verdicts_rep1.json` in the round folder (see `docs/benchmark.md`).
3. Build the dataset, on the machine that holds the judged rounds:
   ```bash
   .venv/bin/python -m tools.agree build --out bench/agree/v1 --round output/blind/nt1 \
     --round output/blind/pack --round output/blind/chipeq --tier pack=D2 \
     --absolute output/blind/abs_rc_dev=output/bench/rc_dev --controls 20
   ```
   - **Inputs live where they were made.** `output/` is gitignored, so judged rounds and bench runs exist only in the clone that produced them. Only `--round` folders with verdicts are required, and at least one is. `--round output/blind/pack`, `--round output/blind/chipeq` and `--absolute output/blind/abs_rc_dev=output/bench/rc_dev` are rounds judged on 2026-10-29 and 30 and are optional. Leave out any that do not exist. `build` names every missing input and stops.
   - **Moving the dataset to the model machine.** Build where the rounds are, then commit `bench/agree/v1/` with `git add`, binary files included, and pull it on the machine that runs the model. If it is over 50 MB, `tar czf output/agree-v1.tgz bench/agree/v1` and copy the archive instead. The model machine can also lay out, judge and build near-ties itself.
   - A pair is decisive only if every rep agrees on a non-tie in both orders (D1 or D2 from `--tier`, D3 for near-ties). All ties is C1; anything mixed is C3 and only counted. C2 controls are identical pairs.
   - a and b are assigned by hash parity, and the `tune` or `hold` split by the parity of the original image's hash. Renders of one source at different sizes hash differently, so the same source can land in both halves across round types.
   - Commit it as `bench/agree/v1/` if it is 50 MB or less, otherwise keep it as a tarball under `output/`.
   - Replay keys (`replay.jsonl`) are hashes of the render bytes, so they match only on the machine, renderer build and settings that drew the round. Re-run `near-ties` and judge again on a different machine.
4. Ask a model, then score:
   ```bash
   .venv/bin/python -m tools.agree run --data bench/agree/v1 --out output/agree/qwen08-p1 --prompt p1 --split hold --env-label mac-metal
   .venv/bin/python -m tools.agree run --data bench/agree/v1 --out output/agree/qwen08-p1-b --prompt p1 --split hold
   .venv/bin/python -m tools.agree score --run output/agree/qwen08-p1 --rerun output/agree/qwen08-p1-b
   .venv/bin/python -m tools.agree mcnemar output/agree/qwen08-p1 output/agree/qwen08-p1json
   ```
   `run` always asks both orders and ignores the breaker. It writes `run.json` first and appends each call to `calls.jsonl` as it is made, so an interrupted run keeps its answers and still scores. After a timeout it waits, up to `--drain-s` (120 s), for the abandoned call to finish before the next request; a `busy` answer is waited out and asked again, never counted as an error. It stops after `--max-errors` transport errors in a row. `run`, `score` and `mcnemar` stop with an error when the dataset's `pairs.jsonl` or `flags.jsonl` is missing or empty. `run.json` stores the dataset path relative to the repo root, and `score --data` and `mcnemar --data` override it. `score` writes `metrics.json` and `report.md`: agreement, contradiction, abstention, precision and coverage per tier with Wilson 95% intervals, D3 override precision, position bias, first-slot share (raw and debiased by the C2 letter prior), SAME, invalid and error rates, the C1 false-decisive rate, determinism, latency, baselines (always-A, seeded random, the critic on D3, and frontier against frontier: 24 of 31, 0 contradictions), and, when the server returns logprobs, soft verdicts with τ tuned on `tune` and applied to `hold`.

**Gates**, judged on `hold` (constants in `tools/agree.py`):

- **Data floor:** at least 60 decisive pairs, at least 30 of them D3; at least 20 distinct sources covering strip, banner, square, portrait, tall, print and tiny; at least 20 controls. Otherwise the verdict is `insufficient_data`.
- **No-go** if any of these holds:
  - invalid answers plus errors exceed 1% of calls;
  - any contradiction on D1;
  - pooled contradictions exceed 5%, or their Wilson upper bound exceeds 12%;
  - the Wilson lower bound on precision is under 0.70;
  - D3 override precision is under 0.70;
  - position bias exceeds 20%, or the debiased first-slot share is outside 0.40 to 0.60;
  - the C1 false-decisive rate exceeds 30%;
  - determinism is under 98% (measure it with `--rerun`; unmeasured counts as a fail);
  - p95 per pair exceeds 8 s, judged only with `--env-label amd64-cpu4`;
  - D3 coverage is under 0.30.

Passing every gate means "go to the end-to-end benchmark", not "ship". Final latency comes from the amd64 container below; Mac Metal latency means nothing for the scorer.

### Latency in the scorer's shape

`tools/docker/agent-bench.Dockerfile` is a development-only amd64 image: llama-server built with `GGML_NATIVE=OFF`, the engine, `tools/`, `bench/` and your `models/` folder. The build checks that llama-server finds its shared libraries.

1. Put the weights in `models/` at the repo root (gitignored, and kept out of the scoring image). `llama-server -hf` downloads into its own cache, not here, so download the files explicitly, with the Hugging Face CLI:
   ```bash
   hf download unsloth/Qwen3.5-0.8B-GGUF --include "*Q4_K_M.gguf" "mmproj-F16.gguf" --local-dir models
   ls models    # the exact file names to pass as MODEL and MMPROJ
   ```
   The build fails if `models/` is missing.
2. Build and run on an Apple silicon Mac, the scorer's hardware, with the names `ls models` printed:
   ```bash
   docker build --platform linux/amd64 -f tools/docker/agent-bench.Dockerfile -t chameleonizer-agent .
   docker run --rm --platform linux/amd64 --cpus 4 --memory 8g --network none \
     -e MODEL=<model>.gguf -e MMPROJ=<mmproj>.gguf -v "$PWD/output:/app/output" \
     chameleonizer-agent python -m tools.agree run --data bench/agree/v1 --out output/agree/amd64 --env-label amd64-cpu4
   ```
3. Score on the host: `run.json` records `bench/agree/v1` relative to the repo root, so `.venv/bin/python -m tools.agree score --run output/agree/amd64` finds the same dataset. If the host has none, add `--data <folder>`, or score inside the container by appending `python -m tools.agree score --run output/agree/amd64` to a `docker run` like the one above.

The entry script starts llama-server on loopback, waits for `/health`, writes the cold-load seconds to `output/agent_load.json`, then runs the command. Loopback works under `--network none`.

### Stage 2: end to end

All configurations run through `tools.bench --agent` with the same harness code.

| Config | Setup | Requirement |
|---|---|---|
| E0 | Current engine | Baseline |
| E1 | Harness off | Byte-identical to E0 (`tools/digests.py diff`) |
| E1b | `null`; `http` to a closed port; stub `sleep:30`; stub `garbage`, each with `CHAMELEON_AGENT_WARMUP_S=0` | Each byte-identical to E0, with the harness on for every task |
| E2 | `random` | Passes structural checks on every task; the noise floor |
| E3 | `replay` of in-session Claude verdicts on D3 near-ties | The ceiling. If E3 does not beat E0, stop, but only after checking that the replay matched. |
| E4 | Each local model with the best stage-1 prompt | The accept rule below |

```bash
.venv/bin/python -m tools.digests run --suite input --suite bench/suite/dev --out output/digests/e0.json
CHAMELEON_AGENT=null .venv/bin/python -m tools.digests run --suite input --suite bench/suite/dev --out output/digests/e1b.json
.venv/bin/python -m tools.digests diff output/digests/e0.json output/digests/e1b.json
# A dead model: without WARMUP_S=0 the warmup fails, the harness is off, and identity proves nothing.
CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:9/v1 CHAMELEON_AGENT_WARMUP_S=0 \
  .venv/bin/python -m tools.digests run --suite input --suite bench/suite/dev --out output/digests/e1b-closed.json
CHAMELEON_AGENT=replay CHAMELEON_AGENT_REPLAY=bench/agree/v1/replay.jsonl CHAMELEON_AGENT_TOP_K=4 \
  .venv/bin/python -m tools.bench --agent --suite bench/suite/dev --out output/bench/e3-dev
```

`tools.digests run` records how many tasks had the harness on and exits 1 when `CHAMELEON_AGENT` names a backend but the harness was off for any task. `tools.bench --agent` with `replay` reports `agent_replay_hits`, `agent_replay_misses` and `agent_replay_unmatched_tasks` (firing tasks whose first question had no recorded verdict), warns on any miss, and exits 1 when nothing matched. Misses after a switch are expected with `top_k` above 2, since a near-ties round pairs each challenger only with the default; a miss on a first question means the round was drawn elsewhere or with other settings, and that task counts as kept without being asked.

Judge changed tasks only, with `tools.blind make`, `tools/workflows/blind_pairwise_judge.js` (2 reps) and `tools.blind resolve`.

**Accept rule:** E4 must meet every condition:
- wins exceed losses on `dev`, `probe` and `formats`, with no new fail in any format family;
- on probe, formats and at least 100 fresh designs pooled: at least 15 decisive tasks, wins at least twice the losses, and a one-sided sign test at p ≤ 0.10;
- E4 beats E2;
- `test` is not worse;
- `pytest` passes and structural checks pass everywhere.

**Runtime gates**, in the amd64 container with 4 CPUs: total per design p95 10 s or less and max 20 s or less, cold load 10 s or less, model RSS 2 GB or less. Figures measured on any other machine must sit at 50% of each budget or less.

Log results in `docs/experiments.md` and `docs/leaderboard.md`, and tag engines as usual.

## 8. What runs without model weights

- E1 and E1b with `tools/digests.py`, E2 with `random`, and every component test (`pytest -q tests`).
- Building datasets from judged rounds, laying out near-ties and judging them with in-session agents.
- Stub runs: `python -m tools.agent_stub_server --mode sleep:30 --port 8099` (modes `answer:<text>`, `script:<file>`, `sleep:<s>[:<text>]`, `status:<code>`, `garbage`, `badjson`, `logprobs:<label>`).
- E3 replays, once near-ties have frontier verdicts.

## 9. Shipping (not done here)

**Stage one.** It would need `llama-server` baked into the scoring image with `GGML_NATIVE=OFF`, weights under the cap, and a supervisor started from `resize.py`. It depends on the organizers' answers to `docs/research.md` section 8 Q3: the model list, the size cap, the per-task timeout, and whether there is one container per task. Cold load hits every task if each task gets its own container.

**The Express add-on.** The harness has no Express form yet. In the finale the Python engine runs as a web service the add-on calls, and the add-on, not the service, draws pages. `choose` renders finalists synchronously through the `Renderer` port in the middle of `resize`, and a page-bound, browser-side rendition API cannot answer that call inside the service. So in the add-on the harness stays off, unless a protocol is built for it: for example, the service returns the eligible finalists, the add-on applies and renders each one and asks the model, and sends back which one to keep, or the service calls back to the add-on for each render. Neither is designed.

## 10. Troubleshooting

- **Proxy variables.** The HTTP adapter ignores `HTTP_PROXY` and `HTTPS_PROXY`, so a loopback server is never reached through a proxy. `tools.agree doctor` does the same.
- **Thinking output.** A reply that starts with `<think>` and never closes it parses to nothing, and so does an empty reply from a model that spent its few answer tokens thinking. Disable thinking the way the server accepts it: `--reasoning off` on llama-server, or `CHAMELEON_AGENT_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'`; on Ollama `CHAMELEON_AGENT_EXTRA='{"reasoning_effort":"none"}'`, since Ollama's `/v1` ignores `chat_template_kwargs`; on LM Studio, the model's settings. `tools.agree doctor` prints these, the configured server's first.
- **Context too small.** Three images at 256 tokens each plus the rubric need about 1,000 tokens; 8192 leaves room for the cached prefix. A server with a 2048 context may truncate the images.
- **The breaker opens on a cold start.** The first call loads the model. Raise `CHAMELEON_AGENT_WARMUP_S` so the warmup, not a task, pays for it.
- **Ollama rejects images.** Try `CHAMELEON_AGENT_IMAGE_FORM=string`.
- **Engine time budget.** The candidate loop stops trying candidates after 20 s of wall time (`_TIME_BUDGET_S`), so a heavily loaded machine can change outputs of the slowest tasks (Level 3 at print sizes). Run digest comparisons on a quiet machine, and re-run any differing task alone before blaming a change. A timed-out call is waited for, up to `_DRAIN_S`, before the next task's engine runs, but a model server still generating after a timeout steals CPU from the next task the same way; the breaker limits that to one generation.
- **Overwrite hazards.** `tools.blind make` deletes its output folder before writing, verdicts included. `tools.agree near-ties` clears only a missing, empty or earlier near-ties folder (one with its `key.json` and `batches.json` and nothing foreign), never the repo or a folder holding `.git`, and replaces a round that holds verdicts only with `--force`. `tools.judge --pair` writes `pairwise.json` into the new run and overwrites the last one: rename it to `pairwise_vs_<base>.json` before collecting it with `build --pairwise`.
