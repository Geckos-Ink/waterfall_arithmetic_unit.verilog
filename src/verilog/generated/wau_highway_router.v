`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_router #(
    parameter CORE_INDEX = 0,
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter GRID_X = `WAU_GRID_X,
    parameter CORE_ID_WIDTH = 8,
    parameter PAYLOAD_WIDTH = 64
) (
    input wire local_in_valid,
    output reg local_in_ready,
    input wire [CORE_ID_WIDTH-1:0] local_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] local_in_payload,

    output reg local_out_valid,
    input wire local_out_ready,
    output reg [CORE_ID_WIDTH-1:0] local_out_dst,
    output reg [PAYLOAD_WIDTH-1:0] local_out_payload,

    input wire north_in_valid,
    output reg north_in_ready,
    input wire [CORE_ID_WIDTH-1:0] north_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] north_in_payload,

    output reg north_out_valid,
    input wire north_out_ready,
    output reg [CORE_ID_WIDTH-1:0] north_out_dst,
    output reg [PAYLOAD_WIDTH-1:0] north_out_payload,

    input wire south_in_valid,
    output reg south_in_ready,
    input wire [CORE_ID_WIDTH-1:0] south_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] south_in_payload,

    output reg south_out_valid,
    input wire south_out_ready,
    output reg [CORE_ID_WIDTH-1:0] south_out_dst,
    output reg [PAYLOAD_WIDTH-1:0] south_out_payload,

    input wire east_in_valid,
    output reg east_in_ready,
    input wire [CORE_ID_WIDTH-1:0] east_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] east_in_payload,

    output reg east_out_valid,
    input wire east_out_ready,
    output reg [CORE_ID_WIDTH-1:0] east_out_dst,
    output reg [PAYLOAD_WIDTH-1:0] east_out_payload,

    input wire west_in_valid,
    output reg west_in_ready,
    input wire [CORE_ID_WIDTH-1:0] west_in_dst,
    input wire [PAYLOAD_WIDTH-1:0] west_in_payload,

    output reg west_out_valid,
    input wire west_out_ready,
    output reg [CORE_ID_WIDTH-1:0] west_out_dst,
    output reg [PAYLOAD_WIDTH-1:0] west_out_payload
);
    localparam DIR_LOCAL = 3'd0;
    localparam DIR_NORTH = 3'd1;
    localparam DIR_SOUTH = 3'd2;
    localparam DIR_EAST = 3'd3;
    localparam DIR_WEST = 3'd4;

    integer sel_local;
    integer sel_north;
    integer sel_south;
    integer sel_east;
    integer sel_west;

    wire [2:0] local_dir;
    wire [2:0] north_dir;
    wire [2:0] south_dir;
    wire [2:0] east_dir;
    wire [2:0] west_dir;

    function [2:0] route_dir;
        input [CORE_ID_WIDTH-1:0] dst_core;
        integer dst_x;
        integer dst_y;
        begin
            if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin
                route_dir = DIR_LOCAL;
            end else begin
                dst_x = dst_core % GRID_X;
                dst_y = dst_core / GRID_X;

                if (dst_x > CORE_X) begin
                    route_dir = DIR_EAST;
                end else if (dst_x < CORE_X) begin
                    route_dir = DIR_WEST;
                end else if (dst_y > CORE_Y) begin
                    route_dir = DIR_SOUTH;
                end else begin
                    route_dir = DIR_NORTH;
                end
            end
        end
    endfunction

    assign local_dir = route_dir(local_in_dst);
    assign north_dir = route_dir(north_in_dst);
    assign south_dir = route_dir(south_in_dst);
    assign east_dir = route_dir(east_in_dst);
    assign west_dir = route_dir(west_in_dst);

    always @(*) begin
        local_in_ready = 1'b0;
        north_in_ready = 1'b0;
        south_in_ready = 1'b0;
        east_in_ready = 1'b0;
        west_in_ready = 1'b0;

        local_out_valid = 1'b0;
        local_out_dst = {CORE_ID_WIDTH{1'b0}};
        local_out_payload = {PAYLOAD_WIDTH{1'b0}};

        north_out_valid = 1'b0;
        north_out_dst = {CORE_ID_WIDTH{1'b0}};
        north_out_payload = {PAYLOAD_WIDTH{1'b0}};

        south_out_valid = 1'b0;
        south_out_dst = {CORE_ID_WIDTH{1'b0}};
        south_out_payload = {PAYLOAD_WIDTH{1'b0}};

        east_out_valid = 1'b0;
        east_out_dst = {CORE_ID_WIDTH{1'b0}};
        east_out_payload = {PAYLOAD_WIDTH{1'b0}};

        west_out_valid = 1'b0;
        west_out_dst = {CORE_ID_WIDTH{1'b0}};
        west_out_payload = {PAYLOAD_WIDTH{1'b0}};

        sel_local = -1;
        sel_north = -1;
        sel_south = -1;
        sel_east = -1;
        sel_west = -1;

        if (local_in_valid && local_dir == DIR_LOCAL) begin
            sel_local = 0;
        end else if (north_in_valid && north_dir == DIR_LOCAL) begin
            sel_local = 1;
        end else if (south_in_valid && south_dir == DIR_LOCAL) begin
            sel_local = 2;
        end else if (east_in_valid && east_dir == DIR_LOCAL) begin
            sel_local = 3;
        end else if (west_in_valid && west_dir == DIR_LOCAL) begin
            sel_local = 4;
        end

        if (local_in_valid && local_dir == DIR_NORTH) begin
            sel_north = 0;
        end else if (north_in_valid && north_dir == DIR_NORTH) begin
            sel_north = 1;
        end else if (south_in_valid && south_dir == DIR_NORTH) begin
            sel_north = 2;
        end else if (east_in_valid && east_dir == DIR_NORTH) begin
            sel_north = 3;
        end else if (west_in_valid && west_dir == DIR_NORTH) begin
            sel_north = 4;
        end

        if (local_in_valid && local_dir == DIR_SOUTH) begin
            sel_south = 0;
        end else if (north_in_valid && north_dir == DIR_SOUTH) begin
            sel_south = 1;
        end else if (south_in_valid && south_dir == DIR_SOUTH) begin
            sel_south = 2;
        end else if (east_in_valid && east_dir == DIR_SOUTH) begin
            sel_south = 3;
        end else if (west_in_valid && west_dir == DIR_SOUTH) begin
            sel_south = 4;
        end

        if (local_in_valid && local_dir == DIR_EAST) begin
            sel_east = 0;
        end else if (north_in_valid && north_dir == DIR_EAST) begin
            sel_east = 1;
        end else if (south_in_valid && south_dir == DIR_EAST) begin
            sel_east = 2;
        end else if (east_in_valid && east_dir == DIR_EAST) begin
            sel_east = 3;
        end else if (west_in_valid && west_dir == DIR_EAST) begin
            sel_east = 4;
        end

        if (local_in_valid && local_dir == DIR_WEST) begin
            sel_west = 0;
        end else if (north_in_valid && north_dir == DIR_WEST) begin
            sel_west = 1;
        end else if (south_in_valid && south_dir == DIR_WEST) begin
            sel_west = 2;
        end else if (east_in_valid && east_dir == DIR_WEST) begin
            sel_west = 3;
        end else if (west_in_valid && west_dir == DIR_WEST) begin
            sel_west = 4;
        end

        case (sel_local)
            0: begin
                local_out_valid = 1'b1;
                local_out_dst = local_in_dst;
                local_out_payload = local_in_payload;
                local_in_ready = local_out_ready;
            end
            1: begin
                local_out_valid = 1'b1;
                local_out_dst = north_in_dst;
                local_out_payload = north_in_payload;
                north_in_ready = local_out_ready;
            end
            2: begin
                local_out_valid = 1'b1;
                local_out_dst = south_in_dst;
                local_out_payload = south_in_payload;
                south_in_ready = local_out_ready;
            end
            3: begin
                local_out_valid = 1'b1;
                local_out_dst = east_in_dst;
                local_out_payload = east_in_payload;
                east_in_ready = local_out_ready;
            end
            4: begin
                local_out_valid = 1'b1;
                local_out_dst = west_in_dst;
                local_out_payload = west_in_payload;
                west_in_ready = local_out_ready;
            end
            default: begin
            end
        endcase

        case (sel_north)
            0: begin
                north_out_valid = 1'b1;
                north_out_dst = local_in_dst;
                north_out_payload = local_in_payload;
                local_in_ready = north_out_ready;
            end
            1: begin
                north_out_valid = 1'b1;
                north_out_dst = north_in_dst;
                north_out_payload = north_in_payload;
                north_in_ready = north_out_ready;
            end
            2: begin
                north_out_valid = 1'b1;
                north_out_dst = south_in_dst;
                north_out_payload = south_in_payload;
                south_in_ready = north_out_ready;
            end
            3: begin
                north_out_valid = 1'b1;
                north_out_dst = east_in_dst;
                north_out_payload = east_in_payload;
                east_in_ready = north_out_ready;
            end
            4: begin
                north_out_valid = 1'b1;
                north_out_dst = west_in_dst;
                north_out_payload = west_in_payload;
                west_in_ready = north_out_ready;
            end
            default: begin
            end
        endcase

        case (sel_south)
            0: begin
                south_out_valid = 1'b1;
                south_out_dst = local_in_dst;
                south_out_payload = local_in_payload;
                local_in_ready = south_out_ready;
            end
            1: begin
                south_out_valid = 1'b1;
                south_out_dst = north_in_dst;
                south_out_payload = north_in_payload;
                north_in_ready = south_out_ready;
            end
            2: begin
                south_out_valid = 1'b1;
                south_out_dst = south_in_dst;
                south_out_payload = south_in_payload;
                south_in_ready = south_out_ready;
            end
            3: begin
                south_out_valid = 1'b1;
                south_out_dst = east_in_dst;
                south_out_payload = east_in_payload;
                east_in_ready = south_out_ready;
            end
            4: begin
                south_out_valid = 1'b1;
                south_out_dst = west_in_dst;
                south_out_payload = west_in_payload;
                west_in_ready = south_out_ready;
            end
            default: begin
            end
        endcase

        case (sel_east)
            0: begin
                east_out_valid = 1'b1;
                east_out_dst = local_in_dst;
                east_out_payload = local_in_payload;
                local_in_ready = east_out_ready;
            end
            1: begin
                east_out_valid = 1'b1;
                east_out_dst = north_in_dst;
                east_out_payload = north_in_payload;
                north_in_ready = east_out_ready;
            end
            2: begin
                east_out_valid = 1'b1;
                east_out_dst = south_in_dst;
                east_out_payload = south_in_payload;
                south_in_ready = east_out_ready;
            end
            3: begin
                east_out_valid = 1'b1;
                east_out_dst = east_in_dst;
                east_out_payload = east_in_payload;
                east_in_ready = east_out_ready;
            end
            4: begin
                east_out_valid = 1'b1;
                east_out_dst = west_in_dst;
                east_out_payload = west_in_payload;
                west_in_ready = east_out_ready;
            end
            default: begin
            end
        endcase

        case (sel_west)
            0: begin
                west_out_valid = 1'b1;
                west_out_dst = local_in_dst;
                west_out_payload = local_in_payload;
                local_in_ready = west_out_ready;
            end
            1: begin
                west_out_valid = 1'b1;
                west_out_dst = north_in_dst;
                west_out_payload = north_in_payload;
                north_in_ready = west_out_ready;
            end
            2: begin
                west_out_valid = 1'b1;
                west_out_dst = south_in_dst;
                west_out_payload = south_in_payload;
                south_in_ready = west_out_ready;
            end
            3: begin
                west_out_valid = 1'b1;
                west_out_dst = east_in_dst;
                west_out_payload = east_in_payload;
                east_in_ready = west_out_ready;
            end
            4: begin
                west_out_valid = 1'b1;
                west_out_dst = west_in_dst;
                west_out_payload = west_in_payload;
                west_in_ready = west_out_ready;
            end
            default: begin
            end
        endcase
    end
endmodule
