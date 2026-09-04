"""KiCad-independent board snapshots and update directives."""

from __future__ import annotations

from dataclasses import dataclass, field


Point = tuple[int, int]
Origin = tuple[int, int, float]
ReferencePair = tuple[str, str]
NetPair = tuple[str, str]


@dataclass
class Footprint:
    reference: str
    footprint_id: str
    position: Point
    orientation: float
    layer: int
    pads: dict[str, int] = field(default_factory=dict)
    pad_nets: dict[str, str] = field(default_factory=dict)
    pad_positions: dict[str, Point] = field(default_factory=dict)
    pad_offsets: dict[str, Point] = field(default_factory=dict)
    kid: str = ""
    locked: bool = False

    @property
    def x(self) -> int:
        return self.position[0]

    @property
    def y(self) -> int:
        return self.position[1]


@dataclass(frozen=True)
class Track:
    kid: str
    net: str
    start: Point
    end: Point


@dataclass(frozen=True)
class Via:
    kid: str
    net: str
    position: Point


@dataclass
class Channel:
    name: str
    group_kid: str
    footprints: list[Footprint] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)

    @property
    def fp_by_ref(self) -> dict[str, Footprint]:
        return {fp.reference: fp for fp in self.footprints}

    @property
    def fp_by_kid(self) -> dict[str, Footprint]:
        return {fp.kid: fp for fp in self.footprints}

    @property
    def center(self) -> Point:
        if not self.footprints:
            return (0, 0)
        return (
            sum(fp.x for fp in self.footprints) // len(self.footprints),
            sum(fp.y for fp in self.footprints) // len(self.footprints),
        )


@dataclass(frozen=True)
class GroupSnapshot:
    group_id: str
    name: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class BoardSnapshot:
    channels: tuple[Channel, ...]
    groups: tuple[GroupSnapshot, ...]


@dataclass(frozen=True)
class BoardTransform:
    center: Point
    translation: Point
    rotation: float


@dataclass(frozen=True)
class UpdatePlan:
    source_channel: str
    target_channel: str
    source_origin: Origin
    target_origin: Origin
    footprint_matches: tuple[ReferencePair, ...]
    net_matches: tuple[NetPair, ...]


@dataclass(frozen=True)
class UpdateResult:
    source_channel: str
    target_channel: str
    footprint_count: int
    track_count: int
    via_count: int
    locked_footprint_count: int = 0
    locked_net_count: int = 0
