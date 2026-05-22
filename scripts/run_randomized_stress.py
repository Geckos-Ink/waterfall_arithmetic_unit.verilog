#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/python"))

from waugen.compiler import compile_project
from waugen.config import load_config
from waugen.scheduler import build_schedule


OPS = ["add", "sub", "mul", "max"]


def all_coords(grid_x: int, grid_y: int) -> list[dict[str, int]]:
    return [{"x": x, "y": y} for y in range(grid_y) for x in range(grid_x)]


def pick_candidate_cores(rng: random.Random, *, grid_x: int, grid_y: int) -> list[dict[str, int]]:
    coords = all_coords(grid_x, grid_y)
    count = rng.randint(1, min(3, len(coords)))
    picks = rng.sample(coords, count)
    picks.sort(key=lambda c: (c["y"], c["x"]))
    return picks


def build_random_payload(seed: int) -> dict:
    rng = random.Random(seed)

    grid_x = rng.choice([3, 4])
    grid_y = rng.choice([2, 3])

    flow_count = rng.randint(2, 5)
    flows: list[dict] = []

    for flow_idx in range(flow_count):
        flow_id = 50 + flow_idx
        node_count = rng.randint(2, 7)

        nodes: list[dict] = []
        for node_idx in range(node_count):
            node_id = f"n{node_idx}"
            op = rng.choice(OPS)

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
                    "candidate_cores": pick_candidate_cores(rng, grid_x=grid_x, grid_y=grid_y),
                    "directive": rng.choice(["auto", "prefer_locality", "prefer_balance"]),
                },
            }
            if deps:
                node["deps"] = deps

            if op in {"add", "sub", "mul"} and rng.random() < 0.35:
                imm = rng.randint(-9, 9)
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
    program_count = rng.randint(1, min(3, len(flow_ids)))
    programs: list[dict] = []

    for idx in range(program_count):
        chosen = sorted(rng.sample(flow_ids, rng.randint(1, min(3, len(flow_ids)))))
        replicas = rng.randint(1, 2)
        programs.append(
            {
                "id": idx + 1,
                "name": f"stress_program_{idx + 1}",
                "flows": chosen,
                "priority": rng.randint(1, 3),
                "replicas": replicas,
                "max_parallel_flows": rng.randint(1, max(1, len(chosen) * replicas)),
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
            "library": OPS,
            "overrides": {"mul": {"latency": 3, "pipelined": True}},
        },
        "compiler": {
            "routing": rng.choice(["waterfall", "serpentine"]),
            "allow_adaptive_reroute": True,
            "fallback_radius": rng.randint(1, 2),
            "allow_cycle_recurrence": True,
            "station_cache": {
                "entries": rng.choice([2, 4, 8]),
                "replacement_policy": rng.choice(["fifo", "lru"]),
            },
        },
        "scheduler": {
            "strategy": "dependency_aware",
            "program_policy": rng.choice(["weighted_fair", "round_robin", "strict_priority"]),
            "emit_timeline": True,
        },
        "flows": flows,
        "programs": programs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run randomized WAU multi-flow stress scheduling")
    parser.add_argument("--start-seed", type=int, default=2000)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--report", type=Path, default=None, help="Optional JSON report output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    seeds = [args.start_seed + i for i in range(args.count)]
    total_instructions = 0
    total_fallback = 0
    makespans: list[int] = []
    ops_seen: set[str] = set()
    cores_seen: set[int] = set()
    programs_seen: set[int] = set()

    for seed in seeds:
        payload = build_random_payload(seed)

        try:
            with tempfile.TemporaryDirectory() as td:
                config_path = Path(td) / "stress.json"
                config_path.write_text(json.dumps(payload, indent=2) + "\n")

                config = load_config(config_path)
                project = compile_project(config)
                schedule = build_schedule(project)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: seed={seed} error={exc}")
            return 1

        instructions = schedule.instructions
        total_instructions += len(instructions)
        makespans.append(schedule.makespan_cycles)

        for ins in instructions:
            ops_seen.add(ins.op_name)
            cores_seen.add(ins.core_index)
            programs_seen.add(ins.program_id)
            if ins.used_fallback:
                total_fallback += 1

    avg_makespan = (sum(makespans) / len(makespans)) if makespans else 0.0
    fallback_ratio = (total_fallback / total_instructions) if total_instructions else 0.0
    report = {
        "seed_start": args.start_seed,
        "seed_count": args.count,
        "seeds": seeds,
        "runs_passed": len(seeds),
        "total_instructions": total_instructions,
        "fallback_instruction_count": total_fallback,
        "fallback_instruction_ratio": round(fallback_ratio, 6),
        "makespan_min": min(makespans) if makespans else 0,
        "makespan_max": max(makespans) if makespans else 0,
        "makespan_avg": avg_makespan,
        "op_coverage": sorted(ops_seen),
        "core_coverage": sorted(cores_seen),
        "program_coverage": sorted(programs_seen),
    }

    print("Randomized stress run passed")
    print(f"Seeds: {seeds[0]}..{seeds[-1]} ({len(seeds)} runs)")
    print(f"Instructions: {total_instructions}")
    print(
        "Makespan cycles: "
        f"min={report['makespan_min']} max={report['makespan_max']} avg={report['makespan_avg']:.2f}"
    )
    print(f"Fallback instructions: {total_fallback}")
    print(f"Operation coverage: {', '.join(report['op_coverage'])}")
    print(f"Core coverage (indices): {report['core_coverage']}")
    print(f"Program coverage (ids): {report['program_coverage']}")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Wrote report: {args.report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
