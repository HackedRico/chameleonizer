from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from engine.adapters.render import OfficialRenderer
from engine.adapters.vision import blank_png, prepare_png

# =============================================================================
# Module Overview
# =============================================================================
# The harness's renderer port and image preprocessing: the official renderer
# returns deterministic PNG bytes without touching the design, and
# `prepare_png` scales long canvases down and tiny banners up, then pads.

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n"


def _png(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (250, 20, 20)).save(out, format="PNG")
    return out.getvalue()


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_official_renderer_is_deterministic_and_leaves_the_design_alone():
    design = json.loads((ROOT / "input" / "chameleon_sample_level1_warmup.json").read_text())["source"]
    before = json.dumps(design, sort_keys=True)
    r = OfficialRenderer(ROOT)
    a, b = r.render(design, 512), r.render(design, 512)
    assert a.startswith(PNG) and a == b
    assert max(_size(a)) == 512
    assert json.dumps(design, sort_keys=True) == before


def test_prepare_png_pads_a_strip():
    out = prepare_png(_png(320, 50))
    assert out.startswith(PNG) and _size(out) == (1024, 224)
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((512, 2)) == (128, 128, 128) and img.getpixel((512, 112))[0] > 200


def test_prepare_png_scales_a_story_down():
    assert _size(prepare_png(_png(1080, 1920))) == (576, 1024)


def test_prepare_png_is_deterministic_and_keeps_mid_sizes():
    src = _png(600, 400)
    assert prepare_png(src) == prepare_png(src) and _size(prepare_png(src)) == (600, 400)
    assert _size(prepare_png(_png(100, 100))) == (300, 300)  # whole-number upscale, as specified
    assert _size(prepare_png(_png(320, 50), short_min=0)) == (320, 50)
    assert _size(blank_png()) == (64, 64)
