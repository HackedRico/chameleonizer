# Team Chameleonizer: Project Chameleon, Adobe University Hackathon 2026

`resize(source, target_canvas)` in `engine/resize.py` takes a finished design as schema JSON and a new canvas size. It returns the same elements moved, resized and refit for that canvas. It never changes copy, font families or asset pixels, never stretches an image, and generates nothing. The submitted version is v1.28.

![The official Level 2 sample, 1080x1080, re-laid-out by v1.28 as an 1800x2400 poster, a 336x280 ad, a 1600x400 banner, Full HD and a 970x90 strip](/docs/assets/resize-examples.jpg)

The official Level 2 sample on five canvases, drawn by the official renderer inside the scoring Docker image.

## Run it

These are the organizers' commands, unchanged:

```bash
docker build --platform linux/amd64 -t my-chameleon-engine .
docker run --rm --network none \
  -v $(pwd)/input:/app/input \
  -v $(pwd)/output:/app/output \
  my-chameleon-engine
```

Each task in `input/` produces `<task>_output.json` and `<task>_output.png` in `output/`. The engine makes no network calls and loads no model. `requirements.txt` pins every package to the image the benchmark ran in.

To run without Docker, you need Python 3.11 or newer and `git`, because the renderer installs from its Git repository:

```bash
pip install -r requirements.txt
python -m engine.main
```

To run the tests, also install `requirements-dev.txt`, then run `python -m pytest -q tests`.

## How the engine works

![Pipeline: input, understand, propose, size and place, score, pick and repair, validate, output. Fallbacks: packed layout, letterbox, emergency layout. An optional model, off by default, may choose between finished layouts.](/docs/assets/pipeline.svg)

1. **Understand.** Each element gets a role, such as background, logo, product, headline, call to action or decoration, from its geometry, type, style and a few copy cues. Element ids never count. Groups and text on a shape move as one unit.
2. **Propose.** The engine classes the canvas as tall, square, landscape, banner or strip and builds candidate layouts for that shape: a vertical stack, the product beside a text column, or a single row.
3. **Size and place.** The engine sizes and wraps text with the official renderer's fonts and metrics, so what it measures is what the renderer draws. Type keeps a floor of 10 px, or 1.1% of the short side when that is larger, and goes below it only when a crowded layout needs the room.
4. **Score.** Coded checks in `engine/core/critic.py` score every candidate for collisions, overflow, readability, brand, hierarchy, use of space, clutter and faithfulness to the source. They read geometry, measured text and probes of the asset pixels, so the engine renders no candidate.
5. **Pick and repair.** The engine rebuilds the best candidate with matched margins and shrinks any text that would run past the canvas. If copy still collides, a packed layout gives every unit its own slot. That happens on 10 of the 169 benchmark tasks, all small ads.
6. **Validate.** As it finishes the layout, the engine clamps every element to the hard rules: each id once, every element and wrapped line inside the canvas, every non-background image within 2% of its source aspect, and integer font sizes.

If the engine raises, `resize()` returns a letterboxed copy of the source. If that fails too, it returns an emergency layout with every element as a box 1 px on its short side in the top-left corner. Both keep every id on the canvas, so `resize()` never raises.

## Why it is built this way

- **Code makes every decision.** Coordinates, sizes and the final pick come from code, because small models are unreliable at coordinates and at judging quality, as `docs/research.md` section 5 sets out.
- **A coded critic instead of rendering.** Scoring from geometry and measured text needs no render, so the engine can score every candidate it builds. The slowest of the 169 benchmark tasks takes 6.3 s in the scoring container.
- **Roles from geometry, never ids.** Hidden designs may use opaque ids, so a rule keyed on a name like `headline` would fail on them. `tests/test_robustness.py` checks that random ids give the same layout.
- **Never crash.** A crash scores nothing, so every failure falls back to a layout that passes the hard rules.
- **Ports for text and assets.** The core takes and returns plain schema dicts, opens no files and imports no renderer. It reaches fonts and asset pixels only through the `TextMeasurer` and `AssetProbe` ports in `engine/core/ports.py`, so the same core can run inside an Adobe Express add-on, where Express draws the text.
- **The model is optional and off.** The organizers have not published their approved model list, and the engine must work with no model at all.

![Ports and adapters: the stage-one adapter and a planned Express adapter both plug into one engine core through the TextMeasurer and AssetProbe ports](/docs/assets/ports.svg)

## Hard rules

- **What changes.** Position, size and font size. Opacity and `z_index` change only on an image whose asset file is missing from `brand_kit/assets/`. The engine shrinks that image, sets it to opacity 0 and places it behind the background, because a grey placeholder box reads as broken.
- **What never changes.** Content, font family and asset pixels. Every image keeps its source aspect within 2%, inside the starter code's 5%. The one exception is a `cover` backdrop under at least 85% of the canvas. It takes the canvas shape and the renderer crops it, so no pixel stretches, as the starter code allows.
- **Nothing else is hidden.** `CHAMELEON_DROP_POLICY=occlude` can hide low-priority units on strip canvases. It is off by default, and the Dockerfile does not set it.

## Results

We tuned the engine against a frozen benchmark of 169 tasks in four splits: `dev` with the 3 official samples and 36 generated designs, `test` with 18 held-out tasks, `probe` with 12 hand-made designs at 5 formats each, and `formats` with 4 designs at 13 sizes. The scorer's rubric is not published, so an LLM judge, Claude, scored the renders on the qualities in section 2.5 of the brief. The engine never calls the judge. Every number below comes from renders made inside the scoring Docker image.

| Check on v1.28 | Result |
|---|---|
| Structural checks | 169 of 169 tasks pass, with each id once: `dev` 39, `test` 18, `probe` 60, `formats` 52 |
| Rules audit | No element and no wrapped line past the canvas, integer font sizes, copy and fonts unchanged, worst image aspect drift 0.024% |
| Pairwise against v1.27, both image orders | 20 wins, 6 losses and 18 ties on the 44 tasks whose output changed |
| Judge score out of 10 | `dev` 6.85, `test` 7.11, `formats` 6.00, against 6.85, 7.11 and 5.96 for v1.27. `probe` was not rescored |
| Cold `--no-cache` build of this package | passes all 3 official samples |

### Known limits

- **Tiny type on the smallest ads.** All 40 of the 709 benchmark texts under 5 px are on the five small ad sizes: 120x90, 234x60, 300x50, 320x50 and 320x100. The 10 drawn at 1 to 2 px are all on the dense official Level 3 sample at 120x90 and 234x60. 11 of the 52 `formats` tasks score 4 or below, all on those five sizes.
- **Assets outside the brand kit.** Any non-background image whose file is not in `brand_kit/assets/` counts as missing, so a design that names other assets loses those images.

## Optional model harness

`docs/agentic-harness.md` describes a harness that lets a small local vision model choose between finished layouts. The model can only pick from a fixed list of answers, and every call has a timeout and a coded default. It ships off: with `CHAMELEON_AGENT` unset, `engine/resize.py` never imports it, and the 169 benchmark outputs are unchanged. We tested one real local model, Qwen3.5-0.8B. It named whichever layout it saw first in all 80 answers, and one call took 29 to 95 s in a linux/amd64 container shaped like the scorer, against a 5 s timeout. No model ships until the organizers publish their approved model list. <!-- harness-result -->

## Viewer add-on

FAQ Q5 asks for a viewer add-on, required but not scored. `addon/` holds an Adobe Express panel that shows an official sample's source beside the engine's output at six formats, using PNGs that `addon/build_renders.py` made in the scoring image. Nobody has tested it inside Express yet. `addon/README.md` has the steps to load it.

## Repo map

| Path | What it holds |
|---|---|
| `engine/resize.py` | The entry point the scorer calls, with the fallbacks |
| `engine/core/` | The engine: understanding, layout, sizing, critic and packed layouts |
| `engine/adapters/` | The stage-one adapters: text measured with the renderer's fonts, asset probes of the `brand_kit/` files |
| `tests/` | Hard rules, robustness, opaque ids and malformed input |
| `tools/`, `bench/suite/` | The benchmark, the judge, the packager, the README figures and the frozen 169 tasks, all outside the Docker image |
| `addon/` | The viewer add-on |

Deeper docs:
- [`docs/benchmark.md`](/docs/benchmark.md): how each change was measured, with the commands.
- [`docs/leaderboard.md`](/docs/leaderboard.md): results for every engine version.
- [`docs/experiments.md`](/docs/experiments.md): every experiment, kept or dropped.
- [`docs/research.md`](/docs/research.md): prior art and the evidence behind the architecture.
- [`docs/agentic-harness.md`](/docs/agentic-harness.md): the optional model harness.
