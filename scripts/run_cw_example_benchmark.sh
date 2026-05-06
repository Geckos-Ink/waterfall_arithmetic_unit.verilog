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
EXEC_FLOW_ID="${EXEC_FLOW_ID:-$FLOW_ID}"
EXEC_TIMEOUT_CYCLES="${EXEC_TIMEOUT_CYCLES:-5000}"

BUILD_DIR=".build/cw_iverilog"
ALU_LOG="$BUILD_DIR/tb_wau_operation_alu.run.log"
MESH_LOG="$BUILD_DIR/tb_wau_highway_mesh.run.log"
EXEC_LOG="$BUILD_DIR/tb_wau_cw_compiled_exec.run.log"
TOP_LOG="$BUILD_DIR/tb_wau_top_demo.run.log"
CW_EXEC_TB="$BUILD_DIR/tb_wau_cw_compiled_exec.v"

mkdir -p "$(dirname "$OUT_CONFIG")" "$(dirname "$BENCH_FILE")" "$OUT_DIR" "$BUILD_DIR"

export PYTHONPATH=src/python

now_ns() {
  date +%s%N
}

elapsed_ms() {
  local start_ns="$1"
  local end_ns="$2"
  echo $(((end_ns - start_ns) / 1000000))
}

run_test() {
  local name="$1"
  local run_log="$2"
  shift 2
  local out_bin="$BUILD_DIR/${name}.out"

  echo "[cw-bench][iverilog] compiling ${name}"
  iverilog -g2005-sv -I "$OUT_DIR" -s "$name" -o "$out_bin" "$@"

  echo "[cw-bench][iverilog] running ${name}"
  if ! vvp "$out_bin" | tee "$run_log"; then
    echo "[cw-bench][iverilog] FAIL: ${name}"
    return 1
  fi
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

echo "[cw-bench] synthesize CW execution testbench"
export CW_BENCH_OUT_CONFIG="$OUT_CONFIG"
export CW_BENCH_EXEC_FLOW_ID="$EXEC_FLOW_ID"
export CW_BENCH_EXEC_TIMEOUT_CYCLES="$EXEC_TIMEOUT_CYCLES"
export CW_BENCH_EXEC_TB="$CW_EXEC_TB"
python3 - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

out_config = Path(os.environ["CW_BENCH_OUT_CONFIG"])
exec_flow_id = int(os.environ["CW_BENCH_EXEC_FLOW_ID"])
timeout_cycles = int(os.environ["CW_BENCH_EXEC_TIMEOUT_CYCLES"])
tb_path = Path(os.environ["CW_BENCH_EXEC_TB"])

payload = json.loads(out_config.read_text())
flow_ids = sorted(
    int(flow["id"])
    for flow in payload.get("flows", [])
    if isinstance(flow, dict) and "id" in flow and isinstance(flow.get("id"), int)
)
if exec_flow_id not in flow_ids:
    raise SystemExit(
        f"Configured EXEC_FLOW_ID={exec_flow_id} is missing in {out_config}; available flow ids: {flow_ids}"
    )

cases = [
    (1, 10, 4),
    (2, -7, 5),
    (3, 21, -3),
]


def sv_lit(value: int) -> str:
    if value < 0:
        return f"-32'sd{abs(value)}"
    return f"32'sd{value}"


case_lines = "\n".join(
    [
        f"        send_and_expect({exec_flow_id}, {sv_lit(a)}, {sv_lit(b)}, {case_id});"
        for case_id, a, b in cases
    ]
)

tb = f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module tb_wau_cw_compiled_exec;
    reg clk;
    reg rst_n;

    reg host_in_valid;
    wire host_in_ready;
    reg [`WAU_FLOW_ID_WIDTH-1:0] host_in_flow_id;
    reg signed [`WAU_DATA_WIDTH-1:0] host_in_a;
    reg signed [`WAU_DATA_WIDTH-1:0] host_in_b;

    wire host_out_valid;
    reg host_out_ready;
    wire [`WAU_FLOW_ID_WIDTH-1:0] host_out_flow_id;
    wire signed [`WAU_DATA_WIDTH-1:0] host_out_value;

    reg enable_auto_adapt;
    integer cycle_count;

    wau_top dut (
        .clk(clk),
        .rst_n(rst_n),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(host_out_ready),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .enable_auto_adapt(enable_auto_adapt)
    );

    always #5 clk = ~clk;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cycle_count <= 0;
        end else begin
            cycle_count <= cycle_count + 1;
        end
    end

    task automatic send_packet;
        input [`WAU_FLOW_ID_WIDTH-1:0] flow_id;
        input signed [`WAU_DATA_WIDTH-1:0] a;
        input signed [`WAU_DATA_WIDTH-1:0] b;
        begin
            @(negedge clk);
            host_in_flow_id = flow_id;
            host_in_a = a;
            host_in_b = b;
            host_in_valid = 1'b1;

            while (!host_in_ready) begin
                @(posedge clk);
            end

            @(negedge clk);
            host_in_valid = 1'b0;
        end
    endtask

    task automatic send_and_expect;
        input [`WAU_FLOW_ID_WIDTH-1:0] flow_id;
        input signed [`WAU_DATA_WIDTH-1:0] a;
        input signed [`WAU_DATA_WIDTH-1:0] b;
        input integer case_id;
        integer start_cycle;
        integer timeout;
        integer matched;
        begin
            send_packet(flow_id, a, b);
            start_cycle = cycle_count;
            matched = 0;

            for (timeout = 0; timeout < {timeout_cycles}; timeout = timeout + 1) begin
                @(posedge clk);
                if (host_out_valid) begin
                    if (host_out_flow_id !== flow_id) begin
                        $display("FAIL: expected flow=%0d got flow=%0d", flow_id, host_out_flow_id);
                        $fatal(1);
                    end
                    matched = 1;
                    $display(
                        "CW_EXEC_BENCH case=%0d flow=%0d latency_cycles=%0d out_value=%0d",
                        case_id,
                        flow_id,
                        (cycle_count - start_cycle),
                        host_out_value
                    );
                    timeout = {timeout_cycles};
                end
            end

            if (!matched) begin
                $display("FAIL: timeout waiting output flow=%0d", flow_id);
                $fatal(1);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        host_in_valid = 1'b0;
        host_in_flow_id = {{`WAU_FLOW_ID_WIDTH{{1'b0}}}};
        host_in_a = {{`WAU_DATA_WIDTH{{1'b0}}}};
        host_in_b = {{`WAU_DATA_WIDTH{{1'b0}}}};
        host_out_ready = 1'b1;
        enable_auto_adapt = 1'b1;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

{case_lines}

        $display("PASS: tb_wau_cw_compiled_exec");
        $finish;
    end
endmodule
"""

tb_path.write_text(tb)
print(f"[cw-bench] generated CW execution testbench: {tb_path}")
PY

echo "[cw-bench] run iverilog tests"
t6="$(now_ns)"

run_test tb_wau_operation_alu "$ALU_LOG" \
  "$OUT_DIR/wau_operation_alu.v" \
  "tests/rtl/tb_wau_operation_alu.v"

run_test tb_wau_highway_mesh "$MESH_LOG" \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "tests/rtl/tb_wau_highway_mesh.v"

run_test tb_wau_cw_compiled_exec "$EXEC_LOG" \
  "$OUT_DIR/wau_operation_alu.v" \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "$OUT_DIR/wau_core_station.v" \
  "$OUT_DIR/wau_core.v" \
  "$OUT_DIR/wau_coordinator.v" \
  "$OUT_DIR/wau_top.v" \
  "$CW_EXEC_TB"

TOP_TEST_RAN=0
if [[ "${INCLUDE_TOP_DEMO:-0}" == "1" ]]; then
  TOP_TEST_RAN=1
  run_test tb_wau_top_demo "$TOP_LOG" \
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

git_commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if git diff --quiet && git diff --cached --quiet; then
  git_tree_state="clean"
else
  git_tree_state="dirty"
fi
python_version="$(python3 --version 2>/dev/null || echo unknown)"
iverilog_version="$(iverilog -V 2>/dev/null | head -n 1 || echo unknown)"

export CW_BENCH_FILE="$BENCH_FILE"
export CW_BENCH_CW_FILE="$CW_FILE"
export CW_BENCH_BASE_CONFIG="$BASE_CONFIG"
export CW_BENCH_OUT_CONFIG="$OUT_CONFIG"
export CW_BENCH_OUT_DIR="$OUT_DIR"
export CW_BENCH_FLOW_ID="$FLOW_ID"
export CW_BENCH_PROGRAM_ID="$PROGRAM_ID"
export CW_BENCH_EXEC_FLOW_ID="$EXEC_FLOW_ID"
export CW_BENCH_COMPILE_MS="$compile_ms"
export CW_BENCH_VALIDATE_MS="$validate_ms"
export CW_BENCH_GENERATE_MS="$generate_ms"
export CW_BENCH_IVERILOG_MS="$iverilog_ms"
export CW_BENCH_TOTAL_MS="$total_ms"
export CW_BENCH_ALU_LOG="$ALU_LOG"
export CW_BENCH_MESH_LOG="$MESH_LOG"
export CW_BENCH_EXEC_LOG="$EXEC_LOG"
export CW_BENCH_TOP_LOG="$TOP_LOG"
export CW_BENCH_TOP_TEST_RAN="$TOP_TEST_RAN"
export CW_BENCH_GIT_COMMIT="$git_commit"
export CW_BENCH_GIT_TREE_STATE="$git_tree_state"
export CW_BENCH_PYTHON_VERSION="$python_version"
export CW_BENCH_IVERILOG_VERSION="$iverilog_version"

python3 - <<'PY'
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path


def parse_test_log(path: Path) -> tuple[str, int | None]:
    if not path.exists():
        return ("not_run", None)
    text = path.read_text()
    if "FATAL:" in text or "FAIL:" in text:
        status = "fail"
    elif "PASS:" in text:
        status = "pass"
    else:
        status = "unknown"

    finish_match = re.search(r"\$finish called at\s+([0-9]+)\s+\(1ps\)", text)
    finish_ps = int(finish_match.group(1)) if finish_match else None
    return (status, finish_ps)


def parse_exec_metrics(exec_log_path: Path) -> list[dict[str, int]]:
    if not exec_log_path.exists():
        return []
    rows: list[dict[str, int]] = []
    pattern = re.compile(
        r"CW_EXEC_BENCH case=(?P<case>[0-9]+) flow=(?P<flow>[0-9]+) "
        r"latency_cycles=(?P<lat>[0-9]+) out_value=(?P<out>-?[0-9]+)"
    )
    for line in exec_log_path.read_text().splitlines():
        match = pattern.search(line)
        if not match:
            continue
        rows.append(
            {
                "case": int(match.group("case")),
                "flow": int(match.group("flow")),
                "latency_cycles": int(match.group("lat")),
                "out_value": int(match.group("out")),
            }
        )
    return sorted(rows, key=lambda row: row["case"])


bench_path = Path(os.environ["CW_BENCH_FILE"])
cw_file = os.environ["CW_BENCH_CW_FILE"]
base_config = os.environ["CW_BENCH_BASE_CONFIG"]
out_config = Path(os.environ["CW_BENCH_OUT_CONFIG"])
out_dir = Path(os.environ["CW_BENCH_OUT_DIR"])
flow_id = int(os.environ["CW_BENCH_FLOW_ID"])
program_id = int(os.environ["CW_BENCH_PROGRAM_ID"])
exec_flow_id = int(os.environ["CW_BENCH_EXEC_FLOW_ID"])

compile_ms = int(os.environ["CW_BENCH_COMPILE_MS"])
validate_ms = int(os.environ["CW_BENCH_VALIDATE_MS"])
generate_ms = int(os.environ["CW_BENCH_GENERATE_MS"])
iverilog_ms = int(os.environ["CW_BENCH_IVERILOG_MS"])
total_ms = int(os.environ["CW_BENCH_TOTAL_MS"])

git_commit = os.environ["CW_BENCH_GIT_COMMIT"]
git_tree_state = os.environ["CW_BENCH_GIT_TREE_STATE"]
python_version = os.environ["CW_BENCH_PYTHON_VERSION"]
iverilog_version = os.environ["CW_BENCH_IVERILOG_VERSION"]

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

alu_log = Path(os.environ["CW_BENCH_ALU_LOG"])
mesh_log = Path(os.environ["CW_BENCH_MESH_LOG"])
exec_log = Path(os.environ["CW_BENCH_EXEC_LOG"])
top_log = Path(os.environ["CW_BENCH_TOP_LOG"])
top_test_ran = os.environ["CW_BENCH_TOP_TEST_RAN"] == "1"

tests = [
    ("tb_wau_operation_alu", alu_log),
    ("tb_wau_highway_mesh", mesh_log),
    ("tb_wau_cw_compiled_exec", exec_log),
]
if top_test_ran:
    tests.append(("tb_wau_top_demo", top_log))

test_lines: list[str] = []
for name, path in tests:
    status, finish_ps = parse_test_log(path)
    finish_repr = str(finish_ps) if finish_ps is not None else "n/a"
    test_lines.append(f"{name}: {status}, finish_ps={finish_repr}, log={path}")

exec_rows = parse_exec_metrics(exec_log)
latencies = [row["latency_cycles"] for row in exec_rows]
lat_min = min(latencies) if latencies else 0
lat_max = max(latencies) if latencies else 0
lat_avg = (sum(latencies) / len(latencies)) if latencies else 0.0

lines = [
    "WAU CW Example Benchmark Reference (latest)",
    f"run_utc: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
    f"git_commit: {git_commit}",
    f"git_tree_state: {git_tree_state}",
    f"python_version: {python_version}",
    f"iverilog_version: {iverilog_version}",
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
    "Effective Execution Benchmark (CW flow smoke)",
    f"exec_flow_id: {exec_flow_id}",
    f"exec_case_count: {len(exec_rows)}",
    f"exec_latency_cycles_min: {lat_min}",
    f"exec_latency_cycles_max: {lat_max}",
    f"exec_latency_cycles_avg: {lat_avg:.2f}",
]

for row in exec_rows:
    lines.append(
        f"exec_case_{row['case']}: flow={row['flow']}, latency_cycles={row['latency_cycles']}, out_value={row['out_value']}"
    )

lines.extend(
    [
        "",
        "Schedule Metrics",
        f"makespan_cycles: {int(schedule.get('makespan_cycles', 0))}",
        f"instruction_count: {len(instructions)}",
        f"fallback_instruction_count: {fallback_count}",
        f"unique_core_count: {core_count}",
        f"flow_ids_in_schedule: {flows}",
        f"program_ids_in_schedule: {programs}",
        f"operations_seen: {ops}",
        "",
        "RTL Test Results",
    ]
)
lines.extend(test_lines)

bench_path.write_text("\n".join(lines) + "\n")
print(f"[cw-bench] wrote benchmark reference: {bench_path}")
PY

echo "[cw-bench] done"
