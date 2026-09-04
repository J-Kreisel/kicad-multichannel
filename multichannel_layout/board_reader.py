"""Read KiCad board groups as channels with footprints and simple routing."""

from __future__ import annotations

import logging
import math
import re

from kipy.board import Board
from kipy.proto.board.board_types_pb2 import Pad as ProtoPad
from kipy.proto.common.types import KiCadObjectType

from .models import BoardSnapshot, Channel, Footprint, GroupSnapshot, Track, Via

_log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


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


def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


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


def _rotate_offset(offset: tuple[int, int], orientation: float) -> tuple[int, int]:
    x, y = offset
    rot_rad = math.radians(orientation)
    return (
        int(round(x * math.cos(rot_rad) + y * math.sin(rot_rad))),
        int(round(-x * math.sin(rot_rad) + y * math.cos(rot_rad))),
    )


def _local_pad_offset(fp_position: tuple[int, int], orientation: float, pad_position) -> tuple[int, int]:
    pad_x, pad_y = _nm(pad_position)
    world_offset = (pad_x - fp_position[0], pad_y - fp_position[1])
    return _rotate_offset(world_offset, -orientation)


def _extract_footprint(fp_wrapper) -> Footprint | None:
    try:
        p = fp_wrapper.proto
        reference = _field_text(p.reference_field)
        pads: dict[str, int] = {}
        pad_nets: dict[str, str] = {}
        pad_positions: dict[str, tuple[int, int]] = {}
        pad_offsets: dict[str, tuple[int, int]] = {}
        position = _nm(p.position)
        orientation = _degrees(p.orientation)

        for item in p.definition.items:
            if not item.type_url.endswith("kiapi.board.types.Pad"):
                continue
            proto_pad = ProtoPad()
            item.Unpack(proto_pad)
            name = str(proto_pad.number)
            pads[name] = int(proto_pad.type)
            if proto_pad.net and proto_pad.net.name:
                pad_nets[name] = str(proto_pad.net.name)
            pad_positions[name] = _nm(proto_pad.position)
            pad_offsets[name] = _local_pad_offset(position, orientation, proto_pad.position)

        return Footprint(
            reference=reference,
            footprint_id=_footprint_id(p.definition),
            position=position,
            orientation=orientation,
            layer=int(p.layer),
            pads=pads,
            pad_nets=pad_nets,
            pad_positions=pad_positions,
            pad_offsets=pad_offsets,
            kid=_kid(p.id),
            locked=bool(fp_wrapper.locked),
        )
    except Exception as exc:
        _log.warning("footprint extraction failed: %s", exc)
        return None


def _extract_track(track_wrapper) -> Track | None:
    try:
        p = track_wrapper.proto
        return Track(
            kid=_kid(p.id),
            net=_net_name(p.net),
            start=_nm(p.start),
            end=_nm(p.end),
        )
    except Exception as exc:
        _log.warning("track extraction failed: %s", exc)
        return None


def _extract_via(via_wrapper) -> Via | None:
    try:
        p = via_wrapper.proto
        return Via(
            kid=_kid(p.id),
            net=_net_name(p.net),
            position=_nm(p.position),
        )
    except Exception as exc:
        _log.warning("via extraction failed: %s", exc)
        return None


def read_board_snapshot(board: Board) -> BoardSnapshot:
    """Read algorithm inputs and complete group memberships from KiCad."""
    fps = {
        fp.kid: fp
        for fp in (_extract_footprint(f) for f in board.get_items(types=[KiCadObjectType.KOT_PCB_FOOTPRINT]))
        if fp is not None
    }

    tracks = {
        track.kid: track
        for track in (_extract_track(t) for t in board.get_items(types=[KiCadObjectType.KOT_PCB_TRACE]))
        if track is not None
    }
    vias = {
        via.kid: via
        for via in (_extract_via(v) for v in board.get_items(types=[KiCadObjectType.KOT_PCB_VIA]))
        if via is not None
    }

    groups = board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    group_snapshots = tuple(
        GroupSnapshot(
            group_id=_kid(group.proto.id),
            name=str(group.proto.name),
            member_ids=tuple(_kid(item_id) for item_id in group.proto.items),
        )
        for group in groups
    )
    channels: list[Channel] = []
    for g in groups:
        name = str(g.proto.name).strip() or "<unnamed>"
        member_kids = [_kid(kiid) for kiid in g.proto.items]
        group_fps = [fps[k] for k in member_kids if k in fps]
        group_tracks = [tracks[k] for k in member_kids if k in tracks]
        group_vias = [vias[k] for k in member_kids if k in vias]
        if not group_fps:
            continue
        channels.append(
            Channel(
                name=name,
                group_kid=_kid(g.proto.id),
                footprints=group_fps,
                tracks=group_tracks,
                vias=group_vias,
            )
        )

    _log.info(
        "read_channels: %d groups, %d channels, %d footprints, %d tracks, %d vias",
        len(groups),
        len(channels),
        len(fps),
        len(tracks),
        len(vias),
    )
    return BoardSnapshot(channels=tuple(channels), groups=group_snapshots)


def read_channels(board: Board) -> list[Channel]:
    """Return one Channel per board group that contains footprints."""
    return list(read_board_snapshot(board).channels)
