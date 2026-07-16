# wau-pipelines-viewer

Interactive, zoomable, step-by-step visualizer for a Waterfall Arithmetic Unit
(WAU) configuration executing a compiled program. The viewer is **iverilog-driven**:
the actual cycle-by-cycle behavior is produced by running the real generated
SystemVerilog through Icarus Verilog, not by re-implementing the model in
Python. Python only orchestrates the simulator, parses the per-cycle trace, and
renders/animates it.

What you get:
- Zoomable / pannable 2-D grid view of the WAU mesh (cores, capabilities,
  highway links).
- **Ad-hoc circuit preparation**: point the viewer at a waugen config
  (`--config`) or directly at a `.cw` kernel (`--cw`) and it compiles the
  program, emits the RTL through the real `waugen` toolchain, and simulates
  that fresh circuit — no manual `generate` step.
- **Stress stimulus** (`--stress N`): a seeded random packet stream that
  interleaves flow ids so independent flows overlap in the multi-issue
  coordinator and the mesh-level concurrency becomes visible.
- **Phased per-cycle animation**: every simulated cycle plays as a slow-motion
  mini-scene — operand packets travel hop-by-hop from the coordinator along
  the same dimension-order (X-first) route the generated router uses, the
  applied operation flashes on the core (`mul(62, #3)`), then result packets
  travel back over the data mesh with an elaboration "pop" where they land,
  and completed flow values drop out toward the host lane. Concurrent packets
  ride offset parallel lanes.
- A **concurrency HUD** (busy cores, packets in flight, peak parallel ops,
  mesh hops/stalls) so how efficiently a program uses the WAU fabric is
  readable at a glance — and captured in recordings.
- A Gantt-style schedule timeline showing program instructions per-core with a
  live "playhead" at the current cycle.
- A performance / bottleneck panel (hops, stalls, forwards, local deliveries,
  station cache hit ratio, busy time per core, dispatch back-pressure events).
- Transport controls: play / pause / single-step / reverse-step / a
  seconds-per-cycle slider (0.2 s – 4 s, so slowing down stretches the packet
  animation instead of freezing frames) / scrub bar.
- Demo export: capture the animation to an MP4 (via `ffmpeg`) or an animated
  GIF (via `ffmpeg` when available, else pure-Pillow fallback).

## Why PySide6?

The viewer uses [PySide6](https://doc.qt.io/qtforpython-6/) (the official Qt
for Python LGPL binding). Reasons it was picked over alternatives:

- `QGraphicsView` / `QGraphicsScene` is the most mature portable framework for
  interactive zoomable 2-D scenes — built-in transform, item picking, hit
  testing, animations, off-screen rendering for frame capture.
- Runs identically on macOS, Linux and Windows.
- Frame grabbing for video export is one call (`QGraphicsView.grab()`).
- Permissive LGPL — embeddable in non-commercial projects per the repo's
  PolyForm license.

If you only have PyQt5 installed instead, swap the `PySide6` import with
`PyQt5` — the Qt API used here is portable across both bindings.

## Install

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need:
- `iverilog` (Icarus Verilog) on your `PATH` — the viewer shells out to it.
- `ffmpeg` on your `PATH` (only for MP4 export; GIF export falls back to
  Pillow when ffmpeg is missing).

## Quickstart

The one-command path — from this directory, hand the viewer a waugen config
and let it prepare the ad-hoc circuit itself (compile → generate → simulate →
animate):

```bash
python3 -m wau_viewer --config examples/wau_3x3_demo.json --stress 6
```

or compile a `.cw` kernel straight onto the demo-independent base config:

```bash
python3 -m wau_viewer --cw ../../CWs/example-program.cw --stress 8
```

Generated artifacts land in `<repo>/.build/viewer/<name>/` (override with
`--build-dir`); the real `waugen compile-cw` / `waugen generate` chain is
invoked under the hood, so the simulated circuit is exactly what you would
synthesize.

Alternatively, point it at pre-generated artifacts:

```bash
python3 -m wau_viewer \
  --rtl-dir src/verilog/generated \
  --program src/verilog/generated/wau_program.json \
  --schedule src/verilog/generated/wau_schedule.json \
  --stimulus tools/wau-pipelines-viewer/examples/de0_nano_demo.stim.json
```

Stimulus options:
- `--stimulus file.json`: a JSON list of `{flow_id, a, b}` packets fed into
  the host input port — see `examples/`.
- `--auto-stimulus`: one deterministic packet per flow.
- `--stress N` (+ `--stress-seed`, `--stress-range`): N seeded random packets
  with round-robin-interleaved flow ids — consecutive packets always target
  different flows, which is what lets the multi-issue coordinator keep several
  flows in flight and makes the concurrency visible on the mesh.

## What happens under the hood

On launch the viewer:

0. (with `--config`/`--cw`) invokes `waugen compile-cw`/`waugen generate` as
   subprocesses to build the ad-hoc circuit into `--build-dir` first.
1. Reads the WAU config from `wau_program.json` (grid size, cores,
   capabilities, flow DAGs).
2. Writes a generated testbench `tb_wau_viewer.v` that:
   - Instantiates `wau_top` with the same parameters as the test fixtures.
   - Reads the stimulus from `stimulus_*.hex` files (one column per field).
   - Streams events to a `trace.log` text file via `$fwrite`, one block per
     simulation cycle.
3. Invokes `iverilog -g2005-sv …` + `vvp` to actually simulate the RTL.
4. Parses `trace.log` into an in-memory list of per-cycle events.
5. Hands the trace + static layout to the Qt UI, which lets you scrub /
   step / play / record.

The same `iverilog` toolchain that the CI uses produces the simulation data —
no parallel simulator implementation is maintained in Python.

## Layout

```
wau_viewer/
  __main__.py        # CLI entry point
  prepare.py         # ad-hoc circuit prep: config/.cw -> waugen -> fresh RTL
  simulator.py       # iverilog/vvp orchestration + RTL discovery + workdirs
  tb_generator.py    # generates the per-config tb_wau_viewer.v
  trace_parser.py    # parses the iverilog $fwrite trace into typed events
  model.py           # in-memory model + stress stimulus + mesh route helper
  graph_view.py      # zoomable scene; phased hop-by-hop packet animation + HUD
  timeline_view.py   # Gantt-style schedule strip with playhead
  stats_panel.py     # live performance + bottleneck readout
  main_window.py     # main app (transport controls + animation clock)
  recorder.py        # frame capture + MP4 (ffmpeg) / GIF (ffmpeg or Pillow)
examples/
  de0_nano_demo.stim.json
```

## Recording a demo video / GIF

Click the **Record** button. The viewer captures every animation frame (at
the ~30 fps animation clock) into a temp dir and, when you click Stop, encodes
an MP4 or GIF depending on the extension you choose.

Headless recording renders `--frames-per-cycle` deterministic sub-frames of
the phased packet animation per simulated cycle, so the exported file plays
the exact same data movement the interactive viewer shows. The demo GIF at the
repository root is produced this way:

```bash
python3 -m wau_viewer \
  --config examples/wau_3x3_demo.json --stress 6 \
  --record examples/wau_3x3_demo.gif \
  --framerate 10 --frames-per-cycle 6 --gif-width 1500 --headless
```

(`frames_per_cycle / framerate` = seconds of video per simulated cycle; the
example plays each cycle over 0.6 s.) `--record-max-cycles N` trims long
traces, `--gif-width` caps the GIF resolution (default 1000 px). MP4 output requires `ffmpeg`; GIF output uses ffmpeg's palette
pipeline when available and falls back to a pure-Pillow encoder otherwise.

In headless mode the GUI is never shown — frames are rendered off-screen by
`QGraphicsView.grab()`.
