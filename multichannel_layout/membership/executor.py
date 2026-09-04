"""Apply routing membership plans through the shared topology manager."""

from __future__ import annotations

from ..domain.models import GroupSnapshot
from ..kicad.groups import (
    GroupTopology,
    GroupTopologyManager,
    leave_active_groups,
    read_group_topology,
    topology_errors,
)
from ..kicad.membership_reader import read_membership_board
from .models import MembershipPlan, MembershipResult


class MembershipExecutor:
    def __init__(self, board):
        self._board = board
        self._group_manager = GroupTopologyManager(board)

    def apply(self, plan: MembershipPlan) -> MembershipResult:
        leave_active_groups(self._board)
        before, _ = read_group_topology(self._board)
        matching_groups = [
            group for group in before.groups if group.name == plan.group_name
        ]
        if len(matching_groups) != 1:
            raise RuntimeError(
                f"expected one group named {plan.group_name!r}, "
                f"found {len(matching_groups)}"
            )
        target = matching_groups[0]

        live_board = read_membership_board(self._board)
        locked_ids = {
            item.item_id for item in live_board.copper if item.locked
        }
        foreign_group_ids = {
            item_id
            for group in before.groups
            if group.group_id != target.group_id
            for item_id in group.member_ids
        }
        add_ids = set(plan.add_item_ids) - locked_ids - foreign_group_ids
        remove_ids = set(plan.remove_item_ids) - locked_ids
        if not add_ids and not remove_ids:
            return MembershipResult(plan.group_name, 0, 0)

        wanted_groups: list[GroupSnapshot] = []
        for group in before.groups:
            members = list(group.member_ids)
            if group.group_id == target.group_id:
                members = [item_id for item_id in members if item_id not in remove_ids]
                seen = set(members)
                for item_id in sorted(add_ids):
                    if item_id not in seen:
                        members.append(item_id)
                        seen.add(item_id)
            wanted_groups.append(
                GroupSnapshot(group.group_id, group.name, tuple(members))
            )

        wanted = GroupTopology(tuple(wanted_groups))
        self._group_manager.repair(wanted)
        actual, _ = read_group_topology(self._board)
        errors = topology_errors(wanted, actual)
        if errors:
            raise RuntimeError(
                "group topology changed after membership update: "
                + "; ".join(errors)
            )
        return MembershipResult(
            group_name=plan.group_name,
            added_count=len(add_ids - set(target.member_ids)),
            removed_count=len(remove_ids & set(target.member_ids)),
        )
