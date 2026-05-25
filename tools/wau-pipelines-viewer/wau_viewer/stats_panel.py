"""Live performance + bottleneck readout panel."""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .model import WauModel
from .trace_parser import CycleSnapshot, ParsedTrace, derive_bottlenecks


class StatsPanel(QWidget):
    def __init__(self, model: WauModel, trace: ParsedTrace) -> None:
        super().__init__()
        self.model = model
        self.trace = trace
        self.bottlenecks = derive_bottlenecks(trace)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("Performance & Bottlenecks")
        title.setFont(QFont("Menlo", 12, QFont.Bold))
        layout.addWidget(title)

        # Live counters
        self.lbl_cycle = QLabel("cycle: 0")
        self.lbl_hops = QLabel("hops: 0")
        self.lbl_stalls = QLabel("stalls: 0")
        self.lbl_forwards = QLabel("forwards: 0")
        self.lbl_delivered = QLabel("local-delivered: 0")
        self.lbl_cache = QLabel("cache: 0 / 0")
        self.lbl_throughput = QLabel("outputs: 0")
        for lbl in (
            self.lbl_cycle,
            self.lbl_hops,
            self.lbl_stalls,
            self.lbl_forwards,
            self.lbl_delivered,
            self.lbl_cache,
            self.lbl_throughput,
        ):
            lbl.setFont(QFont("Menlo", 10))
            layout.addWidget(lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        layout.addWidget(_section_label("Per-core busy ratio (full run)"))
        ratios = self.bottlenecks.get("core_busy_ratio", [])
        self.bars: List[QProgressBar] = []
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        for i, core in enumerate(self.model.cores):
            r = ratios[i] if i < len(ratios) else 0.0
            tag = QLabel(f"[{core.x},{core.y}] #{core.index}")
            tag.setFont(QFont("Menlo", 9))
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(r * 1000))
            bar.setFormat(f"{r * 100:.1f}%")
            bar.setMaximumHeight(14)
            grid.addWidget(tag, i, 0)
            grid.addWidget(bar, i, 1)
            self.bars.append(bar)
        layout.addLayout(grid)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        layout.addWidget(sep2)

        layout.addWidget(_section_label("Summary"))
        busiest = self.bottlenecks.get("busiest_core")
        if busiest is not None:
            cx, cy = self.model.core_xy(busiest)
            layout.addWidget(QLabel(f"busiest core: [{cx},{cy}] #{busiest}"))
        layout.addWidget(QLabel(f"backpressure cycles: {self.bottlenecks.get('backpressure_cycles', 0)}"))
        layout.addWidget(QLabel(f"stall rate: {self.bottlenecks.get('stall_rate', 0):.4f}"))
        layout.addWidget(QLabel(f"total cycles: {self.bottlenecks.get('total_cycles', 0)}"))
        layout.addWidget(QLabel(f"outputs seen: {self.bottlenecks.get('outputs_seen', 0)}"))
        layout.addStretch(1)

    def apply_cycle(self, snap: CycleSnapshot, output_count: int) -> None:
        self.lbl_cycle.setText(f"cycle: {snap.cycle}")
        self.lbl_hops.setText(f"hops: {snap.obs.hops}")
        self.lbl_stalls.setText(f"stalls: {snap.obs.stalls}")
        self.lbl_forwards.setText(f"forwards: {snap.obs.forwards}")
        self.lbl_delivered.setText(f"local-delivered: {snap.obs.delivered}")
        self.lbl_cache.setText(
            f"cache: {snap.obs.cache_hits} / {snap.obs.cache_lookups}"
            + (
                f"  ({100.0 * snap.obs.cache_hits / snap.obs.cache_lookups:.1f}%)"
                if snap.obs.cache_lookups > 0
                else ""
            )
        )
        self.lbl_throughput.setText(f"outputs: {output_count}")


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(QFont("Menlo", 10, QFont.Bold))
    return lbl
