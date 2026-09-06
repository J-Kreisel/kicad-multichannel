"""Read KiCad board groups as channels with footprints and simple routing."""

from __future__ import annotations

import logging
import math
import re

from kipy.board import Board
from kipy.proto.board.board_types_pb2 import Pad as ProtoPad
from kipy.proto.common.types import KiCadObjectType

from ..domain.models import BoardSnapshot, Channel, Footprint, GroupSnapshot, Track, Via

_log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class BoardExtractionError(RuntimeError):
    pass


def _kid(obj) -> str:
    s = ""
    if hasattr(obj, "value"):
        s = str(obj.value)
    if not s:
        s = str(obj)
    m = _UUID_RE.search(s)
    return m.group(1) if m else s.strip()


def _nm(point) -> tuple[int, int]:
    return int(point.x_nm), int(point.y_nm)


def _degrees(angle) -> float:
    return float(angle.value_degrees)


def _net_name(net) -> str:
    return str(net.name)


def _field_text(field) -> str:
    return str(field.text.text.text)


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


def _extract_footprint(fp_wrapper) -> Footprint:
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
        if not item.Unpack(proto_pad):
            raise ValueError(f"could not unpack pad in footprint {reference!r}")
        name = str(proto_pad.number)
        pads[name] = int(proto_pad.type)
        if proto_pad.net.name:
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


def _extract_track(track_wrapper) -> Track:
    p = track_wrapper.proto
    return Track(
        kid=_kid(p.id),
        net=_net_name(p.net),
        start=_nm(p.start),
        end=_nm(p.end),
    )


def _extract_via(via_wrapper) -> Via:
    p = via_wrapper.proto
    return Via(
        kid=_kid(p.id),
        net=_net_name(p.net),
        position=_nm(p.position),
    )


def _extract_items(wrappers, extractor, item_type: str):
    extracted = {}
    for wrapper in wrappers:
        wrapper_id = _kid(wrapper.id)
        if not wrapper_id:
            raise BoardExtractionError(f"{item_type} has an empty ID")
        if wrapper_id in extracted:
            raise BoardExtractionError(f"duplicate {item_type} ID: {wrapper_id}")
        try:
            item = extractor(wrapper)
        except Exception as exc:
            raise BoardExtractionError(
                f"failed to extract {item_type} {wrapper_id}: {exc}"
            ) from exc
        if item.kid != wrapper_id:
            raise BoardExtractionError(
                f"{item_type} ID changed during extraction: {wrapper_id} -> {item.kid}"
            )
        extracted[wrapper_id] = item
    return extracted


def read_board_snapshot(board: Board) -> BoardSnapshot:
    """Read algorithm inputs and complete group memberships from KiCad."""
    fps = _extract_items(
        board.get_items(types=[KiCadObjectType.KOT_PCB_FOOTPRINT]),
        _extract_footprint,
        "footprint",
    )
    tracks = _extract_items(
        board.get_items(types=[KiCadObjectType.KOT_PCB_TRACE]),
        _extract_track,
        "track",
    )
    vias = _extract_items(
        board.get_items(types=[KiCadObjectType.KOT_PCB_VIA]),
        _extract_via,
        "via",
    )

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


def read_channel_summaries(board: Board) -> list[tuple[str, int]]:
    footprint_ids = {
        _kid(footprint.id)
        for footprint in board.get_items(types=[KiCadObjectType.KOT_PCB_FOOTPRINT])
    }
    return [
        (
            str(group.proto.name).strip() or "<unnamed>",
            sum(_kid(member_id) in footprint_ids for member_id in group.proto.items),
        )
        for group in board.get_items(types=[KiCadObjectType.KOT_PCB_GROUP])
    ]
