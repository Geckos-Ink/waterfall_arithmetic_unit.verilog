// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core #(
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter CORE_Z = 0,
    parameter integer CORE_INDEX = 0,
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CORE_COUNT = `WAU_CORE_COUNT,
    parameter STATION_PROGRAM_ENABLE = `WAU_STATION_PROGRAM_ENABLE,
    parameter [7:0] COORD_DST_SENTINEL = 8'd0
) (
    input wire clk,
    input wire rst_n,

    input wire dispatch_valid,
    output wire dispatch_ready,
    input wire [FLOW_ID_WIDTH-1:0] dispatch_flow_id,
    input wire [OPCODE_WIDTH-1:0] dispatch_opcode,
    input wire signed [DATA_WIDTH-1:0] dispatch_a,
    input wire signed [DATA_WIDTH-1:0] dispatch_b,
    input wire dispatch_use_immediate,
    input wire signed [DATA_WIDTH-1:0] dispatch_immediate_b,
    input wire [7:0] dispatch_stage_id,

    // Fast-path self-dispatch, from the fabric's data plane (see
    // `_render_fabric_binding`) rather than the coordinator's control plane.
    input wire self_dispatch_valid,
    output wire self_dispatch_ready,
    input wire [FLOW_ID_WIDTH-1:0] self_dispatch_flow_id,
    input wire [OPCODE_WIDTH-1:0] self_dispatch_opcode,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_a,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_b_reg,
    input wire self_dispatch_use_immediate,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_immediate_b,
    input wire [7:0] self_dispatch_stage_id,
    input wire [CORE_COUNT-1:0] peer_busy,

    output wire result_valid,
    input wire result_ready,
    output wire [FLOW_ID_WIDTH-1:0] result_flow_id,
    output wire [7:0] result_stage_id,
    output wire signed [DATA_WIDTH-1:0] result_value,

    output wire signed [DATA_WIDTH-1:0] result_b_reg,
    output wire result_is_fast_path,
    output wire [7:0] result_dst_core,
    output wire [7:0] result_next_stage_id,
    output wire [OPCODE_WIDTH-1:0] result_next_opcode,
    output wire result_next_use_immediate,
    output wire signed [DATA_WIDTH-1:0] result_next_immediate_b,

    output wire busy,
    output wire cache_hit,
    output wire [31:0] cache_hit_count,
    output wire [31:0] cache_lookup_count
);
    wau_core_station #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH),
        .CORE_COUNT(CORE_COUNT),
        .STATION_PROGRAM_ENABLE(STATION_PROGRAM_ENABLE),
        .COORD_DST_SENTINEL(COORD_DST_SENTINEL),
        .CORE_INDEX(CORE_INDEX)
    ) station_u (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(dispatch_valid),
        .in_ready(dispatch_ready),
        .in_flow_id(dispatch_flow_id),
        .in_opcode(dispatch_opcode),
        .in_a(dispatch_a),
        .in_b(dispatch_b),
        .in_use_immediate(dispatch_use_immediate),
        .in_immediate_b(dispatch_immediate_b),
        .in_stage_id(dispatch_stage_id),
        .self_valid(self_dispatch_valid),
        .self_ready(self_dispatch_ready),
        .self_flow_id(self_dispatch_flow_id),
        .self_opcode(self_dispatch_opcode),
        .self_a(self_dispatch_a),
        .self_b_reg(self_dispatch_b_reg),
        .self_use_immediate(self_dispatch_use_immediate),
        .self_immediate_b(self_dispatch_immediate_b),
        .self_stage_id(self_dispatch_stage_id),
        .peer_busy(peer_busy),
        .out_valid(result_valid),
        .out_ready(result_ready),
        .out_flow_id(result_flow_id),
        .out_stage_id(result_stage_id),
        .out_value(result_value),
        .out_b_reg(result_b_reg),
        .out_is_fast_path(result_is_fast_path),
        .out_dst_core(result_dst_core),
        .out_next_stage_id(result_next_stage_id),
        .out_next_opcode(result_next_opcode),
        .out_next_use_immediate(result_next_use_immediate),
        .out_next_immediate_b(result_next_immediate_b),
        .busy(busy),
        .cache_hit(cache_hit),
        .cache_hit_count(cache_hit_count),
        .cache_lookup_count(cache_lookup_count)
    );
endmodule
