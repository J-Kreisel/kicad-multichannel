"""Small preview-first PySide6 frontend for the native KiCad IPC plugin."""

from __future__ import annotations

import sys

from kipy import KiCad
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from . import service


class Worker(QObject):
    done = Signal(object, object)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            board = KiCad(client_name="com.github.j-kreisel.kicad-multichannel").get_board()
            self.done.emit(self.operation(board), None)
        except Exception as error:
            self.done.emit(None, error)


def describe(preview):
    lines = [f"Board: {preview.board_name}"]
    for plan in preview.copy_plans:
        lines += [
            f"\n{plan.source_channel} → {plan.target_channel}",
            f"{len(plan.footprint_matches)} footprint matches; {len(plan.net_matches)} verified nets",
            f"Origins (nm, degrees): {plan.source_origin} → {plan.target_origin}",
            "Footprints:",
            *[f"  {source} → {target}" for source, target in plan.footprint_matches],
            "Nets:",
            *[f"  {source} → {target}" for source, target in plan.net_matches],
        ]
    if preview.membership_plan:
        plan = preview.membership_plan
        lines += [f"\nGroup: {plan.group_name}",
                  f"Add {len(plan.add_item_ids)} / remove {len(plan.remove_item_ids)} memberships",
                  f"Locked copper observed: {plan.locked_item_count}",
                  *[f"  Add {item}" for item in plan.add_item_ids],
                  *[f"  Remove {item}" for item in plan.remove_item_ids]]
    lines += list(preview.notes)
    lines += ["\nCopy replaces matched target routing. Locked items are preserved.",
              "Copy ignores zones and routed arcs. Inspect results and run DRC.",
              "Each copied target uses two or three undo steps; undo through",
              "'Update multichannel objects' to revert it completely."]
    return "\n".join(lines)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.preview = None
        self.thread = None
        self.setWindowTitle("KiCad Multichannel Layout")
        self.resize(850, 750)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.status = QLabel("Connect to PCB Editor to load channel groups.")
        self.refresh = QPushButton("Refresh channels")
        layout.addWidget(self.status)
        layout.addWidget(self.refresh)
        self.tabs = QTabWidget()
        copy = QWidget()
        form = QFormLayout(copy)
        self.source = QComboBox()
        self.targets = QListWidget()
        self.targets.setMaximumHeight(160)
        self.bucket = QDoubleSpinBox()
        self.bucket.setRange(0.001, 100)
        self.bucket.setDecimals(3)
        self.bucket.setValue(0.5)
        self.bucket.setSuffix(" mm")
        self.ignored = QLineEdit()
        self.ignored.setPlaceholderText("R1, C2 (optional)")
        form.addRow("Source channel", self.source)
        form.addRow("Target channels", self.targets)
        form.addRow("Offset bucket size", self.bucket)
        form.addRow("Ignore references", self.ignored)
        membership = QWidget()
        form = QFormLayout(membership)
        self.group = QComboBox()
        self.mode = QComboBox()
        self.mode.addItem("Add missing routing membership", "add")
        self.mode.addItem("Remove unnecessary routing membership", "remove")
        form.addRow("Channel", self.group)
        form.addRow("Operation", self.mode)
        form.addRow(QLabel("Repairs group membership only; never moves or deletes copper."))
        self.tabs.addTab(copy, "Copy layout")
        self.tabs.addTab(membership, "Repair membership")
        layout.addWidget(self.tabs)
        buttons = QHBoxLayout()
        self.plan_button = QPushButton("Preview")
        self.apply_button = QPushButton("Apply to KiCad")
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.plan_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.setCentralWidget(body)
        self.refresh.clicked.connect(self.load_channels)
        self.plan_button.clicked.connect(self.plan)
        self.apply_button.clicked.connect(self.apply)
        for signal in [self.source.currentIndexChanged, self.targets.itemChanged,
                       self.bucket.valueChanged, self.ignored.textChanged,
                       self.group.currentIndexChanged, self.mode.currentIndexChanged,
                       self.tabs.currentChanged]:
            signal.connect(self.invalidate)
        self.load_channels()

    def invalidate(self, *_):
        self.preview = None
        self.apply_button.setEnabled(False)

    def start(self, operation, callback):
        if self.thread is not None:
            return
        self.invalidate()
        self.tabs.setEnabled(False)
        self.refresh.setEnabled(False)
        self.plan_button.setEnabled(False)
        self.status.setText("Working… Do not edit the board until this operation finishes.")
        self.thread = QThread(self)
        self.callback = callback
        self.worker = Worker(operation)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self.thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self.worker.done.connect(self.finished)
        self.thread.finished.connect(self.cleanup)
        self.thread.start()

    def cleanup(self):
        self.thread.deleteLater()
        self.thread = None

    def finished(self, result, error):
        self.tabs.setEnabled(True)
        self.refresh.setEnabled(True)
        self.plan_button.setEnabled(True)
        if error:
            self.status.setText("Operation failed. See details below.")
            completed = getattr(error, "completed", ())
            self.output.append(f"\nERROR: {error}\nCompleted targets: {completed}")
            QMessageBox.warning(self, "Multichannel Layout", str(error))
        else:
            self.status.setText("Connected to KiCad — operation complete.")
            self.callback(result)

    def load_channels(self):
        def loaded(result):
            name, channels = result
            self.status.setText(f"PCB: {name} — {len(channels)} channels")
            self.source.clear()
            self.group.clear()
            self.targets.clear()
            for name, count in channels:
                self.source.addItem(name)
                self.group.addItem(name)
                item = QListWidgetItem(f"{name} ({count} footprints)")
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.targets.addItem(item)
            self.output.setPlainText("Select channels and preview before applying.")
        self.start(lambda board: (str(board.name), service.list_channels(board)), loaded)

    def plan(self):
        if self.tabs.currentIndex() == 0:
            source = self.source.currentText()
            targets = [self.targets.item(i).data(Qt.ItemDataRole.UserRole)
                       for i in range(self.targets.count())
                       if self.targets.item(i).checkState() == Qt.CheckState.Checked]
            bucket = round(self.bucket.value() * 1_000_000)
            ignored = {ref.strip() for ref in self.ignored.text().split(",") if ref.strip()}
            def operation(board):
                return service.preview_copy(board, source, targets, bucket, ignored)
        else:
            group, mode = self.group.currentText(), self.mode.currentData()
            def operation(board):
                return service.preview_membership(board, group, mode)
        def planned(preview):
            self.preview = preview
            self.output.setPlainText(describe(preview))
            plan = preview.membership_plan
            self.apply_button.setEnabled(bool(preview.copy_plans or
                                              (plan and (plan.add_item_ids or plan.remove_item_ids))))
        self.start(operation, planned)

    def apply(self):
        preview = self.preview
        if preview is None:
            return
        answer = QMessageBox.question(
            self, "Apply preview?",
            "Modify the open PCB? Matched target routing will be replaced when copying.\n"
            "Save a backup first. Multiple undo steps may be needed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.start(lambda board: service.apply_preview(board, preview),
                   lambda results: self.output.append("\nApplied:\n" + "\n".join(map(str, results))))

    def closeEvent(self, event):
        if self.thread is not None:
            QMessageBox.information(self, "Operation running", "Wait for the operation to finish.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
