#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-src/python/configs/wau_de0_nano_demo.json}"
OUT_DIR="src/verilog/generated"
BUILD_DIR=".build/iverilog"
# Kept fixed while BUILD_DIR is repointed per alternate-topology suite below.
ROOT_BUILD_DIR=".build/iverilog"
mkdir -p "$BUILD_DIR"

export PYTHONPATH=src/python
python3 -m waugen generate --config "$CONFIG_PATH" --out "$OUT_DIR" --summary

# Directory the current run_test invocations read RTL from. The alternate-topology
# passes at the end of this script repoint it at further generated trees.
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

  # A single row of cores: one highway line under every topology, so this one
  # is topology-agnostic.
  run_test tb_wau_highway_mesh \
    $(mesh_sources) \
    "tests/rtl/tb_wau_highway_mesh.v"

  run_test tb_wau_highway_contract \
    "$RTL_DIR/wau_highway_contract.v" \
    "tests/rtl/tb_wau_highway_contract.v"

  run_test tb_wau_host_mmio \
    "$RTL_DIR/wau_host_mmio.v" \
    "tests/rtl/tb_wau_host_mmio.v"

  # Topology-specific fabric contracts. Each asserts something only true of the
  # arrangement it names, so each runs only against RTL emitting that topology.
  if grep -q '`define WAU_HIGHWAY_TOPOLOGY_LINES' "$RTL_DIR/wau_defs.vh"; then
    # The default: one independent highway per line of cores, each with its own
    # coordinator hub.
    run_test tb_wau_highway_lines \
      $(mesh_sources) \
      "tests/rtl/tb_wau_highway_lines.v"
  else
    # Vertical up/down links exist only where a single highway spans the grid.
    run_test tb_wau_highway_mesh_3d \
      $(mesh_sources) \
      "tests/rtl/tb_wau_highway_mesh_3d.v"
  fi

  if grep -q '`define WAU_HIGHWAY_TOPOLOGY_CHAIN' "$RTL_DIR/wau_defs.vh"; then
    run_test tb_wau_highway_chain \
      $(mesh_sources) \
      "tests/rtl/tb_wau_highway_chain.v"

    run_test tb_wau_highway_chain_3d \
      $(mesh_sources) \
      "tests/rtl/tb_wau_highway_chain_3d.v"
  fi
}

run_suite

# The `chain` and `matrix` highway topologies are opt-in, so the default config
# never elaborates them. Generate a tree from each tracked demo and re-run the
# fabric suite against it, otherwise those code paths ship unexercised.
for alt in chain matrix; do
  ALT_CONFIG="src/python/configs/wau_${alt}_highway_demo.json"
  ALT_DIR="$ROOT_BUILD_DIR/${alt}_rtl"
  echo "[iverilog] regenerating ${alt}-topology RTL into ${ALT_DIR}"
  python3 -m waugen generate --config "$ALT_CONFIG" --out "$ALT_DIR"

  RTL_DIR="$ALT_DIR"
  BUILD_DIR="$ROOT_BUILD_DIR/${alt}"
  mkdir -p "$BUILD_DIR"
  run_suite
done

echo "All iverilog tests passed"
