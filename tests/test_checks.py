from __future__ import annotations

import pytest

from tools.checks import structural

# =============================================================================
# Module Overview
# =============================================================================
# Tests for the test bench's own hard-rule check. The 5% image-aspect rule
# exempts only a full-bleed `cover` backdrop, so a stretched strip or framed
# photo must be reported whatever its fit or position.

TARGET = {"width": 1600, "height": 400}


def _image(box: tuple[float, float, float, float], fit: str) -> dict:
    x, y, w, h = box
    return {"id": "img", "type": "image", "x": x, "y": y, "width": w, "height": h,
            "asset_id": "solstice_bg_gradient.png", "fit": fit}


def _stretched(src_box: tuple[float, float, float, float], fit: str) -> list[str]:
    """Problems with the source image stretched to fill the whole 1600x400 target."""
    source = {"canvas": {"width": 1080, "height": 1080}, "elements": [_image(src_box, fit)]}
    out = {"canvas": dict(TARGET), "elements": [_image((0, 0, 1600, 400), fit)]}
    return structural(source, TARGET, out)


@pytest.mark.parametrize("src_box, fit", [
    ((0, 0, 1080, 200), "contain"),  # header strip at the top-left that spans the canvas width
    ((0, 0, 1080, 200), "cover"),  # same strip as `cover`: it does not reach the bottom edge
    ((100, 100, 500, 500), "cover"),  # framed `cover` photo inside the canvas
    ((0, 0, 1080, 1080), "contain"),  # full-bleed but `contain`
])
def test_stretched_image_is_reported(src_box, fit):
    assert any("aspect drift" in p for p in _stretched(src_box, fit))


@pytest.mark.parametrize("src_box", [(0, 0, 1080, 1080), (-20, -20, 1120, 1120), (0.4, 0.4, 1079.2, 1079.2)])
def test_full_bleed_cover_backdrop_may_crop(src_box):
    assert _stretched(src_box, "cover") == []
