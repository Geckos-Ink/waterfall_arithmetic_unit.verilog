##############################################################################
# Generic vJTAG MMIO TCL server for the wau_vjtag_bridge protocol.
#
# Run this with `quartus_stp -t wau_jtag_server.tcl [--port N] [--instance K]`.
# The script opens the USB-Blaster, locks the device, and exposes a tiny
# line-protocol TCP server (default port 2540) so Python (or any other) host
# code can speak to the FPGA without needing to fork quartus_stp per command.
#
# Line protocol (commands are case-insensitive, fields are decimal):
#   W <addr> <data>     -> shifts IR_WRITE, returns "OK"
#   R <addr>            -> shifts IR_READ_ADDR + IR_READ_DATA, returns "D <val>"
#   OBS                 -> shifts IR_OBS, returns "D <aux_word>"
#   RST                 -> shifts IR_RESET, returns "OK"
#   PING                -> returns "PONG"
#   QUIT                -> closes the connection
#
# Values are 32-bit unsigned integers (host-side python sends/receives ints).
# Addresses are 8-bit unsigned.
#
# Designed to match wau_vjtag_bridge.v (IR width 4, write DR width 40, read
# address DR width 8, read data DR width 32, obs DR width 32). If you respin
# the bridge with different widths, just update the four constants below.
##############################################################################

# ----- Configurable widths (match wau_vjtag_bridge.v) -----
set IR_BYPASS    0
set IR_WRITE     1
set IR_READ_ADDR 2
set IR_READ_DATA 3
set IR_OBS       4
set IR_RESET     15

set ADDR_WIDTH      8
set DATA_WIDTH      32
set WRITE_DR_WIDTH  40   ;# ADDR_WIDTH + DATA_WIDTH
set OBS_DR_WIDTH    32

# ----- CLI argument parsing -----
set listen_port 2540
set instance_index 0
set hardware_filter "USB-Blaster"

set i 0
while {$i < [llength $argv]} {
    set a [lindex $argv $i]
    switch -- $a {
        "--port"     { incr i; set listen_port [lindex $argv $i] }
        "--instance" { incr i; set instance_index [lindex $argv $i] }
        "--hardware" { incr i; set hardware_filter [lindex $argv $i] }
        default {}
    }
    incr i
}

# ----- Locate USB-Blaster + first device -----
set usbblaster_name ""
foreach hw [get_hardware_names] {
    if { [string match "$hardware_filter*" $hw] } { set usbblaster_name $hw; break }
}
if { $usbblaster_name eq "" } {
    puts "ERROR: no programming hardware matching $hardware_filter found."
    exit 1
}
puts "Using hardware: $usbblaster_name"

set test_device ""
foreach dev [get_device_names -hardware_name $usbblaster_name] {
    if { [string match "@1*" $dev] } { set test_device $dev; break }
}
if { $test_device eq "" } {
    puts "ERROR: no JTAG device @1 on $usbblaster_name. Is the board powered + cabled?"
    exit 1
}
puts "Using device:  $test_device"

# ----- Low-level helpers -----
proc open_jtag {} {
    global usbblaster_name test_device
    open_device -hardware_name $usbblaster_name -device_name $test_device
    device_lock -timeout 10000
}

proc close_jtag {} {
    catch { device_unlock }
    catch { close_device }
}

proc to_binstr {value width} {
    # Format an unsigned integer as zero-padded MSB-first binary string.
    # Quartus device_virtual_dr_shift documents -dr_value as a binary string
    # whose FIRST character is the MOST significant bit. The driver then shifts
    # the value out LSB-first onto TDI so e.g. "0000101" loads DR=7'b0000101=5.
    set v $value
    set out ""
    for {set k 0} {$k < $width} {incr k} {
        set out "[expr {$v & 1}]$out"
        set v [expr {$v >> 1}]
    }
    return $out
}

proc from_binstr {s} {
    # device_virtual_dr_shift returns a binary string whose FIRST character is
    # the MOST significant bit captured at the DR input.
    set v 0
    set bits [split $s ""]
    set len [llength $bits]
    for {set k 0} {$k < $len} {incr k} {
        set bit_pos [expr {$len - 1 - $k}]
        if {[lindex $bits $k] eq "1"} {
            set v [expr {$v | (wide(1) << $bit_pos)}]
        }
    }
    return $v
}

proc do_write {addr data} {
    global instance_index IR_WRITE IR_BYPASS WRITE_DR_WIDTH ADDR_WIDTH DATA_WIDTH
    set u_addr [expr {$addr & ((1 << $ADDR_WIDTH) - 1)}]
    set u_data [expr {$data & 0xFFFFFFFF}]
    # DR layout: [WRITE_DR_WIDTH-1 -: ADDR_WIDTH]=addr, [DATA_WIDTH-1:0]=data
    # i.e. data occupies LSBs, addr is the upper ADDR_WIDTH bits.
    set combined [expr {(wide($u_addr) << $DATA_WIDTH) | wide($u_data)}]
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_WRITE -no_captured_ir_value
    device_virtual_dr_shift -instance_index $instance_index \
        -dr_value [to_binstr $combined $WRITE_DR_WIDTH] -length $WRITE_DR_WIDTH \
        -no_captured_dr_value
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_BYPASS -no_captured_ir_value
}

proc do_read {addr} {
    global instance_index IR_READ_ADDR IR_READ_DATA IR_BYPASS ADDR_WIDTH DATA_WIDTH
    set u_addr [expr {$addr & ((1 << $ADDR_WIDTH) - 1)}]
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_READ_ADDR -no_captured_ir_value
    device_virtual_dr_shift -instance_index $instance_index \
        -dr_value [to_binstr $u_addr $ADDR_WIDTH] -length $ADDR_WIDTH \
        -no_captured_dr_value
    # MMIO read latency: a single byte-shift over USB-Blaster is many us, so
    # mmio_readdatavalid has already pulsed by the time we shift IR_READ_DATA.
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_READ_DATA -no_captured_ir_value
    set ret [device_virtual_dr_shift -instance_index $instance_index \
        -dr_value [to_binstr 0 $DATA_WIDTH] -length $DATA_WIDTH]
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_BYPASS -no_captured_ir_value
    return [from_binstr $ret]
}

proc do_obs {} {
    global instance_index IR_OBS IR_BYPASS OBS_DR_WIDTH
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_OBS -no_captured_ir_value
    set ret [device_virtual_dr_shift -instance_index $instance_index \
        -dr_value [to_binstr 0 $OBS_DR_WIDTH] -length $OBS_DR_WIDTH]
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_BYPASS -no_captured_ir_value
    return [from_binstr $ret]
}

proc do_reset {} {
    global instance_index IR_RESET IR_BYPASS
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_RESET -no_captured_ir_value
    device_virtual_dr_shift -instance_index $instance_index -dr_value 0 -length 1 -no_captured_dr_value
    device_virtual_ir_shift -instance_index $instance_index -ir_value $IR_BYPASS -no_captured_ir_value
}

# ----- TCP server -----
proc start_server {port} {
    set s [socket -server accept $port]
    puts "vJTAG MMIO server listening on TCP $port"
    vwait forever
}

proc accept {sock addr port} {
    fconfigure $sock -buffering line -translation auto
    fileevent $sock readable [list handle $sock]
    puts "accept $sock from $addr:$port"
    catch { puts $sock "READY" }
}

proc handle {sock} {
    if {[eof $sock] || [catch {gets $sock line}]} {
        close $sock
        return
    }
    set line [string trim $line]
    if {$line eq ""} return
    set parts [split $line]
    set cmd [string toupper [lindex $parts 0]]

    if {[catch {
        switch -- $cmd {
            "W" {
                set addr [expr {[lindex $parts 1]}]
                set data [expr {[lindex $parts 2]}]
                do_write $addr $data
                puts $sock "OK"
            }
            "R" {
                set addr [expr {[lindex $parts 1]}]
                set v [do_read $addr]
                puts $sock "D $v"
            }
            "OBS" {
                set v [do_obs]
                puts $sock "D $v"
            }
            "RST" {
                do_reset
                puts $sock "OK"
            }
            "PING" { puts $sock "PONG" }
            "QUIT" { puts $sock "BYE"; close $sock; return }
            default { puts $sock "ERR unknown command: $cmd" }
        }
    } err]} {
        puts "ERROR handling '$line': $err"
        catch { puts $sock "ERR $err" }
    }
}

# Open device once, keep it locked for the lifetime of the server.
open_jtag

if {[catch {start_server $listen_port} err]} {
    puts "Server exited: $err"
} else {
    puts "Server exited cleanly"
}

close_jtag
