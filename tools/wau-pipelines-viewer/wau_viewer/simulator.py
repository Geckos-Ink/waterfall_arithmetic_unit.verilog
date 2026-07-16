"""Drive iverilog/vvp to produce the trace consumed by the viewer."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .tb_generator import render_testbench, write_stimulus_hex
from .trace_parser import parse_trace


REQUIRED_RTL_MODULES = (
    "wau_operation_alu.v",
    "wau_neighbor_forward.v",
    "wau_highway_router.v",
    "wau_highway_mesh.v",
    "wau_core_station.v",
    "wau_core.v",
    "wau_coordinator.v",
    "wau_top.v",
)

# Generated files that must never be fed to the viewer testbench build:
# board wrappers reference physical pins/PLLs, and MMIO glue sits between the
# board wrapper and wau_top (the viewer drives wau_top's ports directly).
EXCLUDED_RTL_PATTERNS = ("de0_nano", "board", "mmio")


def collect_rtl_sources(rtl_dir: Path) -> list[Path]:
    """Return the RTL files to compile for the viewer testbench.

    The required core module set must exist; any additional generated
    ``wau_*.v`` (e.g. new mesh/router variants emitted by future waugen
    versions) is picked up automatically so ad-hoc circuits simulate without
    the viewer needing a hardcoded manifest update, while board wrappers and
    host-bus glue are excluded.
    """
    rtl_dir = Path(rtl_dir)
    missing = [m for m in REQUIRED_RTL_MODULES if not (rtl_dir / m).exists()]
    if missing:
        raise FileNotFoundError(
            f"missing generated RTL file(s) in {rtl_dir}: {', '.join(missing)} — "
            "regenerate with `waugen generate`"
        )
    sources = [rtl_dir / m for m in REQUIRED_RTL_MODULES]
    known = set(REQUIRED_RTL_MODULES)
    for path in sorted(rtl_dir.glob("wau_*.v")):
        if path.name in known:
            continue
        if any(pat in path.name for pat in EXCLUDED_RTL_PATTERNS):
            continue
        sources.append(path)
    return sources


def _tail(text: str, lines: int = 12) -> str:
    chunks = text.strip().splitlines()
    return "\n".join(chunks[-lines:])


@dataclass
class SimulationResult:
    workdir: Path
    trace_path: Path
    vvp_stdout: str
    vvp_stderr: str


def profile_core_operations(
    result: SimulationResult, opcode_names: Mapping[int, str]
) -> dict[int, tuple[str, ...]]:
    """Return the RTL-observed operation component set for every core.

    This is deliberately derived from actual dispatch handshakes in the vvp
    trace, not from placement metadata, so architecture specialization can be
    checked against what the emitted coordinator really sends to each core.
    """
    trace = parse_trace(result.trace_path)
    observed: dict[int, set[str]] = {
        index: set() for index in range(trace.meta.core_count)
    }
    for cycle in trace.cycles:
        for core in cycle.cores:
            if core.dispatched and core.disp_op is not None:
                observed[core.core_index].add(
                    opcode_names.get(core.disp_op, f"opcode_{core.disp_op}")
                )
    return {index: tuple(sorted(names)) for index, names in observed.items()}


class IverilogRunner:
    """Compiles and runs the auto-generated testbench against the provided RTL."""

    def __init__(self, rtl_dir: Path, keep_workdir: bool = False) -> None:
        self.rtl_dir = Path(rtl_dir).resolve()
        if not self.rtl_dir.exists():
            raise FileNotFoundError(f"RTL directory not found: {self.rtl_dir}")
        self.rtl_sources = collect_rtl_sources(self.rtl_dir)
        if shutil.which("iverilog") is None:
            raise RuntimeError("iverilog not found on PATH")
        if shutil.which("vvp") is None:
            raise RuntimeError("vvp not found on PATH")
        self.keep_workdir = keep_workdir

    def run(
        self,
        flow_ids: Sequence[int],
        a_values: Sequence[int],
        b_values: Sequence[int],
        max_cycles: int = 2000,
    ) -> SimulationResult:
        workdir = Path(tempfile.mkdtemp(prefix="wau_viewer_"))
        write_stimulus_hex(workdir, flow_ids, a_values, b_values)
        tb_path = workdir / "tb_wau_viewer.v"
        tb_path.write_text(render_testbench(len(flow_ids), max_cycles))

        sim_bin = workdir / "tb_wau_viewer.out"
        compile_cmd = [
            "iverilog",
            "-g2005-sv",
            "-I", str(self.rtl_dir),
            "-s", "tb_wau_viewer",
            "-o", str(sim_bin),
            *[str(p) for p in self.rtl_sources],
            str(tb_path),
        ]
        cp = subprocess.run(compile_cmd, capture_output=True, text=True)
        if cp.returncode != 0:
            raise RuntimeError(
                "iverilog compile failed:\n"
                f"  cmd: {' '.join(compile_cmd)}\n"
                f"  stdout: {cp.stdout}\n"
                f"  stderr: {cp.stderr}\n"
            )

        # vvp must run with cwd=workdir so $fopen("trace.log") and $readmemh
        # paths resolve relative to the stimulus files we just wrote.
        rp = subprocess.run(
            ["vvp", str(sim_bin)],
            capture_output=True, text=True, cwd=workdir,
        )
        if rp.returncode != 0:
            raise RuntimeError(
                "vvp simulation failed:\n"
                f"  stdout tail: {_tail(rp.stdout)}\n"
                f"  stderr tail: {_tail(rp.stderr)}\n"
                f"  workdir kept for inspection: {workdir}\n"
            )

        trace_path = workdir / "trace.log"
        if not trace_path.exists():
            raise RuntimeError(
                "vvp finished but trace.log was not produced\n"
                f"  stdout tail: {_tail(rp.stdout)}\n"
                f"  workdir kept for inspection: {workdir}\n"
            )

        return SimulationResult(
            workdir=workdir,
            trace_path=trace_path,
            vvp_stdout=rp.stdout,
            vvp_stderr=rp.stderr,
        )
