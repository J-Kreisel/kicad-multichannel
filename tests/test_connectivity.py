import unittest

from multichannel_layout.membership.connectivity import build_adjacency
from multichannel_layout.membership.models import (
    CopperItem,
    MembershipPad,
    PolygonRegion,
)


LAYER = frozenset((3,))


class ConnectivityTests(unittest.TestCase):
    def test_track_connects_inside_pad_polygon_away_from_center(self):
        region = PolygonRegion(3, ((-10, -10), (10, -10), (10, 10), (-10, 10)))
        pad = MembershipPad("pad", "fp", "N", (0, 0), LAYER, (region,))
        track = CopperItem("track", "N", "track", ((9, 0), (20, 0)), LAYER, False, 2)

        adjacency = build_adjacency([pad], [track])

        self.assertIn("track", adjacency["pad"])

    def test_track_width_can_create_physical_contact(self):
        tracks = [
            CopperItem("a", "N", "track", ((0, 0), (20, 0)), LAYER, False, 10),
            CopperItem("b", "N", "track", ((0, 10), (20, 10)), LAYER, False, 10),
        ]

        adjacency = build_adjacency([], tracks)

        self.assertIn("b", adjacency["a"])

    def test_arc_connects_at_midpoint(self):
        arc = CopperItem(
            "arc",
            "N",
            "arc",
            ((-10, 0), (10, 0)),
            LAYER,
            False,
            2,
            (0, 10),
        )
        track = CopperItem("track", "N", "track", ((0, 10), (0, 20)), LAYER, False, 2)

        adjacency = build_adjacency([], [arc, track])

        self.assertIn("track", adjacency["arc"])

    def test_empty_layers_are_not_conductive_wildcards(self):
        pad = MembershipPad("pad", "fp", "N", (0, 0), frozenset())
        track = CopperItem("track", "N", "track", ((0, 0), (10, 0)), LAYER, False, 2)

        adjacency = build_adjacency([pad], [track])

        self.assertNotIn("track", adjacency["pad"])

    def test_npth_layer_ports_do_not_bridge_front_and_back(self):
        front = MembershipPad("pad@3", "fp", "", (0, 0), frozenset((3,)))
        back = MembershipPad("pad@34", "fp", "", (0, 0), frozenset((34,)))
        front_track = CopperItem(
            "front", "", "track", ((0, 0), (10, 0)), frozenset((3,)), False, 2
        )
        back_track = CopperItem(
            "back", "", "track", ((0, 0), (-10, 0)), frozenset((34,)), False, 2
        )

        adjacency = build_adjacency([front, back], [front_track, back_track])

        self.assertNotIn("pad@34", adjacency["pad@3"])
        self.assertNotIn("back", adjacency["front"])


if __name__ == "__main__":
    unittest.main()
