from __future__ import annotations

from pathlib import Path
import unittest

from waugen.cw_reference import (
    CWReferenceError,
    compute_expected_values,
    evaluate_project_flow,
)
from waugen.compiler import compile_project
from waugen.config import load_config


COMPILED_CONFIG = Path("src/python/configs/wau_example_pogram_compiled.json")
EXAMPLE_FLOW_ID = 90


# Known-good reference values: derived from the same coordinator reduction used by
# the compiled Verilog (one pass over the flow's linear stages, accumulator seeded
# with `a`, operand-B register seeded with `b`). The numbers below were taken from
# a verified hardware run captured in benchmarks/example_pogram_benchmark.txt.
HARDWARE_GOLDEN = {
    (10, 4): 481,
    (-7, 5): 421,
    (21, -3): 0,
    (0, 0): 1,
    (31, 31): 151963,
    (-15, -9): 0,
    (127, -11): 0,
    (-64, 17): 2092,
}


class CWReferenceTests(unittest.TestCase):
    def test_reference_matches_hardware_golden(self) -> None:
        project = compile_project(load_config(COMPILED_CONFIG))
        for (a, b), expected in HARDWARE_GOLDEN.items():
            got = evaluate_project_flow(project, EXAMPLE_FLOW_ID, a, b)
            self.assertEqual(
                got,
                expected,
                f"reference disagrees with hardware golden for (a={a}, b={b})",
            )

    def test_compute_expected_values_returns_one_row_per_case(self) -> None:
        cases = [(1, 10, 4), (2, 0, 0), (3, -1, -1)]
        rows = compute_expected_values(COMPILED_CONFIG, EXAMPLE_FLOW_ID, cases)
        self.assertEqual([row["case"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["expected"], 481)

    def test_evaluate_unknown_flow_raises(self) -> None:
        project = compile_project(load_config(COMPILED_CONFIG))
        with self.assertRaises(CWReferenceError):
            evaluate_project_flow(project, 9999, 0, 0)


if __name__ == "__main__":
    unittest.main()
