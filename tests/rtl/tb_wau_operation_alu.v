`timescale 1ns/1ps
`include "wau_defs.vh"

module tb_wau_operation_alu;
    reg clk;
    reg rst_n;
    reg in_valid;
    reg [`WAU_OPCODE_WIDTH-1:0] opcode;
    reg signed [`WAU_DATA_WIDTH-1:0] a;
    reg signed [`WAU_DATA_WIDTH-1:0] b;
    wire out_valid;
    wire signed [`WAU_DATA_WIDTH-1:0] y;

    wau_operation_alu dut (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(in_valid),
        .opcode(opcode),
        .a(a),
        .b(b),
        .out_valid(out_valid),
        .y(y)
    );

    always #5 clk = ~clk;

    task automatic apply_case;
        input [`WAU_OPCODE_WIDTH-1:0] case_opcode;
        input signed [`WAU_DATA_WIDTH-1:0] case_a;
        input signed [`WAU_DATA_WIDTH-1:0] case_b;
        input signed [`WAU_DATA_WIDTH-1:0] expected;
        integer timeout;
        integer matched;
        begin
            matched = 0;
            @(negedge clk);
            opcode = case_opcode;
            a = case_a;
            b = case_b;
            in_valid = 1'b1;

            @(negedge clk);
            in_valid = 1'b0;

            for (timeout = 0; timeout < 8; timeout = timeout + 1) begin
                @(posedge clk);
                if (out_valid) begin
                    if (y !== expected) begin
                        $display("FAIL: opcode=%0d a=%0d b=%0d expected=%0d got=%0d", case_opcode, case_a, case_b, expected, y);
                        $fatal(1);
                    end
                    matched = 1;
                    timeout = 8;
                end
            end

            if (!matched) begin
                $display("FAIL: timeout waiting out_valid for opcode=%0d", case_opcode);
                $fatal(1);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        in_valid = 1'b0;
        opcode = {`WAU_OPCODE_WIDTH{1'b0}};
        a = {`WAU_DATA_WIDTH{1'b0}};
        b = {`WAU_DATA_WIDTH{1'b0}};

        repeat (3) @(posedge clk);
        rst_n = 1'b1;

        apply_case(`WAU_OPCODE_ADD, 32'sd7, 32'sd5, 32'sd12);
        apply_case(`WAU_OPCODE_SUB, 32'sd7, 32'sd5, 32'sd2);
        apply_case(`WAU_OPCODE_MUL, -32'sd3, 32'sd4, -32'sd12);
        apply_case(`WAU_OPCODE_DIV, 32'sd9, 32'sd3, 32'sd3);
        apply_case(`WAU_OPCODE_DIV, 32'sd9, 32'sd0, 32'sd0);
        apply_case(`WAU_OPCODE_MAX, -32'sd2, 32'sd5, 32'sd5);

        $display("PASS: tb_wau_operation_alu");
        $finish;
    end
endmodule
