"""Read pads and explicit routed copper for membership analysis."""

from __future__ import annotations

from collections import Counter

from kipy.board_types import Pad, PadType
from kipy.proto.common.types import KiCadObjectType
from kipy.util.board_layer import is_copper_layer

from ..membership.models import (
    CopperItem,
    MembershipBoard,
    MembershipPad,
    PolygonRegion,
)
from .groups import item_id, read_group_topology


def _polyline_points(polyline) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for node in polyline.nodes:
        if node.has_point:
            points.append((node.point.x, node.point.y))
        elif node.has_arc:
            points.extend(
                (
                    (node.arc.start.x, node.arc.start.y),
                    (node.arc.mid.x, node.arc.mid.y),
                    (node.arc.end.x, node.arc.end.y),
                )
            )
    return tuple(points)


def _item_regions(board, item, copper_layers: list[int]) -> tuple[PolygonRegion, ...]:
    regions: list[PolygonRegion] = []
    for layer in copper_layers:
        polygons = board.get_pad_shapes_as_polygons([item], layer)
        if not polygons:
            continue
        polygon = polygons[0]
        outline = _polyline_points(polygon.outline)
        if len(outline) < 3:
            continue
        regions.append(
            PolygonRegion(
                layer=int(layer),
                outline=outline,
                holes=tuple(_polyline_points(hole) for hole in polygon.holes),
            )
        )
    return tuple(regions)


def _require_unique_ids(items, item_type: str) -> None:
    ids = [item.item_id for item in items]
    if any(not item_id for item_id in ids):
        raise RuntimeError(f"{item_type} has an empty ID")
    duplicates = {
        item_id for item_id, count in Counter(ids).items() if count > 1
    }
    if duplicates:
        raise RuntimeError(f"duplicate {item_type} IDs: {sorted(duplicates)}")


def read_membership_board(board) -> MembershipBoard:
    topology, _ = read_group_topology(board)
    pads: list[MembershipPad] = []
    copper_layers = [
        int(layer) for layer in board.get_enabled_layers() if is_copper_layer(layer)
    ]
    footprints = board.get_footprints()
    for footprint in footprints:
        footprint_id = item_id(footprint.id)
        for pad in footprint.definition.items:
            if not isinstance(pad, Pad):
                continue
            pad_id = item_id(pad.id) or f"{footprint_id}:{pad.number}"
            regions = _item_regions(board, pad, copper_layers)
            if pad.pad_type == PadType.PT_NPTH:
                for region in regions:
                    pads.append(
                        MembershipPad(
                            item_id=f"{pad_id}@{region.layer}",
                            footprint_id=footprint_id,
                            net=str(pad.net.name),
                            position=(pad.position.x, pad.position.y),
                            layers=frozenset((region.layer,)),
                            regions=(region,),
                        )
                    )
            elif regions:
                pads.append(
                    MembershipPad(
                        item_id=pad_id,
                        footprint_id=footprint_id,
                        net=str(pad.net.name),
                        position=(pad.position.x, pad.position.y),
                        layers=frozenset(region.layer for region in regions),
                        regions=regions,
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
                width=int(track.width),
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
                width=int(arc.width),
                mid=(arc.mid.x, arc.mid.y),
            )
        )
    for via in board.get_items(types=[KiCadObjectType.KOT_PCB_VIA]):
        regions = _item_regions(board, via, copper_layers)
        copper.append(
            CopperItem(
                item_id=item_id(via.id),
                net=via.net.name,
                kind="via",
                points=((via.position.x, via.position.y),),
                layers=frozenset(region.layer for region in regions),
                locked=bool(via.locked),
                regions=regions,
            )
        )
    _require_unique_ids(pads, "pad node")
    _require_unique_ids(copper, "copper item")
    return MembershipBoard(topology.groups, tuple(pads), tuple(copper))
