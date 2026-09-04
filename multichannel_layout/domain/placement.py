"""Pure footprint placement planning."""

from __future__ import annotations

from .matching import Match
from .models import (
    BoardTransform,
    Channel,
    Footprint,
)
from .transforms import calculate_transform


MatchedFootprints = tuple[Match, Footprint, Footprint]


def resolve_matched_footprints(
    source: Channel,
    target: Channel,
    pairs: list[Match],
) -> list[MatchedFootprints]:
    resolved: list[MatchedFootprints] = []
    for pair in pairs:
        if not pair.is_matched:
            continue
        source_fp = source.fp_by_ref.get(pair.from_ref)
        target_fp = target.fp_by_ref.get(pair.to_ref)
        if source_fp is not None and target_fp is not None:
            resolved.append((pair, source_fp, target_fp))
    return resolved


def source_center(matches: list[MatchedFootprints]) -> tuple[int, int]:
    if not matches:
        raise ValueError("at least one footprint match is required")
    return (
        sum(source_fp.x for _, source_fp, _ in matches) // len(matches),
        sum(source_fp.y for _, source_fp, _ in matches) // len(matches),
    )


def calculate_match_transforms(
    matches: list[MatchedFootprints],
    center: tuple[int, int],
) -> list[BoardTransform]:
    return [
        calculate_transform(
            source_fp.position,
            source_fp.orientation,
            target_fp.position,
            target_fp.orientation,
            center,
        )
        for _, source_fp, target_fp in matches
    ]
