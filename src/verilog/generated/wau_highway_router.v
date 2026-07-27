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

    input wire prev_in_valid,
    output wire prev_in_ready,
    input wire [CORE_ID_WIDTH-1:0] prev_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] prev_in_payload,

    output wire prev_out_valid,
    input wire prev_out_ready,
    output wire [CORE_ID_WIDTH-1:0] prev_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] prev_out_payload,

    input wire next_in_valid,
    output wire next_in_ready,
    input wire [CORE_ID_WIDTH-1:0] next_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] next_in_payload,

    output wire next_out_valid,
    input wire next_out_ready,
    output wire [CORE_ID_WIDTH-1:0] next_out_dst,
    output wire [PAYLOAD_WIDTH-1:0] next_out_payload
);
    localparam DIR_LOCAL = 3'd0;
    localparam DIR_PREV = 3'd1;
    localparam DIR_NEXT = 3'd2;
    localparam PORT_COUNT = 3;

    // One highway per line of cores. Everything off this line leaves
    // through the coordinator hub hanging off the west end, so the
    // router only has to ask: is the destination further along MY line?
    // Both bounds are elaboration-time constants, so this costs a pair
    // of comparators and no divider at all.
    localparam [CORE_ID_WIDTH-1:0] LINE_LAST = (CORE_INDEX - CORE_X) + GRID_X - 1;

    // Reserved destination meaning "leave this line": the grid is
    // capped at 255 cores, so the all-ones id is never a real core and
    // falls out of the west end into the hub under the rule below.
    localparam [CORE_ID_WIDTH-1:0] HUB_DST = {CORE_ID_WIDTH{1'b1}};

    function [2:0] route_dir;
        input [CORE_ID_WIDTH-1:0] dst_core;
        begin
            if (dst_core == HUB_DST) begin
                route_dir = DIR_PREV;
            end else if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin
                route_dir = DIR_LOCAL;
            end else if ((dst_core > CORE_INDEX[CORE_ID_WIDTH-1:0])
                       && (dst_core <= LINE_LAST)) begin
                route_dir = DIR_NEXT;
            end else begin
                // Either back along this line, or off it entirely --
                // both head west, and off-line traffic falls out of the
                // first core's PREV port into the hub.
                route_dir = DIR_PREV;
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
    assign in_valid[DIR_PREV] = prev_in_valid;
    assign in_valid[DIR_NEXT] = next_in_valid;

    assign in_dst[DIR_LOCAL] = local_in_dst;
    assign in_dst[DIR_PREV] = prev_in_dst;
    assign in_dst[DIR_NEXT] = next_in_dst;

    assign in_payload[DIR_LOCAL] = local_in_payload;
    assign in_payload[DIR_PREV] = prev_in_payload;
    assign in_payload[DIR_NEXT] = next_in_payload;

    assign out_ready[DIR_LOCAL] = local_out_ready;
    assign out_ready[DIR_PREV] = prev_out_ready;
    assign out_ready[DIR_NEXT] = next_out_ready;

    assign local_in_ready = in_ready_r[DIR_LOCAL];
    assign prev_in_ready = in_ready_r[DIR_PREV];
    assign next_in_ready = in_ready_r[DIR_NEXT];

    assign local_out_valid = out_valid_r[DIR_LOCAL];
    assign prev_out_valid = out_valid_r[DIR_PREV];
    assign next_out_valid = out_valid_r[DIR_NEXT];

    assign local_out_dst = out_dst_r[DIR_LOCAL];
    assign prev_out_dst = out_dst_r[DIR_PREV];
    assign next_out_dst = out_dst_r[DIR_NEXT];

    assign local_out_payload = out_payload_r[DIR_LOCAL];
    assign prev_out_payload = out_payload_r[DIR_PREV];
    assign next_out_payload = out_payload_r[DIR_NEXT];

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
