"""Pure group-topology calculations and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kipy.proto.common.commands.editor_commands_pb2 import (
    RunAction,
    RunActionResponse,
    RunActionStatus,
)
from kipy.proto.common.types import KIID, KiCadObjectType

from ..domain.models import GroupSnapshot


_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def item_id(obj) -> str:
    value = str(getattr(obj, "value", obj))
    match = _UUID_RE.search(value)
    return match.group(1) if match else value.strip()


@dataclass(frozen=True)
class GroupTopology:
    groups: tuple[GroupSnapshot, ...]

    @property
    def by_id(self) -> dict[str, GroupSnapshot]:
        return {group.group_id: group for group in self.groups}

    def parent_ids(self, item_id: str) -> set[str]:
        return {
            group.group_id
            for group in self.groups
            if item_id in group.member_ids
        }


def expected_topology(
    before: GroupTopology,
    removed_item_ids: set[str],
    additions_by_group: dict[str, tuple[str, ...]],
) -> GroupTopology:
    groups: list[GroupSnapshot] = []
    for group in before.groups:
        members = [
            item_id
            for item_id in group.member_ids
            if item_id not in removed_item_ids
        ]
        seen = set(members)
        for item_id in additions_by_group.get(group.group_id, ()):
            if item_id not in seen:
                members.append(item_id)
                seen.add(item_id)
        groups.append(GroupSnapshot(group.group_id, group.name, tuple(members)))

    missing = set(additions_by_group) - set(before.by_id)
    if missing:
        raise RuntimeError(f"target groups no longer exist: {sorted(missing)}")
    return GroupTopology(tuple(groups))


def topology_errors(expected: GroupTopology, actual: GroupTopology) -> list[str]:
    errors: list[str] = []
    expected_by_id = expected.by_id
    actual_by_id = actual.by_id
    created = set(actual_by_id) - set(expected_by_id)
    removed = set(expected_by_id) - set(actual_by_id)
    if created:
        errors.append(f"unexpected groups created: {sorted(created)}")
    if removed:
        errors.append(f"groups removed: {sorted(removed)}")

    for group_id in sorted(set(expected_by_id) & set(actual_by_id)):
        wanted = expected_by_id[group_id]
        found = actual_by_id[group_id]
        if wanted.name != found.name:
            errors.append(
                f"group {group_id} renamed from {wanted.name!r} to {found.name!r}"
            )
        if set(wanted.member_ids) != set(found.member_ids):
            missing = set(wanted.member_ids) - set(found.member_ids)
            added = set(found.member_ids) - set(wanted.member_ids)
            errors.append(
                f"group {wanted.name!r} ({group_id}) membership differs; "
                f"missing={sorted(missing)}, added={sorted(added)}"
            )
    return errors


def read_group_topology(board) -> tuple[GroupTopology, dict[str, object]]:
    groups = board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    wrappers = {item_id(group.proto.id): group for group in groups}
    topology = GroupTopology(
        tuple(
            GroupSnapshot(
                group_id=group_id,
                name=str(group.proto.name),
                member_ids=tuple(item_id(member_id) for member_id in group.proto.items),
            )
            for group_id, group in wrappers.items()
        )
    )
    return topology, wrappers


def replace_group_members(board, group, wanted: GroupSnapshot) -> None:
    member_ids = [KIID(value=member_id) for member_id in wanted.member_ids]
    members = list(board.get_items_by_id(member_ids)) if member_ids else []
    resolved_ids = {item_id(item.id) for item in members}
    missing_ids = set(wanted.member_ids) - resolved_ids
    if missing_ids:
        raise RuntimeError(
            f"could not resolve members for group {wanted.name!r}: {sorted(missing_ids)}"
        )
    group.proto.name = wanted.name
    group.items = members


def leave_active_groups(board) -> None:
    """Exit all nested group-edit contexts before IPC mutations."""
    group_count = len(board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP]))
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
            raise RuntimeError("KiCad could not leave the active group before updating")


class GroupTopologyManager:
    def __init__(self, board):
        self._board = board

    def repair(self, wanted: GroupTopology) -> int:
        """Commit removals before additions until KiCad's parent links settle."""
        commit_count = 0
        for _ in range(6):
            actual, _ = read_group_topology(self._board)
            self._assert_same_groups(wanted, actual)

            removals: list[GroupSnapshot] = []
            for group_id, expected_group in wanted.by_id.items():
                current_group = actual.by_id[group_id]
                expected_members = set(expected_group.member_ids)
                retained_members = tuple(
                    member_id
                    for member_id in current_group.member_ids
                    if member_id in expected_members
                )
                if len(retained_members) != len(current_group.member_ids):
                    removals.append(
                        GroupSnapshot(group_id, expected_group.name, retained_members)
                    )
            if removals:
                self._commit_snapshots(removals, "Detach misplaced group items")
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
                self._commit_snapshots(additions, "Restore multichannel groups")
                commit_count += 1
                continue
            return commit_count

        actual, _ = read_group_topology(self._board)
        errors = topology_errors(wanted, actual)
        raise RuntimeError("group topology did not stabilize: " + "; ".join(errors))

    def _commit_snapshots(
        self,
        snapshots: list[GroupSnapshot],
        message: str,
    ) -> None:
        commit = self._board.begin_commit()
        try:
            wrappers = {item_id(group.id): group for group in self._board.get_groups()}
            changed = []
            for snapshot in snapshots:
                wrapper = wrappers.get(snapshot.group_id)
                if wrapper is None:
                    raise RuntimeError(f"group no longer exists: {snapshot.group_id}")
                replace_group_members(self._board, wrapper, snapshot)
                changed.append(wrapper)
            result = self._board.update_items(changed)
            if len(result) != len(changed):
                raise RuntimeError(f"KiCad updated {len(result)}/{len(changed)} groups")
            self._board.push_commit(commit, message)
        except Exception:
            self._board.drop_commit(commit)
            raise

    @staticmethod
    def _assert_same_groups(wanted: GroupTopology, actual: GroupTopology) -> None:
        if set(wanted.by_id) != set(actual.by_id):
            errors = topology_errors(wanted, actual)
            raise RuntimeError("group topology changed: " + "; ".join(errors))
