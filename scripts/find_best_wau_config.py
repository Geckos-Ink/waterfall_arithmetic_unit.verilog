#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.
"""Find the best-fitting WAU config for a program.

Given a `.cw` kernel (or an existing `.json` workload config), this sweeps every
grid shape up to a device budget, predicts each one's behaviour with the real
scheduler (the simulator), and recommends:

  * the best-performing config that fits, and
  * the "efficient" config -- the fewest cores that still land within a small
    makespan tolerance of the best (so you only synthesize as many cores as the
    program actually needs, which matters on a ~20k-LE DE0-Nano where anything
    above a 2x4 grid does not fit).

It is a thin, friendly wrapper over `python -m waugen fit-config`; use that
directly for the full flag set.

Examples:
    python scripts/find_best_wau_config.py CWs/stress/mesh_stress.cw
    python scripts/find_best_wau_config.py CWs/stress/mesh_stress.cw \\
        --out-config .build/fit/mesh_stress_best.json --emit best
    python scripts/find_best_wau_config.py src/python/configs/wau_example_pogram_compiled.json --quick
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "python"))

from waugen.cli import main as waugen_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("program", type=Path, help=".cw kernel or .json workload config")
    parser.add_argument("--device", default="intel_de0_nano", help="device preset (default: intel_de0_nano)")
    parser.add_argument("--max-grid", default="2x4", help="largest grid as XxY[xZ] (default: 2x4)")
    parser.add_argument("--out-config", type=Path, default=None, help="write the recommended config here")
    parser.add_argument("--out-report", type=Path, default=None, help="write the JSON report here")
    parser.add_argument("--emit", choices=["best", "efficient"], default="efficient",
                        help="which recommendation to write to --out-config (default: efficient)")
    parser.add_argument("--quick", action="store_true", help="grid-only sweep (balanced memory, uniform ops)")
    parser.add_argument("--lut-budget", type=int, default=None, help="override estimated LUT budget")
    parser.add_argument("--max-utilization", type=float, default=None, help="override max utilization (default 0.9)")
    parser.add_argument("--tolerance", type=float, default=None, help="override efficient/knee makespan slack (default 0.10)")
    args = parser.parse_args(argv)

    if not args.program.exists():
        print(f"program not found: {args.program}", file=sys.stderr)
        return 2

    fit_argv = ["fit-config"]
    if args.program.suffix == ".cw":
        fit_argv += ["--program-file", str(args.program)]
    else:
        fit_argv += ["--config", str(args.program)]
    fit_argv += ["--device", args.device, "--max-grid", args.max_grid, "--emit", args.emit]
    if args.out_config is not None:
        fit_argv += ["--out-config", str(args.out_config)]
    if args.out_report is not None:
        fit_argv += ["--out-report", str(args.out_report)]
    if args.quick:
        fit_argv += ["--quick"]
    if args.lut_budget is not None:
        fit_argv += ["--lut-budget", str(args.lut_budget)]
    if args.max_utilization is not None:
        fit_argv += ["--max-utilization", str(args.max_utilization)]
    if args.tolerance is not None:
        fit_argv += ["--tolerance", str(args.tolerance)]

    return waugen_main(fit_argv)


if __name__ == "__main__":
    raise SystemExit(main())
