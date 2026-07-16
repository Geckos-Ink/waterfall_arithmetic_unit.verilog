"""Prepare ad-hoc WAU circuits for stress simulation.

Bridges the viewer to the repository's `waugen` toolchain so a user can go
from a workload description straight to a simulated, animated stress run in
one command:

- a raw config (``--config``) is emitted to RTL via ``waugen generate``;
- a ``.cw`` kernel (``--cw``) is first lowered with ``waugen compile-cw`` onto
  a base config (default: the demo-independent
  ``src/python/configs/wau_cw_fit_base.json``), then emitted.

Everything is invoked through ``python -m waugen`` subprocesses so the viewer
never re-implements (or drifts from) the compiler/scheduler/emission chain.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_CW_BASE_CONFIG = Path("src/python/configs/wau_cw_fit_base.json")
DEFAULT_CW_FLOW_ID = 101


@dataclass
class PreparedCircuit:
    """Artifacts of a freshly generated circuit, ready for the simulator."""

    config_path: Path
    rtl_dir: Path
    program_path: Path
    schedule_path: Path


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward looking for the WAU generator package."""
    here = (start or Path(__file__)).resolve()
    for cand in [here, *here.parents]:
        if (cand / "src" / "python" / "waugen").is_dir():
            return cand
    return None


def _run_waugen(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    python_path = str(repo_root / "src" / "python")
    if env.get("PYTHONPATH"):
        python_path = python_path + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = python_path
    cmd = [sys.executable, "-m", "waugen", *args]
    cp = subprocess.run(
        cmd, capture_output=True, text=True, cwd=repo_root, env=env,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            "waugen failed:\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stdout: {cp.stdout.strip()}\n"
            f"  stderr: {cp.stderr.strip()}\n"
        )
    return cp


def prepare_circuit(
    build_dir: Path,
    config: Optional[Path] = None,
    cw_program: Optional[Path] = None,
    base_config: Optional[Path] = None,
    flow_id: int = DEFAULT_CW_FLOW_ID,
    repo_root: Optional[Path] = None,
) -> PreparedCircuit:
    """Compile a workload and emit its ad-hoc RTL into ``build_dir``.

    Exactly one of ``config`` / ``cw_program`` must be given. Returns the
    generated artifact paths (RTL dir + program/schedule JSON) that the
    simulator and viewer consume.
    """
    if (config is None) == (cw_program is None):
        raise ValueError("provide exactly one of `config` or `cw_program`")
    root = repo_root or find_repo_root()
    if root is None:
        raise RuntimeError(
            "could not locate the waugen repository root (src/python/waugen); "
            "pass repo_root explicitly"
        )

    build_dir = Path(build_dir).resolve()
    build_dir.mkdir(parents=True, exist_ok=True)

    if cw_program is not None:
        cw_path = Path(cw_program).resolve()
        base = Path(base_config).resolve() if base_config else root / DEFAULT_CW_BASE_CONFIG
        if not base.exists():
            raise FileNotFoundError(f"base config not found: {base}")
        merged = build_dir / f"{cw_path.stem}_viewer_config.json"
        _run_waugen(root, [
            "compile-cw",
            "--program-file", str(cw_path),
            "--flow-id", str(flow_id),
            "--base-config", str(base),
            "--out-config", str(merged),
            "--replace-existing",
        ])
        config_path = merged
    else:
        config_path = Path(config).resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"config not found: {config_path}")

    rtl_dir = build_dir / "rtl"
    _run_waugen(root, [
        "generate",
        "--config", str(config_path),
        "--out", str(rtl_dir),
        "--summary",
    ])

    program_path = rtl_dir / "wau_program.json"
    schedule_path = rtl_dir / "wau_schedule.json"
    if not program_path.exists():
        raise RuntimeError(f"generate finished but {program_path} is missing")
    return PreparedCircuit(
        config_path=config_path,
        rtl_dir=rtl_dir,
        program_path=program_path,
        schedule_path=schedule_path,
    )
