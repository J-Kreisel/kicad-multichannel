"""Build width-aware copper islands; zones intentionally do not participate."""

from __future__ import annotations

import math
from collections import deque

from .models import CopperItem, MembershipPad, PolygonRegion


Point = tuple[int, int]


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    px, py = point
    ax, ay = start
    bx, by = end
    return (
        (px - ax) * (by - ay) == (py - ay) * (bx - ax)
        and min(ax, bx) <= px <= max(ax, bx)
        and min(ay, by) <= py <= max(ay, by)
    )


def _segments_intersect(left: tuple[Point, Point], right: tuple[Point, Point]) -> bool:
    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (value > 0) - (value < 0)

    a, b = left
    c, d = right
    values = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    if values[0] != values[1] and values[2] != values[3]:
        return True
    return (
        (values[0] == 0 and _point_on_segment(c, a, b))
        or (values[1] == 0 and _point_on_segment(d, a, b))
        or (values[2] == 0 and _point_on_segment(a, c, d))
        or (values[3] == 0 and _point_on_segment(b, c, d))
    )


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    ratio = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(
        point[0] - (start[0] + ratio * dx),
        point[1] - (start[1] + ratio * dy),
    )


def _segment_distance(left: tuple[Point, Point], right: tuple[Point, Point]) -> float:
    if _segments_intersect(left, right):
        return 0.0
    return min(
        _point_segment_distance(left[0], *right),
        _point_segment_distance(left[1], *right),
        _point_segment_distance(right[0], *left),
        _point_segment_distance(right[1], *left),
    )


def _segments(points: tuple[Point, ...], closed: bool = False):
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 2 and points[-1] != points[0]:
        pairs.append((points[-1], points[0]))
    return pairs


def _polyline_distance(left: tuple[Point, ...], right: tuple[Point, ...]) -> float:
    left_segments = _segments(left) or [(left[0], left[0])]
    right_segments = _segments(right) or [(right[0], right[0])]
    return min(_segment_distance(a, b) for a in left_segments for b in right_segments)


def _arc_points(item: CopperItem) -> tuple[Point, ...]:
    if item.mid is None:
        return item.points
    start, end = item.points
    mid = item.mid
    ax, ay = start
    bx, by = mid
    cx, cy = end
    denominator = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) <= 1e-9:
        return (start, mid, end)
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / denominator
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / denominator
    start_angle = math.atan2(ay - uy, ax - ux)
    mid_angle = math.atan2(by - uy, bx - ux)
    end_angle = math.atan2(cy - uy, cx - ux)
    ccw_sweep = (end_angle - start_angle) % (2 * math.pi)
    ccw_mid = (mid_angle - start_angle) % (2 * math.pi)
    sweep = ccw_sweep if ccw_mid <= ccw_sweep else ccw_sweep - 2 * math.pi
    radius = math.hypot(ax - ux, ay - uy)
    max_sagitta = max(1.0, item.width / 16)
    if radius <= max_sagitta:
        step_angle = math.radians(2)
    else:
        step_angle = min(
            math.radians(2),
            2 * math.acos(max(-1.0, 1 - max_sagitta / radius)),
        )
    steps = max(2, int(math.ceil(abs(sweep) / step_angle)))
    mid_progress = ccw_mid if sweep >= 0 else (start_angle - mid_angle) % (2 * math.pi)
    progress_values = {abs(sweep) * index / steps for index in range(steps + 1)}
    progress_values.add(mid_progress)
    return tuple(
        (
            int(round(ux + radius * math.cos(start_angle + math.copysign(progress, sweep)))),
            int(round(uy + radius * math.sin(start_angle + math.copysign(progress, sweep)))),
        )
        for progress in sorted(progress_values)
    )


def _centerline(item: CopperItem) -> tuple[Point, ...]:
    return _arc_points(item) if item.kind == "arc" else item.points


def _stroke_radius(item: CopperItem) -> float:
    radius = item.width / 2
    if item.kind == "arc":
        radius += max(1.0, item.width / 16)
    return radius


def _point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    if any(_point_on_segment(point, *edge) for edge in _segments(polygon, closed=True)):
        return True
    inside = False
    x, y = point
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > y) != (previous[1] > y):
            crossing_x = (
                (previous[0] - current[0]) * (y - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _point_in_region(point: Point, region: PolygonRegion) -> bool:
    return _point_in_polygon(point, region.outline) and not any(
        _point_in_polygon(point, hole) for hole in region.holes
    )


def _stroke_touches_region(
    centerline: tuple[Point, ...],
    radius: float,
    region: PolygonRegion,
) -> bool:
    if any(_point_in_region(point, region) for point in centerline):
        return True
    stroke_segments = _segments(centerline) or [(centerline[0], centerline[0])]
    boundaries = [region.outline, *region.holes]
    boundary_segments = [
        edge
        for boundary in boundaries
        for edge in _segments(boundary, closed=True)
    ]
    return any(
        _segment_distance(stroke_segment, boundary_segment) <= radius
        for stroke_segment in stroke_segments
        for boundary_segment in boundary_segments
    )


def _regions_touch(left: PolygonRegion, right: PolygonRegion) -> bool:
    if left.layer != right.layer:
        return False
    if any(_point_in_region(point, right) for point in left.outline):
        return True
    if any(_point_in_region(point, left) for point in right.outline):
        return True
    return any(
        _segments_intersect(a, b)
        for a in _segments(left.outline, closed=True)
        for b in _segments(right.outline, closed=True)
    )


def _geometries_touch(left, right) -> bool:
    if left.net != right.net:
        return False
    common_layers = left.layers & right.layers
    if not common_layers:
        return False
    for layer in common_layers:
        left_regions = tuple(
            region for region in left.regions if region.layer == layer
        )
        right_regions = tuple(
            region for region in right.regions if region.layer == layer
        )
        if left_regions and right_regions:
            if any(_regions_touch(a, b) for a in left_regions for b in right_regions):
                return True
            continue
        if left_regions:
            centerline = _centerline(right)
            if any(
                _stroke_touches_region(centerline, _stroke_radius(right), region)
                for region in left_regions
            ):
                return True
            continue
        if right_regions:
            centerline = _centerline(left)
            if any(
                _stroke_touches_region(centerline, _stroke_radius(left), region)
                for region in right_regions
            ):
                return True
            continue
        left_line = (left.position,) if isinstance(left, MembershipPad) else _centerline(left)
        right_line = (right.position,) if isinstance(right, MembershipPad) else _centerline(right)
        left_radius = 0 if isinstance(left, MembershipPad) else _stroke_radius(left)
        right_radius = 0 if isinstance(right, MembershipPad) else _stroke_radius(right)
        if _polyline_distance(left_line, right_line) <= left_radius + right_radius:
            return True
    return False


def build_adjacency(
    pads: list[MembershipPad],
    copper: list[CopperItem],
) -> dict[str, set[str]]:
    nodes = [*pads, *copper]
    adjacency = {item.item_id: set() for item in nodes}
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
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
