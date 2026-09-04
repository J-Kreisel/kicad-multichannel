"""Pure group-topology calculations and validation."""

from __future__ import annotations

from dataclasses import dataclass

from .models import GroupSnapshot


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
