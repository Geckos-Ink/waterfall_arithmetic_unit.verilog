from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import Coord, FlowSpec, ProjectConfig


@dataclass(frozen=True)
class CompiledStage:
    stage_index: int
    op_name: str
    opcode: int
    latency: int
    pipelined: bool
    primary_core: Coord
    fallback_core: Coord | None
    immediate_b: int | None
    allow_adaptive: bool


@dataclass(frozen=True)
class CompiledFlow:
    flow_id: int
    flow_slot: int
    name: str
    entry: Coord
    exit: Coord | None
    stages: tuple[CompiledStage, ...]


@dataclass(frozen=True)
class CompiledProject:
    config: ProjectConfig
    flows: tuple[CompiledFlow, ...]
    core_load: dict[Coord, int]

    @property
    def max_stages(self) -> int:
        return max(len(flow.stages) for flow in self.flows)


def _all_coords(grid_x: int, grid_y: int) -> list[Coord]:
    ordered: list[Coord] = []
    for y in range(grid_y):
        if y % 2 == 0:
            xs = range(grid_x)
        else:
            xs = range(grid_x - 1, -1, -1)
        for x in xs:
            ordered.append(Coord(x=x, y=y))
    return ordered


def _nearest_order_index(coords: list[Coord], target: Coord) -> int:
    best_idx = 0
    best_distance = 1_000_000
    for idx, coord in enumerate(coords):
        d = abs(coord.x - target.x) + abs(coord.y - target.y)
        if d < best_distance:
            best_distance = d
            best_idx = idx
    return best_idx


def _is_coord_inside(coord: Coord, *, grid_x: int, grid_y: int) -> bool:
    return 0 <= coord.x < grid_x and 0 <= coord.y < grid_y


def _coords_in_radius(center: Coord, radius: int, *, grid_x: int, grid_y: int) -> Iterable[Coord]:
    if radius <= 0:
        return []
    found: list[Coord] = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            cand = Coord(center.x + dx, center.y + dy)
            if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y):
                found.append(cand)
    found.sort(key=lambda c: (abs(c.x - center.x) + abs(c.y - center.y), c.y, c.x))
    return found


def _resolve_primary_core(
    *,
    flow: FlowSpec,
    stage_index: int,
    order: list[Coord],
    order_start: int,
    grid_x: int,
    grid_y: int,
) -> Coord:
    stage = flow.stages[stage_index]
    if stage.core is not None:
        if not _is_coord_inside(stage.core, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(
                f"flow {flow.flow_id} stage {stage_index} core {stage.core} out of grid bounds"
            )
        return stage.core

    if not order:
        raise ValueError("grid has no coordinates")

    slot = (order_start + stage_index) % len(order)
    return order[slot]


def _resolve_fallback_core(
    *,
    flow: FlowSpec,
    stage_index: int,
    primary: Coord,
    core_load: dict[Coord, int],
    grid_x: int,
    grid_y: int,
    radius: int,
    allow_adaptive: bool,
) -> Coord | None:
    stage = flow.stages[stage_index]
    if stage.fallback_core is not None:
        if not _is_coord_inside(stage.fallback_core, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(
                f"flow {flow.flow_id} stage {stage_index} fallback_core {stage.fallback_core} out of grid bounds"
            )
        if stage.fallback_core == primary:
            return None
        return stage.fallback_core

    if not allow_adaptive:
        return None

    best: Coord | None = None
    best_load = 1_000_000
    for cand in _coords_in_radius(primary, radius, grid_x=grid_x, grid_y=grid_y):
        if cand == primary:
            continue
        load = core_load.get(cand, 0)
        if load < best_load:
            best_load = load
            best = cand
    return best


def compile_project(config: ProjectConfig) -> CompiledProject:
    grid_x = config.device.grid_x
    grid_y = config.device.grid_y

    op_table = {op.name: op for op in config.operations}
    order = _all_coords(grid_x, grid_y)
    core_load: dict[Coord, int] = {coord: 0 for coord in order}

    compiled_flows: list[CompiledFlow] = []
    sorted_flows = sorted(config.flows, key=lambda flow: flow.flow_id)

    for flow_slot, flow in enumerate(sorted_flows):
        if not _is_coord_inside(flow.entry, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(f"flow {flow.flow_id} entry {flow.entry} out of bounds")
        if flow.exit and not _is_coord_inside(flow.exit, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(f"flow {flow.flow_id} exit {flow.exit} out of bounds")

        order_start = _nearest_order_index(order, flow.entry)
        compiled_stages: list[CompiledStage] = []

        for stage_index, stage in enumerate(flow.stages):
            op = op_table[stage.op]
            primary = _resolve_primary_core(
                flow=flow,
                stage_index=stage_index,
                order=order,
                order_start=order_start,
                grid_x=grid_x,
                grid_y=grid_y,
            )
            fallback = _resolve_fallback_core(
                flow=flow,
                stage_index=stage_index,
                primary=primary,
                core_load=core_load,
                grid_x=grid_x,
                grid_y=grid_y,
                radius=config.compiler.fallback_radius,
                allow_adaptive=config.compiler.allow_adaptive_reroute and stage.allow_adaptive,
            )
            core_load[primary] = core_load.get(primary, 0) + 1
            if fallback:
                core_load[fallback] = core_load.get(fallback, 0) + 1

            compiled_stages.append(
                CompiledStage(
                    stage_index=stage_index,
                    op_name=op.name,
                    opcode=op.opcode,
                    latency=op.latency,
                    pipelined=op.pipelined,
                    primary_core=primary,
                    fallback_core=fallback,
                    immediate_b=stage.immediate_b,
                    allow_adaptive=stage.allow_adaptive,
                )
            )

        compiled_flows.append(
            CompiledFlow(
                flow_id=flow.flow_id,
                flow_slot=flow_slot,
                name=flow.name,
                entry=flow.entry,
                exit=flow.exit,
                stages=tuple(compiled_stages),
            )
        )

    return CompiledProject(config=config, flows=tuple(compiled_flows), core_load=core_load)
