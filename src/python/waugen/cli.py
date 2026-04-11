from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .basic_compiler import BasicCompilerError, merge_expression_into_config
from .compiler import compile_project
from .config import ConfigError, load_config
from .device_library import DEVICE_PRESETS
from .operation_library import OPERATION_LIBRARY
from .scheduler import build_schedule
from .verilog_emit import emit_verilog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waugen",
        description="Waterfall Arithmetic Unit Verilog generator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="compile config and emit verilog + schedule artifacts")
    g.add_argument("--config", required=True, type=Path, help="Path to WAU JSON config")
    g.add_argument(
        "--out",
        type=Path,
        default=Path("src/verilog/generated"),
        help="Output directory for generated artifacts",
    )
    g.add_argument(
        "--summary",
        action="store_true",
        help="Print generation summary",
    )

    v = sub.add_parser("validate", help="validate JSON config + compiler/scheduler stages")
    v.add_argument("--config", required=True, type=Path, help="Path to WAU JSON config")

    c = sub.add_parser(
        "compile-expr",
        help="basic WAU compiler: compile arithmetic expression into a flow and merge it in a config",
    )
    c.add_argument("--expr", required=True, help="Arithmetic expression, example: ((a + b) * 3) - b")
    c.add_argument("--flow-id", required=True, type=int, help="Flow id to generate")
    c.add_argument("--name", default=None, help="Flow name (default: expr_flow_<id>)")
    c.add_argument(
        "--entry",
        default="0,0",
        help="Flow entry coordinate as x,y (default: 0,0)",
    )
    c.add_argument(
        "--max-in-flight",
        type=int,
        default=1,
        help="Flow max in-flight packets (default: 1)",
    )
    c.add_argument(
        "--base-config",
        required=True,
        type=Path,
        help="Base WAU config to extend",
    )
    c.add_argument(
        "--out-config",
        required=True,
        type=Path,
        help="Output config path with merged compiled flow",
    )
    c.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing flow if the same flow id already exists",
    )

    sub.add_parser("list-devices", help="list built-in real device presets")
    sub.add_parser("list-operations", help="list built-in operation templates")

    return parser


def _run_generate(config_path: Path, out_dir: Path, summary: bool) -> int:
    config = load_config(config_path)
    compiled = compile_project(config)
    schedule = build_schedule(compiled)
    paths = emit_verilog(compiled, schedule, out_dir)

    print(f"Generated {len(paths)} files in {out_dir}")
    if summary:
        print(f"Project: {config.project_name}")
        print(
            "Device: "
            f"{config.device.vendor} {config.device.family} ({config.device.part}), "
            f"grid={config.device.grid_x}x{config.device.grid_y}"
        )
        print(f"Flows: {len(config.flows)}")
        print(f"Operations: {len(config.operations)}")
        print(f"Max stages per flow: {compiled.max_stages}")
        print(f"Schedule makespan: {schedule.makespan_cycles} cycles")

    return 0


def _run_validate(config_path: Path) -> int:
    config = load_config(config_path)
    compiled = compile_project(config)
    schedule = build_schedule(compiled)

    print("Configuration is valid")
    print(f"Project: {config.project_name}")
    print(f"Flows: {len(config.flows)}")
    print(f"Operations: {len(config.operations)}")
    print(f"Estimated schedule makespan: {schedule.makespan_cycles} cycles")
    return 0


def _parse_entry(entry: str) -> tuple[int, int]:
    try:
        parts = [chunk.strip() for chunk in entry.split(",")]
        if len(parts) != 2:
            raise ValueError
        x = int(parts[0])
        y = int(parts[1])
    except Exception as exc:  # noqa: BLE001
        raise BasicCompilerError("entry must be in format x,y with integer values") from exc
    return x, y


def _run_compile_expr(
    *,
    expr: str,
    flow_id: int,
    name: str | None,
    entry: str,
    max_in_flight: int,
    base_config: Path,
    out_config: Path,
    replace_existing: bool,
) -> int:
    entry_x, entry_y = _parse_entry(entry)
    resolved_name = name or f"expr_flow_{flow_id}"

    flow = merge_expression_into_config(
        base_config_path=base_config,
        out_config_path=out_config,
        expr=expr,
        flow_id=flow_id,
        name=resolved_name,
        entry_x=entry_x,
        entry_y=entry_y,
        replace_existing=replace_existing,
        max_in_flight=max_in_flight,
    )

    print(f"Compiled expression into flow {flow_id} ({resolved_name})")
    print(f"Stages: {len(flow['stages'])}")
    print(f"Wrote merged config: {out_config}")
    return 0


def _run_list_devices() -> int:
    for name in sorted(DEVICE_PRESETS):
        preset = DEVICE_PRESETS[name]
        print(
            f"{name}: {preset.vendor} {preset.family} {preset.part} "
            f"(default_grid={preset.default_grid_x}x{preset.default_grid_y}, "
            f"data_width={preset.data_width})"
        )
    return 0


def _run_list_operations() -> int:
    for name in sorted(OPERATION_LIBRARY):
        op = OPERATION_LIBRARY[name]
        print(
            f"{name}: opcode=0x{op.opcode:02X}, latency={op.latency}, "
            f"pipelined={'yes' if op.pipelined else 'no'}, expr={op.verilog_expr}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate":
            return _run_generate(args.config, args.out, args.summary)
        if args.command == "validate":
            return _run_validate(args.config)
        if args.command == "compile-expr":
            return _run_compile_expr(
                expr=args.expr,
                flow_id=args.flow_id,
                name=args.name,
                entry=args.entry,
                max_in_flight=args.max_in_flight,
                base_config=args.base_config,
                out_config=args.out_config,
                replace_existing=args.replace_existing,
            )
        if args.command == "list-devices":
            return _run_list_devices()
        if args.command == "list-operations":
            return _run_list_operations()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except BasicCompilerError as exc:
        print(f"Compiler error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1
