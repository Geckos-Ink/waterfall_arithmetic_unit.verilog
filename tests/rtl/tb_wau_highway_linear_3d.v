`timescale 1ns/1ps
`include "wau_defs.vh"

// The layered case of the single-dimension highway: with grid.z > 1 each layer
// carries its own index-order chain and the layers are joined only by the
// vertical up/down links. A packet therefore has to take the vertical hop
// first (the router resolves UP/DOWN before PREV/NEXT) and then walk the
// destination layer's chain.
//
// The chain-per-layer arrangement is the one place where dropping the planar
// links could silently partition the fabric, so this sweeps ALL ordered pairs
// on a 2x2x2 grid rather than spot-checking a route.
//
// Meaningful only against RTL generated with
// device.highway.topology = "linear" (the default).
module tb_wau_highway_linear_3d;
    localparam CORE_COUNT = 8;   // 2 x 2 x 2
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
        .GRID_Z(2),
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

    integer src_i;
    integer dst_i;
    integer failures;
    reg [PAYLOAD_WIDTH-1:0] payload;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        local_in_valid = {CORE_COUNT{1'b0}};
        local_in_dst = {(CORE_COUNT*CORE_ID_WIDTH){1'b0}};
        local_in_payload = {(CORE_COUNT*PAYLOAD_WIDTH){1'b0}};
        local_out_ready = {CORE_COUNT{1'b1}};

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        failures = 0;
        for (src_i = 0; src_i < CORE_COUNT; src_i = src_i + 1) begin
            for (dst_i = 0; dst_i < CORE_COUNT; dst_i = dst_i + 1) begin
                if (src_i != dst_i) begin
                    payload = 16'hA000 + (src_i * 16) + dst_i;
                    local_in_valid = {CORE_COUNT{1'b0}};
                    local_in_dst = {(CORE_COUNT*CORE_ID_WIDTH){1'b0}};
                    local_in_payload = {(CORE_COUNT*PAYLOAD_WIDTH){1'b0}};
                    local_in_valid[src_i] = 1'b1;
                    local_in_dst[(src_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] =
                        dst_i[CORE_ID_WIDTH-1:0];
                    local_in_payload[(src_i*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = payload;
                    #1;
                    if (local_out_valid[dst_i] !== 1'b1) begin
                        $display("FAIL: %0d -> %0d unreachable across layers", src_i, dst_i);
                        failures = failures + 1;
                    end else if (local_out_payload[(dst_i*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]
                                 !== payload) begin
                        $display("FAIL: %0d -> %0d payload mismatch", src_i, dst_i);
                        failures = failures + 1;
                    end
                    @(posedge clk);
                end
            end
        end

        if (failures != 0) begin
            $display("FAIL: %0d unreachable/corrupt pairs on the layered linear highway",
                failures);
            $fatal(1);
        end

        $display("PASS: tb_wau_highway_linear_3d (all %0d ordered pairs on 2x2x2)",
            CORE_COUNT * (CORE_COUNT - 1));
        $finish;
    end
endmodule
