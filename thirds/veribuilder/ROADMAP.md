# ROADMAP.md
Roadmap for VeriBuilder as an independent Python library for dynamic Verilog project construction.

## Status Snapshot (2026-07-01)
Implemented baseline:
- `VerilogProject` manifest with deterministic file registration and emission.
- `GeneratedFile` model with optional feature gates.
- `VerilogHeader` helper with idempotent SPDX-style header insertion.
- `TemplateRenderer` for lightweight `{{ parameter }}` substitution.
- Installable package skeleton through `pyproject.toml`.
- README with core usage and feature-gate examples.
- Vendored integration with the WAU generator through `waugen.verilog_emit`.

## Near-Term
- Add a standalone `tests/` tree inside the VeriBuilder project before extracting it from the WAU repository.
- Add typed examples for common RTL generator patterns:
  - include/header files,
  - generated module families,
  - board-wrapper feature variants,
  - JSON/HEX sidecar artifacts.
- Add a small changelog or release-notes file once API changes begin accumulating.
- Decide whether package metadata should use the WAU license or a different license before external publication.

## API Direction
- Keep the API centered on plain Python data structures and strings.
- Prefer composable primitives over framework-level generator classes.
- Keep the template renderer intentionally simple; add escape behavior or stricter validation only when concrete users need it.
- Preserve deterministic output order and idempotent header handling as compatibility guarantees.

## Possible Future Features
- Optional dry-run manifest output without writing files.
- Optional overwrite policy controls (`replace`, `skip`, `error`).
- File content hashing for incremental generation reports.
- Project-level metadata export for downstream build systems.
- Helpers for common Verilog literals and identifiers, as long as they remain generic and do not pull in generator-specific schemas.

## Release Gate
Before publishing VeriBuilder as its own repository/package:
- Standalone unit tests pass without importing WAU.
- README examples run from a fresh checkout.
- Package installs with `pip install -e .`.
- Built distributions are verified with `python3 -m build`.
- License, author, repository URL, and version metadata are finalized in `pyproject.toml`.
