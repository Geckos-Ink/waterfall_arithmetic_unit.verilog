`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core #(
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
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

    output wire result_valid,
    input wire result_ready,
    output wire [FLOW_ID_WIDTH-1:0] result_flow_id,
    output wire [7:0] result_stage_id,
    output wire signed [DATA_WIDTH-1:0] result_value,

    output wire busy,
    output wire cache_hit
);
    wau_core_station #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH)
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
        .out_valid(result_valid),
        .out_ready(result_ready),
        .out_flow_id(result_flow_id),
        .out_stage_id(result_stage_id),
        .out_value(result_value),
        .busy(busy),
        .cache_hit(cache_hit)
    );
endmodule
