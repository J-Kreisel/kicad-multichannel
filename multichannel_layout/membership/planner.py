"""Plan deterministic additions or removals of routing group membership."""

from __future__ import annotations

from .connectivity import build_adjacency, connected_components, internal_pads_connected
from .models import MembershipBoard, MembershipPlan


def _find_group(board: MembershipBoard, group_name: str):
    matches = [group for group in board.groups if group.name == group_name]
    if len(matches) != 1:
        raise ValueError(f"expected one group named {group_name!r}, found {len(matches)}")
    return matches[0]


def build_membership_plan(
    board: MembershipBoard,
    group_name: str,
    mode: str,
) -> MembershipPlan:
    if mode not in {"add", "remove"}:
        raise ValueError(f"unsupported membership mode: {mode}")

    group = _find_group(board, group_name)
    member_ids = set(group.member_ids)
    internal_pads = [pad for pad in board.pads if pad.footprint_id in member_ids]
    internal_nets = {pad.net for pad in internal_pads}
    external_pads = [
        pad
        for pad in board.pads
        if pad.footprint_id not in member_ids and pad.net in internal_nets
    ]
    mixed_nets = {pad.net for pad in external_pads}
    pads = [pad for pad in board.pads if pad.net in internal_nets]
    copper = [item for item in board.copper if item.net in internal_nets]
    adjacency = build_adjacency(pads, copper)
    pad_by_id = {pad.item_id: pad for pad in pads}
    copper_by_id = {item.item_id: item for item in copper}
    internal_pad_ids = {pad.item_id for pad in internal_pads}
    external_pad_ids = {pad.item_id for pad in external_pads}

    additions: set[str] = set()
    removals: set[str] = set()
    locked_items: set[str] = set()
    island_count = 0
    for component in connected_components(adjacency):
        island_internal = component & internal_pad_ids
        island_external = component & external_pad_ids
        if mode == "add" and len(island_internal) < 2:
            continue
        if mode == "remove" and (not island_internal or not island_external):
            continue
        island_count += 1
        island_copper = component & set(copper_by_id)
        island_pads = component & set(pad_by_id)
        locked = {
            item_id
            for item_id in island_copper
            if copper_by_id[item_id].locked
        }
        locked_items.update(locked)

        if mode == "add":
            locked_outside = locked - member_ids
            active = set(island_copper) - locked_outside
            if not internal_pads_connected(
                island_internal, active, island_pads, adjacency
            ):
                raise RuntimeError(
                    f"locked copper outside {group_name!r} blocks membership for net "
                    f"{copper_by_id[min(island_copper)].net!r}"
                )
            candidates = sorted(active - member_ids)
            protected = island_copper & member_ids
        else:
            active = set(island_copper)
            candidates = sorted(
                active - locked,
                key=lambda item_id: (item_id in member_ids, item_id),
            )
            protected = locked

        for item_id in candidates:
            if item_id in protected:
                continue
            active.remove(item_id)
            if not internal_pads_connected(
                island_internal, active, island_pads, adjacency
            ):
                active.add(item_id)

        if mode == "add":
            additions.update(active - member_ids - locked)
        else:
            removals.update((island_copper & member_ids) - active - locked)

    return MembershipPlan(
        group_name=group_name,
        add_item_ids=tuple(sorted(additions)),
        remove_item_ids=tuple(sorted(removals)),
        mixed_net_count=len(mixed_nets),
        island_count=island_count,
        locked_item_count=len(locked_items),
    )
