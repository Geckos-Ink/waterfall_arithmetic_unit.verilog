`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_top #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
    parameter CORE_COUNT = `WAU_CORE_COUNT
) (
    input wire clk,
    input wire rst_n,

    input wire host_in_valid,
    output wire host_in_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_in_a,
    input wire signed [DATA_WIDTH-1:0] host_in_b,

    output wire host_out_valid,
    input wire host_out_ready,
    output wire [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    output wire signed [DATA_WIDTH-1:0] host_out_value,

    input wire enable_auto_adapt
);
    wire [CORE_COUNT-1:0] core_dispatch_valid;
    wire [CORE_COUNT-1:0] core_dispatch_ready;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_dispatch_flow_id;
    wire [CORE_COUNT*OPCODE_WIDTH-1:0] core_dispatch_opcode;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_a;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_b;
    wire [CORE_COUNT-1:0] core_dispatch_use_immediate;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_immediate_b;
    wire [CORE_COUNT*8-1:0] core_dispatch_stage_id;

    wire [CORE_COUNT-1:0] core_result_valid;
    wire [CORE_COUNT-1:0] core_result_ready;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_result_flow_id;
    wire [CORE_COUNT*8-1:0] core_result_stage_id;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_result_value;
    wire [CORE_COUNT-1:0] core_busy;
    wire [CORE_COUNT-1:0] core_cache_hit;

    wau_coordinator #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH),
        .CORE_COUNT(CORE_COUNT)
    ) coordinator_u (
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
        .enable_auto_adapt(enable_auto_adapt),
        .core_dispatch_valid(core_dispatch_valid),
        .core_dispatch_ready(core_dispatch_ready),
        .core_dispatch_flow_id(core_dispatch_flow_id),
        .core_dispatch_opcode(core_dispatch_opcode),
        .core_dispatch_a(core_dispatch_a),
        .core_dispatch_b(core_dispatch_b),
        .core_dispatch_use_immediate(core_dispatch_use_immediate),
        .core_dispatch_immediate_b(core_dispatch_immediate_b),
        .core_dispatch_stage_id(core_dispatch_stage_id),
        .core_result_valid(core_result_valid),
        .core_result_ready(core_result_ready),
        .core_result_flow_id(core_result_flow_id),
        .core_result_stage_id(core_result_stage_id),
        .core_result_value(core_result_value),
        .core_busy(core_busy)
    );

    genvar gy;
    genvar gx;
    generate
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gy * GRID_X) + gx;

                wau_core #(
                    .CORE_X(gx),
                    .CORE_Y(gy),
                    .DATA_WIDTH(DATA_WIDTH),
                    .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
                    .OPCODE_WIDTH(OPCODE_WIDTH)
                ) core_u (
                    .clk(clk),
                    .rst_n(rst_n),
                    .dispatch_valid(core_dispatch_valid[CORE_INDEX]),
                    .dispatch_ready(core_dispatch_ready[CORE_INDEX]),
                    .dispatch_flow_id(core_dispatch_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .dispatch_opcode(core_dispatch_opcode[(CORE_INDEX*OPCODE_WIDTH) +: OPCODE_WIDTH]),
                    .dispatch_a(core_dispatch_a[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_b(core_dispatch_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_use_immediate(core_dispatch_use_immediate[CORE_INDEX]),
                    .dispatch_immediate_b(core_dispatch_immediate_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_stage_id(core_dispatch_stage_id[(CORE_INDEX*8) +: 8]),
                    .result_valid(core_result_valid[CORE_INDEX]),
                    .result_ready(core_result_ready[CORE_INDEX]),
                    .result_flow_id(core_result_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .result_stage_id(core_result_stage_id[(CORE_INDEX*8) +: 8]),
                    .result_value(core_result_value[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .busy(core_busy[CORE_INDEX]),
                    .cache_hit(core_cache_hit[CORE_INDEX])
                );
            end
        end
    endgenerate

    // Highway buses are intentionally left as extension points for future revisions.
    // The current basis routes stage-to-stage traffic through the coordinator.
endmodule
