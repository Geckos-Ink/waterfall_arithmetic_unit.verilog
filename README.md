# Waterfall Arithmetic Unit - Verilog implementation
Python-driven generator for a baseline **Waterfall Arithmetic Unit (WAU)** architecture in Verilog/SystemVerilog.

This repository now contains a working foundation for:
- device-aware WAU configuration (real FPGA presets included),
- flow compilation (flow stages -> core assignments with fallback cores),
- DAG/node-based flow compilation with explicit 2D placement directives,
- per-core capability constraints (operations and data types),
- multi-program scheduling with async dependency-aware execution and recurrence support,
- offline scheduling (cycle timeline + encoded schedule words),
- constrained pseudo-C accumulator frontend (`compile-pseudoc`) in addition to expression compilation,
- Verilog emission for coordinator, core/station, ALU, explicit highway routers/links, and top-level grid.

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

Compile a constrained pseudo-C pipeline program into a new flow:

```bash
PYTHONPATH=src/python python3 -m waugen compile-pseudoc \
  --program 'acc = a; acc = acc + b; acc = acc * 3; acc -= b;' \
  --flow-id 31 \
  --name pseudoc_flow \
  --entry 1,1 \
  --base-config src/python/configs/wau_de0_nano_demo.json \
  --out-config src/python/configs/wau_de0_nano_compiled_expr.json
```

Compile an advanced WAU kernel-style `.cw` program into a DAG flow and execution program:

```bash
PYTHONPATH=src/python python3 -m waugen compile-cw \
  --program-file docs/example-pogram.cw \
  --flow-id 90 \
  --name cw_conv2d_residual_reference \
  --entry 0,0 \
  --max-in-flight 4 \
  --base-config src/python/configs/wau_2d_multiprogram_demo.json \
  --out-config src/python/configs/wau_example_pogram_compiled.json \
  --replace-existing \
  --program-id 90 \
  --program-name cw_reference_program \
  --program-priority 3 \
  --program-replicas 2 \
  --program-max-parallel-flows 2 \
  --program-load-balance least_busy
```

## Testing
Run all RTL test cases with `iverilog` (generation + compile + simulation):

```bash
./scripts/run_iverilog_tests.sh
```

This runs:
- `tests/rtl/tb_wau_operation_alu.v` (ALU opcode behavior),
- `tests/rtl/tb_wau_top_demo.v` (end-to-end flow execution via coordinator/highway/core grid),
- `tests/rtl/tb_wau_highway_mesh.v` (neighbor forwarding and backpressure on the router mesh).

Run randomized multi-flow scheduler stress and emit a coverage-style summary:

```bash
./scripts/run_randomized_stress.py --start-seed 2000 --count 25 --report .build/randomized_stress_report.json
```

Run fast end-to-end compile/validate/generate/RTL checks for the `.cw` reference and write a benchmark snapshot:

```bash
./scripts/run_cw_example_benchmark.sh
```

This script updates `benchmarks/example_pogram_benchmark.txt` as the persistent latest-reference log, including:
- compile/validate/generate timing,
- schedule metrics,
- effective CW execution smoke-benchmark latency/results from generated RTL simulation.

Run autotune sweep to search best score (lowest `exec_latency_cycles_avg`, then `makespan_cycles`, then `total_ms`):

```bash
TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh
```

Autotune writes:
- best/latest benchmark log: `benchmarks/example_pogram_benchmark.txt`
- full sweep summary: `benchmarks/example_pogram_tuning_latest.txt`

Manual tuning knobs are available as environment variables:
- `CW_LANE_PARALLELISM` (example: `4`)
- `PROGRAM_REPLICAS` and `PROGRAM_MAX_PARALLEL`
- `CW_MAX_IN_FLIGHT`
- `CW_DTYPE`

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
- `src/python/configs/wau_de0_nano_compiled_pseudoc.json`: example output of `compile-pseudoc`
- `src/python/configs/wau_example_pogram_compiled.json`: example output of `compile-cw`
- `src/python/configs/wau_2d_multiprogram_demo.json`: advanced DAG + multi-program example
- `src/verilog/generated/`: generated output artifacts
- `tests/rtl/`: SystemVerilog/Verilog testbenches
- `tests/python/`: Python unit tests for compiler helpers
- `benchmarks/example_pogram_benchmark.txt`: tracked benchmark/reference metrics for `.cw` flow compilation

## Generated Artifacts
A `generate` run emits:
- `wau_defs.vh`: project/device/operation constants
- `wau_operation_alu.v`: arithmetic opcode execution unit
- `wau_neighbor_forward.v`: directional valid/ready packet forwarding link
- `wau_highway_router.v`: per-core XY router with local/neighbor arbitration
- `wau_highway_mesh.v`: generated 2D router mesh interconnect
- `wau_core_station.v`: per-core station (dispatch, latency control, multi-entry input/result cache)
- `wau_core.v`: core wrapper
- `wau_coordinator.v`: flow orchestrator with runtime adaptive fallback selection and packetized dispatch/result channels
- `<output_module_name>.v` (demo: `wau_top.v`): top-level 2D core grid
- `wau_de0_nano_top.v` (for DE0-NANO preset): board wrapper with clock/reset/IO hookups
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
  - `data_types` (e.g. `["int32", "float16", "float32"]`)
  - `coordinator_mode`, `enable_runtime_auto_adapt`
- `abstraction`
  - `language` (`wau_flow_ir` or `wau_pseudoc`)
  - `version` (integer, currently `1`)
- `operations`
  - library-driven (`library` + `overrides`) and/or `custom`
- `compiler`
  - `routing` (`waterfall`, `serpentine`, `manual`)
  - `allow_adaptive_reroute`, `fallback_radius`, `allow_cycle_recurrence`
  - `core_capabilities`: per-core operation/data type constraints
- `scheduler`
  - `strategy` (`round_robin`, `serial`, or `dependency_aware`)
- `flows`
  - `id`, `name`, `entry`, optional `exit`
  - per-stage: `op`, optional `core`, `fallback_core`, `immediate_b`, `allow_adaptive`, `dtype`
  - per-node (DAG): `id`, `op`, `deps`, `placement` (`core`/`fallback_core`/`candidate_cores`/`fixed`/`directive`), `dtype`, `recurrent`, `max_iterations`
- `programs`
  - `id`, `name`, `flows`, `priority`, `replicas`, `max_parallel_flows`, `load_balance`
  - `allow_async`, `allow_out_of_order`

## CW Syntax Tuning Hint
`compile-cw` supports an optional `.cw` pragma comment for practical tuning:

```c
// @wau lane_parallelism=4
```

CLI `--lane-parallelism` still overrides the pragma when explicitly set.

## Current Hardware Scope
This is a robust **basis**, not final silicon architecture:
- Control-plane dispatch and data-plane results now traverse explicit neighbor-linked highway meshes with valid/ready backpressure.
- Runtime adaptation is implemented as primary/fallback/candidate core selection per node, constrained by per-core capability metadata.
- Compiler and scheduler outputs are designed so an external compiler/scheduler stack can replace or augment coordinator behavior.
- Current pseudo-C frontend targets accumulator-style pipelines (`acc = a; acc = acc <op> ...`) to stay compatible with the present coordinator execution model.

## Next Steps
Recommended follow-ups:
1. add runtime observability counters for highway hops/stalls and cache hit-rate,
2. extend station caching policy with configurable entry count and replacement policy selection,
3. connect randomized stress script into CI and archive reports as artifacts,
4. extend board wrappers with memory-mapped host control/status registers.
