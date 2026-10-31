from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from renderer.converter.convert import _get_renderer, convert_chameleon_to_crello

# =============================================================================
# Module Overview
# =============================================================================
# Stage-one implementation of the core's `Renderer` port: draws a finished
# design with the official renderer and the brand-kit fonts and assets, the
# same way `tools/checks.render` and the scorer do. Used only by the optional
# model harness. The renderer and the measurer caches are not thread-safe, so
# the harness calls this from the calling thread only. Repeated renders of
# one design are byte-identical.


class OfficialRenderer:
    """PNG bytes of a design, drawn by `renderer/` exactly as the scorer will draw it."""

    def __init__(self, root: Path) -> None:
        self._fonts = str(Path(root) / "brand_kit" / "fonts")
        self._assets = str(Path(root) / "brand_kit" / "assets")

    def render(self, design: dict[str, Any], max_size: int) -> bytes:
        """`design` drawn with its long side scaled down to at most `max_size` pixels."""
        renderer = _get_renderer(self._fonts)
        # A copy, so nothing the converter might set can reach the design that ships.
        example, _warnings = convert_chameleon_to_crello(copy.deepcopy(design), renderer.font_manager, self._assets)
        return renderer.render(example, max_size=max_size, format="png")
