#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CW_FILE="${1:-docs/example-pogram.cw}"
BASE_CONFIG="${2:-src/python/configs/wau_2d_multiprogram_demo.json}"
OUT_CONFIG="${3:-src/python/configs/wau_example_pogram_compiled.json}"
BENCH_FILE="${4:-benchmarks/example_pogram_benchmark.txt}"
OUT_DIR=".build/cw_example_generated"

FLOW_ID="${FLOW_ID:-90}"
PROGRAM_ID="${PROGRAM_ID:-90}"
PROGRAM_REPLICAS="${PROGRAM_REPLICAS:-2}"
PROGRAM_MAX_PARALLEL="${PROGRAM_MAX_PARALLEL:-2}"

mkdir -p "$(dirname "$OUT_CONFIG")" "$(dirname "$BENCH_FILE")" "$OUT_DIR" .build

export PYTHONPATH=src/python

now_ns() {
  date +%s%N
}

elapsed_ms() {
  local start_ns="$1"
  local end_ns="$2"
  echo $(((end_ns - start_ns) / 1000000))
}

echo "[cw-bench] compile-cw"
t0="$(now_ns)"
python3 -m waugen compile-cw \
  --program-file "$CW_FILE" \
  --flow-id "$FLOW_ID" \
  --name "cw_conv2d_residual_reference" \
  --entry "0,0" \
  --max-in-flight 4 \
  --base-config "$BASE_CONFIG" \
  --out-config "$OUT_CONFIG" \
  --replace-existing \
  --program-id "$PROGRAM_ID" \
  --program-name "cw_reference_program" \
  --program-priority 3 \
  --program-replicas "$PROGRAM_REPLICAS" \
  --program-max-parallel-flows "$PROGRAM_MAX_PARALLEL" \
  --program-load-balance least_busy
t1="$(now_ns)"

echo "[cw-bench] validate"
t2="$(now_ns)"
python3 -m waugen validate --config "$OUT_CONFIG"
t3="$(now_ns)"

echo "[cw-bench] generate"
t4="$(now_ns)"
python3 -m waugen generate --config "$OUT_CONFIG" --out "$OUT_DIR" --summary
t5="$(now_ns)"

echo "[cw-bench] run iverilog tests"
t6="$(now_ns)"
BUILD_DIR=".build/cw_iverilog"
mkdir -p "$BUILD_DIR"

run_test() {
  local name="$1"
  shift
  local out_bin="$BUILD_DIR/${name}.out"
  echo "[cw-bench][iverilog] compiling ${name}"
  iverilog -g2005-sv -I "$OUT_DIR" -s "$name" -o "$out_bin" "$@"
  echo "[cw-bench][iverilog] running ${name}"
  vvp "$out_bin"
}

# Fast/portable checks that do not assume a specific flow id/order in coordinator demo packets.
run_test tb_wau_operation_alu \
  "$OUT_DIR/wau_operation_alu.v" \
  "tests/rtl/tb_wau_operation_alu.v"

run_test tb_wau_highway_mesh \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "tests/rtl/tb_wau_highway_mesh.v"

if [[ "${INCLUDE_TOP_DEMO:-0}" == "1" ]]; then
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
fi
t7="$(now_ns)"

compile_ms="$(elapsed_ms "$t0" "$t1")"
validate_ms="$(elapsed_ms "$t2" "$t3")"
generate_ms="$(elapsed_ms "$t4" "$t5")"
iverilog_ms="$(elapsed_ms "$t6" "$t7")"
total_ms="$(elapsed_ms "$t0" "$t7")"

export CW_BENCH_FILE="$BENCH_FILE"
export CW_BENCH_CW_FILE="$CW_FILE"
export CW_BENCH_BASE_CONFIG="$BASE_CONFIG"
export CW_BENCH_OUT_CONFIG="$OUT_CONFIG"
export CW_BENCH_OUT_DIR="$OUT_DIR"
export CW_BENCH_FLOW_ID="$FLOW_ID"
export CW_BENCH_PROGRAM_ID="$PROGRAM_ID"
export CW_BENCH_COMPILE_MS="$compile_ms"
export CW_BENCH_VALIDATE_MS="$validate_ms"
export CW_BENCH_GENERATE_MS="$generate_ms"
export CW_BENCH_IVERILOG_MS="$iverilog_ms"
export CW_BENCH_TOTAL_MS="$total_ms"

python3 - <<'PY'
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

bench_path = Path(os.environ["CW_BENCH_FILE"])
cw_file = os.environ["CW_BENCH_CW_FILE"]
base_config = os.environ["CW_BENCH_BASE_CONFIG"]
out_config = Path(os.environ["CW_BENCH_OUT_CONFIG"])
out_dir = Path(os.environ["CW_BENCH_OUT_DIR"])
flow_id = int(os.environ["CW_BENCH_FLOW_ID"])
program_id = int(os.environ["CW_BENCH_PROGRAM_ID"])

compile_ms = int(os.environ["CW_BENCH_COMPILE_MS"])
validate_ms = int(os.environ["CW_BENCH_VALIDATE_MS"])
generate_ms = int(os.environ["CW_BENCH_GENERATE_MS"])
iverilog_ms = int(os.environ["CW_BENCH_IVERILOG_MS"])
total_ms = int(os.environ["CW_BENCH_TOTAL_MS"])

schedule_path = out_dir / "wau_schedule.json"
schedule = json.loads(schedule_path.read_text())
instructions = schedule.get("instructions", [])

fallback_count = sum(1 for ins in instructions if bool(ins.get("used_fallback", False)))
core_count = len({int(ins["core_index"]) for ins in instructions})
ops = sorted({str(ins["op"]) for ins in instructions})
programs = sorted({int(ins["program_id"]) for ins in instructions})
flows = sorted({int(ins["flow_id"]) for ins in instructions})

payload = json.loads(out_config.read_text())
compiled_flow = next(
    (flow for flow in payload.get("flows", []) if isinstance(flow, dict) and int(flow.get("id", -1)) == flow_id),
    None,
)
node_count = len(compiled_flow.get("nodes", [])) if isinstance(compiled_flow, dict) else 0
cw_hints = compiled_flow.get("cw_hints", {}) if isinstance(compiled_flow, dict) else {}
lane_parallelism = cw_hints.get("lane_parallelism_compiled", "n/a")
dtype = cw_hints.get("dtype", "n/a")
tile_iter = next(
    (
        int(node.get("max_iterations", 0))
        for node in (compiled_flow.get("nodes", []) if isinstance(compiled_flow, dict) else [])
        if isinstance(node, dict) and node.get("id") == "tile_counter"
    ),
    0,
)

lines = [
    "WAU CW Example Benchmark Reference",
    f"run_utc: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
    f"cw_source: {cw_file}",
    f"base_config: {base_config}",
    f"compiled_config: {out_config}",
    f"schedule_json: {schedule_path}",
    "",
    "Workload/Program",
    f"flow_id: {flow_id}",
    f"program_id: {program_id}",
    f"compiled_nodes: {node_count}",
    f"lane_parallelism_compiled: {lane_parallelism}",
    f"dtype: {dtype}",
    f"tile_counter_max_iterations: {tile_iter}",
    "",
    "Timing (ms)",
    f"compile_cw_ms: {compile_ms}",
    f"validate_ms: {validate_ms}",
    f"generate_ms: {generate_ms}",
    f"iverilog_tests_ms: {iverilog_ms}",
    f"total_ms: {total_ms}",
    "",
    "Schedule Metrics",
    f"makespan_cycles: {int(schedule.get('makespan_cycles', 0))}",
    f"instruction_count: {len(instructions)}",
    f"fallback_instruction_count: {fallback_count}",
    f"unique_core_count: {core_count}",
    f"flow_ids_in_schedule: {flows}",
    f"program_ids_in_schedule: {programs}",
    f"operations_seen: {ops}",
]

bench_path.write_text("\n".join(lines) + "\n")
print(f"[cw-bench] wrote benchmark reference: {bench_path}")
PY

echo "[cw-bench] done"
