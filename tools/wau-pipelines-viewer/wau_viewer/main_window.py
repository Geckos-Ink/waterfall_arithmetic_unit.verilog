"""Main application window: transport controls + dock layout + recording.

Playback is paced in *seconds per cycle* rather than frames per second: each
simulated cycle plays as a phased mini-animation (operands travel → operation
flashes → results travel back), so slowing playback stretches the animation
instead of just holding a static frame longer. A ~30 fps animation clock
drives ``WauScene.advance(t)``; single-step plays exactly one cycle animation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
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
    QVBoxLayout,
    QWidget,
)

from .graph_view import WauGraphView, WauScene
from .model import WauModel
from .recorder import FrameRecorder
from .stats_panel import StatsPanel
from .timeline_view import TimelineScene, TimelineView
from .trace_parser import ParsedTrace

ANIM_TICK_MS = 33  # ~30 fps animation clock
DEFAULT_CYCLE_MS = 1400


class ViewerWindow(QMainWindow):
    def __init__(
        self, model: WauModel, trace: ParsedTrace, cycle_ms: int = DEFAULT_CYCLE_MS
    ) -> None:
        super().__init__()
        self.model = model
        self.trace = trace
        self.setWindowTitle("WAU Pipelines Viewer")
        self.resize(1500, 950)

        self._recorder: Optional[FrameRecorder] = None
        self._cycle_idx = 0
        self._progress = 1.0
        self._playing = False
        self._output_count_at = self._precompute_output_counts()

        self.scene = WauScene(model)
        if trace.cycles:
            self.scene.set_total_cycles(trace.cycles[-1].cycle)
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

        # cycle-time slider: how long one simulated cycle plays on screen.
        cl.addWidget(QLabel("cycle time"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(2, 40)  # 0.2 s .. 4.0 s per cycle
        self.speed.setValue(max(2, min(40, round(cycle_ms / 100))))
        self.speed.setFixedWidth(160)
        cl.addWidget(self.speed)
        self.lbl_speed = QLabel("")
        cl.addWidget(self.lbl_speed)
        self._on_speed_changed(self.speed.value())

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

        # animation clock
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._elapsed = QElapsedTimer()

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

        self._apply_cycle(0, animate=False)

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

    def _cycle_period_ms(self) -> int:
        return self.speed.value() * 100

    def _apply_cycle(self, idx: int, animate: bool = True) -> None:
        if not self.trace.cycles:
            return
        idx = max(0, min(idx, len(self.trace.cycles) - 1))
        self._cycle_idx = idx
        snap = self.trace.cycles[idx]
        if idx == 0:
            self.scene.reset_peaks()
        self.scene.apply_cycle(snap)
        self.timeline_scene.set_cycle(snap.cycle)
        self.stats.apply_cycle(snap, self._output_count_at[idx])
        self.lbl_cycle.setText(f"cycle {snap.cycle} / {self.trace.cycles[-1].cycle}")
        if self.slider.value() != idx:
            blocker = self.slider.blockSignals(True)
            self.slider.setValue(idx)
            self.slider.blockSignals(blocker)
        if animate:
            self._progress = 0.0
            self.scene.advance(0.0)
            self._elapsed.restart()
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._progress = 1.0
            self.scene.advance(1.0)
            self._grab_frame()

    def _grab_frame(self) -> None:
        if self._recorder is not None and self._recorder.active:
            self._recorder.grab(self.centralWidget())

    # ----- animation clock -----

    def _on_anim_tick(self) -> None:
        dt = self._elapsed.restart()
        self._progress += dt / max(100.0, float(self._cycle_period_ms()))
        if self._progress < 1.0:
            self.scene.advance(self._progress)
            self._grab_frame()
            return
        self.scene.advance(1.0)
        self._grab_frame()
        if self._playing and self._cycle_idx + 1 < len(self.trace.cycles):
            self._apply_cycle(self._cycle_idx + 1, animate=True)
        else:
            self._playing = False
            self._anim_timer.stop()

    # ----- transport -----

    def _play(self) -> None:
        self._playing = True
        if self._progress >= 1.0:
            nxt = self._cycle_idx + 1
            if nxt >= len(self.trace.cycles):
                nxt = 0
            self._apply_cycle(nxt, animate=True)
        elif not self._anim_timer.isActive():
            self._elapsed.restart()
            self._anim_timer.start()

    def _pause(self) -> None:
        self._playing = False
        self._anim_timer.stop()

    def _toggle_play_pause(self) -> None:
        if self._playing:
            self._pause()
        else:
            self._play()

    def _step_forward(self) -> None:
        self._playing = False
        self._apply_cycle(self._cycle_idx + 1, animate=True)

    def _step_back(self) -> None:
        self._playing = False
        self._apply_cycle(self._cycle_idx - 1, animate=True)

    def _reset(self) -> None:
        self._pause()
        self._apply_cycle(0, animate=False)

    def _on_slider(self, v: int) -> None:
        self._playing = False
        self._apply_cycle(v, animate=False)

    def _on_speed_changed(self, v: int) -> None:
        self.lbl_speed.setText(f"{v / 10:.1f} s/cycle")

    # ----- recording -----

    def _toggle_record(self) -> None:
        if self._recorder is not None and self._recorder.active:
            try:
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save recording", "wau_demo.mp4",
                    "MP4 (*.mp4);;GIF (*.gif)"
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
                # live capture happens at the animation clock rate
                self._recorder = FrameRecorder(framerate=round(1000 / ANIM_TICK_MS))
                self._recorder.start()
                self.btn_record.setText("■ Stop")
                self._grab_frame()
            except Exception as exc:
                QMessageBox.critical(self, "Recording failed to start", str(exc))
                self._recorder = None


def run_headless_recording(
    model: WauModel,
    trace: ParsedTrace,
    out_path: Path,
    framerate: int = 10,
    frames_per_cycle: int = 8,
    cycle_ms: int = DEFAULT_CYCLE_MS,
    max_cycles: Optional[int] = None,
    gif_max_width: Optional[int] = None,
) -> Path:
    """Render the full trace off-screen and encode it. No window is shown.

    Every cycle is rendered as ``frames_per_cycle`` deterministic sub-frames of
    the phased packet animation, so the exported video/GIF shows the same data
    movement the interactive viewer plays. Wall-clock pacing of the output is
    ``frames_per_cycle / framerate`` seconds per simulated cycle.
    """
    app = QApplication.instance() or QApplication([])  # noqa: F841
    win = ViewerWindow(model, trace, cycle_ms=cycle_ms)
    win.resize(1500, 950)
    # render off-screen by attaching to a hidden window
    win.show()
    win.hide()
    # the constructor fitted the view before the final widget geometry existed
    QApplication.processEvents()
    win.graph_view.fit_to_scene()
    rec_kwargs = {} if gif_max_width is None else {"gif_max_width": gif_max_width}
    rec = FrameRecorder(framerate=framerate, **rec_kwargs)
    rec.start()
    cycle_count = len(trace.cycles)
    if max_cycles is not None:
        cycle_count = min(cycle_count, max_cycles)
    for i in range(cycle_count):
        win._apply_cycle(i, animate=False)
        for f in range(frames_per_cycle):
            win.scene.advance((f + 1) / frames_per_cycle)
            QApplication.processEvents()
            rec.grab(win.centralWidget())
    return rec.stop_and_encode(out_path)
