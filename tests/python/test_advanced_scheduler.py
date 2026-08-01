from __future__ import annotations

from pathlib import Path
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule, core_index


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


class ScheduledCoreStaysWithinRuntimeDispatchChoicesTests(unittest.TestCase):
    """The offline plan and contract ROM must describe an executable runtime
    path: generated dispatch can choose only primary or fallback, never an
    analysis-only third candidate retained by the compiler."""

    def _assert_schedule_stays_within_compiled_choices(self, config_path: str) -> None:
        config = load_config(Path(config_path))
        project = compile_project(config)
        schedule = build_schedule(project)

        grid_x = config.device.grid_x
        grid_y = config.device.grid_y

        for flow in project.flows:
            stage_by_node_id = dict(zip(flow.linear_node_order, flow.stages))

            for ins in schedule.instructions:
                if ins.flow_id != flow.flow_id:
                    continue
                stage = stage_by_node_id.get(ins.node_id)
                if stage is None:
                    continue
                primary_idx = core_index(
                    stage.primary_core.x, stage.primary_core.y, grid_x, stage.primary_core.z, grid_y
                )
                fallback_idx = primary_idx
                if stage.fallback_core is not None:
                    fallback_idx = core_index(
                        stage.fallback_core.x, stage.fallback_core.y, grid_x, stage.fallback_core.z, grid_y
                    )
                self.assertIn(
                    ins.core_index,
                    {primary_idx, fallback_idx},
                    msg=(
                        f"{config_path}: flow {flow.flow_id} node {ins.node_id} scheduled onto "
                        f"core {ins.core_index}, outside {{primary={primary_idx}, fallback={fallback_idx}}}"
                    ),
                )

    def test_de0_nano_demo(self) -> None:
        self._assert_schedule_stays_within_compiled_choices(
            "src/python/configs/wau_de0_nano_demo.json"
        )

    def test_3d_demo(self) -> None:
        self._assert_schedule_stays_within_compiled_choices(
            "src/python/configs/wau_3d_demo.json"
        )

    def test_multiprogram_demo(self) -> None:
        self._assert_schedule_stays_within_compiled_choices(
            "src/python/configs/wau_2d_multiprogram_demo.json"
        )


if __name__ == "__main__":
    unittest.main()
