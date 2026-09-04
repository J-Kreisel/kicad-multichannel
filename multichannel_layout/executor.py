"""KiCad adapter that resolves and applies a minimal UpdatePlan."""

from __future__ import annotations

import re

import kipy.geometry as geom
from kipy.board_types import Track as BoardTrack, Via as BoardVia
from kipy.common_types import Vector2
from kipy.proto.common.commands.editor_commands_pb2 import (
    RunAction,
    RunActionResponse,
    RunActionStatus,
)
from kipy.proto.common.types import KIID, KiCadObjectType

from .board_reader import read_board_snapshot
from .group_topology import GroupTopology, expected_topology, topology_errors
from .models import Channel, GroupSnapshot, UpdatePlan, UpdateResult
from .transforms import transform_from_origins, transform_point


_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _item_id(obj) -> str:
    value = str(getattr(obj, "value", obj))
    match = _UUID_RE.search(value)
    return match.group(1) if match else value.strip()


def _find_channel(channels: tuple[Channel, ...], name: str) -> Channel:
    matches = [channel for channel in channels if channel.name == name]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one live channel named {name!r}, found {len(matches)}"
        )
    return matches[0]


def _topology_and_groups(board) -> tuple[GroupTopology, dict[str, object]]:
    groups = board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    wrappers = {_item_id(group.proto.id): group for group in groups}
    topology = GroupTopology(
        tuple(
            GroupSnapshot(
                group_id=group_id,
                name=str(group.proto.name),
                member_ids=tuple(_item_id(item_id) for item_id in group.proto.items),
            )
            for group_id, group in wrappers.items()
        )
    )
    return topology, wrappers


def _replace_group_members(board, group, wanted: GroupSnapshot) -> None:
    member_ids = [KIID(value=item_id) for item_id in wanted.member_ids]
    members = list(board.get_items_by_id(member_ids)) if member_ids else []
    resolved_ids = {_item_id(item.id) for item in members}
    missing_ids = set(wanted.member_ids) - resolved_ids
    if missing_ids:
        raise RuntimeError(
            f"could not resolve members for group {wanted.name!r}: {sorted(missing_ids)}"
        )
    group.proto.name = wanted.name
    group.items = members


def _leave_active_groups(board) -> None:
    """Exit all nested group-edit contexts before IPC mutations."""
    group_count = len(
        board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    )
    test_adapter = getattr(board, "leave_active_group", None)
    for _ in range(group_count):
        if test_adapter is not None:
            test_adapter()
            continue
        command = RunAction(action="common.Interactive.groupLeave")
        response = board._kicad.send(command, RunActionResponse)
        if response.status == RunActionStatus.RAS_INVALID:
            break
        if response.status != RunActionStatus.RAS_OK:
            raise RuntimeError(
                "KiCad could not leave the active group before updating"
            )


class KiCadBoardExecutor:
    """Keep every KiCad wrapper behind an apply(plan) boundary."""

    def __init__(self, board):
        self._board = board

    def apply(self, plan: UpdatePlan) -> UpdateResult:
        """Resolve all board objects internally and apply one source-target plan."""
        _leave_active_groups(self._board)
        snapshot = read_board_snapshot(self._board)
        source = _find_channel(snapshot.channels, plan.source_channel)
        target = _find_channel(snapshot.channels, plan.target_channel)
        before, _ = _topology_and_groups(self._board)
        self._validate_plan(plan, source, target, before)

        footprints = {
            _item_id(item.id): item
            for item in self._board.get_footprints()
        }
        tracks = {
            _item_id(item.id): item
            for item in self._board.get_items(types=[KiCadObjectType.KOT_PCB_TRACE])
        }
        vias = {
            _item_id(item.id): item
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
            {_item_id(item.id) for item in remove_items},
            {target.group_kid: tuple(_item_id(item.id) for item in created)},
        )
        try:
            self._repair_topology(wanted)
        except Exception as exc:
            raise RuntimeError(
                f"object changes were committed, but group restoration failed: {exc}; "
                "undo until the 'Update multichannel objects' step is reverted"
            ) from exc

        actual, _ = _topology_and_groups(self._board)
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

    def _repair_topology(self, wanted: GroupTopology) -> int:
        """Commit removals before additions until KiCad's parent links settle."""
        commit_count = 0
        for _ in range(6):
            actual, _ = _topology_and_groups(self._board)
            self._assert_same_groups(wanted, actual)

            removals: list[GroupSnapshot] = []
            for group_id, expected_group in wanted.by_id.items():
                current_group = actual.by_id[group_id]
                expected_members = set(expected_group.member_ids)
                retained_members = tuple(
                    item_id
                    for item_id in current_group.member_ids
                    if item_id in expected_members
                )
                if len(retained_members) != len(current_group.member_ids):
                    removals.append(
                        GroupSnapshot(group_id, expected_group.name, retained_members)
                    )
            if removals:
                self._commit_group_snapshots(removals, "Detach misplaced group items")
                commit_count += 1
                continue

            additions = [
                expected_group
                for group_id, expected_group in wanted.by_id.items()
                if (
                    actual.by_id[group_id].name != expected_group.name
                    or set(actual.by_id[group_id].member_ids)
                    != set(expected_group.member_ids)
                )
            ]
            if additions:
                self._commit_group_snapshots(additions, "Restore multichannel groups")
                commit_count += 1
                continue
            return commit_count

        actual, _ = _topology_and_groups(self._board)
        errors = topology_errors(wanted, actual)
        raise RuntimeError("group topology did not stabilize: " + "; ".join(errors))

    def _commit_group_snapshots(
        self,
        snapshots: list[GroupSnapshot],
        message: str,
    ) -> None:
        commit = self._board.begin_commit()
        try:
            wrappers = {
                _item_id(group.id): group
                for group in self._board.get_groups()
            }
            changed = []
            for snapshot in snapshots:
                wrapper = wrappers.get(snapshot.group_id)
                if wrapper is None:
                    raise RuntimeError(f"group no longer exists: {snapshot.group_id}")
                _replace_group_members(self._board, wrapper, snapshot)
                changed.append(wrapper)
            self._update_groups(changed)
            self._board.push_commit(commit, message)
        except Exception:
            self._board.drop_commit(commit)
            raise

    @staticmethod
    def _assert_same_groups(wanted: GroupTopology, actual: GroupTopology) -> None:
        if set(wanted.by_id) != set(actual.by_id):
            errors = topology_errors(wanted, actual)
            raise RuntimeError("group topology changed: " + "; ".join(errors))

    def _update_groups(self, groups: list[object]) -> None:
        if not groups:
            return
        result = self._board.update_items(groups)
        if len(result) != len(groups):
            raise RuntimeError(f"KiCad updated {len(result)}/{len(groups)} groups")
