from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .config import load_config


class BasicCompilerError(ValueError):
    """Raised when a high-level arithmetic expression cannot be compiled to WAU stages."""


@dataclass(frozen=True)
class CompiledStageExpr:
    op: str
    immediate_b: int | None


_OP_MAP: dict[type[ast.operator], str] = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
    ast.Mod: "mod",
    ast.BitAnd: "and",
    ast.BitOr: "or",
    ast.BitXor: "xor",
    ast.LShift: "shl",
    ast.RShift: "shr",
}


def _constant_value(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        inner = _constant_value(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner

    return None


def _operand_kind(node: ast.AST) -> tuple[str, int | None]:
    if isinstance(node, ast.Name) and node.id == "b":
        return ("b", None)

    immediate = _constant_value(node)
    if immediate is not None:
        return ("imm", immediate)

    raise BasicCompilerError(
        "Right-side operand must be either variable 'b' or an integer constant"
    )


def _flatten(node: ast.AST) -> list[CompiledStageExpr]:
    if isinstance(node, ast.Name) and node.id == "a":
        return []

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _OP_MAP:
            raise BasicCompilerError(f"Unsupported operator: {type(node.op).__name__}")

        left = _flatten(node.left)
        _, imm = _operand_kind(node.right)

        left.append(
            CompiledStageExpr(
                op=_OP_MAP[type(node.op)],
                immediate_b=imm,
            )
        )
        return left

    raise BasicCompilerError(
        "Expression must be a left-deep chain rooted in variable 'a', "
        "for example: ((a + b) * 3) - b"
    )


def compile_expression_to_stages(expr: str) -> list[CompiledStageExpr]:
    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise BasicCompilerError(f"Invalid expression syntax: {exc}") from exc

    stages = _flatten(parsed.body)
    if not stages:
        raise BasicCompilerError("Expression must produce at least one stage")
    return stages


def build_flow_from_expression(
    *,
    expr: str,
    flow_id: int,
    name: str,
    entry_x: int,
    entry_y: int,
    max_in_flight: int = 1,
) -> dict[str, Any]:
    if flow_id < 0:
        raise BasicCompilerError("flow_id must be non-negative")
    if max_in_flight < 1:
        raise BasicCompilerError("max_in_flight must be >= 1")

    compiled_stages = compile_expression_to_stages(expr)
    raw_stages: list[dict[str, Any]] = []
    for stage in compiled_stages:
        raw = {"op": stage.op}
        if stage.immediate_b is not None:
            raw["immediate_b"] = stage.immediate_b
        raw_stages.append(raw)

    return {
        "id": flow_id,
        "name": name,
        "entry": {"x": entry_x, "y": entry_y},
        "max_in_flight": max_in_flight,
        "stages": raw_stages,
    }


def _extract_operation_name(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        if "name" in raw and isinstance(raw["name"], str):
            return raw["name"]
        if "from_library" in raw and isinstance(raw["from_library"], str):
            return raw["from_library"]
    return None


def _ensure_operations_present(payload: dict[str, Any], needed_ops: set[str]) -> None:
    operations = payload.get("operations")

    if operations is None:
        payload["operations"] = {"library": sorted(needed_ops)}
        return

    if isinstance(operations, list):
        existing = {name for item in operations if (name := _extract_operation_name(item))}
        for op in sorted(needed_ops - existing):
            operations.append(op)
        return

    if isinstance(operations, dict):
        library = operations.setdefault("library", [])
        if not isinstance(library, list):
            raise BasicCompilerError("operations.library must be a list")

        custom = operations.get("custom", [])
        if custom is None:
            custom = []
            operations["custom"] = custom
        if not isinstance(custom, list):
            raise BasicCompilerError("operations.custom must be a list")

        existing: set[str] = set()
        for item in library:
            name = _extract_operation_name(item)
            if name:
                existing.add(name)
        for item in custom:
            name = _extract_operation_name(item)
            if name:
                existing.add(name)

        for op in sorted(needed_ops - existing):
            library.append(op)
        return

    raise BasicCompilerError("operations must be a list or object")


def merge_expression_into_config(
    *,
    base_config_path: Path,
    out_config_path: Path,
    expr: str,
    flow_id: int,
    name: str,
    entry_x: int,
    entry_y: int,
    replace_existing: bool,
    max_in_flight: int = 1,
) -> dict[str, Any]:
    try:
        payload = json.loads(base_config_path.read_text())
    except FileNotFoundError as exc:
        raise BasicCompilerError(f"Config file not found: {base_config_path}") from exc
    except json.JSONDecodeError as exc:
        raise BasicCompilerError(f"Invalid JSON in {base_config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise BasicCompilerError("Base config root must be a JSON object")

    flow = build_flow_from_expression(
        expr=expr,
        flow_id=flow_id,
        name=name,
        entry_x=entry_x,
        entry_y=entry_y,
        max_in_flight=max_in_flight,
    )

    flows = payload.setdefault("flows", [])
    if not isinstance(flows, list):
        raise BasicCompilerError("flows must be a list")

    existing_index: int | None = None
    for idx, raw_flow in enumerate(flows):
        if isinstance(raw_flow, dict) and int(raw_flow.get("id", -1)) == flow_id:
            existing_index = idx
            break

    if existing_index is not None:
        if not replace_existing:
            raise BasicCompilerError(
                f"Flow id {flow_id} already exists. Use --replace-existing to overwrite"
            )
        flows[existing_index] = flow
    else:
        flows.append(flow)

    needed_ops = {stage["op"] for stage in flow["stages"]}
    _ensure_operations_present(payload, needed_ops)

    out_config_path.parent.mkdir(parents=True, exist_ok=True)
    out_config_path.write_text(json.dumps(payload, indent=2) + "\n")

    # Validate with the main pipeline parser to guarantee compatibility.
    load_config(out_config_path)

    return flow
