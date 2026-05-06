// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
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
    localparam ST_IDLE = 2'd0;
    localparam ST_DISPATCH = 2'd1;
    localparam ST_WAIT_RESULT = 2'd2;

    reg [1:0] state;
    reg [7:0] current_flow_slot;
    reg [FLOW_ID_WIDTH-1:0] current_flow_id;
    reg [7:0] current_stage;
    reg [7:0] waiting_core;
    reg signed [DATA_WIDTH-1:0] accumulator;
    reg signed [DATA_WIDTH-1:0] operand_b;

    wire [7:0] stage_last;
    wire [OPCODE_WIDTH-1:0] stage_opcode;
    wire [7:0] stage_primary_core;
    wire [7:0] stage_fallback_core;
    wire stage_use_immediate;
    wire signed [DATA_WIDTH-1:0] stage_immediate_b;

    reg [7:0] chosen_core;

    assign host_in_ready = (state == ST_IDLE) && !host_out_valid;
    assign result_pkt_ready = (state == ST_WAIT_RESULT);

    function [7:0] flow_slot_from_id;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        reg [7:0] value;
        begin
            value = 8'hFF;
            case (flow_id)
                12'd1: value = 8'd0;
                12'd2: value = 8'd1;
                default: value = 8'hFF;
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
                8'd0: value = 8'd2;
                8'd1: value = 8'd1;
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
            value = {OPCODE_WIDTH{1'b0}};
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'h01;
                16'h0001: value = 8'h03;
                16'h0002: value = 8'h02;
                16'h0100: value = 8'h07;
                16'h0101: value = 8'h04;
                default: value = {OPCODE_WIDTH{1'b0}};
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
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'd0;
                16'h0001: value = 8'd1;
                16'h0002: value = 8'd2;
                16'h0100: value = 8'd5;
                16'h0101: value = 8'd2;
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
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'd3;
                16'h0001: value = 8'd4;
                16'h0002: value = 8'd2;
                16'h0100: value = 8'd2;
                16'h0101: value = 8'd1;
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
            case ({flow_slot, stage_idx})
                16'h0000: value = 1'b0;
                16'h0001: value = 1'b1;
                16'h0002: value = 1'b0;
                16'h0100: value = 1'b0;
                16'h0101: value = 1'b0;
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
            value = {DATA_WIDTH{1'b0}};
            case ({flow_slot, stage_idx})
                16'h0000: value = 32'sd0;
                16'h0001: value = 32'sd3;
                16'h0002: value = 32'sd0;
                16'h0100: value = 32'sd0;
                16'h0101: value = 32'sd0;
                default: value = {DATA_WIDTH{1'b0}};
            endcase
            flow_stage_immediate_b = value;
        end
    endfunction

    assign stage_last = flow_last_stage(current_flow_slot);
    assign stage_opcode = flow_stage_opcode(current_flow_slot, current_stage);
    assign stage_primary_core = flow_stage_primary_core(current_flow_slot, current_stage);
    assign stage_fallback_core = flow_stage_fallback_core(current_flow_slot, current_stage);
    assign stage_use_immediate = flow_stage_use_immediate(current_flow_slot, current_stage);
    assign stage_immediate_b = flow_stage_immediate_b(current_flow_slot, current_stage);

    always @(*) begin
        chosen_core = stage_primary_core;
        if (enable_auto_adapt &&
            (stage_fallback_core != stage_primary_core) &&
            core_busy[stage_primary_core] &&
            !core_busy[stage_fallback_core]) begin
            chosen_core = stage_fallback_core;
        end
    end

    always @(*) begin
        dispatch_pkt_valid = 1'b0;
        dispatch_pkt_dst_core = 8'd0;
        dispatch_pkt_flow_id = {FLOW_ID_WIDTH{1'b0}};
        dispatch_pkt_opcode = {OPCODE_WIDTH{1'b0}};
        dispatch_pkt_a = {DATA_WIDTH{1'b0}};
        dispatch_pkt_b = {DATA_WIDTH{1'b0}};
        dispatch_pkt_use_immediate = 1'b0;
        dispatch_pkt_immediate_b = {DATA_WIDTH{1'b0}};
        dispatch_pkt_stage_id = 8'd0;

        if (state == ST_DISPATCH && current_flow_slot != 8'hFF) begin
            dispatch_pkt_valid = 1'b1;
            dispatch_pkt_dst_core = chosen_core;
            dispatch_pkt_flow_id = current_flow_id;
            dispatch_pkt_opcode = stage_opcode;
            dispatch_pkt_a = accumulator;
            dispatch_pkt_b = operand_b;
            dispatch_pkt_use_immediate = stage_use_immediate;
            dispatch_pkt_immediate_b = stage_immediate_b;
            dispatch_pkt_stage_id = current_stage;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            current_flow_slot <= 8'hFF;
            current_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            current_stage <= 8'd0;
            waiting_core <= 8'd0;
            accumulator <= {DATA_WIDTH{1'b0}};
            operand_b <= {DATA_WIDTH{1'b0}};
            host_out_valid <= 1'b0;
            host_out_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            host_out_value <= {DATA_WIDTH{1'b0}};
        end else begin
            if (host_out_valid && host_out_ready) begin
                host_out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    if (host_in_valid && host_in_ready) begin
                        current_flow_slot <= flow_slot_from_id(host_in_flow_id);
                        current_flow_id <= host_in_flow_id;
                        current_stage <= 8'd0;
                        accumulator <= host_in_a;
                        operand_b <= host_in_b;

                        if (flow_slot_from_id(host_in_flow_id) != 8'hFF) begin
                            state <= ST_DISPATCH;
                        end
                    end
                end

                ST_DISPATCH: begin
                    if (current_flow_slot == 8'hFF) begin
                        state <= ST_IDLE;
                    end else if (dispatch_pkt_valid && dispatch_pkt_ready) begin
                        waiting_core <= chosen_core;
                        state <= ST_WAIT_RESULT;
                    end
                end

                ST_WAIT_RESULT: begin
                    if (result_pkt_valid &&
                        result_pkt_ready &&
                        (result_pkt_src_core == waiting_core) &&
                        (result_pkt_flow_id == current_flow_id) &&
                        (result_pkt_stage_id == current_stage)) begin
                        accumulator <= result_pkt_value;
                        if (current_stage >= stage_last) begin
                            host_out_valid <= 1'b1;
                            host_out_flow_id <= result_pkt_flow_id;
                            host_out_value <= result_pkt_value;
                            state <= ST_IDLE;
                        end else begin
                            current_stage <= current_stage + 8'd1;
                            state <= ST_DISPATCH;
                        end
                    end
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end
endmodule
