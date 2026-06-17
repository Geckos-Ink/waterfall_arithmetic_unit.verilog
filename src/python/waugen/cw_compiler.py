# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .config import load_config
from .device_library import get_device_preset


class CWCompilerError(ValueError):
    """Raised when a WAU .cw program cannot be lowered to WAU flow IR."""


@dataclass(frozen=True)
class CWKernelSpec:
    kernel_name: str
    kernel_size: int
    tile_h: int
    tile_w: int
    cin_block: int
    cout_block: int
    worker_count: int
    has_prefetch: bool
    has_residual: bool
    has_relu: bool
    has_double_buffering: bool
    preferred_lane_parallelism: int | None
    preferred_max_in_flight: int | None
    preferred_dtype: str | None
    preferred_placement_policy: str | None
    preferred_lowering_profile: str | None
    preferred_program_priority: int | None
    preferred_program_load_balance: str | None


@dataclass(frozen=True)
class CWWorkloadShape:
    h: int | None
    w: int | None
    cin: int | None
    cout: int | None


_DEFAULT_CW_MAX_IN_FLIGHT = 4
_DEFAULT_CW_PROGRAM_PRIORITY = 2
_DEFAULT_CW_PROGRAM_LOAD_BALANCE = "least_busy"
_DTYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_WAU_PRAGMA_LINE_RE = re.compile(r"^\s*//\s*@wau\b(?P<body>.*)$")
_WAU_PRAGMA_ASSIGN_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^\s]+)\s*$"
)
_SUPPORTED_WAU_PRAGMA_KEYS = (
    "lane_parallelism",
    "max_in_flight",
    "preferred_dtype",
    "placement_policy",
    "lowering_profile",
    "program_priority",
    "program_load_balance",
)
_SUPPORTED_WAU_PLACEMENT_POLICIES = ("locality", "balance")
_SUPPORTED_WAU_LOWERING_PROFILES = (
    "reference",
    "latency_optimized",
    "throughput_optimized",
)
_SUPPORTED_WAU_PROGRAM_LOAD_BALANCE = ("least_busy", "round_robin")


def validate_cw_pragmas(program: str) -> dict[str, int | str]:
    """Validate ``// @wau`` pragmas and return normalized values.

    This intentionally validates only pragma syntax/values. It does not require
    the program to match the current ``compile-cw`` template shape.
    """

    pragmas = _parse_wau_pragmas(program)
    values: dict[str, int | str] = {}

    lane_parallelism = _extract_wau_pragma_int(
        pragmas,
        "lane_parallelism",
        minimum=1,
    )
    if lane_parallelism is not None:
        values["lane_parallelism"] = lane_parallelism

    max_in_flight = _extract_wau_pragma_int(
        pragmas,
        "max_in_flight",
        minimum=1,
    )
    if max_in_flight is not None:
        values["max_in_flight"] = max_in_flight

    preferred_dtype = _extract_wau_pragma_dtype(pragmas)
    if preferred_dtype is not None:
        values["preferred_dtype"] = preferred_dtype

    placement_policy = _extract_wau_pragma_choice(
        pragmas,
        "placement_policy",
        supported=_SUPPORTED_WAU_PLACEMENT_POLICIES,
    )
    if placement_policy is not None:
        values["placement_policy"] = placement_policy

    lowering_profile = _extract_wau_pragma_choice(
        pragmas,
        "lowering_profile",
        supported=_SUPPORTED_WAU_LOWERING_PROFILES,
    )
    if lowering_profile is not None:
        values["lowering_profile"] = lowering_profile

    program_priority = _extract_wau_pragma_int(
        pragmas,
        "program_priority",
        minimum=1,
    )
    if program_priority is not None:
        values["program_priority"] = program_priority

    program_load_balance = _extract_wau_pragma_choice(
        pragmas,
        "program_load_balance",
        supported=_SUPPORTED_WAU_PROGRAM_LOAD_BALANCE,
    )
    if program_load_balance is not None:
        values["program_load_balance"] = program_load_balance

    return values


def _strip_comments(program: str) -> str:
    lines: list[str] = []
    for raw_line in program.splitlines():
        line = raw_line.split("//", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def _extract_int_assignment(source: str, name: str) -> int | None:
    pattern = re.compile(rf"\bint(?:[0-9]+)?\s+{re.escape(name)}\s*=\s*(-?\d+)\s*;")
    match = pattern.search(source)
    if not match:
        return None
    return int(match.group(1))


def _extract_required_int(source: str, name: str) -> int:
    value = _extract_int_assignment(source, name)
    if value is None:
        raise CWCompilerError(f"Missing required integer constant '{name}' in .cw program")
    return value


def _extract_worker_count(source: str) -> int:
    match = re.search(r"\bchannel_worker\s+workers\s*\[\s*(\d+)\s*\]\s*;", source)
    if not match:
        raise CWCompilerError("Missing worker declaration: 'channel_worker workers[N];'")
    return int(match.group(1))


def _extract_kernel_name(source: str) -> str:
    match = re.search(r"\bspace\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", source)
    if not match:
        raise CWCompilerError("No 'space <name> { ... }' block found in .cw program")
    return match.group(1)


def _parse_wau_pragmas(program: str) -> dict[str, tuple[str, int]]:
    parsed: dict[str, tuple[str, int]] = {}
    for line_no, line in enumerate(program.splitlines(), start=1):
        match = _WAU_PRAGMA_LINE_RE.match(line)
        if not match:
            continue

        body = match.group("body").strip()
        assign = _WAU_PRAGMA_ASSIGN_RE.match(body)
        if assign is None:
            raise CWCompilerError(
                "Invalid @wau pragma syntax at line "
                f"{line_no}: expected '// @wau <key>=<value>'"
            )

        key = assign.group("key")
        value = assign.group("value")
        if key not in _SUPPORTED_WAU_PRAGMA_KEYS:
            supported = ", ".join(_SUPPORTED_WAU_PRAGMA_KEYS)
            raise CWCompilerError(
                f"Unsupported @wau pragma '{key}' at line {line_no}. "
                f"Supported keys: {supported}"
            )
        if key in parsed:
            prev_line = parsed[key][1]
            raise CWCompilerError(
                f"Duplicate @wau pragma '{key}' at line {line_no}; first declared at line {prev_line}"
            )
        parsed[key] = (value, line_no)
    return parsed


def _extract_wau_pragma_int(
    pragmas: dict[str, tuple[str, int]],
    key: str,
    *,
    minimum: int,
) -> int | None:
    entry = pragmas.get(key)
    if entry is None:
        return None

    raw, line_no = entry
    try:
        value = int(raw)
    except ValueError as exc:
        raise CWCompilerError(
            f"Invalid @wau {key} value '{raw}' at line {line_no}: expected integer >= {minimum}"
        ) from exc
    if value < minimum:
        raise CWCompilerError(f"@wau {key} must be >= {minimum} (line {line_no})")
    return value


def _extract_wau_pragma_dtype(pragmas: dict[str, tuple[str, int]]) -> str | None:
    entry = pragmas.get("preferred_dtype")
    if entry is None:
        return None
    raw, line_no = entry
    if not _DTYPE_RE.fullmatch(raw):
        raise CWCompilerError(
            "Invalid @wau preferred_dtype "
            f"'{raw}' at line {line_no}: expected lowercase token matching {_DTYPE_RE.pattern!r}"
        )
    return raw


def _extract_wau_pragma_choice(
    pragmas: dict[str, tuple[str, int]],
    key: str,
    *,
    supported: tuple[str, ...],
) -> str | None:
    entry = pragmas.get(key)
    if entry is None:
        return None

    raw, line_no = entry
    if raw not in supported:
        supported_text = ", ".join(supported)
        raise CWCompilerError(
            f"Invalid @wau {key} value '{raw}' at line {line_no}: "
            f"expected one of {supported_text}"
        )
    return raw


def _extract_workload_shape(source: str) -> CWWorkloadShape:
    return CWWorkloadShape(
        h=_extract_int_assignment(source, "H"),
        w=_extract_int_assignment(source, "W"),
        cin=_extract_int_assignment(source, "Cin"),
        cout=_extract_int_assignment(source, "Cout"),
    )


def parse_cw_program(program: str) -> tuple[CWKernelSpec, CWWorkloadShape]:
    cleaned = _strip_comments(program)
    pragmas = _parse_wau_pragmas(program)
    pragma_lane_parallelism = _extract_wau_pragma_int(
        pragmas,
        "lane_parallelism",
        minimum=1,
    )
    pragma_max_in_flight = _extract_wau_pragma_int(
        pragmas,
        "max_in_flight",
        minimum=1,
    )
    pragma_preferred_dtype = _extract_wau_pragma_dtype(pragmas)
    pragma_placement_policy = _extract_wau_pragma_choice(
        pragmas,
        "placement_policy",
        supported=_SUPPORTED_WAU_PLACEMENT_POLICIES,
    )
    pragma_lowering_profile = _extract_wau_pragma_choice(
        pragmas,
        "lowering_profile",
        supported=_SUPPORTED_WAU_LOWERING_PROFILES,
    )
    pragma_program_priority = _extract_wau_pragma_int(
        pragmas,
        "program_priority",
        minimum=1,
    )
    pragma_program_load_balance = _extract_wau_pragma_choice(
        pragmas,
        "program_load_balance",
        supported=_SUPPORTED_WAU_PROGRAM_LOAD_BALANCE,
    )

    required_symbols = (
        "load_input_block(",
        "prefetch_input_block(",
        "load_weight_block(",
        "accumulate_current_block(",
        "finalize_bias_residual_relu(",
        "store_output_tile(",
    )
    for symbol in required_symbols:
        if symbol not in cleaned:
            raise CWCompilerError(
                f"Unsupported or incomplete .cw program. Missing symbol '{symbol}'"
            )

    kernel_size = _extract_required_int(cleaned, "K")
    tile_h = _extract_required_int(cleaned, "TILE_H")
    tile_w = _extract_required_int(cleaned, "TILE_W")
    cin_block = _extract_required_int(cleaned, "CIN_BLOCK")
    cout_block = _extract_required_int(cleaned, "COUT_BLOCK")
    worker_count = _extract_worker_count(cleaned)
    kernel_name = _extract_kernel_name(cleaned)

    if kernel_size < 1:
        raise CWCompilerError("K must be >= 1")
    if tile_h < 1 or tile_w < 1:
        raise CWCompilerError("TILE_H and TILE_W must be >= 1")
    if cin_block < 1 or cout_block < 1:
        raise CWCompilerError("CIN_BLOCK and COUT_BLOCK must be >= 1")
    if worker_count < 1:
        raise CWCompilerError("workers[N] must have N >= 1")
    spec = CWKernelSpec(
        kernel_name=kernel_name,
        kernel_size=kernel_size,
        tile_h=tile_h,
        tile_w=tile_w,
        cin_block=cin_block,
        cout_block=cout_block,
        worker_count=worker_count,
        has_prefetch="prefetch_input_block(" in cleaned,
        has_residual="residual" in cleaned,
        has_relu=("v < 0.0f" in cleaned) or ("max(" in cleaned),
        has_double_buffering=("input_tile_ping" in cleaned and "input_tile_pong" in cleaned),
        preferred_lane_parallelism=pragma_lane_parallelism,
        preferred_max_in_flight=pragma_max_in_flight,
        preferred_dtype=pragma_preferred_dtype,
        preferred_placement_policy=pragma_placement_policy,
        preferred_lowering_profile=pragma_lowering_profile,
        preferred_program_priority=pragma_program_priority,
        preferred_program_load_balance=pragma_program_load_balance,
    )
    shape = _extract_workload_shape(cleaned)
    return spec, shape


def _coord(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _core_from_index(index: int, *, grid_x: int, grid_y: int) -> dict[str, int]:
    core_count = max(1, grid_x * grid_y)
    norm = index % core_count
    return {"x": norm % grid_x, "y": norm // grid_x}


@dataclass(frozen=True)
class _CoreCapabilityFilter:
    op_caps: dict[tuple[int, int], frozenset[str]]
    dtype_caps: dict[tuple[int, int], frozenset[str]]

    def supports(self, x: int, y: int, *, op: str, dtype: str | None) -> bool:
        key = (x, y)
        allowed_ops = self.op_caps.get(key)
        if allowed_ops is not None and op not in allowed_ops:
            return False
        allowed_dtypes = self.dtype_caps.get(key)
        if dtype is not None and allowed_dtypes is not None and dtype not in allowed_dtypes:
            return False
        return True

    @property
    def is_empty(self) -> bool:
        return not self.op_caps and not self.dtype_caps


_EMPTY_CAPABILITY_FILTER = _CoreCapabilityFilter(op_caps={}, dtype_caps={})


def _capability_filter_from_payload(payload: dict[str, Any]) -> _CoreCapabilityFilter:
    compiler_section = payload.get("compiler") or {}
    if not isinstance(compiler_section, dict):
        return _EMPTY_CAPABILITY_FILTER
    raw_caps = compiler_section.get("core_capabilities")
    if not isinstance(raw_caps, list) or not raw_caps:
        return _EMPTY_CAPABILITY_FILTER

    op_caps: dict[tuple[int, int], frozenset[str]] = {}
    dtype_caps: dict[tuple[int, int], frozenset[str]] = {}
    for entry in raw_caps:
        if not isinstance(entry, dict):
            continue
        core = entry.get("core")
        if not isinstance(core, dict):
            continue
        try:
            cx = int(core["x"])
            cy = int(core["y"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (cx, cy)
        ops = entry.get("operations")
        if isinstance(ops, list) and ops:
            op_caps[key] = frozenset(str(op) for op in ops)
        dtypes = entry.get("data_types")
        if isinstance(dtypes, list) and dtypes:
            dtype_caps[key] = frozenset(str(dt) for dt in dtypes)

    return _CoreCapabilityFilter(op_caps=op_caps, dtype_caps=dtype_caps)


def _candidate_cores(
    *,
    base_index: int,
    grid_x: int,
    grid_y: int,
    count: int,
    capabilities: _CoreCapabilityFilter = _EMPTY_CAPABILITY_FILTER,
    op: str | None = None,
    dtype: str | None = None,
) -> list[dict[str, int]]:
    capability_filter_active = not capabilities.is_empty and op is not None
    target_count = max(1, count)
    core_count = max(1, grid_x * grid_y)

    # Natural sequence: identical to the pre-capability behaviour, gives the autotuner
    # a stable baseline when restrictions do not bite.
    natural: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for i in range(target_count):
        cand = _core_from_index(base_index + i, grid_x=grid_x, grid_y=grid_y)
        key = (cand["x"], cand["y"])
        if key in seen:
            continue
        seen.add(key)
        natural.append(cand)

    if not capability_filter_active:
        return natural

    compatible: list[dict[str, int]] = []
    compatible_keys: set[tuple[int, int]] = set()
    incompatible_seen = False
    for cand in natural:
        if capabilities.supports(cand["x"], cand["y"], op=op, dtype=dtype):
            key = (cand["x"], cand["y"])
            compatible.append(cand)
            compatible_keys.add(key)
        else:
            incompatible_seen = True

    if not incompatible_seen:
        return natural

    if not compatible:
        # Every natural candidate is incompatible. Scan the rest of the grid for a
        # capable core so downstream stages have a stable, deterministic primary.
        for i in range(target_count, core_count):
            cand = _core_from_index(base_index + i, grid_x=grid_x, grid_y=grid_y)
            key = (cand["x"], cand["y"])
            if key in compatible_keys:
                continue
            if not capabilities.supports(cand["x"], cand["y"], op=op, dtype=dtype):
                continue
            compatible.append(cand)
            compatible_keys.add(key)
            break

    if not compatible:
        # Capability filter eliminated every candidate: emit a single safe fallback
        # so downstream validation will surface the precise "no compatible core" error.
        fallback = _core_from_index(base_index, grid_x=grid_x, grid_y=grid_y)
        compatible.append(fallback)

    return compatible


def _tile_iteration_hint(spec: CWKernelSpec, shape: CWWorkloadShape) -> int:
    if shape.h is None or shape.w is None or shape.cout is None:
        return 8
    if shape.h <= 0 or shape.w <= 0 or shape.cout <= 0:
        return 8

    tiles_y = (shape.h + spec.tile_h - 1) // spec.tile_h
    tiles_x = (shape.w + spec.tile_w - 1) // spec.tile_w
    oc_blocks = (shape.cout + spec.cout_block - 1) // spec.cout_block
    raw_iters = tiles_y * tiles_x * oc_blocks

    # Keep schedules practical while preserving recurrence semantics in the generated reference flow.
    return max(2, min(raw_iters, 32))


def build_flow_from_cw_program(
    *,
    program: str,
    flow_id: int,
    name: str,
    entry_x: int,
    entry_y: int,
    max_in_flight: int,
    dtype: str,
    grid_x: int,
    grid_y: int,
    lane_parallelism: int | None = None,
    placement_policy: str | None = None,
    lowering_profile: str | None = None,
    parsed_spec_shape: tuple[CWKernelSpec, CWWorkloadShape] | None = None,
    max_in_flight_source: str = "explicit",
    dtype_source: str = "explicit",
    capabilities: _CoreCapabilityFilter = _EMPTY_CAPABILITY_FILTER,
) -> dict[str, Any]:
    if parsed_spec_shape is None:
        spec, shape = parse_cw_program(program)
    else:
        spec, shape = parsed_spec_shape

    if flow_id < 0:
        raise CWCompilerError("flow_id must be non-negative")
    if max_in_flight < 1:
        raise CWCompilerError("max_in_flight must be >= 1")
    if grid_x < 1 or grid_y < 1:
        raise CWCompilerError("grid dimensions must be >= 1")

    available_cores = max(1, grid_x * grid_y)
    lanes = (
        lane_parallelism
        if lane_parallelism is not None
        else (
            spec.preferred_lane_parallelism
            if spec.preferred_lane_parallelism is not None
            else spec.worker_count
        )
    )
    lanes = max(1, lanes)
    lanes = min(lanes, spec.worker_count, spec.cout_block, available_cores)
    lane_source = (
        "cli"
        if lane_parallelism is not None
        else ("pragma" if spec.preferred_lane_parallelism is not None else "worker_count")
    )

    resolved_placement_policy = (
        placement_policy
        if placement_policy is not None
        else (spec.preferred_placement_policy or "balance")
    )
    if resolved_placement_policy not in _SUPPORTED_WAU_PLACEMENT_POLICIES:
        raise CWCompilerError(
            f"placement_policy must be one of {', '.join(_SUPPORTED_WAU_PLACEMENT_POLICIES)}"
        )

    resolved_lowering_profile = (
        lowering_profile
        if lowering_profile is not None
        else (spec.preferred_lowering_profile or "reference")
    )
    if resolved_lowering_profile not in _SUPPORTED_WAU_LOWERING_PROFILES:
        raise CWCompilerError(
            f"lowering_profile must be one of {', '.join(_SUPPORTED_WAU_LOWERING_PROFILES)}"
        )

    directive_local = "prefer_locality"
    directive_balance = "prefer_balance"
    if resolved_placement_policy == "locality":
        default_compute_directive = directive_local
        default_transfer_directive = (
            directive_local
            if resolved_lowering_profile != "throughput_optimized"
            else directive_balance
        )
        lane_candidate_count = 2 if resolved_lowering_profile == "latency_optimized" else 3
        transfer_candidate_count = 2
        lane_stride = 1 if resolved_lowering_profile != "throughput_optimized" else 2
    else:
        default_compute_directive = directive_balance
        default_transfer_directive = directive_balance
        lane_candidate_count = (
            min(5, available_cores)
            if resolved_lowering_profile == "throughput_optimized"
            else min(4, available_cores)
        )
        transfer_candidate_count = 3 if resolved_lowering_profile == "throughput_optimized" else 2
        lane_stride = 2 if resolved_lowering_profile == "throughput_optimized" else 1

    entry_index = entry_y * grid_x + entry_x
    counter_anchor = entry_index + 4 + max(1, lanes * max(1, lane_stride // 1))

    nodes: list[dict[str, Any]] = []

    def transfer_candidates(*, base_index: int, count_hint: int, op: str) -> list[dict[str, int]]:
        return _candidate_cores(
            base_index=base_index,
            grid_x=grid_x,
            grid_y=grid_y,
            count=min(max(count_hint, transfer_candidate_count), available_cores),
            capabilities=capabilities,
            op=op,
            dtype=dtype,
        )

    nodes.append(
        {
            "id": "load_ifmap",
            "op": "add",
            "dtype": dtype,
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=entry_index, count_hint=2, op="add"
                ),
                "directive": default_transfer_directive,
            },
        }
    )
    nodes.append(
        {
            "id": "load_weights",
            "op": "add",
            "dtype": dtype,
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=entry_index + lane_stride, count_hint=2, op="add"
                ),
                "directive": default_transfer_directive,
            },
        }
    )
    nodes.append(
        {
            "id": "prefetch_next_ifmap",
            "op": "add",
            "dtype": dtype,
            "deps": ["load_ifmap"],
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=entry_index + (2 * lane_stride), count_hint=2, op="add"
                ),
                "directive": default_transfer_directive,
            },
        }
    )
    nodes.append(
        {
            "id": "load_bias",
            "op": "add",
            "dtype": dtype,
            "deps": ["load_weights"],
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=entry_index + (3 * lane_stride), count_hint=2, op="add"
                ),
                "directive": default_transfer_directive,
            },
        }
    )

    def lane_candidates_for(*, lane_seed: int, op: str) -> list[dict[str, int]]:
        return _candidate_cores(
            base_index=lane_seed,
            grid_x=grid_x,
            grid_y=grid_y,
            count=min(max(2, lane_candidate_count), available_cores),
            capabilities=capabilities,
            op=op,
            dtype=dtype,
        )

    relu_nodes: list[str] = []
    for lane in range(lanes):
        lane_seed = (entry_index + 4 + (lane * lane_stride)) % available_cores

        mul_id = f"lane{lane}_mul"
        acc_id = f"lane{lane}_acc"
        bias_id = f"lane{lane}_bias"
        residual_id = f"lane{lane}_residual"
        relu_id = f"lane{lane}_relu"

        nodes.append(
            {
                "id": mul_id,
                "op": "mul",
                "dtype": dtype,
                "deps": ["load_ifmap", "load_weights"],
                "placement": {
                    "candidate_cores": lane_candidates_for(lane_seed=lane_seed, op="mul"),
                    "directive": default_compute_directive,
                },
            }
        )
        nodes.append(
            {
                "id": acc_id,
                "op": "add",
                "dtype": dtype,
                "deps": [mul_id],
                "placement": {
                    "candidate_cores": lane_candidates_for(lane_seed=lane_seed, op="add"),
                    "directive": default_compute_directive,
                },
            }
        )
        nodes.append(
            {
                "id": bias_id,
                "op": "add",
                "dtype": dtype,
                "deps": [acc_id, "load_bias"],
                "placement": {
                    "candidate_cores": lane_candidates_for(lane_seed=lane_seed, op="add"),
                    "directive": default_compute_directive,
                },
            }
        )
        nodes.append(
            {
                "id": residual_id,
                "op": "add",
                "dtype": dtype,
                "deps": [bias_id],
                "placement": {
                    "candidate_cores": lane_candidates_for(lane_seed=lane_seed, op="add"),
                    "directive": default_compute_directive,
                },
            }
        )
        nodes.append(
            {
                "id": relu_id,
                "op": "max",
                "dtype": dtype,
                "immediate_b": 0,
                "deps": [residual_id],
                "placement": {
                    "candidate_cores": lane_candidates_for(lane_seed=lane_seed, op="max"),
                    "directive": default_compute_directive,
                },
            }
        )
        relu_nodes.append(relu_id)

    nodes.append(
        {
            "id": "store_tile",
            "op": "add",
            "dtype": dtype,
            "deps": relu_nodes + ["prefetch_next_ifmap"],
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=counter_anchor, count_hint=2, op="add"
                ),
                "directive": default_transfer_directive,
            },
        }
    )

    # tile_counter is intentionally fixed/manual; report a non-blocking diagnostic
    # if its anchor core cannot host the add+dtype combination so capability conflicts
    # surface up the stack rather than later inside the compiler.
    counter_core = _core_from_index(counter_anchor, grid_x=grid_x, grid_y=grid_y)
    if not capabilities.supports(
        counter_core["x"], counter_core["y"], op="add", dtype=dtype
    ):
        replacement = next(
            (
                _core_from_index(counter_anchor + i, grid_x=grid_x, grid_y=grid_y)
                for i in range(1, available_cores)
                if capabilities.supports(
                    _core_from_index(counter_anchor + i, grid_x=grid_x, grid_y=grid_y)["x"],
                    _core_from_index(counter_anchor + i, grid_x=grid_x, grid_y=grid_y)["y"],
                    op="add",
                    dtype=dtype,
                )
            ),
            None,
        )
        if replacement is not None:
            counter_core = replacement

    nodes.append(
        {
            "id": "tile_counter",
            "op": "add",
            "dtype": dtype,
            "immediate_b": 1,
            "deps": ["store_tile"],
            "recurrent": True,
            "max_iterations": _tile_iteration_hint(spec, shape),
            "placement": {
                "candidate_cores": [counter_core],
                "directive": "manual",
                "fixed": True,
            },
        }
    )
    nodes.append(
        {
            "id": "tile_done_gate",
            "op": "max",
            "dtype": dtype,
            "immediate_b": 0,
            "deps": ["tile_counter"],
            "placement": {
                "candidate_cores": transfer_candidates(
                    base_index=counter_anchor + 1, count_hint=1, op="max"
                ),
                "directive": default_transfer_directive,
            },
        }
    )

    return {
        "id": flow_id,
        "name": name,
        "entry": _coord(entry_x, entry_y),
        "max_in_flight": max_in_flight,
        "nodes": nodes,
        "cw_hints": {
            "kernel_name": spec.kernel_name,
            "kernel_size": spec.kernel_size,
            "tile_h": spec.tile_h,
            "tile_w": spec.tile_w,
            "cin_block": spec.cin_block,
            "cout_block": spec.cout_block,
            "worker_count_declared": spec.worker_count,
            "lane_parallelism_preferred": spec.preferred_lane_parallelism,
            "lane_parallelism_compiled": lanes,
            "lane_parallelism_source": lane_source,
            "max_in_flight_preferred": spec.preferred_max_in_flight,
            "max_in_flight_compiled": max_in_flight,
            "max_in_flight_source": max_in_flight_source,
            "double_buffering": spec.has_double_buffering,
            "prefetch": spec.has_prefetch,
            "residual": spec.has_residual,
            "relu": spec.has_relu,
            "preferred_dtype": spec.preferred_dtype,
            "dtype": dtype,
            "dtype_compiled": dtype,
            "dtype_source": dtype_source,
            "placement_policy_preferred": spec.preferred_placement_policy,
            "placement_policy_compiled": resolved_placement_policy,
            "lowering_profile_preferred": spec.preferred_lowering_profile,
            "lowering_profile_compiled": resolved_lowering_profile,
            "capability_filter_active": not capabilities.is_empty,
            "capability_restricted_cores": sorted(
                {f"{x},{y}" for (x, y) in (set(capabilities.op_caps) | set(capabilities.dtype_caps))}
            ),
        },
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
            raise CWCompilerError("operations.library must be a list")

        custom = operations.get("custom", [])
        if custom is None:
            custom = []
            operations["custom"] = custom
        if not isinstance(custom, list):
            raise CWCompilerError("operations.custom must be a list")

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

    raise CWCompilerError("operations must be a list or object")


def _upsert_flow(
    *,
    payload: dict[str, Any],
    flow: dict[str, Any],
    replace_existing: bool,
) -> None:
    flows = payload.setdefault("flows", [])
    if not isinstance(flows, list):
        raise CWCompilerError("flows must be a list")

    flow_id = int(flow.get("id", -1))
    existing_index: int | None = None
    for idx, raw_flow in enumerate(flows):
        if isinstance(raw_flow, dict) and int(raw_flow.get("id", -1)) == flow_id:
            existing_index = idx
            break

    if existing_index is not None:
        if not replace_existing:
            raise CWCompilerError(
                f"Flow id {flow_id} already exists. Use --replace-existing to overwrite"
            )
        flows[existing_index] = flow
    else:
        flows.append(flow)


def _upsert_program(
    *,
    payload: dict[str, Any],
    flow_id: int,
    program_id: int,
    program_name: str,
    program_priority: int,
    program_replicas: int,
    program_max_parallel_flows: int,
    program_load_balance: str,
) -> dict[str, Any]:
    if program_priority < 1:
        raise CWCompilerError("program_priority must be >= 1")
    if program_replicas < 1:
        raise CWCompilerError("program_replicas must be >= 1")
    if program_max_parallel_flows < 1:
        raise CWCompilerError("program_max_parallel_flows must be >= 1")
    if program_load_balance not in {"round_robin", "least_busy"}:
        raise CWCompilerError("program_load_balance must be round_robin or least_busy")

    program = {
        "id": program_id,
        "name": program_name,
        "flows": [flow_id],
        "priority": program_priority,
        "replicas": program_replicas,
        "max_parallel_flows": program_max_parallel_flows,
        "load_balance": program_load_balance,
        "allow_async": True,
        "allow_out_of_order": True,
    }

    programs = payload.setdefault("programs", [])
    if not isinstance(programs, list):
        raise CWCompilerError("programs must be a list")

    existing_index: int | None = None
    for idx, raw_program in enumerate(programs):
        if isinstance(raw_program, dict) and int(raw_program.get("id", -1)) == program_id:
            existing_index = idx
            break

    if existing_index is None:
        programs.append(program)
    else:
        programs[existing_index] = program

    return program


@dataclass(frozen=True)
class _BaseDeviceProfile:
    grid_x: int
    grid_y: int
    supported_data_types: tuple[str, ...]


def _load_base_device_profile(payload: dict[str, Any]) -> _BaseDeviceProfile:
    raw_device = payload.get("device", {})
    if not isinstance(raw_device, dict):
        raise CWCompilerError("device must be an object")

    preset_name = str(raw_device.get("preset", "intel_de0_nano"))
    preset = get_device_preset(preset_name)

    raw_grid = raw_device.get("grid", {})
    if raw_grid is None:
        raw_grid = {}
    if not isinstance(raw_grid, dict):
        raise CWCompilerError("device.grid must be an object")

    grid_x = int(raw_grid.get("x", preset.default_grid_x))
    grid_y = int(raw_grid.get("y", preset.default_grid_y))
    if grid_x < 1 or grid_y < 1:
        raise CWCompilerError("device.grid.x and device.grid.y must be >= 1")

    data_width = int(raw_device.get("data_width", preset.data_width))
    raw_data_types = raw_device.get("data_types")
    if raw_data_types is None:
        supported_data_types = (f"int{data_width}",)
    else:
        if not isinstance(raw_data_types, list) or not raw_data_types:
            raise CWCompilerError("device.data_types must be a non-empty list when provided")
        parsed: list[str] = []
        for idx, raw in enumerate(raw_data_types):
            dtype = str(raw).strip()
            if not dtype:
                raise CWCompilerError(f"device.data_types[{idx}] cannot be empty")
            parsed.append(dtype)
        if len(set(parsed)) != len(parsed):
            raise CWCompilerError("device.data_types contains duplicate entries")
        supported_data_types = tuple(parsed)

    return _BaseDeviceProfile(
        grid_x=grid_x,
        grid_y=grid_y,
        supported_data_types=supported_data_types,
    )


def merge_cw_into_config(
    *,
    base_config_path: Path,
    out_config_path: Path,
    program: str,
    flow_id: int,
    name: str,
    entry_x: int,
    entry_y: int,
    replace_existing: bool,
    max_in_flight: int | None = None,
    dtype: str | None = None,
    lane_parallelism: int | None = None,
    placement_policy: str | None = None,
    lowering_profile: str | None = None,
    program_id: int | None = None,
    program_name: str | None = None,
    program_priority: int | None = None,
    program_replicas: int = 1,
    program_max_parallel_flows: int | None = None,
    program_load_balance: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(base_config_path.read_text())
    except FileNotFoundError as exc:
        raise CWCompilerError(f"Config file not found: {base_config_path}") from exc
    except json.JSONDecodeError as exc:
        raise CWCompilerError(f"Invalid JSON in {base_config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise CWCompilerError("Base config root must be a JSON object")

    base_device = _load_base_device_profile(payload)
    base_capabilities = _capability_filter_from_payload(payload)
    spec, shape = parse_cw_program(program)

    resolved_dtype = dtype
    resolved_dtype_source = "cli"
    if resolved_dtype is None and spec.preferred_dtype is not None:
        resolved_dtype = spec.preferred_dtype
        resolved_dtype_source = "pragma"
    if resolved_dtype is None:
        if "float32" in base_device.supported_data_types:
            resolved_dtype = "float32"
        else:
            resolved_dtype = base_device.supported_data_types[0]
        resolved_dtype_source = "device_default"

    if resolved_dtype not in base_device.supported_data_types:
        raise CWCompilerError(
            f"dtype '{resolved_dtype}' is not supported by device data types "
            f"{list(base_device.supported_data_types)}"
        )

    if max_in_flight is not None:
        resolved_max_in_flight = max_in_flight
        resolved_max_in_flight_source = "cli"
    elif spec.preferred_max_in_flight is not None:
        resolved_max_in_flight = spec.preferred_max_in_flight
        resolved_max_in_flight_source = "pragma"
    else:
        resolved_max_in_flight = _DEFAULT_CW_MAX_IN_FLIGHT
        resolved_max_in_flight_source = "default"

    flow = build_flow_from_cw_program(
        program=program,
        flow_id=flow_id,
        name=name,
        entry_x=entry_x,
        entry_y=entry_y,
        max_in_flight=resolved_max_in_flight,
        dtype=resolved_dtype,
        grid_x=base_device.grid_x,
        grid_y=base_device.grid_y,
        lane_parallelism=lane_parallelism,
        placement_policy=placement_policy,
        lowering_profile=lowering_profile,
        parsed_spec_shape=(spec, shape),
        max_in_flight_source=resolved_max_in_flight_source,
        dtype_source=resolved_dtype_source,
        capabilities=base_capabilities,
    )

    _upsert_flow(payload=payload, flow=flow, replace_existing=replace_existing)
    _ensure_operations_present(payload, needed_ops={"add", "mul", "max"})

    resolved_program_id = flow_id if program_id is None else program_id
    resolved_program_name = program_name or f"cw_program_{resolved_program_id}"
    resolved_program_priority = (
        program_priority
        if program_priority is not None
        else (
            spec.preferred_program_priority
            if spec.preferred_program_priority is not None
            else _DEFAULT_CW_PROGRAM_PRIORITY
        )
    )
    resolved_max_parallel = (
        program_replicas if program_max_parallel_flows is None else program_max_parallel_flows
    )
    resolved_program_load_balance = (
        program_load_balance
        if program_load_balance is not None
        else (
            spec.preferred_program_load_balance
            if spec.preferred_program_load_balance is not None
            else _DEFAULT_CW_PROGRAM_LOAD_BALANCE
        )
    )

    program_obj = _upsert_program(
        payload=payload,
        flow_id=flow_id,
        program_id=resolved_program_id,
        program_name=resolved_program_name,
        program_priority=resolved_program_priority,
        program_replicas=program_replicas,
        program_max_parallel_flows=max(1, resolved_max_parallel),
        program_load_balance=resolved_program_load_balance,
    )
    flow.setdefault("cw_hints", {})
    flow["cw_hints"].update(
        {
            "program_priority_preferred": spec.preferred_program_priority,
            "program_priority_compiled": resolved_program_priority,
            "program_load_balance_preferred": spec.preferred_program_load_balance,
            "program_load_balance_compiled": resolved_program_load_balance,
        }
    )

    out_config_path.parent.mkdir(parents=True, exist_ok=True)
    out_config_path.write_text(json.dumps(payload, indent=2) + "\n")

    # Validate through the canonical parser/compiler boundary.
    load_config(out_config_path)

    return flow, program_obj
