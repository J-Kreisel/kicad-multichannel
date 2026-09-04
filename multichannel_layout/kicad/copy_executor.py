"""KiCad adapter that resolves and applies a minimal UpdatePlan."""

from __future__ import annotations

import kipy.geometry as geom
from kipy.board_types import Track as BoardTrack, Via as BoardVia
from kipy.common_types import Vector2
from kipy.proto.common.types import KiCadObjectType

from ..domain.models import Channel, UpdatePlan, UpdateResult
from ..domain.transforms import transform_from_origins, transform_point
from .groups import (
    GroupTopology,
    GroupTopologyManager,
    expected_topology,
    item_id,
    leave_active_groups,
    read_group_topology,
    topology_errors,
)
from .reader import read_board_snapshot


def _find_channel(channels: tuple[Channel, ...], name: str) -> Channel:
    matches = [channel for channel in channels if channel.name == name]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one live channel named {name!r}, found {len(matches)}"
        )
    return matches[0]


class KiCadBoardExecutor:
    """Keep every KiCad wrapper behind an apply(plan) boundary."""

    def __init__(self, board):
        self._board = board
        self._group_manager = GroupTopologyManager(board)

    def apply(self, plan: UpdatePlan) -> UpdateResult:
        """Resolve all board objects internally and apply one source-target plan."""
        leave_active_groups(self._board)
        snapshot = read_board_snapshot(self._board)
        source = _find_channel(snapshot.channels, plan.source_channel)
        target = _find_channel(snapshot.channels, plan.target_channel)
        before, _ = read_group_topology(self._board)
        self._validate_plan(plan, source, target, before)

        footprints = {
            item_id(item.id): item
            for item in self._board.get_footprints()
        }
        tracks = {
            item_id(item.id): item
            for item in self._board.get_items(types=[KiCadObjectType.KOT_PCB_TRACE])
        }
        vias = {
            item_id(item.id): item
            for item in self._board.get_items(types=[KiCadObjectType.KOT_PCB_VIA])
        }
        nets = {net.name: net for net in self._board.get_nets()}
        transform = transform_from_origins(plan.source_origin, plan.target_origin)

        placement_updates, locked_footprints = self._resolve_placements(
            plan,
            source,
            target,
            footprints,
            transform,
        )
        routing = self._resolve_routing(
            plan,
            source,
            target,
            tracks,
            vias,
            nets,
            transform,
        )

        if not placement_updates and not routing[0] and not routing[1]:
            return UpdateResult(
                source_channel=source.name,
                target_channel=target.name,
                footprint_count=0,
                track_count=0,
                via_count=0,
                locked_footprint_count=locked_footprints,
                locked_net_count=routing[2],
            )

        remove_items, new_items, locked_nets, track_count, via_count = routing
        object_commit = self._board.begin_commit()
        try:
            self._apply_placements(placement_updates)
            if remove_items:
                self._board.remove_items(remove_items)
            created = self._board.create_items(new_items) if new_items else []
            if len(created) != len(new_items):
                raise RuntimeError(
                    f"KiCad created {len(created)}/{len(new_items)} routing items"
                )
            self._board.push_commit(object_commit, "Update multichannel objects")
        except Exception:
            self._board.drop_commit(object_commit)
            raise

        wanted = expected_topology(
            before,
            {item_id(item.id) for item in remove_items},
            {target.group_kid: tuple(item_id(item.id) for item in created)},
        )
        try:
            self._group_manager.repair(wanted)
        except Exception as exc:
            raise RuntimeError(
                f"object changes were committed, but group restoration failed: {exc}; "
                "undo until the 'Update multichannel objects' step is reverted"
            ) from exc

        actual, _ = read_group_topology(self._board)
        errors = topology_errors(wanted, actual)
        if errors:
            raise RuntimeError(
                "group topology changed after restoration commit: "
                + "; ".join(errors)
            )

        return UpdateResult(
            source_channel=source.name,
            target_channel=target.name,
            footprint_count=len(placement_updates),
            track_count=track_count,
            via_count=via_count,
            locked_footprint_count=locked_footprints,
            locked_net_count=locked_nets,
        )

    @staticmethod
    def _validate_plan(
        plan: UpdatePlan,
        source: Channel,
        target: Channel,
        topology: GroupTopology,
    ) -> None:
        if source is target:
            raise RuntimeError("source and target channels must be different")
        if source.group_kid not in topology.by_id or target.group_kid not in topology.by_id:
            raise RuntimeError("source or target group no longer exists")
        if len(dict(plan.footprint_matches)) != len(plan.footprint_matches):
            raise RuntimeError("source footprint appears in more than one match")
        target_references = [target_ref for _, target_ref in plan.footprint_matches]
        if len(set(target_references)) != len(target_references):
            raise RuntimeError("target footprint appears in more than one match")
        if len(dict(plan.net_matches)) != len(plan.net_matches):
            raise RuntimeError("source net appears in more than one match")
        target_nets = [target_net for _, target_net in plan.net_matches]
        if len(set(target_nets)) != len(target_nets):
            raise RuntimeError("target net appears in more than one match")

    @staticmethod
    def _resolve_placements(
        plan: UpdatePlan,
        source: Channel,
        target: Channel,
        footprints: dict[str, object],
        transform,
    ) -> tuple[list[tuple[object, tuple[int, int], float]], int]:
        updates: list[tuple[object, tuple[int, int], float]] = []
        locked_count = 0
        for source_ref, target_ref in plan.footprint_matches:
            source_fp = source.fp_by_ref.get(source_ref)
            target_fp = target.fp_by_ref.get(target_ref)
            if source_fp is None or target_fp is None:
                raise RuntimeError(
                    f"footprint match no longer resolves: {source_ref!r} -> {target_ref!r}"
                )
            target_wrapper = footprints.get(target_fp.kid)
            if target_wrapper is None:
                raise RuntimeError(f"target footprint no longer exists: {target_ref!r}")
            if target_wrapper.locked:
                locked_count += 1
                continue
            updates.append(
                (
                    target_wrapper,
                    transform_point(source_fp.position, transform),
                    source_fp.orientation + transform.rotation,
                )
            )
        return updates, locked_count

    @staticmethod
    def _resolve_routing(
        plan: UpdatePlan,
        source: Channel,
        target: Channel,
        tracks: dict[str, object],
        vias: dict[str, object],
        nets: dict[str, object],
        transform,
    ) -> tuple[list[object], list[object], int, int, int]:
        mappings = dict(plan.net_matches)
        missing_nets = set(mappings.values()) - set(nets)
        if missing_nets:
            raise RuntimeError(f"target nets no longer exist: {sorted(missing_nets)}")

        source_items = [
            *(tracks[item.kid] for item in source.tracks if item.kid in tracks),
            *(vias[item.kid] for item in source.vias if item.kid in vias),
        ]
        target_items = [
            *(tracks[item.kid] for item in target.tracks if item.kid in tracks),
            *(vias[item.kid] for item in target.vias if item.kid in vias),
        ]
        available_source_nets = {item.net.name for item in source_items}
        missing_source_nets = set(mappings) - available_source_nets
        if missing_source_nets:
            raise RuntimeError(
                f"source routing no longer exists for nets: {sorted(missing_source_nets)}"
            )
        locked_target_nets = {
            item.net.name
            for item in target_items
            if item.locked and item.net.name in mappings.values()
        }
        mappings = {
            source_net: target_net
            for source_net, target_net in mappings.items()
            if target_net not in locked_target_nets
        }

        remove_items = [
            item
            for item in target_items
            if item.net.name in mappings.values()
        ]
        new_items: list[object] = []
        track_count = 0
        via_count = 0
        for item in source_items:
            target_net = mappings.get(item.net.name)
            if target_net is None:
                continue
            if isinstance(item, BoardTrack):
                copy = BoardTrack(proto=item.proto)
                copy.proto.id.value = ""
                copy.start = Vector2.from_xy(*transform_point((item.start.x, item.start.y), transform))
                copy.end = Vector2.from_xy(*transform_point((item.end.x, item.end.y), transform))
                track_count += 1
            elif isinstance(item, BoardVia):
                copy = BoardVia(proto=item.proto)
                copy.proto.id.value = ""
                copy.position = Vector2.from_xy(
                    *transform_point((item.position.x, item.position.y), transform)
                )
                via_count += 1
            else:
                continue
            copy.net = nets[target_net]
            new_items.append(copy)
        return remove_items, new_items, len(locked_target_nets), track_count, via_count

    def _apply_placements(
        self,
        updates: list[tuple[object, tuple[int, int], float]],
    ) -> None:
        footprints = []
        for footprint, position, orientation in updates:
            footprint.position = geom.Vector2.from_xy(*position)
            footprint.orientation = geom.Angle.from_degrees(orientation)
            footprints.append(footprint)
        result = self._board.update_items(footprints) if footprints else []
        if len(result) != len(footprints):
            raise RuntimeError(
                f"KiCad updated {len(result)}/{len(footprints)} footprints"
            )
