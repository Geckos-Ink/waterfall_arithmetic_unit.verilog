# waterfall-arithmetic-unit-verilog
Python-driven generator for a baseline **Waterfall Arithmetic Unit (WAU)** architecture in Verilog/SystemVerilog.

This repository now contains a working foundation for:
- device-aware WAU configuration (real FPGA presets included),
- flow compilation (flow stages -> core assignments with fallback cores),
- DAG/node-based flow compilation with explicit 2D placement directives,
- multi-program scheduling with async dependency-aware execution and recurrence support,
- offline scheduling (cycle timeline + encoded schedule words),
- Verilog emission for coordinator, core/station, ALU and top-level grid.

## Quickstart
From repository root:

```bash
PYTHONPATH=src/python python3 -m waugen validate --config src/python/configs/wau_de0_nano_demo.json
PYTHONPATH=src/python python3 -m waugen generate --config src/python/configs/wau_de0_nano_demo.json --out src/verilog/generated --summary
```

Advanced 2D multi-program example (DAG + recurrence + load-balancing directives):

```bash
PYTHONPATH=src/python python3 -m waugen validate --config src/python/configs/wau_2d_multiprogram_demo.json
PYTHONPATH=src/python python3 -m waugen generate --config src/python/configs/wau_2d_multiprogram_demo.json --out src/verilog/generated_2d --summary
```

Compile a basic high-level expression into a new flow and merge it into a config:

```bash
PYTHONPATH=src/python python3 -m waugen compile-expr \
  --expr '((a + b) * 3) - b' \
  --flow-id 30 \
  --name expr_compiled_flow \
  --entry 1,0 \
  --base-config src/python/configs/wau_de0_nano_demo.json \
  --out-config src/python/configs/wau_de0_nano_compiled_expr.json
```

## Testing
Run all RTL test cases with `iverilog` (generation + compile + simulation):

```bash
./scripts/run_iverilog_tests.sh
```

This runs:
- `tests/rtl/tb_wau_operation_alu.v` (ALU opcode behavior),
- `tests/rtl/tb_wau_top_demo.v` (end-to-end flow execution via coordinator/core grid).

Optional direct syntax check of generated RTL:

```bash
iverilog -g2005-sv -I src/verilog/generated -o /tmp/wau_sim \
  src/verilog/generated/wau_operation_alu.v \
  src/verilog/generated/wau_core_station.v \
  src/verilog/generated/wau_core.v \
  src/verilog/generated/wau_coordinator.v \
  src/verilog/generated/wau_top.v
```

## Repository Layout
- `src/python/waugen/`: generator package
  - `config.py`: JSON schema parsing + validation
  - `device_library.py`: real device presets
  - `operation_library.py`: built-in operation templates
  - `basic_compiler.py`: basic high-level expression compiler to WAU flow stages
  - `compiler.py`: flow-to-core compilation with adaptive fallbacks
  - `scheduler.py`: offline schedule timeline + 64-bit word encoding
  - `verilog_emit.py`: RTL + reports emission
  - `cli.py`: CLI entrypoint
- `src/python/configs/wau_de0_nano_demo.json`: example configuration
- `src/python/configs/wau_de0_nano_compiled_expr.json`: example output of `compile-expr`
- `src/python/configs/wau_2d_multiprogram_demo.json`: advanced DAG + multi-program example
- `src/verilog/generated/`: generated output artifacts
- `tests/rtl/`: SystemVerilog/Verilog testbenches
- `tests/python/`: Python unit tests for compiler helpers

## Generated Artifacts
A `generate` run emits:
- `wau_defs.vh`: project/device/operation constants
- `wau_operation_alu.v`: arithmetic opcode execution unit
- `wau_core_station.v`: per-core station (dispatch, latency control, input cache)
- `wau_core.v`: core wrapper
- `wau_coordinator.v`: flow orchestrator with runtime adaptive fallback selection
- `<output_module_name>.v` (demo: `wau_top.v`): top-level 2D core grid
- `wau_program.json`: compiled flow program
- `wau_schedule.json`: human-readable schedule timeline
- `wau_schedule.hex`: encoded 64-bit schedule words

## Config Model (high level)
Main JSON fields:
- `project`, `output_module_name`
- `device`
  - `preset` (e.g. `intel_de0_nano`, `intel_agilex7_fm`, `xilinx_artix7_100t`)
  - `grid.x`, `grid.y`
  - widths/depths (`data_width`, `flow_id_width`, `opcode_width`, `local_ram_depth`, `global_ram_depth`)
  - `coordinator_mode`, `enable_runtime_auto_adapt`
- `operations`
  - library-driven (`library` + `overrides`) and/or `custom`
- `compiler`
  - `routing`, `allow_adaptive_reroute`, `fallback_radius`
- `scheduler`
  - `strategy` (`round_robin` or `serial`)
- `flows`
  - `id`, `name`, `entry`, optional `exit`
  - per-stage: `op`, optional `core`, `fallback_core`, `immediate_b`, `allow_adaptive`
  - per-node (DAG): `id`, `op`, `deps`, `placement` (`core`/`fallback_core`/`candidate_cores`/`fixed`/`directive`), `recurrent`, `max_iterations`
- `programs`
  - `id`, `name`, `flows`, `priority`, `replicas`, `max_parallel_flows`, `load_balance`
  - `allow_async`, `allow_out_of_order`

## Current Hardware Scope
This is a robust **basis**, not final silicon architecture:
- Stage-to-stage transport currently passes through the coordinator (not full highway/router fabric yet).
- Runtime adaptation is implemented as primary/fallback/candidate core selection per node.
- Compiler and scheduler outputs are designed so an external compiler/scheduler stack can replace or augment coordinator behavior.

## Next Steps
Recommended follow-ups:
1. add explicit highway routers + neighbor packet forwarding modules,
2. extend station caching policy (multi-entry or tag-based cache),
3. add randomized multi-flow stress tests and coverage reporting,
4. integrate board-specific wrappers (e.g. DE0-NANO top with clocks/IO).
