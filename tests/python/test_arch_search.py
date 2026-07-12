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
    FitBudget,
    _fit_max_in_flight_values,
    _fits_budget,
    _knobs_from_candidate_id,
    _rank_key,
    build_candidate_payload,
    emit_fit_config,
    run_arch_search,
    run_fit_search,
)
from waugen.cli import main as waugen_main
from waugen.compiler import compile_project
from waugen.config import load_config_obj
from waugen.scheduler import build_schedule
from waugen.verilog_emit import emit_verilog

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
        cand = _candidate_by_id(self.report, "g4x3x1_uniform_balanced")
        self.assertTrue(cand.feasible)
        self.assertGreater(cand.metrics["makespan_cycles"], 0)
        self.assertEqual(
            cand.metrics["estimated_transfer_hops_metric"], "dependency_edges_v1"
        )
        self.assertGreater(cand.resources["luts"], 0)
        self.assertGreater(cand.resources["bram_kbits"], 0)

    def test_arch_search_never_sweeps_max_in_flight(self) -> None:
        # arch-search reshapes a fixed core count and must stay byte-identical:
        # it never co-sweeps coordinator.max_in_flight, so no candidate id may
        # carry the fit-only `_mif` suffix nor expose it in the knobs JSON.
        for cand in self.report.candidates:
            self.assertIsNone(cand.knobs.max_in_flight)
            self.assertNotIn("_mif", cand.knobs.candidate_id)
            self.assertNotIn("max_in_flight", cand.to_json()["knobs"])

    def test_grid_shapes_preserve_core_count(self) -> None:
        core_count = self.report.base_grid_x * self.report.base_grid_y * self.report.base_grid_z
        for cand in self.report.candidates:
            self.assertEqual(
                cand.knobs.grid_x * cand.knobs.grid_y * cand.knobs.grid_z,
                core_count,
            )

    def test_memory_split_estimate_relations(self) -> None:
        balanced = _candidate_by_id(self.report, "g4x3x1_uniform_balanced")
        local_heavy = _candidate_by_id(self.report, "g4x3x1_uniform_local_heavy")
        dram_offload = _candidate_by_id(self.report, "g4x3x1_uniform_dram_offload")

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
        uniform = _candidate_by_id(self.report, "g4x3x1_uniform_balanced")
        column = _candidate_by_id(self.report, "g4x3x1_heavy_column_balanced")
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
            grid_z=1,
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
        restricted_cores = {(e["core"]["x"], e["core"]["y"], e["core"].get("z", 0)) for e in entries}
        self.assertEqual(
            restricted_cores,
            {(x, y, 0) for y in range(3) for x in range(4) if x != 0},
        )
        for entry in entries:
            self.assertEqual(sorted(entry["operations"]), sorted(DEMO_LIGHT))

    def test_profiled_distribution_emits_exact_per_core_alus(self) -> None:
        knobs = self._knobs(op_distribution="profiled")
        payload = build_candidate_payload(
            self.base_payload, knobs, heavy_ops=DEMO_HEAVY, light_ops=DEMO_LIGHT
        )
        entries = payload["compiler"]["core_capabilities"]
        self.assertEqual(len(entries), knobs.grid_x * knobs.grid_y * knobs.grid_z)
        self.assertTrue(all(entry["operations"] for entry in entries))

        config = load_config_obj(payload)
        project = compile_project(config)
        schedule = build_schedule(project)
        with tempfile.TemporaryDirectory() as td:
            emit_verilog(project, schedule, Path(td))
            alu = (Path(td) / "wau_operation_alu.v").read_text()
            core = (Path(td) / "wau_core.v").read_text()
        self.assertIn("parameter integer CORE_INDEX", alu)
        self.assertIn("CORE_INDEX < 0", alu)
        self.assertIn("CORE_INDEX ==", alu)
        self.assertIn(".CORE_INDEX(CORE_INDEX)", core)

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


def _de0_budget(**overrides) -> FitBudget:
    defaults = dict(
        max_grid_x=2,
        max_grid_y=4,
        max_grid_z=1,
        max_cores=8,
        lut_budget=22320,
        max_utilization=0.9,
    )
    defaults.update(overrides)
    return FitBudget(**defaults)


class FitSearchTests(unittest.TestCase):
    """The fit finder must sweep grid *sizes* (not a fixed core count) up to a
    device budget, predict behaviour via the real scheduler, and recommend both
    a best-performance and a fewest-cores (knee) config that fit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_fit_search(CW_CONFIG, budget=_de0_budget())

    def test_report_is_deterministic(self) -> None:
        again = run_fit_search(CW_CONFIG, budget=_de0_budget())
        self.assertEqual(self.report.to_json(), again.to_json())

    def test_sweeps_variable_core_counts_within_box(self) -> None:
        core_counts = {
            c.knobs.grid_x * c.knobs.grid_y * c.knobs.grid_z for c in self.report.candidates
        }
        # A fixed-core-count reshape (arch-search) would give a single value;
        # the fit sweep must span from 1 core up to the budget.
        self.assertGreater(len(core_counts), 1)
        self.assertIn(1, core_counts)
        self.assertLessEqual(max(core_counts), 8)
        for cand in self.report.candidates:
            self.assertLessEqual(cand.knobs.grid_x, 2)
            self.assertLessEqual(cand.knobs.grid_y, 4)
            self.assertLessEqual(cand.knobs.grid_z, 1)

    def test_recommendations_present_and_coherent(self) -> None:
        self.assertIsNotNone(self.report.best_id)
        self.assertIsNotNone(self.report.efficient_id)
        best = _candidate_by_id(self.report, self.report.best_id)
        eff = _candidate_by_id(self.report, self.report.efficient_id)
        best_cores = best.knobs.grid_x * best.knobs.grid_y * best.knobs.grid_z
        eff_cores = eff.knobs.grid_x * eff.knobs.grid_y * eff.knobs.grid_z
        # The efficient/knee pick uses no more cores than the best performer...
        self.assertLessEqual(eff_cores, best_cores)
        # ...and stays within tolerance of the best makespan.
        self.assertLessEqual(
            eff.metrics["makespan_cycles"],
            best.metrics["makespan_cycles"] * (1.0 + self.report.tolerance),
        )
        self.assertTrue(_fits_budget(best, self.report.budget))
        self.assertTrue(_fits_budget(eff, self.report.budget))

    def test_tight_lut_budget_prunes_larger_grids(self) -> None:
        tight = run_fit_search(CW_CONFIG, budget=_de0_budget(lut_budget=2600))
        eligible = [c for c in tight.candidates if _fits_budget(c, tight.budget)]
        self.assertTrue(all(c.resources["luts"] <= 2600 for c in eligible))
        # The best pick under a tight budget must itself fit the budget.
        if tight.best_id is not None:
            self.assertTrue(
                _fits_budget(_candidate_by_id(tight, tight.best_id), tight.budget)
            )

    def test_impossible_budget_yields_no_recommendation(self) -> None:
        impossible = run_fit_search(CW_CONFIG, budget=_de0_budget(lut_budget=10))
        self.assertIsNone(impossible.best_id)
        self.assertIsNone(impossible.efficient_id)

    def test_emit_fit_config_rebuilds_chosen_grid(self) -> None:
        eff = _candidate_by_id(self.report, self.report.efficient_id)
        payload = emit_fit_config(CW_CONFIG, self.report.efficient_id)
        self.assertEqual(payload["device"]["grid"]["x"], eff.knobs.grid_x)
        self.assertEqual(payload["device"]["grid"]["y"], eff.knobs.grid_y)


class FitMaxInFlightSweepTests(unittest.TestCase):
    """The fit finder co-sweeps coordinator.max_in_flight so it can keep the
    cheapest in-flight depth that does not cost makespan (unused slots cost
    LUTs), while never dragging a workload above its concurrency ceiling."""

    def test_depth_values_bounded_by_flow_count(self) -> None:
        # A single-flow workload gets a single, cheapest depth: mif=1 is
        # cycle-identical there, so higher depths would only burn LUTs.
        self.assertEqual(_fit_max_in_flight_values(base_mif=4, num_flows=1), (1,))
        # More flows unlock more concurrency; base value + powers of two + the
        # cap are all offered, deduplicated and sorted.
        self.assertEqual(_fit_max_in_flight_values(base_mif=4, num_flows=4), (1, 2, 4))
        self.assertEqual(_fit_max_in_flight_values(base_mif=2, num_flows=3), (1, 2, 3))
        # Never exceed the schema ceiling of 16.
        self.assertEqual(max(_fit_max_in_flight_values(base_mif=16, num_flows=64)), 16)

    def test_report_records_swept_depths(self) -> None:
        # CW_CONFIG has 4 flows, so the auto sweep spans {1, 2, 4}.
        report = run_fit_search(CW_CONFIG, budget=_de0_budget())
        self.assertEqual(report.max_in_flight_swept, (1, 2, 4))
        self.assertEqual(report.to_json()["max_in_flight_swept"], [1, 2, 4])
        depths = {c.knobs.max_in_flight for c in report.candidates}
        self.assertEqual(depths, {1, 2, 4})
        # Every fit candidate id carries its depth and round-trips.
        for cand in report.candidates:
            self.assertIn(f"_mif{cand.knobs.max_in_flight}", cand.knobs.candidate_id)

    def test_lower_depth_never_costs_more_luts(self) -> None:
        # For a fixed grid/op/memory shape, a smaller in-flight depth must
        # estimate no more LUTs than a larger one (the reclaim the sweep buys).
        report = run_fit_search(CW_CONFIG, budget=_de0_budget())
        by_shape: dict[str, dict[int, int]] = {}
        for cand in report.candidates:
            k = cand.knobs
            shape = f"g{k.grid_x}x{k.grid_y}x{k.grid_z}_{k.op_distribution}_{k.memory_split}"
            by_shape.setdefault(shape, {})[k.max_in_flight] = cand.resources["luts"]
        checked = 0
        for depths in by_shape.values():
            for depth, luts in depths.items():
                for other, other_luts in depths.items():
                    if depth < other:
                        self.assertLessEqual(luts, other_luts)
                        checked += 1
        self.assertGreater(checked, 0)

    def test_explicit_override_respected(self) -> None:
        report = run_fit_search(
            CW_CONFIG, budget=_de0_budget(), max_in_flight_values=(1, 8)
        )
        self.assertEqual(report.max_in_flight_swept, (1, 8))
        self.assertEqual({c.knobs.max_in_flight for c in report.candidates}, {1, 8})

    def test_emit_fit_config_applies_swept_depth(self) -> None:
        report = run_fit_search(CW_CONFIG, budget=_de0_budget())
        eff = _candidate_by_id(report, report.efficient_id)
        payload = emit_fit_config(CW_CONFIG, report.efficient_id)
        self.assertEqual(
            payload["coordinator"]["max_in_flight"], eff.knobs.max_in_flight
        )
        # And the id parser recovers the depth from the id alone.
        parsed = _knobs_from_candidate_id(
            json.loads(CW_CONFIG.read_text()), report.efficient_id
        )
        self.assertEqual(parsed.max_in_flight, eff.knobs.max_in_flight)


class FitConfigCliTests(unittest.TestCase):
    def test_cli_fits_cw_program_and_emits_buildable_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "report.json"
            out_config = Path(td) / "best.json"
            rc = waugen_main(
                [
                    "fit-config",
                    "--program-file",
                    "CWs/stress/mesh_stress.cw",
                    "--device",
                    "intel_de0_nano",
                    "--max-grid",
                    "2x4",
                    "--out-report",
                    str(report_path),
                    "--out-config",
                    str(out_config),
                    "--emit",
                    "efficient",
                ]
            )
            self.assertEqual(rc, 0)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["schema"], "wau_fit_search_v1")
            self.assertIsNotNone(report["efficient_candidate"])
            # The emitted config must round-trip through the real validate path.
            emitted = json.loads(out_config.read_text())
            self.assertIn("device", emitted)
            self.assertGreaterEqual(len(emitted["flows"]), 1)
            vrc = waugen_main(["validate", "--config", str(out_config)])
            self.assertEqual(vrc, 0)

    def test_cli_config_input_quick_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = waugen_main(
                [
                    "fit-config",
                    "--config",
                    str(CW_CONFIG),
                    "--max-grid",
                    "2x4",
                    "--quick",
                    "--out-report",
                    str(Path(td) / "r.json"),
                ]
            )
            self.assertEqual(rc, 0)

    def test_cli_max_in_flight_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "r.json"
            rc = waugen_main(
                [
                    "fit-config",
                    "--config",
                    str(CW_CONFIG),
                    "--max-grid",
                    "2x4",
                    "--quick",
                    "--max-in-flight",
                    "1,3",
                    "--out-report",
                    str(report_path),
                ]
            )
            self.assertEqual(rc, 0)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["max_in_flight_swept"], [1, 3])

    def test_cli_emits_exact_candidate_for_hardware_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_config = Path(td) / "exact.json"
            candidate = "g2x4x1_profiled_balanced_mif1"
            rc = waugen_main(
                [
                    "fit-config", "--config", str(CW_CONFIG),
                    "--max-grid", "2x4", "--candidate-id", candidate,
                    "--out-config", str(out_config),
                ]
            )
            self.assertEqual(rc, 0)
            emitted = json.loads(out_config.read_text())
            self.assertEqual(emitted["device"]["grid"], {"x": 2, "y": 4, "z": 1})
            self.assertEqual(len(emitted["compiler"]["core_capabilities"]), 8)

    def test_cli_rejects_invalid_max_in_flight(self) -> None:
        rc = waugen_main(
            [
                "fit-config",
                "--config",
                str(CW_CONFIG),
                "--max-grid",
                "1x1",
                "--quick",
                "--max-in-flight",
                "two",
            ]
        )
        self.assertEqual(rc, 2)


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
