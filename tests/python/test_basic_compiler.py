from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from waugen.basic_compiler import (
    BasicCompilerError,
    compile_expression_to_stages,
    merge_expression_into_config,
)


class BasicCompilerTests(unittest.TestCase):
    def test_compile_expression_chain(self) -> None:
        stages = compile_expression_to_stages("((a + b) * 3) - b")

        self.assertEqual(len(stages), 3)
        self.assertEqual(stages[0].op, "add")
        self.assertIsNone(stages[0].immediate_b)

        self.assertEqual(stages[1].op, "mul")
        self.assertEqual(stages[1].immediate_b, 3)

        self.assertEqual(stages[2].op, "sub")
        self.assertIsNone(stages[2].immediate_b)

    def test_compile_expression_reject_non_chain(self) -> None:
        with self.assertRaises(BasicCompilerError):
            compile_expression_to_stages("a + (b * 3)")

    def test_merge_expression_into_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            base_path = td_path / "base.json"
            out_path = td_path / "out.json"

            base_payload = {
                "project": "tmp",
                "device": {
                    "preset": "intel_de0_nano",
                    "grid": {"x": 2, "y": 2},
                },
                "operations": {
                    "library": ["add"],
                },
                "flows": [],
            }
            base_path.write_text(json.dumps(base_payload, indent=2) + "\n")

            flow = merge_expression_into_config(
                base_config_path=base_path,
                out_config_path=out_path,
                expr="((a + b) * 3) - b",
                flow_id=17,
                name="compiled_expr",
                entry_x=0,
                entry_y=0,
                replace_existing=False,
            )

            self.assertEqual(flow["id"], 17)
            payload = json.loads(out_path.read_text())
            self.assertEqual(len(payload["flows"]), 1)

            library = payload["operations"]["library"]
            self.assertIn("add", library)
            self.assertIn("mul", library)
            self.assertIn("sub", library)


if __name__ == "__main__":
    unittest.main()
