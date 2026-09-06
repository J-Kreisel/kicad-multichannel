"""KiCad-independent routing connectivity models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..domain.models import GroupSnapshot


Point = tuple[int, int]


@dataclass(frozen=True)
class PolygonRegion:
    layer: int
    outline: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True)
class MembershipPad:
    item_id: str
    footprint_id: str
    net: str
    position: Point
    layers: frozenset[int]
    regions: tuple[PolygonRegion, ...] = ()


@dataclass(frozen=True)
class CopperItem:
    item_id: str
    net: str
    kind: str
    points: tuple[Point, ...]
    layers: frozenset[int]
    locked: bool
    width: int = 0
    mid: Point | None = None
    regions: tuple[PolygonRegion, ...] = ()


@dataclass(frozen=True)
class MembershipBoard:
    groups: tuple[GroupSnapshot, ...]
    pads: tuple[MembershipPad, ...]
    copper: tuple[CopperItem, ...]


@dataclass(frozen=True)
class MembershipPlan:
    group_name: str
    add_item_ids: tuple[str, ...] = ()
    remove_item_ids: tuple[str, ...] = ()
    mixed_net_count: int = 0
    island_count: int = 0
    locked_item_count: int = 0
    board_fingerprint: str = ""


@dataclass(frozen=True)
class MembershipResult:
    group_name: str
    added_count: int
    removed_count: int


def board_fingerprint(board: MembershipBoard) -> str:
    groups = tuple(
        sorted((group.group_id, group.name, tuple(sorted(group.member_ids))) for group in board.groups)
    )
    pads = tuple(sorted(board.pads, key=lambda pad: pad.item_id))
    copper = tuple(sorted(board.copper, key=lambda item: item.item_id))
    return hashlib.sha256(repr((groups, pads, copper)).encode("utf-8")).hexdigest()
