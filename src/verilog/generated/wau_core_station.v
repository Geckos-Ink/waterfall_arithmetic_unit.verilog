// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core_station #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CACHE_ENTRIES = 4
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
    output reg cache_hit
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
    reg [7:0] cache_replace_ptr;

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

    wire signed [DATA_WIDTH-1:0] effective_b;
    assign effective_b = in_use_immediate ? in_immediate_b : in_b;

    assign in_ready = (state == ST_IDLE);
    assign busy = (state != ST_IDLE);

    function [7:0] op_latency;
        input [OPCODE_WIDTH-1:0] opcode;
        begin
            case (opcode)
                `WAU_OPCODE_ADD: op_latency = `WAU_LATENCY_ADD;
                `WAU_OPCODE_SUB: op_latency = `WAU_LATENCY_SUB;
                `WAU_OPCODE_MUL: op_latency = `WAU_LATENCY_MUL;
                `WAU_OPCODE_DIV: op_latency = `WAU_LATENCY_DIV;
                `WAU_OPCODE_MAX: op_latency = `WAU_LATENCY_MAX;
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
        cache_hit_value = {DATA_WIDTH{1'b0}};
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

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            out_valid <= 1'b0;
            out_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            out_stage_id <= 8'd0;
            out_value <= {DATA_WIDTH{1'b0}};
            active_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            active_opcode <= {OPCODE_WIDTH{1'b0}};
            active_stage_id <= 8'd0;
            op_a <= {DATA_WIDTH{1'b0}};
            op_b <= {DATA_WIDTH{1'b0}};
            wait_cycles <= 8'd0;
            cache_replace_ptr <= 8'd0;
            for (cache_idx = 0; cache_idx < CACHE_ENTRIES; cache_idx = cache_idx + 1) begin
                cache_valid[cache_idx] <= 1'b0;
                cache_opcode[cache_idx] <= {OPCODE_WIDTH{1'b0}};
                cache_a[cache_idx] <= {DATA_WIDTH{1'b0}};
                cache_b[cache_idx] <= {DATA_WIDTH{1'b0}};
                cache_value[cache_idx] <= {DATA_WIDTH{1'b0}};
            end
            cache_hit <= 1'b0;
            alu_in_valid <= 1'b0;
            result_latched_valid <= 1'b0;
            result_latched_value <= {DATA_WIDTH{1'b0}};
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
                        if (cache_hit_comb) begin
                            out_valid <= 1'b1;
                            out_flow_id <= in_flow_id;
                            out_stage_id <= in_stage_id;
                            out_value <= cache_hit_value;
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

                        cache_valid[cache_replace_ptr] <= 1'b1;
                        cache_opcode[cache_replace_ptr] <= active_opcode;
                        cache_a[cache_replace_ptr] <= op_a;
                        cache_b[cache_replace_ptr] <= op_b;
                        cache_value[cache_replace_ptr] <= alu_out_valid ? alu_out_value : result_latched_value;

                        if (cache_replace_ptr == (CACHE_ENTRIES - 1)) begin
                            cache_replace_ptr <= 8'd0;
                        end else begin
                            cache_replace_ptr <= cache_replace_ptr + 8'd1;
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
