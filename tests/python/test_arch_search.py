# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for the synthesis-time architecture search (`waugen arch-search`).

The search must be deterministic, rank by the documented
`arch_search_rank_v1` key (feasible, makespan, hops, DRAM bytes, utilization,
LUTs, id), preserve the base core count across grid reshapes, strip placement
pins so every candidate re-derives placement, and expose coherent
resource/DRAM estimate relations between memory splits and operation
distributions.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from waugen.arch_search import (
    CandidateKnobs,
    _rank_key,
    build_candidate_payload,
    run_arch_search,
)
from waugen.cli import main as waugen_main

DEMO_CONFIG = Path("src/python/configs/wau_2d_multiprogram_demo.json")
CW_CONFIG = Path("src/python/configs/wau_example_pogram_compiled.json")

# Demo config heavy/light split: mul (latency 3) and div (latency 8, not
# pipelined) are heavy; add/sub/max are light.
DEMO_HEAVY = ["mul", "div"]
DEMO_LIGHT = ["add", "sub", "max"]


def _candidate_by_id(report, candidate_id: str):
    for cand in report.candidates:
        if cand.knobs.candidate_id == candidate_id:
            return cand
    raise AssertionError(f"candidate {candidate_id} not in report")


class ArchSearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_arch_search(DEMO_CONFIG)

    def test_report_is_deterministic(self) -> None:
        again = run_arch_search(DEMO_CONFIG)
        self.assertEqual(self.report.to_json(), again.to_json())

    def test_ranking_is_dense_and_sorted(self) -> None:
        ranks = [cand.rank for cand in self.report.candidates]
        self.assertEqual(ranks, list(range(1, len(self.report.candidates) + 1)))
        keys = [_rank_key(cand) for cand in self.report.candidates]
        self.assertEqual(keys, sorted(keys))
        feasibility = [cand.feasible for cand in self.report.candidates]
        # No feasible candidate may rank below an infeasible one.
        if False in feasibility:
            self.assertGreater(
                feasibility.index(False),
                max(i for i, ok in enumerate(feasibility) if ok),
            )

    def test_baseline_shape_present_and_feasible(self) -> None:
        cand = _candidate_by_id(self.report, "g4x3_uniform_balanced")
        self.assertTrue(cand.feasible)
        self.assertGreater(cand.metrics["makespan_cycles"], 0)
        self.assertEqual(
            cand.metrics["estimated_transfer_hops_metric"], "dependency_edges_v1"
        )
        self.assertGreater(cand.resources["luts"], 0)
        self.assertGreater(cand.resources["bram_kbits"], 0)

    def test_grid_shapes_preserve_core_count(self) -> None:
        core_count = self.report.base_grid_x * self.report.base_grid_y
        for cand in self.report.candidates:
            self.assertEqual(cand.knobs.grid_x * cand.knobs.grid_y, core_count)

    def test_memory_split_estimate_relations(self) -> None:
        balanced = _candidate_by_id(self.report, "g4x3_uniform_balanced")
        local_heavy = _candidate_by_id(self.report, "g4x3_uniform_local_heavy")
        dram_offload = _candidate_by_id(self.report, "g4x3_uniform_dram_offload")

        self.assertGreater(
            local_heavy.resources["bram_kbits"], balanced.resources["bram_kbits"]
        )
        self.assertLess(
            dram_offload.resources["bram_kbits"], balanced.resources["bram_kbits"]
        )
        self.assertGreater(
            dram_offload.dram["est_dram_bytes"], balanced.dram["est_dram_bytes"]
        )
        self.assertTrue(dram_offload.dram["dram_required"])

    def test_specialization_reduces_estimated_area(self) -> None:
        uniform = _candidate_by_id(self.report, "g4x3_uniform_balanced")
        column = _candidate_by_id(self.report, "g4x3_heavy_column_balanced")
        self.assertLess(column.resources["luts"], uniform.resources["luts"])
        self.assertLess(
            column.resources["dsp_blocks"], uniform.resources["dsp_blocks"]
        )

    def test_max_candidates_cap(self) -> None:
        capped = run_arch_search(DEMO_CONFIG, max_candidates=5)
        self.assertEqual(len(capped.candidates), 5)


class ArchSearchCwWorkloadTests(unittest.TestCase):
    def test_cw_compiled_config_evaluates_all_candidates(self) -> None:
        report = run_arch_search(CW_CONFIG)
        self.assertGreater(len(report.candidates), 0)
        # Placement pins from CW lowering are stripped, so every candidate
        # must compile+schedule (or fail with a recorded reason, never raise).
        for cand in report.candidates:
            if cand.feasible:
                self.assertGreater(cand.metrics["makespan_cycles"], 0)
            else:
                self.assertIsNotNone(cand.infeasible_reason)


class CandidatePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_payload = json.loads(DEMO_CONFIG.read_text())

    def _knobs(self, **overrides) -> CandidateKnobs:
        defaults = dict(
            grid_x=4,
            grid_y=3,
            op_distribution="uniform",
            memory_split="balanced",
            local_ram_depth=128,
            global_ram_depth=2048,
            station_cache_entries=4,
        )
        defaults.update(overrides)
        return CandidateKnobs(**defaults)

    def test_placements_stripped_and_entry_clamped(self) -> None:
        knobs = self._knobs(grid_x=12, grid_y=1)
        payload = build_candidate_payload(
            self.base_payload, knobs, heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        for flow in payload["flows"]:
            self.assertLess(flow["entry"]["y"], 1)
            for node in flow.get("nodes", []):
                for key in ("placement", "core", "fallback_core", "candidate_cores"):
                    self.assertNotIn(key, node)

    def test_uniform_distribution_clears_capabilities(self) -> None:
        payload = build_candidate_payload(
            self.base_payload, self._knobs(), heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        self.assertEqual(payload["compiler"]["core_capabilities"], [])

    def test_heavy_column_restricts_non_column_cores(self) -> None:
        knobs = self._knobs(op_distribution="heavy_column")
        payload = build_candidate_payload(
            self.base_payload, knobs, heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        entries = payload["compiler"]["core_capabilities"]
        # Restricted rows exist for every core off column 0, none on column 0.
        restricted_cores = {(e["core"]["x"], e["core"]["y"]) for e in entries}
        self.assertEqual(
            restricted_cores,
            {(x, y) for y in range(3) for x in range(4) if x != 0},
        )
        for entry in entries:
            self.assertEqual(sorted(entry["operations"]), sorted(DEMO_LIGHT))

    def test_manual_routing_downgraded_to_waterfall(self) -> None:
        payload = copy.deepcopy(self.base_payload)
        payload["compiler"]["routing"] = "manual"
        out = build_candidate_payload(
            payload, self._knobs(), heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        self.assertEqual(out["compiler"]["routing"], "waterfall")

    def test_memory_split_knobs_applied_to_device(self) -> None:
        knobs = self._knobs(
            memory_split="local_heavy",
            local_ram_depth=256,
            global_ram_depth=1024,
            station_cache_entries=16,
        )
        payload = build_candidate_payload(
            self.base_payload, knobs, heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        self.assertEqual(payload["device"]["local_ram_depth"], 256)
        self.assertEqual(payload["device"]["global_ram_depth"], 1024)
        self.assertEqual(payload["compiler"]["station_cache"]["entries"], 16)


class ArchSearchCliTests(unittest.TestCase):
    def test_cli_writes_report_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "report.json"
            summary_path = Path(td) / "summary.txt"
            rc = waugen_main(
                [
                    "arch-search",
                    "--config",
                    str(DEMO_CONFIG),
                    "--out-report",
                    str(report_path),
                    "--out-summary",
                    str(summary_path),
                    "--max-candidates",
                    "8",
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(report_path.read_text())
            self.assertEqual(payload["schema"], "wau_arch_search_v1")
            self.assertEqual(payload["ranking"], "arch_search_rank_v1")
            self.assertEqual(payload["candidate_count"], 8)
            self.assertEqual(len(payload["candidates"]), 8)
            self.assertTrue(summary_path.read_text().strip())

    def test_cli_rejects_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = waugen_main(
                [
                    "arch-search",
                    "--config",
                    str(Path(td) / "missing.json"),
                    "--out-report",
                    str(Path(td) / "report.json"),
                ]
            )
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
