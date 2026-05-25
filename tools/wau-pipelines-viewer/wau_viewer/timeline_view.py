"""Gantt-style schedule timeline with a live playhead."""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from .model import WauModel


ROW_HEIGHT = 24
CYCLE_WIDTH = 28
HEADER_HEIGHT = 22
LEFT_GUTTER = 110


FLOW_COLORS = [
    QColor("#4caf50"),
    QColor("#3f9cff"),
    QColor("#e57373"),
    QColor("#ffb74d"),
    QColor("#ba68c8"),
    QColor("#4dd0e1"),
    QColor("#aed581"),
]


class TimelineScene(QGraphicsScene):
    def __init__(self, model: WauModel, total_cycles: int) -> None:
        super().__init__()
        self.model = model
        self.total_cycles = max(total_cycles, model.makespan_cycles, 32)
        self.setBackgroundBrush(QBrush(QColor("#0f1014")))
        self._playhead: QGraphicsLineItem = QGraphicsLineItem()
        self._build()

    def _build(self) -> None:
        width = LEFT_GUTTER + self.total_cycles * CYCLE_WIDTH
        height = HEADER_HEIGHT + self.model.core_count * ROW_HEIGHT + 12
        self.setSceneRect(0, 0, width, height)

        # row labels
        for i, core in enumerate(self.model.cores):
            y = HEADER_HEIGHT + i * ROW_HEIGHT
            label = QGraphicsSimpleTextItem(f"core[{core.x},{core.y}] #{core.index}")
            label.setFont(QFont("Menlo", 10))
            label.setBrush(QBrush(QColor("#cfd2d6")))
            label.setPos(8, y + 4)
            self.addItem(label)

            sep = QGraphicsLineItem(LEFT_GUTTER, y + ROW_HEIGHT, width, y + ROW_HEIGHT)
            sep.setPen(QPen(QColor("#1f2128"), 1))
            self.addItem(sep)

        # cycle ticks
        for c in range(0, self.total_cycles + 1, 1):
            x = LEFT_GUTTER + c * CYCLE_WIDTH
            major = c % 5 == 0
            tick = QGraphicsLineItem(x, HEADER_HEIGHT - (10 if major else 5), x, height - 12)
            tick.setPen(QPen(QColor("#2a2d35"), 1))
            self.addItem(tick)
            if major:
                lbl = QGraphicsSimpleTextItem(str(c))
                lbl.setFont(QFont("Menlo", 8))
                lbl.setBrush(QBrush(QColor("#888c95")))
                lbl.setPos(x + 2, 2)
                self.addItem(lbl)

        # instructions from the static schedule
        flow_colors: Dict[int, QColor] = {}
        for ins in self.model.schedule:
            color = flow_colors.setdefault(
                ins.flow_id,
                FLOW_COLORS[len(flow_colors) % len(FLOW_COLORS)],
            )
            x0 = LEFT_GUTTER + ins.cycle_start * CYCLE_WIDTH
            w = max(2, (ins.cycle_end - ins.cycle_start) * CYCLE_WIDTH - 2)
            y = HEADER_HEIGHT + ins.core_index * ROW_HEIGHT + 2
            rect = QGraphicsRectItem(x0, y, w, ROW_HEIGHT - 6)
            rect.setBrush(QBrush(color))
            rect.setPen(QPen(QColor("#0b0c10"), 1))
            rect.setToolTip(
                f"flow={ins.flow_id} {ins.op} (stage {ins.node_id})  "
                f"cycles [{ins.cycle_start}..{ins.cycle_end})  "
                f"{'FALLBACK' if ins.used_fallback else 'primary'}"
            )
            self.addItem(rect)
            text = QGraphicsSimpleTextItem(f"{ins.op}")
            text.setFont(QFont("Menlo", 9, QFont.Bold))
            text.setBrush(QBrush(QColor("#0b0c10")))
            text.setPos(x0 + 4, y + 1)
            self.addItem(text)

        # playhead
        self._playhead = QGraphicsLineItem(LEFT_GUTTER, HEADER_HEIGHT - 4, LEFT_GUTTER, height - 12)
        self._playhead.setPen(QPen(QColor("#ffe27a"), 2))
        self._playhead.setZValue(10)
        self.addItem(self._playhead)

    def set_cycle(self, cycle: int) -> None:
        x = LEFT_GUTTER + cycle * CYCLE_WIDTH
        height = self.sceneRect().height()
        self._playhead.setLine(x, HEADER_HEIGHT - 4, x, height - 12)


class TimelineView(QGraphicsView):
    def __init__(self, scene: TimelineScene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFixedHeight(int(scene.sceneRect().height()) + 18)
        self.setBackgroundBrush(QBrush(QColor("#0f1014")))
