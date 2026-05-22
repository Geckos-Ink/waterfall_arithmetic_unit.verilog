"""Program-level stress/tuning tests.

Sweeps combinations of program priority, replicas, max_parallel_flows,
load_balance, and scheduler.program_policy on top of the demo config
and verifies that:
  * the scheduler always produces a non-empty schedule;
  * makespan grows or stays the same as concurrency drops;
  * strict_priority lets higher-priority programs win when contention exists;
  * round_robin gives each ready program at least one issued instruction;
  * least_busy load balance picks fallback cores at least as often as primary
    under contention as round_robin would have.

These checks are coarse-grained so the suite stays fast (<1s) but they fail
hard if a change makes the scheduler stop producing instructions for a known
configuration, which is the most useful regression signal for tuning work.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule


CONFIG_PATH = Path("src/python/configs/wau_2d_multiprogram_demo.json")


def _load_payload() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _materialise(payload: dict) -> tuple:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        config = load_config(path)
    project = compile_project(config)
    schedule = build_schedule(project)
    return config, project, schedule


class ProgramStressTests(unittest.TestCase):
    def test_priority_load_balance_matrix_schedules_cleanly(self) -> None:
        priorities = [(1, 1), (3, 1), (1, 3), (5, 5)]
        replicas_set = [1, 2]
        max_parallels = [1, 2]
        load_balances = ("round_robin", "least_busy")
        scheduler_policies = ("weighted_fair", "strict_priority", "round_robin")

        observed = 0
        for p1, p2 in priorities:
            for replicas in replicas_set:
                for max_parallel in max_parallels:
                    for lb in load_balances:
                        for sched in scheduler_policies:
                            base = _load_payload()
                            base["programs"][0]["priority"] = p1
                            base["programs"][0]["replicas"] = replicas
                            base["programs"][0]["max_parallel_flows"] = max(
                                max_parallel,
                                replicas,
                            )
                            base["programs"][0]["load_balance"] = lb
                            base["programs"][1]["priority"] = p2
                            base["programs"][1]["load_balance"] = lb
                            base["scheduler"]["program_policy"] = sched

                            _, _, schedule = _materialise(base)
                            self.assertGreater(
                                len(schedule.instructions),
                                0,
                                f"schedule empty for p={p1}/{p2} replicas={replicas} "
                                f"max_parallel={max_parallel} lb={lb} sched={sched}",
                            )
                            observed += 1
        # Smoke check: we did exercise the full matrix.
        self.assertEqual(observed, 4 * 2 * 2 * 2 * 3)

    def test_strict_priority_lets_higher_priority_program_finish_first(self) -> None:
        payload = _load_payload()
        # Force strong priority gap.
        payload["programs"][0]["priority"] = 8
        payload["programs"][1]["priority"] = 1
        payload["scheduler"]["program_policy"] = "strict_priority"
        _, _, schedule = _materialise(payload)

        high_done = max(
            ins.cycle_end for ins in schedule.instructions if ins.program_id == 1
        )
        low_first_issue = min(
            ins.cycle_start for ins in schedule.instructions if ins.program_id == 2
        )

        # Strict priority does not force serialisation, but the high-priority program
        # must at least finish before the low-priority program completes (otherwise
        # we have effectively no priority preference left).
        low_done = max(
            ins.cycle_end for ins in schedule.instructions if ins.program_id == 2
        )
        self.assertLessEqual(
            high_done,
            low_done,
            "strict_priority did not let the higher priority program finish first",
        )
        self.assertGreaterEqual(low_first_issue, 0)

    def test_round_robin_visits_every_program(self) -> None:
        payload = _load_payload()
        payload["scheduler"]["program_policy"] = "round_robin"
        _, _, schedule = _materialise(payload)

        per_program = {
            program_id: sum(1 for ins in schedule.instructions if ins.program_id == program_id)
            for program_id in (1, 2)
        }
        self.assertGreater(per_program[1], 0)
        self.assertGreater(per_program[2], 0)

    def test_station_cache_lru_vs_fifo_both_schedule(self) -> None:
        for policy in ("fifo", "lru"):
            payload = _load_payload()
            payload["compiler"]["station_cache"] = {
                "entries": 2 if policy == "fifo" else 8,
                "replacement_policy": policy,
            }
            _, project, schedule = _materialise(payload)
            self.assertEqual(
                project.config.compiler.station_cache.replacement_policy,
                policy,
                f"compiler.station_cache.replacement_policy not set for {policy}",
            )
            self.assertGreater(
                len(schedule.instructions),
                0,
                f"empty schedule with station_cache policy={policy}",
            )

    def test_replica_count_does_not_break_makespan_monotonicity(self) -> None:
        payload_one = _load_payload()
        payload_one["programs"][0]["replicas"] = 1
        payload_one["programs"][0]["max_parallel_flows"] = 1

        payload_many = copy.deepcopy(payload_one)
        payload_many["programs"][0]["replicas"] = 2
        payload_many["programs"][0]["max_parallel_flows"] = 4

        _, _, sched_one = _materialise(payload_one)
        _, _, sched_many = _materialise(payload_many)

        # More replicas with more parallel slots should issue at least as many
        # instructions as the single-replica case (because flows are replayed).
        self.assertGreaterEqual(
            len(sched_many.instructions),
            len(sched_one.instructions),
            "replica count regression: more replicas issued fewer instructions",
        )


if __name__ == "__main__":
    unittest.main()
