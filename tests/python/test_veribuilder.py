# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

import tempfile
import unittest
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIBUILDER_SRC = REPO_ROOT / "thirds" / "veribuilder" / "src"
if str(VERIBUILDER_SRC) not in sys.path:
    sys.path.insert(0, str(VERIBUILDER_SRC))

from veribuilder import TemplateRenderer, VerilogHeader, VerilogProject  # noqa: E402


class VerilogProjectTests(unittest.TestCase):
    def test_emits_header_and_feature_gated_files(self) -> None:
        project = VerilogProject(
            header=VerilogHeader.spdx("MIT", "Generated for tests."),
            features={"top"},
        )
        project.add_verilog("core.v", "module core; endmodule\n")
        project.add_verilog("top.v", "module top; endmodule\n", when="top")
        project.add_verilog("board.v", "module board; endmodule\n", when="board")
        project.add_file("manifest.txt", "ok\n")

        with tempfile.TemporaryDirectory() as td:
            paths = project.emit(td)
            names = sorted(path.name for path in paths)

            self.assertEqual(names, ["core.v", "manifest.txt", "top.v"])
            self.assertTrue((Path(td) / "core.v").read_text().startswith("// SPDX-License-Identifier: MIT"))
            self.assertEqual((Path(td) / "manifest.txt").read_text(), "ok\n")
            self.assertFalse((Path(td) / "board.v").exists())

    def test_template_renderer_replaces_parameters(self) -> None:
        renderer = TemplateRenderer({"module": "counter", "width": 8})

        rendered = renderer.render("module {{ module }} #(parameter W={{width}}); endmodule\n")

        self.assertEqual(rendered, "module counter #(parameter W=8); endmodule\n")


if __name__ == "__main__":
    unittest.main()
