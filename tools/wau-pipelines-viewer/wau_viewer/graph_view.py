"""Zoomable QGraphicsView of the WAU mesh + animated packets.

Each simulated cycle is rendered as a *phased* mini-animation so the order of
operations stays legible even at slow playback:

1. dispatch phase — operand packets travel hop-by-hop from the coordinator to
   their compute core along the same route the generated ``wau_highway_router``
   actually uses (the index-order chain for the default single-dimension
   highway, X-then-Y dimension order for a ``matrix`` highway);
2. execute phase — the operation being applied flashes on the core
   (``mul(14, 3)``-style badge);
3. result phase — data-plane deliveries travel back over the mesh with an
   elaboration "pop" where they land, and host outputs drop out of the
   coordinator toward the host lane.

All packet motion is driven by a single ``advance(t)`` progress function
(``t`` in ``[0, 1]`` within the current cycle) instead of wall-clock
``QPropertyAnimation``s, so live playback, single-step, and headless frame
capture all render the exact same deterministic frames. Concurrent packets are
offset onto parallel lanes and counted in the HUD to make the mesh-level
parallelism of a program visually evident.

Underneath the grid sits the *highway scheme*: a rail standing for the highway's
contracting bus, one numbered tick per core slot, and a stub tapping each core
onto it. The stub is the answer to "when does a core call a highway" — dim while
the core is quiet, dashed while it wants the highway, amber on the cycle it
calls from its own offered slot, and solid red for as long as it owns the
highway under a contract. The sliding marker is the slot the bus is currently
offering, and it parks on the holder while a contract is in force. Every one of
those states is read from the RTL trace, never inferred.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
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

from .model import CONTRACT_MODE_NAMES, WauModel
from .trace_parser import CycleSnapshot


CELL_SIZE = 180.0
CELL_PADDING = 60.0
PITCH = CELL_SIZE + CELL_PADDING

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

# Highway scheme: the contract bus rail, its cycling slot marker, and the
# per-core stubs that light up when a core calls the highway.
COLOR_HWY_RAIL = QColor("#3a4150")
COLOR_HWY_STUB = QColor("#404654")
COLOR_HWY_CALL = QColor("#ff8f3f")       # a core calling the highway
COLOR_HWY_HOLD = QColor("#ff5f5f")       # the core that owns it under contract
COLOR_HWY_OFFER = QColor("#6fd3ff")      # the slot the bus is offering now

COLOR_PACKET_OP = QColor("#5fd0a0")      # operands flowing toward a core
COLOR_PACKET_RESULT = QColor("#3f9cff")  # result data flowing back
COLOR_PACKET_HOST = QColor("#e8e2c0")    # final value handed back to the host
COLOR_OP_FLASH = QColor("#ffc94d")       # the operation applied at the core
PACKET_SIZE = 54.0
LANE_OFFSET = 18.0

# Phase windows within one animated cycle (fractions of the cycle animation).
# Dispatch finishes before the op flash peaks; results depart while the flash
# is still fading so the hand-off reads as cause -> effect.
PHASE_DISPATCH = (0.0, 0.40)
PHASE_EXEC = (0.36, 0.62)
PHASE_RESULT = (0.52, 0.94)
PHASE_HOST_OUT = (0.60, 1.0)


def _smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


class CoreItem(QGraphicsRectItem):
    def __init__(self, core_index: int, x: int, y: int, ops: List[str]) -> None:
        px = x * PITCH
        py = y * PITCH
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
        pen = QPen(COLOR_LINK_ACTIVE if on else COLOR_LINK, 5 if on else 3)
        self.setPen(pen)


class HighwayStub(QGraphicsLineItem):
    """The drop from a core down onto the contract-bus rail.

    This is the "core calls a highway" indicator: it is dim while the core is
    quiet, lights amber on the cycle the core actually calls the highway from
    its own offered slot, and turns red for as long as that core holds the
    highway under a contract.
    """

    def __init__(self, core: "CoreItem", slot_x: float, rail_y: float) -> None:
        super().__init__()
        self.core = core
        c = core.center()
        bottom = core.sceneBoundingRect().bottom()
        self.setLine(c.x(), bottom, slot_x, rail_y)
        self.setZValue(-4)
        self.set_state(request=False, call=False, hold=False)

    def set_state(self, request: bool, call: bool, hold: bool) -> None:
        if hold:
            self.setPen(QPen(COLOR_HWY_HOLD, 6))
        elif call:
            self.setPen(QPen(COLOR_HWY_CALL, 5))
        elif request:
            self.setPen(QPen(COLOR_HWY_CALL.darker(160), 3, Qt.DashLine))
        else:
            self.setPen(QPen(COLOR_HWY_STUB, 2, Qt.DotLine))


class HighwaySlotMarker(QGraphicsRectItem):
    """The bus slot currently being offered, sliding along the rail."""

    SIZE = 20.0

    def __init__(self, rail_y: float) -> None:
        super().__init__(QRectF(0, 0, self.SIZE, self.SIZE))
        self._rail_y = rail_y
        self.setBrush(QBrush(COLOR_HWY_OFFER))
        self.setPen(QPen(QColor("#10131a"), 2))
        self.setZValue(6)

    def move_to(self, x: float, granted: bool) -> None:
        self.setPos(x - self.SIZE / 2, self._rail_y - self.SIZE / 2)
        # While a contract is in force the bus stops offering slots, so the
        # marker parks on the holder rather than continuing to sweep.
        self.setBrush(QBrush(COLOR_HWY_HOLD if granted else COLOR_HWY_OFFER))


class DataPacketItem(QGraphicsObject):
    """A rounded-square 'data packet' rendered on top of the mesh.

    The item itself is dumb: it just paints a labelled rounded square. Its
    position/scale/opacity are driven each frame by ``WauScene.advance`` so
    frames are deterministic for both live playback and headless capture.
    """

    def __init__(self, label: str, color: QColor) -> None:
        super().__init__()
        self._label = label
        self._color = color
        self.setZValue(50)
        self.setTransformOriginPoint(PACKET_SIZE / 2, PACKET_SIZE / 2)

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

    def move_center(self, pt: QPointF) -> None:
        self.setPos(pt - QPointF(PACKET_SIZE / 2, PACKET_SIZE / 2))


class OpFlashItem(QGraphicsObject):
    """A badge that flashes the operation applied at a core (e.g. ``mul(14,3)``)."""

    WIDTH = 150.0
    HEIGHT = 40.0

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self.setZValue(60)
        self.setTransformOriginPoint(self.WIDTH / 2, self.HEIGHT / 2)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.WIDTH, self.HEIGHT)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: D401
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1, 1, self.WIDTH - 2, self.HEIGHT - 2)
        painter.setBrush(QBrush(COLOR_OP_FLASH))
        painter.setPen(QPen(QColor("#10131a"), 2))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QPen(QColor("#161006")))
        painter.setFont(QFont("Menlo", 12, QFont.Bold))
        painter.drawText(rect, Qt.AlignCenter, self._text)

    def move_center(self, pt: QPointF) -> None:
        self.setPos(pt - QPointF(self.WIDTH / 2, self.HEIGHT / 2))


@dataclass
class PacketTrack:
    """One packet's journey within the current cycle animation."""

    item: DataPacketItem
    points: List[QPointF]
    t0: float
    t1: float
    pop_at_end: bool = False
    links: List[MeshLink] = field(default_factory=list)
    _cum: List[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._cum = [0.0]
        for i in range(1, len(self.points)):
            d = self.points[i] - self.points[i - 1]
            self._cum.append(self._cum[-1] + math.hypot(d.x(), d.y()))

    @property
    def total_len(self) -> float:
        return self._cum[-1] if self._cum else 0.0

    def point_at(self, frac: float) -> Tuple[QPointF, int]:
        """Return (position, segment_index) at ``frac`` of the path length."""
        if len(self.points) == 1 or self.total_len <= 0.0:
            return self.points[0], 0
        target = min(1.0, max(0.0, frac)) * self.total_len
        for i in range(1, len(self.points)):
            if target <= self._cum[i] or i == len(self.points) - 1:
                seg_len = self._cum[i] - self._cum[i - 1]
                u = 0.0 if seg_len <= 0 else (target - self._cum[i - 1]) / seg_len
                a, b = self.points[i - 1], self.points[i]
                return QPointF(
                    a.x() + (b.x() - a.x()) * u,
                    a.y() + (b.y() - a.y()) * u,
                ), i - 1
        return self.points[-1], max(0, len(self.points) - 2)


@dataclass
class OpFlash:
    item: OpFlashItem
    center: QPointF
    t0: float
    t1: float


@dataclass
class CycleActivity:
    """Per-cycle concurrency summary surfaced in the HUD."""

    busy_cores: int = 0
    dispatches: int = 0
    deliveries: int = 0
    packets: int = 0
    host_output: Optional[int] = None


class WauScene(QGraphicsScene):
    """Holds the static mesh layout and phase-animates per-cycle activity."""

    def __init__(self, model: WauModel) -> None:
        super().__init__()
        self.model = model
        self.setBackgroundBrush(QBrush(QColor("#15171c")))
        self.core_items: Dict[int, CoreItem] = {}
        self.links: List[MeshLink] = []
        self._link_by_pair: Dict[Tuple[int, int], MeshLink] = {}
        self.animate_data = True
        self._tracks: List[PacketTrack] = []
        self._flashes: List[OpFlash] = []
        self._pulsed_links: List[MeshLink] = []
        self._peak_parallel = 0
        self._total_cycles = 0
        self._build_layout()

    # ----- static layout -----

    def _build_layout(self) -> None:
        for c in self.model.cores:
            item = CoreItem(c.index, c.x, c.y, c.operations)
            self.addItem(item)
            self.core_items[c.index] = item

        # Highway links, drawn from the topology the generator actually emitted:
        # one chain in core-index order for the default single-dimension
        # highway, the full neighbour mesh for `matrix`.
        for a_idx, b_idx in self.model.highway_segments():
            a_item = self.core_items.get(a_idx)
            b_item = self.core_items.get(b_idx)
            if a_item is None or b_item is None:
                continue
            link = MeshLink(a_item, b_item)
            self.links.append(link)
            self._link_by_pair[(min(a_idx, b_idx), max(a_idx, b_idx))] = link
        for link in self.links:
            self.addItem(link)
            link.refresh()

        grid_bottom = self.model.grid_y * PITCH

        self._build_highway_scheme(grid_bottom)
        grid_bottom = self._rail_y + 40

        # host endpoint: final values animate from the coordinator down here
        self._host_point = QPointF(CELL_SIZE / 2, grid_bottom + 44)
        host_text = QGraphicsSimpleTextItem(
            "host  →  coordinator @ core[0,0]   ←  results back to host"
        )
        host_text.setFont(QFont("Menlo", 11))
        host_text.setBrush(QBrush(COLOR_DIM_TEXT))
        host_text.setPos(0, grid_bottom + 64)
        self.addItem(host_text)

        # concurrency HUD (kept inside the scene so recordings include it)
        self.hud_cycle = QGraphicsSimpleTextItem("cycle –")
        self.hud_cycle.setFont(QFont("Menlo", 15, QFont.Bold))
        self.hud_cycle.setBrush(QBrush(COLOR_TEXT))
        self.hud_cycle.setPos(0, -112)
        self.addItem(self.hud_cycle)

        self.hud_parallel = QGraphicsSimpleTextItem("")
        self.hud_parallel.setFont(QFont("Menlo", 12))
        self.hud_parallel.setBrush(QBrush(COLOR_DIM_TEXT))
        self.hud_parallel.setPos(0, -86)
        self.addItem(self.hud_parallel)

        # legend for the packet colors, so the animation is self-explanatory
        legend_font = QFont("Menlo", 10)
        legend_x = 0.0
        for text, color in (
            ("■ operands → core", COLOR_PACKET_OP),
            ("■ op applied", COLOR_OP_FLASH),
            ("■ result over data mesh", COLOR_PACKET_RESULT),
            ("■ output → host", COLOR_PACKET_HOST),
            ("■ core calls highway", COLOR_HWY_CALL),
            ("■ holds highway (contract)", COLOR_HWY_HOLD),
            ("■ bus slot offered", COLOR_HWY_OFFER),
        ):
            entry = QGraphicsSimpleTextItem(text)
            entry.setFont(legend_font)
            entry.setBrush(QBrush(color))
            entry.setPos(legend_x, -30)
            self.addItem(entry)
            legend_x += entry.boundingRect().width() + 28

    def _build_highway_scheme(self, grid_bottom: float) -> None:
        """Draw the highway's contracting bus as an explicit scheme.

        The rail is the bus itself, one tick per core slot; the stub dropping
        from each core is that core's connection to it. Together they answer
        "which core is calling the highway right now, and who owns it" at a
        glance, instead of that arbitration being invisible.
        """
        hwy = self.model.highway
        self._rail_y = grid_bottom + 56
        self._stubs: Dict[int, HighwayStub] = {}
        self._slot_x: Dict[int, float] = {}

        left = 0.0
        right = max(1, self.model.grid_x) * PITCH - CELL_PADDING
        rail = QGraphicsLineItem(left, self._rail_y, right, self._rail_y)
        rail.setPen(QPen(COLOR_HWY_RAIL, 8))
        rail.setZValue(-6)
        self.addItem(rail)

        label = "single-dimension highway (chain)" if hwy.is_linear else "matrix highway (mesh)"
        bus_state = "contract bus" if hwy.contract_bus else "contract bus off"
        rail_label = QGraphicsSimpleTextItem(
            f"{label}  ·  {bus_state}"
            + (f"  ·  max burst {hwy.contract_max_burst}" if hwy.contract_bus else "")
        )
        rail_label.setFont(QFont("Menlo", 11, QFont.Bold))
        rail_label.setBrush(QBrush(COLOR_DIM_TEXT))
        rail_label.setPos(left, self._rail_y + 28)
        self.addItem(rail_label)

        # One slot tick per core, spread evenly along the rail in *slot* order —
        # the order wau_highway_contract offers them in. Slots get their own
        # position rather than sitting under their core, because cores in
        # different rows share an x and their taps would otherwise overlap.
        count = max(1, self.model.core_count)
        pitch = (right - left) / count
        tick_font = QFont("Menlo", 8)
        for idx in range(count):
            core_item = self.core_items.get(idx)
            if core_item is None:
                continue
            x = left + (idx + 0.5) * pitch
            self._slot_x[idx] = x
            tick = QGraphicsLineItem(x, self._rail_y - 10, x, self._rail_y + 10)
            tick.setPen(QPen(COLOR_HWY_RAIL.lighter(150), 2))
            tick.setZValue(-5)
            self.addItem(tick)

            tick_label = QGraphicsSimpleTextItem(str(idx))
            tick_label.setFont(tick_font)
            tick_label.setBrush(QBrush(COLOR_DIM_TEXT))
            tick_label.setPos(x - 4, self._rail_y + 12)
            self.addItem(tick_label)

            stub = HighwayStub(core_item, x, self._rail_y)
            self.addItem(stub)
            self._stubs[idx] = stub

        self._slot_marker = HighwaySlotMarker(self._rail_y)
        self._slot_marker.move_to(self._slot_x.get(0, left), granted=False)
        self.addItem(self._slot_marker)

        self.hud_highway = QGraphicsSimpleTextItem("")
        self.hud_highway.setFont(QFont("Menlo", 12))
        self.hud_highway.setBrush(QBrush(COLOR_HWY_OFFER))
        self.hud_highway.setPos(0, -60)
        self.addItem(self.hud_highway)

    def _apply_highway_state(self, snap: CycleSnapshot) -> None:
        """Light the stub of every core touching the highway this cycle."""
        hwy = snap.highway
        for c in snap.cores:
            stub = self._stubs.get(c.core_index)
            if stub is not None:
                stub.set_state(
                    request=c.highway_request,
                    call=c.highway_call,
                    hold=c.highway_holder,
                )

        marker_core = hwy.grant_core if hwy.grant_valid else hwy.slot
        if marker_core in self._slot_x:
            self._slot_marker.move_to(self._slot_x[marker_core], hwy.grant_valid)

        callers = [c.core_index for c in snap.cores if c.highway_call]
        if hwy.grant_valid:
            mode = CONTRACT_MODE_NAMES.get(hwy.grant_mode, f"mode{hwy.grant_mode}")
            state = (
                f"highway held by core {hwy.grant_core} "
                f"({mode}, {hwy.grant_remaining} beats left)"
            )
        elif callers:
            state = f"core {callers[0]} calling highway on slot {hwy.slot}"
        else:
            state = f"highway open · offering slot {hwy.slot}"
        self.hud_highway.setText(
            f"{state}   grants {hwy.grant_count}   deferred {hwy.defer_count}"
        )

    def set_total_cycles(self, total: int) -> None:
        self._total_cycles = total

    # ----- per-cycle content -----

    def _clear_dynamic_items(self) -> None:
        for track in self._tracks:
            if track.item.scene() is self:
                self.removeItem(track.item)
        for flash in self._flashes:
            if flash.item.scene() is self:
                self.removeItem(flash.item)
        self._tracks = []
        self._flashes = []
        for link in self._pulsed_links:
            link.pulse(False)
        self._pulsed_links = []

    def _route_points(self, src: int, dst: int, lane: float) -> Tuple[List[QPointF], List[MeshLink]]:
        route = self.model.highway_route(src, dst)
        offset = QPointF(lane * LANE_OFFSET, lane * LANE_OFFSET)
        points = [self.core_items[idx].center() + offset for idx in route]
        links: List[MeshLink] = []
        for a, b in zip(route, route[1:]):
            link = self._link_by_pair.get((min(a, b), max(a, b)))
            if link is not None:
                links.append(link)
        return points, links

    def _add_track(
        self,
        points: List[QPointF],
        links: List[MeshLink],
        label: str,
        color: QColor,
        span: Tuple[float, float],
        pop: bool,
    ) -> None:
        item = DataPacketItem(label, color)
        item.setVisible(False)
        self.addItem(item)
        self._tracks.append(PacketTrack(
            item=item, points=points, t0=span[0], t1=span[1],
            pop_at_end=pop, links=links,
        ))

    def apply_cycle(self, snap: CycleSnapshot) -> CycleActivity:
        """Load one cycle's activity; call ``advance(t)`` afterwards to render."""
        self._clear_dynamic_items()
        self._apply_highway_state(snap)
        activity = CycleActivity()

        dispatch_events = []
        delivery_events = []

        for c in snap.cores:
            item = self.core_items.get(c.core_index)
            if item is None:
                continue
            disp_text = ""
            res_text = ""
            flow_badge = ""
            used_fallback = False
            if c.busy:
                activity.busy_cores += 1
            if c.dispatched:
                op_name = self.model.opcode_to_name.get(c.disp_op or 0, f"op{c.disp_op}")
                if c.disp_use_immediate:
                    disp_text = f"{op_name}(a={c.disp_a}, imm={c.disp_immediate_b})  s={c.disp_stage_id}"
                else:
                    disp_text = f"{op_name}(a={c.disp_a}, b={c.disp_b})  s={c.disp_stage_id}"
                flow_badge = f"F{c.disp_flow_id}"
                used_fallback = self._is_fallback(c.disp_flow_id, c.disp_stage_id, c.core_index)
                activity.dispatches += 1
                dispatch_events.append(c)
            if c.has_result:
                res_text = f"= {c.res_value}  (s={c.res_stage_id}, F{c.res_flow_id})"
                flow_badge = flow_badge or f"F{c.res_flow_id}"
            if c.data_delivered and c.ddeliv_src is not None:
                activity.deliveries += 1
                delivery_events.append(c)

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
            # 1) operand packets: coordinator -> compute core, hop by hop.
            n = len(dispatch_events)
            for i, c in enumerate(dispatch_events):
                lane = i - (n - 1) / 2.0
                if c.disp_use_immediate:
                    label = f"{c.disp_a},#{c.disp_immediate_b}"
                else:
                    label = f"{c.disp_a},{c.disp_b}"
                points, links = self._route_points(
                    COORDINATOR_CORE_INDEX, c.core_index, lane
                )
                self._add_track(points, links, label, COLOR_PACKET_OP,
                                PHASE_DISPATCH, pop=False)
                # 2) the operation applied there flashes while it executes
                op_name = self.model.opcode_to_name.get(c.disp_op or 0, f"op{c.disp_op}")
                if c.disp_use_immediate:
                    flash_text = f"{op_name}({c.disp_a}, #{c.disp_immediate_b})"
                else:
                    flash_text = f"{op_name}({c.disp_a}, {c.disp_b})"
                flash = OpFlashItem(flash_text)
                flash.setVisible(False)
                self.addItem(flash)
                core_item = self.core_items[c.core_index]
                self._flashes.append(OpFlash(
                    item=flash,
                    center=core_item.center() + QPointF(0, CELL_SIZE * 0.18),
                    t0=PHASE_EXEC[0],
                    t1=PHASE_EXEC[1],
                ))

            # 3) result data travelling over the data mesh (pop where it lands)
            n = len(delivery_events)
            for i, c in enumerate(delivery_events):
                if c.ddeliv_src not in self.core_items:
                    continue
                lane = i - (n - 1) / 2.0
                points, links = self._route_points(c.ddeliv_src, c.core_index, lane)
                self._add_track(points, links, str(c.ddeliv_value),
                                COLOR_PACKET_RESULT, PHASE_RESULT, pop=True)

            # 4) a completed flow's value leaving toward the host
            if snap.host_out.delivered:
                activity.host_output = snap.host_out.value
                start = self.core_items[COORDINATOR_CORE_INDEX].center()
                self._add_track(
                    [start, self._host_point],
                    [],
                    f"out={snap.host_out.value}",
                    COLOR_PACKET_HOST,
                    PHASE_HOST_OUT,
                    pop=True,
                )

        activity.packets = len(self._tracks)
        parallel = max(activity.dispatches + activity.deliveries, activity.busy_cores)
        self._peak_parallel = max(self._peak_parallel, parallel)

        total = f" / {self._total_cycles}" if self._total_cycles else ""
        self.hud_cycle.setText(f"cycle {snap.cycle}{total}")
        self.hud_parallel.setText(
            f"busy cores {activity.busy_cores}/{self.model.core_count}   "
            f"packets in flight {activity.packets}   "
            f"peak parallel ops {self._peak_parallel}   "
            f"mesh hops {snap.obs.hops}  stalls {snap.obs.stalls}"
        )

        # pulse every link the cycle's packets will traverse
        for track in self._tracks:
            for link in track.links:
                if link not in self._pulsed_links:
                    link.pulse(True)
                    self._pulsed_links.append(link)

        return activity

    def reset_peaks(self) -> None:
        self._peak_parallel = 0

    # ----- frame rendering -----

    def advance(self, t: float) -> None:
        """Render the current cycle's animation at progress ``t`` in [0, 1].

        Pure with respect to the loaded cycle: calling it with any sequence of
        ``t`` values yields the same frame for the same ``t``, which is what
        makes headless recordings identical to live playback.
        """
        t = min(1.0, max(0.0, t))
        for track in self._tracks:
            if track.t1 <= track.t0:
                continue
            u = (t - track.t0) / (track.t1 - track.t0)
            if u <= 0.0 or u >= 1.0:
                track.item.setVisible(False)
                continue
            pos, _seg = track.point_at(_smoothstep(u))
            track.item.move_center(pos)
            track.item.setVisible(True)
            # fade in quickly; fade out as the packet is consumed
            if u < 0.12:
                opacity = u / 0.12
            elif u > 0.9:
                opacity = (1.0 - u) / 0.1
            else:
                opacity = 1.0
            track.item.setOpacity(opacity)
            scale = 1.0
            if track.pop_at_end and u > 0.78:
                # elaboration pop as the data lands
                bump = math.sin((u - 0.78) / 0.22 * math.pi)
                scale = 1.0 + 0.5 * bump
            track.item.setScale(scale)

        for flash in self._flashes:
            if flash.t1 <= flash.t0:
                continue
            u = (t - flash.t0) / (flash.t1 - flash.t0)
            if u <= 0.0 or u >= 1.0:
                flash.item.setVisible(False)
                continue
            flash.item.move_center(flash.center)
            flash.item.setVisible(True)
            grow = _smoothstep(min(1.0, u * 3.0))
            flash.item.setScale(0.7 + 0.35 * grow)
            flash.item.setOpacity(1.0 if u < 0.7 else (1.0 - u) / 0.3)

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
