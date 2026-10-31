# Experiments

One entry per experiment, newest first. Failed ideas belong here too. Read this before trying an idea, so nobody repeats a dead end.

Use this shape for each entry:

```
### YYYY-MM-DD: short name
- Who and branch:
- Hypothesis:
- Change:
- Result: scoreboard before and after, plus links to telling renders
- Verdict: keep, drop or revisit, and why
```

How scores were taken below:
- **From 2026-10-16 on:** entries use the frozen suite and `tools/judge.py`, Claude Opus 5.5 through the API. See `docs/benchmark.md`.
- **The 2026-10-13 entries:** the bench was the 3 official samples plus 22 generated tasks, seed 1. "Judge" meant Claude scoring renders by eye inside a session, because no API key was available yet.
- **"Critic"** is the engine's own coded score. It changes with the engine, so never compare it across versions.
- **The v1.28 candidate entry:** changes were judged blind by in-session Claude agents with `tools/blind.py` and `tools/workflows/`, in both image orders, two independent replicas per change. A verdict that flips with the order is a tie. The other 2026-10-30 entries use the API judge.

### 2026-10-30: v1.29 release, a missing asset stays visible
- **Branch:** `fix/missing-asset-visible`, on `docs/readme-prose` at `3600a2d`.
- **Hypothesis:** the recap of the organizers' October 22 information session says "Every input element should remain visible in the baseline output, consistent with the challenge requirements." v1.28 hid 19 missing-asset images across the benchmark, so the submission has to ship the visible placeholder from the entry below, with docs and add-on previews that match it.
- **Change:** v1.29 is v1.28 plus that placeholder change. The release also updates the submission README, rebuilds the add-on previews and gives the default drop policy's reason in `engine/resize.py`.
- **Result:** container runs in `chameleonizer:v1.26` with the release engine mounted, saved as `output/bench/v129-*`.
  - **Outputs:** all 169 are byte-identical to `vis-*`, so the entry below describes the release. Against `final-*`, exactly the 19 tasks with a missing asset changed.
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52. The independent rules check finds 0 problems, no hidden element and no `opacity` or `z_index` change. The worst aspect drift of an image that is not a full-bleed backdrop is 0.027%, on the 18x8 placeholder in `level3__button_120x90`, against 0.024% for v1.28. 956 tests pass and 11 skip. `tools.package_submission --verify` passes with 0 warnings.
  - **Type sizes and fallbacks:** 709 texts, 40 under 5 px and 10 under 3 px, as in v1.28. The packed fallback now wins on 11 tasks instead of 10, and the new one is the official Level 3 sample at 728x90.
  - **Time:** the slowest task, `level3__large_mobile_banner`, took 9.1 s in the `vis-*` run and 5.4 s in the `v129-*` run, against 6.3 s for v1.28. Both runs produced the same outputs, so the gap is run-to-run timing. The README quotes 9.1 s. The official Level 3 sample at 728x90 got slower. It took 1.7 s in `final-dev`, but 3.9 s in `v129-dev`, 8.3 s in `vis-dev`, 12.1 s in the package verify and 5.1 to 6.3 s in three reruns with a host load average near 16. It now wins with the packed row, and the 20 s budget still leaves room.
  - **Judge:** not run. The key in `.env` returned 401 even on a models list call, so no absolute score or pairwise verdict exists for the 19 changed tasks. The README keeps v1.28's absolute scores and says so.
  - **Viewer add-on:** the previews were rebuilt in the scoring image. Only the 6 Level 3 renders changed, and each now shows the grey box. Level 3 at 300x250, a size outside the benchmark, got worse. The box sits between the button and the fine print, and in the same layout the headline drops from 12 px to 10 px and the ribbon label from 8 px to 5 px, which no longer reads as "SUNSET SALE".
- **Verdict:** keep as v1.29, the submission build, for the rules. Judge the 19 changed tasks pairwise against `final-*` once `.env` has a working key, and look at the Level 3 layout at 300x250.

### 2026-10-30: a missing asset stays visible as the renderer's placeholder
- **Branch:** `fix/missing-asset-visible`, on `docs/readme-prose` at `3600a2d`.
- **Hypothesis:** the October 22 session recap says "Every input element should remain visible in the baseline output", FAQ Q1 allows only position, size and text font size to change, and the Level 3 note offers "skip it, placeholder it". Only the placeholder satisfies all three, so v1.28's hiding of a missing asset, opacity 0 behind the backdrop, has to go whatever it costs in score.
- **Change:** by default an image whose asset file is missing is laid out and drawn as the grey placeholder, with its source `opacity` and `z_index`. It is always a secondary image, never the logo or the hero. Stacks and splits park it like a sticker once the copy is placed, packed rows set it under the action at the copy's scale, and its shorter side keeps at least 8 px. The critic and collision checks treat its whole box as opaque, since the probe reports no transparent margin for it. `CHAMELEON_DROP_POLICY=occlude` still hides it.
- **Result:** container bench in `chameleonizer:v1.26`, saved as `output/bench/vis-*`. Structural checks pass on all 169 tasks. Exactly the 19 tasks with a missing asset changed and the other 150 outputs are byte-identical to `final-*`. The independent rules check finds 0 problems and no `opacity` or `z_index` change anywhere, where `final-*` had 19 of each. Placeholders run from 18x8 at 120x90 to 440x195 on A4, about 1% of the canvas. On 15 tasks the layout is unchanged apart from the parked box. On four Level 3 strips the button and ribbon shift to make room under the button, and Level 3 at 728x90 switches from the strip columns to the packed row, because the box parked beside the rotated ribbon pushed the collision penalty past the packed threshold. No box covers copy. The API judge could not run: the key in `.env` returned 401 on every request, so no pairwise verdict was recorded. Contact sheets of base against new are in the session scratchpad.
- **Verdict:** keep, for the rules. Expect the judge to mark some of these 19 renders down for the grey box.

### 2026-10-30: v1.28 release review fixes
- **Branch:** `rc/engine-v1.28`, on the v1.28 tree.
- **Hypothesis:** the release review's code-safe findings can be fixed without moving any benchmark output.
- **Change:** the longest word ends at a hard line break, as the renderer's wrap does. A zero-width `cover` image skips the fit mapping instead of dividing by zero. A rigid label that shrinks near the canvas bottom caps its box at the edge. The emergency layout scales both sides of an image box by one factor. The `p1-noorig` ablation carries the `p1` grammar. Harness tests that must reach the advisor get a 600 s task budget, so machine load cannot fail them. Each engine fix has a test that fails without it.
- **Result:** container bench in `chameleonizer:v1.26` with the fixed engine mounted, saved as `output/bench/pass-*`. All 169 outputs are byte-identical to `final-*`, and structural checks pass on all 169, so nothing was judged. The bench has no text with a hard line break, so it cannot show the first fix. On the official Level 1 sample with the headline written as "Chase", "the" and "Light" on three lines, the headline at 160x600 went from 7 px to 29 px, and it stays larger than the CTA at all four sizes tested: 1080x1350, 160x600, 300x250 and 728x90.
- **Deferred:** three findings that would move layouts on unseen designs. A `cover` backdrop under 85% to 99.5% of the canvas still takes the canvas shape, which the renderer crops, and the submission README now says so. The 20 s candidate time budget stays, with about 3 times headroom over the slowest task in the container. A banner or strip design with only stickers or only a badge row still falls back to the letterbox.
- **Verdict:** keep.

### 2026-10-30: optional model harness, first real-model run with Qwen3.5-0.8B
- **Branch:** `rc/engine-v1.28` at `0d43efa`, the v1.28 tree, in a detached worktree. No code change. Weights: `Qwen3.5-0.8B-Q4_K_M.gguf` and `mmproj-F16.gguf` from `unsloth/Qwen3.5-0.8B-GGUF`, 737 MB together, kept outside the repo.
- **Hypothesis:** the harness runs end to end against a real local vision model, and the model's tie-breaks are informative and fast enough to be worth turning on.
- **Change:** none. Settings are the llama-server preset in `docs/agentic-harness.md`: prompt `p1` with a grammar, `top_k` 2, epsilon 3, 5 s per call, 10 s per task.
- **Result:**
  - **Mac with Metal:** an Apple M2 with 8 cores and 16 GB, Homebrew llama-server build 11960 with the doc's flags plus `--mmproj`. A Docker build used all 8 CPUs during these runs. Cold start to `/health` took 18.9 s.
    - `tools.bench --agent` on dev: structural 39/39, with no traceback or warning. 15 firing tasks made 15 calls, and all 15 parsed, all `A`, with no errors. Reasons: kept 15, single 15, no_eligible 4, budget 5. Nothing switched, and all 39 outputs are byte-identical to the same machine with the harness off, so nothing was judged.
    - Per call p50 1.49 s and p95 2.17 s. Per task mean 2.55 s and max 12.2 s, against 0.45 s and 4.9 s with the harness off. The 5 budget tasks spent 5.1 to 7.4 s rendering three images under the build's load, which left less than one 5 s call inside the 10 s budget.
    - Position check: both orders asked on all 20 eligible dev pairs with `p1` and `p1-num`. 80 of 80 answers named the candidate shown first, and `tools.agree doctor` did the same on its clean-versus-messy pair. The model does not tell layouts apart, so the both-orders rule turns every answer into keep.
  - **Mac CPU only:** the same server with `-ngl 0 -t 4` and the projector on the CPU, Docker idle. Doctor took 36.0 s and 45.4 s per call.
  - **Scorer shape:** `tools/docker/agent-bench.Dockerfile`, linux/amd64 under Rosetta, AVX2 without AVX-512, run with `--cpus 4 --memory 6g --network none`. The build took 23 min, almost all of it compiling llama.cpp.
    - Cold load 2.3 to 5.4 s, warmup call 3.5 to 4.9 s, llama-server peak RSS 1.46 GB.
    - Doctor took 95.3 s and 58.6 s per call, both `A`. On the same pair that is 29 to 39 times the Metal latency. The M2's CPU alone accounts for most of the gap, and Rosetta adds 1.3 to 2.6 times on top.
    - `tools.bench --agent` on 6 dev tasks with the call timeout raised to 120 s: structural 6/6. 3 tasks fired with one call each, taking 29.3 s, 44.2 s and 46.3 s, all `A`, and firing tasks took 30 to 47 s in all. The other 3 fired on the Mac but were single or no_eligible here, because Linux text metrics finish the runner-up differently.
  - **Not run:** `tools.agree run` and its stage-one gates, because this tree has no `bench/agree/v1`.
  - llama-server warns that Qwen-VL needs at least 1024 image tokens for grounding. The harness caps images at 256, which may be part of why the model cannot see the difference, and more tokens would slow CPU calls further.
- **Verdict:** keep the harness off and do not ship this model. Under the shipped defaults in the scorer, the warmup passes, the first call times out, and the breaker turns the harness off for the run, so outputs stay the engine's. Revisit only with a model that passes the position check, a judged `tools.agree` dataset, and CPU calls under 5 s in the amd64 container.

### 2026-10-30: v1.28 release, the candidate plus three judged fixes
- **Branch:** `exp/final`, cut from `exp/rc` at `5732f41`, which is v1.27.
- **Change:** v1.28 is v1.27 plus the candidate's three engine fixes, the `tools/` fix that exempts only a full-bleed `cover` backdrop from the aspect check, and the three fixes accepted below: buttons kept in one row, cover images sized by their visible pixels, and slimmer floored buttons on small near-square ads. The YouTube safe area was rejected and is left out. The branch also pins every runtime dependency to the benchmark image and carries the release doc fixes. It also carries the optional model harness, off by default, whose entry is below.
- **Result:** container runs in `chameleonizer:v1.26` with this branch's engine mounted, saved as `output/bench/final-*`.
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52, with no tracebacks or fallbacks. The slowest task takes 6.3 s. 677 tests pass and 10 skip, and 938 and 11 with the model harness.
  - **Outputs against the candidate:** dev 2, probe 14 and formats 4 changed, and test is byte-identical. Every changed output matches the run of the one fix that changed it, except `photo_band__medium_rectangle`, which two fixes touch. `level3__full_hd` changed only because the candidate run hit the 20 s time budget there. The release output for that task is byte-identical to v1.27's.
  - **Pairwise against the candidate,** to check that the fixes combine: `tools.judge --pair`, Opus 5.5, both orders. Dev 1 win, 0 losses, 1 tie. Probe 8 wins, 2 losses, 4 ties. Formats 3 wins, 0 losses, 1 tie, counting the `level3__full_hd` timing win. Overall 12 wins, 2 losses and 6 ties, or 11 wins without the timing win. The losses, `photo_band__leaderboard` and `photo_band__medium_rectangle`, crop the bottle's glow behind the text panel, and the second also shrinks the logo.
  - **Pairwise against v1.27:** dev 4 wins, 1 loss, 5 ties. Test 0 wins, 0 losses, 2 ties. Probe 11 wins, 1 loss, 4 ties. Formats 5 wins, 4 losses, 7 ties. Overall 20 wins, 6 losses and 18 ties on 44 changed tasks. The formats losses are `level2__half_banner`, `level3__button_120x90`, `level3__half_banner` and `story1__half_banner`.
  - **Absolute, `tools.judge --run`:** dev 6.85, test 7.11 and formats 6.00, against 6.85, 7.11 and 5.96 for v1.27. Fails at 4 or below: dev 2, test 0, formats 11. The new dev fail, `official_level3_advanced`, is byte-identical to v1.27, so it is judge noise. All 11 formats fails are on the five small ad sizes, and they include all 10 packed tasks. Probe was not rescored.
  - **Rules check on the container outputs,** measured inside the image with the engine's own measurer: 169 tasks and 709 texts. No element box and no wrapped line goes past the canvas, every font size is an integer, and copy and font families are unchanged. The worst aspect drift of an image that is not a full-bleed backdrop is 0.024%. Opacity and `z_index` change only on the 19 missing-asset images.
  - **Type sizes:** 81 texts under 7 px, against 78 for v1.27. 40 under 5 px, against 44, all on the five small ad sizes. 10 under 3 px, against 11, all on the official Level 3 sample at 120x90 and 234x60.
  - **Viewer add-on:** the previews were rebuilt with this engine in the scoring image. Level 2 at 300x250 and Level 3 at 1600x400 and 300x250 changed. Level 3 at 300x250 now draws "SUNSET SALE" with its space, and the space in the button's "Summer Line" is narrow.
- **Verdict:** keep as v1.28, the submission build.

### 2026-10-30: cover images sized by their visible pixels, on the v1.28 candidate
- **Branch:** `exp/fix-cover-fit-visible-fraction`, commit `69cb113`.
- **Hypothesis:** `probe_visible_fraction` counted a `cover` image as visible across its whole box. On `photo_band` the bottle fills only y 108 to 852 of its 1080x945 box, so the hero was sized by empty space, read before the logo, and pushed the logo below it.
- **Change:** `sizing.probe_visible_fraction` maps the asset's alpha bounds through the renderer's center crop for `cover` and clips them to the box, as `contain` already does for its letterbox. Every cover backdrop still maps to the full box, so only the 5 `photo_band` tasks change.
- **Result:** structural 169/169, no fallbacks, and 673 tests pass. Pairwise against the candidate on probe: 2 wins, 1 loss, 2 ties. `instagram_story` and `wide_skyscraper` win because the logo is back on top. `medium_rectangle` loses on a tiny logo and a glow cropped by the text panel. On `leaderboard` and `linkedin_banner` the verdict flipped with the order.
- **Verdict:** keep. The glow halo now scales with the bottle and the text panel clips it on wide formats, which the critic does not see. Counting an owned halo in the hero's footprint is the follow-up.

### 2026-10-30: buttons the source sets side by side stay in one row, on the v1.28 candidate
- **Branch:** `exp/fix-cta-row-side-by-side`, commit `c893fd8`.
- **Hypothesis:** on `two_ctas` the ghost "Learn More" button became a badge chip and stacked about 100 px below "Shop Now". The judge called it detached from the CTA group.
- **Change:** `understand._merge_cta_row` folds the CTA chip together with a chip on its source row when the centres differ by less than half the smaller height, the heights are within 0.8 to 1.25 of each other, and the gap is at most one CTA height. `_size_label_row` then lays the row out side by side. Strip canvases skip the fold, because at 320x50 the row stacked from the top edge to the bottom edge. New `tests/test_cta_row.py`.
- **Result:** structural 169/169, no fallbacks, and 677 tests pass. Only `two_ctas` at billboard, half_page, instagram_post and instagram_story changed. Pairwise against the candidate: 4 wins, 0 losses, the same in both orders. On billboard the judge noted a smaller logo, 139 to 114 px wide.
- **Verdict:** keep.

### 2026-10-30: slimmer floored buttons on small near-square ads, on the v1.28 candidate
- **Branch:** `exp/fix-small-rect-cta-pad`, commits `3ed61ba` and `d0bedf1`.
- **Hypothesis:** at 300x250 and 336x280 a CTA label raised to the readable floor kept its padding in ems, so the button grew into a slab and took the height the fine print needed. On Level 3 at 336x280 the fine print landed under the logo.
- **Change:** `sizing.size_label_chip` also trims a floored CTA's padding by its growth on ads with a width over height of 0.75 to 2.2, at most 120,000 px, and at least 16 lines of copy tall at the readable floor. Without the height floor, `story1__button_120x90` changed too: a split won that wrapped the button onto three lines and covered the logo, and it lost.
- **Result:** structural 169/169, no fallbacks, and 673 tests pass. 11 tasks changed, all at 300x250 or 336x280. Pairwise against the candidate: dev 0 wins, 0 losses, 2 ties. Probe 3 wins, 0 losses, 3 ties. Formats 2 wins, 0 losses, 1 tie, judged against an uncontended rerun of the candidate. On Level 3 at 336x280 the badges come back ahead of the button and the fine print moves to the bottom, in source order, but the headline, body, badges and button each lose 1 or 2 px. The smallest size and the counts under 5 and 3 px are unchanged on every split.
- **Verdict:** keep.

### 2026-10-30: YouTube channel art laid out on its central safe area, rejected
- **Branch:** `exp/fix-youtube-safe-area`, commit `0983180`.
- **Hypothesis:** the absolute judge's top issue on all 4 YouTube banners, 2560x1440 in `formats` only, is copy, logo and button outside the central 1546x423 safe area. Laying out on that strip should lift overall from 6 to 7.
- **Change:** `pipeline.resize_with_report` lays out a canvas within 1% of 2560x1440 on the central strip with the full canvas's 16 px text floor, centres the result, and spreads the backdrops with `finalize.backdrop_box`. It reads geometry only.
- **Result:** structural 169/169, and only the 4 YouTube tasks changed. Pairwise against the candidate: 1 win, 1 loss, 2 ties. Absolute means on those 4 tasks fell from 6.5 to 5.0 overall, 6.5 to 4.75 intentional, 8 to 6 readability and 8.5 to 7 brand, and one task fell to 4. Type shrank to between a third and half of its size, and one Level 3 text came out at 12 px, under the floor. The judge named tiny type, empty bands above and below, and a 3-line headline.
- **Verdict:** drop. The judge names the safe area as the top issue on full-canvas layouts but scores the strip lower, so its top-issue comment is not a reliable fix list.

### 2026-10-30: v1.28 candidate rechecked with the API judge
- **Branch:** `exp/final` at `93a9cb3`: v1.27 plus the candidate's commits `df0632b`, `927aa4a` and `fd7f2b7`, and `5de6938`, which exempts only a full-bleed `cover` backdrop from the aspect check in `tools/`.
- **Result:** container runs `output/bench/v128-*`. Structural 169/169 with no fallbacks, and 673 tests pass. Pairwise against v1.27, API, both orders: dev 3 wins, 0 losses, 5 ties. Test 1 win, 0 losses, 1 tie. Probe 1 win, 0 losses, 2 ties. Formats 7 wins, 5 losses, 3 ties. Overall 12 wins, 5 losses and 11 ties on 28 changed tasks.
  - The losses are all in formats. `level2__half_banner` grew the headline and shrank the product half under the button. `level3__button_120x90` set the headline on the bottle. `story1__half_banner` crowded the logo with the can.
  - Two losses come from a run cut short, not from the engine. The candidate run hit the 20 s time budget on `level3__full_hd` and `level3__large_rectangle`. An uncontended rerun of `level3__full_hd` is byte-identical to v1.27.
  - Formats texts under 5 px fell from 44 to 40 and under 3 px from 11 to 10.
- **Verdict:** keep as the base for today's fixes.

### 2026-10-30: optional model harness, built and checked without weights
- **Branch:** built from `feat/engine-v1-dd4qmp` at `94a31d8` and applied on top of the v1.28 candidate. Off by default.
- **Hypothesis:** a small local vision model could break near-ties between finished layouts better than the critic alone. Before any model is tried, the harness must change nothing when no model is configured or the model is useless.
- **Change:** `docs/agentic-harness.md` describes it. In short:
  - `engine/core/pipeline.py` can finish a few runners-up exactly as it finishes the pick (`Outcome.finalists`); the pick is built first and unchanged.
  - New pure core modules: `advice.py` (enumerated questions and parsers), `validate.py` (the hard rules, ported from `tools/checks.structural`) and `agent.py` (eligibility, both-orders tie-break, flags, budget, breaker).
  - New adapters: the official renderer, image preprocessing, configuration from `CHAMELEON_AGENT*`, scripted, random and replay advisors, an OpenAI-compatible HTTP advisor (stdlib only) and an optional in-process llama.cpp advisor.
  - `engine/resize.py` imports none of it unless `CHAMELEON_AGENT` names a backend.
  - Tools: `tools.bench --agent`, `tools/agree.py`, `tools/agent_stub_server.py`, `tools/digests.py`, and a dev-only amd64 latency image in `tools/docker/`.
- **Result:** cloud runs, no model weights.
  - **E1, harness off:** `tools/digests.py` over `input/` and all four suites, 172 tasks, aggregate `2828a0b5` before (`94a31d8`, E0) and after. The E0 digests also match the `rcchip_*` bench outputs on all 169 suite tasks. `tools.bench` without `--agent` on dev, test, probe and formats changed no task against `rcchip_*`, with structural checks 39/39, 18/18, 60/60 and 52/52.
  - **E1b, useless advisors:** the `null` advisor gave the same aggregate on all 172 tasks. It fired on 63 tasks, kept the default on 60, and ran out of budget on 3. An HTTP advisor pointed at a closed port, a stub server that sleeps 30 s and a stub that answers garbage each opened the breaker on their first firing tasks.
    - The first runs of those three, made with load average about 10 on 4 CPUs, differed from E0 on one or two Level 3 formats tasks (`a4_landscape`, `large_rectangle`). Re-run alone under the same configuration, both tasks were byte-identical to E0.
    - Full re-runs on a quieter machine (load average 3 to 4) gave the E0 aggregate on all 172 tasks for all three.
    - The cause is the engine's 20 s candidate-loop budget (`_TIME_BUDGET_S`), not the harness: under enough load the loop stops trying candidates early, with or without a harness. Run identity checks on a quiet machine, and re-run any differing task alone.
  - **E2, random advisor:** `tools.bench --agent` with `CHAMELEON_AGENT=random`, `TOP_K=4`. Structural checks passed on 169 of 169 tasks. It fired on 65 tasks and switched on 11: dev 4 of 17, test 2 of 9, probe 3 of 22, formats 2 of 17. The tasks changed against `rcchip_*` were exactly the switched ones in every suite. Harness time per task averaged 0.27 to 0.64 s by suite, with a maximum of 4.1 s on formats. Not judged.
  - **Tests:** `pytest -q tests` gave 874 passed and 11 skipped in 395 s; the 12 new files alone give 208 passed and 1 skipped (the real llama.cpp smoke test). They cover the default path, every scripted action, garbage, slow, failing and raising advisors, the HTTP adapter against an in-test stub server, and the llama.cpp adapter with a fake module.
  - **Agreement tool:** `tools.agree build` on the judged rounds `pack`, `chipeq`, `rcchip` and `t_packtier` gave 7 decisive pairs (all D2), 5 all-tie controls (C1), 3 mixed pairs (C3, counted only) and 7 identical-pair controls (C2). It also gave 78 flag rows from `abs_rc_dev` and `abs_v126_dev`. Scripted and random runs scored end to end; the gates say `insufficient_data`, as they must with 7 decisive pairs.
    - `tools.agree near-ties` on dev, with `top_k` 4, laid out 27 near-tie pairs from 17 tasks in `output/blind/nt_dev`, ready for in-session judging.
    - **Replay plumbing (not a judgement):** synthetic verdicts that let every challenger win, on a copy of that round, replayed through `tools.bench --agent` (`CHAMELEON_AGENT=replay`). The engine switched on all 17 firing tasks, with 34 replay hits and 10 misses. So near-ties renders match the engine's own renders byte for byte, and E3 will work once the round has real verdicts.
    - **Flags reach:** with `top_k` 4 and epsilon 3, a flag had a coded gain on an eligible challenger in only 7 issue-and-task combinations on dev and 7 on probe.
- **Review fixes, same day:** a review of the harness found 24 issues; the real ones are fixed.
  - **Safety net:** the final validator now exempts only a `cover` image that bleeds off all four edges from the aspect rule, checks required fields, and refuses any change against finalist 0 beyond boxes and text sizes. Eligibility compares each unreadable-content penalty on its own rather than their sum. On all 172 tasks with `top_k` 4 and epsilon 3, the same 66 tasks have an eligible challenger before and after: no current finalist changes a field other than geometry, drifts under the stricter aspect rule, or trades one unreadable penalty for another.
  - **Failing models:** a call starts only with its whole timeout left, so running short of budget gives `budget` and no longer opens the breaker for the process. A call abandoned at its deadline is waited for before the next engine run, and after a failed warmup, so it cannot steal the engine's CPU; a test with a CPU-burning advisor checks it. Once the breaker is open no alternatives are finished. `engine.resize` refuses the in-process `llamacpp` backend.
  - **Parsers and adapters:** the parsers never raise; `Image 1` and `Image 2` mean A and B when the original is not shown; the HTTP advisor follows no redirect.
  - **Tools:** `tools.agree near-ties` clears only its own rounds. `run` writes each call as it goes and waits out an abandoned in-process call instead of counting `busy` errors. `score` and `mcnemar` refuse a missing dataset and take `--data`. `tools.digests` fails when a named harness never came up and keeps keys out of its file. `tools.bench --agent` reports replay hits and misses and exits 1 when nothing matched. The dev Docker image copies the versioned llama.cpp libraries too (the pinned tag sets SOVERSIONs; a minimal CMake reproduction runs with the new copy step and its build-time `ldd` check), but the image itself is still not built.
- **Not verified here:** any real model, the dev-only Docker image (not built), and latency on the scorer's hardware.
- **On the v1.28 release, in the scoring container:** with `CHAMELEON_AGENT` unset, the bench outputs on all four splits are byte-identical to the release run `final-*`, 169 of 169 tasks, and the full test suite passes: 938 tests pass and 11 skip. One harness test matched model clients as substrings and tripped on the `httpx2` pin in the new frozen `requirements.txt`, so it now compares whole package names. No real model had run the harness at release. The first run, logged above, kept it off. No model ships until the organizers publish the approved model list.
- **Verdict:** keep, off by default. Next: build the agreement dataset from judged rounds, then run stage 1 of `docs/agentic-harness.md` section 7 for each candidate model. Expect a no-go: zero-shot small judges score near chance in the literature, and the harness makes that outcome cost nothing.

### 2026-10-30: v1.28 candidate, on v1.27: equal-width pills, headline lead on small banners, tiered packed text
- **Branch:** `feat/engine-v1-dd4qmp`, with `exp/rc` (v1.27) merged in at `94a31d8`. Fixes were built by parallel agents in their own worktrees, each benched on all four splits, reviewed for hard-rule breaks by a separate skeptical agent, then judged.
- **Kept:**
  - **Equal-width pills** (`sizing._size_label_row`): pills the source drew at one width keep one width, the widest label's plus about 0.9 em of padding when the row has room. On v1.27: 4-0 and 2-1, dev 7 and test 2 changed. On v1.26 it had won 2-0.
  - **Headline lead on small banners** (`layout.scale_cap`, `sizing.size_label_chip`): banner canvases may scale until the headline reaches 1.4x the readable floor, and a banner CTA floored above the design's scale trims its padding instead of outgrowing the headline. 4-1 and 6-2 over 13 changed tasks (dev 4, test 1, probe 3, formats 5). Slowest task 6.0 s.
  - **Tiered packed text** (`packed.py`, `sizing.py`): on canvases of 120,000 px or less, the packed fallback steps text at most 2 sizes down to one whose word spaces render, gives the headline and CTA a lead tier at 1.6x or 2.5x the shared scale (the subhead the square root), and drops a boost that would wrap the headline onto more lines. 7-0 and 7-0, one tie each, on 8 formats tasks. Level 3 at 300x250 now draws 'SUNSET SALE' and 'Shop the Summer Line', not 'SUNSETS ALE' and 'Shop the SummerLine'.
    - The agent's version applied everywhere. Measuring word spaces at print sizes took `packed()` from 0.2 s to 69 s on an A4 canvas, so the change is gated to small ads, where it was judged; larger canvases keep v1.27's pack exactly (0.16-0.26 s on A4).
    - It also broke `tests/test_word_spaces.py`, whose stub context has no `space_drop`; the engine now reads it with `getattr`. A `line_height` of 0 no longer divides by zero.
- **Rejected:**
  - **Faithful candidate for nearby shapes:** scale the source uniformly when |ln(aspect ratio)| <= 0.35, put the slack into its own whitespace, and relax the critic's whitespace thresholds for it. 3-6 and 3-5 on 10 tasks. The judge preferred v1.27's recomposed, fuller layouts to the designer's layout scaled.
  - **Pill-rounding cap:** a source pill may not grow past 2x its unchanged corner radius on big canvases, so the renderer keeps it round. 4-7 and 2-8 on 15 tasks. The judge preferred larger, squarer chips to round, smaller ones.
  - **On v1.26, before the merge:** a shared-scale pack that won 5-0 and 5-0 is superseded by v1.27's `packed.py`; a banner logo share of 0.5 instead of 0.6 flipped `x_header_1` to banner columns.
- **Flagged by the reviews, not changed:** missing-asset images are hidden by writing `z_index` and `opacity` (v2 behaviour), and the CTA role reads the first word of the copy (`understand._starts_with_cta_verb`). Both predate this entry; the first depends on the open question about hiding.
- **Verdict:** keep all three as the v1.28 candidate. Final scores are in `docs/leaderboard.md`.

### 2026-10-29: v1.27 release candidate, v2 plus the word-pair cap and the packed fallback
- **Branch:** `exp/rc`, cut from `exp/engine-v2` at `f1eb224`.
- **Hypothesis:** the two fixes accepted today touch different tasks, so together they keep both gains over v2.
- **Change:** v1.27 is v1.26, plus the `exp/engine-v2` commits, plus the two entries below. The engine commits are cherry-picks of `exp/pairfit` `e461f23` and `exp/tiny-ads` `f422049` and `fa51aab`. The tests and log entries of both branches came along. The branch also holds the packaging script and the viewer add-on, which do not touch `engine/`.
- **Result:** container runs in `chameleonizer:v1.26` with this branch's engine mounted, saved as `output/bench/rc-*`.
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52. 666 tests pass and 10 skip. The worst task takes 4.10 s, on `formats`.
  - **Outputs:** every output that differs from v2 is byte-identical to the run of the single fix that changed it. Dev 1 and probe 3 match `exp/pairfit`, and formats 10 match `exp/tiny-ads`. Test is byte-identical to v2.
  - **Pairwise against v2:** `tools.judge --pair`, Opus 5.5, both orders. Dev 0 wins, 0 losses, 1 tie. Probe 1 win, 0 losses, 2 ties. Formats 10 wins, 0 losses. Test unchanged. Overall 11 wins, 0 losses and 3 ties on 14 changed tasks, the same verdicts the single fixes got.
  - **Pairwise against v1.26:** dev 0 wins, 0 losses, 2 ties. Probe 5 wins, 0 losses, 2 ties. Formats 10 wins, 0 losses. Test unchanged. Overall 15 wins, 0 losses and 4 ties on 19 changed tasks. `editorial_left__wide_skyscraper`, v2's one loss, is byte-identical to v1.26 again.
  - **Absolute, `tools.judge --run`:**

    | Split | Overall | Collisions | Readability | Brand | Intentional | Fails at 4 or below | v2 overall, fails |
    |---|---|---|---|---|---|---|---|
    | dev | 6.85 | 7.49 | 7.23 | 8.28 | 6.64 | 1 | 6.87, 2 |
    | test | 7.11 | 7.61 | 7.56 | 8.44 | 6.89 | 0 | 7.11, 0 |
    | probe | 6.48 | 7.30 | 6.57 | 7.83 | 6.40 | 1 | 6.58, 1 |
    | formats | 5.96 | 6.79 | 6.23 | 7.00 | 6.02 | 11 | 5.73, 12 |

    - The 10 changed formats tasks rose from a mean of 2.1 to 3.4. Story1 at 320x50 now scores 5. The other 9 still score 4 or below, mostly for tiny text.
    - The dev and probe differences are judge noise. The changed dev and probe tasks scored exactly as on v2. The probe drop of 0.10 comes from 12 of the 57 byte-identical tasks, and the dev fail that cleared is `official_level3_advanced`, which is byte-identical too.
  - **Rules check on the container outputs,** measured inside the image with the engine's own measurer: 169 tasks and 709 texts. No element box and no wrapped line goes past the canvas, every font size is an integer, and copy and font families are unchanged. The worst aspect drift of an image that is not a full-bleed backdrop is 0.024%. Opacity and `z_index` change only on the 19 missing-asset images, as in v2.
  - **Type sizes:** 78 texts under 7 px, against 67 for v2. 44 under 5 px, against 5, all at 120x90, 234x60, 300x50, 320x50 and 320x100. 11 under 3 px, against none, all on the official Level 3 sample at 120x90 and 234x60.
  - **Viewer add-on:** the previews were rebuilt with this engine in the scoring image. Only the official Level 3 sample at 300x250 changed, a size outside the benchmark. The packed fallback replaced v2's layout there, which set the disclaimer over the SOLSTICE wordmark and the ribbon over the button. In the packed layout the ribbon label draws as "SUNSETS ALE" and the button as "Shop the SummerLine". The likely cause is that the packed layouts skip the rule that keeps text at sizes whose word spaces render in the container.
- **Verdict:** keep as v1.27, the release candidate. Tag it `engine-v1.27` after review.

### 2026-10-29: packed fallback for small ads that pile copy on copy, on top of v2
- **Branch:** `exp/tiny-ads`, cut from `exp/engine-v2` at `f1eb224`.
- **Hypothesis:** the 12 formats fails at 4 or below are small ad sizes, mostly Level 3 with text on text. The critic already sees each collision, but no archetype can avoid it: measured on the Mac, Level 3 at 320x50 scored a collision penalty of 82 to 107 in every candidate, and lowering the readable floor to 3 px still left 41 or more. The strip columns stack what they can and park the rest over the copy. A layout that gives every unit its own slot and shrinks all copy at one scale removes the overlap without hiding anything. The floorplan engine won the small sizes 16-8 against v1.26 that way.
- **Change:**
  - New `engine/core/packed.py`. It builds a few column arrangements.
    - Banners and strips get a row: one column per picture, then the copy, then a column with the sticker, button and fine print. Variants put the fine print under the copy, split the copy into two columns after the headline, and give pictures 85% or 55% of the height.
    - Other canvases get a stack, and a split with the product beside the copy on its source side and the logo heading the copy.
    - Each arrangement is fitted at the largest shared scale, with font floors of 5, 4, 3 and 1 px. Every unit gets its own slot, a rotated sticker gets the box it draws, and nothing is hidden except the existing missing-asset handling.
  - `pipeline.resize_with_report`: after the usual choice and margin rebuild, a design whose critic collision penalty is still 10 or more is replaced by the best-scoring packed layout that has no collision, cut-off or overflow. A penalty of 10 means one piece over more than a quarter of a piece of copy, or several smaller overlaps. Below it, such as a ribbon grazing fine print at 728x90 or 300x250, the archetype stays. The packed layouts looked worse there by eye, so none of them was judged.
  - `layout.finish_candidate` shares the leftover, separator, container and backdrop steps with the archetypes. No output changes from this.
  - Tests: `tests/test_no_overlaps.py` now covers every size, not only 300x250 and up. New `tests/test_packed.py` checks every packed layout on every suite task under 336x280: no critic collision or cut-off, structural checks pass, and only missing assets lose their layer or opacity.
- **Result:** container runs, `chameleonizer:v1.26` with the engine mounted.
  - **Rules:** dev 39/39, test 18/18, probe 60/60, formats 52/52 structural. Slowest task 4.5 s.
  - **Outputs changed:** formats 10 of 52: Level 3 at 120x90, 234x60, 320x50, 300x50 and 320x100, story1 at 120x90, 234x60, 320x50 and 300x50, and Level 2 at 120x90. Dev, test and probe are byte-identical to v2.
  - **Pairwise against v2:** `tools.judge --pair`, Opus 5.5, both orders. Formats: new 10, v2 0, tie 42, and all 42 ties are identical outputs. Both orders agreed on every changed task. Every reason named v2's text-on-text or ribbon-on-button collisions, and most also called the new text tiny.
  - **Absolute, on the 10 changed tasks:** overall 2.1 to 3.5, collisions 2.1 to 4.7, readability 2.1 to 2.5, brand 3.2 to 4.2, intentional 2.2 to 4.2. Nine of the 10 still score 4 or below. Only story1 at 320x50 reaches 5. Formats fails at 4 or below drop from 12 to 11.
  - **Readability cost:** all 10 changed outputs set text under 8 px, as v2's did, and 55 text elements are under 8 px against v2's 48. Level 3 is the worst. At 120x90 every text is 1 to 3 px. At 234x60 it is 2 to 5 px, at 320x50 3 to 5 px, at 300x50 3 to 4 px and at 320x100 4 to 5 px. On story1 and Level 2 the headline stays at 9 to 14 px, and the subhead and button label run 3 to 5 px. Formats outputs with any text under 8 px: 16 before and 16 after.
- **Verdict:** keep. It is the first change that clears copy from copy on these sizes without hiding anything, and the judge preferred it on every task it touched. The judge still fails most of them because the copy is too small to read. Fixing that needs fewer pieces of copy on the canvas, which waits on the organizers' answer about hiding elements.

### 2026-10-29: word-pair cap measured at each size, on top of exp/engine-v2
- **Branch:** `exp/pairfit`, cut from `exp/engine-v2` at `f1eb224`.
- **Hypothesis:** v2's one judged loss against v1.26, `editorial_left__wide_skyscraper`, comes from `sizing._pair_fit` scaling the widest word pair linearly from the source size.
  - "golden hour." is 478 px wide at 80 px, so the linear cap was 23 px. In the scoring container it measures 142 px at 23 px against a 140 px column. The pair broke and the headline went to 3 lines.
  - Hinted Linux advances are whole pixels, so widths are not linear in the size. macOS widths are. Across 69 suite sources at 50 tall canvases from 100 to 320 px wide, the linear cap never overshoots on macOS, so neither the tests nor a Mac bench can catch this.
- **Change:** `_pair_fit` returns the largest integer size at which every neighbouring pair, measured at that size, fits between the side margins. The linear estimate only seeds the search.
  - In the container, the raw cap moved on 17 of the 41 multi-word texts on the 11 suite skyscrapers: 9 down by 1 or 2 px, 8 up by 1 px. Most never bind, because the v1.26 cap stays as the floor.
  - **Checked for the same assumption:** `_keep_one_line` seeds linearly but measures every trial size, so it cannot overflow. `size_label_chip`, `_roomy_button`, `size_rigid`, `layout._fine_print_band`, `layout._strip_chip`, `layout._cta_column` and `pipeline.contain_text` all measure at the integer size they emit.
- **Result:** container runs, judged pairwise against v2 with `tools/judge.py`, both image orders.
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52. 568 tests pass and 49 skip. The worst task takes 4.49 s.
  - **Outputs changed against v2:** dev 1, `wide_skyscraper_1`. Probe 3: `card_panel`, `editorial_left` and `logo_hero` on the wide skyscraper. Test and formats are byte-identical.
  - `editorial_left__wide_skyscraper` is byte-identical to v1.26 again: "Bottled at / golden hour." at 22 px, with the can shadow back under the can.
  - **Pairwise:** dev 0 wins, 0 losses, 1 tie. Probe 1 win, 0 losses, 2 ties. The editorial task won in both orders. `card_panel` split by order, the other two tied.
- **Not changed:** the renderer also counts the space after a pair against the line when more words follow. Measuring that too would lower 10 more caps on macOS, including the v2 win on `wide_skyscraper_1`, so it was left out.
- **Verdict:** keep. It removes v2's only loss against v1.26 and loses nothing against v2.

### 2026-10-29: the floorplan engine against v1.26
- **Branch:** a second engine on `feat/floorplan-engine` at `056e762`, a slicing-tree engine sized at one global scale. Its own log and `docs/floorplan.md` are on that branch.
- **Hypothesis:** its own judge rounds ran in session on macOS renders and reached 21-26 against v1.26 on `dev` and `test`. On container renders and all four splits, it beats v1.26.
- **Change:** none to this repo. A harness outside the repo called the floorplan `engine.resize.resize` unchanged inside `chameleonizer:v1.26`, rendered with the official renderer, checked `tools.checks.structural` and wrote runs in the `tools.bench` layout. Every source render was byte-identical to the `docker-v1.26f` runs, so both engines went through the same renderer.
- **Result:**
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52. No crash, no letterbox fallback, no source mutation. Like v1.26, it changes opacity and `z_index` only on the 19 images whose asset file is missing.
  - **Speed:** 2x to 9x slower than v1.26 in emulated amd64. Its worst task took 13.3 s of CPU against 1.49 s.
  - **Pairwise against v1.26:** `tools.judge --pair`, Opus 5.5, both orders, every task changed.
    - dev 17-16 with 6 ties, test 7-6 with 5 ties, probe 16-38 with 6 ties, formats 21-26 with 5 ties.
    - Overall 61 wins, 86 losses and 22 ties, sign test p=0.047. Dev and test, the splits it was tuned on, are level. Probe, p=0.004, is a clear loss.
    - It won no `card_panel`, `photo_band`, `two_products` or `wide_source` probe task.
  - **Where it won:** the tiny ad sizes, 16-8. On the dense Level 3 sample at 120x90, 234x60, 300x50, 320x50 and 320x100, v1.26 piles text on text and the judge called it a mess. Floorplan's text is tiny but clear of other text.
  - **Where it lost:** probe structures, where it breaks layering such as the can leaving its card. Simple strips, where it shrinks badges, disclaimers and logos until they are illegible. Tall and print sizes, where it leaves empty bands.
- **Verdict:** drop as the submission engine. Its one idea that won, fitting every unit at one shared scale when copy would pile on copy, became the packed fallback above.

### 2026-10-29: skyscraper headlines and parked fine print, on top of v1.26
- **Branch:** `exp/engine-v2`, cut from `feat/engine-v1` at v1.26.
- **Hypothesis:** two narrow fixes help without moving anything else.
  - On 160x600 the copy filled 99% of the width but only 68% of the height, because the headline cap is 14% of the short side, about 22 px.
  - Fine print parked in free space took an out-of-order spot, such as between a headline and its big number, whenever its largest size had no in-order spot.
- **Change:**
  - `sizing._font_cap`: on canvases taller than 2.5 times their width, headlines may reach 20% of the width and other copy 10%. Only 160x600 in the catalogue qualifies.
  - `layout._find_room`: fine print tries smaller readable sizes for an in-order spot before taking an out-of-order one. It never drops to the last-resort half size just to keep the order. With no in-order spot at a readable size, it parks exactly as v1.26 did.
- **Result:** no API key in this session, so no `tools/judge.py` run. Judged by eye in side-by-side sheets.
  - **Rules:** dev 39/39, test 18/18, probe 60/60, formats 52/52. 404 tests pass. Docker build and offline run pass on the official samples.
  - **Outputs changed:** only skyscraper tasks and `big_number__linkedin_banner`.
    - On skyscrapers, headlines and quotes are larger and fill the column.
    - On the LinkedIn banner, the fine print moved from between the headline and "40% OFF" to under the button.
  - **Fresh designs:** 108 new generated tasks, seeds from 910000, 6 per catalogue format, never tuned on. Only one output changed, `wide_skyscraper_f3`, with a larger headline and button in the same layout.
  - **Tall sizes outside the catalogue:** 30 new designs, seeds from 920000, at 120x600, 300x1050 and 160x600.
    - All 30 pass the structural checks, and 17 outputs changed. In each one the layout is the same with a larger headline: no new overlaps, nothing cut off.
    - **Cost: tighter side margins.** The text edge closest to the canvas, measured on drawn pixels, moved in on 13 of the 17.
      - At 160x600 it went from 17-25 px to 10-14 px.
      - At 300x1050 it went from 34-56 px to 18-27 px.
      - At 120x600 the tightest was 7 px, against 9 px for v1.26. 7 px is still the engine's own margin, 6% of the short side.
    - Worth a judge check: whether it prefers the larger type or the wider margins.
  - **Word-pair guard, added after a Linux check:** the change was rerun inside the scoring image with the repo mounted. The changes matched macOS in kind.
    - **Problem:** growth set some headlines one word to a line. `text_poster__wide_skyscraper` went from 3 to 5 lines, and three-word headlines at 120x600 went from 2 to 3.
    - **Fix:** the larger skyscraper cap now stops where the widest pair of neighbouring words, measured with the brand font, still fits between the side margins (`sizing._pair_fit`). The v1.26 cap stays as the floor.
    - **Result over all 357 tasks:**
      - Every task passes the structural checks, and 14 outputs differ from v1.26: 13 skyscraper-shaped and `big_number__linkedin_banner`.
      - No headline the change touched went to one word per line.
      - No critic penalty rose on any task, counting collision, cut-off, overflow, hidden content, small text and headline lines. The ribbon box that met the logo box on `portrait_ad_300x1050_8` no longer does.
      - Timing is unchanged, with a worst task of 1.35 s.
  - **Fuzz, and one hardening fix:** odd headlines on tall, square and strip canvases, both on v1.26 and on this branch: empty, spaces only, one giant word, emoji, newlines, 60 words, accents, an unknown font, and sizes of 0, 0.5 and 500.
    - Both versions fell back to the letterbox on all 35 `font_size: 0` cases. A division by zero, present in v1.26 too.
    - The schema's minimum is 1, so `pipeline._sanitized` now raises a text font size under 1, or one that is not a number, to 1 before layout, as it already did for geometry.
    - **After the fix:** no fallbacks, and every `font_size: 0` output passes the structural checks.
    - **Valid inputs are untouched:** all 277 suite and fresh outputs are byte-identical to before.
  - **Field fuzz, and a grouping fix:** one field of one element at a time on the Level 1 and Level 2 samples, across 7 targets from 50x50 to A4 (`fuzz2.py`, kept outside the repo).
    - **Not fixed:** most fallbacks come from values the schema forbids, such as text where a number belongs (rotation "abc"), numeric content or a missing `font_family`. They still return valid output through the letterbox.
    - **Fixed: a lone `group_id`.** One element given a `group_id` that no other element shares was read as a rigid group, and every such group became a button.
      - On Level 1, a lone grouped logo became the call to action and the real CTA was demoted to a subhead.
      - A lone grouped backdrop stopped being the background.
      - On 1600x400 and 728x90 the button sizing then failed, because the "button" has no label, and the engine fell back to the letterbox.
    - `understand` now reads a group of one as an ungrouped element. The output still carries the source `group_id`, because outputs are built from `scene.source`.
    - **Check:** every ungrouped element of the three official samples was given a lone group id, across 5 targets, for 90 cases. No crashes. Every layout is identical to the ungrouped design apart from the preserved `group_id`.
    - **Normal designs are untouched:** all 357 suite, fresh, tall and banner outputs are byte-identical, and the tests pass.
  - **Pair-group fuzz, and a crash guard:** every pair of ungrouped elements in each official sample was given a shared `group_id`, across 6 targets, for 288 cases (`fuzz3.py`, outside the repo).
    - **Failures:** two, both the Level 1 backdrop grouped with the logo, at 1600x400 and 728x90.
    - **Cause:** a group with no text reached the strip button sizing, which took `max()` over its label fonts and found none.
    - **Fix:** the label-based rescale in `_cta_column` now runs only when the group has a label.
    - **After the fix:** no failures, all 357 normal outputs byte-identical, and the tests pass.
  - **Element-count fuzz, no changes needed:** the three official samples across 7 targets, from 320x50 to 1080x1920 (`fuzz4.py` and `fuzz5.py`, outside the repo).
    - **Fewer elements:** every single element dropped, plus 25 random subsets per sample, for 749 cases.
    - **More elements:** each non-background element duplicated under a new id, 40 px lower, with renamed groups, plus 20 random sets of 2-6 duplicates per sample, for 623 cases. Duplicating half a button gives a lone group, which also exercises the group-of-one fix.
    - **Result:** no exceptions and no rule violations in either set.
    - **Timing at 65 elements**, Level 3 tripled:
      - 0.3 s at 1080x1920, 1.8 s at 320x50, 2.7 s on A4, 5.5 s at 160x600 and 6.3 s at 728x90.
      - 22 s at 300x250, where the 20 s candidate budget in `pipeline` stops further candidates.
      - No size failed. The rules set no time limit, so this was left alone.
  - **Source-scale fuzz, no changes needed:** each official sample scaled as a whole, canvas, geometry and font sizes, by 0.25, 0.5, 2 and 3, across 6 targets, for 72 cases.
    - **Result:** no exceptions and no rule violations.
    - **Same layout at 0.5x, 2x and 3x:** median drift 0. The largest was 12% of the canvas, Level 3 at 3x on 300x250, with the same strategy and the same critic score as at 1x. Font sizes round to whole pixels at each scale.
    - **0.25x drifts more:** a 270 px source with 3-15 px type, an unlikely input.
  - **Schema-valid fuzz, and a fix for missing font fields:** the schema requires only `id`, `type`, `x`, `y`, `width` and `height`.
    - **The cases:** every optional field dropped from every element, plus every enum value and valid extras, on all three official samples across 5 targets, for 3,485 cases (`fuzz6.py`, outside the repo).
      - Extras: line shapes, ellipses, italic, right alignment, `max_lines`, `style_runs`, null groups, strokes, rotations and opacity 0.
    - **Failures:** a text element without `font_family` or without `font_size` raised a KeyError and fell back to the letterbox, 60 of 1,560 on Level 1 and Level 2. Nothing else failed.
    - **Fix:** `pipeline._sanitized` gives such text the converter's own defaults, Montserrat at 24 px, so the engine measures what the renderer draws. Montserrat is not in the brand kit, so both fall back to Solstice Sans.
    - **Output keeps the designer's font:** `_without_added_fonts` removes the family again from any element whose source had none, so the renderer resolves it exactly as before. The engine never names a font the designer did not.
    - **Result:** no exceptions and no rule violations in any of the 3,485 cases. A render of Level 1 with the headline's family and size removed draws cleanly. All 357 normal outputs are byte-identical, and the tests pass.
  - **Regression tests:** `tests/test_schema_variants.py`, 33 cases.
    - **Covered:** missing font fields, font size under 1, groups of one, a group without a label on strips, and fine print after the copy it follows on `big_number__linkedin_banner`.
    - The tests call `resize_with_report`, so a raise fails the test instead of hiding behind the letterbox.
    - Against v1.26, 32 of the 33 fail. The one that passes is font size 0.5, which v1.26 already handled.
    - A group of one on the headline or CTA also changed the v1.26 layout. The group-of-one fix covers that too.
  - **Linux:** the schema fuzz (3,485 cases) and the pair-group fuzz (288 cases) also run inside the scoring image, with no exceptions and no rule violations.
  - **Placement and id fuzz, no changes needed:**
    - **Off-canvas sources:** every element of each official sample moved half off the left or bottom edge, wholly past the right or top edge, or five canvases away, across 5 targets, for 800 cases. No exceptions and no rule violations.
    - **Unusual ids:** unicode, spaces, 500-character ids, plain numbers, ids shaped like internal unit keys (`g:0`, `auto:bg`, `row:g:x`, `lines:1`, `set:2`) and one empty id, across 5 targets, for 90 cases. No exceptions, and every layout identical to the one with the original ids.
  - **Rubric checks on drawn pixels:** across all 357 tasks, in this branch and in v1.26.
    - **No unwanted collisions:** at 300x250 and up, no text overlaps another text, and no text covers a picture.
      - Text is measured with the renderer's own wrap and alignment.
      - A picture is its asset's visible alpha box placed as `contain` draws it, so transparent margins never count.
      - Rotated stickers and full-canvas backdrops are left out.
      - Zero overlaps in both versions.
      - The first version of the check missed a headline planted on the product, because it skipped every image wider than 95% of the canvas, which includes the bottle's wide transparent box. It now leaves out a backdrop by box area, and it catches both planted cases.
    - **Readable and on brand:** no task's worst text contrast fell below v1.26's, and no logo's visible height shrank.
    - **New test:** `tests/test_no_overlaps.py` runs the overlap check on `dev`, `test`, `probe` and `formats`, 131 tasks with 39 strips skipped. It includes a self-test that the check finds planted overlaps, and it passes on v1.26 and on this branch. The suite is now 568 passed.
  - **Banner sizes for the fine-print change:** 50 new designs, seeds from 930000, 10 each at LinkedIn Banner, X Header, Billboard, Wide Banner and Facebook Cover.
    - All 50 pass the structural checks.
    - **First version:** one output changed. In `facebook_cover_b9` the fine print moved from above the headline to under the button, but only by dropping from 10 px to the last-resort 7 px, under the readable floor of 10.
    - **Tightened:** the in-order search now stops at the readable sizes, so `facebook_cover_b9` matches v1.26 again.
      - `big_number__linkedin_banner` keeps its fix at 11 px, down from 12.
      - Across all 357 tasks above, only the skyscraper-shaped outputs and that banner differ from v1.26.
  - **Penalty audit:** the critic's collision, cut-off, overflow and hidden-content penalties were compared task by task against v1.26 across all 357 tasks.
    - The only increase is a collision on `portrait_ad_300x1050_8`. The stack rose 4 px and the logo's transparent box now meets the ribbon's box.
    - Zoomed renders show the drawn pixels still clear, so it reads the same as v1.26.
- **Not changed:**
  - **Skyscraper logos:** the logo box already spans the full 160 px, and the asset's transparent margins make it look small. A larger box would leave the canvas.
  - **Level 3 at 336x280:** the fine print still sits above the headline, because no size has an in-order free spot. Fixing it means reserving room in the split.
  - **Stickers grazing fine print** on `pinterest_pin_f0`: the no-touch rule already lost 1-4 in v1.26.
- **Judged on 2026-10-29:** `chameleonizer:v1.26` with this branch's engine mounted, at `f1eb224`.
  - **Rules:** structural dev 39/39, test 18/18, probe 60/60, formats 52/52. 568 tests pass and 49 skip. The worst task takes 7.25 s.
  - **Outputs changed against v1.26:** dev 2, both wide skyscrapers. Probe 8: seven wide skyscrapers and `big_number__linkedin_banner`. Test and formats are byte-identical.
  - **Pairwise against v1.26:** `tools.judge --pair`, both orders. Dev 1 win, 0 losses, 1 tie. Probe 4 wins, 1 loss, 3 ties. `big_number__linkedin_banner` won, with the fine print out from between the headline and "40% OFF".
  - **The one loss:** `editorial_left__wide_skyscraper`. In the container the headline broke "golden hour." and went to 3 lines, because `_pair_fit` scaled the pair's width linearly. The word-pair entry above fixes it.
  - **Absolute:** overall dev 6.87, test 7.11, probe 6.58, formats 5.73. Fails at 4 or below: dev 2, test 0, probe 1, formats 12, all 12 at 120x90, 234x60, 300x50, 320x50 or 320x100 and byte-identical to v1.26.
- **Verdict:** keep. It wins 5-1 against v1.26 on the tasks it changes, and the word-pair fix removes the loss.

### 2026-10-18: v1.26, fixes from the v1.25 rules audit
- **Branch:** `feat/engine-v1`.
- **Audit:** four independent auditors plus a skeptic per flagged finding, in the same way as the v1.16 audit. It found no rule break in the engine.
  - About 1,125 runs on macOS and inside the scoring container, at the suite sizes and at extremes from 50x50 to 4000x4000.
  - Every id once, every box inside the canvas, nothing under 1 px, images within 0.25% of source aspect, copy and fonts unchanged, integer font sizes, no crash.
  - The id renames gave identical layouts, the organizer files are untouched, and a fresh Docker build runs offline.
  - **The one gap:** the viewer add-on FAQ Q5 lists is still missing.
- **Fixed:**
  - **Text past the canvas.** The final clamp shortened a text box that overflowed, but the renderer still draws every wrapped line, so the official Level 3 headline ran up to 84 px off small ad sizes.
    - `pipeline.contain_text` measures each text with the renderer's own wrap on every candidate. Grouped text, such as a button label, shrinks in place. Loose text shrinks to 60% and only then lifts.
    - A first version lifted before shrinking, and moved a label under a sticker.
    - Formats probe 5-0 against v1.25, dev tied, test and probe unchanged.
  - **Robustness, no output changes:**
    - Only a `cover` backdrop takes the canvas shape, as the starter code allows; `contain` and unset keep theirs.
    - The emergency layout keeps image shapes and never raises on malformed targets.
    - Internal unit keys never collide with each other.
    - An element with an empty id can still be the logo.
  - **Build:** Pillow and skia-python are pinned to the versions the scoring image resolves.
- **Tried and reverted:**
  - **No-touch sticker rule.** Requiring a corner sticker to touch no copy at all made it park in free space on small story formats, over the headline or button, and it lost 1-4.
  - **Nudging parked stickers onto the canvas.** It lifted a badge onto a button.
  - The ribbon on the official Level 3 at 728x90 still grazes the end of the disclaimer. The judge tied that task either way.
- **Formats probe:** 20 small ad sizes added (300x50, 320x100, 234x60, 120x90 and 320x50), for 52 tasks. `tests/test_text_on_canvas.py` checks every format and sample.
- **Verdict:** keep v1.26.

### 2026-10-18: v1.24 and v1.25, margins and dense small rectangles
- **Branch:** `feat/engine-v1`. The work was an agent in a worktree, judged by the API on container runs.
- **Root causes found:**
  - The gap in `_columns` added the spare width on top of the minimum gaps, so every banner row and strip ran about one gap past both margins. A CTA sat 18 px from the edge on `facebook_cover_0` and 3 px on the mobile banners.
  - Pictures sized larger than their box could fit shrank about their centre but kept their column, leaving lopsided rows and splits.
- **Change:** layouts keep their v1.22 sizes but are placed so the left and right margins match, measured to drawn pixels: visible image pixels, copy by its ink, unfilled shapes ignored.
  - Spare width goes to the gaps, and pictures shrink before gaps get tight. Type never shrinks.
  - Candidates are scored on the old geometry and the winner is rebuilt with the new one, behind `Ctx.balance`. Scoring with the new geometry had flipped four banners to the columns layout the judge dislikes.
- **Result:**
  - **Alone, against v1.22:** dev 10-5, test 4-2, probe 14-3.
  - **Merged into v1.23, against v1.23:** dev 10-0, test 4-1, probe 10-7, formats 4-3.
- **Dense designs on small rectangles, first version rejected:** dev 1-2, probe 2-3, formats 1-0.
  - The dense-copy fallback fixed Level 3 at 336x280; the critic went from 21 to 67.
  - Its stacked-button area cap trimmed buttons on ordinary designs, and those lost.
- **Dense designs on small rectangles, revised, accepted as v1.25:** the button cap is gone, and only layouts whose plain fit drops copy change.
  - A split or stack lowers its readable floor a pixel at a time down to 7 px and lets the product shrink. Badge rows and fine print may run as a full-width footer.
  - A variant is kept only while the headline stays 1.2 times any other copy.
  - **Judged on v1.24:** formats `level3__large_rectangle` 1-0, with the critic going from 21 to 64. Probe `editorial_left__medium_rectangle` tied. Dev and test are unchanged.
- **Verdict:** keep v1.25.

### 2026-10-18: v1.22.1 to v1.23, print sizes, buttons and decorations
- **Branch:** `feat/engine-v1`. Agents worked in parallel worktrees, and the API judge scored each on container runs against the current best.
- **New split, `formats`:** the brief names "a printed poster" among the target shapes, and the catalogue had no print sizes. The split has 32 tasks covering A4, US Letter, an 18x24 poster, Full HD, a YouTube banner, 336x280 and 970x90.
- **Result:** pairwise on changed tasks, new-base.
  - **Big canvases, accepted as v1.22.1:** formats 9-2; dev, test and probe unchanged.
    - Text growth was capped at 2.2x its source size, so on an A4 sheet the headline ended up smaller than its own logo and product.
    - The cap now applies on top of the scale at which the whole design fits.
  - **Banner CTA cap that takes effect, accepted:** dev 1-0, probe 2-1. Since v1.10 the cap had rescaled from a width-limited size, so it never bit.
  - **Button padding and wrap, accepted on tall canvases only:** dev 1-0, probe 2-0, test and formats unchanged.
    - The first version applied everywhere. It lost the three 336x280 tasks 0-3 and flipped two medium rectangles.
    - Gated to canvases narrower than 0.75 of their height, a long label wraps into a two-line pill with real padding.
  - **Corner radii grow with chip height, held:** dev 3-0, test 2-0, probe 8-0, and it changes only `corner_radius`. Whether that field may change is an open organizer question. The commit is `7589c8c` on local `exp/buttons`, also `f1fd7f5` on `exp/buttons-v1`.
  - **Decorations by a weighted cost, accepted as v1.23:** dev 18-5, test 5-1, probe 43-2.
    - The code is `engine/core/decor.py`. Each decoration picks a position and scale from candidate spots: the source-relative spot, edge positions and depths, at 1.0 to 0.6 scale.
    - The cost covers covering copy, buttons and the logo, overlap the source did not have, edges the source kept clear, distance and direction from the source spot, shrink, and halos that keep framing their product.
    - It mirrors when the layout mirrors. The full weights are in the module.
    - Two earlier rewrites were coin flips on macOS renders. Judged on container renders, this one is the largest single gain since v1.15.
- **Interactions after merging:** dev 1-1, probe 4-1, and formats 14-1, where the decoration rule had not been judged before.
- **Still running at the time of writing:** `exp/margins`, outer margins and balance, and `exp/dense-small`, dense designs on small rectangles.
- **Verdict:** keep v1.23.

### 2026-10-17: v1.21 and v1.22, the last strip and badge fixes
- **Branch:** `feat/engine-v1`. Two agents worked in parallel worktrees, and the API judge scored each on container runs against v1.20.
- **Result:** pairwise on changed tasks, new-base.
  - **Readable strip candidates, accepted as v1.21:** test 1-0, probe 1-0 with 4 ties; dev unchanged.
    - On 320x50 and 728x90 strips, two extra candidates, with and without the product in the row, keep copy at 7 px or more and fine print at 6 px. They give up unless the headline stays at least 10 px, on two lines at most, and 1.4 times any other copy.
    - The critic chooses among them and the existing layouts. `mobile_banner_0` and `mobile_banner_1` still have no readable layout without hiding.
  - **Badge rows with the copy, accepted as v1.22:** dev 1-0 on the official Level 3 sample, test 1-0.
    - A badge row the layout would drop stacks with the copy first, on one row and smaller than the subhead, before it is ever parked in free space.
    - The judge called the new Level 3 "the cleaner, more intentional layout".
- **v1.22 absolute, API judge on container renders:** dev 6.77, test 6.94, probe 6.33, with fails at 4 or below at 2, 0 and 2.
  - That is level with v1.19.1. Absolute scores move about plus or minus 0.2 between identical runs, so they no longer separate versions; the pairwise rounds still do.
- **Docker:** the image builds from v1.22 and runs the three official samples offline in 0.2 s, 0.2 s and 1.9 s.
- **Verdict:** keep v1.22 as the submission candidate.

### 2026-10-17: v1.19 to v1.20, and a second judge on container renders
- **Branch:** `feat/engine-v1`. Agents worked in parallel worktrees, and the API judge scored every candidate on container runs against the current best.
- **Result:** pairwise on changed tasks, new-base.
  - **Text-link CTAs, accepted:** dev 1-0, no other change.
    - In banner columns, a CTA with no button shape stacks under the copy at the source ratio instead of taking a button-sized column. This fixes `facebook_cover_1`, a fail since v1.12.
    - A `logo_inflated` critic term, and dropping columns when only the product and copy remain, keep the critic from flipping split layouts to columns.
  - **Line runs, accepted:** probe 6-2; dev and test unchanged. Consecutive loose lines of one style that the source stacks tightly become one `lines` unit, such as an event's time and place, or feature lines with their bullets. On strips with no height, they sit side by side on one baseline.
  - **Picture room, accepted:** dev 1-0, test 2-0, probe 1-1. Two pictures stacked directly keep 1.5 gaps apart, the room coming from the hero.
  - **Picture slots at drawn pixels, rejected:** the first version went dev 3-0 and probe 7-7. The revision went dev 1-0, test 0-1 and probe 10-7.
  - **Sticker label guard, accepted as robustness:** every verdict a tie, with two tasks moving a few pixels. It keeps a rotated label on the canvas at steeper angles than the suite has.
  - **Readable strip candidate, rejected:** alone it won test 1-0 and probe 3-1, but merged into v1.19 it went probe 1-2. Its text-wrap cache was kept as v1.19.1: outputs are byte-identical, and the worst container task dropped from 7.8 s to 1.3 s.
  - **Whole ribbons on the canvas, accepted as v1.20:** dev 2-0, test 4-0, probe 2-1.
    - A corner sticker moves inward until its rotated ribbon stays inside, not just its label.
    - The same idea lost 3-6 on macOS renders. In the container, the judge reads a cut ribbon as clipped.
- **Absolute, API judge on container renders:**
  - **v1.19.1:** dev 6.77, test 7.06, probe 6.35, with fails at 4 or below at 2, 0 and 1.
  - **v1.18:** dev 6.72, test 6.94, probe 6.30.
- **Second judge:** Claude Sonnet, on container renders of both versions, preferred v1.20 over v1.2 28-4 on dev, 12-1 on test and 51-2 on probe.
- **Verdict:** keep v1.20. The remaining fails are the most crowded 320x50 and 728x90 strips and the official Level 3 badge row.

### 2026-10-17: v1.17 and v1.18, judged by the API on container runs
- **Branch:** `feat/engine-v1`. Five agents worked in parallel, each in its own worktree and benched inside the scoring container. The API judge compared every candidate with the container baseline `output/bench/docker-v1.16.1-*`.
- **Result:** pairwise on changed tasks, both image orders, new-base.
  - **Tall rhythm, accepted:** dev 4-1, test 0-0 (1 tie), probe 6-1.
    - A logo pinned to the top of a tall canvas sits at most two stack gaps above its stack; otherwise the two centre together.
    - A roomy tall stack grows up to 1.5 times past the scale cap while its headline gains no line.
  - **Mark clearance, accepted after one revision:** probe 3-0; dev and test unchanged.
    - A mark keeps max(3 px, 0.15 em) from its text's glyphs, descenders included, measured through a new `TextMeasurer.ink`.
    - A separator between two texts centres between their glyphs in tight stacks.
    - The first version also moved roomy marks by a few pixels and went 3-2.
  - **Alignment, accepted after one revision:** probe 9-2; dev and test unchanged.
    - Only free text votes on whether a design is centred.
    - Left-aligned stacks share one drawn left edge, with images measured by their visible pixels and an unfilled button by its first glyph.
    - The first version also inset all text near the canvas edge and lost 0-3 on dev.
  - **Text floors, accepted:** dev 1-0, test 0-0, probe 5-2, and 5-2 again on probe once merged into v1.17.
    - Text sits only at sizes where every word space leaves at least 2 px of clear ink, and 1 px more than the gaps inside the neighbouring words. A new `TextMeasurer.word_gaps` reads this off the glyphs drawn as the renderer draws them.
    - Linux hinting collapses "AWARD WINNING" at 7, 9 and 11 px, but not at 8, 10 or 12, so no single size floor works.
    - It costs about 0.35 s per task in the container, with a worst case of 7 s against a 20 s budget.
  - **Strip fine print on two lines, rejected:** dev 0-1, probe 2-1. Shrinking `leaderboard_1`'s disclaimer from 10 px to 9 px to fit two lines lost.
- **Combined:**
  - **v1.17:** tall rhythm, mark clearance and alignment together. Probe 15-2 against v1.16.1.
  - **v1.18:** v1.17 plus the text floors.
- **v1.18 absolute, API judge on container renders:** dev 6.72, test 6.94, probe 6.30, with fails at 4 or below at 2, 0 and 2.
- **Verdict:** keep v1.18. Both new measurer methods, `ink` and `word_gaps`, must be implemented by the Express add-on's measurer, or approximated from font metrics.

### 2026-10-17: rules audit, v1.16.1 hardening, and the Docker metrics gap
- **Branch:** `feat/engine-v1`.
- **Audit:** five independent auditors, one per rule family, then a skeptic per flagged finding. They covered the rulebook, every output under a fresh checker, asset pixels and the renderer, id blindness by renaming every id, and runtime.
  - **Rule breaks:** none in the engine.
  - **Confirmed facts:**
    - every id is returned exactly once and every box sits inside the canvas;
    - text content and fonts are unchanged and font sizes are integers;
    - images stay within 2% of their aspect;
    - no pixels are written, there is no model or network, and the organizer files are unchanged.
  - **Submission gap:** FAQ Q5 asks for a viewer add-on in stage one, "required (but not scored)"; none exists yet.
- **API judge now available again**, so the session-judged versions were checked against v1.14. v1.16 wins 11-1 on dev, 10-0 on test and 46-5 on probe.
- **v1.16.1 hardening:** no dev or test output changes; the two letter-spaced probe outputs change.
  - Unit keys can no longer collide with element ids. An id shaped like "g:3" had merged a headline with a button group.
  - No image collapses below 1 px on tiny formats such as 88x31.
  - A `fill` backdrop is never stretched, and foreground images keep their aspect even with `cover`.
  - Letter-spaced text keeps the renderer's wrap width while its slot covers the spaced lines.
  - Malformed geometry is cleaned, candidates stop after a time budget, and the letterbox falls back to an emergency layout.
  - `tests/test_robustness.py` reproduces each finding and fails on v1.16.
- **Docker metrics gap:** the scorer renders in the linux/amd64 container, where skia hints text to whole-pixel advances.
  - Bold Sans at 5 to 7 px measures about 10% wider than on macOS, and its space is only 1 px, so small labels read "AWARDWINNING".
  - Layouts therefore differ in the container. The API judge preferred the macOS renders 10-5 on dev, 4-3 on test and 11-9 on probe.
  - From here on, judge candidates on container runs: `docker run --rm --network none --entrypoint python -v "$PWD/engine":/app/engine -v "$PWD/tools":/app/tools -v "$PWD/bench":/app/bench -v "$PWD/output":/app/output <image> -m tools.bench --suite bench/suite/<split> --out output/bench/<name>`.
- **Verdict:** keep v1.16.1, tagged. The container baselines are `output/bench/docker-v1.16.1-*`.

### 2026-10-16: v1.16, generalisation fixes from the probe split
- **Branch:** `feat/engine-v1`. Four fixes came from a parallel workflow, one agent per failure mode, each on its own `exp/` branch.
- **Why:** the new `probe` split showed v1.15 at 5.6 overall in the session judge, against 6.7 on `dev`, with 8 of 60 tasks at 4 or below. Every failure was a structure the generated designs never contain.
- **Result:** blind in-session pairwise against v1.15, both image orders, changed tasks only. None of the fixes changes a `test` output.
  - **Type cap, accepted:** probe 3-0, 3 ties. Text may keep its source size times the scale at which the whole source fits. The fixed cap of 14% of the short side had shrunk a 210 px "40% OFF" below its own subhead's growth.
  - **Containers, accepted:** probe 10-0. A rectangle, not the background or a button, that holds two or more content pieces becomes a container. After layout it is fitted behind the units it held, keeps reaching the canvas edges it touched, and never grows over other content. Before, card panels and photo bands drifted as decoration: empty blobs, bands cutting across the product.
  - **Satellite marks, accepted:** probe 22-3. A small solid shape within about a line height beside, above or below exactly one text joins that text's unit and keeps its scaled offset, as with bullet dots, accent rules and dividers. All three losses are strips where a mark tucked into the stack gap touches a descender.
  - **Reading order, accepted:** probe 13-0, 3 ties, and dev 0-0, 1 tie. Units are ordered by a recursive XY-cut on visible boxes, so a column is read to its end before the next. Small text above the CTA is never legal. Strip leftovers join their column after the unit they follow.
  - **Image sets, accepted:** probe 4-0, 1 tie. Pictures of similar area side by side, or stacked, form one unit that scales as a single picture, so a matched pair stays a pair.
- **Combined, v1.16:** the 13 probe tasks where the fixes interact went 10-1 with 2 ties against v1.15. The loss is the tucked divider again.
  - **Probe absolute, session judge:** 6.28 overall, up from 5.63, with fails at 4 or below down from 8 to 4. Card panels went from 4.8 to 7.0 and photo bands from 4.8 to 6.2.
  - **Weakest left:** `editorial_left` and `event_story`. Their kicker can outweigh a small headline, a product's floor shadow detaches, and strips run short of room.
- **Follow-ups:**
  - A tucked mark should never overlap glyphs; it could reserve height when the gap is too small.
  - A container's corner radius is unchanged, so on a 90 px strip the renderer turns a card into a pill.
  - The critic does not see containers.

### 2026-10-16: v1.15 from parallel candidates, judged blind in session
- **Branch:** `feat/engine-v1`. Five candidates came from a parallel experiment workflow, one agent per idea, each rebased onto v1.14.
- **How it was judged:** Claude agents inside the session judged blind.
  - Each agent saw `original.png`, `A.png` and `B.png` with the API judge's rubric. Each task was asked in both image orders, and the two orders went to different agents.
  - A verdict that flips with the order counts as a tie, as in `tools/judge.py`.
  - **Calibration:** on 31 past tasks where the API judge was decisive, the session judges agreed 24 times, tied 7 times and never contradicted it. They are more conservative, but not misleading.
- **Result:** pairwise against v1.14 on changed tasks only, dev then test, new-base.
  - **Strips, accepted:** 5-0 and 2-0. Leftover copy stacks above or below the strip's copy or button column instead of parking in free space. Buttons pad their labels tighter, edge images keep their transparent margin on the canvas, stickers may shrink to clear the copy, and the copy gives up height before anything is stranded.
  - **Typography, accepted:** 6-1 and 2-0. Wraps take the most even break with the same line count, scored on ragged lines and breaks inside a phrase, and a one-line source headline shrinks up to 12% to stay one line.
  - **Proportions, accepted:** 1-0 and 3-0. A corner sticker follows the headline's growth, up to 1.5 times its area-preserving scale, where the rotated ribbon clears all content.
  - **Decor a2, rejected:** 10-11 and 4-1. It rewrites decoration placement with product-anchored halos and a free grid search. Wins kept a halo behind the product and spread circles apart; losses dropped circles into the gaps between logo, product and copy.
  - **Decor b, rejected:** 4-4, with 14 ties, and 2-1 on test, against the combined engine. It kept v1.14's candidate spots (source spot, edges, corners) and added halos, a stacking penalty and padding around copy. Decoration placement is close to a coin flip for this judge.
  - **Rhythm, rejected:** 0-1, with 4 ties. Equal ink gaps between stacked units.
  - **Image column at drawn width, rejected:** 2-4. Banner product images shrink to stay on the canvas because of transparent margins, leaving their column about 50 px too wide. Sizing the column to the drawn image gave the copy more room, and the critic then preferred banner columns, with their oversized CTA, over the split layouts the judge prefers.
- **Combined, v1.15:** 9-0 with 4 ties on dev, 6-0 with 5 ties on test, against v1.14.
  - Five combined outputs differed from every single-candidate output and were judged again.
  - The one loss, `official_level3_advanced`, put the ribbon across the wordmark: the corner check measured the unrotated box, which only grazed the logo slot. Measuring the drawn, rotated ribbon, and stacking the disclaimer last at no more than the smallest copy size, turned it into a tie.
- **Rejected by inspection:** capping text-only CTAs in banner columns, two variants.
  - The cap fixes `facebook_cover_1`, where "Discover the Collection" wraps into a three-line block bigger than the headline.
  - It also removes the hierarchy penalty from the columns candidate, which then beats the split layout by 0.09 critic points on `wide_banner_0` and `linkedin_banner_2`. This is the same failure the earlier text-CTA caps hit.
  - A height-only variant left the link as big as the headline.
- **Verdict:** keep v1.15. Decoration placement and banner columns against split are both judge coin flips today, so the next gains are elsewhere.

### 2026-10-16: critic recalibrated against the candidate audit
- **Branch:** `exp/critic`.
- **Hypothesis:** the critic's choice between candidates is often arbitrary. In 9 of the 17 dev tasks with a split, `split-left` and `split-right` scored exactly the same, so candidate order picked the left one. The audit reasons in `output/bench/cand-v113-dev/audit.json` name what the judge used to break those ties.
- **Change:** four critic terms in `engine/core/critic.py`. None of them moves an element.
  - **Busy backdrop:** the mean share of each copy box, logo included, that sits on fine detail in the background image, such as baked-in dots. Weight 24, capped at 2 points, so it settles close calls and never overrides structure. A new `AssetProbe.busyness` port supplies the detail mask, so the core still does no file access.
  - **Hero clutter:** the share of the product covered by decorative shapes, weight 1. The judge called a can over the bubbles "cluttered".
  - **Flipped sides:** 1 point when a split puts the product on the opposite side of the copy from the source, if the source had a clear side.
  - **Headline rewrap:** 0.5 per line beyond the source's line count plus 2. A narrow square split had doubled a 4-line headline to 8 lines.
  - Weights were chosen against the 39 decisive audit pairs. Agreement with the judge rose from 31 to 37. The working band is narrow, about 18 to 26 for the dots weight.
- **Result:** pairwise against v1.13.1.
  - **Dev:** 6-0, 33 ties. Seven tasks changed: six mirror or stack-to-split flips and `official_level2_core`, where columns became `split-right`.
  - **Test:** 1-0, 17 ties. Three tasks changed, all mirror flips.
  - Runtime is unchanged.
  - Judge reasons for the wins name dots touching "Only", "Line" or the logo in the old pick.
- **Verdict:** keep. The audit's two remaining disagreements:
  - `mobile_banner_1`: a strip where both candidates park leftovers over content.
  - `linkedin_post_1`: decided by 0.1 point.
  - Neither is a critic problem. Strips need layout work.

### 2026-10-16: robustness, a second judge, and the plateau
- **Branch:** `feat/engine-v1`.
- **Change and result:**
  - **v1.13.1, a robustness fix:** `tests/test_stress.py` runs 14 odd designs on 6 extreme canvases, from 50x50 to 4000x4000 plus strips, for 98 cases.
    - The only failure: image-only designs fell back to the letterbox on banners and strips, because the row layout required a text column. Fixed.
    - Suite outputs are unchanged, and 149 tests pass.
  - **Second-judge check:** Claude Sonnet 5.5, never used for tuning, compared v1.13.1 with v1.2 and preferred v1.13.1 24-3 on dev and 11-1 on test. The tuning judge, Opus 5.5, gave 25-7 and 13-1. The gains are not one model's taste.
  - **Layout selection audit:** `tools/candidates.py` judged the critic's pick against every alternative candidate. The judge sided with the critic in 31 of 39 decisive pairs. The 8 disagreements were near-ties in the critic.
  - **Rejected:**
    - A split-layout bonus lost 1-3 alone, and 2-5 combined with a text-CTA cap.
    - Text-CTA caps, in three variants, each lost 1-3. Shrinking the CTA in banner columns flips the critic's choice from the split layout the judge prefers to columns.
    - Insetting rotated stickers by their overhang lost 3-6. Sometimes the judge likes the corner bleed; sometimes moving inward hits the logo.
- **Verdict:**
  - The current architecture has plateaued on this benchmark: recent changes are near-ties the judge splits about evenly.
  - Remaining gains likely need a new idea rather than parameter tuning. Candidates: more candidate archetypes paired with a learned critic, or alternative layouts specifically for strips.
  - Keep v1.13.1.

### 2026-10-16: v1.12 to v1.13, and two rejected placement ideas
- **Branch:** `feat/engine-v1`.
- **Change and result:** pairwise against the previous best, dev then test.
  - **v1.12, accepted:** 4-2 and 3-0. On canvases more than 2.2 times wider than tall, the font cap follows the row height: 34% of it for headlines, 20% for other text. The old cap, 14% of the short side, left leaderboard headlines at about 12 px beside a much bigger button.
  - **v1.12.1, accepted:** 1-0, all else identical. Text-only CTAs that overflow their banner column shrink until they fit. The higher v1.12 cap had let one balloon and get cut off.
  - **Tiny-size free-space search, rejected:** 0-1, with almost nothing changed. Before parking a leftover on top of content, it searched for empty space at 3 and 4 px.
  - **v1.13, accepted:** 5-1 and 2-1, including the official Level 3. Rotated stickers are tucked into the corner they came from, or the nearest corner that covers no text.
  - **Calm-backdrop offset, rejected:** a new backdrop-busyness port slid each stack to where the background art behind the text has the least detail. A full slide lost 5-8 and 3-7, and a nudge capped at 4% of the height lost 6-7 and 2-6. The judge values balanced, centred placement over dodging the baked-in background dots.
- **Verdict:** keep v1.13. Two lessons:
  - The judge rewards balance and the original proportions.
  - Ideas that trade symmetry for detail-avoidance lose.

### 2026-10-16: v1.11 and two rejected spacing ideas
- **Branch:** `feat/engine-v1`.
- **Change and result:** pairwise against the previous best, dev then test.
  - **Orphan control, accepted as v1.11:** 4-0 and 2-0. When a wrap leaves a single word on the last line, the measure narrows in 5% steps until the last line holds two words or more, keeping the line count (`_balanced_width` in `engine/core/sizing.py`).
  - **Weighted decoration placement, rejected:** tried in the same round, the combination lost 5-8. Isolating orphan control showed this change caused the losses. It weighted the logo and copy at 3, the product at 0.5, and added a distance tie-break.
  - **Proximity gaps, rejected:** 0-17 and 1-8. The subhead was tight under the headline (0.55x) and the CTA got extra air (1.3x). The judge consistently preferred roughly equal gaps between stacked elements and called the grouped version uneven.
- **Verdict:**
  - Keep v1.11.
  - Lesson for future changes: this judge rewards even spacing and the original proportions over textbook design moves. Test spacing ideas against it before investing in them.

### 2026-10-16: hiding policy, judged
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** hiding low-priority elements under the background (`CHAMELEON_DROP_POLICY=occlude`) beats keeping everything visible. This bears on open question 1 for the organizers.
- **Change:** v1.10 run with occlude, compared pairwise against v1.10 with `none`.
- **Result:**
  - **Hiding in every layout:** occlude lost 3-11 on dev and 1-5 on test. The judge compares against the original and penalises content that disappears.
  - **Hiding only on strips:** occlude was even, 2-2 on dev and 1-1 on test. v1.10's strip handling has closed the gap that hiding filled in iteration 2.
- **Verdict:** keep `none` as the default whatever the organizers answer. The occlude policy now hides only on strips (`engine/core/layout.py`, `_place_leftovers`), in case a later engine needs it.

### 2026-10-16: judge-driven iterations v1.8 to v1.10
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** the judge's most repeated complaints have mechanical fixes.
- **Change and result:** pairwise numbers compare against the previous best, dev then test.
  - **v1.8, accepted:** 17-2 and 3-1. Decorative shapes move to the corner or edge spot that covers the least text and button area, instead of keeping their source-relative position. Translucent circles behind copy had been the judge's most common complaint.
  - **v1.9, accepted:** 12-4 and 6-2.
    - Strip CTAs at half the row height.
    - Decorations also avoid the logo.
    - Hero growth on tall canvases limited to 40% of the spare room and 45% of the height.
    - Legal copy may run at 75% of the readable size.
    - The critic penalises headlines wrapped past three lines on wide canvases.
    - The first try, 11-5 and 5-4, had a bug: the readability check still used the normal floor for legal, so legal was dropped and parked out of order. Fixing that produced the accepted result.
  - **v1.10 candidates:**
    - Letting the hero yield space changed almost nothing alone, at 1-2. The real cause of tiny badges was chips from full-width source pills: the code shrank their font before trimming their padding.
    - Trimming padding first won on narrow canvases but lost on banners, where buttons grew oversized. That went 7-8.
    - A pill-height cap kept buttons as pills but made them too small on big canvases. That went 10-7, with losses on stories and Pinterest pins.
    - Capping the banner CTA at 0.85 of the headline was not enough, at 6-6. The judge wants the source's label-to-headline ratio, often about 0.4.
  - **v1.10, accepted:** 6-0, with test outputs identical. Chip reshaping, the CTA floor a step above badges, and hero yielding apply only to stacked layouts. Banner and strip columns keep the source's chip proportions and cap the CTA label at 1.2 times the source ratio.
- **Absolute judge scores, overall, dev then test:**

  | Version | Dev | Test |
  |---|---|---|
  | v1.2 | 5.95 | 6.78 |
  | v1.7 | 6.31 | 6.67 |
  | v1.8 | 6.44 | 6.67 |
  | v1.9 | 6.46 | 6.89 |
  | v1.10 | 6.51 | 6.83 |

  Absolute scores move slowly and drift by about plus or minus 0.2, which is why pairwise decides.
- **Verdict:** keep v1.10. What remains is mostly strips under the no-hiding policy, where everything must stay visible, plus the intentional rubric row.

### 2026-10-16: judge-driven iterations v1.4 to v1.7
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** let Claude's pairwise verdicts on the frozen suite decide each change, instead of my eye or the engine critic.
- **Change:**
  - New tooling: the frozen suite in `bench/suite/`, pairwise judging in both orders, and task-by-task comparison with side-by-side images. See `docs/benchmark.md`.
  - The engine changes per version are in `docs/leaderboard.md`.
- **Result:** every pairwise number below compares against the previous best and counts wins against losses.
  - **v1.4, rejected:** 9-13 dev, 2-5 test against v1.2.
    - The judge consistently said pinning the logo to the top edge split it from its headline. The only exception was genuinely tall canvases.
    - It also disliked stacking badge rows that would have fit side by side.
  - **v1.5, accepted:** 10-2 dev, 2-1 test. It pins only when width over height is below 0.75, keeps badge rows horizontal, trims padding before stacking, and keeps the source's spacing between pills.
  - **v1.6, accepted:** 15-4 dev, 5-1 test. The judge's most common complaints had been illegible logos, sliver-sized products, strip CTAs that outshout the headline, buttons jammed against banner edges, and leftovers parked at the canvas edge.
  - **v1.7 candidates:**
    - A 1.2x CTA boost lost 5-11. The judge called the buttons oversized against the original proportions. Reverted.
    - A one-line disclaimer band for strips lost three variants in a row, 1-2, 1-2 and 1-3 on dev. Too small and it reads as illegible; larger and it competes with the message. Left disabled behind `_FINE_PRINT_BAND` in `engine/core/layout.py`.
  - **v1.7, accepted:** 1-1 dev, 1-0 test. Headline-first strips: the subhead leaves the row when it is as big as the headline, or when dropping it lets the headline grow 25%. Leftovers now avoid covering text when nothing is free. It fixed the worst design in the suite, `mobile_banner_1`, judged 2.
  - **Rubric means for v1.7:** intentional is the weakest row, at 6.1 dev and 6.5 test. Then collisions, 6.7 and 7.4, and readability, 6.8 and 7.2. Brand is strongest, at 8.0 and 8.3.
- **Verdict:** keep v1.7 as the baseline. Next targets:
  1. The intentional rubric row: alignment between columns, and even spacing.
  2. Decorative circles sitting behind text and buttons. The judge mentions this in many critiques.
  3. Tiny badges on narrow canvases.
  4. The official Level 3 sample.

### 2026-10-13: drop policy comparison
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** hiding low-priority elements under the opaque background fixes the 728x90 case.
- **Change:** same engine as iteration 2, with `CHAMELEON_DROP_POLICY=occlude`.
- **Result:**
  - Critic mean 97.2 to 98.8, and the critic minimum 48.6 to 90.8.
  - Level 3 at 728x90 went from judge 4 to 7. It now shows logo, product, headline, subhead and button, which matches the brief's own reference leaderboard.
  - Compare the two in `output/bench/level3_policy_compare.png`.
- **Verdict:** revisit once the organizers answer whether hiding is allowed; it is open question 1 in `docs/research.md`. The default stays `none`.

### 2026-10-13: engine v1, iteration 2
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** four iteration-1 failure clusters have cheap, general fixes.
- **Change:**
  - Images whose transparent margins would cross the canvas edge now shrink around their slot. Before, the final clamp shifted them sideways, which caused six of the seven iteration-1 fails.
  - Text and chip labels are floored at the canvas's readable size.
  - The scale cap now also allows filling the canvas when the source content was a small cluster.
  - Stacks drop the least important unit by fit rather than by font size.
  - Fine print always closes a stack.
  - Leftovers that can't be placed fall back to half-size fine print in free space, then to the least-overlapping spot.
- **Result:**
  - Judge mean 6.1 to 7.2. Fails, scored 4 or below, went from 7 to 1.
  - Critic mean 89.4 to 97.2, and the critic minimum -44.9 to 48.6.
  - All 25 tasks pass structural checks; mean runtime is 26 ms per design, natively.
  - The only remaining fail is Level 3 at 728x90, judged 4: crowded, because policy `none` must keep every element visible.
- **Verdict:** keep. The next fixes, in order:
  1. Scale non-headline text so it never rivals the headline. In synthetic 1018, the subhead nearly matches it.
  2. Reward hero prominence in the critic. Columns with a tiny hero currently beat a good split, as in synthetic 1005.
  3. Set a minimum logo size in strips.
  4. Steer decorative circles away from text.
  5. Time the Docker image under amd64 emulation.

### 2026-10-13: engine v1, iteration 1
- **Branch:** `feat/engine-v1`.
- **Hypothesis:** a deterministic pipeline beats the stretch baseline without any model. The pipeline:
  1. infer roles and groups;
  2. propose layouts per aspect bucket, as a stack, a split, columns or a strip;
  3. fit text with the renderer's own metrics;
  4. score candidates with coded checks and keep the best.
- **Change:** first version of `engine/core`, the bench in `tools/`, and the contract tests.
- **Result:**
  - Judge mean 6.1, with 7 fails out of 25.
  - Critic mean 89.4.
  - All 25 tasks pass structural checks.
  - Strong on banners and landscape targets.
  - The failures:
    - logos and images shifted into text by the canvas clamp;
    - unreadable labels on tiny canvases;
    - fine print landing mid-stack;
    - a pile-up at 728x90.
- **Verdict:** keep the architecture, fix the failure clusters. That became iteration 2.
