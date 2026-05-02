
from brom_drake.motion_planning.algorithms.rrt.connect import (
    RRTConnectPlannerConfig,
    RRTConnectPlanner,
)
from brom_drake.productions.motion_planning.offline import ChemLab1
from brom_drake.watchers.all import DiagramWatcher
from pathlib import Path
from pydrake.all import Simulator
import numpy as np
from saphirapy.productions.evaluators.offline_kinematic_motion_planning import OfflineKinematicMotionPlanningEvaluator
from typing import Tuple
import unittest

class TestOfflineKinematicMotionPlanningEvaluator(unittest.TestCase):
    def _create_simple_offline_kinematic_motion_production(self) -> Tuple[Simulator, ChemLab1]:
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
    
    def create_dummy_offline_kinematic_motion_planning_evaluator(
            self,
            T_sim: float = 0.1,
        ) -> Tuple[
            OfflineKinematicMotionPlanningEvaluator,
            Simulator,
            ChemLab1
        ]:
        
        # Create a dummy production and simulate it to generate the configuration history file
        simulator, production = self._create_simple_offline_kinematic_motion_production()

        # Simulate for a short time to generate the files we want
        simulator.AdvanceTo(T_sim)

        # Save the data from this small simulation. Can be triggered by:
        # 1. Deleting the simulator (triggers the BromWatcher to save the data files)
        # 2. Manually triggering the save function in the BromWatcher (not implemented yet)

        watcher: DiagramWatcher = production.watcher
        watcher._save_figures()
        watcher._save_raw_data()

        # Create the evaluator, which will look for the configuration history file and load it
        evaluator = OfflineKinematicMotionPlanningEvaluator(
            brom_watcher_dir=Path(watcher.options.base_directory),
            start_configuration=production.start_configuration,
            goal_configuration=production.goal_configuration,
        )

        return evaluator, simulator, production
        


    def setUp(self):
        # Create a dummy simulator and production to get the configuration history
        self.evaluator, self.simulator, self.production = self.create_dummy_offline_kinematic_motion_planning_evaluator()

        self.brom_watcher_dir = self.evaluator.brom_watcher_dir
        

    def tearDown(self):
        # Clean up the temporary directory
        for file in self.brom_watcher_dir.iterdir():
            file.unlink()
        self.brom_watcher_dir.rmdir()

    def test_load_data_for_evaluation(self):
        # Test if the data is loaded correctly
        loaded_configuration_history = self.evaluator._load_data_for_evaluation()
        np.testing.assert_array_equal(loaded_configuration_history, self.configuration_history)

if __name__ == "__main__":
    unittest.main()