# Waterfall Arithmetic Unit - Verilog implementation

The **Waterfall Arithmetic Unit (WAU)** is a configurable arithmetic compute fabric for FPGAs: a 2D or layered-3D grid of small ALU cores wired together by a packet-switched mesh, designed to stream pipelines of math operations (add, multiply, max, FMA, ...) from a host program. Think of it as a tiny, generator-driven dataflow accelerator you can drop onto a real board.

This repository is the **toolchain that builds one**. You describe your kernel in a high-level form — an arithmetic expression, a constrained pseudo-C snippet, or a `.cw` program — and the Python generator emits the full Verilog (cores, mesh, coordinator, host MMIO), a compiled schedule, and a software reference model used as a correctness oracle. No hand-written RTL, no separate compiler stack.

It is **silicon-verified**: the same flow has been taken end-to-end onto a Terasic DE0-Nano (Intel Cyclone IV E), where 795/795 random and corner-case operand pairs, 150/150 real Iris samples, 1032/1032 `CWs/example-program.cw` stress cases, and 1552/1552 `CWs/stress/mesh_stress.cw` triggers (1032 random + 520 real MNIST pixels) round-tripped through the live mesh and matched the software reference (see [DE0-Nano demo](#de0-nano-real-silicon-implementation)).

Typical uses: experimenting with small FPGA-side math accelerators, teaching dataflow / NoC concepts on real silicon, or as a reusable reference for the "high-level kernel → generated RTL → working bitstream" path.

![](https://github.com/Geckos-Ink/waterfall_arithmetic_unit.verilog/blob/main/tools/wau-pipelines-viewer/examples/wau_3x3_demo.gif?raw=true)

*Above: the [wau-pipelines-viewer](tools/wau-pipelines-viewer) replaying a real
iverilog simulation of a 3x3 WAU under a randomized multi-flow stress stream.
Each cycle plays as a slow-motion scene: operand packets travel hop-by-hop from
the coordinator, the applied operation flashes on the core, and result data
flows back over the highway — with a HUD tracking busy cores, packets in flight,
and peak parallel operations. Each row of cores has its **own
[highway](#highway-topology)** — the default topology gives one independent
highway per line, drawn as the rail beneath that row, ending at its own
coordinator `hub` on the left. Every rail carries its own
[contracting bus](#highway-contracting-bus) with its own slot numbering and its
own marker, so the three arbitrate in parallel rather than in turn. Watch a
core's stub go dashed when it wants its highway, amber on the cycle it calls
from its own slot, and solid red while it holds that highway under a contract —
and note that a contract on one row never stops another row moving. The viewer can also compile a
`.cw` program or a config into a fresh ad-hoc circuit itself (`--config`/`--cw`
+ `--stress N`).*

### Real data in elaboration — MNIST through the mesh

![](https://github.com/Geckos-Ink/waterfall_arithmetic_unit.verilog/blob/main/tools/wau-pipelines-viewer/examples/wau_mnist_mesh_stress.gif?raw=true)

*Above: the same viewer replaying an iverilog simulation of
[`CWs/stress/mesh_stress.cw`](CWs/stress/mesh_stress.cw) — the 47-node
Conv2D + bias + residual + ReLU kernel lowered onto a 4x2 grid — driven by
**real MNIST pixels** instead of random operands. Operand pairs are streamed
from consecutive bytes of `t10k-images-idx3-ubyte` (test image `7`, a `9`,
from offset `5888`) and centered to `[-128, 127]` exactly as the DE0-Nano
stress runner does, so the animation moves the same values that were checked
bit-exact on silicon in the
[live MNIST board run](#ad-hoc-mesh-stress-kernel--live-board-run-2026-07-07).
The recording covers **one whole elaboration, uncut** — all 198 cycles from
the first operand packet leaving the coordinator to the finished result being
handed back to the host — so no flow animation is truncated mid-flight: you
can follow a single MNIST pixel pair the whole way through the 47-node DAG,
across both independent highways, and watch the run end with core `#7`
holding its highway under a burst contract while the HUD reads `258` mesh
hops and `0` stalls. Reproduce it with:*

```bash
python3 scripts/fetch_dataset.py   # once: writes git-ignored datasets/mnist/

cd tools/wau-pipelines-viewer && python3 -m wau_viewer \
  --cw ../../CWs/stress/mesh_stress.cw \
  --base-config examples/wau_mnist_demo_base.json \
  --mnist-images ../../datasets/mnist/t10k-images-idx3-ubyte.gz \
  --mnist-count 4 --mnist-offset 5888 \
  --record examples/wau_mnist_mesh_stress.gif \
  --framerate 10 --frames-per-cycle 3 --gif-width 1200 \
  --record-max-cycles 198 --headless
```

Drop `--record`/`--headless` to drive the same MNIST run interactively.

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
- selectable [highway topology](#highway-topology) via `device.highway.topology`: **one independent highway per line of cores by default** (3-port routers, per-line coordinator hubs, index-compare routing, no per-port divider) so rows carry traffic in parallel, with a single-highway `chain` and the full `matrix` mesh available opt-in,
- a [highway contracting bus](#highway-contracting-bus) (`device.highway.contract_bus`) that offers one core slot per clock and lets a core answer with either a bare request bit or a contract stating how, how much and how many times it intends to transmit — taking the highway exclusively for that transfer instead of re-arbitrating per beat, bounded by a beat count and a hard lease,
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
  --program-file CWs/example-program.cw \
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
  --program-file CWs/samples/types/fixed_point.cw

# Ask the compiler to convert an expression to a dtype via the class's
# conversion magic methods (__convert__ / __to_float__ / __to_int__).
PYTHONPATH=src/python python3 -m waugen cw-eval \
  --program-file CWs/samples/types/fixed_point.cw \
  --convert 'new q8_8(384)' float32        # -> 1.5
```

Validate `.cw` syntax and `@wau` pragmas without lowering:

```bash
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file CWs/samples/types/fixed_point.cw

# Add the current compile-cw template check for RTL-lowered kernels.
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file CWs/example-program.cw \
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

### Find the best-fitting config for your program

A small board fits only so many cores (the DE0-Nano's EP4CE22, ~20k LEs, tops
out around a 2x4 grid for the heavier CW workloads). `fit-config` answers
"what's the best WAU I can actually synthesize for *this* program, and how few
cores do I really need?" — it sweeps every grid shape up to a device budget,
predicts each one's behaviour with the real scheduler (the simulator), and
recommends both a best-performance config and an efficient/knee config (the
fewest cores still within a small makespan tolerance of the best):

```bash
# From a .cw kernel (compiled on the fly) ...
PYTHONPATH=src/python python3 -m waugen fit-config \
  --program-file CWs/stress/mesh_stress.cw \
  --device intel_de0_nano --max-grid 2x4 \
  --out-report .build/fit/report.json \
  --out-config .build/fit/best.json --emit efficient

# ... or the friendly wrapper (also accepts an existing .json workload):
python3 scripts/find_best_wau_config.py CWs/stress/mesh_stress.cw
```

It prints a ranked table plus the exact `build_cw_stress.ps1` command for the
recommended grid, and writes a ready-to-build config. The fit-only `profiled`
distribution derives the exact operations dispatched to each core, and the RTL
emitter makes those capabilities structural so synthesis can remove unused ALU
components. `--candidate-id <id>` emits an exact evaluated candidate for a
physical grid sweep. `fit-config` is additive; `arch-search` remains unchanged.
Use `--quick` for a grid-only sweep, `--lut-budget` /
`--max-utilization` / `--tolerance` to tune the envelope.

Alongside grid shape, op distribution, and memory split, `fit-config` also
co-sweeps `coordinator.max_in_flight`: every in-flight slot costs LUTs, but the
depth only buys makespan when independent flows overlap, so the finder tries the
workload-appropriate depths (a single-flow program collapses to `1`, reclaiming
those LUTs; multi-flow workloads try `1`, powers of two, and the flow-count
ceiling) and the ranker keeps the cheapest depth that doesn't cost makespan.
Override the depths with `--max-in-flight 1,2,4`; the swept set is reported as
`max_in_flight_swept` and encoded in each candidate id as a `_mif<N>` suffix.

### Datasets for data-exchange testing

Real operand streams (instead of random data) make the mesh/JTAG throughput
numbers representative. `scripts/fetch_dataset.py` downloads MNIST on demand
into a git-ignored `datasets/` directory (skip-if-present, verified):

```bash
python3 scripts/fetch_dataset.py            # -> datasets/mnist/*.gz (~11 MB)
```

The DE0-Nano CW stress runner can then stream real pixels as operands with
`--mnist-images datasets/mnist/t10k-images-idx3-ubyte.gz`.

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
  - `config.py`: JSON schema parsing + validation (includes `device.highway`, `compiler.station_cache` and `compiler.core_capabilities`)
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
- `src/python/configs/wau_matrix_highway_demo.json`: the `wau_de0_nano_demo` flows with `device.highway.topology = "matrix"`; CI elaborates the fabric suite against it so the opt-in mesh path stays exercised
- `src/python/configs/wau_cw_fit_base.json`: minimal DE0-Nano base used by `fit-config` (compiling a raw `.cw`) and by `run_cw_stress_benchmark.sh`
- `src/python/configs/wau_de0_nano_example_2x4_profiled.json`: exact reproducible config for the Quartus Lite 25.1 profiled 2x4 silicon benchmark
- `CWs/`: all real `.cw` programs — `example-program.cw` (compiler-oriented Conv2D reference), `stress/mesh_stress.cw` (ad-hoc mesh/hardware-stress kernel), `basic_arithmetic.cw`, and `samples/{nn,types}/*.cw`
- `datasets/`: git-ignored, populated on demand by `scripts/fetch_dataset.py` (MNIST)
- `src/verilog/generated/`: generated output artifacts
- `tests/rtl/`: SystemVerilog/Verilog testbenches (ALU, top demo, highway mesh + hop counters, per-line highway independence, chain routing, contracting bus, MMIO register file)
- `tests/python/`: Python unit tests for compiler helpers, CW reference scoreboard, and program-level priority/replicas/policy stress matrix
- `scripts/run_randomized_stress.py`: randomized multi-flow stress (CI input)
- `scripts/run_iverilog_tests.sh`: iverilog test runner
- `scripts/run_cw_example_benchmark.sh`: CW kernel benchmark, autotune, saved-candidate replay, multi-run stability, regression check
- `scripts/run_cw_stress_benchmark.sh`: same engine pointed at `CWs/stress/mesh_stress.cw` (own tracked log; never touches the example benchmark)
- `scripts/find_best_wau_config.py`: convenience wrapper over `waugen fit-config` (best/efficient config for a program)
- `scripts/fetch_dataset.py` / `.ps1`: on-demand git-ignored dataset download (MNIST) for data-exchange testing
- `.github/workflows/ci.yml`: CI matrix (python tests, randomized stress, iverilog tests, autotuned CW benchmark) with artifact uploads
- `benchmarks/example_pogram_benchmark.txt`: tracked benchmark/reference metrics for `.cw` flow compilation
- `benchmarks/example_pogram_tuning_latest.txt`: latest autotune sweep summary
- `benchmarks/example_pogram_replay_latest.txt`: latest saved-candidate replay comparison
- `benchmarks/example_pogram_multirun_latest.txt`: latest multi-run stability summary
- `benchmarks/example_pogram_benchmark_latest.json`: machine-readable latest benchmark snapshot
- `benchmarks/example_pogram_benchmark_best.json`: machine-readable best-known benchmark snapshot
- `benchmarks/example_pogram_benchmark_history.json`: benchmark history for trend checks
- `benchmarks/mesh_stress_benchmark.txt`: tracked simulator benchmark for the ad-hoc `CWs/stress/mesh_stress.cw` kernel (heavier 47-node flow; `scoreboard_pass_ratio=1.0`)
- `benchmarks/de0_nano_basic_benchmark.txt`: silicon-verified reference run on the DE0-Nano (resource fit, per-corner Fmax, 795/795 scoreboard pass, live observability counters)
- `benchmarks/de0_nano_iris_stats_benchmark.txt`: real-data DE0-Nano benchmark for the 2D WAU using 150 Iris samples, live board measurements, and tracked JSON sidecars
- `benchmarks/de0_nano_cw_stress_benchmark.txt`: live DE0-Nano benchmark for `CWs/example-program.cw`, including the historical failed 2x4 and repaired profiled 2x4 runs
- `benchmarks/de0_nano_cw_stress_benchmark_latest.json`: machine-readable passing 2x2 stress run (`1032/1032`)
- `benchmarks/de0_nano_cw_stress_2x4_timeout.json`: machine-readable failure capture for the largest fitting (`2x4`) image
- `benchmarks/de0_nano_cw_stress_2x4_profiled_20260713.json`: machine-readable passing profiled 2x4 run (`1032/1032`)
- `benchmarks/de0_nano_mesh_stress_benchmark.txt`: live DE0-Nano run of the ad-hoc `CWs/stress/mesh_stress.cw` kernel at 2x2 (`1032/1032` random + `520/520` MNIST, ~1.7x the example's per-case mesh traffic), with `*_random.json` / `*_mnist.json` sidecars
- `demo/de0-nano/basic-example/`: end-to-end physical deployment — Quartus 25.1 project + reusable vJTAG MMIO bridge RTL + reusable Python/TCL host stack + automation scripts; produces the artifact above

## Generated Artifacts
A `generate` run emits:
- `wau_defs.vh`: project/device/operation constants (also `WAU_STATION_CACHE_ENTRIES`, `WAU_STATION_CACHE_POLICY_{FIFO,LRU}`, `WAU_HIGHWAY_TOPOLOGY_{LINES,CHAIN,MATRIX}`, `WAU_HIGHWAY_PORT_COUNT`, `WAU_HIGHWAY_LINE_{COUNT,SIZE}`, and the `WAU_HIGHWAY_CONTRACT_*` field/limit macros)
- `wau_operation_alu.v`: arithmetic opcode execution unit
- `wau_neighbor_forward.v`: directional valid/ready packet forwarding link
- `wau_highway_contract.v`: the highway contracting bus — cycling slot offer, request/contract acceptance, exclusive grant with beat and lease bounds, grant/hold/defer counters
- `wau_highway_router.v`: per-core router with local/neighbor arbitration, plus 32-bit `hop_count`/`stall_count`/`local_delivered_count`/`forward_count` observability counters. Its port set follows `device.highway.topology`: `local`/`prev`/`next` for the default per-line highway, the same plus `up`/`down` for `chain`, and `north`/`south`/`east`/`west`(+`up`/`down`) for `matrix`
- `wau_highway_mesh.v`: generated highway interconnect — one index-order chain per layer by default, the full neighbour mesh under `matrix` — plus the contract bus and the per-router counter buses
- `wau_core_station.v`: per-core station (dispatch, latency control, configurable FIFO/LRU multi-entry input/result cache, `cache_hit_count`/`cache_lookup_count`)
- `wau_core.v`: core wrapper
- `wau_coordinator.v`: flow orchestrator with runtime adaptive fallback selection and packetized dispatch/result channels
- `wau_host_mmio.v`: 32-bit memory-mapped host control/status register file with observability counter readback
- `<output_module_name>.v` (demo: `wau_top.v`): top-level 2D or layered-3D core grid, exporting `obs_total_hop_count`/`stall_count`/`forward_count`/`local_delivered_count`/`cache_hit_count`/`cache_lookup_count` plus `obs_total_contract_grant_count`/`hold_cycles`/`defer_count`, and carrying the schedule-derived per-core highway contract words
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
  - `highway`: the highway fabric's shape and its contracting bus
    - `topology` (`lines` | `chain` | `matrix`, default **`lines`**): how the highway is laid out over the grid — see [Highway topology](#highway-topology).
    - `contract_bus` (bool, default `true`): emit the per-highway contracting bus (`wau_highway_contract`) on the data-plane highway — see [Highway contracting bus](#highway-contracting-bus).
    - `contract_max_burst` (int `[1,255]`, default `8`): the largest run of beats a single contract may reserve. Also clamps the schedule-derived per-core contract words.
    - `contract_lease_cycles` (int `[1,65535]`, default `64`): hard upper bound on how long one contract may own the highway, so a holder that goes quiet can never wedge it.
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

## Highway topology

`device.highway.topology` chooses how the highway is laid out over the core
grid. All three keep the highway *one-dimensional per highway*; they differ in
how many highways there are and how those reach the coordinator. The default is
deliberately the lightest arrangement that still parallelises.

**`lines` (default) — one highway per line of cores.** A `grid.x × grid.y` grid
gets `grid.y` **independent** highways, one per row of `grid.x` cores; a layered
grid gets that set per layer (`grid.y * grid.z` in total). Each is a
self-contained `PREV`/`NEXT` run whose west end opens onto **its own coordinator
hub**, so routers keep just `LOCAL`/`PREV`/`NEXT` — 3 ports instead of 7 — and
`route_dir` reduces to asking whether the destination lies further along *this*
line, against elaboration-time constants.

The point of the arrangement is that the lines are genuinely independent:

- **Rows move in parallel.** Row 0's traffic shares neither wires, nor
  back-pressure, nor arbitration with row 1's. Blocking one line's hub leaves
  every other line running — [`tb_wau_highway_lines`](tests/rtl/tb_wau_highway_lines.v)
  asserts exactly that, because it is what distinguishes this topology from a
  single shared highway.
- **Every line arbitrates on its own.** The contracting bus is instantiated per
  line, so a contract taken out on one row cannot hold off another.
- **It is materially cheaper.** Three router ports mean a much smaller crossbar
  per core, and a line of `N` cores needs `N-1` links with no row-to-row joints
  at all.
- **It sidesteps the [non-power-of-two LE blow-up](#non-power-of-two-grid-blows-the-le-budget)**
  entirely: with no `dst_core % GRID_X` / `dst_core / GRID_X` in the router,
  there is no `LPM_DIVIDE` to infer per port, whatever the grid shape.

Cores never address each other in the current execution model — all traffic is
coordinator↔core — so per-line hubs cost no reachability. A result is addressed
to a reserved off-line id, walks west along its own line, and leaves through
that line's hub; `wau_top` steers dispatch to the hub owning the destination
core and round-robins the returning lines into the coordinator.

**`chain` — one highway per layer.** Each layer's cores form a *single* 1-D
highway walked in core-index order: core `i` links to `i - 1` and `i + 1`, so the
last core of a row is the previous hop of the first core of the next row. Routers
keep `LOCAL`/`PREV`/`NEXT` plus `UP`/`DOWN` (5 ports), and layers are joined
vertically. Fewest links of the three — but every packet shares one wire, so it
serialises where `lines` would parallelise, and its row-to-row joint is a long
wire in silicon. Opt in when link count matters more than highway throughput.
[`src/python/configs/wau_chain_highway_demo.json`](src/python/configs/wau_chain_highway_demo.json)
is the tracked example.

```json
"device": { "highway": { "topology": "chain" } }
```

**`matrix` — the full mesh.** The original topology: `N`/`S`/`E`/`W` (plus
`U`/`D`) links with X-then-Y-then-Z dimension-order routing, 7 router ports. Opt
in for kernels whose highway traffic actually needs the cross-section.
[`src/python/configs/wau_matrix_highway_demo.json`](src/python/configs/wau_matrix_highway_demo.json)
is the tracked example.

```json
"device": { "highway": { "topology": "matrix" } }
```

CI elaborates the whole fabric suite against all three, so no path ships
unexercised. Every topology keeps the same `wau_highway_mesh` port interface —
per-core `local_*`, per-line `hub_*`, per-line contract bus — so `wau_top`, the
testbenches and the viewer are written once; under `chain`/`matrix` the hub
ports are simply inert and the coordinator keeps using core 0's local port.

## Highway contracting bus

A highway is a shared medium, and `wau_highway_contract` is how cores negotiate
for it. It offers **one core slot per clock**, cycling through the grid. On its
own offered slot a core may answer in one of two ways:

- with a bare **request bit** (a "pong") — it wants one beat, reserves nothing;
- with a full **contract word** describing how it intends to use the highway.

The contract word is 18 bits, `{repeats[7:0], words[7:0], mode[1:0]}`:

| Field | Meaning | Question it answers |
|---|---|---|
| `mode` | `0` pong, `1` burst, `2` stream, `3` reserve | *how* |
| `words` | beats in one run, clamped to `contract_max_burst` | *how much* |
| `repeats` | how many runs the core expects | *how many times* |

While a contract is in force the highway admits **only its holder**: the
transfer runs to completion without interleaving with another core's traffic,
and without the core re-arbitrating for its slot on every beat. When no contract
is active the highway is wide open — an idle bus adds no admission latency, so
the contract bus costs nothing until it is actually used.

Every contract is bounded twice, by its beat count *and* by
`contract_lease_cycles`, and a holder that stops presenting traffic releases
immediately. The round-robin then resumes *after* the holder, so a core cannot
starve the others by re-contracting.

Both sides of "program expectations and real-time requests" are wired:

- the **real-time** side is `data_contract_req`, which a core raises the moment
  it has a result to move (`core_result_valid`);
- the **program** side is a per-core contract word derived from the offline
  schedule and emitted into `wau_top` — `words` is the longest run of
  instructions the core executes for a single flow, `repeats` the number of
  distinct flows placed on it. Cores with no scheduled work get an inert `pong`.

The bus is instantiated on the **data-plane** highway only: the control plane
has a single injector (the coordinator), so there is nothing to arbitrate.
Its counters are aggregated into `obs_total_contract_grant_count` /
`_hold_cycles` / `_defer_count` and readable at MMIO `0x18`–`0x1A`.

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
| `0x18` | `CTR_GRNT`| R    | `obs_total_contract_grant_count` (highway contracts granted)              |
| `0x19` | `CTR_HOLD`| R    | `obs_total_contract_hold_cycles` (cycles a contract owned the highway)    |
| `0x1A` | `CTR_DEFR`| R    | `obs_total_contract_defer_count` (core-cycles held off by a contract)     |

`0x18`–`0x1A` were added with the [highway contracting bus](#highway-contracting-bus);
every previously published address keeps its meaning, so existing host software
is unaffected.

The same counters are also available as direct ports on `wau_top` for
non-MMIO integrations.

## Continuous Integration
`.github/workflows/ci.yml` runs on every push and PR:
- `python-tests`: full `unittest` discovery on `tests/python` (compiler, scheduler, CW frontends, CW reference scoreboard, program-stress matrix).
- `randomized-stress`: 50-seed sweep of `scripts/run_randomized_stress.py` with JSON report artifact.
- `iverilog-tests`: installs Icarus Verilog and runs `scripts/run_iverilog_tests.sh` (uploads generated RTL as artifact).
- `cw-benchmark`: runs `scripts/run_cw_example_benchmark.sh` with the autotuned knobs and `scripts/run_cw_stress_benchmark.sh` for the ad-hoc mesh-stress kernel, surfaces both summaries into the GitHub Step Summary, and uploads `benchmarks/*` plus `cw_scoreboard.json` as artifacts (30-day retention).

## Current Hardware Scope
This is a robust **basis**, not final silicon architecture:
- Control-plane dispatch and data-plane results now traverse explicit neighbor-linked highway meshes with valid/ready backpressure.
- `grid.z > 1` emits layered 3D core indexing and vertical `up/down` mesh links; this path is currently verified with `iverilog` (`tb_wau_highway_mesh_3d`) and has not yet been calibrated on the DE0-NANO board flow.
- The historical 2026-07-06 generic 2x4 image fit at 99% but violated timing and timed out. On 2026-07-13, Quartus Lite 25.1 built a program-profiled 2x4 image with a /16 timing-safe WAU clock: `21,478 / 22,320` LEs (96%), `12 / 132` multiplier elements, positive setup slack at every reported corner, and `1032/1032` live scoreboard pass at `95.4` ops/s. Watchdog expiry is now a fail-fast circuit/configuration fault, never a throughput sample. Quartus still reports a combinational router loop, so registered/elastic router links remain required before restoring a fast mesh clock.
- The DE0-Nano's 32 MB external SDRAM is currently held inactive and is not counted as WAU cache. Active station caches remain on-chip register structures (1..32 entries per core); an SDRAM controller/cache hierarchy remains future work.
- On 2026-07-07 the ad-hoc `CWs/stress/mesh_stress.cw` kernel was synthesized (Quartus Lite 23.1; Standard 25.1's eval is still expired) and run at 2x2 — a 27-node flow at `10,268 / 22,320` LEs (46%) passing `1032/1032` random and `520/520` real-MNIST triggers at ~1.7x the example's per-case mesh traffic and zero stalls. A build-script bug (`$ErrorActionPreference=Stop` turning Quartus 25.1's benign `TBBmalloc` stderr into a fatal error) was fixed so 25.1 runs can proceed once licensed.
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
[`CWs/example-program.cw`](CWs/example-program.cw) kernel rather than the
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
| `2x4` generic (2026-07-06) | fit, fails live | `22,166 / 22,320` logic elements (`99%`); timing-violating image, `0/8` golden |
| `2x4` profiled (2026-07-13) | **fit, passes live** | Quartus Lite 25.1, `21,478 / 22,320` LEs (`96%`), `12 / 132` multipliers; `1032/1032` pass |
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

The repaired profiled `2x4` image runs the WAU/JTAG domain at 3.125 MHz until
elastic router timing cuts are implemented. TimeQuest reports +62.108 ns WAU
setup slack at slow 85 C; the full live run passed `1032/1032` at `95.4` ops/s
with `82,560` hops, zero stalls, `47,472` forwards, and `35,088` deliveries.
The older timeout remains tracked as failure telemetry, not a benchmark score.

### Ad-hoc mesh-stress kernel — live board run (2026-07-07)
The ad-hoc [`CWs/stress/mesh_stress.cw`](CWs/stress/mesh_stress.cw) kernel — built
to *saturate the mesh* rather than stress the compiler — was then synthesized and
run on the same board. Lowered at 2x2 with `lane_parallelism=4` it becomes a
**27-node** `add`/`mul`/`max` flow (vs the example's 17), so it drives markedly
more mesh traffic per trigger. The full report is in
[`benchmarks/de0_nano_mesh_stress_benchmark.txt`](benchmarks/de0_nano_mesh_stress_benchmark.txt).

Toolchain note: the requested Quartus **Standard 25.1** (`C:\altera_standard\25.1std`)
still refuses to compile — `Error (292037): Your 30-day evaluation period has
expired` — so synthesis used the free **Quartus Lite 23.1**, the repo's documented
working fallback. A real build-script bug was fixed en route: `build_cw_stress.ps1`
/ `program.ps1` / `server.ps1` ran under `$ErrorActionPreference = "Stop"`, so
Quartus 25.1's harmless `TBBmalloc` stderr line at startup was promoted to a
terminating error that aborted the build *before compilation*; the native Quartus
calls now relax the preference and check the real exit code.

Resource fit was essentially identical to the example image
(`10,268 / 22,320` LEs, 46%; `24 / 132` multipliers) — lowering the kernel richer
costs no extra area, since the hardware is sized by grid/ops/coordinator, not flow
length. CLOCK_50 setup slack is still negative (`-7.671 ns` fast-0C,
`-23.336 ns` slow-0C), so this is again empirical room-temperature validation.

| Run | Cases | Pass | Throughput | Hops (per case) | Station cache |
|-----|------:|:----:|-----------:|----------------:|--------------:|
| random `[-1023,1023]`, seed `1592594996` | 1032 | **1032/1032** | 81.5 op/s | 123,840 (**~120**) | 490 / 27,864 (1.8%) |
| **real data** — MNIST pixels | 520 | **520/520** | 79.8 op/s | 62,400 (~120) | 3,436 / 14,040 (**24.5%**) |

Two findings worth calling out:
- **~1.7x heavier mesh traffic.** The example image drove ~70 hops/case; this
  27-node flow drives ~120 hops/case at the same footprint and **zero stalls**,
  bit-exact across all 1,552 checked triggers.
- **Real data changes the cache story.** With random operands the station cache
  hits only 1.8% of the time; streaming real MNIST pixels
  (`scripts/fetch_dataset.py` → `--mnist-images`) lifts the hit rate to **24.5%**
  on the same hardware, because image data is spatially correlated. This is the
  data-exchange-efficiency signal the dataset path was added to expose.

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

<!--
> ⚠️ **IMPORTANT NOTICE: UPCOMING LICENSE CHANGE** ⚠️
> 
> Currently, this project is distributed under the PolyForm Noncommercial License 1.0.0. Please be advised that in a future release, the licensing terms will change. The new license will strictly prohibit the use of this software, directly or indirectly, by Italian law enforcement agencies, if not for educational purposes. 
> 
> Users who require compliance with standard Open Source Initiative (OSI) definitions should plan accordingly for future versions.
-->
