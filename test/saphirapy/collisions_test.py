import unittest

import numpy as np
from pydrake.all import (
    AddMultibodyPlantSceneGraph,
    Box,
    CoulombFriction,
    DiagramBuilder,
    PrismaticJoint,
    RigidTransform,
    SpatialInertia,
    UnitInertia,
)

from saphirapy.collisions import (
    collisions_along_configuration_history,
    summarize_collisions,
)


class SummarizeCollisionsTest(unittest.TestCase):
    def test_no_collisions(self):
        summary = summarize_collisions([[], [], []])

        self.assertFalse(summary.collided)
        self.assertEqual(summary.num_collisions, 0)
        self.assertIsNone(summary.first_collision_index)
        self.assertEqual(summary.as_metrics(), {"Collision Occurred": 0.0, "Number of Collisions": 0.0})

    def test_sustained_contact_counts_once(self):
        touching = [("arm::link6", "shelf::board", 0.01)]
        summary = summarize_collisions([[], touching, touching, touching, []])

        self.assertTrue(summary.collided)
        self.assertEqual(summary.num_collisions, 1)
        self.assertEqual(summary.num_samples_in_collision, 3)
        self.assertEqual(summary.first_collision_index, 1)
        self.assertAlmostEqual(summary.max_depth, 0.01)

    def test_separate_and_touch_again_counts_twice_regardless_of_order(self):
        hit = [("a", "b", 0.002)]
        hit_reversed = [("b", "a", 0.004)]
        summary = summarize_collisions([hit, [], hit_reversed])

        self.assertEqual(summary.num_collisions, 2)
        self.assertEqual(summary.collisions_per_pair, {("a", "b"): 2})
        self.assertAlmostEqual(summary.max_depth, 0.004)

    def test_different_pairs_are_counted_separately(self):
        summary = summarize_collisions(
            [[("a", "b", 0.01)], [("a", "b", 0.01), ("a", "c", 0.02)]]
        )

        self.assertEqual(summary.num_collisions, 2)
        self.assertEqual(summary.collisions_per_pair, {("a", "b"): 1, ("a", "c"): 1})

    def test_ignored_pairs_do_not_count(self):
        resting = [("object::body", "table::top", 0.002)]
        summary = summarize_collisions([resting, resting], ignore_pairs=[("table::top", "object::body")])

        self.assertFalse(summary.collided)
        self.assertEqual(summary.max_depth, 0.0)


class CollisionsAlongConfigurationHistoryTest(unittest.TestCase):
    """
    A 0.1 m "robot" cube slides along x on a prismatic joint toward a fixed
    0.1 m "wall" cube at the origin.
    """

    def _build_scene(self):
        builder = DiagramBuilder()
        plant, scene_graph = AddMultibodyPlantSceneGraph(builder, time_step=0.0)
        side = 0.1
        inertia = SpatialInertia(1.0, np.zeros(3), UnitInertia.SolidBox(side, side, side))

        wall_model = plant.AddModelInstance("wall")
        wall = plant.AddRigidBody("wall", wall_model, inertia)
        plant.WeldFrames(plant.world_frame(), wall.body_frame())

        robot_model = plant.AddModelInstance("robot")
        slider = plant.AddRigidBody("slider", robot_model, inertia)
        plant.AddJoint(
            PrismaticJoint("x", plant.world_frame(), slider.body_frame(), [1.0, 0.0, 0.0])
        )

        for body in [wall, slider]:
            plant.RegisterCollisionGeometry(
                body, RigidTransform(), Box(side, side, side), body.name(), CoulombFriction(0.5, 0.5)
            )
        plant.Finalize()

        diagram = builder.Build()
        return plant, scene_graph, diagram.CreateDefaultContext(), robot_model

    def test_replay_counts_each_contact_onset(self):
        plant, scene_graph, context, robot = self._build_scene()
        original_q = plant.GetPositions(plant.GetMyContextFromRoot(context), robot).copy()

        # free, hit, still hit, free, hit again
        history = np.array([[0.5], [0.08], [0.05], [0.5], [0.07]])
        log = collisions_along_configuration_history(
            plant, scene_graph, context, robot, history
        )
        summary = summarize_collisions(log)

        self.assertEqual([len(sample) for sample in log], [0, 1, 1, 0, 1])
        self.assertEqual({log[1][0][0], log[1][0][1]}, {"wall::wall", "robot::slider"})
        self.assertTrue(summary.collided)
        self.assertEqual(summary.num_collisions, 2)
        self.assertAlmostEqual(summary.max_depth, 0.05, places=6)
        # The caller's context is not modified
        np.testing.assert_array_equal(
            plant.GetPositions(plant.GetMyContextFromRoot(context), robot), original_q
        )


if __name__ == "__main__":
    unittest.main()
