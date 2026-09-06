import unittest

from kipy.proto.board.board_types_pb2 import Group
from kipy.proto.common.types import KIID, KiCadObjectType

from multichannel_layout.kicad.reader import (
    BoardExtractionError,
    _extract_items,
    read_channel_summaries,
)
from multichannel_layout.kicad.membership_reader import _item_regions


class Wrapper:
    def __init__(self, item_id):
        self.id = KIID(value=item_id)


class ReaderTests(unittest.TestCase):
    def test_item_extraction_fails_without_returning_partial_data(self):
        wrappers = [Wrapper("valid"), Wrapper("broken")]

        with self.assertRaisesRegex(BoardExtractionError, "broken"):
            _extract_items(
                wrappers,
                lambda wrapper: (_ for _ in ()).throw(ValueError("bad payload"))
                if wrapper.id.value == "broken"
                else type("Item", (), {"kid": "valid"})(),
                "track",
            )

    def test_duplicate_ids_are_rejected(self):
        wrappers = [Wrapper("duplicate"), Wrapper("duplicate")]

        with self.assertRaisesRegex(BoardExtractionError, "duplicate track ID"):
            _extract_items(
                wrappers,
                lambda wrapper: type("Item", (), {"kid": wrapper.id.value})(),
                "track",
            )

    def test_channel_summary_does_not_decode_footprints(self):
        class BrokenFootprint(Wrapper):
            @property
            def proto(self):
                raise AssertionError("deep footprint payload was accessed")

        class GroupWrapper:
            def __init__(self):
                self.proto = Group(
                    id=KIID(value="group"),
                    name="CH1",
                    items=[KIID(value="footprint")],
                )

        class Board:
            def get_items(self, types):
                if types == [KiCadObjectType.KOT_PCB_FOOTPRINT]:
                    return [BrokenFootprint("footprint")]
                if types == [KiCadObjectType.KOT_PCB_GROUP]:
                    return [GroupWrapper()]
                raise AssertionError(types)

        self.assertEqual(read_channel_summaries(Board()), [("CH1", 1)])

    def test_polygon_reader_uses_sequence_overload_for_vias(self):
        class Board:
            def get_pad_shapes_as_polygons(self, items, layer):
                self.items = items
                self.layer = layer
                return []

        board = Board()
        item = object()

        self.assertEqual(_item_regions(board, item, [3]), ())
        self.assertEqual(board.items, [item])
        self.assertEqual(board.layer, 3)


if __name__ == "__main__":
    unittest.main()
