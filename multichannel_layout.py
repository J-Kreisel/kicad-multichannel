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


def _matched_offsets(pairs: list[Match], source: Channel, target: Channel) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in pairs:
        if not p.is_matched:
            continue
        src = source.fp_by_ref.get(p.from_ref)
        dst = target.fp_by_ref.get(p.to_ref)
        if src is None or dst is None:
            continue
        out.append((dst.x - src.x, dst.y - src.y))
    return out


def _choose_offset(offsets: list[tuple[int, int]], bucket_size_nm: int) -> tuple[int, int] | None:
    if not offsets:
        return None

    buckets: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for dx, dy in offsets:
        key = (round(dx / bucket_size_nm), round(dy / bucket_size_nm))
        buckets[key].append((dx, dy))

    mean_dx = sum(o[0] for o in offsets) / len(offsets)
    mean_dy = sum(o[1] for o in offsets) / len(offsets)

    def bucket_avg(items: list[tuple[int, int]]) -> tuple[float, float]:
        return sum(i[0] for i in items) / len(items), sum(i[1] for i in items) / len(items)

    def dist_to_mean(items: list[tuple[int, int]]) -> float:
        ax, ay = bucket_avg(items)
        return math.hypot(ax - mean_dx, ay - mean_dy)

    best_items = max(buckets.values(), key=lambda items: (len(items), -dist_to_mean(items)))
    avg_dx, avg_dy = bucket_avg(best_items)
    return int(round(avg_dx)), int(round(avg_dy))


def _process_target(main: Channel, target: Channel, bucket_size_nm: int, dry_run: bool, verbose: bool, board) -> int:
    pairs = match_channels(main, target)
    offsets = _matched_offsets(pairs, main, target)
    offset = _choose_offset(offsets, bucket_size_nm)

    print(f"\n{main.name} -> {target.name}")
    print(f"  matched: {sum(1 for p in pairs if p.is_matched)}/{len(main.footprints)} source")
    unmatched_src = [p.from_ref for p in pairs if p.from_ref and not p.to_ref]
    unmatched_tgt = [p.to_ref for p in pairs if p.to_ref and not p.from_ref]
    if unmatched_src:
        print(f"  unmatched source: {unmatched_src}")
    if unmatched_tgt:
        print(f"  unmatched target: {unmatched_tgt}")

    if offset is None:
        print("  no confident offset, skipped")
        return 0

    print(f"  chosen offset: ({offset[0]:,}, {offset[1]:,}) nm")

    if verbose:
        print(f"  {'SOURCE':<12} {'TARGET':<12} {'INDIV_OFFSET':>18} {'SCORE':>8}")
        print(f"  {'-'*56}")
        for p in pairs:
            if not p.is_matched:
                continue
            src_fp = main.fp_by_ref.get(p.from_ref)
            dst_fp = target.fp_by_ref.get(p.to_ref)
            if src_fp is None or dst_fp is None:
                continue
            ind_dx = dst_fp.x - src_fp.x
            ind_dy = dst_fp.y - src_fp.y
            print(f"  {p.from_ref:<12} {p.to_ref:<12} ({ind_dx:>10,}, {ind_dy:>6,})  {p.score:>7.3f}")

    if not dry_run:
        all_fps = list(board.get_footprints())
        fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

        commit = board.begin_commit()
        updated_wrappers: list[object] = []
        for p in pairs:
            if not p.is_matched:
                continue
            src_fp = main.fp_by_ref.get(p.from_ref)
            if src_fp is None:
                continue
            fp = fp_by_ref.get(p.to_ref)
            if fp is None:
                continue
            new_x = (src_fp.x + offset[0]) / 1_000_000
            new_y = (src_fp.y + offset[1]) / 1_000_000
            fp.position = geom.Vector2.from_xy_mm(new_x, new_y)
            fp.orientation = geom.Angle.from_degrees(src_fp.orientation)
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
