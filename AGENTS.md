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
   - `PYTHONPATH=src/python python3 -m waugen compile-cw --program-file docs/example-pogram.cw --flow-id <id> --base-config <in> --out-config <out> --replace-existing`
7. Regenerate artifacts when behavior changes:
   - `PYTHONPATH=src/python python3 -m waugen generate --config <config.json> --out src/verilog/generated --summary`
8. Run RTL tests when RTL, scheduler, or flow semantics change:
   - `./scripts/run_iverilog_tests.sh`
9. For CW kernel performance validation/tuning, run and persist the benchmark reference:
   - `./scripts/run_cw_example_benchmark.sh`
   - optional autotune sweep for best score: `TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh`

## Ownership Boundaries
- `config.py`: schema and validation only.
- `compiler.py`: mapping flows/nodes onto 2D cores and adaptive placement strategy.
- `basic_compiler.py`: expression/pseudo-C lowering rules for basic WAU compilation.
- `cw_compiler.py`: `.cw` kernel-structured program lowering into DAG flow/program configs.
- `scheduler.py`: multi-program dependency-aware timing model and encoded schedule outputs.
- `verilog_emit.py`: text emission only; no scheduling decisions should live here.
- Generated files under `src/verilog/generated/` are build outputs and may be overwritten.

## Invariants
- Operation names and opcodes must be unique.
- Flow IDs must be unique.
- Stage operations must exist in the operation table.
- Core indices must stay within `grid_x * grid_y`.
- Compiler core capability constraints must reference existing operations/data types.
- Verilog macros in `wau_defs.vh` must match emitted modules.
- License headers in `src/**/*.py`, `src/**/*.v`, and `src/**/*.vh` are managed by `scripts/sync_license_headers.py`; run it after each implementation and before review.
- `run_cw_example_benchmark.sh` must produce a valid `benchmarks/example_pogram_benchmark.txt` log with passing RTL tests and CW execution metrics.
- Whenever new possible future implementations, optimizations, or architecture changes are identified, update `ROADMAP.md` in the same work cycle.

## CW Benchmark Objective
- Primary objective for `scripts/run_cw_example_benchmark.sh`: minimize `exec_latency_cycles_avg`.
- Secondary tie-breakers: lower `makespan_cycles`, then lower `total_ms`.
- Always keep `benchmarks/example_pogram_benchmark.txt` updated to the latest best known run when tuning is performed.
- Keep `benchmarks/example_pogram_tuning_latest.txt` as the full sweep/reference summary when autotune mode is used.

Current best-known score (as of 2026-05-06, from `TUNE_MODE=1` sweep):
- `cw_lane_parallelism_requested=4` (via `.cw` pragma), `program_replicas=2`, `program_max_parallel_flows=1`
- `exec_latency_cycles_avg=106.67` (min `96`, max `112`)
- `makespan_cycles=43`
- `total_ms=1082`

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

## Review Checklist
Before finalizing changes:
- `python3 scripts/sync_license_headers.py --check` succeeds.
- `validate` succeeds.
- `generate` succeeds.
- `./scripts/run_cw_example_benchmark.sh` succeeds and refreshes benchmark reference log.
- `tests/python` unit tests pass.
- `scripts/run_iverilog_tests.sh` passes.
- README/AGENTS/ROADMAP updated if workflow, architecture, or future implementation direction changed.
- No manual edits left in generated RTL that are not reproducible.
