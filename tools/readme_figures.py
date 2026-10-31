from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

# =============================================================================
# Module Overview
# =============================================================================
# Draws the figures in the submission README. `diagrams` writes the
# pipeline and ports diagrams as plain SVG, and `results` composes one design's
# bench renders at several canvas sizes into `docs/assets/resize-examples.jpg`.
# Run `python -m tools.readme_figures --help` for the options.

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"

# GitHub shows a README about 830 px wide, so a 960-unit figure draws 15-unit text at about 13 px.
WIDTH = 960
FONT = "-apple-system, Segoe UI, Helvetica, Arial, sans-serif"
INK = "#1f2328"
MUTED = "#57606a"
# Fill and stroke per box kind. The light page background keeps the figures readable in GitHub's dark mode.
KINDS = {
    "io": ("#eef1f4", "#8c959f"),
    "stage": ("#ddeafe", "#2f6fdb"),
    "fallback": ("#fff1dc", "#c77700"),
    "model": ("#ffffff", "#8c959f"),
    "port": ("#ffffff", "#2f6fdb"),
}


# =============================================================================
# SVG diagrams
# =============================================================================

@dataclass(frozen=True)
class Box:
    """A labelled rounded box: a bold title and a few plain lines under it."""

    x: float
    y: float
    w: float
    h: float
    title: str
    lines: tuple[str, ...] = ()
    kind: str = "stage"
    dashed: bool = False

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h


def _svg(height: float, body: list[str], label: str) -> str:
    """A complete SVG document of `WIDTH` by `height` with an opaque rounded page behind `body`."""
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(label)}">',
        "<defs>",
        *(f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
          f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
          for name, color in (("ink", MUTED), ("fallback", KINDS["fallback"][1]))),
        "</defs>",
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{height - 2}" rx="16" fill="#ffffff" stroke="#d0d7de"/>',
        f'<g font-family="{FONT}" fill="{INK}">',
        *body,
        "</g>",
        "</svg>",
        "",
    ])


def _box(b: Box) -> str:
    """One box with its title and lines centred horizontally."""
    fill, stroke = KINDS[b.kind]
    dash = ' stroke-dasharray="6 5"' if b.dashed else ""
    parts = [f'<rect x="{b.x}" y="{b.y}" width="{b.w}" height="{b.h}" rx="10" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="1.5"{dash}/>']
    # Title plus lines, centred vertically as one block: 22 units for the title row, 20 per line.
    block = 22 + 20 * len(b.lines)
    y = b.y + (b.h - block) / 2 + 16
    parts.append(f'<text x="{b.cx}" y="{y}" text-anchor="middle" font-size="17" font-weight="600">'
                 f'{escape(b.title)}</text>')
    for i, line in enumerate(b.lines):
        parts.append(f'<text x="{b.cx}" y="{y + 22 + 20 * i}" text-anchor="middle" font-size="15" '
                     f'fill="{MUTED}">{escape(line)}</text>')
    return "\n".join(parts)


def _line(points: list[tuple[float, float]], kind: str = "ink", dashed: bool = False) -> str:
    """An arrow along `points`, with its head on the last point."""
    color = KINDS["fallback"][1] if kind == "fallback" else MUTED
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    path = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(points))
    return (f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"{dash} '
            f'marker-end="url(#arrow-{kind})"/>')


def _row(y: float, h: float, boxes: list[tuple[str, tuple[str, ...], str]], gap: float = 40) -> list[Box]:
    """Equal-width boxes across the page at height `y`, left to right."""
    margin = 24
    w = (WIDTH - 2 * margin - gap * (len(boxes) - 1)) / len(boxes)
    return [Box(margin + i * (w + gap), y, w, h, title, lines, kind)
            for i, (title, lines, kind) in enumerate(boxes)]


def pipeline_svg() -> str:
    """The engine's stages from `engine/core/pipeline.py`, its fallbacks and the optional model."""
    top = _row(28, 112, [
        ("Input", ("source design JSON", "target canvas size"), "io"),
        ("1  Understand", ("roles from geometry,", "type and style,", "never from ids"), "stage"),
        ("2  Propose", ("candidate layouts", "for the canvas shape"), "stage"),
        ("3  Size and place", ("text measured with", "the renderer's fonts"), "stage"),
    ])
    mid = _row(212, 112, [
        ("4  Score", ("coded critic, a check", "per rubric row,", "nothing rendered"), "stage"),
        ("5  Pick and repair", ("best candidate,", "margins matched,", "text kept on canvas"), "stage"),
        ("6  Validate", ("every id once, inside", "the canvas, aspect", "kept, integer sizes"), "stage"),
        ("Output", ("the same elements,", "re-laid-out"), "io"),
    ])
    pick = mid[1]
    model = Box(24, 404, 300, 104, "Optional model, ships off",
                ("may choose between finished", "layouts, fixed answers only,", "timeout and coded default"),
                "model", dashed=True)
    fallbacks = [
        Box(364 + i * 196, 404, 176, 104, title, lines, "fallback")
        for i, (title, lines) in enumerate((
            ("Packed layout", ("if copy still", "collides")),
            ("Letterbox", ("if the engine", "raises")),
            ("Emergency layout", ("if the letterbox", "fails")),
        ))
    ]
    body = [_box(b) for b in top + mid + [model] + fallbacks]
    body += [_line([(a.x + a.w + 2, a.y + a.h / 2), (b.x - 2, b.y + b.h / 2)]) for a, b in zip(top, top[1:])]
    body += [_line([(a.x + a.w + 2, a.y + a.h / 2), (b.x - 2, b.y + b.h / 2)]) for a, b in zip(mid, mid[1:])]
    # Row 1 wraps to row 2: down from Size and place, back along the gap, down into Score.
    turn = (top[-1].bottom + mid[0].y) / 2
    body.append(_line([(top[-1].cx, top[-1].bottom + 2), (top[-1].cx, turn), (mid[0].cx, turn),
                       (mid[0].cx, mid[0].y - 2)]))
    body.append(_line([(pick.x + 40, model.y - 2), (pick.x + 40, pick.bottom + 2)], dashed=True))
    body.append(_line([(fallbacks[0].cx, pick.bottom + 2), (fallbacks[0].cx, fallbacks[0].y - 2)],
                      kind="fallback", dashed=True))
    body.append(f'<text x="{fallbacks[1].x}" y="{fallbacks[1].y - 12}" font-size="15" fill="{MUTED}">'
                f'Every fallback keeps all ids on the canvas</text>')
    return _svg(532, body, "The resize pipeline: understand, propose, size, score, pick and repair, validate")


def ports_svg() -> str:
    """The core and its two ports, with the stage-one adapter and the planned Express one."""
    stage_one = Box(24, 28, 262, 168, "Stage one, this entry",
                    ("engine/adapters/stage_one.py", "official renderer fonts", "and wrap, brand_kit/ pixels"),
                    "io")
    core = Box(326, 28, 308, 168, "Engine core, engine/core/",
               ("schema dicts in and out", "no file access", "no renderer imports",
                "ports: TextMeasurer, AssetProbe"),
               "stage")
    express = Box(674, 28, 262, 168, "Express add-on, planned",
                  ("Express measures the text", "and supplies the assets,", "same engine core"),
                  "model", dashed=True)
    body = [_box(b) for b in (stage_one, core, express)]
    mid_y = core.y + core.h / 2
    body.append(_line([(stage_one.x + stage_one.w + 2, mid_y), (core.x - 2, mid_y)]))
    body.append(_line([(express.x - 2, mid_y), (core.x + core.w + 2, mid_y)], dashed=True))
    return _svg(224, body, "Ports and adapters: one engine core, a stage-one adapter and a planned Express adapter")


# =============================================================================
# Results image
# =============================================================================

# (run, task, label): Level 2, the official 1080x1080 sample, at five very different canvases.
PANELS = (
    ("dev", "official_level2_core_source", "Source, 1080x1080"),
    ("formats", "level2__poster_18x24_output", "Poster, 1800x2400"),
    ("formats", "level2__large_rectangle_output", "Ad, 336x280"),
    ("dev", "official_level2_core_output", "Banner, 1600x400"),
    ("formats", "level2__full_hd_output", "Full HD, 1920x1080"),
    ("formats", "level2__super_leaderboard_output", "Strip, 970x90"),
)
# Panel heights per row in output pixels; the image is drawn at twice its displayed size.
ROWS = ((0, 1, 2), (3, 4), (5,))
ROW_HEIGHTS = (560, 276, 150)
PAD, GAP, LABEL = 32, 36, 44


def results_image(dev: Path, formats: Path, out: Path) -> None:
    """Compose `PANELS` from the two bench runs' renders into one labelled JPEG at `out`."""
    from PIL import Image, ImageDraw, ImageFont  # Pillow is only needed for this command

    runs = {"dev": dev, "formats": formats}
    images = []
    for run, task, _label in PANELS:
        path = runs[run] / "renders" / f"{task}.png"
        if not path.is_file():
            raise SystemExit(f"{path} is missing; run `tools.bench` on that split first")
        images.append(Image.open(path).convert("RGB"))
    font = ImageFont.truetype(str(ROOT / "brand_kit" / "fonts" / "SolsticeSans-Bold.ttf"), 26)
    rows = []
    for indices, height in zip(ROWS, ROW_HEIGHTS):
        scaled = [images[i].resize((round(images[i].width * height / images[i].height), height),
                                   Image.Resampling.LANCZOS) for i in indices]
        rows.append((indices, scaled))
    width = max(sum(im.width for im in scaled) + GAP * (len(scaled) - 1) for _, scaled in rows) + 2 * PAD
    height = sum(LABEL + h for h in ROW_HEIGHTS) + GAP * (len(ROWS) - 1) + 2 * PAD
    sheet = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(sheet)
    y = PAD
    for indices, scaled in rows:
        x = PAD
        for i, im in zip(indices, scaled):
            draw.text((x, y), PANELS[i][2], font=font, fill=INK)
            sheet.paste(im, (x, y + LABEL))
            draw.rectangle((x - 1, y + LABEL - 1, x + im.width, y + LABEL + im.height), outline="#d0d7de")
            x += im.width + GAP
        y += LABEL + scaled[0].height + GAP
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=86, optimize=True, progressive=True)
    print(f"wrote {out}, {width}x{height}, {out.stat().st_size:,} bytes")


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Draw the figures in the submission README.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagrams", help="write docs/assets/pipeline.svg and docs/assets/ports.svg")
    res = sub.add_parser("results", help="write docs/assets/resize-examples.jpg from bench renders")
    res.add_argument("--dev", type=Path, default=Path("output/bench/final-dev"), help="bench run of the dev split")
    res.add_argument("--formats", type=Path, default=Path("output/bench/final-formats"),
                     help="bench run of the formats split")
    args = parser.parse_args()
    if args.command == "diagrams":
        for name, svg in (("pipeline.svg", pipeline_svg()), ("ports.svg", ports_svg())):
            (ASSETS / name).write_text(svg)
            print(f"wrote {ASSETS / name}")
    else:
        results_image(args.dev, args.formats, ASSETS / "resize-examples.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
