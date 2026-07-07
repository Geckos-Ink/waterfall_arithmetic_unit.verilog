#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.
#
# Simulator CW benchmark for the ad-hoc hardware-stress kernel
# (CWs/stress/mesh_stress.cw). This is a thin wrapper over the shared benchmark
# engine `run_cw_example_benchmark.sh`: it reuses the exact same compile ->
# validate -> generate -> iverilog-exec -> scoreboard pipeline (and all of its
# TUNE_MODE / MULTI_RUNS / REGRESSION_CHECK modes) but points every output at
# stress-specific paths so it never disturbs the tracked example-program
# reference (benchmarks/example_pogram_benchmark.txt) that CI gates on.
#
# Usage:
#   ./scripts/run_cw_stress_benchmark.sh
#   TUNE_MODE=1 ./scripts/run_cw_stress_benchmark.sh
#   ./scripts/run_cw_stress_benchmark.sh CWs/example-program.cw   # any CW kernel
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CW_FILE="${1:-CWs/stress/mesh_stress.cw}"
BASE_CONFIG="${2:-src/python/configs/wau_cw_fit_base.json}"
OUT_CONFIG="${3:-.build/cw_stress/wau_mesh_stress_compiled.json}"
BENCH_FILE="${4:-benchmarks/mesh_stress_benchmark.txt}"

# Stress kernel identity + isolated build/output locations.
export FLOW_ID="${FLOW_ID:-91}"
export PROGRAM_ID="${PROGRAM_ID:-91}"
export EXEC_FLOW_ID="${EXEC_FLOW_ID:-91}"
export OUT_DIR="${OUT_DIR:-.build/cw_stress_generated}"
export BUILD_DIR="${BUILD_DIR:-.build/cw_stress_iverilog}"

# Stress-specific benchmark artifacts (do NOT reuse example_pogram_* names).
export SIDECAR_LATEST_JSON="${SIDECAR_LATEST_JSON:-benchmarks/mesh_stress_benchmark_latest.json}"
export SIDECAR_BEST_JSON="${SIDECAR_BEST_JSON:-benchmarks/mesh_stress_benchmark_best.json}"
export SIDECAR_HISTORY_JSON="${SIDECAR_HISTORY_JSON:-benchmarks/mesh_stress_benchmark_history.json}"
export TUNE_SUMMARY_FILE="${TUNE_SUMMARY_FILE:-benchmarks/mesh_stress_tuning_latest.txt}"
export MULTI_SUMMARY_FILE="${MULTI_SUMMARY_FILE:-benchmarks/mesh_stress_multirun_latest.txt}"
export REPLAY_OUTPUT_FILE="${REPLAY_OUTPUT_FILE:-benchmarks/mesh_stress_replay_latest.txt}"
export REGRESSION_BASELINE_JSON="${REGRESSION_BASELINE_JSON:-benchmarks/mesh_stress_benchmark_best.json}"
export RUN_PROFILE="${RUN_PROFILE:-mesh_stress}"

exec "$ROOT_DIR/scripts/run_cw_example_benchmark.sh" \
  "$CW_FILE" "$BASE_CONFIG" "$OUT_CONFIG" "$BENCH_FILE"
