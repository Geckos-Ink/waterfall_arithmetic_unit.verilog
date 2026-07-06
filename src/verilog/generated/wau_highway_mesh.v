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
    parameter PAYLOAD_WIDTH = 64
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

    output wire [CORE_COUNT*32-1:0] router_hop_count,
    output wire [CORE_COUNT*32-1:0] router_stall_count,
    output wire [CORE_COUNT*32-1:0] router_local_delivered_count,
    output wire [CORE_COUNT*32-1:0] router_forward_count
);
    wire [CORE_COUNT-1:0] north_in_valid;
    wire [CORE_COUNT-1:0] north_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] north_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] north_in_payload;
    wire [CORE_COUNT-1:0] north_out_valid;
    wire [CORE_COUNT-1:0] north_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] north_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] north_out_payload;

    wire [CORE_COUNT-1:0] south_in_valid;
    wire [CORE_COUNT-1:0] south_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] south_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] south_in_payload;
    wire [CORE_COUNT-1:0] south_out_valid;
    wire [CORE_COUNT-1:0] south_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] south_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] south_out_payload;

    wire [CORE_COUNT-1:0] east_in_valid;
    wire [CORE_COUNT-1:0] east_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] east_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] east_in_payload;
    wire [CORE_COUNT-1:0] east_out_valid;
    wire [CORE_COUNT-1:0] east_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] east_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] east_out_payload;

    wire [CORE_COUNT-1:0] west_in_valid;
    wire [CORE_COUNT-1:0] west_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] west_in_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] west_in_payload;
    wire [CORE_COUNT-1:0] west_out_valid;
    wire [CORE_COUNT-1:0] west_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] west_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] west_out_payload;

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

    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;

    genvar gz;
    genvar gy;
    genvar gx;
    generate
        for (gz = 0; gz < GRID_Z; gz = gz + 1) begin : gen_z
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gz * LAYER_CORE_COUNT) + (gy * GRID_X) + gx;
                localparam integer NORTH_INDEX = CORE_INDEX - GRID_X;
                localparam integer SOUTH_INDEX = CORE_INDEX + GRID_X;
                localparam integer EAST_INDEX = CORE_INDEX + 1;
                localparam integer WEST_INDEX = CORE_INDEX - 1;
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
                    .local_in_valid(local_in_valid[CORE_INDEX]),
                    .local_in_ready(local_in_ready[CORE_INDEX]),
                    .local_in_dst(local_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .local_in_payload(local_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .local_out_valid(local_out_valid[CORE_INDEX]),
                    .local_out_ready(local_out_ready[CORE_INDEX]),
                    .local_out_dst(local_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .local_out_payload(local_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .north_in_valid(north_in_valid[CORE_INDEX]),
                    .north_in_ready(north_in_ready[CORE_INDEX]),
                    .north_in_dst(north_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .north_in_payload(north_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .north_out_valid(north_out_valid[CORE_INDEX]),
                    .north_out_ready(north_out_ready[CORE_INDEX]),
                    .north_out_dst(north_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .north_out_payload(north_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .south_in_valid(south_in_valid[CORE_INDEX]),
                    .south_in_ready(south_in_ready[CORE_INDEX]),
                    .south_in_dst(south_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .south_in_payload(south_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .south_out_valid(south_out_valid[CORE_INDEX]),
                    .south_out_ready(south_out_ready[CORE_INDEX]),
                    .south_out_dst(south_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .south_out_payload(south_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .east_in_valid(east_in_valid[CORE_INDEX]),
                    .east_in_ready(east_in_ready[CORE_INDEX]),
                    .east_in_dst(east_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .east_in_payload(east_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .east_out_valid(east_out_valid[CORE_INDEX]),
                    .east_out_ready(east_out_ready[CORE_INDEX]),
                    .east_out_dst(east_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .east_out_payload(east_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .west_in_valid(west_in_valid[CORE_INDEX]),
                    .west_in_ready(west_in_ready[CORE_INDEX]),
                    .west_in_dst(west_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .west_in_payload(west_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                    .west_out_valid(west_out_valid[CORE_INDEX]),
                    .west_out_ready(west_out_ready[CORE_INDEX]),
                    .west_out_dst(west_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .west_out_payload(west_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
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

                if (gy == 0) begin : north_edge
                    assign north_in_valid[CORE_INDEX] = 1'b0;
                    assign north_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign north_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign north_out_ready[CORE_INDEX] = 1'b1;
                end else begin : north_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_north_u (
                        .in_valid(south_out_valid[NORTH_INDEX]),
                        .in_ready(south_out_ready[NORTH_INDEX]),
                        .in_dst(south_out_dst[(NORTH_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(south_out_payload[(NORTH_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(north_in_valid[CORE_INDEX]),
                        .out_ready(north_in_ready[CORE_INDEX]),
                        .out_dst(north_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(north_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if (gy == GRID_Y - 1) begin : south_edge
                    assign south_in_valid[CORE_INDEX] = 1'b0;
                    assign south_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign south_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign south_out_ready[CORE_INDEX] = 1'b1;
                end else begin : south_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_south_u (
                        .in_valid(north_out_valid[SOUTH_INDEX]),
                        .in_ready(north_out_ready[SOUTH_INDEX]),
                        .in_dst(north_out_dst[(SOUTH_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(north_out_payload[(SOUTH_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(south_in_valid[CORE_INDEX]),
                        .out_ready(south_in_ready[CORE_INDEX]),
                        .out_dst(south_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(south_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if (gx == GRID_X - 1) begin : east_edge
                    assign east_in_valid[CORE_INDEX] = 1'b0;
                    assign east_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign east_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign east_out_ready[CORE_INDEX] = 1'b1;
                end else begin : east_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_east_u (
                        .in_valid(west_out_valid[EAST_INDEX]),
                        .in_ready(west_out_ready[EAST_INDEX]),
                        .in_dst(west_out_dst[(EAST_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(west_out_payload[(EAST_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(east_in_valid[CORE_INDEX]),
                        .out_ready(east_in_ready[CORE_INDEX]),
                        .out_dst(east_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(east_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
                    );
                end

                if (gx == 0) begin : west_edge
                    assign west_in_valid[CORE_INDEX] = 1'b0;
                    assign west_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                    assign west_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = {PAYLOAD_WIDTH{1'b0}};
                    assign west_out_ready[CORE_INDEX] = 1'b1;
                end else begin : west_link
                    wau_neighbor_forward #(
                        .CORE_ID_WIDTH(CORE_ID_WIDTH),
                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)
                    ) from_west_u (
                        .in_valid(east_out_valid[WEST_INDEX]),
                        .in_ready(east_out_ready[WEST_INDEX]),
                        .in_dst(east_out_dst[(WEST_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .in_payload(east_out_payload[(WEST_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),
                        .out_valid(west_in_valid[CORE_INDEX]),
                        .out_ready(west_in_ready[CORE_INDEX]),
                        .out_dst(west_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                        .out_payload(west_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
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
