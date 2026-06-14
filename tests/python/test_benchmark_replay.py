# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from waugen.benchmark_replay import (
    ReplayPlanError,
    build_replay_plan,
    parse_tuning_summary,
    select_replay_candidates,
)


SUMMARY_PATH = Path("benchmarks/example_pogram_tuning_latest.txt")


class BenchmarkReplayTests(unittest.TestCase):
    def test_saved_summary_selects_best_and_stage_winners(self) -> None:
        candidates = parse_tuning_summary(SUMMARY_PATH.read_text())
        selected = select_replay_candidates(candidates, "best-and-stage-winners")

        self.assertEqual(
            [candidate.run_name for candidate in selected],
            ["r33_program", "r6_topology", "r43_scheduler"],
        )

    def test_saved_summary_selects_worst_passing_candidate(self) -> None:
        candidates = parse_tuning_summary(SUMMARY_PATH.read_text())
        selected = select_replay_candidates(candidates, "worst")

        self.assertEqual(selected[0].run_name, "r15_topology")
        self.assertEqual(selected[0].status, "pass")

    def test_shell_fields_convert_auto_to_empty_overrides(self) -> None:
        candidate = build_replay_plan(SUMMARY_PATH, "best")[0]
        fields = candidate.shell_fields()

        self.assertEqual(candidate.run_name, "r33_program")
        self.assertEqual(fields[6], "")
        self.assertEqual(fields[7], "")

    def test_missing_all_runs_section_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReplayPlanError, "All Runs"):
            parse_tuning_summary("WAU CW Autotune Summary (latest)\n")

    def test_unsupported_mode_is_rejected(self) -> None:
        candidates = parse_tuning_summary(SUMMARY_PATH.read_text())
        with self.assertRaisesRegex(ReplayPlanError, "unsupported replay mode"):
            select_replay_candidates(candidates, "everything")

    def test_missing_summary_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.txt"
            with self.assertRaisesRegex(ReplayPlanError, "not found"):
                build_replay_plan(path, "best")


if __name__ == "__main__":
    unittest.main()
