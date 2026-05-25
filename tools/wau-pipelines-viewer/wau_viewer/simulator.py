"""Drive iverilog/vvp to produce the trace consumed by the viewer."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .tb_generator import render_testbench, write_stimulus_hex


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


@dataclass
class SimulationResult:
    workdir: Path
    trace_path: Path
    vvp_stdout: str
    vvp_stderr: str


class IverilogRunner:
    """Compiles and runs the auto-generated testbench against the provided RTL."""

    def __init__(self, rtl_dir: Path, keep_workdir: bool = False) -> None:
        self.rtl_dir = Path(rtl_dir).resolve()
        if not self.rtl_dir.exists():
            raise FileNotFoundError(f"RTL directory not found: {self.rtl_dir}")
        for module in REQUIRED_RTL_MODULES:
            if not (self.rtl_dir / module).exists():
                raise FileNotFoundError(
                    f"missing generated RTL file: {self.rtl_dir / module} — "
                    "regenerate with `waugen generate`"
                )
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
            *[str(self.rtl_dir / m) for m in REQUIRED_RTL_MODULES],
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
                f"  stdout: {rp.stdout}\n"
                f"  stderr: {rp.stderr}\n"
            )

        trace_path = workdir / "trace.log"
        if not trace_path.exists():
            raise RuntimeError("vvp finished but trace.log was not produced")

        return SimulationResult(
            workdir=workdir,
            trace_path=trace_path,
            vvp_stdout=rp.stdout,
            vvp_stderr=rp.stderr,
        )
