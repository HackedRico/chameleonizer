# Chameleon challenge research

Compiled 2026-10-06 for the Adobe University Hackathon 2026 Chameleon challenge. Sources: the brief PDF, the starter repo at [jhaaj08/chameleon](https://github.com/jhaaj08/chameleon) commit `a44a060` from 2026-09-24, experiments run against the official renderer, and five research passes. The passes covered Adobe, other companies, academic papers and the local runtime, plus one deep-research run in which three independent agents voted on each claim.

## Short version

- **The problem:** take a finished design as JSON and a new canvas size, and return the same elements moved and resized so it still looks designed. Rearrange, never stretch, crop the layout or generate pixels.
- **What you write:** one entry point, `resize(source, target_canvas)`, with as much code behind it as you want. You submit code, a Dockerfile, a README and a viewer add-on that is required but not scored.
- **Stage one environment:** offline linux/amd64 Docker on an Apple M5 Pro. The container cannot reach the GPU or Neural Engine, so any model runs CPU-only under emulation.
- **Models:** no approved list or size cap yet. The same organizer's 2025 hackathon capped models at 200 MB and 1 GB.
- **Scoring:** structural checks plus an AI judge on the rendered PNG.
- **The evidence on your orchestrator plan:** the harness is the right investment, but it should be a deterministic engine that proposes and checks layouts itself. A model should only make discrete choices, such as element roles or which strategy to try, and every choice needs a coded default. Small models are weak at coordinates and at judging designs, and untrained self-critique loops give little or nothing.
- **Finale:** the Express add-on SDK leaves relayout to the caller, so your engine plugs in directly. Cloud models make much better critics, and that is where the agent layer belongs.
- **Ask the organizers now** whether elements may be hidden. The Level 3 sample puts 17 elements into 728x90, and the brief's own reference banner shows four of them.

## 1. Problem and scope

### What you build

The brief calls the scored part "the engine": a standalone service that takes a design and a target size and returns new layout instructions. The starter repo supplies everything else: file I/O, a structural self-check, the official renderer and a Dockerfile. `main.py` is labeled the "orchestrator" in the repo, but it only loops over input files and calls `resize()`.

The README says `resize.py` is "the only file you need to edit" because it is the only file `main.py` imports. Nothing stops you adding modules, dependencies or model weights. The README itself tells you to bake weights into the image at build time, which means editing the Dockerfile and `requirements.txt`.

### Input and output

- **Input:** `source`, a canvas plus a list of `text`, `image` and `shape` elements with pixel geometry, rotation, opacity, `z_index`, `group_id` and type-specific fields. There are no role labels. Also `target_canvas`, a width and height drawn from a catalogue of real formats such as Instagram Post or YouTube Thumbnail.
- **Output:** the same design shape with new geometry for the target canvas. The renderer turns it into the scored PNG.
- **Samples:** 4 elements from 1080x1080 to 1080x1350; 11 elements from 1080x1080 to 1600x400; 17 elements from 1080x1350 to 728x90.

### Hard rules

- Return every element id exactly once.
- Nothing off-canvas.
- Non-background images keep their source aspect ratio within about 5%. `fit: cover` backgrounds are expected to crop.
- Font size may change. Text content and font family may not in stage one; copy rewriting is a finale idea.
- Regenerating, redrawing or replacing any asset's pixels disqualifies the entry, even for a background.
- No hand-tailoring to specific designs.

### Scoring

- **Structural:** ids, required fields, bounds, image proportions.
- **Visual:** an AI evaluator scores the rendered PNG against a written rubric. The brief lists the rows: no unwanted collisions, everything readable, stays on brand, looks intentional, holds up across many designs. "Speed and reliability" is a minor row.
- Both are checked. Good JSON that renders badly gets no credit, and a good-looking render from dishonest JSON gets none either.

### Stage one environment

- `docker build --platform linux/amd64` and `docker run --network none`: no cloud APIs, no remote MCP servers, no model downloads at run time.
- Reference machine: Apple M5 Pro. RULES.md still marks the CPU, RAM and disk limits as a placeholder.
- Local open models allowed, under a size cap and approved list that are "being finalized with the science team". Fine-tuned weights and LoRA adapters must be public, for example on Hugging Face.
- No hard time limit, but an engine that needs more headroom than the reference machine "risks failing or timing out".

### Finale

The top six or seven teams turn the engine into a full Adobe Express add-on, present to a cross-functional jury, and submit a pitch and a demo video. The brief invites offering several options, letting users lock parts, gracefully dropping less important elements, rewriting copy and even regenerating imagery. Cloud models and MCP integrations belong here.

### Still undefined or contradictory

- Model list, size cap, hardware limits and the per-task timeout.
- The schema is titled "v0.9, provisional, pending Peter's sign-off".
- The README calls the off-canvas warning on the Level 2 corner-bleeding circle "an expected one", while RULES.md lists "nothing off-canvas" as a structural check.
- The FAQ describes a `transformer-convertor` app on port 5050 that is not in the repo; the README says the renderer is vendored in `renderer/`.
- The brief's reference image rewrites copy, turning "Shop the Summer Line" into "Shop", and recolors a background. Stage-one rules reward neither, so treat it as a finale target.
- The brief lists "crop & focal point" as something that adapts, but the schema has no crop or focal fields. Cover images are always center-cropped to their box.

## 2. How the official renderer behaves

Verified by running the starter repo's renderer natively on an M2 with Python 3.11 and skia-python 144.

| Behavior | Consequence for the engine |
|---|---|
| Text is top-aligned in its box; each line advances by `font_size * line_height` | No vertical centering. Compute real text height yourself |
| No clipping: a 40 px tall box drew ink from y=30 to y=206 when its text wrapped | Collision checks must use real text extents, not boxes |
| Greedy word wrap at the box width using real glyph widths; a word wider than the box overflows | Measure with the renderer's own `wrap.py` and fonts |
| A text element's `opacity` is ignored; the alpha inside its `rgba()` color is honored | You cannot hide text with `opacity` |
| Shape and image `opacity` is honored; `opacity: 0` draws nothing | |
| An element below an opaque full-bleed image is invisible | z-order can hide things, if hiding is allowed |
| Font size is truncated to an integer when drawn, but wrap is measured with the float | Always emit integer font sizes |
| `contain` letterboxes, `cover` center-crops, `fill` stretches | No focal-point control |
| A missing asset draws a grey box with a red border and "missing asset: <id>" | The judge will see it |
| Canvas defaults to white | Keep the background covering the canvas |
| `style_runs` end index is treated as inclusive; the schema says exclusive | One extra character gets styled |
| About 0.12 to 0.14 s per design at 1080 px, including PNG encode | Rendering many candidates per design is affordable |

Asset facts worth using:

- `solstice_bottle.png` is 800x800, but its visible pixels span only 341x551.
- `solstice_logo_lockup.png` is 900x300, a 3:1 box, but its visible pixels span 364x216, about 1.7:1.
- Collisions the judge sees depend on visible pixels, so compute alpha bounding boxes.

Level 3 text budget: at a barely readable 12 px, the headline, subhead, legal line, button and badges need about 3,200 px of line length. A 728x90 canvas holds about 4,400 px of 12 px lines if nothing else were on it. Fitting every element legibly is impossible, which is why the hiding question matters.

## 3. Why this is still unsolved

- **The file doesn't say what anything is.** Software has to infer which text is the headline, the button or the legal line, and one wrong guess shrinks the wrong thing.
- **Extreme ratios force a structural rebuild.** A tall stack has to become a row, and something has to give.
- **There is no single right answer.** No large dataset pairs designs with correct resized versions, and no public benchmark compares resize tools.
- **Tools route around it.** They crop, follow rules someone authored for that template, have designers build every size in advance, or now generate new pixels. Adobe's own [social variations skill](https://github.com/adobe/skills/tree/main/plugins/creative-cloud/adobe-for-creativity/skills/adobe-create-social-variations) requires Express files to be "exported to JPG/PNG first", then crops or AI-expands them.

## 4. Prior art

### Adobe

- **Patent [US11233920B1](https://patents.google.com/patent/US11233920B1/en),** filed 2020, granted 2022. Calls an element background if it touches three or more edges or covers a large share of the canvas. For small ratio changes it scales backgrounds and repositions foregrounds; for large ones it first builds a single-column "verticalized" intermediate layout.
- **Express Resize.** Launched Oct 2024 as "Resize and Expand"; Nov 2025 added layout variations. The method is undisclosed, and Generative Expand fills backgrounds.
- **Express AI Assistant.** Beta Oct 2025; it can resize, and July 2026 added "Fix the layout of a single-page design".
- **Authored-rule relayout.**
  - InDesign: Liquid Layout rules, Adjust Layout from 2018, and Flex Layout, flexbox-style containers, from Oct 2025.
  - XD Responsive Resize infers constraints from grouping and distance to edges.
- **GenStudio for Performance Marketing.** Display-ad resize duplicates the ad and refits images with Generative Expand plus crop. Multi-size templates are built by designers.
- **Firefly Services.** Creative Production resize and the Express API have no relayout endpoint. The Reframe API crops video only.
- **Project Remix a Lot,** a MAX 2024 Sneak, reformatted designs into batches of sizes, with no technical details published. MAX 2026 runs Nov 10 to 12, so watch for a competing announcement.
- **Adobe Research.**
  - [O'Donovan, Agarwala, Hertzmann 2014](https://www.dgp.toronto.edu/~donovan/layout/index.html) retargeted designs with an energy over alignment, importance and hierarchy, and published 98 retargets.
  - Other related work: visual importance prediction for retargeting (UIST 2017), [AesthetiQ](https://arxiv.org/abs/2503.00591) (CVPR 2025), FlexDoc (2024), the [Agentic Design Review System](https://arxiv.org/abs/2508.10745) (AAAI 2026) and [DesignSense](https://arxiv.org/abs/2602.23438) (Feb 2026).
  - DesignSense is the closest published analog of the Chameleon judge: human preference pairs on Crello relayouts.

### Developer tooling for the finale

- **Express add-on SDK.** The UI iframe can call the network; the document sandbox cannot. The sandbox can read the page's nodes, move and resize them, add pages of any size, and render nodes to PNG with an experimental `createRendition()`.
- **The key line,** from the [PageNode docs](https://developer.adobe.com/express/add-ons/docs/references/document-sandbox/document-apis/classes/page-node): changing a page's size "does not adjust the size or position of any of visual content". Callers bring their own layout logic, which is your engine.
- **MCP options:**
  - The Express Developer MCP server, `@adobe/express-developer-mcp`, GA Jan 2026, helps coding agents build add-ons.
  - The [Adobe for creativity](https://blog.adobe.com/en/publish/2026/04/28/adobe-for-creativity-connector) connector for Claude, Apr 2026, has 50+ tools.
  - GenStudio and Firefly Creative Production also have remote MCP servers.
  - Mike Chambers' [adb-mcp](https://github.com/mikechambers/adb-mcp) drives desktop apps, not Express.
  - All of these need the network, so none apply to stage one.

### Other companies

- **Canva Magic Switch,** Oct 2023, does real relayout. Its public parts:
  - [US11763044B2](https://patents.google.com/patent/US11763044B2/en): groups elements by finding empty-space divisions.
  - [US11972196B1](https://patents.google.com/patent/US11972196B1/en): treats only new overlaps as defects and shrinks or moves the responsible element.
  - [Engineering blog](https://www.canva.dev/blog/engineering/how-we-see-groups-in-design/): a 30M-parameter vision transformer finds implicit groups in about 70 ms.
  - A 2026 review still reports overflowing text, tiny logos and broken dense layouts on big ratio changes.
- **Figma** uses authored constraints and auto layout. Its Buzz resize help page tells users to check for overlaps and distortion.
- **Google responsive display ads** assemble ads from asset pools with an ML model. Google's creative guide advises against text over images and warns that logo corners may be cropped.
- **Creative automation platforms** mostly use designer-authored rules or master layouts per format.
  - Celtra, Bannerflow, Abyssale, Bannerbear and Templated document anchoring, safe zones and text-fit rules.
  - Orshot's 2026 Smart Resize re-anchors elements "to the edge they were designed against".
  - Pencil's 2026 auto-resize beta admits it "does not yet account for safe zones".
- **Asia.**
  - Alibaba LuBan generated millions of banners with "size expansion"; the method is unpublished.
  - Alibaba's AutoPoster resizes by outpainting plus saliency crops, which stage one forbids.
  - ByteDance's [CreatiPoster](https://arxiv.org/abs/2506.10890) relays out from layer JSON plus a new canvas size, but regenerates the background.
- **No public benchmark** compares resize quality across tools.

### Academic work most worth borrowing

- **Treat the source design as the answer key.** Infer a relation graph before moving anything: alignment lines, containment such as text on a button, reading order, size ranks and groups. An overlap the source already has is intended; a new one is a defect, as in [Walsh et al. 2017](https://doi.org/10.1145/3092703.3092712) and Canva's patent.
- **Pick a structure per target ratio, then solve geometry.**
  - [ORC Layout](https://arxiv.org/abs/1912.07827) adds OR-constraints, such as "B right of A or B below A".
  - [FlexDoc](https://arxiv.org/abs/2410.15504) pairs a discrete template choice with a continuous solve, in under a second on a laptop CPU.
  - [kiwisolver](https://github.com/nucleic/kiwi) is a Cassowary solver that runs in milliseconds.
- **[UMSI](https://arxiv.org/abs/2008.02912) reflow:** rank elements by importance, retrieve a template with the same element count and the closest aspect ratio, and assign elements to slots by rank.
- **Score with an explicit energy from a warm start.** Combine O'Donovan's terms, the area, aspect-lock and reading-order losses from [Attribute-conditioned LayoutGAN](https://arxiv.org/abs/2009.05284), and a penalty for drifting from the source. DesignScape refines in under a second from a warm start.
- **Repair at the end** with grid snapping and containment fixes, as in [LayoutRectifier](https://arxiv.org/abs/2508.11177).
- **Measure readability on pixels.**
  - WCAG contrast against the pixels under each text box.
  - Sobel-gradient busyness under text, as in PosterLayout's readability metric; reimplement it in float, since its `eval.py` overflows uint8.
  - Spectral-residual saliency to avoid covering subjects.
- **Test data:**
  - [LICA](https://arxiv.org/abs/2603.16098): about 1,200 public layouts with fields close to this schema.
  - [Crello](https://huggingface.co/datasets/cyberagent/crello): 23k templates in 67 formats, including Leaderboard and Skyscraper.
  - O'Donovan's 98 retargets.
  - [Haraguchi et al.](https://arxiv.org/abs/2410.08885): 700 rated designs.

## 5. What the evidence says about the orchestrator plan

Each finding below passed the deep-research run's three-vote fact check, with the confidence it reported. Where a finding goes beyond the cited data, it is marked as inference.

1. **Keep every coordinate out of the model.** High confidence.
   - [Paper2Poster](https://arxiv.org/abs/2505.21497) found LLM-predicted coordinates "unstable" and switched to a binary-tree layout algorithm.
   - In [Graphic-Design-Bench](https://arxiv.org/abs/2604.04192), the best frontier model reached only about 0.24 mIoU on layout completion.
   - Untuned Qwen2.5-7B writing absolute JSON coordinates collided 69% of the time.
2. **Don't let a zero-shot local model judge or approve candidates.** High confidence.
   - In DesignSense, open judges from InternVL3-8B up to a 241B model scored about 0.54 to 0.55 binary accuracy against human preference, where 0.5 is chance.
   - In TASTE, six open judges including Qwen3-VL-4B scored 0.499 to 0.543. Scaling Qwen3-VL from 4B to 32B left agreement flat.
   - Build the critic from deterministic checks, one per rubric row.
3. **Don't build stage one around a model critiquing its own renders.** High confidence.
   - In [VFLM](https://arxiv.org/abs/2603.22187), render feedback without training gave marginal gains, and Qwen2.5-VL-7B's quality score fell.
   - A Qwen3-VL-4B self-correction study found that even after training, iterating did not beat a single shot under realistic stopping rules.
   - Spend compute on more candidates, with repairs driven by the coded critic.
4. **Treat model output as untrusted.** Medium confidence.
   - A zero-shot 3B model produced fully valid structured output only a minority of the time.
   - Use enumerated choices, a timeout and a coded default for every model decision, then a final validator that guarantees ids and bounds.
5. **Generate distinct candidates and select with the critic, expecting a modest gain.** Medium confidence. DesignSense's best-of-10 selection added about 3.6 points of win rate, and selection cannot rescue weak candidates.
6. **Size the model's job to the cap.** Medium confidence.
   - AesthetiQ's win rate against designer layouts was 2.4% at 1B, 6.1% at 2B, 14.7% at 4B and 17.2% at 8B. Proposing layouts only gets close to 8B quality at about 4B.
   - Under a 1 GB cap, a model can label and choose, not lay out. That conclusion is inference.
7. **Aim to eliminate obvious failures on every design.** Medium confidence. Even frontier judges agree with humans only weakly: DesignSense Cohen's kappa stays at or below 0.164. Worst-case quality likely matters more than chasing subtle aesthetics.
8. **Make the harness the product.** Medium confidence.
   - Agentic-DRS gave nearly the same gain on GPT-4o and Gemini.
   - An open 7B Paper2Poster pipeline nearly matched the GPT-4o one because the structure did the work.
   - A better model then improves choices without pipeline changes.
9. **For the finale, use cloud models as the critic.** Medium confidence. Agentic-DRS's attribute-specific reviewers raised flaw detection by 11 to 13 points over a single GPT-4o prompt.
10. **Training is what made model-driven layout competitive.** Medium confidence.
    - The trained VFLM 7B beat zero-shot GPT-4o and Claude on its own task.
    - A small learned head on frozen Qwen3-VL-Embedding-2B reached 0.611 agreement with designers, against at most 0.543 for every zero-shot judge.
    - If the no-fine-tuning policy relaxes, a small critic head on frozen embeddings is the cheapest start and keeps the base model swappable.

The run found no verified numbers for CPU inference under Rosetta, solver choice or saliency methods. Measure those yourself.

### On fine-tuning

Your reasons to skip it hold for the finale and for demo risk. Within stage one, though, "models get better" does not apply: the approved list and hardware are frozen for the competition, so the harness has to get the most out of whatever small model is allowed. The evidence says that model is most useful for discrete labeling and choices. If you later want more from it, training a small head is cheaper than fine-tuning the model itself.

## 6. Recommended architecture

### Stage one engine

1. **Normalize.** Treat explicit groups as rigid units. Compute visible bounding boxes from asset alpha channels. Detect missing assets. Measure text with the renderer's own skia metrics and wrap function.
2. **Infer structure and roles with code:**
   - background: a cover image at the lowest z covering most of the canvas;
   - logo: the brand-kit logo asset, or a small wide image near the top;
   - hero: the largest contain image;
   - headline: the largest font;
   - CTA: a rounded rectangle grouped with short text;
   - badges: short uppercase groups;
   - legal: the smallest font, long text, low alpha;
   - decoration: low-opacity shapes with no text.

   Do not rely on sample ids like "headline", because hidden designs may use opaque ids.
3. **Choose candidate structures by target aspect bucket:** strip above 5:1, banner 2.2 to 5, wide 1.1 to 2.2, square, portrait and tall story. Use a small template bank per bucket in the UMSI style, with row-versus-stack choices in the ORC style.
4. **Solve geometry.** Use kiwisolver or CP-SAT for positions. Binary-search integer font sizes against the renderer's wrap, with a minimum legible size per canvas. Lock aspect ratios for logos and products.
5. **Render each candidate** with the official renderer at reduced size, then score it with the deterministic critic:
   - pixel-level collisions that are new relative to the source;
   - text overflow;
   - WCAG contrast and busyness under text;
   - minimum font size;
   - logo visible size and aspect;
   - alignment and margin consistency;
   - hierarchy preserved.

   Let the worst defect dominate the score.
6. **Repair and validate.** Snap to a grid, fix containment, then guarantee every id, the bounds, the 5% aspect rule and integer font sizes. On any exception, fall back to a safe layout.

**Optional model slots:** each takes typed, enumerated input, has a timeout and a coded default.
- Text role labels: an embedding model under 100 MB fits even a 200 MB cap.
- Strategy choice.
- A pairwise tie-break between the final two or three renders, asked in both orders.

**Development loop:** big cloud models are allowed during development. Use them to generate a synthetic test set across the format catalogue, act as a stand-in judge, and tune the critic's weights. Ship only the tuned checks and small models.

**Build hygiene:**
- Compile llama.cpp with `GGML_NATIVE=OFF` and no AVX-512, or it may crash under Rosetta.
- Bake in weights and fonts, and keep imports lean.
- Test with `--platform linux/amd64 --network none`.
- Check that `/proc/cpuinfo` shows "VirtualApple" to confirm Rosetta is active.
- macOS 27 does not restore Rosetta after an upgrade, so an unprepared scorer could silently fall back to slow QEMU emulation.

### Finale add-on

- **Data flow:**
  1. The document sandbox serializes the current page into the Chameleon schema.
  2. The engine computes the new layout.
  3. The add-on clones the page, sets the target size and applies the geometry.
  4. `createRendition()` renders it for the cloud critic.
- **Hosting:** host the Python engine as a service, or port it to TypeScript if you need it fully in-browser.
- **Critic:** cloud frontier models as attribute-specific reviewers, given the render plus a text description grounded in the JSON. Keep geometry checks deterministic.
- **Features the brief invites:** batch output to every catalogue size, several options to pick from, locking elements, priority-based dropping, copy shortening, and generative expand for backgrounds.
- **Offline fallback:** a native MLX model on the M5, or the bare engine, so the demo survives a network outage.
- **MCP is optional polish.** You could expose the engine as an MCP tool so Claude or ChatGPT can call it, but the jury judges the add-on experience.

## 7. Models

There is no official list. Licenses and parameter counts below were checked on Hugging Face; file sizes are approximate.

| Likely cap | Candidates |
|---|---|
| 200 MB | No useful vision model fits. [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), 23M params, Apache-2.0, for text role labels |
| 1 GB | [Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B), Apache-2.0, image and text, about 0.7 GB at 4-bit; SmolVLM2-256M, Apache-2.0; Florence-2-base, MIT |
| 2 to 5 GB | [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) or [Qwen3-VL-2B](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct), Apache-2.0, about 1.5 to 2 GB at 4-bit; [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it), Apache-2.0, about 2.6 to 5 GB |

- **Avoid** Qwen2.5-VL-3B, which is non-commercial, and check LFM model licenses.
- **Latency:** a 2B vision model is estimated at 3 to 10 seconds per image under emulation on the M5 Pro. That estimate is unmeasured.
- **Runtime:** use llama.cpp with a JSON schema or GBNF grammar. Avoid PyTorch; its wheel alone is about 550 MB.

## 8. Questions for the organizers

1. May an element be hidden or de-emphasized while keeping its id, for example `opacity: 0` on shapes and images, or placement behind the background? Text opacity is ignored by the renderer.
2. May decorative elements bleed off-canvas? The README and RULES.md disagree.
3. What are the approved models and size cap? What CPU and RAM limits, which container runtime, is Rosetta enabled, and on which macOS version? What is the per-task timeout, how many tasks are there, and is it one container per task or per batch?
4. Will hidden designs use the same brand kit, and may the engine read `brand_kit/assets` at run time?
5. Which other fields may change: `text_align`, `line_height`, `letter_spacing`, `rotation`, `fit`, `z_index`?
6. Can teams see the written rubric, and which model and image resolution does the judge use?
7. What must the stage-one viewer add-on do?
8. How should an element with a missing asset be handled?
9. Are there model or network rules for the finale?
10. How do we submit: repo link, zip or Docker image? Must the submitted repo be public?

The recap of the organizers' October 22 information session answers questions 1 and 8 for the baseline output: "Every input element should remain visible." The engine keeps a missing asset visible as the renderer's grey placeholder and hides nothing by default.
