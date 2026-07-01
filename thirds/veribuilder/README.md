# VeriBuilder

VeriBuilder is a small Python library for constructing parameterized Verilog projects from Python code.
It is intentionally independent from the WAU compiler and scheduler so it can be moved into its own
repository and reused by other RTL generators.

The library provides:

- a `VerilogProject` manifest for generated files,
- deterministic project emission to an output directory,
- optional Verilog license/header insertion,
- a lightweight text renderer with `{{ name }}` placeholders,
- feature-gated file registration for build variants.

## Example

```python
from veribuilder import TemplateRenderer, VerilogHeader, VerilogProject

renderer = TemplateRenderer(parameters={"module_name": "demo_counter", "width": 8})
project = VerilogProject(header=VerilogHeader.spdx("MIT"))

project.add_verilog(
    "demo_counter.v",
    renderer.render(
        """
module {{ module_name }} #(
    parameter WIDTH = {{ width }}
) (
    input wire clk,
    output reg [WIDTH-1:0] value
);
    always @(posedge clk) begin
        value <= value + 1'b1;
    end
endmodule
"""
    ),
)

project.emit("generated")
```

## Feature Gates

```python
project = VerilogProject(features={"board_wrapper"})
project.add_verilog("core.v", "module core; endmodule\n")
project.add_verilog(
    "board_top.v",
    "module board_top; endmodule\n",
    when="board_wrapper",
)
```

Files with unmet feature gates are omitted from the emitted project.
