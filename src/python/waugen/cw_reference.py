# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""CW software reference model.

Provides a per-flow value scoreboard for benchmarks: given (a, b) host inputs,
re-applies the WAU coordinator's sequential reduction over the compiled flow's
linear stage order and returns the expected ALU output, which can be compared
against host_out_value in RTL execution testbenches.

The model mirrors the coordinator state machine (see verilog_emit `_render_coordinator`):
the accumulator is seeded with `a`, the operand-B register with `b`, then every
stage applies `acc = op(acc, stage_use_immediate ? immediate_b : b_reg)` in
`linear_node_order` once. There is no iteration unroll: recurrent runtime nodes
do not cause the coordinator to revisit stages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .compiler import CompiledFlow, CompiledProject, compile_project
from .config import load_config


class CWReferenceError(ValueError):
    """Raised when a CW reference value cannot be computed."""


_DATA_WIDTH = 32
_MASK = (1 << _DATA_WIDTH) - 1
_SIGN_BIT = 1 << (_DATA_WIDTH - 1)


def _to_signed(value: int) -> int:
    value &= _MASK
    if value & _SIGN_BIT:
        value -= 1 << _DATA_WIDTH
    return value


def _apply_op(op_name: str, a: int, b: int) -> int:
    if op_name == "add":
        return _to_signed(a + b)
    if op_name == "sub":
        return _to_signed(a - b)
    if op_name == "mul":
        return _to_signed(a * b)
    if op_name == "div":
        return _to_signed(a // b) if b != 0 else 0
    if op_name == "mod":
        return _to_signed(a - (a // b) * b) if b != 0 else 0
    if op_name == "min":
        return a if a < b else b
    if op_name == "max":
        return a if a > b else b
    if op_name == "and":
        return _to_signed(a & b)
    if op_name == "or":
        return _to_signed(a | b)
    if op_name == "xor":
        return _to_signed(a ^ b)
    if op_name == "shl":
        shift = b & 0x3F
        return _to_signed(a << shift)
    if op_name == "shr":
        shift = b & 0x3F
        # Arithmetic right shift on the signed 32-bit interpretation.
        return _to_signed(a >> shift)
    raise CWReferenceError(f"Unsupported reference operation: {op_name}")


def evaluate_flow(flow: CompiledFlow, a: int, b: int) -> int:
    acc = _to_signed(a)
    b_reg = _to_signed(b)
    for stage in flow.stages:
        eff_b = stage.immediate_b if stage.immediate_b is not None else b_reg
        acc = _apply_op(stage.op_name, _to_signed(acc), _to_signed(eff_b))
    return acc


def evaluate_project_flow(project: CompiledProject, flow_id: int, a: int, b: int) -> int:
    flow = next((f for f in project.flows if f.flow_id == flow_id), None)
    if flow is None:
        raise CWReferenceError(f"flow id {flow_id} is not present in compiled project")
    return evaluate_flow(flow, a, b)


def compute_expected_values(
    config_path: Path,
    flow_id: int,
    cases: Iterable[tuple[int, int, int]],
) -> list[dict[str, int]]:
    """Compile a config and return the expected host_out_value for each (case_id, a, b)."""
    project = compile_project(load_config(config_path))
    rows: list[dict[str, int]] = []
    for case_id, a, b in cases:
        expected = evaluate_project_flow(project, flow_id, a, b)
        rows.append({"case": int(case_id), "a": int(a), "b": int(b), "expected": int(expected)})
    return rows
