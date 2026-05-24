# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""WAU-side wrapper around the generic MMIO master.

Encodes the wau_host_mmio register map (see src/verilog/generated/wau_host_mmio.v
and the README's "Host MMIO Register Map" section) and provides:

  * WAU.trigger(flow_id, a, b)   — submit one job
  * WAU.wait_result(timeout_s)   — poll STATUS until output_pending, drain
  * WAU.execute(flow_id, a, b)   — combined trigger + wait
  * WAU.observability()          — snapshot router + cache counters

`data_width` and `flow_id_width` default to the demo build (32 + 12) — they're
parameters so you can reuse this class with re-tuned WAU configurations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .mmio import MMIO


# Register addresses (word-addressed). Match wau_host_mmio.v.
ADDR_CTRL     = 0x00
ADDR_STATUS   = 0x01
ADDR_FLOW_ID  = 0x02
ADDR_IN_A     = 0x03
ADDR_IN_B     = 0x04
ADDR_TRIGGER  = 0x05
ADDR_OUT_FLOW = 0x10
ADDR_OUT_VAL  = 0x11
ADDR_HOPS     = 0x12
ADDR_STALLS   = 0x13
ADDR_FORWARDS = 0x14
ADDR_DELIVRD  = 0x15
ADDR_CACHE_H  = 0x16
ADDR_CACHE_L  = 0x17

# STATUS bit layout
STATUS_HOST_IN_READY   = 1 << 0
STATUS_HOST_OUT_VALID  = 1 << 1
STATUS_OUTPUT_PENDING  = 1 << 2

# CTRL bit layout
CTRL_SOFT_RESET   = 1 << 0
CTRL_AUTO_ADAPT   = 1 << 1


@dataclass
class WAUResult:
    flow_id: int
    value: int            # signed view, two's-complement
    latency_s: float      # wall-clock between trigger and result


@dataclass
class ObservabilitySnapshot:
    hops: int
    stalls: int
    forwards: int
    local_delivered: int
    cache_hits: int
    cache_lookups: int

    @property
    def hit_rate(self) -> float:
        return self.cache_hits / self.cache_lookups if self.cache_lookups else 0.0

    def delta(self, prev: "ObservabilitySnapshot") -> "ObservabilitySnapshot":
        def d(a: int, b: int) -> int:
            # 32-bit free-running counters: handle wrap modulo 2**32.
            return (a - b) & 0xFFFFFFFF
        return ObservabilitySnapshot(
            hops           = d(self.hops, prev.hops),
            stalls         = d(self.stalls, prev.stalls),
            forwards       = d(self.forwards, prev.forwards),
            local_delivered= d(self.local_delivered, prev.local_delivered),
            cache_hits     = d(self.cache_hits, prev.cache_hits),
            cache_lookups  = d(self.cache_lookups, prev.cache_lookups),
        )


class WAU:
    def __init__(
        self,
        mmio: MMIO,
        *,
        data_width: int = 32,
        flow_id_width: int = 12,
        auto_adapt: bool = True,
    ) -> None:
        self.mmio = mmio
        self.data_width = data_width
        self.flow_id_width = flow_id_width
        self.data_mask = (1 << data_width) - 1
        self.flow_mask = (1 << flow_id_width) - 1
        # ensure auto-adapt bit reflects desired state on attach
        self.mmio.write_reg(ADDR_CTRL, CTRL_AUTO_ADAPT if auto_adapt else 0)

    # ---- basic status -------------------------------------------------
    def status(self) -> int:
        return self.mmio.read_reg(ADDR_STATUS)

    def host_in_ready(self) -> bool:
        return bool(self.status() & STATUS_HOST_IN_READY)

    def output_pending(self) -> bool:
        return bool(self.status() & STATUS_OUTPUT_PENDING)

    def soft_reset(self, via_bridge: bool = False) -> None:
        if via_bridge:
            self.mmio.soft_reset()
        else:
            self.mmio.write_reg(ADDR_CTRL, CTRL_SOFT_RESET | CTRL_AUTO_ADAPT)

    # ---- I/O helpers --------------------------------------------------
    @staticmethod
    def _twos(value: int, width: int) -> int:
        sign_bit = 1 << (width - 1)
        mask = (1 << width) - 1
        v = value & mask
        return v - (1 << width) if v & sign_bit else v

    def _u(self, value: int) -> int:
        return value & self.data_mask

    # ---- single-shot trigger / result --------------------------------
    def trigger(self, flow_id: int, a: int, b: int) -> None:
        """Submit one job. Does not wait for the result."""
        if not self.host_in_ready():
            # spin briefly — this is rare unless a previous job is still in flight
            t0 = time.monotonic()
            while not self.host_in_ready():
                if time.monotonic() - t0 > 1.0:
                    raise TimeoutError("host_in_ready never asserted")
        self.mmio.write_reg(ADDR_FLOW_ID, flow_id & self.flow_mask)
        self.mmio.write_reg(ADDR_IN_A,    self._u(a))
        self.mmio.write_reg(ADDR_IN_B,    self._u(b))
        self.mmio.write_reg(ADDR_TRIGGER, 1)

    def wait_result(self, timeout_s: float = 2.0) -> WAUResult | None:
        t0 = time.monotonic()
        while True:
            st = self.status()
            if st & STATUS_OUTPUT_PENDING:
                flow_id = self.mmio.read_reg(ADDR_OUT_FLOW) & self.flow_mask
                raw     = self.mmio.read_reg(ADDR_OUT_VAL)
                value   = self._twos(raw, self.data_width)
                return WAUResult(flow_id=flow_id, value=value, latency_s=time.monotonic() - t0)
            if time.monotonic() - t0 > timeout_s:
                return None

    def execute(self, flow_id: int, a: int, b: int, *, timeout_s: float = 2.0) -> WAUResult:
        t_start = time.monotonic()
        self.trigger(flow_id, a, b)
        r = self.wait_result(timeout_s=timeout_s)
        if r is None:
            raise TimeoutError(
                f"WAU.execute(flow_id={flow_id}, a={a}, b={b}) timed out after {timeout_s}s"
            )
        r.latency_s = time.monotonic() - t_start
        return r

    # ---- observability ------------------------------------------------
    def observability(self) -> ObservabilitySnapshot:
        return ObservabilitySnapshot(
            hops            = self.mmio.read_reg(ADDR_HOPS),
            stalls          = self.mmio.read_reg(ADDR_STALLS),
            forwards        = self.mmio.read_reg(ADDR_FORWARDS),
            local_delivered = self.mmio.read_reg(ADDR_DELIVRD),
            cache_hits      = self.mmio.read_reg(ADDR_CACHE_H),
            cache_lookups   = self.mmio.read_reg(ADDR_CACHE_L),
        )
