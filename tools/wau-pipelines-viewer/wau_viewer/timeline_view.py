"""Gantt-style operation timeline with a live playhead.

The strip is built from the *actual* RTL trace (each core's dispatch/result
events), not the offline `wau_schedule.json` estimate: stalls, backpressure,
retries and fallback-core reassignment all make the real per-cycle timing
diverge from the static plan, and a run's real makespan can run far longer
than the plan's. Drawing the plan produced blocks that drifted out of sync
with the playhead and simply stopped appearing once the plan's makespan was
exceeded, even though cores kept executing for the rest of the trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

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
from .trace_parser import ParsedTrace


ROW_HEIGHT = 24
CYCLE_WIDTH = 28
HEADER_HEIGHT = 22
LEFT_GUTTER = 110


# Mirrors graph_view.COLOR_PACKET_FASTPATH so a fast-path-fed op's bar reads
# as the same "amber = fast-path" cue as the fabric diagram above it.
COLOR_FASTPATH_BORDER = QColor("#ff8f3f")

FLOW_COLORS = [
    QColor("#4caf50"),
    QColor("#3f9cff"),
    QColor("#e57373"),
    QColor("#ffb74d"),
    QColor("#ba68c8"),
    QColor("#4dd0e1"),
    QColor("#aed581"),
]


@dataclass
class RealInstruction:
    """One core's dispatch-to-result span, as actually observed in the trace."""

    core_index: int
    cycle_start: int
    cycle_end: int  # exclusive; end == start+1 until a matching result arrives
    flow_id: int
    stage_id: int
    op: str
    used_fallback: bool
    used_fastpath: bool
    complete: bool  # False if the trace ended before a matching result showed up


def _build_real_instructions(model: WauModel, trace: ParsedTrace) -> List[RealInstruction]:
    """Reconstruct per-core operation spans from dispatch/result trace events.

    Each core dispatches at most one op at a time (`disp` -- which covers both
    an ordinary coordinator dispatch and a fast-path self-dispatch; see
    `trace_parser.CoreState.disp_fastpath`), and later reports its result
    (`res`) tagged with the same flow/stage id; a per-core FIFO pairs them up
    so the drawn span reflects the real dispatch and completion cycles rather
    than the offline schedule's estimate. Without tracing the fast-path
    dispatch too, a fast-path-fed op's `res` would arrive with no pending
    entry to close (silently dropped -- the op never appears) or would
    wrongly close an unrelated, still-pending entry (stretching that bar out
    to the fast-path op's completion cycle instead).
    """
    primary_core_of: Dict[Tuple[int, int], int] = {
        (stage.flow_id, stage.stage_index): model.core_index(*stage.primary_core)
        for stage in model.flows
    }

    pending: Dict[int, List[RealInstruction]] = {}
    finished: List[RealInstruction] = []
    for snap in trace.cycles:
        for cs in snap.cores:
            queue = pending.setdefault(cs.core_index, [])
            if cs.has_result and queue:
                inst = queue.pop(0)
                inst.cycle_end = snap.cycle + 1
                inst.complete = True
                finished.append(inst)
            if cs.dispatched:
                flow_id = cs.disp_flow_id or 0
                stage_id = cs.disp_stage_id or 0
                primary = primary_core_of.get((flow_id, stage_id))
                queue.append(RealInstruction(
                    core_index=cs.core_index,
                    cycle_start=snap.cycle,
                    cycle_end=snap.cycle + 1,
                    flow_id=flow_id,
                    stage_id=stage_id,
                    op=model.opcode_to_name.get(cs.disp_op, str(cs.disp_op)),
                    used_fallback=primary is not None and primary != cs.core_index,
                    used_fastpath=cs.disp_fastpath,
                    complete=False,
                ))

    # Ops still open when the trace ended (still in flight / never resolved):
    # keep them visible rather than dropping them silently.
    for queue in pending.values():
        finished.extend(queue)

    finished.sort(key=lambda i: (i.core_index, i.cycle_start))
    return finished


class TimelineScene(QGraphicsScene):
    def __init__(self, model: WauModel, trace: ParsedTrace, total_cycles: int) -> None:
        super().__init__()
        self.model = model
        self.instructions = _build_real_instructions(model, trace)
        last_end = max((i.cycle_end for i in self.instructions), default=0)
        self.total_cycles = max(total_cycles, last_end, 32)
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

        # instructions actually observed in the RTL trace
        flow_colors: Dict[int, QColor] = {}
        for ins in self.instructions:
            color = flow_colors.setdefault(
                ins.flow_id,
                FLOW_COLORS[len(flow_colors) % len(FLOW_COLORS)],
            )
            x0 = LEFT_GUTTER + ins.cycle_start * CYCLE_WIDTH
            w = max(2, (ins.cycle_end - ins.cycle_start) * CYCLE_WIDTH - 2)
            y = HEADER_HEIGHT + ins.core_index * ROW_HEIGHT + 2
            rect = QGraphicsRectItem(x0, y, w, ROW_HEIGHT - 6)
            rect.setBrush(QBrush(color, Qt.SolidPattern if ins.complete else Qt.Dense4Pattern))
            rect.setPen(QPen(COLOR_FASTPATH_BORDER if ins.used_fastpath else QColor("#0b0c10"), 2 if ins.used_fastpath else 1))
            rect.setToolTip(
                f"flow={ins.flow_id} {ins.op} (stage {ins.stage_id})  "
                f"cycles [{ins.cycle_start}..{ins.cycle_end})  "
                f"{'FALLBACK' if ins.used_fallback else 'primary'}"
                + (" FAST-PATH" if ins.used_fastpath else "")
                + ("" if ins.complete else "  (no result observed before trace end)")
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

    def follow_cycle(self, cycle: int) -> None:
        """Keep the playhead on screen as the trace advances.

        A long trace makes the timeline scene far wider than the viewport, so
        without this the strip stays parked wherever it was scrolled and shows
        a stretch of schedule unrelated to the cycle being played (blank, for
        a run whose makespan is shorter than its trace).
        """
        x = LEFT_GUTTER + cycle * CYCLE_WIDTH
        self.ensureVisible(
            QRectF(x - 1, 0, 2, self.sceneRect().height()),
            CYCLE_WIDTH * 4,
            0,
        )
