"""Preview/apply orchestration shared by frontends; no GUI dependencies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from kipy.proto.common.types import KiCadObjectType

from .domain.models import UpdatePlan
from .domain.planner import UpdatePlanner
from .kicad.copy_executor import KiCadBoardExecutor
from .kicad.membership_reader import read_membership_board
from .kicad.reader import read_board_snapshot, read_channel_summaries
from .membership.executor import MembershipExecutor
from .membership.models import MembershipPlan
from .membership.planner import build_membership_plan


def copy_fingerprint(board) -> str:
    """Include complete serialized items (widths, layers, locks, pad shapes, etc.)."""
    types = [
        KiCadObjectType.KOT_PCB_FOOTPRINT,
        KiCadObjectType.KOT_PCB_TRACE,
        KiCadObjectType.KOT_PCB_VIA,
        KiCadObjectType.KOT_PCB_GROUP,
    ]
    items = sorted(item.proto.SerializeToString(deterministic=True)
                   for item in board.get_items(types=types))
    digest = hashlib.sha256(str(board.document).encode())
    for item in items:
        digest.update(len(item).to_bytes(8, "big"))
        digest.update(item)
    return digest.hexdigest()


@dataclass(frozen=True)
class Preview:
    board_name: str
    fingerprint: str
    copy_plans: tuple[UpdatePlan, ...] = ()
    membership_plan: MembershipPlan | None = None
    notes: tuple[str, ...] = ()


def list_channels(board):
    summaries = read_channel_summaries(board)
    names = [name for name, count in summaries if count]
    if len(names) != len(set(names)) or "<unnamed>" in names:
        raise ValueError("Give each channel group a unique, nonempty name in KiCad.")
    return [(name, count) for name, count in summaries if count]


def preview_copy(board, source, targets, bucket_size=500_000, ignored=()):
    if bucket_size <= 0 or not targets or source in targets:
        raise ValueError("Select a source, different targets, and a positive bucket size.")
    list_channels(board)
    before = copy_fingerprint(board)
    channels = list(read_board_snapshot(board).channels)
    available = {channel.name for channel in channels}
    if not {source, *targets} <= available:
        raise ValueError("A selected channel no longer exists. Refresh the channel list.")
    for channel in channels:
        channel.footprints = [fp for fp in channel.footprints if fp.reference not in ignored]
    planner = UpdatePlanner(channels, bucket_size)
    plans, notes = [], []
    for target in dict.fromkeys(targets):
        plan = planner.build_update_plan(source, target)
        if plan is None:
            notes.append(f"{target}: no matched footprints; nothing to apply.")
        else:
            plans.append(plan)
            channel = next(c for c in channels if c.name == target)
            unmatched = len(channel.footprints) - len(plan.footprint_matches)
            locked = sum(fp.locked for fp in channel.footprints)
            notes.append(f"{target}: {unmatched} unmatched target footprints; {locked} locked.")
    if copy_fingerprint(board) != before:
        raise RuntimeError("Board changed while planning. Generate a new preview.")
    return Preview(str(board.name), before, tuple(plans), notes=tuple(notes))


def preview_membership(board, group, mode):
    list_channels(board)
    plan = build_membership_plan(read_membership_board(board), group, mode)
    return Preview(str(board.name), copy_fingerprint(board), membership_plan=plan)


class ApplyError(RuntimeError):
    """An apply failure carrying results for targets already completed."""

    def __init__(self, message, completed):
        super().__init__(message)
        self.completed = tuple(completed)


def apply_preview(board, preview):
    if str(board.name) != preview.board_name or copy_fingerprint(board) != preview.fingerprint:
        raise ValueError("Board changed since preview. Generate a new preview before applying.")
    if preview.membership_plan is not None:
        return (MembershipExecutor(board).apply(preview.membership_plan),)
    results = []
    executor = KiCadBoardExecutor(board)
    for plan in preview.copy_plans:
        try:
            results.append(executor.apply(plan))
        except Exception as error:
            raise ApplyError(
                f"{plan.target_channel}: {error}. Earlier targets may already be applied. "
                "Inspect the PCB; undo through 'Update multichannel objects' if needed.",
                results,
            ) from error
    return tuple(results)
