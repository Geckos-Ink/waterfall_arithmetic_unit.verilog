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
    parameter CONTRACT_LEASE_CYCLES = `WAU_HIGHWAY_CONTRACT_LEASE_CYCLES,
    parameter LINE_COUNT = GRID_Y * GRID_Z,
    parameter LINE_SIZE = GRID_X
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

    // Coordinator hubs: one per highway line. Anything addressed off a
    // line leaves through its own hub, which is what lets the lines run
    // independently instead of funnelling through a shared spine.
    input wire [LINE_COUNT-1:0] hub_in_valid,
    output wire [LINE_COUNT-1:0] hub_in_ready,
    input wire [LINE_COUNT*CORE_ID_WIDTH-1:0] hub_in_dst,
    input wire [LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_in_payload,

    output wire [LINE_COUNT-1:0] hub_out_valid,
    input wire [LINE_COUNT-1:0] hub_out_ready,
    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] hub_out_dst,
    output wire [LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_out_payload,

    // Contracting bus, one per highway line: a slot is offered per clock
    // and the slot owner answers with a request bit or a full contract.
    // Slot/grant ids are line-local (0..LINE_SIZE-1).
    input wire [CORE_COUNT-1:0] contract_req,
    input wire [CORE_COUNT*CONTRACT_WORD_WIDTH-1:0] contract_word,
    output wire [CORE_COUNT-1:0] contract_call,
    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] contract_slot,
    output wire [LINE_COUNT-1:0] contract_grant_valid,
    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] contract_grant_core,
    output wire [LINE_COUNT*2-1:0] contract_grant_mode,
    output wire [LINE_COUNT*16-1:0] contract_grant_remaining,
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

    // Contract-bus admission, one bus per highway line. `admit` is a
    // registered decision, so gating the local port here adds no combinational
    // path through the routers. With the bus disabled (or idle) every core is
    // admitted and the highway behaves exactly as an ungoverned fabric.
    wire [CORE_COUNT-1:0] admit;
    wire [CORE_COUNT-1:0] core_in_valid;
    wire [CORE_COUNT-1:0] core_in_ready;
    wire [CORE_COUNT-1:0] local_accepted;

    assign core_in_valid = local_in_valid & admit;
    assign local_in_ready = core_in_ready & admit;
    assign local_accepted = local_in_valid & local_in_ready;

    wire [LINE_COUNT*32-1:0] line_grant_count;
    wire [LINE_COUNT*32-1:0] line_hold_cycles;
    wire [LINE_COUNT*32-1:0] line_defer_count;

    genvar gl;
    generate
        if (CONTRACT_BUS_ENABLE != 0) begin : gen_contract_bus
            // Each line arbitrates on its own: a contract taken out on one row
            // never holds off traffic on another.
            for (gl = 0; gl < LINE_COUNT; gl = gl + 1) begin : gen_line_bus
                wau_highway_contract #(
                    .CORE_COUNT(LINE_SIZE),
                    .CORE_ID_WIDTH(CORE_ID_WIDTH),
                    .WORD_WIDTH(CONTRACT_WORD_WIDTH),
                    .MAX_BURST(CONTRACT_MAX_BURST),
                    .LEASE_CYCLES(CONTRACT_LEASE_CYCLES)
                ) contract_u (
                    .clk(clk),
                    .rst_n(rst_n),
                    .req(contract_req[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .word(contract_word[(gl*LINE_SIZE*CONTRACT_WORD_WIDTH) +: (LINE_SIZE*CONTRACT_WORD_WIDTH)]),
                    .pending(local_in_valid[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .accepted(local_accepted[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .admit(admit[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .call(contract_call[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .slot(contract_slot[(gl*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .grant_valid(contract_grant_valid[gl]),
                    .grant_core(contract_grant_core[(gl*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .grant_mode(contract_grant_mode[(gl*2) +: 2]),
                    .grant_remaining(contract_grant_remaining[(gl*16) +: 16]),
                    .grant_lease(),
                    .grant_count(line_grant_count[(gl*32) +: 32]),
                    .hold_cycles(line_hold_cycles[(gl*32) +: 32]),
                    .defer_count(line_defer_count[(gl*32) +: 32])
                );
            end
        end else begin : gen_no_contract_bus
            assign admit = {CORE_COUNT{1'b1}};
            assign contract_call = {CORE_COUNT{1'b0}};
            assign contract_slot = {(LINE_COUNT*CORE_ID_WIDTH){1'b0}};
            assign contract_grant_valid = {LINE_COUNT{1'b0}};
            assign contract_grant_core = {(LINE_COUNT*CORE_ID_WIDTH){1'b0}};
            assign contract_grant_mode = {(LINE_COUNT*2){1'b0}};
            assign contract_grant_remaining = {(LINE_COUNT*16){1'b0}};
            assign line_grant_count = {(LINE_COUNT*32){1'b0}};
            assign line_hold_cycles = {(LINE_COUNT*32){1'b0}};
            assign line_defer_count = {(LINE_COUNT*32){1'b0}};
        end
    endgenerate

    // Fabric-wide contract totals: the per-line buses summed, so the
    // observability bus keeps one meaning across every topology.
    reg [31:0] total_grant_count;
    reg [31:0] total_hold_cycles;
    reg [31:0] total_defer_count;
    integer line_i;
    always @(*) begin
        total_grant_count = 32'd0;
        total_hold_cycles = 32'd0;
        total_defer_count = 32'd0;
        for (line_i = 0; line_i < LINE_COUNT; line_i = line_i + 1) begin
            total_grant_count = total_grant_count + line_grant_count[(line_i*32) +: 32];
            total_hold_cycles = total_hold_cycles + line_hold_cycles[(line_i*32) +: 32];
            total_defer_count = total_defer_count + line_defer_count[(line_i*32) +: 32];
        end
    end
    assign contract_grant_count = total_grant_count;
    assign contract_hold_cycles = total_hold_cycles;
    assign contract_defer_count = total_defer_count;


    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;

    genvar gz;
    genvar gy;
    genvar gx;
    generate
        for (gz = 0; gz < GRID_Z; gz = gz + 1) begin : gen_z
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gz * LAYER_CORE_COUNT) + (gy * GRID_X) + gx;
                localparam integer LINE_INDEX = (gz * GRID_Y) + gy;
                localparam integer PREV_INDEX = CORE_INDEX - 1;
                localparam integer NEXT_INDEX = CORE_INDEX + 1;

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
                    .next_out_payload(next_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                );

                if (gx == 0) begin : prev_hub
                    assign prev_in_valid[CORE_INDEX] = hub_in_valid[LINE_INDEX];
                    assign hub_in_ready[LINE_INDEX] = prev_in_ready[CORE_INDEX];
                    assign prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = hub_in_dst[(LINE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH];
                    assign prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = hub_in_payload[(LINE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH];
                    assign hub_out_valid[LINE_INDEX] = prev_out_valid[CORE_INDEX];
                    assign prev_out_ready[CORE_INDEX] = hub_out_ready[LINE_INDEX];
                    assign hub_out_dst[(LINE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = prev_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH];
                    assign hub_out_payload[(LINE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = prev_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH];
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

                if (gx == GRID_X - 1) begin : next_edge
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

            end
        end
        end
    endgenerate
endmodule
