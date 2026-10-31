# Team Chameleonizer: Project Chameleon, Adobe University Hackathon 2026

Our entry implements `resize(source, target_canvas)` in `engine/resize.py`. Given a finished design as schema JSON and a new canvas size, it returns the same elements moved, resized and refit for that canvas. The copy, font families and asset pixels stay exactly as given, and the engine never stretches an image or generates anything new. The submitted version is engine v1.29.

![The official Level 2 sample, 1080x1080, re-laid-out by v1.29 as an 1800x2400 poster, a 336x280 ad, a 1600x400 banner, Full HD and a 970x90 strip](/docs/assets/resize-examples.jpg)

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

- Each task in `input/` produces `<task>_output.json` and `<task>_output.png` in `output/`.
- The engine runs on the CPU alone. It needs no GPU, makes no network calls and loads no model at run time.
- It imports the official renderer `cr-renderer`, pinned to a fixed commit, plus `skia-python`, Pillow and NumPy. `requirements.txt` pins every package to the version in the image our benchmark ran in.
- After 20 s on one task, it stops trying new candidates and returns the best one it has scored. The slowest of our 169 benchmark tasks took 9.1 s in the scoring container, and the official Level 3 sample once took 12.1 s there on a busy host.

To run without Docker, you need Python 3.11 or newer, plus `git`, because pip installs the renderer from its Git repository:

```bash
pip install -r requirements.txt
python -m engine.main
```

The tests also need `requirements-dev.txt`. Run them with `python -m pytest -q tests`.

## How the engine works

![Pipeline: input, understand, propose, size and place, score, pick and repair, validate, output. Fallbacks: packed layout, letterbox, emergency layout. An optional model, off by default, may choose between finished layouts.](/docs/assets/pipeline.svg)

1. It gives each element a role, such as background, logo, product, headline, call to action or decoration. The role comes from geometry, type, style and a few cues in the copy, never from the id. Each group then moves as one unit, and so does text with the shape it sits on.
2. It sorts the target canvas into one of five shapes: tall, square, landscape, banner or strip. For that shape it proposes candidate layouts, such as a vertical stack, the product beside a column of text, or everything in one row.
3. It sizes and wraps each candidate's text with the official renderer's own fonts, so what it measures is what the renderer draws. Type stays at 10 px or 1.1% of the canvas's short side, whichever is larger, unless a crowded layout leaves no room.
4. It scores every candidate with coded checks in `engine/core/critic.py` instead of rendering it. The checks cover collisions, overflow, readability, brand, hierarchy, use of space, clutter and faithfulness to the source.
5. It rebuilds the best candidate with matched margins and shrinks any text that would run past the canvas. If copy still collides, it switches to a packed layout that gives every unit its own slot. That happened on 11 of 169 benchmark tasks, nearly all at 320x100 or smaller.
6. It enforces the hard rules as it finishes: each id once, every element and wrapped line inside the canvas, non-background images within 2% of their source aspect, and integer font sizes.

If the engine raises, `resize()` returns a letterboxed copy of the source. If even that fails on malformed input, it returns an emergency layout with every element as a 1 px box in the top-left corner. Either way, `resize()` never raises.

## Why we built it this way

- Code makes every layout decision. Small models are unreliable at placing elements and at judging quality, as section 5 of `docs/research.md` sets out, and the organizers have not published an approved model list.
- The engine scores candidates from geometry and measured text instead of rendering them, so it can afford to score every one it builds.
- Roles come from geometry because hidden test designs may use opaque ids. `tests/test_robustness.py` checks that random ids give the same layout.
- Every failure path ends in a layout that passes the hard rules, because a crash scores nothing.
- The core takes and returns plain schema dicts, opens no files and imports no renderer. Fonts and asset pixels reach it only through the `TextMeasurer` and `AssetProbe` ports in `engine/core/ports.py`, so the same core can run inside an Adobe Express add-on, where Express draws the text.

![Ports and adapters: the stage-one adapter and a planned Express adapter both plug into one engine core through the TextMeasurer and AssetProbe ports](/docs/assets/ports.svg)

## Hard rules

- The engine changes only position, size and, on text, font size.
- Copy, font family and asset pixels never change.
- Nothing is hidden. The recap of the organizers' October 22 session asks that every input element stay visible.
- An image whose asset file is missing from `brand_kit/assets/` stays visible as the renderer's grey placeholder box. The engine never makes it the logo or the hero, and places it after the copy at about its share of the source canvas, at least 8 px on its shorter side.
- Every image keeps its source aspect within 2%, inside the starter code's 5% tolerance. The one exception is a `cover` backdrop that fills at least 85% of the canvas: it takes the canvas shape and the renderer crops it, so no pixel stretches.
- An opt-in setting, `CHAMELEON_DROP_POLICY=occlude`, can hide elements. It is off by default, and the Dockerfile does not set it.

## Results

We tuned the engine against a frozen benchmark of 169 tasks in four splits:

- `dev`: the 3 official samples and 36 generated designs.
- `test`: 18 held-out tasks we never tuned on.
- `probe`: 12 hand-made designs at 5 formats each.
- `formats`: 4 designs at 13 sizes, from print posters down to small ads.

The organizers have not published the scorer's rubric, so we had Claude judge the renders on the qualities in section 2.5 of the brief. The engine never calls the judge. Every number below comes from renders made inside the scoring Docker image.

| Check | Result |
|---|---|
| Structural checks | 169 of 169 tasks pass, with each id once: `dev` 39, `test` 18, `probe` 60, `formats` 52 |
| Rules audit | No element and no wrapped line past the canvas, no element hidden, `opacity` and `z_index` unchanged, integer font sizes, copy and fonts unchanged, worst image aspect drift 0.027% |
| Judge score out of 10 | `dev` 6.85, `test` 7.11, `formats` 6.00. Scored before the last change, which keeps missing-asset images visible and touched only 19 tasks |
| Cold `--no-cache` build of this package | passes all 3 official samples |

We judged each engine change against the engine before it, in both image orders, on the tasks it changed, and kept it only when it won more than it lost. The exception is the last change, which follows the organizers' rule that every element stays visible and shipped without a new score. `docs/leaderboard.md` records each round.

### Known limits

- Tiny text on the smallest ads. Of the 709 benchmark texts, 40 are drawn below 5 px, all at 120x90, 234x60, 300x50, 320x50 and 320x100. The 10 drawn at 1 to 2 px are all on the dense official Level 3 sample at 120x90 and 234x60. The judge scored 11 of the 52 `formats` tasks at 4 or below, all at those five sizes.
- Missing assets. The engine reads asset pixels only from `brand_kit/assets/`, the folder the official renderer draws from. A missing image draws as a grey box and never becomes the hero, so a design whose product photo is missing has no hero. The box also takes room: on the official Level 3 sample at 300x250 it squeezes the ribbon label to 5 px.
- The time limit. The candidate search stops after 20 s per task. On a much slower or heavily loaded machine it can stop early and choose from fewer layouts.
- The renderer draws no strokes, so an outline-only shape, such as a ghost button, is invisible in both the source and our output.

## Optional model harness

`docs/agentic-harness.md` describes a harness that lets a small local vision model choose between finished layouts the engine has already built.

- Models are interchangeable. Any local vision model plugs in through the same interface, with nothing to train, in line with the recap's call for "smaller, interchangeable models".
- The model can pick only from a fixed list of answers, and every call has a timeout and a coded default, so it can never break the hard rules.
- It ships off. With `CHAMELEON_AGENT` unset, `engine/resize.py` never imports it, and the 169 benchmark outputs are unchanged.
- We tested it with one real local model, Qwen3.5-0.8B. It picked whichever layout it saw first in all 80 answers, so it changed no output, and each call took 29 to 95 s in a container set up like the scorer's, against a 5 s timeout. No model ships until the organizers publish their approved list. <!-- harness-result -->

## Viewer add-on

FAQ Q5 asks for a viewer add-on, which is required but not scored. `addon/` holds an Adobe Express panel that shows an official sample's source beside the engine's output at six formats. The panel shows PNGs that `addon/build_renders.py` rendered in the scoring image, and `addon/README.md` has the steps to load it. We loaded it in Adobe Express and used **Add to page** to place a render on the canvas.

## Repo map

| Path | What it holds |
|---|---|
| `engine/resize.py` | The entry point the scorer calls, with the fallbacks |
| `engine/core/` | The engine: understanding, layout, sizing, critic and packed layouts |
| `engine/adapters/` | The stage-one adapters: text measured with the renderer's fonts, asset probes of the `brand_kit/` files |
| `tests/` | Hard rules, robustness, opaque ids and malformed input |
| `tools/`, `bench/suite/` | The benchmark, the judge, the packager, the README figures and the frozen 169 tasks, all outside the Docker image |
| `addon/` | The viewer add-on |
| `docs/` | The benchmark method, results for every engine version, the experiment log, research notes and the harness design |

## Team

Team Chameleonizer, University of Maryland, College Park: [Ricky Chen](https://github.com/HackedRico), [Israel Ogwu](https://github.com/israelo19) and [Aprameya Kannan](https://github.com/aprameyak).
