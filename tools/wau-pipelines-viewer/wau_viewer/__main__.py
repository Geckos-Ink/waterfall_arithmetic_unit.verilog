"""Entry point: prepare/compile a circuit, run iverilog, launch the Qt viewer.

Three ways to point the viewer at a circuit:

1. pre-generated artifacts: ``--rtl-dir`` + ``--program`` (+ ``--schedule``);
2. an ad-hoc config: ``--config <config.json>`` — emitted through the real
   ``waugen generate`` into ``--build-dir`` before simulating;
3. a ``.cw`` kernel: ``--cw <program.cw>`` — compiled with
   ``waugen compile-cw`` onto ``--base-config`` and then emitted.

Stimulus can be an explicit JSON file (``--stimulus``), one packet per flow
(``--auto-stimulus``), or a seeded randomized stress stream (``--stress N``)
that interleaves flow ids to exercise the multi-issue coordinator's
concurrency on the mesh.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

from .model import derive_auto_stimulus, derive_stress_stimulus, load_model
from .prepare import DEFAULT_CW_FLOW_ID, find_repo_root, prepare_circuit
from .simulator import IverilogRunner
from .trace_parser import parse_trace


def _load_stimulus(path: Path) -> List[Tuple[int, int, int]]:
    raw = json.loads(path.read_text())
    out: List[Tuple[int, int, int]] = []
    for item in raw:
        out.append((int(item["flow_id"]), int(item["a"]), int(item["b"])))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wau_viewer")
    src = parser.add_argument_group("circuit source")
    src.add_argument("--rtl-dir", type=Path,
                     help="directory with generated WAU RTL (e.g. src/verilog/generated)")
    src.add_argument("--program", type=Path,
                     help="path to wau_program.json (required with --rtl-dir)")
    src.add_argument("--schedule", type=Path, default=None,
                     help="optional wau_schedule.json for Gantt overlay")
    src.add_argument("--config", type=Path, default=None,
                     help="waugen config JSON: generate an ad-hoc circuit and simulate it")
    src.add_argument("--cw", type=Path, default=None,
                     help=".cw kernel: compile onto --base-config, generate, and simulate")
    src.add_argument("--base-config", type=Path, default=None,
                     help="base config for --cw (default: src/python/configs/wau_cw_fit_base.json)")
    src.add_argument("--flow-id", type=int, default=DEFAULT_CW_FLOW_ID,
                     help="flow id assigned to the compiled .cw kernel")
    src.add_argument("--build-dir", type=Path, default=None,
                     help="where --config/--cw artifacts are generated "
                          "(default: <repo>/.build/viewer/<name>)")

    stim = parser.add_argument_group("stimulus")
    g = stim.add_mutually_exclusive_group()
    g.add_argument("--stimulus", type=Path,
                   help="JSON list of {flow_id,a,b} to feed into the host input")
    g.add_argument("--auto-stimulus", action="store_true",
                   help="derive a deterministic stimulus that covers each flow once")
    g.add_argument("--stress", type=int, default=None, metavar="N",
                   help="feed N seeded random packets, interleaving flow ids so "
                        "independent flows overlap on the mesh")
    stim.add_argument("--stress-seed", type=int, default=7,
                      help="RNG seed for --stress (default 7)")
    stim.add_argument("--stress-range", type=int, default=99,
                      help="max operand value for --stress (default 99)")
    parser.add_argument("--max-cycles", type=int, default=2000,
                        help="hard simulation budget (cycles); auto-raised for stress runs")

    out = parser.add_argument_group("output")
    out.add_argument("--record", type=Path, default=None,
                     help="write an MP4 or GIF (by extension) of the whole trace")
    out.add_argument("--framerate", type=int, default=10,
                     help="output framerate when --record is used")
    out.add_argument("--frames-per-cycle", type=int, default=8,
                     help="animation sub-frames rendered per simulated cycle in "
                          "headless recordings (higher = smoother/slower)")
    out.add_argument("--record-max-cycles", type=int, default=None,
                     help="only record the first N trace cycles")
    out.add_argument("--cycle-ms", type=int, default=1400,
                     help="initial playback pace in ms per simulated cycle")
    out.add_argument("--headless", action="store_true",
                     help="never show the GUI window (useful with --record)")
    out.add_argument("--dump-trace", type=Path, default=None,
                     help="copy the raw iverilog trace.log here for inspection")
    args = parser.parse_args(argv)

    sources = [s for s in (args.rtl_dir, args.config, args.cw) if s]
    if len(sources) != 1:
        parser.error("provide exactly one of --rtl-dir, --config, or --cw")

    if args.config or args.cw:
        workload = args.config or args.cw
        build_dir = args.build_dir
        if build_dir is None:
            repo_root = find_repo_root()
            base = repo_root if repo_root is not None else Path.cwd()
            build_dir = base / ".build" / "viewer" / Path(workload).stem
        print(f"[wau-viewer] preparing ad-hoc circuit for {workload} in {build_dir}…",
              file=sys.stderr)
        prepared = prepare_circuit(
            build_dir=build_dir,
            config=args.config,
            cw_program=args.cw,
            base_config=args.base_config,
            flow_id=args.flow_id,
        )
        rtl_dir = prepared.rtl_dir
        program_path = prepared.program_path
        schedule_path = prepared.schedule_path
        print(f"[wau-viewer] generated RTL at {rtl_dir}", file=sys.stderr)
    else:
        if not args.program:
            parser.error("--program is required with --rtl-dir")
        rtl_dir = args.rtl_dir
        program_path = args.program
        schedule_path = args.schedule

    model = load_model(program_path, schedule_path)

    if args.stimulus:
        stim_list = _load_stimulus(args.stimulus)
    elif args.stress is not None:
        stim_list = derive_stress_stimulus(
            model, args.stress, seed=args.stress_seed,
            value_max=max(1, args.stress_range),
        )
        print(f"[wau-viewer] stress stimulus ({len(stim_list)} packets, "
              f"seed={args.stress_seed}): {stim_list}", file=sys.stderr)
    elif args.auto_stimulus:
        stim_list = derive_auto_stimulus(model)
    else:
        stim_list = derive_auto_stimulus(model)
        print(f"[wau-viewer] no --stimulus given, using auto-stimulus: {stim_list}",
              file=sys.stderr)

    flow_ids = [s[0] for s in stim_list]
    a_vals = [s[1] for s in stim_list]
    b_vals = [s[2] for s in stim_list]

    # a stress stream needs headroom: budget for serialized worst-case latency
    max_cycles = max(args.max_cycles, 400 * len(stim_list))

    runner = IverilogRunner(rtl_dir)
    print(f"[wau-viewer] running iverilog/vvp on {len(stim_list)} stimulus packets…",
          file=sys.stderr)
    sim = runner.run(flow_ids, a_vals, b_vals, max_cycles=max_cycles)
    print(f"[wau-viewer] sim complete, trace at {sim.trace_path}", file=sys.stderr)
    if args.dump_trace:
        args.dump_trace.parent.mkdir(parents=True, exist_ok=True)
        args.dump_trace.write_text(sim.trace_path.read_text())

    trace = parse_trace(sim.trace_path)
    print(f"[wau-viewer] parsed {len(trace.cycles)} trace cycles, "
          f"{trace.meta.outputs_seen} outputs", file=sys.stderr)

    # import Qt lazily so simulation can be run on machines without a GUI
    from PySide6.QtWidgets import QApplication
    from .main_window import ViewerWindow, run_headless_recording

    if args.record and args.headless:
        out_path = run_headless_recording(
            model, trace, args.record,
            framerate=args.framerate,
            frames_per_cycle=args.frames_per_cycle,
            cycle_ms=args.cycle_ms,
            max_cycles=args.record_max_cycles,
        )
        print(f"[wau-viewer] wrote {out_path}")
        return 0

    app = QApplication(sys.argv)
    win = ViewerWindow(model, trace, cycle_ms=args.cycle_ms)
    win.show()
    if args.record:
        # Live record while user interacts: start recorder immediately, play
        # through, then stop and save to the requested path on close.
        from .recorder import FrameRecorder
        win._recorder = FrameRecorder(framerate=args.framerate)
        win._recorder.start()
        win.btn_record.setText("■ Stop")
        win._play()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
