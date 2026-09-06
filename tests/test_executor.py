import unittest

import kipy.geometry as geom
from kipy.board_types import FootprintInstance, Group, Net, Track as BoardTrack
from kipy.common_types import Vector2
from kipy.proto.board.board_types_pb2 import Group as ProtoGroup
from kipy.proto.common.types import KIID, KiCadObjectType

from multichannel_layout.domain.models import UpdatePlan
from multichannel_layout.kicad.copy_executor import KiCadBoardExecutor
from multichannel_layout.membership.executor import MembershipExecutor
from multichannel_layout.kicad.membership_reader import read_membership_board
from multichannel_layout.membership.models import MembershipPlan, board_fingerprint


def group(group_id, name, members):
    return Group(
        ProtoGroup(
            id=KIID(value=group_id),
            name=name,
            items=[KIID(value=member) for member in members],
        )
    )


class FakeItem:
    def __init__(self, item_id):
        self.id = KIID(value=item_id)


def footprint(item_id, reference, position, locked=False):
    item = FootprintInstance()
    item.proto.id.value = item_id
    item.proto.reference_field.text.text.text = reference
    item.position = geom.Vector2.from_xy(*position)
    item.locked = locked
    return item


class FakeBoard:
    def __init__(self, locked=False):
        self.groups = [
            group("source-group", "CH1", ("source-footprint", "source-track")),
            group(
                "target-group",
                "CH2",
                ("target-footprint", "graphic", "old-track"),
            ),
            group("active-group", "editing", ()),
        ]
        self.footprints = [
            footprint("source-footprint", "R1", (0, 0)),
            footprint("target-footprint", "R2", (1_000_000, 2_000_000), locked),
        ]

        source_track = BoardTrack()
        source_track.proto.id.value = "source-track"
        source_track.start = Vector2.from_xy(0, 0)
        source_track.end = Vector2.from_xy(1_000_000, 0)
        source_track.net = Net(name="source-net")
        old_track = BoardTrack()
        old_track.proto.id.value = "old-track"
        old_track.start = Vector2.from_xy(0, 0)
        old_track.end = Vector2.from_xy(1, 1)
        old_track.net = Net(name="target-net")
        old_track.locked = locked
        self.tracks = [source_track, old_track]
        self.nets = [Net(name="source-net"), Net(name="target-net")]
        self.other_items = [FakeItem("graphic")]
        self.committed = False
        self.dropped = False
        self.commit_count = 0
        self.active_group = True

    def get_items(self, types):
        if types == [KiCadObjectType.KOT_PCB_GROUP]:
            return self.groups
        if types == [KiCadObjectType.KOT_PCB_FOOTPRINT]:
            return self.footprints
        if types == [KiCadObjectType.KOT_PCB_TRACE]:
            return self.tracks
        if types == [KiCadObjectType.KOT_PCB_ARC]:
            return []
        if types == [KiCadObjectType.KOT_PCB_VIA]:
            return []
        raise AssertionError(types)

    def get_footprints(self):
        return self.footprints

    def get_groups(self):
        return self.groups

    def get_items_by_id(self, ids):
        items = [*self.groups, *self.footprints, *self.tracks, *self.other_items]
        by_id = {item.id.value: item for item in items}
        return [by_id[item_id.value] for item_id in ids if item_id.value in by_id]

    def get_nets(self):
        return self.nets

    def get_enabled_layers(self):
        return [3, 34]

    def begin_commit(self):
        return object()

    def push_commit(self, commit, message):
        self.committed = True
        self.commit_count += 1

    def drop_commit(self, commit):
        self.dropped = True

    def update_items(self, items):
        if items and not isinstance(items[0], Group) and self.active_group:
            updated_ids = {item.id.value for item in items}
            for board_group in self.groups:
                retained = [
                    item_id
                    for item_id in board_group.proto.items
                    if item_id.value not in updated_ids
                ]
                del board_group.proto.items[:]
                board_group.proto.items.extend(retained)
            self.groups[2].proto.items.extend(item.id for item in items)
        elif items and isinstance(items[0], Group) and self.active_group:
            self.groups[2].proto.items.extend(
                item.id
                for item in items
                if item.id.value != self.groups[2].id.value
            )
        return items

    def leave_active_group(self):
        self.active_group = False

    def remove_items(self, items):
        removed_ids = {item.id.value for item in items}
        self.tracks = [item for item in self.tracks if item.id.value not in removed_ids]
        for board_group in self.groups:
            retained = [
                item_id
                for item_id in board_group.proto.items
                if item_id.value not in removed_ids
            ]
            del board_group.proto.items[:]
            board_group.proto.items.extend(retained)

    def create_items(self, items):
        for index, item in enumerate(items, start=1):
            item.proto.id.value = f"created-{index}"
            self.tracks.append(item)
            if self.active_group:
                self.groups[2].proto.items.append(item.id)
        return items


def update_plan():
    return UpdatePlan(
        source_channel="CH1",
        target_channel="CH2",
        source_origin=(0, 0, 0),
        target_origin=(10_000_000, 20_000_000, 0),
        footprint_matches=(("R1", "R2"),),
        net_matches=(("source-net", "target-net"),),
    )


def membership_plan(board, group_name, **changes):
    snapshot = read_membership_board(board)
    return MembershipPlan(
        group_name,
        board_fingerprint=board_fingerprint(snapshot),
        **changes,
    )


class ExecutorTests(unittest.TestCase):
    def test_apply_resolves_plan_and_reconciles_active_group(self):
        board = FakeBoard()

        result = KiCadBoardExecutor(board).apply(update_plan())

        memberships = {
            board_group.id.value: tuple(member.value for member in board_group.proto.items)
            for board_group in board.groups
        }
        self.assertEqual(
            memberships["target-group"],
            ("target-footprint", "graphic", "created-1"),
        )
        self.assertEqual(memberships["active-group"], ())
        self.assertEqual(result.footprint_count, 1)
        self.assertEqual(result.track_count, 1)
        self.assertTrue(board.committed)
        self.assertEqual(board.commit_count, 2)
        self.assertFalse(board.dropped)
        self.assertFalse(board.active_group)

    def test_locked_footprint_and_routed_net_are_untouched(self):
        board = FakeBoard(locked=True)

        result = KiCadBoardExecutor(board).apply(update_plan())

        self.assertEqual(result.footprint_count, 0)
        self.assertEqual(result.track_count, 0)
        self.assertEqual(result.locked_footprint_count, 1)
        self.assertEqual(result.locked_net_count, 1)
        self.assertFalse(board.committed)
        self.assertEqual({track.id.value for track in board.tracks}, {"source-track", "old-track"})

    def test_membership_executor_skips_items_in_foreign_groups(self):
        board = FakeBoard()
        plan = membership_plan(board, "CH2", add_item_ids=("source-track",))

        with self.assertRaisesRegex(RuntimeError, "another group"):
            MembershipExecutor(board).apply(plan)

        memberships = {
            board_group.id.value: {member.value for member in board_group.proto.items}
            for board_group in board.groups
        }
        self.assertIn("source-track", memberships["source-group"])
        self.assertNotIn("source-track", memberships["target-group"])

    def test_membership_executor_adds_ungrouped_item(self):
        board = FakeBoard()
        loose_track = BoardTrack()
        loose_track.proto.id.value = "loose-track"
        board.tracks.append(loose_track)
        plan = membership_plan(board, "CH2", add_item_ids=("loose-track",))

        result = MembershipExecutor(board).apply(plan)

        memberships = {
            board_group.id.value: {member.value for member in board_group.proto.items}
            for board_group in board.groups
        }
        self.assertIn("loose-track", memberships["target-group"])
        self.assertEqual(result.added_count, 1)

    def test_membership_executor_removes_item_from_target_group(self):
        board = FakeBoard()
        plan = membership_plan(board, "CH2", remove_item_ids=("old-track",))

        result = MembershipExecutor(board).apply(plan)

        memberships = {
            board_group.id.value: {member.value for member in board_group.proto.items}
            for board_group in board.groups
        }
        self.assertNotIn("old-track", memberships["target-group"])
        self.assertEqual(result.removed_count, 1)

    def test_membership_executor_rejects_item_locked_after_planning(self):
        board = FakeBoard()
        loose_track = BoardTrack()
        loose_track.proto.id.value = "loose-track"
        loose_track.locked = True
        board.tracks.append(loose_track)
        plan = membership_plan(board, "CH2", add_item_ids=("loose-track",))

        with self.assertRaisesRegex(RuntimeError, "now locked"):
            MembershipExecutor(board).apply(plan)

        self.assertEqual(board.commit_count, 0)

    def test_membership_executor_rejects_geometry_changed_after_planning(self):
        board = FakeBoard()
        loose_track = BoardTrack()
        loose_track.proto.id.value = "loose-track"
        loose_track.start = Vector2.from_xy(0, 0)
        loose_track.end = Vector2.from_xy(1_000_000, 0)
        board.tracks.append(loose_track)
        plan = membership_plan(board, "CH2", add_item_ids=("loose-track",))
        loose_track.end = Vector2.from_xy(2_000_000, 0)

        with self.assertRaisesRegex(RuntimeError, "board changed"):
            MembershipExecutor(board).apply(plan)

        self.assertEqual(board.commit_count, 0)


if __name__ == "__main__":
    unittest.main()
