// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.
//
// DE0-Nano board top: CLOCK_50, KEY, SW, LED + vJTAG-bridged WAU.
//
// All host I/O happens over USB-Blaster + Altera virtual JTAG; switches/keys
// are NOT required for the host benchmark, but KEY[0] still gates the global
// reset and the LEDs surface live status so users can sanity-check the design.
//
// On-board ports we explicitly tristate so the bitstream is safe even though
// we don't drive the SDRAM, accelerometer, ADC or GPIO headers:
//   * DRAM_* tied to safe levels (no SDRAM controller here)
//   * I2C / G_SENSOR / ADC tied to inactive
//   * GPIO headers left as inputs (no assignments here)

`timescale 1ns/1ps
`include "wau_defs.vh"

`ifndef WAU_BOARD_CLOCK_DIVIDE_LOG2
`define WAU_BOARD_CLOCK_DIVIDE_LOG2 4
`endif

module wau_jtag_top (
    input  wire        CLOCK_50,
    input  wire [1:0]  KEY,
    input  wire [3:0]  SW,
    output wire [7:0]  LED,

    // SDRAM (drive to inactive)
    output wire [12:0] DRAM_ADDR,
    output wire [1:0]  DRAM_BA,
    output wire        DRAM_CAS_N,
    output wire        DRAM_CKE,
    output wire        DRAM_CLK,
    output wire        DRAM_CS_N,
    inout  wire [15:0] DRAM_DQ,
    output wire [1:0]  DRAM_DQM,
    output wire        DRAM_RAS_N,
    output wire        DRAM_WE_N,

    // EPCS (drive to inactive)
    output wire        EPCS_ASDO,
    input  wire        EPCS_DATA0,
    output wire        EPCS_DCLK,
    output wire        EPCS_NCSO,

    // Accelerometer / EEPROM (drive to inactive)
    inout  wire        I2C_SCLK,
    inout  wire        I2C_SDAT,
    output wire        G_SENSOR_CS_N,
    input  wire        G_SENSOR_INT,

    // ADC (drive to inactive)
    output wire        ADC_CS_N,
    output wire        ADC_SADDR,
    output wire        ADC_SCLK,
    input  wire        ADC_SDAT
);
    // The generated mesh has long combinational ready paths.  The former
    // 50 MHz board image violated timing badly as the grid grew and could
    // hang despite fitting.  Run the complete WAU/JTAG domain from a divided
    // board clock until elastic router timing cuts land.  /16 = 3.125 MHz;
    // fixed CW work still finishes far below the host watchdog interval.
    reg [7:0] wau_clock_divider = 8'd0;
    always @(posedge CLOCK_50 or negedge KEY[0]) begin
        if (!KEY[0]) wau_clock_divider <= 8'd0;
        else wau_clock_divider <= wau_clock_divider + 8'd1;
    end
    wire wau_clk = wau_clock_divider[`WAU_BOARD_CLOCK_DIVIDE_LOG2-1];
    localparam integer DATA_WIDTH = `WAU_DATA_WIDTH;
    localparam integer FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH;
    localparam integer MMIO_ADDR_WIDTH = 8;

    // ------------------------------------------------------------------
    // Forward declarations so we can build status / reset glue at the top.
    // ------------------------------------------------------------------
    wire bridge_soft_reset;
    wire mmio_soft_reset_req;
    wire host_in_valid;
    wire host_in_ready;
    wire host_out_valid;
    wire host_out_ready;
    wire enable_auto_adapt_w;
    wire output_pending_w;
    wire jtag_busy;
    wire bridge_mmio_read;
    wire bridge_mmio_write;
    wire [7:0]  bridge_mmio_address;
    wire [31:0] bridge_mmio_writedata;
    wire [31:0] bridge_mmio_readdata;
    wire        bridge_mmio_readdatavalid;
    wire [FLOW_ID_WIDTH-1:0] host_in_flow_id;
    wire signed [DATA_WIDTH-1:0] host_in_a;
    wire signed [DATA_WIDTH-1:0] host_in_b;
    wire [FLOW_ID_WIDTH-1:0] host_out_flow_id;
    wire signed [DATA_WIDTH-1:0] host_out_value;

    // ------------------------------------------------------------------
    // Reset: KEY[0] is active-low push button on DE0-Nano. Stretch it a few
    // cycles for clean release; also accept the bridge-issued soft reset.
    // ------------------------------------------------------------------
    reg [3:0] rst_stretch;
    reg       rst_n_reg;

    always @(posedge wau_clk) begin
        if (!KEY[0]) begin
            rst_stretch <= 4'd0;
            rst_n_reg   <= 1'b0;
        end else if (rst_stretch != 4'hF) begin
            rst_stretch <= rst_stretch + 4'd1;
            rst_n_reg   <= 1'b0;
        end else begin
            rst_n_reg   <= 1'b1;
        end
    end

    wire core_rst_n = rst_n_reg & ~bridge_soft_reset & ~mmio_soft_reset_req;

    // ------------------------------------------------------------------
    // vJTAG bridge -> MMIO bus (bridge_* wires already forward-declared)
    // ------------------------------------------------------------------
    // Aux observability word exposed via IR_OBS — packs a small status
    // snapshot so the host can poll without going through the MMIO read path.
    wire [31:0] obs_aux_word = {
        SW,                       // [31:28]
        KEY,                      // [27:26]
        2'b00,                    // [25:24]
        jtag_busy,                // [23]
        output_pending_w,         // [22]
        host_out_valid,           // [21]
        host_in_ready,            // [20]
        enable_auto_adapt_w,      // [19]
        3'b000,                   // [18:16]
        16'hCAFE                  // [15:0] magic
    };

    wau_vjtag_bridge #(
        .ADDR_WIDTH(MMIO_ADDR_WIDTH),
        .DATA_WIDTH(32)
    ) bridge_u (
        .clk(wau_clk),
        .rst_n(rst_n_reg),
        .mmio_read(bridge_mmio_read),
        .mmio_write(bridge_mmio_write),
        .mmio_address(bridge_mmio_address),
        .mmio_writedata(bridge_mmio_writedata),
        .mmio_readdata(bridge_mmio_readdata),
        .mmio_readdatavalid(bridge_mmio_readdatavalid),
        .soft_reset_pulse(bridge_soft_reset),
        .obs_aux_word(obs_aux_word),
        .jtag_busy(jtag_busy)
    );

    // ------------------------------------------------------------------
    // WAU MMIO register file + core pipeline (handshakes forward-declared)
    // ------------------------------------------------------------------
    wire [31:0] obs_total_hop_count;
    wire [31:0] obs_total_stall_count;
    wire [31:0] obs_total_forward_count;
    wire [31:0] obs_total_local_delivered_count;
    wire [31:0] obs_total_cache_hit_count;
    wire [31:0] obs_total_cache_lookup_count;

    wau_host_mmio #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .ADDR_WIDTH(MMIO_ADDR_WIDTH)
    ) mmio_u (
        .clk(wau_clk),
        .rst_n(rst_n_reg),
        .mmio_read(bridge_mmio_read),
        .mmio_write(bridge_mmio_write),
        .mmio_address(bridge_mmio_address),
        .mmio_writedata(bridge_mmio_writedata),
        .mmio_readdata(bridge_mmio_readdata),
        .mmio_readdatavalid(bridge_mmio_readdatavalid),
        .soft_reset_req(mmio_soft_reset_req),
        .enable_auto_adapt(enable_auto_adapt_w),
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

    // Expose output_pending status (sticky inside wau_host_mmio): tap via the
    // host_out_valid + readdatavalid handshake — for a more faithful surface
    // we mirror it by latching here too.
    reg output_pending_reg;
    always @(posedge wau_clk or negedge rst_n_reg) begin
        if (!rst_n_reg)            output_pending_reg <= 1'b0;
        else if (host_out_valid)   output_pending_reg <= 1'b1;
        else if (bridge_mmio_read && bridge_mmio_address == 8'h11)
                                   output_pending_reg <= 1'b0;
    end
    assign output_pending_w = output_pending_reg;

    wau_top wau_u (
        .clk(wau_clk),
        .rst_n(core_rst_n),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(host_out_ready),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .enable_auto_adapt(enable_auto_adapt_w),
        .obs_total_hop_count(obs_total_hop_count),
        .obs_total_stall_count(obs_total_stall_count),
        .obs_total_forward_count(obs_total_forward_count),
        .obs_total_local_delivered_count(obs_total_local_delivered_count),
        .obs_total_cache_hit_count(obs_total_cache_hit_count),
        .obs_total_cache_lookup_count(obs_total_cache_lookup_count)
    );

    // ------------------------------------------------------------------
    // LED status surface
    //   LED[0] host_out_valid                       (result this cycle)
    //   LED[1] host_in_ready                        (pipeline accepts input)
    //   LED[2] output_pending (sticky)
    //   LED[3] enable_auto_adapt
    //   LED[4] jtag_busy                            (host actively shifting)
    //   LED[5] mmio_readdatavalid                   (blinks per MMIO read)
    //   LED[6] heartbeat ~3 Hz
    //   LED[7] low bit of obs_total_hop_count (traffic LED)
    // ------------------------------------------------------------------
    reg [23:0] heartbeat;
    always @(posedge wau_clk or negedge rst_n_reg) begin
        if (!rst_n_reg) heartbeat <= 24'd0;
        else            heartbeat <= heartbeat + 24'd1;
    end

    assign LED[0] = host_out_valid;
    assign LED[1] = host_in_ready;
    assign LED[2] = output_pending_w;
    assign LED[3] = enable_auto_adapt_w;
    assign LED[4] = jtag_busy;
    assign LED[5] = bridge_mmio_readdatavalid;
    assign LED[6] = heartbeat[23];
    assign LED[7] = obs_total_hop_count[0];

    // ------------------------------------------------------------------
    // Drive unused on-board peripherals to inactive so they don't float.
    // ------------------------------------------------------------------
    assign DRAM_ADDR  = 13'd0;
    assign DRAM_BA    = 2'd0;
    assign DRAM_CAS_N = 1'b1;
    assign DRAM_CKE   = 1'b0;
    assign DRAM_CLK   = 1'b0;
    assign DRAM_CS_N  = 1'b1;
    assign DRAM_DQ    = 16'hzzzz;
    assign DRAM_DQM   = 2'b11;
    assign DRAM_RAS_N = 1'b1;
    assign DRAM_WE_N  = 1'b1;

    assign EPCS_ASDO = 1'b0;
    assign EPCS_DCLK = 1'b0;
    assign EPCS_NCSO = 1'b1;

    assign I2C_SCLK = 1'bz;
    assign I2C_SDAT = 1'bz;
    assign G_SENSOR_CS_N = 1'b1;

    assign ADC_CS_N = 1'b1;
    assign ADC_SADDR = 1'b0;
    assign ADC_SCLK = 1'b0;

endmodule
