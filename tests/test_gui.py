import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from multichannel_layout import gui, service
from multichannel_layout.domain.models import UpdatePlan


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), "GUI worker did not finish"


def test_window_refresh_preview_invalidation_and_apply(monkeypatch):
    app = QApplication.instance() or QApplication([])
    board = SimpleNamespace(name="Example PCB")
    monkeypatch.setattr(gui, "KiCad", lambda **_: SimpleNamespace(get_board=lambda: board))
    monkeypatch.setattr(service, "list_channels", lambda _: [("CH1", 2), ("CH2", 2)])
    preview = service.Preview("Example PCB", "fingerprint", notes=("No matches",))
    monkeypatch.setattr(service, "preview_copy", lambda *args: preview)
    window = gui.Window()
    wait(app, lambda: window.thread is None)
    assert window.source.count() == 2
    assert not window.apply_button.isEnabled()
    window.targets.item(1).setCheckState(gui.Qt.CheckState.Checked)
    window.plan()
    wait(app, lambda: window.thread is None)
    assert "Example PCB" in window.output.toPlainText()
    assert window.preview == preview
    assert not window.apply_button.isEnabled()  # no-op plans cannot be applied
    window.ignored.setText("R1")
    assert window.preview is None
    preview = service.Preview("Example PCB", "fingerprint", (
        UpdatePlan("CH1", "CH2", (0, 0, 0), (10, 10, 0), (("R1", "R2"),), ()),))
    applied = []
    monkeypatch.setattr(service, "apply_preview", lambda board, plan: applied.append(plan) or ())
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *args: gui.QMessageBox.StandardButton.Yes)
    window.plan()
    wait(app, lambda: window.thread is None)
    assert window.apply_button.isEnabled()
    window.apply()
    wait(app, lambda: window.thread is None)
    assert applied == [preview]
    assert window.preview is None
    assert not window.apply_button.isEnabled()
    window.close()
