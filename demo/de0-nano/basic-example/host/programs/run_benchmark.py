#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""End-to-end WAU-on-DE0-Nano benchmark over vJTAG.

Prereqs:
    1. Quartus has built and programmed wau_de0_nano_basic.sof onto the board.
    2. quartus_stp -t host/tcl/wau_jtag_server.tcl is running and printed
       "vJTAG MMIO server listening on TCP 2540".

Then run from the demo root:

    PYTHONPATH=host python host/programs/run_benchmark.py \
        --iters 256 \
        --report build/benchmark.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

# Allow running both as a module and a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waujtag import Bench, MMIO, TCLClient, WAU  # noqa: E402


# --- Software reference models for each flow defined in wau_de0_nano_basic.json
def flow1_accumulate_and_scale(a: int, b: int) -> int:
    # Stage 0 add(a, b)            -> a+b
    # Stage 1 mul(*, 3) immediate  -> (a+b)*3
    # Stage 2 sub(*, b)            -> (a+b)*3 - b
    return _to_int32(((_to_int32(a + b)) * 3) - b)


def flow2_max_then_scale(a: int, b: int) -> int:
    # Stage 0 max(a, b)            -> max(a,b)
    # Stage 1 sub(*, b)            -> max(a,b) - b   (always >= 0)
    # Stage 2 mul(*, 2) immediate  -> (max(a,b)-b) * 2
    return _to_int32((_to_int32(max(a, b) - b)) * 2)


def flow3_fma_a_b_plus_b(a: int, b: int) -> int:
    # Stage 0 mul(a, b)            -> a*b
    # Stage 1 add(*, b)            -> a*b + b
    return _to_int32(_to_int32(a * b) + b)


def _to_int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & (1 << 31) else x


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WAU vJTAG benchmark")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=2540)
    p.add_argument("--iters", type=int, default=256, help="Inputs per flow case")
    p.add_argument("--seed", type=int, default=0xC0FFEE)
    p.add_argument(
        "--timeout-s",
        type=float,
        default=2.0,
        help="Per-trigger result timeout (default 2 s)",
    )
    p.add_argument("--report", type=Path, default=None, help="Write JSON summary here")
    p.add_argument(
        "--skip-soft-reset",
        action="store_true",
        help="Skip CTRL[0] / IR_RESET pulse on connect",
    )
    p.add_argument(
        "--include-cw-flow",
        action="store_true",
        help=(
            "If a compile-cw merged flow is present (flow_id=90 by default), "
            "drive it as a smoke-test using small inputs. The CW kernel is not "
            "value-checked here because its reference is the existing waugen.cw_reference."
        ),
    )
    p.add_argument("--cw-flow-id", type=int, default=90)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(args.seed)

    print(f"connecting to {args.host}:{args.port} ...", flush=True)
    client = TCLClient(host=args.host, port=args.port, timeout=10.0)
    client.connect()
    print("connected. server greeted READY", flush=True)

    mmio = MMIO(client)
    wau = WAU(mmio)

    # --- sanity: PING + observability aux word
    if not client.ping():
        print("PING failed", file=sys.stderr); return 2
    aux = client.obs_aux()
    print(f"PING ok. obs_aux=0x{aux:08X}  (magic should be 0xCAFE in low 16b)")
    if (aux & 0xFFFF) != 0xCAFE:
        print(
            "WARNING: obs_aux magic word mismatch — bridge might not be the expected build",
            file=sys.stderr,
        )

    if not args.skip_soft_reset:
        print("soft-resetting WAU via bridge IR_RESET ...")
        wau.soft_reset(via_bridge=True)
        time.sleep(0.05)

    status = wau.status()
    print(f"STATUS=0x{status:08X}  host_in_ready={(status & 1) != 0}")
    obs0 = wau.observability()
    print(
        f"obs baseline: hops={obs0.hops} stalls={obs0.stalls} "
        f"fwd={obs0.forwards} deliv={obs0.local_delivered} "
        f"cache={obs0.cache_hits}/{obs0.cache_lookups}"
    )

    # --- generate input pairs (deterministic, seeded)
    int32_max = (1 << 30) - 1  # Keep operands away from overflow boundary
    pairs_random = [
        (random.randint(-int32_max, int32_max), random.randint(-int32_max, int32_max))
        for _ in range(args.iters)
    ]

    # Carefully-chosen corner cases prepended for quick regression visibility.
    corners = [
        (0, 0),
        (1, 1),
        (-1, -1),
        (1, -1),
        (1000, 7),
        (-1000, 7),
        (123456, -654321),
        (2**29, -3),
        (-(2**29), 5),
    ]
    # Constrain mul inputs to avoid 32-bit overflow surprises in the reference;
    # the WAU wraps just like Python's int32 helper, so still apples-to-apples.
    mul_max = 1 << 14
    pairs_flow1 = corners + pairs_random
    pairs_flow2 = corners + pairs_random
    pairs_flow3 = corners + [
        (random.randint(-mul_max, mul_max), random.randint(-mul_max, mul_max))
        for _ in range(args.iters)
    ]

    bench = Bench(wau)
    cases = [
        dict(
            name="flow1_accumulate_and_scale",
            flow_id=1,
            inputs=pairs_flow1,
            reference=flow1_accumulate_and_scale,
            timeout_s=args.timeout_s,
        ),
        dict(
            name="flow2_max_then_scale",
            flow_id=2,
            inputs=pairs_flow2,
            reference=flow2_max_then_scale,
            timeout_s=args.timeout_s,
        ),
        dict(
            name="flow3_fma_a_b_plus_b",
            flow_id=3,
            inputs=pairs_flow3,
            reference=flow3_fma_a_b_plus_b,
            timeout_s=args.timeout_s,
        ),
    ]

    if args.include_cw_flow:
        # Smoke-test only: feed a few pairs through; reference unknown without
        # importing waugen.cw_reference here.
        cw_pairs = [(i, i + 1) for i in range(8)]
        cases.append(
            dict(
                name="cw_flow_smoke",
                flow_id=args.cw_flow_id,
                inputs=cw_pairs,
                reference=lambda a, b: 0,  # placeholder; pass_ratio not meaningful
                timeout_s=args.timeout_s,
            )
        )

    results = bench.sweep(cases)

    # --- pretty print
    print()
    print("=" * 72)
    print(f"{'case':32s}  {'n':>5s}  {'pass':>8s}  {'thr(ops/s)':>12s}  {'p50(ms)':>9s}  {'p95(ms)':>9s}")
    print("-" * 72)
    for r in results:
        s = r.summary()
        lat = s["latency_s"]
        print(
            f"{s['name']:32s}  {s['n']:5d}  "
            f"{s['pass_count']:>4d}/{s['n']:<4d}  "
            f"{s['throughput_ops_per_s']:>12.1f}  "
            f"{lat['p50']*1000:>9.2f}  "
            f"{lat['p95']*1000:>9.2f}"
        )
    print("=" * 72)

    # --- observability summary
    obs1 = wau.observability()
    delta = obs1.delta(obs0)
    print(
        f"\nobservability delta after all cases:"
        f"\n  hops    = {delta.hops}"
        f"\n  stalls  = {delta.stalls}"
        f"\n  fwd     = {delta.forwards}"
        f"\n  deliv   = {delta.local_delivered}"
        f"\n  cache_h = {delta.cache_hits}"
        f"\n  cache_l = {delta.cache_lookups}"
        f"\n  hit_rate= {delta.hit_rate:.3f}"
    )

    # --- write report
    if args.report:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": args.host,
            "port": args.port,
            "iters": args.iters,
            "seed": args.seed,
            "cases": [r.summary() for r in results],
            "obs_baseline": {
                "hops": obs0.hops, "stalls": obs0.stalls, "forwards": obs0.forwards,
                "local_delivered": obs0.local_delivered,
                "cache_hits": obs0.cache_hits, "cache_lookups": obs0.cache_lookups,
            },
            "obs_final": {
                "hops": obs1.hops, "stalls": obs1.stalls, "forwards": obs1.forwards,
                "local_delivered": obs1.local_delivered,
                "cache_hits": obs1.cache_hits, "cache_lookups": obs1.cache_lookups,
            },
            "obs_delta": {
                "hops": delta.hops, "stalls": delta.stalls, "forwards": delta.forwards,
                "local_delivered": delta.local_delivered,
                "cache_hits": delta.cache_hits, "cache_lookups": delta.cache_lookups,
                "hit_rate": delta.hit_rate,
            },
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
        print(f"\nreport: {args.report}")

    client.close()

    # Treat non-1.0 pass_ratio for value-checked flows as failure.
    checked = ("flow1_accumulate_and_scale", "flow2_max_then_scale", "flow3_fma_a_b_plus_b")
    bad = [r for r in results if r.name in checked and r.pass_ratio < 1.0]
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
