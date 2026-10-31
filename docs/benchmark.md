# Chameleonizer benchmark

Adobe's scorer only runs after submission, on hidden designs. This benchmark is our stand-in for deciding whether an engine change is better. Treat its numbers as a proxy: they rank our own versions, but they don't predict Adobe's score.

## What it measures

| Adobe's scoring | Our stand-in | Comparable across engine versions |
|---|---|---|
| Hidden test designs | The frozen suite in `bench/suite/`, plus `input/` | Yes, the files are frozen |
| Structural checks on the JSON | `tools/checks.py`, `structural()` | Yes |
| AI judge against a written rubric | `tools/judge.py`: Claude scoring with the brief's rubric rows | Yes, if the model and prompt stay fixed |
| (none) | Pixel checks: worst text contrast and backdrop busyness under text | Yes |
| (none) | The engine's own critic score | **No.** The critic is part of the engine and changes with it; use it only within one version |

## The suite

`python -m tools.freeze_suite` writes the suite; the files are committed, so scores stay comparable even if the generator changes.
- **`dev`:** 39 tasks. These are the 3 official samples plus 2 generated designs for each of the 18 catalogue formats in `tools/catalog.py`. Tune against these.
- **`test`:** 18 tasks, one more generated design for each format. It is held out: report its numbers, but never change the engine because of a specific test task.

The generated designs mimic the official samples: the Solstice brand kit, varied element counts and copy lengths, badge rows, rotated ribbons, decorative shapes, missing assets, and meaningless element ids half the time.

- **`probe`:** 60 tasks, 12 hand-made source designs times 5 differently shaped targets. They test structures the generator never makes:
  - a text-only poster, a quote card and a left-aligned editorial layout;
  - a feature list with bullet dots, two products side by side, and a product photo above a dark text band;
  - a big-number promo, an event story with date, time and place lines, a card panel holding the copy, and two buttons;
  - a wide banner source and a logo-only brand card.

  It measures how well the engine holds up on unseen designs, a rubric row the generated splits barely test. Fix what it reveals with general rules, never per design. v1.15 scored 5.6 overall there with the in-session judge, against 6.7 on `dev`.

- **`formats`:** 52 tasks, the three official samples and one generated story, each resized to 13 formats. All but the 320x50 mobile banner are missing from the catalogue.
  - Print sheets: A4 both ways, US Letter and an 18x24 poster. The brief lists "a printed poster" among the target shapes, and print sizes run to thousands of pixels.
  - Screens: Full HD and a YouTube banner.
  - Ads: a 336x280 rectangle, a 970x90 super leaderboard, and the small sizes 120x90, 234x60, 300x50, 320x50 and 320x100. v1.26 added the small sizes; the split had 32 tasks before.

## How to run it

1. Run the engine on every split inside the scoring image. The scorer renders on Linux, whose text metrics differ from macOS, so every number since v1.16.1 comes from these container runs. Build the image with the `docker build` command in the root README.md, then mount the working tree's engine, tools and suite into it:
   ```bash
   mkdir -p output
   for s in dev test probe formats; do
     docker run --rm --platform linux/amd64 --network none --entrypoint python \
       -v "$PWD/engine":/app/engine:ro -v "$PWD/tools":/app/tools:ro -v "$PWD/bench":/app/bench:ro \
       -v "$PWD/output":/app/output \
       my-chameleon-engine -m tools.bench --suite bench/suite/$s --out output/bench/<version>-$s
   done
   ```
   For a quick look during development, the same bench runs on the host, but its renders use the host's text metrics:
   ```bash
   .venv/bin/python -m tools.bench --suite bench/suite/dev --out output/bench/<version>-dev
   ```
2. Compare against the current best, task by task, with side-by-side images:
   ```bash
   .venv/bin/python -m tools.compare output/bench/<best>-dev output/bench/<version>-dev --sheet output/bench/cmp.png --tasks <task ids>
   ```
3. Ask the LLM judge. This needs `.env` credentials.
   - **Accept or reject a change:** compare it against the current best. Each task is asked in both image orders, and a verdict that flips with the order counts as a tie.
     ```bash
     .venv/bin/python -m tools.judge --pair output/bench/<best>-dev output/bench/<version>-dev
     ```
   - **Leaderboard numbers:** absolute 1 to 10 scores per rubric row.
     ```bash
     .venv/bin/python -m tools.judge --run output/bench/<version>-test
     ```
4. Record the result in `docs/leaderboard.md` and `docs/experiments.md`.

## When the API judge is unavailable

Claude agents inside a Claude Code session can judge instead, with the same rubric. It needs a session with workflows enabled.

- **Pairwise:**
  1. Lay out a round. Each `--pair` is a label, the base run and the candidate run. Without `--tasks`, every task whose output changed is compared.
     ```bash
     .venv/bin/python -m tools.blind make output/blind/<round> --pair <label> output/bench/<best>-dev output/bench/<version>-dev
     ```
  2. Run `tools/workflows/blind_pairwise_judge.js` with `{file, batches}` from the printed output.
     - Each agent views `original.png`, `A.png` and `B.png`.
     - The two image orders of a task go to different agents.
  3. Save the workflow's output file, then tally it. A verdict that flips with the order counts as a tie, exactly as in `tools/judge.py`.
     ```bash
     .venv/bin/python -m tools.blind resolve output/blind/<round> <workflow output file>
     ```
- **Absolute:**
  - `python -m tools.blind absolute output/blind/<name> <run>` lays out one run.
  - `tools/workflows/absolute_judge.js` scores it.
  - `python -m tools.blind scores output/blind/<name> <output file>` prints the means and worst tasks.
- **Calibration:**
  - On 31 tasks where the API judge was decisive, in-session judges agreed 24 times, tied 7 times and never contradicted it.
  - Treat their ties as "no clear difference", not as evidence against a change.
  - Their absolute scores sit on a different scale from the API judge's, so compare versions only within one judge.

## Model harness

`docs/agentic-harness.md` describes the optional model harness, off by default, and how to evaluate a local model with it.
- **Byte identity:** `python -m tools.digests run --suite input --suite bench/suite/dev --out output/digests/<name>.json` records a digest per task under the current `CHAMELEON_*` environment, and `python -m tools.digests diff A B` exits 1 on any difference. Use it for any change that must not move outputs. When `CHAMELEON_AGENT` names a backend, `run` also exits 1 if the harness never came up, since identity would then prove nothing.
- **With a model:** `python -m tools.bench --agent ...` lets the harness configured by `CHAMELEON_AGENT*` choose among the engine's finalists. Each row gets an `agent` field and the run writes `agent_trace.jsonl`; without `--agent` the bench is unchanged.
- **Agreement first:** `python -m tools.agree` measures how often a model agrees with the frontier judge, on a dataset built from judged rounds, before any end-to-end run.

## Accepting a change

- **Hard gate:** every task passes structural checks, and the contract tests pass. Run `python -m pytest -q tests`.
- **Win the pairwise judge on `dev`:** the change must win more tasks than it loses, with no new fail in any format family.
- **Then confirm on `test`:** report the absolute scores and tag the version as `engine-vX.Y`.

## Rules the engine must keep

These are checked in code. The brief and `RULES.md` are the authority.
- Every element id exactly once, everything inside the canvas, non-background images within 5% of their aspect ratio.
- Font size may change, and is expected to. Text content and font family may not.
- Asset pixels are never regenerated or redrawn.
- No tailoring to specific designs, so element ids are never read for meaning.
- In practice, text stays at or above the canvas's readable size, except on crowded layouts and the smallest ad sizes, where keeping every element and every wrapped line on the canvas comes first. The rules allow any font size, but the judge penalizes text it cannot read.

## Known limits

- The judge is Claude, not Adobe's evaluator. Research suggests even frontier models agree only moderately with human design preferences (see `docs/research.md` section 5). So aim to eliminate obvious failures, not to chase small judge differences.
- Generated designs share one brand and one poster style. Hidden designs may be more varied.
- The suite covers the catalogue's formats, but Adobe's catalogue may differ.
- `tools/judge.py` records its API spend in `output/judge_spend.json`, or in the file `JUDGE_LEDGER` names, and stops once the total reaches `JUDGE_BUDGET_USD`. A pairwise task whose output did not change counts as a tie without a call.
