# Waterfall Arithmetic Unit (WAU) — AI Agent Reference

Fast-access operational reference for agents working in this repository.

**What this is.** A Python-driven **generator toolchain** that turns a high-level kernel description (an arithmetic expression, a constrained pseudo-C snippet, or a `.cw` program) into a complete, synthesizable Verilog implementation of a *Waterfall Arithmetic Unit* — a 2D or layered-3D grid of small ALU cores connected by a packet-switched highway mesh, driven by a multi-issue coordinator and a memory-mapped host register file. It also emits the compiled program, the offline schedule, and a software reference model used as a correctness oracle.

**Maturity.** Working, silicon-verified foundation, not a final architecture. The generated design has been taken end-to-end onto a Terasic DE0-Nano (Intel Cyclone IV E EP4CE22F17C6) with bit-exact live scoreboard passes (see [Current Status](#current-status-and-known-gaps)).

**What this is NOT:**

- **Not hand-written RTL.** Everything under `src/verilog/generated/` is build output. Never implement behavior there — edit the emitter in [`src/python/waugen/verilog_emit.py`](src/python/waugen/verilog_emit.py).
- **Not a general C compiler.** The pseudo-C frontend accepts accumulator-style pipelines only; the `.cw` RTL-lowering path accepts a narrow kernel template.
- **Not a general-purpose CPU or soft-core.** There is no instruction fetch, no branching in hardware, no memory hierarchy beyond per-core station caches.
- **`veribuilder` is not part of WAU.** [`thirds/veribuilder/`](thirds/veribuilder) is a vendored, independently publishable library that must not know about WAU types.
- **`cw_lang.py` is not the RTL compiler.** It is a host-side language front-end; `cw_compiler.py` is the RTL-lowering path. They are deliberately separate (see [Two `.cw` paths](#two-cw-paths-host-language-vs-rtl-lowering)).

**Vocabulary (defined on first use):** **WAU** = Waterfall Arithmetic Unit. **Flow** = a DAG (or linear stage chain) of arithmetic nodes with one entry and one exit, identified by a `flow_id`. **Program** = a scheduling container grouping flows with priority/replica/load-balance policy. **Core** = one ALU + station at grid coordinate `(x, y, z)`. **Station** = the per-core dispatch/latency/cache wrapper. **Highway** = the router mesh carrying packets between cores. **Coordinator** = the flow orchestrator that dispatches stages and collects results. **`.cw`** = the repository's kernel language. **MMIO** = the memory-mapped host register file (`wau_host_mmio`).

---

## Read This First

Source-of-truth order for this repository (highest authority first). When sources disagree, inspect the behavior, fix or report the mismatch in task scope, and update the stale document in the same change.

1. **Executable specifications and tests** — [`tests/python/`](tests/python), [`tests/rtl/`](tests/rtl), and the emitted `wau_defs.vh` macro contract. A test proves only what it asserts.
2. **Current source and build configuration** — [`src/python/waugen/`](src/python/waugen), [`.github/workflows/ci.yml`](.github/workflows/ci.yml), [`scripts/`](scripts).
3. **[`FOUNDATIONS.md`](FOUNDATIONS.md)** — the project's design charter (why the WAU exists, why routing and data locality are first-class, why data-type management matters). Normative for *intent*; it constrains architecture decisions but describes no commands. Enforced in spirit by [`tests/python/test_foundation_alignment.py`](tests/python/test_foundation_alignment.py).
4. **[`docs/cw-language.md`](docs/cw-language.md)** — the authoritative `.cw` grammar, pragma contract, and the narrower `compile-cw` template requirements.
5. **[`README.md`](README.md)** — user-facing overview, quickstart, config model, MMIO register map, and the silicon benchmark write-ups. Authoritative for *user-visible* documentation; must stay synchronized with this file.
6. **[`benchmarks/`](benchmarks)** — measured run records (simulator and live board). Authoritative for *what was measured*, never for what is currently implemented.
7. **[`ROADMAP.md`](ROADMAP.md)** — planned work and dated progress entries. **A roadmap entry is not proof a feature exists.**
8. **[`thirds/veribuilder/AGENTS.md`](thirds/veribuilder/AGENTS.md)** — nested handbook; applies only inside [`thirds/veribuilder/`](thirds/veribuilder) and adds the "must not import WAU" rule.

---

## Collaboration and Maintenance Rules

- **Documentation synchronization.** Any change to workflow steps, ownership boundaries, invariants, interfaces, or scope MUST update [`AGENTS.md`](AGENTS.md) **and** [`README.md`](README.md) in the same work cycle. Newly identified future work, optimizations, or architecture changes go to [`ROADMAP.md`](ROADMAP.md) in the same cycle — never presented here as current behavior.
- **License headers.** `src/**/*.py`, `src/**/*.v`, and `src/**/*.vh` carry SPDX headers managed by [`scripts/sync_license_headers.py`](scripts/sync_license_headers.py). Run it after each implementation and before review; `--check` is a gate.
- **Generated output is never edited by hand.** `src/verilog/generated/` is tracked but regenerable; a change there that cannot be reproduced by `waugen generate` is a defect. `.build/` and `datasets/` are git-ignored runtime/scratch trees.
- **Preserve the dirty tree.** Never use destructive cleanup (`git checkout -- .`, `git clean`) to simplify discovery. Preserve unrelated user changes.
- **Benchmark logs are records, not scratch.** [`benchmarks/example_pogram_benchmark.txt`](benchmarks/example_pogram_benchmark.txt) is the CI-gated reference and must be refreshed to the latest best known run when tuning is performed. Historical board reports (`benchmarks/de0_nano_*`) and dated `ROADMAP.md` entries are run-time records: they keep their original paths and text and MUST NOT be rewritten.
- **Test expectations.** Behavior changes must be accompanied by the focused test(s) named in the [Test Ownership Map](#test-ownership-map). RTL, scheduler, or flow semantic changes require an `iverilog` run.
- **CI mirrors local workflow.** If you add a local validation step, mirror it in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Essential Project Principles

### Single generator chain: config → compile → schedule → emit

Every artifact (RTL, `wau_program.json`, `wau_schedule.json/.hex`, the software reference) derives from one `ProjectConfig`. No stage may bypass an earlier one.

- Scheduling decisions MUST NOT live in [`verilog_emit.py`](src/python/waugen/verilog_emit.py); placement decisions MUST NOT live in [`scheduler.py`](src/python/waugen/scheduler.py).
- Nothing may re-implement lowering or emission out-of-band. The viewer's [`prepare.py`](tools/wau-pipelines-viewer/wau_viewer/prepare.py) shells out to the real CLI precisely for this reason.
- **Prohibited:** patching generated Verilog to make a test pass.

### Data locality is architectural, not an optimization

Per [`FOUNDATIONS.md`](FOUNDATIONS.md), data should reside in or near the core that processes it; external memory is used only when efficient and necessary.

- Consequence: per-core station caches (1..32 entries, FIFO/LRU) are the active memory tier; the board's 32 MB SDRAM is **held inactive** and MUST NOT be counted as cache capacity.
- Consequence: `scheduler.locality_bias` exists as a hop-distance tiebreaker, and transfer-hop counts are first-class schedule metrics.

### Cores are heterogeneous by design

Operators may be present on some cores and absent on others; routing distributes work around that.

- Consequence: `compiler.core_capabilities` constrains placement, and [`verilog_emit.py`](src/python/waugen/verilog_emit.py) applies it *structurally* via `CORE_INDEX` so synthesis can physically remove unsupported ALU components.
- **Prohibited:** assuming every core can execute every operation, in the compiler, the scheduler, or a testbench.

### Measurement over intuition

Performance work is gated on closed-loop measurement (iverilog simulation and real board runs), never on developer trial-and-error.

- Consequence: the CW benchmark carries a hard correctness gate (`scoreboard_pass_ratio == 1.0`) *and* a latency objective; a live board watchdog expiry is a failure, never a throughput sample.

### Device portability

Board-specific wrappers stay separate from core WAU logic, so the same generator output retargets.

- Consequence: `wau_de0_nano_top.v` is feature-gated (`de0_nano`) inside the emitter; the vJTAG bridge and Python host stack in [`demo/`](demo/de0-nano/basic-example) are written device-agnostic and used as libraries.

---

## Critical Implementation Contracts

Each contract names its enforcing code and its focused test. Exceptions are stated inline.

- **Unique identifiers.** Operation names and opcodes MUST be unique; flow ids MUST be unique; stage operations MUST exist in the operation table. Enforced in [`config.py`](src/python/waugen/config.py) (`_load_operations`, `_load_flows`).
- **Core index bounds.** Core indices MUST stay within `grid_x * grid_y * grid_z`. Omitted `grid.z` defaults to `1`; omitted coordinate `z` defaults to `0`. Enforced in [`config.py`](src/python/waugen/config.py) (`_parse_placement`, `Coord`); tested by [`tests/python/test_3d_grid.py`](tests/python/test_3d_grid.py).
- **Capability references must exist.** `compiler.core_capabilities` entries MUST reference existing operations and data types. Enforced by `_parse_core_capabilities` in [`config.py`](src/python/waugen/config.py); tested by [`tests/python/test_foundation_alignment.py`](tests/python/test_foundation_alignment.py).
- **Station cache range and macro agreement.** `compiler.station_cache.entries` MUST be in `[1, 32]`; `replacement_policy` MUST be `fifo` or `lru`. The emitted `WAU_STATION_CACHE_ENTRIES` / `WAU_STATION_CACHE_POLICY_{FIFO,LRU}` defs in `wau_defs.vh` MUST agree with the emitted `wau_core_station.v`. Owned by [`config.py`](src/python/waugen/config.py) (`StationCacheSpec`) and [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_defs`, `_render_core_station`); exercised by [`tests/rtl/tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v) and [`tests/python/test_program_stress.py`](tests/python/test_program_stress.py).
- **`locality_bias` is a pure tiebreaker.** `scheduler.locality_bias` MUST be `>= 0`. It is applied **only after** the earliest-free-cycle key, so `0.0` MUST NOT change makespan/latency, and an explicit `0.0` MUST produce a byte-identical schedule to an omitted knob. It is Python-side only and emits **no** RTL or `wau_defs.vh` change. Owned by `_locality_cost` / `_select_core` in [`scheduler.py`](src/python/waugen/scheduler.py); guarded by [`tests/python/test_scheduler_locality.py`](tests/python/test_scheduler_locality.py).
- **Schedule determinism across processes.** Scheduler ties MUST be deterministic across Python processes and `PYTHONHASHSEED` values. Explicit `program_replica` / `runtime_node_key` tiebreakers canonicalize ties that older revisions left to set-iteration order. Guarded by `test_schedule_is_hash_seed_independent_across_processes` in [`tests/python/test_scheduler_locality.py`](tests/python/test_scheduler_locality.py).
- **Coordinator depth contract.** `coordinator.max_in_flight` MUST be in `[1,16]` and MUST match the emitted `WAU_COORD_MAX_IN_FLIGHT` macro and the `wau_coordinator` `MAX_IN_FLIGHT` localparam. Per-flow semantics MUST be preserved: a single in-flight flow stays **cycle-identical** to the legacy serial design (`tb_wau_top_demo` timing, CW exec per-case latencies, and the scoreboard must not regress); unknown flow ids stay accepted-but-dropped. **Exception/limit:** result matching relies on at most one in-flight slot *per flow id* (`host_in_ready` enforces this) because the dispatch/result packet format carries no tag. Owned by `_render_coordinator` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py) and `CoordinatorSpec` in [`config.py`](src/python/waugen/config.py); proven by [`tests/rtl/tb_wau_coordinator_multiissue.v`](tests/rtl/tb_wau_coordinator_multiissue.v) (≥2 cores busy concurrently for independent flows) and [`tests/python/test_coordinator_config.py`](tests/python/test_coordinator_config.py).
- **Highway topology contract.** `device.highway.topology` MUST be `linear` (default) or `matrix`, and MUST reach both `WAU_HIGHWAY_TOPOLOGY_{LINEAR,MATRIX}` / `WAU_HIGHWAY_PORT_COUNT` *and* the emitted router's `PORT_COUNT` localparam and port set. `linear` emits one 1-D highway per layer walked in **core-index order** (`LOCAL`/`PREV`/`NEXT`, plus `UP`/`DOWN` when `grid.z > 1`) and MUST contain no `% GRID_X` / `/ GRID_X` — the index compare is what removes the per-port `LPM_DIVIDE`. `matrix` keeps the seven-port X-then-Y-then-Z mesh. Both topologies MUST keep every core reachable from the coordinator and MUST expose an identical `wau_highway_mesh` port interface, so `wau_top`, the testbenches and the viewer stay single-sourced. Owned by `_render_highway_router` / `_render_highway_mesh` / `_render_defs` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py) and `HighwaySpec` in [`config.py`](src/python/waugen/config.py); proven by [`tests/python/test_highway.py`](tests/python/test_highway.py) and [`tests/rtl/tb_wau_highway_linear.v`](tests/rtl/tb_wau_highway_linear.v).
- **Highway contract bus is non-blocking by default and always releases.** `wau_highway_contract` offers one core slot per clock. With **no contract in force the highway MUST admit every core** — an idle bus adds no admission latency and cannot regress verified timing. While a contract is in force it MUST admit *only* its holder. Every contract MUST be bounded twice — by its beat count **and** by `contract_lease_cycles` — and a holder that stops presenting traffic MUST release immediately, so the highway can never be wedged. The round-robin MUST resume *after* the holder, never on it. `admit` MUST stay a registered decision so gating the local port adds no combinational path through the routers. The bus is instantiated on the **data plane only** (`CONTRACT_BUS_ENABLE`); the control plane has a single injector and nothing to arbitrate. Owned by `_render_highway_contract` / `_render_highway_mesh` / `_render_top`; proven by [`tests/rtl/tb_wau_highway_contract.v`](tests/rtl/tb_wau_highway_contract.v).
- **Contract words come from the schedule, not from the emitter's imagination.** The per-core contract ROM in `wau_top` MUST be derived from `SchedulePlan` by `contract_rom_entries` (`words` = longest single-flow run on that core, clamped to `contract_max_burst`; `repeats` = distinct flow ids on it), and a core with no scheduled work MUST get an inert `pong` that reserves nothing. This is the one place the offline schedule reaches the RTL directly. Guarded by `ContractRomTests` in [`tests/python/test_highway.py`](tests/python/test_highway.py).
- **Macro/module agreement.** `WAU_GRID_X/Y/Z`, `WAU_CORE_COUNT`, and the cache/coordinator/highway macros in `wau_defs.vh` MUST match the emitted modules. Single owner: `_render_defs` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py).
- **Stable host interface.** `wau_top` MUST keep exporting the `obs_total_*` observability bus (`hop_count`, `stall_count`, `forward_count`, `local_delivered_count`, `cache_hit_count`, `cache_lookup_count`). `wau_host_mmio` MUST keep its register map (`CTRL/STATUS/FLOW_ID/IN_A/IN_B/TRIGGER/OUT_FLOW/OUT_VAL/HOPS/STALLS/FORWARDS/DELIVRD/CACHE_H/CACHE_L`, plus the additive `CTR_GRNT/CTR_HOLD/CTR_DEFR` at `0x18`-`0x1A`) stable so host software targeting it does not silently break. New registers may only be **appended** at unused addresses. Guarded by [`tests/rtl/tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v); the map is published in [`README.md`](README.md#host-mmio-register-map).
- **Software reference parity.** `waugen.cw_reference.evaluate_flow` MUST produce the same `host_out_value` as the generated RTL for the deterministic benchmark cases; the generated CW exec testbench `$fatal`s on mismatch. Owned by [`cw_reference.py`](src/python/waugen/cw_reference.py); tested by [`tests/python/test_cw_reference.py`](tests/python/test_cw_reference.py) against hardware-golden values.
- **`arch-search` output is frozen.** `waugen.arch_search.run_arch_search` MUST stay byte-identical. `fit-config` / `run_fit_search` is strictly additive and MUST NOT alter `arch-search` behavior; it may only build and evaluate candidate payloads **in memory** (no scheduling or emission change). `CandidateKnobs.max_in_flight` defaults to `None` — leaving `coordinator.max_in_flight` untouched and omitting the `_mif` id suffix — precisely so `arch-search` reports stay byte-identical; only `fit-config` sets it, and swept depths MUST stay inside the coordinator's `[1,16]` range. Guarded by [`tests/python/test_arch_search.py`](tests/python/test_arch_search.py) (`test_arch_search_never_sweeps_max_in_flight`).
- **Benchmark log integrity.** `run_cw_example_benchmark.sh` MUST produce a valid [`benchmarks/example_pogram_benchmark.txt`](benchmarks/example_pogram_benchmark.txt) with passing RTL tests, CW execution metrics, and `scoreboard_pass_ratio == 1.0`. `run_cw_stress_benchmark.sh` writes **only** `benchmarks/mesh_stress_*` artifacts and MUST NEVER overwrite the CI-gated `example_pogram_*` files, while also holding `scoreboard_pass_ratio == 1.0`.
- **Live-board watchdog is a fault.** A DE0-Nano watchdog expiry is a circuit/configuration failure: abort on the first timeout and emit diagnostics. **Never** continue and report timeout-limited throughput. Owned by [`run_cw_stress_benchmark.py`](demo/de0-nano/basic-example/host/programs/run_cw_stress_benchmark.py).
- **SDRAM is inactive.** The board carries 32 MB external SDRAM, but the current wrapper holds it inactive. Do not count it as cache capacity until a controller/cache path exists.
- **`.cw` canonical location.** All real `.cw` programs live under [`CWs/`](CWs).

---

## Architecture and Data/Control Flow

**Generation (host, Python):**

```
config.json
  → config.load_config            (schema parse + validation → ProjectConfig)
  → compiler.compile_project      (flows/nodes → core placements, fallbacks, cycle groups → CompiledProject)
  → scheduler.build_schedule      (dependency-aware timeline, core selection → SchedulePlan)
  → verilog_emit.emit_verilog     (RTL text render) → thirds/veribuilder (manifest, headers, deterministic write)
  → src/verilog/generated/*
```

Front-ends feed the same chain by *merging a flow + program into a base config* before `load_config`:

```
--expr / --program  → basic_compiler.{merge_expression_into_config, merge_pseudoc_into_config}
kernel.cw           → cw_compiler.merge_cw_into_config
kernel.cw (host)    → cw_lang.{parse, Interpreter}          [no RTL path]
```

**Execution (hardware, generated RTL):**

```
host (MMIO write / vJTAG)
  → wau_host_mmio            (register file: FLOW_ID, IN_A, IN_B, TRIGGER)
  → wau_coordinator          (up to MAX_IN_FLIGHT distinct flows; one accumulator context per slot)
  → wau_highway_mesh         (control plane: dispatch packets; index-order chain, or XYZ dimension-order under `matrix`)
  → wau_highway_router × N   (per-core arbitration + hop/stall/forward/delivery counters)
  → wau_core → wau_core_station (dispatch, latency control, FIFO/LRU cache) → wau_operation_alu
  → wau_highway_contract     (data plane: slot offer → request/contract → exclusive grant)
  → wau_highway_mesh         (data plane: result packets back to coordinator)
  → wau_coordinator          (accumulate, emit host_out_flow_id / host_out_value)
  → wau_host_mmio            (OUT_FLOW / OUT_VAL, sticky output_pending)
```

**Trust and process boundaries.** The Python host and the FPGA are separate processes/devices bridged by `quartus_stp` + a TCL line-protocol TCP server (port 2540) → vJTAG → `wau_vjtag_bridge` → MMIO. The bridge crosses TCK↔CLOCK_50 with toggle-sync plus double-FF data crossing. Everything below `MMIO` in the host stack (`TCLClient`, `MMIO`) is WAU-agnostic.

**Verification loop.** `cw_reference.evaluate_flow` mirrors the coordinator state machine in Python and acts as the oracle; the generated CW exec testbench compares against it and `$fatal`s on mismatch.

---

## Linked Source Tree and File Reference

### [`src/python/waugen/config.py`](src/python/waugen/config.py)

Schema parsing and **validation only**. Owns every dataclass that later stages consume. It does **not** place, schedule, or emit.

- **Key types:** `ProjectConfig` (root), `DeviceSpec` (preset, grid, widths/depths, `data_types`, `coordinator_mode`, `highway`), `HighwaySpec` (topology + contract-bus limits), `OperationSpec`, `FlowSpec` / `FlowStageSpec` / `FlowNodeSpec` (linear stages vs DAG nodes), `PlacementSpec` (`core` / `fallback_core` / `candidate_cores` / `fixed` / `directive`), `CoreCapabilitySpec`, `StationCacheSpec`, `CompilerSpec`, `SchedulerSpec`, `CoordinatorSpec`, `ProgramSpec`, `Coord`.
- **Key functions:** `load_config` (path → `ProjectConfig`) and `load_config_obj` (in-memory payload → `ProjectConfig`, used by `arch_search` candidate evaluation); `_load_operations` enforces name/opcode uniqueness; `_parse_core_capabilities` validates op/dtype references; `_load_flows` / `_load_programs` resolve flow references by id or name; `_parse_dtype` enforces the `[a-z][a-z0-9_]{0,31}` dtype grammar.
- **Called by / depends on:** every entry point — [`cli.py`](src/python/waugen/cli.py), [`arch_search.py`](src/python/waugen/arch_search.py), [`scripts/run_randomized_stress.py`](scripts/run_randomized_stress.py); pulls presets from [`device_library.py`](src/python/waugen/device_library.py) and templates from [`operation_library.py`](src/python/waugen/operation_library.py).
- **Tests:** [`tests/python/test_foundation_alignment.py`](tests/python/test_foundation_alignment.py), [`tests/python/test_3d_grid.py`](tests/python/test_3d_grid.py), [`tests/python/test_coordinator_config.py`](tests/python/test_coordinator_config.py).
- **Common mistakes:** Adding a config key here without (a) a range check, (b) a matching emitter change or an explicit "Python-side only" note, and (c) a `README.md` config-model entry. `locality_bias` is the model for a Python-only knob; `max_in_flight` is the model for a knob that must reach a Verilog macro.

### [`src/python/waugen/compiler.py`](src/python/waugen/compiler.py)

Maps flows/nodes onto 2D or layered-3D cores, resolves fallbacks, and detects recurrence cycles. It does **not** decide *when* anything runs.

- **Key functions and subparts:**
  - `compile_project` — the single entry point; produces `CompiledProject` (flows → `CompiledFlow` → `CompiledNode` / `CompiledStage`).
  - `_flow_nodes_from_stages` — normalizes linear stage flows into the DAG node model so downstream code has one representation.
  - `_tarjan_scc` + `_build_cycle_groups` — find recurrent groups; gated by `compiler.allow_cycle_recurrence`.
  - `_build_linear_node_order` — deterministic topological ordering.
  - `_resolve_primary_core` / `_resolve_candidates` — apply placement directives, `fallback_radius`, and capability filters; `_coords_in_radius` and `_coord_distance` implement the Manhattan neighborhood.
  - `_all_coords` — enumerates coordinates in `waterfall` or `serpentine` order (`manual` routing requires explicit placement).
- **Called by / depends on:** [`scheduler.py`](src/python/waugen/scheduler.py), [`verilog_emit.py`](src/python/waugen/verilog_emit.py), [`cw_reference.py`](src/python/waugen/cw_reference.py), [`arch_search.py`](src/python/waugen/arch_search.py).
- **Tests:** [`tests/python/test_foundation_alignment.py`](tests/python/test_foundation_alignment.py), [`tests/python/test_advanced_scheduler.py`](tests/python/test_advanced_scheduler.py), [`tests/python/test_3d_grid.py`](tests/python/test_3d_grid.py).
- **Common mistakes:** Skipping the capability filter when resolving candidates. `manual` routing with no explicit placement must fail loudly, not silently fall back to `waterfall`.

### [`src/python/waugen/scheduler.py`](src/python/waugen/scheduler.py)

Multi-program, dependency-aware timing model plus the encoded schedule output. Also owns **routing-aware core selection**.

- **Key functions and subparts:**
  - `build_schedule` — the entry point; returns `SchedulePlan(instructions, makespan_cycles)`.
  - `_build_runtime_nodes` — expands programs × replicas × recurrence iterations into `_RuntimeNode`s with canonical `runtime_node_key`s (the determinism anchor).
  - `_select_program` — applies `scheduler.program_policy` (`weighted_fair`, `strict_priority`, `round_robin`) and per-program `load_balance`.
  - `_select_core` — earliest-free-cycle first, then `_locality_cost` (hop-distance weighting by `locality_bias`), then explicit deterministic keys.
  - `_core_release_cycle` — pipelined vs non-pipelined occupancy.
  - `_resolve_dep_iteration` — maps a dependency to the correct recurrence iteration.
  - `_dependency_transfer_metrics` — emits the `dependency_edges_v1` hop metric (true data-dependency edges only, **not** stage adjacency, **not** ordering-only edges).
  - `encode_instruction_word` — the 64-bit schedule word layout: `[63:56] opcode, [55:40] flow_id, [39:32] stage_index, [31:24] core_index, [23:16] latency, [15:8] flags, [7:0] immediate_b`. Flags: `0x01` used_fallback, `0x02` immediate present.
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py), [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`wau_schedule.json/.hex`), [`arch_search.py`](src/python/waugen/arch_search.py) (`_schedule_metrics`), the viewer's timeline.
- **Tests:** [`tests/python/test_scheduler_locality.py`](tests/python/test_scheduler_locality.py), [`tests/python/test_advanced_scheduler.py`](tests/python/test_advanced_scheduler.py), [`tests/python/test_program_stress.py`](tests/python/test_program_stress.py), [`tests/python/test_randomized_multiflow_stress.py`](tests/python/test_randomized_multiflow_stress.py).
- **Common mistakes:** Introducing a tie broken by `set`/`dict` iteration order — it passes locally and diverges under a different `PYTHONHASHSEED`. Always add an explicit key. Also: reordering the core-selection keys so locality outranks earliest-free-cycle silently breaks the `locality_bias=0.0` byte-identity contract.

### [`src/python/waugen/verilog_emit.py`](src/python/waugen/verilog_emit.py)

WAU-specific RTL **text rendering** only. No scheduling or placement decisions. Project assembly (manifest, headers, deterministic write) is delegated to [`veribuilder`](thirds/veribuilder/src/veribuilder/core.py).

- **Key functions and subparts (one renderer per emitted file):**
  - `emit_verilog` — the entry point; builds a `VerilogProject`, enables the `de0_nano` feature gate when the device preset name starts with `intel_de0_nano`, registers every file, and emits.
  - `_render_defs` — `wau_defs.vh`: the **single owner** of `WAU_GRID_X/Y/Z`, `WAU_CORE_COUNT`, `WAU_COORD_MAX_IN_FLIGHT`, `WAU_STATION_CACHE_*`, and per-operation opcode macros.
  - `_render_operation_alu` — ALU case generation from the operation table; structurally applies `compiler.core_capabilities` via `CORE_INDEX` so synthesis removes unsupported operations.
  - `_render_core_station` — dispatch, latency control, configurable FIFO/LRU multi-entry cache, `cache_hit_count`/`cache_lookup_count`.
  - `_render_neighbor_forward`, `_render_highway_router`, `_render_highway_mesh`, `_render_highway_contract` — the packet fabric, rendered per `device.highway.topology`. Under the default `linear` topology the router keeps `LOCAL`/`PREV`/`NEXT`(+`UP`/`DOWN`) and routes by comparing `dst_core` against `CORE_INDEX` (layer bounds first). Under `matrix` it computes `dst_x = dst_core % GRID_X`, `dst_y = (dst_core / GRID_X) % GRID_Y`, `dst_z = dst_core / (GRID_X*GRID_Y)` and routes **X first, then Y, then Z**. The two share one arbitration/observability body (`_HIGHWAY_ROUTER_BODY`) so they cannot drift.
  - `contract_rom_entries` / `_encode_contract_word` / `_render_contract_rom` — the schedule-derived per-core highway contract words (`{repeats, words, mode}`, 18 bits).
  - `_render_coordinator` (+ `_stage_case_entries`, `_flow_last_stage_entries`, `_flow_slot_entries`) — the multi-issue flow orchestrator and its `MAX_IN_FLIGHT` localparam.
  - `_render_top` — the grid top level and the `obs_total_*` aggregation.
  - `_render_host_mmio` — the fixed 32-bit register map.
  - `_render_de0_nano_wrapper` — board wrapper, emitted only under the `de0_nano` gate.
  - `_render_program_json` — the `wau_program.json` report.
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py) (`_run_generate`); consumes `CompiledProject` + `SchedulePlan`; writes through `VerilogProject.emit`.
- **Tests:** all of [`tests/rtl/`](tests/rtl) (via [`scripts/run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh)), plus [`tests/python/test_coordinator_config.py`](tests/python/test_coordinator_config.py) and [`tests/python/test_3d_grid.py`](tests/python/test_3d_grid.py) for macro/metadata assertions.
- **Common mistakes:** Changing a module's parameter without updating the matching macro in `_render_defs` — elaboration then succeeds with a silently wrong grid. Also: `dst_core % GRID_X` infers an `LPM_DIVIDE` per router port when `GRID_X` is not a power of two (see [Non-power-of-two grid blows the LE budget](#non-power-of-two-grid-blows-the-le-budget)).

### [`src/python/waugen/basic_compiler.py`](src/python/waugen/basic_compiler.py)

Expression and pseudo-C lowering. Targets **accumulator-style, left-deep chains** only, to stay compatible with the current coordinator execution model.

- **Key functions and subparts:**
  - `compile_expression_to_stages` / `_flatten` — lower `((a + b) * 3) - b`-shaped chains into `CompiledStageExpr`; `_operand_kind` and `_constant_value` classify operands (variable vs immediate).
  - `compile_pseudoc_to_stages` — accepts `acc = a; acc = acc <op> ...;` statement programs via Python's `ast`; `_strip_pseudoc_comments` and `_statement_from_source` prepare each statement.
  - `build_flow_from_expression` / `build_flow_from_pseudoc` — stages → flow payload.
  - `merge_expression_into_config` / `merge_pseudoc_into_config` — the public API; `_ensure_operations_present` injects any missing library operation into the base config, `_merge_flow_into_config` splices the flow in.
  - `_OP_MAP` — the Python AST operator → WAU operation name table; extend it when adding an operator.
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py) (`compile-expr`, `compile-pseudoc`).
- **Tests:** [`tests/python/test_basic_compiler.py`](tests/python/test_basic_compiler.py).
- **Common mistakes:** Assuming general expression trees work — non-chain expressions are rejected on purpose (`test_compile_expression_reject_non_chain`), and pseudo-C requires an explicit `acc` initialization.

### [`src/python/waugen/cw_compiler.py`](src/python/waugen/cw_compiler.py)

The **RTL-lowering** `.cw` path: a regex/template extractor that turns a kernel-shaped program into a DAG flow plus an execution program. It is **not** a parser — see [Two `.cw` paths](#two-cw-paths-host-language-vs-rtl-lowering).

- **Key functions and subparts:**
  - `parse_cw_program` → `(CWKernelSpec, CWWorkloadShape)`; `_extract_kernel_name`, `_extract_worker_count`, `_extract_required_int`, `_extract_workload_shape` do the regex extraction.
  - `validate_cw_pragmas` / `_parse_wau_pragmas` — the `// @wau key=value` contract. Supported keys are enumerated in `_SUPPORTED_WAU_PRAGMA_KEYS`; policies in `_SUPPORTED_WAU_PLACEMENT_POLICIES` (`locality`, `balance`), `_SUPPORTED_WAU_LOWERING_PROFILES` (`reference`, `latency_optimized`, `throughput_optimized`), `_SUPPORTED_WAU_PROGRAM_LOAD_BALANCE` (`least_busy`, `round_robin`).
  - `_CoreCapabilityFilter` / `_capability_filter_from_payload` / `_candidate_cores` — capability-aware candidate pruning: incompatible cores are removed **before** validation so bad topology combinations fail early during autotune.
  - `build_flow_from_cw_program` — the lowering core (lane parallelism, tiling hint via `_tile_iteration_hint`, placement policy).
  - `merge_cw_into_config` (+ `_upsert_flow`, `_upsert_program`, `_ensure_operations_present`, `_load_base_device_profile`) — splice into a base config.
  - Precedence rule: explicit CLI flags > `@wau` pragmas > compile defaults (`_DEFAULT_CW_MAX_IN_FLIGHT = 4`, `_DEFAULT_CW_PROGRAM_PRIORITY = 2`, `_DEFAULT_CW_PROGRAM_LOAD_BALANCE = "least_busy"`).
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py) (`compile-cw`, and the template side of `cw-lint`), both benchmark scripts, [`arch_search.py`](src/python/waugen/arch_search.py) (`fit-config` compiles a raw `.cw`), the viewer's [`prepare.py`](tools/wau-pipelines-viewer/wau_viewer/prepare.py).
- **Tests:** [`tests/python/test_cw_compiler.py`](tests/python/test_cw_compiler.py), [`tests/python/test_cli_cw_lint.py`](tests/python/test_cli_cw_lint.py).
- **Common mistakes:** Changing the lowering without updating [`cw_reference.py`](src/python/waugen/cw_reference.py) — they must move in lock-step or the scoreboard `$fatal`s. Also: wiring [`cw_lang.py`](src/python/waugen/cw_lang.py) in here "because it is a real parser" — that boundary is deliberate.

### [`src/python/waugen/cw_lang.py`](src/python/waugen/cw_lang.py)

The **real host-side** `.cw` front-end: lexer → AST → recursive-descent parser → tree-walking interpreter. Owns compile-time class magic methods. It MUST NOT be wired into RTL lowering or the benchmark gate.

- **Key functions and subparts:**
  - `tokenize` / `Token` — lexer; `_KEYWORDS`, `_OPS`, `DTYPES`.
  - `Parser` / `parse` — produces `Program` from `AliasDecl`, `ClassDecl` (keyword `class` or legacy `space`), `FuncDecl`; statement/expression nodes are `VarDecl`, `Assign`, `Return`, `If`, `For`, `ExprStmt`, `Literal`, `Name`, `Member`, `Index`, `Call`, `New`, `Unary`, `Binary`.
  - `Interpreter` — the evaluator; `Instance`, `DramArray`, `_System`, `_Env`, `_BoundMethod`, `_BoundBuiltin` are its runtime objects.
  - `Interpreter.convert(value, dtype)` — **the toolchain hook** for dynamic type-format conversion; dispatches to `__convert__` / `__to_int__` / `__to_float__`.
  - `_BINOP_MAGIC` — operator → magic-method table (`__add__`, `__sub__`, `__mul__`, `__div__`, `__mod__`, comparisons, `__neg__`, `__str__`).
  - `_c_div` / `_c_mod` — C semantics: integer division truncates toward zero and division by zero yields zero.
  - `run_program` / `load_program` — convenience entry points.
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py) (`cw-eval`, and the syntax side of `cw-lint`).
- **Tests:** [`tests/python/test_cw_lang.py`](tests/python/test_cw_lang.py) (including `test_all_repo_samples_parse`, which parses every `.cw` under [`CWs/`](CWs)).
- **Common mistakes:** Adding a sample under [`CWs/`](CWs) that the parser rejects — `test_all_repo_samples_parse` fails immediately. Also: assuming `__div__` follows Python semantics; it follows C.
- **Grammar authority:** [`docs/cw-language.md`](docs/cw-language.md).

### [`src/python/waugen/cw_reference.py`](src/python/waugen/cw_reference.py)

Software reference model for CW flows — the correctness oracle. Makes **one pass over `flow.stages` in linear order**, mirroring the coordinator state machine.

- **Key functions and subparts:** `evaluate_flow(flow, a, b)`; `evaluate_project_flow(project, flow_id, a, b)`; `compute_expected_values` (one row per benchmark case); `_apply_op` with 32-bit wrap semantics (`_DATA_WIDTH = 32`, `_MASK`, `_SIGN_BIT`, `_to_signed`).
- **Called by / depends on:** [`scripts/run_cw_example_benchmark.sh`](scripts/run_cw_example_benchmark.sh) (writes `.build/cw_iverilog/cw_scoreboard.json`, consumed by the generated exec testbench).
- **Tests:** [`tests/python/test_cw_reference.py`](tests/python/test_cw_reference.py) — checks against **hardware-golden** values, not just self-consistency.
- **Common mistakes:** Widening coverage past the calibrated `add`/`mul`/`max` paths without re-validating on hardware goldens. Parity across the wider operation set is a [known gap](#known-gaps).

### [`src/python/waugen/arch_search.py`](src/python/waugen/arch_search.py)

Two related but **separately governed** searches: synthesis-time architecture ranking (`arch-search`) and the simulator-driven fit finder (`fit-config`).

- **Key functions and subparts:**
  - `run_arch_search` / `ArchSearchReport` / `_rank_key` — enumerate and rank candidates. Versioned models: `SCHEMA_VERSION = "wau_arch_search_v1"`, `RESOURCE_MODEL_VERSION = "wau_resource_model_v1"`, `DRAM_MODEL_VERSION = "dram_model_v1"`, `RANKING_VERSION = "arch_search_rank_v1"` (feasible first, then lower makespan, transfer hops, DRAM bytes, peak utilization).
  - `CandidateKnobs` / `build_candidate_payload` — the swept dimensions: grid shape (`_grid_shapes`), op distribution (`OP_DISTRIBUTIONS = uniform, heavy_column, heavy_half`), memory split (`MEMORY_SPLITS`), and — **fit-only** — `max_in_flight`.
  - `_estimate_resources` / `_op_lut_cost` / `_estimate_dram` — the estimate models, checked against the device preset's datasheet capacity.
  - `_evaluate_candidate` / `_schedule_metrics` — every candidate runs the **real** `compile_project → build_schedule`, so makespan/hop/fallback numbers are the generator's own.
  - `run_fit_search` / `FitBudget` / `FitReport` / `_fits_budget` / `_fit_grid_shapes` — the device-budget sweep; `FIT_SCHEMA_VERSION = "wau_fit_search_v1"`, defaults `_DEFAULT_FIT_MAX_UTILIZATION = 0.9`, `_DEFAULT_FIT_TOLERANCE = 0.10`.
  - `_profiled_capability_entries` — the **fit-only** `profiled` distribution (`FIT_OP_DISTRIBUTIONS`), deriving the exact per-core operation set actually dispatched, which the emitter then makes structural.
  - `_fit_max_in_flight_values` — coordinator-depth co-sweep, clamped by `_COORD_MAX_IN_FLIGHT = 16`.
  - `emit_fit_config` / `_knobs_from_candidate_id` — rebuild an exact evaluated candidate as a ready-to-build config.
  - `format_report_text` / `format_fit_report_text` — the printed ranked tables.
- **Called by / depends on:** [`cli.py`](src/python/waugen/cli.py) (`arch-search`, `fit-config`) and [`scripts/find_best_wau_config.py`](scripts/find_best_wau_config.py).
- **Tests:** [`tests/python/test_arch_search.py`](tests/python/test_arch_search.py).
- **Common mistakes:** Adding a sweep dimension that also applies to `arch-search`. Any new `CandidateKnobs` field must default to a value that leaves `arch-search` payloads and candidate ids byte-identical — follow the `max_in_flight = None` pattern.

### [`src/python/waugen/benchmark_replay.py`](src/python/waugen/benchmark_replay.py)

Deterministic parsing and selection of saved CW autotune candidates. Runs both as a library and as `python3 -m waugen.benchmark_replay` (invoked by the benchmark script).

- **Key functions and subparts:** `parse_tuning_summary` (regex `_FIELD_RE` over the saved summary; `_REQUIRED_FIELDS` gates validity), `select_replay_candidates`, `build_replay_plan`, `_REPLAY_MODES = {best, stage-winners, best-and-stage-winners, worst}`, `_shell_value` (converts `auto` to an empty shell override), `main`.
- **Called by / depends on:** [`scripts/run_cw_example_benchmark.sh`](scripts/run_cw_example_benchmark.sh) under `REPLAY_MODE`; reads `benchmarks/example_pogram_tuning_latest.txt`.
- **Tests:** [`tests/python/test_benchmark_replay.py`](tests/python/test_benchmark_replay.py).
- **Common mistakes:** Treating historical hop metrics as comparable to `dependency_edges_v1` — the report deliberately labels both metric versions.

### [`src/python/waugen/cli.py`](src/python/waugen/cli.py)

The single CLI entry point (`python3 -m waugen`). One `_run_*` handler per subcommand; it wires modules together and owns no domain logic.

- **Key functions and subparts:** `_build_parser` (the subcommand registry — the authoritative list of public commands), `main`; handlers `_run_generate`, `_run_validate`, `_run_compile_expr`, `_run_compile_pseudoc`, `_run_compile_cw`, `_run_cw_eval`, `_run_cw_lint`, `_run_arch_search`, `_run_fit_config`, `_run_list_devices`, `_run_list_operations`; helpers `_parse_entry` (`x,y[,z]`) and `_parse_grid` (`2x4` / `2x4x1`).
- **Called by / depends on:** [`__main__.py`](src/python/waugen/__main__.py); every script and the viewer shell out to it.
- **Tests:** [`tests/python/test_cli_cw_lint.py`](tests/python/test_cli_cw_lint.py) (subprocess-level).
- **Common mistakes:** Adding a subcommand without mirroring it in the [Interface Ownership Map](#interface-ownership-map) and [`README.md`](README.md).

### [`src/python/waugen/device_library.py`](src/python/waugen/device_library.py)

Real FPGA device presets. `DevicePreset` carries part metadata **and** the synthesis capacity fields (`logic_cells`, `bram_kbits`, `dsp_blocks`) that [`arch_search.py`](src/python/waugen/arch_search.py) feasibility checks depend on.

- **Key subparts:** `DEVICE_PRESETS` (`intel_de0_nano` — Cyclone IV E EP4CE22F17C6N, default grid 4x3, 32-bit; `intel_agilex7_fm` — default grid 12x10, 64-bit; `xilinx_artix7_100t` — default grid 8x6, 32-bit) and `get_device_preset`.
- **Common mistakes:** Adding a preset with placeholder capacity numbers — `fit-config` then recommends a grid that cannot be synthesized. Validate at least one config against any new preset.

### [`src/python/waugen/operation_library.py`](src/python/waugen/operation_library.py)

Built-in operation templates. `OperationTemplate` carries opcode, latency, `pipelined`, and the Verilog expression.

- **Key subparts:** `OPERATION_LIBRARY` — 12 operations: `add`(0x01), `sub`(0x02), `mul`(0x03), `div`(0x04), `mod`(0x05), `min`(0x06), `max`(0x07), `and`(0x08), `or`(0x09), `xor`(0x0A), `shl`(0x0B), `shr`(0x0C). `div`/`mod` are latency 8 and **not** pipelined. `get_operation_template`.
- **Common mistakes:** Adding a combinational multi-cycle operation without setting `pipelined=False` and a truthful latency — see [Combinational divide result latched too early](#combinational-divide-result-is-latched-too-early).

### [`src/python/waugen/utils.py`](src/python/waugen/utils.py)

Three shared helpers used across the generator: `macro_name` (identifier → Verilog macro spelling), `validate_range`, `clamp`. Keep it free of domain types.

### [`src/python/waugen/__main__.py`](src/python/waugen/__main__.py) and [`__init__.py`](src/python/waugen/__init__.py)

Package entry (`python3 -m waugen` → `cli.main`) and the package docstring. No logic belongs here.

### [`src/python/configs/`](src/python/configs)

Tracked example and reference configurations. Grouping rule: `wau_*_demo.json` are hand-written examples, `wau_*_compiled*.json` are tracked outputs of a compile subcommand, and two files carry contracts of their own:

- [`wau_de0_nano_demo.json`](src/python/configs/wau_de0_nano_demo.json) — the default config for [`scripts/run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh) and the README quickstart.
- [`wau_2d_multiprogram_demo.json`](src/python/configs/wau_2d_multiprogram_demo.json) — advanced DAG + multi-program example; the base config for the CW example benchmark. **Validate it whenever flow compilation or scheduling changes.**
- [`wau_3d_demo.json`](src/python/configs/wau_3d_demo.json) — minimal layered-3D example. **Validate it whenever core indexing, placement, or routing dimensions change**, together with [`tests/rtl/tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v).
- [`wau_matrix_highway_demo.json`](src/python/configs/wau_matrix_highway_demo.json) — **contract:** the `wau_de0_nano_demo` flows with `device.highway.topology = "matrix"` and nothing else changed, so the two topologies are compared like for like. [`run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh) generates from it and re-runs the whole fabric suite; keep it in step with `wau_de0_nano_demo.json`.
- [`wau_cw_fit_base.json`](src/python/configs/wau_cw_fit_base.json) — **contract:** minimal, demo-independent DE0-Nano base used to compile a raw `.cw` for `fit-config`, by [`scripts/run_cw_stress_benchmark.sh`](scripts/run_cw_stress_benchmark.sh), and as the viewer's default `.cw` base. It carries the full `add/sub/mul/div/max` op set so the fixed ALU testbench elaborates. The board keeps its own lean `add/mul/max` base at [`wau_de0_nano_cw_stress_base.json`](demo/de0-nano/basic-example/host/config/wau_de0_nano_cw_stress_base.json).
- [`wau_de0_nano_example_2x4_profiled.json`](src/python/configs/wau_de0_nano_example_2x4_profiled.json) — **contract:** the exact source config for the validated Quartus Lite 25.1 profiled 2x4 board image. Keep its per-core capabilities and compiled placements reproducible from the CW/build knobs recorded in the benchmark.
- [`wau_de0_nano_compiled_expr.json`](src/python/configs/wau_de0_nano_compiled_expr.json), [`wau_de0_nano_compiled_pseudoc.json`](src/python/configs/wau_de0_nano_compiled_pseudoc.json), [`wau_example_pogram_compiled.json`](src/python/configs/wau_example_pogram_compiled.json) — tracked outputs of `compile-expr`, `compile-pseudoc`, and `compile-cw`; regenerate rather than hand-edit.

### `src/verilog/generated/` — build output, do not edit

Emitted by `waugen generate`. Tracked so CI and reviewers can diff it, but the **generator is the owner**: `wau_defs.vh`, `wau_operation_alu.v`, `wau_neighbor_forward.v`, `wau_highway_contract.v`, `wau_highway_router.v`, `wau_highway_mesh.v`, `wau_core_station.v`, `wau_core.v`, `wau_coordinator.v`, `wau_host_mmio.v`, `<output_module_name>.v` (demo: `wau_top.v`), `wau_de0_nano_top.v` (DE0-Nano gate only), `wau_program.json`, `wau_schedule.json`, `wau_schedule.hex`. To change any of these, edit the matching `_render_*` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py) and regenerate.

### [`thirds/veribuilder/src/veribuilder/core.py`](thirds/veribuilder/src/veribuilder/core.py)

Generic, WAU-agnostic Verilog project assembly. See the nested handbook at [`thirds/veribuilder/AGENTS.md`](thirds/veribuilder/AGENTS.md) for its local rules.

- **Key types:** `VerilogProject` (`enable`, `add_file`, `add_verilog`, `selected_files`, `emit`), `GeneratedFile` (+ `enabled` feature gate), `VerilogHeader` (`spdx`, `text`, `apply` — idempotent), `TemplateRenderer` (`render`, deliberately minimal substitution only).
- **Called by / depends on:** [`verilog_emit.py`](src/python/waugen/verilog_emit.py) only. It MUST NOT import WAU modules or depend on WAU config/compiler/scheduler types.
- **Tests:** [`tests/python/test_veribuilder.py`](tests/python/test_veribuilder.py).
- **Common mistakes:** Growing `TemplateRenderer` into a control-flow language, or letting a WAU-specific concept leak in — either blocks the planned standalone extraction. File emission must stay deterministic in registration order; non-Verilog files must not receive Verilog headers.

### [`scripts/run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh)

Generate + compile + simulate the whole RTL testbench set. Takes an optional config argument (default [`wau_de0_nano_demo.json`](src/python/configs/wau_de0_nano_demo.json)), writes RTL to `src/verilog/generated`, binaries to `.build/iverilog`.

- **Key subparts:** `run_test <toplevel> <sources...>` — compiles with `iverilog -g2005-sv -I $RTL_DIR -s <name>` and runs `vvp`; `mesh_sources` / `top_sources` are the shared source sets; `run_suite` is the whole fabric suite. Runs, in order: `tb_wau_operation_alu`, `tb_wau_top_demo`, `tb_wau_coordinator_multiissue`, `tb_wau_highway_mesh`, `tb_wau_highway_mesh_3d`, `tb_wau_highway_contract`, `tb_wau_host_mmio`, then `tb_wau_highway_linear` and `tb_wau_highway_linear_3d` (linear RTL only). It then **regenerates a second RTL tree** from [`wau_matrix_highway_demo.json`](src/python/configs/wau_matrix_highway_demo.json) and re-runs `run_suite` against it, so the opt-in `matrix` topology never ships unelaborated.
- **Common mistakes:** Adding a testbench that needs a new generated module without adding that module to the `run_test` source list — `iverilog` reports an unresolved instance rather than a missing file.

### [`scripts/run_cw_example_benchmark.sh`](scripts/run_cw_example_benchmark.sh)

The benchmark **engine** (~2.3k lines): compile → validate → generate → iverilog exec → scoreboard, plus autotune, replay, multi-run stability, and regression-guard modes. Defaults: `CWs/example-program.cw`, base [`wau_2d_multiprogram_demo.json`](src/python/configs/wau_2d_multiprogram_demo.json), output config [`wau_example_pogram_compiled.json`](src/python/configs/wau_example_pogram_compiled.json), log [`benchmarks/example_pogram_benchmark.txt`](benchmarks/example_pogram_benchmark.txt), build dir `.build/cw_example_generated`.

- **Modes (environment variables):** `TUNE_MODE=1` (staged coordinate search: topology stage → program stage → scheduler stage), `REPLAY_MODE=<best|stage-winners|best-and-stage-winners|worst>` (delegates to `python3 -m waugen.benchmark_replay`), `MULTI_RUNS=N`, `REGRESSION_CHECK=1` (knobs `REGRESSION_MAX_LATENCY_DELTA`, `REGRESSION_MAX_MAKESPAN_DELTA`, `REGRESSION_MAX_TOTAL_MS_DELTA`, `REGRESSION_BASELINE_JSON`).
- **Manual knobs:** `CW_LANE_PARALLELISM`, `CW_PLACEMENT_POLICY`, `CW_LOWERING_PROFILE`, `CW_MAX_IN_FLIGHT`, `CW_DTYPE`, `PROGRAM_REPLICAS`, `PROGRAM_MAX_PARALLEL`, `PROGRAM_PRIORITY`, `PROGRAM_LOAD_BALANCE`, `SCHEDULER_PROGRAM_POLICY`, `RUN_PROFILE`, `EXEC_TIMEOUT_CYCLES` (default 5000).
- **Depends on:** `waugen.cw_reference.compute_expected_values`, which writes `.build/cw_iverilog/cw_scoreboard.json` for the generated exec testbench.
- **Common mistakes:** Repointing its output paths instead of using the stress wrapper — the `example_pogram_*` artifacts are the CI gate.

### [`scripts/run_cw_stress_benchmark.sh`](scripts/run_cw_stress_benchmark.sh)

Thin wrapper over the same engine for the ad-hoc mesh-stress kernel. Defaults: [`CWs/stress/mesh_stress.cw`](CWs/stress/mesh_stress.cw), base [`wau_cw_fit_base.json`](src/python/configs/wau_cw_fit_base.json), output config `.build/cw_stress/wau_mesh_stress_compiled.json`, log [`benchmarks/mesh_stress_benchmark.txt`](benchmarks/mesh_stress_benchmark.txt). It accepts any `.cw` as `$1`. **It must write only `benchmarks/mesh_stress_*` artifacts.**

### [`scripts/run_randomized_stress.py`](scripts/run_randomized_stress.py)

Randomized multi-flow scheduler stress (a CI input). Generates seeded configs over `OPS = [add, sub, mul, max]`, sweeps `compiler.station_cache.{entries, replacement_policy}`, runs `compile_project → build_schedule`, and emits a coverage-style JSON report. `--count 25` is the local smoke pass; CI runs 50 seeds from `--start-seed 2000`.

### [`scripts/find_best_wau_config.py`](scripts/find_best_wau_config.py)

Thin convenience wrapper over `waugen fit-config` — accepts a `.cw` kernel or a `.json` workload and prints the best-performance and efficient/knee recommendations. **All logic stays in [`arch_search.py`](src/python/waugen/arch_search.py).**

### [`scripts/sync_license_headers.py`](scripts/sync_license_headers.py)

Inserts/refreshes SPDX headers (`PolyForm-Noncommercial-1.0.0` + `See LICENSE at the repository root.`) across `src/**/*.py`, `src/**/*.v`, `src/**/*.vh`. `--check` is the review gate; header constants must stay in sync with `_SPDX_IDENTIFIER` / `_LICENSE_REF_TEXT` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py).

### [`scripts/fetch_dataset.py`](scripts/fetch_dataset.py) (+ [`.ps1`](scripts/fetch_dataset.ps1))

On-demand, git-ignored dataset download (MNIST, ~11 MB, via the CVDF mirror) for data-exchange testing. Skip-if-present; `--dry-run`, `--force`, `--dest`. Also exposes `load_mnist_images` / `load_mnist_labels` readers (no numpy). `datasets/` is git-ignored and **never committed**. Network access required.

### [`tests/rtl/`](tests/rtl) — Verilog testbenches

Each testbench owns one contract; all are run by [`scripts/run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh).

- [`tb_wau_operation_alu.v`](tests/rtl/tb_wau_operation_alu.v) — ALU opcode behavior. Note: it elaborates against the generated ALU, which is why fit/board base configs must carry the expected op set.
- [`tb_wau_top_demo.v`](tests/rtl/tb_wau_top_demo.v) — end-to-end flow execution through coordinator/highway/core grid; **its timing is the serial-equivalence reference** for `max_in_flight=1`.
- [`tb_wau_coordinator_multiissue.v`](tests/rtl/tb_wau_coordinator_multiissue.v) — proves ≥2 cores busy concurrently for independent flows, and that unknown flow ids are accepted-but-dropped.
- [`tb_wau_highway_mesh.v`](tests/rtl/tb_wau_highway_mesh.v) — neighbor forwarding, valid/ready backpressure, and `router_hop_count` advancement.
- [`tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v) — vertical `up`/`down` routing across `grid.z` layers.
- [`tb_wau_highway_linear.v`](tests/rtl/tb_wau_highway_linear.v) — the single-dimension chain on a multi-row grid: all-pairs reachability over one chain, the row-to-row wrap hop, and that a disabled contract bus is completely inert. Meaningful only against `linear` RTL.
- [`tb_wau_highway_linear_3d.v`](tests/rtl/tb_wau_highway_linear_3d.v) — the layered case: one chain **per layer**, joined only by the vertical links. Sweeps all 56 ordered pairs on a 2x2x2 grid, because a chain-per-layer arrangement is where dropping the planar links could silently partition the fabric.
- [`tb_wau_highway_contract.v`](tests/rtl/tb_wau_highway_contract.v) — the contracting bus: idle slot cycling with no gating, pong = one beat, contract exclusivity + deferral counting, quiet-holder release, lease bound, and that the round-robin resumes after the holder.
- [`tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v) — the MMIO register map: writes, reads, sticky `output_pending` semantics, observability counter readback.

### [`tests/python/`](tests/python) — unit and stress tests

Standard `unittest`; discovered with `-p "test_*.py"`. Mapped to the contracts they protect in the [Test Ownership Map](#test-ownership-map). Grouping rule: one file per subsystem, except [`test_arch_search.py`](tests/python/test_arch_search.py) which covers both `arch-search` and `fit-config`, and the two viewer files which are the Qt-free viewer coverage.

### [`tools/wau-pipelines-viewer/`](tools/wau-pipelines-viewer)

iverilog-driven visual simulator (PySide6). Not in CI — **GUI rendering stays CI-unverified** (no PySide6 in the headless environment). Run from inside the tool directory.

#### [`wau_viewer/prepare.py`](tools/wau-pipelines-viewer/wau_viewer/prepare.py)

Ad-hoc circuit preparation: `--config <config.json>` or `--cw <kernel.cw>` compiles and emits fresh RTL by **shelling out to the real `waugen compile-cw` / `waugen generate`**. It MUST NEVER re-implement lowering or emission. Artifacts land in git-ignored `.build/viewer/<name>/`; the `.cw` default base is [`wau_cw_fit_base.json`](src/python/configs/wau_cw_fit_base.json). RTL source discovery finds extra emitted `wau_*.v` modules dynamically while excluding board wrappers/MMIO glue.

#### [`wau_viewer/model.py`](tools/wau-pipelines-viewer/wau_viewer/model.py)

Static layout model derived from `wau_program.json` + `wau_schedule.json`. Owns the seeded stress-stimulus generator, `HighwayInfo` (read back from the program report), and the route/segment reconstruction: `linear_route`/`linear_segments` for the default single-dimension highway and `manhattan_route`/`matrix_segments` for `matrix`, dispatched by `WauModel.highway_route` / `highway_segments`. **Both MUST mirror `wau_highway_router`** — `manhattan_route` its X-then-Y-then-Z priority, `linear_route` its index compare — so animated packets travel the routers the RTL actually forwards them through. If the emitter's routing changes, these change with it. Tested by [`tests/python/test_viewer_sim_prep.py`](tests/python/test_viewer_sim_prep.py).

#### [`wau_viewer/graph_view.py`](tools/wau-pipelines-viewer/wau_viewer/graph_view.py)

Zoomable `QGraphicsView` of the mesh. Owns the phased packet animation (dispatch → execute → result), the offset parallel lanes for concurrent packets, the in-scene concurrency HUD (busy cores, packets in flight, peak parallel ops, hops/stalls), and the **highway scheme** — `HighwayStub` per core, `HighwaySlotMarker` for the offered slot, and the contract HUD line. The scheme is driven entirely by the trace's `HWY`/`hwy_*` records, never inferred. Playback is paced by a deterministic `advance(t)` clock so headless recordings replay the same frames as live playback.

#### [`wau_viewer/recorder.py`](tools/wau-pipelines-viewer/wau_viewer/recorder.py)

MP4/GIF encoding — ffmpeg with a palette pass, and a pure-Pillow fallback for GIF.

#### Remaining viewer modules

[`__main__.py`](tools/wau-pipelines-viewer/wau_viewer/__main__.py) (entry point: prepare → simulate → launch Qt), [`simulator.py`](tools/wau-pipelines-viewer/wau_viewer/simulator.py) (drives `iverilog`/`vvp`), [`tb_generator.py`](tools/wau-pipelines-viewer/wau_viewer/tb_generator.py) (generates the per-config tracing testbench), [`trace_parser.py`](tools/wau-pipelines-viewer/wau_viewer/trace_parser.py) (parses the per-cycle trace, including the `HWY` highway/contract-bus record and the per-core `hwy_req`/`hwy_call`/`hwy_hold` flags; covered by [`test_viewer_data_trace.py`](tests/python/test_viewer_data_trace.py)), [`main_window.py`](tools/wau-pipelines-viewer/wau_viewer/main_window.py) (transport controls, docks, recording), [`timeline_view.py`](tools/wau-pipelines-viewer/wau_viewer/timeline_view.py) (Gantt timeline with playhead), [`stats_panel.py`](tools/wau-pipelines-viewer/wau_viewer/stats_panel.py) (bottleneck readout).

### [`demo/de0-nano/basic-example/`](demo/de0-nano/basic-example)

End-to-end physical deployment. Local README: [`demo/de0-nano/basic-example/README.md`](demo/de0-nano/basic-example/README.md).

#### [`quartus/rtl/wau_vjtag_bridge.v`](demo/de0-nano/basic-example/quartus/rtl/wau_vjtag_bridge.v)

**Reusable, device-agnostic** 4-bit-IR JTAG↔MMIO master. Crosses TCK↔CLOCK_50 with toggle-sync + double-FF data crossing. Drop it into any Altera design needing a host-driven Avalon-MM-style register file. Paired with [`vJTAG.v`](demo/de0-nano/basic-example/quartus/rtl/vJTAG.v), a thin `sld_virtual_jtag` wrapper.

#### [`quartus/rtl/wau_jtag_top.v`](demo/de0-nano/basic-example/quartus/rtl/wau_jtag_top.v)

The **board-specific** pin-level top. Board-specific wiring belongs here and nowhere else, per the device-portability principle.

#### [`host/waujtag/`](demo/de0-nano/basic-example/host/waujtag)

Layered host stack: [`client.py`](demo/de0-nano/basic-example/host/waujtag/client.py) (`TCLClient`, TCP line protocol) → [`mmio.py`](demo/de0-nano/basic-example/host/waujtag/mmio.py) (`MMIO`, generic 32-bit master) → [`wau.py`](demo/de0-nano/basic-example/host/waujtag/wau.py) (`WAU`, register-map-aware) → [`benchmark.py`](demo/de0-nano/basic-example/host/waujtag/benchmark.py) (`Bench`). **The lower two layers know nothing about WAU** — keep it that way.

#### [`host/programs/run_cw_stress_benchmark.py`](demo/de0-nano/basic-example/host/programs/run_cw_stress_benchmark.py)

Live DE0-Nano scoreboard/observability harness for `CWs/example-program.cw`-class flows compiled into the board wrapper. Supports `--mnist-images <path.gz>` to stream real operands instead of random ones. **Owns the fail-fast watchdog contract.** Siblings: [`run_benchmark.py`](demo/de0-nano/basic-example/host/programs/run_benchmark.py) (the basic 3-flow demo) and [`run_iris_stats_benchmark.py`](demo/de0-nano/basic-example/host/programs/run_iris_stats_benchmark.py) (fixed-point Iris morphology workload over the tracked CSV).

#### [`host/tcl/wau_jtag_server.tcl`](demo/de0-nano/basic-example/host/tcl/wau_jtag_server.tcl)

Generic `quartus_stp`-hosted TCL line-protocol server (TCP 2540). Device-agnostic.

#### [`scripts/`](demo/de0-nano/basic-example/scripts) — PowerShell automation (Windows + Quartus host)

`build.ps1`, `build_cw_stress.ps1`, `program.ps1`, `server.ps1`, `run.ps1`, `run_cw_stress.ps1`, `run_iris_stats.ps1`. **Common mistake — already fixed once, still easy to reintroduce:** these run under `$ErrorActionPreference = "Stop"`; Quartus writes benign `TBBmalloc` text to stderr at startup, which PowerShell promotes to a terminating error and aborts the build before compilation. Native Quartus calls must relax the preference and check the real exit code instead. POSIX-friendly equivalents live in [`Makefile`](demo/de0-nano/basic-example/Makefile) (`generate`, `build`, `build-cw-stress`, `program`, `server`, `run`, `run-iris`, `run-cw-stress`, `clean`).

#### [`host/config/`](demo/de0-nano/basic-example/host/config)

[`wau_de0_nano_basic.json`](demo/de0-nano/basic-example/host/config/wau_de0_nano_basic.json) (the 3-flow demo image) and [`wau_de0_nano_cw_stress_base.json`](demo/de0-nano/basic-example/host/config/wau_de0_nano_cw_stress_base.json) (**lean** `add/mul/max` board base — deliberately narrower than the simulator's `wau_cw_fit_base.json`, to save LEs).

### [`CWs/`](CWs) — canonical `.cw` programs

Grouping rule: RTL-lowering kernels at the top level, host-language samples under `samples/`.

- [`example-program.cw`](CWs/example-program.cw) — the compiler-oriented Conv2D-residual reference kernel; **the CI benchmark gate's input**.
- [`stress/mesh_stress.cw`](CWs/stress/mesh_stress.cw) — ad-hoc kernel built to saturate the mesh rather than stress the compiler.
- [`basic_arithmetic.cw`](CWs/basic_arithmetic.cw) — the small board demo kernel.
- [`samples/types/fixed_point.cw`](CWs/samples/types/fixed_point.cw) — the `cw-eval` / magic-method reference (`q8_8`).
- [`samples/nn/`](CWs/samples/nn) — [`linear.cw`](CWs/samples/nn/linear.cw), [`gru.cw`](CWs/samples/nn/gru.cw), [`transformer.cw`](CWs/samples/nn/transformer.cw); host-language samples, **not** `compile-cw` templates.
- **Every file here must parse under [`cw_lang.py`](src/python/waugen/cw_lang.py)** — `test_all_repo_samples_parse` enforces it.

### [`benchmarks/`](benchmarks) — measured records

Grouping rule by prefix. `example_pogram_*` = the CI-gated simulator benchmark for `example-program.cw` (`.txt` reference log plus `_latest/_best/_history.json` sidecars, `_tuning_latest.txt`, `_replay_latest.txt`, `_multirun_latest.txt`). `mesh_stress_*` = the ad-hoc stress kernel's own simulator log and sidecars. `de0_nano_*` = **live board records** ([`de0_nano_basic_benchmark.txt`](benchmarks/de0_nano_basic_benchmark.txt), [`de0_nano_iris_stats_benchmark.txt`](benchmarks/de0_nano_iris_stats_benchmark.txt), [`de0_nano_cw_stress_benchmark.txt`](benchmarks/de0_nano_cw_stress_benchmark.txt), [`de0_nano_mesh_stress_benchmark.txt`](benchmarks/de0_nano_mesh_stress_benchmark.txt) and their JSON sidecars) — historical run-time records that MUST NOT be rewritten, including the tracked failure telemetry [`de0_nano_cw_stress_2x4_timeout.json`](benchmarks/de0_nano_cw_stress_2x4_timeout.json).

### [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

Four jobs on push/PR to `main` plus `workflow_dispatch`: `python-tests` (full unittest discovery, Python 3.11), then in parallel `randomized-stress` (50 seeds, JSON artifact), `iverilog-tests` (installs `iverilog`, uploads generated RTL, 14-day retention), and `cw-benchmark` (runs both benchmark scripts with the tuned knobs pinned as `env:`, surfaces summaries into the Step Summary, uploads `benchmarks/*` + `cw_scoreboard.json`, 30-day retention). **Keep the matrix aligned with the local Core Workflow** — a new local validation step must be mirrored here.

### [`docs/`](docs)

[`cw-language.md`](docs/cw-language.md) is the authoritative `.cw` contract (see [Read This First](#read-this-first)). [`example-program-syntax.c`](docs/example-program-syntax.c) and [`diagram.drawio`](docs/diagram.drawio) are design references; [`WaterfallArithmeticUnit.en.pdf`](docs/WaterfallArithmeticUnit.en.pdf) is the background paper. None of these are executable contracts.

### [`base_reference_projects/`](base_reference_projects)

Untouched third-party/reference material: [`DE0_NANO_Base/`](base_reference_projects/DE0_NANO_Base) is Terasic's stock board project (pinout/SDC reference) and [`programs_syntax/`](base_reference_projects/programs_syntax) holds language-shape references. **Read-only context — do not treat as WAU source.**

---

## Features and Recurring Development Pitfalls

### Expression and pseudo-C frontends — Shipped

- **Behavior:** `compile-expr` lowers left-deep arithmetic chains (`((a + b) * 3) - b`); `compile-pseudoc` lowers accumulator programs (`acc = a; acc = acc + b; acc *= 3;`). Both merge a new flow into a base config.
- **Flow and owners:** CLI → [`basic_compiler.py`](src/python/waugen/basic_compiler.py) (`compile_expression_to_stages` / `compile_pseudoc_to_stages` → `merge_*_into_config`) → `config.json`.
- **Constraints:** Non-chain expressions and uninitialized `acc` are rejected by design, to stay compatible with the coordinator execution model.
- **Tests and gaps:** [`test_basic_compiler.py`](tests/python/test_basic_compiler.py). Gap: general DAG expressions and common-subexpression reuse are `ROADMAP.md` Phase 2, not implemented.

### `.cw` → RTL lowering — Shipped

- **Behavior:** `compile-cw` turns a kernel-shaped `.cw` program into a DAG flow + execution program, with `@wau` pragma tuning and capability-aware core pruning.
- **Flow and owners:** CLI (`_run_compile_cw`) → [`cw_compiler.py`](src/python/waugen/cw_compiler.py) (`parse_cw_program` → `build_flow_from_cw_program` → `merge_cw_into_config`) → the standard generator chain.
- **Constraints:** Regex/template extraction, not parsing — the accepted kernel shape is narrow. Precedence: CLI flags > pragmas > defaults. Must move in lock-step with [`cw_reference.py`](src/python/waugen/cw_reference.py).
- **Tests and gaps:** [`test_cw_compiler.py`](tests/python/test_cw_compiler.py), [`test_cli_cw_lint.py`](tests/python/test_cli_cw_lint.py); the benchmark scoreboard is the end-to-end gate.

### `.cw` host language (classes, magic methods, conversions) — Shipped

- **Behavior:** `cw-eval` runs `main()` on the host; `cw-lint` validates syntax and pragmas. Classes support Python-style magic methods including the conversion hooks `__to_int__` / `__to_float__` / `__convert__`, so the toolchain can bridge numeric formats dynamically.
- **Flow and owners:** CLI (`_run_cw_eval` / `_run_cw_lint`) → [`cw_lang.py`](src/python/waugen/cw_lang.py) (`tokenize` → `Parser.parse` → `Interpreter`), with `Interpreter.convert` as the documented toolchain hook.
- **Constraints:** C division semantics (truncate toward zero; `x/0 == 0`). This path **never** lowers to RTL and is **never** part of the benchmark gate.
- **Tests and gaps:** [`test_cw_lang.py`](tests/python/test_cw_lang.py); grammar in [`docs/cw-language.md`](docs/cw-language.md).

### Multi-issue coordinator — Shipped

- **Behavior:** `coordinator.max_in_flight` distinct flows execute concurrently across the mesh (one accumulator context per slot), so independent flows injected back-to-back actually overlap on different cores. `1` reproduces the legacy serial coordinator exactly.
- **Flow and owners:** [`config.py`](src/python/waugen/config.py) (`CoordinatorSpec`) → [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_coordinator`, `_render_defs`) → `wau_coordinator.v` + `WAU_COORD_MAX_IN_FLIGHT`.
- **Constraints:** `[1,16]`; at most one in-flight slot **per flow id** (the packet format is untagged); unknown flow ids accepted-but-dropped.
- **Tests and gaps:** [`tb_wau_coordinator_multiissue.v`](tests/rtl/tb_wau_coordinator_multiissue.v), [`tb_wau_top_demo.v`](tests/rtl/tb_wau_top_demo.v) (serial equivalence), [`test_coordinator_config.py`](tests/python/test_coordinator_config.py).

### Highway mesh, station cache, and observability — Shipped

- **Behavior:** Control-plane dispatch and data-plane results traverse explicit neighbor-linked router meshes with valid/ready backpressure. Per-core stations hold a configurable 1..32-entry FIFO/LRU cache. Counters for hops/stalls/forwards/local-deliveries and cache hits/lookups aggregate at `wau_top` and are readable over MMIO.
- **Flow and owners:** `_render_highway_router` / `_render_highway_mesh` / `_render_core_station` / `_render_top` / `_render_host_mmio` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py).
- **Constraints:** `WAU_STATION_CACHE_*` macros must agree with the emitted station; the `obs_total_*` bus and the MMIO map are stable public interfaces.
- **Tests and gaps:** [`tb_wau_highway_mesh.v`](tests/rtl/tb_wau_highway_mesh.v), [`tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v), [`tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v). Gap: no elastic/registered router links, which currently caps the board clock (see [Known Gaps](#known-gaps)).

### Single-dimension highway topology — Shipped

- **Behavior:** `device.highway.topology` selects the highway's dimensionality. `linear` (**the default**) gives each layer one 1-D highway walked in core-index order — the last core of a row is the previous hop of the first core of the next row — so routers keep 5 ports instead of 7 and route by plain index compare. In a layered 3D grid every layer gets its own highway, joined by the vertical links. `matrix` restores the full N/S/E/W(/U/D) mesh with X-then-Y-then-Z routing for kernels that need the cross-section.
- **Flow and owners:** [`config.py`](src/python/waugen/config.py) (`HighwaySpec`) → [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_highway_dirs`, `_render_highway_router`, `_render_highway_mesh`, `_render_defs`) → `wau_highway_router.v` / `wau_highway_mesh.v` / `WAU_HIGHWAY_*`; mirrored in the viewer by `model.linear_route` / `linear_segments`.
- **Constraints:** The `linear` router must stay free of `% GRID_X` and `/ GRID_X` — that is what removes the per-port `LPM_DIVIDE` and with it the [non-power-of-two LE blow-up](#non-power-of-two-grid-blows-the-le-budget) *for any grid shape*. The trade is highway cross-section: one path between any two cores, so heavy all-to-all traffic contends where a mesh would spread. `matrix` is never the default, and CI must keep elaborating it (see [`wau_matrix_highway_demo.json`](src/python/configs/wau_matrix_highway_demo.json)) or the opt-in path ships unexercised.
- **Tests and gaps:** [`test_highway.py`](tests/python/test_highway.py), [`tb_wau_highway_linear.v`](tests/rtl/tb_wau_highway_linear.v), plus the whole fabric suite run twice by [`run_iverilog_tests.sh`](scripts/run_iverilog_tests.sh). Gap: the schedule's `dependency_edges_v1` transfer-hop metric and `_coord_distance` still model Manhattan distance, which understates hops on a linear highway (see [Known Gaps](#known-gaps)); no board calibration of the area saving.

### Highway contracting bus — Shipped

- **Behavior:** `wau_highway_contract` offers one core slot per clock. On its own slot a core answers either with a bare request bit (`pong` — one beat, reserves nothing) or with an 18-bit contract word `{repeats, words, mode}` saying **how** (`pong`/`burst`/`stream`/`reserve`), **how much** (beats per run) and **how many times**. While a contract is in force the highway admits only its holder, so a contracted transfer neither interleaves with other traffic nor re-arbitrates per beat. Both drivers are wired: the real-time one (`data_contract_req = core_result_valid`) and the programmed one (a per-core word derived from the offline schedule).
- **Flow and owners:** [`config.py`](src/python/waugen/config.py) (`HighwaySpec.contract_*`) → [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_highway_contract`, `contract_rom_entries`, `_render_contract_rom`, the `_HIGHWAY_MESH_CONTRACT_BLOCK` admission gate, `_render_top`, `_render_host_mmio`) → `wau_highway_contract.v` + `obs_total_contract_*` + MMIO `0x18`-`0x1A`; surfaced in the viewer's highway scheme.
- **Constraints:** Non-blocking when idle; exclusive when contracted; bounded by beats **and** lease; releases on a quiet holder; round-robin resumes after the holder. Data plane only. `admit` stays registered so no combinational path is added through the routers.
- **Tests and gaps:** [`tb_wau_highway_contract.v`](tests/rtl/tb_wau_highway_contract.v) (slot cycling, pong, exclusivity, quiet-holder release, lease bound, no re-take), `ContractBusEmissionTests`/`ContractRomTests` in [`test_highway.py`](tests/python/test_highway.py), and the highway records in [`test_viewer_data_trace.py`](tests/python/test_viewer_data_trace.py). Gap: because the bus is non-blocking when idle, contracts fire only under genuine contention — on the small demo kernels most injections never need one, so the exclusivity path is exercised by the dedicated testbench rather than by the benchmark gate. No board measurement of its effect yet.

### Layered 3D grid (`grid.z > 1`) — Experimental/scaffold

- **Behavior:** `grid.z > 1` emits layered core indexing and vertical `up`/`down` mesh links.
- **Flow and owners:** [`config.py`](src/python/waugen/config.py) (`Coord`, grid parsing) → [`compiler.py`](src/python/waugen/compiler.py) (`_all_coords`, `_coord_distance`) → [`verilog_emit.py`](src/python/waugen/verilog_emit.py).
- **Constraints:** **Safe boundary — verified in `iverilog` only.** It has not been calibrated on the DE0-Nano board flow; do not present 3D resource or timing numbers as measured.
- **Tests and gaps:** [`test_3d_grid.py`](tests/python/test_3d_grid.py), [`tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v). Gap: no board calibration; the viewer does not render layers.

### Architecture search and fit finder — Shipped

- **Behavior:** `arch-search` ranks synthesis candidates over grid shape, op specialization, memory split, and DRAM reliance. `fit-config` sweeps grid shapes up to a device budget, co-sweeps `coordinator.max_in_flight`, and recommends a best-performance and a fewest-cores (knee) config, emitting a ready-to-build JSON.
- **Flow and owners:** CLI → [`arch_search.py`](src/python/waugen/arch_search.py) (`run_arch_search` / `run_fit_search` → `_evaluate_candidate` → real `compile_project`/`build_schedule`) → report JSON + optional config; wrapper [`find_best_wau_config.py`](scripts/find_best_wau_config.py).
- **Constraints:** `arch-search` output is byte-frozen; `fit-config` is additive and in-memory only. Area/BRAM/DSP figures come from the versioned estimate models, **not** from a synthesis tool.
- **Tests and gaps:** [`test_arch_search.py`](tests/python/test_arch_search.py). Gap: estimates are not calibrated against real Quartus/Vivado results or board-measured scores.

### Pipelines viewer — Shipped (CI-unverified GUI)

- **Behavior:** Compiles an ad-hoc circuit from a config or `.cw`, runs the real generated RTL through iverilog, and replays it as a phased slow-motion animation with a concurrency HUD, Gantt timeline, bottleneck panel, and MP4/GIF export. It also draws the **highway scheme**: the links of the topology actually emitted, plus a contract-bus rail with one numbered tick per core slot, a stub tapping each core onto it (dim / dashed when requesting / amber when the core *calls* the highway on its own slot / solid red while it holds it under contract), and a marker showing the slot being offered. All of it is read from the RTL trace's `HWY`/`hwy_*` records.
- **Flow and owners:** [`__main__.py`](tools/wau-pipelines-viewer/wau_viewer/__main__.py) → [`prepare.py`](tools/wau-pipelines-viewer/wau_viewer/prepare.py) (shells out to `waugen`) → [`tb_generator.py`](tools/wau-pipelines-viewer/wau_viewer/tb_generator.py) → [`simulator.py`](tools/wau-pipelines-viewer/wau_viewer/simulator.py) → [`trace_parser.py`](tools/wau-pipelines-viewer/wau_viewer/trace_parser.py) → [`model.py`](tools/wau-pipelines-viewer/wau_viewer/model.py) → [`graph_view.py`](tools/wau-pipelines-viewer/wau_viewer/graph_view.py).
- **Constraints:** Needs PySide6 (and ffmpeg for MP4). Not run in CI.
- **Tests and gaps:** Qt-free coverage in [`test_viewer_sim_prep.py`](tests/python/test_viewer_sim_prep.py) and [`test_viewer_data_trace.py`](tests/python/test_viewer_data_trace.py) — the latter asserts against real RTL that every result-producing core is seen asking for the highway and that the bus slot actually cycles. Gaps: per-router queue/backpressure visualization, `grid.z` layer rendering, and any GUI-rendering regression.

### Live DE0-Nano deployment — Shipped

- **Behavior:** Host Python drives the board over USB-Blaster + virtual JTAG; operands go in over MMIO and results are checked against the software reference.
- **Flow and owners:** [`host/programs/*`](demo/de0-nano/basic-example/host/programs) → [`waujtag`](demo/de0-nano/basic-example/host/waujtag) → [`wau_jtag_server.tcl`](demo/de0-nano/basic-example/host/tcl/wau_jtag_server.tcl) → [`wau_vjtag_bridge.v`](demo/de0-nano/basic-example/quartus/rtl/wau_vjtag_bridge.v) → `wau_host_mmio` → `wau_top`.
- **Constraints:** Requires Windows + Quartus + the physical board. Watchdog expiry is a hard failure. Per-trigger wall-clock latency (~15 ms) is dominated by JTAG round-trip, not by the WAU.
- **Tests and gaps:** Live scoreboard runs recorded in [`benchmarks/de0_nano_*`](benchmarks). Gap: no closed-loop reprogramming without a rebuild/restart.

---

### Non-power-of-two grid blows the LE budget

- **Symptom / wrong assumption:** A 3×2 grid that "should be small" fails to fit on an EP4CE22 (26,866 vs 22,320 LEs), while a larger 2×4 fits.
- **Cause and invariant:** `dst_core % GRID_X` and `dst_core / GRID_X` in the emitted router infer an `LPM_DIVIDE` per router port when `GRID_X` is not a power of two. Power-of-two `GRID_X` collapses them to bit-selects.
- **Risk area:** [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_highway_router`), and any `fit-config`/`arch-search` grid recommendation.
- **Safe pattern / regression check:** Prefer power-of-two `grid.x` for board targets; when reporting a fit, use the estimate models *and* an actual synthesis run. `PYTHONPATH=src/python python3 -m waugen fit-config --program-file <kernel.cw> --max-grid 2x4` surfaces the budget.
- **Status:** Deliberate limitation of the **`matrix`** router arithmetic; documented in [`README.md`](README.md) and [`benchmarks/de0_nano_cw_stress_benchmark.txt`](benchmarks/de0_nano_cw_stress_benchmark.txt). The default `linear` topology has no `% GRID_X` / `/ GRID_X` at all, so it does not have this failure mode for any grid shape — but the board images recorded in `benchmarks/de0_nano_*` predate it and were built on the `matrix` fabric. Do not retro-apply the saving to those numbers.

### Combinational divide result is latched too early

- **Symptom / wrong assumption:** `div` results read back as garbage on hardware while simulating correctly.
- **Cause and invariant:** `wau_operation_alu.v` emits a purely combinational signed `div` whose 32-bit settling time exceeds one 50 MHz period on Cyclone IV E, and `wau_core_station.v` latches `alu_out_value` on the first cycle after dispatch. Any operation whose real settling time exceeds its declared latency will be sampled early.
- **Risk area:** [`operation_library.py`](src/python/waugen/operation_library.py) (`OPERATION_LIBRARY` latency/`pipelined` fields), [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_operation_alu`, `_render_core_station`).
- **Safe pattern / regression check:** Declare honest latency and `pipelined=False` for multi-cycle operations. The upstream fix is to defer the result latch to `wait_cycles == 0` or use a pipelined `LPM_DIVIDE`. Board benchmarks currently exclude `div`.
- **Status:** **Active known bug.**

### Two `.cw` paths: host language vs RTL lowering

- **Symptom / wrong assumption:** Assuming there is one `.cw` compiler, then "fixing" `cw_compiler.py` by routing it through the real parser in `cw_lang.py`, or expecting an `nn/` sample to lower to RTL.
- **Cause and invariant:** [`cw_compiler.py`](src/python/waugen/cw_compiler.py) is a regex/template extractor feeding the CI-gated RTL path. [`cw_lang.py`](src/python/waugen/cw_lang.py) is a real lexer/parser/interpreter for host-side compile-time behavior (custom numeric formats and their conversions). The separation keeps the benchmark gate insulated from language-surface churn.
- **Risk area:** [`cw_compiler.py`](src/python/waugen/cw_compiler.py), [`cw_lang.py`](src/python/waugen/cw_lang.py), and `_run_cw_lint` in [`cli.py`](src/python/waugen/cli.py) (which spans both: syntax from `cw_lang`, `--compile-template` from `cw_compiler`).
- **Safe pattern / regression check:** Use `cw-lint --compile-template` as the preflight for kernels intended for `compile-cw`; plain `cw-lint` for host-side programs. [`test_cli_cw_lint.py`](tests/python/test_cli_cw_lint.py) asserts both directions, including that a host-only program fails the template lint.
- **Status:** Deliberate limitation.

### Hash-seed-dependent schedules

- **Symptom / wrong assumption:** A schedule that is stable locally but differs in CI or across reruns; a benchmark delta that will not reproduce.
- **Cause and invariant:** Ties broken by `set`/`dict` iteration order vary with `PYTHONHASHSEED`. Every tie must be broken by an explicit key (`program_replica`, `runtime_node_key`).
- **Risk area:** [`scheduler.py`](src/python/waugen/scheduler.py) (`_select_core`, `_select_program`, `_build_runtime_nodes`), [`compiler.py`](src/python/waugen/compiler.py) (`_resolve_candidates`).
- **Safe pattern / regression check:** Sort explicitly before any "first match" decision. `test_schedule_is_hash_seed_independent_across_processes` in [`test_scheduler_locality.py`](tests/python/test_scheduler_locality.py) spawns separate processes with different seeds.
- **Status:** Fixed failure mode; remains a live regression risk on every scheduler edit.

### Config knob added without its RTL counterpart

- **Symptom / wrong assumption:** A new `compiler.*` or `coordinator.*` key validates and appears in the config, but the generated RTL ignores it — simulation "passes" against a design that does not implement the knob.
- **Cause and invariant:** [`config.py`](src/python/waugen/config.py) validates; `_render_defs` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py) is the *only* place macros are defined. A knob is either explicitly Python-side-only (like `locality_bias`) or it reaches a macro *and* the module that consumes it (like `max_in_flight`, `station_cache`).
- **Risk area:** [`config.py`](src/python/waugen/config.py), [`verilog_emit.py`](src/python/waugen/verilog_emit.py).
- **Safe pattern / regression check:** Decide and *document* which category the knob is in, in the same edit. Add a macro/localparam assertion test in the style of [`test_coordinator_config.py`](tests/python/test_coordinator_config.py) (`test_emits_macro_and_module_parameter`).
- **Status:** Deliberate design; recurring risk.

### Viewer route drifting from the emitted router

- **Symptom / wrong assumption:** Animated packets take a visibly different path than the hardware, making the visualization quietly wrong.
- **Cause and invariant:** `model.highway_route` reconstructs routes in Python; the authority is `_render_highway_router`. There are now two orders to keep mirrored — the index compare of the default `linear` chain and the X-then-Y-then-Z dimension order of `matrix` — and the viewer must pick between them from `wau_program.json`'s `device.highway.topology`, not from a hardcoded assumption.
- **Risk area:** [`model.py`](tools/wau-pipelines-viewer/wau_viewer/model.py), [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_highway_router`).
- **Safe pattern / regression check:** Any routing-order change updates both in one edit. `ManhattanRouteTests` asserts X-first ordering against neighbor steps, and `LinearHighwayRouteTests` asserts consecutive-index walking plus that every reconstructed hop is a drawn highway link — both in [`test_viewer_sim_prep.py`](tests/python/test_viewer_sim_prep.py).
- **Status:** Fixed failure mode; live regression risk.

### Stress benchmark overwriting the CI gate

- **Symptom / wrong assumption:** A mesh-stress tuning run silently replaces the tracked example-program reference log, and CI's gate now measures the wrong kernel.
- **Cause and invariant:** Both scripts share one engine. [`run_cw_stress_benchmark.sh`](scripts/run_cw_stress_benchmark.sh) exists only to repoint every output path; it must write `benchmarks/mesh_stress_*` and nothing else.
- **Risk area:** [`scripts/run_cw_stress_benchmark.sh`](scripts/run_cw_stress_benchmark.sh), [`scripts/run_cw_example_benchmark.sh`](scripts/run_cw_example_benchmark.sh).
- **Safe pattern / regression check:** To benchmark a different kernel, pass it as `$1` to the **stress** wrapper. After any benchmark run, `git status benchmarks/` must show only the intended prefix.
- **Status:** Deliberate limitation of the shared engine.

---

## Interface Ownership Map

**CLI (`python3 -m waugen <cmd>`)** — registry: `_build_parser` in [`cli.py`](src/python/waugen/cli.py).

| Command | Handler | Domain owner |
|---|---|---|
| `generate` | `_run_generate` | [`verilog_emit.py`](src/python/waugen/verilog_emit.py) |
| `validate` | `_run_validate` | [`config.py`](src/python/waugen/config.py) + [`compiler.py`](src/python/waugen/compiler.py) + [`scheduler.py`](src/python/waugen/scheduler.py) |
| `compile-expr` | `_run_compile_expr` | [`basic_compiler.py`](src/python/waugen/basic_compiler.py) |
| `compile-pseudoc` | `_run_compile_pseudoc` | [`basic_compiler.py`](src/python/waugen/basic_compiler.py) |
| `compile-cw` | `_run_compile_cw` | [`cw_compiler.py`](src/python/waugen/cw_compiler.py) |
| `cw-eval` | `_run_cw_eval` | [`cw_lang.py`](src/python/waugen/cw_lang.py) |
| `cw-lint` | `_run_cw_lint` | [`cw_lang.py`](src/python/waugen/cw_lang.py) (syntax) + [`cw_compiler.py`](src/python/waugen/cw_compiler.py) (`--compile-template`) |
| `arch-search` | `_run_arch_search` | [`arch_search.py`](src/python/waugen/arch_search.py) (`run_arch_search`) |
| `fit-config` | `_run_fit_config` | [`arch_search.py`](src/python/waugen/arch_search.py) (`run_fit_search`, `emit_fit_config`) |
| `list-devices` | `_run_list_devices` | [`device_library.py`](src/python/waugen/device_library.py) |
| `list-operations` | `_run_list_operations` | [`operation_library.py`](src/python/waugen/operation_library.py) |

Secondary CLI: `python3 -m waugen.benchmark_replay` → [`benchmark_replay.py`](src/python/waugen/benchmark_replay.py) (`main`).

**Hardware module interfaces** — all emitted; registry is `emit_verilog` in [`verilog_emit.py`](src/python/waugen/verilog_emit.py).

| Module | Renderer | Public surface |
|---|---|---|
| `wau_host_mmio` | `_render_host_mmio` | **Stable** word-addressed register map (`0x00 CTRL` … `0x17 CACHE_L`), published in [`README.md`](README.md#host-mmio-register-map) |
| `<output_module_name>` (e.g. `wau_top`) | `_render_top` | **Stable** `obs_total_*` observability bus + host in/out ports |
| `wau_coordinator` | `_render_coordinator` | `MAX_IN_FLIGHT` localparam; dispatch/result packet channels |
| `wau_highway_mesh` / `wau_highway_router` / `wau_neighbor_forward` | `_render_highway_*`, `_render_neighbor_forward` | valid/ready neighbor links; per-router counter buses; the contract-bus port group (identical across topologies) |
| `wau_highway_contract` | `_render_highway_contract` | slot offer / request / contract word in, `admit` + `call` + grant state out |
| `wau_core` / `wau_core_station` / `wau_operation_alu` | `_render_core*`, `_render_operation_alu` | `CORE_INDEX`-gated ALU elaboration; cache counters |
| `wau_de0_nano_top` | `_render_de0_nano_wrapper` | Board wrapper; emitted only under the `de0_nano` feature gate |
| `wau_defs.vh` | `_render_defs` | Sole owner of `WAU_*` macros |

**File formats produced:** `wau_program.json` (`_render_program_json`), `wau_schedule.json` (`SchedulePlan.to_json`), `wau_schedule.hex` (`encode_instruction_word`, 64-bit words).

**Host protocol:** TCL line protocol on TCP 2540 → [`wau_jtag_server.tcl`](demo/de0-nano/basic-example/host/tcl/wau_jtag_server.tcl); client [`client.py`](demo/de0-nano/basic-example/host/waujtag/client.py).

**Python library APIs:** `veribuilder.__all__` in [`thirds/veribuilder/src/veribuilder/__init__.py`](thirds/veribuilder/src/veribuilder/__init__.py); `waugen.cw_lang.Interpreter.convert` as the type-conversion toolchain hook; `scripts/fetch_dataset.py`'s `load_mnist_images` / `load_mnist_labels`.

---

## Build, Run, Test, Debug, and Release

**Prerequisites.** Python 3.11 (CI pins it; no third-party runtime dependency for the generator). `iverilog` + `vvp` for RTL tests. PySide6 (+ optional `ffmpeg`) for the viewer only — see [`tools/wau-pipelines-viewer/requirements.txt`](tools/wau-pipelines-viewer/requirements.txt). Windows + Quartus + a physical DE0-Nano for the board flow. There is no install step: run from the repo root with `PYTHONPATH=src/python`.

### Core workflow

```bash
python3 scripts/sync_license_headers.py
```

```bash
PYTHONPATH=src/python python3 -m waugen validate --config src/python/configs/wau_de0_nano_demo.json
```

```bash
PYTHONPATH=src/python python3 -m waugen generate --config src/python/configs/wau_de0_nano_demo.json --out src/verilog/generated --summary
```

### Frontends

```bash
PYTHONPATH=src/python python3 -m waugen compile-expr --expr '((a + b) * 3) - b' --flow-id 30 --entry 1,0 --base-config src/python/configs/wau_de0_nano_demo.json --out-config src/python/configs/wau_de0_nano_compiled_expr.json
```

```bash
PYTHONPATH=src/python python3 -m waugen compile-pseudoc --program 'acc = a; acc = acc + b; acc *= 3;' --flow-id 31 --entry 1,1 --base-config src/python/configs/wau_de0_nano_demo.json --out-config src/python/configs/wau_de0_nano_compiled_pseudoc.json
```

```bash
PYTHONPATH=src/python python3 -m waugen cw-lint --program-file CWs/example-program.cw --compile-template
```

```bash
PYTHONPATH=src/python python3 -m waugen compile-cw --program-file CWs/example-program.cw --flow-id 90 --entry 0,0 --base-config src/python/configs/wau_2d_multiprogram_demo.json --out-config src/python/configs/wau_example_pogram_compiled.json --replace-existing
```

```bash
PYTHONPATH=src/python python3 -m waugen cw-eval --program-file CWs/samples/types/fixed_point.cw --convert 'new q8_8(384)' float32
```

### Tests

```bash
PYTHONPATH=src/python python3 -m unittest discover -s tests/python -p "test_*.py" -v
```

```bash
./scripts/run_iverilog_tests.sh
```

```bash
PYTHONPATH=src/python python3 scripts/run_randomized_stress.py --start-seed 2000 --count 25 --report .build/stress/randomized.json
```

### Benchmarks (mutate tracked `benchmarks/` logs)

```bash
./scripts/run_cw_example_benchmark.sh
```

```bash
TUNE_MODE=1 ./scripts/run_cw_example_benchmark.sh
```

```bash
REPLAY_MODE=best-and-stage-winners ./scripts/run_cw_example_benchmark.sh
```

```bash
MULTI_RUNS=5 ./scripts/run_cw_example_benchmark.sh
```

```bash
REGRESSION_CHECK=1 ./scripts/run_cw_example_benchmark.sh
```

```bash
./scripts/run_cw_stress_benchmark.sh
```

### Architecture exploration

```bash
PYTHONPATH=src/python python3 -m waugen arch-search --config src/python/configs/wau_example_pogram_compiled.json --out-report .build/arch_search/report.json
```

```bash
PYTHONPATH=src/python python3 -m waugen fit-config --program-file CWs/stress/mesh_stress.cw --max-grid 2x4 --out-report .build/fit/report.json --out-config .build/fit/best.json
```

```bash
python3 scripts/find_best_wau_config.py CWs/stress/mesh_stress.cw
```

### Visual debugging (needs PySide6; run from `tools/wau-pipelines-viewer/`)

```bash
python3 -m wau_viewer --config examples/wau_3x3_demo.json --stress 6
```

```bash
python3 -m wau_viewer --cw ../../CWs/example-program.cw --stress 8
```

```bash
python3 -m wau_viewer --config examples/wau_3x3_demo.json --stress 6 --record examples/wau_3x3_demo.gif --framerate 10 --frames-per-cycle 6 --headless
```

### Datasets (network access; writes git-ignored `datasets/`)

```bash
python3 scripts/fetch_dataset.py
```

### Board flow (Windows + Quartus + physical DE0-Nano; mutates device state)

```bash
demo/de0-nano/basic-example/scripts/build_cw_stress.ps1 -GridX 2 -GridY 2 -QuartusRoot <quartus_root>
```

```bash
demo/de0-nano/basic-example/scripts/program.ps1 -QuartusRoot <quartus_root>
```

```bash
demo/de0-nano/basic-example/scripts/server.ps1 -QuartusRoot <quartus_root>
```

```bash
demo/de0-nano/basic-example/scripts/run_cw_stress.ps1 -Config demo/de0-nano/basic-example/build/cw_stress_2x2_merged.json -RandomIters 1024 -RandomRange 1023
```

Defaults to the ad-hoc [`CWs/stress/mesh_stress.cw`](CWs/stress/mesh_stress.cw); pass `-ProgramFile CWs/example-program.cw` for the original reference kernel. Add `--mnist-images datasets/mnist/t10k-images-idx3-ubyte.gz` to `run_cw_stress_benchmark.py` to stream real operands instead of random ones. POSIX equivalents: `make -C demo/de0-nano/basic-example help`.

**Release.** There is no package release for the generator. [`thirds/veribuilder/`](thirds/veribuilder) has its own `pyproject.toml` and extraction checklist — see [`thirds/veribuilder/AGENTS.md`](thirds/veribuilder/AGENTS.md).

---

## Test Ownership Map

| Contract / subsystem | Focused test |
|---|---|
| Config schema, capabilities, manual-routing rule, dtype grammar | [`test_foundation_alignment.py`](tests/python/test_foundation_alignment.py) |
| `grid.z` indexing, placement, emitted metadata, bounds rejection | [`test_3d_grid.py`](tests/python/test_3d_grid.py) + [`tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v) |
| `coordinator.max_in_flight` range, macro, and localparam | [`test_coordinator_config.py`](tests/python/test_coordinator_config.py) + [`tb_wau_coordinator_multiissue.v`](tests/rtl/tb_wau_coordinator_multiissue.v) |
| Serial-coordinator timing equivalence at depth 1 | [`tb_wau_top_demo.v`](tests/rtl/tb_wau_top_demo.v) |
| `locality_bias` semantics, hop metric definition, hash-seed independence | [`test_scheduler_locality.py`](tests/python/test_scheduler_locality.py) |
| Multi-program DAG + recurrence scheduling | [`test_advanced_scheduler.py`](tests/python/test_advanced_scheduler.py) |
| Priority × replicas × policy matrix, station-cache FIFO/LRU scheduling | [`test_program_stress.py`](tests/python/test_program_stress.py) |
| Randomized multi-flow scheduler stability | [`test_randomized_multiflow_stress.py`](tests/python/test_randomized_multiflow_stress.py) + [`scripts/run_randomized_stress.py`](scripts/run_randomized_stress.py) |
| Expression / pseudo-C lowering and config merge | [`test_basic_compiler.py`](tests/python/test_basic_compiler.py) |
| `.cw` RTL lowering, pragmas, capability-aware candidates, stress kernel shape | [`test_cw_compiler.py`](tests/python/test_cw_compiler.py) |
| `cw-lint` CLI behavior (both lint modes, line-located errors) | [`test_cli_cw_lint.py`](tests/python/test_cli_cw_lint.py) |
| `.cw` lexer/parser/interpreter, magic methods, C division semantics, all repo samples parse | [`test_cw_lang.py`](tests/python/test_cw_lang.py) |
| Software reference vs hardware-golden values | [`test_cw_reference.py`](tests/python/test_cw_reference.py) |
| `arch-search` determinism/byte-identity, `fit-config` budget and emission | [`test_arch_search.py`](tests/python/test_arch_search.py) |
| Autotune summary parsing and replay selection | [`test_benchmark_replay.py`](tests/python/test_benchmark_replay.py) |
| `veribuilder` feature gates, headers, template rendering | [`test_veribuilder.py`](tests/python/test_veribuilder.py) |
| Viewer route reconstruction, stress determinism, RTL discovery, ad-hoc prep | [`test_viewer_sim_prep.py`](tests/python/test_viewer_sim_prep.py) |
| Viewer data-plane trace parsing against real RTL | [`test_viewer_data_trace.py`](tests/python/test_viewer_data_trace.py) |
| ALU opcode behavior | [`tb_wau_operation_alu.v`](tests/rtl/tb_wau_operation_alu.v) |
| Mesh forwarding, backpressure, hop counters | [`tb_wau_highway_mesh.v`](tests/rtl/tb_wau_highway_mesh.v) |
| `device.highway` schema, macro/module agreement for both topologies, contract ROM derivation | [`test_highway.py`](tests/python/test_highway.py) |
| Single-dimension chain routing incl. the row-to-row wrap hop, inert disabled bus | [`tb_wau_highway_linear.v`](tests/rtl/tb_wau_highway_linear.v) |
| Layered single-dimension highway: chain per layer, all-pairs cross-layer reachability | [`tb_wau_highway_linear_3d.v`](tests/rtl/tb_wau_highway_linear_3d.v) |
| Contract bus: slot cycling, pong, exclusivity, lease bound, quiet-holder release | [`tb_wau_highway_contract.v`](tests/rtl/tb_wau_highway_contract.v) |
| MMIO register map, sticky `output_pending`, counter readback | [`tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v) |
| End-to-end value correctness under tuning | [`scripts/run_cw_example_benchmark.sh`](scripts/run_cw_example_benchmark.sh) (`scoreboard_pass_ratio == 1.0`) |

**Known test gaps.** No automated coverage for: the PySide6 GUI rendering path (no PySide6 in CI); the PowerShell board scripts; the vJTAG bridge RTL and the `waujtag` host stack (exercised only by live board runs); `cw_reference` parity beyond the calibrated `add`/`mul`/`max` paths; synthesis-calibrated accuracy of the `arch_search` estimate models. No static typing or lint gate exists for the Python sources ([`ROADMAP.md`](ROADMAP.md) technical debt).

---

## Data, Security, Privacy, and Compatibility Boundaries

- **Canonical vs derived.** Canonical: [`src/python/waugen/`](src/python/waugen), [`src/python/configs/*_demo.json`](src/python/configs) and the two contract configs, [`CWs/`](CWs), [`tests/`](tests), [`scripts/`](scripts), [`demo/`](demo/de0-nano/basic-example) RTL/host sources. Derived and regenerable: everything in `src/verilog/generated/`, `src/python/configs/*compiled*.json`, `benchmarks/*.json` sidecars. Untracked runtime: `.build/` (all build/simulation scratch) and `datasets/`.
- **Records that must not be rewritten.** `benchmarks/de0_nano_*` and dated `ROADMAP.md` entries are historical run records, including failure telemetry. Correct future runs by adding a new record.
- **Compatibility promises.** The `wau_host_mmio` register map and the `wau_top` `obs_total_*` bus are the public hardware ABI — additive changes only, or host software breaks silently. The 64-bit schedule word layout in `encode_instruction_word` is a file-format contract for `wau_schedule.hex`. `arch-search` report bytes are frozen (`wau_arch_search_v1` / `wau_resource_model_v1` / `dram_model_v1` / `arch_search_rank_v1`); bump the version constant rather than mutating output in place. Hop metrics are versioned (`dependency_edges_v1`) precisely so historical proxy values are not compared to current ones.
- **Migration/rollback.** There is no persistent database or schema migration. "Rollback" means regenerating from a tracked config; that is why the two contract configs must stay reproducible from recorded knobs.
- **Secrets and credentials.** None are stored or required. The board flow needs local Quartus paths (passed as `-QuartusRoot`) and USB device access only. **Never** commit a Quartus license, a board serial, or a machine-local absolute path into tracked config.
- **Network and external inputs.** The only network access in the toolchain is [`scripts/fetch_dataset.py`](scripts/fetch_dataset.py) (MNIST via the CVDF mirror) — opt-in, skip-if-present, and written to a git-ignored directory. Downloaded data must never be committed.
- **Input trust.** `.cw` programs and JSON configs are treated as trusted developer input. [`basic_compiler.py`](src/python/waugen/basic_compiler.py) parses pseudo-C with Python's `ast` module and evaluates **no** user code; keep it that way. [`cw_lang.py`](src/python/waugen/cw_lang.py) is a tree-walking interpreter with no host escape (no filesystem, network, or `eval` primitives exposed to `.cw` programs) — do not add one.
- **Licensing.** PolyForm Noncommercial 1.0.0. SPDX headers on `src/**` are mandatory and machine-checked.

---

## Current Status and Known Gaps

### Shipped

- Full generator chain (`config → compile → schedule → emit`) with 12 built-in operations and 3 device presets.
- Three lowering front-ends (`compile-expr`, `compile-pseudoc`, `compile-cw`) plus a real host-side `.cw` language (`cw-eval`, `cw-lint`) with classes and conversion magic methods.
- Multi-issue coordinator, explicit highway router fabric with backpressure (single-dimension chain by default, opt-in `matrix` mesh), the highway contracting bus, configurable FIFO/LRU station caches, full observability counters, and a stable MMIO register file.
- Per-core capability constraints applied structurally in the emitted ALU.
- Software reference model + value scoreboard as a hard correctness gate.
- `arch-search` ranking and the `fit-config` simulator-driven fit finder with `profiled` per-core capability inference and `max_in_flight` co-sweep.
- iverilog-driven pipelines viewer with ad-hoc circuit preparation and GIF/MP4 export.
- **Silicon-verified on DE0-Nano (EP4CE22F17C6):** 795/795 basic-flow cases, 300/300 Iris real-data rows, 1032/1032 `example-program.cw` cases (validated profiled 2x4, Quartus Lite 25.1, 21,478/22,320 LEs = 96%, 95.4 ops/s), and 1552/1552 `mesh_stress.cw` triggers at 2x2 (1032 random + 520 real MNIST). See [`benchmarks/`](benchmarks) for the machine-readable records.

### Experimental / Scaffold

- **Layered 3D (`grid.z > 1`):** emits and passes `iverilog`, but has **not** been calibrated on the board flow. Do not report 3D area or timing as measured.
- **Pipelines viewer GUI:** functional but CI-unverified; only the Qt-free helpers are tested.
- **`arch_search` estimate models:** versioned and self-consistent, but not calibrated against a synthesis tool or board-measured scores.

### Known Gaps

- **Active bug — combinational `div` latched early.** See [the pitfall](#combinational-divide-result-is-latched-too-early). `div` is excluded from board benchmarks.
- **Combinational router loop.** Quartus reports a combinational loop through the router links, so the validated profiled 2x4 image runs the WAU/JTAG domain at a /16 (3.125 MHz) clock. Registered/elastic router links are required before raising it.
- **Non-power-of-two `GRID_X` inflates area.** See [the pitfall](#non-power-of-two-grid-blows-the-le-budget).
- **SDRAM inactive.** The board's 32 MB SDRAM has no controller/cache path; station caches remain the only active tier.
- **No closed-loop reprogramming.** New schedules require a rebuild/reflash; the MMIO bus cannot yet remap programs live.
- **CW reference parity is partial.** Calibrated against the `add`/`mul`/`max` paths used by the example kernel only.
- **No typing/lint gate** for Python sources.
- **Transfer-hop metric assumes a mesh.** `_coord_distance` (compiler) and the `dependency_edges_v1` metric (scheduler) both model Manhattan distance, which is the true hop count only on a `matrix` highway; on the default linear chain the real hop count is the index delta, so the reported estimate understates it. Not corrected because `arch-search` output is byte-frozen and ranks on that metric. Treat `estimated_transfer_hops_*` as a mesh-relative estimate, and use the RTL `obs_total_hop_count` for the real figure.
- **Highway topology not board-calibrated.** The linear topology's area/LE saving is argued from port and link counts and verified in `iverilog`; it has **not** been through Quartus. Do not report a resource saving as measured.
- **Contract bus effect unmeasured.** It is correct and bounded (see its testbench), but no board or benchmark run yet demonstrates a throughput gain — by design it stays inert until the highway is actually contended.
- **Viewer gaps:** no per-router queue/backpressure visualization, no `grid.z` layer rendering.

### Planned

Authoritative backlog: [`ROADMAP.md`](ROADMAP.md). Near-term priorities that shape the next architectural decision:

1. Registered/elastic router links, to lift the board clock and remove the combinational loop.
2. Closed-loop on-FPGA benchmarking — push new schedules through MMIO without reflashing.
3. Synthesis-calibrated and board-measured inputs to `arch-search`/`fit-config` estimates.
4. CW software-reference parity across the wider operation set.
5. Hold `exec_latency_cycles_avg` at or below the recorded best while keeping `scoreboard_pass_ratio == 1.0`.

**Benchmark objective (for tuning work).** Primary: minimize `exec_latency_cycles_avg`. Tie-breakers, in order: lower `makespan_cycles`, then lower `total_ms`. Hard gate: `scoreboard_pass_ratio == 1.0`. Keep [`benchmarks/example_pogram_benchmark.txt`](benchmarks/example_pogram_benchmark.txt) at the latest best known run, and [`benchmarks/example_pogram_tuning_latest.txt`](benchmarks/example_pogram_tuning_latest.txt) as the full sweep summary when autotuning. The current best-known knob set and its measured numbers live in those logs and in [`README.md`](README.md#testing) — read them there rather than trusting a copy.

---

## Extension Recipes

### Adding an arithmetic operation

1. Add the template to `OPERATION_LIBRARY` in [`operation_library.py`](src/python/waugen/operation_library.py) with a unique opcode, an honest latency, and a correct `pipelined` flag.
2. Confirm parsing accepts it in [`config.py`](src/python/waugen/config.py) (`_load_operations`).
3. Confirm ALU case generation in [`verilog_emit.py`](src/python/waugen/verilog_emit.py) (`_render_operation_alu`).
4. Extend `_OP_MAP` in [`basic_compiler.py`](src/python/waugen/basic_compiler.py) if it should be reachable from expressions, and `_apply_op` in [`cw_reference.py`](src/python/waugen/cw_reference.py) if it can appear in a CW flow.
5. Regenerate and run `./scripts/run_iverilog_tests.sh`.

### Adding a device preset

1. Add it to `DEVICE_PRESETS` in [`device_library.py`](src/python/waugen/device_library.py) with **real** part metadata, including `logic_cells`, `bram_kbits`, `dsp_blocks` (consumed by `arch_search` feasibility checks).
2. Ensure width/depth defaults are sane.
3. Validate at least one config against it.

### Changing flow compilation or scheduling

1. Update [`compiler.py`](src/python/waugen/compiler.py), [`basic_compiler.py`](src/python/waugen/basic_compiler.py), and/or [`scheduler.py`](src/python/waugen/scheduler.py).
2. Keep `wau_program.json` and `wau_schedule.json/.hex` consistent.
3. Note behavioral deltas in [`README.md`](README.md).
4. Validate [`wau_2d_multiprogram_demo.json`](src/python/configs/wau_2d_multiprogram_demo.json).
5. If core indexing, placement, or routing dimensions change: validate [`wau_3d_demo.json`](src/python/configs/wau_3d_demo.json) and run [`tb_wau_highway_mesh_3d.v`](tests/rtl/tb_wau_highway_mesh_3d.v).
6. Re-run [`test_program_stress.py`](tests/python/test_program_stress.py) and [`scripts/run_randomized_stress.py`](scripts/run_randomized_stress.py).

### Changing CW lowering or kernel semantics

1. Update [`cw_compiler.py`](src/python/waugen/cw_compiler.py) **and** [`cw_reference.py`](src/python/waugen/cw_reference.py) if the coordinator-side reduction changes — they must stay in lock-step.
2. Re-run [`test_cw_compiler.py`](tests/python/test_cw_compiler.py) and [`test_cw_reference.py`](tests/python/test_cw_reference.py).
3. Re-run `./scripts/run_cw_example_benchmark.sh` and confirm `scoreboard_pass_ratio=1.0` plus the latency/makespan budget.

### Changing station caching or highway routing

1. Update `compiler.station_cache` defaults, `_render_core_station`, and `_render_highway_router` together; `WAU_STATION_CACHE_*` in `wau_defs.vh` must agree with the emitted RTL.
2. If routing order changes, update `manhattan_route` in the viewer's [`model.py`](tools/wau-pipelines-viewer/wau_viewer/model.py) in the same edit.
3. Re-run [`tb_wau_host_mmio.v`](tests/rtl/tb_wau_host_mmio.v) and [`tb_wau_highway_mesh.v`](tests/rtl/tb_wau_highway_mesh.v).
4. Re-run the CW benchmark; check the observability counters via MMIO or the `obs_total_*` ports.

---

## Task Start and Handoff Checklist

**At task start:**

1. Read this file and any nested `AGENTS.md` covering the paths you will touch ([`thirds/veribuilder/AGENTS.md`](thirds/veribuilder/AGENTS.md)).
2. Check `git status`; preserve unrelated changes.
3. Locate the owning file's subsection above and its focused tests before editing.
4. Read the relevant contract, [`FOUNDATIONS.md`](FOUNDATIONS.md) principle, or [`docs/cw-language.md`](docs/cw-language.md) section.
5. Verify the handbook's claim against current code before relying on it; fix the handbook if it has drifted.
6. Note which sections here will need updating.

**Before finalizing:**

1. `python3 scripts/sync_license_headers.py --check` succeeds.
2. `validate` and `generate` succeed for the affected configs.
3. `tests/python` unit tests pass — including [`test_cw_reference.py`](tests/python/test_cw_reference.py) and [`test_program_stress.py`](tests/python/test_program_stress.py).
4. `./scripts/run_iverilog_tests.sh` passes — including `tb_wau_host_mmio`, the hop-counter assertion in `tb_wau_highway_mesh`, and vertical routing in `tb_wau_highway_mesh_3d`.
5. `scripts/run_randomized_stress.py --count 25` passes.
6. `./scripts/run_cw_example_benchmark.sh` succeeds, refreshes the reference log, and holds `scoreboard_pass_ratio == 1.0`.
7. `git status benchmarks/` shows only the artifact prefix you intended to touch.
8. No unreproducible manual edits remain in generated RTL.
9. [`AGENTS.md`](AGENTS.md), [`README.md`](README.md), and [`ROADMAP.md`](ROADMAP.md) reflect the repository **as of this change** — new behavior is not still labeled planned, and incomplete work is not labeled shipped.
10. [`.github/workflows/ci.yml`](.github/workflows/ci.yml) still mirrors the local workflow.
11. Report tests run, tests skipped, and remaining gaps accurately.
