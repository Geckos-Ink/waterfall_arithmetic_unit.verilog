#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CW_FILE="${1:-CWs/example-program.cw}"
BASE_CONFIG="${2:-src/python/configs/wau_2d_multiprogram_demo.json}"
OUT_CONFIG="${3:-src/python/configs/wau_example_pogram_compiled.json}"
BENCH_FILE="${4:-benchmarks/example_pogram_benchmark.txt}"
OUT_DIR="${OUT_DIR:-.build/cw_example_generated}"

FLOW_ID="${FLOW_ID:-90}"
PROGRAM_ID="${PROGRAM_ID:-90}"
PROGRAM_PRIORITY="${PROGRAM_PRIORITY:-}"
PROGRAM_REPLICAS="${PROGRAM_REPLICAS:-2}"
PROGRAM_MAX_PARALLEL="${PROGRAM_MAX_PARALLEL:-1}"
PROGRAM_LOAD_BALANCE="${PROGRAM_LOAD_BALANCE:-}"
SCHEDULER_PROGRAM_POLICY="${SCHEDULER_PROGRAM_POLICY:-}"
EXEC_FLOW_ID="${EXEC_FLOW_ID:-$FLOW_ID}"
EXEC_TIMEOUT_CYCLES="${EXEC_TIMEOUT_CYCLES:-5000}"
CW_MAX_IN_FLIGHT="${CW_MAX_IN_FLIGHT:-4}"
CW_LANE_PARALLELISM="${CW_LANE_PARALLELISM:-}"
CW_DTYPE="${CW_DTYPE:-}"
CW_PLACEMENT_POLICY="${CW_PLACEMENT_POLICY:-}"
CW_LOWERING_PROFILE="${CW_LOWERING_PROFILE:-}"

TUNE_MODE="${TUNE_MODE:-0}"
TUNE_SEARCH_MODE="${TUNE_SEARCH_MODE:-coordinate}"
TUNE_LANE_PARALLELISM_SET="${TUNE_LANE_PARALLELISM_SET:-2,4,6}"
TUNE_PROGRAM_REPLICAS_SET="${TUNE_PROGRAM_REPLICAS_SET:-1,2}"
TUNE_PROGRAM_MAX_PARALLEL_SET="${TUNE_PROGRAM_MAX_PARALLEL_SET:-1,2}"
TUNE_PROGRAM_PRIORITY_SET="${TUNE_PROGRAM_PRIORITY_SET:-3,4,5}"
TUNE_PROGRAM_LOAD_BALANCE_SET="${TUNE_PROGRAM_LOAD_BALANCE_SET:-least_busy,round_robin}"
TUNE_SCHEDULER_PROGRAM_POLICY_SET="${TUNE_SCHEDULER_PROGRAM_POLICY_SET:-weighted_fair,strict_priority,round_robin}"
TUNE_CW_MAX_IN_FLIGHT_SET="${TUNE_CW_MAX_IN_FLIGHT_SET:-2,4}"
TUNE_PLACEMENT_POLICY_SET="${TUNE_PLACEMENT_POLICY_SET:-locality,balance}"
TUNE_LOWERING_PROFILE_SET="${TUNE_LOWERING_PROFILE_SET:-latency_optimized,reference,throughput_optimized}"
TUNE_SUMMARY_FILE="${TUNE_SUMMARY_FILE:-benchmarks/example_pogram_tuning_latest.txt}"
REPLAY_MODE="${REPLAY_MODE:-off}"
REPLAY_SUMMARY_FILE="${REPLAY_SUMMARY_FILE:-$TUNE_SUMMARY_FILE}"
REPLAY_OUTPUT_FILE="${REPLAY_OUTPUT_FILE:-benchmarks/example_pogram_replay_latest.txt}"
REPLAY_ROOT="${REPLAY_ROOT:-.build/cw_replay}"
REPLAY_REQUIRE_ALL_PASS="${REPLAY_REQUIRE_ALL_PASS:-1}"
MULTI_RUNS="${MULTI_RUNS:-1}"
MULTI_REQUIRE_ALL_PASS="${MULTI_REQUIRE_ALL_PASS:-1}"
MULTI_SUMMARY_FILE="${MULTI_SUMMARY_FILE:-benchmarks/example_pogram_multirun_latest.txt}"

REGRESSION_CHECK="${REGRESSION_CHECK:-0}"
REGRESSION_BASELINE_JSON="${REGRESSION_BASELINE_JSON:-benchmarks/example_pogram_benchmark_best.json}"
REGRESSION_ALLOW_MISSING_BASELINE="${REGRESSION_ALLOW_MISSING_BASELINE:-1}"
REGRESSION_MAX_LATENCY_DELTA="${REGRESSION_MAX_LATENCY_DELTA:-0.00}"
REGRESSION_MAX_MAKESPAN_DELTA="${REGRESSION_MAX_MAKESPAN_DELTA:-0}"
REGRESSION_MAX_TOTAL_MS_DELTA="${REGRESSION_MAX_TOTAL_MS_DELTA:-250}"

SIDECAR_LATEST_JSON="${SIDECAR_LATEST_JSON:-benchmarks/example_pogram_benchmark_latest.json}"
SIDECAR_BEST_JSON="${SIDECAR_BEST_JSON:-benchmarks/example_pogram_benchmark_best.json}"
SIDECAR_HISTORY_JSON="${SIDECAR_HISTORY_JSON:-benchmarks/example_pogram_benchmark_history.json}"
SIDECAR_HISTORY_KEEP="${SIDECAR_HISTORY_KEEP:-200}"
UPDATE_BENCH_SIDECAR="${UPDATE_BENCH_SIDECAR:-1}"
RUN_PROFILE="${RUN_PROFILE:-reference}"

BUILD_DIR="${BUILD_DIR:-.build/cw_iverilog}"
ALU_LOG="$BUILD_DIR/tb_wau_operation_alu.run.log"
MESH_LOG="$BUILD_DIR/tb_wau_highway_mesh.run.log"
EXEC_LOG="$BUILD_DIR/tb_wau_cw_compiled_exec.run.log"
TOP_LOG="$BUILD_DIR/tb_wau_top_demo.run.log"
CW_EXEC_TB="$BUILD_DIR/tb_wau_cw_compiled_exec.v"

mkdir -p \
  "$(dirname "$OUT_CONFIG")" \
  "$(dirname "$BENCH_FILE")" \
  "$(dirname "$TUNE_SUMMARY_FILE")" \
  "$(dirname "$REPLAY_OUTPUT_FILE")" \
  "$(dirname "$MULTI_SUMMARY_FILE")" \
  "$(dirname "$SIDECAR_LATEST_JSON")" \
  "$(dirname "$SIDECAR_BEST_JSON")" \
  "$(dirname "$SIDECAR_HISTORY_JSON")" \
  "$OUT_DIR" \
  "$BUILD_DIR"

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

trim_csv_token() {
  local token="$1"
  token="${token#"${token%%[![:space:]]*}"}"
  token="${token%"${token##*[![:space:]]}"}"
  echo "$token"
}

bench_field() {
  local file="$1"
  local key="$2"
  awk -F': ' -v key="$key" '$1 == key {print $2}' "$file" | tail -n 1
}

render_knob_value() {
  local value="${1:-}"
  if [[ -n "$value" ]]; then
    echo "$value"
  else
    echo "auto"
  fi
}

prepare_effective_base_config() {
  local source_config="$1"
  local target_config="$2"

  if [[ -z "$SCHEDULER_PROGRAM_POLICY" ]]; then
    echo "$source_config"
    return 0
  fi

  export CW_BENCH_BASE_SOURCE="$source_config"
  export CW_BENCH_BASE_TARGET="$target_config"
  export CW_BENCH_SCHEDULER_POLICY="$SCHEDULER_PROGRAM_POLICY"
  python3 - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

source = Path(os.environ["CW_BENCH_BASE_SOURCE"])
target = Path(os.environ["CW_BENCH_BASE_TARGET"])
policy = os.environ["CW_BENCH_SCHEDULER_POLICY"]

payload = json.loads(source.read_text())
scheduler = payload.setdefault("scheduler", {})
if not isinstance(scheduler, dict):
    raise SystemExit("scheduler must be an object in base config")
scheduler["program_policy"] = policy

target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, indent=2) + "\n")
print(target)
PY
}

run_tune_candidate() {
  local stage="$1"
  local run_name="$2"
  local run_bench="$3"
  local run_out_dir="$4"
  local run_build_dir="$5"
  local lane="$6"
  local rep="$7"
  local mp="$8"
  local priority="$9"
  local load_balance="${10}"
  local scheduler_policy="${11}"
  local max_in_flight="${12}"
  local placement_policy="${13}"
  local lowering_profile="${14}"
  local rows_file="${15}"
  local candidate_profile="autotune_candidate"
  if [[ "$stage" == replay_* ]]; then
    candidate_profile="autotune_replay_candidate"
  fi

  echo "[cw-bench][tune] stage=${stage} run=${run_name}"

  if TUNE_MODE=0 \
    REPLAY_MODE=off \
    MULTI_RUNS=1 \
    REGRESSION_CHECK=0 \
    UPDATE_BENCH_SIDECAR=0 \
    RUN_PROFILE="$candidate_profile" \
    CW_LANE_PARALLELISM="$lane" \
    PROGRAM_REPLICAS="$rep" \
    PROGRAM_MAX_PARALLEL="$mp" \
    PROGRAM_PRIORITY="$priority" \
    PROGRAM_LOAD_BALANCE="$load_balance" \
    SCHEDULER_PROGRAM_POLICY="$scheduler_policy" \
    CW_MAX_IN_FLIGHT="$max_in_flight" \
    CW_PLACEMENT_POLICY="$placement_policy" \
    CW_LOWERING_PROFILE="$lowering_profile" \
    OUT_DIR="$run_out_dir" \
    BUILD_DIR="$run_build_dir" \
    "${BASH:-bash}" "$0" "$CW_FILE" "$BASE_CONFIG" "$OUT_CONFIG" "$run_bench"; then
    local lat_avg
    local lat_p95
    local makespan
    local fallback_ratio
    local hops_total
    local total_ms
    lat_avg="$(bench_field "$run_bench" "exec_latency_cycles_avg")"
    lat_p95="$(bench_field "$run_bench" "exec_latency_cycles_p95")"
    makespan="$(bench_field "$run_bench" "makespan_cycles")"
    fallback_ratio="$(bench_field "$run_bench" "fallback_instruction_ratio")"
    hops_total="$(bench_field "$run_bench" "estimated_transfer_hops_total")"
    total_ms="$(bench_field "$run_bench" "total_ms")"
    echo "${stage},${run_name},pass,${lane},${rep},${mp},${priority},${load_balance},${scheduler_policy},${max_in_flight},${placement_policy},${lowering_profile},${lat_avg},${lat_p95},${makespan},${fallback_ratio},${hops_total},${total_ms},${run_bench}" >> "$rows_file"
  else
    echo "${stage},${run_name},fail,${lane},${rep},${mp},${priority},${load_balance},${scheduler_policy},${max_in_flight},${placement_policy},${lowering_profile},inf,inf,inf,inf,inf,inf,${run_bench}" >> "$rows_file"
  fi
}

pick_best_tune_row() {
  local rows_file="$1"
  local stage_filter="$2"
  export CW_TUNE_PICK_ROWS="$rows_file"
  export CW_TUNE_PICK_STAGE="$stage_filter"
  python3 - <<'PY'
from __future__ import annotations

import csv
import math
import os

rows_path = os.environ["CW_TUNE_PICK_ROWS"]
stage_filter = os.environ["CW_TUNE_PICK_STAGE"]

rows: list[dict[str, str]] = []
with open(rows_path, newline="") as f:
    reader = csv.reader(f)
    for row in reader:
        if len(row) != 19:
            continue
        (
            stage,
            run_name,
            status,
            lane,
            rep,
            mp,
            priority,
            load_balance,
            scheduler_policy,
            max_in_flight,
            placement_policy,
            lowering_profile,
            lat_avg,
            lat_p95,
            makespan,
            fallback_ratio,
            hops_total,
            total_ms,
            bench_path,
        ) = row
        if stage_filter != "all" and stage != stage_filter:
            continue
        rows.append(
            {
                "stage": stage,
                "run_name": run_name,
                "status": status,
                "lane": lane,
                "rep": rep,
                "mp": mp,
                "priority": priority,
                "load_balance": load_balance,
                "scheduler_policy": scheduler_policy,
                "max_in_flight": max_in_flight,
                "placement_policy": placement_policy,
                "lowering_profile": lowering_profile,
                "lat_avg": lat_avg,
                "lat_p95": lat_p95,
                "makespan": makespan,
                "fallback_ratio": fallback_ratio,
                "hops_total": hops_total,
                "total_ms": total_ms,
                "bench_path": bench_path,
            }
        )

if not rows:
    raise SystemExit("No tuning rows available")


def as_float(value: str) -> float:
    if value == "inf":
        return math.inf
    return float(value)


def as_int(value: str) -> int:
    if value == "inf":
        return 2**31 - 1
    return int(float(value))


def show(value: str) -> str:
    return value if value else "auto"


passing = [row for row in rows if row["status"] == "pass"]
if not passing:
    raise SystemExit("No successful tuning rows available")

best = min(
    passing,
    key=lambda row: (
        as_float(row["lat_avg"]),
        as_float(row["lat_p95"]),
        as_int(row["makespan"]),
        as_float(row["fallback_ratio"]),
        as_int(row["hops_total"]),
        as_int(row["total_ms"]),
    ),
)
print(
    "|".join(
        [
            best["lane"],
            best["rep"],
            best["mp"],
            best["priority"],
            best["load_balance"],
            best["scheduler_policy"],
            best["max_in_flight"],
            best["placement_policy"],
            best["lowering_profile"],
            best["lat_avg"],
            best["lat_p95"],
            best["makespan"],
            best["fallback_ratio"],
            best["hops_total"],
            best["total_ms"],
            best["bench_path"],
            best["run_name"],
        ]
    )
)
PY
}

if [[ "$TUNE_MODE" == "1" && "$MULTI_RUNS" -gt 1 ]]; then
  echo "[cw-bench] ERROR: TUNE_MODE=1 cannot be combined with MULTI_RUNS>1"
  exit 2
fi

if [[ "$REPLAY_MODE" != "off" ]]; then
  if [[ "$TUNE_MODE" == "1" || "$MULTI_RUNS" -gt 1 || "$REGRESSION_CHECK" == "1" ]]; then
    echo "[cw-bench] ERROR: REPLAY_MODE cannot be combined with TUNE_MODE=1, MULTI_RUNS>1, or REGRESSION_CHECK=1"
    exit 2
  fi

  case "$REPLAY_MODE" in
    best|stage-winners|best-and-stage-winners|worst)
      ;;
    *)
      echo "[cw-bench] ERROR: unsupported REPLAY_MODE=${REPLAY_MODE}"
      echo "[cw-bench] supported replay modes: best, stage-winners, best-and-stage-winners, worst"
      exit 2
      ;;
  esac

  echo "[cw-bench] replay mode enabled (mode=${REPLAY_MODE}, summary=${REPLAY_SUMMARY_FILE})"
  REPLAY_PLAN="$REPLAY_ROOT/plan.txt"
  REPLAY_ROWS="$REPLAY_ROOT/rows.csv"
  mkdir -p "$REPLAY_ROOT" "$(dirname "$REPLAY_OUTPUT_FILE")"
  : > "$REPLAY_ROWS"

  python3 -m waugen.benchmark_replay \
    --summary "$REPLAY_SUMMARY_FILE" \
    --mode "$REPLAY_MODE" \
    --format shell > "$REPLAY_PLAN"

  replay_idx=0
  while IFS='|' read -r source_stage source_run lane rep mp priority load_balance scheduler_policy max_in_flight placement_policy lowering_profile _expected_lat _expected_p95 _expected_makespan _expected_fallback _expected_hops _expected_total; do
    [[ -n "$source_run" ]] || continue
    replay_idx=$((replay_idx + 1))
    run_name="replay_${replay_idx}_${source_run}"
    run_bench="$REPLAY_ROOT/${run_name}.txt"
    run_out_dir="$REPLAY_ROOT/${run_name}_generated"
    run_build_dir="$REPLAY_ROOT/${run_name}_iverilog"
    run_config="$REPLAY_ROOT/${run_name}_compiled.json"

    OUT_CONFIG="$run_config" run_tune_candidate \
      "replay_${source_stage}" \
      "$run_name" \
      "$run_bench" \
      "$run_out_dir" \
      "$run_build_dir" \
      "$lane" \
      "$rep" \
      "$mp" \
      "$priority" \
      "$load_balance" \
      "$scheduler_policy" \
      "$max_in_flight" \
      "$placement_policy" \
      "$lowering_profile" \
      "$REPLAY_ROWS"
  done < "$REPLAY_PLAN"

  export CW_REPLAY_PLAN="$REPLAY_PLAN"
  export CW_REPLAY_ROWS="$REPLAY_ROWS"
  export CW_REPLAY_MODE="$REPLAY_MODE"
  export CW_REPLAY_SOURCE_SUMMARY="$REPLAY_SUMMARY_FILE"
  export CW_REPLAY_OUTPUT="$REPLAY_OUTPUT_FILE"
  export CW_REPLAY_REQUIRE_ALL_PASS="$REPLAY_REQUIRE_ALL_PASS"
  python3 - <<'PY'
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path

plan_path = Path(os.environ["CW_REPLAY_PLAN"])
rows_path = Path(os.environ["CW_REPLAY_ROWS"])
output_path = Path(os.environ["CW_REPLAY_OUTPUT"])
mode = os.environ["CW_REPLAY_MODE"]
source_summary = os.environ["CW_REPLAY_SOURCE_SUMMARY"]
require_all_pass = os.environ["CW_REPLAY_REQUIRE_ALL_PASS"] == "1"

source_summary_path = Path(source_summary)
source_hops_metric = "legacy_unversioned"
for line in source_summary_path.read_text().splitlines():
    if line.startswith("estimated_transfer_hops_metric: "):
        source_hops_metric = line.split(": ", 1)[1].strip()
        break

plan_rows: list[list[str]] = []
for line in plan_path.read_text().splitlines():
    if line.strip():
        plan_rows.append(line.split("|"))

with rows_path.open(newline="") as f:
    actual_rows = list(csv.reader(f))

if not plan_rows:
    raise SystemExit("Replay plan is empty")
if len(plan_rows) != len(actual_rows):
    raise SystemExit(
        f"Replay plan/result count mismatch: planned={len(plan_rows)} actual={len(actual_rows)}"
    )


def as_float(value: str) -> float:
    if value == "inf":
        return math.inf
    return float(value)


def as_int(value: str) -> int:
    if value == "inf":
        return 2**31 - 1
    return int(float(value))


results: list[dict[str, str]] = []
for planned, actual in zip(plan_rows, actual_rows):
    if len(planned) != 17 or len(actual) != 19:
        raise SystemExit("Malformed replay plan or result row")

    (
        source_stage,
        source_run,
        lane,
        replicas,
        max_parallel,
        priority,
        load_balance,
        scheduler_policy,
        max_in_flight,
        placement,
        profile,
        expected_lat,
        expected_p95,
        expected_makespan,
        expected_fallback,
        expected_hops,
        expected_total_ms,
    ) = planned
    (
        _actual_stage,
        replay_run,
        status,
        _lane,
        _replicas,
        _max_parallel,
        _priority,
        _load_balance,
        _scheduler_policy,
        _max_in_flight,
        _placement,
        _profile,
        actual_lat,
        actual_p95,
        actual_makespan,
        actual_fallback,
        actual_hops,
        actual_total_ms,
        bench_path,
    ) = actual
    bench_lines = Path(bench_path).read_text().splitlines()
    results.append(
        {
            "source_stage": source_stage,
            "source_run": source_run,
            "replay_run": replay_run,
            "status": status,
            "lane": lane,
            "replicas": replicas,
            "max_parallel": max_parallel,
            "priority": priority or "auto",
            "load_balance": load_balance or "auto",
            "scheduler_policy": scheduler_policy or "auto",
            "max_in_flight": max_in_flight,
            "placement": placement or "auto",
            "profile": profile or "auto",
            "expected_lat": expected_lat,
            "expected_p95": expected_p95,
            "expected_makespan": expected_makespan,
            "expected_fallback": expected_fallback,
            "expected_hops": expected_hops,
            "expected_total_ms": expected_total_ms,
            "actual_lat": actual_lat,
            "actual_p95": actual_p95,
            "actual_makespan": actual_makespan,
            "actual_fallback": actual_fallback,
            "actual_hops": actual_hops,
            "actual_total_ms": actual_total_ms,
            "bench_path": bench_path,
            "actual_hops_metric": next(
                (
                    line.split(": ", 1)[1].strip()
                    for line in bench_lines
                    if line.startswith("estimated_transfer_hops_metric: ")
                ),
                "unknown",
            ),
            "scoreboard_pass_ratio": next(
                (
                    line.split(": ", 1)[1].strip()
                    for line in bench_lines
                    if line.startswith("scoreboard_pass_ratio: ")
                ),
                "0.0000",
            ),
        }
    )

passing = [row for row in results if row["status"] == "pass"]
if not passing:
    raise SystemExit("No replay candidates passed")
if require_all_pass and len(passing) != len(results):
    raise SystemExit(
        f"Replay required all candidates to pass: passed={len(passing)} total={len(results)}"
    )

best = min(
    passing,
    key=lambda row: (
        as_float(row["actual_lat"]),
        as_float(row["actual_p95"]),
        as_int(row["actual_makespan"]),
        as_float(row["actual_fallback"]),
        as_int(row["actual_hops"]),
        as_int(row["actual_total_ms"]),
        row["source_run"],
    ),
)

lines = [
    "WAU CW Autotune Replay Summary (latest)",
    f"run_utc: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
    f"replay_mode: {mode}",
    f"source_summary: {source_summary}",
    f"source_hops_metric: {source_hops_metric}",
    f"actual_hops_metric: {best['actual_hops_metric']}",
    f"replay_candidates: {len(results)}",
    f"replay_passed: {len(passing)}",
    f"replay_failed: {len(results) - len(passing)}",
    f"best_replay_source_run: {best['source_run']}",
    f"best_replay_benchmark: {best['bench_path']}",
    "",
    "Replay Results",
]
for row in results:
    if row["status"] == "pass":
        latency_delta = as_float(row["actual_lat"]) - as_float(row["expected_lat"])
        makespan_delta = as_int(row["actual_makespan"]) - as_int(row["expected_makespan"])
        lines.append(
            f"source_run={row['source_run']} source_stage={row['source_stage']} status=pass "
            f"lane={row['lane']} replicas={row['replicas']} max_parallel={row['max_parallel']} "
            f"priority={row['priority']} load_balance={row['load_balance']} "
            f"scheduler_policy={row['scheduler_policy']} max_in_flight={row['max_in_flight']} "
            f"placement={row['placement']} profile={row['profile']} "
            f"expected_latency_avg={row['expected_lat']} actual_latency_avg={row['actual_lat']} "
            f"latency_delta={latency_delta:+.2f} expected_makespan={row['expected_makespan']} "
            f"actual_makespan={row['actual_makespan']} makespan_delta={makespan_delta:+d} "
            f"expected_fallback_ratio={row['expected_fallback']} actual_fallback_ratio={row['actual_fallback']} "
            f"expected_hops_metric={source_hops_metric} expected_hops={row['expected_hops']} "
            f"actual_hops_metric={row['actual_hops_metric']} actual_hops={row['actual_hops']} "
            f"scoreboard_pass_ratio={row['scoreboard_pass_ratio']} "
            f"total_ms={row['actual_total_ms']} bench={row['bench_path']}"
        )
    else:
        lines.append(
            f"source_run={row['source_run']} source_stage={row['source_stage']} status=fail "
            f"bench={row['bench_path']}"
        )

output_path.write_text("\n".join(lines) + "\n")
print(
    "[cw-bench][replay] complete "
    f"mode={mode} passed={len(passing)}/{len(results)} best_source={best['source_run']}"
)
print(f"[cw-bench][replay] wrote summary: {output_path}")
PY

  echo "[cw-bench] done (replay mode)"
  exit 0
fi

if [[ "$TUNE_MODE" != "1" && "$MULTI_RUNS" -gt 1 ]]; then
  echo "[cw-bench] multi-run mode enabled (runs=${MULTI_RUNS})"
  MULTI_ROOT=".build/cw_multi"
  MULTI_ROWS="$MULTI_ROOT/rows.csv"
  mkdir -p "$MULTI_ROOT" "$(dirname "$MULTI_SUMMARY_FILE")"
  : > "$MULTI_ROWS"

  for ((run_idx = 1; run_idx <= MULTI_RUNS; run_idx++)); do
    run_name="run_${run_idx}"
    run_bench="$MULTI_ROOT/${run_name}.txt"
    run_out_dir="$MULTI_ROOT/${run_name}_generated"
    run_build_dir="$MULTI_ROOT/${run_name}_iverilog"

    echo "[cw-bench][multi] run=${run_name}"
    if TUNE_MODE=0 \
      REPLAY_MODE=off \
      MULTI_RUNS=1 \
      REGRESSION_CHECK=0 \
      UPDATE_BENCH_SIDECAR=0 \
      RUN_PROFILE="multi_run_sample" \
      OUT_DIR="$run_out_dir" \
      BUILD_DIR="$run_build_dir" \
      "${BASH:-bash}" "$0" "$CW_FILE" "$BASE_CONFIG" "$OUT_CONFIG" "$run_bench"; then
      lat_avg="$(bench_field "$run_bench" "exec_latency_cycles_avg")"
      makespan="$(bench_field "$run_bench" "makespan_cycles")"
      total_ms="$(bench_field "$run_bench" "total_ms")"
      fallback_ratio="$(bench_field "$run_bench" "fallback_instruction_ratio")"
      hops_total="$(bench_field "$run_bench" "estimated_transfer_hops_total")"
      echo "${run_name},pass,${lat_avg},${makespan},${total_ms},${fallback_ratio},${hops_total},${run_bench}" >> "$MULTI_ROWS"
    else
      echo "${run_name},fail,inf,inf,inf,inf,inf,${run_bench}" >> "$MULTI_ROWS"
    fi
  done

  export CW_MULTI_ROWS="$MULTI_ROWS"
  export CW_MULTI_TARGET_BENCH="$BENCH_FILE"
  export CW_MULTI_SUMMARY_FILE="$MULTI_SUMMARY_FILE"
  export CW_MULTI_RUNS="$MULTI_RUNS"
  export CW_MULTI_REQUIRE_ALL_PASS="$MULTI_REQUIRE_ALL_PASS"
  python3 - <<'PY'
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path

rows_path = Path(os.environ["CW_MULTI_ROWS"])
target_bench = Path(os.environ["CW_MULTI_TARGET_BENCH"])
summary_path = Path(os.environ["CW_MULTI_SUMMARY_FILE"])
runs_requested = int(os.environ["CW_MULTI_RUNS"])
require_all_pass = os.environ["CW_MULTI_REQUIRE_ALL_PASS"] == "1"

rows: list[dict[str, str]] = []
with rows_path.open(newline="") as f:
    reader = csv.reader(f)
    for run_name, status, lat_avg, makespan, total_ms, fallback_ratio, hops_total, bench_path in reader:
        rows.append(
            {
                "run_name": run_name,
                "status": status,
                "lat_avg": lat_avg,
                "makespan": makespan,
                "total_ms": total_ms,
                "fallback_ratio": fallback_ratio,
                "hops_total": hops_total,
                "bench_path": bench_path,
            }
        )


def as_float(value: str) -> float:
    if value == "inf":
        return float("inf")
    return float(value)


def as_int(value: str) -> int:
    if value == "inf":
        return 2**31 - 1
    return int(float(value))


def percentile_ceil(values: list[float], p: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((p / 100.0) * len(ordered)) - 1))
    return ordered[idx]


passing = [row for row in rows if row["status"] == "pass"]
if not passing:
    raise SystemExit("No successful multi-run samples")
if require_all_pass and len(passing) != len(rows):
    failed = len(rows) - len(passing)
    raise SystemExit(f"Multi-run required all pass but found {failed} failed run(s)")

passing_sorted = sorted(
    passing,
    key=lambda row: (as_float(row["lat_avg"]), as_int(row["makespan"]), as_int(row["total_ms"])),
)
best = passing_sorted[0]
best_path = Path(best["bench_path"])
if not best_path.exists():
    raise SystemExit(f"Best benchmark file missing: {best_path}")

lat_values = [as_float(row["lat_avg"]) for row in passing]
lat_median = percentile_ceil(lat_values, 50.0)
lat_p95 = percentile_ceil(lat_values, 95.0)

best_text = best_path.read_text()
hops_metric = next(
    (
        line.split(": ", 1)[1]
        for line in best_text.splitlines()
        if line.startswith("estimated_transfer_hops_metric: ")
    ),
    "unknown",
)
stability_section = [
    "",
    "Multi-Run Stability",
    f"multi_runs_requested: {runs_requested}",
    f"multi_runs_passed: {len(passing)}",
    f"multi_runs_failed: {len(rows) - len(passing)}",
    f"exec_latency_cycles_median: {lat_median:.2f}",
    f"exec_latency_cycles_p95: {lat_p95:.2f}",
    f"best_sample_run: {best['run_name']}",
    f"best_sample_exec_latency_cycles_avg: {best['lat_avg']}",
    f"best_sample_makespan_cycles: {best['makespan']}",
    f"best_sample_total_ms: {best['total_ms']}",
    f"estimated_transfer_hops_metric: {hops_metric}",
]
target_bench.write_text(best_text.rstrip() + "\n" + "\n".join(stability_section) + "\n")

summary_lines = [
    "WAU CW Multi-Run Summary (latest)",
    f"run_utc: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
    f"multi_runs_requested: {runs_requested}",
    f"multi_runs_passed: {len(passing)}",
    f"multi_runs_failed: {len(rows) - len(passing)}",
    f"exec_latency_cycles_median: {lat_median:.2f}",
    f"exec_latency_cycles_p95: {lat_p95:.2f}",
    f"estimated_transfer_hops_metric: {hops_metric}",
    "",
    "Top Samples (lowest exec latency avg, then makespan, then total_ms)",
]
for idx, row in enumerate(passing_sorted[:5], start=1):
    summary_lines.append(
        f"{idx}. run={row['run_name']} exec_latency_avg={row['lat_avg']} makespan={row['makespan']} "
        f"total_ms={row['total_ms']} fallback_ratio={row['fallback_ratio']} hops_total={row['hops_total']}"
    )

summary_lines.append("")
summary_lines.append("All Samples")
for row in rows:
    summary_lines.append(
        f"run={row['run_name']} status={row['status']} exec_latency_avg={row['lat_avg']} "
        f"makespan={row['makespan']} total_ms={row['total_ms']} fallback_ratio={row['fallback_ratio']} "
        f"hops_total={row['hops_total']} bench={row['bench_path']}"
    )

summary_path.write_text("\n".join(summary_lines) + "\n")
print(
    "[cw-bench][multi] best "
    f"run={best['run_name']} exec_latency_avg={best['lat_avg']} makespan={best['makespan']} total_ms={best['total_ms']}"
)
print(f"[cw-bench][multi] wrote latest benchmark: {target_bench}")
print(f"[cw-bench][multi] wrote multi-run summary: {summary_path}")
PY

  echo "[cw-bench] done (multi-run mode)"
  exit 0
fi

if [[ "$TUNE_MODE" == "1" ]]; then
  if [[ "$TUNE_SEARCH_MODE" != "coordinate" ]]; then
    echo "[cw-bench] ERROR: unsupported TUNE_SEARCH_MODE=${TUNE_SEARCH_MODE} (supported: coordinate)"
    exit 2
  fi

  echo "[cw-bench] tune mode enabled (search_mode=${TUNE_SEARCH_MODE})"
  TUNE_ROOT=".build/cw_tune"
  TUNE_ROWS="$TUNE_ROOT/rows.csv"
  mkdir -p "$TUNE_ROOT" "$(dirname "$TUNE_SUMMARY_FILE")"
  : > "$TUNE_ROWS"

  IFS=',' read -r -a tune_lanes_raw <<< "$TUNE_LANE_PARALLELISM_SET"
  IFS=',' read -r -a tune_replicas_raw <<< "$TUNE_PROGRAM_REPLICAS_SET"
  IFS=',' read -r -a tune_max_parallel_raw <<< "$TUNE_PROGRAM_MAX_PARALLEL_SET"
  IFS=',' read -r -a tune_priorities_raw <<< "$TUNE_PROGRAM_PRIORITY_SET"
  IFS=',' read -r -a tune_load_balance_raw <<< "$TUNE_PROGRAM_LOAD_BALANCE_SET"
  IFS=',' read -r -a tune_scheduler_policy_raw <<< "$TUNE_SCHEDULER_PROGRAM_POLICY_SET"
  IFS=',' read -r -a tune_max_in_flight_raw <<< "$TUNE_CW_MAX_IN_FLIGHT_SET"
  IFS=',' read -r -a tune_placement_policy_raw <<< "$TUNE_PLACEMENT_POLICY_SET"
  IFS=',' read -r -a tune_lowering_profile_raw <<< "$TUNE_LOWERING_PROFILE_SET"

  best_lane="$CW_LANE_PARALLELISM"
  best_rep="$PROGRAM_REPLICAS"
  best_mp="$PROGRAM_MAX_PARALLEL"
  best_priority="$PROGRAM_PRIORITY"
  best_load_balance="$PROGRAM_LOAD_BALANCE"
  best_scheduler_policy="$SCHEDULER_PROGRAM_POLICY"
  best_max_in_flight="$CW_MAX_IN_FLIGHT"
  best_placement_policy="$CW_PLACEMENT_POLICY"
  best_lowering_profile="$CW_LOWERING_PROFILE"

  run_idx=0
  for lane_raw in "${tune_lanes_raw[@]}"; do
    lane="$(trim_csv_token "$lane_raw")"
    [[ -n "$lane" ]] || continue
    for placement_raw in "${tune_placement_policy_raw[@]}"; do
      placement="$(trim_csv_token "$placement_raw")"
      [[ -n "$placement" ]] || continue
      for profile_raw in "${tune_lowering_profile_raw[@]}"; do
        profile="$(trim_csv_token "$profile_raw")"
        [[ -n "$profile" ]] || continue
        run_idx=$((run_idx + 1))
        run_name="r${run_idx}_topology"
        run_bench="$TUNE_ROOT/${run_name}.txt"
        run_out_dir="$TUNE_ROOT/${run_name}_generated"
        run_build_dir="$TUNE_ROOT/${run_name}_iverilog"
        run_tune_candidate \
          "stage1_topology" \
          "$run_name" \
          "$run_bench" \
          "$run_out_dir" \
          "$run_build_dir" \
          "$lane" \
          "$best_rep" \
          "$best_mp" \
          "$best_priority" \
          "$best_load_balance" \
          "$best_scheduler_policy" \
          "$best_max_in_flight" \
          "$placement" \
          "$profile" \
          "$TUNE_ROWS"
      done
    done
  done

  IFS='|' read -r best_lane best_rep best_mp best_priority best_load_balance best_scheduler_policy best_max_in_flight best_placement_policy best_lowering_profile _best_lat _best_p95 _best_makespan _best_fallback _best_hops _best_total _best_bench _best_run <<< "$(pick_best_tune_row "$TUNE_ROWS" "stage1_topology")"

  for rep_raw in "${tune_replicas_raw[@]}"; do
    rep="$(trim_csv_token "$rep_raw")"
    [[ -n "$rep" ]] || continue
    for mp_raw in "${tune_max_parallel_raw[@]}"; do
      mp="$(trim_csv_token "$mp_raw")"
      [[ -n "$mp" ]] || continue
      for priority_raw in "${tune_priorities_raw[@]}"; do
        priority="$(trim_csv_token "$priority_raw")"
        [[ -n "$priority" ]] || continue
        for max_in_flight_raw in "${tune_max_in_flight_raw[@]}"; do
          max_in_flight="$(trim_csv_token "$max_in_flight_raw")"
          [[ -n "$max_in_flight" ]] || continue
          run_idx=$((run_idx + 1))
          run_name="r${run_idx}_program"
          run_bench="$TUNE_ROOT/${run_name}.txt"
          run_out_dir="$TUNE_ROOT/${run_name}_generated"
          run_build_dir="$TUNE_ROOT/${run_name}_iverilog"
          run_tune_candidate \
            "stage2_program" \
            "$run_name" \
            "$run_bench" \
            "$run_out_dir" \
            "$run_build_dir" \
            "$best_lane" \
            "$rep" \
            "$mp" \
            "$priority" \
            "$best_load_balance" \
            "$best_scheduler_policy" \
            "$max_in_flight" \
            "$best_placement_policy" \
            "$best_lowering_profile" \
            "$TUNE_ROWS"
        done
      done
    done
  done

  IFS='|' read -r best_lane best_rep best_mp best_priority best_load_balance best_scheduler_policy best_max_in_flight best_placement_policy best_lowering_profile _best_lat _best_p95 _best_makespan _best_fallback _best_hops _best_total _best_bench _best_run <<< "$(pick_best_tune_row "$TUNE_ROWS" "stage2_program")"

  for load_balance_raw in "${tune_load_balance_raw[@]}"; do
    load_balance="$(trim_csv_token "$load_balance_raw")"
    [[ -n "$load_balance" ]] || continue
    for scheduler_policy_raw in "${tune_scheduler_policy_raw[@]}"; do
      scheduler_policy="$(trim_csv_token "$scheduler_policy_raw")"
      [[ -n "$scheduler_policy" ]] || continue
      run_idx=$((run_idx + 1))
      run_name="r${run_idx}_scheduler"
      run_bench="$TUNE_ROOT/${run_name}.txt"
      run_out_dir="$TUNE_ROOT/${run_name}_generated"
      run_build_dir="$TUNE_ROOT/${run_name}_iverilog"
      run_tune_candidate \
        "stage3_scheduler" \
        "$run_name" \
        "$run_bench" \
        "$run_out_dir" \
        "$run_build_dir" \
        "$best_lane" \
        "$best_rep" \
        "$best_mp" \
        "$best_priority" \
        "$load_balance" \
        "$scheduler_policy" \
        "$best_max_in_flight" \
        "$best_placement_policy" \
        "$best_lowering_profile" \
        "$TUNE_ROWS"
    done
  done

  IFS='|' read -r best_lane best_rep best_mp best_priority best_load_balance best_scheduler_policy best_max_in_flight best_placement_policy best_lowering_profile best_lat best_p95 best_makespan best_fallback best_hops best_total best_bench best_run <<< "$(pick_best_tune_row "$TUNE_ROWS" "all")"

  export CW_TUNE_ROWS="$TUNE_ROWS"
  export CW_TUNE_TARGET_BENCH="$BENCH_FILE"
  export CW_TUNE_SUMMARY_FILE="$TUNE_SUMMARY_FILE"
  export CW_TUNE_SEARCH_MODE="$TUNE_SEARCH_MODE"
  export CW_TUNE_LANE_SET="$TUNE_LANE_PARALLELISM_SET"
  export CW_TUNE_REPLICA_SET="$TUNE_PROGRAM_REPLICAS_SET"
  export CW_TUNE_MAX_PARALLEL_SET="$TUNE_PROGRAM_MAX_PARALLEL_SET"
  export CW_TUNE_PRIORITY_SET="$TUNE_PROGRAM_PRIORITY_SET"
  export CW_TUNE_LOAD_BALANCE_SET="$TUNE_PROGRAM_LOAD_BALANCE_SET"
  export CW_TUNE_SCHEDULER_POLICY_SET="$TUNE_SCHEDULER_PROGRAM_POLICY_SET"
  export CW_TUNE_MAX_IN_FLIGHT_SET="$TUNE_CW_MAX_IN_FLIGHT_SET"
  export CW_TUNE_PLACEMENT_POLICY_SET="$TUNE_PLACEMENT_POLICY_SET"
  export CW_TUNE_LOWERING_PROFILE_SET="$TUNE_LOWERING_PROFILE_SET"
  python3 - <<'PY'
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path

rows_path = Path(os.environ["CW_TUNE_ROWS"])
target_bench = Path(os.environ["CW_TUNE_TARGET_BENCH"])
summary_path = Path(os.environ["CW_TUNE_SUMMARY_FILE"])
search_mode = os.environ["CW_TUNE_SEARCH_MODE"]
lane_set = os.environ["CW_TUNE_LANE_SET"]
rep_set = os.environ["CW_TUNE_REPLICA_SET"]
mp_set = os.environ["CW_TUNE_MAX_PARALLEL_SET"]
priority_set = os.environ["CW_TUNE_PRIORITY_SET"]
load_balance_set = os.environ["CW_TUNE_LOAD_BALANCE_SET"]
scheduler_policy_set = os.environ["CW_TUNE_SCHEDULER_POLICY_SET"]
max_in_flight_set = os.environ["CW_TUNE_MAX_IN_FLIGHT_SET"]
placement_policy_set = os.environ["CW_TUNE_PLACEMENT_POLICY_SET"]
lowering_profile_set = os.environ["CW_TUNE_LOWERING_PROFILE_SET"]

rows: list[dict[str, str]] = []
with rows_path.open(newline="") as f:
    reader = csv.reader(f)
    for (
        stage,
        run_name,
        status,
        lane,
        rep,
        mp,
        priority,
        load_balance,
        scheduler_policy,
        max_in_flight,
        placement_policy,
        lowering_profile,
        lat_avg,
        lat_p95,
        makespan,
        fallback_ratio,
        hops_total,
        total_ms,
        bench_path,
    ) in reader:
        rows.append(
            {
                "stage": stage,
                "run_name": run_name,
                "status": status,
                "lane": lane,
                "rep": rep,
                "mp": mp,
                "priority": priority,
                "load_balance": load_balance,
                "scheduler_policy": scheduler_policy,
                "max_in_flight": max_in_flight,
                "placement_policy": placement_policy,
                "lowering_profile": lowering_profile,
                "lat_avg": lat_avg,
                "lat_p95": lat_p95,
                "makespan": makespan,
                "fallback_ratio": fallback_ratio,
                "hops_total": hops_total,
                "total_ms": total_ms,
                "bench_path": bench_path,
            }
        )


def as_float(value: str) -> float:
    if value == "inf":
        return math.inf
    return float(value)


def as_int(value: str) -> int:
    if value == "inf":
        return 2**31 - 1
    return int(float(value))


def show(value: str) -> str:
    return value if value else "auto"


passing = [row for row in rows if row["status"] == "pass"]
if not passing:
    raise SystemExit("No successful tuning runs found")

passing_sorted = sorted(
    passing,
    key=lambda row: (
        as_float(row["lat_avg"]),
        as_float(row["lat_p95"]),
        as_int(row["makespan"]),
        as_float(row["fallback_ratio"]),
        as_int(row["hops_total"]),
        as_int(row["total_ms"]),
    ),
)
best = passing_sorted[0]
best_path = Path(best["bench_path"])
if not best_path.exists():
    raise SystemExit(f"Best benchmark file missing: {best_path}")

best_text = best_path.read_text()
hops_metric = next(
    (
        line.split(": ", 1)[1]
        for line in best_text.splitlines()
        if line.startswith("estimated_transfer_hops_metric: ")
    ),
    "unknown",
)
tuning_section = [
    "",
    "Tuning Selection",
    "tune_mode: 1",
    f"tune_search_mode: {search_mode}",
    f"search_lanes: {lane_set}",
    f"search_program_replicas: {rep_set}",
    f"search_program_max_parallel: {mp_set}",
    f"search_program_priority: {priority_set}",
    f"search_program_load_balance: {load_balance_set}",
    f"search_scheduler_program_policy: {scheduler_policy_set}",
    f"search_cw_max_in_flight: {max_in_flight_set}",
    f"search_placement_policy: {placement_policy_set}",
    f"search_lowering_profile: {lowering_profile_set}",
    f"best_run: {best['run_name']}",
    f"best_stage: {best['stage']}",
    f"best_lane_parallelism: {show(best['lane'])}",
    f"best_program_replicas: {best['rep']}",
    f"best_program_max_parallel: {best['mp']}",
    f"best_program_priority: {show(best['priority'])}",
    f"best_program_load_balance: {show(best['load_balance'])}",
    f"best_scheduler_program_policy: {show(best['scheduler_policy'])}",
    f"best_cw_max_in_flight: {best['max_in_flight']}",
    f"best_placement_policy: {show(best['placement_policy'])}",
    f"best_lowering_profile: {show(best['lowering_profile'])}",
    f"best_exec_latency_cycles_avg: {best['lat_avg']}",
    f"best_exec_latency_cycles_p95: {best['lat_p95']}",
    f"best_makespan_cycles: {best['makespan']}",
    f"best_fallback_instruction_ratio: {best['fallback_ratio']}",
    f"estimated_transfer_hops_metric: {hops_metric}",
    f"best_estimated_transfer_hops_total: {best['hops_total']}",
    f"best_total_ms: {best['total_ms']}",
]
target_bench.write_text(best_text.rstrip() + "\n" + "\n".join(tuning_section) + "\n")

summary_lines = [
    "WAU CW Autotune Summary (latest)",
    f"run_utc: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
    f"search_mode: {search_mode}",
    f"search_lanes: {lane_set}",
    f"search_program_replicas: {rep_set}",
    f"search_program_max_parallel: {mp_set}",
    f"search_program_priority: {priority_set}",
    f"search_program_load_balance: {load_balance_set}",
    f"search_scheduler_program_policy: {scheduler_policy_set}",
    f"search_cw_max_in_flight: {max_in_flight_set}",
    f"search_placement_policy: {placement_policy_set}",
    f"search_lowering_profile: {lowering_profile_set}",
    f"estimated_transfer_hops_metric: {hops_metric}",
    "",
    "Top Candidates (avg latency, p95 latency, makespan, fallback ratio, hops, total_ms)",
]
for idx, row in enumerate(passing_sorted[:5], start=1):
    summary_lines.append(
        f"{idx}. run={row['run_name']} stage={row['stage']} lane={show(row['lane'])} replicas={row['rep']} "
        f"max_parallel={row['mp']} priority={show(row['priority'])} load_balance={show(row['load_balance'])} "
        f"scheduler_policy={show(row['scheduler_policy'])} max_in_flight={row['max_in_flight']} "
        f"placement={show(row['placement_policy'])} profile={show(row['lowering_profile'])} "
        f"exec_latency_avg={row['lat_avg']} exec_latency_p95={row['lat_p95']} "
        f"makespan={row['makespan']} fallback_ratio={row['fallback_ratio']} "
        f"hops_total={row['hops_total']} total_ms={row['total_ms']}"
    )

summary_lines.append("")
summary_lines.append("Stage Winners")
for stage in ("stage1_topology", "stage2_program", "stage3_scheduler"):
    stage_rows = [row for row in passing_sorted if row["stage"] == stage]
    if not stage_rows:
        continue
    row = stage_rows[0]
    summary_lines.append(
        f"{stage}: run={row['run_name']} lane={show(row['lane'])} replicas={row['rep']} max_parallel={row['mp']} "
        f"priority={show(row['priority'])} load_balance={show(row['load_balance'])} scheduler_policy={show(row['scheduler_policy'])} "
        f"max_in_flight={row['max_in_flight']} placement={show(row['placement_policy'])} profile={show(row['lowering_profile'])} "
        f"exec_latency_avg={row['lat_avg']} exec_latency_p95={row['lat_p95']} makespan={row['makespan']} "
        f"fallback_ratio={row['fallback_ratio']} hops_total={row['hops_total']} total_ms={row['total_ms']}"
    )

summary_lines.append("")
summary_lines.append("All Runs")
for row in rows:
    summary_lines.append(
        f"run={row['run_name']} stage={row['stage']} status={row['status']} lane={show(row['lane'])} "
        f"replicas={row['rep']} max_parallel={row['mp']} priority={show(row['priority'])} "
        f"load_balance={show(row['load_balance'])} scheduler_policy={show(row['scheduler_policy'])} "
        f"max_in_flight={row['max_in_flight']} placement={show(row['placement_policy'])} "
        f"profile={show(row['lowering_profile'])} exec_latency_avg={row['lat_avg']} "
        f"exec_latency_p95={row['lat_p95']} makespan={row['makespan']} "
        f"fallback_ratio={row['fallback_ratio']} hops_total={row['hops_total']} "
        f"total_ms={row['total_ms']} bench={row['bench_path']}"
    )

summary_path.write_text("\n".join(summary_lines) + "\n")
print(
    "[cw-bench][tune] best "
    f"lane={show(best['lane'])} replicas={best['rep']} max_parallel={best['mp']} "
    f"priority={show(best['priority'])} load_balance={show(best['load_balance'])} "
    f"scheduler_policy={show(best['scheduler_policy'])} max_in_flight={best['max_in_flight']} "
    f"placement={show(best['placement_policy'])} profile={show(best['lowering_profile'])} "
    f"exec_latency_avg={best['lat_avg']} exec_latency_p95={best['lat_p95']} "
    f"makespan={best['makespan']} fallback_ratio={best['fallback_ratio']} "
    f"hops_total={best['hops_total']} total_ms={best['total_ms']}"
)
print(f"[cw-bench][tune] wrote latest benchmark: {target_bench}")
print(f"[cw-bench][tune] wrote tuning summary: {summary_path}")
PY

  if [[ "$UPDATE_BENCH_SIDECAR" == "1" ]]; then
    export CW_TUNE_SIDE_BENCH="$BENCH_FILE"
    export CW_TUNE_SIDE_LATEST="$SIDECAR_LATEST_JSON"
    export CW_TUNE_SIDE_BEST="$SIDECAR_BEST_JSON"
    export CW_TUNE_SIDE_HISTORY="$SIDECAR_HISTORY_JSON"
    export CW_TUNE_SIDE_HISTORY_KEEP="$SIDECAR_HISTORY_KEEP"
    python3 - <<'PY'
from __future__ import annotations

import json
import math
import os
from pathlib import Path

bench_path = Path(os.environ["CW_TUNE_SIDE_BENCH"])
latest_path = Path(os.environ["CW_TUNE_SIDE_LATEST"])
best_path = Path(os.environ["CW_TUNE_SIDE_BEST"])
history_path = Path(os.environ["CW_TUNE_SIDE_HISTORY"])
history_keep = max(1, int(os.environ["CW_TUNE_SIDE_HISTORY_KEEP"]))


def field(name: str) -> str:
    prefix = f"{name}: "
    for line in bench_path.read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise KeyError(f"Missing key '{name}' in {bench_path}")


lat = float(field("exec_latency_cycles_avg"))
makespan = int(float(field("makespan_cycles")))
total_ms = int(float(field("best_total_ms" if "Tuning Selection" in bench_path.read_text() else "total_ms")))
score = (lat * 1_000_000.0) + (makespan * 1_000.0) + total_ms

payload = {
    "format_version": 1,
    "run_utc": field("run_utc"),
    "run_profile": "autotune_selected",
    "metrics": {
        "exec_latency_cycles_avg": round(lat, 2),
        "makespan_cycles": makespan,
        "total_ms": total_ms,
        "benchmark_ranking_score": round(score, 2),
    },
    "paths": {
        "benchmark_text": str(bench_path),
        "tuning_summary": str(Path("benchmarks/example_pogram_tuning_latest.txt")),
    },
}

latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def score_tuple(raw: dict) -> tuple[float, int, int]:
    metrics = raw.get("metrics", {})
    return (
        float(metrics.get("exec_latency_cycles_avg", math.inf)),
        int(metrics.get("makespan_cycles", 2**31 - 1)),
        int(metrics.get("total_ms", 2**31 - 1)),
    )


best_raw: dict | None = None
if best_path.exists():
    try:
        loaded = json.loads(best_path.read_text())
        if isinstance(loaded, dict):
            best_raw = loaded
    except Exception:  # noqa: BLE001
        best_raw = None

if best_raw is None or score_tuple(payload) < score_tuple(best_raw):
    best_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

history_payload: dict = {"format_version": 1, "runs": []}
if history_path.exists():
    try:
        loaded = json.loads(history_path.read_text())
        if isinstance(loaded, dict):
            history_payload = loaded
    except Exception:  # noqa: BLE001
        history_payload = {"format_version": 1, "runs": []}

runs = history_payload.get("runs", [])
if not isinstance(runs, list):
    runs = []
runs.append(
    {
        "run_utc": payload["run_utc"],
        "run_profile": payload["run_profile"],
        "benchmark_text": str(bench_path),
        "exec_latency_cycles_avg": payload["metrics"]["exec_latency_cycles_avg"],
        "makespan_cycles": payload["metrics"]["makespan_cycles"],
        "total_ms": payload["metrics"]["total_ms"],
        "benchmark_ranking_score": payload["metrics"]["benchmark_ranking_score"],
    }
)
if len(runs) > history_keep:
    runs = runs[-history_keep:]
history_payload["runs"] = runs
history_payload["best_sidecar"] = str(best_path)
history_path.write_text(json.dumps(history_payload, indent=2, sort_keys=True) + "\n")
print(f"[cw-bench][tune] synced sidecars from selected best benchmark: {bench_path}")
PY
  fi

  if [[ -n "$best_lane" && -n "$best_rep" && -n "$best_mp" ]]; then
    echo "[cw-bench][tune] refreshing OUT_CONFIG with best candidate"
    TUNE_MODE=0 \
      REPLAY_MODE=off \
      MULTI_RUNS=1 \
      REGRESSION_CHECK=0 \
      UPDATE_BENCH_SIDECAR=0 \
      RUN_PROFILE="autotune_best_refresh" \
      CW_LANE_PARALLELISM="$best_lane" \
      CW_MAX_IN_FLIGHT="$best_max_in_flight" \
      CW_PLACEMENT_POLICY="$best_placement_policy" \
      CW_LOWERING_PROFILE="$best_lowering_profile" \
      PROGRAM_REPLICAS="$best_rep" \
      PROGRAM_MAX_PARALLEL="$best_mp" \
      PROGRAM_PRIORITY="$best_priority" \
      PROGRAM_LOAD_BALANCE="$best_load_balance" \
      SCHEDULER_PROGRAM_POLICY="$best_scheduler_policy" \
      OUT_DIR="$OUT_DIR" \
      BUILD_DIR="$BUILD_DIR" \
      "${BASH:-bash}" "$0" "$CW_FILE" "$BASE_CONFIG" "$OUT_CONFIG" "$TUNE_ROOT/best_refresh.txt" >/dev/null
  fi

  echo "[cw-bench] done (tune mode)"
  exit 0
fi

echo "[cw-bench] compile-cw"
t0="$(now_ns)"
EFFECTIVE_BASE_CONFIG="$(prepare_effective_base_config "$BASE_CONFIG" "$BUILD_DIR/benchmark_base_config.json")"
compile_cmd=(
  python3 -m waugen compile-cw
  --program-file "$CW_FILE"
  --flow-id "$FLOW_ID"
  --name "cw_conv2d_residual_reference"
  --entry "0,0"
  --max-in-flight "$CW_MAX_IN_FLIGHT"
  --base-config "$EFFECTIVE_BASE_CONFIG"
  --out-config "$OUT_CONFIG"
  --replace-existing
  --program-id "$PROGRAM_ID"
  --program-name "cw_reference_program"
  --program-replicas "$PROGRAM_REPLICAS"
  --program-max-parallel-flows "$PROGRAM_MAX_PARALLEL"
)

if [[ -n "$CW_DTYPE" ]]; then
  compile_cmd+=(--dtype "$CW_DTYPE")
fi

if [[ -n "$CW_LANE_PARALLELISM" ]]; then
  compile_cmd+=(--lane-parallelism "$CW_LANE_PARALLELISM")
fi

if [[ -n "$CW_PLACEMENT_POLICY" ]]; then
  compile_cmd+=(--placement-policy "$CW_PLACEMENT_POLICY")
fi

if [[ -n "$CW_LOWERING_PROFILE" ]]; then
  compile_cmd+=(--lowering-profile "$CW_LOWERING_PROFILE")
fi

if [[ -n "$PROGRAM_PRIORITY" ]]; then
  compile_cmd+=(--program-priority "$PROGRAM_PRIORITY")
fi

if [[ -n "$PROGRAM_LOAD_BALANCE" ]]; then
  compile_cmd+=(--program-load-balance "$PROGRAM_LOAD_BALANCE")
fi

"${compile_cmd[@]}"
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
import sys
from pathlib import Path

sys.path.insert(0, "src/python")
from waugen.cw_reference import compute_expected_values

out_config = Path(os.environ["CW_BENCH_OUT_CONFIG"])
exec_flow_id = int(os.environ["CW_BENCH_EXEC_FLOW_ID"])
timeout_cycles = int(os.environ["CW_BENCH_EXEC_TIMEOUT_CYCLES"])
tb_path = Path(os.environ["CW_BENCH_EXEC_TB"])
scoreboard_path = Path(os.environ.get("CW_BENCH_SCOREBOARD_JSON", ".build/cw_iverilog/cw_scoreboard.json"))

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
    (4, 0, 0),
    (5, 31, 31),
    (6, -15, -9),
    (7, 127, -11),
    (8, -64, 17),
]

expected_rows = compute_expected_values(out_config, exec_flow_id, cases)
expected_by_case = {row["case"]: int(row["expected"]) for row in expected_rows}

scoreboard_path.parent.mkdir(parents=True, exist_ok=True)
scoreboard_path.write_text(
    json.dumps(
        {
            "flow_id": exec_flow_id,
            "compiled_config": str(out_config),
            "cases": expected_rows,
        },
        indent=2,
    )
    + "\n"
)


def sv_lit(value: int) -> str:
    if value < 0:
        return f"-32'sd{abs(value)}"
    return f"32'sd{value}"


case_lines = "\n".join(
    [
        f"        send_and_expect({exec_flow_id}, {sv_lit(a)}, {sv_lit(b)}, "
        f"{case_id}, {sv_lit(expected_by_case[case_id])});"
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
        input signed [`WAU_DATA_WIDTH-1:0] expected;
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
                    if (host_out_value !== expected) begin
                        $display(
                            "FAIL: CW_EXEC_BENCH case=%0d flow=%0d expected_value=%0d got_value=%0d",
                            case_id,
                            flow_id,
                            expected,
                            host_out_value
                        );
                        $fatal(1);
                    end
                    matched = 1;
                    $display(
                        "CW_EXEC_BENCH case=%0d flow=%0d latency_cycles=%0d out_value=%0d expected_value=%0d scoreboard=match",
                        case_id,
                        flow_id,
                        (cycle_count - start_cycle),
                        host_out_value,
                        expected
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
print(f"[cw-bench] CW scoreboard expectations: {scoreboard_path}")
PY

echo "[cw-bench] run iverilog tests"
t6="$(now_ns)"

run_test tb_wau_operation_alu "$ALU_LOG" \
  "$OUT_DIR/wau_operation_alu.v" \
  "tests/rtl/tb_wau_operation_alu.v"

run_test tb_wau_highway_mesh "$MESH_LOG" \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_contract.v" \
  "$OUT_DIR/wau_highway_router.v" \
  "$OUT_DIR/wau_highway_mesh.v" \
  "tests/rtl/tb_wau_highway_mesh.v"

run_test tb_wau_cw_compiled_exec "$EXEC_LOG" \
  "$OUT_DIR/wau_operation_alu.v" \
  "$OUT_DIR/wau_neighbor_forward.v" \
  "$OUT_DIR/wau_highway_contract.v" \
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
    "$OUT_DIR/wau_highway_contract.v" \
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
export CW_BENCH_EFFECTIVE_BASE_CONFIG="$EFFECTIVE_BASE_CONFIG"
export CW_BENCH_OUT_CONFIG="$OUT_CONFIG"
export CW_BENCH_OUT_DIR="$OUT_DIR"
export CW_BENCH_FLOW_ID="$FLOW_ID"
export CW_BENCH_PROGRAM_ID="$PROGRAM_ID"
export CW_BENCH_PROGRAM_PRIORITY="$PROGRAM_PRIORITY"
export CW_BENCH_PROGRAM_REPLICAS="$PROGRAM_REPLICAS"
export CW_BENCH_PROGRAM_MAX_PARALLEL="$PROGRAM_MAX_PARALLEL"
export CW_BENCH_PROGRAM_LOAD_BALANCE="$PROGRAM_LOAD_BALANCE"
export CW_BENCH_SCHEDULER_PROGRAM_POLICY="$SCHEDULER_PROGRAM_POLICY"
export CW_BENCH_EXEC_FLOW_ID="$EXEC_FLOW_ID"
export CW_BENCH_CW_MAX_IN_FLIGHT="$CW_MAX_IN_FLIGHT"
export CW_BENCH_CW_LANE_PARALLELISM="$CW_LANE_PARALLELISM"
export CW_BENCH_CW_DTYPE="$CW_DTYPE"
export CW_BENCH_CW_PLACEMENT_POLICY="$CW_PLACEMENT_POLICY"
export CW_BENCH_CW_LOWERING_PROFILE="$CW_LOWERING_PROFILE"
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
export CW_BENCH_RUN_PROFILE="$RUN_PROFILE"
export CW_BENCH_TUNE_MODE="$TUNE_MODE"
export CW_BENCH_UPDATE_SIDECAR="$UPDATE_BENCH_SIDECAR"
export CW_BENCH_SIDECAR_LATEST_JSON="$SIDECAR_LATEST_JSON"
export CW_BENCH_SIDECAR_BEST_JSON="$SIDECAR_BEST_JSON"
export CW_BENCH_SIDECAR_HISTORY_JSON="$SIDECAR_HISTORY_JSON"
export CW_BENCH_SIDECAR_HISTORY_KEEP="$SIDECAR_HISTORY_KEEP"

python3 - <<'PY'
from __future__ import annotations

from collections import defaultdict
import datetime as dt
import json
import math
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
        r"(?: expected_value=(?P<expected>-?[0-9]+) scoreboard=(?P<scoreboard>\w+))?"
    )
    for line in exec_log_path.read_text().splitlines():
        match = pattern.search(line)
        if not match:
            continue
        row: dict[str, int] = {
            "case": int(match.group("case")),
            "flow": int(match.group("flow")),
            "latency_cycles": int(match.group("lat")),
            "out_value": int(match.group("out")),
        }
        if match.group("expected") is not None:
            row["expected_value"] = int(match.group("expected"))
            row["scoreboard"] = match.group("scoreboard") or "n/a"
        rows.append(row)
    return sorted(rows, key=lambda row: row["case"])


def score_tuple_from_metrics(metrics: dict[str, float | int]) -> tuple[float, int, int]:
    return (
        float(metrics.get("exec_latency_cycles_avg", math.inf)),
        int(metrics.get("makespan_cycles", 2**31 - 1)),
        int(metrics.get("total_ms", 2**31 - 1)),
    )


def percentile_ceil(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((p / 100.0) * len(ordered)) - 1))
    return float(ordered[idx])


bench_path = Path(os.environ["CW_BENCH_FILE"])
cw_file = os.environ["CW_BENCH_CW_FILE"]
base_config = os.environ["CW_BENCH_BASE_CONFIG"]
effective_base_config = os.environ["CW_BENCH_EFFECTIVE_BASE_CONFIG"]
out_config = Path(os.environ["CW_BENCH_OUT_CONFIG"])
out_dir = Path(os.environ["CW_BENCH_OUT_DIR"])
flow_id = int(os.environ["CW_BENCH_FLOW_ID"])
program_id = int(os.environ["CW_BENCH_PROGRAM_ID"])
program_priority_requested = os.environ["CW_BENCH_PROGRAM_PRIORITY"] or "auto"
program_replicas = int(os.environ["CW_BENCH_PROGRAM_REPLICAS"])
program_max_parallel = int(os.environ["CW_BENCH_PROGRAM_MAX_PARALLEL"])
program_load_balance_requested = os.environ["CW_BENCH_PROGRAM_LOAD_BALANCE"] or "auto"
scheduler_program_policy_requested = os.environ["CW_BENCH_SCHEDULER_PROGRAM_POLICY"] or "auto"
exec_flow_id = int(os.environ["CW_BENCH_EXEC_FLOW_ID"])
cw_max_in_flight = int(os.environ["CW_BENCH_CW_MAX_IN_FLIGHT"])
cw_lane_parallelism_requested = os.environ["CW_BENCH_CW_LANE_PARALLELISM"] or "auto"
cw_dtype_requested = os.environ["CW_BENCH_CW_DTYPE"] or "auto"
cw_placement_policy_requested = os.environ["CW_BENCH_CW_PLACEMENT_POLICY"] or "auto"
cw_lowering_profile_requested = os.environ["CW_BENCH_CW_LOWERING_PROFILE"] or "auto"

compile_ms = int(os.environ["CW_BENCH_COMPILE_MS"])
validate_ms = int(os.environ["CW_BENCH_VALIDATE_MS"])
generate_ms = int(os.environ["CW_BENCH_GENERATE_MS"])
iverilog_ms = int(os.environ["CW_BENCH_IVERILOG_MS"])
total_ms = int(os.environ["CW_BENCH_TOTAL_MS"])

git_commit = os.environ["CW_BENCH_GIT_COMMIT"]
git_tree_state = os.environ["CW_BENCH_GIT_TREE_STATE"]
python_version = os.environ["CW_BENCH_PYTHON_VERSION"]
iverilog_version = os.environ["CW_BENCH_IVERILOG_VERSION"]
run_profile = os.environ["CW_BENCH_RUN_PROFILE"]
tune_mode = os.environ["CW_BENCH_TUNE_MODE"] == "1"
update_sidecar = os.environ["CW_BENCH_UPDATE_SIDECAR"] == "1"
sidecar_latest_path = Path(os.environ["CW_BENCH_SIDECAR_LATEST_JSON"])
sidecar_best_path = Path(os.environ["CW_BENCH_SIDECAR_BEST_JSON"])
sidecar_history_path = Path(os.environ["CW_BENCH_SIDECAR_HISTORY_JSON"])
sidecar_history_keep = max(1, int(os.environ["CW_BENCH_SIDECAR_HISTORY_KEEP"]))

schedule_path = out_dir / "wau_schedule.json"
schedule = json.loads(schedule_path.read_text())
instructions = schedule.get("instructions", [])

fallback_count = sum(1 for ins in instructions if bool(ins.get("used_fallback", False)))
instruction_count = len(instructions)
fallback_ratio = (fallback_count / instruction_count) if instruction_count else 0.0
core_count = len({int(ins["core_index"]) for ins in instructions})
ops = sorted({str(ins["op"]) for ins in instructions})
programs = sorted({int(ins["program_id"]) for ins in instructions})
flows = sorted({int(ins["flow_id"]) for ins in instructions})

flow_totals: dict[int, int] = defaultdict(int)
flow_fallback: dict[int, int] = defaultdict(int)
for ins in instructions:
    flow = int(ins.get("flow_id", 0))
    flow_totals[flow] += 1
    if bool(ins.get("used_fallback", False)):
        flow_fallback[flow] += 1
per_flow_fallback_ratio = {
    flow: (flow_fallback.get(flow, 0) / count) if count else 0.0 for flow, count in sorted(flow_totals.items())
}
per_flow_fallback_ratio_repr = "{" + ", ".join(f"{flow}: {ratio:.3f}" for flow, ratio in per_flow_fallback_ratio.items()) + "}"

estimated_hops_metric = str(schedule["estimated_transfer_hops_metric"])
estimated_hops_total = int(schedule["estimated_transfer_hops_total"])
estimated_hop_edges = int(schedule["estimated_transfer_hops_edge_count"])
estimated_hops_avg = float(schedule["estimated_transfer_hops_avg_edge"])
estimated_hops_unresolved = int(
    schedule["estimated_transfer_hops_unresolved_edges"]
)

core_issue_counts: dict[int, int] = defaultdict(int)
core_busy_cycles: dict[int, int] = defaultdict(int)
node_issue_counts: dict[str, int] = defaultdict(int)
node_total_latency: dict[str, int] = defaultdict(int)
dependency_hotspots: dict[str, int] = defaultdict(int)
for ins in instructions:
    core_idx = int(ins.get("core_index", 0))
    node_id = str(ins.get("node_id", ""))
    latency = int(ins.get("latency", 0))
    dep_count = int(ins.get("dependency_count", 0))
    core_issue_counts[core_idx] += 1
    core_busy_cycles[core_idx] += latency
    node_issue_counts[node_id] += 1
    node_total_latency[node_id] += latency
    dependency_hotspots[node_id] = max(dependency_hotspots.get(node_id, 0), dep_count)

core_hotspots = sorted(
    core_issue_counts.items(),
    key=lambda item: (-item[1], -core_busy_cycles.get(item[0], 0), item[0]),
)[:5]
node_latency_hotspots = sorted(
    node_total_latency.items(),
    key=lambda item: (-item[1], -node_issue_counts.get(item[0], 0), item[0]),
)[:5]
dependency_hotspots_top = sorted(
    dependency_hotspots.items(),
    key=lambda item: (-item[1], item[0]),
)[:5]
core_hotspots_repr = [
    f"core={core_idx} issues={issues} busy_cycles={core_busy_cycles.get(core_idx, 0)}"
    for core_idx, issues in core_hotspots
]
node_latency_hotspots_repr = [
    f"node={node_id} total_latency={total_latency} issues={node_issue_counts.get(node_id, 0)}"
    for node_id, total_latency in node_latency_hotspots
]
dependency_hotspots_repr = [
    f"node={node_id} max_dependencies={dep_count}"
    for node_id, dep_count in dependency_hotspots_top
]
busiest_core_index = core_hotspots[0][0] if core_hotspots else -1
busiest_core_issue_count = core_hotspots[0][1] if core_hotspots else 0
busiest_core_busy_cycles = core_busy_cycles.get(busiest_core_index, 0) if core_hotspots else 0

payload = json.loads(out_config.read_text())
compiled_flow = next(
    (flow for flow in payload.get("flows", []) if isinstance(flow, dict) and int(flow.get("id", -1)) == flow_id),
    None,
)
compiled_program = next(
    (
        program
        for program in payload.get("programs", [])
        if isinstance(program, dict) and int(program.get("id", -1)) == program_id
    ),
    None,
)
payload_scheduler = payload.get("scheduler", {}) if isinstance(payload.get("scheduler"), dict) else {}
node_count = len(compiled_flow.get("nodes", [])) if isinstance(compiled_flow, dict) else 0
cw_hints = compiled_flow.get("cw_hints", {}) if isinstance(compiled_flow, dict) else {}
lane_parallelism = cw_hints.get("lane_parallelism_compiled", "n/a")
dtype = cw_hints.get("dtype_compiled", cw_hints.get("dtype", "n/a"))
placement_policy_compiled = cw_hints.get("placement_policy_compiled", "n/a")
lowering_profile_compiled = cw_hints.get("lowering_profile_compiled", "n/a")
program_priority_compiled = (
    compiled_program.get("priority", cw_hints.get("program_priority_compiled", "n/a"))
    if isinstance(compiled_program, dict)
    else cw_hints.get("program_priority_compiled", "n/a")
)
program_load_balance_compiled = (
    compiled_program.get("load_balance", cw_hints.get("program_load_balance_compiled", "n/a"))
    if isinstance(compiled_program, dict)
    else cw_hints.get("program_load_balance_compiled", "n/a")
)
scheduler_program_policy_compiled = payload_scheduler.get("program_policy", "n/a")
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
lat_p50 = percentile_ceil(latencies, 50.0)
lat_p95 = percentile_ceil(latencies, 95.0)
makespan = int(schedule.get("makespan_cycles", 0))
benchmark_ranking_score = (lat_avg * 1_000_000.0) + (makespan * 1_000.0) + total_ms

critical_tail = sorted(
    instructions,
    key=lambda ins: (
        int(ins.get("cycle_end", 0)),
        int(ins.get("cycle_start", 0)),
        int(ins.get("program_id", 0)),
        int(ins.get("program_replica", 0)),
        int(ins.get("flow_id", 0)),
    ),
    reverse=True,
)[:5]
critical_tail_repr = [
    (
        f"node={ins.get('node_id', 'n/a')} "
        f"program={int(ins.get('program_id', 0))}:{int(ins.get('program_replica', 0))} "
        f"flow={int(ins.get('flow_id', 0))} cycle_end={int(ins.get('cycle_end', 0))}"
    )
    for ins in critical_tail
]

run_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

lines = [
    "WAU CW Example Benchmark Reference (latest)",
    f"run_utc: {run_utc}",
    f"git_commit: {git_commit}",
    f"git_tree_state: {git_tree_state}",
    f"python_version: {python_version}",
    f"iverilog_version: {iverilog_version}",
    f"cw_source: {cw_file}",
    f"base_config: {base_config}",
    f"effective_base_config: {effective_base_config}",
    f"compiled_config: {out_config}",
    f"schedule_json: {schedule_path}",
    "",
    "Benchmark Mode",
    f"run_profile: {run_profile}",
    f"tune_mode: {1 if tune_mode else 0}",
    f"update_sidecar: {1 if update_sidecar else 0}",
    "",
    "Workload/Program",
    f"flow_id: {flow_id}",
    f"program_id: {program_id}",
    f"program_priority_requested: {program_priority_requested}",
    f"program_replicas: {program_replicas}",
    f"program_max_parallel_flows: {program_max_parallel}",
    f"program_load_balance_requested: {program_load_balance_requested}",
    f"scheduler_program_policy_requested: {scheduler_program_policy_requested}",
    f"cw_max_in_flight: {cw_max_in_flight}",
    f"cw_lane_parallelism_requested: {cw_lane_parallelism_requested}",
    f"cw_dtype_requested: {cw_dtype_requested}",
    f"cw_placement_policy_requested: {cw_placement_policy_requested}",
    f"cw_lowering_profile_requested: {cw_lowering_profile_requested}",
    f"compiled_nodes: {node_count}",
    f"lane_parallelism_compiled: {lane_parallelism}",
    f"placement_policy_compiled: {placement_policy_compiled}",
    f"lowering_profile_compiled: {lowering_profile_compiled}",
    f"program_priority_compiled: {program_priority_compiled}",
    f"program_load_balance_compiled: {program_load_balance_compiled}",
    f"scheduler_program_policy_compiled: {scheduler_program_policy_compiled}",
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
    "Effective Execution Benchmark (CW flow stress)",
    f"exec_flow_id: {exec_flow_id}",
    f"exec_case_count: {len(exec_rows)}",
    f"exec_latency_cycles_min: {lat_min}",
    f"exec_latency_cycles_max: {lat_max}",
    f"exec_latency_cycles_avg: {lat_avg:.2f}",
    f"exec_latency_cycles_p50: {lat_p50:.2f}",
    f"exec_latency_cycles_p95: {lat_p95:.2f}",
    f"benchmark_ranking_score: {benchmark_ranking_score:.2f}",
]

scoreboard_total = 0
scoreboard_matches = 0
for row in exec_rows:
    detail = (
        f"exec_case_{row['case']}: flow={row['flow']}, "
        f"latency_cycles={row['latency_cycles']}, out_value={row['out_value']}"
    )
    if "expected_value" in row:
        scoreboard_total += 1
        status = row.get("scoreboard", "n/a")
        if status == "match" and int(row["out_value"]) == int(row["expected_value"]):
            scoreboard_matches += 1
        detail += (
            f", expected_value={row['expected_value']}, scoreboard={row.get('scoreboard', 'n/a')}"
        )
    lines.append(detail)

scoreboard_pass_ratio = (scoreboard_matches / scoreboard_total) if scoreboard_total else 0.0
lines.append(f"scoreboard_total: {scoreboard_total}")
lines.append(f"scoreboard_matches: {scoreboard_matches}")
lines.append(f"scoreboard_pass_ratio: {scoreboard_pass_ratio:.4f}")

lines.extend(
    [
        "",
        "Schedule Metrics",
        f"makespan_cycles: {makespan}",
        f"instruction_count: {instruction_count}",
        f"fallback_instruction_count: {fallback_count}",
        f"fallback_instruction_ratio: {fallback_ratio:.4f}",
        f"fallback_ratio_by_flow: {per_flow_fallback_ratio_repr}",
        f"estimated_transfer_hops_metric: {estimated_hops_metric}",
        f"estimated_transfer_hops_total: {estimated_hops_total}",
        f"estimated_transfer_hops_edge_count: {estimated_hop_edges}",
        f"estimated_transfer_hops_avg_edge: {estimated_hops_avg:.4f}",
        f"estimated_transfer_hops_unresolved_edges: {estimated_hops_unresolved}",
        f"unique_core_count: {core_count}",
        f"busiest_core_index: {busiest_core_index}",
        f"busiest_core_issue_count: {busiest_core_issue_count}",
        f"busiest_core_busy_cycles: {busiest_core_busy_cycles}",
        f"flow_ids_in_schedule: {flows}",
        f"program_ids_in_schedule: {programs}",
        f"operations_seen: {ops}",
        f"core_issue_hotspots: {core_hotspots_repr}",
        f"node_latency_hotspots: {node_latency_hotspots_repr}",
        f"dependency_hotspots: {dependency_hotspots_repr}",
        f"critical_path_tail_by_node: {critical_tail_repr}",
        "",
        "RTL Test Results",
    ]
)
lines.extend(test_lines)

bench_path.write_text("\n".join(lines) + "\n")
print(f"[cw-bench] wrote benchmark reference: {bench_path}")

if update_sidecar:
    metrics_payload = {
        "exec_latency_cycles_min": lat_min,
        "exec_latency_cycles_max": lat_max,
        "exec_latency_cycles_avg": round(lat_avg, 2),
        "exec_latency_cycles_p50": round(lat_p50, 2),
        "exec_latency_cycles_p95": round(lat_p95, 2),
        "makespan_cycles": makespan,
        "total_ms": total_ms,
        "instruction_count": instruction_count,
        "fallback_instruction_count": fallback_count,
        "fallback_instruction_ratio": round(fallback_ratio, 6),
        "estimated_transfer_hops_metric": estimated_hops_metric,
        "estimated_transfer_hops_total": estimated_hops_total,
        "estimated_transfer_hops_edge_count": estimated_hop_edges,
        "estimated_transfer_hops_avg_edge": round(estimated_hops_avg, 6),
        "estimated_transfer_hops_unresolved_edges": estimated_hops_unresolved,
        "busiest_core_index": busiest_core_index,
        "busiest_core_issue_count": busiest_core_issue_count,
        "busiest_core_busy_cycles": busiest_core_busy_cycles,
        "benchmark_ranking_score": round(benchmark_ranking_score, 2),
        "scoreboard_total": scoreboard_total,
        "scoreboard_matches": scoreboard_matches,
        "scoreboard_pass_ratio": round(scoreboard_pass_ratio, 6),
    }
    latest_payload = {
        "format_version": 1,
        "run_utc": run_utc,
        "run_profile": run_profile,
        "inputs": {
            "cw_source": cw_file,
            "base_config": base_config,
            "effective_base_config": effective_base_config,
            "compiled_config": str(out_config),
            "flow_id": flow_id,
            "program_id": program_id,
            "program_priority_requested": program_priority_requested,
            "program_replicas": program_replicas,
            "program_max_parallel_flows": program_max_parallel,
            "program_load_balance_requested": program_load_balance_requested,
            "scheduler_program_policy_requested": scheduler_program_policy_requested,
            "cw_max_in_flight": cw_max_in_flight,
            "cw_lane_parallelism_requested": cw_lane_parallelism_requested,
            "cw_dtype_requested": cw_dtype_requested,
            "cw_placement_policy_requested": cw_placement_policy_requested,
            "cw_lowering_profile_requested": cw_lowering_profile_requested,
        },
        "timing_ms": {
            "compile_cw_ms": compile_ms,
            "validate_ms": validate_ms,
            "generate_ms": generate_ms,
            "iverilog_tests_ms": iverilog_ms,
            "total_ms": total_ms,
        },
        "metrics": metrics_payload,
        "placement_quality": {
            "fallback_ratio_by_flow": {str(flow): round(ratio, 6) for flow, ratio in per_flow_fallback_ratio.items()},
            "core_issue_hotspots": core_hotspots_repr,
            "node_latency_hotspots": node_latency_hotspots_repr,
            "dependency_hotspots": dependency_hotspots_repr,
            "critical_path_tail_by_node": critical_tail_repr,
        },
        "paths": {
            "benchmark_text": str(bench_path),
            "schedule_json": str(schedule_path),
        },
        "tool_versions": {
            "git_commit": git_commit,
            "git_tree_state": git_tree_state,
            "python_version": python_version,
            "iverilog_version": iverilog_version,
        },
    }

    sidecar_latest_path.write_text(json.dumps(latest_payload, indent=2, sort_keys=True) + "\n")
    print(f"[cw-bench] wrote latest sidecar: {sidecar_latest_path}")

    best_updated = False
    existing_best: dict | None = None
    if sidecar_best_path.exists():
        try:
            raw = json.loads(sidecar_best_path.read_text())
            if isinstance(raw, dict):
                existing_best = raw
        except Exception:  # noqa: BLE001
            existing_best = None

    if existing_best is None or score_tuple_from_metrics(latest_payload["metrics"]) < score_tuple_from_metrics(
        existing_best.get("metrics", {})
    ):
        sidecar_best_path.write_text(json.dumps(latest_payload, indent=2, sort_keys=True) + "\n")
        best_updated = True
    print(f"[cw-bench] wrote best sidecar: {sidecar_best_path} (updated={1 if best_updated else 0})")

    history_payload: dict = {"format_version": 1, "runs": []}
    if sidecar_history_path.exists():
        try:
            loaded = json.loads(sidecar_history_path.read_text())
            if isinstance(loaded, dict):
                history_payload = loaded
        except Exception:  # noqa: BLE001
            history_payload = {"format_version": 1, "runs": []}

    runs = history_payload.get("runs", [])
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "run_utc": run_utc,
            "run_profile": run_profile,
            "benchmark_text": str(bench_path),
            "exec_latency_cycles_avg": round(lat_avg, 2),
            "makespan_cycles": makespan,
            "total_ms": total_ms,
            "benchmark_ranking_score": round(benchmark_ranking_score, 2),
        }
    )
    if len(runs) > sidecar_history_keep:
        runs = runs[-sidecar_history_keep:]
    history_payload["runs"] = runs
    history_payload["best_sidecar"] = str(sidecar_best_path)
    sidecar_history_path.write_text(json.dumps(history_payload, indent=2, sort_keys=True) + "\n")
    print(f"[cw-bench] wrote history sidecar: {sidecar_history_path}")
PY

if [[ "$REGRESSION_CHECK" == "1" ]]; then
  echo "[cw-bench] regression check enabled"
  export CW_REG_BENCH_FILE="$BENCH_FILE"
  export CW_REG_CURRENT_JSON="$SIDECAR_LATEST_JSON"
  export CW_REG_BASELINE_PATH="$REGRESSION_BASELINE_JSON"
  export CW_REG_ALLOW_MISSING="$REGRESSION_ALLOW_MISSING_BASELINE"
  export CW_REG_MAX_LATENCY_DELTA="$REGRESSION_MAX_LATENCY_DELTA"
  export CW_REG_MAX_MAKESPAN_DELTA="$REGRESSION_MAX_MAKESPAN_DELTA"
  export CW_REG_MAX_TOTAL_MS_DELTA="$REGRESSION_MAX_TOTAL_MS_DELTA"
  python3 - <<'PY'
from __future__ import annotations

import json
import math
import os
from pathlib import Path


def parse_bench_metric(path: Path, key: str) -> float:
    for line in path.read_text().splitlines():
        if not line.startswith(f"{key}: "):
            continue
        return float(line.split(": ", 1)[1].strip())
    raise KeyError(f"Metric '{key}' not found in {path}")


def load_metrics(path: Path, *, fallback_bench: Path) -> dict[str, float]:
    if path.exists():
        if path.suffix == ".json":
            payload = json.loads(path.read_text())
            if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
                metrics = payload["metrics"]
                return {
                    "exec_latency_cycles_avg": float(metrics["exec_latency_cycles_avg"]),
                    "makespan_cycles": float(metrics["makespan_cycles"]),
                    "total_ms": float(metrics["total_ms"]),
                }
            if isinstance(payload, dict):
                return {
                    "exec_latency_cycles_avg": float(payload["exec_latency_cycles_avg"]),
                    "makespan_cycles": float(payload["makespan_cycles"]),
                    "total_ms": float(payload["total_ms"]),
                }
        else:
            return {
                "exec_latency_cycles_avg": parse_bench_metric(path, "exec_latency_cycles_avg"),
                "makespan_cycles": parse_bench_metric(path, "makespan_cycles"),
                "total_ms": parse_bench_metric(path, "total_ms"),
            }

    return {
        "exec_latency_cycles_avg": parse_bench_metric(fallback_bench, "exec_latency_cycles_avg"),
        "makespan_cycles": parse_bench_metric(fallback_bench, "makespan_cycles"),
        "total_ms": parse_bench_metric(fallback_bench, "total_ms"),
    }


bench_file = Path(os.environ["CW_REG_BENCH_FILE"])
current_json = Path(os.environ["CW_REG_CURRENT_JSON"])
baseline_path = Path(os.environ["CW_REG_BASELINE_PATH"])
allow_missing = os.environ["CW_REG_ALLOW_MISSING"] == "1"
max_latency_delta = float(os.environ["CW_REG_MAX_LATENCY_DELTA"])
max_makespan_delta = float(os.environ["CW_REG_MAX_MAKESPAN_DELTA"])
max_total_ms_delta = float(os.environ["CW_REG_MAX_TOTAL_MS_DELTA"])

current = load_metrics(current_json, fallback_bench=bench_file)
if not baseline_path.exists():
    if allow_missing:
        print(f"[cw-bench][regression] baseline not found: {baseline_path} (skipped)")
        raise SystemExit(0)
    raise SystemExit(f"[cw-bench][regression] baseline not found: {baseline_path}")

baseline = load_metrics(baseline_path, fallback_bench=bench_file)

latency_delta = current["exec_latency_cycles_avg"] - baseline["exec_latency_cycles_avg"]
makespan_delta = current["makespan_cycles"] - baseline["makespan_cycles"]
total_ms_delta = current["total_ms"] - baseline["total_ms"]

failures: list[str] = []
if latency_delta > max_latency_delta:
    failures.append(
        f"exec_latency_cycles_avg delta {latency_delta:.2f} exceeds {max_latency_delta:.2f}"
    )
if makespan_delta > max_makespan_delta:
    failures.append(
        f"makespan_cycles delta {makespan_delta:.2f} exceeds {max_makespan_delta:.2f}"
    )
if total_ms_delta > max_total_ms_delta:
    failures.append(
        f"total_ms delta {total_ms_delta:.2f} exceeds {max_total_ms_delta:.2f}"
    )

if failures:
    print("[cw-bench][regression] FAIL")
    for msg in failures:
        print(f"[cw-bench][regression] {msg}")
    raise SystemExit(3)

print(
    "[cw-bench][regression] PASS "
    f"latency_delta={latency_delta:.2f} makespan_delta={makespan_delta:.2f} total_ms_delta={total_ms_delta:.2f}"
)
PY
fi

echo "[cw-bench] done"
