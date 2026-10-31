from __future__ import annotations

import pytest

from tools.judge import resolve_pair

# =============================================================================
# Module Overview
# =============================================================================
# Offline checks of the pairwise judge's decision rule: a verdict only counts
# when it survives swapping the image order, which cancels position bias.


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("B", "A", "new"),   # new wins whether shown second or first
        ("A", "B", "base"),  # base wins in both orders
        ("A", "A", "tie"),   # always picks the first image: position bias
        ("B", "B", "tie"),   # always picks the second image: position bias
        ("tie", "A", "tie"),
        ("tie", "tie", "tie"),
    ],
)
def test_resolve_pair(first, second, expected):
    assert resolve_pair(first, second) == expected
