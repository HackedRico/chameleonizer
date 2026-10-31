# Team Chameleonizer: Project Chameleon, Adobe University Hackathon 2026

Our entry implements `resize(source, target_canvas)` in `engine/resize.py`. Given a finished design as schema JSON and a new canvas size, it returns the same elements moved, resized and refit for that canvas. The copy, font families and asset pixels stay exactly as given, and the engine never stretches an image or generates anything new. The submitted version is engine v1.28.

![The official Level 2 sample, 1080x1080, re-laid-out by v1.28 as an 1800x2400 poster, a 336x280 ad, a 1600x400 banner, Full HD and a 970x90 strip](/docs/assets/resize-examples.jpg)

The official Level 2 sample on five canvases, drawn by the official renderer inside the scoring Docker image.

## Run it

The organizers' commands work unchanged:

```bash
docker build --platform linux/amd64 -t my-chameleon-engine .
docker run --rm --network none \
  -v $(pwd)/input:/app/input \
  -v $(pwd)/output:/app/output \
  my-chameleon-engine
```

Each task in `input/` produces `<task>_output.json` and `<task>_output.png` in `output/`. The engine makes no network calls and loads no model. `requirements.txt` pins every package to the version installed in the image our benchmark ran in.

To run without Docker, you need Python 3.11 or newer, plus `git`, because pip installs the renderer from its Git repository:

```bash
pip install -r requirements.txt
python -m engine.main
```

The tests also need `requirements-dev.txt`. Run them with `python -m pytest -q tests`.

## How the engine works

![Pipeline: input, understand, propose, size and place, score, pick and repair, validate, output. Fallbacks: packed layout, letterbox, emergency layout. An optional model, off by default, may choose between finished layouts.](/docs/assets/pipeline.svg)

The engine starts by working out what each element is. It gives each element a role, such as background, logo, product, headline, call to action or decoration. The role comes from the element's geometry, type and style, plus a few cues in the copy, and never from its id. From then on, each group moves as one unit, and so does each piece of text with the shape it sits on.

Next it sorts the target canvas into one of five shapes: tall, square, landscape, banner or strip. For that shape it proposes candidate layouts, such as a vertical stack, the product beside a column of text, or everything in one row. It then sizes and wraps each candidate's text with the official renderer's own fonts and metrics, so the text the engine measures is the text the renderer draws. Type stays at 10 px or 1.1% of the canvas's short side, whichever is larger, and the engine goes below that floor only when a crowded layout leaves no other room.

Instead of rendering the candidates, the engine scores them. Coded checks in `engine/core/critic.py` read geometry, measured text and probes of the asset pixels. Together they score every candidate for collisions, overflow, readability, brand, hierarchy, use of space, clutter and faithfulness to the source. The engine then rebuilds the best candidate with matched margins and shrinks any text that would run past the canvas. If copy still collides after that, it switches to a packed layout that gives every unit its own slot. In our benchmark that happened on 10 of 169 tasks, all of them small ads.

As it finishes the layout, the engine enforces the hard rules on every element. Each id appears once, and every element and every wrapped line stays inside the canvas. Non-background images keep their source aspect within 2%, and every font size is an integer.

When something goes wrong, `resize()` still returns a valid design. If the engine raises, `resize()` returns a letterboxed copy of the source. If even that fails on malformed input, it falls back to an emergency layout that shrinks every element to a box 1 px across its short side in the top-left corner. Both keep every id on the canvas, so `resize()` never raises.

## Why we built it this way

In the submitted engine, coordinates, sizes and the final choice of layout come from code. Small models are unreliable both at placing elements and at judging quality, as section 5 of `docs/research.md` sets out. The organizers have also not published their approved model list, so the engine has to work with no model at all.

Scoring from geometry and measured text needs no render, so the engine can score every candidate it builds. The slowest of our 169 benchmark tasks finishes in 6.3 s in the scoring container.

Hidden test designs may use opaque ids, so the engine reads roles from geometry. A rule keyed on an id like `headline` would break on those designs. `tests/test_robustness.py` checks that random ids produce the same layout as the original ids. Every failure path also ends in a layout that passes the hard rules, because a crash scores nothing.

We built the core so it can run inside an Adobe Express add-on after stage one. It takes and returns plain schema dicts, opens no files and imports no renderer. Fonts and asset pixels reach it only through the `TextMeasurer` and `AssetProbe` ports in `engine/core/ports.py`, so inside Express an adapter can supply them while Express draws the text.

![Ports and adapters: the stage-one adapter and a planned Express adapter both plug into one engine core through the TextMeasurer and AssetProbe ports](/docs/assets/ports.svg)

## Hard rules

The engine changes only position, size and font size, except on an image whose asset file is missing from `brand_kit/assets/`. In its place the renderer would draw a grey placeholder box that reads as broken, so the engine shrinks that image, sets its opacity to 0 and gives it a `z_index` behind the background. The engine hides nothing else. An opt-in setting, `CHAMELEON_DROP_POLICY=occlude`, can hide low-priority units on strip canvases, but it is off by default and the Dockerfile does not set it.

Copy, font family and asset pixels never change. Every image keeps its source aspect within 2%, inside the starter code's 5% tolerance. The one exception is a `cover` backdrop that fills at least 85% of the canvas. The engine sizes its box to the full canvas and the renderer crops the image to fit, the way the starter code treats a full-bleed backdrop, so no pixel stretches.

## Results

We tuned the engine against a frozen benchmark of 169 tasks in four splits. The `dev` split has the 3 official samples and 36 generated designs, and `test` has 18 held-out tasks. The `probe` split runs 12 hand-made designs at 5 formats each, and `formats` runs 4 designs at 13 sizes. The organizers have not published the scorer's rubric, so we had Claude judge the renders on the qualities in section 2.5 of the brief. The engine never calls the judge. Every number below comes from renders we made inside the scoring Docker image.

| Check on v1.28 | Result |
|---|---|
| Structural checks | 169 of 169 tasks pass, with each id once: `dev` 39, `test` 18, `probe` 60, `formats` 52 |
| Rules audit | No element and no wrapped line past the canvas, integer font sizes, copy and fonts unchanged, worst image aspect drift 0.024% |
| Pairwise against v1.27, both image orders | 20 wins, 6 losses and 18 ties on the 44 tasks whose output changed |
| Judge score out of 10 | `dev` 6.85, `test` 7.11, `formats` 6.00, against 6.85, 7.11 and 5.96 for v1.27. `probe` was not rescored |
| Cold `--no-cache` build of this package | passes all 3 official samples |

### Known limits

The smallest ads are the weak spot. Of the 709 text elements in the benchmark, 40 are drawn below 5 px, all on the five small ad sizes: 120x90, 234x60, 300x50, 320x50 and 320x100. The 10 drawn at 1 to 2 px all belong to the dense official Level 3 sample at 120x90 and 234x60. In the `formats` split, 11 of 52 tasks score 4 or below, all of them at the five small ad sizes.

The engine also treats any non-background image whose file is not in `brand_kit/assets/` as missing, so a design that names other assets loses those images.

## Optional model harness

`docs/agentic-harness.md` describes a harness that lets a small local vision model choose between finished layouts the engine has already built. The model can pick only from a fixed list of answers, and every call has a timeout and a coded default. The harness is off by default. With `CHAMELEON_AGENT` unset, `engine/resize.py` never imports it, and the 169 benchmark outputs are unchanged.

We tested the harness with one real local model, Qwen3.5-0.8B. In all 80 answers it picked whichever layout it saw first. Each call took 29 to 95 s in a linux/amd64 container set up like the scorer's, far past the harness's 5 s timeout. No model ships with the entry until the organizers publish their approved model list. <!-- harness-result -->

## Viewer add-on

FAQ Q5 asks for a viewer add-on, which is required but not scored. `addon/` holds an Adobe Express panel that shows an official sample's source beside the engine's output at six formats. The panel shows PNGs that `addon/build_renders.py` rendered in the scoring image, and `addon/README.md` has the steps to load it. Nobody has tested the panel inside Express yet.

## Repo map

| Path | What it holds |
|---|---|
| `engine/resize.py` | The entry point the scorer calls, with the fallbacks |
| `engine/core/` | The engine: understanding, layout, sizing, critic and packed layouts |
| `engine/adapters/` | The stage-one adapters: text measured with the renderer's fonts, asset probes of the `brand_kit/` files |
| `tests/` | Hard rules, robustness, opaque ids and malformed input |
| `tools/`, `bench/suite/` | The benchmark, the judge, the packager, the README figures and the frozen 169 tasks, all outside the Docker image |
| `addon/` | The viewer add-on |

These docs go into more detail:

- [`docs/benchmark.md`](/docs/benchmark.md) explains how we measured each change and gives the commands.
- [`docs/leaderboard.md`](/docs/leaderboard.md) has results for every engine version.
- [`docs/experiments.md`](/docs/experiments.md) logs every experiment we kept or dropped.
- [`docs/research.md`](/docs/research.md) covers prior art and the evidence behind the architecture.
- [`docs/agentic-harness.md`](/docs/agentic-harness.md) documents the optional model harness.
