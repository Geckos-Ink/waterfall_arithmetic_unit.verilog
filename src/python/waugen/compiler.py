from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import Coord, FlowNodeSpec, FlowSpec, PlacementSpec, ProjectConfig


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
class CompiledNode:
    node_id: str
    node_slot: int
    op_name: str
    opcode: int
    latency: int
    pipelined: bool
    deps: tuple[str, ...]
    dep_slots: tuple[int, ...]
    primary_core: Coord
    fallback_core: Coord | None
    candidate_cores: tuple[Coord, ...]
    immediate_b: int | None
    allow_adaptive: bool
    recurrent: bool
    max_iterations: int
    cycle_group: int | None


@dataclass(frozen=True)
class CompiledFlow:
    flow_id: int
    flow_slot: int
    name: str
    entry: Coord
    exit: Coord | None
    stages: tuple[CompiledStage, ...]
    nodes: tuple[CompiledNode, ...]
    linear_node_order: tuple[str, ...]


@dataclass(frozen=True)
class CompiledProject:
    config: ProjectConfig
    flows: tuple[CompiledFlow, ...]
    core_load: dict[Coord, int]

    @property
    def max_stages(self) -> int:
        return max((len(flow.stages) for flow in self.flows), default=0)


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


def _unique_coords(coords: Iterable[Coord]) -> tuple[Coord, ...]:
    seen: set[tuple[int, int]] = set()
    ordered: list[Coord] = []
    for coord in coords:
        key = (coord.x, coord.y)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(coord)
    return tuple(ordered)


def _flow_nodes_from_stages(flow: FlowSpec) -> tuple[FlowNodeSpec, ...]:
    nodes: list[FlowNodeSpec] = []
    prev_node_id: str | None = None

    for idx, stage in enumerate(flow.stages):
        node_id = f"s{idx}"
        deps = (prev_node_id,) if prev_node_id is not None else ()

        placement = PlacementSpec(
            core=stage.core,
            fallback_core=stage.fallback_core,
            candidate_cores=(),
            fixed=False,
            directive="auto",
        )

        nodes.append(
            FlowNodeSpec(
                node_id=node_id,
                op=stage.op,
                deps=deps,
                placement=placement,
                immediate_b=stage.immediate_b,
                allow_adaptive=stage.allow_adaptive,
                recurrent=False,
                max_iterations=1,
            )
        )
        prev_node_id = node_id

    return tuple(nodes)


def _tarjan_scc(node_ids: list[str], deps_map: dict[str, tuple[str, ...]]) -> list[list[str]]:
    index = 0
    index_stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlink[node] = index
        index += 1
        index_stack.append(node)
        on_stack.add(node)

        for dep in deps_map.get(node, ()):  # edges node -> dep
            if dep not in indices:
                strongconnect(dep)
                lowlink[node] = min(lowlink[node], lowlink[dep])
            elif dep in on_stack:
                lowlink[node] = min(lowlink[node], indices[dep])

        if lowlink[node] == indices[node]:
            component: list[str] = []
            while index_stack:
                w = index_stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == node:
                    break
            result.append(component)

    for node_id in node_ids:
        if node_id not in indices:
            strongconnect(node_id)

    return result


def _build_cycle_groups(
    raw_nodes: tuple[FlowNodeSpec, ...],
    *,
    allow_cycle_recurrence: bool,
    flow_id: int,
) -> tuple[dict[str, int], dict[int, int]]:
    node_ids = [node.node_id for node in raw_nodes]
    deps_map = {node.node_id: node.deps for node in raw_nodes}
    by_id = {node.node_id: node for node in raw_nodes}

    group_by_node: dict[str, int] = {}
    group_iterations: dict[int, int] = {}

    components = _tarjan_scc(node_ids, deps_map)
    group_idx = 0

    for comp in components:
        is_self_loop = len(comp) == 1 and comp[0] in deps_map.get(comp[0], ())
        if len(comp) == 1 and not is_self_loop:
            continue

        if not allow_cycle_recurrence:
            raise ValueError(
                f"flow {flow_id} has recursive cycle {sorted(comp)} but compiler.allow_cycle_recurrence is disabled"
            )

        for node_id in comp:
            if not by_id[node_id].recurrent:
                raise ValueError(
                    f"flow {flow_id} node {node_id} participates in a recursive cycle and must set recurrent=true"
                )

        max_iterations = max(by_id[node_id].max_iterations for node_id in comp)
        group_iterations[group_idx] = max_iterations

        for node_id in comp:
            group_by_node[node_id] = group_idx

        group_idx += 1

    return group_by_node, group_iterations


def _build_linear_node_order(
    raw_nodes: tuple[FlowNodeSpec, ...],
    *,
    cycle_group_by_node: dict[str, int],
) -> tuple[str, ...]:
    slot_by_id = {node.node_id: idx for idx, node in enumerate(raw_nodes)}
    in_degree: dict[str, int] = {node.node_id: 0 for node in raw_nodes}
    adjacency: dict[str, list[str]] = {node.node_id: [] for node in raw_nodes}

    for node in raw_nodes:
        node_group = cycle_group_by_node.get(node.node_id)
        for dep in node.deps:
            dep_group = cycle_group_by_node.get(dep)
            if node_group is not None and dep_group == node_group:
                continue
            in_degree[node.node_id] += 1
            adjacency[dep].append(node.node_id)

    queue = [node_id for node_id, indeg in in_degree.items() if indeg == 0]
    queue.sort(key=lambda node_id: slot_by_id[node_id])

    ordered: list[str] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for nxt in adjacency.get(current, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
                queue.sort(key=lambda node_id: slot_by_id[node_id])

    if len(ordered) != len(raw_nodes):
        remaining = [node.node_id for node in raw_nodes if node.node_id not in set(ordered)]
        remaining.sort(key=lambda node_id: slot_by_id[node_id])
        ordered.extend(remaining)

    return tuple(ordered)


def _resolve_primary_core(
    *,
    node: FlowNodeSpec,
    node_slot: int,
    order: list[Coord],
    order_start: int,
    core_load: dict[Coord, int],
    grid_x: int,
    grid_y: int,
    flow_id: int,
) -> Coord:
    if node.placement.core is not None:
        if not _is_coord_inside(node.placement.core, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(
                f"flow {flow_id} node {node.node_id} core {node.placement.core} out of grid bounds"
            )
        return node.placement.core

    if node.placement.candidate_cores:
        valid_candidates = [
            cand
            for cand in node.placement.candidate_cores
            if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y)
        ]
        if not valid_candidates:
            raise ValueError(
                f"flow {flow_id} node {node.node_id} has no in-bounds candidate cores"
            )

        if node.placement.directive == "prefer_locality":
            primary = min(
                valid_candidates,
                key=lambda cand: (
                    abs(cand.x - order[order_start].x) + abs(cand.y - order[order_start].y),
                    core_load.get(cand, 0),
                    cand.y,
                    cand.x,
                ),
            )
        else:
            primary = min(
                valid_candidates,
                key=lambda cand: (core_load.get(cand, 0), cand.y, cand.x),
            )
        return primary

    if not order:
        raise ValueError("grid has no coordinates")

    slot = (order_start + node_slot) % len(order)
    return order[slot]


def _resolve_candidates(
    *,
    node: FlowNodeSpec,
    primary: Coord,
    core_load: dict[Coord, int],
    grid_x: int,
    grid_y: int,
    fallback_radius: int,
    allow_adaptive_reroute: bool,
) -> tuple[tuple[Coord, ...], Coord | None]:
    if node.placement.fixed:
        return (primary,), None

    base_candidates: list[Coord] = [primary]
    for cand in node.placement.candidate_cores:
        if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y):
            base_candidates.append(cand)

    if node.placement.fallback_core is not None:
        if not _is_coord_inside(node.placement.fallback_core, grid_x=grid_x, grid_y=grid_y):
            raise ValueError(
                f"node {node.node_id} fallback_core {node.placement.fallback_core} out of bounds"
            )
        base_candidates.append(node.placement.fallback_core)

    if node.placement.directive == "prefer_balance" and len(base_candidates) == 1:
        around = list(_coords_in_radius(primary, max(1, fallback_radius), grid_x=grid_x, grid_y=grid_y))
        around.sort(key=lambda c: (core_load.get(c, 0), abs(c.x - primary.x) + abs(c.y - primary.y), c.y, c.x))
        for cand in around[:3]:
            base_candidates.append(cand)

    if allow_adaptive_reroute and node.allow_adaptive and len(base_candidates) == 1:
        best: Coord | None = None
        best_load = 1_000_000
        for cand in _coords_in_radius(primary, fallback_radius, grid_x=grid_x, grid_y=grid_y):
            if cand == primary:
                continue
            load = core_load.get(cand, 0)
            if load < best_load:
                best_load = load
                best = cand
        if best is not None:
            base_candidates.append(best)

    candidates = _unique_coords(base_candidates)
    fallback = next((cand for cand in candidates if cand != primary), None)
    return candidates, fallback


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

        raw_nodes = flow.nodes if flow.nodes else _flow_nodes_from_stages(flow)
        order_start = _nearest_order_index(order, flow.entry)

        node_slot_by_id = {node.node_id: idx for idx, node in enumerate(raw_nodes)}
        cycle_group_by_node, cycle_iterations = _build_cycle_groups(
            raw_nodes,
            allow_cycle_recurrence=config.compiler.allow_cycle_recurrence,
            flow_id=flow.flow_id,
        )

        compiled_nodes: list[CompiledNode] = []

        for node_slot, node in enumerate(raw_nodes):
            op = op_table[node.op]

            primary = _resolve_primary_core(
                node=node,
                node_slot=node_slot,
                order=order,
                order_start=order_start,
                core_load=core_load,
                grid_x=grid_x,
                grid_y=grid_y,
                flow_id=flow.flow_id,
            )

            candidates, fallback = _resolve_candidates(
                node=node,
                primary=primary,
                core_load=core_load,
                grid_x=grid_x,
                grid_y=grid_y,
                fallback_radius=config.compiler.fallback_radius,
                allow_adaptive_reroute=config.compiler.allow_adaptive_reroute,
            )

            core_load[primary] = core_load.get(primary, 0) + 1
            if fallback is not None:
                core_load[fallback] = core_load.get(fallback, 0) + 1

            dep_slots = tuple(node_slot_by_id[dep] for dep in node.deps)
            cycle_group = cycle_group_by_node.get(node.node_id)
            max_iterations = cycle_iterations.get(cycle_group, node.max_iterations)

            compiled_nodes.append(
                CompiledNode(
                    node_id=node.node_id,
                    node_slot=node_slot,
                    op_name=op.name,
                    opcode=op.opcode,
                    latency=op.latency,
                    pipelined=op.pipelined,
                    deps=node.deps,
                    dep_slots=dep_slots,
                    primary_core=primary,
                    fallback_core=fallback,
                    candidate_cores=candidates,
                    immediate_b=node.immediate_b,
                    allow_adaptive=node.allow_adaptive,
                    recurrent=node.recurrent,
                    max_iterations=max_iterations,
                    cycle_group=cycle_group,
                )
            )

        compiled_node_by_id = {node.node_id: node for node in compiled_nodes}
        linear_order = _build_linear_node_order(raw_nodes, cycle_group_by_node=cycle_group_by_node)

        compiled_stages: list[CompiledStage] = []
        for stage_index, node_id in enumerate(linear_order):
            node = compiled_node_by_id[node_id]
            compiled_stages.append(
                CompiledStage(
                    stage_index=stage_index,
                    op_name=node.op_name,
                    opcode=node.opcode,
                    latency=node.latency,
                    pipelined=node.pipelined,
                    primary_core=node.primary_core,
                    fallback_core=node.fallback_core,
                    immediate_b=node.immediate_b,
                    allow_adaptive=node.allow_adaptive,
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
                nodes=tuple(compiled_nodes),
                linear_node_order=linear_order,
            )
        )

    return CompiledProject(config=config, flows=tuple(compiled_flows), core_load=core_load)
