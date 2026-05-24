# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Generic micro-benchmark harness on top of WAU.

The user supplies:
  * a Callable[(int, int), int] software reference computing the expected value
  * an iterable of (a, b) input pairs
  * a flow_id
  ...and Bench.run() returns a BenchResult with latency percentiles, throughput,
  scoreboard pass ratio (== how many results matched the reference), and the
  observability delta over the run.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .wau import WAU, WAUResult, ObservabilitySnapshot


@dataclass
class BenchResult:
    name: str
    flow_id: int
    n: int
    pass_count: int
    failures: list[tuple[int, int, int, int]] = field(default_factory=list)  # (a,b,expected,got)
    latencies_s: list[float] = field(default_factory=list)
    obs_delta: ObservabilitySnapshot | None = None
    wall_s: float = 0.0

    @property
    def pass_ratio(self) -> float:
        return self.pass_count / self.n if self.n else 0.0

    @property
    def throughput_ops_per_s(self) -> float:
        return self.n / self.wall_s if self.wall_s else 0.0

    def summary(self) -> dict[str, object]:
        if self.latencies_s:
            p50 = statistics.median(self.latencies_s)
            p95 = sorted(self.latencies_s)[max(0, int(0.95 * len(self.latencies_s)) - 1)]
            mn = min(self.latencies_s)
            mx = max(self.latencies_s)
            mean = statistics.fmean(self.latencies_s)
        else:
            p50 = p95 = mn = mx = mean = 0.0
        return {
            "name": self.name,
            "flow_id": self.flow_id,
            "n": self.n,
            "pass_count": self.pass_count,
            "pass_ratio": self.pass_ratio,
            "throughput_ops_per_s": self.throughput_ops_per_s,
            "latency_s": {
                "min":  mn,
                "p50":  p50,
                "mean": mean,
                "p95":  p95,
                "max":  mx,
            },
            "obs_delta": None if self.obs_delta is None else {
                "hops": self.obs_delta.hops,
                "stalls": self.obs_delta.stalls,
                "forwards": self.obs_delta.forwards,
                "local_delivered": self.obs_delta.local_delivered,
                "cache_hits": self.obs_delta.cache_hits,
                "cache_lookups": self.obs_delta.cache_lookups,
                "hit_rate": self.obs_delta.hit_rate,
            },
            "wall_s": self.wall_s,
            "failures": self.failures[:8],  # truncate for readability
        }


class Bench:
    def __init__(self, wau: WAU) -> None:
        self.wau = wau

    def run(
        self,
        *,
        name: str,
        flow_id: int,
        inputs: Iterable[tuple[int, int]],
        reference: Callable[[int, int], int],
        timeout_s: float = 2.0,
        collect_obs: bool = True,
    ) -> BenchResult:
        pairs = list(inputs)
        result = BenchResult(name=name, flow_id=flow_id, n=len(pairs), pass_count=0)

        obs_before = self.wau.observability() if collect_obs else None
        t0 = time.monotonic()

        for a, b in pairs:
            r: WAUResult = self.wau.execute(flow_id, a, b, timeout_s=timeout_s)
            expected = reference(a, b)
            result.latencies_s.append(r.latency_s)
            if r.value == expected:
                result.pass_count += 1
            else:
                if len(result.failures) < 16:
                    result.failures.append((a, b, expected, r.value))

        result.wall_s = time.monotonic() - t0
        if obs_before is not None:
            result.obs_delta = self.wau.observability().delta(obs_before)
        return result

    def sweep(
        self,
        cases: Sequence[dict[str, object]],
    ) -> list[BenchResult]:
        """Run multiple named cases in order. Each case dict matches `run` kwargs."""
        return [self.run(**case) for case in cases]  # type: ignore[arg-type]
