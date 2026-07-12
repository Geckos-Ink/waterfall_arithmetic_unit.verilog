# AGENTS.md
Repository guidance for AI coding agents working on the WAU generator.

## Mission
Maintain and extend a Python-driven generator for Waterfall Arithmetic Unit RTL.
Always keep the **compiler -> scheduler -> Verilog emission** chain coherent.

## Core Workflow
1. Edit WAU source-of-truth files in `src/python/waugen/`, config samples in `src/python/configs/`, and the reusable Verilog project builder in `thirds/veribuilder/` when changing generic emission/project assembly.
2. Sync SPDX license headers for all source files in `src/`:
   - `python3 scripts/sync_license_headers.py`
3. Validate config and pipeline:
   - `PYTHONPATH=src/python python3 -m waugen validate --config <config.json>`
4. If touching expression-to-flow logic, validate the basic compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-expr --expr '((a + b) * 3) - b' --flow-id <id> --base-config <in> --out-config <out>`
5. If touching pseudo-C lowering, validate the pseudo-C compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-pseudoc --program 'acc = a; acc = acc + b; acc *= 3;' --flow-id <id> --base-config <in> --out-config <out>`
6. If touching `.cw` kernel lowering, validate the CW compiler path:
   - syntax/template preflight: `PYTHONPATH=src/python python3 -m waugen cw-lint --program-file CWs/example-program.cw --compile-template`
   - `PYTHONPATH=src/python python3 -m waugen compile-cw --program-file CWs/example-program.cw --flow-id <id> --base-config <in> --out-config <out> --replace-existing`
6b. If touching the `.cw` language front-end (`cw_lang.py`), validate the host-side parser/interpreter path:
   - `PYTHONPATH=src/python python3 -m waugen cw-lint --program-file CWs/samples/types/fixed_point.cw`
   - `PYTHONPATH=src/python python3 -m waugen cw-eval --program-file CWs/samples/types/fixed_point.cw`
   - conversion hook: `PYTHONPATH=src/python python3 -m waugen cw-eval --program-file CWs/samples/types/fixed_point.cw --convert 'new q8_8(384)' float32`
7. Regenerate artifacts when behavior changes:
   - `PYTHONPATH=src/python python3 -m waugen generate --config <config.json> --out src/verilog/generated --summary`
8. Run RTL tests when RTL, scheduler, or flow semantics change:
   - `./scripts/run_iverilog_tests.sh`
9. For CW kernel performance validation/tuning, run and persist the benchmark reference:
   - `./scripts/run_cw_example_benchmark.sh`
   - optional autotune sweep for best score: `TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh`
   - optional low-noise replay of saved best + stage winners:
     `REPLAY_MODE=best-and-stage-winners ./scripts/run_cw_example_benchmark.sh`
   - optional stability run summary: `MULTI_RUNS=5 ./scripts/run_cw_example_benchmark.sh`
   - optional regression guardrail check: `REGRESSION_CHECK=1 ./scripts/run_cw_example_benchmark.sh`
10. For program-level stress and scheduler regression, run randomized stress (also wired into CI):
    - `PYTHONPATH=src/python python3 scripts/run_randomized_stress.py --start-seed 2000 --count 25 --report .build/stress/randomized.json`
11. For synthesis-time architecture exploration of a workload config, run the ranked architecture search:
    - `PYTHONPATH=src/python python3 -m waugen arch-search --config <config.json> --out-report .build/arch_search/report.json`
12. For the DE0-NANO live-board CW stress path, use the board wrapper workflow
    (defaults to the ad-hoc `CWs/stress/mesh_stress.cw`; pass
    `-ProgramFile CWs/example-program.cw` for the original reference kernel):
    - `demo/de0-nano/basic-example/scripts/build_cw_stress.ps1 -GridX 2 -GridY 2 -QuartusRoot <quartus_root>`
    - `demo/de0-nano/basic-example/scripts/program.ps1 -QuartusRoot <quartus_root>`
    - `demo/de0-nano/basic-example/scripts/server.ps1 -QuartusRoot <quartus_root>`
    - `demo/de0-nano/basic-example/scripts/run_cw_stress.ps1 -Config demo/de0-nano/basic-example/build/cw_stress_2x2_merged.json -RandomIters 1024 -RandomRange 1023`
    - stream real data instead of random operands: add `--mnist-images datasets/mnist/t10k-images-idx3-ubyte.gz` to `run_cw_stress_benchmark.py`
13. To pick the best-fitting WAU architecture for a program (the board fits at
    most ~2x4 cores), use the simulator-driven fit finder. It sweeps grid shapes
    up to a device budget, predicts behaviour via the real scheduler, and
    recommends a best-performance and a fewest-cores (knee) config:
    - `PYTHONPATH=src/python python3 -m waugen fit-config --program-file CWs/stress/mesh_stress.cw --max-grid 2x4 --out-report .build/fit/report.json --out-config .build/fit/best.json`
    - convenience wrapper: `python3 scripts/find_best_wau_config.py CWs/stress/mesh_stress.cw`
14. For the ad-hoc stress kernel's simulator benchmark (own tracked log, does not
    touch the CI-gated example benchmark):
    - `./scripts/run_cw_stress_benchmark.sh` (writes `benchmarks/mesh_stress_benchmark.txt`)
15. To fetch a real dataset for data-exchange testing (git-ignored, skip-if-present):
    - `python3 scripts/fetch_dataset.py` (MNIST into `datasets/mnist/`)

## Ownership Boundaries
- `config.py`: schema and validation only (includes `compiler.station_cache`, `compiler.core_capabilities`, `scheduler.locality_bias`, and `coordinator.max_in_flight` schema).
- `compiler.py`: mapping flows/nodes onto 2D or layered-3D cores and adaptive placement strategy.
- `basic_compiler.py`: expression/pseudo-C lowering rules for basic WAU compilation.
- `cw_compiler.py`: `.cw` kernel-structured program lowering into DAG flow/program configs; reads `compiler.core_capabilities` for capability-aware candidate pruning.
- `benchmark_replay.py`: deterministic parsing and selection of saved CW
  autotune candidates for best/stage-winner/worst replay modes.
- `cw_reference.py`: software reference model for CW flows (one pass over `flow.stages` linear order, mirroring the coordinator state machine); consumed by the benchmark scoreboard and tests.
- `demo/de0-nano/basic-example/host/programs/run_cw_stress_benchmark.py`: live DE0-NANO scoreboard/observability harness for `CWs/example-program.cw`-class flows compiled into the board wrapper.
- `cw_lang.py`: the real `.cw` front-end (lexer → AST → recursive-descent parser → host-side tree-walking interpreter). Independent of `cw_compiler.py`'s regex/template RTL-lowering path. Owns **compile-time class magic methods** (operator overloading + type-conversion hooks `__to_int__`/`__to_float__`/`__convert__`); `Interpreter.convert()` is the toolchain hook for dynamic type-format conversion. Driven by the `cw-eval` CLI subcommand and the syntax side of `cw-lint`. Must not be wired into RTL lowering or the benchmark gate.
- `arch_search.py`: synthesis-time architecture-candidate enumeration and ranking (`arch-search`) plus the simulator-driven fit finder (`fit-config`). It owns grid sweeps, fit-only exact per-core `profiled` capability inference, coordinator-depth co-sweeps, estimate models, and device-capacity checks. `arch-search` never sets the additive fit dimensions and must stay byte-identical.
- `scripts/find_best_wau_config.py`: thin convenience wrapper over `waugen fit-config` (a `.cw` kernel or `.json` workload -> best/efficient config recommendation). Keeps all logic in `arch_search.py`.
- `scripts/fetch_dataset.py` (+ `.ps1`): on-demand, git-ignored dataset download (MNIST via the CVDF mirror) for data-exchange testing; also exposes `load_mnist_images`/`load_mnist_labels` readers. `datasets/` is git-ignored and never committed.
- `src/python/configs/wau_cw_fit_base.json`: minimal, demo-independent DE0-NANO base used to compile a raw `.cw` for `fit-config` and as the base for `scripts/run_cw_stress_benchmark.sh`. Carries the full `add/sub/mul/div/max` op set so the fixed ALU testbench elaborates; the board keeps its own lean `add/mul/max` base (`wau_de0_nano_cw_stress_base.json`).
- `src/python/configs/wau_de0_nano_example_2x4_profiled.json`: exact source config for the validated Quartus Lite 25.1 profiled 2x4 board image; keep its per-core capabilities and compiled placements reproducible from the CW/build knobs recorded in the benchmark.
- `scheduler.py`: multi-program dependency-aware timing model and encoded schedule outputs. Also owns routing-aware core selection: `scheduler.locality_bias` (default `0.0`, off) weights candidate cores by 2D/3D Manhattan hop distance to their dependencies' placed cores as a tiebreaker after earliest-free-cycle, so it cannot regress makespan/latency.
- `verilog_emit.py`: WAU-specific RTL text rendering only; no scheduling decisions should live here. It structurally applies `compiler.core_capabilities` to per-core ALU elaboration via `CORE_INDEX`, so synthesis can remove unsupported operations, and also owns mesh, observability, MMIO, and coordinator emission.
- `thirds/veribuilder/`: externalizable Python library for dynamic Verilog project construction, feature-gated file manifests, lightweight template rendering, and deterministic file emission. Keep it independent from WAU config/compiler/scheduler types so it can be published as a standalone repository.
- Generated files under `src/verilog/generated/` are build outputs and may be overwritten.
- `.github/workflows/ci.yml`: CI matrix (python tests, randomized stress, iverilog tests, CW benchmark) — keep aligned with the local Core Workflow steps.

## Invariants
- Operation names and opcodes must be unique.
- Flow IDs must be unique.
- Stage operations must exist in the operation table.
- Core indices must stay within `grid_x * grid_y * grid_z`; omitted `grid.z` defaults to `1`, and omitted coordinate `z` defaults to `0`.
- Compiler core capability constraints must reference existing operations/data types.
- `compiler.station_cache.entries` must stay within `[1, 32]`; `replacement_policy` must be `fifo` or `lru`. The matching `WAU_STATION_CACHE_*` defs in `wau_defs.vh` must agree with the emitted `wau_core_station.v`.
- `scheduler.locality_bias` must be `>= 0`. It is a pure core-selection tiebreaker (after earliest-free-cycle), so `locality_bias=0.0` must not change makespan/latency; explicit `0.0` and an omitted knob must produce the same schedule byte-for-byte (guarded by `tests/python/test_scheduler_locality.py`). It is a Python-side scheduling knob only and emits no RTL/`wau_defs.vh` changes.
- Scheduler ties must be deterministic across Python processes and
  `PYTHONHASHSEED` values. Explicit `program_replica`/runtime-node tiebreakers
  canonicalize ties that older revisions left to set iteration order.
- `coordinator.max_in_flight` must be in `[1,16]` and must match the emitted `WAU_COORD_MAX_IN_FLIGHT` macro and the `wau_coordinator` `MAX_IN_FLIGHT` localparam. The coordinator must preserve per-flow semantics: a single in-flight flow stays cycle-identical to the legacy serial design (`tb_wau_top_demo` timing, CW exec per-case latencies, and the scoreboard must not regress), unknown flow ids stay accepted-but-dropped, and `tb_wau_coordinator_multiissue` must keep proving ≥2 cores busy concurrently for independent flows. Result matching relies on at most one in-flight slot per flow id (`host_in_ready` enforces this) since the dispatch/result packet format carries no tag.
- Verilog macros in `wau_defs.vh` (`WAU_GRID_X/Y/Z`, `WAU_CORE_COUNT`, cache/coordinator macros) must match emitted modules.
- `wau_top` must keep exporting the `obs_total_*` observability bus; `wau_host_mmio` must keep its existing register map (CTRL/STATUS/FLOW_ID/IN_A/IN_B/TRIGGER/OUT_FLOW/OUT_VAL/HOPS/STALLS/FORWARDS/DELIVRD/CACHE_H/CACHE_L) stable so host software targeting it doesn't silently break.
- The CW software reference (`waugen.cw_reference.evaluate_flow`) must produce the same `host_out_value` as the generated RTL for the deterministic benchmark cases; the generated CW exec testbench `$fatal`s on mismatch.
- A live DE0-NANO watchdog expiry is a circuit/configuration failure: abort on the first timeout and emit diagnostics; never continue and report timeout-limited throughput.
- The board carries 32 MB external SDRAM, but the current wrapper holds it inactive. Do not count it as cache capacity until a controller/cache path exists; `station_cache.entries` remains the active 1..32-entry per-core cache.
- License headers in `src/**/*.py`, `src/**/*.v`, and `src/**/*.vh` are managed by `scripts/sync_license_headers.py`; run it after each implementation and before review.
- `run_cw_example_benchmark.sh` must produce a valid `benchmarks/example_pogram_benchmark.txt` log with passing RTL tests, CW execution metrics, and `scoreboard_pass_ratio == 1.0`.
- `run_cw_stress_benchmark.sh` is a thin wrapper over the same engine for `CWs/stress/mesh_stress.cw`; it must write only `benchmarks/mesh_stress_*` artifacts and must never overwrite the CI-gated `example_pogram_*` files. It must also keep `scoreboard_pass_ratio == 1.0`.
- All real `.cw` programs live under `CWs/` (canonical location). Historical board reports (`benchmarks/de0_nano_*`) and dated ROADMAP entries keep their original `docs/...` paths as run-time records and must not be rewritten.
- `waugen.arch_search.run_arch_search` must stay byte-identical (guarded by `tests/python/test_arch_search.py`); `fit-config`/`run_fit_search` is additive and must not alter `arch-search` behavior. `fit-config` must only build/evaluate candidate payloads in memory (no scheduling/emission changes). The `CandidateKnobs.max_in_flight` co-sweep dimension defaults to `None` (leaves `coordinator.max_in_flight` untouched and omits the `_mif` id suffix / knobs key) precisely so `arch-search` reports stay byte-identical; only `fit-config` sets it. Swept depths must stay within the coordinator's `[1,16]` schema range.
- Whenever new possible future implementations, optimizations, or architecture changes are identified, update `ROADMAP.md` in the same work cycle.
- Whenever workflow steps, ownership boundaries, invariants, or scope change, update both `AGENTS.md` and `README.md` in the same work cycle.

## CW Benchmark Objective
- Primary objective for `scripts/run_cw_example_benchmark.sh`: minimize `exec_latency_cycles_avg`.
- Secondary tie-breakers: lower `makespan_cycles`, then lower `total_ms`.
- Hard correctness gate: `scoreboard_pass_ratio` must stay at `1.0` (every deterministic case's `host_out_value` matches the software reference in `waugen.cw_reference`).
- Always keep `benchmarks/example_pogram_benchmark.txt` updated to the latest best known run when tuning is performed.
- Keep `benchmarks/example_pogram_tuning_latest.txt` as the full sweep/reference summary when autotune mode is used.

Current best-known score (as of 2026-06-14, retaining the staged `TUNE_MODE=1`
winner and re-validating deterministic scheduling, dependency-edge metrics, and
the value scoreboard):
- `cw_lane_parallelism_requested=2`, `program_replicas=2`, `program_max_parallel_flows=1`
- `cw_max_in_flight=2`, `placement=balance`, `profile=throughput_optimized`
- `program_priority=4`, `program_load_balance=least_busy`, `scheduler_program_policy=weighted_fair`
- `exec_latency_cycles_avg=68.00` (min `58`, max `70`, p95 `70.00`)
- `makespan_cycles=42`, `fallback_instruction_ratio=0.3043` (21/69),
  `dependency_edges_v1=104` hops across 105 true data-dependency edges
- 3-run stability: `median=68.00`, `p95=68.00`, `3/3` passing
- 8/8 scoreboard cases match the software reference (`scoreboard_pass_ratio=1.0`)

Current DE0-NANO real-board ceiling for `CWs/example-program.cw` (measured
2026-07-13 on EP4CE22 with Quartus Lite 25.1):
- validated profiled `2x4`: `21,478 / 22,320` LEs (96%), `12 / 132` multiplier elements, `1032/1032` pass, 95.4 ops/s
- the WAU/JTAG domain uses a /16 (3.125 MHz) board clock and closes timing; Quartus still reports a combinational router loop, so elastic links remain required before raising it
- non-fitting images: `4x4` (`61,564 / 22,320` logic elements, 276%) and `2x5` (`39,747 / 22,320` logic elements, 178%)
- generic 2x4 remains historical failure telemetry; the profiled 2x4 is the current validated image

## Extension Rules
When adding a new arithmetic operation:
1. Add template in `operation_library.py`.
2. Ensure parsing supports it in `config.py`.
3. Confirm ALU case generation in `verilog_emit.py`.
4. Regenerate and run `iverilog`.

When adding a new device preset:
1. Add it in `device_library.py` with realistic part metadata, including the
   synthesis capacity fields (`logic_cells`, `bram_kbits`, `dsp_blocks`) used
   by `arch_search.py` feasibility checks.
2. Ensure width/depth defaults are sane.
3. Validate at least one config using that preset.

When changing flow compilation/scheduling behavior:
1. Update `compiler.py`, `basic_compiler.py`, and `scheduler.py` as needed.
2. Keep `wau_program.json` and `wau_schedule.json/.hex` consistent.
3. Mention behavioral deltas in README.
4. Validate at least one advanced DAG/program config (`wau_2d_multiprogram_demo.json`).
5. If changing core indexing, placement, or routing dimensions, validate `src/python/configs/wau_3d_demo.json` and run `tests/rtl/tb_wau_highway_mesh_3d.v` through `iverilog`.
6. Re-run `tests/python/test_program_stress.py` (96-cell priority × replicas × policy matrix) and `scripts/run_randomized_stress.py`.

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
- `scripts/run_iverilog_tests.sh` passes (including `tb_wau_host_mmio`, the hop-counter assertion in `tb_wau_highway_mesh`, and vertical routing in `tb_wau_highway_mesh_3d`).
- `scripts/run_randomized_stress.py` passes (`--count 25` is the default smoke pass).
- README/AGENTS/ROADMAP updated if workflow, architecture, or future implementation direction changed.
- The `.github/workflows/ci.yml` matrix still mirrors the local Core Workflow; if you add a new step locally, mirror it in CI.
- No manual edits left in generated RTL that are not reproducible.
