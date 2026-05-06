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

## Progress Update (2026-05-06)
Implemented this cycle:
- Phase 1 slice: explicit highway routing RTL (`wau_highway_router`, `wau_neighbor_forward`, `wau_highway_mesh`) integrated into generated top-level.
- Phase 1 slice: coordinator migrated to packetized dispatch/result channels over separated control/data planes.
- Phase 5 slice: station cache upgraded from single-entry signature to multi-entry tag/value cache with hit-based reuse.
- Phase 3 slice: added dedicated RTL mesh forwarding/backpressure test and randomized multi-flow scheduler stress test + report script.
- Phase 4 slice: added generated DE0-NANO board wrapper (`wau_de0_nano_top.v`) with clock/reset/IO integration scaffold.
- CW benchmark workflow slice: `run_cw_example_benchmark.sh` now executes compile/validate/generate/RTL execution checks and writes persistent latest reference log.
- CW autotuning slice: benchmark script supports tuning sweeps (`TUNE_MODE=1`) and persists best-run + full sweep summary.
- CW syntax slice: `.cw` pragma support for lane tuning (`// @wau lane_parallelism=<N>`), with CLI override precedence.

## CW Compiler and Benchmark Next Steps (2026-05-06+)
Goal: move from reference-level CW lowering to deeper, reproducible, performance-oriented compilation and execution validation.

### Track A: CW Syntax and Grammar Maturity
Scope:
- Formalize a minimal `.cw` grammar contract (token/statement rules) beyond regex-symbol presence checks.
- Add structured pragmas for compilation intent:
  - `@wau lane_parallelism=<N>` (already supported),
  - `@wau max_in_flight=<N>`,
  - `@wau preferred_dtype=<name>`,
  - `@wau placement_policy=<locality|balance|manual>`.
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
- Introduce optional lowering profiles (`reference`, `latency_optimized`, `throughput_optimized`).
- Add compile-time sanity checks for inferred parallelism vs grid capacity to avoid pathological over-subscription.

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
  - critical path by node id.

Acceptance:
- New metrics are emitted in benchmark logs.
- Measurable cycle/makespan reduction on `example-pogram.cw` relative to baseline profile.

### Track D: Benchmarking and Performance Quality Gates
Scope:
- Extend benchmark output with reproducibility metadata:
  - selected tuning profile,
  - compile knobs used,
  - benchmark ranking score.
- Add multi-run statistics mode (`N` repeated runs, median/p95 latency) for stability checks.
- Add optional guardrail thresholds:
  - fail benchmark if `exec_latency_cycles_avg` regresses over configurable baseline.
- Persist and compare historical bests in a machine-readable sidecar (JSON) for CI trend checks.

Acceptance:
- `run_cw_example_benchmark.sh` can run in:
  - single-run reference mode,
  - autotune mode,
  - regression-check mode.
- CI-ready output is available for parsing pass/fail and score deltas.

### Track E: Execution Correctness Depth
Scope:
- Add CW-specific software reference model for smoke vector validation against RTL output values.
- Expand execution testbench vectors (signed/zero/mixed stress, boundary values, recurrent stress).
- Add deterministic replay command for best/worst tuning runs from summary files.

Acceptance:
- CW execution checks validate both timing and value correctness (not timing only).
- Failing vectors include replay parameters and seed in logs.

### Near-Term Targets
1. Keep best-known `exec_latency_cycles_avg` at or below `106.67` while preserving pass status for all RTL tests.
2. Introduce at least one additional grammar pragma and full test coverage for it.
3. Add one new CW benchmark metric that quantifies placement quality (fallback or hops).

## Guiding Priorities
1. Keep generator, compiler, scheduler, and emitted RTL behavior consistent.
2. Improve correctness and observability before aggressive optimization.
3. Preserve device portability (board wrappers stay separate from core WAU logic).

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
- Provide board wrappers (starting with DE0-NANO) and memory-mapped control registers.
- Add runtime protocol docs and example host driver stubs.

Acceptance:
- Board-specific top integrates generated WAU module cleanly.
- Host can load flow program + schedule and execute sample workloads.
- Documentation includes register map and timing expectations.

## Phase 5 (Possible): Performance and Efficiency Optimization
Goal: improve throughput, power behavior, and scalability.

Possible work:
- Multi-entry station input/result caching and reuse policy.
- Deeper pipelining for expensive operations (`mul/div/mod`).
- Flow batching and multi-packet in-flight orchestration.
- Adaptive reroute policy beyond primary/fallback (N candidates).
- Quantitative area/timing analysis scripts per device preset.

Success indicators:
- Better throughput and/or lower cycle count on benchmark flows.
- Bounded impact on LUT/FF/BRAM usage for target devices.

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
