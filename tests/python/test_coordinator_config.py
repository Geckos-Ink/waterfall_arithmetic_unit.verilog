# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Schema + emission tests for the multi-issue coordinator capacity knob.

`coordinator.max_in_flight` is the number of internally tagged transactions the
generated coordinator can keep executing concurrently on the core mesh,
including repeated inputs for one flow id. `1` reproduces the legacy
strictly-serial coordinator. The value must flow into the
`WAU_COORD_MAX_IN_FLIGHT` macro and the emitted `wau_coordinator.v`.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule
from waugen.verilog_emit import emit_verilog


CONFIG_PATH = Path("src/python/configs/wau_de0_nano_demo.json")


def _load_with_coordinator(coordinator: dict | None):
    payload = json.loads(CONFIG_PATH.read_text())
    if coordinator is None:
        payload.pop("coordinator", None)
    else:
        payload["coordinator"] = coordinator
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        return load_config(path)


class CoordinatorConfigTests(unittest.TestCase):
    def test_default_max_in_flight(self) -> None:
        config = _load_with_coordinator(None)
        self.assertEqual(config.coordinator.max_in_flight, 4)

    def test_custom_max_in_flight(self) -> None:
        config = _load_with_coordinator({"max_in_flight": 8})
        self.assertEqual(config.coordinator.max_in_flight, 8)

    def test_rejects_out_of_range(self) -> None:
        for bad in (0, 17):
            with self.assertRaises(ValueError):
                _load_with_coordinator({"max_in_flight": bad})

    def test_emits_macro_and_module_parameter(self) -> None:
        config = _load_with_coordinator({"max_in_flight": 6})
        project = compile_project(config)
        schedule = build_schedule(project)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            emit_verilog(project, schedule, out)
            defs = (out / "wau_defs.vh").read_text()
            coord = (out / "wau_coordinator.v").read_text()
        self.assertIn("`define WAU_COORD_MAX_IN_FLIGHT 6", defs)
        self.assertIn("MAX_IN_FLIGHT = `WAU_COORD_MAX_IN_FLIGHT", coord)
        # Multi-issue structures must be present (not the legacy single FSM).
        self.assertIn("slot_valid", coord)
        self.assertIn("dispatch_pkt_tag", coord)
        self.assertIn("result_pkt_tag", coord)
        self.assertIn("slot_order", coord)
        self.assertIn("assign host_in_ready = alloc_found;", coord)
        self.assertNotIn("flow_busy", coord)
        self.assertNotIn("ST_WAIT_RESULT", coord)


if __name__ == "__main__":
    unittest.main()
