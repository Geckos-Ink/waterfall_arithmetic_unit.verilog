from __future__ import annotations

import json
from pathlib import Path

from .compiler import CompiledProject
from .scheduler import SchedulePlan, core_index
from .utils import macro_name


def _op_macro(op_name: str) -> str:
    return macro_name(op_name)


def _render_defs(project: CompiledProject) -> str:
    cfg = project.config
    lines: list[str] = []
    lines.append("`ifndef WAU_DEFS_VH")
    lines.append("`define WAU_DEFS_VH")
    lines.append("")
    lines.append(f"`define WAU_PROJECT_NAME \"{cfg.project_name}\"")
    lines.append(f"`define WAU_GRID_X {cfg.device.grid_x}")
    lines.append(f"`define WAU_GRID_Y {cfg.device.grid_y}")
    lines.append(f"`define WAU_CORE_COUNT {cfg.device.grid_x * cfg.device.grid_y}")
    lines.append(f"`define WAU_DATA_WIDTH {cfg.device.data_width}")
    lines.append(f"`define WAU_FLOW_ID_WIDTH {cfg.device.flow_id_width}")
    lines.append(f"`define WAU_OPCODE_WIDTH {cfg.device.opcode_width}")
    lines.append(f"`define WAU_LOCAL_RAM_DEPTH {cfg.device.local_ram_depth}")
    lines.append(f"`define WAU_GLOBAL_RAM_DEPTH {cfg.device.global_ram_depth}")
    lines.append(f"`define WAU_FLOW_COUNT {len(project.flows)}")
    lines.append(f"`define WAU_MAX_STAGES {project.max_stages}")
    lines.append(f"`define WAU_OP_COUNT {len(cfg.operations)}")
    lines.append("")
    for op in cfg.operations:
        m = _op_macro(op.name)
        lines.append(f"`define WAU_OPCODE_{m} {cfg.device.opcode_width}'h{op.opcode:02X}")
        lines.append(f"`define WAU_LATENCY_{m} 8'd{op.latency}")
    lines.append("")
    lines.append("`endif")
    return "\n".join(lines) + "\n"


def _render_operation_alu(project: CompiledProject) -> str:
    case_lines = []
    for op in project.config.operations:
        case_lines.append(f"      `WAU_OPCODE_{_op_macro(op.name)}: result_comb = {op.verilog_expr};")
    case_blob = "\n".join(case_lines)

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_operation_alu #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
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


def _render_core_station(project: CompiledProject) -> str:
    latency_cases = []
    for op in project.config.operations:
        latency_cases.append(
            f"                `WAU_OPCODE_{_op_macro(op.name)}: op_latency = `WAU_LATENCY_{_op_macro(op.name)};"
        )
    latency_blob = "\n".join(latency_cases)

    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module wau_core_station #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
) (
    input wire clk,
    input wire rst_n,

    input wire in_valid,
    output wire in_ready,
    input wire [FLOW_ID_WIDTH-1:0] in_flow_id,
    input wire [OPCODE_WIDTH-1:0] in_opcode,
    input wire signed [DATA_WIDTH-1:0] in_a,
    input wire signed [DATA_WIDTH-1:0] in_b,
    input wire in_use_immediate,
    input wire signed [DATA_WIDTH-1:0] in_immediate_b,
    input wire [7:0] in_stage_id,

    output reg out_valid,
    input wire out_ready,
    output reg [FLOW_ID_WIDTH-1:0] out_flow_id,
    output reg [7:0] out_stage_id,
    output reg signed [DATA_WIDTH-1:0] out_value,

    output wire busy,
    output reg cache_hit
);
    localparam ST_IDLE = 2'd0;
    localparam ST_EXEC = 2'd1;
    localparam ST_OUT = 2'd2;

    reg [1:0] state;

    reg [FLOW_ID_WIDTH-1:0] active_flow_id;
    reg [OPCODE_WIDTH-1:0] active_opcode;
    reg [7:0] active_stage_id;
    reg signed [DATA_WIDTH-1:0] op_a;
    reg signed [DATA_WIDTH-1:0] op_b;
    reg [7:0] wait_cycles;

    reg cache_valid;
    reg [OPCODE_WIDTH-1:0] last_opcode;
    reg signed [DATA_WIDTH-1:0] last_a;
    reg signed [DATA_WIDTH-1:0] last_b;

    reg alu_in_valid;
    wire alu_out_valid;
    wire signed [DATA_WIDTH-1:0] alu_out_value;

    reg result_latched_valid;
    reg signed [DATA_WIDTH-1:0] result_latched_value;

    wire signed [DATA_WIDTH-1:0] effective_b;
    assign effective_b = in_use_immediate ? in_immediate_b : in_b;

    assign in_ready = (state == ST_IDLE);
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

    wau_operation_alu #(
        .DATA_WIDTH(DATA_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH)
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

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            out_valid <= 1'b0;
            out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            out_stage_id <= 8'd0;
            out_value <= {{DATA_WIDTH{{1'b0}}}};
            active_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            active_opcode <= {{OPCODE_WIDTH{{1'b0}}}};
            active_stage_id <= 8'd0;
            op_a <= {{DATA_WIDTH{{1'b0}}}};
            op_b <= {{DATA_WIDTH{{1'b0}}}};
            wait_cycles <= 8'd0;
            cache_valid <= 1'b0;
            last_opcode <= {{OPCODE_WIDTH{{1'b0}}}};
            last_a <= {{DATA_WIDTH{{1'b0}}}};
            last_b <= {{DATA_WIDTH{{1'b0}}}};
            cache_hit <= 1'b0;
            alu_in_valid <= 1'b0;
            result_latched_valid <= 1'b0;
            result_latched_value <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            alu_in_valid <= 1'b0;

            if (out_valid && out_ready) begin
                out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    result_latched_valid <= 1'b0;
                    if (in_valid && in_ready) begin
                        active_flow_id <= in_flow_id;
                        active_opcode <= in_opcode;
                        active_stage_id <= in_stage_id;
                        op_a <= in_a;
                        op_b <= effective_b;
                        wait_cycles <= op_latency(in_opcode) - 8'd1;

                        cache_hit <= cache_valid &&
                                     (last_opcode == in_opcode) &&
                                     (last_a == in_a) &&
                                     (last_b == effective_b);
                        cache_valid <= 1'b1;
                        last_opcode <= in_opcode;
                        last_a <= in_a;
                        last_b <= effective_b;

                        alu_in_valid <= 1'b1;
                        state <= ST_EXEC;
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
                        out_flow_id <= active_flow_id;
                        out_stage_id <= active_stage_id;
                        out_value <= alu_out_valid ? alu_out_value : result_latched_value;
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
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH
) (
    input wire clk,
    input wire rst_n,

    input wire dispatch_valid,
    output wire dispatch_ready,
    input wire [FLOW_ID_WIDTH-1:0] dispatch_flow_id,
    input wire [OPCODE_WIDTH-1:0] dispatch_opcode,
    input wire signed [DATA_WIDTH-1:0] dispatch_a,
    input wire signed [DATA_WIDTH-1:0] dispatch_b,
    input wire dispatch_use_immediate,
    input wire signed [DATA_WIDTH-1:0] dispatch_immediate_b,
    input wire [7:0] dispatch_stage_id,

    output wire result_valid,
    input wire result_ready,
    output wire [FLOW_ID_WIDTH-1:0] result_flow_id,
    output wire [7:0] result_stage_id,
    output wire signed [DATA_WIDTH-1:0] result_value,

    output wire busy,
    output wire cache_hit
);
    wau_core_station #(
        .DATA_WIDTH(DATA_WIDTH),
        .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
        .OPCODE_WIDTH(OPCODE_WIDTH)
    ) station_u (
        .clk(clk),
        .rst_n(rst_n),
        .in_valid(dispatch_valid),
        .in_ready(dispatch_ready),
        .in_flow_id(dispatch_flow_id),
        .in_opcode(dispatch_opcode),
        .in_a(dispatch_a),
        .in_b(dispatch_b),
        .in_use_immediate(dispatch_use_immediate),
        .in_immediate_b(dispatch_immediate_b),
        .in_stage_id(dispatch_stage_id),
        .out_valid(result_valid),
        .out_ready(result_ready),
        .out_flow_id(result_flow_id),
        .out_stage_id(result_stage_id),
        .out_value(result_value),
        .busy(busy),
        .cache_hit(cache_hit)
    );
endmodule
"""


def _stage_case_entries(project: CompiledProject, field: str) -> str:
    lines: list[str] = []
    for flow in project.flows:
        for stage in flow.stages:
            key = (flow.flow_slot << 8) | stage.stage_index
            if field == "opcode":
                value = f"{project.config.device.opcode_width}'h{stage.opcode:02X}"
            elif field == "primary_core":
                idx = core_index(stage.primary_core.x, stage.primary_core.y, project.config.device.grid_x)
                value = f"8'd{idx}"
            elif field == "fallback_core":
                fallback = stage.fallback_core or stage.primary_core
                idx = core_index(fallback.x, fallback.y, project.config.device.grid_x)
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

    output reg [CORE_COUNT-1:0] core_dispatch_valid,
    input wire [CORE_COUNT-1:0] core_dispatch_ready,
    output reg [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_dispatch_flow_id,
    output reg [CORE_COUNT*OPCODE_WIDTH-1:0] core_dispatch_opcode,
    output reg [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_a,
    output reg [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_b,
    output reg [CORE_COUNT-1:0] core_dispatch_use_immediate,
    output reg [CORE_COUNT*DATA_WIDTH-1:0] core_dispatch_immediate_b,
    output reg [CORE_COUNT*8-1:0] core_dispatch_stage_id,

    input wire [CORE_COUNT-1:0] core_result_valid,
    output reg [CORE_COUNT-1:0] core_result_ready,
    input wire [CORE_COUNT*FLOW_ID_WIDTH-1:0] core_result_flow_id,
    input wire [CORE_COUNT*8-1:0] core_result_stage_id,
    input wire [CORE_COUNT*DATA_WIDTH-1:0] core_result_value,
    input wire [CORE_COUNT-1:0] core_busy
);
    localparam ST_IDLE = 2'd0;
    localparam ST_DISPATCH = 2'd1;
    localparam ST_WAIT_RESULT = 2'd2;

    reg [1:0] state;
    reg [7:0] current_flow_slot;
    reg [FLOW_ID_WIDTH-1:0] current_flow_id;
    reg [7:0] current_stage;
    reg [7:0] waiting_core;
    reg signed [DATA_WIDTH-1:0] accumulator;
    reg signed [DATA_WIDTH-1:0] operand_b;

    wire [7:0] stage_last;
    wire [OPCODE_WIDTH-1:0] stage_opcode;
    wire [7:0] stage_primary_core;
    wire [7:0] stage_fallback_core;
    wire stage_use_immediate;
    wire signed [DATA_WIDTH-1:0] stage_immediate_b;

    reg [7:0] chosen_core;

    wire [FLOW_ID_WIDTH-1:0] waiting_result_flow_id;
    wire [7:0] waiting_result_stage_id;
    wire signed [DATA_WIDTH-1:0] waiting_result_value;

    assign waiting_result_flow_id = core_result_flow_id[(waiting_core*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH];
    assign waiting_result_stage_id = core_result_stage_id[(waiting_core*8) +: 8];
    assign waiting_result_value = core_result_value[(waiting_core*DATA_WIDTH) +: DATA_WIDTH];

    assign host_in_ready = (state == ST_IDLE) && !host_out_valid;

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

    assign stage_last = flow_last_stage(current_flow_slot);
    assign stage_opcode = flow_stage_opcode(current_flow_slot, current_stage);
    assign stage_primary_core = flow_stage_primary_core(current_flow_slot, current_stage);
    assign stage_fallback_core = flow_stage_fallback_core(current_flow_slot, current_stage);
    assign stage_use_immediate = flow_stage_use_immediate(current_flow_slot, current_stage);
    assign stage_immediate_b = flow_stage_immediate_b(current_flow_slot, current_stage);

    always @(*) begin
        chosen_core = stage_primary_core;
        if (enable_auto_adapt &&
            (stage_fallback_core != stage_primary_core) &&
            core_busy[stage_primary_core] &&
            !core_busy[stage_fallback_core]) begin
            chosen_core = stage_fallback_core;
        end
    end

    always @(*) begin
        core_dispatch_valid = {{CORE_COUNT{{1'b0}}}};
        core_dispatch_flow_id = {{CORE_COUNT*FLOW_ID_WIDTH{{1'b0}}}};
        core_dispatch_opcode = {{CORE_COUNT*OPCODE_WIDTH{{1'b0}}}};
        core_dispatch_a = {{CORE_COUNT*DATA_WIDTH{{1'b0}}}};
        core_dispatch_b = {{CORE_COUNT*DATA_WIDTH{{1'b0}}}};
        core_dispatch_use_immediate = {{CORE_COUNT{{1'b0}}}};
        core_dispatch_immediate_b = {{CORE_COUNT*DATA_WIDTH{{1'b0}}}};
        core_dispatch_stage_id = {{CORE_COUNT*8{{1'b0}}}};

        core_result_ready = {{CORE_COUNT{{1'b0}}}};

        if (state == ST_DISPATCH && current_flow_slot != 8'hFF) begin
            if (core_dispatch_ready[chosen_core]) begin
                core_dispatch_valid[chosen_core] = 1'b1;
                core_dispatch_flow_id[(chosen_core*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH] = current_flow_id;
                core_dispatch_opcode[(chosen_core*OPCODE_WIDTH) +: OPCODE_WIDTH] = stage_opcode;
                core_dispatch_a[(chosen_core*DATA_WIDTH) +: DATA_WIDTH] = accumulator;
                core_dispatch_b[(chosen_core*DATA_WIDTH) +: DATA_WIDTH] = operand_b;
                core_dispatch_use_immediate[chosen_core] = stage_use_immediate;
                core_dispatch_immediate_b[(chosen_core*DATA_WIDTH) +: DATA_WIDTH] = stage_immediate_b;
                core_dispatch_stage_id[(chosen_core*8) +: 8] = current_stage;
            end
        end

        if (state == ST_WAIT_RESULT) begin
            core_result_ready[waiting_core] = 1'b1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            current_flow_slot <= 8'hFF;
            current_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            current_stage <= 8'd0;
            waiting_core <= 8'd0;
            accumulator <= {{DATA_WIDTH{{1'b0}}}};
            operand_b <= {{DATA_WIDTH{{1'b0}}}};
            host_out_valid <= 1'b0;
            host_out_flow_id <= {{FLOW_ID_WIDTH{{1'b0}}}};
            host_out_value <= {{DATA_WIDTH{{1'b0}}}};
        end else begin
            if (host_out_valid && host_out_ready) begin
                host_out_valid <= 1'b0;
            end

            case (state)
                ST_IDLE: begin
                    if (host_in_valid && host_in_ready) begin
                        current_flow_slot <= flow_slot_from_id(host_in_flow_id);
                        current_flow_id <= host_in_flow_id;
                        current_stage <= 8'd0;
                        accumulator <= host_in_a;
                        operand_b <= host_in_b;

                        if (flow_slot_from_id(host_in_flow_id) != 8'hFF) begin
                            state <= ST_DISPATCH;
                        end
                    end
                end

                ST_DISPATCH: begin
                    if (current_flow_slot == 8'hFF) begin
                        state <= ST_IDLE;
                    end else if (core_dispatch_ready[chosen_core]) begin
                        waiting_core <= chosen_core;
                        state <= ST_WAIT_RESULT;
                    end
                end

                ST_WAIT_RESULT: begin
                    if (core_result_valid[waiting_core]) begin
                        accumulator <= waiting_result_value;
                        if (current_stage >= stage_last) begin
                            host_out_valid <= 1'b1;
                            host_out_flow_id <= waiting_result_flow_id;
                            host_out_value <= waiting_result_value;
                            state <= ST_IDLE;
                        end else begin
                            current_stage <= current_stage + 8'd1;
                            state <= ST_DISPATCH;
                        end
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


def _render_top(project: CompiledProject) -> str:
    module_name = project.config.output_module_name
    return f"""`timescale 1ns/1ps
`include "wau_defs.vh"

module {module_name} #(
    parameter DATA_WIDTH = `WAU_DATA_WIDTH,
    parameter FLOW_ID_WIDTH = `WAU_FLOW_ID_WIDTH,
    parameter OPCODE_WIDTH = `WAU_OPCODE_WIDTH,
    parameter GRID_X = `WAU_GRID_X,
    parameter GRID_Y = `WAU_GRID_Y,
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

    input wire enable_auto_adapt
);
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
        .core_dispatch_valid(core_dispatch_valid),
        .core_dispatch_ready(core_dispatch_ready),
        .core_dispatch_flow_id(core_dispatch_flow_id),
        .core_dispatch_opcode(core_dispatch_opcode),
        .core_dispatch_a(core_dispatch_a),
        .core_dispatch_b(core_dispatch_b),
        .core_dispatch_use_immediate(core_dispatch_use_immediate),
        .core_dispatch_immediate_b(core_dispatch_immediate_b),
        .core_dispatch_stage_id(core_dispatch_stage_id),
        .core_result_valid(core_result_valid),
        .core_result_ready(core_result_ready),
        .core_result_flow_id(core_result_flow_id),
        .core_result_stage_id(core_result_stage_id),
        .core_result_value(core_result_value),
        .core_busy(core_busy)
    );

    genvar gy;
    genvar gx;
    generate
        for (gy = 0; gy < GRID_Y; gy = gy + 1) begin : gen_y
            for (gx = 0; gx < GRID_X; gx = gx + 1) begin : gen_x
                localparam integer CORE_INDEX = (gy * GRID_X) + gx;

                wau_core #(
                    .CORE_X(gx),
                    .CORE_Y(gy),
                    .DATA_WIDTH(DATA_WIDTH),
                    .FLOW_ID_WIDTH(FLOW_ID_WIDTH),
                    .OPCODE_WIDTH(OPCODE_WIDTH)
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
                    .result_valid(core_result_valid[CORE_INDEX]),
                    .result_ready(core_result_ready[CORE_INDEX]),
                    .result_flow_id(core_result_flow_id[(CORE_INDEX*FLOW_ID_WIDTH) +: FLOW_ID_WIDTH]),
                    .result_stage_id(core_result_stage_id[(CORE_INDEX*8) +: 8]),
                    .result_value(core_result_value[(CORE_INDEX*DATA_WIDTH) +: DATA_WIDTH]),
                    .busy(core_busy[CORE_INDEX]),
                    .cache_hit(core_cache_hit[CORE_INDEX])
                );
            end
        end
    endgenerate

    // Highway buses are intentionally left as extension points for future revisions.
    // The current basis routes stage-to-stage traffic through the coordinator.
endmodule
"""


def _render_program_json(project: CompiledProject) -> dict:
    flow_to_slot = {flow.flow_id: flow.flow_slot for flow in project.flows}

    return {
        "project": project.config.project_name,
        "device": {
            "name": project.config.device.name,
            "vendor": project.config.device.vendor,
            "family": project.config.device.family,
            "part": project.config.device.part,
            "grid": {"x": project.config.device.grid_x, "y": project.config.device.grid_y},
            "coordinator_mode": project.config.device.coordinator_mode,
        },
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
                        "primary_core": {
                            "x": stage.primary_core.x,
                            "y": stage.primary_core.y,
                            "index": core_index(
                                stage.primary_core.x,
                                stage.primary_core.y,
                                project.config.device.grid_x,
                            ),
                        },
                        "fallback_core": (
                            {
                                "x": stage.fallback_core.x,
                                "y": stage.fallback_core.y,
                                "index": core_index(
                                    stage.fallback_core.x,
                                    stage.fallback_core.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            if stage.fallback_core is not None
                            else None
                        ),
                        "immediate_b": stage.immediate_b,
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
                        "primary_core": {
                            "x": node.primary_core.x,
                            "y": node.primary_core.y,
                            "index": core_index(
                                node.primary_core.x,
                                node.primary_core.y,
                                project.config.device.grid_x,
                            ),
                        },
                        "candidate_cores": [
                            {
                                "x": coord.x,
                                "y": coord.y,
                                "index": core_index(
                                    coord.x,
                                    coord.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            for coord in node.candidate_cores
                        ],
                        "fallback_core": (
                            {
                                "x": node.fallback_core.x,
                                "y": node.fallback_core.y,
                                "index": core_index(
                                    node.fallback_core.x,
                                    node.fallback_core.y,
                                    project.config.device.grid_x,
                                ),
                            }
                            if node.fallback_core is not None
                            else None
                        ),
                        "allow_adaptive": node.allow_adaptive,
                        "immediate_b": node.immediate_b,
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
    out_dir.mkdir(parents=True, exist_ok=True)

    module_name = project.config.output_module_name

    outputs: dict[str, str] = {
        "wau_defs.vh": _render_defs(project),
        "wau_operation_alu.v": _render_operation_alu(project),
        "wau_core_station.v": _render_core_station(project),
        "wau_core.v": _render_core(),
        "wau_coordinator.v": _render_coordinator(project),
        f"{module_name}.v": _render_top(project),
    }

    written_paths: list[Path] = []
    for name, content in outputs.items():
        path = out_dir / name
        path.write_text(content)
        written_paths.append(path)

    program_path = out_dir / "wau_program.json"
    program_path.write_text(json.dumps(_render_program_json(project), indent=2))
    written_paths.append(program_path)

    schedule_json_path = out_dir / "wau_schedule.json"
    schedule_json_path.write_text(json.dumps(schedule.to_json(), indent=2))
    written_paths.append(schedule_json_path)

    schedule_hex_path = out_dir / "wau_schedule.hex"
    schedule_hex_path.write_text("\n".join(schedule.to_hex_lines()) + "\n")
    written_paths.append(schedule_hex_path)

    return written_paths
