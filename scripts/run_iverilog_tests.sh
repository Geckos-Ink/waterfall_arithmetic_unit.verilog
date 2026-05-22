#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-src/python/configs/wau_de0_nano_demo.json}"
OUT_DIR="src/verilog/generated"
BUILD_DIR=".build/iverilog"
mkdir -p "$BUILD_DIR"

export PYTHONPATH=src/python
python3 -m waugen generate --config "$CONFIG_PATH" --out "$OUT_DIR" --summary

run_test() {
  local name="$1"
  shift
  local out_bin="$BUILD_DIR/${name}.out"

  echo "[iverilog] compiling ${name}"
  iverilog -g2005-sv -I "$OUT_DIR" -s "$name" -o "$out_bin" "$@"

  echo "[iverilog] running ${name}"
  vvp "$out_bin"
}

run_test tb_wau_operation_alu \
  "$OUT_DIR/wau_operation_alu.v" \
  "tests/rtl/tb_wau_operation_alu.v"

run_test tb_wau_top_demo \
  "$OUT_DIR/wau_operation_alu.v" \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "$OUT_DIR/wau_core_station.v" \
  "$OUT_DIR/wau_core.v" \
  "$OUT_DIR/wau_coordinator.v" \
  "$OUT_DIR/wau_top.v" \
  "tests/rtl/tb_wau_top_demo.v"

run_test tb_wau_highway_mesh \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "tests/rtl/tb_wau_highway_mesh.v"

run_test tb_wau_host_mmio \
  "$OUT_DIR/wau_host_mmio.v" \
  "tests/rtl/tb_wau_host_mmio.v"

echo "All iverilog tests passed"
