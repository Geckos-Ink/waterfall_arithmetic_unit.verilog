// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.

`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_top #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
    parameter GRID_Z = `WAU_GRID_Z,
    parameter CORE_COUNT = `WAU_CORE_COUNT
) (
    input wire clk,
    input wire rst_n,

    input wire host_in_valid,
    output wire host_in_ready,
    input wire [FLOW_ID_WIDTH-1:0] host_in_flow_id,
    input wire signed [DATA_WIDTH-1:0] host_in_a,
    input wire signed [DATA_WIDTH-1:0] host_in_b,

    output wire host_out_valid,
    input wire host_out_ready,
    output wire [FLOW_ID_WIDTH-1:0] host_out_flow_id,
    output wire signed [DATA_WIDTH-1:0] host_out_value,

    input wire enable_auto_adapt,

    output wire [31:0] obs_total_hop_count,
    output wire [31:0] obs_total_stall_count,
    output wire [31:0] obs_total_forward_count,
    output wire [31:0] obs_total_local_delivered_count,
    output wire [31:0] obs_total_cache_hit_count,
    output wire [31:0] obs_total_cache_lookup_count,

    // Highway contract bus observability (data-plane highway).
    output wire [31:0] obs_total_contract_grant_count,
    output wire [31:0] obs_total_contract_hold_cycles,
    output wire [31:0] obs_total_contract_defer_count
);
    localparam integer CORE_ID_WIDTH = 8;
    localparam integer COORDINATOR_CORE_INDEX = 0;
    localparam integer CONTRACT_WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH;
    localparam integer CONTRACT_BUS_ENABLE = 1;

    // Highway geometry: how many independent highways the fabric has and how
    // many cores share one. `lines` gives a highway per row; chain/matrix have
    // a single highway spanning the whole grid.
    localparam integer HIGHWAY_LINE_COUNT = `WAU_HIGHWAY_LINE_COUNT;
    localparam integer HIGHWAY_LINE_SIZE = `WAU_HIGHWAY_LINE_SIZE;
    // Reserved destination meaning "leave this line" (see wau_highway_router).
    localparam [CORE_ID_WIDTH-1:0] HUB_DST = {CORE_ID_WIDTH{1'b1}};

    localparam integer CTRL_STAGE_LSB = 0;
    localparam integer CTRL_IMM_LSB = CTRL_STAGE_LSB + 8;
    localparam integer CTRL_USE_IMM_LSB = CTRL_IMM_LSB + DATA_WIDTH;
    localparam integer CTRL_B_LSB = CTRL_USE_IMM_LSB + 1;
    localparam integer CTRL_A_LSB = CTRL_B_LSB + DATA_WIDTH;
    localparam integer CTRL_OPCODE_LSB = CTRL_A_LSB + DATA_WIDTH;
    localparam integer CTRL_FLOW_ID_LSB = CTRL_OPCODE_LSB + OPCODE_WIDTH;
    localparam integer CTRL_PAYLOAD_WIDTH = CTRL_FLOW_ID_LSB + FLOW_ID_WIDTH;

    localparam integer DATA_FLOW_ID_LSB = 0;
    localparam integer DATA_STAGE_LSB = DATA_FLOW_ID_LSB + FLOW_ID_WIDTH;
    localparam integer DATA_VALUE_LSB = DATA_STAGE_LSB + 8;
    localparam integer DATA_SRC_CORE_LSB = DATA_VALUE_LSB + DATA_WIDTH;
    // Fast-path fields, appended after the original (unmoved) result fields
    // above -- coord_result_* keeps reading only those original ranges, so
    // the coordinator needs no awareness of this widening. Internal mesh
    // payload width, not part of the frozen host-facing ABI.
    localparam integer DATA_IS_FAST_PATH_LSB = DATA_SRC_CORE_LSB + CORE_ID_WIDTH;
    localparam integer DATA_NEXT_STAGE_LSB = DATA_IS_FAST_PATH_LSB + 1;
    localparam integer DATA_NEXT_OPCODE_LSB = DATA_NEXT_STAGE_LSB + 8;
    localparam integer DATA_NEXT_USE_IMM_LSB = DATA_NEXT_OPCODE_LSB + OPCODE_WIDTH;
    localparam integer DATA_NEXT_IMM_LSB = DATA_NEXT_USE_IMM_LSB + 1;
    localparam integer DATA_B_REG_LSB = DATA_NEXT_IMM_LSB + DATA_WIDTH;
    localparam integer DATA_PAYLOAD_WIDTH = DATA_B_REG_LSB + DATA_WIDTH;

    localparam integer STATION_PROGRAM_ENABLE = `WAU_STATION_PROGRAM_ENABLE;
    // Reserved destination meaning "route to the coordinator" for this
    // topology -- HUB_DST under `lines` (every core owns its own port, so a
    // fast-path hop leaves through its own line's hub like any other
    // off-line traffic), or core 0 under `chain`/`matrix` (the coordinator
    // shares that core's own local port -- see build_fast_path_tables'
    // excluded_destinations, which keeps a fast-path hop from ever being
    // routed there in the first place).
    localparam [CORE_ID_WIDTH-1:0] COORD_DST_SENTINEL = HUB_DST;

    wire [CORE_COUNT-1:0] core_dispatch_valid;
    wire [CORE_COUNT-1:0] core_dispatch_ready;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_dispatch_flow_id;
    wire [CORE_COUNT*OPCODE_WIDTH-1:0] core_dispatch_opcode;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_a;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_b;
    wire [CORE_COUNT-1:0] core_dispatch_use_immediate;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_immediate_b;
    wire [CORE_COUNT*8-1:0] core_dispatch_stage_id;

    wire [CORE_COUNT-1:0] core_result_valid;
    wire [CORE_COUNT-1:0] core_result_ready;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_result_flow_id;
    wire [CORE_COUNT*8-1:0] core_result_stage_id;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_result_value;
    wire [CORE_COUNT-1:0] core_busy;
    wire [CORE_COUNT-1:0] core_cache_hit;
    wire [CORE_COUNT*32-1:0] core_cache_hit_count;
    wire [CORE_COUNT*32-1:0] core_cache_lookup_count;

    // Fast-path result-side buses (see wau_core_station), fed from every
    // core's new result_* outputs and unpacked into the data-plane payload
    // by _CORE_DISPATCH_UNPACK.
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_result_b_reg;
    wire [CORE_COUNT-1:0] core_result_is_fast_path;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] core_result_dst_core;
    wire [CORE_COUNT*8-1:0] core_result_next_stage_id;
    wire [CORE_COUNT*OPCODE_WIDTH-1:0] core_result_next_opcode;
    wire [CORE_COUNT-1:0] core_result_next_use_immediate;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_result_next_immediate_b;

    // Fast-path self-dispatch buses: a peer core's fast-path hop, delivered
    // to this core via the data plane's local port instead of the
    // coordinator's control plane. See _CORE_DISPATCH_UNPACK for how these
    // are unpacked from data_local_out_*.
    wire [CORE_COUNT-1:0] core_self_dispatch_valid;
    wire [CORE_COUNT-1:0] core_self_dispatch_ready;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_self_dispatch_flow_id;
    wire [CORE_COUNT*OPCODE_WIDTH-1:0] core_self_dispatch_opcode;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_self_dispatch_a;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_self_dispatch_b_reg;
    wire [CORE_COUNT-1:0] core_self_dispatch_use_immediate;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_self_dispatch_immediate_b;
    wire [CORE_COUNT*8-1:0] core_self_dispatch_stage_id;

    wire coord_dispatch_valid;
    wire coord_dispatch_ready;
    wire [CORE_ID_WIDTH-1:0] coord_dispatch_dst_core;
    wire [FLOW_ID_WIDTH-1:0] coord_dispatch_flow_id;
    wire [OPCODE_WIDTH-1:0] coord_dispatch_opcode;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_a;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_b;
    wire coord_dispatch_use_immediate;
    wire signed [DATA_WIDTH-1:0] coord_dispatch_immediate_b;
    wire [7:0] coord_dispatch_stage_id;

    wire coord_result_valid;
    wire coord_result_ready;
    wire [CORE_ID_WIDTH-1:0] coord_result_src_core;
    wire [FLOW_ID_WIDTH-1:0] coord_result_flow_id;
    wire [7:0] coord_result_stage_id;
    wire signed [DATA_WIDTH-1:0] coord_result_value;

    wire [CORE_COUNT-1:0] ctrl_local_in_valid;
    wire [CORE_COUNT-1:0] ctrl_local_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] ctrl_local_in_dst;
    wire [CORE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_local_in_payload;
    wire [CORE_COUNT-1:0] ctrl_local_out_valid;
    wire [CORE_COUNT-1:0] ctrl_local_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] ctrl_local_out_dst;
    wire [CORE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_local_out_payload;

    wire [CORE_COUNT-1:0] data_local_in_valid;
    wire [CORE_COUNT-1:0] data_local_in_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] data_local_in_dst;
    wire [CORE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_local_in_payload;
    wire [CORE_COUNT-1:0] data_local_out_valid;
    wire [CORE_COUNT-1:0] data_local_out_ready;
    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] data_local_out_dst;
    wire [CORE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_local_out_payload;

    wau_coordinator #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH),
        .CORE_COUNT(CORE_COUNT)
    ) coordinator_u (
        .clk(clk),
        .rst_n(rst_n),
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
        .dispatch_pkt_valid(coord_dispatch_valid),
        .dispatch_pkt_ready(coord_dispatch_ready),
        .dispatch_pkt_dst_core(coord_dispatch_dst_core),
        .dispatch_pkt_flow_id(coord_dispatch_flow_id),
        .dispatch_pkt_opcode(coord_dispatch_opcode),
        .dispatch_pkt_a(coord_dispatch_a),
        .dispatch_pkt_b(coord_dispatch_b),
        .dispatch_pkt_use_immediate(coord_dispatch_use_immediate),
        .dispatch_pkt_immediate_b(coord_dispatch_immediate_b),
        .dispatch_pkt_stage_id(coord_dispatch_stage_id),
        .result_pkt_valid(coord_result_valid),
        .result_pkt_ready(coord_result_ready),
        .result_pkt_src_core(coord_result_src_core),
        .result_pkt_flow_id(coord_result_flow_id),
        .result_pkt_stage_id(coord_result_stage_id),
        .result_pkt_value(coord_result_value),
        .core_busy(core_busy)
    );

    // Per-line fabric binding. Every core uses its own local port for both
    // planes, and the coordinator sits on the hubs -- one per highway line --
    // rather than sharing core 0's port. Dispatch is steered to the hub that
    // owns the destination core, and results arrive back from whichever line
    // produced them.
    //
    // No core shares its local data-plane port with the coordinator here, so
    // every core's inbound data-plane traffic is genuinely its own
    // fast-path self-dispatch (see _CORE_DISPATCH_UNPACK).
    localparam CORE_SHARES_COORDINATOR_PORT = 1'b0;
    genvar core_i;
    generate
        for (core_i = 0; core_i < CORE_COUNT; core_i = core_i + 1) begin : gen_local_binding
            // Nothing injects control packets at a core; the coordinator hub does.
            assign ctrl_local_in_valid[core_i] = 1'b0;
            assign ctrl_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
            assign ctrl_local_in_payload[(core_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {CTRL_PAYLOAD_WIDTH{1'b0}};
            assign core_dispatch_valid[core_i] = ctrl_local_out_valid[core_i];
            assign ctrl_local_out_ready[core_i] = core_dispatch_ready[core_i];
            assign core_dispatch_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_FLOW_ID_LSB +: FLOW_ID_WIDTH];
            assign core_dispatch_opcode[(core_i*OPCODE_WIDTH) +: OPCODE_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_OPCODE_LSB +: OPCODE_WIDTH];
            assign core_dispatch_a[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_A_LSB +: DATA_WIDTH];
            assign core_dispatch_b[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_B_LSB +: DATA_WIDTH];
            assign core_dispatch_use_immediate[core_i] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_USE_IMM_LSB +: 1];
            assign core_dispatch_immediate_b[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_IMM_LSB +: DATA_WIDTH];
            assign core_dispatch_stage_id[(core_i*8) +: 8] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_STAGE_LSB +: 8];

            assign data_local_in_valid[core_i] = core_result_valid[core_i];
            assign core_result_ready[core_i] = data_local_in_ready[core_i];
            assign data_local_in_payload[(core_i*DATA_PAYLOAD_WIDTH) +: DATA_PAYLOAD_WIDTH] = {
                core_result_b_reg[(core_i*DATA_WIDTH) +: DATA_WIDTH],
                core_result_next_immediate_b[(core_i*DATA_WIDTH) +: DATA_WIDTH],
                core_result_next_use_immediate[core_i],
                core_result_next_opcode[(core_i*OPCODE_WIDTH) +: OPCODE_WIDTH],
                core_result_next_stage_id[(core_i*8) +: 8],
                core_result_is_fast_path[core_i],
                core_i[CORE_ID_WIDTH-1:0],
                core_result_value[(core_i*DATA_WIDTH) +: DATA_WIDTH],
                core_result_stage_id[(core_i*8) +: 8],
                core_result_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]
            };

            // Fast-path self-dispatch: unpack whatever the mesh delivers to
            // this core's local data-plane inbound port into a dispatch-
            // shaped bundle for wau_core's self_dispatch_* port. Structurally
            // inert while nothing ever routes here (today: while
            // WAU_STATION_PROGRAM_ENABLE is 0 everywhere). Under chain/matrix,
            // core 0's inbound data-plane traffic is always coordinator-bound
            // (CORE_SHARES_COORDINATOR_PORT), never a genuine self-dispatch --
            // forced to 0 here rather than risk misreading an ordinary
            // legacy/final-stage result as one. Each fabric-binding template
            // drives data_local_out_ready itself (it differs for that one
            // shared core), not here.
            assign core_self_dispatch_valid[core_i] =
                (CORE_SHARES_COORDINATOR_PORT && (core_i == COORDINATOR_CORE_INDEX))
                    ? 1'b0
                    : data_local_out_valid[core_i];
            assign core_self_dispatch_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_FLOW_ID_LSB +: FLOW_ID_WIDTH];
            assign core_self_dispatch_opcode[(core_i*OPCODE_WIDTH) +: OPCODE_WIDTH] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_NEXT_OPCODE_LSB +: OPCODE_WIDTH];
            assign core_self_dispatch_a[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_VALUE_LSB +: DATA_WIDTH];
            assign core_self_dispatch_b_reg[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_B_REG_LSB +: DATA_WIDTH];
            assign core_self_dispatch_use_immediate[core_i] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_NEXT_USE_IMM_LSB +: 1];
            assign core_self_dispatch_immediate_b[(core_i*DATA_WIDTH) +: DATA_WIDTH] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_NEXT_IMM_LSB +: DATA_WIDTH];
            assign core_self_dispatch_stage_id[(core_i*8) +: 8] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_NEXT_STAGE_LSB +: 8];
            // Each core's own result is routed to whatever
            // wau_core_station decided (COORD_DST_SENTINEL = HUB_DST for a
            // legacy/final-stage result, degenerating to exactly the old
            // hardwired behaviour when the fast-path table is empty; a real
            // core index for a fast-path hit, which walks west along its own
            // line and leaves through that line's hub -- or straight across
            // if the destination is on this same line -- exactly like any
            // other off-line traffic).
            assign data_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] =
                core_result_dst_core[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH];

            // Every core owns its local data-plane port exclusively under
            // `lines` (no coordinator-sharing), so a fast-path packet may
            // land on any core: drain readiness from that core's own
            // self-dispatch station.
            assign data_local_out_ready[core_i] = core_self_dispatch_ready[core_i];
        end
    endgenerate

    // Dispatch demux: send the packet to the hub of the line holding the
    // destination core. Line bounds are elaboration-time constants, so this is
    // one comparator pair per line and no divider.
    genvar hub_i;
    generate
        for (hub_i = 0; hub_i < HIGHWAY_LINE_COUNT; hub_i = hub_i + 1) begin : gen_ctrl_hub
            localparam integer LINE_LO = hub_i * HIGHWAY_LINE_SIZE;
            localparam integer LINE_HI = LINE_LO + HIGHWAY_LINE_SIZE - 1;

            assign ctrl_hub_in_valid[hub_i] = coord_dispatch_valid
                && (coord_dispatch_dst_core >= LINE_LO[CORE_ID_WIDTH-1:0])
                && (coord_dispatch_dst_core <= LINE_HI[CORE_ID_WIDTH-1:0]);
            assign ctrl_hub_in_dst[(hub_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = coord_dispatch_dst_core;
            assign ctrl_hub_in_payload[(hub_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {
                coord_dispatch_flow_id,
                coord_dispatch_opcode,
                coord_dispatch_a,
                coord_dispatch_b,
                coord_dispatch_use_immediate,
                coord_dispatch_immediate_b,
                coord_dispatch_stage_id
            };

            // Nothing is injected into the data plane from the host side, and
            // a control packet should never come back out of a hub.
            assign data_hub_in_valid[hub_i] = 1'b0;
            assign data_hub_in_dst[(hub_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
            assign data_hub_in_payload[(hub_i*DATA_PAYLOAD_WIDTH) +: DATA_PAYLOAD_WIDTH] = {DATA_PAYLOAD_WIDTH{1'b0}};
            assign ctrl_hub_out_ready[hub_i] = 1'b1;
        end
    endgenerate

    // An out-of-range destination is accepted and dropped rather than wedging
    // the coordinator, matching how unknown flow ids are handled.
    wire coord_dispatch_unmapped =
        coord_dispatch_valid && (coord_dispatch_dst_core >= CORE_COUNT[CORE_ID_WIDTH-1:0]);
    assign coord_dispatch_ready =
        (|(ctrl_hub_in_valid & ctrl_hub_in_ready)) || coord_dispatch_unmapped;

    // Result arbiter: every line can present a result in the same cycle, so
    // pick one round-robin. Fixed priority would let the first line starve the
    // rest exactly when the fabric is busiest.
    reg [CORE_ID_WIDTH-1:0] hub_rr_ptr;
    reg [CORE_ID_WIDTH-1:0] hub_sel;
    reg hub_sel_valid;
    integer arb_i;
    integer arb_cand;
    always @(*) begin
        hub_sel_valid = 1'b0;
        hub_sel = {CORE_ID_WIDTH{1'b0}};
        for (arb_i = 0; arb_i < HIGHWAY_LINE_COUNT; arb_i = arb_i + 1) begin
            arb_cand = hub_rr_ptr + arb_i;
            if (arb_cand >= HIGHWAY_LINE_COUNT) begin
                arb_cand = arb_cand - HIGHWAY_LINE_COUNT;
            end
            if (!hub_sel_valid && data_hub_out_valid[arb_cand]) begin
                hub_sel_valid = 1'b1;
                hub_sel = arb_cand[CORE_ID_WIDTH-1:0];
            end
        end
    end

    genvar grant_i;
    generate
        for (grant_i = 0; grant_i < HIGHWAY_LINE_COUNT; grant_i = grant_i + 1) begin : gen_hub_grant
            assign data_hub_out_ready[grant_i] =
                hub_sel_valid && (hub_sel == grant_i[CORE_ID_WIDTH-1:0]) && coord_result_ready;
        end
    endgenerate

    assign coord_result_valid = hub_sel_valid;
    assign coord_result_src_core =
        data_hub_out_payload[(hub_sel*DATA_PAYLOAD_WIDTH) + DATA_SRC_CORE_LSB +: CORE_ID_WIDTH];
    assign coord_result_value =
        data_hub_out_payload[(hub_sel*DATA_PAYLOAD_WIDTH) + DATA_VALUE_LSB +: DATA_WIDTH];
    assign coord_result_stage_id =
        data_hub_out_payload[(hub_sel*DATA_PAYLOAD_WIDTH) + DATA_STAGE_LSB +: 8];
    assign coord_result_flow_id =
        data_hub_out_payload[(hub_sel*DATA_PAYLOAD_WIDTH) + DATA_FLOW_ID_LSB +: FLOW_ID_WIDTH];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hub_rr_ptr <= {CORE_ID_WIDTH{1'b0}};
        end else if (hub_sel_valid && coord_result_ready) begin
            hub_rr_ptr <= (hub_sel == (HIGHWAY_LINE_COUNT - 1))
                ? {CORE_ID_WIDTH{1'b0}}
                : hub_sel + 1'b1;
        end
    end


    wire [CORE_COUNT*32-1:0] ctrl_router_hop_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_stall_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] ctrl_router_forward_count;

    wire [CORE_COUNT*32-1:0] data_router_hop_count;
    wire [CORE_COUNT*32-1:0] data_router_stall_count;
    wire [CORE_COUNT*32-1:0] data_router_local_delivered_count;
    wire [CORE_COUNT*32-1:0] data_router_forward_count;

    // Highway contract bus. Only the data-plane highway is contracted: it is
    // the one many cores compete for (every core pushes its results onto it),
    // whereas the control plane has a single injector (the coordinator) and so
    // has nothing to arbitrate.
    //
    // `data_contract_req` is the *real-time* side — a core raises it the moment
    // it actually has a result to move. `data_contract_word` is the *program*
    // side: the expectation the offline scheduler derived for that core, which
    // the bus applies when the core answers on its offered slot.
    wire [CORE_COUNT-1:0] data_contract_req;
    wire [CORE_COUNT*CONTRACT_WORD_WIDTH-1:0] data_contract_word;
    wire [CORE_COUNT-1:0] data_contract_call;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] data_contract_slot;
    wire [HIGHWAY_LINE_COUNT-1:0] data_contract_grant_valid;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] data_contract_grant_core;
    wire [HIGHWAY_LINE_COUNT*2-1:0] data_contract_grant_mode;
    wire [HIGHWAY_LINE_COUNT*16-1:0] data_contract_grant_remaining;
    wire [31:0] data_contract_grant_count;
    wire [31:0] data_contract_hold_cycles;
    wire [31:0] data_contract_defer_count;

    wire [CORE_COUNT-1:0] ctrl_contract_call;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] ctrl_contract_slot;
    wire [HIGHWAY_LINE_COUNT-1:0] ctrl_contract_grant_valid;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] ctrl_contract_grant_core;
    wire [HIGHWAY_LINE_COUNT*2-1:0] ctrl_contract_grant_mode;
    wire [HIGHWAY_LINE_COUNT*16-1:0] ctrl_contract_grant_remaining;
    wire [31:0] ctrl_contract_grant_count;
    wire [31:0] ctrl_contract_hold_cycles;
    wire [31:0] ctrl_contract_defer_count;

    // Coordinator hub channels, one per highway line, for each plane.
    wire [HIGHWAY_LINE_COUNT-1:0] ctrl_hub_in_valid;
    wire [HIGHWAY_LINE_COUNT-1:0] ctrl_hub_in_ready;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] ctrl_hub_in_dst;
    wire [HIGHWAY_LINE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_hub_in_payload;
    wire [HIGHWAY_LINE_COUNT-1:0] ctrl_hub_out_valid;
    wire [HIGHWAY_LINE_COUNT-1:0] ctrl_hub_out_ready;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] ctrl_hub_out_dst;
    wire [HIGHWAY_LINE_COUNT*CTRL_PAYLOAD_WIDTH-1:0] ctrl_hub_out_payload;

    wire [HIGHWAY_LINE_COUNT-1:0] data_hub_in_valid;
    wire [HIGHWAY_LINE_COUNT-1:0] data_hub_in_ready;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] data_hub_in_dst;
    wire [HIGHWAY_LINE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_hub_in_payload;
    wire [HIGHWAY_LINE_COUNT-1:0] data_hub_out_valid;
    wire [HIGHWAY_LINE_COUNT-1:0] data_hub_out_ready;
    wire [HIGHWAY_LINE_COUNT*CORE_ID_WIDTH-1:0] data_hub_out_dst;
    wire [HIGHWAY_LINE_COUNT*DATA_PAYLOAD_WIDTH-1:0] data_hub_out_payload;

    assign data_contract_req = core_result_valid;

    assign data_contract_word[(0*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00404;  // core 0: pong words=1 repeats=1
    assign data_contract_word[(1*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00404;  // core 1: pong words=1 repeats=1
    assign data_contract_word[(2*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00806;  // core 2: stream words=1 repeats=2
    assign data_contract_word[(3*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00404;  // core 3: pong words=1 repeats=1
    assign data_contract_word[(4*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00404;  // core 4: pong words=1 repeats=1
    assign data_contract_word[(5*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] = 18'h00404;  // core 5: pong words=1 repeats=1

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .GRID_Z(GRID_Z),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(CTRL_PAYLOAD_WIDTH),
        .CONTRACT_BUS_ENABLE(0),
        .LINE_COUNT(HIGHWAY_LINE_COUNT),
        .LINE_SIZE(HIGHWAY_LINE_SIZE)
    ) control_plane_mesh_u (
        .clk(clk),
        .rst_n(rst_n),
        .local_in_valid(ctrl_local_in_valid),
        .local_in_ready(ctrl_local_in_ready),
        .local_in_dst(ctrl_local_in_dst),
        .local_in_payload(ctrl_local_in_payload),
        .local_out_valid(ctrl_local_out_valid),
        .local_out_ready(ctrl_local_out_ready),
        .local_out_dst(ctrl_local_out_dst),
        .local_out_payload(ctrl_local_out_payload),
        .contract_req({CORE_COUNT{1'b0}}),
        .contract_word({(CORE_COUNT*CONTRACT_WORD_WIDTH){1'b0}}),
        .hub_in_valid(ctrl_hub_in_valid),
        .hub_in_ready(ctrl_hub_in_ready),
        .hub_in_dst(ctrl_hub_in_dst),
        .hub_in_payload(ctrl_hub_in_payload),
        .hub_out_valid(ctrl_hub_out_valid),
        .hub_out_ready(ctrl_hub_out_ready),
        .hub_out_dst(ctrl_hub_out_dst),
        .hub_out_payload(ctrl_hub_out_payload),
        .contract_call(ctrl_contract_call),
        .contract_slot(ctrl_contract_slot),
        .contract_grant_valid(ctrl_contract_grant_valid),
        .contract_grant_core(ctrl_contract_grant_core),
        .contract_grant_mode(ctrl_contract_grant_mode),
        .contract_grant_remaining(ctrl_contract_grant_remaining),
        .contract_grant_count(ctrl_contract_grant_count),
        .contract_hold_cycles(ctrl_contract_hold_cycles),
        .contract_defer_count(ctrl_contract_defer_count),
        .router_hop_count(ctrl_router_hop_count),
        .router_stall_count(ctrl_router_stall_count),
        .router_local_delivered_count(ctrl_router_local_delivered_count),
        .router_forward_count(ctrl_router_forward_count)
    );

    wau_highway_mesh #(
        .GRID_X(GRID_X),
        .GRID_Y(GRID_Y),
        .GRID_Z(GRID_Z),
        .CORE_COUNT(CORE_COUNT),
        .CORE_ID_WIDTH(CORE_ID_WIDTH),
        .PAYLOAD_WIDTH(DATA_PAYLOAD_WIDTH),
        .CONTRACT_BUS_ENABLE(CONTRACT_BUS_ENABLE),
        .LINE_COUNT(HIGHWAY_LINE_COUNT),
        .LINE_SIZE(HIGHWAY_LINE_SIZE)
    ) data_plane_mesh_u (
        .clk(clk),
        .rst_n(rst_n),
        .local_in_valid(data_local_in_valid),
        .local_in_ready(data_local_in_ready),
        .local_in_dst(data_local_in_dst),
        .local_in_payload(data_local_in_payload),
        .local_out_valid(data_local_out_valid),
        .local_out_ready(data_local_out_ready),
        .local_out_dst(data_local_out_dst),
        .local_out_payload(data_local_out_payload),
        .contract_req(data_contract_req),
        .contract_word(data_contract_word),
        .hub_in_valid(data_hub_in_valid),
        .hub_in_ready(data_hub_in_ready),
        .hub_in_dst(data_hub_in_dst),
        .hub_in_payload(data_hub_in_payload),
        .hub_out_valid(data_hub_out_valid),
        .hub_out_ready(data_hub_out_ready),
        .hub_out_dst(data_hub_out_dst),
        .hub_out_payload(data_hub_out_payload),
        .contract_call(data_contract_call),
        .contract_slot(data_contract_slot),
        .contract_grant_valid(data_contract_grant_valid),
        .contract_grant_core(data_contract_grant_core),
        .contract_grant_mode(data_contract_grant_mode),
        .contract_grant_remaining(data_contract_grant_remaining),
        .contract_grant_count(data_contract_grant_count),
        .contract_hold_cycles(data_contract_hold_cycles),
        .contract_defer_count(data_contract_defer_count),
        .router_hop_count(data_router_hop_count),
        .router_stall_count(data_router_stall_count),
        .router_local_delivered_count(data_router_local_delivered_count),
        .router_forward_count(data_router_forward_count)
    );

    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;

    genvar gz;
    genvar gy;
    genvar gx;
    generate
        for (gz = 0; gz < GRID_Z; gz = gz + 1) begin : gen_z
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gz * LAYER_CORE_COUNT) + (gy * GRID_X) + gx;

                wau_core #(
                    .CORE_X(gx),
                    .CORE_Y(gy),
                    .CORE_Z(gz),
                    .CORE_INDEX(CORE_INDEX),
                    .DATA_WIDTH(DATA_WIDTH),
                    .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
                    .OPCODE_WIDTH(OPCODE_WIDTH),
                    .CORE_COUNT(CORE_COUNT),
                    .STATION_PROGRAM_ENABLE(STATION_PROGRAM_ENABLE),
                    .COORD_DST_SENTINEL(COORD_DST_SENTINEL)
                ) core_u (
                    .clk(clk),
                    .rst_n(rst_n),
                    .dispatch_valid(core_dispatch_valid[CORE_INDEX]),
                    .dispatch_ready(core_dispatch_ready[CORE_INDEX]),
                    .dispatch_flow_id(core_dispatch_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .dispatch_opcode(core_dispatch_opcode[(CORE_INDEX*OPCODE_WIDTH) +: OPCODE_WIDTH]),
                    .dispatch_a(core_dispatch_a[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_b(core_dispatch_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_use_immediate(core_dispatch_use_immediate[CORE_INDEX]),
                    .dispatch_immediate_b(core_dispatch_immediate_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_stage_id(core_dispatch_stage_id[(CORE_INDEX*8) +: 8]),
                    .self_dispatch_valid(core_self_dispatch_valid[CORE_INDEX]),
                    .self_dispatch_ready(core_self_dispatch_ready[CORE_INDEX]),
                    .self_dispatch_flow_id(core_self_dispatch_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .self_dispatch_opcode(core_self_dispatch_opcode[(CORE_INDEX*OPCODE_WIDTH) +: OPCODE_WIDTH]),
                    .self_dispatch_a(core_self_dispatch_a[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .self_dispatch_b_reg(core_self_dispatch_b_reg[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .self_dispatch_use_immediate(core_self_dispatch_use_immediate[CORE_INDEX]),
                    .self_dispatch_immediate_b(core_self_dispatch_immediate_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .self_dispatch_stage_id(core_self_dispatch_stage_id[(CORE_INDEX*8) +: 8]),
                    .peer_busy(core_busy),
                    .result_valid(core_result_valid[CORE_INDEX]),
                    .result_ready(core_result_ready[CORE_INDEX]),
                    .result_flow_id(core_result_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .result_stage_id(core_result_stage_id[(CORE_INDEX*8) +: 8]),
                    .result_value(core_result_value[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .result_b_reg(core_result_b_reg[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .result_is_fast_path(core_result_is_fast_path[CORE_INDEX]),
                    .result_dst_core(core_result_dst_core[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .result_next_stage_id(core_result_next_stage_id[(CORE_INDEX*8) +: 8]),
                    .result_next_opcode(core_result_next_opcode[(CORE_INDEX*OPCODE_WIDTH) +: OPCODE_WIDTH]),
                    .result_next_use_immediate(core_result_next_use_immediate[CORE_INDEX]),
                    .result_next_immediate_b(core_result_next_immediate_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .busy(core_busy[CORE_INDEX]),
                    .cache_hit(core_cache_hit[CORE_INDEX]),
                    .cache_hit_count(core_cache_hit_count[(CORE_INDEX*32) +: 32]),
                    .cache_lookup_count(core_cache_lookup_count[(CORE_INDEX*32) +: 32])
                );
            end
        end
        end
    endgenerate

    // Observability aggregation: sum per-core / per-router counters into single
    // saturation-unaware 32-bit totals. These are intended for host-side polling
    // (e.g. via wau_host_mmio) and for testbenches that want a single
    // throughput/hit-rate signal across the mesh.
    reg [31:0] total_hop_count;
    reg [31:0] total_stall_count;
    reg [31:0] total_forward_count;
    reg [31:0] total_local_delivered_count;
    reg [31:0] total_cache_hit_count;
    reg [31:0] total_cache_lookup_count;
    integer obs_i;
    always @(*) begin
        total_hop_count = 32'd0;
        total_stall_count = 32'd0;
        total_forward_count = 32'd0;
        total_local_delivered_count = 32'd0;
        for (obs_i = 0; obs_i < CORE_COUNT; obs_i = obs_i + 1) begin
            total_hop_count = total_hop_count
                + ctrl_router_hop_count[(obs_i*32) +: 32]
                + data_router_hop_count[(obs_i*32) +: 32];
            total_stall_count = total_stall_count
                + ctrl_router_stall_count[(obs_i*32) +: 32]
                + data_router_stall_count[(obs_i*32) +: 32];
            total_forward_count = total_forward_count
                + ctrl_router_forward_count[(obs_i*32) +: 32]
                + data_router_forward_count[(obs_i*32) +: 32];
            total_local_delivered_count = total_local_delivered_count
                + ctrl_router_local_delivered_count[(obs_i*32) +: 32]
                + data_router_local_delivered_count[(obs_i*32) +: 32];
        end
    end
    always @(*) begin
        total_cache_hit_count = 32'd0;
        total_cache_lookup_count = 32'd0;
        for (obs_i = 0; obs_i < CORE_COUNT; obs_i = obs_i + 1) begin
            total_cache_hit_count = total_cache_hit_count
                + core_cache_hit_count[(obs_i*32) +: 32];
            total_cache_lookup_count = total_cache_lookup_count
                + core_cache_lookup_count[(obs_i*32) +: 32];
        end
    end
    assign obs_total_hop_count = total_hop_count;
    assign obs_total_stall_count = total_stall_count;
    assign obs_total_forward_count = total_forward_count;
    assign obs_total_local_delivered_count = total_local_delivered_count;
    assign obs_total_cache_hit_count = total_cache_hit_count;
    assign obs_total_cache_lookup_count = total_cache_lookup_count;

    // The control-plane highway runs uncontracted, so its counters are constant
    // zero; summing both planes keeps the observability bus meaningful if the
    // control plane is ever contracted too.
    assign obs_total_contract_grant_count =
        data_contract_grant_count + ctrl_contract_grant_count;
    assign obs_total_contract_hold_cycles =
        data_contract_hold_cycles + ctrl_contract_hold_cycles;
    assign obs_total_contract_defer_count =
        data_contract_defer_count + ctrl_contract_defer_count;
endmodule
