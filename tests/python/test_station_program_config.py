# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Schema tests for the per-core static fast-path dispatch table.

`compiler.station_program` bounds and enables the per-core table described in
`compiler.build_fast_path_tables`: disabled by default (every stage keeps
round-tripping the coordinator exactly as before this feature existed), and
`table_bits` bounds how many distinct `(flow_id, stage_index)` pairs a single
core's table may hold (`2**table_bits` entries).

`device.enable_runtime_auto_adapt` now actually reaches the generated RTL (see
`test_highway.py`-style emission tests / `_render_host_mmio`); its default
flips to `False` here ("disable in-circuit schedulers unless strictly
needed") without touching any tracked config, which already sets the key
explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.config import load_config


CONFIG_PATH = Path("src/python/configs/wau_de0_nano_demo.json")


def _load_with_compiler_key(key: str, value) -> object:
    payload = json.loads(CONFIG_PATH.read_text())
    compiler = dict(payload.get("compiler", {}))
    if value is None:
        compiler.pop(key, None)
    else:
        compiler[key] = value
    payload["compiler"] = compiler
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        return load_config(path)


def _load_with_device_key(key: str, value) -> object:
    payload = json.loads(CONFIG_PATH.read_text())
    device = dict(payload.get("device", {}))
    if value is None:
        device.pop(key, None)
    else:
        device[key] = value
    payload["device"] = device
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        return load_config(path)


class StationProgramConfigTests(unittest.TestCase):
    def test_default_is_disabled_with_5_bit_table(self) -> None:
        config = _load_with_compiler_key("station_program", None)
        self.assertFalse(config.compiler.station_program.enabled)
        self.assertEqual(config.compiler.station_program.table_bits, 5)

    def test_can_enable_with_custom_table_bits(self) -> None:
        config = _load_with_compiler_key(
            "station_program", {"enabled": True, "table_bits": 3}
        )
        self.assertTrue(config.compiler.station_program.enabled)
        self.assertEqual(config.compiler.station_program.table_bits, 3)

    def test_rejects_out_of_range_table_bits(self) -> None:
        for bad in (0, 9):
            with self.assertRaises(ValueError):
                _load_with_compiler_key(
                    "station_program", {"enabled": True, "table_bits": bad}
                )

    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            _load_with_compiler_key("station_program", "not-an-object")


class RuntimeAutoAdaptConfigTests(unittest.TestCase):
    def test_defaults_to_disabled_when_omitted(self) -> None:
        config = _load_with_device_key("enable_runtime_auto_adapt", None)
        self.assertFalse(config.device.enable_runtime_auto_adapt)

    def test_stays_enabled_when_explicit(self) -> None:
        config = _load_with_device_key("enable_runtime_auto_adapt", True)
        self.assertTrue(config.device.enable_runtime_auto_adapt)

    def test_tracked_demo_config_keeps_explicit_true(self) -> None:
        # wau_de0_nano_demo.json must keep setting this explicitly so flipping
        # the default doesn't silently change its recorded behavior.
        payload = json.loads(CONFIG_PATH.read_text())
        self.assertIs(payload["device"]["enable_runtime_auto_adapt"], True)


if __name__ == "__main__":
    unittest.main()
