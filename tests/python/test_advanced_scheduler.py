from __future__ import annotations

from pathlib import Path
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule


class AdvancedSchedulerTests(unittest.TestCase):
    def test_multiprogram_dag_and_recurrence(self) -> None:
        config_path = Path("src/python/configs/wau_2d_multiprogram_demo.json")
        config = load_config(config_path)

        self.assertEqual(len(config.programs), 2)

        project = compile_project(config)
        schedule = build_schedule(project)

        self.assertGreater(len(schedule.instructions), 0)

        program_ids = {ins.program_id for ins in schedule.instructions}
        self.assertEqual(program_ids, {1, 2})

        # Ensure recurrence was unrolled for the control loop.
        acc_iters = {
            ins.iteration
            for ins in schedule.instructions
            if ins.flow_id == 12 and ins.node_id == "acc"
        }
        mix_iters = {
            ins.iteration
            for ins in schedule.instructions
            if ins.flow_id == 12 and ins.node_id == "mix"
        }
        self.assertEqual(acc_iters, {0, 1, 2, 3})
        self.assertEqual(mix_iters, {0, 1, 2, 3})

        # For one replica of async_branches, merge must start only after both branches complete.
        branch_ins = [
            ins
            for ins in schedule.instructions
            if ins.program_id == 1 and ins.program_replica == 0 and ins.flow_id == 11 and ins.iteration == 0
        ]
        self.assertGreaterEqual(len(branch_ins), 3)

        by_node = {ins.node_id: ins for ins in branch_ins}
        self.assertIn("left_add", by_node)
        self.assertIn("right_max", by_node)
        self.assertIn("merge_mul", by_node)

        merge = by_node["merge_mul"]
        left = by_node["left_add"]
        right = by_node["right_max"]

        self.assertGreaterEqual(merge.cycle_start, left.cycle_end)
        self.assertGreaterEqual(merge.cycle_start, right.cycle_end)


if __name__ == "__main__":
    unittest.main()
