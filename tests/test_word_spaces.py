from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.adapters.stage_one import default_ports
from engine.core.sizing import spaced_below, spaced_size, spaces_read

# =============================================================================
# Module Overview
# =============================================================================
# Hinted renderers can draw a word space no wider than the gaps inside words,
# so small labels read as 'AWARDWINNING'. These cases pin the measurer's gap
# report and the core's rule for stepping to a size whose spaces read.

ROOT = Path(__file__).resolve().parents[1]
MEASURER, _PROBE = default_ports(ROOT)
LABEL = {"content": "AWARD WINNING", "font_family": "Solstice Sans", "font_weight": "bold"}


@dataclass(frozen=True)
class _Ctx:
    measurer: object
    keep_spaces: bool = True


class _Collapsing:
    """Reports collapsed word spaces at the sizes in `bad`, clear ones elsewhere."""

    def __init__(self, bad: set[int]) -> None:
        self.bad = bad

    def word_gaps(self, text, family, bold, italic, size):
        gap = (2.0, 2.0) if size in self.bad else (4.0, 2.0)
        return tuple(gap for _ in range(text.strip().count(" ")))


def test_measurer_reports_one_gap_per_word_space():
    assert len(MEASURER.word_gaps("Shop the Summer Line", "Solstice Sans", True, False, 12)) == 3
    assert MEASURER.word_gaps("VEGAN", "Solstice Sans", True, False, 12) == ()


def test_large_type_always_reads():
    for bold in (False, True):
        el = {**LABEL, "font_weight": "bold" if bold else "regular"}
        assert spaces_read(_Ctx(MEASURER), [el], 40)


def test_nearest_spaced_size_prefers_smaller_then_larger():
    ctx = _Ctx(_Collapsing({5, 6, 7, 11}))
    assert spaced_size(ctx, [LABEL], 11) == 10
    assert spaced_size(ctx, [LABEL], 11, lo=11) == 12
    assert spaced_size(ctx, [LABEL], 6, lo=5) == 8
    assert spaced_below(ctx, [LABEL], 8) == 4


def test_single_words_and_the_unspaced_variant_are_never_held():
    ctx = _Ctx(_Collapsing(set(range(1, 50))))
    assert spaces_read(ctx, [{**LABEL, "content": "VEGAN"}], 5)
    assert spaces_read(_Ctx(ctx.measurer, keep_spaces=False), [LABEL], 5)
    assert not spaces_read(ctx, [LABEL], 5)


def test_long_copy_may_keep_a_few_tight_spaces():
    class _OneTight(_Collapsing):
        def word_gaps(self, text, family, bold, italic, size):
            return ((2.0, 2.0),) + ((4.0, 2.0),) * (text.strip().count(" ") - 1)

    ctx = _Ctx(_OneTight(set()))
    long_copy = {**LABEL, "content": "Please drink responsibly and see the terms for the full details today."}
    assert spaces_read(ctx, [long_copy], 8)
    assert not spaces_read(ctx, [LABEL], 8)
