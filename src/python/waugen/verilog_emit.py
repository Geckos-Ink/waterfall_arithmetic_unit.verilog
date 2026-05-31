# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import json
from pathlib import Path

from .compiler import CompiledProject
from .scheduler import SchedulePlan, core_index
from .utils import macro_name

_SPDX_IDENTIFIER = "PolyForm-Noncommercial-1.0.0"
_VERILOG_LICENSE_HEADER = (
    f"// SPDX-License-Identifier: {_SPDX_IDENTIFIER}\n"
    "// See LICENSE at the repository root.\n\n"
)


def _op_macro(op_name: str) -> str:
    return macro_name(op_name)


def _with_verilog_license_header(content: str) -> str:
    if content.startswith(_VERILOG_LICENSE_HEADER):
        return content
    return _VERILOG_LICENSE_HEADER + content


def _render_defs(project: CompiledProject) -> str:
    cfg = project.config
    lines: list[str] = []
    lines.append("`ifndef WAU_DEFS_VH")
    lines.append("`define WAU_DEFS_VH")
    lines.append("")
    lines.append(f"`define WAU_PROJECT_NAME \"{cfg.project_name}\"")
    lines.append(f"`define WAU_ABSTRACTION_LANGUAGE \"{cfg.abstraction_language}\"")
    lines.append(f"`define WAU_ABSTRACTION_VERSION {cfg.abstraction_version}")
    lines.append(f"`define WAU_GRID_X {cfg.device.grid_x}")
    lines.append(f"`define WAU_GRID_Y {cfg.device.grid_y}")
    lines.append(f"`define WAU_CORE_COUNT {cfg.device.grid_x * cfg.device.grid_y}")
    lines.append(f"`define WAU_DATA_WIDTH {cfg.device.data_width}")
    lines.append(f"`define WAU_FLOW_ID_WIDTH {cfg.device.flow_id_width}")
    lines.append(f"`define WAU_OPCODE_WIDTH {cfg.device.opcode_width}")
    lines.append(f"`define WAU_LOCAL_RAM_DEPTH {cfg.device.local_ram_depth}")
    lines.append(f"`define WAU_GLOBAL_RAM_DEPTH {cfg.device.global_ram_depth}")
    lines.append(f"`define WAU_DATA_TYPE_COUNT {len(cfg.device.supported_data_types)}")
    lines.append(f"`define WAU_FLOW_COUNT {len(project.flows)}")
    lines.append(f"`define WAU_MAX_STAGES {project.max_stages}")
    lines.append(f"`define WAU_COORD_MAX_IN_FLIGHT {cfg.coordinator.max_in_flight}")
    lines.append(f"`define WAU_OP_COUNT {len(cfg.operations)}")
    station_cache = cfg.compiler.station_cache
    lines.append(f"`define WAU_STATION_CACHE_ENTRIES {station_cache.entries}")
    lines.append(
        f"`define WAU_STATION_CACHE_POLICY_{station_cache.replacement_policy.upper()} 1"
    )
    lines.append(
        f"// Station cache policy: {station_cache.replacement_policy} "
        f"({station_cache.entries} entries)"
    )
    lines.append(f"// Supported data types: {', '.join(cfg.device.supported_data_types)}")
    lines.append("")
    for op in cfg.operations:
        m = _op_macro(op.name)
        lines.append(f"`define WAU_OPCODE_{m} {cfg.device.opcode_width}'h{op.opcode:02X}")
        lines.append(f"`define WAU_LATENCY_{m} 8'd{op.latency}")
    lines.append("")
    lines.append("`endif")
    return "\n".join(lines) + "\n"


def _render_operation_alu(project: CompiledProject) -> str:
    case_lines = []
    for op in project.config.operations:
        case_lines.append(f"      `WAU_OPCODE_{_op_macro(op.name)}: result_comb = {op.verilog_expr};")
    case_blob = "\n".join(case_lines)

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_operation_alu #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
) (
    input wire clk,
    input wire rst_n,
    input wire in_valid,
    input wire [OPCODE_WIDTH-1:0] opcode,
    input wire signed [DATA_WIDTH-1:0] a,
    input wire signed [DATA_WIDTH-1:0] b,
    output reg out_valid,
    output reg signed [DATA_WIDTH-1:0] y
);
    reg signed [DATA_WIDTH-1:0] result_comb;

    always @(*) begin
        result_comb = {{DATA_WIDTH{{1'b0}}}};
        case (opcode)
{case_blob}
            default: result_comb = {{DATA_WIDTH{{1'b0}}}};
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            y <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            out_valid <= in_valid;
            y <= result_comb;
        end
    end
endmodule
"""


def _render_core_station(project: CompiledProject) -> str:
    latency_cases = []
    for op in project.config.operations:
        latency_cases.append(
            f"                `WAU_OPCODE_{_op_macro(op.name)}: op_latency = `WAU_LATENCY_{_op_macro(op.name)};"
        )
    latency_blob = "\n".join(latency_cases)

    cache_entries = project.config.compiler.station_cache.entries
    policy = project.config.compiler.station_cache.replacement_policy
    lru_enabled = "1" if policy == "lru" else "0"

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core_station #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CACHE_ENTRIES = `WAU_STATION_CACHE_ENTRIES,
    parameter CACHE_LRU_ENABLED = {lru_enabled}
) (
    input wire clk,
    input wire rst_n,

    input wire in_valid,
    output wire in_ready,
    input wire [FLOW_ID_WIDTH-1:0] in_flow_id,
    input wire [OPCODE_WIDTH-1:0] in_opcode,
    input wire signed [DATA_WIDTH-1:0] in_a,
    input wire signed [DATA_WIDTH-1:0] in_b,
    input wire in_use_immediate,
    input wire signed [DATA_WIDTH-1:0] in_immediate_b,
    input wire [7:0] in_stage_id,

    output reg out_valid,
    input wire out_ready,
    output reg [FLOW_ID_WIDTH-1:0] out_flow_id,
    output reg [7:0] out_stage_id,
    output reg signed [DATA_WIDTH-1:0] out_value,

    output wire busy,
    output reg cache_hit,
    output reg [31:0] cache_hit_count,
    output reg [31:0] cache_lookup_count
);
    localparam ST_IDLE = 2'd0;
    localparam ST_EXEC = 2'd1;
    localparam ST_OUT = 2'd2;

    reg [1:0] state;

    reg [FLOW_ID_WIDTH-1:0] active_flow_id;
    reg [OPCODE_WIDTH-1:0] active_opcode;
    reg [7:0] active_stage_id;
    reg signed [DATA_WIDTH-1:0] op_a;
    reg signed [DATA_WIDTH-1:0] op_b;
    reg [7:0] wait_cycles;

    reg cache_valid [0:CACHE_ENTRIES-1];
    reg [OPCODE_WIDTH-1:0] cache_opcode [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_a [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_b [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_value [0:CACHE_ENTRIES-1];
    reg [31:0] cache_age [0:CACHE_ENTRIES-1];
    reg [7:0] cache_replace_ptr;
    reg [31:0] cache_age_clock;

    reg cache_hit_comb;
    reg [7:0] cache_hit_index;
    reg signed [DATA_WIDTH-1:0] cache_hit_value;

    reg alu_in_valid;
    wire alu_out_valid;
    wire signed [DATA_WIDTH-1:0] alu_out_value;

    reg result_latched_valid;
    reg signed [DATA_WIDTH-1:0] result_latched_value;

    integer cache_idx;
    integer cache_scan;
    integer victim_scan;
    reg [7:0] victim_index;
    reg [31:0] victim_age;
    reg victim_filled;

    wire signed [DATA_WIDTH-1:0] effective_b;
    assign effective_b = in_use_immediate ? in_immediate_b : in_b;

    assign in_ready = (state == ST_IDLE);
    assign busy = (state != ST_IDLE);

    function [7:0] op_latency;
        input [OPCODE_WIDTH-1:0] opcode;
        begin
            case (opcode)
{latency_blob}
                default: op_latency = 8'd1;
            endcase
        end
    endfunction

    wau_operation_alu #(
        .DATA_WIDTH(DATA_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH)
    ) alu_u (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(alu_in_valid),
        .opcode(active_opcode),
        .a(op_a),
        .b(op_b),
        .out_valid(alu_out_valid),
        .y(alu_out_value)
    );

    always @(*) begin
        cache_hit_comb = 1'b0;
        cache_hit_index = 8'd0;
        cache_hit_value = {{DATA_WIDTH{{1'b0}}}};
        for (cache_scan = 0; cache_scan < CACHE_ENTRIES; cache_scan = cache_scan + 1) begin
            if (cache_valid[cache_scan] &&
                (cache_opcode[cache_scan] == in_opcode) &&
                (cache_a[cache_scan] == in_a) &&
                (cache_b[cache_scan] == effective_b) &&
                !cache_hit_comb) begin
                cache_hit_comb = 1'b1;
                cache_hit_index = cache_scan[7:0];
                cache_hit_value = cache_value[cache_scan];
            end
        end
    end

    // LRU victim selection: pick the entry with the smallest age (oldest reference);
    // unused entries (cache_valid=0) take priority. Tie-broken by lowest index.
    always @(*) begin
        victim_index = 8'd0;
        victim_age = 32'hFFFFFFFF;
        victim_filled = 1'b0;
        if (CACHE_LRU_ENABLED) begin
            for (victim_scan = 0; victim_scan < CACHE_ENTRIES; victim_scan = victim_scan + 1) begin
                if (!cache_valid[victim_scan] && !victim_filled) begin
                    victim_index = victim_scan[7:0];
                    victim_filled = 1'b1;
                end
            end
            if (!victim_filled) begin
                victim_index = 8'd0;
                victim_age = cache_age[0];
                for (victim_scan = 1; victim_scan < CACHE_ENTRIES; victim_scan = victim_scan + 1) begin
                    if (cache_age[victim_scan] < victim_age) begin
                        victim_index = victim_scan[7:0];
                        victim_age = cache_age[victim_scan];
                    end
                end
            end
        end else begin
            victim_index = cache_replace_ptr;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            out_valid <= 1'b0;
            out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            out_stage_id <= 8'd0;
            out_value <= {{DATA_WIDTH{{1'b0}}}};
            active_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            active_opcode <= {{OPCODE_WIDTH{{1'b0}}}};
            active_stage_id <= 8'd0;
            op_a <= {{DATA_WIDTH{{1'b0}}}};
            op_b <= {{DATA_WIDTH{{1'b0}}}};
            wait_cycles <= 8'd0;
            cache_replace_ptr <= 8'd0;
            cache_age_clock <= 32'd1;
            for (cache_idx = 0; cache_idx < CACHE_ENTRIES; cache_idx = cache_idx + 1) begin
                cache_valid[cache_idx] <= 1'b0;
                cache_opcode[cache_idx] <= {{OPCODE_WIDTH{{1'b0}}}};
                cache_a[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_b[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_value[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_age[cache_idx] <= 32'd0;
            end
            cache_hit <= 1'b0;
            cache_hit_count <= 32'd0;
            cache_lookup_count <= 32'd0;
            alu_in_valid <= 1'b0;
            result_latched_valid <= 1'b0;
            result_latched_value <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            alu_in_valid <= 1'b0;
            cache_hit <= 1'b0;

            if (out_valid && out_ready) begin
                out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    result_latched_valid <= 1'b0;
                    if (in_valid && in_ready) begin
                        cache_hit <= cache_hit_comb;
                        cache_lookup_count <= cache_lookup_count + 32'd1;
                        if (cache_hit_comb) begin
                            cache_hit_count <= cache_hit_count + 32'd1;
                            out_valid <= 1'b1;
                            out_flow_id <= in_flow_id;
                            out_stage_id <= in_stage_id;
                            out_value <= cache_hit_value;
                            if (CACHE_LRU_ENABLED) begin
                                cache_age[cache_hit_index] <= cache_age_clock;
                                cache_age_clock <= cache_age_clock + 32'd1;
                            end
                            state <= ST_OUT;
                        end else begin
                            active_flow_id <= in_flow_id;
                            active_opcode <= in_opcode;
                            active_stage_id <= in_stage_id;
                            op_a <= in_a;
                            op_b <= effective_b;
                            wait_cycles <= op_latency(in_opcode) - 8'd1;

                            alu_in_valid <= 1'b1;
                            state <= ST_EXEC;
                        end
                    end
                end

                ST_EXEC: begin
                    if (alu_out_valid) begin
                        result_latched_valid <= 1'b1;
                        result_latched_value <= alu_out_value;
                    end

                    if (wait_cycles != 8'd0) begin
                        wait_cycles <= wait_cycles - 8'd1;
                    end

                    if ((wait_cycles == 8'd0) && (result_latched_valid || alu_out_valid)) begin
                        out_valid <= 1'b1;
                        out_flow_id <= active_flow_id;
                        out_stage_id <= active_stage_id;
                        out_value <= alu_out_valid ? alu_out_value : result_latched_value;

                        cache_valid[victim_index] <= 1'b1;
                        cache_opcode[victim_index] <= active_opcode;
                        cache_a[victim_index] <= op_a;
                        cache_b[victim_index] <= op_b;
                        cache_value[victim_index] <= alu_out_valid ? alu_out_value : result_latched_value;
                        if (CACHE_LRU_ENABLED) begin
                            cache_age[victim_index] <= cache_age_clock;
                            cache_age_clock <= cache_age_clock + 32'd1;
                        end else begin
                            if (cache_replace_ptr == (CACHE_ENTRIES - 1)) begin
                                cache_replace_ptr <= 8'd0;
                            end else begin
                                cache_replace_ptr <= cache_replace_ptr + 8'd1;
                            end
                        end

                        result_latched_valid <= 1'b0;
                        state <= ST_OUT;
                    end
                end

                ST_OUT: begin
                    if (out_valid && out_ready) begin
                        state <= ST_IDLE;
                    end
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end
endmodule
"""


def _render_core() -> str:
    return """`timescale 1ns/1ps
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
    output wire cache_hit,
    output wire [31:0] cache_hit_count,
    output wire [31:0] cache_lookup_count
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
        .cache_hit(cache_hit),
        .cache_hit_count(cache_hit_count),
        .cache_lookup_count(cache_lookup_count)
    );
endmodule
"""


def _render_neighbor_forward() -> str:
    return """`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_neighbor_forward #(
    parameter CORE_ID_WIDTH = 8,
    parameter PAYLOAD_WIDTH = 64
) (
    input wire in_valid,
    output wire in_ready,
    input wire [CORE_ID_WIDTH-1:0] in_dst,
    input wire [PAYLOAD_WIDTH-1:0] in_payload,

    output wire out_valid,
    input wire out_ready,
    output wire [CORE_ID_WIDTH-1:0] out_dst,
    output wire [PAYLOAD_WIDTH-1:0] out_payload
);
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign out_dst = in_dst;
    assign out_payload = in_payload;
endmodule
"""


def _render_highway_router() -> str:
    return """`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_router #(
    parameter CORE_INDEX = 0,
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter GRID_X = `WAU_GRID_X,
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

    // Observability: count successful forwards (any direction's accepted handshake),
    // local-out deliveries (packets that exit the mesh at this node), and stalls
    // (input valid but downstream not ready, i.e. handshake denied on this cycle).
    wire local_handshake = local_in_valid && local_in_ready;
    wire north_handshake = north_in_valid && north_in_ready;
    wire south_handshake = south_in_valid && south_in_ready;
    wire east_handshake = east_in_valid && east_in_ready;
    wire west_handshake = west_in_valid && west_in_ready;

    wire local_stall = local_in_valid && !local_in_ready;
    wire north_stall = north_in_valid && !north_in_ready;
    wire south_stall = south_in_valid && !south_in_ready;
    wire east_stall = east_in_valid && !east_in_ready;
    wire west_stall = west_in_valid && !west_in_ready;

    wire local_out_handshake = local_out_valid && local_out_ready;
    wire north_out_handshake = north_out_valid && north_out_ready;
    wire south_out_handshake = south_out_valid && south_out_ready;
    wire east_out_handshake = east_out_valid && east_out_ready;
    wire west_out_handshake = west_out_valid && west_out_ready;

    function [3:0] popcount5;
        input [4:0] bits;
        integer pc_i;
        begin
            popcount5 = 4'd0;
            for (pc_i = 0; pc_i < 5; pc_i = pc_i + 1) begin
                if (bits[pc_i]) popcount5 = popcount5 + 4'd1;
            end
        end
    endfunction

    wire [3:0] in_handshake_count = popcount5(
        {west_handshake, east_handshake, south_handshake, north_handshake, local_handshake}
    );
    wire [3:0] stall_now = popcount5(
        {west_stall, east_stall, south_stall, north_stall, local_stall}
    );
    wire [3:0] forwarded_to_neighbour = popcount5(
        {west_out_handshake, east_out_handshake, south_out_handshake, north_out_handshake, 1'b0}
    );

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
"""


def _render_highway_mesh() -> str:
    return """`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_mesh #(
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
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

    genvar gy;
    genvar gx;
    generate
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gy * GRID_X) + gx;
                localparam integer NORTH_INDEX = CORE_INDEX - GRID_X;
                localparam integer SOUTH_INDEX = CORE_INDEX + GRID_X;
                localparam integer EAST_INDEX = CORE_INDEX + 1;
                localparam integer WEST_INDEX = CORE_INDEX - 1;

                wau_highway_router #(
                    .CORE_INDEX(CORE_INDEX),
                    .CORE_X(gx),
                    .CORE_Y(gy),
                    .GRID_X(GRID_X),
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
                    .west_out_payload(west_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])
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
            end
        end
    endgenerate
endmodule
"""


def _stage_case_entries(project: CompiledProject, field: str) -> str:
    lines: list[str] = []
    for flow in project.flows:
        for stage in flow.stages:
            key = (flow.flow_slot << 8) | stage.stage_index
            if field == "opcode":
                value = f"{project.config.device.opcode_width}'h{stage.opcode:02X}"
            elif field == "primary_core":
                idx = core_index(stage.primary_core.x, stage.primary_core.y, project.config.device.grid_x)
                value = f"8'd{idx}"
            elif field == "fallback_core":
                fallback = stage.fallback_core or stage.primary_core
                idx = core_index(fallback.x, fallback.y, project.config.device.grid_x)
                value = f"8'd{idx}"
            elif field == "use_immediate":
                value = "1'b1" if stage.immediate_b is not None else "1'b0"
            elif field == "immediate_b":
                imm = stage.immediate_b if stage.immediate_b is not None else 0
                value = f"{project.config.device.data_width}'sd{imm}"
            else:
                raise ValueError(field)
            lines.append(f"                16'h{key:04X}: value = {value};")
    return "\n".join(lines)


def _flow_last_stage_entries(project: CompiledProject) -> str:
    lines = []
    for flow in project.flows:
        lines.append(
            f"                8'd{flow.flow_slot}: value = 8'd{len(flow.stages) - 1};"
        )
    return "\n".join(lines)


def _flow_slot_entries(project: CompiledProject) -> str:
    lines = []
    for flow in project.flows:
        lines.append(
            f"                {project.config.device.flow_id_width}'d{flow.flow_id}: value = 8'd{flow.flow_slot};"
        )
    return "\n".join(lines)


def _render_coordinator(project: CompiledProject) -> str:
    data_width = project.config.device.data_width
    flow_id_width = project.config.device.flow_id_width
    opcode_width = project.config.device.opcode_width
    flow_slot_default = "8'hFF"

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_coordinator #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CORE_COUNT = `WAU_CORE_COUNT
) (
    input wire clk,
    input wire rst_n,

    input wire host_in_valid,
    output wire host_in_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_in_a,
    input wire signed [DATA_WIDTH-1:0] host_in_b,

    output reg host_out_valid,
    input wire host_out_ready,
    output reg [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    output reg signed [DATA_WIDTH-1:0] host_out_value,

    input wire enable_auto_adapt,

    output reg dispatch_pkt_valid,
    input wire dispatch_pkt_ready,
    output reg [7:0] dispatch_pkt_dst_core,
    output reg [FLOW_ID_WIDTH-1:0] dispatch_pkt_flow_id,
    output reg [OPCODE_WIDTH-1:0] dispatch_pkt_opcode,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_a,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_b,
    output reg dispatch_pkt_use_immediate,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_immediate_b,
    output reg [7:0] dispatch_pkt_stage_id,

    input wire result_pkt_valid,
    output wire result_pkt_ready,
    input wire [7:0] result_pkt_src_core,
    input wire [FLOW_ID_WIDTH-1:0] result_pkt_flow_id,
    input wire [7:0] result_pkt_stage_id,
    input wire signed [DATA_WIDTH-1:0] result_pkt_value,

    input wire [CORE_COUNT-1:0] core_busy
);
    localparam MAX_IN_FLIGHT = `WAU_COORD_MAX_IN_FLIGHT;

    // Multi-issue slot table. Each slot holds one in-flight flow's accumulator
    // context; the coordinator keeps up to MAX_IN_FLIGHT *distinct* flows
    // executing concurrently across the core mesh. Per-flow semantics are
    // identical to the legacy serial coordinator (one accumulator chain walked
    // stage-by-stage), so a single in-flight flow keeps cycle-identical timing
    // while independent flows now overlap on different cores.
    reg                          slot_valid     [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_flow_slot [0:MAX_IN_FLIGHT-1];
    reg [FLOW_ID_WIDTH-1:0]      slot_flow_id   [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_stage     [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_acc       [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_opb       [0:MAX_IN_FLIGHT-1];
    reg                          slot_awaiting  [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_wait_core [0:MAX_IN_FLIGHT-1];
    reg                          slot_done      [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_outval    [0:MAX_IN_FLIGHT-1];

    // Combinational selections, driven by the always @(*) blocks below.
    reg        disp_found;
    reg [7:0]  disp_slot;
    reg [7:0]  disp_core;
    reg        res_found;
    reg [7:0]  res_slot;
    reg        out_found;
    reg [7:0]  out_slot;
    reg        alloc_found;
    reg [7:0]  alloc_slot;
    reg        flow_busy;

    // Arbiter scratch.
    reg [7:0]  cc_p;
    reg [7:0]  cc_f;
    reg [7:0]  cc;
    reg        any_found;
    reg [7:0]  any_slot;
    reg [7:0]  any_core;
    reg [7:0]  sel_flow_slot;
    reg [7:0]  sel_stage;
    integer    di;
    integer    ri;
    integer    oi;
    integer    ai;
    integer    fi;
    integer    k;
    reg        latched_now;

    // Accept a new flow when a slot is free and that flow id is not already in
    // flight (keeps result matching by flow+stage unambiguous without widening
    // the dispatch/result packet format with a tag).
    assign host_in_ready = alloc_found && !flow_busy;
    assign result_pkt_ready = res_found;

    function [7:0] flow_slot_from_id;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        reg [7:0] value;
        begin
            value = {flow_slot_default};
            case (flow_id)
{_flow_slot_entries(project)}
                default: value = {flow_slot_default};
            endcase
            flow_slot_from_id = value;
        end
    endfunction

    function [7:0] flow_last_stage;
        input [7:0] flow_slot;
        reg [7:0] value;
        begin
            value = 8'd0;
            case (flow_slot)
{_flow_last_stage_entries(project)}
                default: value = 8'd0;
            endcase
            flow_last_stage = value;
        end
    endfunction

    function [OPCODE_WIDTH-1:0] flow_stage_opcode;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [OPCODE_WIDTH-1:0] value;
        begin
            value = {{OPCODE_WIDTH{{1'b0}}}};
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "opcode")}
                default: value = {{OPCODE_WIDTH{{1'b0}}}};
            endcase
            flow_stage_opcode = value;
        end
    endfunction

    function [7:0] flow_stage_primary_core;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [7:0] value;
        begin
            value = 8'd0;
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "primary_core")}
                default: value = 8'd0;
            endcase
            flow_stage_primary_core = value;
        end
    endfunction

    function [7:0] flow_stage_fallback_core;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [7:0] value;
        begin
            value = 8'd0;
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "fallback_core")}
                default: value = 8'd0;
            endcase
            flow_stage_fallback_core = value;
        end
    endfunction

    function flow_stage_use_immediate;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg value;
        begin
            value = 1'b0;
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "use_immediate")}
                default: value = 1'b0;
            endcase
            flow_stage_use_immediate = value;
        end
    endfunction

    function signed [DATA_WIDTH-1:0] flow_stage_immediate_b;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg signed [DATA_WIDTH-1:0] value;
        begin
            value = {{DATA_WIDTH{{1'b0}}}};
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "immediate_b")}
                default: value = {{DATA_WIDTH{{1'b0}}}};
            endcase
            flow_stage_immediate_b = value;
        end
    endfunction

    // ---- dispatch arbiter + packet drive -------------------------------
    // Pick a dispatchable slot (valid, not awaiting a result, not finished),
    // preferring one whose chosen core is currently free so independent flows
    // light up different cores in the same cycle window. One dispatch is issued
    // per cycle, but many slots can be awaiting results at once -> real overlap.
    always @(*) begin
        disp_found = 1'b0;
        disp_slot  = 8'd0;
        disp_core  = 8'd0;
        any_found  = 1'b0;
        any_slot   = 8'd0;
        any_core   = 8'd0;
        for (di = 0; di < MAX_IN_FLIGHT; di = di + 1) begin
            if (slot_valid[di] && !slot_awaiting[di] && !slot_done[di] &&
                (slot_flow_slot[di] != 8'hFF)) begin
                cc_p = flow_stage_primary_core(slot_flow_slot[di], slot_stage[di]);
                cc_f = flow_stage_fallback_core(slot_flow_slot[di], slot_stage[di]);
                cc = cc_p;
                if (enable_auto_adapt && (cc_f != cc_p) &&
                    core_busy[cc_p] && !core_busy[cc_f]) begin
                    cc = cc_f;
                end
                if (!any_found) begin
                    any_found = 1'b1;
                    any_slot  = di;
                    any_core  = cc;
                end
                if (!disp_found && !core_busy[cc]) begin
                    disp_found = 1'b1;
                    disp_slot  = di;
                    disp_core  = cc;
                end
            end
        end
        if (!disp_found && any_found) begin
            disp_found = 1'b1;
            disp_slot  = any_slot;
            disp_core  = any_core;
        end

        sel_flow_slot = slot_flow_slot[disp_slot];
        sel_stage     = slot_stage[disp_slot];

        dispatch_pkt_valid         = disp_found;
        dispatch_pkt_dst_core      = disp_core;
        dispatch_pkt_flow_id       = slot_flow_id[disp_slot];
        dispatch_pkt_opcode        = flow_stage_opcode(sel_flow_slot, sel_stage);
        dispatch_pkt_a             = slot_acc[disp_slot];
        dispatch_pkt_b             = slot_opb[disp_slot];
        dispatch_pkt_use_immediate = flow_stage_use_immediate(sel_flow_slot, sel_stage);
        dispatch_pkt_immediate_b   = flow_stage_immediate_b(sel_flow_slot, sel_stage);
        dispatch_pkt_stage_id      = sel_stage;
    end

    // ---- result matcher: map an incoming result to its awaiting slot ----
    always @(*) begin
        res_found = 1'b0;
        res_slot  = 8'd0;
        for (ri = 0; ri < MAX_IN_FLIGHT; ri = ri + 1) begin
            if (!res_found && slot_valid[ri] && slot_awaiting[ri] &&
                (slot_flow_id[ri]   == result_pkt_flow_id) &&
                (slot_stage[ri]     == result_pkt_stage_id) &&
                (slot_wait_core[ri] == result_pkt_src_core)) begin
                res_found = 1'b1;
                res_slot  = ri;
            end
        end
    end

    // ---- completed-output selector + free-slot/flow guards -------------
    always @(*) begin
        out_found = 1'b0;
        out_slot  = 8'd0;
        for (oi = 0; oi < MAX_IN_FLIGHT; oi = oi + 1) begin
            if (!out_found && slot_valid[oi] && slot_done[oi]) begin
                out_found = 1'b1;
                out_slot  = oi;
            end
        end
    end

    always @(*) begin
        alloc_found = 1'b0;
        alloc_slot  = 8'd0;
        for (ai = 0; ai < MAX_IN_FLIGHT; ai = ai + 1) begin
            if (!alloc_found && !slot_valid[ai]) begin
                alloc_found = 1'b1;
                alloc_slot  = ai;
            end
        end
    end

    always @(*) begin
        flow_busy = 1'b0;
        for (fi = 0; fi < MAX_IN_FLIGHT; fi = fi + 1) begin
            if (slot_valid[fi] && (slot_flow_id[fi] == host_in_flow_id)) begin
                flow_busy = 1'b1;
            end
        end
    end

    // ---- sequential state ----------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            host_out_valid   <= 1'b0;
            host_out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            host_out_value   <= {{DATA_WIDTH{{1'b0}}}};
            for (k = 0; k < MAX_IN_FLIGHT; k = k + 1) begin
                slot_valid[k]     <= 1'b0;
                slot_flow_slot[k] <= 8'hFF;
                slot_flow_id[k]   <= {{FLOW_ID_WIDTH{{1'b0}}}};
                slot_stage[k]     <= 8'd0;
                slot_acc[k]       <= {{DATA_WIDTH{{1'b0}}}};
                slot_opb[k]       <= {{DATA_WIDTH{{1'b0}}}};
                slot_awaiting[k]  <= 1'b0;
                slot_wait_core[k] <= 8'd0;
                slot_done[k]      <= 1'b0;
                slot_outval[k]    <= {{DATA_WIDTH{{1'b0}}}};
            end
        end else begin
            latched_now = 1'b0;

            // drain a consumed output
            if (host_out_valid && host_out_ready) begin
                host_out_valid <= 1'b0;
            end

            // accept a new flow into a free slot; unknown flow ids are consumed
            // but never allocated -> silently dropped (legacy behaviour).
            if (host_in_valid && host_in_ready && alloc_found) begin
                if (flow_slot_from_id(host_in_flow_id) != 8'hFF) begin
                    slot_valid[alloc_slot]     <= 1'b1;
                    slot_flow_slot[alloc_slot] <= flow_slot_from_id(host_in_flow_id);
                    slot_flow_id[alloc_slot]   <= host_in_flow_id;
                    slot_stage[alloc_slot]     <= 8'd0;
                    slot_acc[alloc_slot]       <= host_in_a;
                    slot_opb[alloc_slot]       <= host_in_b;
                    slot_awaiting[alloc_slot]  <= 1'b0;
                    slot_done[alloc_slot]      <= 1'b0;
                end
            end

            // dispatch handshake: mark the issued slot awaiting its core result
            if (dispatch_pkt_valid && dispatch_pkt_ready && disp_found) begin
                slot_awaiting[disp_slot]  <= 1'b1;
                slot_wait_core[disp_slot] <= disp_core;
            end

            // result handshake: advance the matched slot's accumulator chain
            if (result_pkt_valid && result_pkt_ready && res_found) begin
                slot_awaiting[res_slot] <= 1'b0;
                if (slot_stage[res_slot] >= flow_last_stage(slot_flow_slot[res_slot])) begin
                    // final stage: latch output immediately when the port is
                    // free (keeps single-flow latency identical to the serial
                    // design); otherwise buffer it in the slot until it drains.
                    if (!host_out_valid || host_out_ready) begin
                        host_out_valid       <= 1'b1;
                        host_out_flow_id     <= slot_flow_id[res_slot];
                        host_out_value       <= result_pkt_value;
                        slot_valid[res_slot] <= 1'b0;
                        slot_done[res_slot]  <= 1'b0;
                        latched_now = 1'b1;
                    end else begin
                        slot_done[res_slot]   <= 1'b1;
                        slot_outval[res_slot] <= result_pkt_value;
                    end
                end else begin
                    slot_acc[res_slot]   <= result_pkt_value;
                    slot_stage[res_slot] <= slot_stage[res_slot] + 8'd1;
                end
            end

            // drain a previously-buffered completed slot when the port is free
            if (!latched_now && (!host_out_valid || host_out_ready) && out_found) begin
                host_out_valid       <= 1'b1;
                host_out_flow_id     <= slot_flow_id[out_slot];
                host_out_value       <= slot_outval[out_slot];
                slot_valid[out_slot] <= 1'b0;
                slot_done[out_slot]  <= 1'b0;
            end
        end
    end
endmodule
"""


def _render_top(project: CompiledProject) -> str:
    module_name = project.config.output_module_name
    template = """`timescale 1ns/1ps
`include "wau_defs.vh"

module __WAU_TOP_MODULE__ #(
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

    input wire enable_auto_adapt,

    output wire [31:0] obs_total_hop_count,
    output wire [31:0] obs_total_stall_count,
    output wire [31:0] obs_total_forward_count,
    output wire [31:0] obs_total_local_delivered_count,
    output wire [31:0] obs_total_cache_hit_count,
    output wire [31:0] obs_total_cache_lookup_count
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
    wire [CORE_COUNT*32-1:0] core_cache_hit_count;
    wire [CORE_COUNT*32-1:0] core_cache_lookup_count;

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

    wire [CORE_COUNT*32-1:0] ctrl_router_hop_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_stall_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_forward_count;

    wire [CORE_COUNT*32-1:0] data_router_hop_count;
    wire [CORE_COUNT*32-1:0] data_router_stall_count;
    wire [CORE_COUNT*32-1:0] data_router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] data_router_forward_count;

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(CTRL_PAYLOAD_WIDTH)
    ) control_plane_mesh_u (
        .clk(clk),
        .rst_n(rst_n),
        .local_in_valid(ctrl_local_in_valid),
        .local_in_ready(ctrl_local_in_ready),
        .local_in_dst(ctrl_local_in_dst),
        .local_in_payload(ctrl_local_in_payload),
        .local_out_valid(ctrl_local_out_valid),
        .local_out_ready(ctrl_local_out_ready),
        .local_out_dst(ctrl_local_out_dst),
        .local_out_payload(ctrl_local_out_payload),
        .router_hop_count(ctrl_router_hop_count),
        .router_stall_count(ctrl_router_stall_count),
        .router_local_delivered_count(ctrl_router_local_delivered_count),
        .router_forward_count(ctrl_router_forward_count)
    );

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(DATA_PAYLOAD_WIDTH)
    ) data_plane_mesh_u (
        .clk(clk),
        .rst_n(rst_n),
        .local_in_valid(data_local_in_valid),
        .local_in_ready(data_local_in_ready),
        .local_in_dst(data_local_in_dst),
        .local_in_payload(data_local_in_payload),
        .local_out_valid(data_local_out_valid),
        .local_out_ready(data_local_out_ready),
        .local_out_dst(data_local_out_dst),
        .local_out_payload(data_local_out_payload),
        .router_hop_count(data_router_hop_count),
        .router_stall_count(data_router_stall_count),
        .router_local_delivered_count(data_router_local_delivered_count),
        .router_forward_count(data_router_forward_count)
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
                    .cache_hit(core_cache_hit[CORE_INDEX]),
                    .cache_hit_count(core_cache_hit_count[(CORE_INDEX*32) +: 32]),
                    .cache_lookup_count(core_cache_lookup_count[(CORE_INDEX*32) +: 32])
                );
            end
        end
    endgenerate

    // Observability aggregation: sum per-core / per-router counters into single
    // saturation-unaware 32-bit totals. These are intended for host-side polling
    // (e.g. via wau_host_mmio) and for testbenches that want a single
    // throughput/hit-rate signal across the mesh.
    reg [31:0] total_hop_count;
    reg [31:0] total_stall_count;
    reg [31:0] total_forward_count;
    reg [31:0] total_local_delivered_count;
    reg [31:0] total_cache_hit_count;
    reg [31:0] total_cache_lookup_count;
    integer obs_i;
    always @(*) begin
        total_hop_count = 32'd0;
        total_stall_count = 32'd0;
        total_forward_count = 32'd0;
        total_local_delivered_count = 32'd0;
        for (obs_i = 0; obs_i < CORE_COUNT; obs_i = obs_i + 1) begin
            total_hop_count = total_hop_count
                + ctrl_router_hop_count[(obs_i*32) +: 32]
                + data_router_hop_count[(obs_i*32) +: 32];
            total_stall_count = total_stall_count
                + ctrl_router_stall_count[(obs_i*32) +: 32]
                + data_router_stall_count[(obs_i*32) +: 32];
            total_forward_count = total_forward_count
                + ctrl_router_forward_count[(obs_i*32) +: 32]
                + data_router_forward_count[(obs_i*32) +: 32];
            total_local_delivered_count = total_local_delivered_count
                + ctrl_router_local_delivered_count[(obs_i*32) +: 32]
                + data_router_local_delivered_count[(obs_i*32) +: 32];
        end
    end
    always @(*) begin
        total_cache_hit_count = 32'd0;
        total_cache_lookup_count = 32'd0;
        for (obs_i = 0; obs_i < CORE_COUNT; obs_i = obs_i + 1) begin
            total_cache_hit_count = total_cache_hit_count
                + core_cache_hit_count[(obs_i*32) +: 32];
            total_cache_lookup_count = total_cache_lookup_count
                + core_cache_lookup_count[(obs_i*32) +: 32];
        end
    end
    assign obs_total_hop_count = total_hop_count;
    assign obs_total_stall_count = total_stall_count;
    assign obs_total_forward_count = total_forward_count;
    assign obs_total_local_delivered_count = total_local_delivered_count;
    assign obs_total_cache_hit_count = total_cache_hit_count;
    assign obs_total_cache_lookup_count = total_cache_lookup_count;
endmodule
"""
    return template.replace("__WAU_TOP_MODULE__", module_name)

def _render_host_mmio() -> str:
    """Memory-mapped host interface.

    Exposes a small register file over a synchronous valid/ready bus so external
    host software (or a board wrapper) can drive WAU like an Avalon-MM slave with
    32-bit registers. The register map is small and stable so host drivers and
    closed-loop benchmarking harnesses can target it across device wrappers.

    Register map (word-addressed):
      0x00  CTRL     [0]=reset_request (RW1S/auto-clear after one cycle)
                     [1]=enable_auto_adapt (RW)
      0x01  STATUS   [0]=host_in_ready
                     [1]=host_out_valid
                     [2]=output_pending
      0x02  FLOW_ID  RW (write-only takes effect at TRIGGER)
      0x03  IN_A     RW input operand A
      0x04  IN_B     RW input operand B
      0x05  TRIGGER  W1S - writing any value asserts host_in_valid for one accepted handshake
      0x10  OUT_FLOW R  last host_out_flow_id
      0x11  OUT_VAL  R  last host_out_value
      0x12  HOPS_LO  R  obs_total_hop_count
      0x13  STALLS   R  obs_total_stall_count
      0x14  FORWARDS R  obs_total_forward_count
      0x15  DELIVRD  R  obs_total_local_delivered_count
      0x16  CACHE_H  R  obs_total_cache_hit_count
      0x17  CACHE_L  R  obs_total_cache_lookup_count
    """
    return """`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_host_mmio #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter ADDR_WIDTH = 8
) (
    input wire clk,
    input wire rst_n,

    // Host (Avalon-MM-like) interface
    input wire mmio_read,
    input wire mmio_write,
    input wire [ADDR_WIDTH-1:0] mmio_address,
    input wire [31:0] mmio_writedata,
    output reg [31:0] mmio_readdata,
    output reg mmio_readdatavalid,

    // Soft reset request (asserted for one cycle after CTRL[0]=1)
    output reg soft_reset_req,
    output reg enable_auto_adapt,

    // Pipeline-side handshakes with wau_top
    output reg host_in_valid,
    input wire host_in_ready,
    output reg [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    output reg signed [DATA_WIDTH-1:0] host_in_a,
    output reg signed [DATA_WIDTH-1:0] host_in_b,

    input wire host_out_valid,
    output wire host_out_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_out_value,

    // Observability bus from wau_top (32-bit free-running counters)
    input wire [31:0] obs_total_hop_count,
    input wire [31:0] obs_total_stall_count,
    input wire [31:0] obs_total_forward_count,
    input wire [31:0] obs_total_local_delivered_count,
    input wire [31:0] obs_total_cache_hit_count,
    input wire [31:0] obs_total_cache_lookup_count
);
    localparam [ADDR_WIDTH-1:0] ADDR_CTRL     = 'h00;
    localparam [ADDR_WIDTH-1:0] ADDR_STATUS   = 'h01;
    localparam [ADDR_WIDTH-1:0] ADDR_FLOW_ID  = 'h02;
    localparam [ADDR_WIDTH-1:0] ADDR_IN_A     = 'h03;
    localparam [ADDR_WIDTH-1:0] ADDR_IN_B     = 'h04;
    localparam [ADDR_WIDTH-1:0] ADDR_TRIGGER  = 'h05;
    localparam [ADDR_WIDTH-1:0] ADDR_OUT_FLOW = 'h10;
    localparam [ADDR_WIDTH-1:0] ADDR_OUT_VAL  = 'h11;
    localparam [ADDR_WIDTH-1:0] ADDR_HOPS     = 'h12;
    localparam [ADDR_WIDTH-1:0] ADDR_STALLS   = 'h13;
    localparam [ADDR_WIDTH-1:0] ADDR_FORWARDS = 'h14;
    localparam [ADDR_WIDTH-1:0] ADDR_DELIVRD  = 'h15;
    localparam [ADDR_WIDTH-1:0] ADDR_CACHE_H  = 'h16;
    localparam [ADDR_WIDTH-1:0] ADDR_CACHE_L  = 'h17;

    reg [FLOW_ID_WIDTH-1:0] last_out_flow;
    reg signed [DATA_WIDTH-1:0] last_out_value;
    reg output_pending;

    // The bus is always ready to drain results into the latched registers.
    assign host_out_ready = 1'b1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mmio_readdata <= 32'd0;
            mmio_readdatavalid <= 1'b0;
            soft_reset_req <= 1'b0;
            enable_auto_adapt <= 1'b1;
            host_in_valid <= 1'b0;
            host_in_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            host_in_a <= {DATA_WIDTH{1'b0}};
            host_in_b <= {DATA_WIDTH{1'b0}};
            last_out_flow <= {FLOW_ID_WIDTH{1'b0}};
            last_out_value <= {DATA_WIDTH{1'b0}};
            output_pending <= 1'b0;
        end else begin
            mmio_readdatavalid <= 1'b0;
            soft_reset_req <= 1'b0;

            // Capture host_out_value/flow into latched status registers once the
            // pipeline produces a result; output_pending stays sticky until the
            // host explicitly reads OUT_VAL/OUT_FLOW or writes CTRL[0].
            if (host_out_valid && host_out_ready) begin
                last_out_flow <= host_out_flow_id;
                last_out_value <= host_out_value;
                output_pending <= 1'b1;
            end

            // Clear host_in_valid on accepted handshake.
            if (host_in_valid && host_in_ready) begin
                host_in_valid <= 1'b0;
            end

            if (mmio_write) begin
                case (mmio_address)
                    ADDR_CTRL: begin
                        soft_reset_req <= mmio_writedata[0];
                        enable_auto_adapt <= mmio_writedata[1];
                        if (mmio_writedata[0]) output_pending <= 1'b0;
                    end
                    ADDR_FLOW_ID: host_in_flow_id <= mmio_writedata[FLOW_ID_WIDTH-1:0];
                    ADDR_IN_A: host_in_a <= mmio_writedata[DATA_WIDTH-1:0];
                    ADDR_IN_B: host_in_b <= mmio_writedata[DATA_WIDTH-1:0];
                    ADDR_TRIGGER: host_in_valid <= 1'b1;
                    default: ;
                endcase
            end

            if (mmio_read) begin
                mmio_readdatavalid <= 1'b1;
                case (mmio_address)
                    ADDR_CTRL: mmio_readdata <= {30'd0, enable_auto_adapt, 1'b0};
                    ADDR_STATUS: mmio_readdata <= {29'd0, output_pending, host_out_valid, host_in_ready};
                    ADDR_FLOW_ID: mmio_readdata <= {{(32-FLOW_ID_WIDTH){1'b0}}, host_in_flow_id};
                    ADDR_IN_A: mmio_readdata <= host_in_a;
                    ADDR_IN_B: mmio_readdata <= host_in_b;
                    ADDR_OUT_FLOW: begin
                        mmio_readdata <= {{(32-FLOW_ID_WIDTH){1'b0}}, last_out_flow};
                        output_pending <= 1'b0;
                    end
                    ADDR_OUT_VAL: begin
                        mmio_readdata <= last_out_value;
                        output_pending <= 1'b0;
                    end
                    ADDR_HOPS: mmio_readdata <= obs_total_hop_count;
                    ADDR_STALLS: mmio_readdata <= obs_total_stall_count;
                    ADDR_FORWARDS: mmio_readdata <= obs_total_forward_count;
                    ADDR_DELIVRD: mmio_readdata <= obs_total_local_delivered_count;
                    ADDR_CACHE_H: mmio_readdata <= obs_total_cache_hit_count;
                    ADDR_CACHE_L: mmio_readdata <= obs_total_cache_lookup_count;
                    default: mmio_readdata <= 32'd0;
                endcase
            end
        end
    end
endmodule
"""


def _render_de0_nano_wrapper(project: CompiledProject) -> str:
    module_name = project.config.output_module_name
    template = """`timescale 1ns/1ps
`include "wau_defs.vh"

// DE0-NANO board wrapper.
//
// Pin-level I/O is exposed via the wau_host_mmio register file so:
//   * external host software / NIOS-II / Avalon-MM can drive WAU through
//     mmio_address/read/write/readdata signals,
//   * on-board push-buttons + switches still trigger the legacy demo flow
//     by emulating MMIO writes (a tiny ROM sequencer drives the bus when
//     KEY[1] is pressed).
//
// LEDs surface live status + observability without needing a JTAG host:
//   LED[0] host_out_valid
//   LED[1] host_in_ready
//   LED[2] output_pending (sticky until host reads OUT_VAL)
//   LED[3] enable_auto_adapt
//   LED[7:4] low nibble of obs_total_hop_count (handy as a coarse traffic LED).
module wau_de0_nano_top (
    input wire CLOCK_50,
    input wire [1:0] KEY,
    input wire [3:0] SW,
    output wire [7:0] LED,

    // Optional external MMIO bus. Tie unused inputs to 0 in board pin assignments.
    input wire ext_mmio_read,
    input wire ext_mmio_write,
    input wire [7:0] ext_mmio_address,
    input wire [31:0] ext_mmio_writedata,
    output wire [31:0] ext_mmio_readdata,
    output wire ext_mmio_readdatavalid
);
    localparam DATA_WIDTH = `WAU_DATA_WIDTH;
    localparam FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH;

    wire core_rst_n = KEY[0];
    wire soft_reset_req;
    wire enable_auto_adapt;

    wire host_in_valid;
    wire host_in_ready;
    wire [FLOW_ID_WIDTH-1:0] host_in_flow_id;
    wire signed [DATA_WIDTH-1:0] host_in_a;
    wire signed [DATA_WIDTH-1:0] host_in_b;

    wire host_out_valid;
    wire host_out_ready;
    wire [FLOW_ID_WIDTH-1:0] host_out_flow_id;
    wire signed [DATA_WIDTH-1:0] host_out_value;

    wire [31:0] obs_total_hop_count;
    wire [31:0] obs_total_stall_count;
    wire [31:0] obs_total_forward_count;
    wire [31:0] obs_total_local_delivered_count;
    wire [31:0] obs_total_cache_hit_count;
    wire [31:0] obs_total_cache_lookup_count;

    // Button-driven MMIO emulator: edge-detect KEY[1] (low-active) and
    // sequence FLOW_ID/IN_A/IN_B/TRIGGER writes. SW[3:0] supplies the data nibble.
    reg key1_d;
    reg [2:0] seq_state;
    reg do_button_write;
    reg [7:0] button_addr;
    reg [31:0] button_data;

    wire trigger_edge = key1_d && !KEY[1];

    always @(posedge CLOCK_50 or negedge core_rst_n) begin
        if (!core_rst_n) begin
            key1_d <= 1'b1;
            seq_state <= 3'd0;
            do_button_write <= 1'b0;
            button_addr <= 8'd0;
            button_data <= 32'd0;
        end else begin
            key1_d <= KEY[1];
            do_button_write <= 1'b0;

            if (trigger_edge) begin
                seq_state <= 3'd1;
            end

            case (seq_state)
                3'd1: begin
                    button_addr <= 8'h02;  // FLOW_ID
                    button_data <= {{(32-FLOW_ID_WIDTH){1'b0}}, SW[3:0], {{(FLOW_ID_WIDTH-4){1'b0}}}};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd2;
                end
                3'd2: begin
                    button_addr <= 8'h03;  // IN_A
                    button_data <= {{(32-DATA_WIDTH){1'b0}}, {(DATA_WIDTH-4){1'b0}}, SW[3:0]};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd3;
                end
                3'd3: begin
                    button_addr <= 8'h04;  // IN_B
                    button_data <= {{(32-DATA_WIDTH){1'b0}}, {(DATA_WIDTH-2){1'b0}}, 2'd3};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd4;
                end
                3'd4: begin
                    button_addr <= 8'h05;  // TRIGGER
                    button_data <= 32'd1;
                    do_button_write <= 1'b1;
                    seq_state <= 3'd0;
                end
                default: seq_state <= 3'd0;
            endcase
        end
    end

    wire bus_write = ext_mmio_write || do_button_write;
    wire bus_read = ext_mmio_read;
    wire [7:0] bus_addr = do_button_write ? button_addr : ext_mmio_address;
    wire [31:0] bus_writedata = do_button_write ? button_data : ext_mmio_writedata;

    wire [31:0] mmio_readdata;
    wire mmio_readdatavalid;
    assign ext_mmio_readdata = mmio_readdata;
    assign ext_mmio_readdatavalid = mmio_readdatavalid;

    wau_host_mmio #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .ADDR_WIDTH(8)
    ) mmio_u (
        .clk(CLOCK_50),
        .rst_n(core_rst_n),
        .mmio_read(bus_read),
        .mmio_write(bus_write),
        .mmio_address(bus_addr),
        .mmio_writedata(bus_writedata),
        .mmio_readdata(mmio_readdata),
        .mmio_readdatavalid(mmio_readdatavalid),
        .soft_reset_req(soft_reset_req),
        .enable_auto_adapt(enable_auto_adapt),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(host_out_ready),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .obs_total_hop_count(obs_total_hop_count),
        .obs_total_stall_count(obs_total_stall_count),
        .obs_total_forward_count(obs_total_forward_count),
        .obs_total_local_delivered_count(obs_total_local_delivered_count),
        .obs_total_cache_hit_count(obs_total_cache_hit_count),
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count)
    );

    assign LED[0] = host_out_valid;
    assign LED[1] = host_in_ready;
    assign LED[2] = mmio_readdatavalid;
    assign LED[3] = enable_auto_adapt;
    assign LED[7:4] = obs_total_hop_count[3:0];

    __WAU_TOP_MODULE__ wau_u (
        .clk(CLOCK_50),
        .rst_n(core_rst_n & ~soft_reset_req),
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
        .obs_total_hop_count(obs_total_hop_count),
        .obs_total_stall_count(obs_total_stall_count),
        .obs_total_forward_count(obs_total_forward_count),
        .obs_total_local_delivered_count(obs_total_local_delivered_count),
        .obs_total_cache_hit_count(obs_total_cache_hit_count),
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count)
    );
endmodule
"""
    return template.replace("__WAU_TOP_MODULE__", module_name)


def _render_program_json(project: CompiledProject) -> dict:
    flow_to_slot = {flow.flow_id: flow.flow_slot for flow in project.flows}

    return {
        "project": project.config.project_name,
        "abstraction": {
            "language": project.config.abstraction_language,
            "version": project.config.abstraction_version,
        },
        "device": {
            "name": project.config.device.name,
            "vendor": project.config.device.vendor,
            "family": project.config.device.family,
            "part": project.config.device.part,
            "grid": {"x": project.config.device.grid_x, "y": project.config.device.grid_y},
            "coordinator_mode": project.config.device.coordinator_mode,
            "supported_data_types": list(project.config.device.supported_data_types),
        },
        "compiler": {
            "routing": project.config.compiler.routing,
            "allow_adaptive_reroute": project.config.compiler.allow_adaptive_reroute,
            "fallback_radius": project.config.compiler.fallback_radius,
            "allow_cycle_recurrence": project.config.compiler.allow_cycle_recurrence,
            "core_capabilities": [
                {
                    "core": {"x": cap.core.x, "y": cap.core.y},
                    "operations": list(cap.operations),
                    "data_types": list(cap.data_types),
                }
                for cap in project.config.compiler.core_capabilities
            ],
        },
        "flows": [
            {
                "flow_id": flow.flow_id,
                "flow_slot": flow.flow_slot,
                "name": flow.name,
                "linear_node_order": list(flow.linear_node_order),
                "stages": [
                    {
                        "stage_index": stage.stage_index,
                        "op": stage.op_name,
                        "opcode": stage.opcode,
                        "latency": stage.latency,
                        "pipelined": stage.pipelined,
                        "primary_core": {
                            "x": stage.primary_core.x,
                            "y": stage.primary_core.y,
                            "index": core_index(
                                stage.primary_core.x,
                                stage.primary_core.y,
                                project.config.device.grid_x,
                            ),
                        },
                        "fallback_core": (
                            {
                                "x": stage.fallback_core.x,
                                "y": stage.fallback_core.y,
                                "index": core_index(
                                    stage.fallback_core.x,
                                    stage.fallback_core.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            if stage.fallback_core is not None
                            else None
                        ),
                        "immediate_b": stage.immediate_b,
                        "dtype": stage.dtype,
                    }
                    for stage in flow.stages
                ],
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "node_slot": node.node_slot,
                        "deps": list(node.deps),
                        "dep_slots": list(node.dep_slots),
                        "op": node.op_name,
                        "opcode": node.opcode,
                        "latency": node.latency,
                        "pipelined": node.pipelined,
                        "primary_core": {
                            "x": node.primary_core.x,
                            "y": node.primary_core.y,
                            "index": core_index(
                                node.primary_core.x,
                                node.primary_core.y,
                                project.config.device.grid_x,
                            ),
                        },
                        "candidate_cores": [
                            {
                                "x": coord.x,
                                "y": coord.y,
                                "index": core_index(
                                    coord.x,
                                    coord.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            for coord in node.candidate_cores
                        ],
                        "fallback_core": (
                            {
                                "x": node.fallback_core.x,
                                "y": node.fallback_core.y,
                                "index": core_index(
                                    node.fallback_core.x,
                                    node.fallback_core.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            if node.fallback_core is not None
                            else None
                        ),
                        "allow_adaptive": node.allow_adaptive,
                        "immediate_b": node.immediate_b,
                        "dtype": node.dtype,
                        "recurrent": node.recurrent,
                        "max_iterations": node.max_iterations,
                        "cycle_group": node.cycle_group,
                    }
                    for node in flow.nodes
                ],
            }
            for flow in project.flows
        ],
        "programs": [
            {
                "program_id": program.program_id,
                "name": program.name,
                "priority": program.priority,
                "replicas": program.replicas,
                "max_parallel_flows": program.max_parallel_flows,
                "load_balance": program.load_balance,
                "allow_async": program.allow_async,
                "allow_out_of_order": program.allow_out_of_order,
                "flow_ids": list(program.flow_ids),
                "flow_slots": [flow_to_slot[flow_id] for flow_id in program.flow_ids if flow_id in flow_to_slot],
            }
            for program in project.config.programs
        ],
    }


def emit_verilog(project: CompiledProject, schedule: SchedulePlan, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    module_name = project.config.output_module_name

    outputs: dict[str, str] = {
        "wau_defs.vh": _render_defs(project),
        "wau_operation_alu.v": _render_operation_alu(project),
        "wau_neighbor_forward.v": _render_neighbor_forward(),
        "wau_highway_router.v": _render_highway_router(),
        "wau_highway_mesh.v": _render_highway_mesh(),
        "wau_core_station.v": _render_core_station(project),
        "wau_core.v": _render_core(),
        "wau_coordinator.v": _render_coordinator(project),
        "wau_host_mmio.v": _render_host_mmio(),
        f"{module_name}.v": _render_top(project),
    }

    if project.config.device.name.lower().startswith("intel_de0_nano"):
        outputs["wau_de0_nano_top.v"] = _render_de0_nano_wrapper(project)

    written_paths: list[Path] = []
    for name, content in outputs.items():
        path = out_dir / name
        if path.suffix in {".v", ".vh"}:
            content = _with_verilog_license_header(content)
        path.write_text(content)
        written_paths.append(path)

    program_path = out_dir / "wau_program.json"
    program_path.write_text(json.dumps(_render_program_json(project), indent=2))
    written_paths.append(program_path)

    schedule_json_path = out_dir / "wau_schedule.json"
    schedule_json_path.write_text(json.dumps(schedule.to_json(), indent=2))
    written_paths.append(schedule_json_path)

    schedule_hex_path = out_dir / "wau_schedule.hex"
    schedule_hex_path.write_text("\n".join(schedule.to_hex_lines()) + "\n")
    written_paths.append(schedule_hex_path)

    return written_paths
