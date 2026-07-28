# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for `compiler.build_fast_path_tables` -- the per-core static
fast-path dispatch table ("concurrence ID" assignment) that lets a core hand
a completed stage's result directly to the next core instead of routing back
through `wau_coordinator`.

Built from `CompiledProject` only (never `SchedulePlan`): the runtime
dispatch path -- both the dynamic coordinator and this table -- only ever
chooses between a stage's `primary_core`/`fallback_core`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from waugen.compiler import build_fast_path_tables, compile_project
from waugen.config import load_config_obj
from waugen.scheduler import core_index


def _grid_payload(*, allow_adaptive_reroute: bool = True) -> dict:
    return {
        "device": {
            "preset": "intel_de0_nano",
            "grid": {"x": 2, "y": 2},
        },
        "operations": {"library": ["add", "sub", "mul"]},
        "compiler": {"allow_adaptive_reroute": allow_adaptive_reroute},
        "flows": [],
    }


def _idx(x: int, y: int) -> int:
    return core_index(x, y, grid_x=2, grid_y=2)


def _three_stage_flow(
    flow_id: int,
    *,
    stage0_fallback: bool = True,
    stage1_fallback: bool = True,
    stage1_allow_adaptive: bool = True,
    stage2_allow_adaptive: bool = True,
) -> dict:
    stage0: dict = {"op": "add", "core": {"x": 0, "y": 0}}
    if stage0_fallback:
        stage0["fallback_core"] = {"x": 1, "y": 0}

    stage1: dict = {
        "op": "mul",
        "core": {"x": 0, "y": 1},
        "allow_adaptive": stage1_allow_adaptive,
    }
    if stage1_fallback:
        stage1["fallback_core"] = {"x": 1, "y": 1}

    # allow_adaptive=False here matters beyond gating *this* stage's own
    # fast-path fallback: with it left True (the default), compile_project's
    # _resolve_candidates auto-derives a nearby fallback core for *any* stage
    # that has no explicit fallback_core/candidate_cores whenever
    # allow_adaptive_reroute is also on -- so "no fallback_core configured" is
    # not the same as "CompiledStage.fallback_core is None" unless adaptive
    # rerouting is explicitly turned off for that stage.
    stage2 = {
        "op": "sub",
        "core": {"x": 0, "y": 0},
        "allow_adaptive": stage2_allow_adaptive,
    }

    return {"id": flow_id, "name": f"flow_{flow_id}", "stages": [stage0, stage1, stage2]}


def _build(payload: dict, *, table_bits: int = 5):
    config = load_config_obj(payload)
    project = compile_project(config)
    return project, build_fast_path_tables(project, table_bits=table_bits)


class FastPathTableShapeTests(unittest.TestCase):
    def test_last_stage_never_gets_an_entry(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [_three_stage_flow(1)]
        _, tables = _build(payload)

        all_keys = {key for table in tables for key in table.hops}
        self.assertIn((1, 0), all_keys)
        self.assertIn((1, 1), all_keys)
        self.assertNotIn((1, 2), all_keys)

    def test_stage_with_distinct_fallback_gets_entry_on_both_producer_cores(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [_three_stage_flow(1)]
        _, tables = _build(payload)

        primary_core = _idx(0, 0)
        fallback_core = _idx(1, 0)
        self.assertIn((1, 0), tables[primary_core].hops)
        self.assertIn((1, 0), tables[fallback_core].hops)

    def test_forward_hop_targets_next_stage(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [_three_stage_flow(1)]
        project, tables = _build(payload)

        flow = next(f for f in project.flows if f.flow_id == 1)
        stage1 = flow.stages[1]
        primary_core = _idx(0, 0)
        hop = tables[primary_core].hops[(1, 0)]

        self.assertEqual(hop.next_stage_index, 1)
        self.assertEqual(hop.next_opcode, stage1.opcode)
        self.assertIsNone(hop.next_immediate_b)
        self.assertFalse(hop.next_use_immediate)
        self.assertEqual(hop.next_dst_primary, _idx(0, 1))

    def test_fallback_destination_populated_when_adaptive_reroute_enabled(self) -> None:
        payload = _grid_payload(allow_adaptive_reroute=True)
        payload["flows"] = [_three_stage_flow(1, stage1_allow_adaptive=True)]
        _, tables = _build(payload)

        primary_core = _idx(0, 0)
        hop = tables[primary_core].hops[(1, 0)]
        self.assertEqual(hop.next_dst_primary, _idx(0, 1))
        self.assertEqual(hop.next_dst_fallback, _idx(1, 1))
        self.assertNotEqual(hop.next_dst_fallback, hop.next_dst_primary)

    def test_fallback_destination_absent_when_next_stage_allow_adaptive_is_false(self) -> None:
        payload = _grid_payload(allow_adaptive_reroute=True)
        payload["flows"] = [_three_stage_flow(1, stage1_allow_adaptive=False)]
        _, tables = _build(payload)

        primary_core = _idx(0, 0)
        hop = tables[primary_core].hops[(1, 0)]
        self.assertEqual(hop.next_dst_fallback, hop.next_dst_primary)

    def test_fallback_destination_absent_when_global_reroute_disabled(self) -> None:
        payload = _grid_payload(allow_adaptive_reroute=False)
        payload["flows"] = [_three_stage_flow(1, stage1_allow_adaptive=True)]
        _, tables = _build(payload)

        primary_core = _idx(0, 0)
        hop = tables[primary_core].hops[(1, 0)]
        self.assertEqual(hop.next_dst_fallback, hop.next_dst_primary)

    def test_fallback_destination_absent_when_next_stage_has_no_fallback_core(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [_three_stage_flow(1, stage2_allow_adaptive=False)]
        _, tables = _build(payload)

        # key (1, 1)'s next stage is stage 2, which never sets fallback_core.
        core2 = _idx(0, 1)
        hop = tables[core2].hops[(1, 1)]
        self.assertEqual(hop.next_dst_fallback, hop.next_dst_primary)


class FastPathTableOverflowTests(unittest.TestCase):
    def test_overflow_is_silent_and_safe(self) -> None:
        payload = _grid_payload()
        # Three independent flows whose stage 0 all land on the same core
        # (0,0), with table_bits=1 (capacity 2) -- one of the three distinct
        # (flow_id, 0) pairs cannot fit.
        payload["flows"] = [
            _three_stage_flow(1, stage0_fallback=False),
            _three_stage_flow(2, stage0_fallback=False),
            _three_stage_flow(3, stage0_fallback=False),
        ]
        _, tables = _build(payload, table_bits=1)

        core0 = _idx(0, 0)
        table = tables[core0]
        self.assertEqual(len(table.hops), 2)
        self.assertEqual(len(table.overflowed_keys), 1)
        # No overlap between what fit and what didn't.
        self.assertFalse(set(table.hops) & set(table.overflowed_keys))
        # The overflowed key is deterministically the highest-sorted one.
        expected_overflow = sorted({(1, 0), (2, 0), (3, 0)})[-1]
        self.assertEqual(table.overflowed_keys[0], expected_overflow)

    def test_capacity_is_exactly_two_pow_table_bits(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [
            _three_stage_flow(i, stage0_fallback=False) for i in range(1, 5)
        ]
        _, tables = _build(payload, table_bits=2)  # capacity 4, exactly enough
        core0 = _idx(0, 0)
        self.assertEqual(len(tables[core0].hops), 4)
        self.assertEqual(tables[core0].overflowed_keys, ())


class FastPathTableDeterminismTests(unittest.TestCase):
    def test_deterministic_across_hash_seeds(self) -> None:
        payload = _grid_payload()
        payload["flows"] = [
            _three_stage_flow(1),
            _three_stage_flow(2, stage0_fallback=False),
        ]
        code = "\n".join(
            [
                "import json",
                "from waugen.compiler import build_fast_path_tables, compile_project",
                "from waugen.config import load_config_obj",
                f"payload = {payload!r}",
                "project = compile_project(load_config_obj(payload))",
                "tables = build_fast_path_tables(project, table_bits=5)",
                "out = [",
                "    {",
                "        'core_index': t.core_index,",
                "        'hops': sorted(",
                "            (list(k) + [h.concurrence_id, h.next_stage_index, h.next_opcode,",
                "             h.next_use_immediate, h.next_immediate_b, h.next_dst_primary,",
                "             h.next_dst_fallback])",
                "            for k, h in t.hops.items()",
                "        ),",
                "        'overflowed_keys': sorted(list(k) for k in t.overflowed_keys),",
                "    }",
                "    for t in tables",
                "]",
                "print(json.dumps(out, sort_keys=True))",
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


if __name__ == "__main__":
    unittest.main()
