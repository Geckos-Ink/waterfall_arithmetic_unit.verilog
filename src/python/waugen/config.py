# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .device_library import get_device_preset
from .operation_library import get_operation_template
from .utils import validate_range


class ConfigError(ValueError):
    """Raised when a WAU JSON config is invalid."""


_DTYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class Coord:
    x: int
    y: int

    @staticmethod
    def from_obj(value: Any, *, field_name: str) -> "Coord":
        if not isinstance(value, dict):
            raise ConfigError(f"{field_name} must be an object with x/y")
        try:
            x = int(value["x"])
            y = int(value["y"])
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"{field_name} must include integer x and y") from exc
        return Coord(x=x, y=y)


@dataclass(frozen=True)
class DeviceSpec:
    name: str
    vendor: str
    family: str
    part: str
    grid_x: int
    grid_y: int
    data_width: int
    supported_data_types: tuple[str, ...]
    flow_id_width: int
    opcode_width: int
    local_ram_depth: int
    global_ram_depth: int
    coordinator_mode: str
    enable_runtime_auto_adapt: bool

    @staticmethod
    def from_obj(value: Any) -> "DeviceSpec":
        if not isinstance(value, dict):
            raise ConfigError("device must be an object")

        preset_name = value.get("preset", "intel_de0_nano")
        preset = get_device_preset(str(preset_name))

        grid = value.get("grid", {})
        if not isinstance(grid, dict):
            raise ConfigError("device.grid must be an object")

        grid_x = int(grid.get("x", preset.default_grid_x))
        grid_y = int(grid.get("y", preset.default_grid_y))

        coordinator_mode = str(value.get("coordinator_mode", "direct"))
        if coordinator_mode not in {"direct", "full_edges", "highway_only"}:
            raise ConfigError(
                "device.coordinator_mode must be one of: direct, full_edges, highway_only"
            )

        data_width = validate_range(
            int(value.get("data_width", preset.data_width)),
            minimum=8,
            name="device.data_width",
        )

        supported_data_types = _parse_data_types(
            value.get("data_types"),
            field_name="device.data_types",
            default=(f"int{data_width}",),
        )

        spec = DeviceSpec(
            name=str(value.get("name", preset.name)),
            vendor=str(value.get("vendor", preset.vendor)),
            family=str(value.get("family", preset.family)),
            part=str(value.get("part", preset.part)),
            grid_x=validate_range(grid_x, minimum=1, name="device.grid.x"),
            grid_y=validate_range(grid_y, minimum=1, name="device.grid.y"),
            data_width=data_width,
            supported_data_types=supported_data_types,
            flow_id_width=validate_range(
                int(value.get("flow_id_width", preset.flow_id_width)),
                minimum=4,
                name="device.flow_id_width",
            ),
            opcode_width=validate_range(
                int(value.get("opcode_width", preset.opcode_width)),
                minimum=4,
                name="device.opcode_width",
            ),
            local_ram_depth=validate_range(
                int(value.get("local_ram_depth", preset.local_ram_depth)),
                minimum=16,
                name="device.local_ram_depth",
            ),
            global_ram_depth=validate_range(
                int(value.get("global_ram_depth", preset.global_ram_depth)),
                minimum=64,
                name="device.global_ram_depth",
            ),
            coordinator_mode=coordinator_mode,
            enable_runtime_auto_adapt=bool(value.get("enable_runtime_auto_adapt", True)),
        )

        if spec.grid_x * spec.grid_y > 255:
            raise ConfigError("grid_x * grid_y must be <= 255 for this generator basis")
        return spec


@dataclass(frozen=True)
class OperationSpec:
    name: str
    opcode: int
    latency: int
    pipelined: bool
    verilog_expr: str


@dataclass(frozen=True)
class PlacementSpec:
    core: Coord | None
    fallback_core: Coord | None
    candidate_cores: tuple[Coord, ...]
    fixed: bool
    directive: str


@dataclass(frozen=True)
class FlowStageSpec:
    op: str
    core: Coord | None
    fallback_core: Coord | None
    immediate_b: int | None
    allow_adaptive: bool
    dtype: str | None


@dataclass(frozen=True)
class FlowNodeSpec:
    node_id: str
    op: str
    deps: tuple[str, ...]
    placement: PlacementSpec
    immediate_b: int | None
    allow_adaptive: bool
    dtype: str | None
    recurrent: bool
    max_iterations: int


@dataclass(frozen=True)
class CoreCapabilitySpec:
    core: Coord
    operations: tuple[str, ...]
    data_types: tuple[str, ...]


@dataclass(frozen=True)
class FlowSpec:
    flow_id: int
    name: str
    entry: Coord
    exit: Coord | None
    stages: tuple[FlowStageSpec, ...]
    nodes: tuple[FlowNodeSpec, ...]
    max_in_flight: int


@dataclass(frozen=True)
class ProgramSpec:
    program_id: int
    name: str
    flow_ids: tuple[int, ...]
    priority: int
    replicas: int
    max_parallel_flows: int
    load_balance: str
    allow_async: bool
    allow_out_of_order: bool


@dataclass(frozen=True)
class CompilerSpec:
    routing: str
    allow_adaptive_reroute: bool
    fallback_radius: int
    allow_cycle_recurrence: bool
    core_capabilities: tuple[CoreCapabilitySpec, ...]

    @staticmethod
    def from_obj(value: Any) -> "CompilerSpec":
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError("compiler must be an object")
        routing = str(value.get("routing", "waterfall"))
        if routing not in {"waterfall", "serpentine", "manual"}:
            raise ConfigError("compiler.routing must be waterfall, serpentine, or manual")
        core_capabilities = _parse_core_capabilities(value.get("core_capabilities"))
        return CompilerSpec(
            routing=routing,
            allow_adaptive_reroute=bool(value.get("allow_adaptive_reroute", True)),
            fallback_radius=validate_range(
                int(value.get("fallback_radius", 1)), minimum=0, name="compiler.fallback_radius"
            ),
            allow_cycle_recurrence=bool(value.get("allow_cycle_recurrence", True)),
            core_capabilities=core_capabilities,
        )


@dataclass(frozen=True)
class SchedulerSpec:
    strategy: str
    emit_timeline: bool
    program_policy: str

    @staticmethod
    def from_obj(value: Any) -> "SchedulerSpec":
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError("scheduler must be an object")
        strategy = str(value.get("strategy", "round_robin"))
        if strategy not in {"round_robin", "serial", "dependency_aware"}:
            raise ConfigError("scheduler.strategy must be round_robin, serial, or dependency_aware")

        program_policy = str(value.get("program_policy", "weighted_fair"))
        if program_policy not in {"weighted_fair", "strict_priority", "round_robin"}:
            raise ConfigError(
                "scheduler.program_policy must be weighted_fair, strict_priority, or round_robin"
            )

        return SchedulerSpec(
            strategy=strategy,
            emit_timeline=bool(value.get("emit_timeline", True)),
            program_policy=program_policy,
        )


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    output_module_name: str
    abstraction_language: str
    abstraction_version: int
    device: DeviceSpec
    operations: tuple[OperationSpec, ...]
    flows: tuple[FlowSpec, ...]
    programs: tuple[ProgramSpec, ...]
    compiler: CompilerSpec
    scheduler: SchedulerSpec


def _parse_dtype(value: Any, *, field_name: str) -> str:
    dtype = str(value).strip()
    if not dtype:
        raise ConfigError(f"{field_name} cannot be empty")
    if not _DTYPE_RE.fullmatch(dtype):
        raise ConfigError(
            f"{field_name} must match pattern {_DTYPE_RE.pattern!r}, got '{dtype}'"
        )
    return dtype


def _parse_data_types(
    value: Any,
    *,
    field_name: str,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if value is None:
        if default is None:
            return ()
        return tuple(_parse_dtype(item, field_name=field_name) for item in default)

    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list")

    data_types: list[str] = []
    for idx, raw in enumerate(value):
        data_types.append(_parse_dtype(raw, field_name=f"{field_name}[{idx}]"))

    if not data_types:
        raise ConfigError(f"{field_name} must contain at least one data type")

    if len(set(data_types)) != len(data_types):
        raise ConfigError(f"{field_name} contains duplicate entries")

    return tuple(data_types)


def _parse_core_capabilities(value: Any) -> tuple[CoreCapabilitySpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("compiler.core_capabilities must be a list")

    capabilities: list[CoreCapabilitySpec] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ConfigError(f"compiler.core_capabilities[{idx}] must be an object")

        core = Coord.from_obj(raw.get("core"), field_name=f"compiler.core_capabilities[{idx}].core")
        operations = _parse_name_list(
            raw.get("operations"),
            field_name=f"compiler.core_capabilities[{idx}].operations",
        )
        data_types = _parse_data_types(
            raw.get("data_types"),
            field_name=f"compiler.core_capabilities[{idx}].data_types",
        )

        capabilities.append(
            CoreCapabilitySpec(
                core=core,
                operations=operations,
                data_types=data_types,
            )
        )

    return tuple(capabilities)


def _parse_name_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list")

    names: list[str] = []
    for idx, raw in enumerate(value):
        name = str(raw).strip()
        if not name:
            raise ConfigError(f"{field_name}[{idx}] cannot be empty")
        names.append(name)

    if len(set(names)) != len(names):
        raise ConfigError(f"{field_name} contains duplicate entries")

    return tuple(names)


def _load_abstraction(value: Any) -> tuple[str, int]:
    if value is None:
        return ("wau_flow_ir", 1)
    if not isinstance(value, dict):
        raise ConfigError("abstraction must be an object")

    language = str(value.get("language", "wau_flow_ir")).strip() or "wau_flow_ir"
    if language not in {"wau_flow_ir", "wau_pseudoc"}:
        raise ConfigError("abstraction.language must be wau_flow_ir or wau_pseudoc")

    version = validate_range(int(value.get("version", 1)), minimum=1, name="abstraction.version")
    return (language, version)


def _load_operations(value: Any) -> tuple[OperationSpec, ...]:
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        library = value.get("library", [])
        custom = value.get("custom", [])
        overrides = value.get("overrides", {})
        if not isinstance(library, list) or not isinstance(custom, list) or not isinstance(overrides, dict):
            raise ConfigError("operations object must contain list fields: library/custom and object overrides")
        entries = []
        for op_name in library:
            if isinstance(op_name, str):
                template = get_operation_template(op_name)
                raw = {
                    "name": template.name,
                    "opcode": template.opcode,
                    "latency": template.latency,
                    "pipelined": template.pipelined,
                    "verilog_expr": template.verilog_expr,
                }
                raw.update(overrides.get(op_name, {}))
                entries.append(raw)
            elif isinstance(op_name, dict):
                entries.append(op_name)
            else:
                raise ConfigError("operations.library entries must be operation names or objects")
        entries.extend(custom)
    else:
        raise ConfigError("operations must be either a list or an object")

    loaded: list[OperationSpec] = []
    for raw in entries:
        if isinstance(raw, str):
            template = get_operation_template(raw)
            loaded.append(
                OperationSpec(
                    name=template.name,
                    opcode=template.opcode,
                    latency=template.latency,
                    pipelined=template.pipelined,
                    verilog_expr=template.verilog_expr,
                )
            )
            continue

        if not isinstance(raw, dict):
            raise ConfigError("operations entries must be objects or operation names")

        if "name" not in raw and "from_library" in raw:
            template = get_operation_template(str(raw["from_library"]))
            merged = {
                "name": template.name,
                "opcode": template.opcode,
                "latency": template.latency,
                "pipelined": template.pipelined,
                "verilog_expr": template.verilog_expr,
            }
            merged.update(raw)
            raw = merged

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ConfigError("operations entries must define name")

        try:
            opcode = int(raw["opcode"])
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"operation '{name}' requires integer opcode") from exc

        latency = validate_range(
            int(raw.get("latency", 1)), minimum=1, name=f"operations.{name}.latency"
        )
        pipelined = bool(raw.get("pipelined", True))
        verilog_expr = str(raw.get("verilog_expr", "a + b")).strip()

        loaded.append(
            OperationSpec(
                name=name,
                opcode=opcode,
                latency=latency,
                pipelined=pipelined,
                verilog_expr=verilog_expr,
            )
        )

    if not loaded:
        raise ConfigError("at least one operation must be declared")

    seen_name: set[str] = set()
    seen_opcode: set[int] = set()
    for op in loaded:
        if op.name in seen_name:
            raise ConfigError(f"duplicate operation name: {op.name}")
        if op.opcode in seen_opcode:
            raise ConfigError(f"duplicate operation opcode: {op.opcode}")
        seen_name.add(op.name)
        seen_opcode.add(op.opcode)

    return tuple(loaded)


def _parse_coord_list(value: Any, *, field_name: str) -> tuple[Coord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list of coord objects")
    coords: list[Coord] = []
    for idx, raw in enumerate(value):
        coords.append(Coord.from_obj(raw, field_name=f"{field_name}[{idx}]") )
    return tuple(coords)


def _parse_placement(raw_node: dict[str, Any], *, flow_id: int, node_label: str) -> PlacementSpec:
    placement = raw_node.get("placement", {})
    if placement is None:
        placement = {}
    if not isinstance(placement, dict):
        raise ConfigError(f"flow {flow_id} node {node_label} placement must be an object")

    core = (
        Coord.from_obj(placement["core"], field_name=f"flow[{flow_id}].nodes[{node_label}].placement.core")
        if "core" in placement
        else (
            Coord.from_obj(raw_node["core"], field_name=f"flow[{flow_id}].nodes[{node_label}].core")
            if "core" in raw_node
            else None
        )
    )

    fallback_core = (
        Coord.from_obj(
            placement["fallback_core"],
            field_name=f"flow[{flow_id}].nodes[{node_label}].placement.fallback_core",
        )
        if "fallback_core" in placement
        else (
            Coord.from_obj(raw_node["fallback_core"], field_name=f"flow[{flow_id}].nodes[{node_label}].fallback_core")
            if "fallback_core" in raw_node
            else None
        )
    )

    candidate_cores = _parse_coord_list(
        placement.get("candidate_cores", raw_node.get("candidate_cores", [])),
        field_name=f"flow[{flow_id}].nodes[{node_label}].candidate_cores",
    )

    fixed = bool(placement.get("fixed", raw_node.get("fixed", False)))
    directive = str(placement.get("directive", raw_node.get("placement_directive", "auto")))
    if directive not in {"auto", "manual", "prefer_locality", "prefer_balance"}:
        raise ConfigError(
            f"flow {flow_id} node {node_label} placement directive must be one of: "
            "auto, manual, prefer_locality, prefer_balance"
        )

    return PlacementSpec(
        core=core,
        fallback_core=fallback_core,
        candidate_cores=candidate_cores,
        fixed=fixed,
        directive=directive,
    )


def _load_flow_nodes(raw_flow: dict[str, Any], *, flow_id: int) -> tuple[FlowNodeSpec, ...]:
    raw_nodes = raw_flow.get("nodes", [])
    if raw_nodes is None:
        raw_nodes = []
    if not isinstance(raw_nodes, list):
        raise ConfigError(f"flow {flow_id}.nodes must be a list")

    nodes: list[FlowNodeSpec] = []
    seen_ids: set[str] = set()

    for idx, raw_node in enumerate(raw_nodes):
        if isinstance(raw_node, str):
            node_id = f"n{idx}"
            op = raw_node
            deps: tuple[str, ...] = ()
            placement = PlacementSpec(
                core=None,
                fallback_core=None,
                candidate_cores=(),
                fixed=False,
                directive="auto",
            )
            immediate_b = None
            allow_adaptive = True
            dtype = None
            recurrent = False
            max_iterations = 1
        else:
            if not isinstance(raw_node, dict):
                raise ConfigError(f"flow {flow_id}.nodes[{idx}] must be object or operation name string")

            node_id = str(raw_node.get("id", f"n{idx}")).strip()
            if not node_id:
                raise ConfigError(f"flow {flow_id}.nodes[{idx}] id cannot be empty")

            op = str(raw_node.get("op", "")).strip()
            if not op:
                raise ConfigError(f"flow {flow_id}.nodes[{idx}] requires op")

            raw_deps = raw_node.get("deps", [])
            if not isinstance(raw_deps, list):
                raise ConfigError(f"flow {flow_id}.nodes[{node_id}].deps must be a list")
            deps = tuple(str(dep).strip() for dep in raw_deps if str(dep).strip())

            placement = _parse_placement(raw_node, flow_id=flow_id, node_label=node_id)
            immediate_b = int(raw_node["immediate_b"]) if "immediate_b" in raw_node else None
            allow_adaptive = bool(raw_node.get("allow_adaptive", True))
            dtype = _parse_dtype(
                raw_node["dtype"],
                field_name=f"flow[{flow_id}].nodes[{node_id}].dtype",
            ) if "dtype" in raw_node else None

            loop_cfg = raw_node.get("loop", {})
            if loop_cfg is None:
                loop_cfg = {}
            if not isinstance(loop_cfg, dict):
                raise ConfigError(f"flow {flow_id}.nodes[{node_id}].loop must be an object")

            recurrent = bool(raw_node.get("recurrent", loop_cfg.get("enabled", False)))
            max_iterations = validate_range(
                int(raw_node.get("max_iterations", loop_cfg.get("max_iterations", 1))),
                minimum=1,
                name=f"flow[{flow_id}].nodes[{node_id}].max_iterations",
            )

        if node_id in seen_ids:
            raise ConfigError(f"flow {flow_id} duplicate node id: {node_id}")
        seen_ids.add(node_id)

        nodes.append(
            FlowNodeSpec(
                node_id=node_id,
                op=op,
                deps=deps,
                placement=placement,
                immediate_b=immediate_b,
                allow_adaptive=allow_adaptive,
                dtype=dtype,
                recurrent=recurrent,
                max_iterations=max_iterations,
            )
        )

    node_ids = {node.node_id for node in nodes}
    for node in nodes:
        for dep in node.deps:
            if dep not in node_ids:
                raise ConfigError(f"flow {flow_id} node {node.node_id} depends on unknown node '{dep}'")

    return tuple(nodes)


def _load_flows(value: Any) -> tuple[FlowSpec, ...]:
    if not isinstance(value, list):
        raise ConfigError("flows must be a list")

    flows: list[FlowSpec] = []
    seen_ids: set[int] = set()

    for raw_flow in value:
        if not isinstance(raw_flow, dict):
            raise ConfigError("each flow must be an object")

        flow_id = int(raw_flow.get("id", -1))
        if flow_id < 0:
            raise ConfigError("each flow must have a non-negative integer id")
        if flow_id in seen_ids:
            raise ConfigError(f"duplicate flow id: {flow_id}")
        seen_ids.add(flow_id)

        name = str(raw_flow.get("name", f"flow_{flow_id}"))
        entry = Coord.from_obj(raw_flow.get("entry", {"x": 0, "y": 0}), field_name=f"flow[{flow_id}].entry")
        exit_coord = (
            Coord.from_obj(raw_flow["exit"], field_name=f"flow[{flow_id}].exit")
            if "exit" in raw_flow
            else None
        )

        raw_stages = raw_flow.get("stages", [])
        if raw_stages is None:
            raw_stages = []
        if not isinstance(raw_stages, list):
            raise ConfigError(f"flow {flow_id} stages must be a list")

        stages: list[FlowStageSpec] = []
        for idx, raw_stage in enumerate(raw_stages):
            if isinstance(raw_stage, str):
                stages.append(
                    FlowStageSpec(
                        op=raw_stage,
                        core=None,
                        fallback_core=None,
                        immediate_b=None,
                        allow_adaptive=True,
                        dtype=None,
                    )
                )
                continue

            if not isinstance(raw_stage, dict):
                raise ConfigError(f"flow {flow_id} stage {idx} must be object or op-name string")

            op = str(raw_stage.get("op", "")).strip()
            if not op:
                raise ConfigError(f"flow {flow_id} stage {idx} requires 'op'")

            core = Coord.from_obj(raw_stage["core"], field_name=f"flow[{flow_id}].stages[{idx}].core") if "core" in raw_stage else None
            fallback_core = (
                Coord.from_obj(raw_stage["fallback_core"], field_name=f"flow[{flow_id}].stages[{idx}].fallback_core")
                if "fallback_core" in raw_stage
                else None
            )
            immediate_b = int(raw_stage["immediate_b"]) if "immediate_b" in raw_stage else None
            allow_adaptive = bool(raw_stage.get("allow_adaptive", True))
            dtype = _parse_dtype(
                raw_stage["dtype"],
                field_name=f"flow[{flow_id}].stages[{idx}].dtype",
            ) if "dtype" in raw_stage else None
            stages.append(
                FlowStageSpec(
                    op=op,
                    core=core,
                    fallback_core=fallback_core,
                    immediate_b=immediate_b,
                    allow_adaptive=allow_adaptive,
                    dtype=dtype,
                )
            )

        nodes = _load_flow_nodes(raw_flow, flow_id=flow_id)

        if not stages and not nodes:
            raise ConfigError(f"flow {flow_id} must contain a non-empty stages or nodes list")

        flows.append(
            FlowSpec(
                flow_id=flow_id,
                name=name,
                entry=entry,
                exit=exit_coord,
                stages=tuple(stages),
                nodes=nodes,
                max_in_flight=validate_range(
                    int(raw_flow.get("max_in_flight", 1)),
                    minimum=1,
                    name=f"flow[{flow_id}].max_in_flight",
                ),
            )
        )

    if not flows:
        raise ConfigError("at least one flow must be declared")

    return tuple(flows)


def _resolve_flow_ref(ref: Any, *, flow_ids: set[int], flow_name_to_id: dict[str, int], program_id: int) -> int:
    if isinstance(ref, int):
        flow_id = ref
    elif isinstance(ref, str):
        raw = ref.strip()
        if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
            flow_id = int(raw)
        else:
            if raw not in flow_name_to_id:
                raise ConfigError(f"program {program_id} references unknown flow name '{raw}'")
            flow_id = flow_name_to_id[raw]
    elif isinstance(ref, dict):
        if "id" in ref:
            flow_id = int(ref["id"])
        elif "name" in ref:
            name = str(ref["name"])
            if name not in flow_name_to_id:
                raise ConfigError(f"program {program_id} references unknown flow name '{name}'")
            flow_id = flow_name_to_id[name]
        else:
            raise ConfigError(f"program {program_id} flow ref object must contain id or name")
    else:
        raise ConfigError(f"program {program_id} flow refs must be int/string/object")

    if flow_id not in flow_ids:
        raise ConfigError(f"program {program_id} references unknown flow id {flow_id}")
    return flow_id


def _load_programs(value: Any, *, flows: tuple[FlowSpec, ...]) -> tuple[ProgramSpec, ...]:
    flow_ids = {flow.flow_id for flow in flows}
    flow_name_to_id = {flow.name: flow.flow_id for flow in flows}
    default_flow_ids = tuple(sorted(flow_ids))

    if value is None:
        return (
            ProgramSpec(
                program_id=0,
                name="default",
                flow_ids=default_flow_ids,
                priority=1,
                replicas=1,
                max_parallel_flows=max(1, len(default_flow_ids)),
                load_balance="round_robin",
                allow_async=True,
                allow_out_of_order=True,
            ),
        )

    if not isinstance(value, list):
        raise ConfigError("programs must be a list")

    programs: list[ProgramSpec] = []
    seen_ids: set[int] = set()

    for idx, raw_program in enumerate(value):
        if not isinstance(raw_program, dict):
            raise ConfigError("each program must be an object")

        program_id = int(raw_program.get("id", idx))
        if program_id < 0:
            raise ConfigError("program id must be non-negative")
        if program_id in seen_ids:
            raise ConfigError(f"duplicate program id: {program_id}")
        seen_ids.add(program_id)

        name = str(raw_program.get("name", f"program_{program_id}")).strip() or f"program_{program_id}"

        raw_refs = raw_program.get("flows", list(default_flow_ids))
        if not isinstance(raw_refs, list) or not raw_refs:
            raise ConfigError(f"program {program_id} flows must be a non-empty list")
        resolved_ids = tuple(
            _resolve_flow_ref(ref, flow_ids=flow_ids, flow_name_to_id=flow_name_to_id, program_id=program_id)
            for ref in raw_refs
        )

        priority = validate_range(
            int(raw_program.get("priority", 1)),
            minimum=1,
            name=f"program[{program_id}].priority",
        )
        replicas = validate_range(
            int(raw_program.get("replicas", 1)),
            minimum=1,
            name=f"program[{program_id}].replicas",
        )
        max_parallel_flows = validate_range(
            int(raw_program.get("max_parallel_flows", len(resolved_ids) * replicas)),
            minimum=1,
            name=f"program[{program_id}].max_parallel_flows",
        )

        load_balance = str(raw_program.get("load_balance", "round_robin"))
        if load_balance not in {"round_robin", "least_busy"}:
            raise ConfigError(
                f"program {program_id} load_balance must be round_robin or least_busy"
            )

        programs.append(
            ProgramSpec(
                program_id=program_id,
                name=name,
                flow_ids=resolved_ids,
                priority=priority,
                replicas=replicas,
                max_parallel_flows=max_parallel_flows,
                load_balance=load_balance,
                allow_async=bool(raw_program.get("allow_async", True)),
                allow_out_of_order=bool(raw_program.get("allow_out_of_order", True)),
            )
        )

    if not programs:
        raise ConfigError("at least one program must be declared")

    return tuple(programs)


def load_config(path: Path) -> ProjectConfig:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("Root JSON object expected")

    project_name = str(payload.get("project", path.stem)).strip() or path.stem
    output_module_name = str(payload.get("output_module_name", "wau_top")).strip() or "wau_top"
    abstraction_language, abstraction_version = _load_abstraction(payload.get("abstraction"))

    device = DeviceSpec.from_obj(payload.get("device", {}))
    operations = _load_operations(payload.get("operations", {"library": ["add", "sub", "mul", "div"]}))
    flows = _load_flows(payload.get("flows", []))
    programs = _load_programs(payload.get("programs"), flows=flows)
    compiler = CompilerSpec.from_obj(payload.get("compiler", {}))
    scheduler = SchedulerSpec.from_obj(payload.get("scheduler", {}))

    op_names = {op.name for op in operations}
    data_types = set(device.supported_data_types)
    for flow in flows:
        for idx, stage in enumerate(flow.stages):
            if stage.op not in op_names:
                raise ConfigError(
                    f"Flow {flow.flow_id} stage {idx} references unknown op '{stage.op}'"
                )
            if stage.dtype is not None and stage.dtype not in data_types:
                raise ConfigError(
                    f"Flow {flow.flow_id} stage {idx} references unsupported dtype '{stage.dtype}'"
                )
        for node in flow.nodes:
            if node.op not in op_names:
                raise ConfigError(
                    f"Flow {flow.flow_id} node {node.node_id} references unknown op '{node.op}'"
                )
            if node.dtype is not None and node.dtype not in data_types:
                raise ConfigError(
                    f"Flow {flow.flow_id} node {node.node_id} references unsupported dtype '{node.dtype}'"
                )

    seen_capability_cores: set[tuple[int, int]] = set()
    for idx, capability in enumerate(compiler.core_capabilities):
        if not (0 <= capability.core.x < device.grid_x and 0 <= capability.core.y < device.grid_y):
            raise ConfigError(
                "compiler.core_capabilities[{idx}].core out of grid bounds".format(idx=idx)
            )
        core_key = (capability.core.x, capability.core.y)
        if core_key in seen_capability_cores:
            raise ConfigError(
                f"compiler.core_capabilities has duplicate core entry at ({capability.core.x}, {capability.core.y})"
            )
        seen_capability_cores.add(core_key)

        for op_name in capability.operations:
            if op_name not in op_names:
                raise ConfigError(
                    f"compiler.core_capabilities[{idx}] references unknown operation '{op_name}'"
                )
        for dtype in capability.data_types:
            if dtype not in data_types:
                raise ConfigError(
                    f"compiler.core_capabilities[{idx}] references unsupported dtype '{dtype}'"
                )

    return ProjectConfig(
        project_name=project_name,
        output_module_name=output_module_name,
        abstraction_language=abstraction_language,
        abstraction_version=abstraction_version,
        device=device,
        operations=operations,
        flows=flows,
        programs=programs,
        compiler=compiler,
        scheduler=scheduler,
    )
