`timescale 1ns/1ps
`include "wau_defs.vh"

module tb_wau_highway_mesh;
    localparam CORE_COUNT = 3;
    localparam CORE_ID_WIDTH = 8;
    // Highway lines this instantiation implies; pinned so the hub port
    // widths cannot drift from the topology under test.
    localparam TB_LINE_COUNT = 1;
    localparam PAYLOAD_WIDTH = 16;

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

    // The contract bus is a highway-side feature; these meshes are elaborated
    // with it disabled so the topology tests stay about routing alone.
    wire [CORE_COUNT-1:0] contract_call;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] contract_slot;
    wire [TB_LINE_COUNT-1:0] contract_grant_valid;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] contract_grant_core;
    wire [TB_LINE_COUNT*2-1:0] contract_grant_mode;
    wire [TB_LINE_COUNT*16-1:0] contract_grant_remaining;
    wire [31:0] contract_grant_count;
    wire [31:0] contract_hold_cycles;
    wire [31:0] contract_defer_count;

    // Hub ports: one coordinator channel per highway line.
    wire [TB_LINE_COUNT-1:0] hub_in_ready;
    wire [TB_LINE_COUNT-1:0] hub_out_valid;
    wire [TB_LINE_COUNT*CORE_ID_WIDTH-1:0] hub_out_dst;
    wire [TB_LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_out_payload;

    wire [CORE_COUNT*32-1:0] router_hop_count;
    wire [CORE_COUNT*32-1:0] router_stall_count;
    wire [CORE_COUNT*32-1:0] router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] router_forward_count;

    always #5 clk = ~clk;

    wau_highway_mesh #(
        .GRID_X(3),
        .GRID_Y(1),
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
        .hub_in_valid({TB_LINE_COUNT{1'b0}}),
        .hub_in_ready(hub_in_ready),
        .hub_in_dst({(TB_LINE_COUNT*CORE_ID_WIDTH){1'b0}}),
        .hub_in_payload({(TB_LINE_COUNT*PAYLOAD_WIDTH){1'b0}}),
        .hub_out_valid(hub_out_valid),
        .hub_out_ready({TB_LINE_COUNT{1'b1}}),
        .hub_out_dst(hub_out_dst),
        .hub_out_payload(hub_out_payload),
        .contract_req({CORE_COUNT{1'b0}}),
        .contract_word({(CORE_COUNT*`WAU_HIGHWAY_CONTRACT_WORD_WIDTH){1'b0}}),
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

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        clear_inputs();

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        // Inject packet from core 0 to core 2 while destination is blocked.
        local_out_ready[2] = 1'b0;
        local_in_valid[0] = 1'b1;
        local_in_dst[(0*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = 8'd2;
        local_in_payload[(0*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = 16'hCAFE;

        #1;
        if (local_in_ready[0] !== 1'b0) begin
            $display("FAIL: expected backpressure to deassert source ready");
            $fatal(1);
        end
        if (local_out_valid[2] !== 1'b1) begin
            $display("FAIL: expected destination valid even while blocked");
            $fatal(1);
        end

        // Unblock destination and verify payload delivery.
        local_out_ready[2] = 1'b1;
        #1;
        if (local_in_ready[0] !== 1'b1) begin
            $display("FAIL: source should become ready after unblocking destination");
            $fatal(1);
        end
        if (local_out_valid[2] !== 1'b1) begin
            $display("FAIL: destination valid missing after unblocking");
            $fatal(1);
        end
        if (local_out_dst[(2*CORE_ID_WIDTH) +: CORE_ID_WIDTH] !== 8'd2) begin
            $display("FAIL: wrong destination id at egress");
            $fatal(1);
        end
        if (local_out_payload[(2*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] !== 16'hCAFE) begin
            $display("FAIL: wrong payload at destination");
            $fatal(1);
        end

        // Reverse direction packet to verify bidirectional neighbor forwarding.
        clear_inputs();
        local_in_valid[2] = 1'b1;
        local_in_dst[(2*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = 8'd0;
        local_in_payload[(2*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = 16'h1234;

        #1;
        if (local_out_valid[0] !== 1'b1) begin
            $display("FAIL: expected reverse-direction packet at core 0");
            $fatal(1);
        end
        if (local_out_payload[(0*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] !== 16'h1234) begin
            $display("FAIL: reverse payload mismatch");
            $fatal(1);
        end

        // Drive a few clock edges with traffic so observability counters can advance,
        // then check that at least one router accumulated a hop.
        @(posedge clk);
        @(posedge clk);
        local_in_valid[0] = 1'b1;
        local_in_dst[(0*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = 8'd2;
        local_in_payload[(0*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] = 16'h00AA;
        repeat (4) @(posedge clk);
        clear_inputs();
        @(posedge clk);

        if ((router_hop_count[(0*32) +: 32]
             + router_hop_count[(1*32) +: 32]
             + router_hop_count[(2*32) +: 32]) == 32'd0) begin
            $display("FAIL: router_hop_count never advanced");
            $fatal(1);
        end

        $display("PASS: tb_wau_highway_mesh");
        $finish;
    end
endmodule
