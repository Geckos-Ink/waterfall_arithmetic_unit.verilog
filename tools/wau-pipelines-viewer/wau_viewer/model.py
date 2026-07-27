"""Static layout model derived from `wau_program.json` + `wau_schedule.json`."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class CoreInfo:
    index: int
    x: int
    y: int
    operations: List[str] = field(default_factory=list)
    data_types: List[str] = field(default_factory=list)


@dataclass
class FlowStageInfo:
    flow_id: int
    flow_name: str
    stage_index: int
    op: str
    opcode: int
    latency: int
    primary_core: Tuple[int, int]
    fallback_core: Optional[Tuple[int, int]]


@dataclass
class ScheduleInstruction:
    cycle_start: int
    cycle_end: int
    flow_id: int
    node_id: str
    op: str
    core_xy: Tuple[int, int]
    core_index: int
    used_fallback: bool
    dtype: str


@dataclass
class HighwayInfo:
    """The emitted highway fabric's shape, read back from `wau_program.json`.

    The viewer must draw and route over the highway the generator actually
    emitted, so this mirrors `device.highway` rather than assuming a mesh.
    """

    topology: str = "linear"
    contract_bus: bool = True
    contract_max_burst: int = 8
    contract_lease_cycles: int = 64

    @property
    def is_linear(self) -> bool:
        return self.topology == "linear"


# Contract-bus modes, mirroring wau_highway_contract's localparams.
CONTRACT_MODE_NAMES = {0: "pong", 1: "burst", 2: "stream", 3: "reserve"}


@dataclass
class WauModel:
    grid_x: int
    grid_y: int
    core_count: int
    cores: List[CoreInfo]
    opcode_to_name: Dict[int, str]
    flow_id_to_name: Dict[int, str]
    flows: List[FlowStageInfo]
    schedule: List[ScheduleInstruction]
    makespan_cycles: int
    highway: HighwayInfo = field(default_factory=HighwayInfo)

    def core_index(self, x: int, y: int) -> int:
        return y * self.grid_x + x

    def core_xy(self, idx: int) -> Tuple[int, int]:
        return (idx % self.grid_x, idx // self.grid_x)

    def highway_route(self, src: int, dst: int) -> List[int]:
        """The route the emitted router will actually take for ``src -> dst``."""
        if self.highway.is_linear:
            return linear_route(src, dst)
        return manhattan_route(self.grid_x, self.grid_y, src, dst)

    def highway_segments(self) -> List[Tuple[int, int]]:
        """The highway's drawn link set, as ordered ``(lower, higher)`` pairs."""
        if self.highway.is_linear:
            return linear_segments(self.core_count)
        return matrix_segments(self.grid_x, self.grid_y)


def load_model(program_path: Path, schedule_path: Optional[Path]) -> WauModel:
    prog = json.loads(Path(program_path).read_text())
    grid_x = prog["device"]["grid"]["x"]
    grid_y = prog["device"]["grid"]["y"]
    core_count = grid_x * grid_y

    hwy = prog["device"].get("highway") or {}
    highway = HighwayInfo(
        topology=str(hwy.get("topology", "linear")),
        contract_bus=bool(hwy.get("contract_bus", True)),
        contract_max_burst=int(hwy.get("contract_max_burst", 8)),
        contract_lease_cycles=int(hwy.get("contract_lease_cycles", 64)),
    )

    capabilities = {
        (cap["core"]["x"], cap["core"]["y"]): cap
        for cap in prog.get("compiler", {}).get("core_capabilities", [])
    }
    cores: List[CoreInfo] = []
    for y in range(grid_y):
        for x in range(grid_x):
            cap = capabilities.get((x, y), {})
            cores.append(CoreInfo(
                index=y * grid_x + x,
                x=x,
                y=y,
                operations=list(cap.get("operations", [])),
                data_types=list(cap.get("data_types", [])),
            ))

    flows: List[FlowStageInfo] = []
    opcode_to_name: Dict[int, str] = {}
    flow_id_to_name: Dict[int, str] = {}
    for flow in prog.get("flows", []):
        flow_id = flow["flow_id"]
        flow_name = flow.get("name", f"flow_{flow_id}")
        flow_id_to_name[flow_id] = flow_name
        for stage in flow.get("stages", []):
            opcode_to_name[stage["opcode"]] = stage["op"]
            fb = stage.get("fallback_core")
            flows.append(FlowStageInfo(
                flow_id=flow_id,
                flow_name=flow_name,
                stage_index=stage["stage_index"],
                op=stage["op"],
                opcode=stage["opcode"],
                latency=stage["latency"],
                primary_core=(stage["primary_core"]["x"], stage["primary_core"]["y"]),
                fallback_core=(fb["x"], fb["y"]) if fb else None,
            ))

    schedule: List[ScheduleInstruction] = []
    makespan = 0
    if schedule_path and Path(schedule_path).exists():
        sch = json.loads(Path(schedule_path).read_text())
        makespan = sch.get("makespan_cycles", 0)
        for ins in sch.get("instructions", []):
            schedule.append(ScheduleInstruction(
                cycle_start=ins["cycle_start"],
                cycle_end=ins["cycle_end"],
                flow_id=ins["flow_id"],
                node_id=ins.get("node_id", ""),
                op=ins["op"],
                core_xy=(ins["core"]["x"], ins["core"]["y"]),
                core_index=ins["core_index"],
                used_fallback=ins.get("used_fallback", False),
                dtype=ins.get("dtype", ""),
            ))

    return WauModel(
        grid_x=grid_x,
        grid_y=grid_y,
        core_count=core_count,
        cores=cores,
        opcode_to_name=opcode_to_name,
        flow_id_to_name=flow_id_to_name,
        flows=flows,
        schedule=schedule,
        makespan_cycles=makespan,
        highway=highway,
    )


def derive_auto_stimulus(model: WauModel) -> List[Tuple[int, int, int]]:
    """Build a deterministic stimulus covering every distinct flow id once."""
    seen: List[Tuple[int, int, int]] = []
    used: set = set()
    for stage in model.flows:
        if stage.flow_id in used:
            continue
        used.add(stage.flow_id)
        seen.append((stage.flow_id, 10 + stage.flow_id, 3 + stage.flow_id))
    return seen or [(1, 10, 4)]


def derive_stress_stimulus(
    model: WauModel,
    count: int,
    seed: int = 7,
    value_min: int = 1,
    value_max: int = 99,
) -> List[Tuple[int, int, int]]:
    """Build a seeded stress stimulus of ``count`` packets across all flows.

    Flow ids are interleaved round-robin so consecutive packets target
    *different* flows whenever more than one flow exists: the multi-issue
    coordinator blocks a second in-flight copy of the same flow id, so
    interleaving is what actually exercises cross-flow concurrency on the mesh.
    Operand values are drawn from a deterministic RNG so runs are reproducible.
    """
    if count <= 0:
        raise ValueError("stress stimulus count must be positive")
    flow_ids = sorted({stage.flow_id for stage in model.flows}) or [1]
    rng = random.Random(seed)
    out: List[Tuple[int, int, int]] = []
    for i in range(count):
        flow_id = flow_ids[i % len(flow_ids)]
        out.append((
            flow_id,
            rng.randint(value_min, value_max),
            rng.randint(value_min, value_max),
        ))
    return out


def linear_route(src: int, dst: int) -> List[int]:
    """Reconstruct the single-dimension ("linear") highway route.

    Mirrors the generated ``wau_highway_router``'s ``route_dir`` under
    ``device.highway.topology = "linear"``: within a layer the highway is one
    chain in core-index order, so the router simply compares the destination
    index against its own and steps PREV or NEXT. Row-to-row movement is the
    chain's wrap hop, not a separate north/south link.

    Returns the inclusive list of core indices from ``src`` to ``dst``.
    """
    step = 1 if dst >= src else -1
    return list(range(src, dst + step, step)) if src != dst else [src]


def linear_segments(core_count: int) -> List[Tuple[int, int]]:
    """Every link of the linear highway chain, as ``(lower, higher)`` pairs."""
    return [(i, i + 1) for i in range(max(0, core_count - 1))]


def matrix_segments(grid_x: int, grid_y: int) -> List[Tuple[int, int]]:
    """Every link of the full mesh highway, as ``(lower, higher)`` pairs."""
    out: List[Tuple[int, int]] = []
    for y in range(grid_y):
        for x in range(grid_x):
            here = y * grid_x + x
            if x + 1 < grid_x:
                out.append((here, here + 1))
            if y + 1 < grid_y:
                out.append((here, here + grid_x))
    return out


def manhattan_route(grid_x: int, grid_y: int, src: int, dst: int) -> List[int]:
    """Reconstruct the dimension-order (X-first, then Y) mesh route.

    Mirrors the generated ``wau_highway_router``'s ``route_dir`` priority
    (EAST/WEST before SOUTH/NORTH), so animated packets travel the same
    intermediate routers the RTL actually forwards them through. Returns the
    inclusive list of core indices from ``src`` to ``dst``.
    """
    def xy(idx: int) -> Tuple[int, int]:
        return idx % grid_x, (idx // grid_x) % grid_y

    x, y = xy(src)
    dx, dy = xy(dst)
    route = [src]
    while x != dx:
        x += 1 if dx > x else -1
        route.append(y * grid_x + x)
    while y != dy:
        y += 1 if dy > y else -1
        route.append(y * grid_x + x)
    return route
