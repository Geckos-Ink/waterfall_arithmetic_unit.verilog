#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Run a value-checked CW stress benchmark on the live DE0-Nano WAU image."""

from __future__ import annotations

import argparse
import gzip
import json
import random
import statistics
import struct
import sys
import time
from collections import Counter
from pathlib import Path

HOST_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = HOST_ROOT.parent
REPO_ROOT = DEMO_ROOT.parents[2]
SRC_ROOT = REPO_ROOT / "src" / "python"

for path in (HOST_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from waujtag import MMIO, TCLClient, WAU  # noqa: E402
from waugen.cw_reference import compute_expected_values  # noqa: E402


GOLDEN_CASES: list[tuple[int, int, int]] = [
    (1, 10, 4),
    (2, -7, 5),
    (3, 21, -3),
    (4, 0, 0),
    (5, 31, 31),
    (6, -15, -9),
    (7, 127, -11),
    (8, -64, 17),
]


def _latency_summary(latencies_s: list[float]) -> dict[str, float]:
    ordered = sorted(latencies_s)
    if not ordered:
        return {"min": 0.0, "p50": 0.0, "mean": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "min": min(ordered),
        "p50": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p95": ordered[max(0, int(0.95 * len(ordered)) - 1)],
        "max": max(ordered),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAU DE0-Nano CW stress benchmark")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2540)
    parser.add_argument("--flow-id", type=int, default=90)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEMO_ROOT / "build" / "wau_de0_nano_cw_stress_last.json",
    )
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=0xC0FFEE)
    parser.add_argument("--random-iters", type=int, default=128)
    parser.add_argument("--random-range", type=int, default=255)
    parser.add_argument(
        "--mnist-images",
        type=Path,
        default=None,
        help=(
            "Optional MNIST images idx3(.gz) file (see scripts/fetch_dataset.py). "
            "When set, the --random-iters operand pairs are streamed from real "
            "MNIST pixels (centered to [-128,127]) instead of the RNG, to test "
            "data-exchange efficiency on representative, spatially-correlated data."
        ),
    )
    parser.add_argument(
        "--mnist-offset",
        type=int,
        default=0,
        help="Skip this many leading MNIST pixels before streaming operand pairs",
    )
    parser.add_argument("--skip-soft-reset", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args()


def _load_mnist_pixels(path: Path) -> bytes:
    """Read raw uint8 pixels from an MNIST images idx3 file (plain or .gz).

    Kept self-contained so the demo host stays independent of repo scripts/.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        magic, count, rows, cols = struct.unpack(">IIII", handle.read(16))
        if magic != 2051:
            raise ValueError(f"{path}: bad MNIST images magic {magic} (expected 2051)")
        pixels = handle.read(count * rows * cols)
    if len(pixels) != count * rows * cols:
        raise ValueError(f"{path}: truncated MNIST image data")
    return pixels


def _build_cases(
    seed: int,
    random_iters: int,
    random_range: int,
    *,
    mnist_images: Path | None = None,
    mnist_offset: int = 0,
) -> list[tuple[int, int, int, str]]:
    rows: list[tuple[int, int, int, str]] = []
    for case_id, a, b in GOLDEN_CASES:
        rows.append((case_id, a, b, "golden"))

    if mnist_images is not None:
        # Stream (a, b) operand pairs from consecutive MNIST pixels, centered to
        # [-128, 127] so signed add/mul/max/residual/ReLU paths are exercised on
        # real, spatially-correlated data instead of the RNG.
        pixels = _load_mnist_pixels(mnist_images)
        start = mnist_offset % max(1, len(pixels))
        for idx in range(random_iters):
            case_id = len(GOLDEN_CASES) + idx + 1
            pa = pixels[(start + 2 * idx) % len(pixels)]
            pb = pixels[(start + 2 * idx + 1) % len(pixels)]
            rows.append((case_id, pa - 128, pb - 128, "mnist"))
        return rows

    rng = random.Random(seed)
    for idx in range(random_iters):
        case_id = len(GOLDEN_CASES) + idx + 1
        a = rng.randint(-random_range, random_range)
        b = rng.randint(-random_range, random_range)
        rows.append((case_id, a, b, "random"))
    return rows


def main() -> int:
    args = parse_args()
    if not args.config.exists():
        print(f"missing compiled config: {args.config}", file=sys.stderr)
        return 2

    cases = _build_cases(
        args.seed,
        args.random_iters,
        args.random_range,
        mnist_images=args.mnist_images,
        mnist_offset=args.mnist_offset,
    )
    expected_rows = compute_expected_values(
        args.config,
        args.flow_id,
        [(case_id, a, b) for case_id, a, b, _kind in cases],
    )
    expected_by_case = {row["case"]: row["expected"] for row in expected_rows}

    print(f"connecting to {args.host}:{args.port} ...", flush=True)
    client = TCLClient(host=args.host, port=args.port, timeout=10.0)
    client.connect()
    print("connected. server greeted READY", flush=True)

    mmio = MMIO(client)
    wau = WAU(mmio)

    if not client.ping():
        print("PING failed", file=sys.stderr)
        return 2
    aux = client.obs_aux()
    print(f"PING ok. obs_aux=0x{aux:08X}  (magic should be 0xCAFE in low 16b)")

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

    latencies_s: list[float] = []
    failures: list[dict[str, int | str | None | bool]] = []
    aborted_on_timeout = False
    pass_count = 0
    pass_by_kind: Counter[str] = Counter()
    total_by_kind: Counter[str] = Counter(kind for _case_id, _a, _b, kind in cases)
    sample_outputs: list[dict[str, int | str]] = []

    t0 = time.monotonic()
    for case_id, a, b, kind in cases:
        expected = expected_by_case[case_id]
        try:
            result = wau.execute(args.flow_id, a, b, timeout_s=args.timeout_s)
        except TimeoutError:
            aborted_on_timeout = True
            if len(failures) < 24:
                failures.append(
                    {
                        "case": case_id,
                        "kind": kind,
                        "a": a,
                        "b": b,
                        "expected": expected,
                        "got_flow": None,
                        "got_value": None,
                        "timeout": True,
                    }
                )
            # A synthesized WAU must finish a fixed schedule in microseconds.
            # Treat the watchdog as a circuit/configuration fault and stop at
            # the first hang; continuing would turn a broken image into a
            # misleadingly slow benchmark.
            status_at_timeout = wau.status()
            obs_at_timeout = wau.observability()
            print(
                f"CIRCUIT HANG: case {case_id} exceeded {args.timeout_s:.3f}s; "
                f"STATUS=0x{status_at_timeout:08X}, "
                f"hops={obs_at_timeout.hops}, stalls={obs_at_timeout.stalls}, "
                f"delivered={obs_at_timeout.local_delivered}",
                file=sys.stderr,
                flush=True,
            )
            break
        latencies_s.append(result.latency_s)

        if len(sample_outputs) < 16:
            sample_outputs.append(
                {
                    "case": case_id,
                    "kind": kind,
                    "a": a,
                    "b": b,
                    "expected": expected,
                    "got_flow": result.flow_id,
                    "got_value": result.value,
                }
            )

        if result.flow_id == args.flow_id and result.value == expected:
            pass_count += 1
            pass_by_kind[kind] += 1
        elif len(failures) < 24:
            failures.append(
                {
                    "case": case_id,
                    "kind": kind,
                    "a": a,
                    "b": b,
                    "expected": expected,
                    "got_flow": result.flow_id,
                    "got_value": result.value,
                    "timeout": False,
                }
            )

    wall_s = time.monotonic() - t0
    obs1 = wau.observability()
    delta = obs1.delta(obs0)
    latency = _latency_summary(latencies_s)
    throughput = len(cases) / wall_s if wall_s else 0.0

    print()
    print("=" * 72)
    print("CW Stress Benchmark (example-program-class flow, live DE0-Nano)")
    print("-" * 72)
    print(f"config             : {args.config}")
    print(f"flow id            : {args.flow_id}")
    print(f"total cases        : {len(cases)}")
    for kind in sorted(total_by_kind):
        print(f"{kind + ' pass':<19}: {pass_by_kind[kind]}/{total_by_kind[kind]}")
    print(f"scoreboard pass    : {pass_count}/{len(cases)}")
    print(f"throughput (ops/s) : {throughput:.1f}")
    print(f"latency p50 / p95  : {latency['p50']*1000:.2f} ms / {latency['p95']*1000:.2f} ms")
    print("=" * 72)
    print(
        f"observability delta:"
        f"\n  hops    = {delta.hops}"
        f"\n  stalls  = {delta.stalls}"
        f"\n  fwd     = {delta.forwards}"
        f"\n  deliv   = {delta.local_delivered}"
        f"\n  cache_h = {delta.cache_hits}"
        f"\n  cache_l = {delta.cache_lookups}"
        f"\n  hit_rate= {delta.hit_rate:.3f}"
    )

    if failures:
        print("\nfailures:")
        for failure in failures[:8]:
            print(
                (
                    "  case={case} kind={kind} a={a} b={b} expected={expected} timeout"
                    if failure.get("timeout")
                    else "  case={case} kind={kind} a={a} b={b} expected={expected} "
                    "got(flow={got_flow}, value={got_value})"
                ).format(**failure)
            )

    if args.report is not None:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": args.host,
            "port": args.port,
            "config": str(args.config),
            "flow_id": args.flow_id,
            "seed": args.seed,
            "random_iters": args.random_iters,
            "random_range": args.random_range,
            "operand_source": "mnist" if args.mnist_images is not None else "random",
            "mnist_images": str(args.mnist_images) if args.mnist_images is not None else None,
            "total_cases": len(cases),
            "pass_count": pass_count,
            "pass_ratio": (pass_count / len(cases)) if cases else 0.0,
            "execution_status": "circuit_hang" if aborted_on_timeout else (
                "pass" if pass_count == len(cases) else "scoreboard_failure"
            ),
            "aborted_on_timeout": aborted_on_timeout,
            "pass_by_kind": dict(pass_by_kind),
            "total_by_kind": dict(total_by_kind),
            "throughput_ops_per_s": throughput,
            "latency_s": latency,
            "obs_delta": {
                "hops": delta.hops,
                "stalls": delta.stalls,
                "forwards": delta.forwards,
                "local_delivered": delta.local_delivered,
                "cache_hits": delta.cache_hits,
                "cache_lookups": delta.cache_lookups,
                "hit_rate": delta.hit_rate,
            },
            "sample_outputs": sample_outputs,
            "failures": failures,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nreport: {args.report}")

    client.close()
    return 0 if pass_count == len(cases) else (3 if aborted_on_timeout else 1)


if __name__ == "__main__":
    raise SystemExit(main())
