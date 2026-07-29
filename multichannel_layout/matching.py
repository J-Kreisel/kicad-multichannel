"""Iterative, ambiguity-first matching between two channels.

1. Score every source/target footprint pair (pad count, footprint id, pad
   names/types, reference prefix).
2. Lock the pair whose best candidate is most clearly better than its
   second-best candidate on both sides.
3. Re-score remaining pairs using already-locked pairs for net-topology and
   relative-placement context.
4. Stop when the best remaining pair is too ambiguous/low-score.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import hypot
from typing import Optional

from .board_reader import Channel, Footprint

_log = logging.getLogger(__name__)

# Stop threshold
_MIN_SCORE = 0.45

# Context weights added after locking pairs
_W_NET = 0.30
_W_PLACE = 0.20


@dataclass
class Match:
    from_ref: Optional[str]
    to_ref: Optional[str]
    score: float

    @property
    def is_matched(self) -> bool:
        return self.from_ref is not None and self.to_ref is not None


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


_REFDES_RE = re.compile(r"^([A-Za-z]+)(\d.*)?$")


def _split_ref(ref: str) -> tuple[str, str]:
    m = _REFDES_RE.match(ref)
    return (m.group(1), m.group(2) or "") if m else (ref, "")


def _shape_class(lib_id: str) -> str:
    if not lib_id:
        return ""
    name = lib_id.split(":")[-1]
    name = re.sub(r"_.*$", "", name)
    m = re.match(
        r"^(DIP|SOIC|TQFP|VQFP|LQFP|QFP|BGA|SOT|QFN|DFN|TO|SIP|DPAK|"
        r"TSSOP|SSOP|MSOP|VSSOP|PLCC|LLCC|CLCC|QFN-)",
        name,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    return name.split("-")[0].upper() if "-" in name else name.upper()


_SHAPE_FAMILIES = {
    "DIP": {"DIP", "SOIC", "SOP", "TSSOP", "SSOP", "MSOP", "VSSOP", "SOT23", "SOT"},
    "QFP": {"QFP", "TQFP", "VQFP", "LQFP"},
    "QFN": {"QFN", "DFN", "QFN-", "DFN-"},
}


def _same_shape_family(a: str, b: str) -> bool:
    if not a or not b:
        return True
    if a == b:
        return True
    for family in _SHAPE_FAMILIES.values():
        if a in family and b in family:
            return True
    return False


def _pad_score(a: Footprint, b: Footprint) -> float:
    if len(a.pads) != len(b.pads):
        return 0.0
    if not a.pads:
        return 1.0

    a_names = sorted(a.pads.keys(), key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])
    b_names = sorted(b.pads.keys(), key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])

    total = 0.0
    for na, nb in zip(a_names, b_names):
        name_sim = _similarity(na, nb)
        type_eq = 1.0 if a.pads[na] == b.pads[nb] else 0.0
        total += 0.5 * name_sim + 0.5 * type_eq
    return total / len(a_names)


def footprint_score(a: Footprint, b: Footprint) -> float:
    """Pairwise footprint similarity in [0, 1]."""
    if len(a.pads) != len(b.pads):
        return 0.0

    sa = _shape_class(a.footprint_id)
    sb = _shape_class(b.footprint_id)
    if not _same_shape_family(sa, sb):
        return 0.0

    score = 0.0

    if a.footprint_id and b.footprint_id:
        if a.footprint_id == b.footprint_id:
            score += 0.40
        else:
            score += 0.20 * _similarity(a.footprint_id.split(":")[-1], b.footprint_id.split(":")[-1])

    if a.pads and b.pads:
        score += 0.20 * _pad_score(a, b)

    prefix_a, suffix_a = _split_ref(a.reference)
    prefix_b, suffix_b = _split_ref(b.reference)
    if prefix_a and prefix_a == prefix_b:
        score += 0.10
        if suffix_a and suffix_a == suffix_b:
            score += 0.05

    if a.pad_nets and b.pad_nets:
        common = set(a.pad_nets.values()) & set(b.pad_nets.values())
        union = set(a.pad_nets.values()) | set(b.pad_nets.values())
        if union:
            score += 0.05 * len(common) / len(union)

    return min(score, 1.0)


def _net_context(a: Footprint, b: Footprint, source: Channel, target: Channel, locked: list[tuple[str, str]]) -> float:
    if not locked:
        return 0.0
    support = 0
    for c_ref, d_ref in locked:
        c = source.fp_by_ref[c_ref]
        d = target.fp_by_ref[d_ref]
        pads = set(a.pad_nets) & set(c.pad_nets) & set(b.pad_nets) & set(d.pad_nets)
        for pad in pads:
            if a.pad_nets[pad] == c.pad_nets[pad] and b.pad_nets[pad] == d.pad_nets[pad]:
                support += 1
    return min(1.0, support * 0.25)


def _place_context(a: Footprint, b: Footprint, source: Channel, target: Channel, locked: list[tuple[str, str]]) -> float:
    if not locked:
        return 0.0
    total = 0.0
    for c_ref, d_ref in locked:
        c = source.fp_by_ref[c_ref]
        d = target.fp_by_ref[d_ref]

        nets_ac = set(a.pad_nets.values()) & set(c.pad_nets.values())
        nets_bd = set(b.pad_nets.values()) & set(d.pad_nets.values())
        if not nets_ac or not nets_bd:
            continue

        dx1, dy1 = c.x - a.x, c.y - a.y
        dx2, dy2 = d.x - b.x, d.y - b.y
        n1 = hypot(dx1, dy1)
        n2 = hypot(dx2, dy2)

        if n1 == 0 and n2 == 0:
            sim = 1.0
        elif n1 == 0 or n2 == 0:
            sim = 0.0
        else:
            dist_sim = max(0.0, 1.0 - abs(n1 - n2) / (n1 + n2))
            cos = (dx1 * dx2 + dy1 * dy2) / (n1 * n2)
            cos_sim = (cos + 1.0) / 2.0
            sim = 0.5 * dist_sim + 0.5 * cos_sim
        total += sim

    return min(1.0, total / max(len(locked), 1))


def match_channels(source: Channel, target: Channel) -> list[Match]:
    """Match footprints from source to target."""
    if not source.footprints or not target.footprints:
        return []

    src = source.fp_by_ref
    tgt = target.fp_by_ref

    base_scores: dict[tuple[str, str], float] = {
        (a_ref, b_ref): footprint_score(a, b)
        for a_ref, a in src.items()
        for b_ref, b in tgt.items()
    }

    locked: list[tuple[str, str]] = []
    locked_score: dict[tuple[str, str], float] = {}
    matched_src: set[str] = set()
    matched_tgt: set[str] = set()

    while True:
        src_open = {r for r in src if r not in matched_src}
        tgt_open = {r for r in tgt if r not in matched_tgt}
        if not src_open or not tgt_open:
            break

        candidates: dict[tuple[str, str], float] = {}
        candidate_base: dict[tuple[str, str], float] = {}
        for a_ref in src_open:
            for b_ref in tgt_open:
                base = base_scores.get((a_ref, b_ref), 0.0)
                if base <= 0.0:
                    continue
                a, b = src[a_ref], tgt[b_ref]
                total = base + _W_NET * _net_context(a, b, source, target, locked) + _W_PLACE * _place_context(a, b, source, target, locked)
                candidates[(a_ref, b_ref)] = total
                candidate_base[(a_ref, b_ref)] = base

        if not candidates:
            break

        by_src: dict[str, list[float]] = {}
        by_tgt: dict[str, list[float]] = {}
        for (a_ref, b_ref), sc in candidates.items():
            by_src.setdefault(a_ref, []).append(sc)
            by_tgt.setdefault(b_ref, []).append(sc)

        def ambiguity(a_ref: str, b_ref: str) -> float:
            def ratio(second: float, best: float) -> float:
                return second / (best + 1e-9)

            s = sorted(by_src[a_ref], reverse=True)
            best_a, second_a = s[0], (s[1] if len(s) > 1 else 0.0)
            s = sorted(by_tgt[b_ref], reverse=True)
            best_b, second_b = s[0], (s[1] if len(s) > 1 else 0.0)
            return (ratio(second_a, best_a) + ratio(second_b, best_b)) / 2.0

        best_pair: tuple[str, str] | None = None
        best_ambiguity = float("inf")
        best_score = 0.0
        for pair, sc in candidates.items():
            # Skip if base score is too low
            if candidate_base[pair] < _MIN_SCORE:
                continue
            amb = ambiguity(*pair)
            if amb < best_ambiguity - 1e-9 or (abs(amb - best_ambiguity) < 1e-9 and sc > best_score):
                best_pair = pair
                best_ambiguity = amb
                best_score = sc

        if best_pair is None:
            break

        a_ref, b_ref = best_pair
        matched_src.add(a_ref)
        matched_tgt.add(b_ref)
        locked.append(best_pair)
        locked_score[best_pair] = best_score

    matches: list[Match] = []
    for a_ref in src:
        if a_ref in matched_src:
            b_ref = next(b for a, b in locked if a == a_ref)
            matches.append(Match(a_ref, b_ref, locked_score[(a_ref, b_ref)]))
        else:
            matches.append(Match(a_ref, None, 0.0))
    for b_ref in tgt:
        if b_ref not in matched_tgt:
            matches.append(Match(None, b_ref, 0.0))

    return matches
