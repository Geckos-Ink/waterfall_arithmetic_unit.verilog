`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core_station #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
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

    reg cache_valid;
    reg [OPCODE_WIDTH-1:0] last_opcode;
    reg signed [DATA_WIDTH-1:0] last_a;
    reg signed [DATA_WIDTH-1:0] last_b;

    reg alu_in_valid;
    wire alu_out_valid;
    wire signed [DATA_WIDTH-1:0] alu_out_value;

    reg result_latched_valid;
    reg signed [DATA_WIDTH-1:0] result_latched_value;

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
            cache_valid <= 1'b0;
            last_opcode <= {OPCODE_WIDTH{1'b0}};
            last_a <= {DATA_WIDTH{1'b0}};
            last_b <= {DATA_WIDTH{1'b0}};
            cache_hit <= 1'b0;
            alu_in_valid <= 1'b0;
            result_latched_valid <= 1'b0;
            result_latched_value <= {DATA_WIDTH{1'b0}};
        end else begin
            alu_in_valid <= 1'b0;

            if (out_valid && out_ready) begin
                out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    result_latched_valid <= 1'b0;
                    if (in_valid && in_ready) begin
                        active_flow_id <= in_flow_id;
                        active_opcode <= in_opcode;
                        active_stage_id <= in_stage_id;
                        op_a <= in_a;
                        op_b <= effective_b;
                        wait_cycles <= op_latency(in_opcode) - 8'd1;

                        cache_hit <= cache_valid &&
                                     (last_opcode == in_opcode) &&
                                     (last_a == in_a) &&
                                     (last_b == effective_b);
                        cache_valid <= 1'b1;
                        last_opcode <= in_opcode;
                        last_a <= in_a;
                        last_b <= effective_b;

                        alu_in_valid <= 1'b1;
                        state <= ST_EXEC;
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
