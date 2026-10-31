from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from renderer.converter.convert import _get_renderer, convert_chameleon_to_crello

# =============================================================================
# Module Overview
# =============================================================================
# Test-bench checks, stricter than `engine/main.py`'s self-check. `structural`
# lists hard-rule violations in an output design; `render` draws a design with
# the official renderer; `pixel_metrics` measures text contrast and background
# busyness on the backdrop each text element actually sits on.

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "brand_kit" / "assets"
FONTS = ROOT / "brand_kit" / "fonts"
REQUIRED = {"text": ("content", "font_family", "font_size", "color"), "image": ("asset_id",),
            "shape": ("shape_type", "fill_color")}
FROZEN = ("type", "content", "font_family", "asset_id", "shape_type")


def structural(source: dict[str, Any], target: dict[str, Any], out: dict[str, Any]) -> list[str]:
    """Every hard-rule violation in `out`, empty when the design is valid."""
    problems = []
    if out.get("canvas") != {"width": target["width"], "height": target["height"]}:
        problems.append("canvas does not match target")
    src = {el["id"]: el for el in source["elements"]}
    ids = [el.get("id") for el in out.get("elements", [])]
    if sorted(ids) != sorted(src):
        problems.append("element ids differ from source")
    W, H = target["width"], target["height"]
    for el in out.get("elements", []):
        s = src.get(el.get("id"))
        if s is None:
            continue
        for field in ("id", "type", "x", "y", "width", "height") + REQUIRED.get(el["type"], ()):
            if field not in el:
                problems.append(f"{el['id']}: missing {field}")
        for field in FROZEN:
            if s.get(field) != el.get(field):
                problems.append(f"{el['id']}: changed {field}")
        if el["x"] < -0.5 or el["y"] < -0.5 or el["x"] + el["width"] > W + 0.5 or el["y"] + el["height"] > H + 0.5:
            problems.append(f"{el['id']}: off-canvas")
        if el["type"] == "text" and float(el["font_size"]) != int(el["font_size"]):
            problems.append(f"{el['id']}: fractional font_size")
        if el["type"] == "image" and not _full_bleed_cover(el, s, source["canvas"]) and el["height"] > 0:
            ratio = (el["width"] / el["height"]) / (s["width"] / s["height"])
            if abs(ratio - 1) > 0.05:
                problems.append(f"{el['id']}: aspect drift {ratio:.3f}")
    return problems


def _full_bleed_cover(el: dict[str, Any], s: dict[str, Any], canvas: dict[str, Any]) -> bool:
    """True for the one image the 5% aspect rule exempts: a `cover` backdrop whose source box
    reaches all four canvas edges. A `contain` strip or a framed `cover` photo keeps its shape."""
    sx, sy, sw, sh = s["x"], s["y"], s["width"], s["height"]
    bleeds = sx <= 0.5 and sy <= 0.5 and sx + sw >= canvas["width"] - 0.5 and sy + sh >= canvas["height"] - 0.5
    return el.get("fit") == "cover" and bleeds


def render(design: dict[str, Any], max_size: int = 2048) -> Image.Image:
    """Draw `design` with the official renderer, returning an RGB image."""
    renderer = _get_renderer(str(FONTS))
    example, _warnings = convert_chameleon_to_crello(design, renderer.font_manager, str(ASSETS))
    data = renderer.render(example, max_size=max_size, format="png")
    return Image.open(io.BytesIO(data)).convert("RGB")


def _luminance(rgb: np.ndarray) -> np.ndarray:
    """WCAG relative luminance of sRGB values in 0..255."""
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _rgba(color: str) -> tuple[float, float, float, float]:
    nums = [float(n) for n in re.findall(r"[\d.]+", color or "")]
    return (nums + [0, 0, 0, 1][len(nums):])[:4] if nums else (0.0, 0.0, 0.0, 1.0)


def pixel_metrics(design: dict[str, Any]) -> dict[str, float]:
    """Worst WCAG contrast and worst backdrop busyness under any visible text."""
    floor = min((int(e.get("z_index", 0)) for e in design["elements"] if e["type"] == "image"
                 and e["x"] <= 0 and e["y"] <= 0), default=-10**9)
    texts = [e for e in design["elements"] if e["type"] == "text" and int(e.get("z_index", 0)) >= floor]
    if not texts:
        return {"min_contrast": 21.0, "max_busy": 0.0}
    backdrop_design = {**design, "elements": [e for e in design["elements"] if e["type"] != "text"]}
    img = np.asarray(render(backdrop_design)).astype(np.float64)
    scale = img.shape[1] / float(design["canvas"]["width"])
    lum = _luminance(img)
    gy, gx = np.gradient(img.mean(axis=2))
    grad = np.hypot(gx, gy) / 255.0
    worst_contrast, worst_busy = 21.0, 0.0
    for el in texts:
        x0, y0 = int(max(0, el["x"] * scale)), int(max(0, el["y"] * scale))
        x1 = int(min(img.shape[1], (el["x"] + el["width"]) * scale))
        y1 = int(min(img.shape[0], (el["y"] + el["height"]) * scale))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        r, g, b, a = _rgba(el.get("color", ""))
        under = lum[y0:y1, x0:x1]
        bg_mean = img[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)
        # Blend semi-transparent text over the average backdrop, as the eye would see it.
        text_rgb = np.array([r, g, b]) * a + bg_mean * (1 - a)
        lt = float(_luminance(text_rgb[None, None, :])[0, 0])
        for lb in (np.percentile(under, 10), np.percentile(under, 90)):
            hi, lo = max(lt, lb), min(lt, lb)
            worst_contrast = min(worst_contrast, (hi + 0.05) / (lo + 0.05))
        worst_busy = max(worst_busy, float(grad[y0:y1, x0:x1].mean()))
    return {"min_contrast": round(worst_contrast, 2), "max_busy": round(worst_busy, 4)}
