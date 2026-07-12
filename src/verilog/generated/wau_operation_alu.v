// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_operation_alu #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter integer CORE_INDEX = -1
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
      `WAU_OPCODE_ADD: begin
          if ((CORE_INDEX < 0) || (CORE_INDEX == 0) || (CORE_INDEX == 3) || (CORE_INDEX == 4)) result_comb = a + b;
      end
      `WAU_OPCODE_SUB: begin
          if ((CORE_INDEX < 0) || (CORE_INDEX == 0) || (CORE_INDEX == 2) || (CORE_INDEX == 3) || (CORE_INDEX == 4)) result_comb = a - b;
      end
      `WAU_OPCODE_MUL: begin
          if ((CORE_INDEX < 0) || (CORE_INDEX == 1) || (CORE_INDEX == 3) || (CORE_INDEX == 4)) result_comb = a * b;
      end
      `WAU_OPCODE_DIV: begin
          if ((CORE_INDEX < 0) || (CORE_INDEX == 1) || (CORE_INDEX == 2) || (CORE_INDEX == 3) || (CORE_INDEX == 4)) result_comb = (b != 0) ? (a / b) : {DATA_WIDTH{1'b0}};
      end
      `WAU_OPCODE_MAX: begin
          if ((CORE_INDEX < 0) || (CORE_INDEX == 2) || (CORE_INDEX == 3) || (CORE_INDEX == 4) || (CORE_INDEX == 5)) result_comb = (a > b) ? a : b;
      end
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
