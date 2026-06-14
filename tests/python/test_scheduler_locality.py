# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Routing-efficiency (locality-aware core selection) scheduler tests.

`scheduler.locality_bias` turns the physical distance between a node's
data-producing dependencies and a candidate core into a tiebreaker during core
selection. The earliest-free cycle stays the primary key, so locality never
trades away latency/makespan; it only shrinks transfer hops among cores that
become free at the same time.

These tests verify that:
  * the knob defaults to 0.0 (off), matching an explicitly configured 0.0;
  * enabling it never inflates makespan on the demo config;
  * it reduces true dependency-edge transfer hops on a workload with real
    placement contention;
  * scheduling stays deterministic across Python hash seeds;
  * invalid (negative) bias is rejected at config load.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import ConfigError, load_config
from waugen.scheduler import build_schedule


CONFIG_PATH = Path("src/python/configs/wau_2d_multiprogram_demo.json")


def _load_payload() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _build(payload: dict):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        config = load_config(path)
    return build_schedule(compile_project(config))


def _transfer_hops(plan) -> int:
    """Dependency-edge Manhattan metric exported in `wau_schedule.json`."""
    return int(plan.to_json()["estimated_transfer_hops_total"])


def _branched_dependency_payload() -> dict:
    payload = _load_payload()
    branch_flow = next(flow for flow in payload["flows"] if flow["id"] == 11)
    branch_flow["nodes"][0]["placement"] = {
        "core": {"x": 0, "y": 0},
        "fixed": True,
    }
    branch_flow["nodes"][1]["placement"] = {
        "core": {"x": 3, "y": 0},
        "fixed": True,
    }
    branch_flow["nodes"][2]["placement"] = {
        "core": {"x": 0, "y": 0},
        "fixed": True,
    }
    payload["flows"] = [branch_flow]
    payload["programs"] = [
        {
            "id": 1,
            "name": "branch_metric",
            "flows": [11],
            "priority": 1,
            "replicas": 1,
            "max_parallel_flows": 1,
            "load_balance": "least_busy",
            "allow_async": True,
            "allow_out_of_order": True,
        }
    ]
    return payload


class SchedulerLocalityTests(unittest.TestCase):
    def test_default_bias_is_zero(self) -> None:
        payload = _load_payload()
        payload.get("scheduler", {}).pop("locality_bias", None)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(payload, indent=2))
            config = load_config(path)
        self.assertEqual(config.scheduler.locality_bias, 0.0)

    def test_disabled_matches_baseline(self) -> None:
        payload = _load_payload()
        payload.setdefault("scheduler", {})["locality_bias"] = 0.0
        plan = _build(payload)

        base = copy.deepcopy(payload)
        base.get("scheduler", {}).pop("locality_bias", None)
        baseline = _build(base)

        self.assertEqual(plan.to_json(), baseline.to_json())

    def test_enabled_does_not_inflate_makespan_and_cuts_hops(self) -> None:
        off = _load_payload()
        off.setdefault("scheduler", {})["locality_bias"] = 0.0
        plan_off = _build(off)

        on = _load_payload()
        on.setdefault("scheduler", {})["locality_bias"] = 1.0
        plan_on = _build(on)

        self.assertLessEqual(plan_on.makespan_cycles, plan_off.makespan_cycles)
        self.assertLessEqual(_transfer_hops(plan_on), _transfer_hops(plan_off))
        # On this contended demo config the locality tiebreak is actually used.
        self.assertLess(_transfer_hops(plan_on), _transfer_hops(plan_off))

    def test_fixed_bias_is_deterministic(self) -> None:
        payload = _load_payload()
        payload.setdefault("scheduler", {})["locality_bias"] = 1.5
        self.assertEqual(_build(payload).to_json(), _build(payload).to_json())

    def test_schedule_is_hash_seed_independent_across_processes(self) -> None:
        code = "\n".join(
            [
                "import json",
                "from waugen.compiler import compile_project",
                "from waugen.config import load_config",
                "from waugen.scheduler import build_schedule",
                "from pathlib import Path",
                f"config = load_config(Path({str(CONFIG_PATH.resolve())!r}))",
                "schedule = build_schedule(compile_project(config)).to_json()",
                "print(json.dumps(schedule, sort_keys=True))",
            ]
        )
        outputs = []
        for seed in ("1", "2", "3", "4"):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            result = subprocess.run(
                [sys.executable, "-c", code],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            outputs.append(result.stdout)

        self.assertTrue(all(output == outputs[0] for output in outputs[1:]))

    def test_transfer_metric_follows_dependency_edges_not_stage_adjacency(self) -> None:
        plan = _build(_branched_dependency_payload())
        schedule = plan.to_json()

        self.assertEqual(
            schedule["estimated_transfer_hops_metric"], "dependency_edges_v1"
        )
        self.assertEqual(schedule["estimated_transfer_hops_total"], 3)
        self.assertEqual(schedule["estimated_transfer_hops_edge_count"], 2)
        self.assertEqual(schedule["estimated_transfer_hops_avg_edge"], 1.5)
        self.assertEqual(schedule["estimated_transfer_hops_unresolved_edges"], 0)

        merge = next(
            ins for ins in schedule["instructions"] if ins["node_id"] == "merge_mul"
        )
        self.assertEqual(merge["data_dependency_count"], 2)
        self.assertEqual(len(merge["data_dependency_keys"]), 2)

    def test_transfer_metric_excludes_ordering_only_edges(self) -> None:
        payload = _branched_dependency_payload()
        payload["programs"][0]["allow_async"] = False
        schedule = _build(payload).to_json()

        self.assertEqual(schedule["estimated_transfer_hops_total"], 3)
        self.assertEqual(schedule["estimated_transfer_hops_edge_count"], 2)

        right = next(
            ins for ins in schedule["instructions"] if ins["node_id"] == "right_max"
        )
        self.assertEqual(right["dependency_count"], 1)
        self.assertEqual(right["data_dependency_count"], 0)

    def test_negative_bias_rejected(self) -> None:
        payload = _load_payload()
        payload.setdefault("scheduler", {})["locality_bias"] = -1.0
        with self.assertRaises(ConfigError):
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "config.json"
                path.write_text(json.dumps(payload, indent=2))
                load_config(path)


if __name__ == "__main__":
    unittest.main()
