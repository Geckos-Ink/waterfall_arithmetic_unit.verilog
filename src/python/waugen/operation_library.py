# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationTemplate:
    name: str
    opcode: int
    latency: int
    pipelined: bool
    verilog_expr: str


OPERATION_LIBRARY: dict[str, OperationTemplate] = {
    "add": OperationTemplate(
        name="add", opcode=0x01, latency=1, pipelined=True, verilog_expr="a + b"
    ),
    "sub": OperationTemplate(
        name="sub", opcode=0x02, latency=1, pipelined=True, verilog_expr="a - b"
    ),
    "mul": OperationTemplate(
        name="mul", opcode=0x03, latency=3, pipelined=True, verilog_expr="a * b"
    ),
    "div": OperationTemplate(
        name="div",
        opcode=0x04,
        latency=8,
        pipelined=False,
        verilog_expr="(b != 0) ? (a / b) : {DATA_WIDTH{1'b0}}",
    ),
    "mod": OperationTemplate(
        name="mod",
        opcode=0x05,
        latency=8,
        pipelined=False,
        verilog_expr="(b != 0) ? (a % b) : {DATA_WIDTH{1'b0}}",
    ),
    "min": OperationTemplate(
        name="min", opcode=0x06, latency=1, pipelined=True, verilog_expr="(a < b) ? a : b"
    ),
    "max": OperationTemplate(
        name="max", opcode=0x07, latency=1, pipelined=True, verilog_expr="(a > b) ? a : b"
    ),
    "and": OperationTemplate(
        name="and", opcode=0x08, latency=1, pipelined=True, verilog_expr="a & b"
    ),
    "or": OperationTemplate(
        name="or", opcode=0x09, latency=1, pipelined=True, verilog_expr="a | b"
    ),
    "xor": OperationTemplate(
        name="xor", opcode=0x0A, latency=1, pipelined=True, verilog_expr="a ^ b"
    ),
    "shl": OperationTemplate(
        name="shl", opcode=0x0B, latency=1, pipelined=True, verilog_expr="a <<< b[5:0]"
    ),
    "shr": OperationTemplate(
        name="shr", opcode=0x0C, latency=1, pipelined=True, verilog_expr="a >>> b[5:0]"
    ),
}


def get_operation_template(name: str) -> OperationTemplate:
    try:
        return OPERATION_LIBRARY[name]
    except KeyError as exc:
        available = ", ".join(sorted(OPERATION_LIBRARY))
        raise KeyError(f"Unknown operation '{name}'. Available: {available}") from exc
