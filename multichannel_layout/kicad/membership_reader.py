"""Read pads and explicit routed copper for membership analysis."""

from __future__ import annotations

from kipy.proto.board.board_types_pb2 import Pad as ProtoPad
from kipy.proto.common.types import KiCadObjectType

from ..membership.models import CopperItem, MembershipBoard, MembershipPad
from .groups import item_id, read_group_topology


def _point(proto_point) -> tuple[int, int]:
    return int(proto_point.x_nm), int(proto_point.y_nm)


def _copper_layers(pad_stack) -> frozenset[int]:
    layers = {int(layer.layer) for layer in pad_stack.copper_layers}
    if not layers:
        layers.update(int(layer) for layer in pad_stack.layers)
    start_layer = int(pad_stack.drill.start_layer)
    end_layer = int(pad_stack.drill.end_layer)
    if 3 <= start_layer <= 34 and 3 <= end_layer <= 34:
        lower, upper = sorted((start_layer, end_layer))
        layers.update(range(lower, upper + 1))
    return frozenset(layers)


def read_membership_board(board) -> MembershipBoard:
    topology, _ = read_group_topology(board)
    pads: list[MembershipPad] = []
    footprints = board.get_items(types=[KiCadObjectType.KOT_PCB_FOOTPRINT])
    for footprint in footprints:
        footprint_id = item_id(footprint.id)
        for packed_item in footprint.proto.definition.items:
            if not packed_item.type_url.endswith("kiapi.board.types.Pad"):
                continue
            pad = ProtoPad()
            packed_item.Unpack(pad)
            if not pad.net.name:
                continue
            pad_id = item_id(pad.id) or f"{footprint_id}:{pad.number}"
            pads.append(
                MembershipPad(
                    item_id=pad_id,
                    footprint_id=footprint_id,
                    net=str(pad.net.name),
                    position=_point(pad.position),
                    layers=_copper_layers(pad.pad_stack),
                )
            )

    copper: list[CopperItem] = []
    for track in board.get_items(types=[KiCadObjectType.KOT_PCB_TRACE]):
        copper.append(
            CopperItem(
                item_id=item_id(track.id),
                net=track.net.name,
                kind="track",
                points=((track.start.x, track.start.y), (track.end.x, track.end.y)),
                layers=frozenset((int(track.layer),)),
                locked=bool(track.locked),
            )
        )
    for arc in board.get_items(types=[KiCadObjectType.KOT_PCB_ARC]):
        copper.append(
            CopperItem(
                item_id=item_id(arc.id),
                net=arc.net.name,
                kind="arc",
                points=((arc.start.x, arc.start.y), (arc.end.x, arc.end.y)),
                layers=frozenset((int(arc.layer),)),
                locked=bool(arc.locked),
            )
        )
    for via in board.get_items(types=[KiCadObjectType.KOT_PCB_VIA]):
        copper.append(
            CopperItem(
                item_id=item_id(via.id),
                net=via.net.name,
                kind="via",
                points=((via.position.x, via.position.y),),
                layers=_copper_layers(via.proto.pad_stack),
                locked=bool(via.locked),
            )
        )
    return MembershipBoard(topology.groups, tuple(pads), tuple(copper))
