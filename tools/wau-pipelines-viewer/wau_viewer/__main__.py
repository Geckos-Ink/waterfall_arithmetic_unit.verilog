"""Entry point: orchestrate iverilog simulation and launch the Qt viewer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

from .model import derive_auto_stimulus, load_model
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
    parser.add_argument("--rtl-dir", type=Path, required=True,
                        help="directory with generated WAU RTL (e.g. src/verilog/generated)")
    parser.add_argument("--program", type=Path, required=True,
                        help="path to wau_program.json")
    parser.add_argument("--schedule", type=Path, default=None,
                        help="optional wau_schedule.json for Gantt overlay")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--stimulus", type=Path,
                   help="JSON list of {flow_id,a,b} to feed into the host input")
    g.add_argument("--auto-stimulus", action="store_true",
                   help="derive a deterministic stimulus that covers each flow once")
    parser.add_argument("--max-cycles", type=int, default=2000,
                        help="hard simulation budget (cycles)")
    parser.add_argument("--record", type=Path, default=None,
                        help="if set, write an MP4 of the whole trace (no live UI)")
    parser.add_argument("--framerate", type=int, default=8,
                        help="MP4 framerate when --record is used")
    parser.add_argument("--headless", action="store_true",
                        help="never show the GUI window (useful with --record)")
    parser.add_argument("--dump-trace", type=Path, default=None,
                        help="copy the raw iverilog trace.log here for inspection")
    args = parser.parse_args(argv)

    model = load_model(args.program, args.schedule)

    if args.stimulus:
        stim = _load_stimulus(args.stimulus)
    elif args.auto_stimulus:
        stim = derive_auto_stimulus(model)
    else:
        stim = derive_auto_stimulus(model)
        print(f"[wau-viewer] no --stimulus given, using auto-stimulus: {stim}", file=sys.stderr)

    flow_ids = [s[0] for s in stim]
    a_vals = [s[1] for s in stim]
    b_vals = [s[2] for s in stim]

    runner = IverilogRunner(args.rtl_dir)
    print(f"[wau-viewer] running iverilog/vvp on {len(stim)} stimulus packets…",
          file=sys.stderr)
    sim = runner.run(flow_ids, a_vals, b_vals, max_cycles=args.max_cycles)
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
        out = run_headless_recording(model, trace, args.record, framerate=args.framerate)
        print(f"[wau-viewer] wrote {out}")
        return 0

    app = QApplication(sys.argv)
    win = ViewerWindow(model, trace)
    win.show()
    if args.record:
        # Live record while user interacts: start recorder immediately, play through,
        # then stop and save to the requested path on close.
        from .recorder import FrameRecorder
        win._recorder = FrameRecorder(framerate=args.framerate)
        win._recorder.start()
        win.btn_record.setText("■ Stop")
        win._play()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
