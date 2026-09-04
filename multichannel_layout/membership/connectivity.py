"""Build explicit track/via islands; zones intentionally do not participate."""

from __future__ import annotations

from collections import defaultdict, deque

from .models import CopperItem, MembershipPad


def _point_on_segment(
    point: tuple[int, int],
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    px, py = point
    ax, ay = start
    bx, by = end
    return (
        (px - ax) * (by - ay) == (py - ay) * (bx - ax)
        and min(ax, bx) <= px <= max(ax, bx)
        and min(ay, by) <= py <= max(ay, by)
    )


def _segments_intersect(
    left: tuple[tuple[int, int], tuple[int, int]],
    right: tuple[tuple[int, int], tuple[int, int]],
) -> bool:
    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (value > 0) - (value < 0)

    a, b = left
    c, d = right
    orientations = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    if orientations[0] != orientations[1] and orientations[2] != orientations[3]:
        return True
    return (
        (orientations[0] == 0 and _point_on_segment(c, a, b))
        or (orientations[1] == 0 and _point_on_segment(d, a, b))
        or (orientations[2] == 0 and _point_on_segment(a, c, d))
        or (orientations[3] == 0 and _point_on_segment(b, c, d))
    )


def _layers_overlap(left: frozenset[int], right: frozenset[int]) -> bool:
    return not left or not right or not left.isdisjoint(right)


def _points(item) -> tuple[tuple[int, int], ...]:
    if isinstance(item, MembershipPad):
        return (item.position,)
    return item.points


def _geometries_touch(left, right) -> bool:
    if left.net != right.net or not _layers_overlap(left.layers, right.layers):
        return False
    left_points = _points(left)
    right_points = _points(right)
    if any(point in right_points for point in left_points):
        return True
    if isinstance(left, CopperItem) and left.kind == "track":
        if any(_point_on_segment(point, left.points[0], left.points[1]) for point in right_points):
            return True
    if isinstance(right, CopperItem) and right.kind == "track":
        if any(_point_on_segment(point, right.points[0], right.points[1]) for point in left_points):
            return True
    return (
        isinstance(left, CopperItem)
        and isinstance(right, CopperItem)
        and left.kind == right.kind == "track"
        and _segments_intersect(
            (left.points[0], left.points[1]),
            (right.points[0], right.points[1]),
        )
    )


def build_adjacency(
    pads: list[MembershipPad],
    copper: list[CopperItem],
) -> dict[str, set[str]]:
    adjacency = {item.item_id: set() for item in [*pads, *copper]}
    contacts: dict[tuple[str, tuple[int, int]], list[tuple[str, frozenset[int]]]] = (
        defaultdict(list)
    )
    for pad in pads:
        contacts[(pad.net, pad.position)].append((pad.item_id, pad.layers))
    for item in copper:
        for point in item.points:
            contacts[(item.net, point)].append((item.item_id, item.layers))

    for entries in contacts.values():
        for index, (left_id, left_layers) in enumerate(entries):
            for right_id, right_layers in entries[index + 1:]:
                if left_layers and right_layers and left_layers.isdisjoint(right_layers):
                    continue
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)

    nodes = [*pads, *copper]
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            if right.item_id in adjacency[left.item_id]:
                continue
            if _geometries_touch(left, right):
                adjacency[left.item_id].add(right.item_id)
                adjacency[right.item_id].add(left.item_id)
    return adjacency


def connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    remaining = set(adjacency)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        component: set[str] = set()
        queue = deque((start,))
        while queue:
            item_id = queue.popleft()
            if item_id in component:
                continue
            component.add(item_id)
            queue.extend(adjacency[item_id] - component)
        remaining -= component
        components.append(component)
    return components


def internal_pads_connected(
    internal_pad_ids: set[str],
    active_copper_ids: set[str],
    all_pad_ids: set[str],
    adjacency: dict[str, set[str]],
) -> bool:
    if len(internal_pad_ids) < 2:
        return True
    allowed = all_pad_ids | active_copper_ids
    start = min(internal_pad_ids)
    reached = {start}
    queue = deque((start,))
    while queue:
        item_id = queue.popleft()
        for neighbor in adjacency[item_id]:
            if neighbor in allowed and neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    return internal_pad_ids <= reached
