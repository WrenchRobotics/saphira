"""
Validates saphirapy's collision replay against brom_drake's own planner on ChemLab1.

The oracle is the collision check that brom's PrototypicalPlannerSystem hands to the
planning algorithm. It queries the same production diagram (same collision filters),
so at the planner's threshold the two must agree on every configuration. A
disagreement means the replay is wrong (wrong context, lost filters, wrong model
instance, ...), not that the plan is bad.

Run on its own, with a printed report:

    .venv/bin/python -m pytest test/saphirapy/collisions_chem_lab1_validation_test.py -v -s
"""

import unittest

import numpy as np
from brom_drake.motion_planning.algorithms.rrt.connect import (
    RRTConnectPlanner,
    RRTConnectPlannerConfig,
)
from brom_drake.productions.motion_planning.offline import ChemLab1
from pydrake.all import Simulator

from saphirapy.collisions import (
    CollisionSummary,
    collisions_along_configuration_history,
    summarize_collisions,
)

# brom's PrototypicalPlannerSystem default: a pair penetrating deeper than this collides.
# TODO: read it from the production once kwesiRutledge/brom_drake-py#106 / #107 land.
PLANNER_THRESHOLD = 1e-2
# OfflineKinematicMotionPlanningEvaluator.calculate_collision_summary default.
EVALUATOR_THRESHOLD = 1e-3

NUM_PLAN_SAMPLES = 400
NUM_RANDOM_CONFIGURATIONS = 200


def _report(title: str, summary: CollisionSummary) -> str:
    lines = [
        f"{title}:",
        f"  collided={summary.collided} num_collisions={summary.num_collisions} "
        f"samples_in_collision={summary.num_samples_in_collision} "
        f"first_index={summary.first_collision_index} max_depth={summary.max_depth * 1e3:.2f} mm",
    ]
    for (a, b), count in sorted(summary.collisions_per_pair.items()):
        lines.append(f"    {a} <-> {b}: {count}")
    return "\n".join(lines)


class ChemLab1CollisionValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = ChemLab1(meshcat_port_number=None)
        cls.planner = RRTConnectPlanner(
            cls.production.arm,
            cls.production.plant,
            cls.production.scene_graph,
            # Same settings as offline_kinematic_motion_planning_test; the seed is fixed
            # by RRTConnectPlannerConfig, so the plan is the same on every run.
            config=RRTConnectPlannerConfig(
                steering_step_size=0.1,
                prob_sample_goal=0.03,
                max_iterations=int(1e5),
                convergence_threshold=1e-3,
                debug_flag=False,
            ),
        )

        captured = {}

        def plan(q_start, q_goal, collision_check):
            captured["collision_check"] = collision_check
            return cls.planner.plan(q_start, q_goal, collision_check)

        _, cls.root_context = cls.production.easy_cast_and_build(plan, with_watcher=False)

        # Planning happens when the plan dispenser first asks for it; no need to
        # simulate the whole (minute-long) execution.
        simulator = Simulator(cls.production.diagram, cls.root_context)
        simulator.Initialize()
        simulator.AdvanceTo(0.01)

        cls.planner_says_collides = captured["collision_check"]

        trajectory = cls.production.plan_dispenser.planned_trajectory
        times = np.linspace(trajectory.start_time(), trajectory.end_time(), NUM_PLAN_SAMPLES)
        cls.plan_samples = np.array([trajectory.value(t).flatten() for t in times])

        np.random.seed(0)  # sample_random_configuration uses np.random
        cls.random_samples = np.array(
            [cls.planner.sample_random_configuration() for _ in range(NUM_RANDOM_CONFIGURATIONS)]
        )

    def _replay(self, samples: np.ndarray, threshold: float):
        return collisions_along_configuration_history(
            self.production.plant,
            self.production.scene_graph,
            self.root_context,
            self.production.arm,
            samples,
            threshold=threshold,
        )

    def _assert_agrees_with_planner(self, samples: np.ndarray):
        log = self._replay(samples, PLANNER_THRESHOLD)
        disagreements = []
        for index, (q, sample_collisions) in enumerate(zip(samples, log)):
            planner_collides = bool(self.planner_says_collides(q))
            replay_collides = len(sample_collisions) > 0
            if planner_collides != replay_collides:
                disagreements.append(
                    f"  #{index}: planner={planner_collides} replay={replay_collides} "
                    f"pairs={sample_collisions} q={np.round(q, 4).tolist()}"
                )
        self.assertEqual(
            disagreements,
            [],
            msg="Replay disagrees with brom's planner check:\n" + "\n".join(disagreements),
        )
        return log

    def test_agrees_with_planner_on_random_configurations(self):
        log = self._assert_agrees_with_planner(self.random_samples)

        # Both outcomes must be exercised, or agreement proves nothing.
        num_colliding = sum(1 for sample in log if sample)
        print(f"\n{num_colliding}/{len(log)} random configurations collide (both checks agree)")
        self.assertGreaterEqual(num_colliding, 10)
        self.assertGreaterEqual(len(log) - num_colliding, 10)

    def test_agrees_with_planner_along_the_rrt_connect_plan(self):
        log = self._assert_agrees_with_planner(self.plan_samples)
        summary = summarize_collisions(log)
        print("\n" + _report(f"RRT-Connect plan at the planner threshold ({PLANNER_THRESHOLD} m)", summary))

        # The planner only accepts waypoints that are collision free at its threshold.
        self.assertFalse(summary.collided, msg=_report("Unexpected collisions", summary))

    def test_rrt_connect_plan_grazes_the_cupboard_at_the_evaluator_threshold(self):
        """
        The plan passes within the 1 cm the planner tolerates, so the evaluator's
        stricter 1 mm threshold reports shallow contacts with the cupboard. This is
        what the CI fanout reports as "Number of Collisions" for ChemLab1.
        """
        summary = summarize_collisions(self._replay(self.plan_samples, EVALUATOR_THRESHOLD))
        print("\n" + _report(f"RRT-Connect plan at the evaluator threshold ({EVALUATOR_THRESHOLD} m)", summary))

        self.assertTrue(summary.collided)
        for pair in summary.collisions_per_pair:
            self.assertTrue(
                any(name.startswith("cupboard::") for name in pair),
                msg=f"Expected only cupboard contacts, got {pair}",
            )
        self.assertGreater(summary.max_depth, EVALUATOR_THRESHOLD)
        self.assertLessEqual(summary.max_depth, PLANNER_THRESHOLD)

    def test_replay_leaves_the_production_context_untouched(self):
        plant_context = self.production.plant.GetMyContextFromRoot(self.root_context)
        before = self.production.plant.GetPositions(plant_context).copy()

        self._replay(self.random_samples[:20], EVALUATOR_THRESHOLD)

        np.testing.assert_array_equal(self.production.plant.GetPositions(plant_context), before)


if __name__ == "__main__":
    unittest.main()
