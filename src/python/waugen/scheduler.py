# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from dataclasses import dataclass

from .compiler import CompiledFlow, CompiledNode, CompiledProject


@dataclass(frozen=True)
class ScheduleInstruction:
    cycle_start: int
    cycle_end: int
    flow_slot: int
    flow_id: int
    stage_index: int
    opcode: int
    op_name: str
    core_index: int
    core_x: int
    core_y: int
    core_z: int
    latency: int
    used_fallback: bool
    immediate_b: int | None
    dtype: str | None = None
    program_id: int = 0
    program_name: str = "default"
    program_replica: int = 0
    node_id: str = ""
    iteration: int = 0
    dependency_count: int = 0
    runtime_node_key: str = ""
    data_dependency_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchedulePlan:
    instructions: tuple[ScheduleInstruction, ...]
    makespan_cycles: int

    def to_json(self) -> dict:
        dependency_metrics = _dependency_transfer_metrics(self.instructions)
        return {
            "makespan_cycles": self.makespan_cycles,
            **dependency_metrics,
            "instructions": [
                {
                    "cycle_start": ins.cycle_start,
                    "cycle_end": ins.cycle_end,
                    "program_id": ins.program_id,
                    "program_name": ins.program_name,
                    "program_replica": ins.program_replica,
                    "flow_slot": ins.flow_slot,
                    "flow_id": ins.flow_id,
                    "node_id": ins.node_id,
                    "iteration": ins.iteration,
                    "stage_index": ins.stage_index,
                    "op": ins.op_name,
                    "opcode": ins.opcode,
                    "core_index": ins.core_index,
                    "core": {"x": ins.core_x, "y": ins.core_y, "z": ins.core_z},
                    "latency": ins.latency,
                    "used_fallback": ins.used_fallback,
                    "immediate_b": ins.immediate_b,
                    "dtype": ins.dtype,
                    "dependency_count": ins.dependency_count,
                    "data_dependency_count": len(ins.data_dependency_keys),
                    "runtime_node_key": ins.runtime_node_key,
                    "data_dependency_keys": list(ins.data_dependency_keys),
                }
                for ins in self.instructions
            ],
        }

    def to_hex_lines(self) -> list[str]:
        return [f"{encode_instruction_word(ins):016X}" for ins in self.instructions]


def _dependency_transfer_metrics(
    instructions: tuple[ScheduleInstruction, ...],
) -> dict[str, str | int | float]:
    """Estimate routed movement across true runtime data-dependency edges."""
    by_runtime_key = {
        ins.runtime_node_key: ins for ins in instructions if ins.runtime_node_key
    }
    total_hops = 0
    edge_count = 0
    unresolved_edges = 0

    for ins in instructions:
        for dep_key in ins.data_dependency_keys:
            dep = by_runtime_key.get(dep_key)
            if dep is None:
                unresolved_edges += 1
                continue
            total_hops += (
                abs(ins.core_x - dep.core_x)
                + abs(ins.core_y - dep.core_y)
                + abs(ins.core_z - dep.core_z)
            )
            edge_count += 1

    average_hops = (total_hops / edge_count) if edge_count else 0.0
    return {
        "estimated_transfer_hops_metric": "dependency_edges_v1",
        "estimated_transfer_hops_total": total_hops,
        "estimated_transfer_hops_edge_count": edge_count,
        "estimated_transfer_hops_avg_edge": average_hops,
        "estimated_transfer_hops_unresolved_edges": unresolved_edges,
    }


def core_index(x: int, y: int, grid_x: int, z: int = 0, grid_y: int = 1) -> int:
    return (z * grid_x * grid_y) + (y * grid_x) + x


def core_coords(index: int, *, grid_x: int, grid_y: int) -> tuple[int, int, int]:
    layer_size = max(1, grid_x * grid_y)
    z = index // layer_size
    rem = index % layer_size
    return rem % grid_x, rem // grid_x, z


@dataclass
class _RuntimeNode:
    key: str
    program_id: int
    program_name: str
    program_replica: int
    program_priority: int
    program_allow_out_of_order: bool
    load_balance: str
    flow_slot: int
    flow_id: int
    node_id: str
    node_slot: int
    opcode: int
    op_name: str
    latency: int
    pipelined: bool
    immediate_b: int | None
    dtype: str | None
    primary_core_idx: int
    candidate_core_indices: tuple[int, ...]
    allow_adaptive: bool
    iteration: int
    dep_keys: list[str]
    data_dep_keys: list[str]


@dataclass(frozen=True)
class _ProgramMeta:
    name: str
    priority: int


def _core_release_cycle(*, pipelined: bool, latency: int, start_cycle: int) -> int:
    if pipelined:
        return start_cycle + 1
    return start_cycle + latency


def _resolve_dep_iteration(current: CompiledNode, dep: CompiledNode, iteration: int) -> int | None:
    if not dep.recurrent:
        return 0

    if not current.recurrent:
        return dep.max_iterations - 1

    if current.cycle_group is not None and dep.cycle_group == current.cycle_group:
        if dep.node_slot < current.node_slot:
            return min(iteration, dep.max_iterations - 1)
        prev = iteration - 1
        if prev < 0:
            return None
        return min(prev, dep.max_iterations - 1)

    return min(iteration, dep.max_iterations - 1)


def _build_runtime_nodes(project: CompiledProject) -> tuple[dict[str, _RuntimeNode], dict[str, list[str]], dict[str, int], dict[int, _ProgramMeta]]:
    grid_x = project.config.device.grid_x
    grid_y = project.config.device.grid_y

    flow_by_id: dict[int, CompiledFlow] = {flow.flow_id: flow for flow in project.flows}
    runtime_nodes: dict[str, _RuntimeNode] = {}
    dependents: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    program_meta: dict[int, _ProgramMeta] = {}

    for program in sorted(project.config.programs, key=lambda p: p.program_id):
        program_meta[program.program_id] = _ProgramMeta(name=program.name, priority=program.priority)

        flow_instances: list[tuple[int, int]] = []
        for replica in range(program.replicas):
            for flow_id in program.flow_ids:
                flow_instances.append((replica, flow_id))

        if len(flow_instances) > program.max_parallel_flows:
            flow_instances = flow_instances[: program.max_parallel_flows]

        for replica, flow_id in flow_instances:
            flow = flow_by_id[flow_id]
            by_id = {node.node_id: node for node in flow.nodes}
            key_of: dict[tuple[str, int], str] = {}
            node_order = sorted(flow.nodes, key=lambda node: node.node_slot)

            for node in node_order:
                iterations = node.max_iterations if node.recurrent else 1
                for iteration in range(iterations):
                    key = (
                        f"p{program.program_id}_r{replica}_f{flow.flow_slot}_"
                        f"n{node.node_id}_i{iteration}"
                    )
                    key_of[(node.node_id, iteration)] = key

                    candidate_core_indices = tuple(
                        core_index(coord.x, coord.y, grid_x, coord.z, grid_y)
                        for coord in node.candidate_cores
                    )
                    if not candidate_core_indices:
                        candidate_core_indices = (
                            core_index(
                                node.primary_core.x,
                                node.primary_core.y,
                                grid_x,
                                node.primary_core.z,
                                grid_y,
                            ),
                        )

                    primary_idx = core_index(
                        node.primary_core.x,
                        node.primary_core.y,
                        grid_x,
                        node.primary_core.z,
                        grid_y,
                    )
                    if primary_idx not in candidate_core_indices:
                        candidate_core_indices = (primary_idx,) + candidate_core_indices

                    runtime_nodes[key] = _RuntimeNode(
                        key=key,
                        program_id=program.program_id,
                        program_name=program.name,
                        program_replica=replica,
                        program_priority=program.priority,
                        program_allow_out_of_order=program.allow_out_of_order,
                        load_balance=program.load_balance,
                        flow_slot=flow.flow_slot,
                        flow_id=flow.flow_id,
                        node_id=node.node_id,
                        node_slot=node.node_slot,
                        opcode=node.opcode,
                        op_name=node.op_name,
                        latency=node.latency,
                        pipelined=node.pipelined,
                        immediate_b=node.immediate_b,
                        dtype=node.dtype,
                        primary_core_idx=primary_idx,
                        candidate_core_indices=candidate_core_indices,
                        allow_adaptive=node.allow_adaptive,
                        iteration=iteration,
                        dep_keys=[],
                        data_dep_keys=[],
                    )

            for node in node_order:
                iterations = node.max_iterations if node.recurrent else 1
                for iteration in range(iterations):
                    current_key = key_of[(node.node_id, iteration)]
                    dep_keys: list[str] = []

                    for dep_id in node.deps:
                        dep_node = by_id[dep_id]
                        dep_iteration = _resolve_dep_iteration(node, dep_node, iteration)
                        if dep_iteration is None:
                            continue
                        dep_iteration = max(0, min(dep_iteration, (dep_node.max_iterations if dep_node.recurrent else 1) - 1))
                        dep_key = key_of.get((dep_id, dep_iteration))
                        if dep_key is not None:
                            dep_keys.append(dep_key)

                    if node.recurrent and iteration > 0 and node.node_id not in node.deps:
                        prev_key = key_of.get((node.node_id, iteration - 1))
                        if prev_key is not None:
                            dep_keys.append(prev_key)

                    dedup: list[str] = []
                    seen: set[str] = set()
                    for dep_key in dep_keys:
                        if dep_key in seen:
                            continue
                        seen.add(dep_key)
                        dedup.append(dep_key)

                    runtime_nodes[current_key].dep_keys = dedup
                    runtime_nodes[current_key].data_dep_keys = list(dedup)

            if not program.allow_async or not program.allow_out_of_order:
                serial_keys: list[str] = []
                for node in sorted(node_order, key=lambda n: (n.node_slot, n.node_id)):
                    iterations = node.max_iterations if node.recurrent else 1
                    for iteration in range(iterations):
                        serial_keys.append(key_of[(node.node_id, iteration)])
                for prev_key, next_key in zip(serial_keys, serial_keys[1:]):
                    if prev_key not in runtime_nodes[next_key].dep_keys:
                        runtime_nodes[next_key].dep_keys.append(prev_key)

    for key, node in runtime_nodes.items():
        indegree[key] = len(node.dep_keys)
        for dep_key in node.dep_keys:
            dependents.setdefault(dep_key, []).append(key)
        dependents.setdefault(key, [])

    return runtime_nodes, dependents, indegree, program_meta


def _select_program(
    *,
    ready_programs: set[int],
    policy: str,
    program_issue_count: dict[int, int],
    program_meta: dict[int, _ProgramMeta],
    round_robin_state: dict[str, int],
) -> int:
    if policy == "strict_priority":
        return max(ready_programs, key=lambda pid: (program_meta[pid].priority, -pid))

    if policy == "round_robin":
        ordered = sorted(ready_programs)
        cursor = round_robin_state.get("program", 0)
        pick = ordered[cursor % len(ordered)]
        round_robin_state["program"] = cursor + 1
        return pick

    # weighted_fair
    def score(pid: int) -> tuple[float, int, int]:
        issued = program_issue_count.get(pid, 0)
        priority = max(1, program_meta[pid].priority)
        return (issued / priority, -priority, pid)

    return min(ready_programs, key=score)


def _locality_cost(
    candidate: int,
    *,
    dep_core_indices: tuple[int, ...],
    grid_x: int,
    grid_y: int,
    locality_bias: float,
) -> int:
    """Routing cost proxy: summed Manhattan hops from the candidate core to the
    cores holding this node's dependency results. Used as a tiebreaker so that,
    among cores that become free at the same cycle, the scheduler keeps data
    movement local and shrinks `estimated_transfer_hops_total`. Returns 0 when
    locality weighting is disabled or the node has no placed dependencies."""
    if locality_bias <= 0.0 or not dep_core_indices:
        return 0
    cx, cy, cz = core_coords(candidate, grid_x=grid_x, grid_y=grid_y)
    total = 0
    for dep in dep_core_indices:
        dx, dy, dz = core_coords(dep, grid_x=grid_x, grid_y=grid_y)
        total += abs(cx - dx) + abs(cy - dy) + abs(cz - dz)
    return int(round(total * locality_bias))


def _select_core(
    *,
    node: _RuntimeNode,
    ready_time: int,
    core_ready: dict[int, int],
    core_issue_count: dict[int, int],
    allow_adaptive_reroute: bool,
    round_robin_state: dict[str, int],
    dep_core_indices: tuple[int, ...],
    grid_x: int,
    grid_y: int,
    locality_bias: float,
) -> tuple[int, int, bool]:
    candidates = list(node.candidate_core_indices)
    if not (allow_adaptive_reroute and node.allow_adaptive):
        candidates = [node.primary_core_idx]

    if not candidates:
        candidates = [node.primary_core_idx]

    candidates = sorted(set(candidates))

    def locality(c: int) -> int:
        return _locality_cost(
            c,
            dep_core_indices=dep_core_indices,
            grid_x=grid_x,
            grid_y=grid_y,
            locality_bias=locality_bias,
        )

    if node.load_balance == "least_busy":
        # Earliest free cycle stays the primary key (so locality never trades
        # away latency); proximity to the producing cores breaks ties before
        # raw issue count.
        chosen = min(
            candidates,
            key=lambda c: (
                max(ready_time, core_ready.get(c, 0)),
                locality(c),
                core_issue_count.get(c, 0),
                c,
            ),
        )
        start = max(ready_time, core_ready.get(chosen, 0))
        return chosen, start, chosen != node.primary_core_idx

    cursor_key = f"core_rr_{node.program_id}"
    cursor = round_robin_state.get(cursor_key, 0)
    offset = cursor % len(candidates)
    rotated = candidates[offset:] + candidates[:offset]

    # Round-robin fairness drives the base order; among equally-early cores
    # prefer the one closest to the data so the rotation still favours locality.
    chosen = rotated[0]
    chosen_key = (max(ready_time, core_ready.get(chosen, 0)), locality(chosen), 0)
    for pos, cand in enumerate(rotated[1:], start=1):
        cand_key = (max(ready_time, core_ready.get(cand, 0)), locality(cand), pos)
        if cand_key < chosen_key:
            chosen = cand
            chosen_key = cand_key

    chosen_start = max(ready_time, core_ready.get(chosen, 0))
    round_robin_state[cursor_key] = cursor + 1
    return chosen, chosen_start, chosen != node.primary_core_idx


def build_schedule(project: CompiledProject) -> SchedulePlan:
    strategy = project.config.scheduler.strategy
    allow_adapt = project.config.compiler.allow_adaptive_reroute
    policy = project.config.scheduler.program_policy
    locality_bias = project.config.scheduler.locality_bias
    grid_x = project.config.device.grid_x
    grid_y = project.config.device.grid_y

    runtime_nodes, dependents, indegree, program_meta = _build_runtime_nodes(project)

    unscheduled: set[str] = set(runtime_nodes)
    ready_keys: set[str] = {key for key, indeg in indegree.items() if indeg == 0}
    dep_end_cycle: dict[str, int] = {}
    placed_core: dict[str, int] = {}

    core_ready: dict[int, int] = {}
    core_issue_count: dict[int, int] = {}
    program_issue_count: dict[int, int] = {}
    round_robin_state: dict[str, int] = {}

    instructions: list[ScheduleInstruction] = []

    while unscheduled:
        available = [key for key in ready_keys if key in unscheduled]
        if not available:
            unresolved = sorted(unscheduled)
            raise ValueError(
                "Unable to build schedule due to unresolved dependency cycle in runtime graph. "
                f"Unscheduled nodes: {unresolved[:8]}"
            )

        if strategy == "serial":
            selected_key = min(
                available,
                key=lambda k: (
                    runtime_nodes[k].program_id,
                    runtime_nodes[k].program_replica,
                    runtime_nodes[k].flow_slot,
                    runtime_nodes[k].iteration,
                    runtime_nodes[k].node_slot,
                    runtime_nodes[k].node_id,
                    k,
                ),
            )
        else:
            ready_programs = {runtime_nodes[key].program_id for key in available}
            selected_program = _select_program(
                ready_programs=ready_programs,
                policy=policy,
                program_issue_count=program_issue_count,
                program_meta=program_meta,
                round_robin_state=round_robin_state,
            )

            program_keys = [key for key in available if runtime_nodes[key].program_id == selected_program]

            def ready_time(node_key: str) -> int:
                deps = runtime_nodes[node_key].dep_keys
                if not deps:
                    return 0
                return max(dep_end_cycle.get(dep_key, 0) for dep_key in deps)

            if runtime_nodes[program_keys[0]].program_allow_out_of_order:
                selected_key = min(
                    program_keys,
                    key=lambda k: (
                        ready_time(k),
                        runtime_nodes[k].program_replica,
                        runtime_nodes[k].flow_slot,
                        runtime_nodes[k].iteration,
                        runtime_nodes[k].node_slot,
                        runtime_nodes[k].node_id,
                        k,
                    ),
                )
            else:
                selected_key = min(
                    program_keys,
                    key=lambda k: (
                        runtime_nodes[k].program_replica,
                        runtime_nodes[k].flow_slot,
                        runtime_nodes[k].iteration,
                        runtime_nodes[k].node_slot,
                        ready_time(k),
                        runtime_nodes[k].node_id,
                        k,
                    ),
                )

        node = runtime_nodes[selected_key]
        deps = node.dep_keys
        ready_time = max((dep_end_cycle.get(dep_key, 0) for dep_key in deps), default=0)

        dep_core_indices = tuple(
            placed_core[dep_key]
            for dep_key in node.data_dep_keys
            if dep_key in placed_core
        )

        chosen_core, start, used_fallback = _select_core(
            node=node,
            ready_time=ready_time,
            core_ready=core_ready,
            core_issue_count=core_issue_count,
            allow_adaptive_reroute=allow_adapt,
            round_robin_state=round_robin_state,
            dep_core_indices=dep_core_indices,
            grid_x=grid_x,
            grid_y=grid_y,
            locality_bias=locality_bias,
        )
        placed_core[selected_key] = chosen_core

        end = start + node.latency
        release = _core_release_cycle(pipelined=node.pipelined, latency=node.latency, start_cycle=start)
        core_ready[chosen_core] = release
        core_issue_count[chosen_core] = core_issue_count.get(chosen_core, 0) + 1
        program_issue_count[node.program_id] = program_issue_count.get(node.program_id, 0) + 1

        dep_end_cycle[selected_key] = end

        core_x, core_y, core_z = core_coords(chosen_core, grid_x=grid_x, grid_y=grid_y)

        instructions.append(
            ScheduleInstruction(
                cycle_start=start,
                cycle_end=end,
                flow_slot=node.flow_slot,
                flow_id=node.flow_id,
                stage_index=node.node_slot,
                opcode=node.opcode,
                op_name=node.op_name,
                core_index=chosen_core,
                core_x=core_x,
                core_y=core_y,
                core_z=core_z,
                latency=node.latency,
                used_fallback=used_fallback,
                immediate_b=node.immediate_b,
                dtype=node.dtype,
                program_id=node.program_id,
                program_name=node.program_name,
                program_replica=node.program_replica,
                node_id=node.node_id,
                iteration=node.iteration,
                dependency_count=len(node.dep_keys),
                runtime_node_key=node.key,
                data_dependency_keys=tuple(node.data_dep_keys),
            )
        )

        unscheduled.remove(selected_key)
        ready_keys.discard(selected_key)

        for dep_key in dependents.get(selected_key, []):
            indegree[dep_key] -= 1
            if indegree[dep_key] == 0:
                ready_keys.add(dep_key)

    instructions.sort(
        key=lambda ins: (
            ins.cycle_start,
            ins.program_id,
            ins.program_replica,
            ins.flow_slot,
            ins.iteration,
            ins.stage_index,
        )
    )
    makespan = max((ins.cycle_end for ins in instructions), default=0)
    return SchedulePlan(instructions=tuple(instructions), makespan_cycles=makespan)


def encode_instruction_word(instruction: ScheduleInstruction) -> int:
    flags = 0
    if instruction.used_fallback:
        flags |= 0x01
    if instruction.immediate_b is not None:
        flags |= 0x02

    # 64-bit encoded word (legacy-compatible payload):
    # [63:56] opcode
    # [55:40] flow_id
    # [39:32] stage_index
    # [31:24] core_index
    # [23:16] latency
    # [15:8] flags
    # [7:0] immediate_b (truncated signed)
    imm = 0
    if instruction.immediate_b is not None:
        imm = instruction.immediate_b & 0xFF

    word = 0
    word |= (instruction.opcode & 0xFF) << 56
    word |= (instruction.flow_id & 0xFFFF) << 40
    word |= (instruction.stage_index & 0xFF) << 32
    word |= (instruction.core_index & 0xFF) << 24
    word |= (instruction.latency & 0xFF) << 16
    word |= (flags & 0xFF) << 8
    word |= imm
    return word
