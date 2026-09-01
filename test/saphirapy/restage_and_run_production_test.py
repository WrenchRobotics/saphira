import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import saphirapy.restage_and_run_production as restage_module


class TestRestageAndRunProduction(unittest.TestCase):
    def test_restage_and_run_one_collects_kinematic_metrics(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target_file = tmp_path / "example.py"
            target_file.write_text("def main():\n    return None\n")

            fake_ref = SimpleNamespace(production_class=object)
            fake_production = object()
            rewritten_tree = ast.parse("def main():\n    production = object()\n")

            with patch.object(restage_module, "BromRestagingManager") as manager_cls, patch.object(
                restage_module.brom_all,
                "ChemLab1",
                object(),
            ), patch.object(
                restage_module,
                "_run_restaged_module",
                return_value=(fake_production, 0),
            ) as run_module_mock, patch.object(
                restage_module,
                "_collect_additional_metrics",
                return_value={"Final Configuration Error": 0.25},
            ) as collect_metrics_mock:
                manager = manager_cls.return_value
                manager.find_production_instances.return_value = [fake_ref]
                manager.replace_production_in_ast.return_value = rewritten_tree

                result = restage_module.restage_and_run_one(
                    target_file=target_file,
                    production_class_name="ChemLab1",
                    output_dir=tmp_path / "out",
                )

            self.assertEqual(result["production_class"], "ChemLab1")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["metrics"], {"Final Configuration Error": 0.25})
            self.assertTrue(Path(result["restaged_file"]).exists())
            manifest = Path(result["restaging_manifest"]).read_text()
            self.assertIn('cast_production_name: "ChemLab1"', manifest)
            self.assertIn('production_class: "ChemLab1"', manifest)
            self.assertIn("package_versions:\n  brom_drake:", manifest)
            self.assertIn("  saphirapy:", manifest)
            self.assertIn("current_actors: []", manifest)
            self.assertIn("actor_variant: null", manifest)
            run_module_mock.assert_called_once()
            collect_metrics_mock.assert_called_once_with(fake_production)

    def test_restage_and_run_one_replaces_selected_actor_variant(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target_file = tmp_path / "example.py"
            target_file.write_text("def main():\n    return None\n")

            fake_ref = SimpleNamespace(production_class=object)
            fake_cast_call = SimpleNamespace(
                cast_member_expr="planner.plan",
                instantiation_node=ast.parse("planner = Planner()").body[0],
                cast_member_type="motion_planning_rrt",
            )
            rewritten_tree = ast.parse("def main():\n    production = object()\n")
            actor_variant = {
                "cast_call_index": 0,
                "name": "bidirectional_rrt",
                "import_statements": ["from example import Planner"],
                "instantiation_code": "planner = Planner()",
                "variable_name": "planner",
                "method_to_call": "plan",
            }

            with patch.object(restage_module, "BromRestagingManager") as manager_cls, patch.object(
                restage_module.brom_all,
                "ChemLab1",
                object(),
            ), patch.object(
                restage_module,
                "_run_restaged_module",
                return_value=(object(), 0),
            ), patch.object(restage_module, "_collect_additional_metrics", return_value={}):
                manager = manager_cls.return_value
                manager.find_production_instances.return_value = [fake_ref]
                manager.find_easy_cast_and_build_calls.return_value = [fake_cast_call]
                manager._extract_class_name_from_instantiation.return_value = "Planner"
                manager.replace_production_in_ast.return_value = rewritten_tree
                manager.replace_cast_member_in_ast.return_value = rewritten_tree

                result = restage_module.restage_and_run_one(
                    target_file=target_file,
                    production_class_name="ChemLab1",
                    output_dir=tmp_path / "out",
                    actor_variant=actor_variant,
                    cast_production_name="actor-bidirectional_rrt-0",
                )

            self.assertEqual(result["actor_variant"], "bidirectional_rrt")
            self.assertEqual(result["cast_production_name"], "actor-bidirectional_rrt-0")
            manifest = Path(result["restaging_manifest"]).read_text()
            self.assertIn('cast_production_name: "actor-bidirectional_rrt-0"', manifest)
            self.assertIn('expression": "planner.plan"', manifest)
            self.assertIn('name: "bidirectional_rrt"', manifest)
            manager.replace_cast_member_in_ast.assert_called_once()


if __name__ == "__main__":
    unittest.main()