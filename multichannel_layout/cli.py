"""Command-line orchestration for multichannel layout operations."""

from __future__ import annotations

import argparse
import logging
import sys

from kipy import KiCad
from kipy.errors import ConnectionError as KipyConnectionError

from .domain.models import Channel, UpdatePlan
from .kicad.reader import read_board_snapshot, read_channel_summaries
from .membership.models import MembershipPlan
from . import service


def _find_channel(channels: list[Channel], name: str) -> Channel | None:
    exact = [channel for channel in channels if channel.name == name]
    if len(exact) == 1:
        return exact[0]
    matches = [channel for channel in channels if name.lower() in channel.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _print_copy_plan(plan: UpdatePlan, verbose: bool) -> None:
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


def _print_membership_plan(plan: MembershipPlan, verbose: bool) -> None:
    print(f"\n{plan.group_name}")
    print(f"  mixed nets: {plan.mixed_net_count}")
    print(f"  candidate copper islands: {plan.island_count}")
    print(f"  add routing items: {len(plan.add_item_ids)}")
    print(f"  remove routing items: {len(plan.remove_item_ids)}")
    print(f"  locked routing items observed: {plan.locked_item_count}")
    if verbose and plan.add_item_ids:
        print("  add:")
        for item_id in plan.add_item_ids:
            print(f"    {item_id}")
    if verbose and plan.remove_item_ids:
        print("  remove:")
        for item_id in plan.remove_item_ids:
            print(f"    {item_id}")


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without modifying the board",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print individual planned matches or routing UUIDs",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy multichannel layout and repair routing group membership."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    copy_parser = commands.add_parser("copy", help="Copy placement and routing")
    copy_parser.add_argument(
        "channels",
        nargs="+",
        help="First channel = source; remaining = targets",
    )
    copy_parser.add_argument(
        "--bucket-size",
        type=int,
        default=500_000,
        help="Offset bucket size in nm (default 500000 = 0.5mm)",
    )
    copy_parser.add_argument(
        "--ignore",
        type=str,
        default="",
        help="Comma-separated footprint references to ignore",
    )
    _add_common_flags(copy_parser)

    add_parser = commands.add_parser(
        "add-membership",
        help="Add routing needed to connect a group's internal pads",
    )
    add_parser.add_argument("group", help="Group/channel name")
    _add_common_flags(add_parser)

    remove_parser = commands.add_parser(
        "remove-membership",
        help="Remove routing not needed to connect a group's internal pads",
    )
    remove_parser.add_argument("group", help="Group/channel name")
    _add_common_flags(remove_parser)

    commands.add_parser("list", help="List channel groups")
    args = parser.parse_args(argv)
    if args.command == "copy":
        if len(args.channels) < 2:
            copy_parser.error("give at least one source and one target channel")
        if args.bucket_size <= 0:
            copy_parser.error("--bucket-size must be positive")
    return args


def _run_copy(args, board, channels: list[Channel]) -> int:
    names = []
    for name in args.channels:
        channel = _find_channel(channels, name)
        if channel is None:
            print(f"Channel {name!r} not found or ambiguous.", file=sys.stderr)
            return 5
        names.append(channel.name)
    try:
        preview = service.preview_copy(
            board, names[0], names[1:], args.bucket_size,
            {ref.strip() for ref in args.ignore.split(",") if ref.strip()},
        )
        for plan in preview.copy_plans:
            _print_copy_plan(plan, args.verbose)
        for note in preview.notes:
            print(note)
        if not args.dry_run:
            for result in service.apply_preview(board, preview):
                print(result)
    except Exception as error:
        print(f"Copy failed: {error}", file=sys.stderr)
        return 8
    return 0


def _run_membership(args, board, mode: str) -> int:
    try:
        preview = service.preview_membership(board, args.group, mode)
        plan = preview.membership_plan
    except Exception as exc:
        print(f"Cannot plan membership update: {exc}", file=sys.stderr)
        return 9
    _print_membership_plan(plan, args.verbose)
    if args.dry_run:
        return 0
    try:
        result = service.apply_preview(board, preview)[0]
    except Exception as exc:
        print(f"Error applying membership update: {exc}", file=sys.stderr)
        return 9
    print(
        f"  applied: added {result.added_count}, removed {result.removed_count} "
        "routing memberships"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        kicad = KiCad(client_name="io.github.johannes.kicad-multichannel-layout")
    except KipyConnectionError as exc:
        print(f"Could not connect to KiCad: {exc}", file=sys.stderr)
        return 1
    try:
        board = kicad.get_board()
    except Exception as exc:
        print(f"No board open: {exc}", file=sys.stderr)
        return 2

    if args.command == "add-membership":
        return _run_membership(args, board, "add")
    if args.command == "remove-membership":
        return _run_membership(args, board, "remove")

    if args.command == "list":
        print("Channels:")
        for name, footprint_count in read_channel_summaries(board):
            print(f"  {name} ({footprint_count} footprints)")
        return 0
    try:
        snapshot = read_board_snapshot(board)
    except Exception as exc:
        print(f"Could not safely read board: {exc}", file=sys.stderr)
        return 3
    channels = list(snapshot.channels)
    if not channels:
        print("No channel groups with footprints found.", file=sys.stderr)
        return 3
    return _run_copy(args, board, channels)
