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

# Directory the current run_test invocations read RTL from. The matrix-topology
# pass at the end of this script repoints it at a second generated tree.
RTL_DIR="$OUT_DIR"

run_test() {
  local name="$1"
  shift
  local out_bin="$BUILD_DIR/${name}.out"

  echo "[iverilog] compiling ${name}"
  iverilog -g2005-sv -I "$RTL_DIR" -s "$name" -o "$out_bin" "$@"

  echo "[iverilog] running ${name}"
  vvp "$out_bin"
}

# Shared source sets. The highway fabric now carries the contract bus, so every
# testbench that elaborates a mesh needs wau_highway_contract.v.
mesh_sources() {
  echo "$RTL_DIR/wau_neighbor_forward.v" \
       "$RTL_DIR/wau_highway_contract.v" \
       "$RTL_DIR/wau_highway_router.v" \
       "$RTL_DIR/wau_highway_mesh.v"
}

top_sources() {
  echo "$RTL_DIR/wau_operation_alu.v" \
       $(mesh_sources) \
       "$RTL_DIR/wau_core_station.v" \
       "$RTL_DIR/wau_core.v" \
       "$RTL_DIR/wau_coordinator.v" \
       "$RTL_DIR/wau_top.v"
}

run_suite() {
  run_test tb_wau_operation_alu \
    "$RTL_DIR/wau_operation_alu.v" \
    "tests/rtl/tb_wau_operation_alu.v"

  run_test tb_wau_top_demo \
    $(top_sources) \
    "tests/rtl/tb_wau_top_demo.v"

  run_test tb_wau_coordinator_multiissue \
    $(top_sources) \
    "tests/rtl/tb_wau_coordinator_multiissue.v"

  run_test tb_wau_highway_mesh \
    $(mesh_sources) \
    "tests/rtl/tb_wau_highway_mesh.v"

  run_test tb_wau_highway_mesh_3d \
    $(mesh_sources) \
    "tests/rtl/tb_wau_highway_mesh_3d.v"

  run_test tb_wau_highway_contract \
    "$RTL_DIR/wau_highway_contract.v" \
    "tests/rtl/tb_wau_highway_contract.v"

  run_test tb_wau_host_mmio \
    "$RTL_DIR/wau_host_mmio.v" \
    "tests/rtl/tb_wau_host_mmio.v"
}

run_suite

# tb_wau_highway_linear asserts the *default* single-dimension chain (including
# the row-to-row wrap hop), so it only makes sense against linear RTL.
if grep -q '`define WAU_HIGHWAY_TOPOLOGY_LINEAR' "$RTL_DIR/wau_defs.vh"; then
  run_test tb_wau_highway_linear \
    $(mesh_sources) \
    "tests/rtl/tb_wau_highway_linear.v"

  run_test tb_wau_highway_linear_3d \
    $(mesh_sources) \
    "tests/rtl/tb_wau_highway_linear_3d.v"
fi

# The `matrix` highway topology is opt-in, so the default config never
# elaborates it. Generate a second tree from the tracked matrix demo and re-run
# the fabric suite against it, otherwise that code path ships unexercised.
MATRIX_CONFIG="src/python/configs/wau_matrix_highway_demo.json"
MATRIX_DIR="$BUILD_DIR/matrix_rtl"
echo "[iverilog] regenerating matrix-topology RTL into ${MATRIX_DIR}"
python3 -m waugen generate --config "$MATRIX_CONFIG" --out "$MATRIX_DIR"

RTL_DIR="$MATRIX_DIR"
BUILD_DIR="$BUILD_DIR/matrix"
mkdir -p "$BUILD_DIR"
run_suite

echo "All iverilog tests passed"
