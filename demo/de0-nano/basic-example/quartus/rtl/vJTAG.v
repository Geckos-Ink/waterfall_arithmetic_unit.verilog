// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// See LICENSE at the repository root.
//
// Wrapper around Altera's sld_virtual_jtag megafunction with a 4-bit IR.
// Reusable across any DE0-Nano / Cyclone IV E design that needs a host-driven
// JTAG-side register file. Pair this with wau_vjtag_bridge.v to get a
// generic 8b-address / 32b-data MMIO master.
//
// Derived structurally from the vJTAG_DE0-Nano_Example reference (1-bit IR);
// widened to 4 bits so multiple commands (write/read-addr/read-data/bypass)
// can be selected without overloading a single DR.

`timescale 1 ps / 1 ps

module vJTAG (
    output wire        tck,
    output wire        tdi,
    input  wire        tdo,
    output wire [3:0]  ir_in,
    input  wire [3:0]  ir_out,
    output wire        virtual_state_cdr,
    output wire        virtual_state_cir,
    output wire        virtual_state_e1dr,
    output wire        virtual_state_e2dr,
    output wire        virtual_state_pdr,
    output wire        virtual_state_sdr,
    output wire        virtual_state_udr,
    output wire        virtual_state_uir
);

    sld_virtual_jtag sld_virtual_jtag_component (
        .ir_out             (ir_out),
        .tdo                (tdo),
        .virtual_state_cir  (virtual_state_cir),
        .virtual_state_pdr  (virtual_state_pdr),
        .ir_in              (ir_in),
        .tdi                (tdi),
        .virtual_state_udr  (virtual_state_udr),
        .tck                (tck),
        .virtual_state_e1dr (virtual_state_e1dr),
        .virtual_state_uir  (virtual_state_uir),
        .virtual_state_cdr  (virtual_state_cdr),
        .virtual_state_e2dr (virtual_state_e2dr),
        .virtual_state_sdr  (virtual_state_sdr)
    );
    defparam
        sld_virtual_jtag_component.sld_auto_instance_index = "YES",
        sld_virtual_jtag_component.sld_instance_index = 0,
        sld_virtual_jtag_component.sld_ir_width = 4,
        sld_virtual_jtag_component.sld_sim_action = "",
        sld_virtual_jtag_component.sld_sim_n_scan = 0,
        sld_virtual_jtag_component.sld_sim_total_length = 0;

endmodule
