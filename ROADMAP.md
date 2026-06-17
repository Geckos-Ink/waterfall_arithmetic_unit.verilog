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

## Progress Update (2026-06-14)
Implemented this cycle:
- Track C slice: aligned the scheduler locality target and benchmark transfer-hop
  metric. Runtime nodes now retain true data-dependency edges separately from
  ordering-only constraints; locality scoring ignores serialization edges, and
  `wau_schedule.json` exports stable runtime-node/dependency keys plus
  dependency-edge Manhattan hop total, edge count, average, and unresolved-edge
  count. The CW benchmark consumes those scheduler metrics directly instead of
  reconstructing a stage-order adjacency proxy. A branched-DAG regression test
  proves the reported value follows fan-in dependencies rather than issue order.
- The metric is explicitly versioned as `dependency_edges_v1`. The prior tuned
  benchmark's value of 54 was produced by the retired stage-adjacency proxy;
  deterministic regeneration of that same 42-cycle/69-instruction config
  reports 104 hops across 105 dependency edges with the new definition.
- Scheduler determinism hardening: replica ties now include explicit
  `program_replica` and runtime-node-key tiebreakers. This removes cross-process
  placement drift caused by Python hash/set iteration order; a subprocess test
  compares complete schedules across multiple `PYTHONHASHSEED` values.
- On `wau_2d_multiprogram_demo`, `locality_bias=1.0` now reduces
  `dependency_edges_v1` from 29 to 23 hops at the same 8-cycle makespan.
- Track D/E replay slice: `run_cw_example_benchmark.sh` now accepts
  `REPLAY_MODE=best|stage-winners|best-and-stage-winners|worst`. A tested
  `waugen.benchmark_replay` parser builds deterministic plans from the saved
  autotune summary, excludes failed candidates, converts saved `auto` knobs
  back to omitted overrides, and replays each candidate in isolated config/build
  paths. The persisted replay report compares expected/current latency,
  makespan, fallback ratio, and hops while labeling old versus current hop
  metric versions.

## Progress Update (2026-06-17)
Implemented this cycle:
- Track A slice: added a documented `.cw` language contract
  (`docs/cw-language.md`) covering lexical rules, top-level declarations, class
  members, statements, expressions, magic methods, WAU pragmas, and the narrower
  RTL-lowering template accepted by `compile-cw`.
- Track A tooling slice: added `waugen cw-lint`, a fast front-end validation
  command that parses `.cw` through `cw_lang`, validates `// @wau` pragma syntax
  and values, and can optionally enforce current `compile-cw` template
  compatibility with `--compile-template`. This gives host-side `.cw` programs
  and RTL-lowered kernels separate preflight paths without wiring `cw_lang.py`
  into RTL lowering.

## Progress Update (2026-06-01)
Implemented this cycle:
- Track A/B slice (real `.cw` front-end with class magic methods): added
  `src/python/waugen/cw_lang.py` — a from-scratch lexer → AST → recursive-descent
  parser → host-side tree-walking interpreter for the `.cw` language, independent
  of the regex/template RTL-lowering path in `cw_compiler.py`. It parses the full
  surface used by the repo's sample programs (verified: `example-program.cw`, the
  three `docs/samples/nn/*.cw`, and `basic_arithmetic.cw` all parse), including
  `class`/`space`, `alias`, `DRAM`, multi-dim arrays, C-style casts, and
  scientific-notation literals.
  - Headline feature: **classes with magic methods** executed at compile time —
    `__init__`, operator overloading (`__add__`/`__sub__`/`__mul__`/`__div__`/
    `__mod__`/`__eq__`/`__ne__`/`__lt__`/`__gt__`/`__le__`/`__ge__`/`__neg__`),
    conversion hooks (`__to_int__`, `__to_float__`, generic `__convert__`), and
    `__str__`. `Interpreter.convert(value, dtype)` is the toolchain hook for
    dynamic type-format conversion ("behaviour that shouldn't run on the WAU").
  - New `cw-eval` CLI subcommand runs `main()` or evaluates `--convert EXPR DTYPE`
    through the class magic methods; new sample `docs/samples/types/fixed_point.cw`
    (a Q8.8 fixed-point type) demonstrates it; `tests/python/test_cw_lang.py`
    covers lexer, parser (all repo samples), interpreter semantics (C integer
    division/÷0, arrays/`.count`, control flow), and the magic-method/conversion
    paths.
  - Follow-ups: connect `cw_lang` conversion magic methods to the lowering path so
    `compile-cw` can use user-defined type promotions during dtype selection;
    optionally execute small `.cw` kernels end-to-end as an additional reference
    oracle alongside `cw_reference.py`.
- Phase 1 / Phase 5 slice (multi-issue coordinator — parallel cores now used at
  runtime): redesigned the generated `wau_coordinator.v` from a strictly serial
  single-`accumulator` state machine (`ST_IDLE → ST_DISPATCH → ST_WAIT_RESULT`,
  one outstanding dispatch) into an N-slot multi-issue coordinator. It now keeps
  up to `coordinator.max_in_flight` **distinct** flows executing concurrently
  across the mesh: a dispatch arbiter issues one packet per cycle (preferring a
  slot whose chosen core is free), while many slots can be awaiting results at
  once, so independent flows overlap on different cores.
  - New `coordinator.max_in_flight` config knob (int `[1,16]`, default `4`)
    flows into a `WAU_COORD_MAX_IN_FLIGHT` macro and the coordinator's
    `MAX_IN_FLIGHT` localparam. `1` reproduces the legacy serial coordinator.
  - Correctness/timing preserved: per-flow semantics are the same linear
    accumulator chain, so a single in-flight flow is **cycle-identical** to the
    old design. `tb_wau_top_demo` finishes at the same time, the CW exec
    scoreboard still passes 1.0 with identical per-case latencies, and the
    unknown-flow-id drop behaviour is retained.
  - Result matching is by `flow_id + stage + src_core`; `host_in_ready` blocks a
    second copy of an already-in-flight flow id, keeping matching unambiguous
    **without** widening the dispatch/result packet format with a tag (no mesh/
    station/core interface change). Widening to a dispatch tag (to allow
    concurrent same-flow-id replicas) is a follow-up.
  - New directed RTL test `tests/rtl/tb_wau_coordinator_multiissue.v` injects two
    independent flows and asserts ≥2 cores are busy in the same cycle (observed:
    2), proving the mesh is used at runtime; new
    `tests/python/test_coordinator_config.py` guards the schema + emission.
- Diagnosis recorded: the emitted `wau_schedule.hex/json` are still artifacts
  only — no RTL consumes them. The multi-issue coordinator re-derives concurrency
  at runtime from in-flight slots rather than replaying the compiled schedule;
  feeding the compiled schedule (per-stage core assignments / ordering) into the
  coordinator is a larger follow-up.

## Progress Update (2026-05-31)
Implemented this cycle:
- Track C / Phase 5 slice (routing efficiency): added a routing-aware,
  locality-weighted core selection path to the scheduler. New
  `scheduler.locality_bias` knob (float `>= 0`, default `0.0`) turns the
  Manhattan distance between a node's data-producing dependency cores and each
  candidate core into a tiebreaker in `_select_core`. The earliest-free cycle
  stays the primary selection key, so locality never trades away
  latency/makespan; it only shrinks data movement among cores that become free
  at the same time. `build_schedule` now tracks the placed core per runtime
  node (`placed_core`) so dependency locations are known at selection time.
  - At introduction, default `0.0` reproduced the then-observed tuned baseline.
    The 2026-06-14 determinism hardening later canonicalized replica ties that
    had previously varied with Python hash order; makespan remains 42.
  - Enabling it cuts the estimated transfer-hop proxy on a contended workload
    (`wau_2d_multiprogram_demo`: 27 -> 22 hops, ~18.5%) at identical makespan.
  - New `tests/python/test_scheduler_locality.py` covers default-off baseline
    equivalence, no-makespan-inflation + hop reduction when enabled, determinism
    for a fixed bias, and rejection of negative bias.
- Historical note: benchmark logs produced before 2026-06-14 used a stage-order
  adjacency proxy. This was replaced by the versioned `dependency_edges_v1`
  metric so the report and scheduler locality target now agree.

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
- Formalize a minimal `.cw` grammar contract (token/statement rules) beyond regex-symbol presence checks. (supported: `cw_lang.py` is a real lexer + recursive-descent parser producing an AST, covering the full surface of the repo's sample `.cw` programs; `docs/cw-language.md` records the written grammar/pragma/template contract; `cw-lint` validates syntax/pragmas and optionally the current `compile-cw` template. Wiring the AST into RTL lowering remains intentionally out of scope under the current ownership boundary.)
- Add structured pragmas for compilation intent:
  - `@wau lane_parallelism=<N>` (already supported),
  - `@wau max_in_flight=<N>` (supported),
  - `@wau preferred_dtype=<name>` (supported),
  - `@wau placement_policy=<locality|balance>` (supported),
  - `@wau lowering_profile=<reference|latency_optimized|throughput_optimized>` (supported),
  - `@wau program_priority=<N>` (supported),
  - `@wau program_load_balance=<least_busy|round_robin>` (supported).
- Add parse diagnostics with line-aware errors and suggestion text. (partially supported: `cw_lang` and pragma/template lint failures include deterministic 1-based line locations where the failing construct is known; suggestion-style recovery remains open.)

Acceptance:
- Invalid syntax/pragma inputs produce deterministic, line-located errors. (supported for parser and pragma lint paths)
- New parser tests cover valid/invalid pragmas and backward compatibility with current `example-program.cw`. (supported, including `cw-lint --compile-template`)

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
  - lane locality weighting (supported: `scheduler.locality_bias` adds
    hop-distance-to-data-dependency weighting as a core-selection tiebreaker,
    and `estimated_transfer_hops_total` is computed from the same true
    dependency edges),
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
- Measurable cycle/makespan reduction on `example-program.cw` relative to baseline profile.

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
- Add a low-noise autotune replay mode that can rerun only the saved winning candidate and stage winners from `example_pogram_tuning_latest.txt`. (supported)
- Add persistent hardware-run history and score sidecars so simulator vs real-board vs synthesized-architecture results can be compared over time.

Acceptance:
- `run_cw_example_benchmark.sh` can run in:
  - single-run reference mode,
  - multi-run stability mode,
  - autotune mode,
  - saved-candidate replay mode,
  - regression-check mode.
- CI-ready output is available for parsing pass/fail and score deltas.
- Real-board benchmark output is normalized enough to compare directly against simulation/reference runs.

### Track E: Execution Correctness Depth
Scope:
- Add CW-specific software reference model for smoke vector validation against RTL output values. (supported via `waugen.cw_reference`; benchmark TB now `$fatal`s on value mismatch and `scoreboard_pass_ratio` is emitted to logs and sidecars)
- Expand execution testbench vectors (signed/zero/mixed stress, boundary values, recurrent stress). (partially supported: wider deterministic stress vector set now in benchmark TB)
- Add deterministic replay command for best/worst tuning runs from summary files. (supported)

Acceptance:
- CW execution checks validate both timing and value correctness (not timing only).
- Failing vectors include replay parameters and seed in logs.

### Near-Term Targets
1. Keep best-known `exec_latency_cycles_avg` at or below `68.00` while preserving pass status for all RTL tests and multirun stability. (held at 68.00 on 2026-06-14 with scoreboard pass ratio 1.0, 3-run stability median/p95=68.00, and saved best/stage-winner replay passing 3/3)
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
- Flow batching and multi-packet in-flight orchestration. (partially supported:
  `wau_coordinator` now keeps `coordinator.max_in_flight` distinct flows in
  flight concurrently; still open: concurrent same-flow-id replicas via a
  dispatch tag, and intra-flow DAG-branch concurrency for non-linear flows.)
- Adaptive reroute policy beyond primary/fallback (N candidates).
- Viewer/observability follow-up (in progress 2026-06-01): the pipelines viewer
  now traces **per-core data-plane deliveries** (`ddeliv=...`: a result packet
  arriving at a core over the data mesh, with src core + value + flow/stage),
  closing the "no data-in-motion trace" gap. `graph_view` renders animated
  rounded-square `DataPacketItem`s — operands easing from the coordinator to the
  compute core, and results easing back over the mesh with an elaboration "pop"
  where they land. `tests/python/test_viewer_data_trace.py` covers the parser
  and a full iverilog-driven capture. Still open: per-router hop-by-hop path
  animation (intermediate routers, not just src→dst endpoints), and overlaying
  the operation applied at each transform; GUI rendering is currently
  unverified in CI (no PySide6 in the headless environment).
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
