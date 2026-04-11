# AGENTS.md
Repository guidance for AI coding agents working on the WAU generator.

## Mission
Maintain and extend a Python-driven generator for Waterfall Arithmetic Unit RTL.
Always keep the **compiler -> scheduler -> Verilog emission** chain coherent.

## Core Workflow
1. Edit source-of-truth files in `src/python/waugen/` and config samples in `src/python/configs/`.
2. Validate config and pipeline:
   - `PYTHONPATH=src/python python3 -m waugen validate --config <config.json>`
3. If touching expression-to-flow logic, validate the basic compiler path:
   - `PYTHONPATH=src/python python3 -m waugen compile-expr --expr '((a + b) * 3) - b' --flow-id <id> --base-config <in> --out-config <out>`
4. Regenerate artifacts when behavior changes:
   - `PYTHONPATH=src/python python3 -m waugen generate --config <config.json> --out src/verilog/generated --summary`
5. Run RTL tests when RTL, scheduler, or flow semantics change:
   - `./scripts/run_iverilog_tests.sh`

## Ownership Boundaries
- `config.py`: schema and validation only.
- `compiler.py`: mapping flows/stages onto cores and fallback strategy.
- `basic_compiler.py`: expression-to-flow lowering rules for basic WAU compilation.
- `scheduler.py`: timing model and encoded schedule outputs.
- `verilog_emit.py`: text emission only; no scheduling decisions should live here.
- Generated files under `src/verilog/generated/` are build outputs and may be overwritten.

## Invariants
- Operation names and opcodes must be unique.
- Flow IDs must be unique.
- Stage operations must exist in the operation table.
- Core indices must stay within `grid_x * grid_y`.
- Verilog macros in `wau_defs.vh` must match emitted modules.

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

## Review Checklist
Before finalizing changes:
- `validate` succeeds.
- `generate` succeeds.
- `tests/python` unit tests pass.
- `scripts/run_iverilog_tests.sh` passes.
- README/AGENTS updated if workflow or architecture changed.
- No manual edits left in generated RTL that are not reproducible.
