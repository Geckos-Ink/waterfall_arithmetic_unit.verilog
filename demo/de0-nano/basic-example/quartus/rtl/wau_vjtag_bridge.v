// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.
//
// Generic vJTAG <-> MMIO master bridge.
//
// Designed to drive any Avalon-MM-like register file (e.g. wau_host_mmio) from
// a host PC over Altera's USB-Blaster + virtual JTAG. The protocol mirrors a
// pair of (write address, write data) and (read address, latched read data)
// shift registers, multiplexed by the JTAG IR.
//
// JTAG IR command set (4-bit IR):
//   IR=0x0  IR_BYPASS    1-bit  bypass DR (data shifts straight through)
//   IR=0x1  IR_WRITE    40-bit  [39:32]=addr, [31:0]=writedata. UDR -> mmio_write
//   IR=0x2  IR_READ_ADDR 8-bit  [7:0]=addr.  UDR -> mmio_read
//   IR=0x3  IR_READ_DATA 32-bit shifts out the latest captured mmio_readdata
//   IR=0x4  IR_OBS      32-bit shifts out an aux observability word (free use)
//   IR=0x5..0xE         reserved (treated as bypass)
//   IR=0xF  IR_RESET     1-bit  on UDR pulses one-cycle soft_reset_req
//
// Clock domains:
//   * JTAG tck is asynchronous to the system clk. UDR strobes are crossed via
//     a toggle + 2-FF synchronizer. The DR shift registers are sampled by the
//     clk domain at the synchronized UDR edge and then held stable while the
//     MMIO operation completes (the host has to stop shifting before issuing
//     a new IR, so the captured value is naturally stable).
//   * mmio_readdata is captured in the clk domain on mmio_readdatavalid and
//     forwarded to a small 2-FF crossing register on the tck domain. The host
//     waits between READ_ADDR and READ_DATA, so the value has many tck cycles
//     to settle.
//
// This bridge is intentionally device-agnostic: nothing here is DE0-Nano
// specific; only the sld_virtual_jtag megafunction parameters in vJTAG.v are
// (and those work on every Altera/Intel device that supports SLD nodes).

`timescale 1ns/1ps

module wau_vjtag_bridge #(
    parameter ADDR_WIDTH = 8,
    parameter DATA_WIDTH = 32
) (
    input  wire                       clk,
    input  wire                       rst_n,

    // MMIO master out (drives e.g. wau_host_mmio)
    output reg                        mmio_read,
    output reg                        mmio_write,
    output reg  [ADDR_WIDTH-1:0]      mmio_address,
    output reg  [DATA_WIDTH-1:0]      mmio_writedata,
    input  wire [DATA_WIDTH-1:0]      mmio_readdata,
    input  wire                       mmio_readdatavalid,

    // Pulsed for one clk cycle when the host sends IR_RESET
    output reg                        soft_reset_pulse,

    // Aux read-only word, exposed via IR_OBS (e.g. concatenated status LEDs).
    input  wire [DATA_WIDTH-1:0]      obs_aux_word,

    // JTAG activity indicator (1 = a non-bypass IR is currently selected).
    output wire                       jtag_busy
);
    localparam [3:0] IR_BYPASS    = 4'h0;
    localparam [3:0] IR_WRITE     = 4'h1;
    localparam [3:0] IR_READ_ADDR = 4'h2;
    localparam [3:0] IR_READ_DATA = 4'h3;
    localparam [3:0] IR_OBS       = 4'h4;
    localparam [3:0] IR_RESET     = 4'hF;

    localparam integer WRITE_DR_WIDTH = ADDR_WIDTH + DATA_WIDTH;

    wire tck;
    wire tdi;
    wire [3:0] ir_in;
    wire v_cdr, v_sdr, v_udr;
    wire v_cir_unused, v_e1dr_unused, v_e2dr_unused, v_pdr_unused, v_uir_unused;
    reg  tdo;

    vJTAG vjtag_u (
        .tck                (tck),
        .tdi                (tdi),
        .tdo                (tdo),
        .ir_in              (ir_in),
        .ir_out             (4'b0000),
        .virtual_state_cdr  (v_cdr),
        .virtual_state_cir  (v_cir_unused),
        .virtual_state_e1dr (v_e1dr_unused),
        .virtual_state_e2dr (v_e2dr_unused),
        .virtual_state_pdr  (v_pdr_unused),
        .virtual_state_sdr  (v_sdr),
        .virtual_state_udr  (v_udr),
        .virtual_state_uir  (v_uir_unused)
    );

    // ------------------------------------------------------------------
    // TCK-domain shift registers (one per supported IR).
    // ------------------------------------------------------------------
    reg [WRITE_DR_WIDTH-1:0] dr_write;
    reg [ADDR_WIDTH-1:0]     dr_readaddr;
    reg [DATA_WIDTH-1:0]     dr_readdata;
    reg [DATA_WIDTH-1:0]     dr_obs;
    reg                      dr_bypass;

    // Crossings from clk -> tck (stable between commands, double-FF).
    // readdata_latched_clk is defined in the clk-domain always block below.
    reg [DATA_WIDTH-1:0] readdata_latched_clk;
    reg [DATA_WIDTH-1:0] readdata_cross1;
    reg [DATA_WIDTH-1:0] readdata_cross2;
    reg [DATA_WIDTH-1:0] obs_cross1;
    reg [DATA_WIDTH-1:0] obs_cross2;

    always @(posedge tck) begin
        readdata_cross1 <= readdata_latched_clk;
        readdata_cross2 <= readdata_cross1;
        obs_cross1      <= obs_aux_word;
        obs_cross2      <= obs_cross1;
    end

    always @(posedge tck) begin
        if (v_cdr) begin
            case (ir_in)
                IR_READ_DATA: dr_readdata <= readdata_cross2;
                IR_OBS:       dr_obs      <= obs_cross2;
                default: ;
            endcase
        end else if (v_sdr) begin
            case (ir_in)
                IR_WRITE:     dr_write     <= {tdi, dr_write[WRITE_DR_WIDTH-1:1]};
                IR_READ_ADDR: dr_readaddr  <= {tdi, dr_readaddr[ADDR_WIDTH-1:1]};
                IR_READ_DATA: dr_readdata  <= {tdi, dr_readdata[DATA_WIDTH-1:1]};
                IR_OBS:       dr_obs       <= {tdi, dr_obs[DATA_WIDTH-1:1]};
                default:      dr_bypass    <= tdi;
            endcase
        end
    end

    always @(*) begin
        case (ir_in)
            IR_WRITE:     tdo = dr_write[0];
            IR_READ_ADDR: tdo = dr_readaddr[0];
            IR_READ_DATA: tdo = dr_readdata[0];
            IR_OBS:       tdo = dr_obs[0];
            default:      tdo = dr_bypass;
        endcase
    end

    // ------------------------------------------------------------------
    // UDR strobe -> request toggle into the system clk domain.
    // ------------------------------------------------------------------
    reg        udr_toggle_tck;
    reg [3:0]  udr_ir_tck;

    always @(posedge tck) begin
        if (v_udr) begin
            udr_toggle_tck <= ~udr_toggle_tck;
            udr_ir_tck     <= ir_in;
        end
    end

    reg [2:0] udr_sync;
    reg [3:0] udr_ir_sync;
    reg [WRITE_DR_WIDTH-1:0] dr_write_sync;
    reg [ADDR_WIDTH-1:0]     dr_readaddr_sync;
    reg                      udr_edge_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            udr_sync         <= 3'd0;
            udr_ir_sync      <= 4'd0;
            dr_write_sync    <= {WRITE_DR_WIDTH{1'b0}};
            dr_readaddr_sync <= {ADDR_WIDTH{1'b0}};
            udr_edge_d       <= 1'b0;
        end else begin
            udr_sync   <= {udr_sync[1:0], udr_toggle_tck};
            udr_edge_d <= udr_sync[2] ^ udr_sync[1];
            if (udr_sync[2] ^ udr_sync[1]) begin
                udr_ir_sync      <= udr_ir_tck;
                // Snapshot the shift register contents at UDR-edge time.
                dr_write_sync    <= dr_write;
                dr_readaddr_sync <= dr_readaddr;
            end
        end
    end

    wire udr_edge = udr_sync[2] ^ udr_sync[1];

    // ------------------------------------------------------------------
    // MMIO master + readdata latch (readdata_latched_clk declared above
    // so the TCK-domain double-FF crossing can see it).
    // ------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mmio_read            <= 1'b0;
            mmio_write           <= 1'b0;
            mmio_address         <= {ADDR_WIDTH{1'b0}};
            mmio_writedata       <= {DATA_WIDTH{1'b0}};
            readdata_latched_clk <= {DATA_WIDTH{1'b0}};
            soft_reset_pulse     <= 1'b0;
        end else begin
            mmio_read        <= 1'b0;
            mmio_write       <= 1'b0;
            soft_reset_pulse <= 1'b0;

            if (mmio_readdatavalid)
                readdata_latched_clk <= mmio_readdata;

            // udr_edge fires the same cycle the snapshot registers update,
            // so consume them on the next cycle via udr_edge_d.
            if (udr_edge_d) begin
                case (udr_ir_sync)
                    IR_WRITE: begin
                        mmio_address   <= dr_write_sync[WRITE_DR_WIDTH-1 -: ADDR_WIDTH];
                        mmio_writedata <= dr_write_sync[DATA_WIDTH-1:0];
                        mmio_write     <= 1'b1;
                    end
                    IR_READ_ADDR: begin
                        mmio_address   <= dr_readaddr_sync;
                        mmio_read      <= 1'b1;
                    end
                    IR_RESET: begin
                        soft_reset_pulse <= 1'b1;
                    end
                    default: ;
                endcase
            end
        end
    end

    assign jtag_busy = (ir_in != IR_BYPASS);

endmodule
