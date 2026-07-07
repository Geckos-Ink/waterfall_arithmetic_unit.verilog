# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest

from waugen.cli import main


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = main(argv)
    return rc, stdout.getvalue(), stderr.getvalue()


class CWLintCliTests(unittest.TestCase):
    def test_lints_host_side_cw_without_compile_template(self) -> None:
        rc, stdout, stderr = _run_cli(
            [
                "cw-lint",
                "--program-file",
                "CWs/samples/types/fixed_point.cw",
                "--json",
            ]
        )

        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["syntax"], "ok")
        self.assertIn("q8_8", payload["classes"])
        self.assertIn("main", payload["functions"])
        self.assertEqual(payload["pragmas"], {})
        self.assertIsNone(payload["compile_template"])

    def test_lints_compile_cw_template_when_requested(self) -> None:
        rc, stdout, stderr = _run_cli(
            [
                "cw-lint",
                "--program-file",
                "CWs/example-program.cw",
                "--compile-template",
                "--json",
            ]
        )

        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        template = payload["compile_template"]
        self.assertEqual(template["kernel_name"], "conv2d_residual_kernel")
        self.assertEqual(template["kernel_size"], 3)
        self.assertEqual(template["worker_count"], 8)
        self.assertEqual(payload["pragmas"]["lane_parallelism"], 4)

    def test_reports_line_located_pragma_error(self) -> None:
        rc, _stdout, stderr = _run_cli(
            [
                "cw-lint",
                "--program",
                "// @wau placement_policy=spread\nvoid main() {}",
            ]
        )

        self.assertEqual(rc, 2)
        self.assertIn("Invalid @wau placement_policy value 'spread' at line 1", stderr)

    def test_compile_template_lint_rejects_host_only_program(self) -> None:
        rc, _stdout, stderr = _run_cli(
            [
                "cw-lint",
                "--program-file",
                "CWs/samples/types/fixed_point.cw",
                "--compile-template",
            ]
        )

        self.assertEqual(rc, 2)
        self.assertIn("Unsupported or incomplete .cw program", stderr)


if __name__ == "__main__":
    unittest.main()
