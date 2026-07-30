"""Iterative footprint matching using pad shape, mapped nets, and placement."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import hypot
from typing import Optional

from .board_reader import Channel, Footprint

_MIN_FOOTPRINT_SCORE = 0.95
_SCORE_EPSILON = 1e-9


@dataclass
class Match:
    from_ref: Optional[str]
    to_ref: Optional[str]
    score: float

    @property
    def is_matched(self) -> bool:
        return self.from_ref is not None and self.to_ref is not None


def _natural_key(value: str) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
        if part
    ]


def pad_pairs(a: Footprint, b: Footprint) -> list[tuple[str, str]]:
    """Pair pads by number when possible, otherwise by natural ordering."""
    if len(a.pads) != len(b.pads):
        return []
    if set(a.pads) == set(b.pads):
        return [(name, name) for name in sorted(a.pads, key=_natural_key)]
    return list(
        zip(
            sorted(a.pads, key=_natural_key),
            sorted(b.pads, key=_natural_key),
        )
    )


def _layout_scale(a: Footprint, b: Footprint) -> float:
    points = list(a.pad_offsets.values()) + list(b.pad_offsets.values())
    if not points:
        return 1.0
    width = max(x for x, _ in points) - min(x for x, _ in points)
    height = max(y for _, y in points) - min(y for _, y in points)
    return max(hypot(width, height), 1_000_000.0)


def footprint_score(a: Footprint, b: Footprint) -> float:
    """Return normalized pad compatibility, with different pad counts rejected."""
    pairs = pad_pairs(a, b)
    if len(a.pads) != len(b.pads):
        return 0.0
    if not pairs:
        return 1.0

    scale = _layout_scale(a, b)
    total = 0.0
    for a_pad, b_pad in pairs:
        number_score = 1.0 if a_pad == b_pad else 0.0
        type_score = 1.0 if a.pads[a_pad] == b.pads[b_pad] else 0.0
        ax, ay = a.pad_offsets.get(a_pad, (0, 0))
        bx, by = b.pad_offsets.get(b_pad, (0, 0))
        distance_score = max(0.0, 1.0 - hypot(ax - bx, ay - by) / scale)
        total += 0.45 * number_score + 0.20 * type_score + 0.35 * distance_score
    return total / len(pairs)


def _net_score(
    a: Footprint,
    b: Footprint,
    source: Channel,
    target: Channel,
    locked: list[tuple[str, str]],
) -> float:
    """Score agreement with locked net relationships by +/- one per pad."""
    pairs = pad_pairs(a, b)
    if not pairs or not locked:
        return 0.0

    locked_pairs = [
        (source.fp_by_ref[a_ref], target.fp_by_ref[b_ref])
        for a_ref, b_ref in locked
    ]
    score = 0.0
    per_pad = 1.0 / len(pairs)
    for a_pad, b_pad in pairs:
        a_net = a.pad_nets.get(a_pad, "")
        b_net = b.pad_nets.get(b_pad, "")
        if not a_net or not b_net:
            continue

        source_connections: set[tuple[int, str]] = set()
        target_connections: set[tuple[int, str]] = set()
        for index, (locked_a, locked_b) in enumerate(locked_pairs):
            for locked_a_pad, locked_b_pad in pad_pairs(locked_a, locked_b):
                if locked_a.pad_nets.get(locked_a_pad) == a_net:
                    source_connections.add((index, locked_a_pad))
                if locked_b.pad_nets.get(locked_b_pad) == b_net:
                    target_connections.add((index, locked_a_pad))

        if not source_connections and not target_connections:
            continue
        score += per_pad if source_connections == target_connections else -per_pad

    return max(0.0, score)


def _position_score(
    a: Footprint,
    b: Footprint,
    source: Channel,
    target: Channel,
    locked: list[tuple[str, str]],
) -> float:
    """Compare distance to locked pairs; this is only a final tiebreaker."""
    if not locked:
        source_center = source.center
        target_center = target.center
        source_distance = hypot(a.x - source_center[0], a.y - source_center[1])
        target_distance = hypot(b.x - target_center[0], b.y - target_center[1])
        denominator = max(source_distance, target_distance, 1.0)
        return max(0.0, 1.0 - abs(source_distance - target_distance) / denominator)

    scores: list[float] = []
    for source_ref, target_ref in locked:
        locked_a = source.fp_by_ref[source_ref]
        locked_b = target.fp_by_ref[target_ref]
        source_distance = hypot(a.x - locked_a.x, a.y - locked_a.y)
        target_distance = hypot(b.x - locked_b.x, b.y - locked_b.y)
        denominator = max(source_distance, target_distance, 1.0)
        scores.append(max(0.0, 1.0 - abs(source_distance - target_distance) / denominator))
    return sum(scores) / len(scores)


def _ambiguity(
    pair: tuple[str, str],
    scores: dict[tuple[str, str], float],
) -> float:
    """Return bidirectional competition relative to this candidate's net score."""
    source_ref, target_ref = pair
    score = scores[pair]
    source_alternatives = [
        value for (a_ref, b_ref), value in scores.items()
        if a_ref == source_ref and b_ref != target_ref
    ]
    target_alternatives = [
        value for (a_ref, b_ref), value in scores.items()
        if b_ref == target_ref and a_ref != source_ref
    ]

    def competition(alternatives: list[float]) -> float:
        if not alternatives:
            return 0.0
        alternative = max(alternatives)
        if score <= _SCORE_EPSILON:
            return 1.0 if alternative <= _SCORE_EPSILON else float("inf")
        return alternative / score

    source_competition = competition(source_alternatives)
    target_competition = competition(target_alternatives)
    return (source_competition + target_competition) / 2.0


def match_channels(source: Channel, target: Channel) -> list[Match]:
    """Match footprints, locking the least ambiguous eligible pair each round."""
    source_by_ref = source.fp_by_ref
    target_by_ref = target.fp_by_ref
    footprint_scores = {
        (source_ref, target_ref): footprint_score(source_fp, target_fp)
        for source_ref, source_fp in source_by_ref.items()
        for target_ref, target_fp in target_by_ref.items()
    }

    locked: list[tuple[str, str]] = []
    locked_scores: dict[tuple[str, str], float] = {}
    matched_source: set[str] = set()
    matched_target: set[str] = set()

    while True:
        candidates = {
            pair: base_score
            for pair, base_score in footprint_scores.items()
            if pair[0] not in matched_source
            and pair[1] not in matched_target
            and base_score >= _MIN_FOOTPRINT_SCORE
        }
        if not candidates:
            break

        net_scores = {
            pair: _net_score(
                source_by_ref[pair[0]],
                target_by_ref[pair[1]],
                source,
                target,
                locked,
            )
            for pair in candidates
        }
        ambiguities = {pair: _ambiguity(pair, net_scores) for pair in candidates}
        best_ambiguity = min(ambiguities.values())
        ambiguity_ties = [
            pair for pair, value in ambiguities.items()
            if abs(value - best_ambiguity) <= _SCORE_EPSILON
        ]

        position_scores = {
            pair: _position_score(
                source_by_ref[pair[0]],
                target_by_ref[pair[1]],
                source,
                target,
                locked,
            )
            for pair in ambiguity_ties
        }
        best_pair = max(
            ambiguity_ties,
            key=lambda pair: (
                position_scores[pair],
                net_scores[pair],
                candidates[pair],
                pair,
            ),
        )

        matched_source.add(best_pair[0])
        matched_target.add(best_pair[1])
        locked.append(best_pair)
        locked_scores[best_pair] = candidates[best_pair] + net_scores[best_pair]

    matches: list[Match] = []
    locked_by_source = dict(locked)
    for source_ref in source_by_ref:
        target_ref = locked_by_source.get(source_ref)
        if target_ref is None:
            matches.append(Match(source_ref, None, 0.0))
        else:
            matches.append(Match(source_ref, target_ref, locked_scores[(source_ref, target_ref)]))
    for target_ref in target_by_ref:
        if target_ref not in matched_target:
            matches.append(Match(None, target_ref, 0.0))
    return matches
