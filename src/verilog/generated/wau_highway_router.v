// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_router #(
    parameter CORE_INDEX = 0,
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter CORE_Z = 0,
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
    parameter CORE_ID_WIDTH = 8,
    parameter PAYLOAD_WIDTH = 64
) (
    input wire clk,
    input wire rst_n,

    output reg [31:0] hop_count,
    output reg [31:0] stall_count,
    output reg [31:0] local_delivered_count,
    output reg [31:0] forward_count,

    input wire local_in_valid,
    output wire local_in_ready,
    input wire [CORE_ID_WIDTH-1:0] local_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] local_in_payload,

    output wire local_out_valid,
    input wire local_out_ready,
    output wire [CORE_ID_WIDTH-1:0] local_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] local_out_payload,

    input wire north_in_valid,
    output wire north_in_ready,
    input wire [CORE_ID_WIDTH-1:0] north_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] north_in_payload,

    output wire north_out_valid,
    input wire north_out_ready,
    output wire [CORE_ID_WIDTH-1:0] north_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] north_out_payload,

    input wire south_in_valid,
    output wire south_in_ready,
    input wire [CORE_ID_WIDTH-1:0] south_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] south_in_payload,

    output wire south_out_valid,
    input wire south_out_ready,
    output wire [CORE_ID_WIDTH-1:0] south_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] south_out_payload,

    input wire east_in_valid,
    output wire east_in_ready,
    input wire [CORE_ID_WIDTH-1:0] east_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] east_in_payload,

    output wire east_out_valid,
    input wire east_out_ready,
    output wire [CORE_ID_WIDTH-1:0] east_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] east_out_payload,

    input wire west_in_valid,
    output wire west_in_ready,
    input wire [CORE_ID_WIDTH-1:0] west_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] west_in_payload,

    output wire west_out_valid,
    input wire west_out_ready,
    output wire [CORE_ID_WIDTH-1:0] west_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] west_out_payload,

    input wire up_in_valid,
    output wire up_in_ready,
    input wire [CORE_ID_WIDTH-1:0] up_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] up_in_payload,

    output wire up_out_valid,
    input wire up_out_ready,
    output wire [CORE_ID_WIDTH-1:0] up_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] up_out_payload,

    input wire down_in_valid,
    output wire down_in_ready,
    input wire [CORE_ID_WIDTH-1:0] down_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] down_in_payload,

    output wire down_out_valid,
    input wire down_out_ready,
    output wire [CORE_ID_WIDTH-1:0] down_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] down_out_payload
);
    localparam DIR_LOCAL = 3'd0;
    localparam DIR_NORTH = 3'd1;
    localparam DIR_SOUTH = 3'd2;
    localparam DIR_EAST = 3'd3;
    localparam DIR_WEST = 3'd4;
    localparam DIR_UP = 3'd5;
    localparam DIR_DOWN = 3'd6;
    localparam PORT_COUNT = 7;

    function [2:0] route_dir;
        input [CORE_ID_WIDTH-1:0] dst_core;
        integer dst_x;
        integer dst_y;
        integer dst_z;
        begin
            if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin
                route_dir = DIR_LOCAL;
            end else begin
                dst_x = dst_core % GRID_X;
                dst_y = (dst_core / GRID_X) % GRID_Y;
                dst_z = dst_core / (GRID_X * GRID_Y);

                if (dst_x > CORE_X) begin
                    route_dir = DIR_EAST;
                end else if (dst_x < CORE_X) begin
                    route_dir = DIR_WEST;
                end else if (dst_y > CORE_Y) begin
                    route_dir = DIR_SOUTH;
                end else if (dst_y < CORE_Y) begin
                    route_dir = DIR_NORTH;
                end else if (dst_z > CORE_Z) begin
                    route_dir = DIR_DOWN;
                end else begin
                    route_dir = DIR_UP;
                end
            end
        end
    endfunction

    wire in_valid [0:PORT_COUNT-1];
    wire [CORE_ID_WIDTH-1:0] in_dst [0:PORT_COUNT-1];
    wire [PAYLOAD_WIDTH-1:0] in_payload [0:PORT_COUNT-1];
    wire out_ready [0:PORT_COUNT-1];

    reg in_ready_r [0:PORT_COUNT-1];
    reg out_valid_r [0:PORT_COUNT-1];
    reg [CORE_ID_WIDTH-1:0] out_dst_r [0:PORT_COUNT-1];
    reg [PAYLOAD_WIDTH-1:0] out_payload_r [0:PORT_COUNT-1];

    assign in_valid[DIR_LOCAL] = local_in_valid;
    assign in_valid[DIR_NORTH] = north_in_valid;
    assign in_valid[DIR_SOUTH] = south_in_valid;
    assign in_valid[DIR_EAST] = east_in_valid;
    assign in_valid[DIR_WEST] = west_in_valid;
    assign in_valid[DIR_UP] = up_in_valid;
    assign in_valid[DIR_DOWN] = down_in_valid;

    assign in_dst[DIR_LOCAL] = local_in_dst;
    assign in_dst[DIR_NORTH] = north_in_dst;
    assign in_dst[DIR_SOUTH] = south_in_dst;
    assign in_dst[DIR_EAST] = east_in_dst;
    assign in_dst[DIR_WEST] = west_in_dst;
    assign in_dst[DIR_UP] = up_in_dst;
    assign in_dst[DIR_DOWN] = down_in_dst;

    assign in_payload[DIR_LOCAL] = local_in_payload;
    assign in_payload[DIR_NORTH] = north_in_payload;
    assign in_payload[DIR_SOUTH] = south_in_payload;
    assign in_payload[DIR_EAST] = east_in_payload;
    assign in_payload[DIR_WEST] = west_in_payload;
    assign in_payload[DIR_UP] = up_in_payload;
    assign in_payload[DIR_DOWN] = down_in_payload;

    assign out_ready[DIR_LOCAL] = local_out_ready;
    assign out_ready[DIR_NORTH] = north_out_ready;
    assign out_ready[DIR_SOUTH] = south_out_ready;
    assign out_ready[DIR_EAST] = east_out_ready;
    assign out_ready[DIR_WEST] = west_out_ready;
    assign out_ready[DIR_UP] = up_out_ready;
    assign out_ready[DIR_DOWN] = down_out_ready;

    assign local_in_ready = in_ready_r[DIR_LOCAL];
    assign north_in_ready = in_ready_r[DIR_NORTH];
    assign south_in_ready = in_ready_r[DIR_SOUTH];
    assign east_in_ready = in_ready_r[DIR_EAST];
    assign west_in_ready = in_ready_r[DIR_WEST];
    assign up_in_ready = in_ready_r[DIR_UP];
    assign down_in_ready = in_ready_r[DIR_DOWN];

    assign local_out_valid = out_valid_r[DIR_LOCAL];
    assign north_out_valid = out_valid_r[DIR_NORTH];
    assign south_out_valid = out_valid_r[DIR_SOUTH];
    assign east_out_valid = out_valid_r[DIR_EAST];
    assign west_out_valid = out_valid_r[DIR_WEST];
    assign up_out_valid = out_valid_r[DIR_UP];
    assign down_out_valid = out_valid_r[DIR_DOWN];

    assign local_out_dst = out_dst_r[DIR_LOCAL];
    assign north_out_dst = out_dst_r[DIR_NORTH];
    assign south_out_dst = out_dst_r[DIR_SOUTH];
    assign east_out_dst = out_dst_r[DIR_EAST];
    assign west_out_dst = out_dst_r[DIR_WEST];
    assign up_out_dst = out_dst_r[DIR_UP];
    assign down_out_dst = out_dst_r[DIR_DOWN];

    assign local_out_payload = out_payload_r[DIR_LOCAL];
    assign north_out_payload = out_payload_r[DIR_NORTH];
    assign south_out_payload = out_payload_r[DIR_SOUTH];
    assign east_out_payload = out_payload_r[DIR_EAST];
    assign west_out_payload = out_payload_r[DIR_WEST];
    assign up_out_payload = out_payload_r[DIR_UP];
    assign down_out_payload = out_payload_r[DIR_DOWN];

    integer out_i;
    integer in_i;
    integer init_i;
    integer selected_input;

    always @(*) begin
        for (init_i = 0; init_i < PORT_COUNT; init_i = init_i + 1) begin
            in_ready_r[init_i] = 1'b0;
            out_valid_r[init_i] = 1'b0;
            out_dst_r[init_i] = {CORE_ID_WIDTH{1'b0}};
            out_payload_r[init_i] = {PAYLOAD_WIDTH{1'b0}};
        end

        for (out_i = 0; out_i < PORT_COUNT; out_i = out_i + 1) begin
            selected_input = -1;
            for (in_i = 0; in_i < PORT_COUNT; in_i = in_i + 1) begin
                if ((selected_input < 0) && in_valid[in_i] && (route_dir(in_dst[in_i]) == out_i)) begin
                    selected_input = in_i;
                end
            end

            if (selected_input >= 0) begin
                out_valid_r[out_i] = 1'b1;
                out_dst_r[out_i] = in_dst[selected_input];
                out_payload_r[out_i] = in_payload[selected_input];
                in_ready_r[selected_input] = out_ready[out_i];
            end
        end
    end

    // Observability: count successful forwards (any direction's accepted handshake),
    // local-out deliveries (packets that exit the mesh at this node), and stalls
    // (input valid but downstream not ready, i.e. handshake denied on this cycle).
    reg [3:0] in_handshake_count;
    reg [3:0] stall_now;
    reg [3:0] forwarded_to_neighbour;
    integer count_i;

    always @(*) begin
        in_handshake_count = 4'd0;
        stall_now = 4'd0;
        forwarded_to_neighbour = 4'd0;
        for (count_i = 0; count_i < PORT_COUNT; count_i = count_i + 1) begin
            if (in_valid[count_i] && in_ready_r[count_i]) begin
                in_handshake_count = in_handshake_count + 4'd1;
            end
            if (in_valid[count_i] && !in_ready_r[count_i]) begin
                stall_now = stall_now + 4'd1;
            end
            if ((count_i != DIR_LOCAL) && out_valid_r[count_i] && out_ready[count_i]) begin
                forwarded_to_neighbour = forwarded_to_neighbour + 4'd1;
            end
        end
    end

    wire local_out_handshake = out_valid_r[DIR_LOCAL] && out_ready[DIR_LOCAL];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hop_count <= 32'd0;
            stall_count <= 32'd0;
            local_delivered_count <= 32'd0;
            forward_count <= 32'd0;
        end else begin
            hop_count <= hop_count + {28'd0, in_handshake_count};
            stall_count <= stall_count + {28'd0, stall_now};
            if (local_out_handshake) begin
                local_delivered_count <= local_delivered_count + 32'd1;
            end
            forward_count <= forward_count + {28'd0, forwarded_to_neighbour};
        end
    end
endmodule
