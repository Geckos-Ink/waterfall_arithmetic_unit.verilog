from __future__ import annotations

from dataclasses import dataclass

from .compiler import CompiledProject, CompiledStage


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
    latency: int
    used_fallback: bool
    immediate_b: int | None


@dataclass(frozen=True)
class SchedulePlan:
    instructions: tuple[ScheduleInstruction, ...]
    makespan_cycles: int

    def to_json(self) -> dict:
        return {
            "makespan_cycles": self.makespan_cycles,
            "instructions": [
                {
                    "cycle_start": ins.cycle_start,
                    "cycle_end": ins.cycle_end,
                    "flow_slot": ins.flow_slot,
                    "flow_id": ins.flow_id,
                    "stage_index": ins.stage_index,
                    "op": ins.op_name,
                    "opcode": ins.opcode,
                    "core_index": ins.core_index,
                    "core": {"x": ins.core_x, "y": ins.core_y},
                    "latency": ins.latency,
                    "used_fallback": ins.used_fallback,
                    "immediate_b": ins.immediate_b,
                }
                for ins in self.instructions
            ],
        }

    def to_hex_lines(self) -> list[str]:
        return [f"{encode_instruction_word(ins):016X}" for ins in self.instructions]


def core_index(x: int, y: int, grid_x: int) -> int:
    return (y * grid_x) + x


def _core_release_cycle(stage: CompiledStage, start_cycle: int) -> int:
    if stage.pipelined:
        return start_cycle + 1
    return start_cycle + stage.latency


def _choose_core(
    *,
    stage: CompiledStage,
    flow_ready: int,
    core_ready: dict[int, int],
    grid_x: int,
    allow_adapt: bool,
) -> tuple[int, int, bool]:
    primary_idx = core_index(stage.primary_core.x, stage.primary_core.y, grid_x)
    primary_start = max(flow_ready, core_ready.get(primary_idx, 0))
    best_idx = primary_idx
    best_start = primary_start
    best_fallback = False

    if allow_adapt and stage.fallback_core is not None:
        fallback_idx = core_index(stage.fallback_core.x, stage.fallback_core.y, grid_x)
        fallback_start = max(flow_ready, core_ready.get(fallback_idx, 0))
        if fallback_start < best_start:
            best_idx = fallback_idx
            best_start = fallback_start
            best_fallback = True

    return best_idx, best_start, best_fallback


def build_schedule(project: CompiledProject) -> SchedulePlan:
    strategy = project.config.scheduler.strategy
    allow_adapt = project.config.compiler.allow_adaptive_reroute
    grid_x = project.config.device.grid_x

    instructions: list[ScheduleInstruction] = []
    core_ready: dict[int, int] = {}
    flow_next_ready: dict[int, int] = {flow.flow_slot: 0 for flow in project.flows}

    if strategy == "serial":
        queue: list[tuple[int, int]] = []
        for flow in project.flows:
            for stage_idx in range(len(flow.stages)):
                queue.append((flow.flow_slot, stage_idx))
    else:
        queue = []
        done = False
        stage_cursor = {flow.flow_slot: 0 for flow in project.flows}
        while not done:
            done = True
            for flow in project.flows:
                cursor = stage_cursor[flow.flow_slot]
                if cursor < len(flow.stages):
                    queue.append((flow.flow_slot, cursor))
                    stage_cursor[flow.flow_slot] = cursor + 1
                    done = False

    flow_by_slot = {flow.flow_slot: flow for flow in project.flows}

    for flow_slot, stage_index in queue:
        flow = flow_by_slot[flow_slot]
        stage = flow.stages[stage_index]

        chosen_core, start, used_fallback = _choose_core(
            stage=stage,
            flow_ready=flow_next_ready[flow_slot],
            core_ready=core_ready,
            grid_x=grid_x,
            allow_adapt=allow_adapt and stage.allow_adaptive,
        )

        end = start + stage.latency
        release = _core_release_cycle(stage, start)
        core_ready[chosen_core] = release
        flow_next_ready[flow_slot] = end

        core_y = chosen_core // grid_x
        core_x = chosen_core % grid_x

        instructions.append(
            ScheduleInstruction(
                cycle_start=start,
                cycle_end=end,
                flow_slot=flow_slot,
                flow_id=flow.flow_id,
                stage_index=stage_index,
                opcode=stage.opcode,
                op_name=stage.op_name,
                core_index=chosen_core,
                core_x=core_x,
                core_y=core_y,
                latency=stage.latency,
                used_fallback=used_fallback,
                immediate_b=stage.immediate_b,
            )
        )

    instructions.sort(key=lambda ins: (ins.cycle_start, ins.flow_slot, ins.stage_index))
    makespan = max((ins.cycle_end for ins in instructions), default=0)
    return SchedulePlan(instructions=tuple(instructions), makespan_cycles=makespan)


def encode_instruction_word(instruction: ScheduleInstruction) -> int:
    flags = 0
    if instruction.used_fallback:
        flags |= 0x01
    if instruction.immediate_b is not None:
        flags |= 0x02

    # 64-bit encoded word:
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
