
from brom_drake.motion_planning.algorithms.rrt.connect import (
    RRTConnectPlannerConfig,
    RRTConnectPlanner,
)
from brom_drake.productions.motion_planning.offline import ChemLab1
from brom_drake.watchers.all import DiagramTarget, DiagramWatcher
from pathlib import Path
from pydrake.all import Simulator
import numpy as np
from saphirapy.productions.evaluators.offline_kinematic_motion_planning import OfflineKinematicMotionPlanningEvaluator
import shutil
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
        # - First, create the target system to evaluate
        target_system = production.station.get_name()
        target_port = "measured_arm_position"
        target = DiagramTarget(
            name=target_system, 
            ports=[target_port]
        )

        # - Create the evaluator
        evaluator = OfflineKinematicMotionPlanningEvaluator(
            production=production,
            state_to_evaluate=target,
        )

        # Extract the configuration history from the port watcher
        # for the target system and port
        port_watcher_for_target_port = watcher.get_port_watcher(
            system_name=target_system,
            port_name=target_port
        )
        port_data_dict = port_watcher_for_target_port.get_data_dictionary(production.diagram_context)
        self.configuration_history = port_data_dict[target_port]


        return evaluator, simulator, production
        


    def setUp(self):
        # Create a dummy simulator and production to get the configuration history
        self.evaluator, self.simulator, self.production = self.create_dummy_offline_kinematic_motion_planning_evaluator()

        self.brom_watcher_dir = self.production.watcher.options.base_directory
        

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.brom_watcher_dir)

    def test_load_data_for_evaluation(self):
        # Production Should have been run by now.
        # Data should be saved in the brom_watcher_dir, and the evaluator should be able to load it.

        print(self.production.arm)
        print(
            self.production.plant.GetModelInstanceName(self.production.arm)
        )

        # Test if the data is loaded correctly
        loaded_configuration_history = self.evaluator._load_data_for_evaluation()
        np.testing.assert_array_equal(loaded_configuration_history, self.configuration_history)

    def test_calculate_cartesian_space_length_of_plan_is_greater_than_50cm(self):
        # The arm moves about 8 cm per simulated second, so the short run from setUp
        # cannot cover 50 cm. Simulate long enough for the arm to really travel that far.
        evaluator, _, production = self.create_dummy_offline_kinematic_motion_planning_evaluator(
            T_sim=10.0,
        )
        self.addCleanup(
            shutil.rmtree,
            production.watcher.options.base_directory,
            ignore_errors=True,
        )

        cartesian_length_m = evaluator.calculate_cartesian_space_length_of_plan(
            self.configuration_history
        )

        self.assertGreater(
            cartesian_length_m,
            0.5,
            msg=f"Expected Cartesian path length > 0.5 m, got {cartesian_length_m:.3f} m",
        )

    def test_evaluate_returns_expected_metric_keys(self):
        metrics = self.evaluator.evaluate()

        self.assertIn("Final Configuration Error", metrics)
        self.assertIn("Joint Space Length of Plan", metrics)
        self.assertIn("Cartesian Space Length of Plan", metrics)

if __name__ == "__main__":
    unittest.main()