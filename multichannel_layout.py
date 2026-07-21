#!/usr/bin/env python3
"""Copy footprint placement from one channel (first named) to the others.

Examples:

    # just print matchings and computed offsets
    python multichannel_layout.py CH1 CH2 CH3 CH4 --dry-run

    # actually copy relative placements to CH2, CH3, CH4
    python multichannel_layout.py CH1 CH2 CH3 CH4
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections import defaultdict

from kipy import KiCad
from kipy.errors import ConnectionError as KipyConnectionError
import kipy.geometry as geom

from multichannel_layout.board_reader import Channel, read_channels
from multichannel_layout.matching import Match, match_channels


def _find_channel(channels: list[Channel], name: str) -> Channel | None:
    exact = [c for c in channels if c.name == name]
    if len(exact) == 1:
        return exact[0]
    matches = [c for c in channels if name.lower() in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def _calculate_rotation(src_rot: float, dst_rot: float) -> float:
    """Calculate rotation difference, normalized to [-180, 180]."""
    rotation = dst_rot - src_rot
    while rotation > 180:
        rotation -= 360
    while rotation < -180:
        rotation += 360
    return rotation


def _compute_translation(src_center_x: int, src_center_y: int, dst_center_x: int, dst_center_y: int, rotation: float) -> tuple[int, int]:
    """Compute translation (dx, dy) from source center to target center.
    
    Translation is applied after per-point rotation in _apply_transform, so
    no rotation is needed here — it's just the center-to-center delta."""
    dx = int(round(dst_center_x - src_center_x))
    dy = int(round(dst_center_y - src_center_y))
    return dx, dy


def _apply_transform(x: int, y: int, rot: float, center_x: int, center_y: int, dx: int, dy: int, rotation: float) -> tuple[float, float, float]:
    """Apply a transform to a point/orientation around a center.
    
    KiCad uses flipped Y coordinates (Y increases downward) with CCW rotation.
    Adjusted rotation formulas account for this.
    
    Returns (new_x_mm, new_y_mm, new_rot_deg).
    """
    # Vector from center to point
    rel_x = x - center_x
    rel_y = y - center_y
    
    # Rotate the vector
    # KiCad has flipped Y (increases downward), negate sin terms for CCW on screen
    rot_rad = math.radians(rotation)
    new_rel_x = rel_x * math.cos(rot_rad) + rel_y * math.sin(rot_rad)
    new_rel_y = -rel_x * math.sin(rot_rad) + rel_y * math.cos(rot_rad)
    
    # New position: center + rotated vector + translation
    new_x = (center_x + new_rel_x + dx) / 1_000_000
    new_y = (center_y + new_rel_y + dy) / 1_000_000
    
    # New orientation
    new_rot = rot + rotation
    
    return new_x, new_y, new_rot


def _calculate_transform(src_x: int, src_y: int, src_rot: float, dst_x: int, dst_y: int, dst_rot: float, center_x: int, center_y: int) -> tuple[int, int, float]:
    """Calculate the transform (dx, dy, rotation) from source to destination around a center."""
    rotation = dst_rot - src_rot
    while rotation > 180:
        rotation -= 360
    while rotation < -180:
        rotation += 360

    v1_x = src_x - center_x
    v1_y = src_y - center_y

    rot_rad = math.radians(rotation)
    rot_x = v1_x * math.cos(rot_rad) + v1_y * math.sin(rot_rad)
    rot_y = - v1_x * math.sin(rot_rad) + v1_y * math.cos(rot_rad)

    dx = dst_x - (center_x + rot_x)
    dy = dst_y - (center_y + rot_y)

    return int(round(dx)), int(round(dy)), rotation


def _round_to_nearest_90(rotation: float) -> int:
    """Round rotation to nearest cardinal angle: -180, -90, 0, 90, 180."""
    rot = rotation % 360
    rounded = round(rot / 90) * 90
    if rounded >= 360:
        rounded = 0
    if rounded > 180:
        rounded -= 360
    return int(rounded)

    # Find best bucket by count
    best_bucket = max(buckets.values(), key=len)
    
    # Average the values in the best bucket
    avg_dx = sum(o[0] for o in best_bucket) / len(best_bucket)
    avg_dy = sum(o[1] for o in best_bucket) / len(best_bucket)
    avg_rot = sum(o[2] for o in best_bucket) / len(best_bucket)
    
    return int(round(avg_dx)), int(round(avg_dy)), avg_rot


def _process_target(main: Channel, target: Channel, bucket_size_nm: int, dry_run: bool, verbose: bool, board) -> int:
    pairs = match_channels(main, target)
    
    # Calculate center from matched source footprints
    matched_src_fps = [main.fp_by_ref[p.from_ref] for p in pairs if p.is_matched and p.from_ref in main.fp_by_ref]
    if not matched_src_fps:
        print(f"\n{main.name} -> {target.name}")
        print("  no matched footprints")
        return 0
    
    center_x = sum(fp.x for fp in matched_src_fps) // len(matched_src_fps)
    center_y = sum(fp.y for fp in matched_src_fps) // len(matched_src_fps)

    # Calculate rotations for each pair
    rotations: list[float] = []
    for p in pairs:
        if not p.is_matched:
            continue
        src = main.fp_by_ref.get(p.from_ref)
        dst = target.fp_by_ref.get(p.to_ref)
        if src is None or dst is None:
            continue
        rotations.append(_calculate_rotation(src.orientation, dst.orientation))

    print(f"\n{main.name} -> {target.name}")
    print(f"  center: ({center_x:,}, {center_y:,}) nm")
    print(f"  matched: {len(rotations)}/{len(main.footprints)} source")
    
    unmatched_src = [p.from_ref for p in pairs if p.from_ref and not p.to_ref]
    unmatched_tgt = [p.to_ref for p in pairs if p.to_ref and not p.from_ref]
    if unmatched_src:
        print(f"  unmatched source: {unmatched_src}")
    if unmatched_tgt:
        print(f"  unmatched target: {unmatched_tgt}")

    if not rotations:
        print("  no confident offset, skipped")
        return 0

    # Bucket rotations to find consensus
    rot_buckets: dict[int, list[float]] = defaultdict(list)
    for rot in rotations:
        rot_buckets[_round_to_nearest_90(rot)].append(rot)
    
    best_rot_key = max(rot_buckets, key=lambda k: len(rot_buckets[k]))
    avg_rot = sum(rot_buckets[best_rot_key]) / len(rot_buckets[best_rot_key])

    # Compute target center from matched pairs
    dst_center_x = sum(dst.x for _, dst in [(p, target.fp_by_ref.get(p.to_ref)) for p in pairs if p.is_matched and p.to_ref in target.fp_by_ref]) // len(rotations)
    dst_center_y = sum(dst.y for _, dst in [(p, target.fp_by_ref.get(p.to_ref)) for p in pairs if p.is_matched and p.to_ref in target.fp_by_ref]) // len(rotations)

    # Translation from centers
    avg_dx, avg_dy = _compute_translation(center_x, center_y, dst_center_x, dst_center_y, avg_rot)

    print(f"  chosen offset: ({avg_dx:,}, {avg_dy:,}) nm, rotation: {avg_rot:.1f}°")

    if verbose:
        print(f"  {'SOURCE':<12} {'TARGET':<12} {'INDIV_OFFSET':>18} {'ROT':>8} {'SCORE':>8}")
        print(f"  {'-'*64}")
        for p in pairs:
            if not p.is_matched:
                continue
            src = main.fp_by_ref.get(p.from_ref)
            dst = target.fp_by_ref.get(p.to_ref)
            if src is None or dst is None:
                continue
            t_dx, t_dy, t_rot = _calculate_transform(src.x, src.y, src.orientation, dst.x, dst.y, dst.orientation, center_x, center_y)
            print(f"  {p.from_ref:<12} {p.to_ref:<12} ({t_dx:>10,}, {t_dy:>6,})  {t_rot:>7.1f}° {p.score:>7.3f}")

    if not dry_run:
        all_fps = list(board.get_footprints())
        fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

        commit = board.begin_commit()
        updated_wrappers: list[object] = []
        
        for p in pairs:
            if not p.is_matched:
                continue
            src = main.fp_by_ref.get(p.from_ref)
            if src is None:
                continue
            fp = fp_by_ref.get(p.to_ref)
            if fp is None:
                continue
            
            # Apply transform using consensus values
            new_x, new_y, new_rot = _apply_transform(src.x, src.y, src.orientation, center_x, center_y, avg_dx, avg_dy, avg_rot)
            
            fp.position = geom.Vector2.from_xy_mm(new_x, new_y)
            fp.orientation = geom.Angle.from_degrees(new_rot)
            updated_wrappers.append(fp)

        if updated_wrappers:
            try:
                board.update_items(updated_wrappers)
                board.push_commit(commit)
                print(f"  updated {len(updated_wrappers)} footprints")
            except Exception as exc:
                print(f"  Error applying changes: {exc}")
                board.drop_commit(commit)
                return 0
        else:
            print("  no footprints to update")
        return len(updated_wrappers)

    return sum(1 for p in pairs if p.is_matched)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy footprint placement from the first channel to all other named channels."
    )
    parser.add_argument("channels", nargs="*", help="First channel = source; remaining = targets")
    parser.add_argument("--dry-run", action="store_true", help="Print matchings/offsets without writing")
    parser.add_argument("--verbose", action="store_true", help="Print per-footprint matchings and individual offsets")
    parser.add_argument("--bucket-size", type=int, default=500_000, help="Offset bucket size in nm (default 500000 = 0.5mm)")
    parser.add_argument("--list", action="store_true", help="List channel groups and exit")
    parser.add_argument("--ignore", type=str, default="", help="Comma-separated list of footprint references to ignore (e.g., 'R1,R2,C10')")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        kicad = KiCad(client_name="io.github.johannes.kicad-multichannel-layout")
    except KipyConnectionError as exc:
        print(f"Could not connect to KiCad: {exc}", file=sys.stderr)
        print("Is the PCB editor running and the KiCad API enabled?", file=sys.stderr)
        return 1

    try:
        board = kicad.get_board()
    except Exception as exc:
        print(f"No board open: {exc}", file=sys.stderr)
        return 2

    channels = read_channels(board)
    if not channels:
        print("No channel groups with footprints found.", file=sys.stderr)
        return 3

    # Parse and apply ignore list
    ignore_set = set(ref.strip() for ref in args.ignore.split(",") if ref.strip())
    if ignore_set:
        print(f"Ignoring footprints: {sorted(ignore_set)}")
        for ch in channels:
            original_count = len(ch.footprints)
            ch.footprints = [fp for fp in ch.footprints if fp.reference not in ignore_set]
            if len(ch.footprints) < original_count:
                print(f"  {ch.name}: removed {original_count - len(ch.footprints)} footprint(s)")

    if args.list:
        print("Channels:")
        for ch in channels:
            print(f"  {ch.name} ({len(ch.footprints)} footprints)")
        return 0

    if len(args.channels) < 2:
        print("Give at least one source and one target channel.", file=sys.stderr)
        return 4

    main_name = args.channels[0]
    target_names = args.channels[1:]

    main = _find_channel(channels, main_name)
    if main is None:
        print(f"Source channel {main_name!r} not found.", file=sys.stderr)
        print("Channels:")
        for ch in channels:
            print(f"  {ch.name}")
        return 5

    targets: list[Channel] = []
    for name in target_names:
        t = _find_channel(channels, name)
        if t is None:
            print(f"Target channel {name!r} not found.", file=sys.stderr)
            return 6
        if t is main:
            print(f"Source and target {name!r} must be different.", file=sys.stderr)
            return 7
        targets.append(t)

    updated_total = 0
    for target in targets:
        updated_total += _process_target(main, target, args.bucket_size, args.dry_run, args.verbose, board)

    mode = "dry-run" if args.dry_run else "applied"
    print(f"\nTotal footprints {mode}: {updated_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
