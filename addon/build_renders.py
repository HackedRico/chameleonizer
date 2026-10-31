from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from engine.resize import resize
from renderer.converter.convert import render_design_to_png
from tools.catalog import FORMATS as CATALOG
from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# Builds the previews the viewer add-on bundles. `build` resizes each official
# sample in `input/` to every format in `FORMATS` with the stage-one `resize`,
# draws sources and outputs with the official renderer, and writes the PNGs and
# the `index.json` catalogue the panel reads into `addon/renders/`.

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "brand_kit" / "assets"
OUT = ROOT / "addon" / "renders"
# Catalogue names covering portrait, tall, landscape, banner and small ad shapes,
# including each official sample's own target.
FORMATS = (
    "Instagram Portrait",
    "Instagram Story",
    "YouTube Thumbnail",
    "Wide Banner",
    "Leaderboard",
    "Medium Rectangle",
)


def build(engine_label: str) -> dict[str, Any]:
    """Render every sample in every format into `OUT` and return the catalogue."""
    OUT.mkdir(parents=True, exist_ok=True)
    formats = [
        {"key": f"{w}x{h}", "name": name, "width": w, "height": h}
        for name in FORMATS
        for w, h in [CATALOG[name]]
    ]
    written: set[Path] = set()
    samples = []
    for path in sorted((ROOT / "input").glob("*.json")):
        task = json.loads(path.read_text())
        source, target = task["source"], task["target_canvas"]
        sample_id = path.stem.removeprefix("chameleon_sample_")
        source_png = OUT / f"{sample_id}_source.png"
        render_design_to_png(source, ASSETS, source_png)
        written.add(source_png)
        renders = {}
        for fmt in formats:
            canvas = {"width": fmt["width"], "height": fmt["height"]}
            design = resize(source, canvas)
            violations = structural(source, canvas, design)
            if violations:
                raise SystemExit(f"{sample_id} at {fmt['key']} breaks the hard rules: {violations}")
            output_png = OUT / f"{sample_id}_{fmt['key']}.png"
            warnings = render_design_to_png(design, ASSETS, output_png)
            written.add(output_png)
            renders[fmt["key"]] = output_png.name
            for warning in warnings:
                print(f"{output_png.name}: {warning}")
        samples.append({
            "id": sample_id,
            # The organizers' labels separate level and name with an em dash; show a colon instead.
            "label": task.get("difficulty_label", sample_id).replace(" \u2014 ", ": "),
            "width": source["canvas"]["width"],
            "height": source["canvas"]["height"],
            "elements": len(source["elements"]),
            "target": f"{target['width']}x{target['height']}",
            "source": source_png.name,
            "renders": renders,
        })
    # Drop PNGs from earlier runs that this catalogue no longer lists, so the bundle ships only what the panel offers.
    for stale in set(OUT.glob("*.png")) - written:
        stale.unlink()
    catalog = {"engine": engine_label, "formats": formats, "samples": samples}
    (OUT / "index.json").write_text(json.dumps(catalog, indent=2) + "\n")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the viewer add-on's bundled previews.")
    parser.add_argument("--engine-label", required=True,
                        help="engine version shown in the panel, e.g. the output of `git describe --tags`")
    args = parser.parse_args()
    catalog = build(args.engine_label)
    count = sum(len(s["renders"]) + 1 for s in catalog["samples"])
    print(f"wrote {count} PNGs and index.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
