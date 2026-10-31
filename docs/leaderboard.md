# Engine leaderboard

One row per tagged or candidate engine version on the frozen suite (`bench/suite/`), newest first. See `docs/benchmark.md` for how each number is produced.

How to read the columns:
- **Pairwise** compares a version against the previous best in both image orders, counting only tasks whose output changed. It decides acceptance.
- **Absolute** judge scores drift by about plus or minus 0.2 between identical runs, so treat small differences as noise.
- **Formats** had 32 tasks until v1.26 added 20 at five small ad sizes, for 52.

| Version | Tag | Structural (dev / test) | Pairwise vs previous best (dev / test) | Judge overall (dev / test) | Judge fails at 4 or below (dev / test) | Verdict |
|---|---|---|---|---|---|---|
| v1.29 | none yet | 39/39, 18/18, probe 60/60, formats 52/52, in Docker | vs v1.28: not judged, because the API key in `.env` returned 401. The 19 tasks with a missing asset changed and the other 150 outputs are byte-identical | not rescored. v1.28's 6.85 / 7.11, formats 6.00, still describe the 150 unchanged tasks | not rescored | **Submission build.** v1.28 plus a missing asset kept visible as the renderer's grey placeholder, as the organizers' October 22 recap asks. No element is hidden and no `opacity` or `z_index` changes. Slowest task 9.1 s. The add-on's Level 3 preview at 300x250, outside the benchmark, regressed |
| v1.28 | none yet | 39/39, 18/18, probe 60/60, formats 52/52, in Docker | vs v1.27, API in the container: dev 4-1 with 5 ties, test 0-0 with 2 ties, probe 11-1 with 4 ties, formats 5-4 with 7 ties. Fixes combined vs the candidate: dev 1-0, probe 8-2, formats 3-0 | API container: 6.85 / 7.11, formats 6.00, probe not rescored | 2 / 0, formats 11 | Accepted, superseded by v1.29. v1.27 plus equal-width pills, a headline lead on small banners, tiered packed text, buttons kept in one row, cover images sized by their visible pixels, and slimmer floored buttons at 300x250 and 336x280. Slowest task 6.3 s. YouTube safe area rejected |
| v1.27 | none yet | 39/39, 18/18, probe 60/60, formats 52/52, in Docker | vs v1.26: dev 0-0 (2 ties), probe 5-0 (2 ties), formats 10-0, test unchanged. Vs v2: dev 0-0 (1 tie), probe 1-0 (2 ties), formats 10-0 (API, container) | API container: 6.85 / 7.11, probe 6.48, formats 5.96 | 1 / 0, probe 1, formats 11 | Accepted, superseded by v1.28. v1.26 plus `exp/engine-v2`'s skyscraper headlines and fine-print order, the word-pair cap measured at each size, and the packed fallback for small ads that pile content on copy |
| v1.26 | `engine-v1.26` | 39/39, 18/18, probe 60/60, formats 52/52, in Docker | vs v1.25: formats 5-0, dev 0-0 (1 tie), test and probe unchanged (API, container) | not rescored | not rescored | Accepted, superseded by v1.27. No text draws past the canvas; rules-audit robustness fixes |
| v1.25 | `engine-v1.25` | 39/39, 18/18, probe 60/60, formats 32/32, in Docker | vs v1.24: formats 1-0 (official Level 3 at 336x280), probe 0-0, dev and test unchanged (API, container) | not rescored | not rescored | Accepted. Dense copy fits small splits and stacks before parking |
| v1.24 | `engine-v1.24` | 39/39, 18/18, probe 60/60, formats 32/32, in Docker | vs v1.23: dev 10-0, test 4-1, probe 10-7, formats 4-3 (API, container) | not rescored | not rescored | Accepted. Left and right margins match on drawn pixels; banner row gap bug fixed |
| v1.23 | `engine-v1.23` | 39/39, 18/18, probe 60/60, formats 32/32, in Docker | decorations vs v1.22: dev 18-5, test 5-1, probe 43-2; merged interactions dev 1-1, probe 4-1, formats 14-1 (API, container) | not rescored | not rescored | Accepted. Decorations placed by a weighted cost; tall-canvas button padding; banner CTA cap that takes effect |
| v1.22.1 | `engine-v1.22.1` | 39/39, 18/18, probe 60/60, formats 32/32 | formats 9-2, other splits unchanged (API, container) | not rescored | not rescored | Accepted. Type keeps its proportions on canvases larger than the source |
| v1.22 | `engine-v1.22` | 39/39, 18/18, probe 60/60, in Docker | vs v1.21: dev 1-0 (official Level 3), test 1-0 (API, container) | API container: 6.77 / 6.94, probe 6.33 | 2 / 0, probe 2 | Accepted. Badge rows stack with the copy |
| v1.21 | `engine-v1.21` | 39/39, 18/18, probe 60/60 | vs v1.20: test 1-0, probe 1-0 (API, container) | not rescored | not rescored | Accepted. Readable candidates for the smallest strips |
| v1.20 | `engine-v1.20` | 39/39, 18/18, probe 60/60, in Docker | vs v1.19.1: dev 2-0, test 4-0, probe 2-1 (API, container). Vs v1.2: 28-4 / 12-1, probe 51-2 (Sonnet, container) | not rescored | not rescored | Accepted. Rotated ribbons stay whole on the canvas |
| v1.19.1 | `engine-v1.19.1` | 39/39, 18/18, probe 60/60 | outputs identical to v1.19 | API container: 6.77 / 7.06, probe 6.35 | 2 / 0, probe 1 | Accepted. Text-wrap cache, worst container task 7.8 s to 1.3 s |
| v1.19 | `engine-v1.19` | 39/39, 18/18, probe 60/60 | vs v1.18: dev 2-0, test 2-0, probe 5-3 (API, container) | about v1.19.1 | about v1.19.1 | Accepted. Text-link CTAs under the copy, line runs as one unit, room between stacked pictures, sticker label guard |
| v1.18 | `engine-v1.18` | 39/39, 18/18, probe 60/60, also in Docker | v1.17 plus text floors: dev 1-0, probe 5-2 (API, container) | API container: 6.72 / 6.94, probe 6.30 | 2 / 0, probe 2 | Accepted. Text only at sizes whose word spaces render in the scoring container |
| v1.17 | `engine-v1.17` | 39/39, 18/18, probe 60/60 | vs v1.16.1: dev 4-1, test tie, probe 15-2 (API, container) | not rescored | not rescored | Accepted. Tall-canvas rhythm, marks clear of glyphs, shared left edges |
| v1.16.1 | `engine-v1.16.1` | 39/39, 18/18, probe 60/60 | dev and test unchanged | not rescored | not rescored | Accepted. Rules-audit hardening: id-blind keys, no vanishing or stretched images, letter spacing, malformed input |
| v1.16 | `engine-v1.16` | 39/39, 18/18, probe 60/60 | dev 0-0 (1 tie), test unchanged, probe wins every fix (see `docs/experiments.md`) vs v1.15 | session probe 6.28 (v1.15: 5.63) | session probe 4 (v1.15: 8) | Accepted. Oversized source type kept, container panels fitted behind their copy, bullets and rules kept with their text, source reading order, matched image sets |
| v1.15 | `engine-v1.15` | 39/39, 18/18 | 9-0 / 6-0 vs v1.14, blind in-session judge | session: 6.74 / 7.39, probe 5.63 | session: 3 / 0, probe 8 | Accepted. Strip leftovers stack onto the copy and button columns, balanced line breaks, one-line headlines kept whole, stickers follow the headline's scale and stay off the logo |
| v1.14 | `engine-v1.14` | 39/39, 18/18 | 6-0 / 1-0 vs v1.13.1 | not rescored | not rescored | Accepted. Critic breaks ties on backdrop detail under copy, decorations over the product, the source's product side, and headline rewrap; no element moves |
| v1.13.1 | `engine-v1.13.1` | 39/39, 18/18 | outputs identical to v1.13 | 6.72 / 7.00 | 4 / 0 | Accepted. Robustness fix for image-only designs; 98 stress cases pass. Against v1.2 directly: 25-7 / 13-1 (Opus), 24-3 / 11-1 (Sonnet, a second judge) |
| v1.13 | `engine-v1.13` | 39/39, 18/18 | 5-1 / 2-1 vs v1.12.1 | 6.72 / 7.00 | 4 / 0 | Accepted. Rotated stickers tucked into their source corner |
| v1.12.1 | `engine-v1.12.1` | 39/39, 18/18 | 1-0 / no change vs v1.12 | about v1.12 | about v1.12 | Accepted. Text-only CTAs that overflow their column shrink to fit |
| v1.12 | `engine-v1.12` | 39/39, 18/18 | 4-2 / 3-0 vs v1.11 | 6.44 / 7.00 | 5 / 0 | Accepted. Banner and strip headlines scale with row height. One dev fail, `facebook_cover_1`, was a regression fixed in v1.12.1 |
| v1.11 | `engine-v1.11` | 39/39, 18/18 | 4-0 / 2-0 vs v1.10 | not rescored | not rescored | Accepted. Orphan-free wrapping |
| v1.10 | `engine-v1.10` | 39/39, 18/18 | 6-0 / no change vs v1.9 | 6.51 / 6.83 | 3 / 0 | Accepted. Narrow stacked canvases trim chip padding before shrinking copy, keep the CTA above badge size, and let the hero yield before dropping copy; banners keep source chip proportions |
| v1.9 | `engine-v1.9` | 39/39, 18/18 | 12-4 / 6-2 vs v1.8 | 6.46 / 6.89 | 4 / 0 | Accepted. Calmer strip CTA, decorations avoid the logo, modest hero growth on tall canvases, smaller fine print with a matching readability check |
| v1.8 | `engine-v1.8` | 39/39, 18/18 | 17-2 / 3-1 vs v1.7 | 6.44 / 6.67 | 4 / 0 | Accepted. Decorative shapes move off text and buttons |
| v1.7 | `engine-v1.7` | 39/39, 18/18 | 1-1 / 1-0 vs v1.6 | 6.31 / 6.67 | 4 / 0 | Accepted. Strip headlines stay dominant; leftovers avoid covering text |
| v1.6 | `engine-v1.6` | 39/39, 18/18 | 15-4 / 5-1 vs v1.5 | 6.21 / 6.83 | 3 / 0 | Accepted. Logo and hero size floors, calmer strip CTA, wider banner side margins, parking inside the safe area |
| v1.5 | `engine-v1.5` | 39/39, 18/18 | 10-2 / 2-1 vs v1.2 | 6.15 / 6.67 | 3 / 1 | Accepted. Pin logo and fine print only on tall canvases; badge rows stay horizontal when they fit |
| v1.4 | none | 39/39, 18/18 | 9-13 / 2-5 vs v1.2 | 6.15 / 6.61 | 3 / 0 | Rejected: pinning the logo everywhere split it from the headline |
| v1.2 | `engine-v1.2` | 39/39, 18/18 | (baseline) | 5.95 / 6.78 | 5 / 0 | First tagged engine |

Rejected experiments along the way are logged in `docs/experiments.md`:
- a 1.2x CTA boost, a disclaimer band for strips, a pill-height cap and chip reshaping in banners;
- an early weighted decoration placement, later replaced by v1.23's weighted cost, two decoration rewrites, proximity-based gaps and equal ink gaps;
- a tiny-size free-space search and a calm-backdrop offset;
- text-CTA caps, a split-layout bonus, and banner image columns at their drawn width;
- the floorplan engine, a separate slicing-tree engine that lost 61-86 to v1.26 over all four splits.

The v1.15 and v1.16 rows come from Claude agents judging blind inside a Claude Code session, with the same rubric and both image orders. From v1.17 on, every pairwise result comes from the API judge on renders made in the scoring container. `docs/benchmark.md` describes both judges.
