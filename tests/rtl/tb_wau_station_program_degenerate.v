`timescale 1ns/1ps

// Safety proof for the per-core fast-path table: with
// compiler.station_program.enabled=false, wau_core_station's fast-path
// lookup degenerates to an always-miss function (see
// _core_fast_path_lookup_function), so every stage keeps round-tripping
// wau_coordinator exactly as it did before this feature existed.
//
// Generated from src/python/configs/wau_station_program_demo_disabled.json --
// the exact same device/flows/placement as
// wau_station_program_demo.json (see tb_wau_core_fast_path_overlap.v), only
// station_program.enabled flipped to false. Running the identical scenario
// (same two flows, same operands, same injection order) must produce the
// identical values and the identical (slower, legacy) cycle count: 17, not
// the 15 the enabled twin achieves. That two-cycle gap is the whole
// contribution of the fast path made visible by this pair of testbenches.
module tb_wau_station_program_degenerate;
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

    integer cyc;
    reg signed [31:0] out_flow1;
    reg signed [31:0] out_flow3;
    reg got_flow1;
    reg got_flow3;

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
        if (rst_n) begin
            cyc = cyc + 1;
            if (host_out_valid && host_out_ready) begin
                if (host_out_flow_id == 12'd1) begin
                    out_flow1 = host_out_value;
                    got_flow1 = 1'b1;
                end else if (host_out_flow_id == 12'd3) begin
                    out_flow3 = host_out_value;
                    got_flow3 = 1'b1;
                end
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
        cyc = 0;
        got_flow1 = 1'b0;
        got_flow3 = 1'b0;
        out_flow1 = 32'sd0;
        out_flow3 = 32'sd0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        inject(12'd1, 32'sd10, 32'sd4);
        inject(12'd3, 32'sd6, 32'sd2);

        for (guard = 0; guard < 400; guard = guard + 1) begin
            @(posedge clk);
            if (got_flow1 && got_flow3) begin
                guard = 400;
            end
        end

        if (!got_flow1 || !got_flow3) begin
            $display("FAIL: missing output(s) flow1=%0b flow3=%0b", got_flow1, got_flow3);
            $fatal(1);
        end
        if (out_flow1 !== 32'sd38) begin
            $display("FAIL: flow1 expected 38 got %0d", out_flow1);
            $fatal(1);
        end
        if (out_flow3 !== 32'sd16) begin
            $display("FAIL: flow3 expected 16 got %0d", out_flow3);
            $fatal(1);
        end
        if (cyc !== 17) begin
            $display("FAIL: disabled fast path should reproduce the legacy 17-cycle timing exactly, got %0d", cyc);
            $fatal(1);
        end

        $display("PASS: tb_wau_station_program_degenerate (cycles=%0d, byte-identical to legacy timing)", cyc);
        $finish;
    end
endmodule
