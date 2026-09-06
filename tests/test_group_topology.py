import unittest

from kipy.board_types import Group
from kipy.proto.board.board_types_pb2 import Group as ProtoGroup
from kipy.proto.common.types import KIID, KiCadObjectType

from multichannel_layout.kicad.groups import (
    GroupTopology,
    GroupTopologyManager,
    expected_topology,
    topology_errors,
)
from multichannel_layout.domain.models import GroupSnapshot


class GroupTopologyTests(unittest.TestCase):
    def setUp(self):
        self.before = GroupTopology(
            (
                GroupSnapshot(
                    "channel",
                    "CH2",
                    ("footprint", "graphic", "old-track"),
                ),
                GroupSnapshot("parent", "top", ("channel", "label")),
                GroupSnapshot("active", "editing", ("other-item",)),
            )
        )

    def test_expected_topology_preserves_opaque_and_nested_members(self):
        expected = expected_topology(
            self.before,
            removed_item_ids={"old-track"},
            additions_by_group={"channel": ("new-track",)},
        )

        self.assertEqual(
            expected.by_id["channel"].member_ids,
            ("footprint", "graphic", "new-track"),
        )
        self.assertEqual(expected.by_id["parent"].member_ids, ("channel", "label"))
        self.assertEqual(expected.by_id["active"].member_ids, ("other-item",))

    def test_validation_detects_implicit_active_group_membership(self):
        actual = GroupTopology(
            (
                self.before.groups[0],
                self.before.groups[1],
                GroupSnapshot("active", "editing", ("other-item", "new-track")),
            )
        )

        errors = topology_errors(self.before, actual)

        self.assertTrue(any("new-track" in error for error in errors))

    def test_validation_detects_group_created_by_api(self):
        actual = GroupTopology(
            (*self.before.groups, GroupSnapshot("new-group", "", ("graphic",)))
        )

        errors = topology_errors(self.before, actual)

        self.assertTrue(any("unexpected groups created" in error for error in errors))

    def test_executor_detaches_implicit_membership_before_target_membership(self):
        class FakeItem:
            def __init__(self, item_id):
                self.id = KIID(value=item_id)

        class FakeBoard:
            def __init__(self):
                self.groups = [
                    Group(
                        ProtoGroup(
                            id=KIID(value="target"),
                            name="CH2",
                            items=[KIID(value="footprint")],
                        )
                    ),
                    Group(
                        ProtoGroup(
                            id=KIID(value="active"),
                            name="editing",
                            items=[KIID(value="new-track")],
                        )
                    ),
                ]
                self.items = [
                    FakeItem("footprint"),
                    FakeItem("new-track"),
                ]

            def get_items(self, types):
                self.assert_group_query(types)
                return self.groups

            def update_items(self, groups):
                return groups

            def begin_commit(self):
                return object()

            def push_commit(self, commit, message):
                pass

            def drop_commit(self, commit):
                raise AssertionError("group repair should not fail")

            def get_groups(self):
                return self.groups

            def get_items_by_id(self, ids):
                items = [*self.groups, *self.items]
                by_id = {item.id.value: item for item in items}
                return [by_id[item_id.value] for item_id in ids if item_id.value in by_id]

            @staticmethod
            def assert_group_query(types):
                if types != [KiCadObjectType.KOT_PCB_GROUP]:
                    raise AssertionError(types)

        board = FakeBoard()
        wanted = GroupTopology(
            (
                GroupSnapshot("target", "CH2", ("footprint", "new-track")),
                GroupSnapshot("active", "editing", ()),
            )
        )

        GroupTopologyManager(board).repair(wanted)

        target_members = tuple(item.value for item in board.groups[0].proto.items)
        active_members = tuple(item.value for item in board.groups[1].proto.items)
        self.assertEqual(target_members, ("footprint", "new-track"))
        self.assertEqual(active_members, ())


if __name__ == "__main__":
    unittest.main()
