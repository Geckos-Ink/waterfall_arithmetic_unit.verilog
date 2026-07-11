# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .arch_search import (
    ArchSearchError,
    FitBudget,
    emit_fit_config,
    format_fit_report_text,
    format_report_text,
    run_arch_search,
    run_fit_search,
)
from .basic_compiler import BasicCompilerError, merge_expression_into_config, merge_pseudoc_into_config
from .compiler import compile_project
from .config import ConfigError, load_config
from .cw_compiler import CWCompilerError, merge_cw_into_config, parse_cw_program, validate_cw_pragmas
from .cw_lang import CWLangError, Interpreter, parse
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
        help="Flow entry coordinate as x,y or x,y,z (default: 0,0)",
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

    pc = sub.add_parser(
        "compile-pseudoc",
        help="compile constrained pseudo-C accumulator program into a flow and merge it in a config",
    )
    source = pc.add_mutually_exclusive_group(required=True)
    source.add_argument("--program", help="Pseudo-C program string")
    source.add_argument("--program-file", type=Path, help="Path to a pseudo-C program file")
    pc.add_argument("--flow-id", required=True, type=int, help="Flow id to generate")
    pc.add_argument("--name", default=None, help="Flow name (default: pseudoc_flow_<id>)")
    pc.add_argument(
        "--entry",
        default="0,0",
        help="Flow entry coordinate as x,y or x,y,z (default: 0,0)",
    )
    pc.add_argument(
        "--max-in-flight",
        type=int,
        default=1,
        help="Flow max in-flight packets (default: 1)",
    )
    pc.add_argument(
        "--base-config",
        required=True,
        type=Path,
        help="Base WAU config to extend",
    )
    pc.add_argument(
        "--out-config",
        required=True,
        type=Path,
        help="Output config path with merged compiled flow",
    )
    pc.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing flow if the same flow id already exists",
    )

    cw = sub.add_parser(
        "compile-cw",
        help="compile WAU .cw kernel-style program into a node DAG flow and merge it in a config",
    )
    cw_source = cw.add_mutually_exclusive_group(required=True)
    cw_source.add_argument("--program", help="CW program string")
    cw_source.add_argument("--program-file", type=Path, help="Path to a .cw program file")
    cw.add_argument("--flow-id", required=True, type=int, help="Flow id to generate")
    cw.add_argument("--name", default=None, help="Flow name (default: cw_flow_<id>)")
    cw.add_argument(
        "--entry",
        default="0,0",
        help="Flow entry coordinate as x,y or x,y,z (default: 0,0)",
    )
    cw.add_argument(
        "--max-in-flight",
        type=int,
        default=None,
        help="Optional flow max in-flight packets override (default: @wau pragma or 4)",
    )
    cw.add_argument(
        "--dtype",
        default=None,
        help=(
            "Optional dtype override for generated nodes "
            "(default: @wau preferred_dtype pragma, else float32 if supported, else first device dtype)"
        ),
    )
    cw.add_argument(
        "--lane-parallelism",
        type=int,
        default=None,
        help="Optional override for number of channel lanes compiled into the flow",
    )
    cw.add_argument(
        "--placement-policy",
        choices=["locality", "balance"],
        default=None,
        help="Optional placement-policy override for generated CW nodes (default: @wau pragma or balance)",
    )
    cw.add_argument(
        "--lowering-profile",
        choices=["reference", "latency_optimized", "throughput_optimized"],
        default=None,
        help="Optional lowering-profile override for CW lowering shape (default: @wau pragma or reference)",
    )
    cw.add_argument(
        "--base-config",
        required=True,
        type=Path,
        help="Base WAU config to extend",
    )
    cw.add_argument(
        "--out-config",
        required=True,
        type=Path,
        help="Output config path with merged compiled flow",
    )
    cw.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing flow if the same flow id already exists",
    )
    cw.add_argument(
        "--program-id",
        type=int,
        default=None,
        help="Program id to upsert for executing the compiled flow (default: flow id)",
    )
    cw.add_argument(
        "--program-name",
        default=None,
        help="Program name to upsert (default: cw_program_<program-id>)",
    )
    cw.add_argument(
        "--program-priority",
        type=int,
        default=None,
        help="Program priority override (default: @wau pragma or 2)",
    )
    cw.add_argument(
        "--program-replicas",
        type=int,
        default=1,
        help="Program replicas (default: 1)",
    )
    cw.add_argument(
        "--program-max-parallel-flows",
        type=int,
        default=None,
        help="Program max parallel flows (default: replicas)",
    )
    cw.add_argument(
        "--program-load-balance",
        choices=["round_robin", "least_busy"],
        default=None,
        help="Program load balance override (default: @wau pragma or least_busy)",
    )

    ev = sub.add_parser(
        "cw-eval",
        help="parse + execute a .cw program on the host (runs main(); supports class magic methods)",
    )
    ev_source = ev.add_mutually_exclusive_group(required=True)
    ev_source.add_argument("--program", help="CW program string")
    ev_source.add_argument("--program-file", type=Path, help="Path to a .cw program file")
    ev.add_argument(
        "--convert",
        nargs=2,
        metavar=("EXPR", "DTYPE"),
        default=None,
        help=(
            "Instead of running main(), evaluate EXPR (an expression in the program's "
            "global scope) and convert it to DTYPE via class magic methods"
        ),
    )
    ev.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    lint = sub.add_parser(
        "cw-lint",
        help="validate .cw syntax and @wau pragmas without lowering to RTL",
    )
    lint_source = lint.add_mutually_exclusive_group(required=True)
    lint_source.add_argument("--program", help="CW program string")
    lint_source.add_argument("--program-file", type=Path, help="Path to a .cw program file")
    lint.add_argument(
        "--compile-template",
        action="store_true",
        help="also require compatibility with the current compile-cw kernel template",
    )
    lint.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    arch = sub.add_parser(
        "arch-search",
        help=(
            "rank synthesis-time WAU architecture candidates (grid shape, op "
            "distribution, memory split, DRAM reliance) for a workload config"
        ),
    )
    arch.add_argument("--config", required=True, type=Path, help="Base WAU JSON config (the workload)")
    arch.add_argument(
        "--out-report",
        required=True,
        type=Path,
        help="Output path for the machine-readable JSON report",
    )
    arch.add_argument(
        "--out-summary",
        type=Path,
        default=None,
        help="Optional output path for the human-readable text summary",
    )
    arch.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top-ranked candidates to print (default: 10)",
    )
    arch.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Optional cap on the number of evaluated candidates",
    )

    fit = sub.add_parser(
        "fit-config",
        help=(
            "find the best-fitting WAU config for a program: sweep grid shapes up "
            "to a device budget, predict behaviour via the scheduler, and rank the "
            "ones that fit (recommends both a best-performance and a fewest-cores config)"
        ),
    )
    fit_source = fit.add_mutually_exclusive_group(required=True)
    fit_source.add_argument("--config", type=Path, help="Existing WAU JSON config (the workload)")
    fit_source.add_argument(
        "--program-file", type=Path, help="A .cw kernel to compile then fit (via compile-cw)"
    )
    fit.add_argument(
        "--device",
        default="intel_de0_nano",
        help="Device preset to fit against (default: intel_de0_nano)",
    )
    fit.add_argument(
        "--max-grid",
        default="2x4",
        help="Largest grid to consider as XxY or XxYxZ (default: 2x4, the DE0-Nano CW ceiling)",
    )
    fit.add_argument(
        "--max-cores",
        type=int,
        default=None,
        help="Optional cap on total cores (default: product of --max-grid)",
    )
    fit.add_argument(
        "--lut-budget",
        type=int,
        default=None,
        help="Estimated LUT budget (default: device preset logic_cells)",
    )
    fit.add_argument(
        "--max-utilization",
        type=float,
        default=0.9,
        help="Max estimated resource utilization a candidate may use (default: 0.9)",
    )
    fit.add_argument(
        "--tolerance",
        type=float,
        default=0.10,
        help="Makespan slack for the efficient/knee pick vs the best (default: 0.10)",
    )
    fit.add_argument(
        "--quick",
        action="store_true",
        help="Only sweep grid shapes at balanced memory + uniform ops (faster)",
    )
    fit.add_argument(
        "--max-in-flight",
        default=None,
        help=(
            "Comma-separated coordinator.max_in_flight depths to co-sweep "
            "(default: auto from the workload's flow count; single-flow "
            "workloads collapse to 1 to reclaim unused in-flight-slot LUTs)"
        ),
    )
    fit.add_argument(
        "--base-config",
        type=Path,
        default=Path("src/python/configs/wau_cw_fit_base.json"),
        help="Base config used to compile a --program-file (default: wau_cw_fit_base.json)",
    )
    fit.add_argument("--flow-id", type=int, default=90, help="Flow id for --program-file compile")
    fit.add_argument("--lane-parallelism", type=int, default=None, help="Optional lane override for compile")
    fit.add_argument(
        "--out-report", type=Path, default=None, help="Optional machine-readable JSON report path"
    )
    fit.add_argument(
        "--out-summary", type=Path, default=None, help="Optional human-readable summary path"
    )
    fit.add_argument(
        "--out-config",
        type=Path,
        default=None,
        help="Optional path to write the recommended concrete, buildable config",
    )
    fit.add_argument(
        "--emit",
        choices=["best", "efficient"],
        default="efficient",
        help="Which recommendation to write to --out-config (default: efficient)",
    )
    fit.add_argument(
        "--top", type=int, default=12, help="Number of ranked candidates to print (default: 12)"
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
            f"grid={config.device.grid_x}x{config.device.grid_y}x{config.device.grid_z}"
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


def _parse_entry(entry: str) -> tuple[int, int, int]:
    try:
        parts = [chunk.strip() for chunk in entry.split(",")]
        if len(parts) not in {2, 3}:
            raise ValueError
        x = int(parts[0])
        y = int(parts[1])
        z = int(parts[2]) if len(parts) == 3 else 0
    except Exception as exc:  # noqa: BLE001
        raise BasicCompilerError("entry must be in format x,y or x,y,z with integer values") from exc
    return x, y, z


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
    entry_x, entry_y, entry_z = _parse_entry(entry)
    resolved_name = name or f"expr_flow_{flow_id}"

    flow = merge_expression_into_config(
        base_config_path=base_config,
        out_config_path=out_config,
        expr=expr,
        flow_id=flow_id,
        name=resolved_name,
        entry_x=entry_x,
        entry_y=entry_y,
        entry_z=entry_z,
        replace_existing=replace_existing,
        max_in_flight=max_in_flight,
    )

    print(f"Compiled expression into flow {flow_id} ({resolved_name})")
    print(f"Stages: {len(flow['stages'])}")
    print(f"Wrote merged config: {out_config}")
    return 0


def _run_compile_pseudoc(
    *,
    program: str,
    flow_id: int,
    name: str | None,
    entry: str,
    max_in_flight: int,
    base_config: Path,
    out_config: Path,
    replace_existing: bool,
) -> int:
    entry_x, entry_y, entry_z = _parse_entry(entry)
    resolved_name = name or f"pseudoc_flow_{flow_id}"

    flow = merge_pseudoc_into_config(
        base_config_path=base_config,
        out_config_path=out_config,
        program=program,
        flow_id=flow_id,
        name=resolved_name,
        entry_x=entry_x,
        entry_y=entry_y,
        entry_z=entry_z,
        replace_existing=replace_existing,
        max_in_flight=max_in_flight,
    )

    print(f"Compiled pseudo-C into flow {flow_id} ({resolved_name})")
    print(f"Stages: {len(flow['stages'])}")
    print(f"Wrote merged config: {out_config}")
    return 0


def _run_compile_cw(
    *,
    program: str,
    flow_id: int,
    name: str | None,
    entry: str,
    max_in_flight: int | None,
    dtype: str | None,
    lane_parallelism: int | None,
    placement_policy: str | None,
    lowering_profile: str | None,
    base_config: Path,
    out_config: Path,
    replace_existing: bool,
    program_id: int | None,
    program_name: str | None,
    program_priority: int | None,
    program_replicas: int,
    program_max_parallel_flows: int | None,
    program_load_balance: str | None,
) -> int:
    entry_x, entry_y, entry_z = _parse_entry(entry)
    resolved_name = name or f"cw_flow_{flow_id}"

    flow, upserted_program = merge_cw_into_config(
        base_config_path=base_config,
        out_config_path=out_config,
        program=program,
        flow_id=flow_id,
        name=resolved_name,
        entry_x=entry_x,
        entry_y=entry_y,
        entry_z=entry_z,
        replace_existing=replace_existing,
        max_in_flight=max_in_flight,
        dtype=dtype,
        lane_parallelism=lane_parallelism,
        placement_policy=placement_policy,
        lowering_profile=lowering_profile,
        program_id=program_id,
        program_name=program_name,
        program_priority=program_priority,
        program_replicas=program_replicas,
        program_max_parallel_flows=program_max_parallel_flows,
        program_load_balance=program_load_balance,
    )

    print(f"Compiled .cw program into flow {flow_id} ({resolved_name})")
    print(f"Nodes: {len(flow['nodes'])}")
    print(
        "Program upserted: "
        f"id={upserted_program['id']} name={upserted_program['name']} flows={upserted_program['flows']}"
    )
    print(f"Wrote merged config: {out_config}")
    return 0


def _run_cw_eval(*, program: str, convert: list[str] | None, as_json: bool) -> int:
    import json

    parsed = parse(program)
    interp = Interpreter(parsed)

    if convert is not None:
        expr_src, dtype = convert
        value = interp.eval_expression(expr_src)
        result = interp.convert(value, dtype)
        if as_json:
            print(json.dumps({"expr": expr_src, "dtype": dtype, "result": result}))
        else:
            print(f"{expr_src} -> {dtype} = {result}")
        return 0

    result = interp.run()
    if as_json:
        print(
            json.dumps(
                {
                    "classes": sorted(parsed.classes),
                    "aliases": {k: v.target for k, v in parsed.aliases.items()},
                    "functions": sorted(parsed.functions),
                    "main_result": result,
                    "output": interp.output,
                }
            )
        )
    else:
        for line in interp.output:
            print(line)
        print(f"[cw-eval] main() -> {result}")
    return 0


def _run_cw_lint(*, program: str, compile_template: bool, as_json: bool) -> int:
    import json

    parsed = parse(program)
    pragmas = validate_cw_pragmas(program)
    payload: dict[str, object] = {
        "syntax": "ok",
        "aliases": {name: decl.target for name, decl in sorted(parsed.aliases.items())},
        "classes": sorted(parsed.classes),
        "functions": sorted(parsed.functions),
        "pragmas": pragmas,
        "compile_template": None,
    }

    if compile_template:
        spec, shape = parse_cw_program(program)
        payload["compile_template"] = {
            "kernel_name": spec.kernel_name,
            "kernel_size": spec.kernel_size,
            "tile_h": spec.tile_h,
            "tile_w": spec.tile_w,
            "cin_block": spec.cin_block,
            "cout_block": spec.cout_block,
            "worker_count": spec.worker_count,
            "workload_shape": {
                "h": shape.h,
                "w": shape.w,
                "cin": shape.cin,
                "cout": shape.cout,
            },
        }

    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("[cw-lint] syntax: ok")
        if parsed.classes:
            print(f"[cw-lint] classes: {', '.join(sorted(parsed.classes))}")
        if parsed.functions:
            print(f"[cw-lint] functions: {', '.join(sorted(parsed.functions))}")
        if pragmas:
            parts = [f"{key}={value}" for key, value in sorted(pragmas.items())]
            print(f"[cw-lint] pragmas: {', '.join(parts)}")
        else:
            print("[cw-lint] pragmas: none")
        if compile_template:
            template = payload["compile_template"]
            assert isinstance(template, dict)
            print(
                "[cw-lint] compile-template: "
                f"{template['kernel_name']} "
                f"tile={template['tile_h']}x{template['tile_w']} "
                f"workers={template['worker_count']}"
            )
    return 0


def _run_arch_search(
    *,
    config_path: Path,
    out_report: Path,
    out_summary: Path | None,
    top: int,
    max_candidates: int | None,
) -> int:
    import json

    report = run_arch_search(config_path, max_candidates=max_candidates)

    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(json.dumps(report.to_json(), indent=2) + "\n")

    summary = format_report_text(report, top=top)
    if out_summary is not None:
        out_summary.parent.mkdir(parents=True, exist_ok=True)
        out_summary.write_text(summary + "\n")

    print(summary)
    print(f"Wrote arch-search report: {out_report}")
    if out_summary is not None:
        print(f"Wrote arch-search summary: {out_summary}")
    return 0


def _parse_grid(text: str) -> tuple[int, int, int]:
    try:
        parts = [int(chunk) for chunk in text.lower().split("x")]
    except ValueError as exc:
        raise ArchSearchError(f"--max-grid must be XxY or XxYxZ integers, got '{text}'") from exc
    if len(parts) == 2:
        gx, gy, gz = parts[0], parts[1], 1
    elif len(parts) == 3:
        gx, gy, gz = parts
    else:
        raise ArchSearchError(f"--max-grid must be XxY or XxYxZ, got '{text}'")
    if gx < 1 or gy < 1 or gz < 1:
        raise ArchSearchError("--max-grid dimensions must be >= 1")
    return gx, gy, gz


def _run_fit_config(args) -> int:
    import json
    import tempfile

    from .device_library import get_device_preset

    gx, gy, gz = _parse_grid(args.max_grid)
    max_cores = args.max_cores if args.max_cores is not None else gx * gy * gz
    preset = get_device_preset(args.device)
    lut_budget = args.lut_budget if args.lut_budget is not None else preset.logic_cells

    budget = FitBudget(
        max_grid_x=gx,
        max_grid_y=gy,
        max_grid_z=gz,
        max_cores=max_cores,
        lut_budget=lut_budget,
        max_utilization=args.max_utilization,
    )

    tmp_dir: tempfile.TemporaryDirectory | None = None
    if args.program_file is not None:
        # Compile the .cw into a base config sized to the max grid so lane
        # parallelism is not capped below the largest candidate we consider.
        base_payload = json.loads(args.base_config.read_text())
        base_payload.setdefault("device", {})["preset"] = args.device
        base_payload["device"].setdefault("grid", {})
        base_payload["device"]["grid"] = {"x": gx, "y": gy, "z": gz}
        tmp_dir = tempfile.TemporaryDirectory(prefix="wau_fit_")
        tmp_path = Path(tmp_dir.name)
        staged_base = tmp_path / "fit_base.json"
        staged_base.write_text(json.dumps(base_payload, indent=2) + "\n")
        merged = tmp_path / "fit_workload.json"
        merge_cw_into_config(
            base_config_path=staged_base,
            out_config_path=merged,
            program=args.program_file.read_text(),
            flow_id=args.flow_id,
            name=f"fit_flow_{args.flow_id}",
            entry_x=0,
            entry_y=0,
            replace_existing=True,
            lane_parallelism=args.lane_parallelism,
            program_id=args.flow_id,
        )
        config_path = merged
    else:
        config_path = args.config

    memory_splits = ("balanced",) if args.quick else None
    op_distributions = ("uniform",) if args.quick else None

    max_in_flight_values: tuple[int, ...] | None = None
    if args.max_in_flight is not None:
        try:
            max_in_flight_values = tuple(
                int(v) for v in str(args.max_in_flight).split(",") if v.strip()
            )
        except ValueError:
            print(
                f"Invalid --max-in-flight value: {args.max_in_flight!r} "
                "(expected comma-separated integers)",
                file=sys.stderr,
            )
            return 2
        if not max_in_flight_values:
            max_in_flight_values = None

    kwargs: dict = {"budget": budget, "device_preset": args.device}
    if memory_splits is not None:
        kwargs["memory_splits"] = memory_splits
    if op_distributions is not None:
        kwargs["op_distributions"] = op_distributions
    if max_in_flight_values is not None:
        kwargs["max_in_flight_values"] = max_in_flight_values
    kwargs["tolerance"] = args.tolerance

    try:
        report = run_fit_search(config_path, **kwargs)

        summary = format_fit_report_text(report, top=args.top)
        print(summary)

        if args.out_report is not None:
            args.out_report.parent.mkdir(parents=True, exist_ok=True)
            args.out_report.write_text(json.dumps(report.to_json(), indent=2) + "\n")
            print(f"Wrote fit-config report: {args.out_report}")
        if args.out_summary is not None:
            args.out_summary.parent.mkdir(parents=True, exist_ok=True)
            args.out_summary.write_text(summary + "\n")
            print(f"Wrote fit-config summary: {args.out_summary}")

        chosen_id = report.efficient_id if args.emit == "efficient" else report.best_id
        if args.out_config is not None:
            if chosen_id is None:
                print(
                    "No candidate fits the budget; nothing written to --out-config.",
                    file=sys.stderr,
                )
                return 1
            payload = emit_fit_config(config_path, chosen_id, device_preset=args.device)
            args.out_config.parent.mkdir(parents=True, exist_ok=True)
            args.out_config.write_text(json.dumps(payload, indent=2) + "\n")
            grid = payload.get("device", {}).get("grid", {})
            print(f"Wrote recommended ({args.emit}) config: {args.out_config}")
            print(
                "Build this grid on the DE0-Nano (uses the board's lean base) with:\n"
                f"  demo/de0-nano/basic-example/scripts/build_cw_stress.ps1 "
                f"-GridX {grid.get('x')} -GridY {grid.get('y')}\n"
                "  (or pass -BaseConfig "
                f"{args.out_config} to build the exact simulated architecture)"
            )
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()

    return 0 if report.best_id is not None else 1


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
        if args.command == "compile-pseudoc":
            if args.program is not None:
                program = args.program
            else:
                program = args.program_file.read_text()
            return _run_compile_pseudoc(
                program=program,
                flow_id=args.flow_id,
                name=args.name,
                entry=args.entry,
                max_in_flight=args.max_in_flight,
                base_config=args.base_config,
                out_config=args.out_config,
                replace_existing=args.replace_existing,
            )
        if args.command == "compile-cw":
            if args.program is not None:
                program = args.program
            else:
                program = args.program_file.read_text()
            return _run_compile_cw(
                program=program,
                flow_id=args.flow_id,
                name=args.name,
                entry=args.entry,
                max_in_flight=args.max_in_flight,
                dtype=args.dtype,
                lane_parallelism=args.lane_parallelism,
                placement_policy=args.placement_policy,
                lowering_profile=args.lowering_profile,
                base_config=args.base_config,
                out_config=args.out_config,
                replace_existing=args.replace_existing,
                program_id=args.program_id,
                program_name=args.program_name,
                program_priority=args.program_priority,
                program_replicas=args.program_replicas,
                program_max_parallel_flows=args.program_max_parallel_flows,
                program_load_balance=args.program_load_balance,
            )
        if args.command == "cw-eval":
            program = args.program if args.program is not None else args.program_file.read_text()
            return _run_cw_eval(
                program=program,
                convert=args.convert,
                as_json=args.json,
            )
        if args.command == "cw-lint":
            program = args.program if args.program is not None else args.program_file.read_text()
            return _run_cw_lint(
                program=program,
                compile_template=args.compile_template,
                as_json=args.json,
            )
        if args.command == "fit-config":
            return _run_fit_config(args)
        if args.command == "arch-search":
            return _run_arch_search(
                config_path=args.config,
                out_report=args.out_report,
                out_summary=args.out_summary,
                top=args.top,
                max_candidates=args.max_candidates,
            )
        if args.command == "list-devices":
            return _run_list_devices()
        if args.command == "list-operations":
            return _run_list_operations()
    except CWLangError as exc:
        print(f"CW language error: {exc}", file=sys.stderr)
        return 2
    except ArchSearchError as exc:
        print(f"Arch-search error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except BasicCompilerError as exc:
        print(f"Compiler error: {exc}", file=sys.stderr)
        return 2
    except CWCompilerError as exc:
        print(f"Compiler error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1
