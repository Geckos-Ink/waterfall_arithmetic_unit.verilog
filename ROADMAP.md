# ROADMAP.md
Roadmap for evolving this repository from a WAU generator basis to a production-capable hardware/compiler stack.

## Status Snapshot (2026-04-11)
Completed baseline:
- Python WAU generator pipeline (`config -> compiler -> scheduler -> verilog_emit`).
- Basic expression compiler (`compile-expr`) for left-deep arithmetic chains.
- Constrained pseudo-C accumulator frontend (`compile-pseudoc`) for higher-level pipeline authoring.
- DAG/node-based flows with explicit 2D placement candidates and directives.
- Per-core capability constraints (operation and data-type aware placement).
- Program-level scheduling metadata (priority, replicas, load balance, async policies).
- Recurrence-aware scheduling support for in-device recursive cycles.
- Generated RTL for coordinator/core/station/ALU/top.
- Initial verification with Python unit tests + `iverilog` testbenches.

## Progress Update (2026-05-18)
Implemented this cycle:
- Phase 1 slice: explicit highway routing RTL (`wau_highway_router`, `wau_neighbor_forward`, `wau_highway_mesh`) integrated into generated top-level.
- Phase 1 slice: coordinator migrated to packetized dispatch/result channels over separated control/data planes.
- Phase 5 slice: station cache upgraded from single-entry signature to multi-entry tag/value cache with hit-based reuse.
- Phase 3 slice: added dedicated RTL mesh forwarding/backpressure test and randomized multi-flow scheduler stress test + report script.
- Phase 4 slice: added generated DE0-NANO board wrapper (`wau_de0_nano_top.v`) with clock/reset/IO integration scaffold.
- CW benchmark workflow slice: `run_cw_example_benchmark.sh` now executes compile/validate/generate/RTL execution checks and writes persistent latest reference log.
- CW autotuning slice: benchmark script supports tuning sweeps (`TUNE_MODE=1`) and persists best-run + full sweep summary.
- CW benchmark stability slice: benchmark script supports repeated multi-run mode (`MULTI_RUNS=<N>`) with median/p95 latency statistics and persisted summary log.
- CW regression-gate slice: benchmark script supports regression-check mode (`REGRESSION_CHECK=1`) with configurable latency/makespan/wall-time thresholds.
- CW benchmark observability slice: benchmark logs now emit placement-quality metrics (fallback ratio, per-flow fallback ratio, estimated transfer hops, critical-path tail).
- CW benchmark CI sidecar slice: latest/best/history machine-readable JSON sidecars are persisted for trend analysis.
- CW compiler slice: `.cw` integer constant parsing now accepts typed declarations such as `int32 K = 3;`, keeping the example kernel parser/compiler path coherent with the documented syntax.
- CW syntax slice: `.cw` pragma support for lane tuning (`// @wau lane_parallelism=<N>`), with CLI override precedence.
- CW syntax slice: added structured pragma parsing with deterministic line-located diagnostics; new pragmas `@wau max_in_flight=<N>` and `@wau preferred_dtype=<name>` are now supported with CLI override precedence and parser test coverage.
- CW syntax slice: added structured tuning/placement pragmas `@wau placement_policy=<locality|balance>`, `@wau lowering_profile=<reference|latency_optimized|throughput_optimized>`, `@wau program_priority=<N>`, and `@wau program_load_balance=<least_busy|round_robin>`.
- CW lowering slice: lane parallelism is now capped by declared workers/output-channel block/device capacity, and lowering profiles now emit materially different locality/balance candidate sets instead of only changing lane count.
- CW benchmark observability slice: benchmark logs now emit stress-latency percentiles (`p50`, `p95`) plus bottleneck summaries (`busiest_core`, core issue hotspots, node latency hotspots, dependency hotspots).
- CW autotune slice: default tuning moved from a fixed exhaustive 3-knob sweep to a staged coordinate search across topology, program policy, and scheduler policy knobs to keep search cost bounded while widening the architecture space explored.
- CW benchmark tuning update: staged autotune on 2026-05-17 UTC selected `lane=2`, `placement=balance`, `profile=throughput_optimized`, `priority=4`, `replicas=2`, `max_parallel=1`, `max_in_flight=2`, `load_balance=least_busy`, `scheduler_policy=weighted_fair`, reaching `exec_latency_cycles_avg=68.00`, `exec_latency_cycles_p95=70.00`, `makespan=42`, `fallback_ratio=0.2899`, `estimated_transfer_hops_total=54`.
- CW stability update: a 5-run stability pass on 2026-05-17 UTC held `exec_latency_cycles_median=68.00` and `exec_latency_cycles_p95=68.00` with all 5 runs passing.

## Progress Update (2026-05-22)
Implemented this cycle:
- Track B slice: CW lowering is now capability-aware. `merge_cw_into_config` reads
  `compiler.core_capabilities` from the base config and prunes candidate cores whose
  op/dtype capability set excludes the lowered node's op or dtype, scanning further
  in the grid only when the natural sequence has no compatible core. `cw_hints` now
  reports `capability_filter_active` and `capability_restricted_cores` so autotune
  reports can attribute placement decisions to capability constraints.
- Track E slice: CW execution testbench now validates output values, not timing
  shape alone. The benchmark script computes per-case expected outputs through a
  new `waugen.cw_reference` software model (one pass over the flow's linear stages,
  mirroring the coordinator state machine) and embeds them in the SV testbench so
  `host_out_value !== expected` triggers `$fatal`. Run logs and JSON sidecars now
  carry `expected_value`, `scoreboard_total`, `scoreboard_matches`, and
  `scoreboard_pass_ratio` for CI consumption.
- Re-validated the autotuned config (`lane=2`, `placement=balance`,
  `profile=throughput_optimized`, `priority=4`, `replicas=2`, `max_parallel=1`,
  `max_in_flight=2`, `load_balance=least_busy`, `scheduler_policy=weighted_fair`)
  on 2026-05-22 UTC: `exec_latency_cycles_avg=68.00`, makespan=42, scoreboard
  pass ratio=1.0 over 8 deterministic cases. A 3-run multi-run pass held
  `exec_latency_cycles_median=68.00` / `exec_latency_cycles_p95=68.00`.
- Phase 4 / Track D slice: introduced `wau_host_mmio`, a 32-bit memory-mapped
  control/status register file (CTRL/STATUS/FLOW_ID/IN_A/IN_B/TRIGGER/OUT_FLOW/
  OUT_VAL/HOPS/STALLS/FORWARDS/DELIVRD/CACHE_H/CACHE_L). The DE0-NANO board
  wrapper now goes through MMIO and exposes an `ext_mmio_*` bus, while still
  driving demo packets from KEY[1]/SW[3:0] via an on-chip sequencer. A new
  `tb_wau_host_mmio` testbench covers the register map, scoreboard counters,
  and output-pending semantics.
- Phase 5 slice (station cache): added `compiler.station_cache.{entries,
  replacement_policy}` config knobs (defaults `entries=4`, `policy=fifo`).
  `wau_core_station` now supports a 32-entry-max configurable cache with a
  selectable LRU policy that updates `cache_age` on hit/refill, in addition
  to the existing FIFO round-robin. New `WAU_STATION_CACHE_ENTRIES`/
  `WAU_STATION_CACHE_POLICY_*` macros flow through `wau_defs.vh`.
- Phase 1+3 slice (observability counters): `wau_highway_router` emits
  `hop_count`, `stall_count`, `local_delivered_count`, and `forward_count`
  per node, the mesh aggregates per-router buses, and `wau_top` exposes
  `obs_total_*` totals across both control and data planes plus per-core
  cache hit/lookup counts. Counters are wired into `wau_host_mmio` so host
  software can poll them between benchmark runs.
- Track C / D slice (program tuning + stress): new
  `tests/python/test_program_stress.py` sweeps a 96-cell matrix of
  priority/replicas/max_parallel/load_balance/scheduler_policy combinations,
  plus targeted checks for strict_priority ordering, round-robin coverage,
  station-cache policy switching, and replica monotonicity. The randomized
  stress script also randomises `compiler.station_cache.{entries,
  replacement_policy}` and reports `fallback_instruction_ratio`.
- DevX slice: added `.github/workflows/ci.yml` running python tests +
  randomized stress + iverilog tests + autotuned CW benchmark on every push
  and PR, uploading benchmark logs, scoreboard JSON, randomized-stress JSON,
  and generated RTL as workflow artifacts (30-day retention).

## CW Compiler and Benchmark Next Steps (2026-05-18+)
Goal: move from reference-level CW lowering to deeper, reproducible, performance-oriented compilation and execution validation.

### Essential Goal: Closed-Loop Verilog + Real-Hardware Benchmarking
Scope:
- Make real-time benchmarking an essential workflow, not a developer-only manual tuning activity.
- Add a Verilog-native benchmark/control harness that can continuously inject workloads, capture latency/throughput/utilization counters, and apply updated schedules/programs without requiring full developer-driven restart/rebuild loops between each trial.
- Add an FPGA board execution path with ad hoc runtime remapping so the tuner can retarget program/core placement decisions on real hardware, measure the result immediately, and iterate automatically.
- Add closed-loop tuning for runtime program decisions:
  - flow/core mapping,
  - operation distribution across cores,
  - runtime parallelism / in-flight depth,
  - scheduler/load-balance policy,
  - adaptive/predictive reroute or locality heuristics,
  - local station/cache and in-circuit memory usage vs shared on-chip memory vs external DRAM traffic.
- Add closed-loop architecture exploration for synthesis-time WAU variants targeted at programmable FPGA users:
  - core-grid shape and physical disposition,
  - operation specialization/distribution across the fabric,
  - amount of lightweight vs predictive/adaptive control logic inside the design,
  - BRAM/LUTRAM/register usage per core vs shared memory pools,
  - external DRAM dependence,
  - highway/router path bandwidth and buffering depth,
  - area/fmax/power/performance tradeoffs across device presets.
- Ensure the same benchmark description can drive:
  - RTL simulation,
  - board-level execution on real FPGA hardware,
  - architecture search / synthesis candidate ranking.

Acceptance:
- A developer can launch a benchmark/tuning session that automatically replays workloads, updates mapping/program parameters, and reports ranked improvements without manual trial-and-error between each attempt.
- Real-hardware benchmark runs expose machine-readable counters and score deltas comparable to the Verilog/simulation path.
- Runtime tuning can improve an already loaded design without requiring full FPGA reflashing for each small mapping/schedule change.
- Architecture search produces a ranked synthesis report for FPGA deployments covering performance, resource use, and bandwidth/memory tradeoffs, so users can choose or synthesize the best WAU architecture for their workload.

### Track A: CW Syntax and Grammar Maturity
Scope:
- Formalize a minimal `.cw` grammar contract (token/statement rules) beyond regex-symbol presence checks.
- Add structured pragmas for compilation intent:
  - `@wau lane_parallelism=<N>` (already supported),
  - `@wau max_in_flight=<N>` (supported),
  - `@wau preferred_dtype=<name>` (supported),
  - `@wau placement_policy=<locality|balance>` (supported),
  - `@wau lowering_profile=<reference|latency_optimized|throughput_optimized>` (supported),
  - `@wau program_priority=<N>` (supported),
  - `@wau program_load_balance=<least_busy|round_robin>` (supported).
- Add parse diagnostics with line-aware errors and suggestion text.

Acceptance:
- Invalid syntax/pragma inputs produce deterministic, line-located errors.
- New parser tests cover valid/invalid pragmas and backward compatibility with current `example-pogram.cw`.

### Track B: Deeper CW-to-Flow Lowering
Scope:
- Extend lowering from coarse node templates to phase-aware kernel decomposition:
  - explicit load/compute/store groups,
  - lane fan-out/fan-in nodes with configurable reduction strategy,
  - data-movement vs arithmetic op tagging in `cw_hints`.
- Introduce optional lowering profiles (`reference`, `latency_optimized`, `throughput_optimized`). (supported)
- Add compile-time sanity checks for inferred parallelism vs grid capacity to avoid pathological over-subscription. (supported for worker/COUT/device-capacity caps; still open for capability-aware preflight)
- Add capability-aware candidate pruning during CW lowering so invalid candidate sets are avoided before `validate`. (supported)

Acceptance:
- Lowered graphs remain valid for `validate`/`generate` across at least two device presets.
- `cw_hints` include profile, lane source (pragma/CLI/default), and effective lowering decisions.
- No regressions in existing CW and RTL tests.

### Track C: Scheduler and Placement for CW Workloads
Scope:
- Add CW-aware cost heuristics:
  - lane locality weighting,
  - fallback penalty minimization,
  - memory-path pressure balancing.
- Tune recurrent-node handling to reduce schedule inflation under multiprogram contention.
- Export additional schedule metrics for CW flows:
  - per-flow fallback ratio,
  - estimated transfer-hop count,
  - critical path by node id,
  - busiest-core/core-hotspot summaries.

Acceptance:
- New metrics are emitted in benchmark logs.
- Measurable cycle/makespan reduction on `example-pogram.cw` relative to baseline profile.

### Track D: Benchmarking and Performance Quality Gates
Scope:
- Extend benchmark output with reproducibility metadata:
  - selected tuning profile,
  - compile knobs used,
  - benchmark ranking score.
- Add a shared benchmark descriptor/runtime so the same workload can be executed in:
  - Verilog simulation,
  - closed-loop board benchmarking,
  - synthesis-time architecture search.
- Add multi-run statistics mode (`N` repeated runs, median/p95 latency) for stability checks. (supported)
- Add optional guardrail thresholds:
  - fail benchmark if `exec_latency_cycles_avg` regresses over configurable baseline. (supported)
- Persist and compare historical bests in a machine-readable sidecar (JSON) for CI trend checks. (supported)
- Add a low-noise autotune replay mode that can rerun only the saved winning candidate and stage winners from `example_pogram_tuning_latest.txt`.
- Add persistent hardware-run history and score sidecars so simulator vs real-board vs synthesized-architecture results can be compared over time.

Acceptance:
- `run_cw_example_benchmark.sh` can run in:
  - single-run reference mode,
  - multi-run stability mode,
  - autotune mode,
  - regression-check mode.
- CI-ready output is available for parsing pass/fail and score deltas.
- Real-board benchmark output is normalized enough to compare directly against simulation/reference runs.

### Track E: Execution Correctness Depth
Scope:
- Add CW-specific software reference model for smoke vector validation against RTL output values. (supported via `waugen.cw_reference`; benchmark TB now `$fatal`s on value mismatch and `scoreboard_pass_ratio` is emitted to logs and sidecars)
- Expand execution testbench vectors (signed/zero/mixed stress, boundary values, recurrent stress). (partially supported: wider deterministic stress vector set now in benchmark TB)
- Add deterministic replay command for best/worst tuning runs from summary files.

Acceptance:
- CW execution checks validate both timing and value correctness (not timing only).
- Failing vectors include replay parameters and seed in logs.

### Near-Term Targets
1. Keep best-known `exec_latency_cycles_avg` at or below `68.00` while preserving pass status for all RTL tests and multirun stability. (held at 68.00 on 2026-05-22 with scoreboard pass ratio 1.0 and 3-run stability median/p95=68.00)
2. Add capability-aware CW candidate generation so known-invalid topology combinations fail earlier and less often during autotune. (supported)
3. Add a CW software reference/value scoreboard so benchmark execution checks cover correctness, not timing shape alone. (supported)
4. Stand up a first closed-loop FPGA benchmark path that can re-run workloads and remap programs on real hardware without full restart between each tuning attempt.
5. Stand up a first architecture-search report that ranks at least core disposition, operation distribution, on-chip memory split, and external-DRAM usage for synthesis candidates.

## Guiding Priorities
1. Keep generator, compiler, scheduler, and emitted RTL behavior consistent.
2. Improve correctness and observability before aggressive optimization.
3. Treat closed-loop measurement on Verilog and real FPGA hardware as essential for performance work; do not rely only on manual developer trial-and-error.
4. Preserve device portability (board wrappers stay separate from core WAU logic).

## Phase 1 (Required): Architecture Hardening
Goal: move from a coordinator-mediated basis to a real WAU interconnect model.

Scope:
- Implement explicit horizontal/vertical highway/router modules.
- Add neighbor-to-neighbor packet transfer protocol (valid/ready + backpressure).
- Separate control-plane messages (programming, interrupts/opcodes) from data-plane packets.

Acceptance:
- At least one generated config emits highway modules and compiles with `iverilog`.
- End-to-end test proves packet progression core -> neighbor/highway -> core.
- No regressions in existing demo tests.

## Phase 2 (Required): Compiler and Scheduler Maturity
Goal: compile real arithmetic DAGs while minimizing data bouncing.

Scope:
- Extend `basic_compiler.py` from left-deep chains to general DAG expressions.
- Add flow partitioning and placement heuristics (core affinity, locality, congestion).
- Add cost model in scheduler for latency, utilization, and transfer hops.
- Emit richer schedule metadata for runtime debugging and replay.

Acceptance:
- Compiler supports multi-branch expressions and common sub-expression reuse.
- Scheduler metrics are exported (makespan, utilization %, transfer count).
- New regression vectors verify deterministic output across multiple seeds/configs.

## Phase 3 (Required): Verification Infrastructure
Goal: raise confidence for hardware and toolchain changes.

Scope:
- Add directed and randomized tests for coordinator, station, and routing logic.
- Add scoreboard-based end-to-end checks comparing RTL vs software reference model.
- Add CI workflow for Python tests + `iverilog` simulations.

Acceptance:
- CI runs on every push/PR and blocks failing checks.
- Coverage-oriented test matrix across at least 3 device presets.
- Reproducible failing seeds are printed and replayable.

## Phase 4 (Required): Device Integration and Runtime Interface
Goal: make WAU usable by external host software.

Scope:
- Define stable host/Coordinator interface (inject, retrieve, status, interrupts).
- Provide board wrappers (starting with DE0-NANO) and memory-mapped control registers. (supported: `wau_host_mmio` + DE0-NANO wrapper now goes through it)
- Add runtime protocol docs and example host driver stubs.
- Add runtime benchmark/control hooks so host software can update program/schedule mappings and collect live performance counters during board execution. (partially supported: obs_total_hop/stall/forward/delivered + cache hit/lookup counters exposed via MMIO; live mapping update still TBD)

Acceptance:
- Board-specific top integrates generated WAU module cleanly.
- Host can load flow program + schedule and execute sample workloads.
- Host can run iterative benchmark/tuning sessions without manual restart-heavy workflows for each mapping attempt.
- Documentation includes register map, timing expectations, and benchmark/control protocol expectations.

## Phase 5 (Possible): Performance and Efficiency Optimization
Goal: improve throughput, power behavior, and scalability.

Possible work:
- Multi-entry station input/result caching and reuse policy. (supported: configurable entries + FIFO/LRU policy via `compiler.station_cache`)
- Deeper pipelining for expensive operations (`mul/div/mod`).
- Flow batching and multi-packet in-flight orchestration.
- Adaptive reroute policy beyond primary/fallback (N candidates).
- Quantitative area/timing analysis scripts per device preset.
- Automatic design-space exploration for FPGA synthesis candidates:
  - core disposition / grid reshaping,
  - operation specialization balance,
  - predictive/adaptive logic budget,
  - BRAM/LUTRAM/shared-memory partitioning,
  - external DRAM reliance,
  - highway path bandwidth/buffering tradeoffs.

Success indicators:
- Better throughput and/or lower cycle count on benchmark flows.
- Bounded impact on LUT/FF/BRAM usage for target devices.
- Ranked architecture candidates can be selected based on measured board/runtime behavior plus synthesis metrics, not ad hoc manual guessing.

## Phase 6 (Possible): Ecosystem and Developer UX
Goal: make the project easier to use and extend.

Possible work:
- Config schema versioning and migration tool.
- YAML/TOML frontend for configs with JSON export.
- Visual flow graph export (Graphviz/Draw.io generation).
- Plugin-style operation packs and device packs.
- Prebuilt benchmark suite and result dashboard.

## Cross-Cutting Technical Debt
- Keep generated files reproducible and avoid manual RTL drift.
- Improve error messages in CLI/compiler for invalid flows/expressions.
- Add strict typing and linting gate for Python source.
- Keep AGENTS/README/ROADMAP aligned when architecture evolves.

## Suggested Milestone Order
1. Phase 1 + Phase 3 (minimum reliability and architecture shape).
2. Phase 2 (compiler/scheduler power-up with robust tests).
3. Phase 4 (real host/device usability).
4. Phase 5 and 6 as optimization/productivity tracks.

## Reference todos
- Supporting 3D layering is important not only for chips those effectively supports three dimensions of circuits, but may be very useful also on classic 2D circuits for creating very fast lane for major combinations of paths, even if with a more confusing and condensed synthetized circuit.