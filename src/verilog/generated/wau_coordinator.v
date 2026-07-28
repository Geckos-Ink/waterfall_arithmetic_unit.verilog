// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_coordinator #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CORE_COUNT = `WAU_CORE_COUNT
) (
    input wire clk,
    input wire rst_n,

    input wire host_in_valid,
    output wire host_in_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_in_a,
    input wire signed [DATA_WIDTH-1:0] host_in_b,

    output reg host_out_valid,
    input wire host_out_ready,
    output reg [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    output reg signed [DATA_WIDTH-1:0] host_out_value,

    input wire enable_auto_adapt,

    output reg dispatch_pkt_valid,
    input wire dispatch_pkt_ready,
    output reg [7:0] dispatch_pkt_dst_core,
    output reg [FLOW_ID_WIDTH-1:0] dispatch_pkt_flow_id,
    output reg [OPCODE_WIDTH-1:0] dispatch_pkt_opcode,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_a,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_b,
    output reg dispatch_pkt_use_immediate,
    output reg signed [DATA_WIDTH-1:0] dispatch_pkt_immediate_b,
    output reg [7:0] dispatch_pkt_stage_id,

    input wire result_pkt_valid,
    output wire result_pkt_ready,
    input wire [7:0] result_pkt_src_core,
    input wire [FLOW_ID_WIDTH-1:0] result_pkt_flow_id,
    input wire [7:0] result_pkt_stage_id,
    input wire signed [DATA_WIDTH-1:0] result_pkt_value,

    input wire [CORE_COUNT-1:0] core_busy
);
    localparam MAX_IN_FLIGHT = `WAU_COORD_MAX_IN_FLIGHT;

    // Multi-issue slot table. Each slot holds one in-flight flow's accumulator
    // context; the coordinator keeps up to MAX_IN_FLIGHT *distinct* flows
    // executing concurrently across the core mesh. Per-flow semantics are
    // identical to the legacy serial coordinator (one accumulator chain walked
    // stage-by-stage), so a single in-flight flow keeps cycle-identical timing
    // while independent flows now overlap on different cores.
    reg                          slot_valid     [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_flow_slot [0:MAX_IN_FLIGHT-1];
    reg [FLOW_ID_WIDTH-1:0]      slot_flow_id   [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_stage     [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_acc       [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_opb       [0:MAX_IN_FLIGHT-1];
    reg                          slot_awaiting  [0:MAX_IN_FLIGHT-1];
    reg [7:0]                    slot_wait_core [0:MAX_IN_FLIGHT-1];
    reg                          slot_done      [0:MAX_IN_FLIGHT-1];
    reg signed [DATA_WIDTH-1:0]  slot_outval    [0:MAX_IN_FLIGHT-1];

    // Combinational selections, driven by the always @(*) blocks below.
    reg        disp_found;
    reg [7:0]  disp_slot;
    reg [7:0]  disp_core;
    reg        res_found;
    reg [7:0]  res_slot;
    reg        out_found;
    reg [7:0]  out_slot;
    reg        alloc_found;
    reg [7:0]  alloc_slot;
    reg        flow_busy;

    // Arbiter scratch.
    reg [7:0]  cc_p;
    reg [7:0]  cc_f;
    reg [7:0]  cc;
    reg        any_found;
    reg [7:0]  any_slot;
    reg [7:0]  any_core;
    reg [7:0]  sel_flow_slot;
    reg [7:0]  sel_stage;
    integer    di;
    integer    ri;
    integer    oi;
    integer    ai;
    integer    fi;
    integer    k;
    reg        latched_now;

    // Accept a new flow when a slot is free and that flow id is not already in
    // flight (keeps result matching by flow+stage unambiguous without widening
    // the dispatch/result packet format with a tag).
    assign host_in_ready = alloc_found && !flow_busy;
    assign result_pkt_ready = res_found;

    function [7:0] flow_slot_from_id;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        reg [7:0] value;
        begin
            value = 8'hFF;
            case (flow_id)
                12'd1: value = 8'd0;
                12'd2: value = 8'd1;
                default: value = 8'hFF;
            endcase
            flow_slot_from_id = value;
        end
    endfunction

    function [7:0] flow_last_stage;
        input [7:0] flow_slot;
        reg [7:0] value;
        begin
            value = 8'd0;
            case (flow_slot)
                8'd0: value = 8'd2;
                8'd1: value = 8'd1;
                default: value = 8'd0;
            endcase
            flow_last_stage = value;
        end
    endfunction

    function [OPCODE_WIDTH-1:0] flow_stage_opcode;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [OPCODE_WIDTH-1:0] value;
        begin
            value = {OPCODE_WIDTH{1'b0}};
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'h01;
                16'h0001: value = 8'h03;
                16'h0002: value = 8'h02;
                16'h0100: value = 8'h07;
                16'h0101: value = 8'h04;
                default: value = {OPCODE_WIDTH{1'b0}};
            endcase
            flow_stage_opcode = value;
        end
    endfunction

    function [7:0] flow_stage_primary_core;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [7:0] value;
        begin
            value = 8'd0;
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'd0;
                16'h0001: value = 8'd1;
                16'h0002: value = 8'd2;
                16'h0100: value = 8'd5;
                16'h0101: value = 8'd2;
                default: value = 8'd0;
            endcase
            flow_stage_primary_core = value;
        end
    endfunction

    function [7:0] flow_stage_fallback_core;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg [7:0] value;
        begin
            value = 8'd0;
            case ({flow_slot, stage_idx})
                16'h0000: value = 8'd3;
                16'h0001: value = 8'd4;
                16'h0002: value = 8'd2;
                16'h0100: value = 8'd2;
                16'h0101: value = 8'd1;
                default: value = 8'd0;
            endcase
            flow_stage_fallback_core = value;
        end
    endfunction

    function flow_stage_use_immediate;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg value;
        begin
            value = 1'b0;
            case ({flow_slot, stage_idx})
                16'h0000: value = 1'b0;
                16'h0001: value = 1'b1;
                16'h0002: value = 1'b0;
                16'h0100: value = 1'b0;
                16'h0101: value = 1'b0;
                default: value = 1'b0;
            endcase
            flow_stage_use_immediate = value;
        end
    endfunction

    function signed [DATA_WIDTH-1:0] flow_stage_immediate_b;
        input [7:0] flow_slot;
        input [7:0] stage_idx;
        reg signed [DATA_WIDTH-1:0] value;
        begin
            value = {DATA_WIDTH{1'b0}};
            case ({flow_slot, stage_idx})
                16'h0000: value = 32'sd0;
                16'h0001: value = 32'sd3;
                16'h0002: value = 32'sd0;
                16'h0100: value = 32'sd0;
                16'h0101: value = 32'sd0;
                default: value = {DATA_WIDTH{1'b0}};
            endcase
            flow_stage_immediate_b = value;
        end
    endfunction

    // ---- dispatch arbiter + packet drive -------------------------------
    // Pick a dispatchable slot (valid, not awaiting a result, not finished),
    // preferring one whose chosen core is currently free so independent flows
    // light up different cores in the same cycle window. One dispatch is issued
    // per cycle, but many slots can be awaiting results at once -> real overlap.
    always @(*) begin
        disp_found = 1'b0;
        disp_slot  = 8'd0;
        disp_core  = 8'd0;
        any_found  = 1'b0;
        any_slot   = 8'd0;
        any_core   = 8'd0;
        for (di = 0; di < MAX_IN_FLIGHT; di = di + 1) begin
            if (slot_valid[di] && !slot_awaiting[di] && !slot_done[di] &&
                (slot_flow_slot[di] != 8'hFF)) begin
                cc_p = flow_stage_primary_core(slot_flow_slot[di], slot_stage[di]);
                cc_f = flow_stage_fallback_core(slot_flow_slot[di], slot_stage[di]);
                cc = cc_p;
                if (enable_auto_adapt && (cc_f != cc_p) &&
                    core_busy[cc_p] && !core_busy[cc_f]) begin
                    cc = cc_f;
                end
                if (!any_found) begin
                    any_found = 1'b1;
                    any_slot  = di;
                    any_core  = cc;
                end
                if (!disp_found && !core_busy[cc]) begin
                    disp_found = 1'b1;
                    disp_slot  = di;
                    disp_core  = cc;
                end
            end
        end
        if (!disp_found && any_found) begin
            disp_found = 1'b1;
            disp_slot  = any_slot;
            disp_core  = any_core;
        end

        sel_flow_slot = slot_flow_slot[disp_slot];
        sel_stage     = slot_stage[disp_slot];

        dispatch_pkt_valid         = disp_found;
        dispatch_pkt_dst_core      = disp_core;
        dispatch_pkt_flow_id       = slot_flow_id[disp_slot];
        dispatch_pkt_opcode        = flow_stage_opcode(sel_flow_slot, sel_stage);
        dispatch_pkt_a             = slot_acc[disp_slot];
        dispatch_pkt_b             = slot_opb[disp_slot];
        dispatch_pkt_use_immediate = flow_stage_use_immediate(sel_flow_slot, sel_stage);
        dispatch_pkt_immediate_b   = flow_stage_immediate_b(sel_flow_slot, sel_stage);
        dispatch_pkt_stage_id      = sel_stage;
    end

    // ---- result matcher: map an incoming result to its awaiting slot ----
    // Matched by flow_id and a stage_id that has reached or passed the slot's
    // own bookkeeping (not exact-equality, and not also src_core) so that a
    // chain of per-core fast-path hops (see wau_core_station) can run ahead
    // of this coordinator's stage counter without ever routing back to it:
    // the eventual hub-bound packet (always the flow's last stage -- see
    // compiler.build_fast_path_tables, which never gives a last stage a
    // fast-path entry) is still recognized and jumps slot_stage to wherever
    // the chain actually got to. flow_id alone is already the sole *required*
    // disambiguator (at most one in-flight slot per flow id), so this is a
    // strict superset of the old exact match: with an empty fast-path table
    // every stage still round-trips one at a time and
    // result_pkt_stage_id == slot_stage[ri] always, making this
    // byte-identical to before. slot_wait_core stays populated for
    // debug/trace but is no longer part of the match.
    always @(*) begin
        res_found = 1'b0;
        res_slot  = 8'd0;
        for (ri = 0; ri < MAX_IN_FLIGHT; ri = ri + 1) begin
            if (!res_found && slot_valid[ri] && slot_awaiting[ri] &&
                (slot_flow_id[ri] == result_pkt_flow_id) &&
                (result_pkt_stage_id >= slot_stage[ri])) begin
                res_found = 1'b1;
                res_slot  = ri;
            end
        end
    end

    // ---- completed-output selector + free-slot/flow guards -------------
    always @(*) begin
        out_found = 1'b0;
        out_slot  = 8'd0;
        for (oi = 0; oi < MAX_IN_FLIGHT; oi = oi + 1) begin
            if (!out_found && slot_valid[oi] && slot_done[oi]) begin
                out_found = 1'b1;
                out_slot  = oi;
            end
        end
    end

    always @(*) begin
        alloc_found = 1'b0;
        alloc_slot  = 8'd0;
        for (ai = 0; ai < MAX_IN_FLIGHT; ai = ai + 1) begin
            if (!alloc_found && !slot_valid[ai]) begin
                alloc_found = 1'b1;
                alloc_slot  = ai;
            end
        end
    end

    always @(*) begin
        flow_busy = 1'b0;
        for (fi = 0; fi < MAX_IN_FLIGHT; fi = fi + 1) begin
            if (slot_valid[fi] && (slot_flow_id[fi] == host_in_flow_id)) begin
                flow_busy = 1'b1;
            end
        end
    end

    // ---- sequential state ----------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            host_out_valid   <= 1'b0;
            host_out_flow_id <= {FLOW_ID_WIDTH{1'b0}};
            host_out_value   <= {DATA_WIDTH{1'b0}};
            for (k = 0; k < MAX_IN_FLIGHT; k = k + 1) begin
                slot_valid[k]     <= 1'b0;
                slot_flow_slot[k] <= 8'hFF;
                slot_flow_id[k]   <= {FLOW_ID_WIDTH{1'b0}};
                slot_stage[k]     <= 8'd0;
                slot_acc[k]       <= {DATA_WIDTH{1'b0}};
                slot_opb[k]       <= {DATA_WIDTH{1'b0}};
                slot_awaiting[k]  <= 1'b0;
                slot_wait_core[k] <= 8'd0;
                slot_done[k]      <= 1'b0;
                slot_outval[k]    <= {DATA_WIDTH{1'b0}};
            end
        end else begin
            latched_now = 1'b0;

            // drain a consumed output
            if (host_out_valid && host_out_ready) begin
                host_out_valid <= 1'b0;
            end

            // accept a new flow into a free slot; unknown flow ids are consumed
            // but never allocated -> silently dropped (legacy behaviour).
            if (host_in_valid && host_in_ready && alloc_found) begin
                if (flow_slot_from_id(host_in_flow_id) != 8'hFF) begin
                    slot_valid[alloc_slot]     <= 1'b1;
                    slot_flow_slot[alloc_slot] <= flow_slot_from_id(host_in_flow_id);
                    slot_flow_id[alloc_slot]   <= host_in_flow_id;
                    slot_stage[alloc_slot]     <= 8'd0;
                    slot_acc[alloc_slot]       <= host_in_a;
                    slot_opb[alloc_slot]       <= host_in_b;
                    slot_awaiting[alloc_slot]  <= 1'b0;
                    slot_done[alloc_slot]      <= 1'b0;
                end
            end

            // dispatch handshake: mark the issued slot awaiting its core result
            if (dispatch_pkt_valid && dispatch_pkt_ready && disp_found) begin
                slot_awaiting[disp_slot]  <= 1'b1;
                slot_wait_core[disp_slot] <= disp_core;
            end

            // result handshake: advance the matched slot's accumulator chain
            if (result_pkt_valid && result_pkt_ready && res_found) begin
                slot_awaiting[res_slot] <= 1'b0;
                if (result_pkt_stage_id >= flow_last_stage(slot_flow_slot[res_slot])) begin
                    // final stage: latch output immediately when the port is
                    // free (keeps single-flow latency identical to the serial
                    // design); otherwise buffer it in the slot until it drains.
                    if (!host_out_valid || host_out_ready) begin
                        host_out_valid       <= 1'b1;
                        host_out_flow_id     <= slot_flow_id[res_slot];
                        host_out_value       <= result_pkt_value;
                        slot_valid[res_slot] <= 1'b0;
                        slot_done[res_slot]  <= 1'b0;
                        latched_now = 1'b1;
                    end else begin
                        slot_done[res_slot]   <= 1'b1;
                        slot_outval[res_slot] <= result_pkt_value;
                    end
                end else begin
                    slot_acc[res_slot]   <= result_pkt_value;
                    slot_stage[res_slot] <= result_pkt_stage_id + 8'd1;
                end
            end

            // drain a previously-buffered completed slot when the port is free
            if (!latched_now && (!host_out_valid || host_out_ready) && out_found) begin
                host_out_valid       <= 1'b1;
                host_out_flow_id     <= slot_flow_id[out_slot];
                host_out_value       <= slot_outval[out_slot];
                slot_valid[out_slot] <= 1'b0;
                slot_done[out_slot]  <= 1'b0;
            end
        end
    end
endmodule
