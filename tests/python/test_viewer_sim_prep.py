# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for the viewer's simulator-side helpers.

Covers the ad-hoc circuit preparation path (config/.cw -> waugen -> RTL), the
seeded stress-stimulus generator that interleaves flow ids to exercise the
multi-issue coordinator, the dimension-order route reconstruction used by the
hop-by-hop packet animation, and the dynamic RTL source discovery. All of
these are Qt-free so they run headless in CI.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER_ROOT = REPO_ROOT / "tools" / "wau-pipelines-viewer"
sys.path.insert(0, str(VIEWER_ROOT))

from wau_viewer.model import (  # noqa: E402
    FlowStageInfo,
    WauModel,
    derive_stress_stimulus,
    manhattan_route,
)
from wau_viewer.simulator import (  # noqa: E402
    REQUIRED_RTL_MODULES,
    collect_rtl_sources,
)


def _make_model(flow_ids) -> WauModel:
    flows = [
        FlowStageInfo(
            flow_id=fid,
            flow_name=f"flow_{fid}",
            stage_index=0,
            op="add",
            opcode=1,
            latency=1,
            primary_core=(0, 0),
            fallback_core=None,
        )
        for fid in flow_ids
    ]
    return WauModel(
        grid_x=3,
        grid_y=3,
        core_count=9,
        cores=[],
        opcode_to_name={1: "add"},
        flow_id_to_name={fid: f"flow_{fid}" for fid in flow_ids},
        flows=flows,
        schedule=[],
        makespan_cycles=0,
    )


class ManhattanRouteTests(unittest.TestCase):
    def test_route_is_x_first_then_y(self) -> None:
        # 3x3 grid: core 0 = (0,0), core 8 = (2,2). The generated
        # wau_highway_router resolves EAST/WEST before SOUTH/NORTH.
        self.assertEqual(manhattan_route(3, 3, 0, 8), [0, 1, 2, 5, 8])

    def test_route_westward_and_northward(self) -> None:
        self.assertEqual(manhattan_route(3, 3, 8, 0), [8, 7, 6, 3, 0])

    def test_route_same_core_is_single_point(self) -> None:
        self.assertEqual(manhattan_route(3, 3, 4, 4), [4])

    def test_route_length_matches_manhattan_distance(self) -> None:
        for src in range(9):
            for dst in range(9):
                route = manhattan_route(3, 3, src, dst)
                dist = abs(src % 3 - dst % 3) + abs(src // 3 - dst // 3)
                self.assertEqual(len(route), dist + 1)
                self.assertEqual(route[0], src)
                self.assertEqual(route[-1], dst)
                # every hop is a mesh neighbor
                for a, b in zip(route, route[1:]):
                    hop = abs(a % 3 - b % 3) + abs(a // 3 - b // 3)
                    self.assertEqual(hop, 1)


class StressStimulusTests(unittest.TestCase):
    def test_deterministic_for_fixed_seed(self) -> None:
        model = _make_model([1, 2, 3])
        a = derive_stress_stimulus(model, 12, seed=42)
        b = derive_stress_stimulus(model, 12, seed=42)
        self.assertEqual(a, b)
        self.assertNotEqual(a, derive_stress_stimulus(model, 12, seed=43))

    def test_interleaves_flow_ids_round_robin(self) -> None:
        model = _make_model([1, 2, 3])
        stim = derive_stress_stimulus(model, 7, seed=1)
        self.assertEqual([s[0] for s in stim], [1, 2, 3, 1, 2, 3, 1])
        # consecutive packets always target different flows (what actually
        # produces multi-issue overlap: host_in_ready blocks same-flow reuse)
        for prev, cur in zip(stim, stim[1:]):
            self.assertNotEqual(prev[0], cur[0])

    def test_operands_respect_bounds(self) -> None:
        model = _make_model([5])
        stim = derive_stress_stimulus(model, 50, seed=3, value_min=2, value_max=9)
        for _, a, b in stim:
            self.assertGreaterEqual(min(a, b), 2)
            self.assertLessEqual(max(a, b), 9)

    def test_rejects_non_positive_count(self) -> None:
        with self.assertRaises(ValueError):
            derive_stress_stimulus(_make_model([1]), 0)


class CollectRtlSourcesTests(unittest.TestCase):
    def _touch_required(self, rtl_dir: Path) -> None:
        for name in REQUIRED_RTL_MODULES:
            (rtl_dir / name).write_text("// stub\n")

    def test_missing_required_module_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rtl_dir = Path(td)
            self._touch_required(rtl_dir)
            (rtl_dir / "wau_top.v").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "wau_top.v"):
                collect_rtl_sources(rtl_dir)

    def test_extra_modules_included_board_wrappers_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rtl_dir = Path(td)
            self._touch_required(rtl_dir)
            (rtl_dir / "wau_future_mesh_ext.v").write_text("// stub\n")
            (rtl_dir / "wau_de0_nano_top.v").write_text("// board\n")
            (rtl_dir / "wau_host_mmio.v").write_text("// glue\n")
            names = [p.name for p in collect_rtl_sources(rtl_dir)]
            self.assertIn("wau_future_mesh_ext.v", names)
            self.assertNotIn("wau_de0_nano_top.v", names)
            self.assertNotIn("wau_host_mmio.v", names)
            for required in REQUIRED_RTL_MODULES:
                self.assertIn(required, names)


class PrepareCircuitTests(unittest.TestCase):
    """End-to-end: viewer-driven ad-hoc circuit generation via waugen."""

    def setUp(self) -> None:
        if not (REPO_ROOT / "src" / "python" / "waugen").is_dir():
            self.skipTest("waugen package not present")

    def test_prepare_from_config_generates_rtl(self) -> None:
        from wau_viewer.prepare import find_repo_root, prepare_circuit

        self.assertEqual(find_repo_root(Path(__file__)), REPO_ROOT)
        config = VIEWER_ROOT / "examples" / "wau_3x3_demo.json"
        with tempfile.TemporaryDirectory() as td:
            prepared = prepare_circuit(build_dir=Path(td), config=config)
            self.assertTrue(prepared.program_path.exists())
            self.assertTrue(prepared.schedule_path.exists())
            self.assertTrue((prepared.rtl_dir / "wau_top.v").exists())
            # the generated artifacts must satisfy the simulator's manifest
            sources = collect_rtl_sources(prepared.rtl_dir)
            self.assertTrue(sources)

    def test_prepare_requires_exactly_one_source(self) -> None:
        from wau_viewer.prepare import prepare_circuit

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                prepare_circuit(build_dir=Path(td))
            with self.assertRaises(ValueError):
                prepare_circuit(
                    build_dir=Path(td),
                    config=Path("a.json"),
                    cw_program=Path("b.cw"),
                )


if __name__ == "__main__":
    unittest.main()
