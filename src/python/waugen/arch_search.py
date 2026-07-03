# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Synthesis-time architecture search for WAU variants.

Enumerates deterministic candidate architectures for a given workload config
across four axes:

- core disposition: every grid shape (x, y) with x*y equal to the base
  config's core count;
- operation distribution: uniform capability vs specializing heavy operations
  (latency > 1 or non-pipelined) onto a column or onto half of the grid,
  expressed through `compiler.core_capabilities`;
- on-chip memory split: local-vs-shared RAM depth and station-cache sizing
  (`local_heavy` / `balanced` / `shared_heavy`);
- external DRAM reliance: a `dram_offload` split that shrinks on-chip memory
  and deliberately streams the working set through external DRAM.

Every candidate is compiled and scheduled through the real
`compile_project` -> `build_schedule` pipeline, so performance numbers
(makespan, dependency-edge transfer hops, fallback ratio) are the same ones
the generator reports. Placement pins from the base config are stripped so
each architecture re-derives placement and shapes compare apples-to-apples.

Resource and DRAM-traffic figures are estimates, versioned so reports remain
comparable:

- `wau_resource_model_v1`: per-core LUT/FF costs from the operation mix each
  core is allowed to run (specialized cores get cheaper ALUs), station-cache
  registers, two router planes per core, the multi-issue coordinator, and
  MMIO glue; BRAM bits from local/global RAM depths; DSP blocks for
  multiplier-capable cores. Checked against the device preset's datasheet
  capacity (`logic_cells`, `bram_kbits`, `dsp_blocks`).
- `dram_model_v1`: the workload working set is one data word per scheduled
  instruction plus operand/result movement (3 words per instruction).
  On-chip splits report the spill beyond configured on-chip capacity;
  `dram_offload` charges the full workload traffic to DRAM.

Ranking (`arch_search_rank_v1`): feasible candidates first, then lower
makespan, lower transfer hops, lower estimated DRAM traffic, lower peak
resource utilization, lower LUT count, then candidate id.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from .compiler import compile_project
from .config import ConfigError, load_config_obj
from .device_library import DevicePreset, get_device_preset
from .scheduler import build_schedule

SCHEMA_VERSION = "wau_arch_search_v1"
RESOURCE_MODEL_VERSION = "wau_resource_model_v1"
DRAM_MODEL_VERSION = "dram_model_v1"
RANKING_VERSION = "arch_search_rank_v1"

OP_DISTRIBUTIONS = ("uniform", "heavy_column", "heavy_half")
MEMORY_SPLITS = ("balanced", "local_heavy", "shared_heavy", "dram_offload")

_MIN_LOCAL_RAM_DEPTH = 16
_MIN_GLOBAL_RAM_DEPTH = 64
_MAX_STATION_CACHE_ENTRIES = 32


class ArchSearchError(ValueError):
    """Raised when architecture search cannot run on the given base config."""


@dataclass(frozen=True)
class CandidateKnobs:
    grid_x: int
    grid_y: int
    op_distribution: str
    memory_split: str
    local_ram_depth: int
    global_ram_depth: int
    station_cache_entries: int

    @property
    def candidate_id(self) -> str:
        return (
            f"g{self.grid_x}x{self.grid_y}_{self.op_distribution}_{self.memory_split}"
        )


@dataclass
class CandidateResult:
    knobs: CandidateKnobs
    feasible: bool
    infeasible_reason: str | None
    metrics: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    dram: dict[str, Any] = field(default_factory=dict)
    rank: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.knobs.candidate_id,
            "rank": self.rank,
            "feasible": self.feasible,
            "infeasible_reason": self.infeasible_reason,
            "knobs": {
                "grid_x": self.knobs.grid_x,
                "grid_y": self.knobs.grid_y,
                "op_distribution": self.knobs.op_distribution,
                "memory_split": self.knobs.memory_split,
                "local_ram_depth": self.knobs.local_ram_depth,
                "global_ram_depth": self.knobs.global_ram_depth,
                "station_cache_entries": self.knobs.station_cache_entries,
            },
            "metrics": self.metrics,
            "resources": self.resources,
            "dram": self.dram,
        }


@dataclass
class ArchSearchReport:
    base_config_path: str
    project_name: str
    device_preset: str
    device_part: str
    base_grid_x: int
    base_grid_y: int
    workload: dict[str, Any]
    candidates: list[CandidateResult]

    def to_json(self) -> dict[str, Any]:
        feasible = sum(1 for cand in self.candidates if cand.feasible)
        return {
            "schema": SCHEMA_VERSION,
            "resource_model": RESOURCE_MODEL_VERSION,
            "dram_model": DRAM_MODEL_VERSION,
            "ranking": RANKING_VERSION,
            "base_config": self.base_config_path,
            "project": self.project_name,
            "device_preset": self.device_preset,
            "device_part": self.device_part,
            "base_grid": {"x": self.base_grid_x, "y": self.base_grid_y},
            "core_count": self.base_grid_x * self.base_grid_y,
            "placement": "re-derived per candidate (base placement pins stripped)",
            "candidate_count": len(self.candidates),
            "feasible_count": feasible,
            "candidates": [cand.to_json() for cand in self.candidates],
        }


def _grid_shapes(core_count: int) -> list[tuple[int, int]]:
    """All (x, y) factor pairs preserving the base core count, widest-x last."""
    shapes = [
        (x, core_count // x)
        for x in range(1, core_count + 1)
        if core_count % x == 0
    ]
    shapes.sort(key=lambda s: (abs(s[0] - s[1]), s[0]))
    return shapes


def _heavy_op_names(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split operation names into (heavy, light) using the loaded op table."""
    config = load_config_obj(copy.deepcopy(payload))
    heavy: list[str] = []
    light: list[str] = []
    for op in config.operations:
        if op.latency > 1 or not op.pipelined:
            heavy.append(op.name)
        else:
            light.append(op.name)
    return heavy, light


def _strip_placements(payload: dict[str, Any], *, grid_x: int, grid_y: int) -> None:
    """Remove placement pins so every candidate re-derives placement, and
    clamp flow entry/exit coordinates into the candidate grid."""
    for flow in payload.get("flows", []):
        for coord_key in ("entry", "exit"):
            coord = flow.get(coord_key)
            if isinstance(coord, dict):
                coord["x"] = max(0, min(int(coord.get("x", 0)), grid_x - 1))
                coord["y"] = max(0, min(int(coord.get("y", 0)), grid_y - 1))
        for stage in flow.get("stages", []) or []:
            if isinstance(stage, dict):
                stage.pop("core", None)
                stage.pop("fallback_core", None)
        for node in flow.get("nodes", []) or []:
            if isinstance(node, dict):
                node.pop("placement", None)
                node.pop("core", None)
                node.pop("fallback_core", None)
                node.pop("candidate_cores", None)
                node.pop("fixed", None)
                node.pop("placement_directive", None)


def _capability_entries(
    knobs: CandidateKnobs, *, heavy_ops: list[str], light_ops: list[str]
) -> list[dict[str, Any]]:
    """Capability rows restricting non-specialized cores to light ops only."""
    if knobs.op_distribution == "uniform" or not heavy_ops or not light_ops:
        return []

    if knobs.op_distribution == "heavy_column":
        def restricted(x: int, _y: int) -> bool:
            return x != 0
    else:  # heavy_half
        heavy_width = max(1, math.ceil(knobs.grid_x / 2))

        def restricted(x: int, _y: int) -> bool:
            return x >= heavy_width

    entries: list[dict[str, Any]] = []
    for y in range(knobs.grid_y):
        for x in range(knobs.grid_x):
            if restricted(x, y):
                entries.append({"core": {"x": x, "y": y}, "operations": list(light_ops)})
    return entries


def build_candidate_payload(
    base_payload: dict[str, Any],
    knobs: CandidateKnobs,
    *,
    heavy_ops: list[str],
    light_ops: list[str],
) -> dict[str, Any]:
    """Materialize a candidate config payload from the base workload config."""
    payload = copy.deepcopy(base_payload)

    device = payload.setdefault("device", {})
    device.setdefault("grid", {})
    device["grid"]["x"] = knobs.grid_x
    device["grid"]["y"] = knobs.grid_y
    device["local_ram_depth"] = knobs.local_ram_depth
    device["global_ram_depth"] = knobs.global_ram_depth

    compiler = payload.setdefault("compiler", {})
    if compiler.get("routing") == "manual":
        # Manual routing requires explicit cores, which the search strips.
        compiler["routing"] = "waterfall"
    compiler["core_capabilities"] = _capability_entries(
        knobs, heavy_ops=heavy_ops, light_ops=light_ops
    )
    station_cache = compiler.setdefault("station_cache", {})
    station_cache["entries"] = knobs.station_cache_entries

    _strip_placements(payload, grid_x=knobs.grid_x, grid_y=knobs.grid_y)
    return payload


def _memory_split_knobs(
    split: str, *, base_local: int, base_global: int, base_cache_entries: int
) -> tuple[int, int, int]:
    if split == "local_heavy":
        return (
            base_local * 2,
            max(_MIN_GLOBAL_RAM_DEPTH, base_global // 2),
            min(_MAX_STATION_CACHE_ENTRIES, base_cache_entries * 4),
        )
    if split == "shared_heavy":
        return (
            max(_MIN_LOCAL_RAM_DEPTH, base_local // 2),
            base_global * 2,
            base_cache_entries,
        )
    if split == "dram_offload":
        return (
            max(_MIN_LOCAL_RAM_DEPTH, base_local // 4),
            max(_MIN_GLOBAL_RAM_DEPTH, base_global // 4),
            max(1, base_cache_entries // 2),
        )
    return base_local, base_global, base_cache_entries


def _op_lut_cost(verilog_expr: str, data_width: int) -> tuple[float, bool]:
    """(estimated LUTs, uses_dsp) for one ALU operation at the given width."""
    expr = verilog_expr
    if "*" in expr:
        return 2.0 * data_width, True
    if "/" in expr or "%" in expr:
        return 12.0 * data_width, False
    if "<<" in expr or ">>" in expr:
        return 2.5 * data_width, False
    if "&" in expr or "|" in expr or "^" in expr:
        return 1.0 * data_width, False
    if "<" in expr or ">" in expr:
        return 1.5 * data_width, False
    return 1.2 * data_width, False


def _estimate_resources(
    config: Any, knobs: CandidateKnobs, preset: DevicePreset
) -> dict[str, Any]:
    width = config.device.data_width
    grid_n = knobs.grid_x * knobs.grid_y
    dsp_per_mul = math.ceil(width / 18) ** 2

    op_caps: dict[tuple[int, int], set[str]] = {}
    for capability in config.compiler.core_capabilities:
        if capability.operations:
            op_caps[(capability.core.x, capability.core.y)] = set(capability.operations)

    total_luts = 0.0
    total_dsp = 0
    cache_entries = config.compiler.station_cache.entries

    for y in range(knobs.grid_y):
        for x in range(knobs.grid_x):
            allowed = op_caps.get((x, y))
            core_luts = 150.0 + cache_entries * 6.0  # station control + cache mgmt
            core_uses_dsp = False
            for op in config.operations:
                if allowed is not None and op.name not in allowed:
                    continue
                luts, uses_dsp = _op_lut_cost(op.verilog_expr, width)
                core_luts += luts
                core_uses_dsp = core_uses_dsp or uses_dsp
            total_luts += core_luts
            if core_uses_dsp:
                total_dsp += dsp_per_mul

    # Two router planes (control + data) per node.
    total_luts += grid_n * 2 * (100.0 + 2.0 * width)
    # Multi-issue coordinator and host MMIO glue.
    total_luts += 250.0 + config.coordinator.max_in_flight * (60.0 + 2.0 * width)
    total_luts += 350.0

    total_ffs = 0.6 * total_luts + grid_n * cache_entries * (width + 16)
    bram_kbits = (grid_n * knobs.local_ram_depth + knobs.global_ram_depth) * width / 1024.0

    luts = int(round(total_luts))
    ffs = int(round(total_ffs))
    util_luts = luts / preset.logic_cells if preset.logic_cells else 0.0
    util_bram = bram_kbits / preset.bram_kbits if preset.bram_kbits else 0.0
    util_dsp = total_dsp / preset.dsp_blocks if preset.dsp_blocks else 0.0
    utilization_max = max(util_luts, util_bram, util_dsp)

    return {
        "model": RESOURCE_MODEL_VERSION,
        "luts": luts,
        "ffs": ffs,
        "bram_kbits": round(bram_kbits, 2),
        "dsp_blocks": total_dsp,
        "util_luts": round(util_luts, 4),
        "util_bram": round(util_bram, 4),
        "util_dsp": round(util_dsp, 4),
        "utilization_max": round(utilization_max, 4),
        "fits_device": utilization_max <= 1.0,
    }


def _estimate_dram(
    knobs: CandidateKnobs, *, instruction_count: int, data_width: int
) -> dict[str, Any]:
    word_bytes = data_width / 8.0
    working_set_bytes = int(math.ceil(instruction_count * word_bytes))
    workload_traffic_bytes = int(math.ceil(instruction_count * 3 * word_bytes))
    onchip_bytes = int(
        (knobs.grid_x * knobs.grid_y * knobs.local_ram_depth + knobs.global_ram_depth)
        * word_bytes
    )

    if knobs.memory_split == "dram_offload":
        est_dram_bytes = workload_traffic_bytes
    else:
        est_dram_bytes = max(0, working_set_bytes - onchip_bytes)

    return {
        "model": DRAM_MODEL_VERSION,
        "working_set_bytes": working_set_bytes,
        "onchip_capacity_bytes": onchip_bytes,
        "est_dram_bytes": est_dram_bytes,
        "dram_required": est_dram_bytes > 0,
    }


def _schedule_metrics(schedule: Any) -> dict[str, Any]:
    instructions = schedule.instructions
    fallback_count = sum(1 for ins in instructions if ins.used_fallback)
    per_core_issues: dict[int, int] = {}
    for ins in instructions:
        per_core_issues[ins.core_index] = per_core_issues.get(ins.core_index, 0) + 1

    plan_json = schedule.to_json()
    return {
        "makespan_cycles": schedule.makespan_cycles,
        "instructions": len(instructions),
        "fallback_instruction_ratio": round(
            fallback_count / len(instructions), 4
        ) if instructions else 0.0,
        "estimated_transfer_hops_metric": plan_json["estimated_transfer_hops_metric"],
        "estimated_transfer_hops_total": plan_json["estimated_transfer_hops_total"],
        "estimated_transfer_hops_edge_count": plan_json["estimated_transfer_hops_edge_count"],
        "cores_used": len(per_core_issues),
        "peak_core_issues": max(per_core_issues.values(), default=0),
    }


def _rank_key(cand: CandidateResult) -> tuple:
    big = 10**12
    return (
        0 if cand.feasible else 1,
        cand.metrics.get("makespan_cycles", big),
        cand.metrics.get("estimated_transfer_hops_total", big),
        cand.dram.get("est_dram_bytes", big),
        int(cand.resources.get("utilization_max", 10**6) * 10000),
        cand.resources.get("luts", big),
        cand.knobs.candidate_id,
    )


def run_arch_search(
    base_config_path: Path, *, max_candidates: int | None = None
) -> ArchSearchReport:
    try:
        base_payload = json.loads(base_config_path.read_text())
    except FileNotFoundError as exc:
        raise ArchSearchError(f"Config file not found: {base_config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ArchSearchError(f"Invalid JSON in {base_config_path}: {exc}") from exc
    if not isinstance(base_payload, dict):
        raise ArchSearchError("Root JSON object expected")

    try:
        base_config = load_config_obj(copy.deepcopy(base_payload))
    except ConfigError as exc:
        raise ArchSearchError(f"Base config invalid: {exc}") from exc

    preset = get_device_preset(
        str(base_payload.get("device", {}).get("preset", "intel_de0_nano"))
    )
    heavy_ops, light_ops = _heavy_op_names(base_payload)

    base_local = base_config.device.local_ram_depth
    base_global = base_config.device.global_ram_depth
    base_cache_entries = base_config.compiler.station_cache.entries
    core_count = base_config.device.grid_x * base_config.device.grid_y

    op_distributions = (
        OP_DISTRIBUTIONS if heavy_ops and light_ops else ("uniform",)
    )

    candidates: list[CandidateResult] = []
    for grid_x, grid_y in _grid_shapes(core_count):
        for op_distribution in op_distributions:
            for memory_split in MEMORY_SPLITS:
                local_depth, global_depth, cache_entries = _memory_split_knobs(
                    memory_split,
                    base_local=base_local,
                    base_global=base_global,
                    base_cache_entries=base_cache_entries,
                )
                knobs = CandidateKnobs(
                    grid_x=grid_x,
                    grid_y=grid_y,
                    op_distribution=op_distribution,
                    memory_split=memory_split,
                    local_ram_depth=local_depth,
                    global_ram_depth=global_depth,
                    station_cache_entries=cache_entries,
                )
                if max_candidates is not None and len(candidates) >= max_candidates:
                    break
                candidates.append(_evaluate_candidate(base_payload, knobs, preset,
                                                      heavy_ops=heavy_ops,
                                                      light_ops=light_ops))
            if max_candidates is not None and len(candidates) >= max_candidates:
                break
        if max_candidates is not None and len(candidates) >= max_candidates:
            break

    candidates.sort(key=_rank_key)
    for rank, cand in enumerate(candidates, start=1):
        cand.rank = rank

    workload = {
        "flows": len(base_config.flows),
        "nodes": sum(
            len(flow.nodes) if flow.nodes else len(flow.stages)
            for flow in base_config.flows
        ),
        "programs": len(base_config.programs),
        "operations": len(base_config.operations),
        "heavy_operations": heavy_ops,
    }

    return ArchSearchReport(
        base_config_path=str(base_config_path),
        project_name=base_config.project_name,
        device_preset=preset.name,
        device_part=preset.part,
        base_grid_x=base_config.device.grid_x,
        base_grid_y=base_config.device.grid_y,
        workload=workload,
        candidates=candidates,
    )


def _evaluate_candidate(
    base_payload: dict[str, Any],
    knobs: CandidateKnobs,
    preset: DevicePreset,
    *,
    heavy_ops: list[str],
    light_ops: list[str],
) -> CandidateResult:
    payload = build_candidate_payload(
        base_payload, knobs, heavy_ops=heavy_ops, light_ops=light_ops
    )

    try:
        config = load_config_obj(payload)
        compiled = compile_project(config)
        schedule = build_schedule(compiled)
    except (ConfigError, ValueError) as exc:
        return CandidateResult(
            knobs=knobs,
            feasible=False,
            infeasible_reason=f"compile/schedule failed: {exc}",
        )

    metrics = _schedule_metrics(schedule)
    resources = _estimate_resources(config, knobs, preset)
    dram = _estimate_dram(
        knobs,
        instruction_count=metrics["instructions"],
        data_width=config.device.data_width,
    )

    feasible = bool(resources["fits_device"])
    reason = None if feasible else "resource estimate exceeds device capacity"
    return CandidateResult(
        knobs=knobs,
        feasible=feasible,
        infeasible_reason=reason,
        metrics=metrics,
        resources=resources,
        dram=dram,
    )


def format_report_text(report: ArchSearchReport, *, top: int = 10) -> str:
    lines = [
        f"arch-search report ({SCHEMA_VERSION}, ranking {RANKING_VERSION})",
        f"project: {report.project_name}  device: {report.device_preset} ({report.device_part})",
        f"base grid: {report.base_grid_x}x{report.base_grid_y}  candidates: {len(report.candidates)}"
        f" (feasible: {sum(1 for c in report.candidates if c.feasible)})",
        "",
        f"{'rank':>4}  {'candidate':<38} {'makespan':>8} {'hops':>5} "
        f"{'dram_B':>8} {'util':>6} {'luts':>7}  feasible",
    ]
    for cand in report.candidates[:top]:
        lines.append(
            f"{cand.rank:>4}  {cand.knobs.candidate_id:<38} "
            f"{cand.metrics.get('makespan_cycles', '-'):>8} "
            f"{cand.metrics.get('estimated_transfer_hops_total', '-'):>5} "
            f"{cand.dram.get('est_dram_bytes', '-'):>8} "
            f"{cand.resources.get('utilization_max', '-'):>6} "
            f"{cand.resources.get('luts', '-'):>7}  "
            f"{'yes' if cand.feasible else 'no'}"
        )
    return "\n".join(lines)
