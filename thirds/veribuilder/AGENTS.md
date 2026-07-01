# AGENTS.md
Repository guidance for AI coding agents working on VeriBuilder as a standalone project.

## Mission
Maintain a small, dependency-light Python library for constructing parameterized Verilog projects.
Keep VeriBuilder independent from any one RTL generator, including WAU, so it can be published and reused by other projects.

## Core Workflow
1. Edit package code under `src/veribuilder/`.
2. Keep public APIs documented in `README.md` when behavior or usage changes.
3. Add focused tests for public behavior in the consuming repository or a future `tests/` directory after extraction.
4. Validate the package can be imported directly from source:
   - `PYTHONPATH=src python3 -c "import veribuilder; print(veribuilder.__all__)"`
5. Run unit tests before review:
   - In this vendored WAU checkout: `PYTHONPATH=src/python python3 -m unittest tests.python.test_veribuilder`
   - After extraction to a standalone repo: `python3 -m unittest`
6. Before publishing, verify package metadata:
   - `python3 -m pip install -e .`
   - `python3 -m build`

## Ownership Boundaries
- `src/veribuilder/core.py`: project manifest, generated file model, feature gates, headers, lightweight template rendering, and filesystem emission.
- `src/veribuilder/__init__.py`: stable public exports only.
- `README.md`: user-facing quickstart and API examples.
- `pyproject.toml`: package metadata and build configuration.
- `AGENTS.md`: development guidance for agents and maintainers.
- `ROADMAP.md`: package-local feature and release direction.

## Invariants
- The package must not import WAU modules or depend on WAU config/compiler/scheduler types.
- Public helpers should operate on plain Python values, strings, and `pathlib.Path`.
- File emission must be deterministic in the order files are registered.
- Feature gates must be explicit and side-effect free.
- Verilog header insertion must be idempotent.
- Generated non-Verilog files must not receive Verilog comments or headers.
- Template rendering must stay intentionally small; avoid growing it into a general programming language.
- The library should remain usable without mandatory third-party runtime dependencies.

## Extension Rules
When adding a new project-emission feature:
1. Add it to `core.py` only if it is generic across RTL generators.
2. Document the public usage in `README.md`.
3. Add or update tests that exercise both enabled and disabled behavior.

When adding templating capability:
1. Prefer simple deterministic substitutions over control-flow syntax.
2. Keep missing-value behavior explicit.
3. Avoid embedding Verilog-specific semantics in the renderer.

When preparing a standalone release:
1. Confirm `pyproject.toml` metadata is no longer WAU-specific.
2. Add a standalone `tests/` directory if still relying on parent-repo tests.
3. Add release notes or changelog entries for public API changes.
4. Confirm license files and SPDX references make sense outside the WAU repository.

## Review Checklist
- Public API remains small and documented.
- Existing WAU integration tests still pass when the package is vendored.
- Standalone import works with `PYTHONPATH=src`.
- No WAU-specific dependency or generated artifact leaked into the package.
- No `__pycache__`, build outputs, or local virtual environment files are committed.
