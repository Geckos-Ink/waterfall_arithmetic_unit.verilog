# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePreset:
    name: str
    vendor: str
    family: str
    part: str
    default_grid_x: int
    default_grid_y: int
    data_width: int
    flow_id_width: int
    opcode_width: int
    local_ram_depth: int
    global_ram_depth: int


DEVICE_PRESETS: dict[str, DevicePreset] = {
    "intel_de0_nano": DevicePreset(
        name="intel_de0_nano",
        vendor="Intel",
        family="Cyclone IV E",
        part="EP4CE22F17C6N",
        default_grid_x=4,
        default_grid_y=3,
        data_width=32,
        flow_id_width=12,
        opcode_width=8,
        local_ram_depth=128,
        global_ram_depth=2048,
    ),
    "intel_agilex7_fm": DevicePreset(
        name="intel_agilex7_fm",
        vendor="Intel",
        family="Agilex 7",
        part="AGFB014R24A2E2V",
        default_grid_x=12,
        default_grid_y=10,
        data_width=64,
        flow_id_width=16,
        opcode_width=8,
        local_ram_depth=512,
        global_ram_depth=16384,
    ),
    "xilinx_artix7_100t": DevicePreset(
        name="xilinx_artix7_100t",
        vendor="AMD/Xilinx",
        family="Artix-7",
        part="XC7A100T",
        default_grid_x=8,
        default_grid_y=6,
        data_width=32,
        flow_id_width=12,
        opcode_width=8,
        local_ram_depth=256,
        global_ram_depth=4096,
    ),
}


def get_device_preset(name: str) -> DevicePreset:
    try:
        return DEVICE_PRESETS[name]
    except KeyError as exc:
        available = ", ".join(sorted(DEVICE_PRESETS))
        raise KeyError(f"Unknown device preset '{name}'. Available: {available}") from exc
