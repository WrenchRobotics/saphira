import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from saphirapy.detect_production_map import detect_production_mapping
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    build_summary,
    finalize_result_payload,
    find_alternative_actors_for_production,
    parse_detection_outputs,
)


class TestGitHubActionContracts(unittest.TestCase):
    def setUp(self):
        self.data_directory = Path(__file__).resolve().parents[1] / "data"

    def test_parse_detection_outputs_matches_matrix_contract(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"

        detection_payload = detect_production_mapping(target_file)
        outputs = parse_detection_outputs(json.dumps(detection_payload))

        related_productions = json.loads(outputs["related-productions-json"])

        self.assertEqual(outputs["detected-parent"], detection_payload["detected_parent_class"])
        self.assertEqual(related_productions, detection_payload["related_productions"])
        self.assertGreater(len(related_productions), 0)
        self.assertTrue(all(isinstance(production, str) for production in related_productions))

    def test_parse_detection_outputs_defaults_to_unmapped(self):
        outputs = parse_detection_outputs(
            json.dumps({
                "detected_parent_class": None,
                "related_productions": [],
            })
        )

        self.assertEqual(outputs["detected-parent"], "UNMAPPED")
        self.assertEqual(outputs["related-productions-json"], "[]")

    def test_finds_registered_alternative_actors_for_production(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"

        actors = find_alternative_actors_for_production(target_file)

        self.assertEqual(len(actors), 1)
        self.assertEqual(actors[0]["production_variable"], "production")
        self.assertEqual(actors[0]["current_actor"], "planner2.plan")
        self.assertEqual(actors[0]["current_actor_class"], "RRTConnectPlanner")
        self.assertEqual(actors[0]["actor_type"], "motion_planning_rrt")
        self.assertEqual(
            [alternative["name"] for alternative in actors[0]["alternatives"]],
            ["bidirectional_rrt", "bidirectional_rrt_connect"],
        )
        self.assertTrue(
            all(
                {"import_statements", "instantiation_code", "variable_name", "method_to_call"}
                <= alternative.keys()
                for alternative in actors[0]["alternatives"]
            )
        )

    def test_cast_production_outputs_keep_baseline_separate_from_opt_in_variants(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        detection_payload = json.dumps(detect_production_mapping(target_file))

        disabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, False, False
        )
        enabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, True, True
        )

        self.assertEqual(disabled_outputs["detected-production"], "ChemLab1")
        self.assertEqual(json.loads(disabled_outputs["alternative-productions-json"]), [])
        self.assertEqual(json.loads(disabled_outputs["alternative-actors-json"]), [])
        self.assertEqual(enabled_outputs["has-alternative-productions"], "true")
        self.assertEqual(
            json.loads(enabled_outputs["alternative-productions-json"]), ["ShelfPlanning1"]
        )
        self.assertEqual(
            [actor["name"] for actor in json.loads(enabled_outputs["alternative-actors-json"])],
            ["bidirectional_rrt", "bidirectional_rrt_connect"],
        )

    def test_cast_productions_json_combines_productions_and_actors(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        detection_payload = json.dumps(detect_production_mapping(target_file))

        disabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, False, False
        )
        enabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, True, True
        )

        disabled_cast_productions = json.loads(disabled_outputs["cast-productions-json"])
        self.assertEqual(
            [cast_production["cast-production-name"] for cast_production in disabled_cast_productions],
            ["production-ChemLab1", "production-ShelfPlanning1"],
        )
        self.assertTrue(
            all(cast_production["actor-variant-json"] == "" for cast_production in disabled_cast_productions)
        )

        enabled_cast_productions = json.loads(enabled_outputs["cast-productions-json"])
        self.assertEqual(
            [cast_production["cast-production-name"] for cast_production in enabled_cast_productions],
            [
                "production-ChemLab1",
                "production-ShelfPlanning1",
                "actor-bidirectional_rrt-0",
                "actor-bidirectional_rrt_connect-0",
            ],
        )
        actor_cast_productions = [
            cast_production
            for cast_production in enabled_cast_productions
            if cast_production["actor-variant-json"]
        ]
        self.assertTrue(
            all(cast_production["production-class"] == "ChemLab1" for cast_production in actor_cast_productions)
        )
        self.assertTrue(
            all(
                json.loads(cast_production["actor-variant-json"])["cast_call_index"] == 0
                for cast_production in actor_cast_productions
            )
        )

    def test_result_payloads_feed_combined_summary(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            first_results_dir = tmp_path / "matrix-results-a"
            second_results_dir = tmp_path / "matrix-results-b"

            finalize_result_payload(
                first_results_dir / "ShelfPlanning1.json",
                "ShelfPlanning1",
                0,
                12.5,
                "productions/restaged/shelf_planning1.py",
                "baseline-ChemLab1",
            )
            finalize_result_payload(
                second_results_dir / "no-related-productions.json",
                "NO_RELATED_PRODUCTIONS",
                1,
                3.25,
                "",
            )

            summary = build_summary(f"{first_results_dir}\n{second_results_dir}")

            self.assertIn("| baseline-ChemLab1 | 0 | 12.5 | passed |", summary)
            self.assertIn("| NO_RELATED_PRODUCTIONS | 1 | 3.25 | failed |", summary)
            self.assertNotIn("productions/restaged/shelf_planning1.py", summary)
            self.assertIn("Totals: 1 passed, 1 failed", summary)

    def test_summary_includes_additional_metrics_when_present(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            result_path = tmp_path / "matrix-results" / "ChemLab1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(
                    {
                        "production_class": "ChemLab1",
                        "restaged_file": "productions/restaged/chem_lab1.py",
                        "exit_code": 0,
                        "duration_seconds": 1.5,
                        "metrics": {
                            "Final Configuration Error": 0.01,
                            "Joint Space Length of Plan": 2.5,
                        },
                    }
                )
            )

            summary = build_summary(str(result_path.parent))

            self.assertIn("Additional Metrics:", summary)
            self.assertIn(
                "| Production | Final Configuration Error | Joint Space Length of Plan |",
                summary,
            )
            self.assertIn(
                "| ChemLab1 | 0.01 | 2.5 |",
                summary,
            )


if __name__ == "__main__":
    unittest.main()