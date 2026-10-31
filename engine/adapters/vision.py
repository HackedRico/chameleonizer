from __future__ import annotations

import io
import math

from PIL import Image

# =============================================================================
# Module Overview
# =============================================================================
# The one image preprocessing step between a render and a vision model. Every
# model adapter and `tools/agree.py` call `prepare_png`, so the engine and the
# agreement benchmark show a model the same pixels. Long canvases are scaled
# down to the model's input cap; tiny banners are scaled up, then padded, so
# their short side reaches the size a vision encoder can see.

# Grey padding: neutral against both light and dark designs, and plainly not part of the design.
_PAD = (128, 128, 128)


def prepare_png(png: bytes, long_max: int = 1024, short_min: int = 224) -> bytes:
    """`png` as RGB, scaled so its long side is at most `long_max` and, where that allows, its short
    side at least `short_min`, padded centred to `short_min` if still short. Deterministic."""
    with Image.open(io.BytesIO(png)) as im:
        img = im.convert("RGB")
    w, h = img.size
    long_side, short_side = max(w, h), max(1, min(w, h))
    grow = max(1, math.ceil(short_min / short_side)) if short_min > 0 else 1
    s = min(long_max / long_side, grow)
    if s != 1:
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    w, h = img.size
    if short_min > 0 and min(w, h) < short_min:
        pw, ph = (w, short_min) if w >= h else (short_min, h)
        canvas = Image.new("RGB", (pw, ph), _PAD)
        canvas.paste(img, ((pw - w) // 2, (ph - h) // 2))
        img = canvas
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


def blank_png(w: int = 64, h: int = 64) -> bytes:
    """A plain white PNG: the warmup image that loads a model's vision projector."""
    out = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(out, format="PNG")
    return out.getvalue()
