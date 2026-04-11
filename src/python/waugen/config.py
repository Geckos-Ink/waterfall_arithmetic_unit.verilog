from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .device_library import get_device_preset
from .operation_library import get_operation_template
from .utils import validate_range


class ConfigError(ValueError):
    """Raised when a WAU JSON config is invalid."""


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

        spec = DeviceSpec(
            name=str(value.get("name", preset.name)),
            vendor=str(value.get("vendor", preset.vendor)),
            family=str(value.get("family", preset.family)),
            part=str(value.get("part", preset.part)),
            grid_x=validate_range(grid_x, minimum=1, name="device.grid.x"),
            grid_y=validate_range(grid_y, minimum=1, name="device.grid.y"),
            data_width=validate_range(
                int(value.get("data_width", preset.data_width)),
                minimum=8,
                name="device.data_width",
            ),
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
class FlowStageSpec:
    op: str
    core: Coord | None
    fallback_core: Coord | None
    immediate_b: int | None
    allow_adaptive: bool


@dataclass(frozen=True)
class FlowSpec:
    flow_id: int
    name: str
    entry: Coord
    exit: Coord | None
    stages: tuple[FlowStageSpec, ...]
    max_in_flight: int


@dataclass(frozen=True)
class CompilerSpec:
    routing: str
    allow_adaptive_reroute: bool
    fallback_radius: int

    @staticmethod
    def from_obj(value: Any) -> "CompilerSpec":
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError("compiler must be an object")
        routing = str(value.get("routing", "waterfall"))
        if routing not in {"waterfall", "serpentine", "manual"}:
            raise ConfigError("compiler.routing must be waterfall, serpentine, or manual")
        return CompilerSpec(
            routing=routing,
            allow_adaptive_reroute=bool(value.get("allow_adaptive_reroute", True)),
            fallback_radius=validate_range(
                int(value.get("fallback_radius", 1)), minimum=0, name="compiler.fallback_radius"
            ),
        )


@dataclass(frozen=True)
class SchedulerSpec:
    strategy: str
    emit_timeline: bool

    @staticmethod
    def from_obj(value: Any) -> "SchedulerSpec":
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError("scheduler must be an object")
        strategy = str(value.get("strategy", "round_robin"))
        if strategy not in {"round_robin", "serial"}:
            raise ConfigError("scheduler.strategy must be round_robin or serial")
        return SchedulerSpec(
            strategy=strategy,
            emit_timeline=bool(value.get("emit_timeline", True)),
        )


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    output_module_name: str
    device: DeviceSpec
    operations: tuple[OperationSpec, ...]
    flows: tuple[FlowSpec, ...]
    compiler: CompilerSpec
    scheduler: SchedulerSpec


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
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ConfigError(f"flow {flow_id} must contain a non-empty stages list")

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
            stages.append(
                FlowStageSpec(
                    op=op,
                    core=core,
                    fallback_core=fallback_core,
                    immediate_b=immediate_b,
                    allow_adaptive=allow_adaptive,
                )
            )

        flows.append(
            FlowSpec(
                flow_id=flow_id,
                name=name,
                entry=entry,
                exit=exit_coord,
                stages=tuple(stages),
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

    device = DeviceSpec.from_obj(payload.get("device", {}))
    operations = _load_operations(payload.get("operations", {"library": ["add", "sub", "mul", "div"]}))
    flows = _load_flows(payload.get("flows", []))
    compiler = CompilerSpec.from_obj(payload.get("compiler", {}))
    scheduler = SchedulerSpec.from_obj(payload.get("scheduler", {}))

    op_names = {op.name for op in operations}
    for flow in flows:
        for idx, stage in enumerate(flow.stages):
            if stage.op not in op_names:
                raise ConfigError(
                    f"Flow {flow.flow_id} stage {idx} references unknown op '{stage.op}'"
                )

    return ProjectConfig(
        project_name=project_name,
        output_module_name=output_module_name,
        device=device,
        operations=operations,
        flows=flows,
        compiler=compiler,
        scheduler=scheduler,
    )
