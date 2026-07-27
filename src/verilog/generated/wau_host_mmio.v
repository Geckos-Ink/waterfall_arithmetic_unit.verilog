// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_host_mmio #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter ADDR_WIDTH = 8
) (
    input wire clk,
    input wire rst_n,

    // Host (Avalon-MM-like) interface
    input wire mmio_read,
    input wire mmio_write,
    input wire [ADDR_WIDTH-1:0] mmio_address,
    input wire [31:0] mmio_writedata,
    output reg [31:0] mmio_readdata,
    output reg mmio_readdatavalid,

    // Soft reset request (asserted for one cycle after CTRL[0]=1)
    output reg soft_reset_req,
    output reg enable_auto_adapt,

    // Pipeline-side handshakes with wau_top
    output reg host_in_valid,
    input wire host_in_ready,
    output reg [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    output reg signed [DATA_WIDTH-1:0] host_in_a,
    output reg signed [DATA_WIDTH-1:0] host_in_b,

    input wire host_out_valid,
    output wire host_out_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_out_value,

    // Observability bus from wau_top (32-bit free-running counters)
    input wire [31:0] obs_total_hop_count,
    input wire [31:0] obs_total_stall_count,
    input wire [31:0] obs_total_forward_count,
    input wire [31:0] obs_total_local_delivered_count,
    input wire [31:0] obs_total_cache_hit_count,
    input wire [31:0] obs_total_cache_lookup_count,
    input wire [31:0] obs_total_contract_grant_count,
    input wire [31:0] obs_total_contract_hold_cycles,
    input wire [31:0] obs_total_contract_defer_count
);
    localparam [ADDR_WIDTH-1:0] ADDR_CTRL     = 'h00;
    localparam [ADDR_WIDTH-1:0] ADDR_STATUS   = 'h01;
    localparam [ADDR_WIDTH-1:0] ADDR_FLOW_ID  = 'h02;
    localparam [ADDR_WIDTH-1:0] ADDR_IN_A     = 'h03;
    localparam [ADDR_WIDTH-1:0] ADDR_IN_B     = 'h04;
    localparam [ADDR_WIDTH-1:0] ADDR_TRIGGER  = 'h05;
    localparam [ADDR_WIDTH-1:0] ADDR_OUT_FLOW = 'h10;
    localparam [ADDR_WIDTH-1:0] ADDR_OUT_VAL  = 'h11;
    localparam [ADDR_WIDTH-1:0] ADDR_HOPS     = 'h12;
    localparam [ADDR_WIDTH-1:0] ADDR_STALLS   = 'h13;
    localparam [ADDR_WIDTH-1:0] ADDR_FORWARDS = 'h14;
    localparam [ADDR_WIDTH-1:0] ADDR_DELIVRD  = 'h15;
    localparam [ADDR_WIDTH-1:0] ADDR_CACHE_H  = 'h16;
    localparam [ADDR_WIDTH-1:0] ADDR_CACHE_L  = 'h17;
    localparam [ADDR_WIDTH-1:0] ADDR_CTR_GRNT = 'h18;
    localparam [ADDR_WIDTH-1:0] ADDR_CTR_HOLD = 'h19;
    localparam [ADDR_WIDTH-1:0] ADDR_CTR_DEFR = 'h1A;

    reg [FLOW_ID_WIDTH-1:0] last_out_flow;
    reg signed [DATA_WIDTH-1:0] last_out_value;
    reg output_pending;

    // The bus is always ready to drain results into the latched registers.
    assign host_out_ready = 1'b1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mmio_readdata <= 32'd0;
            mmio_readdatavalid <= 1'b0;
            soft_reset_req <= 1'b0;
            enable_auto_adapt <= 1'b1;
            host_in_valid <= 1'b0;
            host_in_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            host_in_a <= {DATA_WIDTH{1'b0}};
            host_in_b <= {DATA_WIDTH{1'b0}};
            last_out_flow <= {FLOW_ID_WIDTH{1'b0}};
            last_out_value <= {DATA_WIDTH{1'b0}};
            output_pending <= 1'b0;
        end else begin
            mmio_readdatavalid <= 1'b0;
            soft_reset_req <= 1'b0;

            // Capture host_out_value/flow into latched status registers once the
            // pipeline produces a result; output_pending stays sticky until the
            // host explicitly reads OUT_VAL/OUT_FLOW or writes CTRL[0].
            if (host_out_valid && host_out_ready) begin
                last_out_flow <= host_out_flow_id;
                last_out_value <= host_out_value;
                output_pending <= 1'b1;
            end

            // Clear host_in_valid on accepted handshake.
            if (host_in_valid && host_in_ready) begin
                host_in_valid <= 1'b0;
            end

            if (mmio_write) begin
                case (mmio_address)
                    ADDR_CTRL: begin
                        soft_reset_req <= mmio_writedata[0];
                        enable_auto_adapt <= mmio_writedata[1];
                        if (mmio_writedata[0]) output_pending <= 1'b0;
                    end
                    ADDR_FLOW_ID: host_in_flow_id <= mmio_writedata[FLOW_ID_WIDTH-1:0];
                    ADDR_IN_A: host_in_a <= mmio_writedata[DATA_WIDTH-1:0];
                    ADDR_IN_B: host_in_b <= mmio_writedata[DATA_WIDTH-1:0];
                    ADDR_TRIGGER: host_in_valid <= 1'b1;
                    default: ;
                endcase
            end

            if (mmio_read) begin
                mmio_readdatavalid <= 1'b1;
                case (mmio_address)
                    ADDR_CTRL: mmio_readdata <= {30'd0, enable_auto_adapt, 1'b0};
                    ADDR_STATUS: mmio_readdata <= {29'd0, output_pending, host_out_valid, host_in_ready};
                    ADDR_FLOW_ID: mmio_readdata <= {{(32-FLOW_ID_WIDTH){1'b0}}, host_in_flow_id};
                    ADDR_IN_A: mmio_readdata <= host_in_a;
                    ADDR_IN_B: mmio_readdata <= host_in_b;
                    ADDR_OUT_FLOW: begin
                        mmio_readdata <= {{(32-FLOW_ID_WIDTH){1'b0}}, last_out_flow};
                        output_pending <= 1'b0;
                    end
                    ADDR_OUT_VAL: begin
                        mmio_readdata <= last_out_value;
                        output_pending <= 1'b0;
                    end
                    ADDR_HOPS: mmio_readdata <= obs_total_hop_count;
                    ADDR_STALLS: mmio_readdata <= obs_total_stall_count;
                    ADDR_FORWARDS: mmio_readdata <= obs_total_forward_count;
                    ADDR_DELIVRD: mmio_readdata <= obs_total_local_delivered_count;
                    ADDR_CACHE_H: mmio_readdata <= obs_total_cache_hit_count;
                    ADDR_CACHE_L: mmio_readdata <= obs_total_cache_lookup_count;
                    ADDR_CTR_GRNT: mmio_readdata <= obs_total_contract_grant_count;
                    ADDR_CTR_HOLD: mmio_readdata <= obs_total_contract_hold_cycles;
                    ADDR_CTR_DEFR: mmio_readdata <= obs_total_contract_defer_count;
                    default: mmio_readdata <= 32'd0;
                endcase
            end
        end
    end
endmodule
