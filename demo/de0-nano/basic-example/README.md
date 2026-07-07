# WAU on the DE0-Nano — basic-example

End-to-end demo of the Waterfall Arithmetic Unit running on real silicon
(Terasic DE0-Nano, Intel Cyclone IV E EP4CE22F17C6). The host PC drives the
WAU over USB-Blaster + Altera virtual JTAG: programs are loaded by Quartus,
operands are pushed via vJTAG, and results stream back the same way.

```
┌──────────────────┐     USB-Blaster    ┌─────────────────────────────┐
│  Python host     │ ◀────────────────▶ │  DE0-Nano (Cyclone IV E)    │
│                  │       JTAG         │                             │
│  waujtag.WAU     │                    │  vJTAG ── wau_vjtag_bridge  │
│   └ MMIO         │     TCP 2540       │           └ wau_host_mmio   │
│      └ TCLClient ├────────────────────┤              └ wau_top      │
│                  │                    │                  (2x2 grid) │
│  quartus_stp     │   TCL server       │                             │
└──────────────────┘                    └─────────────────────────────┘
```

Everything that is not DE0-Nano-specific lives in reusable libraries you can
drop into other Altera designs:

* `quartus/rtl/vJTAG.v` — `sld_virtual_jtag` wrapper (IR width 4)
* `quartus/rtl/wau_vjtag_bridge.v` — generic JTAG↔MMIO master
* `host/tcl/wau_jtag_server.tcl` — generic TCL line-protocol server
* `host/waujtag/` — generic Python host library (`TCLClient`, `MMIO`, `WAU`, `Bench`)

The DE0-Nano-specific pieces are:

* `quartus/rtl/wau_jtag_top.v` — pin-level board top
* `quartus/wau_de0_nano_basic.{qpf,qsf,sdc}` — Quartus project + pinout

---

## Layout

```
demo/de0-nano/basic-example/
├── README.md                          ← you are here
├── Makefile                           POSIX-friendly entrypoints
├── quartus/
│   ├── wau_de0_nano_basic.qpf
│   ├── wau_de0_nano_basic.qsf         DE0-Nano pin assignments + sources
│   ├── wau_de0_nano_basic.sdc         50 MHz CLOCK_50 + TCK CDC false-paths
│   ├── rtl/
│   │   ├── wau_jtag_top.v             board top
│   │   ├── wau_vjtag_bridge.v         vJTAG ↔ MMIO bridge (REUSABLE)
│   │   └── vJTAG.v                    sld_virtual_jtag wrapper (REUSABLE)
│   └── wau_rtl/                       populated by scripts/build.ps1
├── host/
│   ├── waujtag/                       Python library (REUSABLE)
│   │   ├── client.py                  TCP line-protocol client
│   │   ├── mmio.py                    generic 32b MMIO master
│   │   ├── wau.py                     wau_host_mmio register map
│   │   └── benchmark.py               benchmark harness
│   ├── tcl/
│   │   └── wau_jtag_server.tcl        quartus_stp TCL server (REUSABLE)
│   ├── programs/
│   │   ├── run_benchmark.py           randomized arithmetic benchmark driver
│   │   ├── run_iris_stats_benchmark.py real-data Iris benchmark driver
│   │   └── run_cw_stress_benchmark.py live CW stress driver (default CWs/stress/mesh_stress.cw)
│   ├── data/
│   │   └── iris_sepal_petal_tenths.csv tracked real dataset copy (scaled)
│   └── config/
│       ├── wau_de0_nano_basic.json    WAU config (2x2 grid, 4 ops, 4 flows)
│       └── wau_de0_nano_cw_stress_base.json board base config for CW stress kernels
├── scripts/
│   ├── build.ps1                      generate RTL + Quartus full compile
│   ├── build_cw_stress.ps1            build CW stress board image (default CWs/stress/mesh_stress.cw)
│   ├── program.ps1                    quartus_pgm download
│   ├── server.ps1                     quartus_stp TCL server
│   ├── run.ps1                        randomized arithmetic benchmark
│   ├── run_iris_stats.ps1             real-data Iris benchmark
│   └── run_cw_stress.ps1              live CW stress benchmark
└── build/                             gitignored: generated/, *.json reports
```

---

## Prerequisites

* Quartus Standard 25.1 at `C:\altera_standard\25.1std`
  (pass `-QuartusRoot` if your install lives elsewhere; the bundled
  `quartus_sh`, `quartus_pgm`, and `quartus_stp` are all used)
* If the local 25.1 Standard install cannot compile because its evaluation has
  expired, use `-QuartusRoot C:\intelFPGA_lite\23.1std` for build/program/server.
  The tracked 2026-07-06 Iris board report in this repo was produced that way.
* DE0-Nano connected via USB (USB-Blaster driver visible in Device Manager)
* Python 3.10+ on PATH

Reset the board with KEY[0] (active-low). The other LEDs come up at boot:

| LED  | Meaning                                          |
|------|--------------------------------------------------|
| 0    | `host_out_valid` — result available this cycle   |
| 1    | `host_in_ready` — pipeline accepts an input      |
| 2    | `output_pending` (sticky until result is drained)|
| 3    | `enable_auto_adapt` (CTRL[1])                    |
| 4    | `jtag_busy` (non-bypass IR currently selected)   |
| 5    | `mmio_readdatavalid` (flickers per MMIO read)    |
| 6    | ~3 Hz heartbeat                                  |
| 7    | low bit of `obs_total_hop_count` (traffic LED)   |

If LED[6] is blinking and LED[1] is solid, the design is alive and ready.

---

## Quickstart (PowerShell)

```powershell
# 1. Generate RTL + compile the bitstream (~ 5–10 min on first run).
.\scripts\build.ps1

# 2. Program the FPGA over USB-Blaster (volatile RAM-only load).
.\scripts\program.ps1

# 3. In a SECOND PowerShell, start the TCL vJTAG MMIO server.
.\scripts\server.ps1

# 4. In a THIRD PowerShell, run the arithmetic smoke benchmark.
.\scripts\run.ps1 -Iters 256

# 5. Or run the real-data statistical benchmark instead.
.\scripts\run_iris_stats.ps1

# 6. Or build and run the heavier CW mesh-stress board image.
.\scripts\build_cw_stress.ps1 -GridX 2 -GridY 2 -QuartusRoot C:\intelFPGA_lite\23.1std
.\scripts\program.ps1 -QuartusRoot C:\intelFPGA_lite\23.1std
# server still running
.\scripts\run_cw_stress.ps1 -Config .\build\cw_stress_2x2_merged.json -RandomIters 1024 -RandomRange 1023 -Seed 1592594996
```

POSIX equivalent (Git Bash / WSL / MSYS):

```bash
make build
make program
make server         # in its own terminal
make run            # in another terminal
make run-iris       # real-data Iris workload
make build-cw-stress QUARTUS_ROOT=C:/intelFPGA_lite/23.1std
make run-cw-stress
```

Validated end-to-end output on a real DE0-Nano (Quartus 25.1std, USB-Blaster,
50 MHz CLOCK_50, fit utilisation 37% LE / 18% multipliers):

```
PING ok. obs_aux=0x0C98CAFE  (magic should be 0xCAFE in low 16b)
soft-resetting WAU via bridge IR_RESET ...
STATUS=0x00000001  host_in_ready=True
obs baseline: hops=0 stalls=0 fwd=0 deliv=0 cache=0/0

========================================================================
case                              n      pass    thr(ops/s)   p50(ms)   p95(ms)
------------------------------------------------------------------------
flow1_accumulate_and_scale      265   265/265          85.2     15.00     16.00
flow2_max_then_scale            265   265/265          90.7     15.00     16.00
flow3_fma_a_b_plus_b            265   265/265          92.7     15.00     16.00
========================================================================

observability delta after all cases:
  hops    = 6890        ← real router traffic
  stalls  = 0           ← no backpressure events
  fwd     = 2650        ← packets forwarded between neighbours
  deliv   = 4240        ← packets exiting the mesh locally
  cache_h = 93/2120     ← 4.4% station-cache hit rate (random inputs)
```

Reports are written to `build/benchmark_<timestamp>.json`.

Real-data Iris benchmark reports are written to
`build/iris_benchmark_<timestamp>.json`.

Live CW stress reports are written to
`build/cw_stress_benchmark_<timestamp>.json`.

Per-trigger wall-clock latency (~15 ms p50) is dominated by USB-Blaster JTAG
round-trip — the WAU itself completes each 2- or 3-stage flow in well under
20 cycles at 50 MHz. The observability counters confirm the data plane really
does traverse the mesh: each trigger averages ~26 hops and ~16 packets
delivered, and the station cache picks up the small fraction of repeat operand
pairs that the random+corner input mix produces.

---

## What gets executed

The default WAU config (`host/config/wau_de0_nano_basic.json`) defines:

* a **2×2 core grid** (chosen so `dst_core % GRID_X` collapses to a bit-select
  in `wau_highway_router.v` — anything else infers a full divider per router
  port and blows past the 22 320 LE budget on EP4CE22)
* int32 datatype, 4 ops (add / sub / mul / max)
* per-core capability constraints:

  | core | x,y | ops             |
  |------|-----|-----------------|
  | 0    | 0,0 | add, sub, max   |
  | 1    | 1,0 | mul             |
  | 2    | 0,1 | add, sub        |
  | 3    | 1,1 | max             |

* **flow 1 — `accumulate_and_scale`** (3 stages):
  `y = ((a + b) × 3) − b`   (add → mul-by-3 → sub)
* **flow 2 — `max_then_scale`** (3 stages):
  `y = (max(a, b) − b) × 2`  (max → sub → mul-by-2)
* **flow 3 — `fma_a_b_plus_b`** (2 stages):
  `y = a × b + b`            (mul → add)
* **flow 4 — `iris_morphology_score`** (10 stages):
  `score(a, b) = max((((max((((a - 58) * 4 + b - 44) * 3 + 32), 0)) * 2) - 80), 0)`
  where `a` is sepal length and `b` is petal length, both scaled to tenths of
  a centimeter

Why no `div`? The bundled `wau_operation_alu.v` emits a purely combinational
signed divide whose 32-bit settling time exceeds one 50 MHz period on
Cyclone IV E, and `wau_core_station.v` latches the ALU output on the very
first cycle after dispatch — so divide results are captured before the
divider has settled and come back as garbage on silicon. The other four ops
are 1- or 3-cycle bounded and are reliable. Re-enabling division is a
station/divider rework upstream of this demo.

`run.ps1` feeds 265 randomized + corner-case `(a, b)` pairs into flows 1-3
over JTAG, polls `wau_host_mmio` for results, validates each result against
the software reference, and reports throughput / latency percentiles plus the
**delta** of all observability counters (hops, stalls, forwards,
local-delivered, cache hit-rate).

`run_iris_stats.ps1` instead streams the tracked dataset
`host/data/iris_sepal_petal_tenths.csv` through flow 4, computes a host-side
reference with the same staged fixed-point chain, and emits per-label score
distributions plus the same observability deltas. The tracked board reference
for that workload is
[`../../../benchmarks/de0_nano_iris_stats_benchmark.txt`](../../../benchmarks/de0_nano_iris_stats_benchmark.txt).

### Optional: also compile the `.cw` kernel

```powershell
.\scripts\build.ps1 -WithCw
.\scripts\program.ps1
# server still running
.\scripts\run.ps1 -IncludeCw
```

This invokes `python -m waugen compile-cw` against `CWs/basic_arithmetic.cw`
(a minimal Conv2D+bias+residual+ReLU kernel sized for EP4CE22) to merge a
new flow_id=90 into the config before regenerating the RTL. The benchmark
exercises it as a smoke-test (no per-pair scoreboard, since the reference
is the existing `waugen.cw_reference`).

### Optional: run a heavier CW stress kernel on the board

```powershell
.\scripts\build_cw_stress.ps1 -GridX 2 -GridY 2 -QuartusRoot C:\intelFPGA_lite\23.1std
.\scripts\program.ps1 -QuartusRoot C:\intelFPGA_lite\23.1std
# server still running
.\scripts\run_cw_stress.ps1 -Config .\build\cw_stress_2x2_merged.json -RandomIters 1024 -RandomRange 1023 -Seed 1592594996
```

This path lowers a heavier CW kernel instead of the tiny `basic_arithmetic.cw`
demo. `build_cw_stress.ps1` defaults to the ad-hoc mesh-stress kernel
`CWs/stress/mesh_stress.cw` (tuned to saturate the mesh on small grids); pass
`-ProgramFile ..\..\..\CWs\example-program.cw` to build the original Conv2D
reference kernel instead. The generated board flow keeps the tuned CW knobs
(`lane_parallelism=2`, `max_in_flight=2`, `program_replicas=2`,
`placement=balance`, `lowering_profile=throughput_optimized`) and validates
every board result against `waugen.cw_reference`.

The 2026-07-06 tracked board result (measured with `CWs/example-program.cw`)
is:

* `2x2` grid: `1032/1032` pass (`8` golden + `1024` seeded random), `88.3` ops/s,
  `15/16 ms` p50/p95 latency, `72,240` mesh hops total
* `2x4` grid: fits at `22,166 / 22,320` logic elements (99%) but times out all
  `8/8` golden cases on hardware
* `4x4` and `2x5`: fail fit on EP4CE22

The tracked report for that exploration is
[`../../../benchmarks/de0_nano_cw_stress_benchmark.txt`](../../../benchmarks/de0_nano_cw_stress_benchmark.txt).

---

## Protocol reference

### JTAG IR command set (4-bit IR, see [wau_vjtag_bridge.v](quartus/rtl/wau_vjtag_bridge.v))

| IR  | Name        | DR width | Direction | Meaning                                |
|-----|-------------|----------|-----------|----------------------------------------|
| 0x0 | BYPASS      | 1        | —         | shift-through                          |
| 0x1 | WRITE       | 40       | TDI→FPGA  | `{addr[7:0], data[31:0]}`, UDR→write   |
| 0x2 | READ_ADDR   | 8        | TDI→FPGA  | `addr[7:0]`, UDR→read                  |
| 0x3 | READ_DATA   | 32       | FPGA→TDO  | latched read result                    |
| 0x4 | OBS         | 32       | FPGA→TDO  | static aux observability word          |
| 0xF | RESET       | 1        | —         | UDR pulses one-cycle soft reset        |

### MMIO register map (see [wau_host_mmio.v](quartus/wau_rtl/wau_host_mmio.v))

| Addr | Name      | Access | Meaning                                                  |
|-----:|-----------|:------:|----------------------------------------------------------|
| 0x00 | CTRL      | RW     | `[0]` soft_reset, `[1]` enable_auto_adapt                |
| 0x01 | STATUS    | R      | `[0]` host_in_ready, `[1]` host_out_valid, `[2]` pending |
| 0x02 | FLOW_ID   | RW     | flow id for next TRIGGER                                 |
| 0x03 | IN_A      | RW     | operand A latched into coordinator on TRIGGER            |
| 0x04 | IN_B      | RW     | operand B latched into coordinator on TRIGGER            |
| 0x05 | TRIGGER   | W1S    | any write raises `host_in_valid`                         |
| 0x10 | OUT_FLOW  | R      | last completed flow id (clears `output_pending`)         |
| 0x11 | OUT_VAL   | R      | last completed value (clears `output_pending`)           |
| 0x12 | HOPS      | R      | total router hop counter                                 |
| 0x13 | STALLS    | R      | total router stall counter                               |
| 0x14 | FORWARDS  | R      | total packets forwarded between neighbors                |
| 0x15 | DELIVRD   | R      | total packets locally delivered                          |
| 0x16 | CACHE_H   | R      | total station-cache hits                                 |
| 0x17 | CACHE_L   | R      | total station-cache lookups                              |

### TCP line protocol (see [wau_jtag_server.tcl](host/tcl/wau_jtag_server.tcl))

```
> W <addr> <data>     < OK
> R <addr>            < D <value>
> OBS                 < D <value>
> RST                 < OK
> PING                < PONG
> QUIT                < BYE
```

All numbers are unsigned decimal. The server is single-threaded and serializes
JTAG access — multiple python clients can connect, but each command is atomic.

---

## Using the libraries in your own design

### Python

```python
from waujtag import TCLClient, MMIO, WAU

with TCLClient("localhost", 2540) as c:
    wau = WAU(MMIO(c))
    result = wau.execute(flow_id=1, a=10, b=20)
    print(result.value, result.latency_s)
    obs = wau.observability()
    print("cache hit rate:", obs.hit_rate)
```

### Quartus RTL

Drop `vJTAG.v` and `wau_vjtag_bridge.v` into any Altera project, instantiate
the bridge, and wire its `mmio_*` ports to your own Avalon-MM-style register
file. The TCL server + Python library will then drive it untouched.

---

## Practical limits on EP4CE22

The Cyclone IV E EP4CE22F17C6 has ~22k LEs, 594 Kb of M9K block memory, and
132 multiplier blocks. The current 3×2 WAU plus the vJTAG bridge fits with
plenty of headroom; the 32 MB external SDRAM (ISSI IS42S16160G) is NOT used
by this design — it is pinned to safe levels in the QSF to keep the Fitter
happy. Reusing those pins for a memory controller is the natural next step
once the WAU outgrows on-chip BRAM.

vJTAG round-trip latency is dominated by USB-Blaster traffic (~15–30 ms per
`execute()` call), so per-trigger throughput tops out near 30–60 ops/s. The
WAU itself completes a 3-stage flow in well under 20 cycles at 50 MHz; this
is a *control* benchmark, not a peak compute one. To push real throughput
you'd front-end the WAU with a host-side burst loader and stream operands
without round-tripping per result — the MMIO already supports that pattern.
