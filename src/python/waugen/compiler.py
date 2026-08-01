# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

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
    dtype: str | None


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
    dtype: str | None
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


def _coord_key(coord: Coord) -> tuple[int, int, int]:
    return (coord.x, coord.y, coord.z)


def _coord_distance(a: Coord, b: Coord) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y) + abs(a.z - b.z)


def _all_coords(grid_x: int, grid_y: int, grid_z: int, *, routing: str) -> list[Coord]:
    ordered: list[Coord] = []
    serpentine = routing == "serpentine"
    for z in range(grid_z):
        for y in range(grid_y):
            if serpentine and ((z * grid_y + y) % 2 == 1):
                xs = range(grid_x - 1, -1, -1)
            else:
                xs = range(grid_x)
            for x in xs:
                ordered.append(Coord(x=x, y=y, z=z))
    return ordered


def _nearest_order_index(coords: list[Coord], target: Coord) -> int:
    best_idx = 0
    best_distance = 1_000_000
    for idx, coord in enumerate(coords):
        d = _coord_distance(coord, target)
        if d < best_distance:
            best_distance = d
            best_idx = idx
    return best_idx


def _is_coord_inside(coord: Coord, *, grid_x: int, grid_y: int, grid_z: int) -> bool:
    return (
        0 <= coord.x < grid_x
        and 0 <= coord.y < grid_y
        and 0 <= coord.z < grid_z
    )


def _coords_in_radius(
    center: Coord, radius: int, *, grid_x: int, grid_y: int, grid_z: int
) -> Iterable[Coord]:
    if radius <= 0:
        return []
    found: list[Coord] = []
    for dz in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if abs(dx) + abs(dy) + abs(dz) > radius:
                    continue
                cand = Coord(center.x + dx, center.y + dy, center.z + dz)
                if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z):
                    found.append(cand)
    found.sort(key=lambda c: (_coord_distance(c, center), c.z, c.y, c.x))
    return found


def _unique_coords(coords: Iterable[Coord]) -> tuple[Coord, ...]:
    seen: set[tuple[int, int, int]] = set()
    ordered: list[Coord] = []
    for coord in coords:
        key = _coord_key(coord)
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
                dtype=stage.dtype,
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
    grid_z: int,
    flow_id: int,
    op_name: str,
    dtype: str | None,
    op_caps: dict[tuple[int, int, int], set[str]],
    dtype_caps: dict[tuple[int, int, int], set[str]],
) -> Coord:
    def supports(coord: Coord) -> bool:
        key = _coord_key(coord)
        allowed_ops = op_caps.get(key)
        if allowed_ops is not None and op_name not in allowed_ops:
            return False
        allowed_dtypes = dtype_caps.get(key)
        if dtype is not None and allowed_dtypes is not None and dtype not in allowed_dtypes:
            return False
        return True

    if node.placement.core is not None:
        if not _is_coord_inside(
            node.placement.core, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z
        ):
            raise ValueError(
                f"flow {flow_id} node {node.node_id} core {node.placement.core} out of grid bounds"
            )
        if not supports(node.placement.core):
            raise ValueError(
                f"flow {flow_id} node {node.node_id} core "
                f"({node.placement.core.x},{node.placement.core.y},{node.placement.core.z}) "
                f"does not support op '{op_name}' dtype '{dtype or 'default'}'"
            )
        return node.placement.core

    if node.placement.candidate_cores:
        valid_candidates = [
            cand
            for cand in node.placement.candidate_cores
            if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z)
            and supports(cand)
        ]
        if not valid_candidates:
            raise ValueError(
                f"flow {flow_id} node {node.node_id} has no in-bounds compatible candidate cores"
            )

        if node.placement.directive == "prefer_locality":
            primary = min(
                valid_candidates,
                key=lambda cand: (
                    _coord_distance(cand, order[order_start]),
                    core_load.get(cand, 0),
                    cand.z,
                    cand.y,
                    cand.x,
                ),
            )
        else:
            primary = min(
                valid_candidates,
                key=lambda cand: (core_load.get(cand, 0), cand.z, cand.y, cand.x),
            )
        return primary

    if not order:
        raise ValueError("grid has no coordinates")

    slot = (order_start + node_slot) % len(order)
    for offset in range(len(order)):
        cand = order[(slot + offset) % len(order)]
        if supports(cand):
            return cand

    raise ValueError(
        f"flow {flow_id} node {node.node_id} has no compatible core for op '{op_name}' dtype '{dtype or 'default'}'"
    )


def _resolve_candidates(
    *,
    node: FlowNodeSpec,
    primary: Coord,
    core_load: dict[Coord, int],
    grid_x: int,
    grid_y: int,
    grid_z: int,
    fallback_radius: int,
    allow_adaptive_reroute: bool,
    op_name: str,
    dtype: str | None,
    op_caps: dict[tuple[int, int, int], set[str]],
    dtype_caps: dict[tuple[int, int, int], set[str]],
    preserve_legacy_order: bool,
) -> tuple[tuple[Coord, ...], Coord | None]:
    def supports(coord: Coord) -> bool:
        key = _coord_key(coord)
        allowed_ops = op_caps.get(key)
        if allowed_ops is not None and op_name not in allowed_ops:
            return False
        allowed_dtypes = dtype_caps.get(key)
        if dtype is not None and allowed_dtypes is not None and dtype not in allowed_dtypes:
            return False
        return True

    if node.placement.fixed:
        return (primary,), None

    base_candidates: list[Coord] = [primary]
    for cand in node.placement.candidate_cores:
        if _is_coord_inside(cand, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z) and supports(cand):
            base_candidates.append(cand)

    if node.placement.fallback_core is not None:
        if not _is_coord_inside(
            node.placement.fallback_core, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z
        ):
            raise ValueError(
                f"node {node.node_id} fallback_core {node.placement.fallback_core} out of bounds"
            )
        if not supports(node.placement.fallback_core):
            raise ValueError(
                f"node {node.node_id} fallback_core "
                f"({node.placement.fallback_core.x},{node.placement.fallback_core.y},{node.placement.fallback_core.z}) "
                f"does not support op '{op_name}' dtype '{dtype or 'default'}'"
            )
        base_candidates.append(node.placement.fallback_core)

    if node.placement.directive == "prefer_balance" and len(base_candidates) == 1:
        around = list(
            _coords_in_radius(
                primary, max(1, fallback_radius), grid_x=grid_x, grid_y=grid_y, grid_z=grid_z
            )
        )
        around = [cand for cand in around if supports(cand)]
        around.sort(key=lambda c: (core_load.get(c, 0), _coord_distance(c, primary), c.z, c.y, c.x))
        for cand in around[:3]:
            base_candidates.append(cand)

    if allow_adaptive_reroute and node.allow_adaptive and len(base_candidates) == 1:
        best: Coord | None = None
        best_load = 1_000_000
        for cand in _coords_in_radius(
            primary, fallback_radius, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z
        ):
            if cand == primary:
                continue
            if not supports(cand):
                continue
            load = core_load.get(cand, 0)
            if load < best_load:
                best_load = load
                best = cand
        if best is not None:
            base_candidates.append(best)

    candidates = _unique_coords(base_candidates)
    if (
        not preserve_legacy_order
        and node.placement.directive == "prefer_balance"
        and len(candidates) > 1
    ):
        # Runtime auto-adapt can see only one fallback, so make that choice the
        # deterministic least-loaded alternative instead of inheriting an
        # incidental candidate-list order from a frontend. Keep every candidate
        # in the compiled report for analysis, with primary fixed at position 0.
        alternatives = sorted(
            (cand for cand in candidates if cand != primary),
            key=lambda cand: (
                core_load.get(cand, 0),
                _coord_distance(cand, primary),
                cand.z,
                cand.y,
                cand.x,
            ),
        )
        candidates = (primary, *alternatives)
    fallback = next((cand for cand in candidates if cand != primary), None)
    return candidates, fallback


def compile_project(
    config: ProjectConfig, *, _preserve_arch_search_v1: bool = False
) -> CompiledProject:
    grid_x = config.device.grid_x
    grid_y = config.device.grid_y
    grid_z = config.device.grid_z

    op_table = {op.name: op for op in config.operations}
    order = _all_coords(grid_x, grid_y, grid_z, routing=config.compiler.routing)
    core_load: dict[Coord, int] = {coord: 0 for coord in order}
    op_caps: dict[tuple[int, int, int], set[str]] = {}
    dtype_caps: dict[tuple[int, int, int], set[str]] = {}
    for capability in config.compiler.core_capabilities:
        key = _coord_key(capability.core)
        if capability.operations:
            op_caps[key] = set(capability.operations)
        if capability.data_types:
            dtype_caps[key] = set(capability.data_types)

    compiled_flows: list[CompiledFlow] = []
    sorted_flows = sorted(config.flows, key=lambda flow: flow.flow_id)

    for flow_slot, flow in enumerate(sorted_flows):
        if not _is_coord_inside(flow.entry, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z):
            raise ValueError(f"flow {flow.flow_id} entry {flow.entry} out of bounds")
        if flow.exit and not _is_coord_inside(
            flow.exit, grid_x=grid_x, grid_y=grid_y, grid_z=grid_z
        ):
            raise ValueError(f"flow {flow.flow_id} exit {flow.exit} out of bounds")

        raw_nodes = flow.nodes if flow.nodes else _flow_nodes_from_stages(flow)
        order_start = _nearest_order_index(order, flow.entry)

        if config.compiler.routing == "manual":
            for node in raw_nodes:
                if node.placement.core is None and not node.placement.candidate_cores:
                    raise ValueError(
                        f"flow {flow.flow_id} node {node.node_id} requires explicit core or candidate_cores "
                        "when compiler.routing=manual"
                    )

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
                grid_z=grid_z,
                flow_id=flow.flow_id,
                op_name=node.op,
                dtype=node.dtype,
                op_caps=op_caps,
                dtype_caps=dtype_caps,
            )

            candidates, fallback = _resolve_candidates(
                node=node,
                primary=primary,
                core_load=core_load,
                grid_x=grid_x,
                grid_y=grid_y,
                grid_z=grid_z,
                fallback_radius=config.compiler.fallback_radius,
                allow_adaptive_reroute=config.compiler.allow_adaptive_reroute,
                op_name=node.op,
                dtype=node.dtype,
                op_caps=op_caps,
                dtype_caps=dtype_caps,
                # `arch-search` is a frozen report schema/ranking. Its private
                # compatibility path retains the candidate order from v1;
                # normal compilation promotes the best runtime fallback.
                preserve_legacy_order=_preserve_arch_search_v1,
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
                    dtype=node.dtype,
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
                    dtype=node.dtype,
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


def _core_index_for(coord: Coord, *, grid_x: int, grid_y: int) -> int:
    """Mirrors `scheduler.core_index`'s formula exactly. Duplicated (rather
    than imported) because `scheduler.py` imports from `compiler.py`, and this
    module must not import back from it."""
    return (coord.z * grid_x * grid_y) + (coord.y * grid_x) + coord.x


@dataclass(frozen=True)
class FastPathHop:
    """One per-core fast-path table entry: what a core should do with a
    completed (flow_id, stage_index) result, if that stage is not the flow's
    last. `concurrence_id` is the entry's ordinal position within its core's
    table (assigned deterministically in `build_fast_path_tables`) -- it
    exists to bound and report per-core table capacity; the RTL lookup itself
    is keyed by (flow_id, stage_index), not by this id."""

    concurrence_id: int
    next_stage_index: int
    next_opcode: int
    next_use_immediate: bool
    next_immediate_b: int | None
    next_dst_primary: int
    next_dst_fallback: int  # == next_dst_primary when no real alternate is offered


@dataclass(frozen=True)
class CoreFastPathTable:
    core_index: int
    hops: dict[tuple[int, int], FastPathHop]
    overflowed_keys: tuple[tuple[int, int], ...]


def build_fast_path_tables(
    project: CompiledProject,
    *,
    table_bits: int,
    excluded_destinations: frozenset[int] = frozenset(),
) -> tuple[CoreFastPathTable, ...]:
    """Derive each core's static fast-path dispatch table from the already-
    compiled placement -- never from `SchedulePlan`. The dynamic coordinator,
    this table, and the scheduler all restrict execution to a stage's
    `primary_core` or `fallback_core`; any additional candidates are retained
    only as compiler analysis inputs.

    A flow's last stage never gets an entry: that structurally guarantees
    every flow chain still reaches the coordinator's hub eventually, however
    long a fast-path run in between, which is what keeps flow completion
    detection correct. This is a read-only analysis pass -- it does not
    change any placement decision `compile_project` already made.

    `excluded_destinations` lets the emitter (verilog_emit.py) veto specific
    physical core indices as fast-path targets. This exists for the `chain`/
    `matrix` highway topologies, where the coordinator shares one physical
    core's own local data-plane port (`COORDINATOR_CORE_INDEX`, always core
    0) rather than owning a dedicated one (unlike `lines`, where every core's
    local port is exclusively its own). A fast-path packet routed to that
    shared port would be misread as a coordinator-bound legacy result instead
    of reaching that core's own station. When a hop's primary destination is
    excluded, the whole entry is skipped -- that stage transition simply keeps
    using the always-correct legacy coordinator path, exactly like a
    capacity overflow. When only the fallback candidate is excluded, the hop
    still gets an entry with just its primary destination."""
    grid_x = project.config.device.grid_x
    grid_y = project.config.device.grid_y
    core_count = grid_x * grid_y * project.config.device.grid_z
    allow_adaptive_reroute = project.config.compiler.allow_adaptive_reroute

    capacity = 1 << table_bits
    # pending[core_index][(flow_id, stage_index)] = FastPathHop stub (no concurrence_id yet)
    pending: dict[int, dict[tuple[int, int], FastPathHop]] = {
        idx: {} for idx in range(core_count)
    }

    for flow in project.flows:
        stages = flow.stages
        for stage_index in range(len(stages) - 1):
            stage = stages[stage_index]
            next_stage = stages[stage_index + 1]

            producer_indices = {
                _core_index_for(stage.primary_core, grid_x=grid_x, grid_y=grid_y)
            }
            if stage.fallback_core is not None:
                producer_indices.add(
                    _core_index_for(stage.fallback_core, grid_x=grid_x, grid_y=grid_y)
                )

            next_dst_primary = _core_index_for(
                next_stage.primary_core, grid_x=grid_x, grid_y=grid_y
            )
            if next_dst_primary in excluded_destinations:
                # This stage transition can never be a fast-path hop (its
                # destination shares a port the coordinator itself owns);
                # leave it out entirely so the legacy path handles it.
                continue

            next_dst_fallback = next_dst_primary
            if (
                next_stage.fallback_core is not None
                and next_stage.allow_adaptive
                and allow_adaptive_reroute
            ):
                candidate = _core_index_for(
                    next_stage.fallback_core, grid_x=grid_x, grid_y=grid_y
                )
                if candidate != next_dst_primary and candidate not in excluded_destinations:
                    next_dst_fallback = candidate

            hop_stub = FastPathHop(
                concurrence_id=-1,  # assigned per-core below
                next_stage_index=next_stage.stage_index,
                next_opcode=next_stage.opcode,
                next_use_immediate=next_stage.immediate_b is not None,
                next_immediate_b=next_stage.immediate_b,
                next_dst_primary=next_dst_primary,
                next_dst_fallback=next_dst_fallback,
            )
            key = (flow.flow_id, stage_index)
            for producer in producer_indices:
                pending[producer][key] = hop_stub

    tables: list[CoreFastPathTable] = []
    for idx in range(core_count):
        ordered_keys = sorted(pending[idx].keys())
        hops: dict[tuple[int, int], FastPathHop] = {}
        overflowed: list[tuple[int, int]] = []
        for concurrence_id, key in enumerate(ordered_keys):
            stub = pending[idx][key]
            if concurrence_id >= capacity:
                overflowed.append(key)
                continue
            hops[key] = FastPathHop(
                concurrence_id=concurrence_id,
                next_stage_index=stub.next_stage_index,
                next_opcode=stub.next_opcode,
                next_use_immediate=stub.next_use_immediate,
                next_immediate_b=stub.next_immediate_b,
                next_dst_primary=stub.next_dst_primary,
                next_dst_fallback=stub.next_dst_fallback,
            )
        tables.append(
            CoreFastPathTable(
                core_index=idx, hops=hops, overflowed_keys=tuple(overflowed)
            )
        )

    return tuple(tables)
