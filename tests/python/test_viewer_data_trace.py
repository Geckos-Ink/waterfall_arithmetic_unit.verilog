# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for the viewer's per-link data-plane packet trace.

The pipelines viewer previously captured per-core dispatch/result plus aggregate
counters, but no per-link/per-core *data movement*. The trace TB now emits a
``ddeliv=...`` record whenever a result packet is delivered to a core over the
data mesh (src core -> dst core), which the graph view animates as a moving data
packet. These tests cover the parser unconditionally and the full
iverilog-driven trace when the toolchain is available.
"""
from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER_ROOT = REPO_ROOT / "tools" / "wau-pipelines-viewer"
sys.path.insert(0, str(VIEWER_ROOT))

from wau_viewer.trace_parser import parse_trace  # noqa: E402


_SYNTHETIC_TRACE = """# wau-pipelines-viewer trace v1
META grid_x=3 grid_y=2 core_count=6 stim_count=1
@CYCLE 5
HOST_IN v=0 r=1 flow=0 a=0 b=0
HOST_OUT v=0 r=1 flow=0 val=0
CORE 0 busy=0 cache_hit=0 ddeliv=1 ddeliv_src=2 ddeliv_val=38 ddeliv_flow=1 ddeliv_stage=2 cache_h_count=0 cache_l_count=1
CORE 1 busy=1 cache_hit=0 disp=1 disp_op=3 disp_a=14 disp_b=3 disp_imm=0 disp_use_imm=1 disp_stage=1 disp_flow=1 cache_h_count=0 cache_l_count=0
OBS hops=4 stalls=0 forwards=2 deliv=1 cache_h=0 cache_l=1
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


class DataTraceEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("iverilog") is None or shutil.which("vvp") is None:
            self.skipTest("iverilog/vvp not available")
        self.rtl_dir = REPO_ROOT / "src" / "verilog" / "generated"
        if not (self.rtl_dir / "wau_top.v").exists():
            self.skipTest("generated RTL not present (run `waugen generate`)")

    def test_data_movement_is_captured_from_real_rtl(self) -> None:
        from wau_viewer.simulator import IverilogRunner

        runner = IverilogRunner(self.rtl_dir)
        res = runner.run([1, 2], [10, 9], [4, 3], max_cycles=400)
        trace = parse_trace(res.trace_path)

        deliveries = [
            c for snap in trace.cycles for c in snap.cores if c.data_delivered
        ]
        # The demo flows both return results to the coordinator, so we must see
        # at least one data-plane delivery with a real source core + value.
        self.assertTrue(deliveries, "no data-plane deliveries captured from RTL")
        self.assertTrue(any(c.ddeliv_value not in (None, 0) for c in deliveries))


if __name__ == "__main__":
    unittest.main()
