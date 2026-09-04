"""Build minimal update plans through a channel-name API."""

from __future__ import annotations

from .matching import match_channels
from .models import Channel, Origin, UpdatePlan
from .origins import ChannelOriginResolver
from .placement import resolve_matched_footprints
from .routing import verified_net_mappings


class UpdatePlanner:
    def __init__(self, channels: list[Channel], bucket_size_nm: int):
        self._origins = ChannelOriginResolver(channels, bucket_size_nm)

    def find_channel_origins(
        self,
        source_name: str,
        target_name: str,
    ) -> tuple[Origin, Origin] | None:
        return self._origins.find_channel_origins(source_name, target_name)

    def build_update_plan(
        self,
        source_name: str,
        target_name: str,
    ) -> UpdatePlan | None:
        origins = self.find_channel_origins(source_name, target_name)
        if origins is None:
            return None

        source = self._origins.channel(source_name)
        target = self._origins.channel(target_name)
        pairs = match_channels(source, target)
        matches = resolve_matched_footprints(source, target, pairs)
        net_mappings = verified_net_mappings(source, target, pairs)
        return UpdatePlan(
            source_channel=source.name,
            target_channel=target.name,
            source_origin=origins[0],
            target_origin=origins[1],
            footprint_matches=tuple(
                (source_fp.reference, target_fp.reference)
                for _, source_fp, target_fp in matches
            ),
            net_matches=tuple(net_mappings.items()),
        )
