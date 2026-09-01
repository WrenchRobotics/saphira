import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

# Import Drake first to ensure proper binding order
import pydrake.all

# Internal imports
from saphirapy.restaging import BromRestagingManager
from saphirapy.syntax_tree.cast_call_reference import CastCallReference
from saphirapy.syntax_tree.cast_member_option import CastMemberOption
from saphirapy.productions.cast_member_registry import (
    CAST_MEMBER_ALTERNATIVES,
    get_alternatives_for_type,
)
from saphirapy.utils import identify_algorithm_type


class TestCastMemberDetection(unittest.TestCase):
    """Test Phase 1: Detection of easy_cast_and_build() calls"""

    def setUp(self):
        # Save the data directory path for use in the tests
        self.data_directory = Path(__file__).resolve().parents[1] / "data"
        self.output_directory = Path(__file__).resolve().parents[1] / "saphira" / "test_output"
        self.manager = BromRestagingManager(output_dir=self.output_directory / "cast_member_detection")

    def test_detect_single_easy_cast_and_build_call(self):
        """Test detection of a single easy_cast_and_build() call in real code"""
        # Load the chem_lab_demo.py file which contains an easy_cast_and_build() call
        target_file_path = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        target_file_contents = target_file_path.read_text()
        parsed_tree = ast.parse(target_file_contents)

        # Find easy_cast_and_build() calls
        cast_calls = self.manager.find_easy_cast_and_build_calls(parsed_tree)

        self.assertEqual(len(cast_calls), 1)

        cast_call = cast_calls[0]
        self.assertIsInstance(cast_call, CastCallReference)
        self.assertEqual(cast_call.production_var, "production")
        self.assertEqual(cast_call.cast_member_arg_index, 0)
        self.assertEqual(cast_call.cast_member_expr, "planner2.plan")
        self.assertEqual(cast_call.base_variable_name, "planner2")
        self.assertEqual(cast_call.cast_member_type, "motion_planning_rrt")
        self.assertEqual(ast.unparse(cast_call.instantiation_node), "planner2 = RRTConnectPlanner(production.arm, production.plant, production.scene_graph, config=config)")
        self.assertIs(cast_call.node, self.manager._node_at_path(parsed_tree, cast_call.node_path))
        self.assertIs(
            cast_call.instantiation_node,
            self.manager._node_at_path(parsed_tree, cast_call.instantiation_node_path),
        )

    def test_cast_call_reference_has_required_fields(self):
        """Test that CastCallReference has all required fields"""
        target_file_path = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        target_file_contents = target_file_path.read_text()
        parsed_tree = ast.parse(target_file_contents)

        cast_calls = self.manager.find_easy_cast_and_build_calls(parsed_tree)
        self.assertGreater(len(cast_calls), 0)

        cast_call = cast_calls[0]

        # Verify all required fields are present and non-None
        self.assertIsNotNone(cast_call.production_var)
        self.assertIsNotNone(cast_call.cast_member_expr)
        self.assertIsNotNone(cast_call.cast_member_type)
        self.assertIsNotNone(cast_call.base_variable_name)
        self.assertIsNotNone(cast_call.instantiation_node)
        self.assertIsNotNone(cast_call.instantiation_node_path)
        self.assertIsNotNone(cast_call.call_args)
        self.assertIsNotNone(cast_call.call_kwargs)
        self.assertIsNotNone(cast_call.node)
        self.assertIsNotNone(cast_call.node_path)

    def test_extract_base_variable_from_name(self):
        """Test _extract_base_variable() with simple name"""
        code = "planner_func"
        expr = ast.parse(code, mode='eval').body
        result = self.manager._extract_base_variable(expr)
        self.assertEqual(result, "planner_func")

    def test_extract_base_variable_from_attribute(self):
        """Test _extract_base_variable() with attribute access"""
        code = "planner.plan"
        expr = ast.parse(code, mode='eval').body
        result = self.manager._extract_base_variable(expr)
        self.assertEqual(result, "planner")

    def test_extract_base_variable_from_nested_attribute(self):
        """Test _extract_base_variable() with nested attributes"""
        code = "my_obj.planner.plan"
        expr = ast.parse(code, mode='eval').body
        result = self.manager._extract_base_variable(expr)
        self.assertEqual(result, "my_obj")

    def test_extract_base_variable_from_call(self):
        """Test _extract_base_variable() with function call returns None"""
        code = "get_planner().plan"
        expr = ast.parse(code, mode='eval').body
        result = self.manager._extract_base_variable(expr)
        # Should return None since we can't extract base var from a call
        self.assertIsNone(result)

    def test_find_variable_instantiation(self):
        """Test _find_variable_instantiation() finds correct assignment"""
        code = """
x = 10
planner = SomePlanner()
y = 20
"""
        tree = ast.parse(code)
        assign_node, assign_path = self.manager._find_variable_instantiation(tree, "planner")

        self.assertIsNotNone(assign_node)
        self.assertIsNotNone(assign_path)
        self.assertIsInstance(assign_node, ast.Assign)
        self.assertEqual(assign_node.targets[0].id, "planner")

    def test_find_variable_instantiation_not_found(self):
        """Test _find_variable_instantiation() when variable doesn't exist"""
        code = """
x = 10
y = 20
"""
        tree = ast.parse(code)
        assign_node, assign_path = self.manager._find_variable_instantiation(tree, "planner")

        self.assertIsNone(assign_node)
        self.assertIsNone(assign_path)

    def test_extract_class_name_simple(self):
        """Test _extract_class_name_from_instantiation() with simple class"""
        code = "planner = RRTConnectPlanner()"
        tree = ast.parse(code)
        assign_node = tree.body[0]

        class_name = self.manager._extract_class_name_from_instantiation(assign_node)
        self.assertEqual(class_name, "RRTConnectPlanner")

    def test_extract_class_name_with_args(self):
        """Test _extract_class_name_from_instantiation() with constructor arguments"""
        code = "planner = RRTConnectPlanner(arm, plant, config=cfg)"
        tree = ast.parse(code)
        assign_node = tree.body[0]

        class_name = self.manager._extract_class_name_from_instantiation(assign_node)
        self.assertEqual(class_name, "RRTConnectPlanner")

    def test_extract_class_name_with_module(self):
        """Test _extract_class_name_from_instantiation() with module prefix"""
        code = "planner = algorithms.RRTConnectPlanner()"
        tree = ast.parse(code)
        assign_node = tree.body[0]

        class_name = self.manager._extract_class_name_from_instantiation(assign_node)
        self.assertEqual(class_name, "RRTConnectPlanner")

    def test_extract_class_name_from_factory_call(self):
        """Test _extract_class_name_from_instantiation() with non-call assignment"""
        code = "planner = get_planner()"
        tree = ast.parse(code)
        assign_node = tree.body[0]

        class_name = self.manager._extract_class_name_from_instantiation(assign_node)
        self.assertEqual(class_name, "get_planner")

    def test_no_easy_cast_and_build_calls_in_empty_file(self):
        """Test detection in empty or code without easy_cast_and_build()"""
        code = """
x = 10
y = 20
z = x + y
"""
        tree = ast.parse(code)
        cast_calls = self.manager.find_easy_cast_and_build_calls(tree)

        self.assertEqual(len(cast_calls), 0)

    def test_multiple_easy_cast_and_build_calls(self):
        """Test detection of multiple easy_cast_and_build() calls"""
        code = """
from brom_drake.motion_planning.algorithms.rrt.connect import RRTConnectPlanner
from brom_drake.motion_planning.algorithms.rrt.bidirectional import BidirectionalRRTPlanner
from brom_drake.productions.motion_planning.offline import ChemLab1

production1 = ChemLab1()
planner1 = RRTConnectPlanner(...)
diagram1, ctx1 = production1.easy_cast_and_build(planner1.plan, with_watcher=True)

production2 = ChemLab1()
planner2 = BidirectionalRRTPlanner(...)
diagram2, ctx2 = production2.easy_cast_and_build(planner2.plan, with_watcher=False)
"""
        tree = ast.parse(code)
        cast_calls = self.manager.find_easy_cast_and_build_calls(tree)

        self.assertEqual(len(cast_calls), 2)
        self.assertEqual([call.production_var for call in cast_calls], ["production1", "production2"])
        self.assertEqual([call.cast_member_type for call in cast_calls], ["motion_planning_rrt", "motion_planning_rrt"])

    def test_cast_call_with_multiple_args(self):
        """Test detection of easy_cast_and_build() with multiple arguments"""
        code = """
production = ChemLab1()
planner = RRTConnectPlanner()
diagram, ctx = production.easy_cast_and_build(planner.plan, with_watcher=True, extra_arg=42)
"""
        tree = ast.parse(code)
        cast_calls = self.manager.find_easy_cast_and_build_calls(tree)

        self.assertEqual(len(cast_calls), 1)
        self.assertEqual(cast_calls[0].call_kwargs.keys(), {"with_watcher", "extra_arg"})

    def test_cast_call_with_lambda_not_detected(self):
        """Test that lambda arguments are not detected as base variables"""
        code = """
production = ChemLab1()
diagram, ctx = production.easy_cast_and_build(lambda: print("test"))
"""
        tree = ast.parse(code)
        cast_calls = self.manager.find_easy_cast_and_build_calls(tree)

        self.assertEqual(cast_calls, [])

    def test_cast_call_with_bare_callable_is_not_detected(self):
        """Test that only bound methods are supported cast members."""
        code = """
production = ChemLab1()
planner = RRTConnectPlanner()
diagram, ctx = production.easy_cast_and_build(planner)
"""
        tree = ast.parse(code)

        cast_calls = self.manager.find_easy_cast_and_build_calls(tree)

        self.assertEqual(cast_calls, [])


class TestCastMemberOption(unittest.TestCase):
    """Test Phase 2: Alternative cast-member metadata."""

    def test_stores_all_replacement_metadata(self):
        option = CastMemberOption(
            name="prm_star",
            import_statements=[
                "from brom_drake.motion_planning.algorithms.prm_star import PRMStarConfig, PRMStarPlanner"
            ],
            instantiation_code="prm_planner = PRMStarPlanner(config=PRMStarConfig())",
            variable_name="prm_planner",
            method_to_call="plan_path",
        )

        self.assertEqual(option.name, "prm_star")
        self.assertEqual(
            option.import_statements,
            ["from brom_drake.motion_planning.algorithms.prm_star import PRMStarConfig, PRMStarPlanner"],
        )
        self.assertEqual(
            option.instantiation_code,
            "prm_planner = PRMStarPlanner(config=PRMStarConfig())",
        )
        self.assertEqual(option.variable_name, "prm_planner")
        self.assertEqual(option.method_to_call, "plan_path")

    def test_preserves_multiline_instantiation_code(self):
        instantiation_code = """bidirrt_planner = BiRRTPlanner(
    arm=production.arm,
    plant=production.plant,
    config=BiRRTConfig(max_time=10.0),
)"""
        option = CastMemberOption(
            name="bidirrt",
            import_statements=[
                "from brom_drake.motion_planning.algorithms.bidirrt import BiRRTConfig, BiRRTPlanner"
            ],
            instantiation_code=instantiation_code,
            variable_name="bidirrt_planner",
            method_to_call="execute",
        )

        self.assertEqual(option.instantiation_code, instantiation_code)
        self.assertIn("BiRRTConfig(max_time=10.0)", option.instantiation_code)


class TestCastMemberReplacement(unittest.TestCase):
    """Test Phase 3: AST replacement using a detected cast call."""

    def setUp(self):
        self.manager = BromRestagingManager(output_dir=Path("unused"))

    def _replace(self, code: str, option: CastMemberOption) -> tuple[ast.Module, ast.Module]:
        source_tree = ast.parse(code)
        references = self.manager.find_easy_cast_and_build_calls(source_tree)
        self.assertEqual(len(references), 1)
        return source_tree, self.manager.replace_cast_member_in_ast(source_tree, references[0], option)

    def test_replaces_assignment_and_cast_member_without_mutating_source(self):
        source_tree, replacement_tree = self._replace(
            """planner = RRTConnectPlanner(config)
diagram, context = production.easy_cast_and_build(planner.plan, with_watcher=True)
""",
            CastMemberOption(
                name="prm_star",
                import_statements=["from planners import PRMStarPlanner"],
                instantiation_code="prm_planner = PRMStarPlanner(config)",
                variable_name="prm_planner",
                method_to_call="plan_path",
            ),
        )

        self.assertEqual(
            ast.unparse(source_tree),
            "planner = RRTConnectPlanner(config)\ndiagram, context = production.easy_cast_and_build(planner.plan, with_watcher=True)",
        )
        self.assertEqual(
            ast.unparse(replacement_tree),
            "from planners import PRMStarPlanner\nprm_planner = PRMStarPlanner(config)\ndiagram, context = production.easy_cast_and_build(prm_planner.plan_path, with_watcher=True)",
        )
        compile(replacement_tree, filename="<replacement>", mode="exec")

    def test_removes_only_obsolete_planner_import(self):
        source_tree, replacement_tree = self._replace(
            '''"""A documented production script."""
from legacy_planners import RRTConnectPlanner, RRTConnectPlannerConfig
from helpers import report

config = RRTConnectPlannerConfig()
planner = RRTConnectPlanner(config)
diagram, context = production.easy_cast_and_build(planner.plan)
''',
            CastMemberOption(
                name="prm_star",
                import_statements=["from new_planners import PRMStarPlanner"],
                instantiation_code="prm_planner = PRMStarPlanner(config)",
                variable_name="prm_planner",
                method_to_call="plan_path",
            ),
        )

        replacement_code = ast.unparse(replacement_tree)
        self.assertTrue(ast.get_docstring(replacement_tree))
        self.assertIn("from legacy_planners import RRTConnectPlannerConfig", replacement_code)
        self.assertNotIn("RRTConnectPlanner,", replacement_code)
        self.assertIn("from helpers import report", replacement_code)
        self.assertIn("from new_planners import PRMStarPlanner", replacement_code)
        self.assertEqual(ast.unparse(source_tree.body[1]), "from legacy_planners import RRTConnectPlanner, RRTConnectPlannerConfig")

    def test_replaces_nested_assignment_with_multiple_statements(self):
        _, replacement_tree = self._replace(
            """def build(production):
    planner = RRTConnectPlanner()
    return production.easy_cast_and_build(planner.plan, with_watcher=False)
""",
            CastMemberOption(
                name="bidirrt",
                import_statements=["from planners import BiRRTConfig, BiRRTPlanner"],
                instantiation_code="""new_config = BiRRTConfig(max_time=10.0)
bidirrt_planner = BiRRTPlanner(config=new_config)""",
                variable_name="bidirrt_planner",
                method_to_call="execute",
            ),
        )

        function_body = replacement_tree.body[1].body
        self.assertEqual(ast.unparse(function_body[0]), "new_config = BiRRTConfig(max_time=10.0)")
        self.assertEqual(ast.unparse(function_body[1]), "bidirrt_planner = BiRRTPlanner(config=new_config)")
        self.assertEqual(
            ast.unparse(function_body[2]),
            "return production.easy_cast_and_build(bidirrt_planner.execute, with_watcher=False)",
        )
        compile(replacement_tree, filename="<replacement>", mode="exec")

    def test_does_not_add_duplicate_import(self):
        _, replacement_tree = self._replace(
            """from planners import PRMStarPlanner
planner = RRTConnectPlanner()
diagram, context = production.easy_cast_and_build(planner.plan)
""",
            CastMemberOption(
                name="prm_star",
                import_statements=["from planners import PRMStarPlanner"],
                instantiation_code="prm_planner = PRMStarPlanner()",
                variable_name="prm_planner",
                method_to_call="plan_path",
            ),
        )

        self.assertEqual(ast.unparse(replacement_tree).count("from planners import PRMStarPlanner"), 1)

    def test_rejects_empty_replacement_instantiation(self):
        source_tree = ast.parse(
            """planner = RRTConnectPlanner()
diagram, context = production.easy_cast_and_build(planner.plan)
"""
        )
        reference = self.manager.find_easy_cast_and_build_calls(source_tree)[0]
        option = CastMemberOption(
            name="empty",
            import_statements=[],
            instantiation_code="",
            variable_name="replacement",
            method_to_call="plan",
        )

        with self.assertRaisesRegex(ValueError, "must not be empty"):
            self.manager.replace_cast_member_in_ast(source_tree, reference, option)

    def test_rejects_non_import_option_statement(self):
        source_tree = ast.parse(
            """planner = RRTConnectPlanner()
diagram, context = production.easy_cast_and_build(planner.plan)
"""
        )
        reference = self.manager.find_easy_cast_and_build_calls(source_tree)[0]
        option = CastMemberOption(
            name="invalid-import",
            import_statements=["replacement = Planner()"],
            instantiation_code="replacement = Planner()",
            variable_name="replacement",
            method_to_call="plan",
        )

        with self.assertRaisesRegex(ValueError, "exactly one import statement"):
            self.manager.replace_cast_member_in_ast(source_tree, reference, option)


class TestCastMemberRegistry(unittest.TestCase):
    """Test Phase 4: The built-in alternative registry."""

    def test_motion_planning_registry_contains_verified_alternatives(self):
        options = get_alternatives_for_type("motion_planning_rrt")

        self.assertIs(options, CAST_MEMBER_ALTERNATIVES["motion_planning_rrt"])
        self.assertEqual(
            [option.name for option in options],
            ["rrt_connect", "bidirectional_rrt", "bidirectional_rrt_connect"],
        )
        for option in options:
            self.assertEqual(option.method_to_call, "plan")
            self.assertEqual(ast.unparse(ast.parse(option.instantiation_code).body[-1].targets[0]), option.variable_name)
            for import_statement in option.import_statements:
                namespace = {}
                exec(import_statement, namespace)

    def test_unknown_algorithm_type_has_no_alternatives(self):
        self.assertEqual(get_alternatives_for_type("unknown"), [])


class TestAlgorithmTypeIdentification(unittest.TestCase):
    """Test classifications used to select the default alternative registry."""

    def test_recognizes_all_registered_motion_planner_classes(self):
        class_names = [
            "RRTConnectPlanner",
            "BidirectionalRRTPlanner",
            "BidirectionalRRTConnectPlanner",
        ]

        self.assertEqual(
            [identify_algorithm_type(class_name) for class_name in class_names],
            ["motion_planning_rrt", "motion_planning_rrt", "motion_planning_rrt"],
        )

    def test_rejects_unregistered_planner_class(self):
        with self.assertRaisesRegex(ValueError, "Unknown algorithm class"):
            identify_algorithm_type("UnknownPlanner")


class TestCastMemberVariantGeneration(unittest.TestCase):
    """Test Phase 4: Generating Python source variants from alternatives."""

    def _write_source(self, directory: Path, contents: str) -> Path:
        source_file = directory / "source.py"
        source_file.write_text(contents)
        return source_file

    def _custom_options(self) -> list[CastMemberOption]:
        return [
            CastMemberOption(
                name="first",
                import_statements=["from alternatives import FirstPlanner"],
                instantiation_code="first_planner = FirstPlanner()",
                variable_name="first_planner",
                method_to_call="plan",
            ),
            CastMemberOption(
                name="second",
                import_statements=["from alternatives import SecondPlanner"],
                instantiation_code="second_planner = SecondPlanner()",
                variable_name="second_planner",
                method_to_call="plan_path",
            ),
        ]

    def test_generates_one_file_per_injected_alternative(self):
        source_code = """planner = RRTConnectPlanner()
diagram, context = production.easy_cast_and_build(planner.plan, with_watcher=True)
"""
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_file = self._write_source(root, source_code)
            manager = BromRestagingManager(root / "variants")

            output_files = manager.generate_cast_member_variants(
                source_file,
                {"motion_planning_rrt": self._custom_options()},
            )

            self.assertEqual([path.name for path in output_files], ["source_first.py", "source_second.py"])
            self.assertTrue(all(path.is_file() for path in output_files))
            self.assertEqual(source_file.read_text(), source_code)
            self.assertIn("first_planner.plan", output_files[0].read_text())
            self.assertIn("second_planner.plan_path", output_files[1].read_text())
            self.assertTrue(all(ast.parse(path.read_text()) for path in output_files))

    def test_generates_default_variants_for_real_chem_lab_fixture(self):
        source_file = Path(__file__).resolve().parents[1] / "data" / "motion_planning" / "chem_lab_demo.py"
        with TemporaryDirectory() as temporary_directory:
            manager = BromRestagingManager(Path(temporary_directory))

            output_files = manager.generate_cast_member_variants(source_file)

            self.assertEqual(
                [path.name for path in output_files],
                [
                    "chem_lab_demo_rrt_connect.py",
                    "chem_lab_demo_bidirectional_rrt.py",
                    "chem_lab_demo_bidirectional_rrt_connect.py",
                ],
            )
            for output_file in output_files:
                generated_tree = ast.parse(output_file.read_text())
                compile(generated_tree, filename=str(output_file), mode="exec")

    def test_returns_no_files_when_source_has_no_supported_cast_call(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_file = self._write_source(root, "value = 1\n")
            output_directory = root / "variants"
            manager = BromRestagingManager(output_directory)

            output_files = manager.generate_cast_member_variants(source_file)

            self.assertEqual(output_files, [])
            self.assertFalse(output_directory.exists())

    def test_rejects_unknown_algorithm_type(self):
        source_code = """planner = CustomPlanner()
diagram, context = production.easy_cast_and_build(planner.plan)
"""
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manager = BromRestagingManager(root / "variants")
            source_file = self._write_source(root, source_code)

            with self.assertRaisesRegex(ValueError, "does not have a registered algorithm type"):
                manager.generate_cast_member_variants(source_file)

    def test_rejects_multiple_supported_cast_calls(self):
        source_code = """first_planner = RRTConnectPlanner()
second_planner = RRTConnectPlanner()
first_diagram, first_context = first_production.easy_cast_and_build(first_planner.plan)
second_diagram, second_context = second_production.easy_cast_and_build(second_planner.plan)
"""
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manager = BromRestagingManager(root / "variants")
            source_file = self._write_source(root, source_code)

            with self.assertRaisesRegex(ValueError, "exactly one supported cast call"):
                manager.generate_cast_member_variants(source_file)

    def test_rejects_missing_injected_alternatives(self):
        source_code = """planner = RRTConnectPlanner()
diagram, context = production.easy_cast_and_build(planner.plan)
"""
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manager = BromRestagingManager(root / "variants")
            source_file = self._write_source(root, source_code)

            with self.assertRaisesRegex(ValueError, "No cast-member alternatives"):
                manager.generate_cast_member_variants(source_file, {})


if __name__ == "__main__":
    unittest.main()
