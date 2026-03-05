import ast
from pathlib import Path
import unittest

# Import Drake first to ensure proper binding order
import pydrake.all
from brom_drake.productions.all import AttemptGraspWithPuppeteerWrist

# Internal imports
from saphirapy.restaging import BromRestagingManager

class TestTransformer(unittest.TestCase):
    def setUp(self):
        pass

    def test_init1(self):
        manager1 = BromRestagingManager(
            output_dir="./test_output",
        )
        self.assertTrue(True)

    def test_find_production_instances1(self):
        # Setup
        manager1 = BromRestagingManager(
            output_dir="./test_output",
        )
       
        # Read contents of the target file and put it into a syntax tree
        target_file_path = Path("../data/grasping_test/attempt.py")
        target_file_contents = target_file_path.read_text()
        parsed_tree = ast.parse(target_file_contents)

        # Find the Production instantiations in the syntax tree
        production_instances = manager1.find_production_instances(parsed_tree)

        print(f"Found {len(production_instances)} object instantiations:")
        for instance in production_instances:
            print(f"  {instance.variable_name} = {instance.production_class}()")

        # Check that we find only one production instantiation and that it is of the correct class
        self.assertEqual(len(production_instances), 1)

        self.assertIs(production_instances[0].production_class, AttemptGraspWithPuppeteerWrist)

    def test_transform_invalid_data(self):
        # Setup
        # manager1 = BromRestagingManager(
        #     target_file="../data/attempt.py",
        # )
       
        # Read contents of the target file
        target_file_path = Path("../data/grasping_test/attempt.py")
        target_file_contents = target_file_path.read_text()
        parsed_tree = ast.parse(target_file_contents)
    
        # Extract all class definitions and their instantiations
        production_instances = []
        
        # Walk through the AST to find all variable assignments
        for node in ast.walk(parsed_tree):
            if isinstance(node, ast.Assign):
                # Check if the value is a Call (instantiation)
                if isinstance(node.value, ast.Call):
                    # Get the class name being instantiated
                    if isinstance(node.value.func, ast.Name):
                        class_name = node.value.func.id
                        # Get variable name
                        if node.targets and isinstance(node.targets[0], ast.Name):
                            var_name = node.targets[0].id
                            production_instances.append({
                                'variable': var_name,
                                'class': class_name,
                                'node': node
                            })
        
        print(f"Found {len(production_instances)} object instantiations:")
        for instance in production_instances:
            print(f"  {instance['variable']} = {instance['class']}()")
        
        # Now check which classes inherit from BaseProduction
        # You need to import and check at runtime
        from brom_drake.productions.all import BaseProduction
        
        production_subclasses = []
        if 'brom_drake.productions' not in target_file_contents:
            self.fail("The target file does not import brom_drake.productions, cannot proceed with the test.")

        for instance in production_instances:
            try:
                # Try to get the class from brom_drake.productions
                from brom_drake.productions import all as prod_module
                print(f"Importing brom_drake.productions to check class {instance['class']}...")
                if hasattr(prod_module, instance['class']):
                    print(f"Checking class {instance['class']}...")
                    cls = getattr(prod_module, instance['class'])
                    if issubclass(cls, BaseProduction):
                        production_subclasses.append(instance)
            except Exception as e:
                print(f"Could not check {instance['class']}: {e}")
        
        print(f"\nFound {len(production_subclasses)} BaseProduction subclasses:")
        for prod in production_subclasses:
            print(f"  {prod['variable']} is a BaseProduction ({prod['class']})")

        self.assertGreater(len(production_subclasses), 0)

    def test_generate_restaged_code1(self):
        # Setup
        manager1 = BromRestagingManager(
            output_dir="./saphira/test_generate_restaged_code1/",
        )

        target_file_path = Path("../data/motion_planning/chem_lab_demo.py")
        new_files = manager1.generate_restaged_code(target_file_path)

        print(f"Generated {len(new_files)} new files:")
        for file in new_files:
            print(f"  {file}")

if __name__ == '__main__':
    unittest.main()
