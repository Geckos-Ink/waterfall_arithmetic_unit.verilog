"""Parse the textual per-cycle trace emitted by the auto-generated testbench."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _parse_kv(tokens: List[str]) -> Dict[str, str]:
    """Parse a list of `k=v` tokens into a dict, accepting signed/unsigned ints."""
    out: Dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k] = v
    return out


@dataclass
class HostInState:
    valid: bool = False
    ready: bool = False
    flow_id: int = 0
    a: int = 0
    b: int = 0
    accepted: bool = False  # valid && ready in this cycle


@dataclass
class HostOutState:
    valid: bool = False
    ready: bool = False
    flow_id: int = 0
    value: int = 0
    delivered: bool = False  # valid && ready


@dataclass
class CoreState:
    core_index: int
    busy: bool = False
    cache_hit_pulse: bool = False
    cache_hit_count: int = 0
    cache_lookup_count: int = 0
    dispatched: bool = False
    disp_op: Optional[int] = None
    disp_a: Optional[int] = None
    disp_b: Optional[int] = None
    disp_immediate_b: Optional[int] = None
    disp_use_immediate: bool = False
    disp_stage_id: Optional[int] = None
    disp_flow_id: Optional[int] = None
    disp_tag: Optional[int] = None
    # Set when this dispatch is a fast-path self-dispatch (core_self_dispatch_*)
    # rather than an ordinary coordinator dispatch (core_dispatch_*) -- the
    # direct core-to-core handoff `compiler.station_program` introduces.
    disp_fastpath: bool = False
    has_result: bool = False
    res_value: Optional[int] = None
    res_stage_id: Optional[int] = None
    res_flow_id: Optional[int] = None
    res_tag: Optional[int] = None
    # Data-plane delivery: a result packet arrived at this core over the data
    # mesh this cycle (src core -> this core). Drives the data-movement animation.
    data_delivered: bool = False
    ddeliv_src: Optional[int] = None
    ddeliv_value: Optional[int] = None
    ddeliv_flow_id: Optional[int] = None
    ddeliv_stage_id: Optional[int] = None
    ddeliv_tag: Optional[int] = None
    # Set when the delivery left the fabric at a highway line's hub rather than
    # at this core's local port (the default `lines` topology).
    ddeliv_line: Optional[int] = None
    # Fast-path delivery (compiler.build_fast_path_tables / wau_core_station):
    # this data-plane delivery is a core handing its result directly to the
    # next stage's core instead of routing back through wau_coordinator.
    # ddeliv_next_stage/_next_op are only set when ddeliv_fastpath is true,
    # and only for a genuine per-core delivery -- a hub delivery tagged
    # fastpath means the hop crossed highway lines and was safely absorbed
    # by the coordinator instead (no per-core next-stage info to show).
    ddeliv_fastpath: bool = False
    ddeliv_next_stage: Optional[int] = None
    ddeliv_next_op: Optional[int] = None
    # Highway contract bus. `highway_request` is the core wanting the data
    # highway at all; `highway_call` is that request landing on the core's own
    # offered slot -- the cycle it actually calls the highway; `highway_holder`
    # marks the core that currently owns the highway under a contract.
    highway_request: bool = False
    highway_call: bool = False
    highway_holder: bool = False


@dataclass
class ObsState:
    hops: int = 0
    stalls: int = 0
    forwards: int = 0
    delivered: int = 0
    cache_hits: int = 0
    cache_lookups: int = 0


@dataclass
class LineHighwayState:
    """Contract-bus state of ONE highway line for one cycle.

    Each line arbitrates independently, so every line has its own slot cursor
    and its own holder. ``slot`` and ``grant_core`` are line-local ids
    (``0..line_size-1``); ``line_base`` converts them to global core indices.

    ``grant_valid`` / ``grant_core`` say who owns this line outright,
    ``grant_mode`` which kind of contract they posted, and ``grant_remaining``
    how many beats of it are still to run.
    """

    line: int = 0
    line_base: int = 0
    slot: int = 0
    grant_valid: bool = False
    grant_core: int = 0
    grant_mode: int = 0
    grant_remaining: int = 0

    @property
    def slot_core(self) -> int:
        """The global core index of the slot being offered."""
        return self.line_base + self.slot

    @property
    def grant_core_index(self) -> int:
        """The global core index of the contract holder."""
        return self.line_base + self.grant_core


@dataclass
class HighwayState:
    """Contract-bus state of the whole data highway fabric for one cycle.

    ``lines`` holds one record per independent highway. The counters are
    fabric-wide totals across every line.
    """

    lines: List[LineHighwayState] = field(default_factory=list)
    grant_count: int = 0
    hold_cycles: int = 0
    defer_count: int = 0

    @property
    def any_granted(self) -> bool:
        return any(line.grant_valid for line in self.lines)

    def line_for_core(self, core_index: int, line_size: int) -> "LineHighwayState":
        """The line record governing ``core_index``."""
        idx = core_index // line_size if line_size else 0
        for line in self.lines:
            if line.line == idx:
                return line
        return LineHighwayState(line=idx, line_base=idx * line_size)


@dataclass
class CycleSnapshot:
    cycle: int
    host_in: HostInState = field(default_factory=HostInState)
    host_out: HostOutState = field(default_factory=HostOutState)
    cores: List[CoreState] = field(default_factory=list)
    obs: ObsState = field(default_factory=ObsState)
    highway: HighwayState = field(default_factory=HighwayState)


@dataclass
class TraceMeta:
    grid_x: int = 0
    grid_y: int = 0
    core_count: int = 0
    stim_count: int = 0
    total_cycles: int = 0
    outputs_seen: int = 0
    # Highway geometry: how many independent highways, and how many cores share
    # one. Under the default `lines` topology that is one highway per grid row.
    line_count: int = 1
    line_size: int = 0


@dataclass
class ParsedTrace:
    meta: TraceMeta
    cycles: List[CycleSnapshot]


def _to_int(v: str) -> int:
    return int(v)


def parse_trace(path: Path) -> ParsedTrace:
    meta = TraceMeta()
    cycles: List[CycleSnapshot] = []
    current: Optional[CycleSnapshot] = None

    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            head, _, rest = line.partition(" ")
            tokens = rest.split()

            if head == "META":
                kv = _parse_kv(tokens)
                meta.grid_x = int(kv.get("grid_x", 0))
                meta.grid_y = int(kv.get("grid_y", 0))
                meta.core_count = int(kv.get("core_count", 0))
                meta.stim_count = int(kv.get("stim_count", 0))
                meta.line_count = int(kv.get("line_count", 1)) or 1
                meta.line_size = int(kv.get("line_size", 0)) or meta.core_count
            elif head == "@CYCLE":
                if current is not None:
                    cycles.append(current)
                current = CycleSnapshot(cycle=int(tokens[0]))
            elif head == "@END":
                kv = _parse_kv(tokens)
                meta.total_cycles = int(kv.get("total_cycles", 0))
                meta.outputs_seen = int(kv.get("outputs_seen", 0))
                if current is not None:
                    cycles.append(current)
                    current = None
            elif head == "HOST_IN" and current is not None:
                kv = _parse_kv(tokens)
                current.host_in.valid = kv.get("v") == "1"
                current.host_in.ready = kv.get("r") == "1"
                current.host_in.flow_id = _to_int(kv.get("flow", "0"))
                current.host_in.a = _to_int(kv.get("a", "0"))
                current.host_in.b = _to_int(kv.get("b", "0"))
                current.host_in.accepted = current.host_in.valid and current.host_in.ready
            elif head == "HOST_OUT" and current is not None:
                kv = _parse_kv(tokens)
                current.host_out.valid = kv.get("v") == "1"
                current.host_out.ready = kv.get("r") == "1"
                current.host_out.flow_id = _to_int(kv.get("flow", "0"))
                current.host_out.value = _to_int(kv.get("val", "0"))
                current.host_out.delivered = current.host_out.valid and current.host_out.ready
            elif head == "CORE" and current is not None:
                core_idx = int(tokens[0])
                kv = _parse_kv(tokens[1:])
                cs = CoreState(core_index=core_idx)
                cs.busy = kv.get("busy") == "1"
                cs.cache_hit_pulse = kv.get("cache_hit") == "1"
                cs.cache_hit_count = _to_int(kv.get("cache_h_count", "0"))
                cs.cache_lookup_count = _to_int(kv.get("cache_l_count", "0"))
                if kv.get("disp") == "1":
                    cs.dispatched = True
                    cs.disp_op = _to_int(kv.get("disp_op", "0"))
                    cs.disp_a = _to_int(kv.get("disp_a", "0"))
                    cs.disp_b = _to_int(kv.get("disp_b", "0"))
                    cs.disp_immediate_b = _to_int(kv.get("disp_imm", "0"))
                    cs.disp_use_immediate = kv.get("disp_use_imm") == "1"
                    cs.disp_stage_id = _to_int(kv.get("disp_stage", "0"))
                    cs.disp_flow_id = _to_int(kv.get("disp_flow", "0"))
                    cs.disp_tag = _to_int(kv.get("disp_tag", "0"))
                    cs.disp_fastpath = kv.get("disp_fastpath") == "1"
                if kv.get("res") == "1":
                    cs.has_result = True
                    cs.res_value = _to_int(kv.get("res_val", "0"))
                    cs.res_stage_id = _to_int(kv.get("res_stage", "0"))
                    cs.res_flow_id = _to_int(kv.get("res_flow", "0"))
                    cs.res_tag = _to_int(kv.get("res_tag", "0"))
                if kv.get("ddeliv") == "1":
                    cs.data_delivered = True
                    cs.ddeliv_src = _to_int(kv.get("ddeliv_src", "0"))
                    cs.ddeliv_value = _to_int(kv.get("ddeliv_val", "0"))
                    cs.ddeliv_flow_id = _to_int(kv.get("ddeliv_flow", "0"))
                    cs.ddeliv_stage_id = _to_int(kv.get("ddeliv_stage", "0"))
                    cs.ddeliv_tag = _to_int(kv.get("ddeliv_tag", "0"))
                    if "ddeliv_line" in kv:
                        cs.ddeliv_line = _to_int(kv["ddeliv_line"])
                    cs.ddeliv_fastpath = kv.get("ddeliv_fastpath") == "1"
                    if "ddeliv_next_stage" in kv:
                        cs.ddeliv_next_stage = _to_int(kv["ddeliv_next_stage"])
                    if "ddeliv_next_op" in kv:
                        cs.ddeliv_next_op = _to_int(kv["ddeliv_next_op"])
                cs.highway_request = kv.get("hwy_req") == "1"
                cs.highway_call = kv.get("hwy_call") == "1"
                cs.highway_holder = kv.get("hwy_hold") == "1"
                current.cores.append(cs)
            elif head == "OBS" and current is not None:
                kv = _parse_kv(tokens)
                current.obs = ObsState(
                    hops=_to_int(kv.get("hops", "0")),
                    stalls=_to_int(kv.get("stalls", "0")),
                    forwards=_to_int(kv.get("forwards", "0")),
                    delivered=_to_int(kv.get("deliv", "0")),
                    cache_hits=_to_int(kv.get("cache_h", "0")),
                    cache_lookups=_to_int(kv.get("cache_l", "0")),
                )
            elif head == "HWY" and current is not None:
                kv = _parse_kv(tokens)
                line_index = _to_int(kv.get("line", "0"))
                line_size = meta.line_size or 1
                current.highway.lines.append(LineHighwayState(
                    line=line_index,
                    line_base=line_index * line_size,
                    slot=_to_int(kv.get("slot", "0")),
                    grant_valid=kv.get("grant") == "1",
                    grant_core=_to_int(kv.get("gcore", "0")),
                    grant_mode=_to_int(kv.get("gmode", "0")),
                    grant_remaining=_to_int(kv.get("grem", "0")),
                ))
                # Fabric-wide totals are repeated on every line record.
                current.highway.grant_count = _to_int(kv.get("grants", "0"))
                current.highway.hold_cycles = _to_int(kv.get("hold", "0"))
                current.highway.defer_count = _to_int(kv.get("defer", "0"))

    if current is not None:
        cycles.append(current)

    if meta.core_count == 0 and cycles:
        meta.core_count = len(cycles[0].cores)
    if meta.total_cycles == 0 and cycles:
        meta.total_cycles = cycles[-1].cycle

    return ParsedTrace(meta=meta, cycles=cycles)


def derive_bottlenecks(trace: ParsedTrace) -> Dict[str, object]:
    """Lightweight post-analysis surfaced in the stats panel."""
    if not trace.cycles:
        return {"busiest_core": None, "core_busy_ratio": [], "stall_rate": 0.0}

    n = len(trace.cycles)
    core_count = trace.meta.core_count
    busy = [0] * core_count
    dispatched = [0] * core_count
    highway_calls = [0] * core_count
    backpressure = 0
    highway_held_cycles = 0
    for snap in trace.cycles:
        for c in snap.cores:
            if c.busy:
                busy[c.core_index] += 1
            if c.dispatched:
                dispatched[c.core_index] += 1
            if c.highway_call:
                highway_calls[c.core_index] += 1
        if snap.host_in.valid and not snap.host_in.ready:
            backpressure += 1
        if snap.highway.any_granted:
            highway_held_cycles += 1

    busy_ratio = [b / n for b in busy]
    busy_per_cycle = [sum(1 for c in snap.cores if c.busy) for snap in trace.cycles]
    active_core_count = sum(1 for count in dispatched if count > 0)
    peak_busy_cores = max(busy_per_cycle, default=0)
    average_busy_cores = sum(busy_per_cycle) / n
    fabric_busy_ratio = (
        sum(busy_per_cycle) / (n * core_count) if core_count else 0.0
    )
    busiest = int(max(range(core_count), key=lambda i: busy_ratio[i])) if core_count else None
    last_obs = trace.cycles[-1].obs
    last_hwy = trace.cycles[-1].highway
    stall_rate = last_obs.stalls / max(1, last_obs.hops)
    return {
        "busiest_core": busiest,
        "core_busy_ratio": busy_ratio,
        "core_dispatch_count": dispatched,
        "active_core_count": active_core_count,
        "peak_busy_cores": peak_busy_cores,
        "average_busy_cores": average_busy_cores,
        "fabric_busy_ratio": fabric_busy_ratio,
        "backpressure_cycles": backpressure,
        "stall_rate": stall_rate,
        "total_cycles": n,
        "outputs_seen": trace.meta.outputs_seen,
        # Highway contract bus: how often cores called the highway, how long it
        # was owned under contract, and how much traffic that held off.
        "highway_call_count": highway_calls,
        "highway_held_cycles": highway_held_cycles,
        "highway_hold_ratio": highway_held_cycles / n,
        "highway_grant_count": last_hwy.grant_count,
        "highway_defer_count": last_hwy.defer_count,
    }
