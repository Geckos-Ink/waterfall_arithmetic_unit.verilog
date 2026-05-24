# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Generic 32-bit MMIO master on top of TCLClient.

Knows nothing about WAU — usable for any wau_vjtag_bridge-style register file
(or anything else with a compatible JTAG MMIO protocol). Keeping this layer
separate lets non-WAU designs reuse the bridge + TCL server + this client.
"""

from __future__ import annotations

from .client import TCLClient


class MMIO:
    def __init__(self, client: TCLClient) -> None:
        self.client = client

    # --- basic ----------------------------------------------------------
    def write_reg(self, address: int, value: int) -> None:
        self.client.write32(address, value)

    def read_reg(self, address: int) -> int:
        return self.client.read32(address)

    # --- helpers --------------------------------------------------------
    def write_field(self, address: int, lsb: int, width: int, value: int) -> None:
        """Read-modify-write a contiguous field within a 32-bit register."""
        mask = ((1 << width) - 1) << lsb
        cur = self.read_reg(address)
        new = (cur & ~mask) | ((value << lsb) & mask)
        self.write_reg(address, new)

    def read_field(self, address: int, lsb: int, width: int) -> int:
        mask = (1 << width) - 1
        return (self.read_reg(address) >> lsb) & mask

    def burst_write(self, items: list[tuple[int, int]]) -> None:
        """Issue a sequence of writes in order (no JTAG-side batching today)."""
        for addr, value in items:
            self.write_reg(addr, value)

    def burst_read(self, addresses: list[int]) -> list[int]:
        return [self.read_reg(addr) for addr in addresses]

    def soft_reset(self) -> None:
        """Issue the bridge-level soft reset (IR_RESET, pulses 1 clk)."""
        self.client.reset()

    def obs_aux(self) -> int:
        return self.client.obs_aux()
