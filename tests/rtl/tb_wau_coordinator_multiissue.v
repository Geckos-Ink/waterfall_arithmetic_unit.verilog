`timescale 1ns/1ps

// Directed test for the tagged multi-issue coordinator: inject two copies of
// the *same* flow plus an independent flow back-to-back (without waiting for
// results) and prove that
//   (a) all transactions produce correct results,
//   (b) same-flow reuse is accepted before the first copy retires, and
//   (c) the parallel core mesh is actually used at runtime -- i.e. at least
//       two cores are busy in the same cycle, which the legacy strictly-serial
//       coordinator could never do.
module tb_wau_coordinator_multiissue;
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

    integer max_busy;
    integer busy_now;
    integer outputs_seen;
    reg signed [31:0] out_flow1_a;
    reg signed [31:0] out_flow1_b;
    reg signed [31:0] out_flow2;
    reg got_flow1_a;
    reg got_flow1_b;
    reg got_flow2;
    reg same_flow_overlapped;
    reg output_order_ok;

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

    // Inject a packet, returning as soon as it is accepted (does NOT wait for
    // the result), so multiple flows can be put in flight together.
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

    // Concurrency + output monitor.
    always @(posedge clk) begin
        if (rst_n) begin
            busy_now = $countones(dut.core_busy);
            if (busy_now > max_busy) begin
                max_busy = busy_now;
            end
            if (host_out_valid && host_out_ready) begin
                outputs_seen = outputs_seen + 1;
                if ((outputs_seen == 1) &&
                    !((host_out_flow_id == 12'd1) && (host_out_value == 32'sd38))) begin
                    output_order_ok = 1'b0;
                end else if ((outputs_seen == 2) &&
                    !((host_out_flow_id == 12'd1) && (host_out_value == 32'sd25))) begin
                    output_order_ok = 1'b0;
                end else if ((outputs_seen == 3) &&
                    !((host_out_flow_id == 12'd2) && (host_out_value == 32'sd3))) begin
                    output_order_ok = 1'b0;
                end
                if (host_out_flow_id == 12'd1) begin
                    if (host_out_value == 32'sd38) begin
                        out_flow1_a = host_out_value;
                        got_flow1_a = 1'b1;
                    end else if (host_out_value == 32'sd25) begin
                        out_flow1_b = host_out_value;
                        got_flow1_b = 1'b1;
                    end
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
        max_busy = 0;
        busy_now = 0;
        outputs_seen = 0;
        got_flow1_a = 1'b0;
        got_flow1_b = 1'b0;
        got_flow2 = 1'b0;
        same_flow_overlapped = 1'b0;
        output_order_ok = 1'b1;
        out_flow1_a = 32'sd0;
        out_flow1_b = 32'sd0;
        out_flow2 = 32'sd0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // Same flow id twice: the internal packet tag, not flow id, must keep
        // their accumulator contexts and results separate.
        // flow 1: ((a + b) * 3) - b
        //   a=10,b=4 -> 38; a=7,b=2 -> 25
        // flow 2: max(a,b) / b       with a=9 ,b=3 -> 3
        inject(12'd1, 32'sd10, 32'sd4);
        inject(12'd1, 32'sd7, 32'sd2);
        same_flow_overlapped = (outputs_seen == 0);
        inject(12'd2, 32'sd9, 32'sd3);

        // Wait for all three results.
        for (guard = 0; guard < 400; guard = guard + 1) begin
            @(posedge clk);
            if (got_flow1_a && got_flow1_b && got_flow2) begin
                guard = 400;
            end
        end

        if (!same_flow_overlapped) begin
            $display("FAIL: second same-flow transaction was not admitted before the first retired");
            $fatal(1);
        end
        if (!output_order_ok) begin
            $display("FAIL: tagged transactions did not retire in host acceptance order");
            $fatal(1);
        end
        if (!got_flow1_a || !got_flow1_b || !got_flow2) begin
            $display("FAIL: missing output(s) flow1a=%0b flow1b=%0b flow2=%0b",
                got_flow1_a, got_flow1_b, got_flow2);
            $fatal(1);
        end
        if ((out_flow1_a !== 32'sd38) || (out_flow1_b !== 32'sd25)) begin
            $display("FAIL: same-flow outputs expected 38/25 got %0d/%0d", out_flow1_a, out_flow1_b);
            $fatal(1);
        end
        if (out_flow2 !== 32'sd3) begin
            $display("FAIL: flow2 expected 3 got %0d", out_flow2);
            $fatal(1);
        end
        if (max_busy < 2) begin
            $display("FAIL: cores never ran concurrently (max simultaneous busy = %0d); coordinator is still serial", max_busy);
            $fatal(1);
        end

        $display("PASS: tb_wau_coordinator_multiissue (max simultaneous busy cores = %0d)", max_busy);
        $finish;
    end
endmodule
