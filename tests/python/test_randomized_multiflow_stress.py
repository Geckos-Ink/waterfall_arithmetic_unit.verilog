from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule


_OPS = ["add", "sub", "mul", "max"]


def _coords(grid_x: int, grid_y: int) -> list[dict[str, int]]:
    return [{"x": x, "y": y} for y in range(grid_y) for x in range(grid_x)]


def _pick_candidate_cores(
    rng: random.Random,
    *,
    grid_x: int,
    grid_y: int,
) -> list[dict[str, int]]:
    all_coords = _coords(grid_x, grid_y)
    count = rng.randint(1, min(3, len(all_coords)))
    picks = rng.sample(all_coords, count)
    picks.sort(key=lambda c: (c["y"], c["x"]))
    return picks


def _build_random_payload(seed: int) -> dict:
    rng = random.Random(seed)

    grid_x = rng.choice([3, 4])
    grid_y = rng.choice([2, 3])

    flow_count = rng.randint(2, 4)
    flows: list[dict] = []

    for flow_idx in range(flow_count):
        flow_id = 20 + flow_idx
        node_count = rng.randint(2, 6)

        nodes: list[dict] = []
        for node_idx in range(node_count):
            node_id = f"n{node_idx}"
            op = rng.choice(_OPS)

            deps: list[str] = []
            if node_idx > 0:
                prior = [f"n{i}" for i in range(node_idx)]
                rng.shuffle(prior)
                dep_count = rng.randint(0, min(2, len(prior)))
                deps = prior[:dep_count]
                deps.sort(key=lambda raw: int(raw[1:]))

            node: dict = {
                "id": node_id,
                "op": op,
                "dtype": "int32",
                "placement": {
                    "candidate_cores": _pick_candidate_cores(rng, grid_x=grid_x, grid_y=grid_y),
                    "directive": rng.choice(["auto", "prefer_locality", "prefer_balance"]),
                },
            }

            if deps:
                node["deps"] = deps

            if op in {"add", "sub", "mul"} and rng.random() < 0.35:
                imm = rng.randint(-7, 7)
                if imm != 0:
                    node["immediate_b"] = imm

            nodes.append(node)

        flows.append(
            {
                "id": flow_id,
                "name": f"stress_flow_{flow_id}",
                "entry": {"x": 0, "y": 0},
                "nodes": nodes,
            }
        )

    flow_ids = [flow["id"] for flow in flows]

    program_count = rng.randint(1, min(3, flow_count))
    programs: list[dict] = []
    for program_idx in range(program_count):
        chosen_count = rng.randint(1, min(2, len(flow_ids)))
        chosen_flows = rng.sample(flow_ids, chosen_count)
        chosen_flows.sort()

        replicas = rng.randint(1, 2)
        max_parallel = rng.randint(1, max(1, len(chosen_flows) * replicas))

        programs.append(
            {
                "id": program_idx + 1,
                "name": f"stress_program_{program_idx + 1}",
                "flows": chosen_flows,
                "priority": rng.randint(1, 3),
                "replicas": replicas,
                "max_parallel_flows": max_parallel,
                "load_balance": rng.choice(["round_robin", "least_busy"]),
                "allow_async": True,
                "allow_out_of_order": rng.choice([True, False]),
            }
        )

    return {
        "project": f"wau_random_stress_{seed}",
        "output_module_name": "wau_top",
        "abstraction": {"language": "wau_flow_ir", "version": 1},
        "device": {
            "preset": "intel_de0_nano",
            "grid": {"x": grid_x, "y": grid_y},
            "coordinator_mode": "direct",
            "enable_runtime_auto_adapt": True,
            "data_width": 32,
            "flow_id_width": 12,
            "opcode_width": 8,
            "data_types": ["int32"],
        },
        "operations": {
            "library": _OPS,
            "overrides": {
                "mul": {"latency": 3, "pipelined": True},
            },
        },
        "compiler": {
            "routing": rng.choice(["waterfall", "serpentine"]),
            "allow_adaptive_reroute": True,
            "fallback_radius": rng.randint(1, 2),
            "allow_cycle_recurrence": True,
        },
        "scheduler": {
            "strategy": "dependency_aware",
            "program_policy": rng.choice(["weighted_fair", "round_robin", "strict_priority"]),
            "emit_timeline": True,
        },
        "flows": flows,
        "programs": programs,
    }


class RandomizedMultiFlowStressTests(unittest.TestCase):
    def test_randomized_configs_schedule_stably(self) -> None:
        seeds = list(range(1100, 1120))

        for seed in seeds:
            with self.subTest(seed=seed):
                payload = _build_random_payload(seed)

                with tempfile.TemporaryDirectory() as td:
                    config_path = Path(td) / "stress.json"
                    config_path.write_text(json.dumps(payload, indent=2) + "\n")

                    config = load_config(config_path)
                    project = compile_project(config)

                    schedule_a = build_schedule(project)
                    schedule_b = build_schedule(project)

                self.assertGreater(len(schedule_a.instructions), 0)
                self.assertEqual(schedule_a.to_json(), schedule_b.to_json())

                expected_makespan = max(ins.cycle_end for ins in schedule_a.instructions)
                self.assertEqual(schedule_a.makespan_cycles, expected_makespan)

                valid_program_ids = {program.program_id for program in config.programs}
                core_count = (
                    config.device.grid_x * config.device.grid_y * config.device.grid_z
                )

                for ins in schedule_a.instructions:
                    self.assertGreater(ins.cycle_end, ins.cycle_start)
                    self.assertIn(ins.program_id, valid_program_ids)
                    self.assertGreaterEqual(ins.core_index, 0)
                    self.assertLess(ins.core_index, core_count)


if __name__ == "__main__":
    unittest.main()
