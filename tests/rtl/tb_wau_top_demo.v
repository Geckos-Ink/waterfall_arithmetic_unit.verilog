`timescale 1ns/1ps

module tb_wau_top_demo;
    reg clk;
    reg rst_n;

    reg host_in_valid;
    wire host_in_ready;
    reg [11:0] host_in_flow_id;
    reg signed [31:0] host_in_a;
    reg signed [31:0] host_in_b;

    wire host_out_valid;
    reg host_out_ready;
    wire [11:0] host_out_flow_id;
    wire signed [31:0] host_out_value;

    reg enable_auto_adapt;

    wau_top dut (
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
        .enable_auto_adapt(enable_auto_adapt)
    );

    always #5 clk = ~clk;

    task automatic send_packet;
        input [11:0] flow_id;
        input signed [31:0] a;
        input signed [31:0] b;
        begin
            @(negedge clk);
            host_in_flow_id = flow_id;
            host_in_a = a;
            host_in_b = b;
            host_in_valid = 1'b1;

            while (!host_in_ready) begin
                @(posedge clk);
            end

            @(negedge clk);
            host_in_valid = 1'b0;
        end
    endtask

    task automatic expect_packet;
        input [11:0] expected_flow;
        input signed [31:0] expected_value;
        integer timeout;
        integer matched;
        begin
            matched = 0;
            for (timeout = 0; timeout < 300; timeout = timeout + 1) begin
                @(posedge clk);
                if (host_out_valid) begin
                    if (host_out_flow_id !== expected_flow) begin
                        $display("FAIL: expected flow=%0d got flow=%0d", expected_flow, host_out_flow_id);
                        $fatal(1);
                    end
                    if (host_out_value !== expected_value) begin
                        $display("FAIL: flow=%0d expected value=%0d got=%0d", expected_flow, expected_value, host_out_value);
                        $fatal(1);
                    end
                    matched = 1;
                    timeout = 300;
                end
            end
            if (!matched) begin
                $display("FAIL: timeout waiting output flow=%0d", expected_flow);
                $fatal(1);
            end
        end
    endtask

    task automatic expect_no_output;
        input integer cycles;
        integer idx;
        begin
            for (idx = 0; idx < cycles; idx = idx + 1) begin
                @(posedge clk);
                if (host_out_valid) begin
                    $display("FAIL: unexpected output flow=%0d value=%0d", host_out_flow_id, host_out_value);
                    $fatal(1);
                end
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        host_in_valid = 1'b0;
        host_in_flow_id = 12'd0;
        host_in_a = 32'sd0;
        host_in_b = 32'sd0;
        host_out_ready = 1'b1;
        enable_auto_adapt = 1'b1;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // flow 1: ((a + b) * 3) - b
        send_packet(12'd1, 32'sd10, 32'sd4);
        expect_packet(12'd1, 32'sd38);

        send_packet(12'd1, -32'sd7, 32'sd5);
        expect_packet(12'd1, -32'sd11);

        // flow 2: max(a,b) / b (division-by-zero => 0)
        send_packet(12'd2, 32'sd9, 32'sd3);
        expect_packet(12'd2, 32'sd3);

        send_packet(12'd2, 32'sd2, 32'sd8);
        expect_packet(12'd2, 32'sd1);

        send_packet(12'd2, 32'sd5, 32'sd0);
        expect_packet(12'd2, 32'sd0);

        // unknown flow id: ignored by coordinator
        send_packet(12'd99, 32'sd1, 32'sd1);
        expect_no_output(20);

        $display("PASS: tb_wau_top_demo");
        $finish;
    end
endmodule
