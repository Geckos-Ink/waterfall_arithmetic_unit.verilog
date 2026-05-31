"""Zoomable QGraphicsView of the WAU mesh + animated packets."""

from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from .model import WauModel
from .trace_parser import CycleSnapshot


CELL_SIZE = 180.0
CELL_PADDING = 60.0

# Matches wau_top's COORDINATOR_CORE_INDEX (the host/coordinator endpoint).
COORDINATOR_CORE_INDEX = 0

COLOR_IDLE = QColor("#2c2f36")
COLOR_BUSY = QColor("#c98a16")
COLOR_DISPATCH = QColor("#4caf50")
COLOR_RESULT = QColor("#3f9cff")
COLOR_FALLBACK = QColor("#b54a4a")
COLOR_TEXT = QColor("#f0f0f0")
COLOR_DIM_TEXT = QColor("#a0a4ad")
COLOR_LINK = QColor("#555a66")
COLOR_LINK_ACTIVE = QColor("#ffe27a")


class CoreItem(QGraphicsRectItem):
    def __init__(self, core_index: int, x: int, y: int, ops: List[str]) -> None:
        px = x * (CELL_SIZE + CELL_PADDING)
        py = y * (CELL_SIZE + CELL_PADDING)
        super().__init__(QRectF(px, py, CELL_SIZE, CELL_SIZE))
        self.core_index = core_index
        self.x_grid = x
        self.y_grid = y
        self.base_brush = QBrush(COLOR_IDLE)
        self.setBrush(self.base_brush)
        self.setPen(QPen(QColor("#1a1c20"), 2))
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

        font_title = QFont("Menlo", 12, QFont.Bold)
        font_small = QFont("Menlo", 9)

        self.title = QGraphicsSimpleTextItem(f"core[{x},{y}]  #{core_index}", self)
        self.title.setFont(font_title)
        self.title.setBrush(QBrush(COLOR_TEXT))
        self.title.setPos(px + 10, py + 8)

        ops_text = ", ".join(ops) if ops else "(no ops)"
        self.ops = QGraphicsSimpleTextItem(f"ops: {ops_text}", self)
        self.ops.setFont(font_small)
        self.ops.setBrush(QBrush(COLOR_DIM_TEXT))
        self.ops.setPos(px + 10, py + 30)

        self.state_line = QGraphicsSimpleTextItem("idle", self)
        self.state_line.setFont(QFont("Menlo", 10, QFont.Bold))
        self.state_line.setBrush(QBrush(COLOR_TEXT))
        self.state_line.setPos(px + 10, py + 56)

        self.detail_line = QGraphicsSimpleTextItem("", self)
        self.detail_line.setFont(font_small)
        self.detail_line.setBrush(QBrush(COLOR_DIM_TEXT))
        self.detail_line.setPos(px + 10, py + 80)

        self.cache_line = QGraphicsSimpleTextItem("cache 0/0", self)
        self.cache_line.setFont(font_small)
        self.cache_line.setBrush(QBrush(COLOR_DIM_TEXT))
        self.cache_line.setPos(px + 10, py + CELL_SIZE - 22)

        self.flow_badge = QGraphicsSimpleTextItem("", self)
        self.flow_badge.setFont(QFont("Menlo", 10, QFont.Bold))
        self.flow_badge.setBrush(QBrush(QColor("#1a1c20")))
        self.flow_badge.setPos(px + CELL_SIZE - 60, py + 10)

    def center(self) -> QPointF:
        return self.sceneBoundingRect().center()

    def apply_state(
        self,
        busy: bool,
        dispatched: bool,
        has_result: bool,
        disp_text: str,
        res_text: str,
        cache_text: str,
        flow_badge: str,
        used_fallback: bool,
    ) -> None:
        if has_result:
            color = COLOR_RESULT
            state = "RESULT"
        elif dispatched:
            color = COLOR_DISPATCH
            state = "DISPATCH"
        elif busy:
            color = COLOR_BUSY
            state = "BUSY"
        else:
            color = COLOR_IDLE
            state = "idle"
        if used_fallback:
            color = COLOR_FALLBACK
            state = state + " (fb)"
        self.setBrush(QBrush(color))
        self.state_line.setText(state)
        # show whichever of dispatch/result is most recent
        self.detail_line.setText(disp_text or res_text)
        self.cache_line.setText(cache_text)
        self.flow_badge.setText(flow_badge)


class MeshLink(QGraphicsLineItem):
    def __init__(self, a: CoreItem, b: CoreItem) -> None:
        super().__init__()
        self.a = a
        self.b = b
        self.setPen(QPen(COLOR_LINK, 3))
        self.setZValue(-5)
        self.refresh()

    def refresh(self) -> None:
        ca = self.a.center()
        cb = self.b.center()
        self.setLine(ca.x(), ca.y(), cb.x(), cb.y())

    def pulse(self, on: bool) -> None:
        pen = QPen(COLOR_LINK_ACTIVE if on else COLOR_LINK, 4 if on else 3)
        self.setPen(pen)


COLOR_PACKET_OP = QColor("#5fd0a0")     # operands flowing toward a core
COLOR_PACKET_RESULT = QColor("#3f9cff")  # result data flowing back
PACKET_SIZE = 54.0


class DataPacketItem(QGraphicsObject):
    """A rounded-square 'data packet' rendered on top of the mesh.

    It carries a short label (the operand/result value), eases from a source
    core to a destination core to make data movement explicit, and can do a
    brief scale 'pop' at the destination to suggest the elaboration/transform
    happening there. Movement and transform are driven by QPropertyAnimation on
    the item's ``pos`` and ``scale`` so the effect reads clearly even at low fps.
    """

    def __init__(self, label: str, color: QColor) -> None:
        super().__init__()
        self._label = label
        self._color = color
        self.setZValue(50)
        self.setTransformOriginPoint(PACKET_SIZE / 2, PACKET_SIZE / 2)
        self._anims: list[QPropertyAnimation] = []

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, PACKET_SIZE, PACKET_SIZE)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: D401
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(2, 2, PACKET_SIZE - 4, PACKET_SIZE - 4)
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(QColor("#10131a"), 2))
        painter.drawRoundedRect(rect, 14, 14)
        painter.setPen(QPen(QColor("#0d1016")))
        painter.setFont(QFont("Menlo", 11, QFont.Bold))
        painter.drawText(rect, Qt.AlignCenter, self._label)

    def animate_move(
        self,
        start: QPointF,
        end: QPointF,
        duration_ms: int,
        *,
        pop_at_end: bool,
        on_done,
    ) -> None:
        half = QPointF(PACKET_SIZE / 2, PACKET_SIZE / 2)
        self.setPos(start - half)

        move = QPropertyAnimation(self, b"pos")
        move.setStartValue(start - half)
        move.setEndValue(end - half)
        move.setDuration(max(60, duration_ms))
        move.setEasingCurve(QEasingCurve.InOutCubic)
        self._anims.append(move)

        if pop_at_end:
            pop = QPropertyAnimation(self, b"scale")
            pop.setStartValue(1.0)
            pop.setKeyValueAt(0.7, 1.0)
            pop.setKeyValueAt(0.85, 1.5)
            pop.setEndValue(1.0)
            pop.setDuration(max(60, duration_ms))
            self._anims.append(pop)

        move.finished.connect(on_done)
        for anim in self._anims:
            anim.start()


class WauScene(QGraphicsScene):
    """Holds the static mesh layout and applies per-cycle updates."""

    def __init__(self, model: WauModel) -> None:
        super().__init__()
        self.model = model
        self.setBackgroundBrush(QBrush(QColor("#15171c")))
        self.core_items: Dict[int, CoreItem] = {}
        self.links: List[MeshLink] = []
        self._build_layout()
        self._last_active_links: List[MeshLink] = []
        # Live data-packet animations.
        self._packets: List[DataPacketItem] = []
        self.animate_data = True
        self.step_interval_ms = 125

    def set_step_interval(self, ms: int) -> None:
        """Match packet animation duration to the playback cadence."""
        self.step_interval_ms = max(40, int(ms))

    def _clear_packets(self) -> None:
        for pkt in self._packets:
            if pkt.scene() is self:
                self.removeItem(pkt)
        self._packets = []

    def _spawn_packet(
        self, src_index: int, dst_index: int, label: str, color: QColor, pop: bool
    ) -> None:
        src = self.core_items.get(src_index)
        dst = self.core_items.get(dst_index)
        if src is None or dst is None:
            return
        pkt = DataPacketItem(label, color)
        self.addItem(pkt)
        self._packets.append(pkt)

        def _done() -> None:
            if pkt.scene() is self:
                self.removeItem(pkt)
            if pkt in self._packets:
                self._packets.remove(pkt)

        duration = min(self.step_interval_ms, 600)
        pkt.animate_move(src.center(), dst.center(), duration, pop_at_end=pop, on_done=_done)

    def _build_layout(self) -> None:
        for c in self.model.cores:
            item = CoreItem(c.index, c.x, c.y, c.operations)
            self.addItem(item)
            self.core_items[c.index] = item

        # mesh links (horizontal + vertical neighbors)
        for y in range(self.model.grid_y):
            for x in range(self.model.grid_x):
                here = self.core_items[self.model.core_index(x, y)]
                if x + 1 < self.model.grid_x:
                    nb = self.core_items[self.model.core_index(x + 1, y)]
                    self.links.append(MeshLink(here, nb))
                if y + 1 < self.model.grid_y:
                    nb = self.core_items[self.model.core_index(x, y + 1)]
                    self.links.append(MeshLink(here, nb))
        for link in self.links:
            self.addItem(link)
            link.refresh()

        # legend / host endpoints
        host_text = QGraphicsSimpleTextItem(
            "host  →  coordinator @ core[0,0]   ←  results back to host"
        )
        host_text.setFont(QFont("Menlo", 11))
        host_text.setBrush(QBrush(COLOR_DIM_TEXT))
        host_text.setPos(0, self.model.grid_y * (CELL_SIZE + CELL_PADDING) + 4)
        self.addItem(host_text)

    def apply_cycle(self, snap: CycleSnapshot) -> None:
        # reset previous link pulses
        for link in self._last_active_links:
            link.pulse(False)
        self._last_active_links = []

        # restart data-packet animations for this cycle
        self._clear_packets()

        active_cores = set()

        for c in snap.cores:
            item = self.core_items.get(c.core_index)
            if item is None:
                continue
            disp_text = ""
            res_text = ""
            flow_badge = ""
            used_fallback = False
            if c.dispatched:
                op_name = self.model.opcode_to_name.get(c.disp_op or 0, f"op{c.disp_op}")
                if c.disp_use_immediate:
                    disp_text = f"{op_name}(a={c.disp_a}, imm={c.disp_immediate_b})  s={c.disp_stage_id}"
                else:
                    disp_text = f"{op_name}(a={c.disp_a}, b={c.disp_b})  s={c.disp_stage_id}"
                flow_badge = f"F{c.disp_flow_id}"
                active_cores.add(c.core_index)
                # mark as fallback if the schedule says primary is elsewhere
                used_fallback = self._is_fallback(c.disp_flow_id, c.disp_stage_id, c.core_index)
            if c.has_result:
                res_text = f"= {c.res_value}  (s={c.res_stage_id}, F{c.res_flow_id})"
                flow_badge = flow_badge or f"F{c.res_flow_id}"
                active_cores.add(c.core_index)

            cache_text = f"cache {c.cache_hit_count}/{c.cache_lookup_count}"
            item.apply_state(
                busy=c.busy,
                dispatched=c.dispatched,
                has_result=c.has_result,
                disp_text=disp_text,
                res_text=res_text,
                cache_text=cache_text,
                flow_badge=flow_badge,
                used_fallback=used_fallback,
            )

            if self.animate_data:
                # operands streaming from the coordinator to the compute core
                if c.dispatched:
                    if c.disp_use_immediate:
                        label = f"{c.disp_a},#{c.disp_immediate_b}"
                    else:
                        label = f"{c.disp_a},{c.disp_b}"
                    self._spawn_packet(
                        COORDINATOR_CORE_INDEX, c.core_index, label, COLOR_PACKET_OP, pop=False
                    )
                # result data travelling back over the data mesh (with an
                # elaboration 'pop' where it lands)
                if c.data_delivered and c.ddeliv_src is not None:
                    self._spawn_packet(
                        c.ddeliv_src,
                        c.core_index,
                        str(c.ddeliv_value),
                        COLOR_PACKET_RESULT,
                        pop=True,
                    )

        # pulse any link that touches an active core (a coarse highlight under
        # the animated data packets; exact data-plane deliveries are now traced
        # per core via `data_delivered` and drawn as moving DataPacketItems).
        for link in self.links:
            if link.a.core_index in active_cores or link.b.core_index in active_cores:
                link.pulse(True)
                self._last_active_links.append(link)

    def _is_fallback(self, flow_id, stage_id, core_index) -> bool:
        if flow_id is None or stage_id is None:
            return False
        for stage in self.model.flows:
            if stage.flow_id == flow_id and stage.stage_index == stage_id:
                primary = self.model.core_index(*stage.primary_core)
                return core_index != primary
        return False


class WauGraphView(QGraphicsView):
    """Zoom-with-wheel, drag-to-pan QGraphicsView wrapper."""

    def __init__(self, scene: WauScene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMinimumSize(600, 400)
        self._zoom = 1.0
        self.fit_to_scene()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if 0.1 <= new_zoom <= 10.0:
            self.scale(factor, factor)
            self._zoom = new_zoom

    def fit_to_scene(self) -> None:
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()
