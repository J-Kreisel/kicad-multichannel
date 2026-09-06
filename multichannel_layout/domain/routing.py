"""Pure routing verification and update-plan construction."""

from __future__ import annotations

from .matching import Match, footprint_score, pad_pairs
from .models import Channel


def verified_net_mappings(
    source: Channel,
    target: Channel,
    pairs: list[Match],
) -> dict[str, str]:
    """Map source nets whose channel pads all map to one target net."""
    routed_nets = {
        item.net
        for item in [*source.tracks, *source.vias]
        if item.net
    }
    pair_by_source = {
        pair.from_ref: pair.to_ref
        for pair in pairs
        if pair.is_matched
    }
    mappings: dict[str, str] = {}

    for source_net in routed_nets:
        target_nets: set[str] = set()
        source_pad_count = 0
        matched_pad_count = 0
        valid = True
        for source_fp in source.footprints:
            target_ref = pair_by_source.get(source_fp.reference)
            target_fp = target.fp_by_ref.get(target_ref) if target_ref else None
            paired_pads = dict(pad_pairs(source_fp, target_fp)) if target_fp else {}

            for source_pad, pad_net in source_fp.pad_nets.items():
                if pad_net != source_net:
                    continue
                source_pad_count += 1
                target_pad = paired_pads.get(source_pad)
                if (
                    target_fp is None
                    or target_pad is None
                    or footprint_score(source_fp, target_fp) < 0.95
                ):
                    valid = False
                    continue
                target_net = target_fp.pad_nets.get(target_pad, "")
                if not target_net:
                    valid = False
                    continue
                target_nets.add(target_net)
                matched_pad_count += 1

        if (
            valid
            and source_pad_count > 0
            and matched_pad_count == source_pad_count
            and len(target_nets) == 1
        ):
            mappings[source_net] = target_nets.pop()

    duplicate_targets = {
        target_net
        for target_net in mappings.values()
        if sum(value == target_net for value in mappings.values()) > 1
    }
    return {
        source_net: target_net
        for source_net, target_net in mappings.items()
        if target_net not in duplicate_targets
    }
