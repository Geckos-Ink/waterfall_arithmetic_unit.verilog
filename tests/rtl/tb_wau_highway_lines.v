`timescale 1ns/1ps
`include "wau_defs.vh"

// Owns the default per-line highway contract: there is one highway per LINE of
// cores, not one highway threaded through the whole grid.
//
// Three things have to hold, and the third is the whole point of the topology:
//
//   1. every core reaches every other core *on its own line* directly;
//   2. anything addressed off-line leaves through that line's own coordinator
//      hub, and a packet handed to a hub reaches any core on that line;
//   3. the lines are INDEPENDENT — two lines carry traffic in the same cycle
//      without sharing wires, backpressure or arbitration. A shared chain
//      would serialise exactly here, so this is what distinguishes the two.
//
// Meaningful only against RTL generated with
// device.highway.topology = "lines" (the default).
module tb_wau_highway_lines;
    localparam GRID_X = 3;
    localparam GRID_Y = 2;
    localparam CORE_COUNT = GRID_X * GRID_Y;   // 6 cores, 2 lines of 3
    localparam CORE_ID_WIDTH = 8;
    localparam PAYLOAD_WIDTH = 16;
    localparam TB_LINE_COUNT = GRID_Y;
    localparam CONTRACT_WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH;

    // The reserved "leave this line" destination (see wau_highway_router).
    localparam [CORE_ID_WIDTH-1:0] HUB_DST = {CORE_ID_WIDTH{1'b1}};

    reg clk;
    reg rst_n;

    reg [CORE_COUNT-1:0] local_in_valid;
    wire [CORE_COUNT-1:0] local_in_ready;
    reg [CORE_COUNT*CORE_ID_WIDTH-1:0] local_in_dst;
    reg [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_in_payload;

    wire [CORE_COUNT-1:0] local_out_valid;
    reg [CORE_COUNT-1:0] local_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] local_out_dst;
    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_out_payload;

    reg [TB_LINE_COUNT-1:0] hub_in_valid;
    wire [TB_LINE_COUNT-1:0] hub_in_ready;
    reg [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] hub_in_dst;
    reg [TB_LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_in_payload;

    wire [TB_LINE_COUNT-1:0] hub_out_valid;
    reg [TB_LINE_COUNT-1:0] hub_out_ready;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] hub_out_dst;
    wire [TB_LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_out_payload;

    wire [CORE_COUNT-1:0] contract_call;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] contract_slot;
    wire [TB_LINE_COUNT-1:0] contract_grant_valid;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] contract_grant_core;
    wire [TB_LINE_COUNT*2-1:0] contract_grant_mode;
    wire [TB_LINE_COUNT*16-1:0] contract_grant_remaining;
    wire [31:0] contract_grant_count;
    wire [31:0] contract_hold_cycles;
    wire [31:0] contract_defer_count;

    wire [CORE_COUNT*32-1:0] router_hop_count;
    wire [CORE_COUNT*32-1:0] router_stall_count;
    wire [CORE_COUNT*32-1:0] router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] router_forward_count;

    always #5 clk = ~clk;

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .GRID_Z(1),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(PAYLOAD_WIDTH),
        .CONTRACT_BUS_ENABLE(0)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .local_in_valid(local_in_valid),
        .local_in_ready(local_in_ready),
        .local_in_dst(local_in_dst),
        .local_in_payload(local_in_payload),
        .local_out_valid(local_out_valid),
        .local_out_ready(local_out_ready),
        .local_out_dst(local_out_dst),
        .local_out_payload(local_out_payload),
        .hub_in_valid(hub_in_valid),
        .hub_in_ready(hub_in_ready),
        .hub_in_dst(hub_in_dst),
        .hub_in_payload(hub_in_payload),
        .hub_out_valid(hub_out_valid),
        .hub_out_ready(hub_out_ready),
        .hub_out_dst(hub_out_dst),
        .hub_out_payload(hub_out_payload),
        .contract_req({CORE_COUNT{1'b0}}),
        .contract_word({(CORE_COUNT*CONTRACT_WORD_WIDTH){1'b0}}),
        .contract_call(contract_call),
        .contract_slot(contract_slot),
        .contract_grant_valid(contract_grant_valid),
        .contract_grant_core(contract_grant_core),
        .contract_grant_mode(contract_grant_mode),
        .contract_grant_remaining(contract_grant_remaining),
        .contract_grant_count(contract_grant_count),
        .contract_hold_cycles(contract_hold_cycles),
        .contract_defer_count(contract_defer_count),
        .router_hop_count(router_hop_count),
        .router_stall_count(router_stall_count),
        .router_local_delivered_count(router_local_delivered_count),
        .router_forward_count(router_forward_count)
    );

    task automatic clear_inputs;
        begin
            local_in_valid = {CORE_COUNT{1'b0}};
            local_in_dst = {(CORE_COUNT*CORE_ID_WIDTH){1'b0}};
            local_in_payload = {(CORE_COUNT*PAYLOAD_WIDTH){1'b0}};
            local_out_ready = {CORE_COUNT{1'b1}};
            hub_in_valid = {TB_LINE_COUNT{1'b0}};
            hub_in_dst = {(TB_LINE_COUNT*CORE_ID_WIDTH){1'b0}};
            hub_in_payload = {(TB_LINE_COUNT*PAYLOAD_WIDTH){1'b0}};
            hub_out_ready = {TB_LINE_COUNT{1'b1}};
        end
    endtask

    task automatic drive_core;
        input integer src;
        input [CORE_ID_WIDTH-1:0] dst;
        input [PAYLOAD_WIDTH-1:0] payload;
        begin
            local_in_valid[src] = 1'b1;
            local_in_dst[(src*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = dst;
            local_in_payload[(src*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = payload;
        end
    endtask

    task automatic drive_hub;
        input integer line;
        input [CORE_ID_WIDTH-1:0] dst;
        input [PAYLOAD_WIDTH-1:0] payload;
        begin
            hub_in_valid[line] = 1'b1;
            hub_in_dst[(line*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = dst;
            hub_in_payload[(line*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = payload;
        end
    endtask

    integer src_i;
    integer dst_i;
    integer line_i;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        clear_inputs();

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        // ---- 1. every core reaches every other core on its own line ----
        for (line_i = 0; line_i < TB_LINE_COUNT; line_i = line_i + 1) begin
            for (src_i = 0; src_i < GRID_X; src_i = src_i + 1) begin
                for (dst_i = 0; dst_i < GRID_X; dst_i = dst_i + 1) begin
                    if (src_i != dst_i) begin
                        clear_inputs();
                        drive_core(line_i * GRID_X + src_i,
                                   (line_i * GRID_X + dst_i),
                                   16'hC000 + (src_i * 16) + dst_i);
                        #1;
                        if (local_out_valid[line_i * GRID_X + dst_i] !== 1'b1) begin
                            $display("FAIL: line %0d: %0d -> %0d not delivered",
                                line_i, src_i, dst_i);
                            $fatal(1);
                        end
                        @(posedge clk);
                    end
                end
            end
        end

        // ---- 2. off-line traffic leaves through THIS line's own hub ----
        for (line_i = 0; line_i < TB_LINE_COUNT; line_i = line_i + 1) begin
            clear_inputs();
            // The last core of the line is the furthest from the hub, so this
            // also proves the whole line drains westward.
            drive_core(line_i * GRID_X + (GRID_X - 1), HUB_DST, 16'hD000 + line_i);
            #1;
            if (hub_out_valid[line_i] !== 1'b1) begin
                $display("FAIL: line %0d did not present its packet at its hub", line_i);
                $fatal(1);
            end
            if (hub_out_payload[(line_i*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]
                !== (16'hD000 + line_i)) begin
                $display("FAIL: line %0d hub payload mismatch", line_i);
                $fatal(1);
            end
            // ...and it must NOT leak onto any other line's hub.
            if (|(hub_out_valid & ~(1 << line_i))) begin
                $display("FAIL: line %0d packet leaked to another line's hub", line_i);
                $fatal(1);
            end
            @(posedge clk);
        end

        // ---- 2b. a hub can reach every core on its line ----
        for (line_i = 0; line_i < TB_LINE_COUNT; line_i = line_i + 1) begin
            for (dst_i = 0; dst_i < GRID_X; dst_i = dst_i + 1) begin
                clear_inputs();
                drive_hub(line_i, (line_i * GRID_X + dst_i), 16'hE000 + dst_i);
                #1;
                if (local_out_valid[line_i * GRID_X + dst_i] !== 1'b1) begin
                    $display("FAIL: hub %0d could not reach core %0d",
                        line_i, line_i * GRID_X + dst_i);
                    $fatal(1);
                end
                @(posedge clk);
            end
        end

        // ---- 3. the lines are independent ----
        // Both lines carry a packet in the same cycle. Neither may be delayed
        // by the other, and blocking one line's hub must not stall the other.
        clear_inputs();
        hub_out_ready[0] = 1'b0;                       // wedge line 0's hub
        drive_core(GRID_X - 1, HUB_DST, 16'h1111);     // line 0, blocked
        drive_core(2 * GRID_X - 1, HUB_DST, 16'h2222); // line 1, must be free
        #1;
        if (hub_out_valid[0] !== 1'b1 || hub_out_valid[1] !== 1'b1) begin
            $display("FAIL: lines did not both present in the same cycle");
            $fatal(1);
        end
        if (local_in_ready[2 * GRID_X - 1] !== 1'b1) begin
            $display("FAIL: blocking line 0's hub back-pressured line 1 -- the lines are not independent");
            $fatal(1);
        end
        if (local_in_ready[GRID_X - 1] !== 1'b0) begin
            $display("FAIL: line 0 accepted its packet while its hub was blocked");
            $fatal(1);
        end
        if (hub_out_payload[(1*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] !== 16'h2222) begin
            $display("FAIL: line 1 payload corrupted by line 0's traffic");
            $fatal(1);
        end
        @(posedge clk);

        clear_inputs();
        repeat (2) @(posedge clk);

        if ((router_hop_count[(0*32) +: 32] + router_hop_count[(5*32) +: 32]) == 32'd0) begin
            $display("FAIL: per-line router_hop_count never advanced");
            $fatal(1);
        end

        $display("PASS: tb_wau_highway_lines (%0d independent lines of %0d cores)",
            TB_LINE_COUNT, GRID_X);
        $finish;
    end
endmodule
