from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.cw_compiler import CWCompilerError, merge_cw_into_config, parse_cw_program


class CWCompilerTests(unittest.TestCase):
    def test_parse_reference_example(self) -> None:
        program = Path("docs/example-pogram.cw").read_text()
        spec, shape = parse_cw_program(program)

        self.assertEqual(spec.kernel_name, "conv2d_residual_kernel")
        self.assertEqual(spec.kernel_size, 3)
        self.assertEqual(spec.tile_h, 16)
        self.assertEqual(spec.tile_w, 16)
        self.assertEqual(spec.cin_block, 16)
        self.assertEqual(spec.cout_block, 8)
        self.assertEqual(spec.worker_count, 8)
        self.assertTrue(spec.has_prefetch)
        self.assertTrue(spec.has_residual)
        self.assertTrue(spec.has_relu)
        self.assertTrue(spec.has_double_buffering)
        self.assertEqual(spec.preferred_lane_parallelism, 4)
        self.assertEqual(shape.h, 224)
        self.assertEqual(shape.w, 224)
        self.assertEqual(shape.cin, 64)
        self.assertEqual(shape.cout, 128)

    def test_parse_reference_example_with_lane_pragma(self) -> None:
        base_program = Path("docs/example-pogram.cw").read_text()
        program = "// @wau lane_parallelism=6\n" + base_program
        spec, _shape = parse_cw_program(program)

        self.assertEqual(spec.preferred_lane_parallelism, 6)

    def test_merge_cw_into_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = {
                "project": "tmp",
                "device": {
                    "preset": "intel_de0_nano",
                    "grid": {"x": 4, "y": 3},
                    "data_types": ["int32", "float32"],
                },
                "operations": {"library": ["add"]},
                "compiler": {"allow_cycle_recurrence": True},
                "scheduler": {"strategy": "dependency_aware"},
                "flows": [],
                "programs": [],
            }
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = Path("docs/example-pogram.cw").read_text()
            flow, program_obj = merge_cw_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                program=program,
                flow_id=77,
                name="cw_kernel",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
                max_in_flight=4,
                program_id=19,
                program_name="cw_program",
                program_priority=3,
                program_replicas=2,
                program_max_parallel_flows=2,
                program_load_balance="least_busy",
            )

            self.assertEqual(flow["id"], 77)
            self.assertEqual(flow["name"], "cw_kernel")
            self.assertIn("cw_hints", flow)
            self.assertGreater(len(flow["nodes"]), 20)
            self.assertEqual(program_obj["id"], 19)
            self.assertEqual(program_obj["flows"], [77])

            payload = json.loads(out_path.read_text())
            self.assertEqual(len(payload["flows"]), 1)
            self.assertEqual(len(payload["programs"]), 1)
            self.assertIn("mul", payload["operations"]["library"])
            self.assertIn("max", payload["operations"]["library"])

            node_ids = {
                node["id"] for node in payload["flows"][0]["nodes"] if isinstance(node, dict) and "id" in node
            }
            self.assertIn("lane0_relu", node_ids)
            self.assertIn("store_tile", node_ids)
            self.assertIn("tile_counter", node_ids)

    def test_merge_cw_pragma_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = {
                "project": "tmp",
                "device": {
                    "preset": "intel_de0_nano",
                    "grid": {"x": 4, "y": 3},
                    "data_types": ["int32", "float32"],
                },
                "operations": {"library": ["add"]},
                "compiler": {"allow_cycle_recurrence": True},
                "scheduler": {"strategy": "dependency_aware"},
                "flows": [],
                "programs": [],
            }
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = "// @wau lane_parallelism=3\n" + Path("docs/example-pogram.cw").read_text()
            flow, _program_obj = merge_cw_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                program=program,
                flow_id=71,
                name="cw_pragma_override",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
                max_in_flight=4,
                lane_parallelism=5,
                program_id=18,
                program_name="cw_program_override",
                program_priority=2,
                program_replicas=1,
                program_max_parallel_flows=1,
                program_load_balance="least_busy",
            )

            hints = flow.get("cw_hints", {})
            self.assertEqual(hints.get("lane_parallelism_preferred"), 3)
            self.assertEqual(hints.get("lane_parallelism_compiled"), 5)

    def test_parse_rejects_incomplete_program(self) -> None:
        with self.assertRaises(CWCompilerError):
            parse_cw_program("void main() { int x = 1; }")


if __name__ == "__main__":
    unittest.main()
