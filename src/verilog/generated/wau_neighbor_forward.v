// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_neighbor_forward #(
    parameter CORE_ID_WIDTH = 8,
    parameter PAYLOAD_WIDTH = 64
) (
    input wire in_valid,
    output wire in_ready,
    input wire [CORE_ID_WIDTH-1:0] in_dst,
    input wire [PAYLOAD_WIDTH-1:0] in_payload,

    output wire out_valid,
    input wire out_ready,
    output wire [CORE_ID_WIDTH-1:0] out_dst,
    output wire [PAYLOAD_WIDTH-1:0] out_payload
);
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign out_dst = in_dst;
    assign out_payload = in_payload;
endmodule
