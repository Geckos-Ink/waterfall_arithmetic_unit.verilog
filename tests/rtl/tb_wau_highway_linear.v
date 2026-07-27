`timescale 1ns/1ps
`include "wau_defs.vh"

// Owns the single-dimension ("linear") highway contract: on a multi-row grid
// the highway is ONE chain walked in core-index order, so the last core of a
// row is the previous hop of the first core of the next row. A packet from
// core 0 to core 3 on a 2x2 grid must therefore be reachable even though the
// two cores share neither a row nor a column -- there are no north/south links
// left to carry it.
//
// The layered case matters just as much: with grid.z > 1 each layer gets its
// OWN chain, joined only by the vertical up/down links, so cross-layer traffic
// has to take the vertical hop first and then walk the destination layer's
// chain. tb_wau_highway_linear_3d covers that below.
//
// This testbench is meaningful only against RTL generated with
// device.highway.topology = "linear" (the default).
module tb_wau_highway_linear;
    localparam CORE_COUNT = 4;
    localparam CORE_ID_WIDTH = 8;
    localparam PAYLOAD_WIDTH = 16;
    localparam CONTRACT_WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH;

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

    wire [CORE_COUNT-1:0] contract_call;
    wire [CORE_ID_WIDTH-1:0] contract_slot;
    wire contract_grant_valid;
    wire [CORE_ID_WIDTH-1:0] contract_grant_core;
    wire [1:0] contract_grant_mode;
    wire [15:0] contract_grant_remaining;
    wire [31:0] contract_grant_count;
    wire [31:0] contract_hold_cycles;
    wire [31:0] contract_defer_count;

    wire [CORE_COUNT*32-1:0] router_hop_count;
    wire [CORE_COUNT*32-1:0] router_stall_count;
    wire [CORE_COUNT*32-1:0] router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] router_forward_count;

    always #5 clk = ~clk;

    wau_highway_mesh #(
        .GRID_X(2),
        .GRID_Y(2),
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
        end
    endtask

    // Drive one packet and check it comes out at `dst`.
    task automatic send_and_check;
        input integer src;
        input integer dst;
        input [PAYLOAD_WIDTH-1:0] payload;
        begin
            clear_inputs();
            local_in_valid[src] = 1'b1;
            local_in_dst[(src*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = dst[CORE_ID_WIDTH-1:0];
            local_in_payload[(src*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = payload;
            #1;
            if (local_out_valid[dst] !== 1'b1) begin
                $display("FAIL: packet %0d -> %0d never reached its destination", src, dst);
                $fatal(1);
            end
            if (local_out_payload[(dst*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] !== payload) begin
                $display("FAIL: payload mismatch for %0d -> %0d", src, dst);
                $fatal(1);
            end
            if (local_in_ready[src] !== 1'b1) begin
                $display("FAIL: source %0d not accepted for dst %0d", src, dst);
                $fatal(1);
            end
            @(posedge clk);
        end
    endtask

    integer src_i;
    integer dst_i;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        clear_inputs();

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        // The wrap link: core 1 is the end of row 0, core 2 the start of row 1.
        // Under the old matrix topology this hop was north/south; under the
        // linear topology it is the chain's row-to-row joint.
        send_and_check(1, 2, 16'hBEEF);
        send_and_check(2, 1, 16'hFEED);

        // Every core must still reach every other core over the one chain.
        for (src_i = 0; src_i < CORE_COUNT; src_i = src_i + 1) begin
            for (dst_i = 0; dst_i < CORE_COUNT; dst_i = dst_i + 1) begin
                if (src_i != dst_i) begin
                    send_and_check(src_i, dst_i, 16'h1000 + (src_i * 16) + dst_i);
                end
            end
        end

        clear_inputs();
        repeat (2) @(posedge clk);

        if ((router_hop_count[(0*32) +: 32]
             + router_hop_count[(1*32) +: 32]
             + router_hop_count[(2*32) +: 32]
             + router_hop_count[(3*32) +: 32]) == 32'd0) begin
            $display("FAIL: linear highway router_hop_count never advanced");
            $fatal(1);
        end

        // With CONTRACT_BUS_ENABLE=0 the bus must be entirely absent: no grants,
        // no deferrals, no admission cost.
        if (contract_grant_valid !== 1'b0 || contract_grant_count !== 32'd0
            || contract_defer_count !== 32'd0) begin
            $display("FAIL: disabled contract bus is not inert");
            $fatal(1);
        end

        $display("PASS: tb_wau_highway_linear");
        $finish;
    end
endmodule
