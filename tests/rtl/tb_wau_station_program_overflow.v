`timescale 1ns/1ps

// Overflow-safety proof for the per-core fast-path table. Generated from
// src/python/configs/wau_station_program_overflow_demo.json:
// compiler.station_program.table_bits=1 (capacity 2 entries/core), and
// three independent 2-stage flows (10, 11, 12) whose stage 0 all place on
// the SAME core (0,0). compiler.build_fast_path_tables can only fit 2 of
// those 3 distinct (flow_id, stage_index) keys in that core's table
// (deterministically, by sorted flow_id: 10 and 11 fit, 12 overflows -- see
// wau_program.json's station_program.cores[0].overflowed_keys). Flow 12's
// stage 0 -> stage 1 transition therefore has no fast-path entry at all and
// must keep using the legacy dynamic-coordinator path automatically -- this
// test's whole point is proving that "no entry" degrades to "still
// correct", never to "wedged" or "wrong value".
module tb_wau_station_program_overflow;
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

    reg signed [31:0] out_10;
    reg signed [31:0] out_11;
    reg signed [31:0] out_12;
    reg got_10;
    reg got_11;
    reg got_12;

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

    task automatic inject;
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

    always @(posedge clk) begin
        if (rst_n && host_out_valid && host_out_ready) begin
            if (host_out_flow_id == 12'd10) begin
                out_10 = host_out_value;
                got_10 = 1'b1;
            end else if (host_out_flow_id == 12'd11) begin
                out_11 = host_out_value;
                got_11 = 1'b1;
            end else if (host_out_flow_id == 12'd12) begin
                out_12 = host_out_value;
                got_12 = 1'b1;
            end
        end
    end

    integer guard;
    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        host_in_valid = 1'b0;
        host_in_flow_id = 12'd0;
        host_in_a = 32'sd0;
        host_in_b = 32'sd0;
        host_out_ready = 1'b1;
        enable_auto_adapt = 1'b1;
        got_10 = 1'b0;
        got_11 = 1'b0;
        got_12 = 1'b0;
        out_10 = 32'sd0;
        out_11 = 32'sd0;
        out_12 = 32'sd0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // All three: acc = a + b; acc = acc * immediate.
        // 10: (5+1)*2 = 12  (fast-path entry present)
        // 11: (5+1)*3 = 18  (fast-path entry present)
        // 12: (5+1)*4 = 24  (overflowed -- legacy path only)
        inject(12'd10, 32'sd5, 32'sd1);
        inject(12'd11, 32'sd5, 32'sd1);
        inject(12'd12, 32'sd5, 32'sd1);

        for (guard = 0; guard < 400; guard = guard + 1) begin
            @(posedge clk);
            if (got_10 && got_11 && got_12) begin
                guard = 400;
            end
        end

        if (!got_10 || !got_11 || !got_12) begin
            $display("FAIL: missing output(s) 10=%0b 11=%0b 12=%0b", got_10, got_11, got_12);
            $fatal(1);
        end
        if (out_10 !== 32'sd12) begin
            $display("FAIL: flow 10 expected 12 got %0d", out_10);
            $fatal(1);
        end
        if (out_11 !== 32'sd18) begin
            $display("FAIL: flow 11 expected 18 got %0d", out_11);
            $fatal(1);
        end
        if (out_12 !== 32'sd24) begin
            $display("FAIL: flow 12 (overflowed, legacy-path-only) expected 24 got %0d", out_12);
            $fatal(1);
        end

        $display("PASS: tb_wau_station_program_overflow (overflowed flow still correct)");
        $finish;
    end
endmodule
