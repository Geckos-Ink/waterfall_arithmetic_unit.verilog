// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`ifndef WAU_DEFS_VH
`define WAU_DEFS_VH

`define WAU_PROJECT_NAME "wau_de0_nano_demo"
`define WAU_ABSTRACTION_LANGUAGE "wau_flow_ir"
`define WAU_ABSTRACTION_VERSION 1
`define WAU_GRID_X 3
`define WAU_GRID_Y 2
`define WAU_CORE_COUNT 6
`define WAU_DATA_WIDTH 32
`define WAU_FLOW_ID_WIDTH 12
`define WAU_OPCODE_WIDTH 8
`define WAU_LOCAL_RAM_DEPTH 128
`define WAU_GLOBAL_RAM_DEPTH 2048
`define WAU_DATA_TYPE_COUNT 2
`define WAU_FLOW_COUNT 2
`define WAU_MAX_STAGES 3
`define WAU_COORD_MAX_IN_FLIGHT 4
`define WAU_OP_COUNT 5
`define WAU_STATION_CACHE_ENTRIES 4
`define WAU_STATION_CACHE_POLICY_FIFO 1
// Station cache policy: fifo (4 entries)
// Supported data types: int32, float16

`define WAU_OPCODE_ADD 8'h01
`define WAU_LATENCY_ADD 8'd1
`define WAU_OPCODE_SUB 8'h02
`define WAU_LATENCY_SUB 8'd1
`define WAU_OPCODE_MUL 8'h03
`define WAU_LATENCY_MUL 8'd3
`define WAU_OPCODE_DIV 8'h04
`define WAU_LATENCY_DIV 8'd8
`define WAU_OPCODE_MAX 8'h07
`define WAU_LATENCY_MAX 8'd1

`endif
