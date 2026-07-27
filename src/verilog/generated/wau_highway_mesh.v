// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_mesh #(
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
    parameter GRID_Z = `WAU_GRID_Z,
    parameter CORE_COUNT = `WAU_CORE_COUNT,
    parameter CORE_ID_WIDTH = 8,
    parameter PAYLOAD_WIDTH = 64,
    parameter CONTRACT_BUS_ENABLE = 0,
    parameter CONTRACT_WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH,
    parameter CONTRACT_MAX_BURST = `WAU_HIGHWAY_CONTRACT_MAX_BURST,
    parameter CONTRACT_LEASE_CYCLES = `WAU_HIGHWAY_CONTRACT_LEASE_CYCLES
) (
    input wire clk,
    input wire rst_n,

    input wire [CORE_COUNT-1:0] local_in_valid,
    output wire [CORE_COUNT-1:0] local_in_ready,
    input wire [CORE_COUNT*CORE_ID_WIDTH-1:0] local_in_dst,
    input wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_in_payload,

    output wire [CORE_COUNT-1:0] local_out_valid,
    input wire [CORE_COUNT-1:0] local_out_ready,
    output wire [CORE_COUNT*CORE_ID_WIDTH-1:0] local_out_dst,
    output wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_out_payload,

    // Contracting bus: one core slot is offered per clock; the slot
    // owner answers with a request bit or a full transmission contract.
    input wire [CORE_COUNT-1:0] contract_req,
    input wire [CORE_COUNT*CONTRACT_WORD_WIDTH-1:0] contract_word,
    output wire [CORE_COUNT-1:0] contract_call,
    output wire [CORE_ID_WIDTH-1:0] contract_slot,
    output wire contract_grant_valid,
    output wire [CORE_ID_WIDTH-1:0] contract_grant_core,
    output wire [1:0] contract_grant_mode,
    output wire [15:0] contract_grant_remaining,
    output wire [31:0] contract_grant_count,
    output wire [31:0] contract_hold_cycles,
    output wire [31:0] contract_defer_count,

    output wire [CORE_COUNT*32-1:0] router_hop_count,
    output wire [CORE_COUNT*32-1:0] router_stall_count,
    output wire [CORE_COUNT*32-1:0] router_local_delivered_count,
    output wire [CORE_COUNT*32-1:0] router_forward_count
);
    wire [CORE_COUNT-1:0] prev_in_valid;
    wire [CORE_COUNT-1:0] prev_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] prev_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] prev_in_payload;
    wire [CORE_COUNT-1:0] prev_out_valid;
    wire [CORE_COUNT-1:0] prev_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] prev_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] prev_out_payload;

    wire [CORE_COUNT-1:0] next_in_valid;
    wire [CORE_COUNT-1:0] next_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] next_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] next_in_payload;
    wire [CORE_COUNT-1:0] next_out_valid;
    wire [CORE_COUNT-1:0] next_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] next_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] next_out_payload;

    wire [CORE_COUNT-1:0] up_in_valid;
    wire [CORE_COUNT-1:0] up_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] up_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] up_in_payload;
    wire [CORE_COUNT-1:0] up_out_valid;
    wire [CORE_COUNT-1:0] up_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] up_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] up_out_payload;

    wire [CORE_COUNT-1:0] down_in_valid;
    wire [CORE_COUNT-1:0] down_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] down_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] down_in_payload;
    wire [CORE_COUNT-1:0] down_out_valid;
    wire [CORE_COUNT-1:0] down_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] down_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] down_out_payload;

    // Contract-bus admission. `admit` is a registered decision, so gating the
    // local port here adds no combinational path through the routers. With the
    // bus disabled (or idle) every core is admitted and the highway behaves
    // exactly as an ungoverned mesh.
    wire [CORE_COUNT-1:0] admit;
    wire [CORE_COUNT-1:0] core_in_valid;
    wire [CORE_COUNT-1:0] core_in_ready;

    assign core_in_valid = local_in_valid & admit;
    assign local_in_ready = core_in_ready & admit;

    generate
        if (CONTRACT_BUS_ENABLE != 0) begin : gen_contract_bus
            wau_highway_contract #(
                .CORE_COUNT(CORE_COUNT),
                .CORE_ID_WIDTH(CORE_ID_WIDTH),
                .WORD_WIDTH(CONTRACT_WORD_WIDTH),
                .MAX_BURST(CONTRACT_MAX_BURST),
                .LEASE_CYCLES(CONTRACT_LEASE_CYCLES)
            ) contract_u (
                .clk(clk),
                .rst_n(rst_n),
                .req(contract_req),
                .word(contract_word),
                .pending(local_in_valid),
                .accepted(local_in_valid & local_in_ready),
                .admit(admit),
                .call(contract_call),
                .slot(contract_slot),
                .grant_valid(contract_grant_valid),
                .grant_core(contract_grant_core),
                .grant_mode(contract_grant_mode),
                .grant_remaining(contract_grant_remaining),
                .grant_lease(),
                .grant_count(contract_grant_count),
                .hold_cycles(contract_hold_cycles),
                .defer_count(contract_defer_count)
            );
        end else begin : gen_no_contract_bus
            assign admit = {CORE_COUNT{1'b1}};
            assign contract_call = {CORE_COUNT{1'b0}};
            assign contract_slot = {CORE_ID_WIDTH{1'b0}};
            assign contract_grant_valid = 1'b0;
            assign contract_grant_core = {CORE_ID_WIDTH{1'b0}};
            assign contract_grant_mode = 2'd0;
            assign contract_grant_remaining = 16'd0;
            assign contract_grant_count = 32'd0;
            assign contract_hold_cycles = 32'd0;
            assign contract_defer_count = 32'd0;
        end
    endgenerate


    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;

    genvar gz;
    genvar gy;
    genvar gx;
    generate
        for (gz = 0; gz < GRID_Z; gz = gz + 1) begin : gen_z
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gz * LAYER_CORE_COUNT) + (gy * GRID_X) + gx;
                localparam integer PREV_INDEX = CORE_INDEX - 1;
                localparam integer NEXT_INDEX = CORE_INDEX + 1;
                localparam integer UP_INDEX = CORE_INDEX - LAYER_CORE_COUNT;
                localparam integer DOWN_INDEX = CORE_INDEX + LAYER_CORE_COUNT;

                wau_highway_router #(
                    .CORE_INDEX(CORE_INDEX),
                    .CORE_X(gx),
                    .CORE_Y(gy),
                    .CORE_Z(gz),
                    .GRID_X(GRID_X),
                    .GRID_Y(GRID_Y),
                    .CORE_ID_WIDTH(CORE_ID_WIDTH),
                    .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                ) router_u (
                    .clk(clk),
                    .rst_n(rst_n),
                    .hop_count(router_hop_count[(CORE_INDEX*32) +: 32]),
                    .stall_count(router_stall_count[(CORE_INDEX*32) +: 32]),
                    .local_delivered_count(router_local_delivered_count[(CORE_INDEX*32) +: 32]),
                    .forward_count(router_forward_count[(CORE_INDEX*32) +: 32]),
                    .local_in_valid(core_in_valid[CORE_INDEX]),
                    .local_in_ready(core_in_ready[CORE_INDEX]),
                    .local_in_dst(local_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .local_in_payload(local_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .local_out_valid(local_out_valid[CORE_INDEX]),
                    .local_out_ready(local_out_ready[CORE_INDEX]),
                    .local_out_dst(local_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .local_out_payload(local_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .prev_in_valid(prev_in_valid[CORE_INDEX]),
                    .prev_in_ready(prev_in_ready[CORE_INDEX]),
                    .prev_in_dst(prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .prev_in_payload(prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .prev_out_valid(prev_out_valid[CORE_INDEX]),
                    .prev_out_ready(prev_out_ready[CORE_INDEX]),
                    .prev_out_dst(prev_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .prev_out_payload(prev_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .next_in_valid(next_in_valid[CORE_INDEX]),
                    .next_in_ready(next_in_ready[CORE_INDEX]),
                    .next_in_dst(next_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .next_in_payload(next_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .next_out_valid(next_out_valid[CORE_INDEX]),
                    .next_out_ready(next_out_ready[CORE_INDEX]),
                    .next_out_dst(next_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .next_out_payload(next_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .up_in_valid(up_in_valid[CORE_INDEX]),
                    .up_in_ready(up_in_ready[CORE_INDEX]),
                    .up_in_dst(up_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .up_in_payload(up_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .up_out_valid(up_out_valid[CORE_INDEX]),
                    .up_out_ready(up_out_ready[CORE_INDEX]),
                    .up_out_dst(up_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .up_out_payload(up_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .down_in_valid(down_in_valid[CORE_INDEX]),
                    .down_in_ready(down_in_ready[CORE_INDEX]),
                    .down_in_dst(down_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .down_in_payload(down_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .down_out_valid(down_out_valid[CORE_INDEX]),
                    .down_out_ready(down_out_ready[CORE_INDEX]),
                    .down_out_dst(down_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .down_out_payload(down_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                );

                if ((gx == 0) && (gy == 0)) begin : prev_edge
                    assign prev_in_valid[CORE_INDEX] = 1'b0;
                    assign prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign prev_out_ready[CORE_INDEX] = 1'b1;
                end else begin : prev_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_prev_u (
                        .in_valid(next_out_valid[PREV_INDEX]),
                        .in_ready(next_out_ready[PREV_INDEX]),
                        .in_dst(next_out_dst[(PREV_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(next_out_payload[(PREV_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(prev_in_valid[CORE_INDEX]),
                        .out_ready(prev_in_ready[CORE_INDEX]),
                        .out_dst(prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if ((gx == GRID_X - 1) && (gy == GRID_Y - 1)) begin : next_edge
                    assign next_in_valid[CORE_INDEX] = 1'b0;
                    assign next_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign next_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign next_out_ready[CORE_INDEX] = 1'b1;
                end else begin : next_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_next_u (
                        .in_valid(prev_out_valid[NEXT_INDEX]),
                        .in_ready(prev_out_ready[NEXT_INDEX]),
                        .in_dst(prev_out_dst[(NEXT_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(prev_out_payload[(NEXT_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(next_in_valid[CORE_INDEX]),
                        .out_ready(next_in_ready[CORE_INDEX]),
                        .out_dst(next_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(next_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if (gz == 0) begin : up_edge
                    assign up_in_valid[CORE_INDEX] = 1'b0;
                    assign up_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign up_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign up_out_ready[CORE_INDEX] = 1'b1;
                end else begin : up_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_up_u (
                        .in_valid(down_out_valid[UP_INDEX]),
                        .in_ready(down_out_ready[UP_INDEX]),
                        .in_dst(down_out_dst[(UP_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(down_out_payload[(UP_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(up_in_valid[CORE_INDEX]),
                        .out_ready(up_in_ready[CORE_INDEX]),
                        .out_dst(up_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(up_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if (gz == GRID_Z - 1) begin : down_edge
                    assign down_in_valid[CORE_INDEX] = 1'b0;
                    assign down_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign down_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign down_out_ready[CORE_INDEX] = 1'b1;
                end else begin : down_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_down_u (
                        .in_valid(up_out_valid[DOWN_INDEX]),
                        .in_ready(up_out_ready[DOWN_INDEX]),
                        .in_dst(up_out_dst[(DOWN_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(up_out_payload[(DOWN_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(down_in_valid[CORE_INDEX]),
                        .out_ready(down_in_ready[CORE_INDEX]),
                        .out_dst(down_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(down_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

            end
        end
        end
    endgenerate
endmodule
