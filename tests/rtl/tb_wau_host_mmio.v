`timescale 1ns/1ps

module tb_wau_host_mmio;
    localparam DATA_WIDTH = 32;
    localparam FLOW_ID_WIDTH = 12;

    reg clk;
    reg rst_n;

    reg mmio_read;
    reg mmio_write;
    reg [7:0] mmio_address;
    reg [31:0] mmio_writedata;
    wire [31:0] mmio_readdata;
    wire mmio_readdatavalid;

    wire soft_reset_req;
    wire enable_auto_adapt;

    wire host_in_valid;
    reg host_in_ready;
    wire [FLOW_ID_WIDTH-1:0] host_in_flow_id;
    wire signed [DATA_WIDTH-1:0] host_in_a;
    wire signed [DATA_WIDTH-1:0] host_in_b;

    reg host_out_valid;
    wire host_out_ready;
    reg [FLOW_ID_WIDTH-1:0] host_out_flow_id;
    reg signed [DATA_WIDTH-1:0] host_out_value;

    reg [31:0] obs_total_hop_count;
    reg [31:0] obs_total_stall_count;
    reg [31:0] obs_total_forward_count;
    reg [31:0] obs_total_local_delivered_count;
    reg [31:0] obs_total_cache_hit_count;
    reg [31:0] obs_total_cache_lookup_count;

    always #5 clk = ~clk;

    wau_host_mmio #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .ADDR_WIDTH(8)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .mmio_read(mmio_read),
        .mmio_write(mmio_write),
        .mmio_address(mmio_address),
        .mmio_writedata(mmio_writedata),
        .mmio_readdata(mmio_readdata),
        .mmio_readdatavalid(mmio_readdatavalid),
        .soft_reset_req(soft_reset_req),
        .enable_auto_adapt(enable_auto_adapt),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(host_out_ready),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .obs_total_hop_count(obs_total_hop_count),
        .obs_total_stall_count(obs_total_stall_count),
        .obs_total_forward_count(obs_total_forward_count),
        .obs_total_local_delivered_count(obs_total_local_delivered_count),
        .obs_total_cache_hit_count(obs_total_cache_hit_count),
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count)
    );

    task automatic mmio_wr;
        input [7:0] addr;
        input [31:0] data;
        begin
            @(negedge clk);
            mmio_address = addr;
            mmio_writedata = data;
            mmio_write = 1'b1;
            @(posedge clk);
            @(negedge clk);
            mmio_write = 1'b0;
        end
    endtask

    task automatic mmio_rd;
        input [7:0] addr;
        output [31:0] data;
        begin
            @(negedge clk);
            mmio_address = addr;
            mmio_read = 1'b1;
            @(posedge clk);
            @(negedge clk);
            mmio_read = 1'b0;
            data = mmio_readdata;
        end
    endtask

    reg [31:0] rd;
    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        mmio_read = 1'b0;
        mmio_write = 1'b0;
        mmio_address = 8'd0;
        mmio_writedata = 32'd0;
        host_in_ready = 1'b1;
        host_out_valid = 1'b0;
        host_out_flow_id = {FLOW_ID_WIDTH{1'b0}};
        host_out_value = {DATA_WIDTH{1'b0}};
        obs_total_hop_count = 32'd0;
        obs_total_stall_count = 32'd0;
        obs_total_forward_count = 32'd0;
        obs_total_local_delivered_count = 32'd0;
        obs_total_cache_hit_count = 32'd0;
        obs_total_cache_lookup_count = 32'd0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // Write CTRL with enable_auto_adapt=1, no reset; expect bit reflected on read.
        mmio_wr(8'h00, 32'd2);
        if (!enable_auto_adapt) begin
            $display("FAIL: enable_auto_adapt not set after CTRL write");
            $fatal(1);
        end

        // Program input operands and flow id.
        mmio_wr(8'h02, 32'h00000007);  // FLOW_ID
        mmio_wr(8'h03, 32'h00000010);  // IN_A
        mmio_wr(8'h04, 32'h00000020);  // IN_B
        if (host_in_flow_id !== 12'd7) begin
            $display("FAIL: FLOW_ID write not reflected, got %0d", host_in_flow_id);
            $fatal(1);
        end
        if (host_in_a !== 32'd16 || host_in_b !== 32'd32) begin
            $display("FAIL: IN_A/IN_B mismatch (%0d, %0d)", host_in_a, host_in_b);
            $fatal(1);
        end

        // TRIGGER write should raise host_in_valid until accepted.
        mmio_wr(8'h05, 32'd1);
        if (!host_in_valid) begin
            $display("FAIL: TRIGGER did not raise host_in_valid");
            $fatal(1);
        end
        // host_in_ready is already 1, so handshake completes next clock edge.
        @(posedge clk);
        @(negedge clk);
        if (host_in_valid) begin
            $display("FAIL: host_in_valid did not drop after accepted handshake");
            $fatal(1);
        end

        // Simulate pipeline producing a result.
        @(negedge clk);
        host_out_valid = 1'b1;
        host_out_flow_id = 12'd7;
        host_out_value = 32'sd1234;
        @(posedge clk);
        @(negedge clk);
        host_out_valid = 1'b0;

        // STATUS should report output_pending sticky high.
        mmio_rd(8'h01, rd);
        if (rd[2] !== 1'b1) begin
            $display("FAIL: STATUS output_pending should be set, got %h", rd);
            $fatal(1);
        end

        // Read OUT_VAL/OUT_FLOW; this also clears output_pending.
        mmio_rd(8'h11, rd);
        if (rd !== 32'sd1234) begin
            $display("FAIL: OUT_VAL mismatch got %0d", rd);
            $fatal(1);
        end
        mmio_rd(8'h10, rd);
        if (rd[FLOW_ID_WIDTH-1:0] !== 12'd7) begin
            $display("FAIL: OUT_FLOW mismatch got %0d", rd);
            $fatal(1);
        end
        mmio_rd(8'h01, rd);
        if (rd[2] !== 1'b0) begin
            $display("FAIL: STATUS output_pending should clear after OUT_VAL read, got %h", rd);
            $fatal(1);
        end

        // Observability counters read-through.
        obs_total_hop_count = 32'd42;
        obs_total_cache_hit_count = 32'd17;
        obs_total_cache_lookup_count = 32'd34;
        @(posedge clk);
        mmio_rd(8'h12, rd);
        if (rd !== 32'd42) begin
            $display("FAIL: HOPS readback got %0d", rd);
            $fatal(1);
        end
        mmio_rd(8'h16, rd);
        if (rd !== 32'd17) begin
            $display("FAIL: CACHE_H readback got %0d", rd);
            $fatal(1);
        end
        mmio_rd(8'h17, rd);
        if (rd !== 32'd34) begin
            $display("FAIL: CACHE_L readback got %0d", rd);
            $fatal(1);
        end

        $display("PASS: tb_wau_host_mmio");
        $finish;
    end
endmodule
