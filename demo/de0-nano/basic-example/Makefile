# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# POSIX-style entrypoints. The PowerShell scripts in scripts/ are the
# authoritative path on Windows; this Makefile mirrors them for use under
# Git Bash, WSL, or MSYS where `make` is available.

QUARTUS_ROOT ?= C:/altera_standard/25.1std
DEMO_ROOT    := $(CURDIR)
REPO_ROOT    := $(realpath $(DEMO_ROOT)/../../..)
PYTHON       ?= python

.PHONY: help generate build program server run run-iris clean

help:
	@echo "make generate   - regenerate WAU RTL into quartus/wau_rtl/"
	@echo "make build      - generate + quartus_sh full compile"
	@echo "make program    - quartus_pgm download .sof to USB-Blaster"
	@echo "make server     - run TCL vJTAG MMIO server (port 2540)"
	@echo "make run        - python benchmark against running server"
	@echo "make run-iris   - python Iris benchmark against running server"
	@echo "make clean      - remove build/ and Quartus output_files/"

generate:
	PYTHONPATH=$(REPO_ROOT)/src/python $(PYTHON) -m waugen generate \
	    --config host/config/wau_de0_nano_basic.json \
	    --out build/generated --summary
	mkdir -p quartus/wau_rtl
	cp build/generated/wau_defs.vh           quartus/wau_rtl/
	cp build/generated/wau_operation_alu.v   quartus/wau_rtl/
	cp build/generated/wau_neighbor_forward.v quartus/wau_rtl/
	cp build/generated/wau_highway_router.v  quartus/wau_rtl/
	cp build/generated/wau_highway_mesh.v    quartus/wau_rtl/
	cp build/generated/wau_core_station.v    quartus/wau_rtl/
	cp build/generated/wau_core.v            quartus/wau_rtl/
	cp build/generated/wau_coordinator.v     quartus/wau_rtl/
	cp build/generated/wau_host_mmio.v       quartus/wau_rtl/
	cp build/generated/wau_top.v             quartus/wau_rtl/

build: generate
	mkdir -p build
	printf 'load_package flow\nproject_open -force -revision wau_de0_nano_basic wau_de0_nano_basic\nexecute_flow -compile\nproject_close\n' > build/flow.tcl
	cd quartus && $(QUARTUS_ROOT)/quartus/bin64/quartus_sh.exe -t ../build/flow.tcl

program:
	@SOF=quartus/wau_de0_nano_basic.sof; \
	if [ ! -f "$$SOF" ]; then SOF=quartus/output_files/wau_de0_nano_basic.sof; fi; \
	$(QUARTUS_ROOT)/quartus/bin64/quartus_pgm.exe -c 1 -m jtag -o "p;$$SOF"

server:
	$(QUARTUS_ROOT)/quartus/bin64/quartus_stp.exe -t host/tcl/wau_jtag_server.tcl --port 2540

run:
	PYTHONPATH=host $(PYTHON) host/programs/run_benchmark.py \
	    --iters 256 --report build/benchmark_$$(date +%Y%m%d_%H%M%S).json

run-iris:
	PYTHONPATH=host $(PYTHON) host/programs/run_iris_stats_benchmark.py \
	    --report build/iris_benchmark_$$(date +%Y%m%d_%H%M%S).json

clean:
	rm -rf build/ quartus/wau_rtl quartus/db quartus/incremental_db quartus/output_files quartus/*.qws
