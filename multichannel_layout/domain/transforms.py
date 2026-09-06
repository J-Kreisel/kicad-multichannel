"""Pure geometry used by planning and routing directives."""

from __future__ import annotations

import math
from collections import defaultdict

from .models import BoardTransform, Origin, Point


def normalize_angle(rotation: float) -> float:
    normalized = (rotation + 180.0) % 360.0 - 180.0
    return 180.0 if abs(normalized + 180.0) <= 1e-9 else normalized


def angular_distance(left: float, right: float) -> float:
    return abs(normalize_angle(left - right))


def _circular_mean(rotations: list[float]) -> float:
    sin_sum = sum(math.sin(math.radians(rotation)) for rotation in rotations)
    cos_sum = sum(math.cos(math.radians(rotation)) for rotation in rotations)
    if abs(sin_sum) <= 1e-12 and abs(cos_sum) <= 1e-12:
        return normalize_angle(rotations[0])
    return normalize_angle(math.degrees(math.atan2(sin_sum, cos_sum)))


def transform_point(point: Point, transform: BoardTransform) -> Point:
    x, y = point
    center_x, center_y = transform.center
    dx, dy = transform.translation
    rel_x = x - center_x
    rel_y = y - center_y
    rot_rad = math.radians(transform.rotation)
    return (
        int(round(center_x + rel_x * math.cos(rot_rad) + rel_y * math.sin(rot_rad) + dx)),
        int(round(center_y - rel_x * math.sin(rot_rad) + rel_y * math.cos(rot_rad) + dy)),
    )


def transform_from_origins(source: Origin, target: Origin) -> BoardTransform:
    rotation = normalize_angle(target[2] - source[2])
    return BoardTransform(
        center=(source[0], source[1]),
        translation=(target[0] - source[0], target[1] - source[1]),
        rotation=rotation,
    )


def calculate_transform(
    source_position: Point,
    source_rotation: float,
    target_position: Point,
    target_rotation: float,
    center: Point,
) -> BoardTransform:
    rotation = normalize_angle(target_rotation - source_rotation)

    source_x, source_y = source_position
    center_x, center_y = center
    rel_x = source_x - center_x
    rel_y = source_y - center_y
    rot_rad = math.radians(rotation)
    rotated_x = rel_x * math.cos(rot_rad) + rel_y * math.sin(rot_rad)
    rotated_y = -rel_x * math.sin(rot_rad) + rel_y * math.cos(rot_rad)
    return BoardTransform(
        center=center,
        translation=(
            int(round(target_position[0] - (center_x + rotated_x))),
            int(round(target_position[1] - (center_y + rotated_y))),
        ),
        rotation=rotation,
    )


def _round_to_nearest_90(rotation: float) -> int:
    rounded = round((rotation % 360) / 90) * 90
    if rounded >= 360:
        rounded = 0
    if rounded > 180:
        rounded -= 360
    return int(normalize_angle(rounded))


def choose_consensus_transform(
    transforms: list[BoardTransform],
    bucket_size_nm: int,
) -> BoardTransform:
    if not transforms:
        raise ValueError("at least one transform is required")
    if bucket_size_nm <= 0:
        raise ValueError("bucket size must be positive")

    buckets: dict[tuple[int, int, int], list[BoardTransform]] = defaultdict(list)
    for transform in transforms:
        dx, dy = transform.translation
        key = (
            round(dx / bucket_size_nm),
            round(dy / bucket_size_nm),
            _round_to_nearest_90(transform.rotation),
        )
        buckets[key].append(transform)

    best_bucket = max(buckets.values(), key=len)
    return BoardTransform(
        center=transforms[0].center,
        translation=(
            int(round(sum(item.translation[0] for item in best_bucket) / len(best_bucket))),
            int(round(sum(item.translation[1] for item in best_bucket) / len(best_bucket))),
        ),
        rotation=_circular_mean([item.rotation for item in best_bucket]),
    )
