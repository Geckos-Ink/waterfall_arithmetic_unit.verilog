from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import ConfigError, load_config


class FoundationAlignmentTests(unittest.TestCase):
    def _write_tmp_config(self, payload: dict) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "config.json"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        return path

    def test_core_capabilities_constrain_placement(self) -> None:
        payload = {
            "project": "caps",
            "device": {
                "preset": "intel_de0_nano",
                "grid": {"x": 2, "y": 2},
                "data_types": ["int32", "float16"],
            },
            "operations": {"library": ["add", "mul"]},
            "compiler": {
                "routing": "waterfall",
                "core_capabilities": [
                    {"core": {"x": 0, "y": 0}, "operations": ["add"], "data_types": ["int32"]},
                    {"core": {"x": 1, "y": 0}, "operations": ["mul"], "data_types": ["int32"]},
                ],
            },
            "flows": [
                {
                    "id": 1,
                    "name": "typed_chain",
                    "entry": {"x": 0, "y": 0},
                    "stages": [
                        {"op": "add", "dtype": "int32"},
                        {"op": "mul", "dtype": "int32"},
                    ],
                }
            ],
        }
        config = load_config(self._write_tmp_config(payload))
        project = compile_project(config)

        flow = project.flows[0]
        self.assertEqual((flow.stages[0].primary_core.x, flow.stages[0].primary_core.y), (0, 0))
        self.assertEqual((flow.stages[1].primary_core.x, flow.stages[1].primary_core.y), (1, 0))

    def test_manual_routing_requires_explicit_placement(self) -> None:
        payload = {
            "project": "manual_mode",
            "device": {
                "preset": "intel_de0_nano",
                "grid": {"x": 2, "y": 2},
            },
            "operations": {"library": ["add"]},
            "compiler": {"routing": "manual"},
            "flows": [
                {
                    "id": 1,
                    "name": "bad_manual",
                    "entry": {"x": 0, "y": 0},
                    "stages": [{"op": "add"}],
                }
            ],
        }
        config = load_config(self._write_tmp_config(payload))

        with self.assertRaises(ValueError):
            compile_project(config)

    def test_rejects_unknown_dtype(self) -> None:
        payload = {
            "project": "dtype_error",
            "device": {
                "preset": "intel_de0_nano",
                "grid": {"x": 2, "y": 2},
                "data_types": ["int32"],
            },
            "operations": {"library": ["add"]},
            "flows": [
                {
                    "id": 1,
                    "name": "bad_dtype",
                    "entry": {"x": 0, "y": 0},
                    "stages": [{"op": "add", "dtype": "float16"}],
                }
            ],
        }

        with self.assertRaises(ConfigError):
            load_config(self._write_tmp_config(payload))


if __name__ == "__main__":
    unittest.main()
