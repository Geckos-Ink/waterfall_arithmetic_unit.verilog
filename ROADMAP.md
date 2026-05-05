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
