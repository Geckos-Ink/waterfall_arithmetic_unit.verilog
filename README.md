# Waterfall Arithmetic Unit - Verilog implementation

The **Waterfall Arithmetic Unit (WAU)** is a configurable arithmetic compute fabric for FPGAs: a 2D or layered-3D grid of small ALU cores wired together by a packet-switched mesh, designed to stream pipelines of math operations (add, multiply, max, FMA, ...) from a host program. Think of it as a tiny, generator-driven dataflow accelerator you can drop onto a real board.

This repository is the **toolchain that builds one**. You describe your kernel in a high-level form — an arithmetic expression, a constrained pseudo-C snippet, or a `.cw` program — and the Python generator emits the full Verilog (cores, mesh, coordinator, host MMIO), a compiled schedule, and a software reference model used as a correctness oracle. No hand-written RTL, no separate compiler stack.

It is **silicon-verified**: the same flow has been taken end-to-end onto a Terasic DE0-Nano (Intel Cyclone IV E), where 795/795 random and corner-case operand pairs, 150/150 real Iris samples, and 1032/1032 `docs/example-program.cw` stress cases round-tripped through the live mesh and matched the software reference (see [DE0-Nano demo](#de0-nano-real-silicon-implementation)).

Typical uses: experimenting with small FPGA-side math accelerators, teaching dataflow / NoC concepts on real silicon, or as a reusable reference for the "high-level kernel → generated RTL → working bitstream" path.

![](https://github.com/Geckos-Ink/waterfall_arithmetic_unit.verilog/blob/main/tools/wau-pipelines-viewer/examples/wau_3x3_demo.gif?raw=true)

This repository now contains a working foundation for:
- device-aware WAU configuration (real FPGA presets included),
- flow compilation (flow stages -> core assignments with fallback cores),
- DAG/node-based flow compilation with explicit 2D/3D placement directives,
- per-core capability constraints (operations and data types), with capability-aware CW lowering that prunes incompatible candidate cores before validation,
- multi-program scheduling with async dependency-aware execution and recurrence support,
- offline scheduling (cycle timeline + encoded schedule words),
- routing-aware (locality-weighted) core selection via `scheduler.locality_bias` (default off): biases candidate cores toward their dependencies' placed cores to cut transfer hops without inflating makespan/latency,
- constrained pseudo-C accumulator frontend (`compile-pseudoc`) and kernel-style `.cw` frontend (`compile-cw`) in addition to expression compilation,
- a real `.cw` language front-end (`cw-lint`/`cw-eval`: lexer → AST → host-side interpreter) with **classes and magic methods** for compile-time type handling — operator overloading and type-conversion hooks (`__to_float__`/`__to_int__`/`__convert__`) the compiler can invoke to bridge precisions dynamically,
- CW software reference model + benchmark value scoreboard (`scoreboard_pass_ratio` gate on top of latency/makespan),
- Verilog emission for a multi-issue coordinator (keeps up to `coordinator.max_in_flight` distinct flows executing concurrently across the core mesh, so independent flows actually overlap on different cores at runtime), core/station, ALU, explicit highway routers/links, top-level grid, and a memory-mapped host control/status register file (`wau_host_mmio`),
- reusable generated-project assembly through `thirds/veribuilder`, an externalizable Python package for parameterized Verilog project manifests, feature-gated files, simple templates, headers, and deterministic file emission,
- configurable station cache size and replacement policy (FIFO/LRU) via `compiler.station_cache`,
- runtime observability counters for highway hops/stalls/forwards/local-deliveries and per-core cache hit/lookup rate, aggregated at top-level and exposed via MMIO,
- CI matrix (python tests + randomized stress + iverilog tests + autotuned CW benchmark) with artifact archival.

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
  --program-file docs/example-program.cw \
  --flow-id 90 \
  --name cw_conv2d_residual_reference \
  --entry 0,0 \
  --max-in-flight 2 \
  --lane-parallelism 2 \
  --placement-policy balance \
  --lowering-profile throughput_optimized \
  --base-config src/python/configs/wau_2d_multiprogram_demo.json \
  --out-config src/python/configs/wau_example_pogram_compiled.json \
  --replace-existing \
  --program-id 90 \
  --program-name cw_reference_program \
  --program-priority 4 \
  --program-replicas 2 \
  --program-max-parallel-flows 1 \
  --program-load-balance least_busy
```

Execute a `.cw` program on the host (real parser + interpreter), including
classes with magic methods used for compile-time type conversion. This path is
separate from `compile-cw` (it does not lower to RTL); it is for compiler-side
behaviour that should not run on the WAU, such as custom numeric formats and
their conversions:

```bash
# Run main() and print its output + return value.
PYTHONPATH=src/python python3 -m waugen cw-eval \
  --program-file docs/samples/types/fixed_point.cw

# Ask the compiler to convert an expression to a dtype via the class's
# conversion magic methods (__convert__ / __to_float__ / __to_int__).
PYTHONPATH=src/python python3 -m waugen cw-eval \
  --program-file docs/samples/types/fixed_point.cw \
  --convert 'new q8_8(384)' float32        # -> 1.5
```

Validate `.cw` syntax and `@wau` pragmas without lowering:

```bash
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file docs/samples/types/fixed_point.cw

# Add the current compile-cw template check for RTL-lowered kernels.
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file docs/example-program.cw \
  --compile-template
```

`.cw` classes (declared with `class` or the legacy `space` keyword) support
Python-style magic methods: `__init__`, the arithmetic/comparison operators
(`__add__`, `__sub__`, `__mul__`, `__div__`, `__mod__`, `__eq__`, `__lt__`, …),
`__neg__`, conversion hooks (`__to_int__`, `__to_float__`, and the generic
`__convert__(target_dtype)`), and `__str__`. `a + b` on a class instance calls
`a.__add__(b)`; a builtin cast like `float32(x)` on an instance dispatches to its
conversion hook, and the same dispatch is exposed to the toolchain through
`waugen.cw_lang.Interpreter.convert(value, dtype)`.

The accepted host-side `.cw` grammar, pragma contract, and the narrower
`compile-cw` RTL-template requirements are documented in
[`docs/cw-language.md`](docs/cw-language.md).

Rank synthesis-time architecture candidates for a workload config (2D/3D core
disposition/grid shape, heavy-op specialization via core capabilities,
on-chip memory split, and external-DRAM reliance):

```bash
PYTHONPATH=src/python python3 -m waugen arch-search \
  --config src/python/configs/wau_example_pogram_compiled.json \
  --out-report .build/arch_search/report.json \
  --out-summary .build/arch_search/summary.txt \
  --top 10
```

Every candidate runs through the real `compile_project -> build_schedule`
pipeline, so makespan/transfer-hop/fallback numbers are the generator's own;
area/BRAM/DSP figures come from the versioned `wau_resource_model_v1`
estimator checked against the device preset's datasheet capacity, and DRAM
traffic from `dram_model_v1`. Ranking is `arch_search_rank_v1`: feasible
first, then lower makespan, transfer hops, DRAM bytes, peak utilization.

## Testing
Run all RTL test cases with `iverilog` (generation + compile + simulation):

```bash
./scripts/run_iverilog_tests.sh
```

This runs:
- `tests/rtl/tb_wau_operation_alu.v` (ALU opcode behavior),
- `tests/rtl/tb_wau_top_demo.v` (end-to-end flow execution via coordinator/highway/core grid),
- `tests/rtl/tb_wau_highway_mesh.v` (neighbor forwarding, backpressure, and `router_hop_count` advancement),
- `tests/rtl/tb_wau_highway_mesh_3d.v` (vertical `up/down` routing across `grid.z` layers),
- `tests/rtl/tb_wau_host_mmio.v` (MMIO register map: writes, reads, output_pending sticky semantics, observability counter readback).

Run the python unit-test suite (compiler/scheduler/CW frontends/program stress matrix/CW reference scoreboard):

```bash
PYTHONPATH=src/python python3 -m unittest discover -s tests/python -p "test_*.py" -v
```

Run randomized multi-flow scheduler stress (also sweeps `compiler.station_cache.{entries, replacement_policy}`) and emit a coverage-style summary:

```bash
PYTHONPATH=src/python python3 scripts/run_randomized_stress.py --start-seed 2000 --count 25 --report .build/randomized_stress_report.json
```

Run fast end-to-end compile/validate/generate/RTL checks for the `.cw` reference and write a benchmark snapshot:

```bash
./scripts/run_cw_example_benchmark.sh
```

This script updates `benchmarks/example_pogram_benchmark.txt` as the persistent latest-reference log, including:
- compile/validate/generate timing,
- schedule metrics,
- effective CW execution stress-benchmark latency/results from generated RTL simulation,
- per-case `expected_value` and `scoreboard=match|...` lines plus aggregate `scoreboard_total`, `scoreboard_matches`, `scoreboard_pass_ratio`,
- stress latency percentiles (`p50`, `p95`),
- placement-quality metrics (`fallback_instruction_ratio`, per-flow fallback ratio, true dependency-edge estimated transfer hops, critical-path tail),
- bottleneck summaries (`busiest_core`, core hotspots, node latency hotspots, dependency hotspots),
- reproducibility profile metadata and benchmark ranking score.

The testbench `$fatal`s on any value mismatch against the software reference in
`waugen.cw_reference`, so the scoreboard is a hard correctness gate on top of
the latency/makespan targets. The reference is also exposed as
`.build/cw_iverilog/cw_scoreboard.json` for downstream tooling.

Latest tuned result as of 2026-06-14 UTC (retaining the staged autotune winner
and re-validating deterministic scheduling, capability-aware CW lowering,
configurable station cache, and the value scoreboard):
- selected tuning point: `lane=2`, `placement=balance`, `profile=throughput_optimized`, `priority=4`, `replicas=2`, `max_parallel=1`, `max_in_flight=2`, `load_balance=least_busy`, `scheduler_policy=weighted_fair`
- `exec_latency_cycles_avg=68.00`, `exec_latency_cycles_p95=70.00`, `makespan_cycles=42`
- `fallback_instruction_ratio=0.3043` (21/69) and
  `dependency_edges_v1=104` hops across 105 true data-dependency edges
- 3-run stability check: `median=68.00`, `p95=68.00`, `3/3` passing
- scoreboard: `8/8` deterministic cases match the software reference (`scoreboard_pass_ratio=1.0`)

Run autotune sweep to search best score (lowest `exec_latency_cycles_avg`, then `makespan_cycles`, then `total_ms`):

```bash
TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh
```

Autotune writes:
- best/latest benchmark log: `benchmarks/example_pogram_benchmark.txt`
- full sweep summary: `benchmarks/example_pogram_tuning_latest.txt`
- JSON sidecars:
  - `benchmarks/example_pogram_benchmark_latest.json`
  - `benchmarks/example_pogram_benchmark_best.json`
  - `benchmarks/example_pogram_benchmark_history.json`

Default autotune now uses a staged coordinate search rather than one flat exhaustive grid:
- topology stage: `lane_parallelism`, `placement_policy`, `lowering_profile`
- program stage: `replicas`, `max_parallel_flows`, `priority`, `max_in_flight`
- scheduler stage: `load_balance`, `scheduler.program_policy`

Replay saved autotune candidates without rerunning the full sweep:

```bash
REPLAY_MODE=best-and-stage-winners ./scripts/run_cw_example_benchmark.sh
```

Supported modes are `best`, `stage-winners`, `best-and-stage-winners`, and
`worst`. `REPLAY_SUMMARY_FILE` selects the source summary; replay uses isolated
configs/build directories and writes
`benchmarks/example_pogram_replay_latest.txt` without replacing the canonical
benchmark sidecars. The report compares saved and current latency/makespan and
labels both hop-metric versions so historical proxy values are not treated as
directly comparable to `dependency_edges_v1`.

Run stability mode with repeated samples (`median` and `p95` latency summary):

```bash
MULTI_RUNS=5 ./scripts/run_cw_example_benchmark.sh
```

This writes:
- `benchmarks/example_pogram_benchmark.txt` (best sample with appended stability section),
- `benchmarks/example_pogram_multirun_latest.txt` (full multi-run summary).

Run regression-guard mode against the best sidecar baseline:

```bash
REGRESSION_CHECK=1 ./scripts/run_cw_example_benchmark.sh
```

Useful guardrail knobs:
- `REGRESSION_MAX_LATENCY_DELTA` (default `0.00`)
- `REGRESSION_MAX_MAKESPAN_DELTA` (default `0`)
- `REGRESSION_MAX_TOTAL_MS_DELTA` (default `250`)
- `REGRESSION_BASELINE_JSON` (default `benchmarks/example_pogram_benchmark_best.json`)

Manual tuning knobs are available as environment variables:
- `CW_LANE_PARALLELISM` (example: `4`)
- `CW_PLACEMENT_POLICY` (`locality` or `balance`)
- `CW_LOWERING_PROFILE` (`reference`, `latency_optimized`, `throughput_optimized`)
- `PROGRAM_REPLICAS` and `PROGRAM_MAX_PARALLEL`
- `PROGRAM_PRIORITY` and `PROGRAM_LOAD_BALANCE`
- `SCHEDULER_PROGRAM_POLICY`
- `CW_MAX_IN_FLIGHT`
- `CW_DTYPE`
- `RUN_PROFILE` (tag run intent in benchmark metadata)

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
  - `config.py`: JSON schema parsing + validation (includes `compiler.station_cache` and `compiler.core_capabilities`)
  - `device_library.py`: real device presets
  - `operation_library.py`: built-in operation templates
  - `basic_compiler.py`: basic high-level expression compiler to WAU flow stages
  - `cw_compiler.py`: `.cw` kernel-style lowering with capability-aware candidate pruning
  - `benchmark_replay.py`: saved autotune summary parser and replay-plan selection
  - `cw_reference.py`: software reference model for CW flows (drives the value scoreboard)
  - `compiler.py`: flow-to-core compilation with adaptive fallbacks
  - `scheduler.py`: offline schedule timeline + 64-bit word encoding
  - `verilog_emit.py`: WAU-specific RTL + report renderers (router/cache observability counters and the `wau_host_mmio` register file live here); generated-project assembly is delegated to `thirds/veribuilder`
  - `cli.py`: CLI entrypoint
- `thirds/veribuilder/`: standalone-ready Python package for dynamic Verilog project construction
  - `src/veribuilder/core.py`: `VerilogProject`, `GeneratedFile`, `VerilogHeader`, and `TemplateRenderer`
  - `pyproject.toml`: package metadata for publishing or installing separately
- `src/python/configs/wau_de0_nano_demo.json`: example configuration
- `src/python/configs/wau_de0_nano_compiled_expr.json`: example output of `compile-expr`
- `src/python/configs/wau_de0_nano_compiled_pseudoc.json`: example output of `compile-pseudoc`
- `src/python/configs/wau_example_pogram_compiled.json`: example output of `compile-cw`
- `src/python/configs/wau_2d_multiprogram_demo.json`: advanced DAG + multi-program example
- `src/python/configs/wau_3d_demo.json`: minimal layered-3D example using `grid.z` and vertical core placement
- `src/verilog/generated/`: generated output artifacts
- `tests/rtl/`: SystemVerilog/Verilog testbenches (ALU, top demo, highway mesh + hop counters, MMIO register file)
- `tests/python/`: Python unit tests for compiler helpers, CW reference scoreboard, and program-level priority/replicas/policy stress matrix
- `scripts/run_randomized_stress.py`: randomized multi-flow stress (CI input)
- `scripts/run_iverilog_tests.sh`: iverilog test runner
- `scripts/run_cw_example_benchmark.sh`: CW kernel benchmark, autotune, saved-candidate replay, multi-run stability, regression check
- `.github/workflows/ci.yml`: CI matrix (python tests, randomized stress, iverilog tests, autotuned CW benchmark) with artifact uploads
- `benchmarks/example_pogram_benchmark.txt`: tracked benchmark/reference metrics for `.cw` flow compilation
- `benchmarks/example_pogram_tuning_latest.txt`: latest autotune sweep summary
- `benchmarks/example_pogram_replay_latest.txt`: latest saved-candidate replay comparison
- `benchmarks/example_pogram_multirun_latest.txt`: latest multi-run stability summary
- `benchmarks/example_pogram_benchmark_latest.json`: machine-readable latest benchmark snapshot
- `benchmarks/example_pogram_benchmark_best.json`: machine-readable best-known benchmark snapshot
- `benchmarks/example_pogram_benchmark_history.json`: benchmark history for trend checks
- `benchmarks/de0_nano_basic_benchmark.txt`: silicon-verified reference run on the DE0-Nano (resource fit, per-corner Fmax, 795/795 scoreboard pass, live observability counters)
- `benchmarks/de0_nano_iris_stats_benchmark.txt`: real-data DE0-Nano benchmark for the 2D WAU using 150 Iris samples, live board measurements, and tracked JSON sidecars
- `benchmarks/de0_nano_cw_stress_benchmark.txt`: live DE0-Nano benchmark for `docs/example-program.cw`, including the passing 2x2 image and the measured larger-grid failure ceiling
- `benchmarks/de0_nano_cw_stress_benchmark_latest.json`: machine-readable passing 2x2 stress run (`1032/1032`)
- `benchmarks/de0_nano_cw_stress_2x4_timeout.json`: machine-readable failure capture for the largest fitting (`2x4`) image
- `demo/de0-nano/basic-example/`: end-to-end physical deployment — Quartus 25.1 project + reusable vJTAG MMIO bridge RTL + reusable Python/TCL host stack + automation scripts; produces the artifact above

## Generated Artifacts
A `generate` run emits:
- `wau_defs.vh`: project/device/operation constants (now also `WAU_STATION_CACHE_ENTRIES` and `WAU_STATION_CACHE_POLICY_{FIFO,LRU}`)
- `wau_operation_alu.v`: arithmetic opcode execution unit
- `wau_neighbor_forward.v`: directional valid/ready packet forwarding link
- `wau_highway_router.v`: per-core XYZ router with local/neighbor arbitration, plus 32-bit `hop_count`/`stall_count`/`local_delivered_count`/`forward_count` observability counters
- `wau_highway_mesh.v`: generated router mesh interconnect with north/south/east/west plus optional up/down layer links, exposing per-router counter buses
- `wau_core_station.v`: per-core station (dispatch, latency control, configurable FIFO/LRU multi-entry input/result cache, `cache_hit_count`/`cache_lookup_count`)
- `wau_core.v`: core wrapper
- `wau_coordinator.v`: flow orchestrator with runtime adaptive fallback selection and packetized dispatch/result channels
- `wau_host_mmio.v`: 32-bit memory-mapped host control/status register file with observability counter readback
- `<output_module_name>.v` (demo: `wau_top.v`): top-level 2D or layered-3D core grid, exporting `obs_total_hop_count`/`stall_count`/`forward_count`/`local_delivered_count`/`cache_hit_count`/`cache_lookup_count`
- `wau_de0_nano_top.v` (for DE0-NANO preset): board wrapper that instantiates `wau_host_mmio` for external Avalon-MM-style hosts and emulates writes from KEY[1]/SW[3:0] for stand-alone demos
- `wau_program.json`: compiled flow program
- `wau_schedule.json`: human-readable schedule timeline
- `wau_schedule.hex`: encoded 64-bit schedule words

## Config Model (high level)
Main JSON fields:
- `project`, `output_module_name`
- `device`
  - `preset` (e.g. `intel_de0_nano`, `intel_agilex7_fm`, `xilinx_artix7_100t`)
  - `grid.x`, `grid.y`, optional `grid.z` (default `1`; `z > 1` emits layered 3D core indexing and vertical mesh links)
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
  - `core_capabilities`: per-core operation/data type constraints (also consumed by CW lowering to prune incompatible candidate cores up-front)
  - `station_cache`: `{ "entries": <1..32>, "replacement_policy": "fifo" | "lru" }` (default `entries=4`, `replacement_policy=fifo`)
- `scheduler`
  - `strategy` (`round_robin`, `serial`, or `dependency_aware`)
  - `program_policy` (`weighted_fair`, `strict_priority`, `round_robin`)
  - `locality_bias` (float `>= 0`, default `0.0`): routing-aware core-selection tiebreaker that weights each candidate core by its Manhattan hop distance to the cores holding the node's true data-dependency results. Applied only after the earliest-free-cycle key, so it shrinks transfer hops without inflating makespan/latency; `0.0` disables locality weighting. Scheduler ties use explicit replica/runtime-node keys, so output is stable across Python hash seeds. `wau_schedule.json` exports the matching `dependency_edges_v1` metric name, hop total/count/average, and unresolved-edge count.
- `coordinator`
  - `max_in_flight` (int `[1,16]`, default `4`): hardware capacity of the generated `wau_coordinator` — the number of **distinct** flows it can keep executing concurrently across the core mesh (one accumulator context per slot). Independent flows injected back-to-back overlap on different cores instead of running strictly one-at-a-time. `1` reproduces the legacy serial coordinator. Emitted as `WAU_COORD_MAX_IN_FLIGHT`. Per-flow results are unchanged; a single in-flight flow keeps identical timing.
- `flows`
  - `id`, `name`, `entry`, optional `exit`; coordinates are `x,y` with optional `z` (default `0`)
  - per-stage: `op`, optional `core`, `fallback_core`, `immediate_b`, `allow_adaptive`, `dtype`
  - per-node (DAG): `id`, `op`, `deps`, `placement` (`core`/`fallback_core`/`candidate_cores`/`fixed`/`directive`), `dtype`, `recurrent`, `max_iterations`
- `programs`
  - `id`, `name`, `flows`, `priority`, `replicas`, `max_parallel_flows`, `load_balance`
  - `allow_async`, `allow_out_of_order`

## CW Syntax Tuning Hint
`compile-cw` supports optional `.cw` pragmas for practical tuning:

```c
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32
// @wau placement_policy=locality
// @wau lowering_profile=latency_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy
```

Precedence is:
- explicit CLI flags (`--lane-parallelism`, `--max-in-flight`, `--dtype`) win,
- otherwise pragma values are used,
- otherwise compile defaults apply.

Use `cw-lint --compile-template` as a fast preflight for `.cw` sources intended
for `compile-cw`; use plain `cw-lint` for host-side language programs that are
not meant to lower onto the WAU grid.

## Host MMIO Register Map
`wau_host_mmio` exposes a small 32-bit register file with a simple
`mmio_read`/`mmio_write`/`mmio_address`/`mmio_writedata`/`mmio_readdata` bus that
external host software (Avalon-MM, NIOS-II, on-chip CPU, etc.) can drive. The
DE0-NANO wrapper instantiates it and additionally emulates writes from KEY[1]
plus SW[3:0] for stand-alone board demos.

Word-addressed map:

| Addr | Name      | Access | Meaning                                                                   |
|-----:|-----------|:------:|---------------------------------------------------------------------------|
| `0x00` | `CTRL`    | RW   | `[0]` soft_reset_request (auto-clears), `[1]` enable_auto_adapt           |
| `0x01` | `STATUS`  | R    | `[0]` host_in_ready, `[1]` host_out_valid, `[2]` output_pending (sticky)  |
| `0x02` | `FLOW_ID` | RW   | Flow id used by next `TRIGGER`                                            |
| `0x03` | `IN_A`    | RW   | Operand A latched into the coordinator on `TRIGGER`                       |
| `0x04` | `IN_B`    | RW   | Operand B latched into the coordinator on `TRIGGER`                       |
| `0x05` | `TRIGGER` | W1S  | Any write raises `host_in_valid` until accepted                           |
| `0x10` | `OUT_FLOW`| R    | Last `host_out_flow_id` (reading also clears `output_pending`)            |
| `0x11` | `OUT_VAL` | R    | Last `host_out_value` (reading also clears `output_pending`)              |
| `0x12` | `HOPS`    | R    | `obs_total_hop_count` (sum across control/data router meshes)             |
| `0x13` | `STALLS`  | R    | `obs_total_stall_count`                                                   |
| `0x14` | `FORWARDS`| R    | `obs_total_forward_count` (packets forwarded between neighbors)           |
| `0x15` | `DELIVRD` | R    | `obs_total_local_delivered_count` (packets exiting the mesh locally)      |
| `0x16` | `CACHE_H` | R    | `obs_total_cache_hit_count` (sum across all core stations)                |
| `0x17` | `CACHE_L` | R    | `obs_total_cache_lookup_count`                                            |

The same counters are also available as direct ports on `wau_top` for
non-MMIO integrations.

## Continuous Integration
`.github/workflows/ci.yml` runs on every push and PR:
- `python-tests`: full `unittest` discovery on `tests/python` (compiler, scheduler, CW frontends, CW reference scoreboard, program-stress matrix).
- `randomized-stress`: 50-seed sweep of `scripts/run_randomized_stress.py` with JSON report artifact.
- `iverilog-tests`: installs Icarus Verilog and runs `scripts/run_iverilog_tests.sh` (uploads generated RTL as artifact).
- `cw-benchmark`: runs `scripts/run_cw_example_benchmark.sh` with the autotuned knobs, surfaces a summary into the GitHub Step Summary, and uploads `benchmarks/*` plus `cw_scoreboard.json` as artifacts (30-day retention).

## Current Hardware Scope
This is a robust **basis**, not final silicon architecture:
- Control-plane dispatch and data-plane results now traverse explicit neighbor-linked highway meshes with valid/ready backpressure.
- `grid.z > 1` emits layered 3D core indexing and vertical `up/down` mesh links; this path is currently verified with `iverilog` (`tb_wau_highway_mesh_3d`) and has not yet been calibrated on the DE0-NANO board flow.
- A 2026-07-06 Quartus 23.1std compile of the regenerated 2D DE0-NANO demo fits, was programmed onto the board, and completed both a real-data Iris benchmark (`150/150` per run) and a heavier `docs/example-program.cw` stress benchmark (`1032/1032` at `2x2`). The same session also established the current heavier-workload ceiling on EP4CE22: `2x4` fits at `99%` logic but times out all `8/8` golden cases on hardware, while `4x4` and `2x5` fail fit. TimeQuest still does not close the 50 MHz board clock on either tracked 23.1 image, so treat the board results as empirical room-temperature validation and treat registered/elastic mesh timing cuts or a slower board-clock profile as the next hardware-closing slice.
- Runtime adaptation is implemented as primary/fallback/candidate core selection per node, constrained by per-core capability metadata.
- Compiler and scheduler outputs are designed so an external compiler/scheduler stack can replace or augment coordinator behavior.
- Current pseudo-C frontend targets accumulator-style pipelines (`acc = a; acc = acc <op> ...`) to stay compatible with the present coordinator execution model.

## DE0-Nano Real-Silicon Implementation
The `demo/de0-nano/basic-example/` project is the first end-to-end **physical**
deployment of the WAU on actual FPGA silicon: a Terasic DE0-Nano (Intel
Cyclone IV E EP4CE22F17C6) talking to a Python host over USB-Blaster + Altera
virtual JTAG. It exists as a working reference for everyone who wants to take
the generator's RTL and put it onto a real board.

What the demo bundles:

- **Reusable RTL** — a generic `wau_vjtag_bridge.v` (4-bit IR JTAG↔MMIO master,
  with TCK↔CLOCK_50 CDC done right via toggle-sync + double-FF data crossing)
  and a thin `vJTAG.v` wrapping `sld_virtual_jtag`. Drop them into any Altera
  design that needs a host-driven Avalon-MM-style register file.
- **Reusable host stack** — a layered Python library
  (`waujtag.TCLClient` → `MMIO` → `WAU` → `Bench`) plus a `quartus_stp`-hosted
  TCL line-protocol server. The lower layers know nothing about WAU and can
  drive any compatible bridge.
- **A working Quartus 25.1 project** — pin assignments, SDC, board top wiring
  the WAU `wau_host_mmio` and the bridge, and Make/PowerShell automation that
  goes from JSON config → RTL → `.sof` → programmed board → benchmark report.

### Benchmark results — silicon-verified
Reference run captured 2026-05-24 (see
[`benchmarks/de0_nano_basic_benchmark.txt`](benchmarks/de0_nano_basic_benchmark.txt)
for the full machine-readable snapshot):

| Flow                          | Stages | Reference                 |     n |     Pass | Throughput | p50 / p95 |
|-------------------------------|:------:|---------------------------|------:|:--------:|-----------:|----------:|
| `flow1_accumulate_and_scale`  |   3    | `((a + b) * 3) - b`       |   265 | **265/265** |   85.2 op/s | 15 / 16 ms |
| `flow2_max_then_scale`        |   3    | `(max(a, b) - b) * 2`     |   265 | **265/265** |   90.7 op/s | 15 / 16 ms |
| `flow3_fma_a_b_plus_b`        |   2    | `a * b + b`               |   265 | **265/265** |   92.7 op/s | 15 / 16 ms |
| **Aggregate scoreboard**      |        |                           | **795** | **795/795 (100 %)** |  ~90 op/s |  |

Live router/cache observability deltas confirm the data plane really does
traverse the mesh (not a degenerate short-circuit): `6 890` total hops,
`0` stall events, `4 240` packets locally delivered, `93 / 2 120` station-cache
hits (4.4 % — expected for random operand pairs).

### Real-data statistical workload — live board run (2026-07-06)
The same 2D DE0-Nano path was then used for a deeper, fixed-point statistical
program over real data: flow `4`, `iris_morphology_score`, driven by the
tracked dataset copy at
[`demo/de0-nano/basic-example/host/data/iris_sepal_petal_tenths.csv`](demo/de0-nano/basic-example/host/data/iris_sepal_petal_tenths.csv).
The exact board report lives in
[`benchmarks/de0_nano_iris_stats_benchmark.txt`](benchmarks/de0_nano_iris_stats_benchmark.txt),
with JSON sidecars for both captured runs.

The hardware formula is intentionally simple but nontrivial for the 2D WAU:

```text
score(a, b) = max((((max((((a - 58) * 4 + b - 44) * 3 + 32), 0)) * 2) - 80), 0)
```

where `a` is sepal length in tenths of a centimeter and `b` is petal length in
the same fixed-point scale.

| Run | Rows | Pass | Throughput | p50 / p95 |
|-----|-----:|:----:|-----------:|----------:|
| `run1` | 150 | **150/150** | 80.7 op/s | 15 / 16 ms |
| `run2` | 150 | **150/150** | 79.3 op/s | 16 / 16 ms |
| **Aggregate** | **300** | **300/300 (100%)** | 79.3-80.7 op/s | p95 16 ms |

Per-label output summaries were stable across both board runs:

| Label | Nonzero | Min | p50 | p95 | Max |
|-------|--------:|----:|----:|----:|----:|
| `Iris-setosa` | 0 / 50 | 0 | 0 | 0 | 0 |
| `Iris-versicolor` | 25 / 50 | 0 | 4 | 236 | 290 |
| `Iris-virginica` | 47 / 50 | 0 | 200 | 578 | 608 |

This is not presented as a classifier. It is a compact morphology score used
to exercise a longer arithmetic chain on real measurements while keeping a
precise host-side reference. The useful point is that the live device stayed
bit-exact across all 300 checked samples and the observability counters still
showed real mesh traffic (`5 700` hops, `0` stalls, `2 700` forwards,
`3 000` local deliveries per 150-row run).

### Complex CW workload - live board run (2026-07-06)
The same board wrapper was then pushed with the repository's tracked
[`docs/example-program.cw`](docs/example-program.cw) kernel rather than the
small `basic_arithmetic.cw` demo. The board build path is now tracked in
[`demo/de0-nano/basic-example/scripts/build_cw_stress.ps1`](demo/de0-nano/basic-example/scripts/build_cw_stress.ps1),
and the live scoreboard harness is
[`demo/de0-nano/basic-example/host/programs/run_cw_stress_benchmark.py`](demo/de0-nano/basic-example/host/programs/run_cw_stress_benchmark.py).
The full captured report lives in
[`benchmarks/de0_nano_cw_stress_benchmark.txt`](benchmarks/de0_nano_cw_stress_benchmark.txt).

The passing board image used the tuned CW knobs already proven in `iverilog`
(`lane_parallelism=2`, `max_in_flight=2`, `program_replicas=2`,
`program_max_parallel_flows=1`, `placement=balance`,
`lowering_profile=throughput_optimized`) and lowered into a 17-node
`add`/`mul`/`max` flow on the classic 2D grid.

| Grid | Outcome | Notes |
|------|:-------:|-------|
| `4x4` | no fit | `61,564 / 22,320` logic elements (`276%`), `96 / 132` multipliers |
| `2x5` | no fit | `39,747 / 22,320` logic elements (`178%`), `60 / 132` multipliers |
| `2x4` | fit, fails live | `22,166 / 22,320` logic elements (`99%`); `0/8` golden cases passed, all timed out |
| `2x2` | fit, passes live | `10,372 / 22,320` logic elements (`46%`); `1032/1032` pass (`8` golden + `1024` seeded random) |

The validated `2x2` live run used random inputs in `[-1023, 1023]` with seed
`1592594996` and produced:

| Cases | Pass | Throughput | p50 / p95 |
|------:|:----:|-----------:|----------:|
| `1032` | **1032/1032** | `88.3 op/s` | `15 / 16 ms` |

Observability counters stayed coherent on the passing image:
`72,240` hops, `0` stalls, `37,152` forwards, `35,088` local deliveries,
`37 / 17,544` station-cache hits. Averaged across all 1032 triggers, the
heavier CW flow drove about `70` hops, `36` forwards, and `34` local
deliveries per case.

The failing `2x4` image is useful precisely because it bounds the board:
although it still fits, TimeQuest slack falls to `-167.709 ns` at the slow
0 C setup corner and `-101.518 ns` at the fast 0 C setup corner, and the live
board run times out every golden case. For this workload on EP4CE22, `2x2`
is therefore the largest empirically validated image today.

### Resource & timing
Post-fit on EP4CE22F17C6 (Quartus Standard 25.1, 2×2 grid, int32, 4 ops):

| Metric                          |        Used |    Available |      % |
|---------------------------------|------------:|-------------:|-------:|
| Total logic elements            |       8 248 |       22 320 |   37 % |
| Dedicated logic registers       |       3 652 |       22 320 |   16 % |
| Embedded 9-bit multipliers      |          24 |          132 |   18 % |
| Total memory bits               |           0 |      608 256 |    0 % |
| I/O pins                        |          66 |          154 |   43 % |

Setup timing closes at the Fast corner (+4.06 ns slack) and the empirically
verified room-temperature build runs cleanly at 50 MHz. Per-corner Fmax:
36 MHz @ slow-85 °C, 40 MHz @ slow-0 °C, > 50 MHz @ fast-0 °C — see
section 4 of the benchmark txt for the honest worst-case story.

For the 2026-07-06 Iris benchmark image, the machine-local Quartus Standard
25.1 installation at `C:\altera_standard\25.1std` could not compile because
its evaluation period had expired, so synthesis/programming fell back to
Quartus Lite 23.1 at `C:\intelFPGA_lite\23.1std`. That build still fit
comfortably (`9 616 / 22 320` logic elements, `24 / 132` embedded
multipliers), but TimeQuest reported negative setup slack at both published
corners. The board measurements above are therefore empirical validation of
this exact bitstream on July 6, 2026, not a claim of formal 50 MHz timing
closure.

### Conclusions
- **The WAU works on real silicon.** 795 / 795 random + corner-case operand
  pairs round-tripped through the live mesh and matched the software
  reference, at 4 different signed flows spanning add / sub / mul / max
  across all four cores of the 2×2 grid, with zero stall events recorded.
- **The generator's flow IR → Verilog pipeline is production-faithful.**
  The same Python compiler that produces `wau_program.json` for the
  testbench also produces the bitstream that just passed on hardware,
  without any per-board manual RTL edits.
- **The vJTAG bridge + Python stack are reusable.** They were written
  device-agnostic and the demo deliberately uses them as libraries, so any
  follow-on project (different grid, different ops, different board) only
  has to write its own board-level pin wrapper.
- **Two real architectural issues were uncovered and documented honestly**
  rather than papered over:
  1. `dst_core % GRID_X` in `wau_highway_router.v` infers an `LPM_DIVIDE`
     per router port when GRID_X is not a power of 2. A 3×2 grid blows
     past the EP4CE22 LE budget (26 866 vs 22 320). Power-of-2 grids
     collapse the mod/div to bit-selects and fit with room to spare.
  2. `wau_operation_alu.v` emits a purely combinational signed `div` whose
     32-bit settling time exceeds one 50 MHz period on Cyclone IV E, and
     `wau_core_station.v` latches `alu_out_value` on the first cycle after
     dispatch — so divide results are captured before the divider settles
     and read back as garbage. The benchmark excludes `div` for this
     reason; the upstream fix is to defer the result-latch to
     `wait_cycles == 0` or to swap in a pipelined `LPM_DIVIDE`.
- **Where the throughput goes.** Per-trigger wall-clock latency (~15 ms)
  is dominated by USB-Blaster JTAG round-trip, not by the WAU. The WAU
  itself completes a 2–3 stage flow in well under 20 cycles at 50 MHz
  (< 400 ns). To turn this into a real compute benchmark instead of a
  control benchmark, the natural next step is a host-side burst loader
  that streams many operands through MMIO before draining results — the
  `wau_host_mmio` register file already supports the pattern.

## Next Steps
See `ROADMAP.md` for the full plan. Recommended follow-ups now that observability/MMIO/CI/cache-policy basics are in place:
1. closed-loop on-FPGA benchmarking that pushes new schedules through the MMIO bus without reflashing the bitstream,
2. deepen the `waugen arch-search` reports (first simulation-side slice landed: ranked 2D/3D grid-shape/op-specialization/memory-split/DRAM candidates) with synthesis-tool-calibrated area/fmax numbers and board-measured scores,
3. CW software reference parity across the wider operation set (currently calibrated against add/mul/max paths used by the example kernel).

---

## License
PolyForm Noncommercial License 1.0.0 - Copyright 2026 Riccardo Cecchini <cekkr>
