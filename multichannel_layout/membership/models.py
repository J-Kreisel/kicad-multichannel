"""KiCad-independent routing connectivity models."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import GroupSnapshot


Point = tuple[int, int]


@dataclass(frozen=True)
class MembershipPad:
    item_id: str
    footprint_id: str
    net: str
    position: Point
    layers: frozenset[int]


@dataclass(frozen=True)
class CopperItem:
    item_id: str
    net: str
    kind: str
    points: tuple[Point, ...]
    layers: frozenset[int]
    locked: bool


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


@dataclass(frozen=True)
class MembershipResult:
    group_name: str
    added_count: int
    removed_count: int
