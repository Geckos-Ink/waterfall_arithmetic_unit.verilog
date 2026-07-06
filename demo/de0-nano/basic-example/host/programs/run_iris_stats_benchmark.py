#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Run the DE0-Nano iris morphology benchmark over vJTAG.

This benchmark drives flow_id=4 in `wau_de0_nano_basic.json`, feeding real
Iris measurements (sepal length and petal length, scaled to tenths) through a
fixed-point morphology score derived from the dataset's global medians.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waujtag import MMIO, TCLClient, WAU  # noqa: E402


def _to_int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & (1 << 31) else x


def iris_morphology_score(a: int, b: int) -> int:
    """Reference for flow 4.

    The chain is intentionally identical to the staged WAU flow:
      acc = a
      acc = acc - 58
      acc = acc * 4
      acc = acc + b
      acc = acc - 44
      acc = acc * 3
      acc = acc + 32
      acc = max(acc, 0)
      acc = acc * 2
      acc = acc - 80
      acc = max(acc, 0)
    """
    acc = _to_int32(a)
    acc = _to_int32(acc - 58)
    acc = _to_int32(acc * 4)
    acc = _to_int32(acc + b)
    acc = _to_int32(acc - 44)
    acc = _to_int32(acc * 3)
    acc = _to_int32(acc + 32)
    acc = max(acc, 0)
    acc = _to_int32(acc * 2)
    acc = _to_int32(acc - 80)
    acc = max(acc, 0)
    return _to_int32(acc)


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


def _score_summary(scores: list[int]) -> dict[str, float | int]:
    ordered = sorted(scores)
    if not ordered:
        return {"min": 0, "p50": 0.0, "p95": 0.0, "max": 0, "nonzero": 0}
    return {
        "min": min(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[max(0, int(0.95 * len(ordered)) - 1)],
        "max": max(ordered),
        "nonzero": sum(1 for score in ordered if score != 0),
    }


def _load_dataset(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "a": int(row["sepal_length_tenths"]),
                    "b": int(row["petal_length_tenths"]),
                    "label": row["label"],
                }
            )
    if not rows:
        raise ValueError(f"dataset is empty: {path}")
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAU DE0-Nano Iris benchmark")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2540)
    parser.add_argument("--flow-id", type=int, default=4)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data" / "iris_sepal_petal_tenths.csv",
    )
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--skip-soft-reset", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = _load_dataset(args.dataset)

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
    label_scores: dict[str, list[int]] = defaultdict(list)
    failures: list[dict[str, object]] = []
    sample_outputs: list[dict[str, object]] = []
    pass_count = 0

    t0 = time.monotonic()
    for idx, row in enumerate(rows):
        a = int(row["a"])
        b = int(row["b"])
        label = str(row["label"])
        expected = iris_morphology_score(a, b)
        result = wau.execute(args.flow_id, a, b, timeout_s=args.timeout_s)
        latencies_s.append(result.latency_s)
        label_scores[label].append(result.value)

        if len(sample_outputs) < 12:
            sample_outputs.append(
                {
                    "row": idx,
                    "label": label,
                    "a": a,
                    "b": b,
                    "expected": expected,
                    "got": result.value,
                }
            )

        if result.flow_id == args.flow_id and result.value == expected:
            pass_count += 1
        elif len(failures) < 16:
            failures.append(
                {
                    "row": idx,
                    "label": label,
                    "a": a,
                    "b": b,
                    "expected": expected,
                    "got_flow": result.flow_id,
                    "got_value": result.value,
                }
            )

    wall_s = time.monotonic() - t0
    obs1 = wau.observability()
    delta = obs1.delta(obs0)
    latency = _latency_summary(latencies_s)
    throughput = len(rows) / wall_s if wall_s else 0.0

    print()
    print("=" * 72)
    print("Iris Morphology Score Benchmark (real dataset, live DE0-Nano)")
    print("-" * 72)
    print(f"dataset rows       : {len(rows)}")
    print(f"scoreboard pass    : {pass_count}/{len(rows)}")
    print(f"throughput (ops/s) : {throughput:.1f}")
    print(f"latency p50 / p95  : {latency['p50']*1000:.2f} ms / {latency['p95']*1000:.2f} ms")
    print("=" * 72)
    print("label stats")
    for label in sorted(label_scores):
        stats = _score_summary(label_scores[label])
        print(
            f"  {label:16s} n={len(label_scores[label]):3d} "
            f"nonzero={stats['nonzero']:3d} "
            f"min={stats['min']:4.0f} p50={stats['p50']:6.1f} "
            f"p95={stats['p95']:6.1f} max={stats['max']:4.0f}"
        )

    print(
        f"\nobservability delta:"
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
                "  row={row} label={label} a={a} b={b} expected={expected} "
                "got(flow={got_flow}, value={got_value})".format(**failure)
            )

    if args.report is not None:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": args.host,
            "port": args.port,
            "flow_id": args.flow_id,
            "dataset": str(args.dataset),
            "dataset_rows": len(rows),
            "pass_count": pass_count,
            "pass_ratio": pass_count / len(rows) if rows else 0.0,
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
            "label_stats": {
                label: _score_summary(scores)
                for label, scores in sorted(label_scores.items())
            },
            "sample_outputs": sample_outputs,
            "failures": failures,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
        print(f"\nreport: {args.report}")

    client.close()
    return 0 if pass_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
