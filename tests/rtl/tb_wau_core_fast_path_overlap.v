`timescale 1ns/1ps

// Proves the per-core fast-path table (compiler.build_fast_path_tables,
// wau_core_station's self_* port) actually delivers what it exists to
// deliver: lower end-to-end latency than the legacy coordinator round-trip,
// by letting a flow's interior stages hand results directly core-to-core
// instead of bouncing back through wau_coordinator every time.
//
// Generated from src/python/configs/wau_station_program_demo.json
// (compiler.station_program.enabled=true), which places two independent
// 3-stage flows on two fully disjoint sets of cores:
//   flow 1 "accumulate_and_scale": ((a + b) * 3) - b   on cores (0,0)->(1,0)->(2,0)
//   flow 3 "disjoint_accumulate" : max((a + b) * 2, b) on cores (0,1)->(1,1)->(2,1)
// Both flows are injected back-to-back (as tb_wau_coordinator_multiissue
// already does for inter-flow concurrency), so this also re-confirms that
// invariant still holds with the fast path wired in, not just the new one.
//
// The cycle bound below is empirically measured against this exact config:
// with the fast path enabled both flows complete in 15 cycles; against the
// disabled twin (wau_station_program_demo_disabled.json, see
// tb_wau_station_program_degenerate.v) the identical scenario takes 17 --
// two cycles saved, one per flow, exactly the two interior stage
// transitions (stage0->stage1, stage1->stage2) that no longer round-trip
// the coordinator. A regression here (a slower fast path, or a fast path
// that silently stopped firing) would show up as this bound being missed.
//
// After that, flow 2 "max_then_div" is triggered on its own and checked too.
// Its stage 0 (core (2,1), highway line y=1) has a fast-path entry whose
// destination (core (2,0), line y=0) is on a *different* highway line --
// under `lines` there is no hub-to-hub bridge (only hub-to-coordinator), so
// that packet is safely absorbed by the coordinator instead of ever reaching
// core (2,0)'s own station. The packet retains its transaction tag alongside
// flow_id/stage_id/value, so the coordinator's relaxed tagged matching (see
// _render_coordinator) treats it as an ordinary stage-0 completion and
// dispatches stage 1 normally -- correct, just without a speedup for that one
// hop. This is the same "no entry ->
// no risk, just no speedup" guarantee as a capacity overflow (see
// tb_wau_station_program_overflow.v), just reached a different way.
module tb_wau_core_fast_path_overlap;
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
    integer max_busy;
    integer busy_now;
    reg signed [31:0] out_flow1;
    reg signed [31:0] out_flow3;
    reg signed [31:0] out_flow2;
    reg got_flow1;
    reg got_flow3;
    reg got_flow2;

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
            busy_now = $countones(dut.core_busy);
            if (busy_now > max_busy) begin
                max_busy = busy_now;
            end
            if (host_out_valid && host_out_ready) begin
                if (host_out_flow_id == 12'd1) begin
                    out_flow1 = host_out_value;
                    got_flow1 = 1'b1;
                end else if (host_out_flow_id == 12'd3) begin
                    out_flow3 = host_out_value;
                    got_flow3 = 1'b1;
                end else if (host_out_flow_id == 12'd2) begin
                    out_flow2 = host_out_value;
                    got_flow2 = 1'b1;
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
        max_busy = 0;
        busy_now = 0;
        got_flow1 = 1'b0;
        got_flow3 = 1'b0;
        got_flow2 = 1'b0;
        out_flow1 = 32'sd0;
        out_flow3 = 32'sd0;
        out_flow2 = 32'sd0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // flow 1: ((a + b) * 3) - b   with a=10,b=4 -> 38
        // flow 3: max((a + b) * 2, b) with a=6, b=2 -> 16
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
        if (max_busy < 2) begin
            $display("FAIL: cores never ran concurrently (max simultaneous busy = %0d)", max_busy);
            $fatal(1);
        end
        if (cyc > 15) begin
            $display("FAIL: fast path did not speed up completion (took %0d cycles, expected <= 15)", cyc);
            $fatal(1);
        end

        // flow 2: max(a,b) then div by b_reg, with a=9,b=3 -> max=9, 9/3=3.
        // Crosses highway lines (see the file header) -- correctness must
        // survive even though this specific hop cannot get a speedup.
        inject(12'd2, 32'sd9, 32'sd3);
        for (guard = 0; guard < 400; guard = guard + 1) begin
            @(posedge clk);
            if (got_flow2) begin
                guard = 400;
            end
        end
        if (!got_flow2) begin
            $display("FAIL: missing output for cross-line flow 2");
            $fatal(1);
        end
        if (out_flow2 !== 32'sd3) begin
            $display("FAIL: flow2 (cross-line hop) expected 3 got %0d", out_flow2);
            $fatal(1);
        end

        $display("PASS: tb_wau_core_fast_path_overlap (cycles=%0d, max simultaneous busy cores = %0d)", cyc, max_busy);
        $finish;
    end
endmodule
