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
- Animated dispatch & result packets traveling on the control- and data-plane
  meshes.
- A Gantt-style schedule timeline showing program instructions per-core with a
  live "playhead" at the current cycle.
- A performance / bottleneck panel (hops, stalls, forwards, local deliveries,
  station cache hit ratio, busy time per core, dispatch back-pressure events).
- Transport controls: play / pause / single-step / reverse-step / speed slider
  / scrub bar.
- Demo-video export: capture the running animation to an MP4 via `ffmpeg`.

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
- `ffmpeg` on your `PATH` (only if you want to export an MP4).

## Quickstart

From the repository root, regenerate the RTL + program JSON for the
configuration you want to visualize (or skip if `src/verilog/generated/`
already matches the config you care about):

```bash
PYTHONPATH=src/python python3 -m waugen generate \
  --config src/python/configs/wau_de0_nano_demo.json \
  --out src/verilog/generated --summary
```

Then launch the viewer with the same generated artifacts:

```bash
python3 -m wau_viewer \
  --rtl-dir src/verilog/generated \
  --program src/verilog/generated/wau_program.json \
  --schedule src/verilog/generated/wau_schedule.json \
  --stimulus tools/wau-pipelines-viewer/examples/de0_nano_demo.stim.json
```

A stimulus file is a JSON list of `{flow_id, a, b}` packets fed into the host
input port — see `examples/`. Or pass `--auto-stimulus` to derive one from the
test fixtures.

## What happens under the hood

On launch the viewer:

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
  simulator.py       # iverilog/vvp orchestration + workdir management
  tb_generator.py    # generates the per-config tb_wau_viewer.v
  trace_parser.py    # parses the iverilog $fwrite trace into typed events
  model.py           # in-memory model assembled from program + schedule + trace
  graph_view.py      # zoomable QGraphicsView/Scene of the WAU mesh
  timeline_view.py   # Gantt-style schedule strip with playhead
  stats_panel.py     # live performance + bottleneck readout
  main_window.py     # main app (transport controls + dock layout)
  recorder.py        # frame capture + ffmpeg MP4 muxing
examples/
  de0_nano_demo.stim.json
```

## Recording a demo video

Click the **Record** button. The viewer writes one PNG per simulated cycle
into a temp dir and, when you click Stop, invokes
`ffmpeg -framerate N -i %06d.png … out.mp4`. The exact framerate is set by
the speed slider at the moment recording starts.

You can also do it headless:

```bash
python3 -m wau_viewer \
  --rtl-dir src/verilog/generated \
  --program src/verilog/generated/wau_program.json \
  --schedule src/verilog/generated/wau_schedule.json \
  --stimulus tools/wau-pipelines-viewer/examples/de0_nano_demo.stim.json \
  --record out.mp4 --framerate 8 --headless
```

In headless mode the GUI is never shown — frames are rendered off-screen by
`QGraphicsView.grab()` and piped straight to `ffmpeg`.
