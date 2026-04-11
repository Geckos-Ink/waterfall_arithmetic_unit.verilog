`timescale 1ns/1ps
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
        result_comb = {DATA_WIDTH{1'b0}};
        case (opcode)
      `WAU_OPCODE_ADD: result_comb = a + b;
      `WAU_OPCODE_SUB: result_comb = a - b;
      `WAU_OPCODE_MUL: result_comb = a * b;
      `WAU_OPCODE_DIV: result_comb = (b != 0) ? (a / b) : {DATA_WIDTH{1'b0}};
      `WAU_OPCODE_MAX: result_comb = (a > b) ? a : b;
            default: result_comb = {DATA_WIDTH{1'b0}};
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            y <= {DATA_WIDTH{1'b0}};
        end else begin
            out_valid <= in_valid;
            y <= result_comb;
        end
    end
endmodule
