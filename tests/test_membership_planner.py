import unittest

from multichannel_layout.domain.models import GroupSnapshot
from multichannel_layout.membership.models import (
    CopperItem,
    MembershipBoard,
    MembershipPad,
)
from multichannel_layout.membership.planner import build_membership_plan


LAYER = frozenset((0,))


def pad(item_id, footprint_id, net, position):
    return MembershipPad(item_id, footprint_id, net, position, LAYER)


def track(item_id, net, start, end, locked=False):
    return CopperItem(item_id, net, "track", (start, end), LAYER, locked)


def board(group_members, pads, copper):
    return MembershipBoard(
        groups=(GroupSnapshot("group-id", "CH1", tuple(group_members)),),
        pads=tuple(pads),
        copper=tuple(copper),
    )


class MembershipPlannerTests(unittest.TestCase):
    def setUp(self):
        self.pads = [
            pad("pad-a", "fp-in-a", "N", (0, 0)),
            pad("pad-b", "fp-in-b", "N", (20, 0)),
            pad("pad-out", "fp-out", "N", (30, 0)),
        ]
        self.copper = [
            track("core-a", "N", (0, 0), (10, 0)),
            track("core-b", "N", (10, 0), (20, 0)),
            track("outside-branch", "N", (20, 0), (30, 0)),
        ]

    def test_add_keeps_existing_members_and_adds_only_required_copper(self):
        snapshot = board(
            ("fp-in-a", "fp-in-b", "core-a"),
            self.pads,
            self.copper,
        )

        plan = build_membership_plan(snapshot, "CH1", "add")

        self.assertEqual(plan.add_item_ids, ("core-b",))
        self.assertEqual(plan.remove_item_ids, ())
        self.assertEqual(plan.island_count, 1)

    def test_remove_drops_external_branch_but_keeps_internal_path(self):
        snapshot = board(
            ("fp-in-a", "fp-in-b", "core-a", "core-b", "outside-branch"),
            self.pads,
            self.copper,
        )

        plan = build_membership_plan(snapshot, "CH1", "remove")

        self.assertEqual(plan.remove_item_ids, ("outside-branch",))

    def test_single_internal_pad_requires_no_copper(self):
        snapshot = board(
            ("fp-in-a", "core-a", "core-b", "outside-branch"),
            self.pads,
            self.copper,
        )

        plan = build_membership_plan(snapshot, "CH1", "remove")

        self.assertEqual(
            set(plan.remove_item_ids),
            {"core-a", "core-b", "outside-branch"},
        )

    def test_remove_ignores_internal_island_disconnected_from_external_pad(self):
        disconnected_pads = [
            pad("pad-a", "fp-in-a", "N", (0, 0)),
            pad("pad-b", "fp-in-b", "N", (10, 0)),
            pad("pad-out", "fp-out", "N", (100, 0)),
        ]
        disconnected_copper = [track("internal", "N", (0, 0), (10, 0))]
        snapshot = board(
            ("fp-in-a", "fp-in-b", "internal"),
            disconnected_pads,
            disconnected_copper,
        )

        plan = build_membership_plan(snapshot, "CH1", "remove")

        self.assertEqual(plan.island_count, 0)
        self.assertEqual(plan.add_item_ids, ())

    def test_add_includes_internal_only_net(self):
        snapshot = board(
            ("fp-in-a", "fp-in-b"),
            self.pads[:2],
            self.copper[:2],
        )

        plan = build_membership_plan(snapshot, "CH1", "add")

        self.assertEqual(plan.mixed_net_count, 0)
        self.assertEqual(set(plan.add_item_ids), {"core-a", "core-b"})

    def test_remove_ignores_internal_only_net(self):
        snapshot = board(
            ("fp-in-a", "fp-in-b", "core-a", "core-b"),
            self.pads[:2],
            self.copper[:2],
        )

        plan = build_membership_plan(snapshot, "CH1", "remove")

        self.assertEqual(plan.remove_item_ids, ())

    def test_add_supports_no_net_pad_connection(self):
        pads = [
            pad("pad-a", "fp-in-a", "", (0, 0)),
            pad("pad-b", "fp-in-b", "", (10, 0)),
        ]
        copper = [track("no-net-track", "", (0, 0), (10, 0))]
        snapshot = board(("fp-in-a", "fp-in-b"), pads, copper)

        plan = build_membership_plan(snapshot, "CH1", "add")

        self.assertEqual(plan.add_item_ids, ("no-net-track",))

    def test_track_endpoint_on_another_track_forms_a_junction(self):
        junction_copper = [
            track("main", "N", (0, 0), (20, 0)),
            track("branch", "N", (10, 0), (10, 10)),
        ]
        junction_pads = [
            pad("pad-a", "fp-in-a", "N", (0, 0)),
            pad("pad-b", "fp-in-b", "N", (20, 0)),
            pad("pad-out", "fp-out", "N", (10, 10)),
        ]
        snapshot = board(
            ("fp-in-a", "fp-in-b"),
            junction_pads,
            junction_copper,
        )

        plan = build_membership_plan(snapshot, "CH1", "add")

        self.assertEqual(plan.add_item_ids, ("main",))

    def test_via_connects_tracks_on_different_layers(self):
        bottom = frozenset((31,))
        via_layers = frozenset((0, 31))
        pads = [
            MembershipPad("pad-a", "fp-in-a", "N", (0, 0), LAYER),
            MembershipPad("pad-b", "fp-in-b", "N", (20, 0), bottom),
            MembershipPad("pad-out", "fp-out", "N", (30, 0), bottom),
        ]
        copper = [
            track("top", "N", (0, 0), (10, 0)),
            CopperItem("via", "N", "via", ((10, 0),), via_layers, False),
            CopperItem("bottom", "N", "track", ((10, 0), (20, 0)), bottom, False),
            CopperItem("branch", "N", "track", ((20, 0), (30, 0)), bottom, False),
        ]
        snapshot = board(("fp-in-a", "fp-in-b"), pads, copper)

        plan = build_membership_plan(snapshot, "CH1", "add")

        self.assertEqual(set(plan.add_item_ids), {"top", "via", "bottom"})

    def test_locked_outside_copper_cannot_be_added_as_required_path(self):
        copper = [
            track("core-a", "N", (0, 0), (10, 0)),
            track("core-b", "N", (10, 0), (20, 0), locked=True),
            track("outside-branch", "N", (20, 0), (30, 0)),
        ]
        snapshot = board(("fp-in-a", "fp-in-b"), self.pads, copper)

        with self.assertRaisesRegex(RuntimeError, "locked copper"):
            build_membership_plan(snapshot, "CH1", "add")


if __name__ == "__main__":
    unittest.main()
