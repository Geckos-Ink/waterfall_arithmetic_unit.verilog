// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

// DE0-NANO board wrapper.
//
// Pin-level I/O is exposed via the wau_host_mmio register file so:
//   * external host software / NIOS-II / Avalon-MM can drive WAU through
//     mmio_address/read/write/readdata signals,
//   * on-board push-buttons + switches still trigger the legacy demo flow
//     by emulating MMIO writes (a tiny ROM sequencer drives the bus when
//     KEY[1] is pressed).
//
// LEDs surface live status + observability without needing a JTAG host:
//   LED[0] host_out_valid
//   LED[1] host_in_ready
//   LED[2] output_pending (sticky until host reads OUT_VAL)
//   LED[3] enable_auto_adapt
//   LED[7:4] low nibble of obs_total_hop_count (handy as a coarse traffic LED).
module wau_de0_nano_top (
    input wire CLOCK_50,
    input wire [1:0] KEY,
    input wire [3:0] SW,
    output wire [7:0] LED,

    // Optional external MMIO bus. Tie unused inputs to 0 in board pin assignments.
    input wire ext_mmio_read,
    input wire ext_mmio_write,
    input wire [7:0] ext_mmio_address,
    input wire [31:0] ext_mmio_writedata,
    output wire [31:0] ext_mmio_readdata,
    output wire ext_mmio_readdatavalid
);
    localparam DATA_WIDTH = `WAU_DATA_WIDTH;
    localparam FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH;

    wire core_rst_n = KEY[0];
    wire soft_reset_req;
    wire enable_auto_adapt;

    wire host_in_valid;
    wire host_in_ready;
    wire [FLOW_ID_WIDTH-1:0] host_in_flow_id;
    wire signed [DATA_WIDTH-1:0] host_in_a;
    wire signed [DATA_WIDTH-1:0] host_in_b;

    wire host_out_valid;
    wire host_out_ready;
    wire [FLOW_ID_WIDTH-1:0] host_out_flow_id;
    wire signed [DATA_WIDTH-1:0] host_out_value;

    wire [31:0] obs_total_hop_count;
    wire [31:0] obs_total_stall_count;
    wire [31:0] obs_total_forward_count;
    wire [31:0] obs_total_local_delivered_count;
    wire [31:0] obs_total_cache_hit_count;
    wire [31:0] obs_total_cache_lookup_count;
    wire [31:0] obs_total_contract_grant_count;
    wire [31:0] obs_total_contract_hold_cycles;
    wire [31:0] obs_total_contract_defer_count;

    // Button-driven MMIO emulator: edge-detect KEY[1] (low-active) and
    // sequence FLOW_ID/IN_A/IN_B/TRIGGER writes. SW[3:0] supplies the data nibble.
    reg key1_d;
    reg [2:0] seq_state;
    reg do_button_write;
    reg [7:0] button_addr;
    reg [31:0] button_data;

    wire trigger_edge = key1_d && !KEY[1];

    always @(posedge CLOCK_50 or negedge core_rst_n) begin
        if (!core_rst_n) begin
            key1_d <= 1'b1;
            seq_state <= 3'd0;
            do_button_write <= 1'b0;
            button_addr <= 8'd0;
            button_data <= 32'd0;
        end else begin
            key1_d <= KEY[1];
            do_button_write <= 1'b0;

            if (trigger_edge) begin
                seq_state <= 3'd1;
            end

            case (seq_state)
                3'd1: begin
                    button_addr <= 8'h02;  // FLOW_ID
                    button_data <= {{(32-FLOW_ID_WIDTH){1'b0}}, SW[3:0], {{(FLOW_ID_WIDTH-4){1'b0}}}};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd2;
                end
                3'd2: begin
                    button_addr <= 8'h03;  // IN_A
                    button_data <= {{(32-DATA_WIDTH){1'b0}}, {(DATA_WIDTH-4){1'b0}}, SW[3:0]};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd3;
                end
                3'd3: begin
                    button_addr <= 8'h04;  // IN_B
                    button_data <= {{(32-DATA_WIDTH){1'b0}}, {(DATA_WIDTH-2){1'b0}}, 2'd3};
                    do_button_write <= 1'b1;
                    seq_state <= 3'd4;
                end
                3'd4: begin
                    button_addr <= 8'h05;  // TRIGGER
                    button_data <= 32'd1;
                    do_button_write <= 1'b1;
                    seq_state <= 3'd0;
                end
                default: seq_state <= 3'd0;
            endcase
        end
    end

    wire bus_write = ext_mmio_write || do_button_write;
    wire bus_read = ext_mmio_read;
    wire [7:0] bus_addr = do_button_write ? button_addr : ext_mmio_address;
    wire [31:0] bus_writedata = do_button_write ? button_data : ext_mmio_writedata;

    wire [31:0] mmio_readdata;
    wire mmio_readdatavalid;
    assign ext_mmio_readdata = mmio_readdata;
    assign ext_mmio_readdatavalid = mmio_readdatavalid;

    wau_host_mmio #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .ADDR_WIDTH(8)
    ) mmio_u (
        .clk(CLOCK_50),
        .rst_n(core_rst_n),
        .mmio_read(bus_read),
        .mmio_write(bus_write),
        .mmio_address(bus_addr),
        .mmio_writedata(bus_writedata),
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
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count),
        .obs_total_contract_grant_count(obs_total_contract_grant_count),
        .obs_total_contract_hold_cycles(obs_total_contract_hold_cycles),
        .obs_total_contract_defer_count(obs_total_contract_defer_count)
    );

    assign LED[0] = host_out_valid;
    assign LED[1] = host_in_ready;
    assign LED[2] = mmio_readdatavalid;
    assign LED[3] = enable_auto_adapt;
    assign LED[7:4] = obs_total_hop_count[3:0];

    wau_top wau_u (
        .clk(CLOCK_50),
        .rst_n(core_rst_n & ~soft_reset_req),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(host_out_ready),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .enable_auto_adapt(enable_auto_adapt),
        .obs_total_hop_count(obs_total_hop_count),
        .obs_total_stall_count(obs_total_stall_count),
        .obs_total_forward_count(obs_total_forward_count),
        .obs_total_local_delivered_count(obs_total_local_delivered_count),
        .obs_total_cache_hit_count(obs_total_cache_hit_count),
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count),
        .obs_total_contract_grant_count(obs_total_contract_grant_count),
        .obs_total_contract_hold_cycles(obs_total_contract_hold_cycles),
        .obs_total_contract_defer_count(obs_total_contract_defer_count)
    );
endmodule
