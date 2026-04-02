
from brom_drake.motion_planning.algorithms.rrt.connect import (
    RRTConnectPlannerConfig,
    RRTConnectPlanner,
)
from brom_drake.productions.motion_planning.offline import ChemLab1
from pathlib import Path
from pydrake.all import Simulator
import numpy as np
from saphirapy.productions.evaluators.offline_kinematic_motion_planning import OfflineKinematicMotionPlanningEvaluator
from typing import Tuple
import unittest

class TestOfflineKinematicMotionPlanningEvaluator(unittest.TestCase):
    def create_dummy_offline_kinematic_motion_planning_evaluator(
            self
        ) -> Tuple[Simulator, ChemLab1]:
        # Create production
        chem_lab_production = ChemLab1(meshcat_port_number=None)  # Use None for CI

        # Create a dummy planner object
        config = RRTConnectPlannerConfig(
            steering_step_size=0.1,
            prob_sample_goal=0.03,
            max_iterations=int(1e5),
            convergence_threshold=1e-3,
            debug_flag=False,
        )
        planner2 = RRTConnectPlanner(
            chem_lab_production.arm,
            chem_lab_production.plant,
            chem_lab_production.scene_graph,
            config=config,
        )

        # To build the production, we only need to provide a planning function
        # (can come from anywhere, not just a BaseRRTPlanner object)
        diagram, diagram_context = chem_lab_production.easy_cast_and_build(
            planner2.plan,
            with_watcher=True,
        )

        print("Simulating...")

        # Simulate the diagram
        simulator = Simulator(diagram, diagram_context)
        simulator.set_target_realtime_rate(1.0)

        # Prepare simulat to run
        simulator.Initialize()

        return simulator, chem_lab_production

    def setUp(self):
        # Set up a temporary directory and create a dummy configuration history file
        self.brom_watcher_dir = Path("./temp_brom_watcher")
        self.brom_watcher_dir.mkdir(exist_ok=True)
        
        # Create a dummy simulator and production to get the configuration history
        simulator, production = self.create_dummy_offline_kinematic_motion_planning_evaluator()

        # Simulate to generate the configuration history file
        simulator.AdvanceTo(0.1)
        simulator.AdvanceTo(0.2)
        simulator.AdvanceTo(0.3)

    def tearDown(self):
        # Clean up the temporary directory
        for file in self.brom_watcher_dir.iterdir():
            file.unlink()
        self.brom_watcher_dir.rmdir()

    def test_load_data_for_evaluation(self):
        # Test if the data is loaded correctly
        loaded_configuration_history = self.evaluator._load_data_for_evaluation()
        np.testing.assert_array_equal(loaded_configuration_history, self.configuration_history)