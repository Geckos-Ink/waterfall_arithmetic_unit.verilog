#**************************************************************
# WAU DE0-Nano basic-example timing constraints.
#
# CLOCK_50 = 50 MHz on-board oscillator (PIN_R8). vJTAG / TCK is
# constrained automatically by Quartus as a generated clock; we
# explicitly false-path the readdata-crossing register pair so
# the async TCK->CLOCK_50 crossing in wau_vjtag_bridge.v is honored.
#**************************************************************

create_clock -name CLOCK_50 -period 20.000 [get_ports {CLOCK_50}]

# vJTAG / sld_virtual_jtag adds its own JTAG clock + altera_reserved_tck.
# Pull it in as a constrained 10 MHz clock if the auto-derivation misses it.
derive_pll_clocks
derive_clock_uncertainty

# Async crossings between TCK and CLOCK_50 in the bridge are explicitly
# resynchronized; mark them as false paths so the fitter doesn't fight them.
set_false_path -from [get_clocks {altera_reserved_tck}] -to [get_clocks {CLOCK_50}]
set_false_path -from [get_clocks {CLOCK_50}] -to [get_clocks {altera_reserved_tck}]

# Push buttons / switches: treat as untimed slow inputs.
set_false_path -from [get_ports {KEY[*] SW[*]}] -to *
set_false_path -from * -to [get_ports {LED[*]}]
