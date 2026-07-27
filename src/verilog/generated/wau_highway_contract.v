// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_highway_contract #(
    parameter CORE_COUNT = `WAU_CORE_COUNT,
    parameter CORE_ID_WIDTH = 8,
    parameter WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH,
    parameter MAX_BURST = `WAU_HIGHWAY_CONTRACT_MAX_BURST,
    parameter LEASE_CYCLES = `WAU_HIGHWAY_CONTRACT_LEASE_CYCLES
) (
    input wire clk,
    input wire rst_n,

    // Real-time side: a core raises `req` when it actually wants the highway.
    input wire [CORE_COUNT-1:0] req,
    // Program side: the expectation the offline schedule derived for that core.
    input wire [CORE_COUNT*WORD_WIDTH-1:0] word,
    // Highway side: who is presenting, and whose presentation was taken.
    input wire [CORE_COUNT-1:0] pending,
    input wire [CORE_COUNT-1:0] accepted,

    // Injection mask applied by the mesh, and the per-core "called the
    // highway" pulse the viewer renders.
    output wire [CORE_COUNT-1:0] admit,
    output wire [CORE_COUNT-1:0] call,

    output reg [CORE_ID_WIDTH-1:0] slot,
    output reg grant_valid,
    output reg [CORE_ID_WIDTH-1:0] grant_core,
    output reg [1:0] grant_mode,
    output reg [15:0] grant_remaining,
    output reg [15:0] grant_lease,

    output reg [31:0] grant_count,
    output reg [31:0] hold_cycles,
    output reg [31:0] defer_count
);
    localparam [1:0] MODE_PONG    = 2'd0;
    localparam [1:0] MODE_BURST   = 2'd1;
    localparam [1:0] MODE_STREAM  = 2'd2;
    localparam [1:0] MODE_RESERVE = 2'd3;

    localparam integer MODE_LSB    = 0;
    localparam integer WORDS_LSB   = 2;
    localparam integer REPEATS_LSB = 10;

    localparam [CORE_ID_WIDTH-1:0] LAST_SLOT = CORE_COUNT - 1;
    localparam [7:0] MAX_BURST_BEATS = MAX_BURST;
    localparam [15:0] LEASE_INIT = LEASE_CYCLES;

    // With no contract in force the highway stays wide open, so an idle bus
    // adds no admission latency; a contract narrows it to its holder alone.
    wire [CORE_COUNT-1:0] grant_mask;
    genvar gi;
    generate
        for (gi = 0; gi < CORE_COUNT; gi = gi + 1) begin : gen_slot
            assign grant_mask[gi] = (grant_core == gi[CORE_ID_WIDTH-1:0]);
            assign call[gi] = (!grant_valid) && (slot == gi[CORE_ID_WIDTH-1:0]) && req[gi];
        end
    endgenerate
    assign admit = grant_valid ? grant_mask : {CORE_COUNT{1'b1}};

    wire [WORD_WIDTH-1:0] slot_word = word[(slot*WORD_WIDTH) +: WORD_WIDTH];
    wire [1:0] slot_mode = slot_word[MODE_LSB +: 2];
    wire [7:0] slot_words = slot_word[WORDS_LSB +: 8];
    wire [7:0] slot_repeats = slot_word[REPEATS_LSB +: 8];

    // "how much" per run and "how many times", clamped to what was synthesised.
    wire [7:0] eff_words = (slot_words == 8'd0)
        ? 8'd1
        : ((slot_words > MAX_BURST_BEATS) ? MAX_BURST_BEATS : slot_words);
    wire [7:0] eff_repeats = (slot_repeats == 8'd0) ? 8'd1 : slot_repeats;
    wire [15:0] stream_beats = eff_words * eff_repeats;

    reg [15:0] beats_next;
    always @(*) begin
        case (slot_mode)
            MODE_PONG:   beats_next = 16'd1;
            MODE_BURST:  beats_next = {8'd0, eff_words};
            MODE_STREAM: beats_next = stream_beats;
            // RESERVE holds the highway for the whole lease regardless of how
            // many beats actually flow.
            default:     beats_next = 16'hFFFF;
        endcase
    end

    wire holder_beat = grant_valid && accepted[grant_core];
    wire contract_done = holder_beat && (grant_remaining <= 16'd1);
    wire lease_expired = grant_valid && (grant_lease == 16'd0);
    // A holder that has gone quiet releases immediately rather than sitting on
    // the highway until its lease runs out.
    wire holder_idle = grant_valid && !pending[grant_core] && !req[grant_core];
    wire release_now = contract_done || lease_expired || holder_idle;

    // Cores that presented traffic but were held off by someone else's
    // contract this cycle.
    reg [CORE_ID_WIDTH:0] deferred_now;
    integer di;
    always @(*) begin
        deferred_now = {(CORE_ID_WIDTH+1){1'b0}};
        for (di = 0; di < CORE_COUNT; di = di + 1) begin
            if (pending[di] && !admit[di]) begin
                deferred_now = deferred_now + 1'b1;
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            slot <= {CORE_ID_WIDTH{1'b0}};
            grant_valid <= 1'b0;
            grant_core <= {CORE_ID_WIDTH{1'b0}};
            grant_mode <= MODE_PONG;
            grant_remaining <= 16'd0;
            grant_lease <= 16'd0;
            grant_count <= 32'd0;
            hold_cycles <= 32'd0;
            defer_count <= 32'd0;
        end else begin
            defer_count <= defer_count + {{(31-CORE_ID_WIDTH){1'b0}}, deferred_now};

            if (!grant_valid) begin
                if (req[slot]) begin
                    grant_valid <= 1'b1;
                    grant_core <= slot;
                    grant_mode <= slot_mode;
                    grant_remaining <= beats_next;
                    grant_lease <= LEASE_INIT;
                    grant_count <= grant_count + 32'd1;
                end else begin
                    // Nothing asked on this slot: offer the next one.
                    slot <= (slot == LAST_SLOT) ? {CORE_ID_WIDTH{1'b0}} : slot + 1'b1;
                end
            end else begin
                hold_cycles <= hold_cycles + 32'd1;
                if (grant_lease != 16'd0) begin
                    grant_lease <= grant_lease - 16'd1;
                end
                if (holder_beat && (grant_remaining != 16'd0)) begin
                    grant_remaining <= grant_remaining - 16'd1;
                end
                if (release_now) begin
                    grant_valid <= 1'b0;
                    // Resume the round-robin *after* the holder so a contracted
                    // core cannot immediately re-take the highway.
                    slot <= (grant_core == LAST_SLOT)
                        ? {CORE_ID_WIDTH{1'b0}}
                        : grant_core + 1'b1;
                end
            end
        end
    end
endmodule
