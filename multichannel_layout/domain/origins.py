"""Encapsulated channel-origin calculation and transform voting."""

from __future__ import annotations

from .matching import match_channels
from .models import Channel, Origin
from .placement import (
    calculate_match_transforms,
    resolve_matched_footprints,
    source_center,
)
from .transforms import choose_consensus_transform, transform_point


class ChannelOriginResolver:
    """Resolve two channel names to origins without exposing transform details."""

    def __init__(self, channels: list[Channel], bucket_size_nm: int):
        if bucket_size_nm <= 0:
            raise ValueError("bucket size must be positive")
        self._channels = tuple(channels)
        self._bucket_size_nm = bucket_size_nm
        self._cache: dict[tuple[str, str], tuple[Origin, Origin] | None] = {}

    def find_channel_origins(
        self,
        source_name: str,
        target_name: str,
    ) -> tuple[Origin, Origin] | None:
        key = (source_name, target_name)
        if key not in self._cache:
            self._cache[key] = self._calculate(source_name, target_name)
        return self._cache[key]

    def channel(self, name: str) -> Channel:
        matches = [channel for channel in self._channels if channel.name == name]
        if len(matches) != 1:
            raise ValueError(
                f"expected one channel named {name!r}, found {len(matches)}"
            )
        return matches[0]

    def _calculate(
        self,
        source_name: str,
        target_name: str,
    ) -> tuple[Origin, Origin] | None:
        source = self.channel(source_name)
        target = self.channel(target_name)
        if source is target:
            raise ValueError("source and target channels must be different")

        matches = resolve_matched_footprints(
            source,
            target,
            match_channels(source, target),
        )
        if not matches:
            return None

        center = source_center(matches)
        matched_target_ids = {target_fp.kid for _, _, target_fp in matches}
        unmatched_locked = [
            footprint.reference
            for footprint in target.footprints
            if footprint.locked and footprint.kid not in matched_target_ids
        ]
        if unmatched_locked:
            raise ValueError(
                f"locked target footprints have no source match: {unmatched_locked}"
            )

        locked_matches = [
            match
            for match in matches
            if match[2].locked
        ]
        if locked_matches:
            locked_transforms = calculate_match_transforms(locked_matches, center)
            first_transform = locked_transforms[0]
            conflicts = [
                target_fp.reference
                for (_, _, target_fp), transform in zip(
                    locked_matches[1:],
                    locked_transforms[1:],
                )
                if transform.translation != first_transform.translation
                or abs(transform.rotation - first_transform.rotation) > 1e-9
            ]
            if conflicts:
                anchor_reference = locked_matches[0][2].reference
                raise ValueError(
                    "locked target footprints disagree on the channel transform: "
                    f"{anchor_reference!r} conflicts with {conflicts}"
                )

            _, source_anchor, target_anchor = locked_matches[0]
            return (
                (
                    source_anchor.x,
                    source_anchor.y,
                    source_anchor.orientation,
                ),
                (
                    target_anchor.x,
                    target_anchor.y,
                    target_anchor.orientation,
                ),
            )

        transform = choose_consensus_transform(
            calculate_match_transforms(matches, center),
            self._bucket_size_nm,
        )
        target_position = transform_point(center, transform)
        return (
            (center[0], center[1], 0.0),
            (target_position[0], target_position[1], transform.rotation),
        )
