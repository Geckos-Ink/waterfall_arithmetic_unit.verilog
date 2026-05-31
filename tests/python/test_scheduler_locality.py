# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Routing-efficiency (locality-aware core selection) scheduler tests.

`scheduler.locality_bias` turns the physical distance between a node's
data-producing dependencies and a candidate core into a tiebreaker during core
selection. The earliest-free cycle stays the primary key, so locality never
trades away latency/makespan; it only shrinks transfer hops among cores that
become free at the same time.

These tests verify that:
  * the knob defaults to 0.0 (off) and reproduces the untuned baseline exactly;
  * enabling it never inflates makespan on the demo config;
  * it reduces the estimated transfer-hop proxy on a workload with real
    placement contention;
  * scheduling stays deterministic for a fixed bias;
  * invalid (negative) bias is rejected at config load.
"""
from __future__ import annotations

import copy
import dataclasses
import json
from collections import defaultdict
from pathlib import Path
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
    """Same stage-order Manhattan proxy the benchmark logs as
    `estimated_transfer_hops_total`."""
    groups: dict[tuple[int, int, int], list] = defaultdict(list)
    for ins in plan.instructions:
        groups[(ins.program_id, ins.program_replica, ins.flow_id)].append(ins)
    total = 0
    for group in groups.values():
        group.sort(key=lambda i: (i.iteration, i.cycle_start, i.stage_index, i.node_id))
        for prev, cur in zip(group, group[1:]):
            total += abs(cur.core_x - prev.core_x) + abs(cur.core_y - prev.core_y)
    return total


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
