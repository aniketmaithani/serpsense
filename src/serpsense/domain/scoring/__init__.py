"""Scores: how a brand looks on each surface, its health and its crisis level (BUILD_PLAN §11).

`surfaces` scores each surface and the brand's health; `crisis` measures change against the
brand's usual. Weights and thresholds are scoring version `s1`; the database holds the same
numbers as reference rows (docs/scoring.md has every formula).
"""

VERSION = "s1"
