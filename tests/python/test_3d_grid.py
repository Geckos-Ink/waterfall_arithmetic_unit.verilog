# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule, core_index
from waugen.verilog_emit import emit_verilog


def _write_config(payload: dict, tmp: str) -> Path:
    path = Path(tmp) / "config.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _payload() -> dict:
    return {
        "project": "wau_3d_unit",
        "device": {
            "preset": "intel_de0_nano",
            "grid": {"x": 2, "y": 1, "z": 2},
        },
        "operations": {"library": ["add", "mul"]},
        "compiler": {"routing": "waterfall", "allow_adaptive_reroute": False},
        "flows": [
            {
                "id": 3,
                "name": "vertical_chain",
                "entry": {"x": 0, "y": 0, "z": 1},
                "nodes": [
                    {
                        "id": "upper_add",
                        "op": "add",
                        "placement": {
                            "core": {"x": 0, "y": 0, "z": 1},
                            "fixed": True,
                        },
                    },
                    {
                        "id": "lower_mul",
                        "op": "mul",
                        "deps": ["upper_add"],
                        "immediate_b": 3,
                        "placement": {
                            "core": {"x": 0, "y": 0, "z": 0},
                            "fixed": True,
                        },
                    },
                ],
            }
        ],
    }


class Grid3DTests(unittest.TestCase):
    def test_config_compile_and_schedule_use_z_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = load_config(_write_config(_payload(), td))

        self.assertEqual(config.device.grid_z, 2)
        project = compile_project(config)
        flow = project.flows[0]

        self.assertEqual(flow.entry.z, 1)
        self.assertEqual(flow.nodes[0].primary_core.z, 1)
        self.assertEqual(flow.nodes[1].primary_core.z, 0)

        plan = build_schedule(project)
        schedule = plan.to_json()
        by_node = {ins["node_id"]: ins for ins in schedule["instructions"]}

        self.assertEqual(by_node["upper_add"]["core"], {"x": 0, "y": 0, "z": 1})
        self.assertEqual(by_node["upper_add"]["core_index"], core_index(0, 0, 2, 1, 1))
        self.assertEqual(by_node["lower_mul"]["core"], {"x": 0, "y": 0, "z": 0})
        self.assertEqual(schedule["estimated_transfer_hops_total"], 1)

    def test_emit_verilog_metadata_includes_grid_z(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = load_config(_write_config(_payload(), td))
            project = compile_project(config)
            schedule = build_schedule(project)
            out_dir = Path(td) / "generated"
            emit_verilog(project, schedule, out_dir)

            defs = (out_dir / "wau_defs.vh").read_text()
            self.assertIn("`define WAU_GRID_Z 2", defs)
            self.assertIn("`define WAU_CORE_COUNT 4", defs)

            program = json.loads((out_dir / "wau_program.json").read_text())
            self.assertEqual(program["device"]["grid"], {"x": 2, "y": 1, "z": 2})
            self.assertEqual(program["flows"][0]["nodes"][0]["primary_core"]["z"], 1)
            self.assertEqual(program["flows"][0]["nodes"][0]["primary_core"]["index"], 2)

    def test_rejects_out_of_bounds_z(self) -> None:
        payload = _payload()
        payload["flows"][0]["entry"]["z"] = 2
        with tempfile.TemporaryDirectory() as td:
            path = _write_config(payload, td)
            with self.assertRaises(ValueError):
                compile_project(load_config(path))


if __name__ == "__main__":
    unittest.main()
