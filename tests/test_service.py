from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from multichannel_layout import service
from multichannel_layout.domain.models import BoardSnapshot, Channel, UpdatePlan


def test_fingerprint_is_order_independent_and_includes_board_identity():
    items = [SimpleNamespace(proto=Mock(SerializeToString=Mock(return_value=value)))
             for value in (b"first", b"second")]
    board = SimpleNamespace(document="board-a", get_items=lambda **_: items)
    fingerprint = service.copy_fingerprint(board)
    items.reverse()
    assert service.copy_fingerprint(board) == fingerprint
    board.document = "board-b"
    assert service.copy_fingerprint(board) != fingerprint
    board.document = "board-a"
    items[0].proto.SerializeToString.return_value = b"changed width or lock"
    assert service.copy_fingerprint(board) != fingerprint


def test_duplicate_channel_names_rejected(monkeypatch):
    monkeypatch.setattr(service, "read_channel_summaries", lambda _: [("CH1", 2), ("CH1", 3)])
    with pytest.raises(ValueError, match="unique"):
        service.list_channels(object())


def test_copy_preview_does_not_apply_and_rejects_changes_during_read(monkeypatch):
    monkeypatch.setattr(service, "list_channels", lambda _: [("CH1", 0), ("CH2", 0)])
    monkeypatch.setattr(service, "read_board_snapshot", lambda _: BoardSnapshot(
        (Channel("CH1", "1"), Channel("CH2", "2")), ()))
    executor = Mock()
    monkeypatch.setattr(service, "KiCadBoardExecutor", executor)
    fingerprints = iter(["old", "new"])
    monkeypatch.setattr(service, "copy_fingerprint", lambda _: next(fingerprints))
    with pytest.raises(RuntimeError, match="changed while planning"):
        service.preview_copy(SimpleNamespace(name="PCB"), "CH1", ["CH2"])
    executor.assert_not_called()


def test_stale_preview_never_constructs_executor(monkeypatch):
    monkeypatch.setattr(service, "copy_fingerprint", lambda _: "new")
    executor = Mock()
    monkeypatch.setattr(service, "KiCadBoardExecutor", executor)
    with pytest.raises(ValueError, match="changed since preview"):
        service.apply_preview(SimpleNamespace(name="PCB"), service.Preview("PCB", "old"))
    executor.assert_not_called()


def test_partial_apply_failure_reports_completed_targets(monkeypatch):
    monkeypatch.setattr(service, "copy_fingerprint", lambda _: "same")
    result = SimpleNamespace(target_channel="CH2")
    executor = Mock()
    executor.apply.side_effect = [result, RuntimeError("IPC failure")]
    monkeypatch.setattr(service, "KiCadBoardExecutor", lambda _: executor)
    plans = tuple(UpdatePlan("CH1", target, (0, 0, 0), (1, 1, 0), (), ())
                  for target in ["CH2", "CH3"])
    with pytest.raises(service.ApplyError, match="CH3") as error:
        service.apply_preview(SimpleNamespace(name="PCB"), service.Preview("PCB", "same", plans))
    assert error.value.completed == (result,)
