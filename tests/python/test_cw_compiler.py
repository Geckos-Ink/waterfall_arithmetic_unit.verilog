from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.cw_compiler import CWCompilerError, merge_cw_into_config, parse_cw_program


def _base_payload() -> dict[str, object]:
    return {
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


def _base_program_without_wau_pragmas() -> str:
    program = Path("docs/example-program.cw").read_text()
    stripped_lines = [
        line for line in program.splitlines() if not line.lstrip().startswith("// @wau ")
    ]
    return "\n".join(stripped_lines) + "\n"


class CWCompilerTests(unittest.TestCase):
    def test_parse_reference_example(self) -> None:
        program = Path("docs/example-program.cw").read_text()
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
        self.assertEqual(spec.preferred_max_in_flight, 4)
        self.assertEqual(spec.preferred_dtype, "float32")
        self.assertEqual(spec.preferred_placement_policy, "locality")
        self.assertEqual(spec.preferred_lowering_profile, "latency_optimized")
        self.assertEqual(spec.preferred_program_priority, 4)
        self.assertEqual(spec.preferred_program_load_balance, "least_busy")
        self.assertEqual(shape.h, 224)
        self.assertEqual(shape.w, 224)
        self.assertEqual(shape.cin, 64)
        self.assertEqual(shape.cout, 128)

    def test_parse_reference_example_with_lane_pragma(self) -> None:
        base_program = _base_program_without_wau_pragmas()
        program = "// @wau lane_parallelism=6\n" + base_program
        spec, _shape = parse_cw_program(program)

        self.assertEqual(spec.preferred_lane_parallelism, 6)
        self.assertIsNone(spec.preferred_max_in_flight)
        self.assertIsNone(spec.preferred_dtype)

    def test_merge_cw_into_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = _base_payload()
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = Path("docs/example-program.cw").read_text()
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
            hints = flow.get("cw_hints", {})
            self.assertEqual(hints.get("lane_parallelism_compiled"), 4)
            self.assertEqual(hints.get("lane_parallelism_source"), "pragma")
            self.assertEqual(hints.get("max_in_flight_compiled"), 4)
            self.assertEqual(hints.get("max_in_flight_source"), "cli")
            self.assertEqual(hints.get("dtype_compiled"), "float32")
            self.assertEqual(hints.get("dtype_source"), "pragma")
            self.assertEqual(hints.get("placement_policy_compiled"), "locality")
            self.assertEqual(hints.get("lowering_profile_compiled"), "latency_optimized")
            self.assertEqual(hints.get("program_priority_compiled"), 3)
            self.assertEqual(hints.get("program_load_balance_compiled"), "least_busy")
            self.assertEqual(program_obj["priority"], 3)
            self.assertEqual(program_obj["load_balance"], "least_busy")

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

            base_payload = _base_payload()
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = (
                "// @wau lane_parallelism=3\n"
                "// @wau max_in_flight=7\n"
                "// @wau preferred_dtype=int32\n"
                "// @wau placement_policy=balance\n"
                "// @wau lowering_profile=throughput_optimized\n"
                "// @wau program_priority=5\n"
                "// @wau program_load_balance=round_robin\n"
                + _base_program_without_wau_pragmas()
            )
            flow, _program_obj = merge_cw_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                program=program,
                flow_id=71,
                name="cw_pragma_override",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
                max_in_flight=5,
                dtype="float32",
                lane_parallelism=5,
                placement_policy="locality",
                lowering_profile="latency_optimized",
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
            self.assertEqual(hints.get("lane_parallelism_source"), "cli")
            self.assertEqual(hints.get("max_in_flight_preferred"), 7)
            self.assertEqual(hints.get("max_in_flight_compiled"), 5)
            self.assertEqual(hints.get("max_in_flight_source"), "cli")
            self.assertEqual(hints.get("preferred_dtype"), "int32")
            self.assertEqual(hints.get("dtype_compiled"), "float32")
            self.assertEqual(hints.get("dtype_source"), "cli")
            self.assertEqual(hints.get("placement_policy_preferred"), "balance")
            self.assertEqual(hints.get("placement_policy_compiled"), "locality")
            self.assertEqual(hints.get("lowering_profile_preferred"), "throughput_optimized")
            self.assertEqual(hints.get("lowering_profile_compiled"), "latency_optimized")
            self.assertEqual(hints.get("program_priority_preferred"), 5)
            self.assertEqual(hints.get("program_priority_compiled"), 2)
            self.assertEqual(hints.get("program_load_balance_preferred"), "round_robin")
            self.assertEqual(hints.get("program_load_balance_compiled"), "least_busy")

    def test_merge_cw_pragma_applies_when_cli_omits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = _base_payload()
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = (
                "// @wau lane_parallelism=2\n"
                "// @wau max_in_flight=6\n"
                "// @wau preferred_dtype=int32\n"
                "// @wau placement_policy=balance\n"
                "// @wau lowering_profile=throughput_optimized\n"
                "// @wau program_priority=6\n"
                "// @wau program_load_balance=round_robin\n"
                + _base_program_without_wau_pragmas()
            )
            flow, program_obj = merge_cw_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                program=program,
                flow_id=72,
                name="cw_pragma_defaults",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
                program_id=20,
                program_name="cw_program_defaults",
                program_replicas=1,
                program_max_parallel_flows=1,
            )

            hints = flow.get("cw_hints", {})
            self.assertEqual(flow.get("max_in_flight"), 6)
            self.assertEqual(hints.get("lane_parallelism_compiled"), 2)
            self.assertEqual(hints.get("lane_parallelism_source"), "pragma")
            self.assertEqual(hints.get("max_in_flight_compiled"), 6)
            self.assertEqual(hints.get("max_in_flight_source"), "pragma")
            self.assertEqual(hints.get("dtype_compiled"), "int32")
            self.assertEqual(hints.get("dtype_source"), "pragma")
            self.assertEqual(hints.get("placement_policy_compiled"), "balance")
            self.assertEqual(hints.get("lowering_profile_compiled"), "throughput_optimized")
            self.assertEqual(hints.get("program_priority_compiled"), 6)
            self.assertEqual(hints.get("program_load_balance_compiled"), "round_robin")
            self.assertEqual(program_obj["priority"], 6)
            self.assertEqual(program_obj["load_balance"], "round_robin")

    def test_parse_rejects_invalid_pragma_syntax(self) -> None:
        with self.assertRaisesRegex(
            CWCompilerError,
            r"Invalid @wau pragma syntax at line 1",
        ):
            parse_cw_program("// @wau lane_parallelism\nvoid main() {}")

    def test_parse_rejects_unknown_pragma_key(self) -> None:
        with self.assertRaisesRegex(
            CWCompilerError,
            r"Unsupported @wau pragma 'unknown' at line 1",
        ):
            parse_cw_program("// @wau unknown=1\nvoid main() {}")

    def test_parse_rejects_invalid_preferred_dtype(self) -> None:
        with self.assertRaisesRegex(
            CWCompilerError,
            r"Invalid @wau preferred_dtype 'Float32' at line 1",
        ):
            parse_cw_program("// @wau preferred_dtype=Float32\nvoid main() {}")

    def test_parse_rejects_invalid_placement_policy(self) -> None:
        with self.assertRaisesRegex(
            CWCompilerError,
            r"Invalid @wau placement_policy value 'spread' at line 1",
        ):
            parse_cw_program("// @wau placement_policy=spread\nvoid main() {}")

    def test_parse_rejects_invalid_program_load_balance(self) -> None:
        with self.assertRaisesRegex(
            CWCompilerError,
            r"Invalid @wau program_load_balance value 'fifo' at line 1",
        ):
            parse_cw_program("// @wau program_load_balance=fifo\nvoid main() {}")

    def test_parse_rejects_incomplete_program(self) -> None:
        with self.assertRaises(CWCompilerError):
            parse_cw_program("void main() { int x = 1; }")

    def test_capability_aware_candidate_generation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = _base_payload()
            # Restrict a handful of cores so the compiler/CW lowering must skip them
            # for ops they do not support:
            #   - (1,1) only does add (no mul, no max)
            #   - (0,1) and (2,1) only do add+mul (no max)
            # All other cores remain unrestricted.
            base_payload["compiler"] = {
                "allow_cycle_recurrence": True,
                "core_capabilities": [
                    {
                        "core": {"x": 0, "y": 1},
                        "operations": ["add", "mul"],
                        "data_types": ["int32"],
                    },
                    {
                        "core": {"x": 1, "y": 1},
                        "operations": ["add"],
                        "data_types": ["int32"],
                    },
                    {
                        "core": {"x": 2, "y": 1},
                        "operations": ["add", "mul"],
                        "data_types": ["int32"],
                    },
                ],
            }
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            program = Path("docs/example-program.cw").read_text()
            flow, _program_obj = merge_cw_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                program=program,
                flow_id=88,
                name="cw_capability_aware",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
                dtype="int32",
                lane_parallelism=2,
                placement_policy="balance",
                lowering_profile="reference",
                program_id=88,
                program_replicas=1,
                program_max_parallel_flows=1,
            )

            mul_nodes = [n for n in flow["nodes"] if n.get("op") == "mul"]
            self.assertTrue(mul_nodes, "expected at least one mul lane node")
            for node in mul_nodes:
                candidate_coords = {
                    (cand["x"], cand["y"])
                    for cand in node["placement"]["candidate_cores"]
                }
                # (1,1) has no mul capability so it must be filtered out for mul nodes.
                self.assertNotIn(
                    (1, 1),
                    candidate_coords,
                    f"mul node {node['id']} candidates {candidate_coords} include (1,1) which lacks mul capability",
                )

            max_nodes = [n for n in flow["nodes"] if n.get("op") == "max"]
            self.assertTrue(max_nodes, "expected at least one max node")
            for node in max_nodes:
                candidate_coords = {
                    (cand["x"], cand["y"])
                    for cand in node["placement"]["candidate_cores"]
                }
                # None of the restricted lane-1 cores include max in their op set.
                self.assertFalse(
                    candidate_coords & {(0, 1), (1, 1), (2, 1)},
                    f"max node {node['id']} candidates {candidate_coords} include lane-1 cores lacking max capability",
                )

            hints = flow.get("cw_hints", {})
            self.assertTrue(hints.get("capability_filter_active"))
            self.assertIn("1,1", hints.get("capability_restricted_cores", []))


if __name__ == "__main__":
    unittest.main()
