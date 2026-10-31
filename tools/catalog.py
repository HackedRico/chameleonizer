from __future__ import annotations

# =============================================================================
# Module Overview
# =============================================================================
# The catalogue of real-world target formats the bench draws from. The brief
# promises hidden targets "look like real platform formats", so testing across
# these sizes is the closest stand-in for the unseen scoring set.

FORMATS: dict[str, tuple[int, int]] = {
    "Instagram Post": (1080, 1080),
    "Instagram Portrait": (1080, 1350),
    "Instagram Story": (1080, 1920),
    "Facebook Post": (1200, 630),
    "Facebook Cover": (820, 312),
    "X Post": (1600, 900),
    "X Header": (1500, 500),
    "LinkedIn Post": (1200, 627),
    "LinkedIn Banner": (1584, 396),
    "YouTube Thumbnail": (1280, 720),
    "Pinterest Pin": (1000, 1500),
    "Leaderboard": (728, 90),
    "Medium Rectangle": (300, 250),
    "Wide Skyscraper": (160, 600),
    "Half Page": (300, 600),
    "Billboard": (970, 250),
    "Mobile Banner": (320, 50),
    "Wide Banner": (1600, 400),
}
