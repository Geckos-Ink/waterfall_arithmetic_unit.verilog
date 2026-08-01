# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for the viewer's per-link data-plane packet trace.

The pipelines viewer previously captured per-core dispatch/result plus aggregate
counters, but no per-link/per-core *data movement*. The trace TB now emits a
``ddeliv=...`` record whenever a result packet is delivered to a core over the
data mesh (src core -> dst core), which the graph view animates as a moving data
packet, and one ``HWY`` record per highway *line* carrying that line's
contracting bus -- which slot it is offering, which core is calling the highway,
and who holds it under a contract. Because the default topology gives every line
its own bus, slot/grant ids are line-local and resolve to global cores through
the line base. These tests cover the parser unconditionally and the full
iverilog-driven trace when the toolchain is available.
"""
from __future__ import annotations

import shutil
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER_ROOT = REPO_ROOT / "tools" / "wau-pipelines-viewer"
sys.path.insert(0, str(VIEWER_ROOT))

from wau_viewer.trace_parser import derive_bottlenecks, parse_trace  # noqa: E402


_SYNTHETIC_TRACE = """# wau-pipelines-viewer trace v1
META grid_x=3 grid_y=2 core_count=6 stim_count=1 line_count=2 line_size=3
@CYCLE 5
HOST_IN v=0 r=1 flow=0 a=0 b=0
HOST_OUT v=0 r=1 flow=0 val=0
CORE 0 busy=0 cache_hit=0 ddeliv=1 ddeliv_src=2 ddeliv_val=38 ddeliv_flow=1 ddeliv_stage=2 hwy_req=0 hwy_call=0 hwy_hold=0 cache_h_count=0 cache_l_count=1
CORE 1 busy=1 cache_hit=0 disp=1 disp_op=3 disp_a=14 disp_b=3 disp_imm=0 disp_use_imm=1 disp_stage=1 disp_flow=1 hwy_req=1 hwy_call=1 hwy_hold=0 cache_h_count=0 cache_l_count=0
CORE 2 busy=0 cache_hit=0 hwy_req=1 hwy_call=0 hwy_hold=1 cache_h_count=0 cache_l_count=0
OBS hops=4 stalls=0 forwards=2 deliv=1 cache_h=0 cache_l=1
HWY line=0 slot=1 grant=0 gcore=0 gmode=0 grem=0 grants=3 hold=9 defer=4
HWY line=1 slot=0 grant=1 gcore=2 gmode=2 grem=5 grants=3 hold=9 defer=4
@END total_cycles=5 outputs_seen=1
"""


class DataTraceParserTests(unittest.TestCase):
    def test_parses_data_delivery_record(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(_SYNTHETIC_TRACE)
            trace = parse_trace(p)

        snap = trace.cycles[0]
        core0 = next(c for c in snap.cores if c.core_index == 0)
        self.assertTrue(core0.data_delivered)
        self.assertEqual(core0.ddeliv_src, 2)
        self.assertEqual(core0.ddeliv_value, 38)
        self.assertEqual(core0.ddeliv_flow_id, 1)
        self.assertEqual(core0.ddeliv_stage_id, 2)

        core1 = next(c for c in snap.cores if c.core_index == 1)
        self.assertFalse(core1.data_delivered)
        self.assertTrue(core1.dispatched)

    def test_parses_fast_path_delivery_fields(self) -> None:
        import tempfile

        trace_text = (
            "# wau-pipelines-viewer trace v1\n"
            "META grid_x=3 grid_y=2 core_count=6 stim_count=1 line_count=2 line_size=3\n"
            "@CYCLE 5\n"
            "HOST_IN v=0 r=1 flow=0 a=0 b=0\n"
            "HOST_OUT v=0 r=1 flow=0 val=0\n"
            "CORE 0 busy=0 cache_hit=0 ddeliv=1 ddeliv_src=1 ddeliv_val=14 ddeliv_flow=1"
            " ddeliv_stage=0 ddeliv_fastpath=1 ddeliv_next_stage=1 ddeliv_next_op=3"
            " hwy_req=0 hwy_call=0 hwy_hold=0 cache_h_count=0 cache_l_count=1\n"
            "CORE 1 busy=0 cache_hit=0 ddeliv=1 ddeliv_src=2 ddeliv_val=38 ddeliv_flow=1"
            " ddeliv_stage=2 ddeliv_fastpath=0"
            " hwy_req=0 hwy_call=0 hwy_hold=0 cache_h_count=0 cache_l_count=0\n"
            "OBS hops=4 stalls=0 forwards=2 deliv=2 cache_h=0 cache_l=1\n"
            "@END total_cycles=5 outputs_seen=1\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(trace_text)
            trace = parse_trace(p)

        snap = trace.cycles[0]
        core0 = next(c for c in snap.cores if c.core_index == 0)
        self.assertTrue(core0.data_delivered)
        self.assertTrue(core0.ddeliv_fastpath)
        self.assertEqual(core0.ddeliv_next_stage, 1)
        self.assertEqual(core0.ddeliv_next_op, 3)

        # A legacy (non-fast-path) delivery must not report next-stage fields.
        core1 = next(c for c in snap.cores if c.core_index == 1)
        self.assertTrue(core1.data_delivered)
        self.assertFalse(core1.ddeliv_fastpath)
        self.assertIsNone(core1.ddeliv_next_stage)
        self.assertIsNone(core1.ddeliv_next_op)

    def test_parses_highway_contract_bus_records(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(_SYNTHETIC_TRACE)
            trace = parse_trace(p)

        snap = trace.cycles[0]
        hwy = snap.highway
        # One record per independent highway line.
        self.assertEqual([line.line for line in hwy.lines], [0, 1])
        self.assertEqual((hwy.grant_count, hwy.hold_cycles, hwy.defer_count), (3, 9, 4))

        # Line 0 is merely offering a slot; nobody holds it.
        line0 = hwy.lines[0]
        self.assertEqual(line0.slot, 1)
        self.assertFalse(line0.grant_valid)
        # Line-local ids resolve to global core indices through the line base.
        self.assertEqual(line0.slot_core, 1)

        # Line 1 has a holder, and its line-local id 2 is global core 5.
        line1 = hwy.lines[1]
        self.assertTrue(line1.grant_valid)
        self.assertEqual(line1.grant_core, 2)
        self.assertEqual(line1.grant_core_index, 5)
        self.assertEqual(line1.grant_mode, 2)  # stream
        self.assertEqual(line1.grant_remaining, 5)
        self.assertTrue(hwy.any_granted)

        # A core is governed by its own line's bus, never another's.
        self.assertIs(hwy.line_for_core(4, line_size=3), line1)
        self.assertIs(hwy.line_for_core(1, line_size=3), line0)

        by_index = {c.core_index: c for c in snap.cores}
        # Core 1 called the highway from its own offered slot...
        self.assertTrue(by_index[1].highway_request)
        self.assertTrue(by_index[1].highway_call)
        self.assertFalse(by_index[1].highway_holder)
        # ...while core 2 is the one actually holding it under a contract.
        self.assertTrue(by_index[2].highway_holder)
        self.assertFalse(by_index[2].highway_call)
        # A quiet core touches nothing.
        self.assertFalse(by_index[0].highway_request)

    def test_highway_stats_reach_the_bottleneck_summary(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(_SYNTHETIC_TRACE)
            trace = parse_trace(p)

        stats = derive_bottlenecks(trace)
        self.assertEqual(stats["active_core_count"], 1)
        self.assertEqual(stats["peak_busy_cores"], 1)
        self.assertAlmostEqual(stats["average_busy_cores"], 1.0)
        self.assertAlmostEqual(stats["fabric_busy_ratio"], 1.0 / 6.0)
        self.assertEqual(stats["highway_call_count"][1], 1)
        self.assertEqual(stats["highway_call_count"][0], 0)
        self.assertEqual(stats["highway_held_cycles"], 1)
        self.assertEqual(stats["highway_grant_count"], 3)
        self.assertEqual(stats["highway_defer_count"], 4)

    def test_trace_without_highway_records_still_parses(self) -> None:
        """Older/contract-free traces must not break the parser."""
        import tempfile

        legacy = "\n".join(
            line
            for line in _SYNTHETIC_TRACE.splitlines()
            if not line.startswith("HWY")
        ) + "\n"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(legacy)
            trace = parse_trace(p)

        self.assertEqual(trace.cycles[0].highway.lines, [])
        self.assertFalse(trace.cycles[0].highway.any_granted)

    def test_profiles_operations_from_real_dispatch_records(self) -> None:
        import tempfile

        from wau_viewer.simulator import SimulationResult, profile_core_operations

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.log"
            p.write_text(_SYNTHETIC_TRACE)
            result = SimulationResult(Path(td), p, "", "")
            profile = profile_core_operations(result, {3: "mul"})

        self.assertEqual(profile[0], ())
        self.assertEqual(profile[1], ("mul",))
        self.assertEqual(profile[5], ())


class DataTraceEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("iverilog") is None or shutil.which("vvp") is None:
            self.skipTest("iverilog/vvp not available")
        self.rtl_dir = REPO_ROOT / "src" / "verilog" / "generated"
        if not (self.rtl_dir / "wau_top.v").exists():
            self.skipTest("generated RTL not present (run `waugen generate`)")

    def test_data_movement_is_captured_from_real_rtl(self) -> None:
        from wau_viewer.simulator import IverilogRunner

        program = json.loads((self.rtl_dir / "wau_program.json").read_text())
        flow_ids = [int(flow["flow_id"]) for flow in program["flows"][:2]]
        self.assertEqual(len(flow_ids), 2)
        runner = IverilogRunner(self.rtl_dir)
        res = runner.run(flow_ids, [10, 9], [4, 3], max_cycles=400)
        trace = parse_trace(res.trace_path)

        deliveries = [
            c for snap in trace.cycles for c in snap.cores if c.data_delivered
        ]
        # The demo flows both return results to the coordinator, so we must see
        # at least one data-plane delivery with a real source core + value.
        self.assertTrue(deliveries, "no data-plane deliveries captured from RTL")
        self.assertTrue(any(c.ddeliv_value not in (None, 0) for c in deliveries))

    def test_fast_path_delivery_is_captured_from_real_rtl(self) -> None:
        """`compiler.station_program.enabled=true` (see
        src/python/configs/wau_station_program_demo.json, proven end-to-end
        by tests/rtl/tb_wau_core_fast_path_overlap.v) must show up in the
        viewer's own trace, not just in a dedicated RTL testbench."""
        import tempfile

        from waugen.compiler import compile_project
        from waugen.config import load_config
        from waugen.scheduler import build_schedule
        from waugen.verilog_emit import emit_verilog
        from wau_viewer.simulator import IverilogRunner

        config_path = REPO_ROOT / "src/python/configs/wau_station_program_demo.json"
        with tempfile.TemporaryDirectory() as td:
            rtl_dir = Path(td)
            project = compile_project(load_config(config_path))
            schedule = build_schedule(project)
            emit_verilog(project, schedule, rtl_dir)

            # Flow 1 "accumulate_and_scale": (0,0)->(1,0)->(2,0), both interior
            # transitions have a fast-path entry (see wau_program.json's
            # station_program section for this config).
            runner = IverilogRunner(rtl_dir)
            res = runner.run([1], [10], [4], max_cycles=400)
            trace = parse_trace(res.trace_path)

        fast_path_deliveries = [
            c
            for snap in trace.cycles
            for c in snap.cores
            if c.data_delivered and c.ddeliv_fastpath
        ]
        self.assertTrue(
            fast_path_deliveries, "no fast-path delivery captured from real RTL"
        )
        self.assertTrue(
            any(c.ddeliv_next_stage is not None for c in fast_path_deliveries)
        )

    def test_highway_calls_are_captured_from_real_rtl(self) -> None:
        from wau_viewer.simulator import IverilogRunner

        program = json.loads((self.rtl_dir / "wau_program.json").read_text())
        if not program["device"].get("highway", {}).get("contract_bus", False):
            self.skipTest("generated RTL has the contract bus disabled")
        flow_ids = [int(flow["flow_id"]) for flow in program["flows"][:2]]
        runner = IverilogRunner(self.rtl_dir)
        res = runner.run(flow_ids, [10, 9], [4, 3], max_cycles=400)
        trace = parse_trace(res.trace_path)

        # Every core that produces a result must be seen asking for the highway,
        # otherwise the viewer's "core calls a highway" indicator is decorative.
        requesting = {
            c.core_index
            for snap in trace.cycles
            for c in snap.cores
            if c.highway_request
        }
        producing = {
            c.core_index for snap in trace.cycles for c in snap.cores if c.has_result
        }
        self.assertTrue(producing, "no core produced a result")
        self.assertTrue(producing <= requesting)

        # Every line must run its own bus, and each must actually cycle its
        # slot rather than sitting on one core.
        self.assertEqual(
            {len(snap.highway.lines) for snap in trace.cycles},
            {trace.meta.line_count},
        )
        for line_index in range(trace.meta.line_count):
            offered = {
                line.slot
                for snap in trace.cycles
                for line in snap.highway.lines
                if line.line == line_index
            }
            self.assertGreater(
                len(offered), 1, f"line {line_index} never advanced its slot"
            )

    def test_4x4_demo_exercises_every_core_with_tagged_pipeline_overlap(self) -> None:
        import tempfile

        from waugen.compiler import compile_project
        from waugen.config import load_config
        from waugen.scheduler import build_schedule
        from waugen.verilog_emit import emit_verilog
        from wau_viewer.simulator import IverilogRunner

        config_path = (
            REPO_ROOT
            / "tools/wau-pipelines-viewer/examples/wau_4x4_pipeline_demo.json"
        )
        with tempfile.TemporaryDirectory() as td:
            rtl_dir = Path(td)
            project = compile_project(load_config(config_path))
            emit_verilog(project, build_schedule(project), rtl_dir)
            flow_ids = [1, 2, 3, 4] * 4
            operands = list(range(10, 26))
            result = IverilogRunner(rtl_dir).run(
                flow_ids, operands, list(reversed(operands)), max_cycles=500
            )
            trace = parse_trace(result.trace_path)

        stats = derive_bottlenecks(trace)
        self.assertEqual(trace.meta.outputs_seen, 16)
        self.assertEqual(stats["active_core_count"], 16)
        self.assertGreaterEqual(stats["peak_busy_cores"], 8)
        self.assertTrue(all(count > 0 for count in stats["core_dispatch_count"]))

        flow1_tags = {
            core.disp_tag
            for snapshot in trace.cycles
            for core in snapshot.cores
            if core.dispatched and core.disp_flow_id == 1
        }
        self.assertGreaterEqual(len(flow1_tags), 4)


if __name__ == "__main__":
    unittest.main()
