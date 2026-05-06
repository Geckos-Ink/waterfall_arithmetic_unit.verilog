// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

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
    localparam integer CORE_ID_WIDTH = 8;
    localparam integer COORDINATOR_CORE_INDEX = 0;

    localparam integer CTRL_STAGE_LSB = 0;
    localparam integer CTRL_IMM_LSB = CTRL_STAGE_LSB + 8;
    localparam integer CTRL_USE_IMM_LSB = CTRL_IMM_LSB + DATA_WIDTH;
    localparam integer CTRL_B_LSB = CTRL_USE_IMM_LSB + 1;
    localparam integer CTRL_A_LSB = CTRL_B_LSB + DATA_WIDTH;
    localparam integer CTRL_OPCODE_LSB = CTRL_A_LSB + DATA_WIDTH;
    localparam integer CTRL_FLOW_ID_LSB = CTRL_OPCODE_LSB + OPCODE_WIDTH;
    localparam integer CTRL_PAYLOAD_WIDTH = CTRL_FLOW_ID_LSB + FLOW_ID_WIDTH;

    localparam integer DATA_FLOW_ID_LSB = 0;
    localparam integer DATA_STAGE_LSB = DATA_FLOW_ID_LSB + FLOW_ID_WIDTH;
    localparam integer DATA_VALUE_LSB = DATA_STAGE_LSB + 8;
    localparam integer DATA_SRC_CORE_LSB = DATA_VALUE_LSB + DATA_WIDTH;
    localparam integer DATA_PAYLOAD_WIDTH = DATA_SRC_CORE_LSB + CORE_ID_WIDTH;

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

    wire coord_dispatch_valid;
    wire coord_dispatch_ready;
    wire [CORE_ID_WIDTH-1:0] coord_dispatch_dst_core;
    wire [FLOW_ID_WIDTH-1:0] coord_dispatch_flow_id;
    wire [OPCODE_WIDTH-1:0] coord_dispatch_opcode;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_a;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_b;
    wire coord_dispatch_use_immediate;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_immediate_b;
    wire [7:0] coord_dispatch_stage_id;

    wire coord_result_valid;
    wire coord_result_ready;
    wire [CORE_ID_WIDTH-1:0] coord_result_src_core;
    wire [FLOW_ID_WIDTH-1:0] coord_result_flow_id;
    wire [7:0] coord_result_stage_id;
    wire signed [DATA_WIDTH-1:0] coord_result_value;

    wire [CORE_COUNT-1:0] ctrl_local_in_valid;
    wire [CORE_COUNT-1:0] ctrl_local_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] ctrl_local_in_dst;
    wire [CORE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_local_in_payload;
    wire [CORE_COUNT-1:0] ctrl_local_out_valid;
    wire [CORE_COUNT-1:0] ctrl_local_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] ctrl_local_out_dst;
    wire [CORE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_local_out_payload;

    wire [CORE_COUNT-1:0] data_local_in_valid;
    wire [CORE_COUNT-1:0] data_local_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] data_local_in_dst;
    wire [CORE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_local_in_payload;
    wire [CORE_COUNT-1:0] data_local_out_valid;
    wire [CORE_COUNT-1:0] data_local_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] data_local_out_dst;
    wire [CORE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_local_out_payload;

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
        .dispatch_pkt_valid(coord_dispatch_valid),
        .dispatch_pkt_ready(coord_dispatch_ready),
        .dispatch_pkt_dst_core(coord_dispatch_dst_core),
        .dispatch_pkt_flow_id(coord_dispatch_flow_id),
        .dispatch_pkt_opcode(coord_dispatch_opcode),
        .dispatch_pkt_a(coord_dispatch_a),
        .dispatch_pkt_b(coord_dispatch_b),
        .dispatch_pkt_use_immediate(coord_dispatch_use_immediate),
        .dispatch_pkt_immediate_b(coord_dispatch_immediate_b),
        .dispatch_pkt_stage_id(coord_dispatch_stage_id),
        .result_pkt_valid(coord_result_valid),
        .result_pkt_ready(coord_result_ready),
        .result_pkt_src_core(coord_result_src_core),
        .result_pkt_flow_id(coord_result_flow_id),
        .result_pkt_stage_id(coord_result_stage_id),
        .result_pkt_value(coord_result_value),
        .core_busy(core_busy)
    );

    genvar core_i;
    generate
        for (core_i = 0; core_i < CORE_COUNT; core_i = core_i + 1) begin : gen_local_binding
            if (core_i == COORDINATOR_CORE_INDEX) begin : gen_ctrl_ingress
                assign ctrl_local_in_valid[core_i] = coord_dispatch_valid;
                assign coord_dispatch_ready = ctrl_local_in_ready[core_i];
                assign ctrl_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = coord_dispatch_dst_core;
                assign ctrl_local_in_payload[(core_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {
                    coord_dispatch_flow_id,
                    coord_dispatch_opcode,
                    coord_dispatch_a,
                    coord_dispatch_b,
                    coord_dispatch_use_immediate,
                    coord_dispatch_immediate_b,
                    coord_dispatch_stage_id
                };
            end else begin : gen_ctrl_ingress_zero
                assign ctrl_local_in_valid[core_i] = 1'b0;
                assign ctrl_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                assign ctrl_local_in_payload[(core_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {CTRL_PAYLOAD_WIDTH{1'b0}};
            end

            assign core_dispatch_valid[core_i] = ctrl_local_out_valid[core_i];
            assign ctrl_local_out_ready[core_i] = core_dispatch_ready[core_i];
            assign core_dispatch_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_FLOW_ID_LSB +: FLOW_ID_WIDTH];
            assign core_dispatch_opcode[(core_i*OPCODE_WIDTH) +: OPCODE_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_OPCODE_LSB +: OPCODE_WIDTH];
            assign core_dispatch_a[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_A_LSB +: DATA_WIDTH];
            assign core_dispatch_b[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_B_LSB +: DATA_WIDTH];
            assign core_dispatch_use_immediate[core_i] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_USE_IMM_LSB +: 1];
            assign core_dispatch_immediate_b[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_IMM_LSB +: DATA_WIDTH];
            assign core_dispatch_stage_id[(core_i*8) +: 8] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_STAGE_LSB +: 8];

            assign data_local_in_valid[core_i] = core_result_valid[core_i];
            assign core_result_ready[core_i] = data_local_in_ready[core_i];
            assign data_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
            assign data_local_in_payload[(core_i*DATA_PAYLOAD_WIDTH) +: DATA_PAYLOAD_WIDTH] = {
                core_i[CORE_ID_WIDTH-1:0],
                core_result_value[(core_i*DATA_WIDTH) +: DATA_WIDTH],
                core_result_stage_id[(core_i*8) +: 8],
                core_result_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]
            };

            if (core_i == COORDINATOR_CORE_INDEX) begin : gen_data_egress
                assign coord_result_valid = data_local_out_valid[core_i];
                assign data_local_out_ready[core_i] = coord_result_ready;
                assign coord_result_src_core =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_SRC_CORE_LSB +: CORE_ID_WIDTH];
                assign coord_result_value =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_VALUE_LSB +: DATA_WIDTH];
                assign coord_result_stage_id =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_STAGE_LSB +: 8];
                assign coord_result_flow_id =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_FLOW_ID_LSB +: FLOW_ID_WIDTH];
            end else begin : gen_data_egress_drop
                assign data_local_out_ready[core_i] = 1'b1;
            end
        end
    endgenerate

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(CTRL_PAYLOAD_WIDTH)
    ) control_plane_mesh_u (
        .local_in_valid(ctrl_local_in_valid),
        .local_in_ready(ctrl_local_in_ready),
        .local_in_dst(ctrl_local_in_dst),
        .local_in_payload(ctrl_local_in_payload),
        .local_out_valid(ctrl_local_out_valid),
        .local_out_ready(ctrl_local_out_ready),
        .local_out_dst(ctrl_local_out_dst),
        .local_out_payload(ctrl_local_out_payload)
    );

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(DATA_PAYLOAD_WIDTH)
    ) data_plane_mesh_u (
        .local_in_valid(data_local_in_valid),
        .local_in_ready(data_local_in_ready),
        .local_in_dst(data_local_in_dst),
        .local_in_payload(data_local_in_payload),
        .local_out_valid(data_local_out_valid),
        .local_out_ready(data_local_out_ready),
        .local_out_dst(data_local_out_dst),
        .local_out_payload(data_local_out_payload)
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
endmodule
