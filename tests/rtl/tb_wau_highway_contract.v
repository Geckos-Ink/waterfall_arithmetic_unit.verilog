`timescale 1ns/1ps
`include "wau_defs.vh"

// Owns the highway contracting-bus contract:
//
//   1. the bus offers one core slot per clock while no contract is in force;
//   2. a bare request bit on the offered slot ("pong") wins a single beat;
//   3. a contract word wins the highway *exclusively* for its whole duration --
//      other cores are deferred, so a contracted transfer never interleaves and
//      never re-waits for its turn;
//   4. every contract is bounded twice, by its beat count and by a hard lease,
//      so a holder that goes quiet can never wedge the highway;
//   5. the round-robin resumes *after* the holder, so one core cannot starve
//      the others by re-contracting immediately.
module tb_wau_highway_contract;
    localparam CORE_COUNT = 4;
    localparam CORE_ID_WIDTH = 8;
    localparam WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH;
    localparam LEASE_CYCLES = 12;

    localparam [1:0] MODE_PONG    = 2'd0;
    localparam [1:0] MODE_BURST   = 2'd1;
    localparam [1:0] MODE_STREAM  = 2'd2;
    localparam [1:0] MODE_RESERVE = 2'd3;

    reg clk;
    reg rst_n;

    reg [CORE_COUNT-1:0] req;
    reg [CORE_COUNT*WORD_WIDTH-1:0] word;
    reg [CORE_COUNT-1:0] pending;
    reg [CORE_COUNT-1:0] accepted;

    wire [CORE_COUNT-1:0] admit;
    wire [CORE_COUNT-1:0] call;
    wire [CORE_ID_WIDTH-1:0] slot;
    wire grant_valid;
    wire [CORE_ID_WIDTH-1:0] grant_core;
    wire [1:0] grant_mode;
    wire [15:0] grant_remaining;
    wire [15:0] grant_lease;
    wire [31:0] grant_count;
    wire [31:0] hold_cycles;
    wire [31:0] defer_count;

    always #5 clk = ~clk;

    wau_highway_contract #(
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .WORD_WIDTH(WORD_WIDTH),
        .MAX_BURST(8),
        .LEASE_CYCLES(LEASE_CYCLES)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .req(req),
        .word(word),
        .pending(pending),
        .accepted(accepted),
        .admit(admit),
        .call(call),
        .slot(slot),
        .grant_valid(grant_valid),
        .grant_core(grant_core),
        .grant_mode(grant_mode),
        .grant_remaining(grant_remaining),
        .grant_lease(grant_lease),
        .grant_count(grant_count),
        .hold_cycles(hold_cycles),
        .defer_count(defer_count)
    );

    // Build a contract word: "how" (mode), "how much" (words per run) and
    // "how many times" (repeats).
    function [WORD_WIDTH-1:0] make_word;
        input [1:0] mode;
        input [7:0] words_per_run;
        input [7:0] repeats;
        begin
            make_word = {repeats, words_per_run, mode};
        end
    endfunction

    task automatic set_word;
        input integer core;
        input [1:0] mode;
        input [7:0] words_per_run;
        input [7:0] repeats;
        begin
            word[(core*WORD_WIDTH) +: WORD_WIDTH] = make_word(mode, words_per_run, repeats);
        end
    endtask

    task automatic reset_inputs;
        begin
            req = {CORE_COUNT{1'b0}};
            pending = {CORE_COUNT{1'b0}};
            accepted = {CORE_COUNT{1'b0}};
        end
    endtask

    // Wait until the round-robin is offering `target`'s slot, so the test drives
    // the bus the way a core actually would: only answer when asked.
    task automatic wait_for_slot;
        input integer target;
        integer guard;
        begin
            guard = 0;
            while (grant_valid || (slot !== target[CORE_ID_WIDTH-1:0])) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (guard > 200) begin
                    $display("FAIL: slot %0d was never offered", target);
                    $fatal(1);
                end
            end
        end
    endtask

    integer i;
    integer cycles;
    integer seen_slots;
    reg [CORE_COUNT-1:0] slots_seen;
    reg [31:0] defer_before;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        word = {(CORE_COUNT*WORD_WIDTH){1'b0}};
        reset_inputs();
        for (i = 0; i < CORE_COUNT; i = i + 1) begin
            set_word(i, MODE_PONG, 8'd1, 8'd1);
        end

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        #1;

        // ---- 1. the slot index cycles one core per clock while idle ----
        slots_seen = {CORE_COUNT{1'b0}};
        for (i = 0; i < CORE_COUNT * 2; i = i + 1) begin
            slots_seen[slot] = 1'b1;
            @(posedge clk);
            #1;
        end
        if (slots_seen !== {CORE_COUNT{1'b1}}) begin
            $display("FAIL: idle bus did not offer every core slot (saw %b)", slots_seen);
            $fatal(1);
        end
        // An idle bus must not gate anybody.
        if (admit !== {CORE_COUNT{1'b1}}) begin
            $display("FAIL: idle bus is gating injection (admit=%b)", admit);
            $fatal(1);
        end

        // ---- 2. bare request bit on the offered slot == a single-beat grant ----
        set_word(2, MODE_PONG, 8'd1, 8'd1);
        wait_for_slot(2);
        req[2] = 1'b1;
        pending[2] = 1'b1;
        #1;
        if (call[2] !== 1'b1) begin
            $display("FAIL: request on core 2's own slot did not raise call[2]");
            $fatal(1);
        end
        @(posedge clk);
        #1;
        if (grant_valid !== 1'b1 || grant_core !== 8'd2) begin
            $display("FAIL: pong request did not win the highway (v=%0d core=%0d)",
                grant_valid, grant_core);
            $fatal(1);
        end
        if (grant_mode !== MODE_PONG || grant_remaining !== 16'd1) begin
            $display("FAIL: pong grant is not a single beat (mode=%0d rem=%0d)",
                grant_mode, grant_remaining);
            $fatal(1);
        end
        // Consume the beat; the grant must fall away right after.
        accepted[2] = 1'b1;
        @(posedge clk);
        #1;
        reset_inputs();
        if (grant_valid !== 1'b0) begin
            $display("FAIL: pong grant outlived its single beat");
            $fatal(1);
        end

        // ---- 3. a contract owns the highway exclusively for its whole run ----
        // core 1 asks for 3 words x 2 repeats = 6 beats.
        set_word(1, MODE_STREAM, 8'd3, 8'd2);
        wait_for_slot(1);
        req[1] = 1'b1;
        pending[1] = 1'b1;
        @(posedge clk);
        #1;
        if (grant_valid !== 1'b1 || grant_core !== 8'd1) begin
            $display("FAIL: contract did not take the highway");
            $fatal(1);
        end
        if (grant_remaining !== 16'd6) begin
            $display("FAIL: stream contract beats = %0d, expected 6", grant_remaining);
            $fatal(1);
        end

        // Everyone else now presents traffic and must be held off, while the
        // holder is never asked to wait for its slot again.
        pending = {CORE_COUNT{1'b1}};
        #1;
        if (admit !== (1 << 1)) begin
            $display("FAIL: contract is not exclusive (admit=%b)", admit);
            $fatal(1);
        end
        defer_before = defer_count;

        // Push the six beats back to back.
        for (i = 0; i < 6; i = i + 1) begin
            if (grant_valid !== 1'b1 || grant_core !== 8'd1) begin
                $display("FAIL: contract released early at beat %0d", i);
                $fatal(1);
            end
            accepted = (1 << 1);
            @(posedge clk);
            #1;
            accepted = {CORE_COUNT{1'b0}};
        end
        if (grant_valid !== 1'b0) begin
            $display("FAIL: contract outlived its %0d beats", 6);
            $fatal(1);
        end
        if (defer_count <= defer_before) begin
            $display("FAIL: deferred cores were not counted");
            $fatal(1);
        end
        // The round-robin resumes after the holder, not on it.
        if (slot === 8'd1) begin
            $display("FAIL: released bus re-offered the holder's own slot");
            $fatal(1);
        end
        reset_inputs();

        // ---- 4. a holder that stops presenting releases immediately ----
        set_word(3, MODE_RESERVE, 8'd1, 8'd1);
        wait_for_slot(3);
        req[3] = 1'b1;
        pending[3] = 1'b1;
        @(posedge clk);
        #1;
        if (grant_valid !== 1'b1 || grant_core !== 8'd3) begin
            $display("FAIL: reserve contract did not take the highway");
            $fatal(1);
        end
        reset_inputs();
        @(posedge clk);
        #1;
        if (grant_valid !== 1'b0) begin
            $display("FAIL: a quiet holder kept the highway");
            $fatal(1);
        end

        // ---- 5. the lease bounds a holder that never completes its beats ----
        set_word(0, MODE_RESERVE, 8'd1, 8'd1);
        wait_for_slot(0);
        req[0] = 1'b1;
        pending[0] = 1'b1;
        @(posedge clk);
        #1;
        if (grant_valid !== 1'b1 || grant_core !== 8'd0) begin
            $display("FAIL: lease test could not take the highway");
            $fatal(1);
        end
        // Keep asking, never send a beat: only the lease can end this.
        cycles = 0;
        while (grant_valid && cycles < (LEASE_CYCLES * 4)) begin
            @(posedge clk);
            #1;
            cycles = cycles + 1;
        end
        if (grant_valid !== 1'b0) begin
            $display("FAIL: lease never expired -- the highway is wedgeable");
            $fatal(1);
        end
        if (cycles < LEASE_CYCLES) begin
            $display("FAIL: lease expired after %0d cycles, expected >= %0d",
                cycles, LEASE_CYCLES);
            $fatal(1);
        end
        reset_inputs();
        @(posedge clk);

        if (grant_count < 32'd4) begin
            $display("FAIL: grant_count = %0d, expected at least 4", grant_count);
            $fatal(1);
        end
        if (hold_cycles == 32'd0) begin
            $display("FAIL: hold_cycles never advanced");
            $fatal(1);
        end

        $display("PASS: tb_wau_highway_contract (grants=%0d hold=%0d defers=%0d)",
            grant_count, hold_cycles, defer_count);
        $finish;
    end
endmodule
