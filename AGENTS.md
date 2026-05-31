# AGENTS.md
Repository guidance for AI coding agents working on the WAU generator.

## Mission
Maintain and extend a Python-driven generator for Waterfall Arithmetic Unit RTL.
Always keep the **compiler -> scheduler -> Verilog emission** chain coherent.

## Core Workflow
1. Edit source-of-truth files in `src/python/waugen/` and config samples in `src/python/configs/`.
2. Sync SPDX license headers for all source files in `src/`:
   - `python3 scripts/sync_license_headers.py`
3. Validate config and pipeline:
   - `PYTHONPATH=src/python python3 -m waugen validate --config <config.json>`
4. If touching expression-to-flow logic, validate the basic compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-expr --expr '((a + b) * 3) - b' --flow-id <id> --base-config <in> --out-config <out>`
5. If touching pseudo-C lowering, validate the pseudo-C compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-pseudoc --program 'acc = a; acc = acc + b; acc *= 3;' --flow-id <id> --base-config <in> --out-config <out>`
6. If touching `.cw` kernel lowering, validate the CW compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-cw --program-file docs/example-program.cw --flow-id <id> --base-config <in> --out-config <out> --replace-existing`
7. Regenerate artifacts when behavior changes:
   - `PYTHONPATH=src/python python3 -m waugen generate --config <config.json> --out src/verilog/generated --summary`
8. Run RTL tests when RTL, scheduler, or flow semantics change:
   - `./scripts/run_iverilog_tests.sh`
9. For CW kernel performance validation/tuning, run and persist the benchmark reference:
   - `./scripts/run_cw_example_benchmark.sh`
   - optional autotune sweep for best score: `TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh`
   - optional stability run summary: `MULTI_RUNS=5 ./scripts/run_cw_example_benchmark.sh`
   - optional regression guardrail check: `REGRESSION_CHECK=1 ./scripts/run_cw_example_benchmark.sh`
10. For program-level stress and scheduler regression, run randomized stress (also wired into CI):
    - `PYTHONPATH=src/python python3 scripts/run_randomized_stress.py --start-seed 2000 --count 25 --report .build/stress/randomized.json`

## Ownership Boundaries
- `config.py`: schema and validation only (includes `compiler.station_cache`, `compiler.core_capabilities`, and `scheduler.locality_bias` schema).
- `compiler.py`: mapping flows/nodes onto 2D cores and adaptive placement strategy.
- `basic_compiler.py`: expression/pseudo-C lowering rules for basic WAU compilation.
- `cw_compiler.py`: `.cw` kernel-structured program lowering into DAG flow/program configs; reads `compiler.core_capabilities` for capability-aware candidate pruning.
- `cw_reference.py`: software reference model for CW flows (one pass over `flow.stages` linear order, mirroring the coordinator state machine); consumed by the benchmark scoreboard and tests.
- `scheduler.py`: multi-program dependency-aware timing model and encoded schedule outputs. Also owns routing-aware core selection: `scheduler.locality_bias` (default `0.0`, off) weights candidate cores by Manhattan hop distance to their dependencies' placed cores as a tiebreaker after earliest-free-cycle, so it cannot regress makespan/latency.
- `verilog_emit.py`: text emission only; no scheduling decisions should live here. Also owns the per-router/per-station observability counter wiring and the `wau_host_mmio` register file emission.
- Generated files under `src/verilog/generated/` are build outputs and may be overwritten.
- `.github/workflows/ci.yml`: CI matrix (python tests, randomized stress, iverilog tests, CW benchmark) — keep aligned with the local Core Workflow steps.

## Invariants
- Operation names and opcodes must be unique.
- Flow IDs must be unique.
- Stage operations must exist in the operation table.
- Core indices must stay within `grid_x * grid_y`.
- Compiler core capability constraints must reference existing operations/data types.
- `compiler.station_cache.entries` must stay within `[1, 32]`; `replacement_policy` must be `fifo` or `lru`. The matching `WAU_STATION_CACHE_*` defs in `wau_defs.vh` must agree with the emitted `wau_core_station.v`.
- `scheduler.locality_bias` must be `>= 0`. It is a pure core-selection tiebreaker (after earliest-free-cycle), so it must never change makespan/latency for `locality_bias=0.0`; the default `0.0` must reproduce the prior schedule byte-for-byte (guarded by `tests/python/test_scheduler_locality.py`). It is a Python-side scheduling knob only and emits no RTL/`wau_defs.vh` changes.
- Verilog macros in `wau_defs.vh` must match emitted modules.
- `wau_top` must keep exporting the `obs_total_*` observability bus; `wau_host_mmio` must keep its existing register map (CTRL/STATUS/FLOW_ID/IN_A/IN_B/TRIGGER/OUT_FLOW/OUT_VAL/HOPS/STALLS/FORWARDS/DELIVRD/CACHE_H/CACHE_L) stable so host software targeting it doesn't silently break.
- The CW software reference (`waugen.cw_reference.evaluate_flow`) must produce the same `host_out_value` as the generated RTL for the deterministic benchmark cases; the generated CW exec testbench `$fatal`s on mismatch.
- License headers in `src/**/*.py`, `src/**/*.v`, and `src/**/*.vh` are managed by `scripts/sync_license_headers.py`; run it after each implementation and before review.
- `run_cw_example_benchmark.sh` must produce a valid `benchmarks/example_pogram_benchmark.txt` log with passing RTL tests, CW execution metrics, and `scoreboard_pass_ratio == 1.0`.
- Whenever new possible future implementations, optimizations, or architecture changes are identified, update `ROADMAP.md` in the same work cycle.
- Whenever workflow steps, ownership boundaries, invariants, or scope change, update both `AGENTS.md` and `README.md` in the same work cycle.

## CW Benchmark Objective
- Primary objective for `scripts/run_cw_example_benchmark.sh`: minimize `exec_latency_cycles_avg`.
- Secondary tie-breakers: lower `makespan_cycles`, then lower `total_ms`.
- Hard correctness gate: `scoreboard_pass_ratio` must stay at `1.0` (every deterministic case's `host_out_value` matches the software reference in `waugen.cw_reference`).
- Always keep `benchmarks/example_pogram_benchmark.txt` updated to the latest best known run when tuning is performed.
- Keep `benchmarks/example_pogram_tuning_latest.txt` as the full sweep/reference summary when autotune mode is used.

Current best-known score (as of 2026-05-22, from staged `TUNE_MODE=1` sweep,
re-validated with capability-aware CW lowering and the value scoreboard):
- `cw_lane_parallelism_requested=2`, `program_replicas=2`, `program_max_parallel_flows=1`
- `cw_max_in_flight=2`, `placement=balance`, `profile=throughput_optimized`
- `program_priority=4`, `program_load_balance=least_busy`, `scheduler_program_policy=weighted_fair`
- `exec_latency_cycles_avg=68.00` (min `58`, max `70`, p95 `70.00`)
- `makespan_cycles=42`, `fallback_instruction_ratio=0.2899`, `estimated_transfer_hops_total=54`
- 3-run stability: `median=68.00`, `p95=68.00`, `3/3` passing
- 8/8 scoreboard cases match the software reference (`scoreboard_pass_ratio=1.0`)

## Extension Rules
When adding a new arithmetic operation:
1. Add template in `operation_library.py`.
2. Ensure parsing supports it in `config.py`.
3. Confirm ALU case generation in `verilog_emit.py`.
4. Regenerate and run `iverilog`.

When adding a new device preset:
1. Add it in `device_library.py` with realistic part metadata.
2. Ensure width/depth defaults are sane.
3. Validate at least one config using that preset.

When changing flow compilation/scheduling behavior:
1. Update `compiler.py`, `basic_compiler.py`, and `scheduler.py` as needed.
2. Keep `wau_program.json` and `wau_schedule.json/.hex` consistent.
3. Mention behavioral deltas in README.
4. Validate at least one advanced DAG/program config (`wau_2d_multiprogram_demo.json`).
5. Re-run `tests/python/test_program_stress.py` (96-cell priority × replicas × policy matrix) and `scripts/run_randomized_stress.py`.

When changing CW lowering or kernel semantics:
1. Update `cw_compiler.py` (and `cw_reference.py` if the coordinator-side reduction
   changes — they must stay in lock-step).
2. Re-run `tests/python/test_cw_compiler.py` and `tests/python/test_cw_reference.py`
   (the latter checks against hardware-golden values).
3. Re-run `./scripts/run_cw_example_benchmark.sh` and confirm
   `scoreboard_pass_ratio=1.0` plus the latency/makespan budget from the section
   above.

When changing station caching or highway routing:
1. Update `compiler.station_cache` defaults / `wau_core_station.v` /
   `wau_highway_router.v` together; `WAU_STATION_CACHE_*` defs in `wau_defs.vh`
   must agree with the emitted RTL.
2. Re-run `tests/rtl/tb_wau_host_mmio.v` and `tests/rtl/tb_wau_highway_mesh.v`
   (the mesh testbench asserts `router_hop_count` advances).
3. Re-run the CW benchmark; observability counters are visible via the MMIO
   register map and via the `obs_total_*` ports on `wau_top`.

## Review Checklist
Before finalizing changes:
- `python3 scripts/sync_license_headers.py --check` succeeds.
- `validate` succeeds.
- `generate` succeeds.
- `./scripts/run_cw_example_benchmark.sh` succeeds and refreshes benchmark reference log; `scoreboard_pass_ratio` is `1.0`.
- `tests/python` unit tests pass (including `test_cw_reference`, `test_program_stress`).
- `scripts/run_iverilog_tests.sh` passes (including `tb_wau_host_mmio` and the hop-counter assertion in `tb_wau_highway_mesh`).
- `scripts/run_randomized_stress.py` passes (`--count 25` is the default smoke pass).
- README/AGENTS/ROADMAP updated if workflow, architecture, or future implementation direction changed.
- The `.github/workflows/ci.yml` matrix still mirrors the local Core Workflow; if you add a new step locally, mirror it in CI.
- No manual edits left in generated RTL that are not reproducible.
