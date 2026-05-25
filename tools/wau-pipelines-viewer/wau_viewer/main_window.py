"""Main application window: transport controls + dock layout + recording."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .graph_view import WauGraphView, WauScene
from .model import WauModel
from .recorder import FrameRecorder
from .stats_panel import StatsPanel
from .timeline_view import TimelineScene, TimelineView
from .trace_parser import ParsedTrace


class ViewerWindow(QMainWindow):
    def __init__(self, model: WauModel, trace: ParsedTrace) -> None:
        super().__init__()
        self.model = model
        self.trace = trace
        self.setWindowTitle("WAU Pipelines Viewer")
        self.resize(1500, 950)

        self._recorder: Optional[FrameRecorder] = None
        self._cycle_idx = 0
        self._output_count_at = self._precompute_output_counts()

        self.scene = WauScene(model)
        self.graph_view = WauGraphView(self.scene)

        total_cycles = max(trace.meta.total_cycles, model.makespan_cycles, len(trace.cycles))
        self.timeline_scene = TimelineScene(model, total_cycles)
        self.timeline_view = TimelineView(self.timeline_scene)

        self.stats = StatsPanel(model, trace)

        # transport controls
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(4, 4, 4, 4)
        self.btn_play = QPushButton("▶ Play")
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_step_back = QPushButton("⏮ Step")
        self.btn_step_fwd = QPushButton("Step ⏭")
        self.btn_reset = QPushButton("⟲ Reset")
        self.btn_record = QPushButton("● Record")
        self.btn_fit = QPushButton("Fit")

        for b in (
            self.btn_step_back, self.btn_play, self.btn_pause, self.btn_step_fwd,
            self.btn_reset, self.btn_fit, self.btn_record,
        ):
            cl.addWidget(b)

        self.lbl_cycle = QLabel("cycle 0 / 0")
        cl.addSpacing(12)
        cl.addWidget(self.lbl_cycle)
        cl.addSpacing(12)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, len(trace.cycles) - 1))
        cl.addWidget(self.slider, 1)

        cl.addWidget(QLabel("speed"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 60)
        self.speed.setValue(8)
        self.speed.setFixedWidth(160)
        cl.addWidget(self.speed)
        self.lbl_speed = QLabel("8 fps")
        cl.addWidget(self.lbl_speed)

        # layout: center = graph; right dock = stats; bottom = timeline
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.graph_view, 1)
        cv.addWidget(self.timeline_view)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(center)
        splitter.addWidget(self.stats)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(splitter, 1)
        rv.addWidget(controls)
        self.setCentralWidget(root)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            f"grid={model.grid_x}x{model.grid_y}  cores={model.core_count}  "
            f"cycles={len(trace.cycles)}  outputs={trace.meta.outputs_seen}"
        )

        # timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        # signals
        self.btn_play.clicked.connect(self._play)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_step_fwd.clicked.connect(self._step_forward)
        self.btn_step_back.clicked.connect(self._step_back)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_record.clicked.connect(self._toggle_record)
        self.btn_fit.clicked.connect(self.graph_view.fit_to_scene)
        self.slider.valueChanged.connect(self._on_slider)
        self.speed.valueChanged.connect(self._on_speed_changed)

        # shortcuts
        for key, fn in (
            (Qt.Key_Space, self._toggle_play_pause),
            (Qt.Key_Right, self._step_forward),
            (Qt.Key_Left, self._step_back),
            (Qt.Key_Home, self._reset),
            (Qt.Key_F, self.graph_view.fit_to_scene),
        ):
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(fn)
            self.addAction(act)

        self._apply_cycle(0)

    # ----- helpers -----

    def _precompute_output_counts(self):
        """Cumulative number of host outputs delivered up to and including cycle i."""
        counts = []
        running = 0
        for snap in self.trace.cycles:
            if snap.host_out.delivered:
                running += 1
            counts.append(running)
        return counts

    def _apply_cycle(self, idx: int) -> None:
        if not self.trace.cycles:
            return
        idx = max(0, min(idx, len(self.trace.cycles) - 1))
        self._cycle_idx = idx
        snap = self.trace.cycles[idx]
        self.scene.apply_cycle(snap)
        self.timeline_scene.set_cycle(snap.cycle)
        self.stats.apply_cycle(snap, self._output_count_at[idx])
        self.lbl_cycle.setText(f"cycle {snap.cycle} / {self.trace.cycles[-1].cycle}")
        if self.slider.value() != idx:
            blocker = self.slider.blockSignals(True)
            self.slider.setValue(idx)
            self.slider.blockSignals(blocker)
        if self._recorder is not None and self._recorder.active:
            self._recorder.grab(self.centralWidget())

    # ----- transport -----

    def _play(self) -> None:
        interval_ms = max(16, int(1000 / max(1, self.speed.value())))
        self._timer.start(interval_ms)

    def _pause(self) -> None:
        self._timer.stop()

    def _toggle_play_pause(self) -> None:
        if self._timer.isActive():
            self._pause()
        else:
            self._play()

    def _step_forward(self) -> None:
        self._apply_cycle(self._cycle_idx + 1)

    def _step_back(self) -> None:
        self._apply_cycle(self._cycle_idx - 1)

    def _reset(self) -> None:
        self._pause()
        self._apply_cycle(0)

    def _on_tick(self) -> None:
        if self._cycle_idx + 1 >= len(self.trace.cycles):
            self._timer.stop()
            return
        self._apply_cycle(self._cycle_idx + 1)

    def _on_slider(self, v: int) -> None:
        self._apply_cycle(v)

    def _on_speed_changed(self, v: int) -> None:
        self.lbl_speed.setText(f"{v} fps")
        if self._timer.isActive():
            self._play()

    # ----- recording -----

    def _toggle_record(self) -> None:
        if self._recorder is not None and self._recorder.active:
            try:
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save MP4 recording", "wau_demo.mp4", "MP4 (*.mp4)"
                )
                if not path:
                    self._recorder = None
                    self.btn_record.setText("● Record")
                    return
                out = self._recorder.stop_and_encode(Path(path))
                QMessageBox.information(self, "Recording saved", f"Saved {out}")
            except Exception as exc:
                QMessageBox.critical(self, "Recording failed", str(exc))
            self._recorder = None
            self.btn_record.setText("● Record")
        else:
            try:
                self._recorder = FrameRecorder(framerate=self.speed.value())
                self._recorder.start()
                self.btn_record.setText("■ Stop")
                # grab the current frame so the video starts from cycle 0
                self._recorder.grab(self.centralWidget())
            except Exception as exc:
                QMessageBox.critical(self, "Recording failed to start", str(exc))
                self._recorder = None


def run_headless_recording(
    model: WauModel,
    trace: ParsedTrace,
    out_path: Path,
    framerate: int = 8,
) -> Path:
    """Render the full trace off-screen and mux to MP4. No window is shown."""
    app = QApplication.instance() or QApplication([])  # noqa: F841
    win = ViewerWindow(model, trace)
    win.resize(1500, 950)
    # render off-screen by attaching to a hidden window
    win.show()
    win.hide()
    rec = FrameRecorder(framerate=framerate)
    rec.start()
    for i in range(len(trace.cycles)):
        win._apply_cycle(i)
        QApplication.processEvents()
        rec.grab(win.centralWidget())
    return rec.stop_and_encode(out_path)
