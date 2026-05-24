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
│                  │                    │                  (3x2 grid) │
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
│   │   ├── basic_arithmetic.cw        compact compile-cw kernel for DE0-Nano
│   │   └── run_benchmark.py           benchmark driver
│   └── config/
│       └── wau_de0_nano_basic.json    WAU config (3x2 grid, 5 ops, 2 flows)
├── scripts/
│   ├── build.ps1                      generate RTL + Quartus full compile
│   ├── program.ps1                    quartus_pgm download
│   ├── server.ps1                     quartus_stp TCL server
│   └── run.ps1                        python benchmark
└── build/                             gitignored: generated/, *.json reports
```

---

## Prerequisites

* Quartus Standard 25.1 at `C:\altera_standard\25.1std`
  (the bundled `quartus_sh`, `quartus_pgm`, `quartus_stp` are all used)
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

# 4. In a THIRD PowerShell, run the host benchmark.
.\scripts\run.ps1 -Iters 256
```

POSIX equivalent (Git Bash / WSL / MSYS):

```bash
make build
make program
make server         # in its own terminal
make run            # in another terminal
```

You should see something like:

```
PING ok. obs_aux=0x0XYZCAFE  (magic should be 0xCAFE in low 16b)
========================================================================
case                              n     pass    thr(ops/s)   p50(ms)   p95(ms)
------------------------------------------------------------------------
flow1_accumulate_and_scale      265  265/265           42.1     22.30     31.50
flow2_max_then_div              265  265/265           39.8     24.10     33.20
========================================================================

observability delta after all cases:
  hops    = 2384
  stalls  = 17
  fwd     = 1192
  deliv   = 1060
  cache_h = 318
  cache_l = 530
  hit_rate= 0.600
```

Reports are written to `build/benchmark_<timestamp>.json`.

---

## What gets executed

The default WAU config (`host/config/wau_de0_nano_basic.json`) defines:

* a 3×2 core grid, int32 datatype, 5 ops (add/sub/mul/div/max)
* per-core capability constraints matching the DE0-Nano demo preset
* **flow 1 — `accumulate_and_scale`**:
  `y = ((a + b) × 3) − b`   (add → mul-by-3 → sub)
* **flow 2 — `max_then_div`**:
  `y = max(a, b) / b`        (max → div)

The Python benchmark feeds 256 randomized + corner-case `(a, b)` pairs into
each flow over JTAG, polls `wau_host_mmio` for results, validates each result
against the software reference, and reports throughput / latency percentiles
plus the **delta** of all observability counters (hops, stalls, forwards,
local-delivered, cache hit-rate).

### Optional: also compile the `.cw` kernel

```powershell
.\scripts\build.ps1 -WithCw
.\scripts\program.ps1
# server still running
.\scripts\run.ps1 -IncludeCw
```

This invokes `python -m waugen compile-cw` against `basic_arithmetic.cw`
(a minimal Conv2D+bias+residual+ReLU kernel sized for EP4CE22) to merge a
new flow_id=90 into the config before regenerating the RTL. The benchmark
exercises it as a smoke-test (no per-pair scoreboard, since the reference
is the existing `waugen.cw_reference`).

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
