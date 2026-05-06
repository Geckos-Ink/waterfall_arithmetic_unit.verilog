// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_de0_nano_top (
    input wire CLOCK_50,
    input wire [1:0] KEY,
    input wire [3:0] SW,
    output wire [7:0] LED
);
    localparam DATA_WIDTH = `WAU_DATA_WIDTH;
    localparam FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH;

    wire rst_n;
    assign rst_n = KEY[0];

    reg host_in_valid;
    wire host_in_ready;
    reg [FLOW_ID_WIDTH-1:0] host_in_flow_id;
    reg signed [DATA_WIDTH-1:0] host_in_a;
    reg signed [DATA_WIDTH-1:0] host_in_b;

    wire host_out_valid;
    wire [FLOW_ID_WIDTH-1:0] host_out_flow_id;
    wire signed [DATA_WIDTH-1:0] host_out_value;

    reg key1_d;
    reg pending_fire;
    reg signed [DATA_WIDTH-1:0] last_out;
    reg [FLOW_ID_WIDTH-1:0] last_flow;

    wire trigger_pulse;
    assign trigger_pulse = key1_d && !KEY[1];

    always @(posedge CLOCK_50 or negedge rst_n) begin
        if (!rst_n) begin
            host_in_valid <= 1'b0;
            host_in_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            host_in_a <= {DATA_WIDTH{1'b0}};
            host_in_b <= {DATA_WIDTH{1'b0}};
            key1_d <= 1'b1;
            pending_fire <= 1'b0;
            last_out <= {DATA_WIDTH{1'b0}};
            last_flow <= {FLOW_ID_WIDTH{1'b0}};
        end else begin
            key1_d <= KEY[1];

            if (trigger_pulse) begin
                host_in_flow_id <= {{(FLOW_ID_WIDTH-4){1'b0}}, SW};
                host_in_a <= {{(DATA_WIDTH-4){1'b0}}, SW};
                host_in_b <= {{(DATA_WIDTH-2){1'b0}}, 2'd3};
                pending_fire <= 1'b1;
            end

            if (pending_fire && host_in_ready && !host_in_valid) begin
                host_in_valid <= 1'b1;
                pending_fire <= 1'b0;
            end

            if (host_in_valid && host_in_ready) begin
                host_in_valid <= 1'b0;
            end

            if (host_out_valid) begin
                last_out <= host_out_value;
                last_flow <= host_out_flow_id;
            end
        end
    end

    assign LED[0] = host_out_valid;
    assign LED[1] = host_in_ready;
    assign LED[2] = pending_fire;
    assign LED[3] = SW[0];
    assign LED[7:4] = last_out[3:0];

    wau_top wau_u (
        .clk(CLOCK_50),
        .rst_n(rst_n),
        .host_in_valid(host_in_valid),
        .host_in_ready(host_in_ready),
        .host_in_flow_id(host_in_flow_id),
        .host_in_a(host_in_a),
        .host_in_b(host_in_b),
        .host_out_valid(host_out_valid),
        .host_out_ready(1'b1),
        .host_out_flow_id(host_out_flow_id),
        .host_out_value(host_out_value),
        .enable_auto_adapt(SW[0])
    );
endmodule
