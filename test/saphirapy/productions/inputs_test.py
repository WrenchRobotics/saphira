from brom_drake.productions.all import KinematicMotionPlanningProduction
from saphirapy.productions.inputs import identify_base_class_arguments
import unittest


# TODO: Revive this test once we define a better way to initialize these productions.

# class TestProductionsInputs(unittest.TestCase):
#     def test_productions_inputs(self):
#         # Use the base kinematic motion planning production as an example to test the identify_base_class_arguments function
#         test_production_type = KinematicMotionPlanningProduction
#         arguments = identify_base_class_arguments(test_production_type)

#         # Assert that there is at least one argument identified (since all productions should have at least one argument, which is the meshcat_port_number)
#         self.assertGreaterEqual(len(arguments), 1)

if __name__ == "__main__":
    unittest.main()