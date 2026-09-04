import json
import unittest
from dataclasses import asdict

from multichannel_layout.models import Channel, Footprint, Track, Via
from multichannel_layout.planner import UpdatePlanner


def footprint(reference, kid, position, orientation, net, *, locked=False, pad_count=1):
    pads = {str(index): 1 for index in range(1, pad_count + 1)}
    return Footprint(
        reference=reference,
        footprint_id="Test:R",
        position=position,
        orientation=orientation,
        layer=0,
        pads=pads,
        pad_nets={pad: net for pad in pads},
        pad_positions={pad: position for pad in pads},
        pad_offsets={pad: (0, 0) for pad in pads},
        kid=kid,
        locked=locked,
    )


class PlannerTests(unittest.TestCase):
    def test_plan_contains_only_origins_and_match_pairs(self):
        source = Channel(
            name="CH1",
            group_kid="source-group",
            footprints=[footprint("R1", "source-fp", (0, 0), 0, "/CH1/SIG")],
            tracks=[Track("source-track", "/CH1/SIG", (0, 0), (1_000_000, 0))],
            vias=[Via("source-via", "/CH1/SIG", (1_000_000, 0))],
        )
        target = Channel(
            name="CH2",
            group_kid="target-group",
            footprints=[
                footprint("R2", "target-fp", (10_000_000, 20_000_000), 90, "/CH2/SIG")
            ],
        )

        planner = UpdatePlanner([source, target], 500_000)
        origins = planner.find_channel_origins("CH1", "CH2")
        plan = planner.build_update_plan("CH1", "CH2")

        self.assertIsNotNone(plan)
        self.assertEqual(
            origins,
            ((0, 0, 0.0), (10_000_000, 20_000_000, 90.0)),
        )
        self.assertEqual(
            set(asdict(plan)),
            {
                "source_channel",
                "target_channel",
                "source_origin",
                "target_origin",
                "footprint_matches",
                "net_matches",
            },
        )
        self.assertEqual(plan.source_origin, (0, 0, 0.0))
        self.assertEqual(plan.target_origin, (10_000_000, 20_000_000, 90.0))
        self.assertEqual(plan.footprint_matches, (("R1", "R2"),))
        self.assertEqual(plan.net_matches, (("/CH1/SIG", "/CH2/SIG"),))
        json.dumps(asdict(plan))

    def test_locked_target_footprint_is_the_origin_anchor(self):
        source = Channel(
            "CH1",
            "source-group",
            footprints=[
                footprint("R1", "s1", (1_000_000, 2_000_000), 90, "s1"),
                footprint(
                    "U1",
                    "s2",
                    (10_000_000, 20_000_000),
                    0,
                    "s2",
                    pad_count=2,
                ),
            ],
        )
        target = Channel(
            "CH2",
            "target-group",
            footprints=[
                footprint(
                    "R2",
                    "t1",
                    (101_000_000, 202_000_000),
                    180,
                    "t1",
                    locked=True,
                ),
                footprint(
                    "U2",
                    "t2",
                    (900_000_000, 900_000_000),
                    0,
                    "t2",
                    pad_count=2,
                ),
            ],
        )

        origins = UpdatePlanner([source, target], 500_000).find_channel_origins(
            "CH1", "CH2"
        )

        self.assertEqual(
            origins,
            (
                (1_000_000, 2_000_000, 90),
                (101_000_000, 202_000_000, 180),
            ),
        )

    def test_conflicting_locked_target_footprints_are_rejected(self):
        source = Channel(
            "CH1",
            "source-group",
            footprints=[
                footprint("R1", "s1", (0, 0), 0, "s1"),
                footprint("U1", "s2", (10_000_000, 0), 0, "s2", pad_count=2),
            ],
        )
        target = Channel(
            "CH2",
            "target-group",
            footprints=[
                footprint("R2", "t1", (100_000_000, 0), 0, "t1", locked=True),
                footprint(
                    "U2",
                    "t2",
                    (111_000_000, 0),
                    0,
                    "t2",
                    locked=True,
                    pad_count=2,
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "disagree"):
            UpdatePlanner([source, target], 500_000).find_channel_origins(
                "CH1", "CH2"
            )

    def test_agreeing_locked_target_footprints_use_first_anchor(self):
        source = Channel(
            "CH1",
            "source-group",
            footprints=[
                footprint("R1", "s1", (0, 0), 0, "s1"),
                footprint("U1", "s2", (10_000_000, 0), 0, "s2", pad_count=2),
            ],
        )
        target = Channel(
            "CH2",
            "target-group",
            footprints=[
                footprint("R2", "t1", (100_000_000, 0), 0, "t1", locked=True),
                footprint(
                    "U2",
                    "t2",
                    (110_000_000, 0),
                    0,
                    "t2",
                    locked=True,
                    pad_count=2,
                ),
            ],
        )

        origins = UpdatePlanner([source, target], 500_000).find_channel_origins(
            "CH1", "CH2"
        )

        self.assertEqual(origins, ((0, 0, 0), (100_000_000, 0, 0)))

    def test_unmatched_locked_target_footprint_is_rejected(self):
        source = Channel(
            "CH1",
            "source-group",
            footprints=[footprint("R1", "s1", (0, 0), 0, "s1")],
        )
        target = Channel(
            "CH2",
            "target-group",
            footprints=[
                footprint("R2", "t1", (100_000_000, 0), 0, "t1"),
                footprint(
                    "U2",
                    "t2",
                    (110_000_000, 0),
                    0,
                    "t2",
                    locked=True,
                    pad_count=2,
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "no source match"):
            UpdatePlanner([source, target], 500_000).find_channel_origins(
                "CH1", "CH2"
            )


if __name__ == "__main__":
    unittest.main()
