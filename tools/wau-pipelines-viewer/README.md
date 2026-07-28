# wau-pipelines-viewer

Interactive, zoomable, step-by-step visualizer for a Waterfall Arithmetic Unit
(WAU) configuration executing a compiled program. The viewer is **iverilog-driven**:
the actual cycle-by-cycle behavior is produced by running the real generated
SystemVerilog through Icarus Verilog, not by re-implementing the model in
Python. Python only orchestrates the simulator, parses the per-cycle trace, and
renders/animates it.

What you get:
- Zoomable / pannable 2-D grid view of the WAU fabric (cores, capabilities,
  highway links, contract bus).
- **Ad-hoc circuit preparation**: point the viewer at a waugen config
  (`--config`) or directly at a `.cw` kernel (`--cw`) and it compiles the
  program, emits the RTL through the real `waugen` toolchain, and simulates
  that fresh circuit — no manual `generate` step.
- **Stress stimulus** (`--stress N`): a seeded random packet stream that
  interleaves flow ids so independent flows overlap in the multi-issue
  coordinator and the mesh-level concurrency becomes visible.
- **Phased per-cycle animation**: every simulated cycle plays as a slow-motion
  mini-scene — operand packets travel hop-by-hop from the coordinator along
  the same route the generated router uses (along its own line under the
  default per-line highways, the index-order chain under `chain`, or X-first
  dimension order under `matrix`), the applied operation flashes on the core
  (`mul(62, #3)`),
  then result packets travel back over the data highway with an elaboration
  "pop" where they land, and completed flow values drop out toward the host
  lane. Concurrent packets ride offset parallel lanes.
- **Highway scheme**: the grid is linked with the topology actually emitted,
  drawn orthogonally, and packets animate along the links *as drawn* — never
  on a path the fabric does not have. Under the default topology each row of
  cores has its **own** highway, drawn as the rail beneath it and ending in
  that line's coordinator `hub`; there is deliberately no spine joining them,
  because the lines do not touch. Each rail carries its own contracting bus
  with its own line-local slot numbering and its own marker, so you can see
  the rows arbitrating in parallel. (`chain` and `matrix` have a single
  highway, so their rails *are* joined by a spine.) Each core's slot sits
  directly beneath it, making every stub a plain vertical drop that crosses
  nothing. A stub answers *when does this core call its highway*: dim when
  quiet, dashed while it wants the highway, amber on the cycle it calls from
  its own offered slot, and solid red for as long as it holds that highway
  under a contract. Every one of those states is read from the RTL trace.
- A **concurrency HUD** (busy cores, packets in flight, peak parallel ops,
  mesh hops/stalls) plus a highway line (how many highways are open, or which
  core holds one with its contract mode and beats left, and grant/defer
  totals), so how efficiently a program uses the WAU fabric is readable at a
  glance — and captured in recordings.
- A Gantt-style timeline showing the operations actually dispatched per-core
  (from the RTL trace, not the offline schedule) with a live "playhead" at the
  current cycle.
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
- `--mnist-images <idx3(.gz)>` (+ `--mnist-count`, `--mnist-offset`): **real
  data** instead of an RNG. Operand pairs are streamed from consecutive MNIST
  pixels centered to `[-128, 127]`, the same values the DE0-Nano stress runner
  feeds through the live board with its own `--mnist-images`, so an animated
  run and a silicon run drive the fabric identically. Fetch the file first
  with `python3 scripts/fetch_dataset.py` (writes a git-ignored
  `datasets/mnist/`). Real pixels are spatially correlated, so the station
  cache behaves very differently from random operands — that difference is
  visible in the stats panel's hit ratio.

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
  timeline_view.py   # Gantt-style trace-derived operation strip with playhead
  stats_panel.py     # live performance + bottleneck readout
  main_window.py     # main app (transport controls + animation clock)
  recorder.py        # frame capture + MP4 (ffmpeg) / GIF (ffmpeg or Pillow)
examples/
  de0_nano_demo.stim.json
  wau_3x3_demo.json          # ad-hoc 3x3 demo circuit
  wau_mnist_demo_base.json   # 4x2 base used by the MNIST recording
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
  --framerate 10 --frames-per-cycle 6 --gif-width 2000 --window-size 3000x1900 --headless
```

(`frames_per_cycle / framerate` = seconds of video per simulated cycle; the
example plays each cycle over 0.6 s.) `--record-max-cycles N` trims long
traces, `--gif-width` caps the GIF resolution (default 1000 px), and
`--window-size WxH` shapes the off-screen window to the fabric — a tall grid
(2 columns × 4 rows) wastes most of a landscape frame, a wide one fills it.
MP4 output requires `ffmpeg`; GIF output uses ffmpeg's palette
pipeline when available and falls back to a pure-Pillow encoder otherwise.

The second demo GIF in the repository root — the same mesh-stress kernel
elaborating **real MNIST pixels**, recorded over one complete elaboration so
no flow animation is cut off mid-flight — is produced with:

```bash
python3 ../../scripts/fetch_dataset.py   # once

python3 -m wau_viewer \
  --cw ../../CWs/stress/mesh_stress.cw \
  --base-config examples/wau_mnist_demo_base.json \
  --mnist-images ../../datasets/mnist/t10k-images-idx3-ubyte.gz \
  --mnist-count 4 --mnist-offset 5888 \
  --record examples/wau_mnist_mesh_stress.gif \
  --framerate 10 --frames-per-cycle 3 --gif-width 2400 --window-size 3000x1900 \
  --record-max-cycles 198 --headless
```

`examples/wau_mnist_demo_base.json` is the demo's own base config: the
`wau_cw_fit_base` DE0-Nano preset laid out as a 4x2 grid (two independent
highways of four cores, which reads better in a landscape recording than the
2x4 board grid) with every core given the full op set. `198` is where the
first elaboration's result reaches the host, so the recording ends on a
completed flow rather than in the middle of one; the untrimmed trace runs
769 cycles for the four packets.

The third demo GIF in the repository root — the per-core fast-path table
lighting up amber "core → core" hops — is produced from the tracked
`compiler.station_program`-enabled demo config with an auto-derived stimulus:

```bash
python3 -m wau_viewer \
  --config ../../src/python/configs/wau_station_program_demo.json --auto-stimulus \
  --record examples/wau_fast_path_demo.gif \
  --framerate 10 --frames-per-cycle 6 --gif-width 2000 --window-size 3000x1900 --headless
```

The Gantt strip is built from the RTL trace itself (each core's dispatch and
result events), not the *offline* `wau_schedule.json` estimate: for this
kernel the offline schedule's makespan is only 44 cycles, while the real RTL
takes 198 cycles to retire the same flow, so drawing the plan would leave the
strip empty for most of the run. Blocks track the cycle a core actually
dispatched an op and the cycle its result actually came back, so they stay in
sync with the playhead for the full 769-cycle trace.

In headless mode the GUI is never shown — frames are rendered off-screen by
`QGraphicsView.grab()`.
