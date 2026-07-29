"""Read KiCad board groups as channels containing only footprints."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from kipy.board import Board
from kipy.proto.board.board_types_pb2 import Pad as ProtoPad
from kipy.proto.common.types import KiCadObjectType

_log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


@dataclass
class Footprint:
    reference: str
    footprint_id: str
    position: tuple[int, int]  # nm
    orientation: float         # degrees
    layer: int
    pads: dict[str, int] = field(default_factory=dict)  # pad name -> type
    pad_nets: dict[str, str] = field(default_factory=dict)  # pad name -> net name
    kid: str = ""
    raw: object = None

    @property
    def x(self) -> int:
        return self.position[0]

    @property
    def y(self) -> int:
        return self.position[1]


@dataclass
class Channel:
    name: str
    group_kid: str
    footprints: list[Footprint] = field(default_factory=list)

    @property
    def fp_by_ref(self) -> dict[str, Footprint]:
        return {fp.reference: fp for fp in self.footprints}

    @property
    def fp_by_kid(self) -> dict[str, Footprint]:
        return {fp.kid: fp for fp in self.footprints}

    @property
    def center(self) -> tuple[int, int]:
        """Geometric center of all footprints (average position)."""
        if not self.footprints:
            return (0, 0)
        avg_x = sum(fp.x for fp in self.footprints) // len(self.footprints)
        avg_y = sum(fp.y for fp in self.footprints) // len(self.footprints)
        return (avg_x, avg_y)


def _kid(obj) -> str:
    s = ""
    if hasattr(obj, "value"):
        s = str(obj.value)
    if not s:
        s = str(obj)
    m = _UUID_RE.search(s)
    return m.group(1) if m else s.strip()


def _nm(point) -> tuple[int, int]:
    x = getattr(point, "x_nm", getattr(point, "x", 0)) or 0
    y = getattr(point, "y_nm", getattr(point, "y", 0)) or 0
    return int(x), int(y)


def _nm_value(msg) -> int:
    return int(getattr(msg, "value_nm", 0) or 0)


def _degrees(angle) -> float:
    return float(getattr(angle, "value_degrees", 0.0) or 0.0)


def _field_text(field) -> str:
    try:
        return str(field.text.text.text)
    except Exception:
        try:
            return str(field.text.text)
        except Exception:
            try:
                return str(field.text)
            except Exception:
                return str(field)


def _footprint_id(definition) -> str:
    def_id = definition.id
    nick = str(def_id.library_nickname).strip()
    entry = str(def_id.entry_name).strip()
    if nick and entry:
        return f"{nick}:{entry}"
    return entry or nick or ""


def _extract_footprint(fp_wrapper) -> Footprint | None:
    try:
        p = fp_wrapper.proto
        reference = _field_text(p.reference_field)
        pads: dict[str, int] = {}
        pad_nets: dict[str, str] = {}

        for item in p.definition.items:
            if not item.type_url.endswith("kiapi.board.types.Pad"):
                continue
            proto_pad = ProtoPad()
            item.Unpack(proto_pad)
            name = str(proto_pad.number)
            pads[name] = int(proto_pad.type)
            if proto_pad.net and proto_pad.net.name:
                pad_nets[name] = str(proto_pad.net.name)

        return Footprint(
            reference=reference,
            footprint_id=_footprint_id(p.definition),
            position=_nm(p.position),
            orientation=_degrees(p.orientation),
            layer=int(p.layer),
            pads=pads,
            pad_nets=pad_nets,
            kid=_kid(p.id),
            raw=fp_wrapper,
        )
    except Exception as exc:
        _log.warning("footprint extraction failed: %s", exc)
        return None


def read_channels(board: Board) -> list[Channel]:
    """Return one Channel per board group that contains footprints."""
    fps = {
        fp.kid: fp
        for fp in (_extract_footprint(f) for f in board.get_items(types=[KiCadObjectType.KOT_PCB_FOOTPRINT]))
        if fp is not None
    }

    groups = board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    channels: list[Channel] = []
    for g in groups:
        name = str(g.proto.name).strip() or "<unnamed>"
        member_kids = [_kid(kiid) for kiid in g.proto.items]
        group_fps = [fps[k] for k in member_kids if k in fps]
        if not group_fps:
            continue
        channels.append(Channel(name=name, group_kid=_kid(g.proto.id), footprints=group_fps))

    _log.info("read_channels: %d groups, %d channels, %d footprints", len(groups), len(channels), len(fps))
    return channels
