# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-side helpers for talking to a wau_vjtag_bridge-equipped FPGA.

The package is layered:

    waujtag.client     low-level line-protocol TCP client to the TCL server
    waujtag.mmio       generic 32-bit MMIO master (read_reg / write_reg)
    waujtag.wau        WAU-specific wrapper: trigger flows, read results,
                       collect observability counters
    waujtag.benchmark  generic micro-benchmark harness on top of WAU

Each layer is usable on its own. The lower layers know nothing about WAU,
so they can drive any other wau_vjtag_bridge-style register file. The WAU
layer encodes only the `wau_host_mmio` register map.
"""

from .client import TCLClient, TCLClientError
from .mmio import MMIO
from .wau import WAU, WAUResult, ObservabilitySnapshot
from .benchmark import Bench, BenchResult

__all__ = [
    "TCLClient",
    "TCLClientError",
    "MMIO",
    "WAU",
    "WAUResult",
    "ObservabilitySnapshot",
    "Bench",
    "BenchResult",
]
