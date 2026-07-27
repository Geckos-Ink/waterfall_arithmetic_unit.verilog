# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Schema + emission tests for the highway fabric and its contracting bus.

Two contracts live here:

- `device.highway.topology` chooses how many dimensions of highway the emitted
  mesh carries. `linear` (the default) is one 1-D highway per layer, walked in
  core-index order, so each router keeps LOCAL/PREV/NEXT(+UP/DOWN) instead of
  the seven-port mesh and routes by plain index compare. `matrix` keeps the full
  N/S/E/W(/U/D) mesh with X-then-Y-then-Z dimension-order routing.
- `device.highway.contract_bus` adds the per-highway contracting bus, whose
  per-core contract words are derived from the offline schedule.

Both must reach `wau_defs.vh` *and* the modules that consume them, per the
"config knob added without its RTL counterpart" rule.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import ConfigError, load_config
from waugen.scheduler import build_schedule
from waugen.verilog_emit import (
    _CONTRACT_MODE_BURST,
    _CONTRACT_MODE_PONG,
    _CONTRACT_MODE_STREAM,
    _encode_contract_word,
    contract_rom_entries,
    emit_verilog,
)


CONFIG_PATH = Path("src/python/configs/wau_de0_nano_demo.json")
MATRIX_CONFIG_PATH = Path("src/python/configs/wau_matrix_highway_demo.json")


def _load_with_highway(highway: dict | None):
    payload = json.loads(CONFIG_PATH.read_text())
    if highway is None:
        payload["device"].pop("highway", None)
    else:
        payload["device"]["highway"] = highway
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(payload, indent=2))
        return load_config(path)


def _emit(config_path: Path) -> dict[str, str]:
    cfg = load_config(config_path)
    project = compile_project(cfg)
    schedule = build_schedule(project)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        emit_verilog(project, schedule, out)
        return {p.name: p.read_text() for p in out.iterdir()}


class HighwayConfigTests(unittest.TestCase):
    def test_linear_topology_is_the_default(self) -> None:
        cfg = _load_with_highway(None)
        self.assertEqual(cfg.device.highway.topology, "linear")
        self.assertTrue(cfg.device.highway.is_linear)
        self.assertTrue(cfg.device.highway.contract_bus)
        self.assertEqual(cfg.device.highway.contract_max_burst, 8)
        self.assertEqual(cfg.device.highway.contract_lease_cycles, 64)

    def test_matrix_topology_is_opt_in(self) -> None:
        cfg = _load_with_highway({"topology": "matrix"})
        self.assertEqual(cfg.device.highway.topology, "matrix")
        self.assertFalse(cfg.device.highway.is_linear)

    def test_unknown_topology_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "device.highway.topology"):
            _load_with_highway({"topology": "torus"})

    def test_contract_bus_can_be_disabled(self) -> None:
        cfg = _load_with_highway({"contract_bus": False})
        self.assertFalse(cfg.device.highway.contract_bus)

    def test_burst_and_lease_ranges_are_enforced(self) -> None:
        # ConfigError subclasses ValueError; the shared `validate_range` helper
        # raises the plain ValueError for the lower bound, as elsewhere in the
        # schema (see StationCacheSpec).
        for payload in (
            {"contract_max_burst": 0},
            {"contract_max_burst": 256},
            {"contract_lease_cycles": 0},
            {"contract_lease_cycles": 70000},
        ):
            key = next(iter(payload))
            with self.subTest(**payload):
                with self.assertRaisesRegex(ValueError, key):
                    _load_with_highway(payload)

    def test_non_object_highway_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "device.highway must be an object"):
            _load_with_highway("linear")


class HighwayEmissionTests(unittest.TestCase):
    """Macro/module agreement for both topologies."""

    def test_linear_emits_three_plane_router_without_a_divider(self) -> None:
        files = _emit(CONFIG_PATH)
        defs = files["wau_defs.vh"]
        router = files["wau_highway_router.v"]

        self.assertIn("`define WAU_HIGHWAY_TOPOLOGY_LINEAR 1", defs)
        self.assertIn('`define WAU_HIGHWAY_TOPOLOGY_NAME "linear"', defs)
        self.assertIn("`define WAU_HIGHWAY_PORT_COUNT 5", defs)

        # The macro must match the module's own PORT_COUNT localparam.
        self.assertIn("localparam PORT_COUNT = 5;", router)
        for direction in ("local", "prev", "next", "up", "down"):
            self.assertIn(f"input wire {direction}_in_valid,", router)
        for absent in ("north", "south", "east", "west"):
            self.assertNotIn(f"{absent}_in_valid", router)

        # The whole point of the index-order chain: no per-port LPM_DIVIDE.
        self.assertNotIn("% GRID_X", router)
        self.assertNotIn("/ GRID_X", router)
        self.assertIn("dst_core > CORE_INDEX[CORE_ID_WIDTH-1:0]", router)

    def test_matrix_keeps_the_seven_port_dimension_order_router(self) -> None:
        files = _emit(MATRIX_CONFIG_PATH)
        defs = files["wau_defs.vh"]
        router = files["wau_highway_router.v"]

        self.assertIn("`define WAU_HIGHWAY_TOPOLOGY_MATRIX 1", defs)
        self.assertIn("`define WAU_HIGHWAY_PORT_COUNT 7", defs)
        self.assertIn("localparam PORT_COUNT = 7;", router)
        for direction in ("north", "south", "east", "west", "up", "down"):
            self.assertIn(f"input wire {direction}_in_valid,", router)
        self.assertIn("dst_x = dst_core % GRID_X;", router)

    def test_linear_mesh_joins_rows_end_to_end(self) -> None:
        mesh = _emit(CONFIG_PATH)["wau_highway_mesh.v"]
        # The chain's only edges are the first and last core of a layer; a row
        # boundary is an ordinary PREV/NEXT hop, not an edge tie-off.
        self.assertIn("if ((gx == 0) && (gy == 0)) begin : prev_edge", mesh)
        self.assertIn(
            "if ((gx == GRID_X - 1) && (gy == GRID_Y - 1)) begin : next_edge", mesh
        )
        self.assertIn("localparam integer PREV_INDEX = CORE_INDEX - 1;", mesh)
        self.assertIn("localparam integer NEXT_INDEX = CORE_INDEX + 1;", mesh)
        self.assertNotIn("north_edge", mesh)

    def test_matrix_mesh_keeps_the_four_planar_edges(self) -> None:
        mesh = _emit(MATRIX_CONFIG_PATH)["wau_highway_mesh.v"]
        for edge in ("north_edge", "south_edge", "east_edge", "west_edge"):
            self.assertIn(edge, mesh)
        self.assertNotIn("prev_edge", mesh)

    def test_layers_stay_connected_in_both_topologies(self) -> None:
        for path in (CONFIG_PATH, MATRIX_CONFIG_PATH):
            with self.subTest(config=path.name):
                mesh = _emit(path)["wau_highway_mesh.v"]
                self.assertIn("up_edge", mesh)
                self.assertIn("down_edge", mesh)


class ContractBusEmissionTests(unittest.TestCase):
    def test_macros_match_the_contract_module(self) -> None:
        files = _emit(CONFIG_PATH)
        defs = files["wau_defs.vh"]
        contract = files["wau_highway_contract.v"]

        self.assertIn("`define WAU_HIGHWAY_CONTRACT_BUS 1", defs)
        self.assertIn("`define WAU_HIGHWAY_CONTRACT_WORD_WIDTH 18", defs)
        self.assertIn("`define WAU_HIGHWAY_CONTRACT_MAX_BURST 8", defs)
        self.assertIn("`define WAU_HIGHWAY_CONTRACT_LEASE_CYCLES 64", defs)

        self.assertIn("WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH", contract)
        self.assertIn("MAX_BURST = `WAU_HIGHWAY_CONTRACT_MAX_BURST", contract)
        self.assertIn("LEASE_CYCLES = `WAU_HIGHWAY_CONTRACT_LEASE_CYCLES", contract)
        # Field offsets in the module must agree with the emitted macros.
        self.assertIn("localparam integer MODE_LSB    = 0;", contract)
        self.assertIn("localparam integer WORDS_LSB   = 2;", contract)
        self.assertIn("localparam integer REPEATS_LSB = 10;", contract)

    def test_only_the_data_plane_highway_is_contracted(self) -> None:
        top = _emit(CONFIG_PATH)["wau_top.v"]
        # The control plane has a single injector (the coordinator), so there is
        # nothing to arbitrate and its bus stays out of the fabric entirely.
        self.assertIn(".CONTRACT_BUS_ENABLE(0)\n    ) control_plane_mesh_u (", top)
        self.assertIn(
            ".CONTRACT_BUS_ENABLE(CONTRACT_BUS_ENABLE)\n    ) data_plane_mesh_u (", top
        )
        self.assertIn("localparam integer CONTRACT_BUS_ENABLE = 1;", top)
        # The real-time side: a core calls the highway when it has a result.
        self.assertIn("assign data_contract_req = core_result_valid;", top)

    def test_disabling_the_bus_reaches_the_macro_and_the_top(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text())
        payload["device"]["highway"] = {"contract_bus": False}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(payload, indent=2))
            files = _emit(path)
        self.assertIn("`define WAU_HIGHWAY_CONTRACT_BUS 0", files["wau_defs.vh"])
        self.assertIn(
            "localparam integer CONTRACT_BUS_ENABLE = 0;", files["wau_top.v"]
        )

    def test_observability_reaches_the_mmio_map(self) -> None:
        files = _emit(CONFIG_PATH)
        top = files["wau_top.v"]
        mmio = files["wau_host_mmio.v"]
        for signal in (
            "obs_total_contract_grant_count",
            "obs_total_contract_hold_cycles",
            "obs_total_contract_defer_count",
        ):
            self.assertIn(signal, top)
            self.assertIn(signal, mmio)
        # Additive only: the published map keeps its existing addresses.
        self.assertIn("ADDR_CACHE_L  = 'h17;", mmio)
        self.assertIn("ADDR_CTR_GRNT = 'h18;", mmio)
        self.assertIn("ADDR_CTR_HOLD = 'h19;", mmio)
        self.assertIn("ADDR_CTR_DEFR = 'h1A;", mmio)


class ContractRomTests(unittest.TestCase):
    """The programmed side of the bus: expectations derived from the schedule."""

    def _rom(self, config_path: Path = CONFIG_PATH):
        cfg = load_config(config_path)
        project = compile_project(cfg)
        schedule = build_schedule(project)
        return project, schedule, contract_rom_entries(project, schedule)

    def test_one_entry_per_core(self) -> None:
        project, _schedule, rom = self._rom()
        dev = project.config.device
        self.assertEqual(len(rom), dev.grid_x * dev.grid_y * dev.grid_z)

    def test_idle_cores_get_an_inert_pong_contract(self) -> None:
        project, schedule, rom = self._rom()
        used = {ins.core_index for ins in schedule.instructions}
        for idx, entry in enumerate(rom):
            if idx not in used:
                self.assertEqual(
                    entry,
                    (_CONTRACT_MODE_PONG, 1, 1),
                    f"core {idx} has no scheduled work but reserves the highway",
                )

    def test_expectation_matches_the_schedule(self) -> None:
        _project, schedule, rom = self._rom()
        per_core: dict[int, dict[int, int]] = {}
        for ins in schedule.instructions:
            per_core.setdefault(ins.core_index, {})
            counts = per_core[ins.core_index]
            counts[ins.flow_id] = counts.get(ins.flow_id, 0) + 1

        for core_index, counts in per_core.items():
            mode, words, repeats = rom[core_index]
            self.assertEqual(words, min(max(counts.values()), 8))
            self.assertEqual(repeats, len(counts))
            if words <= 1 and repeats <= 1:
                self.assertEqual(mode, _CONTRACT_MODE_PONG)
            elif repeats <= 1:
                self.assertEqual(mode, _CONTRACT_MODE_BURST)
            else:
                self.assertEqual(mode, _CONTRACT_MODE_STREAM)

    def test_words_are_clamped_to_the_synthesised_burst_budget(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text())
        payload["device"]["highway"] = {"contract_max_burst": 1}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(payload, indent=2))
            _project, _schedule, rom = self._rom(path)
        for _mode, words, _repeats in rom:
            self.assertEqual(words, 1)

    def test_word_encoding_round_trips(self) -> None:
        word = _encode_contract_word(_CONTRACT_MODE_STREAM, 5, 7)
        self.assertEqual(word & 0x3, _CONTRACT_MODE_STREAM)
        self.assertEqual((word >> 2) & 0xFF, 5)
        self.assertEqual((word >> 10) & 0xFF, 7)
        self.assertLess(word, 1 << 18)

    def test_rom_is_emitted_into_the_top(self) -> None:
        _project, _schedule, rom = self._rom()
        top = _emit(CONFIG_PATH)["wau_top.v"]
        for idx, (mode, words, repeats) in enumerate(rom):
            expected = _encode_contract_word(mode, words, repeats)
            self.assertIn(
                f"assign data_contract_word[({idx}*CONTRACT_WORD_WIDTH)"
                f" +: CONTRACT_WORD_WIDTH] = 18'h{expected:05X};",
                top,
            )


class HighwayProgramJsonTests(unittest.TestCase):
    def test_highway_is_published_for_the_viewer(self) -> None:
        files = _emit(CONFIG_PATH)
        payload = json.loads(files["wau_program.json"])
        self.assertEqual(
            payload["device"]["highway"],
            {
                "topology": "linear",
                "contract_bus": True,
                "contract_max_burst": 8,
                "contract_lease_cycles": 64,
            },
        )


if __name__ == "__main__":
    unittest.main()
