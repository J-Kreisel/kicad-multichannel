"""Command-line orchestration for the multichannel layout tool."""

from __future__ import annotations

import argparse
import logging
import sys

from kipy import KiCad
from kipy.errors import ConnectionError as KipyConnectionError

from .board_reader import read_board_snapshot
from .executor import KiCadBoardExecutor
from .models import Channel, UpdatePlan
from .planner import UpdatePlanner


def _find_channel(channels: list[Channel], name: str) -> Channel | None:
    exact = [channel for channel in channels if channel.name == name]
    if len(exact) == 1:
        return exact[0]
    matches = [channel for channel in channels if name.lower() in channel.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _print_plan(plan: UpdatePlan, verbose: bool) -> None:
    print(f"\n{plan.source_channel} -> {plan.target_channel}")
    source_x, source_y, source_rotation = plan.source_origin
    target_x, target_y, target_rotation = plan.target_origin
    print(
        f"  source origin: ({source_x:,}, {source_y:,}) nm, "
        f"rotation: {source_rotation:.1f}°"
    )
    print(
        f"  target origin: ({target_x:,}, {target_y:,}) nm, "
        f"rotation: {target_rotation:.1f}°"
    )
    print(f"  footprint matches: {len(plan.footprint_matches)}")
    print(f"  verified net matches: {len(plan.net_matches)}")

    if verbose:
        print(f"  {'SOURCE FOOTPRINT':<24} {'TARGET FOOTPRINT':<24}")
        for source_ref, target_ref in plan.footprint_matches:
            print(f"  {source_ref:<24} {target_ref:<24}")
        print(f"  {'SOURCE NET':<40} {'TARGET NET':<40}")
        for source_net, target_net in plan.net_matches:
            print(f"  {source_net:<40} {target_net:<40}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy placement and verified routing from the first channel to the others."
    )
    parser.add_argument(
        "channels",
        nargs="*",
        help="First channel = source; remaining = targets",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matchings/offsets without writing",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print footprint and net match pairs",
    )
    parser.add_argument(
        "--bucket-size",
        type=int,
        default=500_000,
        help="Offset bucket size in nm (default 500000 = 0.5mm)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List channel groups and exit",
    )
    parser.add_argument(
        "--ignore",
        type=str,
        default="",
        help="Comma-separated footprint references to ignore (e.g. 'R1,R2,C10')",
    )
    args = parser.parse_args(argv)
    if args.bucket_size <= 0:
        parser.error("--bucket-size must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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

    snapshot = read_board_snapshot(board)
    channels = list(snapshot.channels)
    if not channels:
        print("No channel groups with footprints found.", file=sys.stderr)
        return 3

    ignored = {reference.strip() for reference in args.ignore.split(",") if reference.strip()}
    if ignored:
        print(f"Ignoring footprints: {sorted(ignored)}")
        for channel in channels:
            original_count = len(channel.footprints)
            channel.footprints = [
                footprint
                for footprint in channel.footprints
                if footprint.reference not in ignored
            ]
            removed_count = original_count - len(channel.footprints)
            if removed_count:
                print(f"  {channel.name}: removed {removed_count} footprint(s)")

    if args.list:
        print("Channels:")
        for channel in channels:
            print(f"  {channel.name} ({len(channel.footprints)} footprints)")
        return 0

    if len(args.channels) < 2:
        print("Give at least one source and one target channel.", file=sys.stderr)
        return 4

    source = _find_channel(channels, args.channels[0])
    if source is None:
        print(f"Source channel {args.channels[0]!r} not found.", file=sys.stderr)
        print("Channels:")
        for channel in channels:
            print(f"  {channel.name}")
        return 5

    targets: list[Channel] = []
    for name in args.channels[1:]:
        target = _find_channel(channels, name)
        if target is None:
            print(f"Target channel {name!r} not found.", file=sys.stderr)
            return 6
        if target is source:
            print(f"Source and target {name!r} must be different.", file=sys.stderr)
            return 7
        targets.append(target)

    plans: list[UpdatePlan] = []
    planner = UpdatePlanner(channels, args.bucket_size)
    for target in targets:
        try:
            plan = planner.build_update_plan(source.name, target.name)
        except ValueError as exc:
            print(
                f"\nCannot plan {source.name} -> {target.name}: {exc}",
                file=sys.stderr,
            )
            return 8
        if plan is None:
            print(f"\n{source.name} -> {target.name}")
            print("  no matched footprints")
            continue
        plans.append(plan)
        _print_plan(plan, args.verbose)

    if args.dry_run:
        count = sum(len(plan.footprint_matches) for plan in plans)
        print(f"\nTotal footprints dry-run: {count}")
        return 0

    executor = KiCadBoardExecutor(board)
    updated_count = 0
    for plan in plans:
        try:
            result = executor.apply(plan)
        except Exception as exc:
            print(
                f"\nError applying {plan.source_channel} -> "
                f"{plan.target_channel}: {exc}",
                file=sys.stderr,
            )
            print("Review the error before continuing with another update.", file=sys.stderr)
            return 8
        updated_count += result.footprint_count
        print(
            f"  {result.target_channel}: updated {result.footprint_count} footprints, "
            f"created {result.track_count} tracks/{result.via_count} vias"
        )
        if result.locked_footprint_count or result.locked_net_count:
            print(
                f"    skipped locked: {result.locked_footprint_count} footprints, "
                f"{result.locked_net_count} routed nets"
            )
    print(f"\nTotal footprints applied: {updated_count}")
    return 0
