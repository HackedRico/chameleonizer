from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import skia  # type: ignore
from PIL import Image, ImageChops, ImageFilter, ImageStat

from engine.core.ports import TextLayout
from engine.core.types import Box
from renderer.converter.brand_fonts import load_brand_font_manager
from renderer.converter.wrap import wrap_text

# =============================================================================
# Module Overview
# =============================================================================
# Stage-one implementations of the core's ports. `SkiaTextMeasurer` wraps text
# with the same fonts, skia metrics and greedy wrap the official renderer uses,
# and draws it the same way to see where hinting closes up word spaces;
# `FileAssetProbe` reads brand-kit images to find their visible pixels and
# how busy each region of a backdrop is.


# Wraps kept before the layout cache starts over; a few hundred bytes each.
_LAYOUT_CACHE = 200_000


class SkiaTextMeasurer:
    """Measures text the way `renderer/` will draw it, using the brand-kit fonts."""

    def __init__(self, fonts_dir: str | Path) -> None:
        self._fonts = load_brand_font_manager(str(fonts_dir))
        self._typefaces: dict[tuple[str, bool, bool], skia.Typeface] = {}
        self._gaps: dict[tuple[str, str, bool, bool, int], tuple[tuple[float, float], ...]] = {}
        self._rows: dict[tuple, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._layouts: dict[tuple[str, str, bool, bool, int, float], TextLayout] = {}
        self._inks: dict[tuple[str, str, bool, bool, int], tuple[float, float]] = {}

    def _typeface(self, family: str, bold: bool, italic: bool) -> skia.Typeface:
        key = (family, bold, italic)
        if key not in self._typefaces:
            # Mirror the converter's weight/style derivation so measured and drawn faces match.
            style = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
            data = self._fonts.lookup(family, "bold" if bold else "regular", style)
            self._typefaces[key] = skia.Typeface.MakeFromData(data)
        return self._typefaces[key]

    def layout(
        self, text: str, family: str, bold: bool, italic: bool, size: int, max_width: float
    ) -> TextLayout:
        """Wrap `text` at `max_width` and return each line's drawn width."""
        if size < 1:
            raise ValueError("size must be at least 1")
        if not text:
            return TextLayout(lines=("",), widths=(0.0,), longest_word=0.0)
        key = (text, family, bold, italic, size, max_width)
        hit = self._layouts.get(key)
        if hit is None:
            # Stacks search scales and widths, so the same wrap is asked for many times over.
            if len(self._layouts) >= _LAYOUT_CACHE:
                self._layouts.clear()
            hit = self._layouts[key] = self._layout(text, family, bold, italic, size, max_width)
        return hit

    def _layout(self, text: str, family: str, bold: bool, italic: bool, size: int, max_width: float) -> TextLayout:
        font = skia.Font(self._typeface(family, bold, italic), size)
        widths = list(font.getWidths(font.textToGlyphs(text)))
        wrapped = wrap_text(text, widths, max_width)
        lines, line_widths, start = [], [], 0
        for i, ch in enumerate(wrapped + "\n"):
            if ch == "\n":
                lines.append(wrapped[start:i])
                line_widths.append(sum(widths[start:i]))
                start = i + 1
        longest, run = 0.0, 0.0
        for ch, w in zip(text, widths):
            run = 0.0 if ch in " \n" else run + w  # the renderer breaks lines at "\n" too
            longest = max(longest, run)
        return TextLayout(lines=tuple(lines), widths=tuple(line_widths), longest_word=longest)

    def ink(self, line: str, family: str, bold: bool, italic: bool, size: int) -> tuple[float, float]:
        """Drawn glyph extent of `line`, down from the line top, the way `renderer/` places it."""
        if size < 1:
            raise ValueError("size must be at least 1")
        key = (line, family, bold, italic, size)
        if key not in self._inks:
            if len(self._inks) >= _LAYOUT_CACHE:
                self._inks.clear()
            self._inks[key] = self._ink(line, family, bold, italic, size)
        return self._inks[key]

    def _ink(self, line: str, family: str, bold: bool, italic: bool, size: int) -> tuple[float, float]:
        font = skia.Font(self._typeface(family, bold, italic), size)
        baseline = -font.getMetrics().fAscent
        bounds = [r for r in font.getBounds(font.textToGlyphs(line)) if r.height() > 0]
        if not bounds:
            return baseline, baseline
        return baseline + min(r.top() for r in bounds), baseline + max(r.bottom() for r in bounds)

    def word_gaps(
        self, text: str, family: str, bold: bool, italic: bool, size: int
    ) -> tuple[tuple[float, float], ...]:
        """Word and letter gaps read off the glyphs drawn where the renderer draws them.

        A gap is the narrowest clear run from one glyph's ink to the next glyph's, comparing each
        row with the rows near it, so overhangs count and italic slants do not."""
        if size < 1:
            raise ValueError("size must be at least 1")
        key = (text, family, bold, italic, size)
        if key not in self._gaps:
            self._gaps[key] = self._measure_gaps(text.replace("\n", " "), family, bold, italic, size)
        return self._gaps[key]

    def _measure_gaps(self, text: str, family: str, bold: bool, italic: bool, size: int) -> tuple[tuple[float, float], ...]:
        font = skia.Font(self._typeface(family, bold, italic), size)
        glyphs = font.textToGlyphs(text)
        if " " not in text.strip() or len(glyphs) != len(text):
            return ()
        xs = list(font.getXPos(glyphs))

        def rows(i: int) -> tuple[int, np.ndarray, np.ndarray, np.ndarray]:
            origin = math.floor(xs[i])
            # Quarter-pixel offsets are as fine as a renderer's subpixel glyph positions get.
            frac = round((xs[i] - origin) * 4) / 4
            key = (family, bold, italic, glyphs[i], size, frac)
            if key not in self._rows:
                self._rows[key] = _glyph_rows(font, glyphs[i], frac)
            return (origin, *self._rows[key])

        reach = max(1, round(_GAP_REACH * size))

        def inked(i: int) -> bool:
            return bool(rows(i)[1].any())

        def gap(a: int, b: int) -> float:
            xa, inked_a, _left_a, right_a = rows(a)
            xb, inked_b, left_b, _right_b = rows(b)
            # The left glyph's right edge, widened to its furthest reach within a band of rows, so a
            # bowl beside a diagonal counts as close as it looks while an italic slant does not.
            edge = np.where(inked_a, right_a, -10**6)
            near = np.full(edge.shape, -10**6)
            for shift in range(-reach, reach + 1):
                near = np.maximum(near, np.roll(edge, shift) if shift else edge)
            rows_b = inked_b & (near > -10**6)
            if not rows_b.any():
                return float((xb + left_b[inked_b].min()) - (xa + right_a[inked_a].max() + 1))
            return float(((xb + left_b) - (xa + near + 1))[rows_b].min())

        out = []
        for k, ch in enumerate(text):
            if ch != " " or k == 0 or k == len(text) - 1 or text[k - 1] == " " or text[k + 1] == " ":
                continue
            lo = text.rfind(" ", 0, k) + 1
            hi = text.find(" ", k + 1)
            hi = len(text) if hi < 0 else hi
            # A mark too faint to leave ink, such as a period at a few pixels, does not bound the space.
            left = next((i for i in range(k - 1, lo - 1, -1) if inked(i)), None)
            right = next((i for i in range(k + 1, hi) if inked(i)), None)
            if left is None or right is None:
                # Too small to leave any ink: nothing there reads, spaces included.
                out.append((0.0, 0.0))
                continue
            letters = [gap(i, i + 1) for i in range(lo, hi - 1)
                       if i != k and i + 1 != k and text[i].isalnum() and text[i + 1].isalnum()
                       and inked(i) and inked(i + 1)]
            out.append((gap(left, right), max(letters, default=0.0)))
        return tuple(out)


# A pixel with alpha at or below this reads as clear background between glyphs.
_INK_ALPHA = 64
# Rows, as a share of the font size, over which the eye compares two glyphs' facing edges.
_GAP_REACH = 0.3


def _glyph_rows(font: skia.Font, glyph: int, frac: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pixel row of `glyph` drawn at fractional offset `frac`: whether the row has ink, and
    its leftmost and rightmost inked column, relative to the glyph's whole-pixel origin."""
    size = int(math.ceil(font.getSize()))
    pad = 2 * size + 2
    builder = skia.TextBlobBuilder()
    builder.allocRunPosH(font, [glyph], [0.0], 0)
    blob = builder.make()
    surface = skia.Surface(5 * size + 2 * pad, 3 * size + 4)
    with surface as canvas:
        canvas.clear(skia.ColorTRANSPARENT)
        paint = skia.Paint(AntiAlias=True, Color=skia.ColorBLACK)
        canvas.drawTextBlob(blob, pad + frac, size + 2 - font.getMetrics().fAscent, paint)
    ink = surface.makeImageSnapshot().toarray()[:, :, 3] > _INK_ALPHA
    width = ink.shape[1]
    left = ink.argmax(axis=1) - pad
    right = width - 1 - ink[:, ::-1].argmax(axis=1) - pad
    return ink.any(axis=1), left, right


class FileAssetProbe:
    """Answers asset questions from files in a brand-kit `assets/` folder."""

    def __init__(self, assets_dir: str | Path) -> None:
        self._dir = Path(assets_dir)

    def exists(self, asset_id: str) -> bool:
        return bool(asset_id) and (self._dir / asset_id).is_file()

    def visible_box(self, asset_id: str) -> Box | None:
        return _visible_box(str(self._dir / asset_id)) if self.exists(asset_id) else None

    def aspect(self, asset_id: str) -> float | None:
        return _aspect(str(self._dir / asset_id)) if self.exists(asset_id) else None

    def busyness(self, asset_id: str, region: Box) -> float | None:
        if not self.exists(asset_id):
            return None
        mask = _detail_mask(str(self._dir / asset_id))
        w, h = mask.size
        x0, y0 = max(0, int(region.x * w)), max(0, int(region.y * h))
        x1, y1 = min(w, int(math.ceil(region.x1 * w))), min(h, int(math.ceil(region.y1 * h)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return ImageStat.Stat(mask.crop((x0, y0, x1, y1))).mean[0] / 255.0


@lru_cache(maxsize=8)
def _detail_mask(path: str) -> Image.Image:
    """Mask of pixels that stand out from their smoothed surroundings: dots, sparkles, texture."""
    with Image.open(path) as im:
        grey = im.convert("L")
    # A blur about 1% of the image wide keeps gradients and vignettes out of the mask.
    blurred = grey.filter(ImageFilter.GaussianBlur(max(2, round(0.01 * min(grey.size)))))
    return ImageChops.difference(grey, blurred).point(lambda v: 255 if v > 8 else 0)


@lru_cache(maxsize=64)
def _visible_box(path: str) -> Box | None:
    """Fractional bounds of the non-transparent pixels in the image at `path`."""
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        w, h = rgba.size
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return Box(x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)


@lru_cache(maxsize=64)
def _aspect(path: str) -> float:
    """Pixel width over height of the image at `path`."""
    with Image.open(path) as im:
        w, h = im.size
    return w / h


def default_ports(root: str | Path = ".") -> tuple[SkiaTextMeasurer, FileAssetProbe]:
    """Measurer and probe wired to the repo's `brand_kit/`, as the scorer lays it out."""
    root = Path(root)
    return SkiaTextMeasurer(root / "brand_kit" / "fonts"), FileAssetProbe(root / "brand_kit" / "assets")
