# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re


class ReplayPlanError(ValueError):
    """Raised when a saved autotune summary cannot produce a replay plan."""


@dataclass(frozen=True)
class ReplayCandidate:
    stage: str
    run_name: str
    status: str
    lane: str
    replicas: str
    max_parallel: str
    priority: str
    load_balance: str
    scheduler_policy: str
    max_in_flight: str
    placement: str
    profile: str
    latency_avg: str
    latency_p95: str
    makespan: str
    fallback_ratio: str
    hops_total: str
    total_ms: str

    def score_key(self) -> tuple[float, float, int, float, int, int, str]:
        return (
            _as_float(self.latency_avg),
            _as_float(self.latency_p95),
            _as_int(self.makespan),
            _as_float(self.fallback_ratio),
            _as_int(self.hops_total),
            _as_int(self.total_ms),
            self.run_name,
        )

    def shell_fields(self) -> tuple[str, ...]:
        return (
            self.stage,
            self.run_name,
            _shell_value(self.lane),
            self.replicas,
            self.max_parallel,
            _shell_value(self.priority),
            _shell_value(self.load_balance),
            _shell_value(self.scheduler_policy),
            self.max_in_flight,
            _shell_value(self.placement),
            _shell_value(self.profile),
            self.latency_avg,
            self.latency_p95,
            self.makespan,
            self.fallback_ratio,
            self.hops_total,
            self.total_ms,
        )

    def to_json(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "run_name": self.run_name,
            "status": self.status,
            "lane": self.lane,
            "replicas": self.replicas,
            "max_parallel": self.max_parallel,
            "priority": self.priority,
            "load_balance": self.load_balance,
            "scheduler_policy": self.scheduler_policy,
            "max_in_flight": self.max_in_flight,
            "placement": self.placement,
            "profile": self.profile,
            "latency_avg": self.latency_avg,
            "latency_p95": self.latency_p95,
            "makespan": self.makespan,
            "fallback_ratio": self.fallback_ratio,
            "hops_total": self.hops_total,
            "total_ms": self.total_ms,
        }


_FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
_REQUIRED_FIELDS = (
    "run",
    "stage",
    "status",
    "lane",
    "replicas",
    "max_parallel",
    "priority",
    "load_balance",
    "scheduler_policy",
    "max_in_flight",
    "placement",
    "profile",
    "exec_latency_avg",
    "exec_latency_p95",
    "makespan",
    "fallback_ratio",
    "hops_total",
    "total_ms",
)
_REPLAY_MODES = {"best", "stage-winners", "best-and-stage-winners", "worst"}


def parse_tuning_summary(text: str) -> tuple[ReplayCandidate, ...]:
    candidates: list[ReplayCandidate] = []
    in_all_runs = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line == "All Runs":
            in_all_runs = True
            continue
        if not in_all_runs or not line.startswith("run="):
            continue

        fields = dict(_FIELD_RE.findall(line))
        missing = [name for name in _REQUIRED_FIELDS if name not in fields]
        if missing:
            raise ReplayPlanError(
                f"line {line_number}: replay candidate is missing fields: {', '.join(missing)}"
            )

        candidates.append(
            ReplayCandidate(
                stage=fields["stage"],
                run_name=fields["run"],
                status=fields["status"],
                lane=fields["lane"],
                replicas=fields["replicas"],
                max_parallel=fields["max_parallel"],
                priority=fields["priority"],
                load_balance=fields["load_balance"],
                scheduler_policy=fields["scheduler_policy"],
                max_in_flight=fields["max_in_flight"],
                placement=fields["placement"],
                profile=fields["profile"],
                latency_avg=fields["exec_latency_avg"],
                latency_p95=fields["exec_latency_p95"],
                makespan=fields["makespan"],
                fallback_ratio=fields["fallback_ratio"],
                hops_total=fields["hops_total"],
                total_ms=fields["total_ms"],
            )
        )

    if not candidates:
        raise ReplayPlanError("summary does not contain an 'All Runs' candidate section")
    return tuple(candidates)


def select_replay_candidates(
    candidates: tuple[ReplayCandidate, ...],
    mode: str,
) -> tuple[ReplayCandidate, ...]:
    if mode not in _REPLAY_MODES:
        raise ReplayPlanError(
            f"unsupported replay mode '{mode}'; expected one of: {', '.join(sorted(_REPLAY_MODES))}"
        )

    passing = [candidate for candidate in candidates if candidate.status == "pass"]
    if not passing:
        raise ReplayPlanError("summary contains no passing candidates")

    best = min(passing, key=ReplayCandidate.score_key)
    if mode == "best":
        return (best,)
    if mode == "worst":
        return (max(passing, key=ReplayCandidate.score_key),)

    stage_winners = tuple(
        min(
            (candidate for candidate in passing if candidate.stage == stage),
            key=ReplayCandidate.score_key,
        )
        for stage in sorted({candidate.stage for candidate in passing})
    )
    if mode == "stage-winners":
        return stage_winners

    selected: list[ReplayCandidate] = [best]
    seen = {best.run_name}
    for candidate in stage_winners:
        if candidate.run_name in seen:
            continue
        seen.add(candidate.run_name)
        selected.append(candidate)
    return tuple(selected)


def build_replay_plan(summary_path: Path, mode: str) -> tuple[ReplayCandidate, ...]:
    if not summary_path.exists():
        raise ReplayPlanError(f"tuning summary not found: {summary_path}")
    return select_replay_candidates(parse_tuning_summary(summary_path.read_text()), mode)


def _as_float(value: str) -> float:
    if value == "inf":
        return math.inf
    return float(value)


def _as_int(value: str) -> int:
    if value == "inf":
        return 2**31 - 1
    return int(float(value))


def _shell_value(value: str) -> str:
    return "" if value == "auto" else value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic replay plans from WAU CW autotune summaries"
    )
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=sorted(_REPLAY_MODES))
    parser.add_argument("--format", choices=["shell", "json"], default="shell")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        selected = build_replay_plan(args.summary, args.mode)
    except ReplayPlanError as exc:
        print(f"Replay plan error: {exc}")
        return 2

    if args.format == "json":
        print(json.dumps([candidate.to_json() for candidate in selected], indent=2))
    else:
        for candidate in selected:
            print("|".join(candidate.shell_fields()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
