# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import json
from pathlib import Path
import sys

from .compiler import CompiledProject, build_fast_path_tables
from .scheduler import SchedulePlan, core_index
from .utils import macro_name

try:
    from veribuilder import VerilogHeader, VerilogProject
except ModuleNotFoundError as exc:
    if exc.name != "veribuilder":
        raise
    _VERIBUILDER_SRC = (
        Path(__file__).resolve().parents[3] / "thirds" / "veribuilder" / "src"
    )
    if _VERIBUILDER_SRC.exists():
        sys.path.insert(0, str(_VERIBUILDER_SRC))
    from veribuilder import VerilogHeader, VerilogProject

_SPDX_IDENTIFIER = "PolyForm-Noncommercial-1.0.0"
_LICENSE_REF_TEXT = "See LICENSE at the repository root."


def _op_macro(op_name: str) -> str:
    return macro_name(op_name)


def _render_defs(project: CompiledProject) -> str:
    cfg = project.config
    lines: list[str] = []
    lines.append("`ifndef WAU_DEFS_VH")
    lines.append("`define WAU_DEFS_VH")
    lines.append("")
    lines.append(f"`define WAU_PROJECT_NAME \"{cfg.project_name}\"")
    lines.append(f"`define WAU_ABSTRACTION_LANGUAGE \"{cfg.abstraction_language}\"")
    lines.append(f"`define WAU_ABSTRACTION_VERSION {cfg.abstraction_version}")
    lines.append(f"`define WAU_GRID_X {cfg.device.grid_x}")
    lines.append(f"`define WAU_GRID_Y {cfg.device.grid_y}")
    lines.append(f"`define WAU_GRID_Z {cfg.device.grid_z}")
    lines.append(f"`define WAU_CORE_COUNT {cfg.device.grid_x * cfg.device.grid_y * cfg.device.grid_z}")
    lines.append(f"`define WAU_DATA_WIDTH {cfg.device.data_width}")
    lines.append(f"`define WAU_FLOW_ID_WIDTH {cfg.device.flow_id_width}")
    lines.append(f"`define WAU_OPCODE_WIDTH {cfg.device.opcode_width}")
    lines.append(f"`define WAU_LOCAL_RAM_DEPTH {cfg.device.local_ram_depth}")
    lines.append(f"`define WAU_GLOBAL_RAM_DEPTH {cfg.device.global_ram_depth}")
    lines.append(f"`define WAU_DATA_TYPE_COUNT {len(cfg.device.supported_data_types)}")
    lines.append(f"`define WAU_FLOW_COUNT {len(project.flows)}")
    lines.append(f"`define WAU_MAX_STAGES {project.max_stages}")
    lines.append(f"`define WAU_COORD_MAX_IN_FLIGHT {cfg.coordinator.max_in_flight}")
    lines.append(f"`define WAU_OP_COUNT {len(cfg.operations)}")
    station_cache = cfg.compiler.station_cache
    lines.append(f"`define WAU_STATION_CACHE_ENTRIES {station_cache.entries}")
    lines.append(
        f"`define WAU_STATION_CACHE_POLICY_{station_cache.replacement_policy.upper()} 1"
    )
    lines.append(
        f"// Station cache policy: {station_cache.replacement_policy} "
        f"({station_cache.entries} entries)"
    )
    highway = cfg.device.highway
    lines.append(f"`define WAU_HIGHWAY_TOPOLOGY_{highway.topology.upper()} 1")
    lines.append(f"`define WAU_HIGHWAY_TOPOLOGY_NAME \"{highway.topology}\"")
    lines.append(
        f"`define WAU_HIGHWAY_PORT_COUNT {len(_highway_dirs(project))}"
    )
    lines.append(f"`define WAU_HIGHWAY_LINE_COUNT {_highway_line_count(project)}")
    lines.append(f"`define WAU_HIGHWAY_LINE_SIZE {_highway_line_size(project)}")
    lines.append(
        f"`define WAU_HIGHWAY_CONTRACT_BUS {1 if highway.contract_bus else 0}"
    )
    lines.append(f"`define WAU_HIGHWAY_CONTRACT_WORD_WIDTH {_CONTRACT_WORD_WIDTH}")
    lines.append(f"`define WAU_HIGHWAY_CONTRACT_MODE_LSB {_CONTRACT_MODE_LSB}")
    lines.append(f"`define WAU_HIGHWAY_CONTRACT_WORDS_LSB {_CONTRACT_WORDS_LSB}")
    lines.append(f"`define WAU_HIGHWAY_CONTRACT_REPEATS_LSB {_CONTRACT_REPEATS_LSB}")
    lines.append(f"`define WAU_HIGHWAY_CONTRACT_MAX_BURST {highway.contract_max_burst}")
    lines.append(
        f"`define WAU_HIGHWAY_CONTRACT_LEASE_CYCLES {highway.contract_lease_cycles}"
    )
    lines.append(
        f"// Highway topology: {highway.topology} "
        f"({len(_highway_dirs(project))} router ports per core, "
        f"{_highway_line_count(project)} highway line(s) of "
        f"{_highway_line_size(project)} core(s))"
    )
    lines.append(
        "// Contract bus modes: 0=pong, 1=burst, 2=stream, 3=reserve"
    )
    lines.append(f"// Supported data types: {', '.join(cfg.device.supported_data_types)}")
    station_program = cfg.compiler.station_program
    lines.append(
        f"`define WAU_STATION_PROGRAM_ENABLE {1 if station_program.enabled else 0}"
    )
    lines.append(f"`define WAU_STATION_PROGRAM_TABLE_BITS {station_program.table_bits}")
    lines.append(
        f"// Per-core fast-path table: {'enabled' if station_program.enabled else 'disabled'} "
        f"({1 << station_program.table_bits} entries/core when enabled)"
    )
    lines.append(
        f"`define WAU_DEFAULT_AUTO_ADAPT {1 if cfg.device.enable_runtime_auto_adapt else 0}"
    )
    lines.append("")
    for op in cfg.operations:
        m = _op_macro(op.name)
        lines.append(f"`define WAU_OPCODE_{m} {cfg.device.opcode_width}'h{op.opcode:02X}")
        lines.append(f"`define WAU_LATENCY_{m} 8'd{op.latency}")
    lines.append("")
    lines.append("`endif")
    return "\n".join(lines) + "\n"


def _render_operation_alu(project: CompiledProject) -> str:
    cfg = project.config
    core_count = cfg.device.grid_x * cfg.device.grid_y * cfg.device.grid_z
    explicit_caps = {
        core_index(
            cap.core.x,
            cap.core.y,
            cfg.device.grid_x,
            cap.core.z,
            cfg.device.grid_y,
        ): set(cap.operations)
        for cap in cfg.compiler.core_capabilities
        if cap.operations
    }
    case_lines = []
    for op in project.config.operations:
        allowed = [
            idx for idx in range(core_count)
            if idx not in explicit_caps or op.name in explicit_caps[idx]
        ]
        core_condition = " || ".join(f"(CORE_INDEX == {idx})" for idx in allowed) or "1'b0"
        condition = f"(CORE_INDEX < 0) || {core_condition}"
        case_lines.append(
            f"      `WAU_OPCODE_{_op_macro(op.name)}: begin\n"
            f"          if ({condition}) result_comb = {op.verilog_expr};\n"
            "      end"
        )
    case_blob = "\n".join(case_lines)

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_operation_alu #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter integer CORE_INDEX = -1
) (
    input wire clk,
    input wire rst_n,
    input wire in_valid,
    input wire [OPCODE_WIDTH-1:0] opcode,
    input wire signed [DATA_WIDTH-1:0] a,
    input wire signed [DATA_WIDTH-1:0] b,
    output reg out_valid,
    output reg signed [DATA_WIDTH-1:0] y
);
    reg signed [DATA_WIDTH-1:0] result_comb;

    always @(*) begin
        result_comb = {{DATA_WIDTH{{1'b0}}}};
        case (opcode)
{case_blob}
            default: result_comb = {{DATA_WIDTH{{1'b0}}}};
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            y <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            out_valid <= in_valid;
            y <= result_comb;
        end
    end
endmodule
"""


def _fast_path_word_layout(opcode_width: int, data_width: int) -> dict[str, int]:
    """Bit layout of the packed word `fast_path_lookup` returns: one hit bit
    plus every field a receiving core needs to run the next stage itself,
    self-describing so the receiving station needs no lookup of its own (see
    `_core_fast_path_lookup_function`)."""
    return {
        "hit_lsb": 0,
        "next_stage_lsb": 1,
        "next_opcode_lsb": 9,
        "next_use_imm_lsb": 9 + opcode_width,
        "next_imm_lsb": 10 + opcode_width,
        "next_dst_primary_lsb": 10 + opcode_width + data_width,
        "next_dst_fallback_lsb": 18 + opcode_width + data_width,
        "word_width": 26 + opcode_width + data_width,
    }


def _fast_path_excluded_destinations(project: CompiledProject) -> frozenset[int]:
    """Physical core indices a fast-path hop must never target -- see
    `build_fast_path_tables`'s `excluded_destinations` docstring. Shared by
    `_core_fast_path_lookup_function` (RTL) and `_render_program_json`
    (reporting) so the two never drift apart."""
    return frozenset() if project.config.device.highway.is_lines else frozenset({0})


def _core_fast_path_lookup_function(project: CompiledProject) -> str:
    """Renders `wau_core_station`'s `fast_path_lookup` function: a
    `case (CORE_INDEX) ... case ({flow_id, stage_id}) ...` lookup, following
    the same established idiom as `_stage_case_entries`/`flow_stage_opcode`
    (one `case` arm per compiled entry, safe `default`), just keyed directly
    by `(flow_id, stage_id)` and packed per `_fast_path_word_layout` instead
    of returning one field per function. `compiler.build_fast_path_tables` is
    the single source of which entries exist and on which core; this
    function only serializes that data into Verilog text (no scheduling or
    placement decision belongs here). When the feature is disabled, or a
    project has no eligible entries at all, this degenerates to an
    always-miss function -- structurally identical to having no fast path."""
    cfg = project.config
    station_program = cfg.compiler.station_program
    opcode_width = cfg.device.opcode_width
    data_width = cfg.device.data_width
    layout = _fast_path_word_layout(opcode_width, data_width)
    word_width = layout["word_width"]
    key_width = cfg.device.flow_id_width + 8

    def miss_only() -> str:
        return f"""    function [{word_width - 1}:0] fast_path_lookup;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        input [7:0] stage_id;
        begin
            fast_path_lookup = {{{word_width}{{1'b0}}}};
        end
    endfunction
"""

    if not station_program.enabled:
        return miss_only()

    excluded_destinations = _fast_path_excluded_destinations(project)
    tables = build_fast_path_tables(
        project,
        table_bits=station_program.table_bits,
        excluded_destinations=excluded_destinations,
    )

    core_blocks: list[str] = []
    for table in tables:
        if not table.hops:
            continue
        case_lines: list[str] = []
        for (flow_id, stage_id), hop in sorted(table.hops.items()):
            key = (flow_id << 8) | stage_id
            imm = hop.next_immediate_b if hop.next_immediate_b is not None else 0
            imm_bits = imm & ((1 << data_width) - 1)
            word = 1  # hit bit (bit 0)
            word |= hop.next_stage_index << layout["next_stage_lsb"]
            word |= hop.next_opcode << layout["next_opcode_lsb"]
            word |= (1 if hop.next_use_immediate else 0) << layout["next_use_imm_lsb"]
            word |= imm_bits << layout["next_imm_lsb"]
            word |= hop.next_dst_primary << layout["next_dst_primary_lsb"]
            word |= hop.next_dst_fallback << layout["next_dst_fallback_lsb"]
            case_lines.append(
                f"                        {key_width}'d{key}: value = {word_width}'d{word};"
            )
        case_blob = "\n".join(case_lines)
        core_blocks.append(
            f"                {table.core_index}: begin\n"
            f"                    case ({{flow_id, stage_id}})\n"
            f"{case_blob}\n"
            f"                        default: value = {{{word_width}{{1'b0}}}};\n"
            f"                    endcase\n"
            f"                end"
        )

    if not core_blocks:
        return miss_only()

    core_blob = "\n".join(core_blocks)
    return f"""    function [{word_width - 1}:0] fast_path_lookup;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        input [7:0] stage_id;
        reg [{word_width - 1}:0] value;
        begin
            value = {{{word_width}{{1'b0}}}};
            case (CORE_INDEX)
{core_blob}
                default: value = {{{word_width}{{1'b0}}}};
            endcase
            fast_path_lookup = value;
        end
    endfunction
"""


def _render_core_station(project: CompiledProject) -> str:
    latency_cases = []
    for op in project.config.operations:
        latency_cases.append(
            f"                `WAU_OPCODE_{_op_macro(op.name)}: op_latency = `WAU_LATENCY_{_op_macro(op.name)};"
        )
    latency_blob = "\n".join(latency_cases)

    cache_entries = project.config.compiler.station_cache.entries
    policy = project.config.compiler.station_cache.replacement_policy
    lru_enabled = "1" if policy == "lru" else "0"
    fast_path_lookup_fn = _core_fast_path_lookup_function(project)

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core_station #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CACHE_ENTRIES = `WAU_STATION_CACHE_ENTRIES,
    parameter CACHE_LRU_ENABLED = {lru_enabled},
    parameter CORE_COUNT = `WAU_CORE_COUNT,
    parameter STATION_PROGRAM_ENABLE = `WAU_STATION_PROGRAM_ENABLE,
    parameter [7:0] COORD_DST_SENTINEL = 8'd0,
    parameter integer CORE_INDEX = 0
) (
    input wire clk,
    input wire rst_n,

    input wire in_valid,
    output wire in_ready,
    input wire [7:0] in_tag,
    input wire [FLOW_ID_WIDTH-1:0] in_flow_id,
    input wire [OPCODE_WIDTH-1:0] in_opcode,
    input wire signed [DATA_WIDTH-1:0] in_a,
    input wire signed [DATA_WIDTH-1:0] in_b,
    input wire in_use_immediate,
    input wire signed [DATA_WIDTH-1:0] in_immediate_b,
    input wire [7:0] in_stage_id,

    // Fast-path self-dispatch: a peer core (or this same core, via the
    // fabric loopback) handing this station a stage whose destination was
    // decided offline by `compiler.build_fast_path_tables`, bypassing the
    // coordinator round-trip. Mirrors `in_*` exactly, plus the flow's live
    // `b_reg` operand (carried forward hop-to-hop, since it is a host-time
    // value the offline table cannot bake in as an immediate) and the whole
    // chip's busy bus, needed to pick between a hop's primary/fallback
    // destination locally instead of via the central coordinator.
    input wire self_valid,
    output wire self_ready,
    input wire [7:0] self_tag,
    input wire [FLOW_ID_WIDTH-1:0] self_flow_id,
    input wire [OPCODE_WIDTH-1:0] self_opcode,
    input wire signed [DATA_WIDTH-1:0] self_a,
    input wire signed [DATA_WIDTH-1:0] self_b_reg,
    input wire self_use_immediate,
    input wire signed [DATA_WIDTH-1:0] self_immediate_b,
    input wire [7:0] self_stage_id,
    input wire [CORE_COUNT-1:0] peer_busy,

    output reg out_valid,
    input wire out_ready,
    output reg [7:0] out_tag,
    output reg [FLOW_ID_WIDTH-1:0] out_flow_id,
    output reg [7:0] out_stage_id,
    output reg signed [DATA_WIDTH-1:0] out_value,

    // Fast-path result-side outputs, valid whenever out_valid is: out_b_reg
    // carries the flow's live operand forward regardless of path; the rest
    // are only meaningful when out_is_fast_path is set, in which case they
    // fully describe the next stage a receiving station should run, and
    // out_dst_core is the routed destination (COORD_DST_SENTINEL --
    // structurally identical to today's hardwired hub routing -- when this
    // was not a fast-path hit).
    output reg signed [DATA_WIDTH-1:0] out_b_reg,
    output reg out_is_fast_path,
    output reg [7:0] out_dst_core,
    output reg [7:0] out_next_stage_id,
    output reg [OPCODE_WIDTH-1:0] out_next_opcode,
    output reg out_next_use_immediate,
    output reg signed [DATA_WIDTH-1:0] out_next_immediate_b,

    output wire busy,
    output reg cache_hit,
    output reg [31:0] cache_hit_count,
    output reg [31:0] cache_lookup_count
);
    localparam ST_IDLE = 2'd0;
    localparam ST_EXEC = 2'd1;
    localparam ST_OUT = 2'd2;

    // fast_path_lookup's packed return-word layout -- must match
    // `_fast_path_word_layout` in verilog_emit.py exactly.
    localparam FP_HIT_LSB = 0;
    localparam FP_NEXT_STAGE_LSB = 1;
    localparam FP_NEXT_OPCODE_LSB = 9;
    localparam FP_NEXT_USE_IMM_LSB = 9 + OPCODE_WIDTH;
    localparam FP_NEXT_IMM_LSB = 10 + OPCODE_WIDTH;
    localparam FP_NEXT_DST_PRIMARY_LSB = 10 + OPCODE_WIDTH + DATA_WIDTH;
    localparam FP_NEXT_DST_FALLBACK_LSB = 18 + OPCODE_WIDTH + DATA_WIDTH;
    localparam FP_WORD_WIDTH = 26 + OPCODE_WIDTH + DATA_WIDTH;

    reg [1:0] state;

    reg [7:0] active_tag;
    reg [FLOW_ID_WIDTH-1:0] active_flow_id;
    reg [OPCODE_WIDTH-1:0] active_opcode;
    reg [7:0] active_stage_id;
    reg signed [DATA_WIDTH-1:0] op_a;
    reg signed [DATA_WIDTH-1:0] op_b;
    reg signed [DATA_WIDTH-1:0] active_b_reg;
    reg [7:0] wait_cycles;

    reg cache_valid [0:CACHE_ENTRIES-1];
    reg [OPCODE_WIDTH-1:0] cache_opcode [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_a [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_b [0:CACHE_ENTRIES-1];
    reg signed [DATA_WIDTH-1:0] cache_value [0:CACHE_ENTRIES-1];
    reg [31:0] cache_age [0:CACHE_ENTRIES-1];
    reg [7:0] cache_replace_ptr;
    reg [31:0] cache_age_clock;

    reg cache_hit_comb;
    reg [7:0] cache_hit_index;
    reg signed [DATA_WIDTH-1:0] cache_hit_value;

    reg alu_in_valid;
    wire alu_out_valid;
    wire signed [DATA_WIDTH-1:0] alu_out_value;

    reg result_latched_valid;
    reg signed [DATA_WIDTH-1:0] result_latched_value;

    integer cache_idx;
    integer cache_scan;
    integer victim_scan;
    reg [7:0] victim_index;
    reg [31:0] victim_age;
    reg victim_filled;

    // Fast-path lookup scratch (blocking-assigned within the clocked block
    // below, same idiom as `latched_now` in wau_coordinator): computed once
    // per completion and sliced, rather than calling the function repeatedly.
    reg [FP_WORD_WIDTH-1:0] fp_scratch;
    reg [7:0] fp_dst_p;
    reg [7:0] fp_dst_f;

    // Whichever source wins arbitration this cycle (self-dispatch always
    // takes priority over a same-cycle control-plane dispatch -- see
    // in_ready/self_ready below); every field the ST_IDLE branch consumes is
    // muxed once here so the branch itself does not care which source it was.
    wire sel_use_self;
    assign sel_use_self = self_valid;

    wire signed [DATA_WIDTH-1:0] effective_b;
    wire signed [DATA_WIDTH-1:0] self_effective_b;
    assign effective_b = in_use_immediate ? in_immediate_b : in_b;
    assign self_effective_b = self_use_immediate ? self_immediate_b : self_b_reg;

    wire [7:0] sel_tag;
    wire [FLOW_ID_WIDTH-1:0] sel_flow_id;
    wire [OPCODE_WIDTH-1:0] sel_opcode;
    wire signed [DATA_WIDTH-1:0] sel_a;
    wire signed [DATA_WIDTH-1:0] sel_b;
    wire signed [DATA_WIDTH-1:0] sel_b_reg;
    wire [7:0] sel_stage_id;
    assign sel_tag      = sel_use_self ? self_tag           : in_tag;
    assign sel_flow_id  = sel_use_self ? self_flow_id       : in_flow_id;
    assign sel_opcode   = sel_use_self ? self_opcode        : in_opcode;
    assign sel_a        = sel_use_self ? self_a             : in_a;
    assign sel_b        = sel_use_self ? self_effective_b   : effective_b;
    assign sel_b_reg    = sel_use_self ? self_b_reg         : in_b;
    assign sel_stage_id = sel_use_self ? self_stage_id      : in_stage_id;

    // Self-dispatch gets unconditional priority over a same-cycle
    // control-plane dispatch. When self_valid is always 0 (feature disabled
    // or this core has no fast-path entries), in_ready reduces to exactly
    // `state == ST_IDLE` -- today's expression, byte-identical.
    assign in_ready = (state == ST_IDLE) && !self_valid;
    assign self_ready = (state == ST_IDLE);
    assign busy = (state != ST_IDLE);

    function [7:0] op_latency;
        input [OPCODE_WIDTH-1:0] opcode;
        begin
            case (opcode)
{latency_blob}
                default: op_latency = 8'd1;
            endcase
        end
    endfunction

{fast_path_lookup_fn}
    wau_operation_alu #(
        .DATA_WIDTH(DATA_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH),
        .CORE_INDEX(CORE_INDEX)
    ) alu_u (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(alu_in_valid),
        .opcode(active_opcode),
        .a(op_a),
        .b(op_b),
        .out_valid(alu_out_valid),
        .y(alu_out_value)
    );

    always @(*) begin
        cache_hit_comb = 1'b0;
        cache_hit_index = 8'd0;
        cache_hit_value = {{DATA_WIDTH{{1'b0}}}};
        for (cache_scan = 0; cache_scan < CACHE_ENTRIES; cache_scan = cache_scan + 1) begin
            if (cache_valid[cache_scan] &&
                (cache_opcode[cache_scan] == sel_opcode) &&
                (cache_a[cache_scan] == sel_a) &&
                (cache_b[cache_scan] == sel_b) &&
                !cache_hit_comb) begin
                cache_hit_comb = 1'b1;
                cache_hit_index = cache_scan[7:0];
                cache_hit_value = cache_value[cache_scan];
            end
        end
    end

    // LRU victim selection: pick the entry with the smallest age (oldest reference);
    // unused entries (cache_valid=0) take priority. Tie-broken by lowest index.
    always @(*) begin
        victim_index = 8'd0;
        victim_age = 32'hFFFFFFFF;
        victim_filled = 1'b0;
        if (CACHE_LRU_ENABLED) begin
            for (victim_scan = 0; victim_scan < CACHE_ENTRIES; victim_scan = victim_scan + 1) begin
                if (!cache_valid[victim_scan] && !victim_filled) begin
                    victim_index = victim_scan[7:0];
                    victim_filled = 1'b1;
                end
            end
            if (!victim_filled) begin
                victim_index = 8'd0;
                victim_age = cache_age[0];
                for (victim_scan = 1; victim_scan < CACHE_ENTRIES; victim_scan = victim_scan + 1) begin
                    if (cache_age[victim_scan] < victim_age) begin
                        victim_index = victim_scan[7:0];
                        victim_age = cache_age[victim_scan];
                    end
                end
            end
        end else begin
            victim_index = cache_replace_ptr;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            out_valid <= 1'b0;
            out_tag <= 8'd0;
            out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            out_stage_id <= 8'd0;
            out_value <= {{DATA_WIDTH{{1'b0}}}};
            active_tag <= 8'd0;
            active_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            active_opcode <= {{OPCODE_WIDTH{{1'b0}}}};
            active_stage_id <= 8'd0;
            op_a <= {{DATA_WIDTH{{1'b0}}}};
            op_b <= {{DATA_WIDTH{{1'b0}}}};
            active_b_reg <= {{DATA_WIDTH{{1'b0}}}};
            wait_cycles <= 8'd0;
            cache_replace_ptr <= 8'd0;
            cache_age_clock <= 32'd1;
            for (cache_idx = 0; cache_idx < CACHE_ENTRIES; cache_idx = cache_idx + 1) begin
                cache_valid[cache_idx] <= 1'b0;
                cache_opcode[cache_idx] <= {{OPCODE_WIDTH{{1'b0}}}};
                cache_a[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_b[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_value[cache_idx] <= {{DATA_WIDTH{{1'b0}}}};
                cache_age[cache_idx] <= 32'd0;
            end
            cache_hit <= 1'b0;
            cache_hit_count <= 32'd0;
            cache_lookup_count <= 32'd0;
            alu_in_valid <= 1'b0;
            result_latched_valid <= 1'b0;
            result_latched_value <= {{DATA_WIDTH{{1'b0}}}};
            out_b_reg <= {{DATA_WIDTH{{1'b0}}}};
            out_is_fast_path <= 1'b0;
            out_dst_core <= COORD_DST_SENTINEL;
            out_next_stage_id <= 8'd0;
            out_next_opcode <= {{OPCODE_WIDTH{{1'b0}}}};
            out_next_use_immediate <= 1'b0;
            out_next_immediate_b <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            alu_in_valid <= 1'b0;
            cache_hit <= 1'b0;

            if (out_valid && out_ready) begin
                out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    result_latched_valid <= 1'b0;
                    if (sel_use_self || in_valid) begin
                        cache_hit <= cache_hit_comb;
                        cache_lookup_count <= cache_lookup_count + 32'd1;
                        if (cache_hit_comb) begin
                            cache_hit_count <= cache_hit_count + 32'd1;
                            out_valid <= 1'b1;
                            out_tag <= sel_tag;
                            out_flow_id <= sel_flow_id;
                            out_stage_id <= sel_stage_id;
                            out_value <= cache_hit_value;
                            out_b_reg <= sel_b_reg;

                            fp_scratch = fast_path_lookup(sel_flow_id, sel_stage_id);
                            if (STATION_PROGRAM_ENABLE && fp_scratch[FP_HIT_LSB]) begin
                                out_is_fast_path <= 1'b1;
                                out_next_stage_id <= fp_scratch[FP_NEXT_STAGE_LSB +: 8];
                                out_next_opcode <= fp_scratch[FP_NEXT_OPCODE_LSB +: OPCODE_WIDTH];
                                out_next_use_immediate <= fp_scratch[FP_NEXT_USE_IMM_LSB];
                                out_next_immediate_b <= fp_scratch[FP_NEXT_IMM_LSB +: DATA_WIDTH];
                                fp_dst_p = fp_scratch[FP_NEXT_DST_PRIMARY_LSB +: 8];
                                fp_dst_f = fp_scratch[FP_NEXT_DST_FALLBACK_LSB +: 8];
                                out_dst_core <= (peer_busy[fp_dst_p] && !peer_busy[fp_dst_f]) ? fp_dst_f : fp_dst_p;
                            end else begin
                                out_is_fast_path <= 1'b0;
                                out_dst_core <= COORD_DST_SENTINEL;
                            end

                            if (CACHE_LRU_ENABLED) begin
                                cache_age[cache_hit_index] <= cache_age_clock;
                                cache_age_clock <= cache_age_clock + 32'd1;
                            end
                            state <= ST_OUT;
                        end else begin
                            active_tag <= sel_tag;
                            active_flow_id <= sel_flow_id;
                            active_opcode <= sel_opcode;
                            active_stage_id <= sel_stage_id;
                            active_b_reg <= sel_b_reg;
                            op_a <= sel_a;
                            op_b <= sel_b;
                            wait_cycles <= op_latency(sel_opcode) - 8'd1;

                            alu_in_valid <= 1'b1;
                            state <= ST_EXEC;
                        end
                    end
                end

                ST_EXEC: begin
                    if (alu_out_valid) begin
                        result_latched_valid <= 1'b1;
                        result_latched_value <= alu_out_value;
                    end

                    if (wait_cycles != 8'd0) begin
                        wait_cycles <= wait_cycles - 8'd1;
                    end

                    if ((wait_cycles == 8'd0) && (result_latched_valid || alu_out_valid)) begin
                        out_valid <= 1'b1;
                        out_tag <= active_tag;
                        out_flow_id <= active_flow_id;
                        out_stage_id <= active_stage_id;
                        out_value <= alu_out_valid ? alu_out_value : result_latched_value;
                        out_b_reg <= active_b_reg;

                        fp_scratch = fast_path_lookup(active_flow_id, active_stage_id);
                        if (STATION_PROGRAM_ENABLE && fp_scratch[FP_HIT_LSB]) begin
                            out_is_fast_path <= 1'b1;
                            out_next_stage_id <= fp_scratch[FP_NEXT_STAGE_LSB +: 8];
                            out_next_opcode <= fp_scratch[FP_NEXT_OPCODE_LSB +: OPCODE_WIDTH];
                            out_next_use_immediate <= fp_scratch[FP_NEXT_USE_IMM_LSB];
                            out_next_immediate_b <= fp_scratch[FP_NEXT_IMM_LSB +: DATA_WIDTH];
                            fp_dst_p = fp_scratch[FP_NEXT_DST_PRIMARY_LSB +: 8];
                            fp_dst_f = fp_scratch[FP_NEXT_DST_FALLBACK_LSB +: 8];
                            out_dst_core <= (peer_busy[fp_dst_p] && !peer_busy[fp_dst_f]) ? fp_dst_f : fp_dst_p;
                        end else begin
                            out_is_fast_path <= 1'b0;
                            out_dst_core <= COORD_DST_SENTINEL;
                        end

                        cache_valid[victim_index] <= 1'b1;
                        cache_opcode[victim_index] <= active_opcode;
                        cache_a[victim_index] <= op_a;
                        cache_b[victim_index] <= op_b;
                        cache_value[victim_index] <= alu_out_valid ? alu_out_value : result_latched_value;
                        if (CACHE_LRU_ENABLED) begin
                            cache_age[victim_index] <= cache_age_clock;
                            cache_age_clock <= cache_age_clock + 32'd1;
                        end else begin
                            if (cache_replace_ptr == (CACHE_ENTRIES - 1)) begin
                                cache_replace_ptr <= 8'd0;
                            end else begin
                                cache_replace_ptr <= cache_replace_ptr + 8'd1;
                            end
                        end

                        result_latched_valid <= 1'b0;
                        state <= ST_OUT;
                    end
                end

                ST_OUT: begin
                    if (out_valid && out_ready) begin
                        state <= ST_IDLE;
                    end
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end
endmodule
"""


def _render_core() -> str:
    return """`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core #(
    parameter CORE_X = 0,
    parameter CORE_Y = 0,
    parameter CORE_Z = 0,
    parameter integer CORE_INDEX = 0,
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter CORE_COUNT = `WAU_CORE_COUNT,
    parameter STATION_PROGRAM_ENABLE = `WAU_STATION_PROGRAM_ENABLE,
    parameter [7:0] COORD_DST_SENTINEL = 8'd0
) (
    input wire clk,
    input wire rst_n,

    input wire dispatch_valid,
    output wire dispatch_ready,
    input wire [7:0] dispatch_tag,
    input wire [FLOW_ID_WIDTH-1:0] dispatch_flow_id,
    input wire [OPCODE_WIDTH-1:0] dispatch_opcode,
    input wire signed [DATA_WIDTH-1:0] dispatch_a,
    input wire signed [DATA_WIDTH-1:0] dispatch_b,
    input wire dispatch_use_immediate,
    input wire signed [DATA_WIDTH-1:0] dispatch_immediate_b,
    input wire [7:0] dispatch_stage_id,

    // Fast-path self-dispatch, from the fabric's data plane (see
    // `_render_fabric_binding`) rather than the coordinator's control plane.
    input wire self_dispatch_valid,
    output wire self_dispatch_ready,
    input wire [7:0] self_dispatch_tag,
    input wire [FLOW_ID_WIDTH-1:0] self_dispatch_flow_id,
    input wire [OPCODE_WIDTH-1:0] self_dispatch_opcode,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_a,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_b_reg,
    input wire self_dispatch_use_immediate,
    input wire signed [DATA_WIDTH-1:0] self_dispatch_immediate_b,
    input wire [7:0] self_dispatch_stage_id,
    input wire [CORE_COUNT-1:0] peer_busy,

    output wire result_valid,
    input wire result_ready,
    output wire [7:0] result_tag,
    output wire [FLOW_ID_WIDTH-1:0] result_flow_id,
    output wire [7:0] result_stage_id,
    output wire signed [DATA_WIDTH-1:0] result_value,

    output wire signed [DATA_WIDTH-1:0] result_b_reg,
    output wire result_is_fast_path,
    output wire [7:0] result_dst_core,
    output wire [7:0] result_next_stage_id,
    output wire [OPCODE_WIDTH-1:0] result_next_opcode,
    output wire result_next_use_immediate,
    output wire signed [DATA_WIDTH-1:0] result_next_immediate_b,

    output wire busy,
    output wire cache_hit,
    output wire [31:0] cache_hit_count,
    output wire [31:0] cache_lookup_count
);
    wau_core_station #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH),
        .CORE_COUNT(CORE_COUNT),
        .STATION_PROGRAM_ENABLE(STATION_PROGRAM_ENABLE),
        .COORD_DST_SENTINEL(COORD_DST_SENTINEL),
        .CORE_INDEX(CORE_INDEX)
    ) station_u (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(dispatch_valid),
        .in_ready(dispatch_ready),
        .in_tag(dispatch_tag),
        .in_flow_id(dispatch_flow_id),
        .in_opcode(dispatch_opcode),
        .in_a(dispatch_a),
        .in_b(dispatch_b),
        .in_use_immediate(dispatch_use_immediate),
        .in_immediate_b(dispatch_immediate_b),
        .in_stage_id(dispatch_stage_id),
        .self_valid(self_dispatch_valid),
        .self_ready(self_dispatch_ready),
        .self_tag(self_dispatch_tag),
        .self_flow_id(self_dispatch_flow_id),
        .self_opcode(self_dispatch_opcode),
        .self_a(self_dispatch_a),
        .self_b_reg(self_dispatch_b_reg),
        .self_use_immediate(self_dispatch_use_immediate),
        .self_immediate_b(self_dispatch_immediate_b),
        .self_stage_id(self_dispatch_stage_id),
        .peer_busy(peer_busy),
        .out_valid(result_valid),
        .out_ready(result_ready),
        .out_tag(result_tag),
        .out_flow_id(result_flow_id),
        .out_stage_id(result_stage_id),
        .out_value(result_value),
        .out_b_reg(result_b_reg),
        .out_is_fast_path(result_is_fast_path),
        .out_dst_core(result_dst_core),
        .out_next_stage_id(result_next_stage_id),
        .out_next_opcode(result_next_opcode),
        .out_next_use_immediate(result_next_use_immediate),
        .out_next_immediate_b(result_next_immediate_b),
        .busy(busy),
        .cache_hit(cache_hit),
        .cache_hit_count(cache_hit_count),
        .cache_lookup_count(cache_lookup_count)
    );
endmodule
"""


def _render_neighbor_forward() -> str:
    return """`timescale 1ns/1ps
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
"""


# Router port sets per topology. `lines` needs no vertical ports at all: each
# row is a self-contained highway whose only way off-line is its coordinator
# hub, which hangs off the west end's PREV link rather than costing a port.
_HIGHWAY_LINE_DIRS = ("local", "prev", "next")
_HIGHWAY_CHAIN_DIRS = ("local", "prev", "next", "up", "down")
_HIGHWAY_MATRIX_DIRS = ("local", "north", "south", "east", "west", "up", "down")

# Highway contract word layout, shared by the emitter, `wau_highway_contract`
# and the viewer. "how" = mode, "how much" = words, "how many times" = repeats.
_CONTRACT_WORD_WIDTH = 18
_CONTRACT_MODE_LSB = 0
_CONTRACT_WORDS_LSB = 2
_CONTRACT_REPEATS_LSB = 10
_CONTRACT_MODE_PONG = 0
_CONTRACT_MODE_BURST = 1
_CONTRACT_MODE_STREAM = 2
_CONTRACT_MODE_RESERVE = 3
_CONTRACT_MODE_NAMES = {
    _CONTRACT_MODE_PONG: "pong",
    _CONTRACT_MODE_BURST: "burst",
    _CONTRACT_MODE_STREAM: "stream",
    _CONTRACT_MODE_RESERVE: "reserve",
}


_HIGHWAY_ROUTER_BODY = """    integer out_i;
    integer in_i;
    integer init_i;
    integer selected_input;

    always @(*) begin
        for (init_i = 0; init_i < PORT_COUNT; init_i = init_i + 1) begin
            in_ready_r[init_i] = 1'b0;
            out_valid_r[init_i] = 1'b0;
            out_dst_r[init_i] = {CORE_ID_WIDTH{1'b0}};
            out_payload_r[init_i] = {PAYLOAD_WIDTH{1'b0}};
        end

        for (out_i = 0; out_i < PORT_COUNT; out_i = out_i + 1) begin
            selected_input = -1;
            for (in_i = 0; in_i < PORT_COUNT; in_i = in_i + 1) begin
                if ((selected_input < 0) && in_valid[in_i] && (route_dir(in_dst[in_i]) == out_i)) begin
                    selected_input = in_i;
                end
            end

            if (selected_input >= 0) begin
                out_valid_r[out_i] = 1'b1;
                out_dst_r[out_i] = in_dst[selected_input];
                out_payload_r[out_i] = in_payload[selected_input];
                in_ready_r[selected_input] = out_ready[out_i];
            end
        end
    end

    // Observability: count successful forwards (any direction's accepted handshake),
    // local-out deliveries (packets that exit the mesh at this node), and stalls
    // (input valid but downstream not ready, i.e. handshake denied on this cycle).
    reg [3:0] in_handshake_count;
    reg [3:0] stall_now;
    reg [3:0] forwarded_to_neighbour;
    integer count_i;

    always @(*) begin
        in_handshake_count = 4'd0;
        stall_now = 4'd0;
        forwarded_to_neighbour = 4'd0;
        for (count_i = 0; count_i < PORT_COUNT; count_i = count_i + 1) begin
            if (in_valid[count_i] && in_ready_r[count_i]) begin
                in_handshake_count = in_handshake_count + 4'd1;
            end
            if (in_valid[count_i] && !in_ready_r[count_i]) begin
                stall_now = stall_now + 4'd1;
            end
            if ((count_i != DIR_LOCAL) && out_valid_r[count_i] && out_ready[count_i]) begin
                forwarded_to_neighbour = forwarded_to_neighbour + 4'd1;
            end
        end
    end

    wire local_out_handshake = out_valid_r[DIR_LOCAL] && out_ready[DIR_LOCAL];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hop_count <= 32'd0;
            stall_count <= 32'd0;
            local_delivered_count <= 32'd0;
            forward_count <= 32'd0;
        end else begin
            hop_count <= hop_count + {28'd0, in_handshake_count};
            stall_count <= stall_count + {28'd0, stall_now};
            if (local_out_handshake) begin
                local_delivered_count <= local_delivered_count + 32'd1;
            end
            forward_count <= forward_count + {28'd0, forwarded_to_neighbour};
        end
    end
endmodule
"""


_HIGHWAY_MESH_CONTRACT_BLOCK = """    // Contract-bus admission, one bus per highway line. `admit` is a
    // registered decision, so gating the local port here adds no combinational
    // path through the routers. With the bus disabled (or idle) every core is
    // admitted and the highway behaves exactly as an ungoverned fabric.
    wire [CORE_COUNT-1:0] admit;
    wire [CORE_COUNT-1:0] core_in_valid;
    wire [CORE_COUNT-1:0] core_in_ready;
    wire [CORE_COUNT-1:0] local_accepted;

    assign core_in_valid = local_in_valid & admit;
    assign local_in_ready = core_in_ready & admit;
    assign local_accepted = local_in_valid & local_in_ready;

    wire [LINE_COUNT*32-1:0] line_grant_count;
    wire [LINE_COUNT*32-1:0] line_hold_cycles;
    wire [LINE_COUNT*32-1:0] line_defer_count;

    genvar gl;
    generate
        if (CONTRACT_BUS_ENABLE != 0) begin : gen_contract_bus
            // Each line arbitrates on its own: a contract taken out on one row
            // never holds off traffic on another.
            for (gl = 0; gl < LINE_COUNT; gl = gl + 1) begin : gen_line_bus
                wau_highway_contract #(
                    .CORE_COUNT(LINE_SIZE),
                    .CORE_ID_WIDTH(CORE_ID_WIDTH),
                    .WORD_WIDTH(CONTRACT_WORD_WIDTH),
                    .MAX_BURST(CONTRACT_MAX_BURST),
                    .LEASE_CYCLES(CONTRACT_LEASE_CYCLES)
                ) contract_u (
                    .clk(clk),
                    .rst_n(rst_n),
                    .req(contract_req[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .word(contract_word[(gl*LINE_SIZE*CONTRACT_WORD_WIDTH) +: (LINE_SIZE*CONTRACT_WORD_WIDTH)]),
                    .pending(local_in_valid[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .accepted(local_accepted[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .admit(admit[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .call(contract_call[(gl*LINE_SIZE) +: LINE_SIZE]),
                    .slot(contract_slot[(gl*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .grant_valid(contract_grant_valid[gl]),
                    .grant_core(contract_grant_core[(gl*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),
                    .grant_mode(contract_grant_mode[(gl*2) +: 2]),
                    .grant_remaining(contract_grant_remaining[(gl*16) +: 16]),
                    .grant_lease(),
                    .grant_count(line_grant_count[(gl*32) +: 32]),
                    .hold_cycles(line_hold_cycles[(gl*32) +: 32]),
                    .defer_count(line_defer_count[(gl*32) +: 32])
                );
            end
        end else begin : gen_no_contract_bus
            assign admit = {CORE_COUNT{1'b1}};
            assign contract_call = {CORE_COUNT{1'b0}};
            assign contract_slot = {(LINE_COUNT*CORE_ID_WIDTH){1'b0}};
            assign contract_grant_valid = {LINE_COUNT{1'b0}};
            assign contract_grant_core = {(LINE_COUNT*CORE_ID_WIDTH){1'b0}};
            assign contract_grant_mode = {(LINE_COUNT*2){1'b0}};
            assign contract_grant_remaining = {(LINE_COUNT*16){1'b0}};
            assign line_grant_count = {(LINE_COUNT*32){1'b0}};
            assign line_hold_cycles = {(LINE_COUNT*32){1'b0}};
            assign line_defer_count = {(LINE_COUNT*32){1'b0}};
        end
    endgenerate

    // Fabric-wide contract totals: the per-line buses summed, so the
    // observability bus keeps one meaning across every topology.
    reg [31:0] total_grant_count;
    reg [31:0] total_hold_cycles;
    reg [31:0] total_defer_count;
    integer line_i;
    always @(*) begin
        total_grant_count = 32'd0;
        total_hold_cycles = 32'd0;
        total_defer_count = 32'd0;
        for (line_i = 0; line_i < LINE_COUNT; line_i = line_i + 1) begin
            total_grant_count = total_grant_count + line_grant_count[(line_i*32) +: 32];
            total_hold_cycles = total_hold_cycles + line_hold_cycles[(line_i*32) +: 32];
            total_defer_count = total_defer_count + line_defer_count[(line_i*32) +: 32];
        end
    end
    assign contract_grant_count = total_grant_count;
    assign contract_hold_cycles = total_hold_cycles;
    assign contract_defer_count = total_defer_count;

"""


def _highway_dirs(project: CompiledProject) -> tuple[str, ...]:
    highway = project.config.device.highway
    if highway.is_lines:
        return _HIGHWAY_LINE_DIRS
    if highway.is_chain:
        return _HIGHWAY_CHAIN_DIRS
    return _HIGHWAY_MATRIX_DIRS


def _highway_line_count(project: CompiledProject) -> int:
    dev = project.config.device
    return dev.highway.line_count(dev.grid_x, dev.grid_y, dev.grid_z)


def _highway_line_size(project: CompiledProject) -> int:
    dev = project.config.device
    return dev.highway.line_size(dev.grid_x, dev.grid_y, dev.grid_z)


def _encode_contract_word(mode: int, words: int, repeats: int) -> int:
    return (
        (mode & 0x3) << _CONTRACT_MODE_LSB
        | (words & 0xFF) << _CONTRACT_WORDS_LSB
        | (repeats & 0xFF) << _CONTRACT_REPEATS_LSB
    )


def contract_rom_entries(
    project: CompiledProject, schedule: SchedulePlan
) -> list[tuple[int, int, int]]:
    """Derive each core's *programmed* highway expectation from the schedule.

    The contract bus accepts two kinds of answer on a core's offered slot: a
    bare request bit, or a contract saying how the core intends to use the
    highway. This builds the latter from what the offline scheduler already
    knows, so a core that will push a run of results for one flow asks for the
    whole run once instead of re-arbitrating per beat:

    - ``words``   — the longest run of instructions the core executes for a
      single flow id (how much it transmits in one go), clamped to the
      synthesised ``contract_max_burst``;
    - ``repeats`` — how many distinct flow ids land on the core (how many times
      it expects to come back);
    - ``mode``    — ``pong`` for a single isolated beat, ``burst`` for one run,
      ``stream`` for several.

    Cores with no scheduled work get a ``pong`` contract, which is exactly the
    "request bit" behaviour and reserves nothing.
    """
    cfg = project.config
    core_count = cfg.device.grid_x * cfg.device.grid_y * cfg.device.grid_z
    max_burst = cfg.device.highway.contract_max_burst

    per_core: list[dict[int, int]] = [dict() for _ in range(core_count)]
    for ins in schedule.instructions:
        if 0 <= ins.core_index < core_count:
            counts = per_core[ins.core_index]
            counts[ins.flow_id] = counts.get(ins.flow_id, 0) + 1

    entries: list[tuple[int, int, int]] = []
    for counts in per_core:
        if not counts:
            entries.append((_CONTRACT_MODE_PONG, 1, 1))
            continue
        words = min(max(counts.values()), max_burst)
        repeats = min(len(counts), 255)
        if words <= 1 and repeats <= 1:
            mode = _CONTRACT_MODE_PONG
        elif repeats <= 1:
            mode = _CONTRACT_MODE_BURST
        else:
            mode = _CONTRACT_MODE_STREAM
        entries.append((mode, max(words, 1), max(repeats, 1)))
    return entries


def _render_highway_contract() -> str:
    """The highway's contracting bus.

    One core slot is offered per clock. The offered core answers either with a
    bare request bit (``pong`` — a single beat, no reservation) or with a
    contract word describing how it intends to occupy this highway. While a
    contract is in force the highway admits *only* its holder, so a contracted
    transfer never interleaves with another core's traffic and never has to
    wait for its slot to come round again. Every contract is bounded twice —
    by its beat count and by a hard lease — so the bus always makes progress
    even if the holder goes quiet.
    """
    return """`timescale 1ns/1ps
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
"""


def _render_highway_router(project: CompiledProject) -> str:
    """Per-core highway router.

    The port set follows ``device.highway.topology``: ``lines`` keeps
    LOCAL/PREV/NEXT and routes against compile-time line bounds; ``chain`` adds
    UP/DOWN for layered grids and routes against this core's index; ``matrix``
    keeps the full N/S/E/W/U/D mesh with X-then-Y-then-Z dimension-order
    routing. The arbitration and observability logic below is shared by all
    three.
    """
    highway = project.config.device.highway
    dirs = _highway_dirs(project)
    port_count = len(dirs)

    lines: list[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append('`include "wau_defs.vh"')
    lines.append("")
    lines.append("module wau_highway_router #(")
    lines.append("    parameter CORE_INDEX = 0,")
    lines.append("    parameter CORE_X = 0,")
    lines.append("    parameter CORE_Y = 0,")
    lines.append("    parameter CORE_Z = 0,")
    lines.append("    parameter GRID_X = `WAU_GRID_X,")
    lines.append("    parameter GRID_Y = `WAU_GRID_Y,")
    lines.append("    parameter CORE_ID_WIDTH = 8,")
    lines.append("    parameter PAYLOAD_WIDTH = 64")
    lines.append(") (")
    lines.append("    input wire clk,")
    lines.append("    input wire rst_n,")
    lines.append("")
    lines.append("    output reg [31:0] hop_count,")
    lines.append("    output reg [31:0] stall_count,")
    lines.append("    output reg [31:0] local_delivered_count,")
    lines.append("    output reg [31:0] forward_count,")
    port_blocks: list[str] = []
    for name in dirs:
        port_blocks.append(
            "\n".join(
                [
                    f"    input wire {name}_in_valid,",
                    f"    output wire {name}_in_ready,",
                    f"    input wire [CORE_ID_WIDTH-1:0] {name}_in_dst,",
                    f"    input wire [PAYLOAD_WIDTH-1:0] {name}_in_payload,",
                    "",
                    f"    output wire {name}_out_valid,",
                    f"    input wire {name}_out_ready,",
                    f"    output wire [CORE_ID_WIDTH-1:0] {name}_out_dst,",
                    f"    output wire [PAYLOAD_WIDTH-1:0] {name}_out_payload",
                ]
            )
        )
    lines.append("")
    lines.append(",\n\n".join(port_blocks))
    lines.append(");")

    for i, name in enumerate(dirs):
        lines.append(f"    localparam DIR_{name.upper()} = 3'd{i};")
    lines.append(f"    localparam PORT_COUNT = {port_count};")
    lines.append("")

    if highway.is_lines:
        lines.append("    // One highway per line of cores. Everything off this line leaves")
        lines.append("    // through the coordinator hub hanging off the west end, so the")
        lines.append("    // router only has to ask: is the destination further along MY line?")
        lines.append("    // Both bounds are elaboration-time constants, so this costs a pair")
        lines.append("    // of comparators and no divider at all.")
        lines.append(
            "    localparam [CORE_ID_WIDTH-1:0] LINE_LAST ="
            " (CORE_INDEX - CORE_X) + GRID_X - 1;"
        )
        lines.append("")
        lines.append("    // Reserved destination meaning \"leave this line\": the grid is")
        lines.append("    // capped at 255 cores, so the all-ones id is never a real core and")
        lines.append("    // falls out of the west end into the hub under the rule below.")
        lines.append(
            "    localparam [CORE_ID_WIDTH-1:0] HUB_DST = {CORE_ID_WIDTH{1'b1}};"
        )
        lines.append("")
        lines.append("    function [2:0] route_dir;")
        lines.append("        input [CORE_ID_WIDTH-1:0] dst_core;")
        lines.append("        begin")
        lines.append("            if (dst_core == HUB_DST) begin")
        lines.append("                route_dir = DIR_PREV;")
        lines.append("            end else if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin")
        lines.append("                route_dir = DIR_LOCAL;")
        lines.append(
            "            end else if ((dst_core > CORE_INDEX[CORE_ID_WIDTH-1:0])"
        )
        lines.append("                       && (dst_core <= LINE_LAST)) begin")
        lines.append("                route_dir = DIR_NEXT;")
        lines.append("            end else begin")
        lines.append("                // Either back along this line, or off it entirely --")
        lines.append("                // both head west, and off-line traffic falls out of the")
        lines.append("                // first core's PREV port into the hub.")
        lines.append("                route_dir = DIR_PREV;")
        lines.append("            end")
        lines.append("        end")
        lines.append("    endfunction")
    elif highway.is_chain:
        lines.append("    // One 1-D highway per layer, walked in core-index order: the last")
        lines.append("    // core of a row is the previous hop of the first core of the next")
        lines.append("    // row. Comparing plain indices keeps the router free of the")
        lines.append("    // per-port modulo/divide the matrix topology needs to recover x/y,")
        lines.append("    // which is what makes a non-power-of-two grid infer an LPM_DIVIDE.")
        lines.append("    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;")
        lines.append(
            "    localparam [CORE_ID_WIDTH-1:0] LAYER_FIRST = CORE_Z * LAYER_CORE_COUNT;"
        )
        lines.append(
            "    localparam [CORE_ID_WIDTH-1:0] LAYER_LAST ="
            " (CORE_Z * LAYER_CORE_COUNT) + LAYER_CORE_COUNT - 1;"
        )
        lines.append("")
        lines.append("    function [2:0] route_dir;")
        lines.append("        input [CORE_ID_WIDTH-1:0] dst_core;")
        lines.append("        begin")
        lines.append("            if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin")
        lines.append("                route_dir = DIR_LOCAL;")
        lines.append("            end else if (dst_core < LAYER_FIRST) begin")
        lines.append("                route_dir = DIR_UP;")
        lines.append("            end else if (dst_core > LAYER_LAST) begin")
        lines.append("                route_dir = DIR_DOWN;")
        lines.append("            end else if (dst_core > CORE_INDEX[CORE_ID_WIDTH-1:0]) begin")
        lines.append("                route_dir = DIR_NEXT;")
        lines.append("            end else begin")
        lines.append("                route_dir = DIR_PREV;")
        lines.append("            end")
        lines.append("        end")
        lines.append("    endfunction")
    else:
        lines.append("    function [2:0] route_dir;")
        lines.append("        input [CORE_ID_WIDTH-1:0] dst_core;")
        lines.append("        integer dst_x;")
        lines.append("        integer dst_y;")
        lines.append("        integer dst_z;")
        lines.append("        begin")
        lines.append("            if (dst_core == CORE_INDEX[CORE_ID_WIDTH-1:0]) begin")
        lines.append("                route_dir = DIR_LOCAL;")
        lines.append("            end else begin")
        lines.append("                dst_x = dst_core % GRID_X;")
        lines.append("                dst_y = (dst_core / GRID_X) % GRID_Y;")
        lines.append("                dst_z = dst_core / (GRID_X * GRID_Y);")
        lines.append("")
        lines.append("                if (dst_x > CORE_X) begin")
        lines.append("                    route_dir = DIR_EAST;")
        lines.append("                end else if (dst_x < CORE_X) begin")
        lines.append("                    route_dir = DIR_WEST;")
        lines.append("                end else if (dst_y > CORE_Y) begin")
        lines.append("                    route_dir = DIR_SOUTH;")
        lines.append("                end else if (dst_y < CORE_Y) begin")
        lines.append("                    route_dir = DIR_NORTH;")
        lines.append("                end else if (dst_z > CORE_Z) begin")
        lines.append("                    route_dir = DIR_DOWN;")
        lines.append("                end else begin")
        lines.append("                    route_dir = DIR_UP;")
        lines.append("                end")
        lines.append("            end")
        lines.append("        end")
        lines.append("    endfunction")

    lines.append("")
    lines.append("    wire in_valid [0:PORT_COUNT-1];")
    lines.append("    wire [CORE_ID_WIDTH-1:0] in_dst [0:PORT_COUNT-1];")
    lines.append("    wire [PAYLOAD_WIDTH-1:0] in_payload [0:PORT_COUNT-1];")
    lines.append("    wire out_ready [0:PORT_COUNT-1];")
    lines.append("")
    lines.append("    reg in_ready_r [0:PORT_COUNT-1];")
    lines.append("    reg out_valid_r [0:PORT_COUNT-1];")
    lines.append("    reg [CORE_ID_WIDTH-1:0] out_dst_r [0:PORT_COUNT-1];")
    lines.append("    reg [PAYLOAD_WIDTH-1:0] out_payload_r [0:PORT_COUNT-1];")

    for field, source in (
        ("in_valid", "{name}_in_valid"),
        ("in_dst", "{name}_in_dst"),
        ("in_payload", "{name}_in_payload"),
        ("out_ready", "{name}_out_ready"),
    ):
        lines.append("")
        for name in dirs:
            lines.append(
                f"    assign {field}[DIR_{name.upper()}] = {source.format(name=name)};"
            )

    for target, source in (
        ("{name}_in_ready", "in_ready_r"),
        ("{name}_out_valid", "out_valid_r"),
        ("{name}_out_dst", "out_dst_r"),
        ("{name}_out_payload", "out_payload_r"),
    ):
        lines.append("")
        for name in dirs:
            lines.append(
                f"    assign {target.format(name=name)} = {source}[DIR_{name.upper()}];"
            )

    lines.append("")
    lines.append(_HIGHWAY_ROUTER_BODY)
    return "\n".join(lines)


def _render_highway_mesh(project: CompiledProject) -> str:
    """Wire one router per core into the configured highway topology.

    Every topology exposes the same module interface — per-core ``local_*``
    ports, per-line ``hub_*`` ports and a per-line contract bus — so `wau_top`
    and the testbenches are written once. What differs is how many highways
    there are and how they are joined:

    - ``lines``  — ``GRID_Y * GRID_Z`` independent highways, one per row of
      ``GRID_X`` cores. Each is a self-contained PREV/NEXT run whose west end
      opens onto its own coordinator hub, so rows neither share wires nor share
      arbitration and move in parallel.
    - ``chain``  — one highway per layer, walked in core-index order, joined
      vertically. The hubs are unused: the coordinator keeps using core 0's
      local port.
    - ``matrix`` — the full neighbour mesh, hubs likewise unused.
    """
    highway = project.config.device.highway
    dirs = _highway_dirs(project)
    per_line = highway.is_lines

    line_count = "GRID_Y * GRID_Z" if per_line else "1"
    line_size = "GRID_X" if per_line else "CORE_COUNT"

    lines: list[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append('`include "wau_defs.vh"')
    lines.append("")
    lines.append("module wau_highway_mesh #(")
    lines.append("    parameter GRID_X = `WAU_GRID_X,")
    lines.append("    parameter GRID_Y = `WAU_GRID_Y,")
    lines.append("    parameter GRID_Z = `WAU_GRID_Z,")
    lines.append("    parameter CORE_COUNT = `WAU_CORE_COUNT,")
    lines.append("    parameter CORE_ID_WIDTH = 8,")
    lines.append("    parameter PAYLOAD_WIDTH = 64,")
    lines.append("    parameter CONTRACT_BUS_ENABLE = 0,")
    lines.append("    parameter CONTRACT_WORD_WIDTH = `WAU_HIGHWAY_CONTRACT_WORD_WIDTH,")
    lines.append("    parameter CONTRACT_MAX_BURST = `WAU_HIGHWAY_CONTRACT_MAX_BURST,")
    lines.append("    parameter CONTRACT_LEASE_CYCLES = `WAU_HIGHWAY_CONTRACT_LEASE_CYCLES,")
    lines.append(f"    parameter LINE_COUNT = {line_count},")
    lines.append(f"    parameter LINE_SIZE = {line_size}")
    lines.append(") (")
    lines.append("    input wire clk,")
    lines.append("    input wire rst_n,")
    lines.append("")
    lines.append("    input wire [CORE_COUNT-1:0] local_in_valid,")
    lines.append("    output wire [CORE_COUNT-1:0] local_in_ready,")
    lines.append("    input wire [CORE_COUNT*CORE_ID_WIDTH-1:0] local_in_dst,")
    lines.append("    input wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_in_payload,")
    lines.append("")
    lines.append("    output wire [CORE_COUNT-1:0] local_out_valid,")
    lines.append("    input wire [CORE_COUNT-1:0] local_out_ready,")
    lines.append("    output wire [CORE_COUNT*CORE_ID_WIDTH-1:0] local_out_dst,")
    lines.append("    output wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] local_out_payload,")
    lines.append("")
    lines.append("    // Coordinator hubs: one per highway line. Anything addressed off a")
    lines.append("    // line leaves through its own hub, which is what lets the lines run")
    lines.append("    // independently instead of funnelling through a shared spine.")
    lines.append("    input wire [LINE_COUNT-1:0] hub_in_valid,")
    lines.append("    output wire [LINE_COUNT-1:0] hub_in_ready,")
    lines.append("    input wire [LINE_COUNT*CORE_ID_WIDTH-1:0] hub_in_dst,")
    lines.append("    input wire [LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_in_payload,")
    lines.append("")
    lines.append("    output wire [LINE_COUNT-1:0] hub_out_valid,")
    lines.append("    input wire [LINE_COUNT-1:0] hub_out_ready,")
    lines.append("    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] hub_out_dst,")
    lines.append("    output wire [LINE_COUNT*PAYLOAD_WIDTH-1:0] hub_out_payload,")
    lines.append("")
    lines.append("    // Contracting bus, one per highway line: a slot is offered per clock")
    lines.append("    // and the slot owner answers with a request bit or a full contract.")
    lines.append("    // Slot/grant ids are line-local (0..LINE_SIZE-1).")
    lines.append("    input wire [CORE_COUNT-1:0] contract_req,")
    lines.append("    input wire [CORE_COUNT*CONTRACT_WORD_WIDTH-1:0] contract_word,")
    lines.append("    output wire [CORE_COUNT-1:0] contract_call,")
    lines.append("    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] contract_slot,")
    lines.append("    output wire [LINE_COUNT-1:0] contract_grant_valid,")
    lines.append("    output wire [LINE_COUNT*CORE_ID_WIDTH-1:0] contract_grant_core,")
    lines.append("    output wire [LINE_COUNT*2-1:0] contract_grant_mode,")
    lines.append("    output wire [LINE_COUNT*16-1:0] contract_grant_remaining,")
    lines.append("    output wire [31:0] contract_grant_count,")
    lines.append("    output wire [31:0] contract_hold_cycles,")
    lines.append("    output wire [31:0] contract_defer_count,")
    lines.append("")
    lines.append("    output wire [CORE_COUNT*32-1:0] router_hop_count,")
    lines.append("    output wire [CORE_COUNT*32-1:0] router_stall_count,")
    lines.append("    output wire [CORE_COUNT*32-1:0] router_local_delivered_count,")
    lines.append("    output wire [CORE_COUNT*32-1:0] router_forward_count")
    lines.append(");")

    for name in dirs:
        if name == "local":
            continue
        lines.append(f"    wire [CORE_COUNT-1:0] {name}_in_valid;")
        lines.append(f"    wire [CORE_COUNT-1:0] {name}_in_ready;")
        lines.append(f"    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] {name}_in_dst;")
        lines.append(f"    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] {name}_in_payload;")
        lines.append(f"    wire [CORE_COUNT-1:0] {name}_out_valid;")
        lines.append(f"    wire [CORE_COUNT-1:0] {name}_out_ready;")
        lines.append(f"    wire [CORE_COUNT*CORE_ID_WIDTH-1:0] {name}_out_dst;")
        lines.append(f"    wire [CORE_COUNT*PAYLOAD_WIDTH-1:0] {name}_out_payload;")
        lines.append("")

    lines.append(_HIGHWAY_MESH_CONTRACT_BLOCK)

    if not per_line:
        lines.append("    // chain/matrix keep a single highway reached through core 0's")
        lines.append("    // local port, so the hub ports stay inert.")
        lines.append("    assign hub_in_ready = {LINE_COUNT{1'b1}};")
        lines.append("    assign hub_out_valid = {LINE_COUNT{1'b0}};")
        lines.append("    assign hub_out_dst = {(LINE_COUNT*CORE_ID_WIDTH){1'b0}};")
        lines.append("    assign hub_out_payload = {(LINE_COUNT*PAYLOAD_WIDTH){1'b0}};")
        lines.append("")

    lines.append("    localparam integer LAYER_CORE_COUNT = GRID_X * GRID_Y;")
    lines.append("")
    lines.append("    genvar gz;")
    lines.append("    genvar gy;")
    lines.append("    genvar gx;")
    lines.append("    generate")
    lines.append("        for (gz = 0; gz < GRID_Z; gz = gz + 1) begin : gen_z")
    lines.append("        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y")
    lines.append("            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x")
    lines.append(
        "                localparam integer CORE_INDEX ="
        " (gz * LAYER_CORE_COUNT) + (gy * GRID_X) + gx;"
    )
    if per_line:
        lines.append("                localparam integer LINE_INDEX = (gz * GRID_Y) + gy;")
        lines.append("                localparam integer PREV_INDEX = CORE_INDEX - 1;")
        lines.append("                localparam integer NEXT_INDEX = CORE_INDEX + 1;")
    elif highway.is_chain:
        lines.append("                localparam integer PREV_INDEX = CORE_INDEX - 1;")
        lines.append("                localparam integer NEXT_INDEX = CORE_INDEX + 1;")
    else:
        lines.append("                localparam integer NORTH_INDEX = CORE_INDEX - GRID_X;")
        lines.append("                localparam integer SOUTH_INDEX = CORE_INDEX + GRID_X;")
        lines.append("                localparam integer EAST_INDEX = CORE_INDEX + 1;")
        lines.append("                localparam integer WEST_INDEX = CORE_INDEX - 1;")
    if "up" in dirs:
        lines.append(
            "                localparam integer UP_INDEX = CORE_INDEX - LAYER_CORE_COUNT;"
        )
        lines.append(
            "                localparam integer DOWN_INDEX = CORE_INDEX + LAYER_CORE_COUNT;"
        )
    lines.append("")
    lines.append("                wau_highway_router #(")
    lines.append("                    .CORE_INDEX(CORE_INDEX),")
    lines.append("                    .CORE_X(gx),")
    lines.append("                    .CORE_Y(gy),")
    lines.append("                    .CORE_Z(gz),")
    lines.append("                    .GRID_X(GRID_X),")
    lines.append("                    .GRID_Y(GRID_Y),")
    lines.append("                    .CORE_ID_WIDTH(CORE_ID_WIDTH),")
    lines.append("                    .PAYLOAD_WIDTH(PAYLOAD_WIDTH)")
    lines.append("                ) router_u (")
    lines.append("                    .clk(clk),")
    lines.append("                    .rst_n(rst_n),")
    lines.append("                    .hop_count(router_hop_count[(CORE_INDEX*32) +: 32]),")
    lines.append("                    .stall_count(router_stall_count[(CORE_INDEX*32) +: 32]),")
    lines.append(
        "                    .local_delivered_count("
        "router_local_delivered_count[(CORE_INDEX*32) +: 32]),"
    )
    lines.append(
        "                    .forward_count(router_forward_count[(CORE_INDEX*32) +: 32]),"
    )
    conn: list[str] = []
    for name in dirs:
        # The local port is the contract-gated view of the module's local port.
        vin = (
            "core_in_valid[CORE_INDEX]"
            if name == "local"
            else f"{name}_in_valid[CORE_INDEX]"
        )
        rin = (
            "core_in_ready[CORE_INDEX]"
            if name == "local"
            else f"{name}_in_ready[CORE_INDEX]"
        )
        conn.append(f"                    .{name}_in_valid({vin}),")
        conn.append(f"                    .{name}_in_ready({rin}),")
        conn.append(
            f"                    .{name}_in_dst("
            f"{name}_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        conn.append(
            f"                    .{name}_in_payload("
            f"{name}_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),"
        )
        conn.append(f"                    .{name}_out_valid({name}_out_valid[CORE_INDEX]),")
        conn.append(f"                    .{name}_out_ready({name}_out_ready[CORE_INDEX]),")
        conn.append(
            f"                    .{name}_out_dst("
            f"{name}_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        conn.append(
            f"                    .{name}_out_payload("
            f"{name}_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),"
        )
    conn[-1] = conn[-1].rstrip(",")
    lines.extend(conn)
    lines.append("                );")
    lines.append("")

    if per_line:
        # The west end of every line opens onto that line's coordinator hub
        # instead of being tied off -- no extra router port, and the hub is
        # simply "what is on the other side of core x=0's PREV link".
        lines.append("                if (gx == 0) begin : prev_hub")
        lines.append(
            "                    assign prev_in_valid[CORE_INDEX] = hub_in_valid[LINE_INDEX];"
        )
        lines.append(
            "                    assign hub_in_ready[LINE_INDEX] = prev_in_ready[CORE_INDEX];"
        )
        lines.append(
            "                    assign prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] ="
            " hub_in_dst[(LINE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH];"
        )
        lines.append(
            "                    assign prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] ="
            " hub_in_payload[(LINE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH];"
        )
        lines.append(
            "                    assign hub_out_valid[LINE_INDEX] = prev_out_valid[CORE_INDEX];"
        )
        lines.append(
            "                    assign prev_out_ready[CORE_INDEX] = hub_out_ready[LINE_INDEX];"
        )
        lines.append(
            "                    assign hub_out_dst[(LINE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] ="
            " prev_out_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH];"
        )
        lines.append(
            "                    assign hub_out_payload[(LINE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] ="
            " prev_out_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH];"
        )
        lines.append("                end else begin : prev_link")
        lines.append("                    wau_neighbor_forward #(")
        lines.append("                        .CORE_ID_WIDTH(CORE_ID_WIDTH),")
        lines.append("                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)")
        lines.append("                    ) from_prev_u (")
        lines.append("                        .in_valid(next_out_valid[PREV_INDEX]),")
        lines.append("                        .in_ready(next_out_ready[PREV_INDEX]),")
        lines.append(
            "                        .in_dst(next_out_dst[(PREV_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        lines.append(
            "                        .in_payload(next_out_payload[(PREV_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),"
        )
        lines.append("                        .out_valid(prev_in_valid[CORE_INDEX]),")
        lines.append("                        .out_ready(prev_in_ready[CORE_INDEX]),")
        lines.append(
            "                        .out_dst(prev_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        lines.append(
            "                        .out_payload(prev_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])"
        )
        lines.append("                    );")
        lines.append("                end")
        lines.append("")
        link_specs = [("next", "prev", "NEXT_INDEX", "gx == GRID_X - 1")]
    elif highway.is_chain:
        link_specs = [
            ("prev", "next", "PREV_INDEX", "(gx == 0) && (gy == 0)"),
            ("next", "prev", "NEXT_INDEX", "(gx == GRID_X - 1) && (gy == GRID_Y - 1)"),
            ("up", "down", "UP_INDEX", "gz == 0"),
            ("down", "up", "DOWN_INDEX", "gz == GRID_Z - 1"),
        ]
    else:
        link_specs = [
            ("north", "south", "NORTH_INDEX", "gy == 0"),
            ("south", "north", "SOUTH_INDEX", "gy == GRID_Y - 1"),
            ("east", "west", "EAST_INDEX", "gx == GRID_X - 1"),
            ("west", "east", "WEST_INDEX", "gx == 0"),
            ("up", "down", "UP_INDEX", "gz == 0"),
            ("down", "up", "DOWN_INDEX", "gz == GRID_Z - 1"),
        ]

    for side, peer_side, peer_index, edge_cond in link_specs:
        lines.append(f"                if ({edge_cond}) begin : {side}_edge")
        lines.append(f"                    assign {side}_in_valid[CORE_INDEX] = 1'b0;")
        lines.append(
            f"                    assign {side}_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH] ="
            " {CORE_ID_WIDTH{1'b0}};"
        )
        lines.append(
            f"                    assign {side}_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH] ="
            " {PAYLOAD_WIDTH{1'b0}};"
        )
        lines.append(f"                    assign {side}_out_ready[CORE_INDEX] = 1'b1;")
        lines.append(f"                end else begin : {side}_link")
        lines.append("                    wau_neighbor_forward #(")
        lines.append("                        .CORE_ID_WIDTH(CORE_ID_WIDTH),")
        lines.append("                        .PAYLOAD_WIDTH(PAYLOAD_WIDTH)")
        lines.append(f"                    ) from_{side}_u (")
        lines.append(f"                        .in_valid({peer_side}_out_valid[{peer_index}]),")
        lines.append(f"                        .in_ready({peer_side}_out_ready[{peer_index}]),")
        lines.append(
            f"                        .in_dst({peer_side}_out_dst[({peer_index}*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        lines.append(
            f"                        .in_payload({peer_side}_out_payload[({peer_index}*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH]),"
        )
        lines.append(f"                        .out_valid({side}_in_valid[CORE_INDEX]),")
        lines.append(f"                        .out_ready({side}_in_ready[CORE_INDEX]),")
        lines.append(
            f"                        .out_dst({side}_in_dst[(CORE_INDEX*CORE_ID_WIDTH) +: CORE_ID_WIDTH]),"
        )
        lines.append(
            f"                        .out_payload({side}_in_payload[(CORE_INDEX*PAYLOAD_WIDTH) +: PAYLOAD_WIDTH])"
        )
        lines.append("                    );")
        lines.append("                end")
        lines.append("")

    lines.append("            end")
    lines.append("        end")
    lines.append("        end")
    lines.append("    endgenerate")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _stage_case_entries(project: CompiledProject, field: str) -> str:
    lines: list[str] = []
    for flow in project.flows:
        for stage in flow.stages:
            key = (flow.flow_slot << 8) | stage.stage_index
            if field == "opcode":
                value = f"{project.config.device.opcode_width}'h{stage.opcode:02X}"
            elif field == "primary_core":
                idx = core_index(
                    stage.primary_core.x,
                    stage.primary_core.y,
                    project.config.device.grid_x,
                    stage.primary_core.z,
                    project.config.device.grid_y,
                )
                value = f"8'd{idx}"
            elif field == "fallback_core":
                fallback = stage.fallback_core or stage.primary_core
                idx = core_index(
                    fallback.x,
                    fallback.y,
                    project.config.device.grid_x,
                    fallback.z,
                    project.config.device.grid_y,
                )
                value = f"8'd{idx}"
            elif field == "use_immediate":
                value = "1'b1" if stage.immediate_b is not None else "1'b0"
            elif field == "immediate_b":
                imm = stage.immediate_b if stage.immediate_b is not None else 0
                value = f"{project.config.device.data_width}'sd{imm}"
            else:
                raise ValueError(field)
            lines.append(f"                16'h{key:04X}: value = {value};")
    return "\n".join(lines)


def _flow_last_stage_entries(project: CompiledProject) -> str:
    lines = []
    for flow in project.flows:
        lines.append(
            f"                8'd{flow.flow_slot}: value = 8'd{len(flow.stages) - 1};"
        )
    return "\n".join(lines)


def _flow_slot_entries(project: CompiledProject) -> str:
    lines = []
    for flow in project.flows:
        lines.append(
            f"                {project.config.device.flow_id_width}'d{flow.flow_id}: value = 8'd{flow.flow_slot};"
        )
    return "\n".join(lines)


def _render_coordinator(project: CompiledProject) -> str:
    data_width = project.config.device.data_width
    flow_id_width = project.config.device.flow_id_width
    opcode_width = project.config.device.opcode_width
    flow_slot_default = "8'hFF"

    return f"""`timescale 1ns/1ps
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
    output reg [7:0] dispatch_pkt_tag,
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
    input wire [7:0] result_pkt_tag,
    input wire [FLOW_ID_WIDTH-1:0] result_pkt_flow_id,
    input wire [7:0] result_pkt_stage_id,
    input wire signed [DATA_WIDTH-1:0] result_pkt_value,

    input wire [CORE_COUNT-1:0] core_busy
);
    localparam MAX_IN_FLIGHT = `WAU_COORD_MAX_IN_FLIGHT;

    // Multi-issue slot table. Each slot holds one in-flight transaction's
    // accumulator context; an internal tag equal to the slot index travels with
    // every dispatch/result packet, so multiple inputs for the same flow id can
    // execute concurrently without ambiguous result matching. Per-transaction
    // semantics are identical to the legacy serial coordinator (one accumulator
    // chain walked stage-by-stage), so a single in-flight flow keeps
    // cycle-identical timing while independent flows overlap on different cores.
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
    reg [31:0]                   slot_order     [0:MAX_IN_FLIGHT-1];
    reg [31:0]                   alloc_order;
    reg [31:0]                   retire_order;

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
    integer    k;
    reg        latched_now;

    // The packet-carried slot tag makes same-flow transactions unambiguous, so
    // admission is limited only by physical coordinator capacity.
    assign host_in_ready = alloc_found;
    assign result_pkt_ready = res_found;

    function [7:0] flow_slot_from_id;
        input [FLOW_ID_WIDTH-1:0] flow_id;
        reg [7:0] value;
        begin
            value = {flow_slot_default};
            case (flow_id)
{_flow_slot_entries(project)}
                default: value = {flow_slot_default};
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
{_flow_last_stage_entries(project)}
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
            value = {{OPCODE_WIDTH{{1'b0}}}};
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "opcode")}
                default: value = {{OPCODE_WIDTH{{1'b0}}}};
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
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "primary_core")}
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
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "fallback_core")}
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
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "use_immediate")}
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
            value = {{DATA_WIDTH{{1'b0}}}};
            case ({{flow_slot, stage_idx}})
{_stage_case_entries(project, "immediate_b")}
                default: value = {{DATA_WIDTH{{1'b0}}}};
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
        dispatch_pkt_tag           = disp_slot;
        dispatch_pkt_flow_id       = slot_flow_id[disp_slot];
        dispatch_pkt_opcode        = flow_stage_opcode(sel_flow_slot, sel_stage);
        dispatch_pkt_a             = slot_acc[disp_slot];
        dispatch_pkt_b             = slot_opb[disp_slot];
        dispatch_pkt_use_immediate = flow_stage_use_immediate(sel_flow_slot, sel_stage);
        dispatch_pkt_immediate_b   = flow_stage_immediate_b(sel_flow_slot, sel_stage);
        dispatch_pkt_stage_id      = sel_stage;
    end

    // ---- result matcher: map an incoming result to its tagged slot --------
    // The transaction tag is the authoritative identity. Stage progression is
    // still checked so that a
    // chain of per-core fast-path hops (see wau_core_station) can run ahead
    // of this coordinator's stage counter without ever routing back to it:
    // the eventual hub-bound packet (always the flow's last stage -- see
    // compiler.build_fast_path_tables, which never gives a last stage a
    // fast-path entry) is still recognized and jumps slot_stage to wherever
    // the chain actually got to. With an empty fast-path table
    // every stage still round-trips one at a time and
    // result_pkt_stage_id == slot_stage[ri] always, making this
    // byte-identical to before. slot_wait_core stays populated for
    // debug/trace but is no longer part of the match.
    always @(*) begin
        res_found = 1'b0;
        res_slot  = 8'd0;
        for (ri = 0; ri < MAX_IN_FLIGHT; ri = ri + 1) begin
            if (!res_found && slot_valid[ri] && slot_awaiting[ri] &&
                (result_pkt_tag == ri[7:0]) &&
                (slot_flow_id[ri] == result_pkt_flow_id) &&
                (result_pkt_stage_id >= slot_stage[ri])) begin
                res_found = 1'b1;
                res_slot  = ri;
            end
        end
    end

    // ---- completed-output selector + free-slot guard --------------------
    always @(*) begin
        out_found = 1'b0;
        out_slot  = 8'd0;
        for (oi = 0; oi < MAX_IN_FLIGHT; oi = oi + 1) begin
            if (!out_found && slot_valid[oi] && slot_done[oi] &&
                (slot_order[oi] == retire_order)) begin
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

    // ---- sequential state ----------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            host_out_valid   <= 1'b0;
            host_out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            host_out_value   <= {{DATA_WIDTH{{1'b0}}}};
            alloc_order      <= 32'd0;
            retire_order     <= 32'd0;
            for (k = 0; k < MAX_IN_FLIGHT; k = k + 1) begin
                slot_valid[k]     <= 1'b0;
                slot_flow_slot[k] <= 8'hFF;
                slot_flow_id[k]   <= {{FLOW_ID_WIDTH{{1'b0}}}};
                slot_stage[k]     <= 8'd0;
                slot_acc[k]       <= {{DATA_WIDTH{{1'b0}}}};
                slot_opb[k]       <= {{DATA_WIDTH{{1'b0}}}};
                slot_awaiting[k]  <= 1'b0;
                slot_wait_core[k] <= 8'd0;
                slot_done[k]      <= 1'b0;
                slot_outval[k]    <= {{DATA_WIDTH{{1'b0}}}};
                slot_order[k]     <= 32'd0;
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
                    slot_order[alloc_slot]     <= alloc_order;
                    alloc_order                <= alloc_order + 32'd1;
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
                    if ((slot_order[res_slot] == retire_order) &&
                        (!host_out_valid || host_out_ready)) begin
                        host_out_valid       <= 1'b1;
                        host_out_flow_id     <= slot_flow_id[res_slot];
                        host_out_value       <= result_pkt_value;
                        slot_valid[res_slot] <= 1'b0;
                        slot_done[res_slot]  <= 1'b0;
                        retire_order         <= retire_order + 32'd1;
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
                retire_order         <= retire_order + 32'd1;
            end
        end
    end
endmodule
"""


_CORE_DISPATCH_UNPACK = """
            assign core_dispatch_valid[core_i] = ctrl_local_out_valid[core_i];
            assign ctrl_local_out_ready[core_i] = core_dispatch_ready[core_i];
            assign core_dispatch_tag[(core_i*8) +: 8] =
                ctrl_local_out_payload[(core_i*CTRL_PAYLOAD_WIDTH) + CTRL_TAG_LSB +: 8];
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
                core_result_flow_id[(core_i*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH],
                core_result_tag[(core_i*8) +: 8]
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
            assign core_self_dispatch_tag[(core_i*8) +: 8] =
                data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_TAG_LSB +: 8];
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
"""


_FABRIC_BINDING_HUBBED = """    // Per-line fabric binding. Every core uses its own local port for both
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
__WAU_CORE_DISPATCH_UNPACK__
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
                coord_dispatch_stage_id,
                coord_dispatch_tag
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
    assign coord_result_tag =
        data_hub_out_payload[(hub_sel*DATA_PAYLOAD_WIDTH) + DATA_TAG_LSB +: 8];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hub_rr_ptr <= {CORE_ID_WIDTH{1'b0}};
        end else if (hub_sel_valid && coord_result_ready) begin
            hub_rr_ptr <= (hub_sel == (HIGHWAY_LINE_COUNT - 1))
                ? {CORE_ID_WIDTH{1'b0}}
                : hub_sel + 1'b1;
        end
    end
"""


_FABRIC_BINDING_SHARED = """    // Single-highway fabric binding (chain / matrix): the coordinator shares
    // core 0's local port -- injecting dispatch on its input side and draining
    // results from its output side -- and the hub ports stay unused.
    //
    // Core 0's inbound data-plane traffic is therefore always coordinator-
    // bound, never a genuine fast-path self-dispatch to core 0's own station
    // (build_fast_path_tables' excluded_destinations guarantees no hop is
    // ever routed there); _CORE_DISPATCH_UNPACK must not treat it as one.
    localparam CORE_SHARES_COORDINATOR_PORT = 1'b1;
    genvar core_i;
    generate
        for (core_i = 0; core_i < CORE_COUNT; core_i = core_i + 1) begin : gen_local_binding
            if (core_i == COORDINATOR_CORE_INDEX) begin : gen_ctrl_ingress
                assign ctrl_local_in_valid[core_i] = coord_dispatch_valid;
                assign coord_dispatch_ready = ctrl_local_in_ready[core_i];
                assign ctrl_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = coord_dispatch_dst_core;
                assign ctrl_local_in_payload[(core_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {
                    coord_dispatch_flow_id,
                    coord_dispatch_opcode,
                    coord_dispatch_a,
                    coord_dispatch_b,
                    coord_dispatch_use_immediate,
                    coord_dispatch_immediate_b,
                    coord_dispatch_stage_id,
                    coord_dispatch_tag
                };
            end else begin : gen_ctrl_ingress_zero
                assign ctrl_local_in_valid[core_i] = 1'b0;
                assign ctrl_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
                assign ctrl_local_in_payload[(core_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {CTRL_PAYLOAD_WIDTH{1'b0}};
            end
__WAU_CORE_DISPATCH_UNPACK__
            // Each core's own result is routed to whatever wau_core_station
            // decided (COORD_DST_SENTINEL = core 0 for a legacy/final-stage
            // result, degenerating to exactly the old hardwired behaviour
            // when the fast-path table is empty; a real -- and never
            // core-0 -- core index for a fast-path hit, see
            // build_fast_path_tables' excluded_destinations).
            assign data_local_in_dst[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] =
                core_result_dst_core[(core_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH];

            if (core_i == COORDINATOR_CORE_INDEX) begin : gen_data_egress
                assign coord_result_valid = data_local_out_valid[core_i];
                assign data_local_out_ready[core_i] = coord_result_ready;
                assign coord_result_src_core =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_SRC_CORE_LSB +: CORE_ID_WIDTH];
                assign coord_result_value =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_VALUE_LSB +: DATA_WIDTH];
                assign coord_result_stage_id =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_STAGE_LSB +: 8];
                assign coord_result_flow_id =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_FLOW_ID_LSB +: FLOW_ID_WIDTH];
                assign coord_result_tag =
                    data_local_out_payload[(core_i*DATA_PAYLOAD_WIDTH) + DATA_TAG_LSB +: 8];
            end else begin : gen_data_egress_drop
                // Any core other than the one the coordinator shares a port
                // with genuinely owns its inbound data-plane traffic --
                // drain readiness from its own fast-path self-dispatch port.
                assign data_local_out_ready[core_i] = core_self_dispatch_ready[core_i];
            end
        end
    endgenerate

    // Hub ports are inert in these topologies.
    genvar hub_i;
    generate
        for (hub_i = 0; hub_i < HIGHWAY_LINE_COUNT; hub_i = hub_i + 1) begin : gen_idle_hub
            assign ctrl_hub_in_valid[hub_i] = 1'b0;
            assign ctrl_hub_in_dst[(hub_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
            assign ctrl_hub_in_payload[(hub_i*CTRL_PAYLOAD_WIDTH) +: CTRL_PAYLOAD_WIDTH] = {CTRL_PAYLOAD_WIDTH{1'b0}};
            assign ctrl_hub_out_ready[hub_i] = 1'b1;
            assign data_hub_in_valid[hub_i] = 1'b0;
            assign data_hub_in_dst[(hub_i*CORE_ID_WIDTH) +: CORE_ID_WIDTH] = {CORE_ID_WIDTH{1'b0}};
            assign data_hub_in_payload[(hub_i*DATA_PAYLOAD_WIDTH) +: DATA_PAYLOAD_WIDTH] = {DATA_PAYLOAD_WIDTH{1'b0}};
            assign data_hub_out_ready[hub_i] = 1'b1;
        end
    endgenerate
"""


def _render_fabric_binding(project: CompiledProject) -> str:
    """The wiring between the coordinator, the cores and the highway fabric.

    Under ``lines`` the coordinator sits on one hub per highway line, so lines
    stay independent all the way up to it. Under ``chain``/``matrix`` there is
    a single highway and the coordinator keeps sharing core 0's local port,
    exactly as before.
    """
    template = (
        _FABRIC_BINDING_HUBBED
        if project.config.device.highway.is_lines
        else _FABRIC_BINDING_SHARED
    )
    return template.replace("__WAU_CORE_DISPATCH_UNPACK__", _CORE_DISPATCH_UNPACK.strip("\n"))


def _render_contract_rom(project: CompiledProject, schedule: SchedulePlan) -> str:
    """Emit the per-core programmed contract words as plain continuous assigns.

    Kept explicit (one line per core, annotated) rather than packed into a
    literal so the schedule-derived expectation stays readable in the emitted
    RTL and diffable across generator runs.
    """
    entries = contract_rom_entries(project, schedule)
    width = _CONTRACT_WORD_WIDTH
    lines: list[str] = []
    for idx, (mode, words, repeats) in enumerate(entries):
        value = _encode_contract_word(mode, words, repeats)
        lines.append(
            f"    assign data_contract_word[({idx}*CONTRACT_WORD_WIDTH) +: CONTRACT_WORD_WIDTH] ="
            f" {width}'h{value:05X};"
            f"  // core {idx}: {_CONTRACT_MODE_NAMES[mode]}"
            f" words={words} repeats={repeats}"
        )
    return "\n".join(lines)


def _render_top(project: CompiledProject, schedule: SchedulePlan) -> str:
    module_name = project.config.output_module_name
    contract_enable = 1 if project.config.device.highway.contract_bus else 0
    # The reserved "route to the coordinator" destination for a fast-path
    # hop, matching whichever constant _render_fabric_binding already
    # hardwires legacy results to for this topology.
    coord_dst_sentinel = (
        "HUB_DST" if project.config.device.highway.is_lines else "{CORE_ID_WIDTH{1'b0}}"
    )
    template = """`timescale 1ns/1ps
`include "wau_defs.vh"

module __WAU_TOP_MODULE__ #(
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
    localparam integer CONTRACT_BUS_ENABLE = __WAU_CONTRACT_ENABLE__;

    // Highway geometry: how many independent highways the fabric has and how
    // many cores share one. `lines` gives a highway per row; chain/matrix have
    // a single highway spanning the whole grid.
    localparam integer HIGHWAY_LINE_COUNT = `WAU_HIGHWAY_LINE_COUNT;
    localparam integer HIGHWAY_LINE_SIZE = `WAU_HIGHWAY_LINE_SIZE;
    // Reserved destination meaning "leave this line" (see wau_highway_router).
    localparam [CORE_ID_WIDTH-1:0] HUB_DST = {CORE_ID_WIDTH{1'b1}};

    // Internal transaction tags are coordinator slot ids. They are deliberately
    // carried only inside the generated fabric; the host MMIO ABI remains
    // flow-id based and unchanged.
    localparam integer CTRL_TAG_LSB = 0;
    localparam integer CTRL_STAGE_LSB = CTRL_TAG_LSB + 8;
    localparam integer CTRL_IMM_LSB = CTRL_STAGE_LSB + 8;
    localparam integer CTRL_USE_IMM_LSB = CTRL_IMM_LSB + DATA_WIDTH;
    localparam integer CTRL_B_LSB = CTRL_USE_IMM_LSB + 1;
    localparam integer CTRL_A_LSB = CTRL_B_LSB + DATA_WIDTH;
    localparam integer CTRL_OPCODE_LSB = CTRL_A_LSB + DATA_WIDTH;
    localparam integer CTRL_FLOW_ID_LSB = CTRL_OPCODE_LSB + OPCODE_WIDTH;
    localparam integer CTRL_PAYLOAD_WIDTH = CTRL_FLOW_ID_LSB + FLOW_ID_WIDTH;

    localparam integer DATA_TAG_LSB = 0;
    localparam integer DATA_FLOW_ID_LSB = DATA_TAG_LSB + 8;
    localparam integer DATA_STAGE_LSB = DATA_FLOW_ID_LSB + FLOW_ID_WIDTH;
    localparam integer DATA_VALUE_LSB = DATA_STAGE_LSB + 8;
    localparam integer DATA_SRC_CORE_LSB = DATA_VALUE_LSB + DATA_WIDTH;
    // Fast-path fields follow the tagged base result. This is an internal mesh
    // payload layout, not part of the frozen host-facing ABI.
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
    localparam [CORE_ID_WIDTH-1:0] COORD_DST_SENTINEL = __WAU_COORD_DST_SENTINEL__;

    wire [CORE_COUNT-1:0] core_dispatch_valid;
    wire [CORE_COUNT-1:0] core_dispatch_ready;
    wire [CORE_COUNT*8-1:0] core_dispatch_tag;
    wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_dispatch_flow_id;
    wire [CORE_COUNT*OPCODE_WIDTH-1:0] core_dispatch_opcode;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_a;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_b;
    wire [CORE_COUNT-1:0] core_dispatch_use_immediate;
    wire [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_immediate_b;
    wire [CORE_COUNT*8-1:0] core_dispatch_stage_id;

    wire [CORE_COUNT-1:0] core_result_valid;
    wire [CORE_COUNT-1:0] core_result_ready;
    wire [CORE_COUNT*8-1:0] core_result_tag;
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
    wire [CORE_COUNT*8-1:0] core_self_dispatch_tag;
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
    wire [7:0] coord_dispatch_tag;
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
    wire [7:0] coord_result_tag;
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
        .dispatch_pkt_tag(coord_dispatch_tag),
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
        .result_pkt_tag(coord_result_tag),
        .result_pkt_flow_id(coord_result_flow_id),
        .result_pkt_stage_id(coord_result_stage_id),
        .result_pkt_value(coord_result_value),
        .core_busy(core_busy)
    );

__WAU_FABRIC_BINDING__

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

__WAU_CONTRACT_ROM__

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
                    .dispatch_tag(core_dispatch_tag[(CORE_INDEX*8) +: 8]),
                    .dispatch_flow_id(core_dispatch_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .dispatch_opcode(core_dispatch_opcode[(CORE_INDEX*OPCODE_WIDTH) +: OPCODE_WIDTH]),
                    .dispatch_a(core_dispatch_a[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_b(core_dispatch_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_use_immediate(core_dispatch_use_immediate[CORE_INDEX]),
                    .dispatch_immediate_b(core_dispatch_immediate_b[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .dispatch_stage_id(core_dispatch_stage_id[(CORE_INDEX*8) +: 8]),
                    .self_dispatch_valid(core_self_dispatch_valid[CORE_INDEX]),
                    .self_dispatch_ready(core_self_dispatch_ready[CORE_INDEX]),
                    .self_dispatch_tag(core_self_dispatch_tag[(CORE_INDEX*8) +: 8]),
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
                    .result_tag(core_result_tag[(CORE_INDEX*8) +: 8]),
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
"""
    return (
        template.replace("__WAU_TOP_MODULE__", module_name)
        .replace("__WAU_CONTRACT_ENABLE__", str(contract_enable))
        .replace("__WAU_CONTRACT_ROM__", _render_contract_rom(project, schedule))
        .replace("__WAU_FABRIC_BINDING__", _render_fabric_binding(project))
        .replace("__WAU_COORD_DST_SENTINEL__", coord_dst_sentinel)
    )

def _render_host_mmio() -> str:
    """Memory-mapped host interface.

    Exposes a small register file over a synchronous valid/ready bus so external
    host software (or a board wrapper) can drive WAU like an Avalon-MM slave with
    32-bit registers. The register map is small and stable so host drivers and
    closed-loop benchmarking harnesses can target it across device wrappers.

    Register map (word-addressed):
      0x00  CTRL     [0]=reset_request (RW1S/auto-clear after one cycle)
                     [1]=enable_auto_adapt (RW)
      0x01  STATUS   [0]=host_in_ready
                     [1]=host_out_valid
                     [2]=output_pending
      0x02  FLOW_ID  RW (write-only takes effect at TRIGGER)
      0x03  IN_A     RW input operand A
      0x04  IN_B     RW input operand B
      0x05  TRIGGER  W1S - writing any value asserts host_in_valid for one accepted handshake
      0x10  OUT_FLOW R  last host_out_flow_id
      0x11  OUT_VAL  R  last host_out_value
      0x12  HOPS_LO  R  obs_total_hop_count
      0x13  STALLS   R  obs_total_stall_count
      0x14  FORWARDS R  obs_total_forward_count
      0x15  DELIVRD  R  obs_total_local_delivered_count
      0x16  CACHE_H  R  obs_total_cache_hit_count
      0x17  CACHE_L  R  obs_total_cache_lookup_count
      0x18  CTR_GRNT R  obs_total_contract_grant_count
      0x19  CTR_HOLD R  obs_total_contract_hold_cycles
      0x1A  CTR_DEFR R  obs_total_contract_defer_count
    """
    return """`timescale 1ns/1ps
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
            enable_auto_adapt <= `WAU_DEFAULT_AUTO_ADAPT;
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
"""


def _render_de0_nano_wrapper(project: CompiledProject) -> str:
    module_name = project.config.output_module_name
    template = """`timescale 1ns/1ps
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

    __WAU_TOP_MODULE__ wau_u (
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
"""
    return template.replace("__WAU_TOP_MODULE__", module_name)


def _render_station_program_report(project: CompiledProject) -> dict:
    """`wau_program.json`'s "station_program" section: observability/debugging
    view of the per-core fast-path table actually emitted into the RTL by
    `_core_fast_path_lookup_function`. Computed the same way (same
    `excluded_destinations`), so this always describes what the hardware
    really has."""
    station_program = project.config.compiler.station_program
    if not station_program.enabled:
        return {
            "enabled": False,
            "table_bits": station_program.table_bits,
            "table_capacity": 1 << station_program.table_bits,
            "cores": [],
        }

    tables = build_fast_path_tables(
        project,
        table_bits=station_program.table_bits,
        excluded_destinations=_fast_path_excluded_destinations(project),
    )
    return {
        "enabled": True,
        "table_bits": station_program.table_bits,
        "table_capacity": 1 << station_program.table_bits,
        "cores": [
            {
                "core_index": table.core_index,
                "entries": [
                    {
                        "flow_id": flow_id,
                        "stage_index": stage_index,
                        "concurrence_id": hop.concurrence_id,
                        "next_stage_index": hop.next_stage_index,
                        "next_opcode": hop.next_opcode,
                        "next_use_immediate": hop.next_use_immediate,
                        "next_immediate_b": hop.next_immediate_b,
                        "next_dst_primary": hop.next_dst_primary,
                        "next_dst_fallback": hop.next_dst_fallback,
                    }
                    for (flow_id, stage_index), hop in sorted(table.hops.items())
                ],
                "overflowed_keys": [
                    {"flow_id": flow_id, "stage_index": stage_index}
                    for flow_id, stage_index in table.overflowed_keys
                ],
            }
            for table in tables
        ],
    }


def _render_program_json(project: CompiledProject) -> dict:
    flow_to_slot = {flow.flow_id: flow.flow_slot for flow in project.flows}
    grid_x = project.config.device.grid_x
    grid_y = project.config.device.grid_y

    def coord_with_index(coord: object) -> dict[str, int]:
        x = getattr(coord, "x")
        y = getattr(coord, "y")
        z = getattr(coord, "z")
        return {
            "x": x,
            "y": y,
            "z": z,
            "index": core_index(x, y, grid_x, z, grid_y),
        }

    return {
        "project": project.config.project_name,
        "abstraction": {
            "language": project.config.abstraction_language,
            "version": project.config.abstraction_version,
        },
        "device": {
            "name": project.config.device.name,
            "vendor": project.config.device.vendor,
            "family": project.config.device.family,
            "part": project.config.device.part,
            "grid": {
                "x": project.config.device.grid_x,
                "y": project.config.device.grid_y,
                "z": project.config.device.grid_z,
            },
            "coordinator_mode": project.config.device.coordinator_mode,
            "supported_data_types": list(project.config.device.supported_data_types),
            "highway": {
                "topology": project.config.device.highway.topology,
                "contract_bus": project.config.device.highway.contract_bus,
                "contract_max_burst": project.config.device.highway.contract_max_burst,
                "contract_lease_cycles": (
                    project.config.device.highway.contract_lease_cycles
                ),
            },
        },
        "compiler": {
            "routing": project.config.compiler.routing,
            "allow_adaptive_reroute": project.config.compiler.allow_adaptive_reroute,
            "fallback_radius": project.config.compiler.fallback_radius,
            "allow_cycle_recurrence": project.config.compiler.allow_cycle_recurrence,
            "core_capabilities": [
                {
                    "core": {"x": cap.core.x, "y": cap.core.y, "z": cap.core.z},
                    "operations": list(cap.operations),
                    "data_types": list(cap.data_types),
                }
                for cap in project.config.compiler.core_capabilities
            ],
        },
        "station_program": _render_station_program_report(project),
        "flows": [
            {
                "flow_id": flow.flow_id,
                "flow_slot": flow.flow_slot,
                "name": flow.name,
                "linear_node_order": list(flow.linear_node_order),
                "stages": [
                    {
                        "stage_index": stage.stage_index,
                        "op": stage.op_name,
                        "opcode": stage.opcode,
                        "latency": stage.latency,
                        "pipelined": stage.pipelined,
                        "primary_core": coord_with_index(stage.primary_core),
                        "fallback_core": (
                            coord_with_index(stage.fallback_core)
                            if stage.fallback_core is not None
                            else None
                        ),
                        "immediate_b": stage.immediate_b,
                        "dtype": stage.dtype,
                    }
                    for stage in flow.stages
                ],
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "node_slot": node.node_slot,
                        "deps": list(node.deps),
                        "dep_slots": list(node.dep_slots),
                        "op": node.op_name,
                        "opcode": node.opcode,
                        "latency": node.latency,
                        "pipelined": node.pipelined,
                        "primary_core": coord_with_index(node.primary_core),
                        "candidate_cores": [
                            coord_with_index(coord)
                            for coord in node.candidate_cores
                        ],
                        "fallback_core": (
                            coord_with_index(node.fallback_core)
                            if node.fallback_core is not None
                            else None
                        ),
                        "allow_adaptive": node.allow_adaptive,
                        "immediate_b": node.immediate_b,
                        "dtype": node.dtype,
                        "recurrent": node.recurrent,
                        "max_iterations": node.max_iterations,
                        "cycle_group": node.cycle_group,
                    }
                    for node in flow.nodes
                ],
            }
            for flow in project.flows
        ],
        "programs": [
            {
                "program_id": program.program_id,
                "name": program.name,
                "priority": program.priority,
                "replicas": program.replicas,
                "max_parallel_flows": program.max_parallel_flows,
                "load_balance": program.load_balance,
                "allow_async": program.allow_async,
                "allow_out_of_order": program.allow_out_of_order,
                "flow_ids": list(program.flow_ids),
                "flow_slots": [flow_to_slot[flow_id] for flow_id in program.flow_ids if flow_id in flow_to_slot],
            }
            for program in project.config.programs
        ],
    }


def emit_verilog(project: CompiledProject, schedule: SchedulePlan, out_dir: Path) -> list[Path]:
    module_name = project.config.output_module_name
    verilog_project = VerilogProject(
        header=VerilogHeader.spdx(_SPDX_IDENTIFIER, _LICENSE_REF_TEXT),
        features=(
            {"de0_nano"}
            if project.config.device.name.lower().startswith("intel_de0_nano")
            else set()
        ),
    )

    verilog_project.add_verilog("wau_defs.vh", _render_defs(project))
    verilog_project.add_verilog("wau_operation_alu.v", _render_operation_alu(project))
    verilog_project.add_verilog("wau_neighbor_forward.v", _render_neighbor_forward())
    verilog_project.add_verilog(
        "wau_highway_contract.v", _render_highway_contract()
    )
    verilog_project.add_verilog("wau_highway_router.v", _render_highway_router(project))
    verilog_project.add_verilog("wau_highway_mesh.v", _render_highway_mesh(project))
    verilog_project.add_verilog("wau_core_station.v", _render_core_station(project))
    verilog_project.add_verilog("wau_core.v", _render_core())
    verilog_project.add_verilog("wau_coordinator.v", _render_coordinator(project))
    verilog_project.add_verilog("wau_host_mmio.v", _render_host_mmio())
    verilog_project.add_verilog(f"{module_name}.v", _render_top(project, schedule))
    verilog_project.add_verilog(
        "wau_de0_nano_top.v",
        _render_de0_nano_wrapper(project),
        when="de0_nano",
    )
    verilog_project.add_file(
        "wau_program.json",
        json.dumps(_render_program_json(project), indent=2),
    )
    verilog_project.add_file("wau_schedule.json", json.dumps(schedule.to_json(), indent=2))
    verilog_project.add_file("wau_schedule.hex", "\n".join(schedule.to_hex_lines()) + "\n")

    return verilog_project.emit(out_dir)
